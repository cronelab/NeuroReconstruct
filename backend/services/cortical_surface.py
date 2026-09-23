"""Pial-like cortical surface for the 3D viewer's "cortical surface" render mode.

One opaque, cerebrum-only surface with sulci and gyri readable, plus two per-vertex
scalars: a sulcal-depth value that shades the fundi dark, and a DKT parcel id that
recolors the surface by anatomy. Because it is a single opaque mesh it cannot show
the draw-order flicker the nested translucent structure meshes do.

THE DKT LABELS DECIDE WHERE CORTEX IS; THE T1 ONLY REFINES THE OUTER EDGE. The
first five versions did it the other way round -- thresholded the T1 between the
local grey and CSF levels, used the labels only as a working region, and fought the
result with an opening, an intensity ceiling and a travel cap. On the scans this app
actually gets (post-implant, often with weak grey/CSF contrast) that fails three
ways, all measured on PY26N010_dev1, PY26N009_dev3, PY26N002 and PY26N005_dev1:

  * Sulci never open. A sulcus 1-2 mm wide is partial-volumed well above the
    GM/CSF midpoint (PY26N010_dev1: grey 146, CSF 78). Of the 170 cm3 DKT leaves
    open between cortical banks there, 68% ended up in the old mask, so the lateral
    convexity rendered as a lumpy sheet with hardly a sulcus in it.
  * Extra-cerebral tissue next to cortex -- a ~2 mm slab of CSF, arachnoid and vein
    over the temporal lobe -- sits at the grey level, so no intensity ceiling can
    see it and no distance rule can tell it from cortex.
  * Electrode tracks. These MRIs are acquired with the electrodes in: the T1 along
    each shaft is 0.35-0.58x its value 4 mm away. A threshold follows the signal
    void into the brain and the surface shows a crater at every entry site.

The DKT network gets all three right: it leaves the sulci open, stops at the
cortex, and labels straight through the voids. So the mask is now the labels plus
the white matter they enclose, and the T1 may only add a thin, never-bright margin
outside the labelled envelope, where DKT's 1-2 mm erosion clips real cortex.

THE INTERIOR HAS TO BE SEALED EXPLICITLY. Cerebral white matter is not a DKT label
(the inner model's tuple, dkt_lowmem.py:170-172, has no 2/41), and DKT leaves an
unlabelled, WM-bright channel through the basal forebrain 3.2-3.5 mm from any
label. The old fill_holes after a 3 mm dilation therefore never sealed: it added
0-1 cm3 on every test scan and left 190-375 cm3 of deep WM out of the mask. Those
surfaces were hollow, and the inner wall was ~870 of PY26N010_dev1's 2383 cm2 --
which is why "area rises as tissue is removed" once looked like the signature of a
good guard, and why tightening the travel cap once looked like perforation. Filling
after a SEAL_MM dilation that also covers the posterior-fossa labels (otherwise the
peduncles leak) and eroding back closes it on all four.

White matter is then found by intensity and connectivity: unlabelled voxels inside
that envelope brighter than WM_REL x the local grey level, kept where they connect
to the deep structures. Connectivity never runs through cortex, which is not
bright, so a vessel lying in a sulcus is its own small component and is dropped.

Two facts about the label volume that the recipe depends on, both verified against
real data rather than assumed:
  * Cerebral white matter is NOT a label (above). WM shares label 0 with
    background and with the CSF in the sulci.
  * Labels 630/631/632 are cerebellar vermal lobules -- midline, y -44..-58,
    z -32..-53, co-located with the cerebellum centroid. They are excluded with the
    cerebellum and brainstem, or the vermis is left dangling under a cut cerebrum.

Two things from the T1-first versions that still hold and are kept for the margin:
the GM->CSF threshold is a smooth local field (_gm_level_field; the per-lobe GM
median varies +-15%), and every sigma is in voxels, because each scan here is
anisotropic in a different axis.

Measured schema 5 -> 6 (area cm2 / genus; the schema-5 area includes the cavity wall):

    PY26N010_dev1    2383 / 595  ->  2335 / 1352
    PY26N009_dev3    2483 / 969  ->  1783 /  452
    PY26N002         2715 / 590  ->  1708 /  391
    PY26N005_dev1    2046 / 429  ->  1550 /  403
    PY26N004         2388 / 595  ->  1633 /  370

Every one is a single shell with 99.1-100% parcel coverage. Build 32-49 s and peak
working set 1.59-2.05 GB at 4 threads (PY26N004, 126 Mvox, and PY26N010_dev1),
against 52-54 s and 2.09-2.15 GB for schema 5 on the same two scans: the mask is
twice as fast without the geodesic travel cap, which was also the old memory peak.

Known costs. DKT's labels step in ~1 mm, which shows as a faceted texture at close
zoom; blurring the mask by 0.8 mm or more would smooth it but also fuses the sulcal
gaps the labels leave, so it is not done. The same steps leave pinholes, which is
the genus above and shows as specks on the inferior surface. Any DKT labelling
error is inherited, and where DKT is eroded the surface sits up to a millimetre
inside the true pial boundary.

Display surface only -- the label volume is untouched, so contact_labeling.py is
unaffected. It is not a topologically-correct FreeSurfer pial surface: arbitrary
genus, no intensity-gradient sub-voxel refinement, no self-intersection constraint.
Do not present it as a measurement surface.
"""

import base64
import json
import os
import sys
import time

import nibabel as nib
import numpy as np
import trimesh
from scipy.ndimage import (binary_fill_holes, distance_transform_edt,
                           label as nd_label, zoom)
from skimage import measure
from skimage.filters import gaussian

try:                                            # normal package import
    from services.structure_extractor import ALL_STRUCTURES
    from services.worker_mem import (HEAVY_JOB_LOCK, describe_limit,
                                     describe_outcome, run_worker)
except ImportError:                             # run directly as a script
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from services.structure_extractor import ALL_STRUCTURES
    from services.worker_mem import (HEAVY_JOB_LOCK, describe_limit,
                                     describe_outcome, run_worker)

# ── Label sets (FreeSurfer/DKT numbering; see structure_extractor's docstring) ──
CORTICAL_GM = tuple(range(1000, 1036)) + tuple(range(2000, 2036))
SUBCORTICAL = (10, 11, 12, 13, 17, 18, 26, 28, 49, 50, 51, 52, 53, 54, 58, 60)
VENTRICLES = (4, 43, 14, 15, 44)
CSF = (24,)
# Cerebellum, brainstem, and the cerebellar vermal lobules -- the posterior fossa.
EXCLUDE = (6, 7, 8, 45, 46, 47, 16, 630, 631, 632)
REGION = CORTICAL_GM + SUBCORTICAL + VENTRICLES

# ── Parameters ────────────────────────────────────────────────────────────────
# The cerebrum is sealed by filling after this dilation and eroding back. 3 mm
# leaks through the unlabelled basal forebrain on every scan measured; 5 mm is
# the smallest that sealed all four test scans once the fossa labels are included.
SEAL_MM = 6.0
EXCLUDE_DILATE_MM = 1.0     # keep the cerebrum clear of the tentorium / brainstem cut

# White matter: unlabelled, inside the sealed envelope, and at least this bright
# as a multiple of the LOCAL grey level. Deep WM sits at 1.28-1.81x on the test
# scans, so 1.2 cut into it on the dimmest and left pits wherever the surface
# dipped into the missing WM; 1.1 lowered the genus 24-47% on all four without
# closing a sulcus. Labelled cortex is never a candidate, so a cut this close to
# grey only admits unlabelled grey/white partial volume, which is inside anyway.
WM_REL = 1.1

# The T1 may extend the surface this far past the cortical labels, outside the
# labelled envelope only -- that is where DKT's erosion clips real cortex -- and
# never onto tissue brighter than MARGIN_CEIL x the local grey level (vessel, dura).
# 0.5-1.5 mm made no visible difference; between the banks of a sulcus the labels
# decide alone, because any margin there re-closes the gap they leave.
MARGIN_MM = 1.0
MARGIN_CEIL = 1.2
THRESHOLD_FRAC = 0.5        # GM -> CSF midpoint for the margin, against the LOCAL grey level

# Both smoothings are in VOXELS, not millimetres. The staircase this has to
# suppress is an artifact of the acquisition grid, and every scan here is
# anisotropic in a different axis (0.52/0.52/0.98 axial, 1.0/0.57/0.57 sagittal).
# A millimetre-isotropic sigma therefore smooths least along the very axis that
# needs it most, and to compensate it over-blurs in plane. Do not raise
# MASK_SIGMA_VOX to hide the labels' facets: at 0.8 mm the blur fuses the sulci.
DENOISE_VOX = 0.5           # on the T1, before thresholding
MASK_SIGMA_VOX = 0.45       # on the binary mask, before marching cubes

# Local grey level for the threshold. Measured spread of the per-lobe GM median
# about the global one is +-15% on these scans, which at a 0.5 GM->CSF fraction
# moves the effective fraction by ~0.3 -- enough on a low-contrast T1 to flood
# one lobe and erode another.
BIAS_BLOCK_MM = 4.0         # grid the field is estimated on
BIAS_SMOOTH_MM = 30.0       # how far the field is allowed to vary
BIAS_CLIP = (0.55, 1.8)     # guard rails, as a multiple of the global GM median

# The cerebrum crop must hold the depth hull's closing ball plus the margin and
# the Gaussian supports; SEAL_MM is well inside it.
CROP_PAD_MM = 14.0

CLOSE_FOR_DEPTH_MM = 7.0    # ball that bridges a sulcus to define the outer hull
DEPTH_SMOOTH_VOX = 1.0      # de-terrace the depth field (see below)
TAUBIN_LAMB, TAUBIN_NU, TAUBIN_ITERS = 0.5, 0.52, 25   # nu MUST stay positive
DEFAULT_TARGET_FACES = 250_000
INWARD_STEPS_MM = (0.0, 0.75, 1.5, 2.25, 3.0)
VOTE_ITERS = 30

# Bump whenever the payload shape or any geometry parameter above changes: a
# cached file whose schema does not match is rebuilt rather than served, which is
# the only thing standing between a parameter change and stale surfaces on disk.
#   1 -> 2  added parcel_labels
#   2 -> 3  bias-corrected threshold, voxel-unit smoothing, shell-only opening
#   3 -> 4  de-terraced sulcal depth field
#   4 -> 5  FreeSurfer's intensity ceiling and travel cap on the outer shell
#   5 -> 6  label-first mask with a sealed interior; ceiling, travel cap and
#           shell opening removed
SCHEMA_VERSION = 6
CACHE_NAME = "cortical_surface.json"


# ── Morphology in millimetres (EDT-based, exact under anisotropic voxels) ─────
def _dilate(mask, r, vox):
    return distance_transform_edt(~mask, sampling=vox) <= r


def _erode(mask, r, vox):
    return distance_transform_edt(mask, sampling=vox) > r


def _close(mask, r, vox):
    return _erode(_dilate(mask, r, vox), r, vox)


def _sealed_interior(ribbon, excl, vox):
    """Everything the labelled ribbon encloses, as far as the labels reach.

    Filling after a SEAL_MM dilation closes the unlabelled basal-forebrain channel
    that a 3 mm dilation leaves open; eroding back by the same radius returns to
    the labels' own extent, bridging only concavities narrower than 2 * SEAL_MM --
    sulci and fissures, which is why the margin is never added inside this set.
    The fossa labels join the fill, or the cerebral peduncles leak instead; they
    are cut back out afterwards.
    """
    filled = binary_fill_holes(_dilate(ribbon | excl, SEAL_MM, vox))
    inner = _erode(filled, SEAL_MM, vox)
    del filled
    return inner & ~_dilate(excl, EXCLUDE_DILATE_MM, vox)


def _white_matter(candidates, labels):
    """The candidates connected to the deep structures, and nothing else.

    Candidates are bright unlabelled voxels inside the envelope. Connectivity may
    run through the subcortical and ventricle labels, so white matter that the
    basal ganglia or a ventricle splits still counts as one piece, but never
    through cortex, which is not bright: a vessel in a sulcus is its own small
    component.
    """
    hub = np.isin(labels, SUBCORTICAL + VENTRICLES)
    lab, n = nd_label(candidates | hub)
    if n == 0:
        return candidates
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return (lab == sizes.argmax()) & candidates


def _gm_level_field(mri, gm, vox):
    """Local grey-matter intensity, so the threshold follows the bias field.

    Estimated on a ~4 mm grid and interpolated back: the field is smooth by
    construction, and a full-resolution box filter would cost several hundred MB
    of transient float32 on a step that is already the memory peak.

    Blocks with no grey matter in them -- white matter, ventricle, air -- get the
    level of the nearest blocks that do, by normalized convolution (smooth value
    and weight together, then divide). Filling them with zero instead would drag
    the threshold to nothing wherever the cortex is thin.
    """
    f = np.maximum(np.round(BIAS_BLOCK_MM / vox).astype(int), 1)
    pad = [(0, int((-n) % k)) for n, k in zip(mri.shape, f)]
    num = np.pad(np.where(gm, mri, 0.0).astype(np.float32), pad)
    den = np.pad(gm, pad).astype(np.float32)
    sh = num.shape

    def coarse(a):
        return a.reshape(sh[0] // f[0], f[0], sh[1] // f[1], f[1],
                         sh[2] // f[2], f[2]).sum(axis=(1, 3, 5))

    num_c, den_c = coarse(num), coarse(den)
    del num, den
    sig = BIAS_SMOOTH_MM / BIAS_BLOCK_MM / 2.0
    w = (den_c > 0.02 * float(np.prod(f))).astype(np.float32)
    v = np.where(w > 0, num_c / np.maximum(den_c, 1e-6), 0.0).astype(np.float32)
    del num_c, den_c
    for _ in range(3):
        sw = gaussian(w, sigma=sig)
        v = gaussian(v * w, sigma=sig) / np.maximum(sw, 1e-6)
        w = np.maximum(w, (sw > 1e-3).astype(np.float32))
    out = zoom(v, [n / c for n, c in zip(mri.shape, v.shape)], order=1)
    out = out[:mri.shape[0], :mri.shape[1], :mri.shape[2]]
    if out.shape != mri.shape:                    # zoom rounds; pad the last row
        out = np.pad(out, [(0, n - m) for n, m in zip(mri.shape, out.shape)],
                     mode="edge")
    return out.astype(np.float32)


def _largest_cc(mask):
    lab, n = nd_label(mask)
    if n <= 1:
        return mask, n
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return lab == sizes.argmax(), n


def _b64(arr):
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


def _cortical_catalog():
    """DKT label id -> (hex, display name), from the one catalog in
    structure_extractor. Shipped with the payload so the frontend can color and
    name parcels without a second request and without duplicating the catalog --
    it has no structure colour table of its own today, and the cortical surface
    is usable without the per-structure meshes ever being fetched."""
    colors, names = {}, {}
    for info in ALL_STRUCTURES.values():
        for lb in info["labels"]:
            if lb >= 1000:
                colors[int(lb)] = info["color"]
                names[int(lb)] = info["label"]
    return colors, names


def _orient_outward(mesh):
    """marching_cubes winding is sign-ambiguous; make vertex normals point out.

    This is load-bearing: the parcel lookup walks *inward* along -normal, and with
    the winding flipped it walks out into CSF and silently halves coverage.
    """
    c = mesh.vertices.mean(axis=0)
    if np.einsum("ij,ij->i", mesh.vertex_normals, mesh.vertices - c).mean() < 0:
        mesh.faces = mesh.faces[:, ::-1]
        mesh._cache.clear()
    return mesh


def _geodesic_vote(mesh, pid, iters=VOTE_ITERS):
    """Fill unlabeled vertices by plurality vote among labeled mesh neighbours.

    The inward walk alone reaches only ~65%: the misses are concentrated in sulcal
    fundi, where stepping along -normal crosses into the opposite bank or into CSF.
    Voting over the mesh graph fills them from the surrounding banks *geodesically*,
    so a fundus takes the label of its own gyrus rather than the one 2 mm away
    across the sulcus. Mirrors the plurality vote in contact_labeling.py and
    anatomy.js. Measured: 64.9% -> 99.8% in 0.5 s.
    """
    uniq = np.unique(pid[pid > 0])
    if not len(uniq):
        return pid
    code = {int(lb): i + 1 for i, lb in enumerate(uniq)}
    cur = np.zeros(len(pid), np.int32)
    for lb, i in code.items():
        cur[pid == lb] = i

    e = mesh.edges_unique
    src = np.concatenate([e[:, 0], e[:, 1]])
    dst = np.concatenate([e[:, 1], e[:, 0]])
    K = len(uniq) + 1
    for _ in range(iters):
        todo = np.flatnonzero(cur == 0)
        if not len(todo):
            break
        nb = cur[dst]
        valid = nb > 0
        counts = np.zeros((len(pid), K), np.int32)
        np.add.at(counts, (src[valid], nb[valid]), 1)
        best = counts[todo].argmax(1)
        got = counts[todo, best] > 0
        if not got.any():
            break
        cur[todo[got]] = best[got]

    out = np.zeros(len(pid), np.uint16)
    for lb, i in code.items():
        out[cur == i] = lb
    return out


def build_cortical_surface(mri_path, label_path, brain_mesh_path,
                           target_faces=DEFAULT_TARGET_FACES, verbose=True):
    """Build the surface. Returns the payload dict (see module docstring)."""
    t0 = time.time()

    mri_img = nib.as_closest_canonical(nib.load(mri_path))
    lab_img = nib.as_closest_canonical(nib.load(label_path))
    if mri_img.shape != lab_img.shape or not np.allclose(
            mri_img.affine, lab_img.affine, atol=1e-4):
        raise ValueError(
            "MRI and DKT label volume are not on the same grid "
            f"({mri_img.shape} vs {lab_img.shape}) -- cannot sample labels safely")

    affine = mri_img.affine
    vox = np.linalg.norm(affine[:3, :3], axis=0)
    vox_vol = float(np.prod(vox))
    with open(brain_mesh_path) as fh:
        brain_mesh = json.load(fh)
    # The whole app's world space is anchored on THIS centre -- contacts, structure
    # meshes and the CT mesh are all relative to it. Never use our own centroid.
    center = np.array(brain_mesh["center"], np.float32)

    labels = np.round(np.asanyarray(lab_img.dataobj)).astype(np.uint16)
    ribbon = np.isin(labels, REGION)
    if not ribbon.any():
        raise ValueError("no cortical/subcortical labels in the DKT volume")

    # Crop to the cerebrum bounding box and never touch the full head again: a
    # full-volume float32 copy is 463 MB on the largest scan here, and the EDTs
    # below allocate float64. Same argument as structure_extractor.py:264-272.
    nz = np.argwhere(ribbon)
    pad = np.ceil(CROP_PAD_MM / vox).astype(int)
    lo = np.maximum(nz.min(0) - pad, 0)
    hi = np.minimum(nz.max(0) + pad + 1, np.array(labels.shape))
    del nz, ribbon
    sl = tuple(slice(a, b) for a, b in zip(lo, hi))
    labels_c = labels[sl]
    del labels
    mri_c = np.asanyarray(mri_img.dataobj)[sl].astype(np.float32)

    if verbose:
        print(f"[CORTEX] crop {tuple(hi - lo)} vox {np.round(vox, 3)} mm")

    # ── The envelope the labels enclose, sealed (see the module docstring) ─────
    ribbon_c = np.isin(labels_c, REGION)
    excl = np.isin(labels_c, EXCLUDE)
    inner = _sealed_interior(ribbon_c, excl, vox)

    # ── Contrast direction from the data, never from the stored modality ───────
    # T2 uploads are supported (main.py passes modality into skull stripping) and
    # invert GM/CSF, so a hard-coded "parenchyma is brighter" rule is wrong on half
    # the corpus. Deriving it from the medians also beats mesh_extractor.py:176's
    # 0.20 * data.max(), which a single bright artifact voxel skews.
    gm = np.isin(labels_c, CORTICAL_GM)
    med_gm = float(np.median(mri_c[gm]))
    med_csf = float(np.median(mri_c[np.isin(labels_c, VENTRICLES + CSF)]))
    bright = med_gm > med_csf

    # Both the margin's threshold and "brighter than grey matter" are measured
    # against the LOCAL grey level, so they follow the bias field.
    gm_field = np.clip(_gm_level_field(mri_c, gm, vox),
                       BIAS_CLIP[0] * med_gm, BIAS_CLIP[1] * med_gm)
    thr = gm_field + THRESHOLD_FRAC * (med_csf - gm_field)
    smooth = gaussian(mri_c, sigma=np.full(3, DENOISE_VOX))
    del mri_c
    tissue = (smooth > thr) if bright else (smooth < thr)
    del thr
    # rel > 1 always means "on the white-matter side of grey", whichever way the
    # contrast runs.
    rel = (smooth / gm_field) if bright else (gm_field / np.maximum(smooth, 1e-3))
    del smooth, gm_field

    # ── Labels + the white matter they enclose ───────────────────────────────
    wm = _white_matter(inner & ~ribbon_c & (rel > WM_REL), labels_c)
    wm_cm3 = wm.sum() * vox_vol / 1000.0
    core = binary_fill_holes(ribbon_c | wm)
    del wm, ribbon_c

    # ── The T1 margin, outside the labelled envelope only ────────────────────
    near_gm = distance_transform_edt(~gm, sampling=vox) <= MARGIN_MM
    del gm
    margin = tissue & (rel < MARGIN_CEIL) & near_gm & ~inner & ~core
    del tissue, rel, near_gm, inner

    paren = (core | margin) & ~_dilate(excl, EXCLUDE_DILATE_MM, vox)
    del core, margin, excl
    paren = binary_fill_holes(paren)
    paren, ncc = _largest_cc(paren)
    vol_cm3 = paren.sum() * vox_vol / 1000.0
    if verbose:
        print(f"[CORTEX] GM {med_gm:.0f} CSF {med_csf:.0f} "
              f"({'bright' if bright else 'dark'}) | WM {wm_cm3:.0f} cm3 | "
              f"{ncc} components | {vol_cm3:.0f} cm3")

    # ── Sulcal depth field: close the sulci, then EDT inside that hull ─────────
    # 0 on a gyral crown, several mm at a fundus. A Taubin-smoothed reference does
    # NOT work here -- Taubin is deliberately shrink-free, so it keeps the folds
    # and the displacement collapses to +-1 mm.
    hull = _close(paren, CLOSE_FOR_DEPTH_MM, vox)
    depth_field = distance_transform_edt(hull, sampling=vox).astype(np.float32)
    del hull
    # The hull is a voxel set, so distance-to-hull comes out terraced, and the
    # shading ramp turns those terraces into contour rings across a gyrus --
    # obvious on the 1 mm MNI152 template. One voxel of blur removes them and
    # costs nothing: this value only drives a shading ramp.
    depth_field = gaussian(depth_field, sigma=np.full(3, DEPTH_SMOOTH_VOX))

    # ── Surface ───────────────────────────────────────────────────────────────
    vol = gaussian(paren.astype(np.float32), sigma=np.full(3, MASK_SIGMA_VOX))
    del paren
    verts, faces, _, _ = measure.marching_cubes(
        vol, level=0.5, step_size=1, allow_degenerate=False)
    del vol
    hom = np.hstack([verts.astype(np.float32) + lo,
                     np.ones((len(verts), 1), np.float32)])
    del verts
    world = (affine @ hom.T).T[:, :3].astype(np.float32)
    del hom
    mesh = trimesh.Trimesh(vertices=world - center, faces=faces, process=False)
    del world, faces

    # The mask is already one connected component, but blurring it before
    # marching cubes pinches thin necks off into their own shells -- 20-45 of
    # them per scan, each a few hundred faces, floating beside the cortex.
    cc = trimesh.graph.connected_component_labels(
        mesh.face_adjacency, node_count=len(mesh.faces))
    biggest = np.bincount(cc).argmax()
    if not (cc == biggest).all():
        mesh.update_faces(cc == biggest)
        mesh.remove_unreferenced_vertices()
    del cc

    before = mesh.vertices.copy()
    try:
        trimesh.smoothing.filter_taubin(mesh, lamb=TAUBIN_LAMB, nu=TAUBIN_NU,
                                        iterations=TAUBIN_ITERS)
        if not np.isfinite(mesh.vertices).all():
            mesh.vertices = before
    except Exception:
        mesh.vertices = before
    del before

    if target_faces and len(mesh.faces) > target_faces:
        mesh = mesh.simplify_quadric_decimation(target_faces)
    mesh = _orient_outward(mesh)

    # ── Per-vertex scalars, baked on the decimated mesh ───────────────────────
    inv = np.linalg.inv(affine)
    world_v = mesh.vertices + center
    hom = np.hstack([world_v, np.ones((len(world_v), 1), np.float32)])
    vidx = np.rint((inv @ hom.T).T[:, :3]).astype(np.int32) - lo
    np.clip(vidx, 0, np.array(depth_field.shape) - 1, out=vidx)
    sulc = depth_field[vidx[:, 0], vidx[:, 1], vidx[:, 2]].astype(np.float32)
    del depth_field, hom, vidx

    normals = mesh.vertex_normals
    shape = np.array(labels_c.shape)
    pid = np.zeros(len(mesh.vertices), np.uint16)
    for step in INWARD_STEPS_MM:
        todo = pid == 0
        if not todo.any():
            break
        pts = world_v[todo] - normals[todo] * step
        h = np.hstack([pts, np.ones((len(pts), 1), np.float32)])
        vx = np.rint((inv @ h.T).T[:, :3]).astype(np.int32) - lo
        ok = np.all((vx >= 0) & (vx < shape), axis=1)
        vals = np.zeros(len(vx), np.uint16)
        g = vx[ok]
        vals[ok] = labels_c[g[:, 0], g[:, 1], g[:, 2]]
        vals[~np.isin(vals, CORTICAL_GM)] = 0
        idx = np.flatnonzero(todo)
        pid[idx] = np.where(vals > 0, vals, pid[idx])
    del labels_c, world_v
    coverage_walk = float((pid > 0).mean())
    pid = _geodesic_vote(mesh, pid)

    # ── Payload ───────────────────────────────────────────────────────────────
    # sulc is quantized to uint8 over its own observed range: it only ever drives
    # a shading ramp, so 1/255 of the range is far finer than the eye resolves and
    # it costs a quarter of a float32.
    s_lo = float(np.percentile(sulc, 1))
    s_hi = float(np.percentile(sulc, 99))
    span = max(s_hi - s_lo, 1e-6)
    sulc_u8 = np.clip((sulc - s_lo) / span * 255.0, 0, 255).astype(np.uint8)

    parcel_colors, parcel_names = _cortical_catalog()
    v = mesh.vertices.astype(np.float32)
    payload = {
        "schema": SCHEMA_VERSION,
        "encoding": "base64",
        "vertex_count": int(len(mesh.vertices)),
        "face_count": int(len(mesh.faces)),
        "vertices": _b64(v),
        "faces": _b64(mesh.faces.astype(np.uint32)),
        "sulc": _b64(sulc_u8),
        "sulc_range_mm": [s_lo, s_hi],
        "parcel": _b64(pid),
        "parcel_colors": {str(k): c for k, c in parcel_colors.items()},
        "parcel_labels": {str(k): n for k, n in parcel_names.items()},
        "center": np.asarray(center, float).tolist(),
        "bounds": {"min": v.min(0).tolist(), "max": v.max(0).tolist()},
        "stats": {
            "area_cm2": round(float(mesh.area) / 100.0, 1),
            "volume_cm3": round(vol_cm3, 1),
            "parcel_coverage": round(float((pid > 0).mean()), 4),
            "parcel_coverage_walk_only": round(coverage_walk, 4),
            "components": int(ncc),
            "seconds": round(time.time() - t0, 1),
        },
    }
    if verbose:
        st = payload["stats"]
        print(f"[CORTEX] {st['area_cm2']} cm2  {payload['face_count']} faces  "
              f"{payload['vertex_count']} verts  coverage "
              f"{st['parcel_coverage']*100:.1f}%  {st['seconds']} s")
    return payload


INPUT_FILES = ("mri.nii.gz", "structures_cortical.nii.gz", "mesh.json")


def _inputs_fingerprint(recon_dir):
    """(size, mtime) for each input the surface is built from.

    SCHEMA_VERSION alone only catches changes to THIS module. Re-running DKT,
    re-registering, or replacing mri.nii.gz leaves the schema untouched, so the
    surface built from the old inputs is served indefinitely -- and on the
    deployed app the imaging share outlives the container, so a redeploy does not
    clear it either. Size plus mtime is enough: the pipeline writes these files
    once and never edits them in place, whereas hashing hundreds of megabytes on
    every request would not be free.
    """
    out = {}
    for name in INPUT_FILES:
        try:
            st = os.stat(os.path.join(recon_dir, name))
            out[name] = [st.st_size, int(st.st_mtime)]
        except OSError:
            out[name] = None
    return out


def _load_valid_cache(recon_dir):
    """The cached payload when it is still current, else None."""
    cache = os.path.join(recon_dir, CACHE_NAME)
    if not os.path.exists(cache):
        return None
    try:
        with open(cache) as fh:
            got = json.load(fh)
    except (ValueError, OSError):
        return None  # corrupt or truncated -- rebuild
    if got.get("schema") != SCHEMA_VERSION:
        return None
    # A cache written before this check carries no fingerprint. Rebuild it once
    # so it gains one, rather than trusting it for the rest of its life.
    if got.get("inputs") != _inputs_fingerprint(recon_dir):
        return None
    return got


def get_or_build(recon_dir, target_faces=DEFAULT_TARGET_FACES, verbose=True):
    """Cached entry point. Returns the payload, or None when inputs are missing.

    Returns None rather than raising when the DKT volume is absent: the mode is
    simply unavailable for that reconstruction. It must never fall back to another
    reconstruction's surface -- showing one patient's anatomy on another's scan is
    worse than showing none (same rule as main.py's /structures handler).
    """
    cache = os.path.join(recon_dir, CACHE_NAME)
    got = _load_valid_cache(recon_dir)
    if got is not None:
        return got

    mri = os.path.join(recon_dir, "mri.nii.gz")
    lab = os.path.join(recon_dir, "structures_cortical.nii.gz")
    mesh_json = os.path.join(recon_dir, "mesh.json")
    if not all(os.path.exists(p) for p in (mri, lab, mesh_json)):
        return None

    # Sampled BEFORE the build, so it describes the inputs this surface was
    # actually built from. Sampling afterwards would stamp a surface built from
    # the old inputs with the new fingerprint, and an input rewritten mid-build
    # would then never trigger the rebuild it should.
    fingerprint = _inputs_fingerprint(recon_dir)
    payload = build_cortical_surface(mri, lab, mesh_json, target_faces, verbose)
    payload["inputs"] = fingerprint
    tmp = cache + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh)
    os.replace(tmp, cache)
    return payload


def get_or_build_isolated(recon_dir, target_faces=DEFAULT_TARGET_FACES):
    """get_or_build, run in a child process when a build is actually needed.

    Measured peak working set is 1.59-2.05 GB (PY26N004 at 126 Mvox, and
    PY26N010_dev1). It tracks how much surface a scan yields rather than its voxel
    count, because everything works on the cerebrum crop and meshing is the peak --
    modest next to DKT's 13.02 GB. It is still transient allocation that CPython
    will not hand back
    to the OS, and this pipeline's OOM history is precisely about residual web-
    process memory being charged against a later child (see the note in
    mesh_extractor.extract_brain_mesh_isolated). A cache hit costs nothing and is
    served in-process; only a real build pays for the spawn.
    """
    # The worker runs with cwd=backend_dir, so a relative recon_dir would resolve
    # against the wrong root inside the child and it would report "no inputs".
    recon_dir = os.path.abspath(recon_dir)
    got = _load_valid_cache(recon_dir)
    if got is not None:
        return got

    if not all(os.path.exists(os.path.join(recon_dir, f)) for f in INPUT_FILES):
        return None

    if getattr(sys, "frozen", False):
        # Under PyInstaller sys.executable is the bundled app, so spawning it
        # would start a second server rather than a worker.
        return get_or_build(recon_dir, target_faces)

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [sys.executable, os.path.abspath(__file__), recon_dir,
           "--faces", str(target_faces)]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [backend_dir, env["PYTHONPATH"]] if env.get("PYTHONPATH") else [backend_dir])

    if not HEAVY_JOB_LOCK.acquire(blocking=False):
        print("[CORTEX] Another heavy job is running; waiting for it to finish")
        HEAVY_JOB_LOCK.acquire()
    try:
        # Another worker for this reconstruction may have finished while queued.
        got = _load_valid_cache(recon_dir)
        if got is not None:
            print("[CORTEX] Built by another worker while queued")
            return got
        print(f"[CORTEX] Spawning surface worker (pid parent {os.getpid()}, "
              f"container limit {describe_limit()})")
        returncode, peak = run_worker(cmd, cwd=backend_dir, env=env)
    finally:
        HEAVY_JOB_LOCK.release()

    print(f"[CORTEX] {describe_outcome(returncode, peak)}")
    cache = os.path.join(recon_dir, CACHE_NAME)
    if returncode != 0:
        raise RuntimeError(f"cortical surface build {describe_outcome(returncode, peak)}")
    if not os.path.exists(cache):
        raise RuntimeError("cortical surface worker exited 0 but wrote no cache")
    with open(cache) as fh:
        return json.load(fh)


if __name__ == "__main__":
    args = sys.argv[1:]
    faces = DEFAULT_TARGET_FACES
    if "--faces" in args:
        i = args.index("--faces")
        faces = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    if not args:
        print("usage: cortical_surface.py <recon_dir> [...] [--faces N]",
              file=sys.stderr)
        raise SystemExit(2)
    failed = False
    for d in args:
        try:
            r = get_or_build(d, target_faces=faces)
            if r is None:
                # The parent checks inputs before spawning, so reaching here means
                # they went missing underneath us -- a failure, not a no-op.
                failed = True
                print(f"{os.path.basename(d)}: no inputs", file=sys.stderr)
            else:
                print(f"{os.path.basename(d)}: {r['stats']}")
        except Exception as exc:
            failed = True
            print(f"{os.path.basename(d)}: ERROR {type(exc).__name__}: {exc}",
                  file=sys.stderr)
    raise SystemExit(1 if failed else 0)
