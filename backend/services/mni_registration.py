"""
MNI152 normalization — first step of the results-export pipeline.

Registers a completed reconstruction (patient MRI, post-implant CT, and
electrode localization) into MNI152 standard space so contacts can be
compared across patients and labeled against standard atlases.

Pipeline
--------
1. MRI -> MNI152 via ANTs affine + SyN nonlinear (``type_of_transform="SyNRA"``).
   The forward/inverse transforms are persisted for reuse by later export steps.
2. CT -> MNI: the CT is first resampled into MRI space using the existing rigid
   ``ct_to_mri.npy`` transform, then warped into MNI with the same MRI->MNI transforms.
3. Electrode contacts (already stored in MRI-space world RAS) are pushed through
   the MRI->MNI transform as points and written as an MNI coordinate table.
4. Each contact is labeled with the patient-specific brain structure it sits in
   (or the nearest one, with a distance) -> ``electrodes_structures.csv``. This
   step is native-space and uses the cached DKT segmentation, not MNI.

Coordinate conventions
-----------------------
- Contacts / meshes in this app are in **RAS** world mm.
- ANTs / ITK work in **LPS**. ``ants.image_read`` / ``to_filename`` handle the
  RAS<->LPS flip for *images* automatically, but ``apply_transforms_to_points``
  does **not** — points must be flipped to LPS on the way in and back to RAS out
  (negate x and y). See ``_ras_to_lps`` / ``_lps_to_ras``.
- Point mapping runs in the **opposite** direction from image warping: to send an
  MRI-space point into MNI we apply the *inverse* transform list. See
  ``transform_contacts_to_mni``.

All heavy ANTs/nibabel imports are done lazily inside functions; ANTs is forced
onto CPU (GPU gives no speedup for this pipeline — see structure_extractor).
"""

import os

# Hide any GPU before ANTs / TensorFlow import them (CPU-only, deterministic).
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

import json
import shutil
import time
from datetime import datetime

import numpy as np


# ── MNI152 template ────────────────────────────────────────────────────────────

_MNI_TEMPLATE_CACHE = None


def get_mni_template_path() -> str:
    """
    Resolve a path to the MNI152 T1 template NIfTI.

    Primary: ``ants.get_ants_data('mni')`` — a whole-head MNI152 T1 that ships
    with ANTs (downloaded/cached on first use), a good match for the full-head
    patient MRI stored by this app.
    Fallback: antspynet ``get_antsxnet_data('mni152')`` (brain-extracted).
    """
    global _MNI_TEMPLATE_CACHE
    if _MNI_TEMPLATE_CACHE and os.path.exists(_MNI_TEMPLATE_CACHE):
        return _MNI_TEMPLATE_CACHE

    import ants
    try:
        path = ants.get_ants_data("mni")
    except Exception as e:
        print(f"[MNI] ants.get_ants_data('mni') failed ({e}); trying antspynet...")
        from antspynet.utilities import get_antsxnet_data
        path = get_antsxnet_data("mni152")

    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"Could not resolve MNI152 template (got {path!r})")
    _MNI_TEMPLATE_CACHE = path
    print(f"[MNI] Template: {path}")
    return path


# ── Coordinate helpers ─────────────────────────────────────────────────────────

def _ras_to_lps(pts: np.ndarray) -> np.ndarray:
    """Flip RAS world points to LPS (negate x, y). pts: (N, 3)."""
    out = np.asarray(pts, dtype=np.float64).copy()
    out[:, 0] *= -1.0
    out[:, 1] *= -1.0
    return out


def _lps_to_ras(pts: np.ndarray) -> np.ndarray:
    """Flip LPS world points back to RAS (negate x, y). pts: (N, 3)."""
    return _ras_to_lps(pts)  # involution — same operation


# ── Step 1: MRI -> MNI ──────────────────────────────────────────────────────────

def register_mri_to_mni(mri_path: str, out_dir: str) -> dict:
    """
    Register the patient MRI to MNI152 (affine + SyN nonlinear).

    Writes ``MRI_mni.nii.gz`` and persists the transforms into ``out_dir`` as
    ``mri_to_mni_warp.nii.gz``, ``mri_to_mni_affine.mat``, ``mni_to_mri_invwarp.nii.gz``.

    Returns the raw ANTs result dict (contains in-memory ``fwdtransforms`` /
    ``invtransforms`` temp paths, valid for the remainder of this export run).
    """
    import ants

    os.makedirs(out_dir, exist_ok=True)
    mni = ants.image_read(get_mni_template_path())
    mri = ants.image_read(mri_path)

    print("[MNI] Running ANTs affine + SyN (SyNRA) MRI->MNI (this can take several minutes)...")
    reg = ants.registration(fixed=mni, moving=mri, type_of_transform="SyNRA", verbose=False)

    warped = reg["warpedmovout"]
    warped.to_filename(os.path.join(out_dir, "MRI_mni.nii.gz"))

    # Persist transforms for later export steps / re-runs.
    # fwdtransforms = [warp, affine]  (maps MNI->MRI points; warps MRI image into MNI)
    # invtransforms = [affine, inverseWarp]
    fwd = reg["fwdtransforms"]
    inv = reg["invtransforms"]
    try:
        shutil.copy(fwd[0], os.path.join(out_dir, "mri_to_mni_warp.nii.gz"))
        shutil.copy(fwd[1], os.path.join(out_dir, "mri_to_mni_affine.mat"))
        shutil.copy(inv[-1], os.path.join(out_dir, "mni_to_mri_invwarp.nii.gz"))
    except Exception as e:
        print(f"[MNI] WARNING: could not persist transform files: {e}")

    print("[MNI] MRI->MNI registration complete.")
    return reg


# ── Step 2: CT -> MNI ───────────────────────────────────────────────────────────

def _resample_ct_into_mri_grid(ct_path: str, mri_path: str, ct_to_mri_ras: np.ndarray):
    """
    Resample the CT onto the MRI voxel grid using the rigid CT->MRI (RAS) transform.
    Returns a nibabel image on the MRI grid (HU, air = -1000 outside FOV).
    """
    import nibabel as nib
    from scipy.ndimage import map_coordinates

    ct = nib.load(ct_path)
    mri = nib.load(mri_path)
    ct_data = ct.get_fdata()
    nx, ny, nz = mri.shape[:3]

    # MRI voxel indices -> MRI world RAS -> CT world RAS -> CT voxel indices
    ii, jj, kk = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing="ij")
    vox = np.stack([ii.ravel(), jj.ravel(), kk.ravel(), np.ones(ii.size)], axis=0).astype(np.float64)
    mri_world = mri.affine @ vox
    ct_world = np.linalg.inv(ct_to_mri_ras) @ mri_world
    ct_vox = np.linalg.inv(ct.affine) @ ct_world

    sampled = map_coordinates(ct_data, ct_vox[:3], order=1, mode="constant", cval=-1000.0)
    out = sampled.reshape((nx, ny, nz)).astype(np.float32)
    return nib.Nifti1Image(out, mri.affine)


# ── Step 3: electrodes -> MNI ───────────────────────────────────────────────────

def transform_contacts_to_mni(contacts_world_ras: np.ndarray, reg: dict) -> np.ndarray:
    """
    Map MRI-space electrode points (RAS world mm) into MNI152 RAS world mm.

    Points travel the OPPOSITE direction from images: to send a moving-space
    (MRI) point into the fixed (MNI) space we apply the inverse transform list
    with the affine inverted.

    contacts_world_ras: (N, 3) RAS mm.  Returns (N, 3) MNI RAS mm.
    """
    import ants
    import pandas as pd

    if len(contacts_world_ras) == 0:
        return np.zeros((0, 3))

    lps = _ras_to_lps(contacts_world_ras)
    df = pd.DataFrame({"x": lps[:, 0], "y": lps[:, 1], "z": lps[:, 2]})

    # invtransforms = [affine, inverseWarp]; invert the affine for the point map.
    inv = reg["invtransforms"]
    whichtoinvert = [True] + [False] * (len(inv) - 1)
    warped = ants.apply_transforms_to_points(
        3, df, transformlist=inv, whichtoinvert=whichtoinvert
    )
    out_lps = warped[["x", "y", "z"]].to_numpy()
    return _lps_to_ras(out_lps)


# ── Orchestrator ────────────────────────────────────────────────────────────────

def export_reconstruction_to_mni(recon_dir: str, mri_path: str, ct_path: str,
                                 ct_to_mri_npy: str, contacts: list,
                                 mesh_center) -> dict:
    """
    Full MNI export for one reconstruction.

    Args:
        recon_dir:      per-reconstruction data dir; artifacts land in ``recon_dir/export/``
        mri_path:       absolute path to patient MRI NIfTI
        ct_path:        absolute path to CT NIfTI (or None)
        ct_to_mri_npy:  absolute path to ``ct_to_mri.npy`` (or None)
        contacts:       list of dicts with shaft_name, contact_number, x_mm/y_mm/z_mm
                        (already filtered to placed contacts, MRI-space mesh-centered)
        mesh_center:    (3,) brain-mesh center in world RAS (from mesh.json ``center``)

    Returns a manifest dict (also written to ``export/export_manifest.json``).
    """
    import ants
    import nibabel as nib

    out_dir = os.path.join(recon_dir, "export")
    os.makedirs(out_dir, exist_ok=True)
    center = np.asarray(mesh_center, dtype=np.float64).reshape(3)

    # Phase timings land in the manifest so slow runs / regressions are visible
    # without re-instrumenting. SyN registration dominates; everything else is seconds.
    t_start = time.perf_counter()
    durations = {}

    # Step 1: MRI -> MNI
    _t = time.perf_counter()
    reg = register_mri_to_mni(mri_path, out_dir)
    durations["mri_to_mni"] = round(time.perf_counter() - _t, 1)
    print(f"[MNI] MRI->MNI took {durations['mri_to_mni']:.1f}s")

    # Step 2: CT -> MNI (optional)
    ct_written = False
    _t = time.perf_counter()
    if ct_path and os.path.exists(ct_path) and ct_to_mri_npy and os.path.exists(ct_to_mri_npy):
        try:
            ct_to_mri_ras = np.load(ct_to_mri_npy)
            ct_in_mri = _resample_ct_into_mri_grid(ct_path, mri_path, ct_to_mri_ras)
            ct_in_mri_path = os.path.join(out_dir, "_ct_in_mri.nii.gz")
            ct_in_mri.to_filename(ct_in_mri_path)
            ct_in_mri_ants = ants.image_read(ct_in_mri_path)
            mni = ants.image_read(get_mni_template_path())
            ct_mni = ants.apply_transforms(
                fixed=mni, moving=ct_in_mri_ants,
                transformlist=reg["fwdtransforms"], interpolator="linear",
                defaultvalue=-1000.0,
            )
            ct_mni.to_filename(os.path.join(out_dir, "CT_mni.nii.gz"))
            os.remove(ct_in_mri_path)
            ct_written = True
            print("[MNI] CT warped into MNI space.")
        except Exception as e:
            print(f"[MNI] WARNING: CT->MNI failed ({e}); continuing without CT_mni")
    durations["ct_to_mni"] = round(time.perf_counter() - _t, 1) if ct_written else None

    # Step 3: electrodes -> MNI
    _t = time.perf_counter()
    placed = [c for c in contacts if c.get("x_mm") is not None]
    world = np.array([[c["x_mm"] + center[0],
                       c["y_mm"] + center[1],
                       c["z_mm"] + center[2]] for c in placed], dtype=np.float64) \
        if placed else np.zeros((0, 3))
    mni_pts = transform_contacts_to_mni(world, reg) if len(world) else np.zeros((0, 3))

    rows = []
    for c, p in zip(placed, mni_pts):
        rows.append({
            "shaft_name": c.get("shaft_name", ""),
            "contact_number": c.get("contact_number"),
            "x_mni": round(float(p[0]), 3),
            "y_mni": round(float(p[1]), 3),
            "z_mni": round(float(p[2]), 3),
            "x_native_mm": round(float(c["x_mm"]), 3),
            "y_native_mm": round(float(c["y_mm"]), 3),
            "z_native_mm": round(float(c["z_mm"]), 3),
        })

    # CSV (spec columns) + JSON (with native coords for traceability)
    csv_path = os.path.join(out_dir, "electrodes_mni.csv")
    with open(csv_path, "w", newline="") as f:
        import csv as _csv
        w = _csv.writer(f)
        w.writerow(["shaft_name", "contact_number", "x_mni", "y_mni", "z_mni"])
        for r in rows:
            w.writerow([r["shaft_name"], r["contact_number"], r["x_mni"], r["y_mni"], r["z_mni"]])
    with open(os.path.join(out_dir, "electrodes_mni.json"), "w") as f:
        json.dump(rows, f, indent=2)

    # Sanity self-check (surfaces flips/offsets in logs; not a hard gate)
    if len(mni_pts):
        inb = np.mean((np.abs(mni_pts[:, 0]) <= 95) &
                      (mni_pts[:, 1] >= -130) & (mni_pts[:, 1] <= 95) &
                      (mni_pts[:, 2] >= -80) & (mni_pts[:, 2] <= 110))
        n_left = int(np.sum(mni_pts[:, 0] < 0))
        print(f"[MNI] Electrodes: {len(mni_pts)} contacts, "
              f"{inb*100:.0f}% inside MNI bounding box, "
              f"{n_left} left-hemisphere (x<0), {len(mni_pts)-n_left} right (x>=0).")
        if inb < 0.9:
            print("[MNI] WARNING: many contacts fell outside the MNI bounding box - "
                  "check RAS/LPS flip and point-transform direction.")

    durations["electrodes"] = round(time.perf_counter() - _t, 1)

    # Step 4: contact -> brain-structure labels (native space, patient-specific).
    # Uses the DKT label volume cached by structure_extractor; if structures have
    # never been computed for this reconstruction we generate them here (slow,
    # ~2-3 min, but cached afterwards and reused by the viewer).
    _t = time.perf_counter()
    structure_summary = None
    try:
        from services.contact_labeling import (
            get_label_volume_path, label_contacts, write_contact_structure_csv,
        )
        label_path = get_label_volume_path(recon_dir)
        if not os.path.exists(label_path):
            print("[MNI] Structure labels not cached - running segmentation "
                  "(first time only, this takes a few minutes)...")
            from services.structure_extractor import extract_all_structures
            extract_all_structures(os.path.join(recon_dir, "mesh.json"), recon_dir, mri_path)

        if os.path.exists(label_path) and len(world):
            labels = label_contacts(label_path, world)
            structure_summary = write_contact_structure_csv(
                os.path.join(out_dir, "electrodes_structures.csv"), placed, labels
            )
            print(f"[MNI] Contact structures: {structure_summary['inside_structure']} inside a "
                  f"structure, {structure_summary['near_structure']} near one, "
                  f"{structure_summary['unassigned']} unassigned "
                  f"(of {len(labels)} contacts).")
        elif not os.path.exists(label_path):
            print("[MNI] WARNING: no structure label volume - skipping electrodes_structures.csv")
    except Exception as e:
        print(f"[MNI] WARNING: contact structure labeling failed ({e}); "
              "continuing without electrodes_structures.csv")
    durations["structures"] = round(time.perf_counter() - _t, 1)

    durations["total"] = round(time.perf_counter() - t_start, 1)

    try:
        ants_version = ants.__version__
    except Exception:
        ants_version = "unknown"

    manifest = {
        "template": get_mni_template_path(),
        "ants_version": ants_version,
        "transform_type": "SyNRA",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "n_contacts": len(rows),
        "has_ct_mni": ct_written,
        "has_structure_labels": structure_summary is not None,
        "contact_structures": structure_summary,
        "durations_sec": durations,
        "artifacts": sorted(
            fn for fn in os.listdir(out_dir) if not fn.startswith("_")
        ),
    }
    with open(os.path.join(out_dir, "export_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    # NOTE: keep log output ASCII-only. Under uvicorn on Windows stdout is cp1252,
    # and a non-ASCII character here raises UnicodeEncodeError *after* all artifacts
    # are written -- failing the whole export on a cosmetic log line.
    print(f"[MNI] Export complete in {durations['total']:.1f}s -> {out_dir}")
    return manifest
