"""
MNI152 normalization — first step of the results-export pipeline.

Brings a completed reconstruction (patient MRI, post-implant CT, and electrode
localization) into MNI152 standard space so contacts can be compared across
patients and labeled against standard atlases.

Target
------
ICBM 2009c nonlinear asymmetric MNI152, 1 mm (193 x 229 x 193), bundled in
``backend/assets/mni_icbm152_2009c/``. It is the template FreeSurfer 8's
recon-all registers every subject to, which is what makes method 1 free.

Two ways to the template, one way out
-------------------------------------
1. **SynthMorph (preferred).** recon-all already registered the subject's
   ``orig.mgz`` to this template with SynthMorph (affine + deformable) and left
   the forward and inverse displacement fields in
   ``mri/transforms/synthmorph.1.0mm.1.0mm/``; the FreeSurfer import keeps that
   folder. Nothing is registered at export time. See ``synthmorph_transforms``.
2. **ANTs brain -> brain (fallback)** when there is no such folder (no
   FreeSurfer import, FastSurfer, older FreeSurfer): ``antsRegistrationSyN[s]``
   (rigid + affine + SyN at full resolution) of the skull-stripped patient MRI
   (the antspynet mask the brain mesh is built from) to the skull-stripped
   template. About 4.5 minutes. See ``register_brain_to_mni``.

Measured on 8 patients against the template's own SynthSeg labels (2026-11-28):
the old whole-head ``SyNRA`` -> FSL MNI152 export put 34 % of contacts in the
same structure as in the patient and failed outright on 6 of 34 patients
(11-21 mm); SynthMorph and ANTs brain -> brain both reach 61 %, with deep-nuclei
Dice 0.85 (was 0.33).

Either way the export writes ITK/ANTs displacement fields
(``mri_to_mni_warp.nii.gz`` on the template grid, ``mni_to_mri_invwarp.nii.gz``
on a patient grid) and the same code warps the MRI and CT and maps the contacts.

Coordinate conventions
-----------------------
- Contacts / meshes in this app are in **RAS** world mm. FreeSurfer's fields and
  LTAs are RAS too.
- ANTs / ITK work in **LPS**. ``ants.image_read`` / ``to_filename`` handle the
  RAS<->LPS flip for *images* automatically, but ``apply_transforms_to_points``
  does **not** — points must be flipped to LPS on the way in and back to RAS out
  (negate x and y). Displacement vectors in ITK field files are LPS as well.
- Point mapping runs in the **opposite** direction from image warping: the field
  that pulls the patient image into MNI (defined on the MNI grid) maps MNI points
  to the patient; patient points go to MNI through the inverse field.

All heavy ANTs/nibabel imports are done lazily inside functions; ANTs is forced
onto CPU (GPU gives no speedup for this pipeline — see structure_extractor).
"""

import os
import sys

# Hide any GPU before ANTs / TensorFlow import them (CPU-only, deterministic).
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

import json
import shutil
import time
from datetime import datetime

import numpy as np


# ── MNI152 template ────────────────────────────────────────────────────────────

_BACKEND = (sys._MEIPASS if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE_NAME = "ICBM 2009c nonlinear asymmetric MNI152, 1 mm (193x229x193)"
TEMPLATE_BRAIN = os.path.join(_BACKEND, "assets", "mni_icbm152_2009c",
                              "mni_icbm152_t1_tal_nlin_asym_09c_brain.nii.gz")


def get_mni_template_path() -> str:
    """The bundled skull-stripped ICBM 2009c T1: the grid every MNI image is
    written on, and the fixed image of the ANTs fallback."""
    if not os.path.exists(TEMPLATE_BRAIN):
        raise FileNotFoundError(f"MNI152 2009c template missing at {TEMPLATE_BRAIN}")
    return TEMPLATE_BRAIN


# Every file an export may write. A re-run removes the ones it did not write
# this time (a SynthMorph export has no affine; a failed CT step leaves no CT),
# so the download never mixes two runs.
ARTIFACTS = (
    "MRI_mni.nii.gz", "CT_mni.nii.gz",
    "electrodes_mni.csv", "electrodes_mni.json", "electrodes_structures.csv",
    "mri_to_mni_warp.nii.gz", "mri_to_mni_affine.mat", "mni_to_mri_invwarp.nii.gz",
    "export_manifest.json",
)


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


def _apply(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Apply a 4x4 affine to (N, 3) points."""
    return pts @ m[:3, :3].T + m[:3, 3]


def _grid_slab(shape, affine, k0, k1):
    """(voxel indices (N, 3) int, world RAS (N, 3)) for the slab k0 <= k < k1,
    in C order of an array[:, :, k0:k1]."""
    i, j, k = np.meshgrid(np.arange(shape[0]), np.arange(shape[1]),
                          np.arange(k0, k1), indexing="ij")
    vox = np.stack([i.ravel(), j.ravel(), k.ravel()], axis=1)
    return vox, _apply(affine, vox.astype(np.float64))


def _write_itk_field(path: str, disp_ras: np.ndarray, affine: np.ndarray):
    """Write an (X, Y, Z, 3) RAS displacement field (mm) as an ITK/ANTs
    displacement-field NIfTI: 5D (X, Y, Z, 1, 3), intent "vector", LPS
    components -- the layout ANTs writes its own warps in. Flips disp_ras in place."""
    import nibabel as nib

    disp_ras[..., 0] *= -1.0
    disp_ras[..., 1] *= -1.0
    img = nib.Nifti1Image(disp_ras[:, :, :, None, :], affine)
    img.header.set_intent("vector", (), "")
    img.header.set_xyzt_units("mm")
    img.header.set_qform(affine, 1)
    img.header.set_sform(affine, 1)
    nib.save(img, path)


# ── Method 1: recon-all's SynthMorph registration ──────────────────────────────

SYNTHMORPH_DIR = ("freesurfer", "subject", "mri", "transforms", "synthmorph.1.0mm.1.0mm")
SM_FWD = "warp.to.mni152.1.0mm.1.0mm.nii.gz"       # on the template grid: MNI -> orig
SM_INV = "warp.to.mni152.1.0mm.1.0mm.inv.nii.gz"   # on orig.mgz's grid: orig -> MNI
SM_LTA = "reg.invol_to_targ.lta"                    # the affine part, orig RAS -> MNI RAS


def find_synthmorph(recon_dir: str):
    """Paths to the SynthMorph fields + affine kept by the FreeSurfer import, and
    the orig -> mri.nii.gz transform (identity unless the import had to register
    a different T1). None when any file is missing."""
    d = os.path.join(recon_dir, *SYNTHMORPH_DIR)
    paths = {"fwd": os.path.join(d, SM_FWD), "inv": os.path.join(d, SM_INV),
             "lta": os.path.join(d, SM_LTA)}
    if not all(os.path.exists(p) for p in paths.values()):
        return None
    from services.freesurfer_import import read_info
    m = ((read_info(recon_dir) or {}).get("alignment") or {}).get("orig_to_mri")
    paths["orig_to_mri"] = np.asarray(m, dtype=np.float64) if m is not None else np.eye(4)
    return paths


def read_lta(path: str) -> np.ndarray:
    """The 4x4 matrix of a FreeSurfer LTA, which must be RAS -> RAS."""
    lines = open(path).read().splitlines()
    kind = next((ln.split("=", 1)[1].split()[0] for ln in lines
                 if ln.strip().startswith("type")), None)
    if kind != "1":
        raise ValueError(f"{path}: expected a LINEAR_RAS_TO_RAS LTA (type 1), got type {kind}")
    i = next(k for k, ln in enumerate(lines) if ln.split() == ["1", "4", "4"])
    return np.array([[float(x) for x in lines[i + 1 + r].split()] for r in range(4)])


def _load_field(path: str):
    """A FreeSurfer displacement field as ((X, Y, Z, 3) float32 RAS mm, affine)."""
    import nibabel as nib

    img = nib.load(path)
    f = np.asarray(img.dataobj, dtype=np.float32)
    if f.ndim == 5:
        f = f[:, :, :, 0, :]
    if f.ndim != 4 or f.shape[-1] != 3:
        raise ValueError(f"{path}: not a 3-component displacement field (shape {img.shape})")
    return np.ascontiguousarray(f), img.affine


_SLAB = 24          # z-slices per chunk when walking a field
_FACE_TOL = 2e-3    # voxels; "clamped onto the crop box" detection


def synthmorph_transforms(sm: dict, out_dir: str) -> dict:
    """
    Turn recon-all's SynthMorph fields into the export's two ITK fields.

    FreeSurfer stores RAS displacements: the forward field (template grid) gives,
    for each MNI point q, the orig.mgz point q + fwd(q); the inverse (orig grid)
    gives, for each orig point o, the MNI point o + inv(o).

    SynthMorph only solves inside a crop of the template (about x -76..75,
    y -112..76, z -76..85 mm). Outside it the forward field sends every voxel to
    one sentinel point (orig voxel 0) and the inverse clamps its result onto the
    crop box. That is most of the scalp, and about 2.5 % of contacts (nearly all
    extracranial). Both fields are completed there with the affine plus the
    nonlinear residual at the nearest point of the box:

        forward  src(q) = A^-1 q + [src(b) - A^-1 b],      b = box point nearest q
        inverse  mni(o) = A o    + [b - A src(b)],          b = box point nearest A o

    (A = reg.invol_to_targ.lta). Both are continuous at the box. Contacts that
    land there are flagged ``synthmorph_affine_extrapolated``.

    Writes ``mri_to_mni_warp.nii.gz`` and ``mni_to_mri_invwarp.nii.gz`` (each a
    complete transform on its own -- no separate affine) and returns
    ``{"method", "fwdtransforms", "map_points", "crop_box_mni", ...}``.
    """
    from scipy.ndimage import map_coordinates

    A = read_lta(sm["lta"])                 # orig RAS -> MNI RAS
    A_inv = np.linalg.inv(A)
    M = sm["orig_to_mri"]                   # orig RAS -> mri.nii.gz RAS
    M_inv = np.linalg.inv(M)

    # ── forward field: source point in orig for every template voxel ──────────
    src, aff_f = _load_field(sm["fwd"])     # becomes positions, in place
    shape_f = src.shape[:3]
    for k0 in range(0, shape_f[2], _SLAB):
        k1 = min(k0 + _SLAB, shape_f[2])
        _, world = _grid_slab(shape_f, aff_f, k0, k1)
        src[:, :, k0:k1] += world.reshape(shape_f[0], shape_f[1], k1 - k0, 3).astype(np.float32)

    # Crop box = voxels NOT sent to the sentinel. Opposite corners of the grid
    # going to one point can only be the sentinel; otherwise nothing was cropped.
    if np.allclose(src[0, 0, 0], src[-1, -1, -1], atol=1e-2):
        valid = ~np.all(np.abs(src - src[0, 0, 0]) < 1e-2, axis=-1)
    else:
        valid = np.ones(shape_f, dtype=bool)
    if not valid.any():
        raise ValueError("SynthMorph forward field has no valid voxels")
    lo = np.array([np.flatnonzero(valid.any(axis=tuple(a for a in range(3) if a != d)))[0]
                   for d in range(3)])
    hi = np.array([np.flatnonzero(valid.any(axis=tuple(a for a in range(3) if a != d)))[-1]
                   for d in range(3)])
    n_box = int(np.prod(hi - lo + 1))
    if int(valid.sum()) != n_box:
        print(f"[MNI] WARNING: SynthMorph's valid region is not a box "
              f"({int(valid.sum())} of {n_box} voxels); extrapolating from its bounding box")
    del valid
    box_lo, box_hi = _apply(aff_f, lo[None].astype(float))[0], _apply(aff_f, hi[None].astype(float))[0]

    # Complete the forward field outside the box (src[box voxels] are untouched,
    # so reading them while writing the others is safe).
    for k0 in range(0, shape_f[2], _SLAB):
        k1 = min(k0 + _SLAB, shape_f[2])
        vox, world = _grid_slab(shape_f, aff_f, k0, k1)
        vc = np.clip(vox, lo, hi)
        out = np.any(vc != vox, axis=1)
        if out.any():
            vo, vco = vox[out], vc[out]
            bc = _apply(aff_f, vco.astype(np.float64))
            resid = src[vco[:, 0], vco[:, 1], vco[:, 2]] - _apply(A_inv, bc)
            src[vo[:, 0], vo[:, 1], vo[:, 2]] = _apply(A_inv, world[out]) + resid

    src_c = [np.ascontiguousarray(src[..., d]) for d in range(3)]

    def src_at(u):
        """Trilinear source position (orig RAS) at template voxel coords u (N, 3)."""
        return np.stack([map_coordinates(src_c[d], u.T, order=1, mode="nearest")
                         for d in range(3)], axis=1)

    def nonlinear_residual_mni(qa):
        """b - A src(b) at the box point b nearest each MNI point qa (N, 3)."""
        uc = np.clip(_apply(np.linalg.inv(aff_f), qa), lo, hi)
        b = _apply(aff_f, uc)
        return b - _apply(A, src_at(uc).astype(np.float64))

    # ── inverse field: MNI point for every orig voxel ──────────────────────────
    mni, aff_i = _load_field(sm["inv"])     # becomes MNI positions, in place
    shape_i = mni.shape[:3]
    clamped = np.zeros(shape_i, dtype=bool)
    aff_f_inv = np.linalg.inv(aff_f)
    for k0 in range(0, shape_i[2], _SLAB):
        k1 = min(k0 + _SLAB, shape_i[2])
        _, o = _grid_slab(shape_i, aff_i, k0, k1)
        q = o + mni[:, :, k0:k1].reshape(-1, 3)
        u = _apply(aff_f_inv, q)
        face = np.any((np.abs(u - lo) < _FACE_TOL) | (np.abs(u - hi) < _FACE_TOL), axis=1)
        if face.any():
            qa = _apply(A, o[face])
            q[face] = qa + nonlinear_residual_mni(qa)
        mni[:, :, k0:k1] = q.reshape(shape_i[0], shape_i[1], k1 - k0, 3)
        clamped[:, :, k0:k1] = face.reshape(shape_i[0], shape_i[1], k1 - k0)
    print(f"[MNI] SynthMorph crop box x {box_lo[0]:.0f}..{box_hi[0]:.0f}, "
          f"y {box_lo[1]:.0f}..{box_hi[1]:.0f}, z {box_lo[2]:.0f}..{box_hi[2]:.0f} mm; "
          f"{100 * clamped.mean():.0f}% of the orig grid extrapolated")

    def map_points(c_mri: np.ndarray):
        """mri.nii.gz RAS points -> (MNI RAS points, extrapolated flags)."""
        c_mri = np.asarray(c_mri, dtype=np.float64).reshape(-1, 3)
        if not len(c_mri):
            return np.zeros((0, 3)), np.zeros(0, dtype=bool)
        o = _apply(M_inv, c_mri)
        v = _apply(np.linalg.inv(aff_i), o)
        inside = np.all((v >= 0) & (v <= np.array(shape_i) - 1), axis=1)
        q = np.empty_like(o)
        if inside.any():
            q[inside] = np.stack([map_coordinates(mni[..., d], v[inside].T, order=1)
                                  for d in range(3)], axis=1)
        flag = ~inside
        if inside.any():
            vi = np.round(v[inside]).astype(int)
            flag[inside] = clamped[vi[:, 0], vi[:, 1], vi[:, 2]]
        if (~inside).any():                 # off the orig grid: affine + residual
            qa = _apply(A, o[~inside])
            q[~inside] = qa + nonlinear_residual_mni(qa)
        return q, flag

    # ── write the ITK fields (in mri.nii.gz's frame) ───────────────────────────
    fwd_path = os.path.join(out_dir, "mri_to_mni_warp.nii.gz")
    inv_path = os.path.join(out_dir, "mni_to_mri_invwarp.nii.gz")
    # forward: template voxel q -> mri point M src(q); displacement M src(q) - q
    disp = np.empty_like(src)
    for k0 in range(0, shape_f[2], _SLAB):
        k1 = min(k0 + _SLAB, shape_f[2])
        _, world = _grid_slab(shape_f, aff_f, k0, k1)
        s = _apply(M, src[:, :, k0:k1].reshape(-1, 3).astype(np.float64))
        disp[:, :, k0:k1] = (s - world).reshape(shape_f[0], shape_f[1], k1 - k0, 3)
    _write_itk_field(fwd_path, disp, aff_f)
    del disp
    # inverse: on orig's grid moved into mri's frame (affine M aff_i); voxel at
    # mri point M o -> MNI point mni(o)
    aff_inv_out = M @ aff_i
    disp = np.empty_like(mni)
    for k0 in range(0, shape_i[2], _SLAB):
        k1 = min(k0 + _SLAB, shape_i[2])
        _, c = _grid_slab(shape_i, aff_inv_out, k0, k1)
        disp[:, :, k0:k1] = (mni[:, :, k0:k1].reshape(-1, 3) - c).reshape(
            shape_i[0], shape_i[1], k1 - k0, 3)
    _write_itk_field(inv_path, disp, aff_inv_out)
    del disp

    return {
        "method": "synthmorph",
        "fwdtransforms": [fwd_path],
        "map_points": map_points,
        "crop_box_mni": {"min": [round(float(x), 1) for x in box_lo],
                         "max": [round(float(x), 1) for x in box_hi]},
        "orig_registered_to_mri": not np.allclose(M, np.eye(4)),
    }


# ── Method 2: ANTs brain -> brain ──────────────────────────────────────────────

def register_brain_to_mni(mri_path: str, out_dir: str, modality: str = "t1") -> dict:
    """
    antsRegistrationSyN[s] of the skull-stripped patient MRI to the skull-stripped
    template. The patient mask is the one the brain mesh is built from (antspynet
    brain extraction, in a child process; morphological fallback without it).

    Writes ``mri_to_mni_warp.nii.gz`` + ``mri_to_mni_affine.mat`` (apply both, warp
    first) and ``mni_to_mri_invwarp.nii.gz``.
    """
    import ants
    from services.mesh_extractor import write_brain_mask_isolated

    mask_path = os.path.join(out_dir, "_brain_mask.nii.gz")
    write_brain_mask_isolated(mri_path, mask_path, modality)
    mri = ants.image_read(mri_path)
    # The mask is on the same physical grid with axes in canonical order:
    # nearest-neighbour resampling onto the MRI is an exact re-indexing.
    mask = ants.resample_image_to_target(ants.image_read(mask_path), mri,
                                         interp_type="nearestNeighbor")
    os.remove(mask_path)
    brain = mri * mask.clone("float")
    fixed = ants.image_read(get_mni_template_path())

    print("[MNI] Running ANTs antsRegistrationSyN[s] brain->brain to ICBM 2009c "
          "(this takes several minutes)...")
    reg = ants.registration(fixed=fixed, moving=brain,
                            type_of_transform="antsRegistrationSyN[s]", verbose=False)

    # fwdtransforms = [warp, affine]  (warps the MRI into MNI; maps MNI points to the MRI)
    # invtransforms = [affine, inverseWarp]
    warp = os.path.join(out_dir, "mri_to_mni_warp.nii.gz")
    affine = os.path.join(out_dir, "mri_to_mni_affine.mat")
    invwarp = os.path.join(out_dir, "mni_to_mri_invwarp.nii.gz")
    shutil.copy(reg["fwdtransforms"][0], warp)
    shutil.copy(reg["fwdtransforms"][1], affine)
    shutil.copy(reg["invtransforms"][-1], invwarp)

    def map_points(c_mri: np.ndarray):
        return transform_contacts_to_mni(c_mri, [affine, invwarp]), \
            np.zeros(len(c_mri), dtype=bool)

    print("[MNI] MRI->MNI registration complete.")
    return {"method": "ants_syn_brain", "fwdtransforms": [warp, affine],
            "map_points": map_points}


def transform_contacts_to_mni(contacts_world_ras: np.ndarray, invtransforms: list) -> np.ndarray:
    """
    Map MRI-space points (RAS world mm) into MNI RAS mm through an ANTs inverse
    transform list ``[affine, inverseWarp]`` (the affine is inverted for points).
    """
    import ants
    import pandas as pd

    if len(contacts_world_ras) == 0:
        return np.zeros((0, 3))

    lps = _ras_to_lps(contacts_world_ras)
    df = pd.DataFrame({"x": lps[:, 0], "y": lps[:, 1], "z": lps[:, 2]})
    whichtoinvert = [p.endswith(".mat") for p in invtransforms]
    warped = ants.apply_transforms_to_points(
        3, df, transformlist=invtransforms, whichtoinvert=whichtoinvert
    )
    return _lps_to_ras(warped[["x", "y", "z"]].to_numpy())


def mni_transforms(recon_dir: str, mri_path: str, out_dir: str, method: str = "auto") -> dict:
    """SynthMorph when the FreeSurfer import has it (method "auto"), else ANTs.
    method "synthmorph" / "ants" forces one (tests, comparisons)."""
    sm = find_synthmorph(recon_dir) if method in ("auto", "synthmorph") else None
    if method == "synthmorph" and sm is None:
        raise FileNotFoundError("No SynthMorph registration in this reconstruction's FreeSurfer import")
    if sm is not None:
        print("[MNI] Using recon-all's SynthMorph registration to ICBM 2009c (no registration run)")
        return synthmorph_transforms(sm, out_dir)
    print("[MNI] ANTs brain->brain (forced)" if method == "ants" else
          "[MNI] No SynthMorph registration (no recon-all import); falling back to ANTs")
    return register_brain_to_mni(mri_path, out_dir)


# ── CT -> MRI grid ──────────────────────────────────────────────────────────────

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


# ── Orchestrator ────────────────────────────────────────────────────────────────

def export_reconstruction_to_mni(recon_dir: str, mri_path: str, ct_path: str,
                                 ct_to_mri_npy: str, contacts: list,
                                 mesh_center, out_dir: str = None,
                                 method: str = "auto") -> dict:
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
        out_dir:        override the output folder (tests / comparisons)
        method:         "auto" (SynthMorph if available, else ANTs), "synthmorph", "ants"

    Returns a manifest dict (also written to ``export/export_manifest.json``).
    """
    import ants

    out_dir = out_dir or os.path.join(recon_dir, "export")
    os.makedirs(out_dir, exist_ok=True)
    center = np.asarray(mesh_center, dtype=np.float64).reshape(3)
    written = set()

    # Phase timings land in the manifest so slow runs / regressions are visible
    # without re-instrumenting.
    t_start = time.perf_counter()
    durations = {}

    # Step 1: MRI -> MNI transforms, then the MRI itself (whole head)
    _t = time.perf_counter()
    reg = mni_transforms(recon_dir, mri_path, out_dir, method)
    written.update(os.path.basename(p) for p in reg["fwdtransforms"])
    written.add("mni_to_mri_invwarp.nii.gz")
    template = ants.image_read(get_mni_template_path())
    mri_mni = ants.apply_transforms(fixed=template, moving=ants.image_read(mri_path),
                                    transformlist=reg["fwdtransforms"], interpolator="linear")
    mri_mni.to_filename(os.path.join(out_dir, "MRI_mni.nii.gz"))
    written.add("MRI_mni.nii.gz")
    durations["mri_to_mni"] = round(time.perf_counter() - _t, 1)
    print(f"[MNI] MRI->MNI ({reg['method']}) took {durations['mri_to_mni']:.1f}s")

    # Step 2: CT -> MNI (optional)
    ct_written = False
    _t = time.perf_counter()
    if ct_path and os.path.exists(ct_path) and ct_to_mri_npy and os.path.exists(ct_to_mri_npy):
        try:
            ct_to_mri_ras = np.load(ct_to_mri_npy)
            ct_in_mri = _resample_ct_into_mri_grid(ct_path, mri_path, ct_to_mri_ras)
            ct_in_mri_path = os.path.join(out_dir, "_ct_in_mri.nii.gz")
            ct_in_mri.to_filename(ct_in_mri_path)
            ct_mni = ants.apply_transforms(
                fixed=template, moving=ants.image_read(ct_in_mri_path),
                transformlist=reg["fwdtransforms"], interpolator="linear",
                defaultvalue=-1000.0,
            )
            ct_mni.to_filename(os.path.join(out_dir, "CT_mni.nii.gz"))
            os.remove(ct_in_mri_path)
            ct_written = True
            written.add("CT_mni.nii.gz")
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
    mni_pts, extrapolated = reg["map_points"](world)
    point_method = np.where(extrapolated, f"{reg['method']}_affine_extrapolated", reg["method"])

    rows = []
    for c, p, how in zip(placed, mni_pts, point_method):
        rows.append({
            "shaft_name": c.get("shaft_name", ""),
            "contact_number": c.get("contact_number"),
            "x_mni": round(float(p[0]), 3),
            "y_mni": round(float(p[1]), 3),
            "z_mni": round(float(p[2]), 3),
            "x_native_mm": round(float(c["x_mm"]), 3),
            "y_native_mm": round(float(c["y_mm"]), 3),
            "z_native_mm": round(float(c["z_mm"]), 3),
            "mni_method": str(how),
        })

    # CSV (spec columns) + JSON (with native coords and per-contact method)
    csv_path = os.path.join(out_dir, "electrodes_mni.csv")
    with open(csv_path, "w", newline="") as f:
        import csv as _csv
        w = _csv.writer(f)
        w.writerow(["shaft_name", "contact_number", "x_mni", "y_mni", "z_mni"])
        for r in rows:
            w.writerow([r["shaft_name"], r["contact_number"], r["x_mni"], r["y_mni"], r["z_mni"]])
    with open(os.path.join(out_dir, "electrodes_mni.json"), "w") as f:
        json.dump(rows, f, indent=2)
    written.update({"electrodes_mni.csv", "electrodes_mni.json"})

    # Sanity self-check (surfaces flips/offsets in logs; not a hard gate)
    if len(mni_pts):
        lo = np.array([-96.0, -132.0, -78.0])          # template grid extent
        hi = np.array([96.0, 96.0, 114.0])
        inb = np.mean(np.all((mni_pts >= lo) & (mni_pts <= hi), axis=1))
        n_left = int(np.sum(mni_pts[:, 0] < 0))
        print(f"[MNI] Electrodes: {len(mni_pts)} contacts, "
              f"{inb*100:.0f}% inside the template grid, "
              f"{n_left} left-hemisphere (x<0), {len(mni_pts)-n_left} right (x>=0)"
              + (f", {int(extrapolated.sum())} outside SynthMorph's crop (affine + residual)"
                 if reg["method"] == "synthmorph" else "") + ".")
        if inb < 0.9:
            print("[MNI] WARNING: many contacts fell outside the template grid - "
                  "check RAS/LPS flip and point-transform direction.")

    durations["electrodes"] = round(time.perf_counter() - _t, 1)

    # Step 4: contact -> brain-structure labels (native space, patient-specific).
    # Uses the DKT label volume cached by structure_extractor; if structures have
    # never been computed for this reconstruction we generate them here (slow,
    # ~2-3 min, but cached afterwards and reused by the viewer).
    _t = time.perf_counter()
    structure_summary = None
    parcellation = "fast"
    try:
        from services.contact_labeling import (
            get_label_volume_path, label_contacts, write_contact_structure_csv,
        )
        from services.freesurfer_import import active_source
        parcellation = active_source(recon_dir)
        label_path = get_label_volume_path(recon_dir)
        if not os.path.exists(label_path):
            print("[MNI] Structure labels not cached - running segmentation "
                  "(first time only, this takes a few minutes)...")
            from services.structure_extractor import extract_all_structures_isolated
            extract_all_structures_isolated(
                os.path.join(recon_dir, "mesh.json"), recon_dir, mri_path)

        if os.path.exists(label_path) and len(world):
            labels = label_contacts(label_path, world)
            structure_summary = write_contact_structure_csv(
                os.path.join(out_dir, "electrodes_structures.csv"), placed, labels,
                parcellation=parcellation,
            )
            written.add("electrodes_structures.csv")
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

    written.add("export_manifest.json")
    for fn in ARTIFACTS:                     # leftovers from an earlier run
        if fn not in written and os.path.exists(os.path.join(out_dir, fn)):
            os.remove(os.path.join(out_dir, fn))

    synthmorph = reg["method"] == "synthmorph"
    manifest = {
        "template": TEMPLATE_NAME,
        "method": reg["method"],
        "transform_type": ("SynthMorph affine + deformable, computed by FreeSurfer recon-all"
                           if synthmorph else "ANTs antsRegistrationSyN[s], brain to brain"),
        "transforms_apply": (["mri_to_mni_warp.nii.gz"] if synthmorph
                             else ["mri_to_mni_warp.nii.gz", "mri_to_mni_affine.mat"]),
        "transforms_note": ("ITK/ANTs displacement fields (LPS). Images into MNI: "
                            "transforms_apply, in order, with MRI_mni.nii.gz as reference. "
                            "Points from the patient into MNI: mni_to_mri_invwarp.nii.gz"
                            + ("" if synthmorph else
                               " after the inverted affine (mri_to_mni_affine.mat)") + "."),
        "ants_version": ants_version,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "n_contacts": len(rows),
        "n_contacts_extrapolated": int(extrapolated.sum()),
        "has_ct_mni": ct_written,
        "has_structure_labels": structure_summary is not None,
        "contact_structures": structure_summary,
        "parcellation_source": parcellation,
        "durations_sec": durations,
        "artifacts": sorted(
            {fn for fn in os.listdir(out_dir) if not fn.startswith("_")} | {"export_manifest.json"}
        ),
    }
    if synthmorph:
        manifest["synthmorph"] = {
            "source": "/".join(SYNTHMORPH_DIR[1:]),
            "crop_box_mni": reg["crop_box_mni"],
            "orig_registered_to_mri": reg["orig_registered_to_mri"],
        }
    with open(os.path.join(out_dir, "export_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    # NOTE: keep log output ASCII-only. Under uvicorn on Windows stdout is cp1252,
    # and a non-ASCII character here raises UnicodeEncodeError *after* all artifacts
    # are written -- failing the whole export on a cosmetic log line.
    print(f"[MNI] Export complete in {durations['total']:.1f}s -> {out_dir}")
    return manifest
