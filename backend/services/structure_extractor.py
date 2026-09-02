"""
Patient-specific brain structure segmentation using antspynet.

A single segmentation pass on the patient's native T1 MRI:
  antspynet.desikan_killiany_tourville_labeling(do_preprocessing=True)

Despite the name, this one model emits **both** cortical (DKT) and subcortical
labels into a single volume, cached as `structures_cortical.nii.gz`. The
`do_preprocessing=True` flag runs N4 bias correction, brain extraction, and
template registration internally — that preprocessing, not the inference, is
the bulk of the runtime.

All label volumes are in the patient's native MRI space — no MNI atlas,
no standard-space registration. Every structure mesh is unique to this patient.

Cortical vs subcortical is a *label-numbering* convention (FreeSurfer scheme),
not two separate algorithms:
  Cortical (DKT): hemisphere as an offset — left 1000+, right 2000+, same
                  region id within each (e.g. precentral 1024 / 2024)
  Subcortical:    plain FreeSurfer indices, not offset-paired
                  (e.g. hippocampus 17 / 53, thalamus 10 / 49)
  Midline:        may claim several indices (cerebellum 6,7,8,45,46,47)
  Not anatomy:    0 background/WM, 2/41 cerebral WM, 4/43 lateral ventricles,
                  14/15 3rd/4th ventricle, 24 CSF — absent from the catalog
                  below (see services/contact_labeling.py, NON_CATALOG_LABELS)

The ALL_STRUCTURES catalog is what turns those indices into anatomy: ~84
structures across 6 groups (subcortical, frontal, temporal, parietal,
occipital, cingulate). The `group` field drives the Group -> Side -> Structure
tree in the UI. A handful of the smallest DKT regions may yield no mesh — see
the <5 voxel / <20 face guards in _labels_to_mesh.
"""

import numpy as np
import nibabel as nib
from skimage import measure
from skimage.filters import gaussian
from scipy.ndimage import distance_transform_edt
import trimesh
import json
import os
import subprocess
import sys
import threading
import concurrent.futures

# ── Structure catalog ─────────────────────────────────────────────────────────
# FreeSurfer/DKT label indices as output by antspynet DKT parcellation.
# Left hemisphere cortical: 1000+, Right hemisphere cortical: 2000+
# Subcortical: FreeSurfer standard indices.

SUBCORTICAL_STRUCTURES = {
    # Midline
    "brainstem":      {"labels": [16],                "label": "Brainstem",            "color": "#F06595", "group": "subcortical"},
    "cerebellum":     {"labels": [6, 7, 8, 45, 46, 47], "label": "Cerebellum",         "color": "#74C0FC", "group": "subcortical"},
    # Thalamus
    "thalamus_l":     {"labels": [10],                "label": "Left Thalamus",        "color": "#4DABF7", "group": "subcortical"},
    "thalamus_r":     {"labels": [49],                "label": "Right Thalamus",       "color": "#A5D8FF", "group": "subcortical"},
    # Caudate
    "caudate_l":      {"labels": [11],                "label": "Left Caudate",         "color": "#63E6BE", "group": "subcortical"},
    "caudate_r":      {"labels": [50],                "label": "Right Caudate",        "color": "#96F2D7", "group": "subcortical"},
    # Putamen
    "putamen_l":      {"labels": [12],                "label": "Left Putamen",         "color": "#CC5DE8", "group": "subcortical"},
    "putamen_r":      {"labels": [51],                "label": "Right Putamen",        "color": "#E599F7", "group": "subcortical"},
    # Pallidum
    "pallidum_l":     {"labels": [13],                "label": "Left Pallidum",        "color": "#FFD43B", "group": "subcortical"},
    "pallidum_r":     {"labels": [52],                "label": "Right Pallidum",       "color": "#FFE066", "group": "subcortical"},
    # Hippocampus
    "hippocampus_l":  {"labels": [17],                "label": "Left Hippocampus",     "color": "#FF6B6B", "group": "subcortical"},
    "hippocampus_r":  {"labels": [53],                "label": "Right Hippocampus",    "color": "#FF8E8E", "group": "subcortical"},
    # Amygdala
    "amygdala_l":     {"labels": [18],                "label": "Left Amygdala",        "color": "#FFA94D", "group": "subcortical"},
    "amygdala_r":     {"labels": [54],                "label": "Right Amygdala",       "color": "#FFC07A", "group": "subcortical"},
    # Nucleus accumbens
    "accumbens_l":    {"labels": [26],                "label": "Left Accumbens",       "color": "#20C997", "group": "subcortical"},
    "accumbens_r":    {"labels": [58],                "label": "Right Accumbens",      "color": "#63E6BE", "group": "subcortical"},
    # Ventral diencephalon (substantia nigra / subthalamic nucleus region)
    "ventral_dc_l":   {"labels": [28],                "label": "Left Ventral DC",      "color": "#F783AC", "group": "subcortical"},
    "ventral_dc_r":   {"labels": [60],                "label": "Right Ventral DC",     "color": "#FAA2C1", "group": "subcortical"},
}

FRONTAL_STRUCTURES = {
    "precentral_l":           {"labels": [1024], "label": "Left Precentral",          "color": "#FF4444", "group": "frontal"},
    "precentral_r":           {"labels": [2024], "label": "Right Precentral",         "color": "#FF7777", "group": "frontal"},
    "paracentral_l":          {"labels": [1017], "label": "Left Paracentral",         "color": "#FF6B35", "group": "frontal"},
    "paracentral_r":          {"labels": [2017], "label": "Right Paracentral",        "color": "#FF8C5A", "group": "frontal"},
    "superiorfrontal_l":      {"labels": [1028], "label": "Left Sup. Frontal",        "color": "#FF9B3C", "group": "frontal"},
    "superiorfrontal_r":      {"labels": [2028], "label": "Right Sup. Frontal",       "color": "#FFB366", "group": "frontal"},
    "rostralmiddlefrontal_l": {"labels": [1027], "label": "Left Rostral Mid. Frontal","color": "#FFAD47", "group": "frontal"},
    "rostralmiddlefrontal_r": {"labels": [2027], "label": "Right Rostral Mid. Frontal","color": "#FFC06E","group": "frontal"},
    "caudalmiddlefrontal_l":  {"labels": [1003], "label": "Left Caudal Mid. Frontal", "color": "#F76707", "group": "frontal"},
    "caudalmiddlefrontal_r":  {"labels": [2003], "label": "Right Caudal Mid. Frontal","color": "#FF922B", "group": "frontal"},
    "parsopercularis_l":      {"labels": [1018], "label": "Left Pars Opercularis",    "color": "#E64980", "group": "frontal"},
    "parsopercularis_r":      {"labels": [2018], "label": "Right Pars Opercularis",   "color": "#F06595", "group": "frontal"},
    "parstriangularis_l":     {"labels": [1020], "label": "Left Pars Triangularis",   "color": "#D6336C", "group": "frontal"},
    "parstriangularis_r":     {"labels": [2020], "label": "Right Pars Triangularis",  "color": "#E8507E", "group": "frontal"},
    "parsorbitalis_l":        {"labels": [1019], "label": "Left Pars Orbitalis",      "color": "#C92A2A", "group": "frontal"},
    "parsorbitalis_r":        {"labels": [2019], "label": "Right Pars Orbitalis",     "color": "#E03131", "group": "frontal"},
    "lateralorbitofrontal_l": {"labels": [1012], "label": "Left Lat. Orbitofrontal",  "color": "#E67700", "group": "frontal"},
    "lateralorbitofrontal_r": {"labels": [2012], "label": "Right Lat. Orbitofrontal", "color": "#F59F00", "group": "frontal"},
    "medialorbitofrontal_l":  {"labels": [1014], "label": "Left Med. Orbitofrontal",  "color": "#D9480F", "group": "frontal"},
    "medialorbitofrontal_r":  {"labels": [2014], "label": "Right Med. Orbitofrontal", "color": "#E8590C", "group": "frontal"},
    "frontalpole_l":          {"labels": [1032], "label": "Left Frontal Pole",        "color": "#862E9C", "group": "frontal"},
    "frontalpole_r":          {"labels": [2032], "label": "Right Frontal Pole",       "color": "#9C36B5", "group": "frontal"},
}

TEMPORAL_STRUCTURES = {
    "superiortemporal_l":  {"labels": [1030], "label": "Left Sup. Temporal",        "color": "#2F9E44", "group": "temporal"},
    "superiortemporal_r":  {"labels": [2030], "label": "Right Sup. Temporal",       "color": "#40C057", "group": "temporal"},
    "middletemporal_l":    {"labels": [1015], "label": "Left Mid. Temporal",        "color": "#099268", "group": "temporal"},
    "middletemporal_r":    {"labels": [2015], "label": "Right Mid. Temporal",       "color": "#0CA678", "group": "temporal"},
    "inferiortemporal_l":  {"labels": [1009], "label": "Left Inf. Temporal",        "color": "#087F5B", "group": "temporal"},
    "inferiortemporal_r":  {"labels": [2009], "label": "Right Inf. Temporal",       "color": "#0B8A63", "group": "temporal"},
    "fusiform_l":          {"labels": [1007], "label": "Left Fusiform",             "color": "#1098AD", "group": "temporal"},
    "fusiform_r":          {"labels": [2007], "label": "Right Fusiform",            "color": "#15AABF", "group": "temporal"},
    "parahippocampal_l":   {"labels": [1016], "label": "Left Parahippocampal",      "color": "#0C8599", "group": "temporal"},
    "parahippocampal_r":   {"labels": [2016], "label": "Right Parahippocampal",     "color": "#1098AD", "group": "temporal"},
    "entorhinal_l":        {"labels": [1006], "label": "Left Entorhinal",           "color": "#37B24D", "group": "temporal"},
    "entorhinal_r":        {"labels": [2006], "label": "Right Entorhinal",          "color": "#4CAF50", "group": "temporal"},
    "temporalpole_l":      {"labels": [1033], "label": "Left Temporal Pole",        "color": "#51CF66", "group": "temporal"},
    "temporalpole_r":      {"labels": [2033], "label": "Right Temporal Pole",       "color": "#69DB7C", "group": "temporal"},
    "transversetemporal_l":{"labels": [1034], "label": "Left Transverse Temporal",  "color": "#74C0FC", "group": "temporal"},
    "transversetemporal_r":{"labels": [2034], "label": "Right Transverse Temporal", "color": "#A5D8FF", "group": "temporal"},
    "insula_l":            {"labels": [1035], "label": "Left Insula",               "color": "#FFAA00", "group": "temporal"},
    "insula_r":            {"labels": [2035], "label": "Right Insula",              "color": "#FFCC44", "group": "temporal"},
}

PARIETAL_STRUCTURES = {
    "postcentral_l":      {"labels": [1022], "label": "Left Postcentral",      "color": "#4444FF", "group": "parietal"},
    "postcentral_r":      {"labels": [2022], "label": "Right Postcentral",     "color": "#7777FF", "group": "parietal"},
    "superiorparietal_l": {"labels": [1029], "label": "Left Sup. Parietal",    "color": "#1C7ED6", "group": "parietal"},
    "superiorparietal_r": {"labels": [2029], "label": "Right Sup. Parietal",   "color": "#339AF0", "group": "parietal"},
    "inferiorparietal_l": {"labels": [1008], "label": "Left Inf. Parietal",    "color": "#1971C2", "group": "parietal"},
    "inferiorparietal_r": {"labels": [2008], "label": "Right Inf. Parietal",   "color": "#1C7ED6", "group": "parietal"},
    "supramarginal_l":    {"labels": [1031], "label": "Left Supramarginal",    "color": "#5C7CFA", "group": "parietal"},
    "supramarginal_r":    {"labels": [2031], "label": "Right Supramarginal",   "color": "#748FFC", "group": "parietal"},
    "precuneus_l":        {"labels": [1025], "label": "Left Precuneus",        "color": "#3B5BDB", "group": "parietal"},
    "precuneus_r":        {"labels": [2025], "label": "Right Precuneus",       "color": "#4C6EF5", "group": "parietal"},
}

OCCIPITAL_STRUCTURES = {
    "lateraloccipital_l": {"labels": [1011], "label": "Left Lat. Occipital",   "color": "#7048E8", "group": "occipital"},
    "lateraloccipital_r": {"labels": [2011], "label": "Right Lat. Occipital",  "color": "#845EF7", "group": "occipital"},
    "lingual_l":          {"labels": [1013], "label": "Left Lingual",          "color": "#6741D9", "group": "occipital"},
    "lingual_r":          {"labels": [2013], "label": "Right Lingual",         "color": "#7950F2", "group": "occipital"},
    "cuneus_l":           {"labels": [1005], "label": "Left Cuneus",           "color": "#5F3DC4", "group": "occipital"},
    "cuneus_r":           {"labels": [2005], "label": "Right Cuneus",          "color": "#6741D9", "group": "occipital"},
    "pericalcarine_l":    {"labels": [1021], "label": "Left Pericalcarine",    "color": "#9775FA", "group": "occipital"},
    "pericalcarine_r":    {"labels": [2021], "label": "Right Pericalcarine",   "color": "#B197FC", "group": "occipital"},
}

CINGULATE_STRUCTURES = {
    "rostralanteriorcingulate_l":  {"labels": [1026], "label": "Left Rostral Ant. Cingulate",  "color": "#F783AC", "group": "cingulate"},
    "rostralanteriorcingulate_r":  {"labels": [2026], "label": "Right Rostral Ant. Cingulate", "color": "#FFA8CC", "group": "cingulate"},
    "caudalanteriorcingulate_l":   {"labels": [1002], "label": "Left Caudal Ant. Cingulate",   "color": "#AA44AA", "group": "cingulate"},
    "caudalanteriorcingulate_r":   {"labels": [2002], "label": "Right Caudal Ant. Cingulate",  "color": "#CC66CC", "group": "cingulate"},
    "posteriorcingulate_l":        {"labels": [1023], "label": "Left Post. Cingulate",         "color": "#CC44CC", "group": "cingulate"},
    "posteriorcingulate_r":        {"labels": [2023], "label": "Right Post. Cingulate",        "color": "#DD77DD", "group": "cingulate"},
    "isthmuscingulate_l":          {"labels": [1010], "label": "Left Isthmus Cingulate",       "color": "#E64980", "group": "cingulate"},
    "isthmuscingulate_r":          {"labels": [2010], "label": "Right Isthmus Cingulate",      "color": "#F06595", "group": "cingulate"},
}

ALL_STRUCTURES = {
    **SUBCORTICAL_STRUCTURES,
    **FRONTAL_STRUCTURES,
    **TEMPORAL_STRUCTURES,
    **PARIETAL_STRUCTURES,
    **OCCIPITAL_STRUCTURES,
    **CINGULATE_STRUCTURES,
}

CORTICAL_GROUPS = {"frontal", "temporal", "parietal", "occipital", "cingulate"}

# ── Surface smoothing ─────────────────────────────────────────────────────────
# DKT label boundaries are voxel-quantized, so raw marching cubes produces a
# visible staircase. These settings affect DISPLAY MESHES ONLY -- the label
# volume (structures_cortical.nii.gz) is never modified, so contact-to-structure
# labeling in services/contact_labeling.py is unaffected by any change here.
#
# Measured across all 78 extractable structures on PY26N009_dev3, comparing mesh
# volume against the true voxel volume of the label mask. Roughness = surface
# area / area of an equal-volume sphere (lower is smoother):
#     old (sigma .5 @ level .4)   mean|err| 4.9%  (always inflated, +1.1..+7.6%)
#     fixed sigma .8 + Taubin     mean|err| 3.3%  (-10.7..-0.2%), 69/78 improved
#     adaptive sigma + Taubin     mean|err| 2.7%  ( -6.2..-0.2%), 76/78 improved
# and 78/78 structures came out smoother (mean roughness -6.2%).
#
# ITK AntiAliasBinary was benchmarked too and matched a flat sigma .8 almost
# exactly (-2.9/-5.1% vol, rough 1.50/3.36) for more cost, so it is not used.
#
# level 0.5 is the midpoint of the 0/1 mask and preserves boundary position;
# the previous 0.4 systematically inflated every structure (measured +4.9% mean
# across 78 structures, always positive).
SMOOTH_ISOLEVEL = 0.5

# Sigma is scaled by how thick the structure actually is. A fixed sigma erodes
# thin parcels far more than bulky ones -- with a flat 0.8, thin ribbons like
# isthmus cingulate and pericalcarine lost ~10% volume while cerebellum lost
# 0.2% (correlation between half-thickness and volume error: +0.50). Scaling
# keeps the strong smoothing where it is safe and backs off where it is not.
SMOOTH_SIGMA_MAX = 0.8          # bulky structures (thalamus, cerebellum, ...)
SMOOTH_SIGMA_MIN = 0.5          # thin cortical ribbons
SIGMA_REF_HALF_THICKNESS_MM = 3.5  # half-thickness at which sigma reaches the max
# Taubin lambda/nu: shrink-free Laplacian smoothing. nu must be POSITIVE --
# trimesh applies `vertices -= nu * dot`, so a negative nu turns the
# compensating pass into a second shrinking pass (~7% volume loss).
# Constraint from trimesh: 0 < 1/lambda - 1/nu < 0.1.
TAUBIN_LAMB = 0.5
TAUBIN_NU = 0.52
TAUBIN_ITERATIONS = 10


# ── ANTs → nibabel affine helper ─────────────────────────────────────────────

def _ants_to_nib(ants_img, debug_label=""):
    """
    Convert an ANTs image to a nibabel NIfTI using the ANTs image's own
    spacing/origin/direction — NOT the original MRI affine.
    antspynet resamples internally so the output space differs from the input.
    """
    spacing   = np.array(ants_img.spacing)
    origin    = np.array(ants_img.origin)
    direction = np.array(ants_img.direction).reshape(3, 3)
    # Build affine: direction @ diag(spacing) — scale each axis column by voxel size
    affine = np.eye(4)
    affine[:3, :3] = direction @ np.diag(spacing)
    affine[:3, 3]  = origin
    tag = f"[STRUCT DEBUG] {debug_label}" if debug_label else "[STRUCT DEBUG]"
    print(f"{tag} ANTs spacing : {spacing}")
    print(f"{tag} ANTs origin  : {origin}")
    print(f"{tag} ANTs direction:\n{direction}")
    print(f"{tag} built affine :\n{affine}")
    print(f"{tag} volume shape  : {ants_img.numpy().shape}")
    return nib.Nifti1Image(ants_img.numpy().astype(np.int16), affine)


# ── Mesh extraction helper ────────────────────────────────────────────────────

def _labels_to_mesh(label_data: np.ndarray, affine: np.ndarray,
                    label_indices: list, center: np.ndarray,
                    max_faces: int = 10000) -> dict | None:
    """
    Given a label volume, extract a surface mesh for the given label indices,
    centered at `center` (brain mesh origin in world space).
    """
    # Round to int to avoid float32 precision issues (e.g. 17.0000001 != 17)
    label_int = np.round(label_data).astype(np.int32)
    mask = np.zeros(label_data.shape, dtype=np.float32)
    for idx in label_indices:
        mask[label_int == idx] = 1.0

    if mask.sum() < 5:
        return None

    # Debug: voxel bounding box of this structure
    nz = np.argwhere(mask > 0)
    vox_min, vox_max = nz.min(axis=0), nz.max(axis=0)
    vox_center = (vox_min + vox_max) / 2.0
    vox_center_hom = np.append(vox_center, 1.0)
    world_center = (affine @ vox_center_hom)[:3]
    print(f"[STRUCT DEBUG] mask vox bbox  : {vox_min} - {vox_max}")
    print(f"[STRUCT DEBUG] mask vox center: {vox_center} -> world {world_center}")
    print(f"[STRUCT DEBUG] after -= center: {world_center - center}")

    # Scale smoothing to the structure's own thickness (see notes at SMOOTH_SIGMA_MAX).
    # Peak of the interior distance transform = half-thickness, in mm so that
    # anisotropic voxels are handled correctly.
    vox_mm = np.linalg.norm(affine[:3, :3], axis=0)
    half_thickness_mm = float(distance_transform_edt(mask > 0, sampling=vox_mm).max())
    sigma = SMOOTH_SIGMA_MAX * min(1.0, half_thickness_mm / SIGMA_REF_HALF_THICKNESS_MM)
    sigma = float(np.clip(sigma, SMOOTH_SIGMA_MIN, SMOOTH_SIGMA_MAX))

    smoothed = gaussian(mask, sigma=sigma)

    try:
        verts_vox, faces, _, _ = measure.marching_cubes(
            smoothed, level=SMOOTH_ISOLEVEL, step_size=1, allow_degenerate=False
        )
    except (ValueError, RuntimeError):
        return None

    if len(faces) < 20:
        return None

    # Voxel → world RAS
    verts_hom = np.hstack([verts_vox, np.ones((len(verts_vox), 1))])
    verts_world = (affine @ verts_hom.T).T[:, :3]

    # Subtract brain mesh center → Three.js space
    verts_aligned = verts_world - center
    print(f"[STRUCT DEBUG] world range    : {verts_world.min(axis=0)} - {verts_world.max(axis=0)}")
    print(f"[STRUCT DEBUG] aligned range  : {verts_aligned.min(axis=0)} - {verts_aligned.max(axis=0)}")

    mesh = trimesh.Trimesh(vertices=verts_aligned, faces=faces, process=False)

    # Taubin smoothing on the dense mesh (before decimation) to take the
    # remaining voxel staircase off the surface without shrinking the structure.
    # Guarded: if smoothing produces non-finite vertices, keep the unsmoothed mesh.
    if TAUBIN_ITERATIONS > 0 and len(mesh.faces) >= 20:
        try:
            before = mesh.vertices.copy()
            trimesh.smoothing.filter_taubin(
                mesh, lamb=TAUBIN_LAMB, nu=TAUBIN_NU, iterations=TAUBIN_ITERATIONS
            )
            if not np.isfinite(mesh.vertices).all():
                mesh.vertices = before
        except Exception:
            mesh = trimesh.Trimesh(vertices=verts_aligned, faces=faces, process=False)

    if len(mesh.faces) > max_faces:
        mesh = mesh.simplify_quadric_decimation(max_faces)

    return {
        "vertices": mesh.vertices.flatten().tolist(),
        "faces": mesh.faces.flatten().tolist(),
        "vertex_count": len(mesh.vertices),
        "face_count": len(mesh.faces),
    }


# ── Parallel mesh extraction workers ─────────────────────────────────────────
# Per-structure mesh extraction is CPU-bound and independent across structures,
# so it runs in a ProcessPoolExecutor. Each worker loads the label volume once
# (in the initializer) rather than receiving the full array with every task.

_WORKER_LABEL_DATA = None
_WORKER_AFFINE = None
_WORKER_CENTER = None


def _mesh_worker_init(label_path: str, center: np.ndarray) -> None:
    """Runs once per worker process: cache the shared label volume in globals."""
    global _WORKER_LABEL_DATA, _WORKER_AFFINE, _WORKER_CENTER
    img = nib.load(label_path)
    _WORKER_LABEL_DATA = img.get_fdata()
    _WORKER_AFFINE = img.affine
    _WORKER_CENTER = center


def _mesh_worker_task(key: str, label_indices: list, max_faces: int):
    """Extract one structure's mesh from the worker-local label volume."""
    mesh_data = _labels_to_mesh(
        _WORKER_LABEL_DATA, _WORKER_AFFINE, label_indices, _WORKER_CENTER,
        max_faces=max_faces,
    )
    return key, mesh_data


# ── Main segmentation entry point ─────────────────────────────────────────────

_MANIFEST_NAME = "_manifest.json"

# One extraction worker at a time. A parcellation peaks near 8.9 GB, and the
# App Service plan this runs on has 16 GB, so two at once do not fit -- two
# clinicians opening the structures view on different reconstructions would be
# enough to OOM. Queueing costs the second request some latency; not queueing
# costs it the process.
_WORKER_LOCK = threading.Lock()


def _structures_complete(output_dir: str) -> bool:
    """True when a previous run finished and everything it produced is still there.

    Counting files cannot answer this: the smallest DKT regions fall below the
    voxel/face guards in _labels_to_mesh and legitimately yield no mesh, so a
    finished run leaves fewer files than ALL_STRUCTURES has entries (78 of 84 on
    recon_fa94010a). A manifest written at the end of a successful run records
    what that run actually produced, which is the only way to tell "finished" from
    "interrupted".
    """
    manifest = os.path.join(output_dir, "structures", _MANIFEST_NAME)
    if not os.path.exists(manifest):
        return False
    try:
        with open(manifest) as f:
            keys = json.load(f).get("keys", [])
    except (json.JSONDecodeError, OSError):
        return False
    if not keys:
        return False
    structures_dir = os.path.join(output_dir, "structures")
    return all(os.path.exists(os.path.join(structures_dir, f"{k}.json"))
               for k in keys)


def _load_cached_structures(output_dir: str) -> dict:
    """Structure meshes already written to disk for this reconstruction."""
    structures_dir = os.path.join(output_dir, "structures")
    cached = {}
    for key in ALL_STRUCTURES:
        out_path = os.path.join(structures_dir, f"{key}.json")
        if os.path.exists(out_path):
            try:
                with open(out_path) as f:
                    cached[key] = json.load(f)
            except (json.JSONDecodeError, OSError):
                # A file half-written by a process that was killed mid-dump.
                # Treat it as absent so it gets recomputed.
                print(f"[STRUCT] Ignoring unreadable cache entry: {out_path}")
    return cached


def extract_all_structures_isolated(mri_mesh_path: str, output_dir: str,
                                    mri_nifti_path: str = None) -> dict:
    """extract_all_structures, run in a child process.

    DKT parcellation is the largest allocation in the whole application -- 8.9 GB
    on a 126 Mvox T1 even after the streaming-argmax rewrite, and 57 GB with stock
    antspynet. Run in-process it takes the web server down with it: on Azure the
    OOM killer terminated the container mid-parcellation, App Service stopped the
    entire site, and every other user was offline until it cold-started. The
    reconstruction meanwhile still read "ready", because the code that would have
    recorded a failure was inside the process that died.

    In a child process the same kill costs one request. The parent sees a non-zero
    return code, raises, and the caller can report the failure honestly.

    Results travel through the on-disk cache the function already maintains, so
    nothing large is pickled back across the process boundary.
    """
    cached = _load_cached_structures(output_dir)
    if _structures_complete(output_dir):
        print(f"[STRUCT] All {len(cached)} structures loaded from cache")
        return cached

    if getattr(sys, "frozen", False):
        # Under PyInstaller sys.executable is the bundled app, so spawning it
        # would start a second server rather than a worker. The frozen build
        # ships without antspynet anyway, so there is no large allocation to
        # isolate here.
        return extract_all_structures(mri_mesh_path, output_dir, mri_nifti_path)

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [sys.executable, os.path.abspath(__file__), mri_mesh_path, output_dir]
    if mri_nifti_path:
        cmd.append(mri_nifti_path)

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [backend_dir, env["PYTHONPATH"]] if env.get("PYTHONPATH") else [backend_dir])

    if not _WORKER_LOCK.acquire(blocking=False):
        print("[STRUCT] Another extraction is running; waiting for it to finish")
        _WORKER_LOCK.acquire()
    try:
        # A worker for this same reconstruction may have finished while we
        # queued, in which case there is nothing left to do.
        if _structures_complete(output_dir):
            print("[STRUCT] Completed by another worker while queued")
            return _load_cached_structures(output_dir)

        print(f"[STRUCT] Spawning extraction worker (pid parent {os.getpid()})")
        # stdout/stderr are inherited so the worker's [STRUCT] lines land in the
        # same log as everything else.
        proc = subprocess.run(cmd, cwd=backend_dir, env=env)
    finally:
        _WORKER_LOCK.release()

    if proc.returncode != 0:
        # 137 from a shell, -9 from Python's own view of the signal: both mean
        # the kernel killed it, which for this workload means out of memory.
        oom = proc.returncode in (137, -9)
        detail = (" -- killed by the OOM killer; the machine or container does "
                  "not have enough memory for this scan"
                  if oom else "")
        raise RuntimeError(
            f"structure extraction worker exited with {proc.returncode}{detail}")

    cached = _load_cached_structures(output_dir)
    if not cached:
        raise RuntimeError(
            "structure extraction worker reported success but produced no structures")
    return cached


def extract_all_structures(mri_mesh_path: str, output_dir: str,
                           mri_nifti_path: str = None) -> dict:
    """
    Run antspynet segmentation on the patient's T1 MRI and extract structure meshes.

    Args:
        mri_mesh_path:  Path to the brain mesh JSON (for center coordinates)
        output_dir:     Reconstruction directory (structures/ will be created here)
        mri_nifti_path: Path to the T1 NIfTI. If None, inferred from output_dir.

    Returns:
        dict of {key: {label, color, group, vertices, faces, ...}}
    """
    # Load brain mesh center
    with open(mri_mesh_path) as f:
        brain_mesh = json.load(f)
    center = np.array(brain_mesh["center"])
    print(f"[STRUCT DEBUG] Brain mesh center (RAS, mm): {center}")

    structures_dir = os.path.join(output_dir, "structures")
    os.makedirs(structures_dir, exist_ok=True)

    # Load whatever structure files are already cached
    cached = _load_cached_structures(output_dir)
    if _structures_complete(output_dir):
        print(f"[STRUCT] All {len(cached)} structures loaded from cache")
        return cached
    if cached:
        print(f"[STRUCT] Partial cache: {len(cached)}/{len(ALL_STRUCTURES)} structures. Attempting to compute missing ones.")

    # Find MRI NIfTI
    if mri_nifti_path is None:
        # Try to find it in the output_dir
        for fname in ["mri.nii.gz", "mri.nii", "t1.nii.gz", "t1.nii"]:
            candidate = os.path.join(output_dir, fname)
            if os.path.exists(candidate):
                mri_nifti_path = candidate
                break
        if mri_nifti_path is None:
            if cached:
                print(f"[STRUCT] MRI not found, returning {len(cached)} cached structures")
                return cached
            raise FileNotFoundError(
                f"Could not find MRI NIfTI in {output_dir}. "
                "Pass mri_nifti_path explicitly."
            )

    print(f"[STRUCT] Running patient-specific segmentation on {mri_nifti_path}")

    # Segmentation runs on CPU only. Hide any GPU from TensorFlow before antspynet
    # imports it — benchmarking showed the GPU gives no speedup for this pipeline
    # (the cost is CPU-bound ANTs preprocessing + mesh extraction, not the small
    # GPU-able inference), so CPU keeps the environment simple and deterministic.
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

    try:
        import ants
        import antspynet
    except ImportError as e:
        if cached:
            print(f"[STRUCT] antspynet not available ({e}), returning {len(cached)} cached structures")
            return cached
        raise ImportError(f"antspynet not available: {e}")

    # Load MRI as ANTs image via ants.image_read so that ANTs handles the
    # NIfTI RAS → ITK LPS axis convention flip correctly.
    # Using ants.from_numpy with a nibabel RAS affine causes ANTs to interpret
    # RAS metadata as LPS, flipping L/R and A/P — making the DKT model assign
    # left-hemisphere labels to the right side of the brain.
    ants_img = ants.image_read(mri_nifti_path)
    print(f"[STRUCT DEBUG] ANTs input spacing : {ants_img.spacing}")
    print(f"[STRUCT DEBUG] ANTs input origin  : {ants_img.origin}")
    print(f"[STRUCT DEBUG] ANTs input shape   : {ants_img.shape}")

    results = dict(cached)  # start with whatever was cached

    # ── Pass 1: DKT parcellation (contains both cortical AND subcortical labels) ─
    cortical_label_path = os.path.join(output_dir, "structures_cortical.nii.gz")
    if not os.path.exists(cortical_label_path):
        print("[STRUCT] Running cortical parcellation (DKT)...")
        # Stock antspynet peaks at 56.96 GB resident / 158.66 GB commit on a
        # 126 Mvox clinical T1 -- it collects all 63 native-resolution
        # probability volumes before reducing them. dkt_lowmem streams the same
        # argmax and peaks at 8.87 GB, verified bit-identical to upstream.
        # Fall back to antspynet if it is ever unavailable, so a packaging
        # mistake degrades to slow rather than broken.
        try:
            from services.dkt_lowmem import desikan_killiany_tourville_labeling_lowmem
        except ImportError:
            from dkt_lowmem import desikan_killiany_tourville_labeling_lowmem
        try:
            dkt = desikan_killiany_tourville_labeling_lowmem(
                ants_img, do_preprocessing=True, verbose=False
            )
        except Exception as e:
            print(f"[STRUCT] Low-memory DKT failed ({e}); falling back to antspynet")
            dkt = antspynet.desikan_killiany_tourville_labeling(
                ants_img, do_preprocessing=True, verbose=False
            )
        # ants.to_filename handles the ITK LPS → NIfTI RAS conversion correctly
        dkt.to_filename(cortical_label_path)
        print(f"[STRUCT] Cortical labels saved to {cortical_label_path}")
    else:
        print("[STRUCT] Loading cached cortical labels...")

    cort_img = nib.load(cortical_label_path)
    cort_data = cort_img.get_fdata()
    cort_affine = cort_img.affine  # use label volume's own affine, not MRI affine
    unique_cort = np.unique(cort_data).astype(int)
    print(f"[STRUCT DEBUG] DKT NIfTI affine (cort_affine):\n{cort_affine}")
    print(f"[STRUCT DEBUG] DKT NIfTI shape : {cort_data.shape}")
    print(f"[STRUCT DEBUG] DKT NIfTI world extent:")
    corners = np.array([[0,0,0,1],[cort_data.shape[0]-1,0,0,1],
                         [0,cort_data.shape[1]-1,0,1],[0,0,cort_data.shape[2]-1,1]])
    for c in corners:
        w = cort_affine @ c
        print(f"[STRUCT DEBUG]   vox {c[:3].astype(int)} -> world {w[:3]}")
    print(f"[STRUCT DEBUG] Brain center vs DKT extent - offset = "
          f"{center} (should land inside above range)")
    print(f"[STRUCT] DKT label values present (sample): {unique_cort[:30]}")

    # Extract all structures in parallel — each structure's marching-cubes is
    # independent, so fan the loop out across CPU cores. Workers reload the label
    # volume from disk (via initializer) to avoid pickling the full array per task.
    pending = [(key, info) for key, info in ALL_STRUCTURES.items() if key not in results]

    if pending:
        max_workers = min(len(pending), max(1, (os.cpu_count() or 2) - 1), 8)
        print(f"[STRUCT] Extracting {len(pending)} structures on {max_workers} worker(s)...")
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_mesh_worker_init,
            initargs=(cortical_label_path, center),
        ) as pool:
            futures = {
                pool.submit(
                    _mesh_worker_task, key, info["labels"],
                    8000 if info["group"] in CORTICAL_GROUPS else 10000,
                ): (key, info)
                for key, info in pending
            }
            for fut in concurrent.futures.as_completed(futures):
                key, info = futures[fut]
                try:
                    _, mesh_data = fut.result()
                except Exception as e:
                    print(f"[STRUCT] {info['label']}: extraction failed ({e})")
                    continue
                if mesh_data:
                    full = {**info, "key": key, **mesh_data}
                    with open(os.path.join(structures_dir, f"{key}.json"), "w") as f:
                        json.dump(full, f)
                    results[key] = full
                    print(f"[STRUCT] {info['label']}: {mesh_data['face_count']} faces")
                else:
                    print(f"[STRUCT] {info['label']}: no voxels found for labels {info['labels']}")

    print(f"[STRUCT] Done. {len(results)}/{len(ALL_STRUCTURES)} structures extracted.")

    # Record what this run produced so a later call can tell a finished run from
    # an interrupted one without recomputing. Written last, on purpose.
    with open(os.path.join(structures_dir, _MANIFEST_NAME), "w") as f:
        json.dump({"keys": sorted(results)}, f)

    return results


if __name__ == "__main__":
    # Worker entry point for extract_all_structures_isolated(). Kept deliberately
    # thin: the parent reads results from the on-disk cache, so this only has to
    # populate it and exit non-zero if it cannot.
    if not 3 <= len(sys.argv) <= 4:
        print("usage: structure_extractor.py <mesh.json> <output_dir> [mri.nii.gz]",
              file=sys.stderr)
        raise SystemExit(2)
    extract_all_structures(sys.argv[1], sys.argv[2],
                           sys.argv[3] if len(sys.argv) == 4 else None)
