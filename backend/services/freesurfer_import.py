"""Import FreeSurfer outputs (a zipped recon-all or FastSurfer subject folder).

The fast in-app pipeline labels the T1 with a DKT network and builds a display
surface from those labels (cortical_surface.py). This module is the other
source: FreeSurfer, run locally by whoever owns the data, uploaded as a zip of
its standard outputs. Nothing here depends on how the zip was made -- the
`freesurfer` pipeline repo produces one, but so does zipping any recon-all
subject directory.

WHAT AN IMPORT PRODUCES (recon_dir/freesurfer/):
  subject/...                  the whitelisted FreeSurfer files, canonical names
  cortical_surface.json        lh+rh pial in the app frame, same payload schema
                               as the fast surface plus Desikan/Destrieux
  labels_dkt_on_mri.nii.gz     aparc.DKTatlas+aseg resampled onto mri.nii.gz
  info.json                    provenance, alignment check, counts
and recon_dir/freesurfer_status.json (processing | ready | error), which lives
outside that directory so it survives the directory being replaced.

HOW IT BECOMES THE ACTIVE SOURCE. Everything downstream -- structure meshes,
contact labels, the 2D overlay, the MNI export CSV -- reads one file,
structures_cortical.nii.gz. activate_source() swaps FreeSurfer's labels into
that file and keeps the fast labels beside it, so switching back never re-runs
the 9 GB DKT network. The marker structures_cortical.source records which one
is in place; it is the single source of truth for "which parcellation is this".

COORDINATES. FreeSurfer surfaces are in tkr-RAS of the conformed volume. The
scanner-RAS transform is orig.vox2ras @ inv(orig.vox2ras_tkr) -- the full
matrices, because these T1s are oblique and the c_ras-only shortcut is only
right for axis-aligned scans. Scanner RAS is the frame of mri.nii.gz, and the
app frame is that minus mesh.json's center, which must never change (contacts
are stored relative to it). When FreeSurfer ran on a different T1 than this
reconstruction's (another session, another reformat), orig.mgz no longer
overlays mri.nii.gz; that is caught by an intensity correlation and fixed with
a rigid registration composed into every transform.

Log output must stay ASCII (uvicorn stdout is cp1252 on Windows).
"""

import base64
import datetime as _dt
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile

import numpy as np

try:                                            # normal package import
    from services.structure_extractor import ALL_STRUCTURES
    from services.worker_mem import (HEAVY_JOB_LOCK, describe_limit,
                                     describe_outcome, run_worker)
except ImportError:                             # run directly as a worker script
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from services.structure_extractor import ALL_STRUCTURES
    from services.worker_mem import (HEAVY_JOB_LOCK, describe_limit,
                                     describe_outcome, run_worker)

FS_DIR = "freesurfer"
STAGING_DIR = "freesurfer.staging"
STATUS_NAME = "freesurfer_status.json"
INFO_NAME = "info.json"
SURFACE_NAME = "cortical_surface.json"
FS_LABELS = "labels_dkt_on_mri.nii.gz"
ACTIVE_LABELS = "structures_cortical.nii.gz"
FAST_BACKUP = "structures_cortical.fast.nii.gz"
SOURCE_MARKER = "structures_cortical.source"
UPLOAD_NAME = "upload.zip"

SOURCES = ("fast", "freesurfer")
SURFACE_SCHEMA = 1

# A full recon-all subject is ~0.5-1 GB; the slim export is ~50 MB.
MAX_UNCOMPRESSED_BYTES = 3 * 1024 ** 3
# orig.mgz vs mri.nii.gz intensity correlation. The same T1 conformed by
# FreeSurfer correlates at ~0.95+; anything below this is treated as a
# different scan and registered.
NCC_ALIGNED = 0.80
# After registration it must at least clear this, or the zip is for someone else.
NCC_MIN_REGISTERED = 0.50


class FreeSurferImportError(ValueError):
    """A problem with the uploaded outputs that the user can fix."""


# ── Files ─────────────────────────────────────────────────────────────────────
# Canonical name -> candidates inside the subject folder, first match wins. The
# alternatives cover FastSurfer's names and copies that lost recon-all's
# lh.pial -> lh.pial.T1 symlink.
REQUIRED = {
    "mri/orig.mgz": ["mri/orig.mgz"],
    "mri/aparc.DKTatlas+aseg.mgz": ["mri/aparc.DKTatlas+aseg.mgz",
                                    "mri/aparc.DKTatlas+aseg.mapped.mgz",
                                    "mri/aparc.DKTatlas+aseg.deep.mgz"],
}
OPTIONAL = {
    "mri/aparc+aseg.mgz": ["mri/aparc+aseg.mgz"],
    "mri/aparc.a2009s+aseg.mgz": ["mri/aparc.a2009s+aseg.mgz"],
    "mri/aseg.mgz": ["mri/aseg.mgz"],
    "scripts/build-stamp.txt": ["scripts/build-stamp.txt"],
    "scripts/recon-all.done": ["scripts/recon-all.done"],
    "fspipe_manifest.json": ["fspipe_manifest.json"],
    "stats/aseg.stats": ["stats/aseg.stats"],
}
for _h in ("lh", "rh"):
    REQUIRED[f"surf/{_h}.pial"] = [f"surf/{_h}.pial", f"surf/{_h}.pial.T2",
                                   f"surf/{_h}.pial.FLAIR", f"surf/{_h}.pial.T1"]
    REQUIRED[f"surf/{_h}.sulc"] = [f"surf/{_h}.sulc"]
    REQUIRED[f"label/{_h}.aparc.DKTatlas.annot"] = [f"label/{_h}.aparc.DKTatlas.annot",
                                                    f"label/{_h}.aparc.DKTatlas.mapped.annot"]
    OPTIONAL[f"surf/{_h}.white"] = [f"surf/{_h}.white"]
    OPTIONAL[f"surf/{_h}.thickness"] = [f"surf/{_h}.thickness"]
    OPTIONAL[f"label/{_h}.aparc.annot"] = [f"label/{_h}.aparc.annot"]
    OPTIONAL[f"label/{_h}.aparc.a2009s.annot"] = [f"label/{_h}.aparc.a2009s.annot"]

_ROOT_RE = re.compile(r"^(.*?)surf/lh\.pial(\.T1|\.T2|\.FLAIR)?$")


def _is_symlink(info):
    return stat.S_ISLNK(info.external_attr >> 16)


def _safe_name(name):
    """Normalised member name, or None if it could escape the extraction dir."""
    n = name.replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        return None
    if any(part == ".." for part in n.split("/")):
        return None
    return n


def inspect_zip(zip_path):
    """Check an upload without extracting it.

    Returns {canonical name: member name}. Raises FreeSurferImportError with a
    message meant for the user. Only reads the zip's central directory, so it is
    cheap enough to run in the request before accepting the upload.
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise FreeSurferImportError("Not a zip file")
    with zf:
        infos = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = _safe_name(info.filename)
            if name is None:
                raise FreeSurferImportError(f"Unsafe path in zip: {info.filename!r}")
            if _is_symlink(info) or info.file_size == 0:
                continue        # a stored link is its target's name, not data
            infos[name] = info

    roots = sorted({m.group(1) for n in infos for m in [_ROOT_RE.match(n)] if m})
    if not roots:
        raise FreeSurferImportError(
            "No FreeSurfer subject found: the zip needs a subject folder with "
            "surf/lh.pial (zip the whole recon-all subject folder, or use fspipe export)")
    if len(roots) > 1:
        raise FreeSurferImportError(
            "The zip holds more than one subject (" + ", ".join(r.rstrip('/') or '.' for r in roots)
            + "); upload one at a time")
    root = roots[0]

    chosen, missing = {}, []
    for table, required in ((REQUIRED, True), (OPTIONAL, False)):
        for canon, cands in table.items():
            hit = next((root + c for c in cands if root + c in infos), None)
            if hit:
                chosen[canon] = hit
            elif required:
                missing.append(canon)
    if missing:
        raise FreeSurferImportError(
            "Missing FreeSurfer outputs: " + ", ".join(missing)
            + ". Is the recon-all/FastSurfer run complete?")
    total = sum(infos[m].file_size for m in chosen.values())
    if total > MAX_UNCOMPRESSED_BYTES:
        raise FreeSurferImportError(f"Selected files expand to {total / 1e9:.1f} GB; too large")
    return chosen


def extract_upload(zip_path, dest_dir):
    """Extract the whitelisted files to dest_dir/<canonical name>.
    Returns {canonical name: absolute path}."""
    chosen = inspect_zip(zip_path)
    out = {}
    with zipfile.ZipFile(zip_path) as zf:
        for canon, member in chosen.items():
            info = zf.getinfo(member)
            target = os.path.join(dest_dir, *canon.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            written = 0
            with zf.open(info) as src, open(target, "wb") as dst:
                while True:
                    block = src.read(1 << 20)
                    if not block:
                        break
                    written += len(block)
                    # The declared size is what was vetted; never trust a stream
                    # that runs past it.
                    if written > info.file_size:
                        raise FreeSurferImportError(f"{member} is larger than declared")
                    dst.write(block)
            out[canon] = target
    return out


# ── Geometry ──────────────────────────────────────────────────────────────────
def tkr_to_scanner(orig_img):
    """4x4: FreeSurfer surface (tkr-RAS) -> scanner RAS, from orig.mgz."""
    return orig_img.affine @ np.linalg.inv(orig_img.header.get_vox2ras_tkr())


def _apply(m, pts):
    return pts @ m[:3, :3].T + m[:3, 3]


def alignment_ncc(orig_img, mri_img, orig_to_mri=None, step=4):
    """Pearson correlation of orig.mgz sampled onto mri.nii.gz's grid.

    orig_to_mri maps orig's world into mri's world (identity when FreeSurfer
    ran on this same T1). Sampled on every `step`-th voxel: ~1e6 points is
    plenty for a correlation and keeps this to a second or two.
    """
    from scipy.ndimage import map_coordinates

    mri = np.asanyarray(mri_img.dataobj)
    if mri.ndim > 3:
        mri = mri[..., 0]
    orig = np.asanyarray(orig_img.dataobj).astype(np.float32)
    ii, jj, kk = np.meshgrid(*(np.arange(0, s, step) for s in mri.shape[:3]), indexing="ij")
    ijk = np.stack([ii.ravel(), jj.ravel(), kk.ravel()], axis=1).astype(np.float64)
    world = _apply(mri_img.affine, ijk)
    if orig_to_mri is not None:
        world = _apply(np.linalg.inv(orig_to_mri), world)
    src = _apply(np.linalg.inv(orig_img.affine), world)
    vo = map_coordinates(orig, src.T, order=1, mode="constant", cval=0.0)
    vm = mri[ii.ravel(), jj.ravel(), kk.ravel()].astype(np.float32)
    # Head only: FreeSurfer zeroes nothing in orig, but air is near 0 in both.
    keep = (vo > 5) & (vm > np.percentile(vm, 20))
    if keep.sum() < 1000:
        return 0.0
    return float(np.corrcoef(vo[keep], vm[keep])[0, 1])


def _register_orig(orig_img, mri_path, workdir):
    """Rigid orig.mgz -> mri.nii.gz. Returns the 4x4 orig world -> mri world."""
    import nibabel as nib
    from services.registration import register_secondary_to_primary

    orig_nii = os.path.join(workdir, "_orig.nii.gz")
    nib.save(nib.Nifti1Image(np.asanyarray(orig_img.dataobj), orig_img.affine), orig_nii)
    out = os.path.join(workdir, "_orig_in_mri.nii.gz")
    m = register_secondary_to_primary(mri_path, orig_nii, out, threads=min(8, os.cpu_count() or 1))
    for p in (orig_nii, out):
        try:
            os.remove(p)
        except OSError:
            pass
    return np.asarray(m, dtype=np.float64)


def _outward(vertices, faces):
    """faces wound so normals point out (FreeSurfer's are; a reflection would
    flip them and three.js would then cull the outside of the brain)."""
    v0, v1, v2 = (vertices[faces[:, k]] for k in range(3))
    c = vertices.mean(axis=0)
    signed = np.einsum("ij,ij->i", v0 - c, np.cross(v1 - c, v2 - c)).sum()
    return faces if signed >= 0 else faces[:, [0, 2, 1]]


# ── Parcellations ─────────────────────────────────────────────────────────────
# FreeSurfer's aparc colour-table order. aparc+aseg labels cortex 1000 + this
# index (left) and 2000 + index (right); the DKT annot uses the same table with
# three regions unused. Mapping by NAME keeps this right whatever order the
# annot's own table happens to have.
APARC_NAMES = [
    "unknown", "bankssts", "caudalanteriorcingulate", "caudalmiddlefrontal",
    "corpuscallosum", "cuneus", "entorhinal", "fusiform", "inferiorparietal",
    "inferiortemporal", "isthmuscingulate", "lateraloccipital", "lateralorbitofrontal",
    "lingual", "medialorbitofrontal", "middletemporal", "parahippocampal", "paracentral",
    "parsopercularis", "parsorbitalis", "parstriangularis", "pericalcarine", "postcentral",
    "posteriorcingulate", "precentral", "precuneus", "rostralanteriorcingulate",
    "rostralmiddlefrontal", "superiorfrontal", "superiorparietal", "superiortemporal",
    "supramarginal", "frontalpole", "temporalpole", "transversetemporal", "insula",
]
APARC_INDEX = {n: i for i, n in enumerate(APARC_NAMES)}
_NOT_CORTEX = {"unknown", "corpuscallosum", "medialwall", "medial_wall", "???"}

ATLASES = {
    # key: (annot stem, display name)
    "dkt": ("aparc.DKTatlas", "DKT"),
    "desikan": ("aparc", "Desikan-Killiany"),
    "destrieux": ("aparc.a2009s", "Destrieux"),
}
_PRETTY = {"bankssts": "Banks STS", "corpuscallosum": "Corpus Callosum"}


def _catalog():
    colors, names = {}, {}
    for info in ALL_STRUCTURES.values():
        for lb in info["labels"]:
            if lb >= 1000:
                colors[int(lb)] = info["color"]
                names[int(lb)] = info["label"]
    return colors, names


def _hex(rgb):
    return "#%02X%02X%02X" % tuple(int(c) for c in rgb[:3])


def _annot_ids(atlas, hemi, path, n_vertices):
    """(per-vertex uint16 ids, {id: hex}, {id: name}) for one hemisphere."""
    import nibabel as nib

    labels, ctab, names = nib.freesurfer.read_annot(path)
    if len(labels) != n_vertices:
        raise FreeSurferImportError(
            f"{os.path.basename(path)} has {len(labels)} vertices but the {hemi} pial "
            f"surface has {n_vertices}; they come from different runs")
    names = [n.decode() if isinstance(n, bytes) else str(n) for n in names]
    side = "Left" if hemi == "lh" else "Right"
    base_dk, base_ds = (1000, 11100) if hemi == "lh" else (2000, 12100)
    cat_colors, cat_names = _catalog()

    lut = np.zeros(len(names) + 1, np.uint16)   # last slot catches label -1
    colors, display = {}, {}
    for idx, name in enumerate(names):
        if name.lower() in _NOT_CORTEX:
            continue
        if atlas == "destrieux":
            fid = base_ds + idx
            label = f"{side} {name}"
            color = _hex(ctab[idx])
        else:
            if name not in APARC_INDEX:
                continue
            fid = base_dk + APARC_INDEX[name]
            label = cat_names.get(fid) or f"{side} {_PRETTY.get(name, name)}"
            color = cat_colors.get(fid) or _hex(ctab[idx])
        lut[idx] = fid
        colors[fid] = color
        display[fid] = label
    ids = lut[np.where(labels >= 0, labels, len(names))]
    return ids.astype(np.uint16), colors, display


def _b64(arr):
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


def build_surface_payload(files, scanner_to_app_world, center):
    """The cortical-surface payload for both pial surfaces.

    scanner_to_app_world: 4x4 taking FreeSurfer scanner RAS into mri.nii.gz's
    world (identity unless a registration was needed). center: mesh.json center.
    """
    import nibabel as nib

    orig = nib.load(files["mri/orig.mgz"])
    m = scanner_to_app_world @ tkr_to_scanner(orig)
    verts, faces, sulc, n_lh = [], [], [], 0
    per_atlas = {k: ([], {}, {}) for k in ATLASES}
    available = [k for k, (stem, _) in ATLASES.items()
                 if all(f"label/{h}.{stem}.annot" in files for h in ("lh", "rh"))]
    for hemi in ("lh", "rh"):
        v, f = nib.freesurfer.read_geometry(files[f"surf/{hemi}.pial"])
        v = _apply(m, v.astype(np.float64))
        f = _outward(v, f.astype(np.int64))
        s = nib.freesurfer.read_morph_data(files[f"surf/{hemi}.sulc"])
        if len(s) != len(v):
            raise FreeSurferImportError(
                f"{hemi}.sulc has {len(s)} values but {hemi}.pial has {len(v)} vertices")
        for atlas in available:
            stem = ATLASES[atlas][0]
            ids, colors, names = _annot_ids(atlas, hemi, files[f"label/{hemi}.{stem}.annot"], len(v))
            per_atlas[atlas][0].append(ids)
            per_atlas[atlas][1].update(colors)
            per_atlas[atlas][2].update(names)
        faces.append(f + sum(len(x) for x in verts))
        verts.append(v)
        sulc.append(s)
        if hemi == "lh":
            n_lh = len(v)

    v = (np.vstack(verts) - np.asarray(center, np.float64)).astype(np.float32)
    f = np.vstack(faces).astype(np.uint32)
    # FreeSurfer sulc is signed: negative on gyral crowns, positive in sulci, in
    # mm. The viewer's shading ramp reads "mm of depth below the crowns", the
    # fast surface's hull distance, and turns negative values into NaN. Gyri are
    # all crown for that purpose, so clip them to 0.
    depth = np.clip(np.concatenate(sulc), 0.0, None).astype(np.float32)
    s_lo = float(np.percentile(depth, 1))
    s_hi = float(np.percentile(depth, 99))
    span = max(s_hi - s_lo, 1e-6)
    sulc_u8 = np.clip((depth - s_lo) / span * 255.0, 0, 255).astype(np.uint8)

    tri = v[f]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1).sum()

    parcellations = {}
    for atlas in available:
        ids = np.concatenate(per_atlas[atlas][0])
        parcellations[atlas] = {
            "name": ATLASES[atlas][1],
            "parcel": _b64(ids),
            "colors": {str(k): c for k, c in sorted(per_atlas[atlas][1].items())},
            "labels": {str(k): n for k, n in sorted(per_atlas[atlas][2].items())},
            "coverage": round(float((ids > 0).mean()), 4),
        }
    dkt = parcellations.pop("dkt")
    return {
        "schema": SURFACE_SCHEMA,
        "source": "freesurfer",
        "encoding": "base64",
        "vertex_count": int(len(v)),
        "face_count": int(len(f)),
        "lh_vertex_count": int(n_lh),
        "vertices": _b64(v),
        "faces": _b64(f),
        "sulc": _b64(sulc_u8),
        "sulc_range_mm": [s_lo, s_hi],
        "atlas": "dkt",
        "atlas_name": dkt["name"],
        "parcel": dkt["parcel"],
        "parcel_colors": dkt["colors"],
        "parcel_labels": dkt["labels"],
        "parcellations": parcellations,
        "center": [float(c) for c in center],
        "bounds": {"min": v.min(0).tolist(), "max": v.max(0).tolist()},
        "stats": {"area_cm2": round(float(area) / 100.0, 1),
                  "parcel_coverage": dkt["coverage"]},
    }


def resample_labels(label_img, mri_img, orig_to_mri=None, slab=16):
    """Nearest-neighbour labels on mri.nii.gz's grid, as int16.

    Done slab by slab through world coordinates: the MRIs here run to 50+ Mvox,
    and a full coordinate array for one would be over a gigabyte.
    """
    labels = np.asanyarray(label_img.dataobj)
    if labels.ndim > 3:
        labels = labels[..., 0]
    labels = np.rint(labels).astype(np.int16)
    shape = mri_img.shape[:3]
    # target voxel -> mri world -> (orig world) -> label voxel
    m = np.linalg.inv(label_img.affine)
    if orig_to_mri is not None:
        m = m @ np.linalg.inv(orig_to_mri)
    m = m @ mri_img.affine
    out = np.zeros(shape, np.int16)
    src_shape = np.array(labels.shape)
    ii, jj = np.meshgrid(np.arange(shape[0]), np.arange(shape[1]), indexing="ij")
    for k0 in range(0, shape[2], slab):
        k1 = min(k0 + slab, shape[2])
        kk = np.arange(k0, k1)
        n = len(kk)
        ijk = np.stack([np.repeat(ii[..., None], n, 2), np.repeat(jj[..., None], n, 2),
                        np.broadcast_to(kk, ii.shape + (n,))], axis=-1).reshape(-1, 3)
        src = np.rint(_apply(m, ijk.astype(np.float64))).astype(np.int64)
        ok = np.all((src >= 0) & (src < src_shape), axis=1)
        vals = np.zeros(len(src), np.int16)
        g = src[ok]
        vals[ok] = labels[g[:, 0], g[:, 1], g[:, 2]]
        out[:, :, k0:k1] = vals.reshape(shape[0], shape[1], n)
    return out


# ── Provenance ────────────────────────────────────────────────────────────────
def _read_text(path):
    try:
        with open(path, errors="replace") as fh:
            return fh.read().strip()
    except (OSError, TypeError):
        return None


def _provenance(files):
    info = {"engine": None, "engine_version": None, "freesurfer_version": None,
            "subject": None, "processed_at": None}
    manifest = None
    if "fspipe_manifest.json" in files:
        try:
            with open(files["fspipe_manifest.json"]) as fh:
                manifest = json.load(fh)
        except (OSError, ValueError):
            manifest = None
    if manifest:
        info.update(engine=manifest.get("engine"), engine_version=manifest.get("engine_version"),
                    freesurfer_version=manifest.get("freesurfer_version"),
                    subject=manifest.get("subject"), processed_at=manifest.get("finished"),
                    runtime_seconds=manifest.get("runtime_seconds"),
                    gpu=manifest.get("gpu"), qc=manifest.get("qc"),
                    inputs=manifest.get("inputs"))
    if not info["freesurfer_version"]:
        info["freesurfer_version"] = _read_text(files.get("scripts/build-stamp.txt"))
    done = _read_text(files.get("scripts/recon-all.done")) or ""
    for line in done.splitlines():
        m = re.match(r"#(\w+)\s+(.*)", line.strip())
        if not m:
            continue
        key, val = m.groups()
        if key == "SUBJECT" and not info["subject"]:
            info["subject"] = val
        elif key == "END_TIME" and not info["processed_at"]:
            info["processed_at"] = val
        elif key == "VERSION" and not info["freesurfer_version"]:
            info["freesurfer_version"] = val
    if not info["engine"]:
        info["engine"] = "recon-all" if done else None
    return info


# ── Status ────────────────────────────────────────────────────────────────────
def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def write_status(recon_dir, state, message=None):
    path = os.path.join(recon_dir, STATUS_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"state": state, "message": message, "updated_at": _now()}, fh)
    os.replace(tmp, path)


def read_status(recon_dir):
    try:
        with open(os.path.join(recon_dir, STATUS_NAME)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_info(recon_dir):
    try:
        with open(os.path.join(recon_dir, FS_DIR, INFO_NAME)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def summary(recon_dir):
    """What the API reports about a reconstruction's FreeSurfer import, or None."""
    status = read_status(recon_dir)
    info = read_info(recon_dir)
    if status is None and info is None:
        return None
    ready = info is not None and os.path.exists(os.path.join(recon_dir, FS_DIR, SURFACE_NAME))
    return {"state": (status or {}).get("state") or ("ready" if ready else "error"),
            "message": (status or {}).get("message"),
            "updated_at": (status or {}).get("updated_at"),
            "ready": ready,
            "info": info}


# ── The import job ────────────────────────────────────────────────────────────
def import_freesurfer(recon_dir, zip_path, verbose=True):
    """Build everything from an uploaded zip into recon_dir/freesurfer/.

    Works in a staging directory and swaps it in at the end, so a failed upload
    leaves an earlier good import untouched. Does NOT activate the source; the
    caller decides that (and owns the DB side of it).
    """
    import nibabel as nib

    recon_dir = os.path.abspath(recon_dir)
    mri_path = os.path.join(recon_dir, "mri.nii.gz")
    mesh_json = os.path.join(recon_dir, "mesh.json")
    if not os.path.exists(mri_path) or not os.path.exists(mesh_json):
        raise FreeSurferImportError("This reconstruction has no MRI/brain mesh yet")
    with open(mesh_json) as fh:
        center = np.asarray(json.load(fh)["center"], np.float64)

    staging = os.path.join(recon_dir, STAGING_DIR)
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    try:
        files = extract_upload(zip_path, os.path.join(staging, "subject"))
        if verbose:
            print(f"[FS] Extracted {len(files)} files")

        orig = nib.load(files["mri/orig.mgz"])
        mri = nib.load(mri_path)
        ncc = alignment_ncc(orig, mri)
        registered = None
        if verbose:
            print(f"[FS] orig.mgz vs mri.nii.gz correlation {ncc:.3f}")
        if ncc < NCC_ALIGNED:
            print("[FS] Not the same T1 as this reconstruction; registering rigidly")
            registered = _register_orig(orig, mri_path, staging)
            ncc_reg = alignment_ncc(orig, mri, registered)
            print(f"[FS] Correlation after registration {ncc_reg:.3f}")
            if ncc_reg < NCC_MIN_REGISTERED:
                raise FreeSurferImportError(
                    f"These FreeSurfer outputs do not match this reconstruction's MRI "
                    f"(correlation {ncc_reg:.2f} even after registration). Was it run on "
                    "another patient?")
        orig_to_mri = registered if registered is not None else np.eye(4)

        payload = build_surface_payload(files, orig_to_mri, center)
        payload_path = os.path.join(staging, SURFACE_NAME)
        with open(payload_path, "w") as fh:
            json.dump(payload, fh)
        if verbose:
            print(f"[FS] Surface: {payload['vertex_count']} vertices, {payload['face_count']} "
                  f"faces, {payload['stats']['area_cm2']} cm2, atlases "
                  f"{['dkt'] + sorted(payload['parcellations'])}")

        label_img = nib.load(files["mri/aparc.DKTatlas+aseg.mgz"])
        labels = resample_labels(label_img, mri, registered)
        out_img = nib.Nifti1Image(labels, mri.affine)
        out_img.set_data_dtype(np.int16)
        nib.save(out_img, os.path.join(staging, FS_LABELS))
        present = np.unique(labels)
        n_ctx = int(np.sum((present >= 1000) & (present < 3000)))
        if verbose:
            print(f"[FS] Labels on MRI grid: {len(present)} distinct, {n_ctx} cortical")
        if n_ctx < 40:
            raise FreeSurferImportError(
                f"aparc.DKTatlas+aseg has only {n_ctx} cortical labels on this MRI's grid; "
                "the volume does not overlap the reconstruction")

        info = _provenance(files)
        info.update({
            "imported_at": _now(),
            "atlases": ["dkt"] + sorted(payload["parcellations"]),
            "alignment": {"ncc": round(ncc, 4), "registered": registered is not None,
                          "orig_to_mri": registered.tolist() if registered is not None else None},
            "vertex_count": payload["vertex_count"],
            "face_count": payload["face_count"],
            "area_cm2": payload["stats"]["area_cm2"],
            "files": sorted(files),
        })
        with open(os.path.join(staging, INFO_NAME), "w") as fh:
            json.dump(info, fh, indent=2)

        final = os.path.join(recon_dir, FS_DIR)
        shutil.rmtree(final, ignore_errors=True)
        os.replace(staging, final)
        return info
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def import_isolated(recon_dir, zip_path):
    """import_freesurfer in a child process, under HEAVY_JOB_LOCK.

    Resampling 50+ Mvox of labels and building a ~600k-face payload is a few
    hundred MB to a couple of GB of transient allocation; like every other heavy
    stage here it runs in a child so the web process stays small. Returns info.
    """
    recon_dir = os.path.abspath(recon_dir)
    if getattr(sys, "frozen", False):
        # Under PyInstaller sys.executable is the app, not python.
        with HEAVY_JOB_LOCK:
            return import_freesurfer(recon_dir, zip_path)

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    err_path = os.path.join(recon_dir, "freesurfer_error.txt")
    try:
        os.remove(err_path)
    except OSError:
        pass
    cmd = [sys.executable, os.path.abspath(__file__), recon_dir, os.path.abspath(zip_path)]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [backend_dir, env["PYTHONPATH"]] if env.get("PYTHONPATH") else [backend_dir])
    if not HEAVY_JOB_LOCK.acquire(blocking=False):
        print("[FS] Another heavy job is running; waiting for it to finish")
        HEAVY_JOB_LOCK.acquire()
    try:
        print(f"[FS] Spawning import worker (container limit {describe_limit()})")
        returncode, peak = run_worker(cmd, cwd=backend_dir, env=env)
    finally:
        HEAVY_JOB_LOCK.release()
    print(f"[FS] {describe_outcome(returncode, peak)}")
    if returncode != 0:
        msg = _read_text(err_path)
        try:
            os.remove(err_path)
        except OSError:
            pass
        if msg:
            raise FreeSurferImportError(msg)
        raise RuntimeError(f"FreeSurfer import {describe_outcome(returncode, peak)}")
    info = read_info(recon_dir)
    if info is None:
        raise RuntimeError("FreeSurfer import worker exited 0 but wrote no info.json")
    return info


# ── Source switching ──────────────────────────────────────────────────────────
def active_source(recon_dir):
    """Which parcellation structures_cortical.nii.gz currently holds."""
    return "freesurfer" if (_read_text(os.path.join(recon_dir, SOURCE_MARKER)) or "") \
        .strip() == "freesurfer" else "fast"


def _same_file(a, b):
    import hashlib
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    digests = []
    for p in (a, b):
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        digests.append(h.digest())
    return digests[0] == digests[1]


def _invalidate_derived(recon_dir):
    """Drop everything built from the label volume. Structure meshes are only
    trusted via their manifest, which would otherwise vouch for meshes of the
    previous parcellation; the fast cortical surface is fingerprinted, but
    removing it is cheaper than relying on that."""
    shutil.rmtree(os.path.join(recon_dir, "structures"), ignore_errors=True)
    try:
        os.remove(os.path.join(recon_dir, "cortical_surface.json"))
    except OSError:
        pass


def activate_source(recon_dir, source):
    """Put `source`'s labels in structures_cortical.nii.gz. Idempotent, and each
    step leaves a state the next call can finish from."""
    if source not in SOURCES:
        raise ValueError(f"unknown parcellation source {source!r}")
    active = os.path.join(recon_dir, ACTIVE_LABELS)
    backup = os.path.join(recon_dir, FAST_BACKUP)
    marker = os.path.join(recon_dir, SOURCE_MARKER)
    current = active_source(recon_dir)

    if source == "freesurfer":
        fs_labels = os.path.join(recon_dir, FS_DIR, FS_LABELS)
        if not os.path.exists(fs_labels):
            raise FreeSurferImportError("No FreeSurfer import is ready for this reconstruction")
        if current == "fast" and os.path.exists(active) and not os.path.exists(backup):
            os.replace(active, backup)
        tmp = active + ".tmp"
        shutil.copyfile(fs_labels, tmp)
        os.replace(tmp, active)
        with open(marker, "w") as fh:
            fh.write("freesurfer\n")
    else:
        fs_labels = os.path.join(recon_dir, FS_DIR, FS_LABELS)
        if os.path.exists(backup):
            os.replace(backup, active)
        elif current == "freesurfer" and os.path.exists(active) \
                and (not os.path.exists(fs_labels) or _same_file(active, fs_labels)):
            # No fast labels were ever computed; let the fast pipeline do it on
            # demand rather than leave FreeSurfer's labels posing as its own.
            # Only ever delete a copy of FreeSurfer's file: a switch interrupted
            # after restoring the backup leaves the marker behind, and the file
            # it points at is then the real (expensive) fast parcellation.
            os.remove(active)
        try:
            os.remove(marker)
        except OSError:
            pass
    _invalidate_derived(recon_dir)
    return source


def remove(recon_dir):
    """Delete the import and fall back to the fast source."""
    if active_source(recon_dir) == "freesurfer":
        activate_source(recon_dir, "fast")
    shutil.rmtree(os.path.join(recon_dir, FS_DIR), ignore_errors=True)
    shutil.rmtree(os.path.join(recon_dir, STAGING_DIR), ignore_errors=True)
    for name in (STATUS_NAME, UPLOAD_NAME):
        try:
            os.remove(os.path.join(recon_dir, name))
        except OSError:
            pass


def load_surface(recon_dir):
    """The imported surface payload with DKT colours refreshed from the catalog
    (same rule as the fast surface: recolouring must not need a re-import)."""
    path = os.path.join(recon_dir, FS_DIR, SURFACE_NAME)
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        payload = json.load(fh)
    colors, _ = _catalog()
    for key in payload.get("parcel_colors", {}):
        if int(key) in colors:
            payload["parcel_colors"][key] = colors[int(key)]
    return payload


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: freesurfer_import.py <recon_dir> <upload.zip>", file=sys.stderr)
        raise SystemExit(2)
    _recon_dir, _zip = sys.argv[1], sys.argv[2]
    try:
        _info = import_freesurfer(_recon_dir, _zip)
        print(f"[FS] Import complete: {_info['vertex_count']} vertices, "
              f"alignment {_info['alignment']}")
    except FreeSurferImportError as exc:
        # A message for the user: hand it to the parent through a file, since
        # only the exit code crosses the process boundary.
        with open(os.path.join(_recon_dir, "freesurfer_error.txt"), "w") as fh:
            fh.write(str(exc))
        print(f"[FS] Import rejected: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(f"[FS] Import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
