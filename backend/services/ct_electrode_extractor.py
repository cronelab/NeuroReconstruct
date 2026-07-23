"""
CT Threshold Mesh Service

Takes a CT NIfTI and a user-defined HU threshold, extracts all voxels
above that threshold, and returns them as a surface mesh for Three.js rendering.

The user adjusts the threshold interactively until only electrode metal
is visible, then clicks directly on the mesh to place contacts.
No automated detection — full user control.

If a ct_to_mri transform matrix is provided (4x4 numpy array), all output
coordinates are transformed into MRI world space so the CT mesh and
contacts align correctly with the brain surface.
"""

import numpy as np
import nibabel as nib
from skimage import measure
from skimage.filters import gaussian
import json
import os
import hashlib



def _resolve_ct_path(ct_path: str) -> str:
    """Use the preprocessed (skull-only) CT if available, else raw CT."""
    masked = os.path.join(os.path.dirname(ct_path), "ct_masked.nii.gz")
    if os.path.exists(masked):
        return masked
    return ct_path

def _apply_transform(verts_world: np.ndarray, transform: np.ndarray) -> np.ndarray:
    """Apply a 4x4 affine transform to an (N, 3) array of world coordinates."""
    hom = np.hstack([verts_world, np.ones((len(verts_world), 1))])
    return (transform @ hom.T).T[:, :3]


def build_threshold_mesh(
    ct_path: str,
    mesh_center: list,
    hu_threshold: float = 1500.0,
    cache_dir: str = None,
    transform: np.ndarray = None,
    hu_ceiling: float = None,
) -> dict:
    """
    Load CT NIfTI, window voxels to the HU range (hu_threshold, hu_ceiling],
    run marching cubes on the binary mask, align to brain mesh coordinates,
    and return geometry.

    Args:
        ct_path:      Path to CT NIfTI file
        mesh_center:  [x,y,z] offset of the MRI brain mesh center
        hu_threshold: lower HU bound — voxels strictly above this are included
        cache_dir:    Directory to cache mesh JSON by threshold value
        transform:    Optional (4,4) array mapping CT world RAS -> MRI world RAS.
                      If provided, vertices are transformed into MRI space before
                      subtracting mesh_center.
        hu_ceiling:   Optional upper HU bound — voxels at or below this are kept.
                      None (default) means no upper limit (floor-only, open top).

    Returns:
        dict with vertices, faces, vertex_count, face_count
    """
    # Cache key includes whether a transform is applied and the ceiling value
    reg_flag = "reg" if transform is not None else "noreg"
    ceil_flag = "open" if hu_ceiling is None else f"{hu_ceiling:g}"

    if cache_dir:
        cache_key = hashlib.md5(
            f"{ct_path}_{hu_threshold}_{ceil_flag}_{reg_flag}".encode()
        ).hexdigest()[:12]
        cache_path = os.path.join(cache_dir, f"ct_threshold_{cache_key}.json")
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                return json.load(f)

    ct_path = _resolve_ct_path(ct_path)
    img = nib.load(ct_path)
    data = img.get_fdata()
    affine = img.affine

    # Create binary mask of voxels inside the HU window (floor, ceiling]
    mask = data > hu_threshold
    if hu_ceiling is not None:
        mask &= data <= hu_ceiling
    binary = mask.astype(np.float32)

    if not binary.any():
        return {
            "vertices": [],
            "faces": [],
            "vertex_count": 0,
            "face_count": 0,
            "hu_threshold": hu_threshold,
            "hu_ceiling": hu_ceiling,
            "empty": True,
        }

    # Light smoothing
    smoothed = gaussian(binary, sigma=0.5) if hu_threshold > -500 else binary.astype(float)

    step = 1 if hu_threshold > 800 else (2 if hu_threshold > -200 else 3)
    try:
        verts_vox, faces, normals, _ = measure.marching_cubes(
            smoothed,
            level=0.5,
            step_size=step,
            allow_degenerate=False,
        )
    except Exception as e:
        return {
            "vertices": [], "faces": [],
            "vertex_count": 0, "face_count": 0,
            "hu_threshold": hu_threshold,
            "hu_ceiling": hu_ceiling,
            "error": str(e),
        }

    # CT voxel → CT world RAS
    verts_hom = np.hstack([verts_vox, np.ones((len(verts_vox), 1))])
    verts_world = (affine @ verts_hom.T).T[:, :3]
    print(f"[CT MESH] CT world center (pre-transform): {verts_world.mean(axis=0).round(1)}")

    # If registered: CT world RAS → MRI world RAS
    if transform is not None:
        verts_world = _apply_transform(verts_world, transform)
        print(f"[CT MESH] CT world center (post-transform): {verts_world.mean(axis=0).round(1)}")

    # Subtract brain mesh center so everything aligns in Three.js
    mc = np.array(mesh_center)
    print(f"[CT MESH] Brain mesh_center: {np.array(mc).round(1)}")
    verts_aligned = verts_world - mc
    print(f"[CT MESH] CT aligned center (should be near 0,0,0): {verts_aligned.mean(axis=0).round(1)}")

    result = {
        "vertices": verts_aligned.flatten().tolist(),
        "faces": faces.flatten().tolist(),
        "vertex_count": len(verts_aligned),
        "face_count": len(faces),
        "hu_threshold": hu_threshold,
        "hu_ceiling": hu_ceiling,
        "empty": False,
    }

    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(result, f)

    return result


def compute_ct_histogram(
    ct_path: str,
    hu_min: float = -1000.0,
    bins: int = 128,
    cache_dir: str = None,
) -> dict:
    """
    Compute a histogram of CT HU values for the threshold slider background.

    The upper end of the histogram (and therefore the slider's ceiling range)
    is the CT's actual maximum HU (`data_max`) — data-driven, so extended-scale
    or metal-artifact-reduction CTs that store metal above the usual 12-bit
    3071 ceiling are not clipped. Returns bin edges, per-bin counts, and the
    real HU data range. Cached to disk since it's static per CT.
    """
    # `_v2` busts caches written by the earlier fixed-3100-ceiling version.
    if cache_dir:
        cache_key = hashlib.md5(
            f"{ct_path}_{hu_min}_{bins}_hist_v2".encode()
        ).hexdigest()[:12]
        cache_path = os.path.join(cache_dir, f"ct_histogram_{cache_key}.json")
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                return json.load(f)

    img = nib.load(_resolve_ct_path(ct_path))
    data = img.get_fdata()

    data_min = float(np.min(data))
    data_max = float(np.max(data))

    # Data-driven top of range; guard against a degenerate flat volume.
    hu_max = data_max if data_max > hu_min else hu_min + 100.0

    counts, edges = np.histogram(data, bins=bins, range=(hu_min, hu_max))

    result = {
        "bin_edges": edges.round(1).tolist(),      # length bins+1
        "counts": counts.astype(int).tolist(),     # length bins
        "hu_min": hu_min,
        "hu_max": round(hu_max, 1),
        "data_min": round(data_min, 1),
        "data_max": round(data_max, 1),
    }

    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(result, f)

    return result


def snap_to_blob_centroid(
    ct_path: str,
    world_pos: list,
    mesh_center: list,
    hu_threshold: float,
    search_radius_mm: float = 8.0,
    transform: np.ndarray = None,
) -> list:
    """
    Given a world-space click position (Three.js coords = registered world - mesh_center),
    find the connected blob of voxels above hu_threshold near that point and
    return its centroid in the same Three.js coordinate space.

    If transform is provided, world_pos is assumed to be in MRI space and is
    converted back to CT space before blob finding, then the result is
    transformed back to MRI space.

    If no blob is found within search_radius_mm, returns the original position.
    """
    ct_path = _resolve_ct_path(ct_path)
    img = nib.load(ct_path)
    data = img.get_fdata()
    affine = img.affine
    inv_affine = np.linalg.inv(affine)
    mc = np.array(mesh_center)

    # Three.js world → add mesh_center → MRI world RAS (or CT world RAS if no transform)
    registered_world = np.array(world_pos) + mc

    # If registered: MRI world → CT world (inverse transform)
    if transform is not None:
        inv_transform = np.linalg.inv(transform)
        ct_world = _apply_transform(registered_world.reshape(1, 3), inv_transform)[0]
    else:
        ct_world = registered_world

    # CT world → CT voxel
    ct_world_hom = np.append(ct_world, 1.0)
    vox_float = (inv_affine @ ct_world_hom)[:3]
    vox = np.round(vox_float).astype(int)

    print(f"[SNAP] world_pos={np.round(world_pos,1)}, mesh_center={np.round(mc,1)}")
    print(f"[SNAP] ct_world={np.round(ct_world,1)}, voxel={vox}, CT shape={data.shape}")

    vox = np.clip(vox, 0, np.array(data.shape) - 1)
    print(f"[SNAP] clamped voxel={vox}, HU={data[vox[0],vox[1],vox[2]]:.0f} (threshold={hu_threshold})")

    shape = np.array(data.shape)
    vox_size = np.sqrt((affine[:3, :3] ** 2).sum(axis=0)).mean()
    search_vox = max(3, int(np.ceil(search_radius_mm / vox_size)))

    lo = np.clip(vox - search_vox, 0, shape - 1)
    hi = np.clip(vox + search_vox + 1, 0, shape)
    region = data[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]
    binary_region = (region > hu_threshold).astype(np.uint8)

    if not binary_region.any():
        print(f"[SNAP] No voxels above threshold in region - returning original")
        return world_pos

    # ── Intensity × proximity weighted centroid (robust to close neighbours) ──
    # A connected-component centroid fails when CT blooming bridges close
    # contacts into one blob — the mean lands in the GAP between them, so every
    # click near either contact snaps to the same midpoint. Instead, anchor to
    # the CLICK: weight each above-threshold voxel by (HU - threshold) — locking
    # onto the bright metal core — times a Gaussian in physical distance from the
    # click, which keeps the result on the contact the user aimed at and
    # suppresses a neighbour a couple mm away. No connected components, so a
    # merged blob no longer collapses distinct contacts to one point.
    region_shape = np.array(region.shape)
    local_vox = np.clip(vox - lo, 0, region_shape - 1).astype(float)

    # Per-axis voxel size (mm) for physical distances (handles anisotropy).
    vox_dims = np.sqrt((affine[:3, :3] ** 2).sum(axis=0))

    coords = np.argwhere(binary_region)  # region-local voxels above threshold
    if len(coords) == 0:
        print("[SNAP] No metal near click - returning original")
        return world_pos

    hu = region[coords[:, 0], coords[:, 1], coords[:, 2]].astype(float)
    offs_mm = (coords - local_vox) * vox_dims       # mm offset from click
    d2 = (offs_mm ** 2).sum(axis=1)                 # squared mm distance
    SIGMA_MM = 0.9                                   # < typical contact spacing
    w = (hu - hu_threshold) * np.exp(-0.5 * d2 / (SIGMA_MM ** 2))

    if w.sum() <= 0:
        # Click landed far from any metal core — snap to the nearest metal voxel.
        centroid_region = coords[np.argmin(d2)].astype(float)
    else:
        centroid_region = (coords * w[:, None]).sum(axis=0) / w.sum()

    centroid_vox = centroid_region + lo  # region-local → full-volume voxel
    print(f"[SNAP] binary voxels: {binary_region.sum()}, "
          f"weighted centroid(vox)={np.round(centroid_vox, 1)}")

    # CT voxel → CT world RAS
    centroid_hom = np.append(centroid_vox, 1.0)
    centroid_ct_world = (affine @ centroid_hom)[:3]

    # If registered: CT world → MRI world RAS
    if transform is not None:
        centroid_world = _apply_transform(centroid_ct_world.reshape(1, 3), transform)[0]
    else:
        centroid_world = centroid_ct_world

    # Subtract mesh_center → Three.js space
    centroid_threejs = centroid_world - mc
    return centroid_threejs.tolist()
