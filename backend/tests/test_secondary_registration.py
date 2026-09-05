"""
Tests for register_secondary_to_primary — the secondary-MRI (T2 / FLAIR) base
layers in the 2D slice viewer. Self-contained: run directly with

    python backend/tests/test_secondary_registration.py

(No pytest dependency in the neuro-recon env.) Builds its own volumes, so it
needs no patient data and runs in well under a minute.

Two properties are load-bearing for the feature and are what these cover:

  1. The output shares the primary's voxel grid EXACTLY. The slice viewer swaps
     base layers by pointing /mri-slice at a different file and changing nothing
     else, so a secondary whose shape or affine differs would put the structure
     overlay and the electrode contacts on the wrong pixels — or, if the slice
     count differed, ask for indices that do not exist.

  2. The registration actually registers. The first implementation reused the
     CT→MRI settings, which drift on same-modality data: registering a volume to
     ITSELF moved it 13 mm and reported convergence. test_self_registration is
     that bug's regression guard — it is deliberately the cheapest possible case,
     because it is the one that caught it.
"""

import os
import sys
import tempfile

import numpy as np
import SimpleITK as sitk

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

from services.registration import register_secondary_to_primary   # noqa: E402

# Deliberately coarse: these tests are about geometry and convergence, not
# resolution, and a small volume keeps the whole file quick.
SHAPE = (64, 56, 48)
SPACING = 2.0


def _phantom(shape=SHAPE, spacing=SPACING, contrast="t1"):
    """A head-like phantom: an outer shell, an inner body and two off-centre
    structures, so the registration has asymmetric features to lock onto in all
    three axes. `contrast` re-orders the intensities the way a different
    acquisition would, without changing the geometry."""
    zz, yy, xx = np.mgrid[:shape[0], :shape[1], :shape[2]].astype(np.float32)
    cz, cy, cx = [s / 2 for s in shape]

    def ell(dz, dy, dx, rz, ry, rx):
        return (((zz - cz - dz) / rz) ** 2 +
                ((yy - cy - dy) / ry) ** 2 +
                ((xx - cx - dx) / rx) ** 2) <= 1.0

    skull = ell(0, 0, 0, 26, 23, 20) & ~ell(0, 0, 0, 23, 20, 17)
    brain = ell(0, 0, 0, 23, 20, 17)
    ventricle = ell(2, -4, 0, 6, 4, 3)
    lesion = ell(-7, 6, 5, 4, 4, 4)

    vol = np.zeros(shape, dtype=np.float32)
    if contrast == "t1":
        vol[skull] = 120.0
        vol[brain] = 600.0
        vol[ventricle] = 150.0
        vol[lesion] = 300.0
    else:  # "t2": fluid bright, the ordering a T2 actually inverts
        vol[skull] = 60.0
        vol[brain] = 250.0
        vol[ventricle] = 900.0
        vol[lesion] = 700.0
    # A little texture so the metric is not driven by four flat plateaus alone.
    vol += np.random.default_rng(0).normal(0, 4, shape).astype(np.float32) * (vol > 0)

    img = sitk.GetImageFromArray(vol)
    img.SetSpacing((spacing, spacing, spacing))
    img.SetOrigin((-shape[2] * spacing / 2, -shape[1] * spacing / 2, -shape[0] * spacing / 2))
    return img


def _offset_transform(img, rot_deg, trans_mm):
    tf = sitk.Euler3DTransform()
    tf.SetCenter(img.TransformContinuousIndexToPhysicalPoint(
        [(n - 1) / 2.0 for n in img.GetSize()]))
    tf.SetRotation(*[np.radians(d) for d in rot_deg])
    tf.SetTranslation(trans_mm)
    return tf


def _displace(primary, tf, coarsen=1.25):
    """The primary seen on its own, coarser grid and in a different pose — what a
    separately-acquired secondary scan looks like."""
    size = [max(8, int(round(n / coarsen))) for n in primary.GetSize()]
    ref = sitk.Image(size, sitk.sitkFloat32)
    ref.SetSpacing(tuple(s * coarsen for s in primary.GetSpacing()))
    ref.SetOrigin(primary.GetOrigin())
    ref.SetDirection(primary.GetDirection())
    return sitk.Resample(primary, ref, tf.GetInverse(), sitk.sitkLinear, 0.0)


def _head_points(img, n=4000):
    a = sitk.GetArrayFromImage(img)
    idx = np.argwhere(a > np.percentile(a[a > 0], 50))
    if len(idx) > n:
        idx = idx[np.random.default_rng(0).choice(len(idx), n, replace=False)]
    return np.array([img.TransformContinuousIndexToPhysicalPoint(
        [float(k[2]), float(k[1]), float(k[0])]) for k in idx])


def _residual_mm(primary, secondary_img, out_path, truth):
    """Median distance, in mm, between where the registered volume put the
    anatomy and where the known transform says it belongs.

    Measured on the images rather than on transforms, by comparing against an
    'oracle' resample built with the true transform: both sides then go through
    identical interpolation, so what is left is registration error alone.
    """
    oracle = sitk.Resample(secondary_img, primary, truth, sitk.sitkLinear, 0.0)
    got = sitk.ReadImage(out_path, sitk.sitkFloat32)
    a = sitk.GetArrayFromImage(oracle)
    b = sitk.GetArrayFromImage(got)
    # Centre of mass is a robust, interpolation-insensitive summary of "where the
    # anatomy ended up"; for a rigid offset it tracks the error directly.
    def com(arr):
        w = np.where(arr > np.percentile(arr[arr > 0], 50), arr, 0) if (arr > 0).any() else arr
        t = w.sum()
        if t <= 0:
            return np.zeros(3)
        return np.array([(w.sum(axis=(1, 2)) * np.arange(w.shape[0])).sum() / t,
                         (w.sum(axis=(0, 2)) * np.arange(w.shape[1])).sum() / t,
                         (w.sum(axis=(0, 1)) * np.arange(w.shape[2])).sum() / t])
    return float(np.linalg.norm((com(a) - com(b)) * primary.GetSpacing()[0]))


def _run(primary, secondary, tmpdir, name):
    p = os.path.join(tmpdir, f"{name}_primary.nii.gz")
    s = os.path.join(tmpdir, f"{name}_secondary.nii.gz")
    o = os.path.join(tmpdir, f"{name}_out.nii.gz")
    sitk.WriteImage(primary, p)
    sitk.WriteImage(secondary, s)
    register_secondary_to_primary(p, s, o, threads=min(8, os.cpu_count() or 1))
    return o


def test_output_shares_the_primary_grid():
    """The slice viewer's whole design rests on this: identical shape and affine."""
    with tempfile.TemporaryDirectory() as d:
        primary = _phantom(contrast="t1")
        truth = _offset_transform(primary, (3.0, -2.0, 2.5), (5.0, -3.0, 4.0))
        secondary = _displace(_phantom(contrast="t2"), truth)
        out = sitk.ReadImage(_run(primary, secondary, d, "grid"))

        assert out.GetSize() == primary.GetSize(), (out.GetSize(), primary.GetSize())
        assert np.allclose(out.GetSpacing(), primary.GetSpacing()), out.GetSpacing()
        assert np.allclose(out.GetOrigin(), primary.GetOrigin()), out.GetOrigin()
        assert np.allclose(out.GetDirection(), primary.GetDirection()), out.GetDirection()
    print("test_output_shares_the_primary_grid OK")


def test_self_registration_does_not_move():
    """Registering a volume to itself must be a no-op.

    Regression guard: the first implementation reused the CT->MRI settings, which
    failed exactly here -- it moved an image 13 mm away from a perfect start and
    reported that it had converged.
    """
    with tempfile.TemporaryDirectory() as d:
        primary = _phantom(contrast="t1")
        out_path = _run(primary, primary, d, "self")
        got = sitk.ReadImage(out_path, sitk.sitkFloat32)

        a = sitk.GetArrayFromImage(primary).ravel()
        b = sitk.GetArrayFromImage(got).ravel()
        mask = (a != 0) | (b != 0)
        r = float(np.corrcoef(a[mask], b[mask])[0, 1])
        assert r > 0.99, f"self-registration changed the image (r={r:.4f})"
    print("test_self_registration_does_not_move OK")


def test_recovers_a_known_offset():
    """A known rigid offset between two contrasts must actually be removed."""
    with tempfile.TemporaryDirectory() as d:
        primary = _phantom(contrast="t1")
        truth = _offset_transform(primary, (3.0, -2.0, 2.5), (5.0, -3.0, 4.0))
        secondary = _displace(_phantom(contrast="t2"), truth)
        out_path = _run(primary, secondary, d, "offset")

        residual = _residual_mm(primary, secondary, out_path, truth)
        left_in = _residual_mm(
            primary, secondary,
            _resampled_with_identity(primary, secondary, d), truth)
        assert residual < 2.0, (
            f"registration left {residual:.2f} mm of error "
            f"(doing nothing would leave {left_in:.2f} mm)")
    print("test_recovers_a_known_offset OK")


def _resampled_with_identity(primary, secondary, tmpdir):
    """The secondary dropped into the primary's grid with no registration at all
    -- the baseline the real result has to beat."""
    path = os.path.join(tmpdir, "identity.nii.gz")
    sitk.WriteImage(
        sitk.Resample(secondary, primary, sitk.Transform(3, sitk.sitkIdentity),
                      sitk.sitkLinear, 0.0), path)
    return path


if __name__ == "__main__":
    test_output_shares_the_primary_grid()
    test_self_registration_does_not_move()
    test_recovers_a_known_offset()
    print("\nAll secondary-registration tests passed.")
