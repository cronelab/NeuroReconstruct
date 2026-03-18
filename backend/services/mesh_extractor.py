"""
Extracts a brain surface mesh from a T1 MRI NIfTI file.

Skull stripping strategy (in order of preference):
  1. antspynet brain_extraction() — deep-learning T1 brain mask (best)
  2. Morphological fallback — erode → largest component → dilate

After skull stripping: binary fill holes → smooth → marching cubes → decimate.
"""

import numpy as np
import nibabel as nib
from skimage import measure
from skimage.filters import gaussian
from skimage.morphology import binary_closing, binary_erosion, binary_dilation, ball
from scipy.ndimage import binary_fill_holes, label as nd_label
import trimesh
import json
import os


# ── Skull stripping ────────────────────────────────────────────────────────────

def _skull_strip_antspynet(data: np.ndarray, affine: np.ndarray) -> np.ndarray:
    """
    Use antspynet brain_extraction() to produce a binary brain mask.
    Returns None if antspynet/antspy is not installed.
    """
    try:
        import ants
        import antspynet
    except Exception as e:
        print(f"[MESH] antspynet import failed: {e}")
        return None

    print("[MESH] Using antspynet brain extraction...")

    # Build ANTs image from numpy array + affine
    spacing = tuple(float(np.sqrt((affine[:3, i] ** 2).sum())) for i in range(3))
    origin  = tuple(float(affine[i, 3]) for i in range(3))
    direction = (affine[:3, :3] / np.array(spacing)).flatten().tolist()

    ants_img = ants.from_numpy(
        data.astype(np.float32),
        origin=origin,
        spacing=spacing,
        direction=np.array(direction).reshape(3, 3),
    )

    try:
        prob = antspynet.brain_extraction(ants_img, modality="t1", verbose=False)
        mask = ants.threshold_image(prob, 0.5, 1.0, 1, 0)
        brain_mask = mask.numpy().astype(bool)
        n_voxels = brain_mask.sum()
        print(f"[MESH] antspynet mask: {n_voxels} voxels "
              f"({100*n_voxels/data.size:.1f}% of volume)")
        return brain_mask
    except Exception as e:
        print(f"[MESH] antspynet inference failed: {e}")
        return None


def _skull_strip_morphological(data: np.ndarray, affine: np.ndarray,
                                threshold: float) -> np.ndarray:
    """
    Fallback skull stripping via erode→largest component→dilate.
    Uses a tissue-level threshold (at least 15% of max intensity) so that
    scalp/noise/CSF are excluded before erosion, then erodes ~8 mm to
    disconnect residual skull from brain core.
    """
    print("[MESH] Using morphological skull stripping (antspynet not available)...")

    # Use a threshold high enough to capture brain tissue, not just noise.
    # The mesh threshold can be near-zero; skull stripping needs tissue level.
    tissue_threshold = max(threshold, 0.15 * float(data.max()))
    print(f"[MESH] Skull-strip tissue threshold: {tissue_threshold:.2f} (mesh threshold was {threshold:.2f})")
    binary = data > tissue_threshold

    vox_sizes = np.sqrt((affine[:3, :3] ** 2).sum(axis=0))
    mean_vox_mm = float(vox_sizes.mean())
    erode_r = max(3, round(8.0 / mean_vox_mm))
    dilate_r = erode_r + 2
    print(f"[MESH] Voxel size ~{mean_vox_mm:.2f} mm — erode r={erode_r}, dilate r={dilate_r}")

    eroded = binary_erosion(binary, ball(erode_r))
    labeled, n = nd_label(eroded)

    if n == 0:
        print("[MESH] Erosion removed everything — using raw largest component")
        labeled, n = nd_label(binary)

    sizes = np.bincount(labeled.ravel())
    sizes[0] = 0
    brain_core = labeled == sizes.argmax()
    print(f"[MESH] After erosion: {n} components, largest has {brain_core.sum()} voxels")

    brain_mask = binary_dilation(brain_core, ball(dilate_r))
    brain_mask = brain_mask & binary
    return brain_mask


# ── Main extraction ────────────────────────────────────────────────────────────

def extract_brain_mesh(nifti_path: str, output_path: str,
                       threshold: float = None) -> dict:
    """
    Load a NIfTI MRI, skull-strip, run marching cubes, save mesh as JSON.
    """
    img = nib.load(nifti_path)
    img = nib.as_closest_canonical(img)
    data = img.get_fdata()
    affine = img.affine

    print(f"[MESH] Loaded volume: shape={data.shape}, "
          f"intensity range={data.min():.1f}–{data.max():.1f}")

    # ── Threshold for tissue detection ────────────────────────────────────────
    nonzero = data[data > 0]
    if len(nonzero) == 0:
        raise ValueError("NIfTI volume appears empty (all zeros)")

    if threshold is None:
        threshold = float(np.percentile(nonzero, 40))
    print(f"[MESH] Using threshold: {threshold:.2f}")

    # ── Skull stripping ────────────────────────────────────────────────────────
    brain_mask = _skull_strip_antspynet(data, affine)
    used_morphological = brain_mask is None
    if used_morphological:
        brain_mask = _skull_strip_morphological(data, affine, threshold)

    # ── Fill holes + light closing ─────────────────────────────────────────────
    brain_mask = binary_fill_holes(brain_mask)
    brain_mask = binary_closing(brain_mask, ball(1))

    # ── Smooth before marching cubes ──────────────────────────────────────────
    # antspynet mask is already clean — no smoothing needed (would blur sulci).
    # Morphological mask is a blob — refine it by thresholding on actual MRI
    # intensity within the mask so CSF-filled sulci appear as gaps, then apply
    # very light smoothing to remove voxel-staircase artifacts.
    if used_morphological:
        parenchyma_thr = max(threshold, 0.20 * float(data.max()))
        print(f"[MESH] Parenchyma threshold for sulci recovery: {parenchyma_thr:.2f}")
        refined = brain_mask & (data > parenchyma_thr)
        refined = binary_fill_holes(refined)
        smoothed = gaussian(refined.astype(np.float32), sigma=0.5)
    else:
        smoothed = brain_mask.astype(np.float32)

    # ── Marching cubes ────────────────────────────────────────────────────────
    # step_size=1 gives full resolution — sulci are ~2-3 voxels wide so we
    # need every voxel. Decimation below will manage the polygon count.
    verts_vox, faces, normals, _ = measure.marching_cubes(
        smoothed,
        level=0.5,
        step_size=1,
        allow_degenerate=False,
    )

    # ── Voxel → world (RAS) ───────────────────────────────────────────────────
    verts_hom = np.hstack([verts_vox, np.ones((len(verts_vox), 1))])
    verts_world = (affine @ verts_hom.T).T[:, :3]

    # ── Decimate ──────────────────────────────────────────────────────────────
    mesh = trimesh.Trimesh(vertices=verts_world, faces=faces, process=False)
    print(f"[MESH] Pre-decimation: {len(mesh.faces)} faces")
    if len(mesh.faces) > 120000:
        mesh = mesh.simplify_quadric_decimation(120000)
    print(f"[MESH] Post-decimation: {len(mesh.faces)} faces")

    # ── Center at origin ──────────────────────────────────────────────────────
    center = mesh.vertices.mean(axis=0)
    mesh.vertices -= center

    result = {
        "vertices": mesh.vertices.flatten().tolist(),
        "faces": mesh.faces.flatten().tolist(),
        "center": center.tolist(),
        "bounds": {
            "min": mesh.vertices.min(axis=0).tolist(),
            "max": mesh.vertices.max(axis=0).tolist(),
        },
        "vertex_count": len(mesh.vertices),
        "face_count": len(mesh.faces),
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f)

    print(f"[MESH] Saved mesh to {output_path}")
    return result


def voxel_to_world(voxel_coords: list, affine: np.ndarray) -> list:
    coords = np.array(voxel_coords + [1.0])
    return (affine @ coords)[:3].tolist()


def world_to_voxel(world_coords: list, affine: np.ndarray) -> list:
    inv_affine = np.linalg.inv(affine)
    coords = np.array(world_coords + [1.0])
    return (inv_affine @ coords)[:3].tolist()


def get_nifti_affine(nifti_path: str) -> list:
    return nib.load(nifti_path).affine.tolist()
