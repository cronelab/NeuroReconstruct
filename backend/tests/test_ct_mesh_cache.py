"""
Tests for the CT threshold-mesh cache (build_threshold_mesh). Self-contained:
run directly with

    python backend/tests/test_ct_mesh_cache.py

(No pytest dependency in the neuro-recon env.) Builds its own small CT, so it
needs no patient data and runs in a few seconds.

The cached mesh is what the 3D view draws the electrode metal from, while
snapping a contact reads the CT through the CURRENT ct_to_mri.npy. The cache
used to be keyed on the HU window and a bare "registered or not" flag, so after a
re-registration it kept serving the mesh built with the old transform: on
PY26N013 the metal was drawn ~6.5 mm from where the confirmed registration put
it, and every contact snapped a couple of millimetres off the drawn metal.

Covered: a new transform, a new brain-mesh centre or a rewritten CT each yield a
mesh built from them; the same inputs still hit the cache.
"""

import glob
import os
import sys
import tempfile
import time

import nibabel as nib
import numpy as np

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

from services.ct_electrode_extractor import build_threshold_mesh   # noqa: E402


def _write_ct(path, offset_vox=0):
    """Air with one bright metal rod, 1 mm voxels."""
    vol = np.full((40, 40, 40), -1000.0, dtype=np.float32)
    vol[10 + offset_vox:30 + offset_vox, 18:22, 18:22] = 3000.0
    nib.save(nib.Nifti1Image(vol, np.eye(4)), path)


def _translation(dx, dy, dz):
    t = np.eye(4)
    t[:3, 3] = (dx, dy, dz)
    return t


def _verts(mesh):
    return np.array(mesh["vertices"]).reshape(-1, 3)


def _cached(cache):
    return len(glob.glob(os.path.join(cache, "ct_threshold_*.json")))


def test_new_transform_rebuilds():
    """The reported bug: a re-registration must not get the old mesh back."""
    with tempfile.TemporaryDirectory() as d:
        ct, cache = os.path.join(d, "ct.nii.gz"), os.path.join(d, "ct_cache")
        _write_ct(ct)
        a = _verts(build_threshold_mesh(ct, [0, 0, 0], 2000.0, cache, _translation(0, 0, 0)))
        b = _verts(build_threshold_mesh(ct, [0, 0, 0], 2000.0, cache, _translation(0, 0, 6.5)))
        assert a.shape == b.shape
        assert np.allclose(b - a, [0, 0, 6.5]), "second transform was served the first one's mesh"
        again = _verts(build_threshold_mesh(ct, [0, 0, 0], 2000.0, cache, _translation(0, 0, 0)))
        assert np.array_equal(again, a) and _cached(cache) == 2, "same inputs should hit the cache"
    print("test_new_transform_rebuilds OK")


def test_new_mesh_center_rebuilds():
    with tempfile.TemporaryDirectory() as d:
        ct, cache = os.path.join(d, "ct.nii.gz"), os.path.join(d, "ct_cache")
        _write_ct(ct)
        a = _verts(build_threshold_mesh(ct, [0, 0, 0], 2000.0, cache, None))
        b = _verts(build_threshold_mesh(ct, [1, 2, 3], 2000.0, cache, None))
        assert np.allclose(a - b, [1, 2, 3]), "mesh centre change was served the old mesh"
    print("test_new_mesh_center_rebuilds OK")


def test_rewritten_ct_rebuilds():
    with tempfile.TemporaryDirectory() as d:
        ct, cache = os.path.join(d, "ct.nii.gz"), os.path.join(d, "ct_cache")
        _write_ct(ct)
        a = _verts(build_threshold_mesh(ct, [0, 0, 0], 2000.0, cache, None))
        time.sleep(0.05)                       # a distinct mtime even on coarse clocks
        _write_ct(ct, offset_vox=3)            # re-upload at the same path
        b = _verts(build_threshold_mesh(ct, [0, 0, 0], 2000.0, cache, None))
        assert np.isclose(b[:, 0].min() - a[:, 0].min(), 3.0), "replaced CT was served the old mesh"
    print("test_rewritten_ct_rebuilds OK")


if __name__ == "__main__":
    test_new_transform_rebuilds()
    test_new_mesh_center_rebuilds()
    test_rewritten_ct_rebuilds()
    print("\nAll CT mesh cache tests passed.")
