"""
Patient-specific brain structure segmentation using antspynet.

Two segmentation passes on the patient's native T1 MRI:
  1. deep_atropos()                        — subcortical + tissue classes
  2. desikan_killiany_tourville_labeling() — cortical parcellation (DKT atlas)

All label volumes are in the patient's native MRI space — no MNI atlas,
no standard-space registration. Every structure mesh is unique to this patient.

Structures extracted:
  Subcortical: hippocampus (L/R), amygdala (L/R), thalamus (L/R),
               caudate (L/R), putamen (L/R), pallidum (L/R), brainstem,
               cerebellum
  Cortical:    precentral (motor), postcentral (sensory),
               superior temporal gyrus, insula, cingulate (L/R each)
"""

import numpy as np
import nibabel as nib
from skimage import measure
from skimage.filters import gaussian
import trimesh
import json
import os

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
    print(f"[STRUCT DEBUG] mask vox bbox  : {vox_min} – {vox_max}")
    print(f"[STRUCT DEBUG] mask vox center: {vox_center} -> world {world_center}")
    print(f"[STRUCT DEBUG] after -= center: {world_center - center}")

    smoothed = gaussian(mask, sigma=0.5)

    try:
        verts_vox, faces, _, _ = measure.marching_cubes(
            smoothed, level=0.4, step_size=1, allow_degenerate=False
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
    print(f"[STRUCT DEBUG] world range    : {verts_world.min(axis=0)} – {verts_world.max(axis=0)}")
    print(f"[STRUCT DEBUG] aligned range  : {verts_aligned.min(axis=0)} – {verts_aligned.max(axis=0)}")

    mesh = trimesh.Trimesh(vertices=verts_aligned, faces=faces, process=False)
    if len(mesh.faces) > max_faces:
        mesh = mesh.simplify_quadric_decimation(max_faces)

    return {
        "vertices": mesh.vertices.flatten().tolist(),
        "faces": mesh.faces.flatten().tolist(),
        "vertex_count": len(mesh.vertices),
        "face_count": len(mesh.faces),
    }


# ── Main segmentation entry point ─────────────────────────────────────────────

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
    cached = {}
    for key, info in ALL_STRUCTURES.items():
        out_path = os.path.join(structures_dir, f"{key}.json")
        if os.path.exists(out_path):
            with open(out_path) as f:
                cached[key] = json.load(f)
    if len(cached) == len(ALL_STRUCTURES):
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
    print(f"[STRUCT DEBUG] Brain center vs DKT extent — offset = "
          f"{center} (should land inside above range)")
    print(f"[STRUCT] DKT label values present (sample): {unique_cort[:30]}")

    # Extract all structures — subcortical at full res, cortical at reduced res
    for key, info in ALL_STRUCTURES.items():
        if key in results:
            continue
        out_path = os.path.join(structures_dir, f"{key}.json")
        max_faces = 8000 if info["group"] in CORTICAL_GROUPS else 10000
        mesh_data = _labels_to_mesh(cort_data, cort_affine, info["labels"], center,
                                     max_faces=max_faces)
        if mesh_data:
            full = {**info, "key": key, **mesh_data}
            with open(out_path, "w") as f:
                json.dump(full, f)
            results[key] = full
            print(f"[STRUCT] {info['label']}: {mesh_data['face_count']} faces")
        else:
            print(f"[STRUCT] {info['label']}: no voxels found for labels {info['labels']}")

    print(f"[STRUCT] Done. {len(results)}/{len(ALL_STRUCTURES)} structures extracted.")
    return results
