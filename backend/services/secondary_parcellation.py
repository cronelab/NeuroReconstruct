"""
Second-MRI cortical parcellation.

When a reconstruction has an optional second MRI (e.g. a cleaner pre-op T1), the
user can opt to derive the DKT cortical/subcortical parcellation from *it* instead
of the main reconstruction MRI — while keeping everything in the main MRI's
coordinate frame.

Two phases:
  * ``register_mri2_to_main`` — Phase A, runs in the background at upload. ANTs
    **affine** MRI2->main; persists the transform (``mri2_to_main_affine.mat``) so a
    later opt-in is fast. Uses plain ``ants`` (available in the frozen exe).
  * ``build_cortical_labels_from_secondary`` — Phase B, runs only when the user
    opts in. Runs DKT on the *native* second MRI (pristine image), then warps the
    label volume onto the main MRI grid with label-safe (``genericLabel``)
    interpolation and writes ``structures_cortical.nii.gz`` in the main frame.
    Requires antspynet (dev/conda only, like all structure computation).

Coordinate note: the warped label volume lands on the main MRI grid with the main
MRI's affine, so ``structure_extractor``'s existing centering (subtract the
``mesh.json`` center) is correct with no changes. Registration is affine-only by
design — fast and deterministic, but it will not correct genuine brain shift
between the two scans.

Log output must stay ASCII (uvicorn stdout is cp1252 on Windows).
"""

import os

# CPU-only, deterministic (GPU gives no speedup here -- see structure_extractor).
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

AFFINE_TRANSFORM_NAME = "mri2_to_main_affine.mat"


def get_affine_transform_path(recon_dir: str) -> str:
    """Path to the persisted MRI2->main affine transform for a reconstruction."""
    return os.path.join(recon_dir, AFFINE_TRANSFORM_NAME)


def register_mri2_to_main(recon_dir: str, main_mri_path: str, mri2_path: str) -> str:
    """
    Phase A (background): affine-register the second MRI to the main MRI.

    Persists the forward affine transform as ``mri2_to_main_affine.mat`` in
    ``recon_dir`` and returns its path. Uses plain ``ants`` (no antspynet), so it
    also works in the frozen exe. Overwrites any existing transform.
    """
    import shutil
    import ants

    out_path = get_affine_transform_path(recon_dir)
    fixed = ants.image_read(main_mri_path)   # target frame (main MRI)
    moving = ants.image_read(mri2_path)      # second MRI
    print("[MRI2] Affine-registering second MRI -> main MRI ...")
    reg = ants.registration(
        fixed=fixed, moving=moving, type_of_transform="Affine", verbose=False
    )
    # For a pure affine, fwdtransforms is a single .mat mapping moving -> fixed.
    fwd = reg["fwdtransforms"]
    if not fwd:
        raise RuntimeError("ANTs affine registration returned no transform")
    shutil.copy(fwd[0], out_path)
    print(f"[MRI2] Saved affine transform -> {out_path}")
    return out_path


def build_cortical_labels_from_secondary(recon_dir: str, main_mri_path: str,
                                         mri2_path: str, out_label_path: str) -> str:
    """
    Phase B (opt-in): parcellate the native second MRI and warp the labels onto the
    main MRI grid, writing ``structures_cortical.nii.gz`` in the main frame.

    Requires antspynet for DKT. If the affine transform is not present yet (Phase A
    still running or failed), it is computed first.
    """
    import numpy as np
    import ants
    import antspynet

    transform_path = get_affine_transform_path(recon_dir)
    if not os.path.exists(transform_path):
        print("[MRI2] Affine transform missing; registering now...")
        register_mri2_to_main(recon_dir, main_mri_path, mri2_path)

    fixed = ants.image_read(main_mri_path)   # target grid/frame (main MRI)
    moving = ants.image_read(mri2_path)      # native second MRI

    print(f"[MRI2] Running DKT parcellation on native second MRI: {mri2_path}")
    dkt = antspynet.desikan_killiany_tourville_labeling(
        moving, do_preprocessing=True, verbose=False
    )  # labels returned in the second MRI's own space

    print("[MRI2] Warping label volume onto main MRI grid (genericLabel)...")
    warped = ants.apply_transforms(
        fixed=fixed, moving=dkt,
        transformlist=[transform_path], interpolator="genericLabel",
    )
    warped.to_filename(out_label_path)
    print(f"[MRI2] Wrote {out_label_path}")

    # Integrity check: label-safe interpolation must keep values integer-valued.
    arr = warped.numpy()
    frac = float(np.mean(np.abs(arr - np.round(arr)) > 1e-6))
    n_labels = int(np.unique(np.round(arr).astype(np.int64)).size)
    print(f"[MRI2] Label integrity: {frac*100:.4f}% non-integer voxels "
          f"(should be 0), {n_labels} distinct labels.")
    return out_label_path
