"""
Tests for services/mni_registration.py's use of recon-all's SynthMorph
registration. Self-contained: run directly with

    python backend/tests/test_mni_registration.py

(No pytest dependency in the neuro-recon env.) Builds synthetic FreeSurfer-style
fields from a known affine, so it needs no patient data.

What is load-bearing, and covered here:

  1. Points: patient RAS -> MNI through the inverse field equals the true map,
     inside SynthMorph's crop box and outside it (where FreeSurfer clamps onto
     the box and the export extrapolates with affine + residual), and off the
     orig grid. Out-of-box contacts are flagged.
  2. A T1 that FreeSurfer saw in a different frame (the import's orig_to_mri)
     is composed in, not ignored.
  3. The ITK fields the export writes mean what the manifest says when ANTs
     reads them: [mri_to_mni_warp] pulls a patient image into MNI, and
     mni_to_mri_invwarp maps patient points into MNI. This is where an RAS/LPS
     sign slip would hide.
  4. The extrapolation is exact for an affine truth even when the LTA's affine
     differs from it, i.e. the residual has the right sign.
"""

import os
import sys
import tempfile

import nibabel as nib
import numpy as np

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

from services import mni_registration as mr                       # noqa: E402


def rot(axis, deg):
    a = np.deg2rad(deg)
    c, s = np.cos(a), np.sin(a)
    i, j = [(1, 2), (0, 2), (0, 1)][axis]
    m = np.eye(4)
    m[i, i], m[i, j], m[j, i], m[j, j] = c, -s, s, c
    return m


def shift(t):
    m = np.eye(4)
    m[:3, 3] = t
    return m


# Truth: orig RAS -> MNI RAS. The LTA is deliberately a little off (as
# SynthMorph's affine stage is off from its final warp): extrapolation must
# still land on the truth, because the residual corrects for it.
B = shift([3.0, -10.0, 8.0]) @ rot(2, 6) @ rot(0, -4) @ np.diag([1.05, 0.97, 1.02, 1.0])
LTA = shift([2.0, -1.5, 1.0]) @ B

# Template grid: 3 mm, axis aligned. Crop box in voxel indices.
AFF_F = np.diag([3.0, 3.0, 3.0, 1.0])
AFF_F[:3, 3] = [-57.0, -66.0, -57.0]
SHAPE_F = (39, 45, 41)
BOX_LO, BOX_HI = np.array([4, 5, 4]), np.array([34, 39, 35])

# orig.mgz-like grid: 2 mm, LIA (FreeSurfer's conformed orientation).
AFF_I = np.array([[-2.0, 0, 0, 64], [0, 0, 2, -64], [0, -2, 0, 64], [0, 0, 0, 1]])
SHAPE_I = (64, 64, 64)


def apply(m, p):
    return p @ m[:3, :3].T + m[:3, 3]


def grid_world(shape, aff):
    idx = np.stack(np.meshgrid(*[np.arange(n) for n in shape], indexing="ij"), -1).reshape(-1, 3)
    return idx, apply(aff, idx.astype(float))


def write_fs_field(path, disp, aff):
    img = nib.Nifti1Image(disp[:, :, :, None, :].astype(np.float32), aff)
    img.header.set_intent("displacement vector", (), "")
    nib.save(img, path)


def make_synthmorph(d, orig_to_mri=None):
    """FreeSurfer-style folder contents for the truth B, with the crop."""
    box_lo_w, box_hi_w = apply(AFF_F, BOX_LO[None].astype(float))[0], apply(AFF_F, BOX_HI[None].astype(float))[0]
    sentinel = AFF_I[:3, 3]                                  # orig voxel (0, 0, 0)
    idx, q = grid_world(SHAPE_F, AFF_F)
    inbox = np.all((idx >= BOX_LO) & (idx <= BOX_HI), axis=1)
    src = np.where(inbox[:, None], apply(np.linalg.inv(B), q), sentinel)
    write_fs_field(os.path.join(d, mr.SM_FWD), (src - q).reshape(SHAPE_F + (3,)), AFF_F)

    _, o = grid_world(SHAPE_I, AFF_I)
    m = np.clip(apply(B, o), box_lo_w, box_hi_w)             # FreeSurfer clamps onto the box
    write_fs_field(os.path.join(d, mr.SM_INV), (m - o).reshape(SHAPE_I + (3,)), AFF_I)

    with open(os.path.join(d, mr.SM_LTA), "w") as fh:
        fh.write("# transform file\ntype      = 1 # LINEAR_RAS_TO_RAS\nnxforms   = 1\n"
                 "mean      = 0.0000 0.0000 0.0000\nsigma     = 1.0000\n1 4 4\n")
        for row in LTA:
            fh.write(" ".join(f"{v:.15e}" for v in row) + "\n")
        fh.write("src volume info\nvalid = 1\n")
    return {"fwd": os.path.join(d, mr.SM_FWD), "inv": os.path.join(d, mr.SM_INV),
            "lta": os.path.join(d, mr.SM_LTA),
            "orig_to_mri": np.eye(4) if orig_to_mri is None else orig_to_mri}


def sample_points(n, rng):
    """Points in the orig volume, about half of them outside the crop box's preimage."""
    return rng.uniform(-55, 55, size=(n, 3))


def truth_mri_to_mni(c, M):
    return apply(B @ np.linalg.inv(M), c)


def _run(M):
    rng = np.random.default_rng(0)
    with tempfile.TemporaryDirectory() as d:
        sm = make_synthmorph(d, M)
        reg = mr.synthmorph_transforms(sm, d)

        # 1 + 2: points, in and out of the box, and the flag
        c_orig = sample_points(400, rng)
        c = apply(M, c_orig)                                 # in mri.nii.gz's frame
        q, flag = reg["map_points"](c)
        err = np.linalg.norm(q - truth_mri_to_mni(c, M), axis=1)
        u = apply(np.linalg.inv(AFF_F), apply(B, c_orig))
        outside = np.any((u < BOX_LO) | (u > BOX_HI), axis=1)
        assert outside.sum() > 50 and (~outside).sum() > 50, "test points should straddle the box"
        assert err.max() < 0.02, f"point map off by {err.max():.3f} mm"
        assert flag[outside].mean() > 0.95, "points FreeSurfer clamped must be flagged"
        deep = np.all((u > BOX_LO + 2) & (u < BOX_HI - 2), axis=1)
        assert not flag[deep].any(), "points well inside the box must not be flagged"

        # off the orig grid entirely: affine + residual, still exact here
        far = apply(M, np.array([[0.0, 0.0, 90.0], [-80.0, 10.0, 0.0]]))
        qf, ff = reg["map_points"](far)
        assert np.abs(qf - truth_mri_to_mni(far, M)).max() < 0.02 and ff.all()

        # 3a: ANTs reads the inverse field as patient points -> MNI
        import ants
        import pandas as pd
        lps = mr._ras_to_lps(c)
        out = ants.apply_transforms_to_points(
            3, pd.DataFrame(lps, columns=["x", "y", "z"]),
            transformlist=[os.path.join(d, "mni_to_mri_invwarp.nii.gz")], whichtoinvert=[False])
        q_ants = mr._lps_to_ras(out[["x", "y", "z"]].to_numpy())
        err_a = np.linalg.norm(q_ants - truth_mri_to_mni(c, M), axis=1)
        assert err_a.max() < 0.05, f"ANTs reading of mni_to_mri_invwarp off by {err_a.max():.3f} mm"

        # 3b: ANTs pulls a patient image into MNI with [mri_to_mni_warp]. A linear
        # ramp in patient RAS makes the expected value exact under trilinear.
        w = np.array([0.7, -0.4, 0.25])
        pat_aff = M @ np.array([[0, 0, -1.5, 60], [1.5, 0, 0, -70],   # permuted axes,
                                [0, 1.5, 0, -65], [0, 0, 0, 1]])      # in M's frame
        shape_p = (94, 90, 86)
        _, pw = grid_world(shape_p, pat_aff)
        ramp = (pw @ w + 200.0).reshape(shape_p).astype(np.float32)
        nib.save(nib.Nifti1Image(ramp, pat_aff), os.path.join(d, "pat.nii.gz"))
        ref = nib.Nifti1Image(np.zeros(SHAPE_F, np.float32), AFF_F)
        nib.save(ref, os.path.join(d, "ref.nii.gz"))
        warped = ants.apply_transforms(fixed=ants.image_read(os.path.join(d, "ref.nii.gz")),
                                       moving=ants.image_read(os.path.join(d, "pat.nii.gz")),
                                       transformlist=reg["fwdtransforms"], interpolator="linear")
        warped.to_filename(os.path.join(d, "warped.nii.gz"))
        got = np.asarray(nib.load(os.path.join(d, "warped.nii.gz")).dataobj).ravel()
        _, qw = grid_world(SHAPE_F, AFF_F)
        src_mri = apply(M @ np.linalg.inv(B), qw)
        expect = src_mri @ w + 200.0
        vp = apply(np.linalg.inv(pat_aff), src_mri)
        ok = np.all((vp > 1) & (vp < np.array(shape_p) - 2), axis=1)
        assert ok.sum() > 1000
        diff = np.abs(got[ok] - expect[ok])
        assert diff.max() < 0.05, f"ANTs image warp with mri_to_mni_warp off by {diff.max():.3f}"
        return err.max(), err_a.max(), diff.max()


def test_points_and_itk_fields_identity_frame():
    e = _run(np.eye(4))
    print(f"  identity frame: point err {e[0]:.4f} mm, ANTs points {e[1]:.4f} mm, image {e[2]:.4f}")


def test_points_and_itk_fields_registered_frame():
    M = shift([4.0, -3.0, 6.0]) @ rot(2, 9) @ rot(1, -5)
    e = _run(M)
    print(f"  registered frame: point err {e[0]:.4f} mm, ANTs points {e[1]:.4f} mm, image {e[2]:.4f}")


def test_lta_must_be_ras_to_ras():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.lta")
        with open(p, "w") as fh:
            fh.write("type      = 0 # LINEAR_VOX_TO_VOX\nnxforms   = 1\n1 4 4\n"
                     + "1 0 0 0\n0 1 0 0\n0 0 1 0\n0 0 0 1\n")
        try:
            mr.read_lta(p)
        except ValueError:
            return
        raise AssertionError("a voxel-to-voxel LTA must be refused")


def test_find_synthmorph_needs_all_three_files():
    with tempfile.TemporaryDirectory() as d:
        sd = os.path.join(d, *mr.SYNTHMORPH_DIR)
        os.makedirs(sd)
        for f in (mr.SM_FWD, mr.SM_INV):
            open(os.path.join(sd, f), "w").close()
        assert mr.find_synthmorph(d) is None
        open(os.path.join(sd, mr.SM_LTA), "w").close()
        found = mr.find_synthmorph(d)
        assert found is not None and np.allclose(found["orig_to_mri"], np.eye(4))


def test_template_is_bundled_on_synthmorph_grid():
    img = nib.load(mr.get_mni_template_path())
    assert img.shape == (193, 229, 193)
    assert np.allclose(img.affine[:3, 3], [-96, -132, -78]) and np.allclose(img.affine[:3, :3], np.eye(3))


if __name__ == "__main__":
    tests = [test_template_is_bundled_on_synthmorph_grid, test_lta_must_be_ras_to_ras,
             test_find_synthmorph_needs_all_three_files,
             test_points_and_itk_fields_identity_frame, test_points_and_itk_fields_registered_frame]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
