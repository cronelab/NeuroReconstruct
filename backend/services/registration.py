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
import time

# ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS must be set (via launcher.py or env)
# before this import so ITK initializes its thread pool with 1 thread.
import SimpleITK as sitk
sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(1)


def register_ct_to_mri(mri_path: str, ct_path: str, out_path: str, threads: int = 1,
                       init_jitter=None) -> np.ndarray:
    """
    Rigidly register CT to MRI using mutual information.

    Args:
        mri_path: Path to pre-op T1 MRI NIfTI
        ct_path:  Path to post-implant CT NIfTI
        out_path: Where to save the 4x4 transform matrix (.npy)
        threads:  ITK worker threads for this run. Default 1 = deterministic
                  (see module note). Values > 1 give a ~4x speed-up but the
                  optimizer may converge to a different MI optimum (drift is
                  case-dependent, up to several mm), so multithreaded results
                  MUST be human-reviewed before use.
        init_jitter: optional (rot_deg, trans_mm, seed) — perturb the initial
                  transform by Gaussian rotation/translation offsets (used by the
                  multi-start "precise" mode to explore both MI basins).

    Returns:
        (4, 4) numpy array: CT world RAS -> MRI world RAS
    """
    # Per-call thread control. The module/launcher pin the global default to 1;
    # here we raise it only for the duration of this registration, then restore
    # it in the finally so nothing else inherits a nondeterministic default.
    threads = max(1, int(threads))
    sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(threads)
    try:
        return _register_ct_to_mri_impl(mri_path, ct_path, out_path, threads, init_jitter)
    finally:
        sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(1)


def _make_registration_method():
    """Build the ImageRegistrationMethod with the production Mattes-MI / gradient-
    descent / 5-level-pyramid settings. Shared by the single registration and the
    multi-start precise mode so they use identical parameters."""
    reg = sitk.ImageRegistrationMethod()
    # Mattes mutual information — best for multi-modal MRI/CT
    reg.SetMetricAsMattesMutualInformation(numberOfHistogramBins=100)
    reg.SetMetricSamplingStrategy(reg.REGULAR)
    # 50% sampling: enough signal to avoid local minima, still deterministic
    reg.SetMetricSamplingPercentage(0.50)
    reg.SetInterpolator(sitk.sitkLinear)
    # Gradient descent with tighter convergence
    reg.SetOptimizerAsGradientDescent(
        learningRate=1.0, numberOfIterations=800,
        convergenceMinimumValue=1e-9, convergenceWindowSize=40,
    )
    reg.SetOptimizerScalesFromPhysicalShift()
    # Multi-resolution: 5 levels, extra-coarse start to escape local minima.
    reg.SetShrinkFactorsPerLevel(shrinkFactors=[8, 4, 2, 2, 1])
    reg.SetSmoothingSigmasPerLevel(smoothingSigmas=[4, 3, 2, 1, 0])
    reg.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
    return reg


def _build_initial_transform(mri_sitk, ct_sitk, init_jitter=None):
    """Geometry-centered Euler3D initializer, optionally jittered by
    init_jitter=(rot_deg, trans_mm, seed). MOMENTS is avoided — metal electrode
    artifacts skew the intensity center of mass."""
    init = sitk.CenteredTransformInitializer(
        mri_sitk, ct_sitk, sitk.Euler3DTransform(),
        sitk.CenteredTransformInitializerFilter.GEOMETRY,
    )
    if init_jitter is None:
        return init
    rot_deg, trans_mm, seed = init_jitter
    euler = sitk.Euler3DTransform(init)
    rng = np.random.default_rng(int(seed))
    p = list(euler.GetParameters())          # (rx, ry, rz, tx, ty, tz)
    for a in range(3):
        p[a] += np.radians(rng.normal(0.0, rot_deg))
    for a in range(3, 6):
        p[a] += rng.normal(0.0, trans_mm)
    euler.SetParameters(tuple(p))
    return euler


def _final_to_ct_to_mri(final_transform, metric):
    """Convert an Execute(fixed=MRI, moving=CT) result to a CT->MRI RAS matrix.
    Near-zero metric => images already aligned => identity."""
    if metric > -0.1:
        print("[REG] Images appear already aligned (metric near zero). Saving identity.")
        return np.eye(4)
    # Execute returns MRI->CT (fixed->moving); we need CT->MRI, so invert.
    return np.linalg.inv(_sitk_transform_to_ras_matrix(final_transform))


def _register_ct_to_mri_impl(mri_path: str, ct_path: str, out_path: str, threads: int,
                             init_jitter=None) -> np.ndarray:
    print("[REG] Loading images...")
    # Let SimpleITK read NIfTI directly — it handles the coordinate system
    # correctly without manual nibabel->SimpleITK conversion
    mri_sitk = sitk.ReadImage(mri_path, sitk.sitkFloat32)
    ct_sitk  = sitk.ReadImage(ct_path,  sitk.sitkFloat32)

    print(f"[REG] MRI size: {mri_sitk.GetSize()}, spacing: {[round(s,2) for s in mri_sitk.GetSpacing()]}")
    print(f"[REG] CT  size: {ct_sitk.GetSize()},  spacing: {[round(s,2) for s in ct_sitk.GetSpacing()]}")

    print("[REG] Setting up registration...")
    reg = _make_registration_method()
    reg.SetInitialTransform(_build_initial_transform(mri_sitk, ct_sitk, init_jitter), inPlace=False)

    print("[REG] Running registration...")
    _t0 = time.perf_counter()
    final_transform = reg.Execute(mri_sitk, ct_sitk)
    _elapsed = time.perf_counter() - _t0
    print(f"[REG] Done in {_elapsed:.1f} s ({_elapsed/60:.2f} min). "
          f"Metric: {reg.GetMetricValue():.4f}, "
          f"Stop: {reg.GetOptimizerStopConditionDescription()}")
    try:
        _thr = sitk.ProcessObject.GetGlobalDefaultNumberOfThreads()
    except Exception:
        _thr = "?"
    print(f"[REG] Timing context: MRI(fixed) size {mri_sitk.GetSize()}, "
          f"CT(moving) size {ct_sitk.GetSize()}, threads={_thr}")

    matrix = _final_to_ct_to_mri(final_transform, reg.GetMetricValue())
    np.save(out_path, matrix)
    print(f"[REG] Transform saved to {out_path}")
    return matrix


# ── Multi-start "precise" registration (enumerate MI basins for human review) ──
# On ill-conditioned cases the Mattes-MI landscape has multiple near-degenerate
# optima a few mm apart that NO similarity metric can rank (see the registration
# basin-ambiguity investigation). We therefore run many jittered-init starts,
# cluster the results by physical displacement, collapse to <= max_basins, and
# return one representative per basin for a human to choose in the fusion viewer.

def _anatomy_world_points(ct_path: str, hu: float = 300.0, n: int = 60000):
    """Sample CT bony-anatomy voxels (HU>hu) as world-space points, for the
    displacement metric used to cluster transforms."""
    import nibabel as nib
    img = nib.load(ct_path)
    data = img.get_fdata()
    idx = np.argwhere(data > hu)
    if len(idx) == 0:
        idx = np.argwhere(data > -200)
    if len(idx) > n:
        idx = idx[np.random.default_rng(0).choice(len(idx), n, replace=False)]
    return img.affine @ np.c_[idx, np.ones(len(idx))].T.astype(np.float64)


def median_displacement(Ta: np.ndarray, Tb: np.ndarray, points_world: np.ndarray) -> float:
    """Median physical distance (mm) between where Ta and Tb map the same points."""
    return float(np.median(np.linalg.norm((Tb @ points_world)[:3] - (Ta @ points_world)[:3], axis=0)))


def cluster_basins(transforms, points_world, metrics=None, thresh_mm: float = 2.0, max_basins: int = 2):
    """Cluster transforms into AT MOST max_basins basins by median displacement
    (single-linkage at thresh_mm; if more clusters emerge, keep the largest as
    anchors and assign the rest to the nearer). Returns basins sorted by size,
    each: {transform, size, spread_mm, metric, member_indices}."""
    n = len(transforms)
    D = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            D[i, j] = D[j, i] = median_displacement(transforms[i], transforms[j], points_world)

    seen, comps = set(), []
    for i in range(n):
        if i in seen:
            continue
        comp, stack = [], [i]
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k); comp.append(k)
            stack += [j for j in range(n) if j not in seen and D[k, j] < thresh_mm]
        comps.append(comp)

    if len(comps) > max_basins:
        comps.sort(key=len, reverse=True)
        anchors = [min(c, key=lambda a: sum(D[a, b] for b in c)) for c in comps[:max_basins]]
        groups = [[] for _ in anchors]
        for i in range(n):
            g = min(range(len(anchors)), key=lambda gi: D[i, anchors[gi]])
            groups[g].append(i)
        comps = groups

    basins = []
    for comp in comps:
        medoid = min(comp, key=lambda a: sum(D[a, b] for b in comp))
        spread = max((D[a, b] for a in comp for b in comp), default=0.0)
        best_metric = None
        if metrics is not None:
            best_metric = float(min(metrics[c] for c in comp))  # most-negative MI
        basins.append({"transform": transforms[medoid], "size": len(comp),
                       "spread_mm": float(spread), "metric": best_metric,
                       "member_indices": comp})
    basins.sort(key=lambda b: -b["size"])
    return basins


def run_multistart(mri_path: str, ct_path: str, k: int = 11, threads: int = 8,
                   rot_deg: float = 12.0, trans_mm: float = 20.0, seed0: int = 0,
                   thresh_mm: float = 2.0, max_basins: int = 2):
    # Jitter (12 deg, 20 mm) chosen by a coverage sweep on fa94010a: per-start
    # P(basin A) ~= 0.40 -> P(A appears across k=11) ~= 0.996, with no wasted
    # "other"-basin starts (wider jitter plateaus p_A but starts landing in garbage
    # optima). See the jitter-coverage sweep in the basin-ambiguity investigation.
    """Run k jittered-init registrations (all multithreaded for speed), then
    cluster into <= max_basins distinct basins. Returns the basins list from
    cluster_basins (medoid transform + size/spread/metric per basin). The caller
    presents each basin for human selection — no automatic winner is chosen,
    because no metric can rank the near-degenerate basins."""
    threads = max(1, int(threads))
    sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(threads)
    try:
        mri_sitk = sitk.ReadImage(mri_path, sitk.sitkFloat32)
        ct_sitk = sitk.ReadImage(ct_path, sitk.sitkFloat32)
        transforms, metrics = [], []
        for s in range(k):
            reg = _make_registration_method()
            reg.SetInitialTransform(
                _build_initial_transform(mri_sitk, ct_sitk, (rot_deg, trans_mm, seed0 + s)),
                inPlace=False,
            )
            _t0 = time.perf_counter()
            final = reg.Execute(mri_sitk, ct_sitk)
            metric = reg.GetMetricValue()
            transforms.append(_final_to_ct_to_mri(final, metric))
            metrics.append(metric)
            print(f"[MULTISTART] start {s+1}/{k}: {time.perf_counter()-_t0:.0f} s, metric {metric:.4f}")
    finally:
        sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(1)

    points = _anatomy_world_points(ct_path)
    basins = cluster_basins(transforms, points, metrics=metrics,
                            thresh_mm=thresh_mm, max_basins=max_basins)
    print(f"[MULTISTART] {k} starts -> {len(basins)} basin(s): "
          f"sizes {[b['size'] for b in basins]}")
    return basins


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

    print(f"[CT PREP] CT shape={data.shape}, range={data.min():.0f}-{data.max():.0f} HU")

    # Everything above -200 HU is anatomy (soft tissue, bone, metal)
    body_mask = data > -200

    labeled, n = nd_label(body_mask)
    if n == 0:
        print("[CT PREP] WARNING: no voxels above -200 HU - saving original CT unchanged")
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

    print(f"[CT PREP] Found {n} components - keeping largest "
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
