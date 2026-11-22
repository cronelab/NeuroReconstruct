"""
Tests for the skull-band second registration pass (refine_on_skull in
services/registration.py). Self-contained: run directly with

    python backend/tests/test_skull_refine.py

(No pytest dependency in the neuro-recon env.) Builds its own CT and MRI phantoms,
so it needs no patient data.

The phantom is one head drawn twice: as a CT (air -1000, scalp 40, skull 1200,
brain 35 HU) on a 1 mm grid, and as a T1 (bright scalp fat, dark skull, grey
brain, dark ventricles) on a 1.5 mm grid, related by a known rigid transform.
The skull is thicker on one side and has an occipital ridge, so every rotation
and shift changes how the band lines up.

What these cover:

  1. From a pose 5 mm left-right and 2 degrees off, the pass lands on the true
     pose -- the failure it exists for (whole-head MI drifting sideways).
  2. From the true pose it stays put: a correct registration is not disturbed.
  3. A volume that is not a CT in HU (no air below -500) is left alone, which is
     also what keeps the pass out of the thread tests' same-image phantoms.
  4. After a precise re-run, basins that refine onto one pose are merged and
     basins that stay apart are kept.
"""

import os
import sys
import tempfile

import numpy as np
import nibabel as nib
from scipy.ndimage import gaussian_filter
from scipy.spatial.transform import Rotation

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

import services.registration as registration   # noqa: E402
from services.registration import _merge_converged, refine_on_skull   # noqa: E402

# Coarser search grid than production (1.5 mm) so each refine takes seconds.
registration.SKULL_GRID_MM = 3.0
registration.SKULL_MARGIN_STEPS = 3

AIR, SCALP, SKULL, CSF, BRAIN, VENTRICLE = range(6)
CT_HU = np.array([-1000.0, 40.0, 1200.0, 10.0, 35.0, 10.0], dtype=np.float32)
T1 = np.array([5.0, 320.0, 40.0, 60.0, 210.0, 50.0], dtype=np.float32)


def _rigid(rot_deg, shift_mm):
    T = np.eye(4)
    T[:3, :3] = Rotation.from_euler("xyz", rot_deg, degrees=True).as_matrix()
    T[:3, 3] = shift_mm
    return T


# CT world -> MRI world for the phantom pair
TRUE = _rigid((2.0, -3.0, 4.0), (6.0, -4.0, 3.0))


def _tissue(p):
    """Tissue map of the head in MRI world coordinates (mm), p: (3, N)."""
    x, y, z = p

    def inside(rx, ry, rz, cx=0.0, cy=0.0, cz=0.0):
        return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 + ((z - cz) / rz) ** 2 <= 1.0

    out = np.full(x.shape, AIR, dtype=np.int8)
    out[inside(76, 92, 82)] = SCALP
    # skull: 5 mm, 9 mm on the right, plus an occipital ridge
    thick = np.where(x > 0, 9.0, 5.0)
    skull = inside(71, 87, 77) | ((y < -80) & (abs(x) < 20) & (z > -10) & (z < 10) & inside(76, 92, 82))
    out[skull] = SKULL
    out[inside(71 - thick, 87 - thick, 77 - thick)] = CSF
    out[inside(68 - thick, 84 - thick, 74 - thick)] = BRAIN
    out[inside(8, 22, 10, cx=-6, cy=5, cz=10) | inside(8, 22, 10, cx=8, cy=0, cz=12)] = VENTRICLE
    neck = (z < -70) & (out != AIR)
    out[neck] = SCALP                                                  # crude neck: soft tissue only
    return out


def _volume(shape, spacing, origin, to_mri, table, noise, seed):
    idx = np.indices(shape).reshape(3, -1).astype(float)
    world = idx * spacing + np.asarray(origin)[:, None]
    p_mri = (to_mri @ np.r_[world, np.ones((1, world.shape[1]))])[:3]
    lab = _tissue(p_mri)
    vol = table[lab].reshape(shape)
    vol = gaussian_filter(vol, 0.8)
    vol += np.random.default_rng(seed).normal(0, noise, shape).astype(np.float32)
    aff = np.diag([spacing, spacing, spacing, 1.0])
    aff[:3, 3] = origin
    return nib.Nifti1Image(vol, aff)


def _phantoms(d):
    mri = _volume((120, 136, 124), 1.5, (-90, -102, -92), np.eye(4), T1, 6.0, 1)
    ct = _volume((180, 204, 186), 1.0, (-90, -102, -95), TRUE, CT_HU, 8.0, 2)
    mp, cp = os.path.join(d, "mri.nii.gz"), os.path.join(d, "ct.nii.gz")
    nib.save(mri, mp)
    nib.save(ct, cp)
    return mp, cp


def _skull_error(T):
    """Median distance (mm) between where T and the truth put the skull."""
    pts = np.random.default_rng(0).normal(size=(3, 4000))
    pts = pts / np.linalg.norm(pts, axis=0) * np.array([[73], [89], [79]])   # on the skull, CT ~ MRI frame
    ct_pts = np.linalg.inv(TRUE) @ np.r_[pts, np.ones((1, pts.shape[1]))]
    return float(np.median(np.linalg.norm((T @ ct_pts - TRUE @ ct_pts)[:3], axis=0)))


def test_refine_recovers_a_sideways_drift():
    with tempfile.TemporaryDirectory() as d:
        mp, cp = _phantoms(d)
        start = _rigid((0.0, 0.0, 2.0), (5.0, 0.0, 0.0)) @ TRUE
        assert _skull_error(start) > 4.5
        out, info = refine_on_skull(mp, cp, start)
        assert info["applied"], info
        err = _skull_error(out)
        assert err < 0.7, f"refined pose still {err:.2f} mm off ({info})"
    print(f"test_refine_recovers_a_sideways_drift OK ({err:.2f} mm, {info['seconds']} s)")


def test_refine_keeps_a_correct_pose():
    with tempfile.TemporaryDirectory() as d:
        mp, cp = _phantoms(d)
        out, info = refine_on_skull(mp, cp, TRUE)
        err = _skull_error(out)
        assert err < 0.5, f"moved a correct registration by {err:.2f} mm ({info})"
    print(f"test_refine_keeps_a_correct_pose OK ({err:.2f} mm)")


def test_non_hu_volume_is_left_alone():
    with tempfile.TemporaryDirectory() as d:
        mp, _ = _phantoms(d)                   # an MRI passed as the "CT": no air below -500
        out, info = refine_on_skull(mp, mp, TRUE)
        assert not info["applied"] and info["reason"] == "CT not in HU", info
        assert np.array_equal(out, TRUE)
    print("test_non_hu_volume_is_left_alone OK")


def test_basins_that_refine_together_are_merged():
    pts = np.r_[np.random.default_rng(1).uniform(-60, 60, (3, 500)), np.ones((1, 500))]
    a, b, c = np.eye(4), _rigid((0, 0, 0), (0.6, 0, 0)), _rigid((0, 0, 0), (8.0, 0, 0))
    basins = [{"transform": a, "size": 10, "spread_mm": 0.4, "metric": -0.57, "member_indices": list(range(10))},
              {"transform": b, "size": 1, "spread_mm": 0.0, "metric": -0.58, "member_indices": [10]}]
    merged = _merge_converged(basins, pts, thresh_mm=2.0)
    assert len(merged) == 1 and merged[0]["size"] == 11 and merged[0]["metric"] == -0.58, merged
    assert merged[0]["transform"] is a, "the larger basin's pose should be kept"
    apart = [dict(basins[0]), {**basins[1], "transform": c, "size": 1}]
    assert len(_merge_converged(apart, pts, thresh_mm=2.0)) == 2
    print("test_basins_that_refine_together_are_merged OK")


if __name__ == "__main__":
    test_refine_recovers_a_sideways_drift()
    test_refine_keeps_a_correct_pose()
    test_non_hu_volume_is_left_alone()
    test_basins_that_refine_together_are_merged()
    print("\nAll skull-refine tests passed.")
