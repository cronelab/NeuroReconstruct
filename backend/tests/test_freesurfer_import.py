"""
Tests for services/freesurfer_import.py -- uploading FreeSurfer outputs as an
alternative parcellation + cortical surface. Self-contained: run directly with

    python backend/tests/test_freesurfer_import.py

(No pytest dependency in the neuro-recon env.) Builds a synthetic FreeSurfer
subject, so it needs no patient data.

What is load-bearing, and covered here:

  1. Coordinates. Surfaces arrive in tkr-RAS and must land in the app frame
     (mri.nii.gz scanner RAS minus the mesh centre). The synthetic orig.mgz is
     OBLIQUE on purpose: the c_ras-only shortcut is right for axis-aligned scans
     and wrong for oblique ones, which is what the clinical T1s here are.
  2. Labels on the MRI grid. Every consumer of structures_cortical.nii.gz reads
     it on mri.nii.gz's grid; the resampled volume must have that exact shape
     and affine, and the right label at a known world point.
  3. Parcel ids by NAME. A DKT annot's colour table need not be in aparc order;
     ids must still come out as the catalog's 1000+/2000+ numbers.
  4. Switching source round-trips the fast labels without recomputing them.
  5. Uploads cannot write outside their directory, and incomplete or
     multi-subject zips are refused with a readable message.
  6. A zip made from a different T1 of the same head is registered, not
     silently misplaced.
"""

import json
import os
import sys
import tempfile
import zipfile

import nibabel as nib
import numpy as np

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

from services import freesurfer_import as fsi                     # noqa: E402
from services.structure_extractor import ALL_STRUCTURES            # noqa: E402

LH_CENTRE = np.array([-28.0, 4.0, 12.0])
RH_CENTRE = np.array([28.0, 4.0, 12.0])
RADIUS = 18.0
MESH_CENTRE = np.array([3.0, -5.0, 10.0])


def _rot(deg_x, deg_z):
    ax, az = np.radians(deg_x), np.radians(deg_z)
    rx = np.array([[1, 0, 0], [0, np.cos(ax), -np.sin(ax)], [0, np.sin(ax), np.cos(ax)]])
    rz = np.array([[np.cos(az), -np.sin(az), 0], [np.sin(az), np.cos(az), 0], [0, 0, 1]])
    return rz @ rx


def _affine(rot, vox, shape, centre_world):
    """vox->world with the volume centre at centre_world."""
    a = np.eye(4)
    a[:3, :3] = rot @ np.diag(vox)
    a[:3, 3] = centre_world - a[:3, :3] @ (np.array(shape) / 2.0)
    return a


def _phantom_world(xyz):
    """A head-ish intensity field defined in world mm, so any grid sampling it
    sees the same anatomy: two bright hemispheres in a dimmer head."""
    head = (np.sum((xyz / np.array([70.0, 85.0, 75.0])) ** 2, axis=-1) < 1) * 40.0
    hemis = np.zeros(xyz.shape[:-1])
    for c in (LH_CENTRE, RH_CENTRE):
        hemis += (np.linalg.norm(xyz - c, axis=-1) < RADIUS) * 80.0
    ridge = 20.0 * (np.sin(xyz[..., 0] / 7.0) > 0.6)       # asymmetric texture
    return head + hemis + ridge * (head > 0)


SECTORS = 24


def _sector(d):
    """Azimuthal wedge index of offsets d from a hemisphere centre."""
    theta = np.arctan2(d[..., 1], d[..., 0])
    return np.clip(((theta + np.pi) / (2 * np.pi) * SECTORS).astype(int), 0, SECTORS - 1)


def _sample(affine, shape):
    ijk = np.stack(np.meshgrid(*(np.arange(s) for s in shape), indexing="ij"), -1)
    world = ijk @ affine[:3, :3].T + affine[:3, 3]
    return world, _phantom_world(world)


def _icosphere(level=3):
    t = (1 + 5 ** 0.5) / 2
    v = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t),
         (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
    f = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4),
         (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8),
         (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    v = [np.array(p, float) / np.linalg.norm(p) for p in v]
    for _ in range(level):
        cache, nf = {}, []

        def mid(a, b):
            key = (min(a, b), max(a, b))
            if key not in cache:
                m = v[a] + v[b]
                v.append(m / np.linalg.norm(m))
                cache[key] = len(v) - 1
            return cache[key]
        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        f = nf
    return np.array(v), np.array(f, dtype=np.int32)


def make_subject(root, name="S1", mri_offset=(0.0, 0.0, 0.0), dkt_name="aparc.DKTatlas"):
    """A FreeSurfer subject dir plus a matching reconstruction dir.

    mri_offset shifts the anatomy in mri.nii.gz relative to FreeSurfer's T1,
    which is what a different session looks like to the importer.
    Returns (subject_dir, recon_dir, expected) where expected holds the
    per-vertex DKT ids the annots encode.
    """
    sd = os.path.join(root, name)
    for sub in ("mri", "surf", "label", "scripts"):
        os.makedirs(os.path.join(sd, sub))

    # orig.mgz: 64^3 at 2 mm, oblique (10 deg about x, 7 about z).
    oshape = (64, 64, 64)
    oaff = _affine(_rot(10, 7), (2.0, 2.0, 2.0), oshape, np.array([2.0, -3.0, 8.0]))
    _, odata = _sample(oaff, oshape)
    orig = nib.MGHImage(odata.astype(np.uint8), oaff)
    nib.save(orig, os.path.join(sd, "mri", "orig.mgz"))
    orig = nib.load(os.path.join(sd, "mri", "orig.mgz"))
    world_to_tkr = np.linalg.inv(fsi.tkr_to_scanner(orig))

    # aparc.DKTatlas+aseg on orig's grid: each hemisphere sphere cut into
    # SECTORS azimuthal wedges (labels base+2 ...), enough distinct cortical
    # labels for the importer's coverage check, each wide enough to survive
    # resampling onto the coarser MRI grid.
    world, _ = _sample(oaff, oshape)
    lab = np.zeros(oshape, np.int32)
    for base, c in ((1000, LH_CENTRE), (2000, RH_CENTRE)):
        inside = np.linalg.norm(world - c, axis=-1) < RADIUS
        lab[inside] = base + 2 + _sector(world - c)[inside]
    lab[(lab == 0) & (np.linalg.norm(world - LH_CENTRE, axis=-1) < RADIUS + 3)] = 2
    nib.save(nib.MGHImage(lab, oaff), os.path.join(sd, "mri", "aparc.DKTatlas+aseg.mgz"))

    unit, faces = _icosphere(3)
    expected = {}
    for hemi, c in (("lh", LH_CENTRE), ("rh", RH_CENTRE)):
        world_v = c + RADIUS * unit
        tkr = world_v @ world_to_tkr[:3, :3].T + world_to_tkr[:3, 3]
        # Only the .T1 file, as in a copy that lost recon-all's lh.pial symlink.
        nib.freesurfer.write_geometry(os.path.join(sd, "surf", f"{hemi}.pial.T1"), tkr, faces)
        # Signed like FreeSurfer's: negative crowns (top), positive fundi.
        nib.freesurfer.write_morph_data(os.path.join(sd, "surf", f"{hemi}.sulc"),
                                        (unit[:, 2] * -6.0).astype(np.float32))
        # DKT colour table deliberately NOT in aparc order.
        names = [b"unknown", b"superiorfrontal", b"precentral"]
        ctab = np.array([[25, 5, 25, 0, 0], [20, 220, 160, 0, 0], [60, 20, 220, 0, 0]])
        labels = np.where(unit[:, 1] > 0.2, 1, np.where(unit[:, 1] < -0.2, 2, -1))
        nib.freesurfer.write_annot(os.path.join(sd, "label", f"{hemi}.{dkt_name}.annot"),
                                   labels, ctab, names, fill_ctab=True)
        base = 1000 if hemi == "lh" else 2000
        expected[hemi] = np.where(labels == 1, base + 28, np.where(labels == 2, base + 24, 0))
        d_names = [b"Unknown", b"G_front_sup", b"S_central"]
        nib.freesurfer.write_annot(os.path.join(sd, "label", f"{hemi}.aparc.a2009s.annot"),
                                   labels, ctab, d_names, fill_ctab=True)
    with open(os.path.join(sd, "scripts", "build-stamp.txt"), "w") as fh:
        fh.write("freesurfer-linux-ubuntu24_x86_64-8.2.0-test\n")

    # The reconstruction: mri.nii.gz on a DIFFERENT, differently oblique grid.
    recon = os.path.join(root, "recon")
    os.makedirs(recon)
    mshape = (80, 90, 70)
    maff = _affine(_rot(-6, 3), (1.7, 1.6, 1.9), mshape, np.array([0.0, 0.0, 6.0]))
    mworld, _ = _sample(maff, mshape)
    mdata = _phantom_world(mworld - np.asarray(mri_offset))
    nib.save(nib.Nifti1Image(mdata.astype(np.float32), maff), os.path.join(recon, "mri.nii.gz"))
    with open(os.path.join(recon, "mesh.json"), "w") as fh:
        json.dump({"center": MESH_CENTRE.tolist()}, fh)
    return sd, recon, expected


def zip_subject(sd, out):
    with zipfile.ZipFile(out, "w") as zf:
        for dirpath, _, files in os.walk(sd):
            for f in files:
                p = os.path.join(dirpath, f)
                zf.write(p, os.path.relpath(p, os.path.dirname(sd)))
    return out


def _decode(payload, key, dtype):
    import base64
    return np.frombuffer(base64.b64decode(payload[key]), dtype=dtype)


# ── Tests ─────────────────────────────────────────────────────────────────────
def test_tkr_to_scanner_handles_oblique():
    rot = _rot(12, -9)
    aff = _affine(rot, (1.0, 1.0, 1.0), (32, 32, 32), np.array([5.0, -7.0, 3.0]))
    img = nib.MGHImage(np.zeros((32, 32, 32), np.uint8), aff)
    m = fsi.tkr_to_scanner(img)
    # Any voxel: tkr coordinates of it, mapped through m, give its world point.
    vox = np.array([3.0, 20.0, 11.0, 1.0])
    tkr = img.header.get_vox2ras_tkr() @ vox
    assert np.allclose(m @ tkr, aff @ vox, atol=1e-4)
    # And the shortcut (tkr + c_ras) is NOT the same on an oblique scan.
    c_ras = (aff @ np.array([16.0, 16.0, 16.0, 1.0]))[:3]
    assert not np.allclose(tkr[:3] + c_ras, (aff @ vox)[:3], atol=0.5)
    print("ok  tkr_to_scanner handles an oblique orig.mgz")


def test_inspect_zip_rejects_bad_uploads():
    with tempfile.TemporaryDirectory() as d:
        sd, _, _ = make_subject(d)
        good = zip_subject(sd, os.path.join(d, "good.zip"))
        chosen = fsi.inspect_zip(good)
        assert chosen["surf/lh.pial"].endswith(("surf/lh.pial", "surf/lh.pial.T1"))
        assert "label/lh.aparc.a2009s.annot" in chosen

        evil = os.path.join(d, "evil.zip")
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("S1/surf/lh.pial", b"x")
            zf.writestr("../../outside.txt", b"x")
        _expect_error(evil, "Unsafe path")

        incomplete = os.path.join(d, "incomplete.zip")
        with zipfile.ZipFile(good) as src, zipfile.ZipFile(incomplete, "w") as dst:
            for n in src.namelist():
                if not n.endswith("rh.sulc"):
                    dst.writestr(n, src.read(n))
        _expect_error(incomplete, "surf/rh.sulc")

        two = os.path.join(d, "two.zip")
        with zipfile.ZipFile(good) as src, zipfile.ZipFile(two, "w") as dst:
            for n in src.namelist():
                dst.writestr(n, src.read(n))
                dst.writestr("S2/" + n.split("/", 1)[1], src.read(n))
        _expect_error(two, "more than one subject")

        not_zip = os.path.join(d, "x.zip")
        with open(not_zip, "wb") as fh:
            fh.write(b"not a zip")
        _expect_error(not_zip, "Not a zip")
    print("ok  inspect_zip refuses unsafe, incomplete, multi-subject and non-zip uploads")


def _expect_error(path, fragment):
    try:
        fsi.inspect_zip(path)
    except fsi.FreeSurferImportError as e:
        assert fragment in str(e), f"{fragment!r} not in {e}"
        return
    raise AssertionError(f"{path} was accepted")


def test_import_places_surface_and_labels():
    with tempfile.TemporaryDirectory() as d:
        sd, recon, expected = make_subject(d)
        info = fsi.import_freesurfer(recon, zip_subject(sd, os.path.join(d, "s.zip")),
                                     verbose=False)
        assert info["alignment"]["registered"] is False, info["alignment"]
        assert info["alignment"]["ncc"] > 0.9, info["alignment"]
        assert info["freesurfer_version"].endswith("8.2.0-test")
        assert info["atlases"] == ["dkt", "destrieux"], info["atlases"]

        payload = fsi.load_surface(recon)
        v = _decode(payload, "vertices", np.float32).reshape(-1, 3) + MESH_CENTRE
        f = _decode(payload, "faces", np.uint32).reshape(-1, 3)
        n_lh = payload["lh_vertex_count"]
        # Coordinates: each hemisphere's sphere lands back on its world centre.
        assert np.allclose(v[:n_lh].mean(0), LH_CENTRE, atol=0.05), v[:n_lh].mean(0)
        assert np.allclose(v[n_lh:].mean(0), RH_CENTRE, atol=0.05), v[n_lh:].mean(0)
        assert np.allclose(np.linalg.norm(v[:n_lh] - LH_CENTRE, axis=1), RADIUS, atol=0.01)
        # Faces index the merged array and wind outward on both hemispheres.
        assert f.max() == len(v) - 1 and f[len(f) // 2:].min() >= n_lh
        for sl in (slice(0, len(f) // 2), slice(len(f) // 2, None)):
            tri = v[f[sl]]
            c = v[np.unique(f[sl])].mean(0)
            vol = np.einsum("ij,ij->i", tri[:, 0] - c,
                            np.cross(tri[:, 1] - c, tri[:, 2] - c)).sum()
            assert vol > 0
        # sulc: non-negative depth, so the viewer's shading never sees NaN.
        assert payload["sulc_range_mm"][0] >= 0
        # DKT ids by name, colours from the catalog.
        parcel = _decode(payload, "parcel", np.uint16)
        assert np.array_equal(parcel, np.concatenate([expected["lh"], expected["rh"]]))
        precentral = ALL_STRUCTURES["precentral_l"]
        assert payload["parcel_colors"]["1024"] == precentral["color"]
        assert payload["parcel_labels"]["1024"] == precentral["label"]
        des = payload["parcellations"]["destrieux"]
        des_ids = np.frombuffer(__import__("base64").b64decode(des["parcel"]), np.uint16)
        assert set(np.unique(des_ids)) == {0, 11101, 11102, 12101, 12102}
        assert des["labels"]["12102"] == "Right S_central"

        # Labels: on the MRI grid exactly, with the right value at a known point.
        mri = nib.load(os.path.join(recon, "mri.nii.gz"))
        lab = nib.load(os.path.join(recon, fsi.FS_DIR, fsi.FS_LABELS))
        assert lab.shape == mri.shape and np.allclose(lab.affine, mri.affine)
        assert lab.get_data_dtype() == np.int16
        out = np.asanyarray(lab.dataobj)
        # Exactly nearest-neighbour: any voxel, looked up directly through world
        # space, agrees. Catches axis-order and slab-reshape mistakes.
        src = nib.load(os.path.join(sd, "mri", "aparc.DKTatlas+aseg.mgz"))
        src_data = np.asanyarray(src.dataobj)
        rng = np.random.default_rng(0)
        vox = np.stack([rng.integers(0, s, 4000) for s in mri.shape], 1)
        world = vox @ mri.affine[:3, :3].T + mri.affine[:3, 3]
        sijk = np.rint(world @ np.linalg.inv(src.affine)[:3, :3].T
                       + np.linalg.inv(src.affine)[:3, 3]).astype(int)
        ok = np.all((sijk >= 0) & (sijk < src_data.shape), 1)
        direct = np.zeros(len(vox), int)
        direct[ok] = src_data[sijk[ok, 0], sijk[ok, 1], sijk[ok, 2]]
        assert np.array_equal(out[vox[:, 0], vox[:, 1], vox[:, 2]], direct)
        # And anatomically right: the core of wedge 5 of the left sphere is 1007.
        mw = np.stack(np.meshgrid(*(np.arange(s) for s in mri.shape), indexing="ij"), -1)
        mw = mw @ mri.affine[:3, :3].T + mri.affine[:3, 3]
        d = mw - LH_CENTRE
        r = np.linalg.norm(d, axis=-1)
        mid = (5.5 / SECTORS) * 2 * np.pi - np.pi
        core = (r > 9) & (r < 16) & (np.abs(np.arctan2(d[..., 1], d[..., 0]) - mid) < np.radians(3))
        assert np.mean(out[core] == 1007) > 0.9, np.unique(out[core], return_counts=True)
    print("ok  import places both pial surfaces, parcels and labels correctly")


def test_switching_source_round_trips_fast_labels():
    with tempfile.TemporaryDirectory() as d:
        sd, recon, _ = make_subject(d)
        fsi.import_freesurfer(recon, zip_subject(sd, os.path.join(d, "s.zip")), verbose=False)
        active = os.path.join(recon, fsi.ACTIVE_LABELS)
        fast = nib.Nifti1Image(np.full((4, 4, 4), 7, np.float32), np.eye(4))
        nib.save(fast, active)
        os.makedirs(os.path.join(recon, "structures"))
        open(os.path.join(recon, "cortical_surface.json"), "w").close()

        assert fsi.active_source(recon) == "fast"
        fsi.activate_source(recon, "freesurfer")
        assert fsi.active_source(recon) == "freesurfer"
        assert nib.load(active).shape == nib.load(os.path.join(recon, "mri.nii.gz")).shape
        assert os.path.exists(os.path.join(recon, fsi.FAST_BACKUP))
        assert not os.path.exists(os.path.join(recon, "structures"))
        assert not os.path.exists(os.path.join(recon, "cortical_surface.json"))
        fsi.activate_source(recon, "freesurfer")          # idempotent
        assert os.path.exists(os.path.join(recon, fsi.FAST_BACKUP))

        fsi.activate_source(recon, "fast")
        assert fsi.active_source(recon) == "fast"
        assert nib.load(active).shape == (4, 4, 4)         # the original, untouched
        assert not os.path.exists(os.path.join(recon, fsi.FAST_BACKUP))

        # Without fast labels, switching back removes FreeSurfer's rather than
        # leaving them to pose as the fast parcellation.
        os.remove(active)
        fsi.activate_source(recon, "freesurfer")
        fsi.activate_source(recon, "fast")
        assert not os.path.exists(active)

        # A switch to fast interrupted after restoring the backup leaves the
        # marker saying "freesurfer" over the real fast labels. Finishing the
        # switch must keep them, not delete them as if they were a copy.
        nib.save(fast, active)
        with open(os.path.join(recon, fsi.SOURCE_MARKER), "w") as fh:
            fh.write("freesurfer\n")
        fsi.activate_source(recon, "fast")
        assert os.path.exists(active) and nib.load(active).shape == (4, 4, 4)

        fsi.activate_source(recon, "freesurfer")
        fsi.remove(recon)
        assert fsi.active_source(recon) == "fast" and fsi.summary(recon) is None
        assert nib.load(active).shape == (4, 4, 4)
    print("ok  switching source round-trips the fast labels without recomputing")


def test_annot_from_another_run_is_refused():
    with tempfile.TemporaryDirectory() as d:
        sd, recon, _ = make_subject(d)
        unit, faces = _icosphere(2)                         # fewer vertices
        nib.freesurfer.write_annot(os.path.join(sd, "label", "rh.aparc.DKTatlas.annot"),
                                   np.zeros(len(unit), int),
                                   np.array([[1, 2, 3, 0, 0]]), [b"precentral"], fill_ctab=True)
        try:
            fsi.import_freesurfer(recon, zip_subject(sd, os.path.join(d, "s.zip")), verbose=False)
        except fsi.FreeSurferImportError as e:
            assert "different runs" in str(e), e
        else:
            raise AssertionError("mismatched annot accepted")
        assert not os.path.exists(os.path.join(recon, fsi.FS_DIR))
        assert not os.path.exists(os.path.join(recon, fsi.STAGING_DIR))
    print("ok  an annot that does not match its surface is refused cleanly")


def test_fastsurfer_names_are_accepted():
    with tempfile.TemporaryDirectory() as d:
        sd, recon, expected = make_subject(d, dkt_name="aparc.DKTatlas.mapped")
        os.rename(os.path.join(sd, "mri", "aparc.DKTatlas+aseg.mgz"),
                  os.path.join(sd, "mri", "aparc.DKTatlas+aseg.deep.mgz"))
        fsi.import_freesurfer(recon, zip_subject(sd, os.path.join(d, "s.zip")), verbose=False)
        parcel = _decode(fsi.load_surface(recon), "parcel", np.uint16)
        assert np.array_equal(parcel, np.concatenate([expected["lh"], expected["rh"]]))
    print("ok  FastSurfer's file names import the same way")


def test_different_t1_is_registered():
    """mri.nii.gz shifted 7 mm against FreeSurfer's T1: the importer must notice
    and register, and the surfaces must follow the anatomy, not the header."""
    offset = np.array([7.0, 0.0, 0.0])
    with tempfile.TemporaryDirectory() as d:
        sd, recon, _ = make_subject(d, mri_offset=offset)
        info = fsi.import_freesurfer(recon, zip_subject(sd, os.path.join(d, "s.zip")),
                                     verbose=False)
        assert info["alignment"]["registered"] is True, info["alignment"]
        v = _decode(fsi.load_surface(recon), "vertices", np.float32).reshape(-1, 3) + MESH_CENTRE
        n_lh = info["vertex_count"] // 2
        err = np.linalg.norm(v[:n_lh].mean(0) - (LH_CENTRE + offset))
        assert err < 1.5, f"surface {err:.2f} mm from the shifted anatomy"
    print(f"ok  a different T1 is registered (surface within {err:.2f} mm)")


if __name__ == "__main__":
    test_tkr_to_scanner_handles_oblique()
    test_inspect_zip_rejects_bad_uploads()
    test_import_places_surface_and_labels()
    test_switching_source_round_trips_fast_labels()
    test_annot_from_another_run_is_refused()
    test_fastsurfer_names_are_accepted()
    test_different_t1_is_registered()
    print("all FreeSurfer import tests passed")
