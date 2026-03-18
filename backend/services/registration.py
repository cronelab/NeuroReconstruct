"""
MRI-CT Co-registration Service

Rigidly registers the post-implant CT to the pre-op MRI using SimpleITK
mutual information. Produces a 4x4 affine transform matrix (CT world RAS
-> MRI world RAS) saved as a .npy file next to the CT.

Usage:
    matrix = register_ct_to_mri(mri_path, ct_path, out_path)
    # matrix is a (4,4) numpy array
    # apply: mri_world = matrix @ [ct_x, ct_y, ct_z, 1]
"""

import numpy as np
import os

# ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS must be set (via launcher.py or env)
# before this import so ITK initializes its thread pool with 1 thread.
import SimpleITK as sitk
sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(1)


def register_ct_to_mri(mri_path: str, ct_path: str, out_path: str) -> np.ndarray:
    """
    Rigidly register CT to MRI using mutual information.

    Args:
        mri_path: Path to pre-op T1 MRI NIfTI
        ct_path:  Path to post-implant CT NIfTI
        out_path: Where to save the 4x4 transform matrix (.npy)

    Returns:
        (4, 4) numpy array: CT world RAS -> MRI world RAS
    """
    print("[REG] Loading images...")
    # Let SimpleITK read NIfTI directly — it handles the coordinate system
    # correctly without manual nibabel->SimpleITK conversion
    mri_sitk = sitk.ReadImage(mri_path, sitk.sitkFloat32)
    ct_sitk  = sitk.ReadImage(ct_path,  sitk.sitkFloat32)

    print(f"[REG] MRI size: {mri_sitk.GetSize()}, spacing: {[round(s,2) for s in mri_sitk.GetSpacing()]}")
    print(f"[REG] CT  size: {ct_sitk.GetSize()},  spacing: {[round(s,2) for s in ct_sitk.GetSpacing()]}")

    print("[REG] Setting up registration...")
    reg = sitk.ImageRegistrationMethod()

    # Mattes mutual information — best for multi-modal MRI/CT
    reg.SetMetricAsMattesMutualInformation(numberOfHistogramBins=100)
    reg.SetMetricSamplingStrategy(reg.REGULAR)
    # 50% sampling: enough signal to avoid local minima, still deterministic
    reg.SetMetricSamplingPercentage(0.50)

    reg.SetInterpolator(sitk.sitkLinear)

    # Gradient descent with tighter convergence
    reg.SetOptimizerAsGradientDescent(
        learningRate=1.0,
        numberOfIterations=800,
        convergenceMinimumValue=1e-9,
        convergenceWindowSize=40,
    )
    reg.SetOptimizerScalesFromPhysicalShift()

    # Geometry-based centering — aligns image geometric centers.
    # MOMENTS is unreliable for post-implant CT due to metal electrode artifacts
    # skewing the intensity center of mass.
    initial_transform = sitk.CenteredTransformInitializer(
        mri_sitk,
        ct_sitk,
        sitk.Euler3DTransform(),
        sitk.CenteredTransformInitializerFilter.GEOMETRY,
    )
    reg.SetInitialTransform(initial_transform, inPlace=False)

    # Multi-resolution: 5 levels, extra-coarse start to escape local minima.
    # The shrink-8 level blurs both images heavily — the optimizer finds a
    # basin of attraction that carries through to finer levels.
    reg.SetShrinkFactorsPerLevel(shrinkFactors=[8, 4, 2, 2, 1])
    reg.SetSmoothingSigmasPerLevel(smoothingSigmas=[4, 3, 2, 1, 0])
    reg.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()

    print("[REG] Running registration (this may take 1-3 minutes)...")
    final_transform = reg.Execute(mri_sitk, ct_sitk)
    print(f"[REG] Done. Metric: {reg.GetMetricValue():.4f}, "
          f"Stop: {reg.GetOptimizerStopConditionDescription()}")

    metric = reg.GetMetricValue()
    if metric > -0.1:
        print("[REG] Images appear already aligned (metric near zero). "
              "Saving identity transform — no spatial correction will be applied.")
        matrix = np.eye(4)
    else:
        # SimpleITK Execute(fixed=MRI, moving=CT) returns a transform that maps
        # MRI→CT (fixed→moving). We need CT→MRI, so convert to matrix then invert.
        matrix = np.linalg.inv(_sitk_transform_to_ras_matrix(final_transform))

    np.save(out_path, matrix)
    print(f"[REG] Transform saved to {out_path}")
    return matrix


def load_transform(transform_path: str) -> np.ndarray:
    """Load a saved 4x4 CT->MRI transform matrix."""
    return np.load(transform_path)


def get_transform_path(ct_path: str) -> str:
    """Derive the transform path from the CT path."""
    return os.path.join(os.path.dirname(ct_path), "ct_to_mri.npy")


def get_masked_ct_path(ct_path: str) -> str:
    """Derive the masked CT path from the original CT path."""
    return os.path.join(os.path.dirname(ct_path), "ct_masked.nii.gz")


def preprocess_ct(ct_path: str, out_path: str) -> str:
    """
    Strip non-anatomical structures from a CT scan:
      1. Threshold at -200 HU to remove air and background
      2. Keep only the largest connected component — removes scanner table,
         headrest, and disconnected noise, leaving only the patient head/body
      3. Fill internal holes so electrode contacts inside the skull are not masked

    Saves the masked CT as a new NIfTI alongside the original.
    """
    import nibabel as nib
    from scipy.ndimage import label as nd_label, binary_fill_holes

    print("[CT PREP] Loading CT for preprocessing...")
    img = nib.load(ct_path)
    data = img.get_fdata()

    print(f"[CT PREP] CT shape={data.shape}, range={data.min():.0f}–{data.max():.0f} HU")

    # Everything above -200 HU is anatomy (soft tissue, bone, metal)
    body_mask = data > -200

    labeled, n = nd_label(body_mask)
    if n == 0:
        print("[CT PREP] WARNING: no voxels above -200 HU — saving original CT unchanged")
        img.to_filename(out_path)
        return out_path

    sizes = np.bincount(labeled.ravel())
    sizes[0] = 0  # ignore background label
    largest = sizes.argmax()
    patient_mask = labeled == largest

    # Cylindrical crop to remove the scanner table.
    # In a head CT the skull is always centered in the axial (x-y) plane.
    # The table intrudes from below and may survive the largest-component step
    # because it's physically connected through the headrest.
    # Solution: restrict the mask to a cylinder centered on the axial FOV center
    # with radius = 90% of the inscribed FOV circle. This removes flat table
    # extensions while keeping the full skull and all electrode contacts.
    nx, ny, nz = data.shape
    cx, cy = nx / 2.0, ny / 2.0
    vox_x = float(np.sqrt((img.affine[:3, 0] ** 2).sum()))
    vox_y = float(np.sqrt((img.affine[:3, 1] ** 2).sum()))
    head_radius_mm = 0.90 * min(cx * vox_x, cy * vox_y)
    xi = np.arange(nx)
    yi = np.arange(ny)
    XX, YY = np.meshgrid(xi, yi, indexing="ij")
    dist_mm = np.sqrt(((XX - cx) * vox_x) ** 2 + ((YY - cy) * vox_y) ** 2)
    cylinder_3d = (dist_mm < head_radius_mm)[:, :, np.newaxis]
    patient_mask = patient_mask & cylinder_3d
    print(f"[CT PREP] Cylindrical crop: radius={head_radius_mm:.1f} mm")

    # Fill internal holes so sinuses/ears/skull interior don't get masked
    patient_mask = binary_fill_holes(patient_mask)

    print(f"[CT PREP] Found {n} components — keeping largest "
          f"({patient_mask.sum()} voxels, {100*patient_mask.mean():.1f}% of volume)")

    # Set everything outside the patient to -1000 HU (air)
    masked_data = data.copy()
    masked_data[~patient_mask] = -1000

    masked_img = nib.Nifti1Image(masked_data, img.affine, img.header)
    masked_img.to_filename(out_path)
    print(f"[CT PREP] Masked CT saved to {out_path}")
    return out_path


# ── Internal helpers ──────────────────────────────────────────────────────────

def _sitk_transform_to_ras_matrix(transform) -> np.ndarray:
    """
    Convert a SimpleITK rigid transform to a 4x4 matrix in RAS world space.

    SimpleITK uses LPS convention. nibabel and Three.js use RAS.
    Conversion: RAS = diag(-1,-1,1,1) @ LPS

    The rigid transform in LPS maps:
        p_out = R * (p_in - center) + center + translation

    We build the full 4x4 in LPS then convert to RAS.
    """
    import SimpleITK as sitk

    # Unwrap CompositeTransform if needed
    inner = transform
    if hasattr(transform, 'GetName') and transform.GetName() == 'CompositeTransform':
        ct = sitk.CompositeTransform(transform)
        ct.FlattenTransform()
        inner = ct.GetNthTransform(0)

    euler = sitk.Euler3DTransform(inner)

    R = np.array(euler.GetMatrix()).reshape(3, 3)
    t = np.array(euler.GetTranslation())
    c = np.array(euler.GetFixedParameters()[:3])  # rotation center

    # Full translation in LPS: T_full = -R*c + c + t
    t_full = -R @ c + c + t

    # 4x4 in LPS
    M_lps = np.eye(4)
    M_lps[:3, :3] = R
    M_lps[:3,  3] = t_full

    # Convert LPS -> RAS by flipping x and y axes
    flip = np.diag([-1., -1., 1., 1.])
    M_ras = flip @ M_lps @ flip

    return M_ras
