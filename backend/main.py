"""
Brain Reconstruction Viewer - FastAPI Backend
Handles: auth, reconstruction CRUD, NIfTI mesh extraction, electrode management
"""

import os
from collections import OrderedDict
import sys
import uuid
import json
import hashlib
import asyncio
from typing import List, Optional
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
import io
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel

from database import init_db, get_db, AsyncSessionLocal, engine, IS_SQLITE, User, Reconstruction, ElectrodeShaft, ElectrodeContact, SeegRecording
from auth import (
    verify_password, hash_password, create_access_token,
    get_current_user, require_editor, require_admin
)
from services.mesh_extractor import (extract_brain_mesh_isolated,
                                     get_nifti_affine, voxel_to_world)
from services.worker_mem import describe_limit
import numpy as np
from PIL import Image
from fastapi.responses import Response as FastAPIResponse

# ── In-memory NIfTI slice cache ─────────────────────────────────────────────
#
# These caches used to be plain dicts that only ever grew. Keyed by file path
# they are shared across users, so ten people looking at one scan cost what one
# person costs -- but ten people opening ten different scans accumulated a copy
# of each, and nothing was ever released. get_fdata() also returns float64,
# doubling data that is float32 on disk. Together that put roughly 13 GB of
# volumes inside a 15.62 GB container, and the failure mode is not slowness:
# one OOM takes out the single uvicorn worker and drops everybody at once.
#
# So: keep them as float32, and bound the total with an LRU. The budget is
# deliberately modest because a parcellation child peaks near 13 GB and the
# parent has to fit alongside it -- see BEFORE_HEAVY_JOB, which empties these
# first.
_VOLUME_CACHE_BYTES = int(float(os.environ.get("NEURO_VOLUME_CACHE_GB", "3.0")) * 2**30)


class _VolumeCache:
    """Least-recently-used cache of decoded NIfTI volumes, bounded by bytes."""

    def __init__(self, name: str):
        self.name = name
        self._items = OrderedDict()          # key -> entry dict (holds "data")

    @staticmethod
    def _size(entry) -> int:
        total = entry["data"].nbytes
        for k in ("png_cache", "fusion_png_cache"):
            for v in entry.get(k, {}).values():
                # cached render tuples carry the PNG bytes in position 0
                blob = v[0] if isinstance(v, tuple) else v
                total += len(blob) if isinstance(blob, (bytes, bytearray)) else 0
        return total

    def total_bytes(self) -> int:
        return sum(self._size(e) for e in self._items.values())

    def get(self, key):
        entry = self._items.get(key)
        if entry is not None:
            self._items.move_to_end(key)     # most recently used
        return entry

    def put(self, key, entry):
        self._items[key] = entry
        self._items.move_to_end(key)
        _evict_across_caches()
        return entry

    def clear(self):
        n, mb = len(self._items), self.total_bytes() / 2**20
        self._items.clear()
        return n, mb

    def evict_oldest(self):
        if not self._items:
            return None
        key, entry = self._items.popitem(last=False)
        return key, self._size(entry)


_mri_volume_cache = _VolumeCache("mri")

def _evict_across_caches():
    """Trim to the byte budget, always dropping the oldest entry in whichever
    cache currently holds the most."""
    caches = [_mri_volume_cache, _ct_volume_cache, _struct_overlay_cache]
    guard = 0
    while sum(c.total_bytes() for c in caches) > _VOLUME_CACHE_BYTES and guard < 64:
        guard += 1
        victim = max(caches, key=lambda c: c.total_bytes())
        dropped = victim.evict_oldest()
        if not dropped:
            break
        key, size = dropped
        # basename alone is useless here: every volume is called mri.nii.gz.
        label = os.path.join(*str(key).replace("\\", "/").split("/")[-2:])
        print(f"[CACHE] evicted {victim.name} {label} ({size / 2**20:.0f} MB)")


def _drop_volume_caches():
    """Release every cached volume. Registered as a BEFORE_HEAVY_JOB hook."""
    freed = 0.0
    for c in (_mri_volume_cache, _ct_volume_cache, _struct_overlay_cache):
        _, mb = c.clear()
        freed += mb
    if freed:
        print(f"[CACHE] released {freed:.0f} MB before spawning a heavy worker")


# Parcellation runs in a child that peaks near 13 GB; the parent cannot be
# holding gigabytes of display volumes at the same time.
import services.worker_mem as _worker_mem  # noqa: E402
_worker_mem.BEFORE_HEAVY_JOB.append(_drop_volume_caches)


def _get_mri_volume(mri_path: str):
    """Load and canonicalize NIfTI once; cache the float array."""
    cached = _mri_volume_cache.get(mri_path)
    if cached is None:
        import nibabel as nib
        img = nib.load(mri_path)
        img_ras = nib.as_closest_canonical(img)
        # float32, not get_fdata()'s float64: these are display slices, and the
        # volume is float32 on disk anyway.
        data = np.asanyarray(img_ras.dataobj, dtype=np.float32)
        # Pre-compute per-axis normalization stats (percentile over whole volume)
        axis_stats = {}
        for ax, name in [(0, "sagittal"), (1, "coronal"), (2, "axial")]:
            flat = data.ravel()
            nonzero = flat[flat > 0]
            vmin = float(np.percentile(nonzero, 2)) if len(nonzero) else 0.0
            vmax = float(np.percentile(nonzero, 98)) if len(nonzero) else 1.0
            axis_stats[name] = (vmin, vmax)
        cached = _mri_volume_cache.put(mri_path, {
            "data": data,
            "affine": img_ras.affine,
            "axis_stats": axis_stats,
            "png_cache": {},  # (axis, slice_idx) -> png_bytes
        })
    return cached

def _render_slice(mri_path: str, axis: str, slice_idx: int):
    """Return (png_bytes, shape, world_coord, voxel_size_mm, count, actual_idx,
    inv_affine, vol_shape, px_w_mm, px_h_mm, plane_normal, plane_offset), cached.
    px_w_mm/px_h_mm are the physical mm-per-pixel along the displayed width/height,
    for aspect-correct rendering of anisotropic voxels. plane_normal/plane_offset
    define the slice plane exactly — see the comment on their computation below."""
    vol = _get_mri_volume(mri_path)
    data = vol["data"]
    affine = vol["affine"]
    ax = {"sagittal": 0, "coronal": 1, "axial": 2}[axis]
    n = data.shape[ax]
    if slice_idx < 0 or slice_idx >= n:
        slice_idx = n // 2

    key = (axis, slice_idx)
    if key in vol["png_cache"]:
        return vol["png_cache"][key]  # full tuple cached

    if ax == 0:   sl = data[slice_idx, :, :]
    elif ax == 1: sl = data[:, slice_idx, :]
    else:         sl = data[:, :, slice_idx]

    sl = np.fliplr(np.rot90(sl, k=1))

    vmin, vmax = vol["axis_stats"][axis]
    sl_norm = np.clip((sl - vmin) / max(vmax - vmin, 1e-6), 0, 1)
    sl_uint8 = (sl_norm * 255).astype(np.uint8)

    buf = io.BytesIO()
    Image.fromarray(sl_uint8, mode="L").save(buf, format="PNG", optimize=False, compress_level=1)
    png_bytes = buf.getvalue()

    voxel_sizes = np.sqrt((affine[:3, :3] ** 2).sum(axis=0))
    voxel_size_mm = float(voxel_sizes[ax])
    inv_affine = np.linalg.inv(affine)

    # ── Slice plane geometry in world RAS ─────────────────────────────────────
    # A constant-voxel-index plane is perpendicular to a world axis only when the
    # affine is axis-aligned. Oblique acquisitions (AC-PC tilt and friends) are
    # rotated, so "the world coordinate of this slice" is not one number — it
    # varies across the plane. On this project's own data a constant-index axial
    # plane sweeps ~18 mm of world z, i.e. ~31 slices. Hence two values:
    #
    #   world_coord  — the value at the CENTRE of the plane. A representative
    #                  figure for labels and readouts, and identical to the old
    #                  corner-voxel formula whenever the affine is axis-aligned.
    #
    #   plane_normal / plane_offset — the plane itself, as  normal · P = offset
    #                  with |normal| == 1. So |normal · P - offset| is the exact
    #                  perpendicular distance in mm from any world point P to
    #                  this slice, for any affine.
    #
    # To test whether a point lies on this slice, use plane_normal/plane_offset.
    # Never compare a point's world x/y/z against world_coord.
    row = inv_affine[ax, :3]                  # ∇(voxel index `ax`) w.r.t. world
    row_norm = float(np.linalg.norm(row))
    plane_normal = (row / row_norm).tolist()
    plane_offset = float((slice_idx - inv_affine[ax, 3]) / row_norm)

    centre_vox = [(d - 1) / 2.0 for d in data.shape]
    centre_vox[ax] = float(slice_idx)
    world_coord = float((affine @ np.array([*centre_vox, 1.0]))[ax])

    # Physical mm-per-pixel of the *displayed* image. After the np.fliplr(np.rot90)
    # above, displayed WIDTH maps to the slice's "row" data-axis and displayed
    # HEIGHT to its "col" data-axis:
    #   sagittal(ax0): rows=y(1) cols=z(2) | coronal(ax1): rows=x(0) cols=z(2) | axial(ax2): rows=x(0) cols=y(1)
    row_ax, col_ax = {0: (1, 2), 1: (0, 2), 2: (0, 1)}[ax]
    px_w_mm = float(voxel_sizes[row_ax])
    px_h_mm = float(voxel_sizes[col_ax])

    result = (png_bytes, sl_uint8.shape, world_coord, voxel_size_mm, n, slice_idx,
              inv_affine.flatten().tolist(), list(data.shape), px_w_mm, px_h_mm,
              plane_normal, plane_offset)
    vol["png_cache"][key] = result  # cache the full tuple
    return result


from services.electrode_service import autofill_contacts
from services.ct_electrode_extractor import build_threshold_mesh, snap_to_blob_centroid, _resolve_ct_path, compute_ct_histogram

# ── Fusion (CT-in-MRI-space) slice cache ──────────────────────────────────────
# Fixed bone/metal window for the registration-QA fusion view — this is for
# visually checking alignment (skull outline, ventricles), not diagnostic
# reading, so a fixed window is simpler and more consistent across patients
# than a per-volume percentile (which metal artifacts would skew).
_CT_FUSION_WINDOW_MIN = -100.0
_CT_FUSION_WINDOW_MAX = 1900.0

_ct_volume_cache = _VolumeCache("ct")  # ct_path -> {"data", "affine", "fusion_png_cache"}

def _get_ct_volume(ct_path: str):
    """Load and canonicalize the (masked, if available) CT NIfTI once; cache the float array."""
    cached = _ct_volume_cache.get(ct_path)
    if cached is None:
        import nibabel as nib
        resolved = _resolve_ct_path(ct_path)
        img = nib.load(resolved)
        img_ras = nib.as_closest_canonical(img)
        cached = _ct_volume_cache.put(ct_path, {
            "data": np.asanyarray(img_ras.dataobj, dtype=np.float32),
            "affine": img_ras.affine,
            "fusion_png_cache": {},  # (mri_path, axis, slice_idx) -> (png_bytes, actual_idx, count)
        })
    return cached

def _render_fusion_slice(mri_path: str, ct_path: str, transform: np.ndarray, axis: str, slice_idx: int):
    """
    Resample the CT onto the exact MRI slice plane at (axis, slice_idx) and render
    as a grayscale PNG that is pixel-for-pixel aligned with /mri-slice's output.

    Unlike the structure overlay (which picks the nearest same-axis slice — a valid
    shortcut only because DKT labels share the MRI's own axes), the CT is related to
    the MRI by an arbitrary rigid rotation, so an MRI slice plane generally maps to
    an OBLIQUE plane through the CT volume. This does true 3D trilinear resampling
    of that oblique plane via scipy.ndimage.map_coordinates, using the full
    MRI-voxel -> CT-voxel affine (CT affine, registration transform, and MRI affine
    composed together), rather than picking a single CT slice index.

    Cached per (ct_path, mri_path, axis, slice_idx) since it's moderately expensive.
    """
    from scipy.ndimage import map_coordinates

    ct_vol = _get_ct_volume(ct_path)
    ct_data, ct_affine = ct_vol["data"], ct_vol["affine"]
    mri_vol = _get_mri_volume(mri_path)
    mri_data, mri_affine = mri_vol["data"], mri_vol["affine"]

    ax = {"sagittal": 0, "coronal": 1, "axial": 2}[axis]
    n = mri_data.shape[ax]
    if slice_idx < 0 or slice_idx >= n:
        slice_idx = n // 2

    # Include a signature of the transform matrix in the cache key so that a
    # re-registration (which overwrites ct_to_mri.npy with a different matrix)
    # invalidates any previously cached render for this slice.
    t_sig = hashlib.sha1(np.ascontiguousarray(transform, dtype=np.float64).tobytes()).hexdigest()[:12]
    cache_key = (mri_path, axis, slice_idx, t_sig)
    if cache_key in ct_vol["fusion_png_cache"]:
        return ct_vol["fusion_png_cache"][cache_key]

    nx, ny, nz = mri_data.shape
    if ax == 0:
        vy, vz = np.meshgrid(np.arange(ny), np.arange(nz), indexing="ij")
        vx = np.full_like(vy, slice_idx)
    elif ax == 1:
        vx, vz = np.meshgrid(np.arange(nx), np.arange(nz), indexing="ij")
        vy = np.full_like(vx, slice_idx)
    else:
        vx, vy = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
        vz = np.full_like(vx, slice_idx)

    ones = np.ones_like(vx, dtype=np.float64)
    mri_vox_hom = np.stack([vx, vy, vz, ones]).astype(np.float64).reshape(4, -1)

    # MRI voxel -> MRI world -> CT world (inverse of the saved CT->MRI transform) -> CT voxel
    M = np.linalg.inv(ct_affine) @ np.linalg.inv(transform) @ mri_affine
    ct_vox_hom = M @ mri_vox_hom
    coords = ct_vox_hom[:3].reshape(3, *vx.shape)

    ct_slice_raw = map_coordinates(ct_data, coords, order=1, cval=-1000.0, mode="constant")

    # Same orientation flip as MRI slices, so the two PNGs composite pixel-for-pixel
    sl = np.fliplr(np.rot90(ct_slice_raw, k=1))
    sl_norm = np.clip((sl - _CT_FUSION_WINDOW_MIN) / (_CT_FUSION_WINDOW_MAX - _CT_FUSION_WINDOW_MIN), 0, 1)
    sl_uint8 = (sl_norm * 255).astype(np.uint8)

    buf = io.BytesIO()
    Image.fromarray(sl_uint8, mode="L").save(buf, format="PNG", optimize=False, compress_level=1)
    result = (buf.getvalue(), slice_idx, n)
    ct_vol["fusion_png_cache"][cache_key] = result
    return result


# ── Structure overlay slice cache ─────────────────────────────────────────────
_struct_overlay_cache = _VolumeCache("labels")  # label_path -> {"data", "affine"}

def _get_label_volume(label_path: str):
    """Load DKT label NIfTI (RAS canonical) and cache it."""
    cached = _struct_overlay_cache.get(label_path)
    if cached is None:
        import nibabel as nib
        img = nib.load(label_path)
        img_ras = nib.as_closest_canonical(img)
        # Read at the stored dtype and round there, rather than materialising a
        # float64 copy of the whole volume just to throw it away.
        raw = np.asanyarray(img_ras.dataobj)
        cached = _struct_overlay_cache.put(label_path, {
            "data": np.rint(raw).astype(np.int32),
            "affine": img_ras.affine,
        })
    return cached

def _render_structure_slice(mri_path: str, label_path: str, axis: str, slice_idx: int,
                            visible_keys: set | None = None) -> bytes | None:
    """
    Return an RGBA PNG overlay for the given slice position, aligned to the MRI slice.
    visible_keys: set of structure keys to include; None = all structures.
    Returns None if the label file doesn't exist or has no labels at this slice.
    """
    if not os.path.exists(label_path):
        return None

    from services.structure_extractor import ALL_STRUCTURES

    # Build label index → RGBA lookup filtered by visibility
    label_rgba: dict[int, tuple] = {}
    for key, info in ALL_STRUCTURES.items():
        if visible_keys is not None and key not in visible_keys:
            continue
        h = info["color"].lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        for lbl in info["labels"]:
            label_rgba[lbl] = (r, g, b, 180)  # 180/255 ≈ 70% opacity in the overlay layer

    vol = _get_label_volume(label_path)
    ldata  = vol["data"]
    laff   = vol["affine"]

    # Map axis name → array axis index
    ax = {"sagittal": 0, "coronal": 1, "axial": 2}[axis]

    # Get MRI slice world coordinate for alignment
    mri_vol  = _get_mri_volume(mri_path)
    mri_aff  = mri_vol["affine"]
    mri_data = mri_vol["data"]
    n_mri    = mri_data.shape[ax]
    if slice_idx < 0 or slice_idx >= n_mri:
        slice_idx = n_mri // 2

    # Find the DKT voxel index matching this MRI slice. Go through world space with
    # the full affines rather than each volume's [ax, ax] diagonal term — the
    # diagonal shortcut silently assumes both grids are axis-aligned, which oblique
    # acquisitions are not. In practice the DKT labels are resampled onto the MRI's
    # own grid, so this resolves to dkt_idx == slice_idx; doing it properly just
    # means that stays true if the label volume ever lands on a different grid.
    centre_vox = [(d - 1) / 2.0 for d in mri_data.shape]
    centre_vox[ax] = float(slice_idx)
    world_centre = mri_aff @ np.array([*centre_vox, 1.0])
    dkt_idx = int(round((np.linalg.inv(laff) @ world_centre)[ax]))
    dkt_idx = max(0, min(ldata.shape[ax] - 1, dkt_idx))

    # Extract label slice
    if ax == 0:   sl = ldata[dkt_idx, :, :]
    elif ax == 1: sl = ldata[:, dkt_idx, :]
    else:         sl = ldata[:, :, dkt_idx]

    # Same orientation flip as MRI slices
    sl = np.fliplr(np.rot90(sl, k=1))

    # Target dimensions from corresponding MRI slice
    if ax == 0:   mri_sl = mri_data[slice_idx, :, :]
    elif ax == 1: mri_sl = mri_data[:, slice_idx, :]
    else:         mri_sl = mri_data[:, :, slice_idx]
    mri_sl    = np.fliplr(np.rot90(mri_sl, k=1))
    tgt_h, tgt_w = mri_sl.shape

    # Build RGBA array
    rgba = np.zeros((*sl.shape, 4), dtype=np.uint8)
    for lbl, color in label_rgba.items():
        mask = sl == lbl
        if mask.any():
            rgba[mask] = color

    # Resize to match MRI slice pixel dimensions so the overlay aligns
    pil = Image.fromarray(rgba, mode="RGBA")
    if pil.size != (tgt_w, tgt_h):
        pil = pil.resize((tgt_w, tgt_h), Image.NEAREST)

    buf = io.BytesIO()
    pil.save(buf, format="PNG", optimize=False, compress_level=1)
    return buf.getvalue()

# ─── App Setup ───────────────────────────────────────────────────────────────

app = FastAPI(title="Brain Reconstruction Viewer", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    # Dev origins by default; CORS_ORIGINS (comma-separated) adds the deployed
    # hostname. In the container the React build is served same-origin from
    # frontend_build/, so this mainly covers local `npm start` against a remote API.
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000",
                   "http://127.0.0.1:8000", "http://localhost:8000"]
                  + [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Path helper: works in both normal dev mode and PyInstaller frozen mode ────
def _get_runtime_dir():
    """Returns the directory next to the .exe (frozen) or next to main.py (dev)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# NEURO_DATA_DIR overrides the data root so imaging files can live on a mounted
# share (cloud deploys) rather than inside the deployment directory, which is
# replaced on every deploy. database.py reads the same variable, so the DB
# (<root>/brain_viewer.db) and the recon_* folders (<root>/data/) share a root.
DATA_DIR = os.path.join(os.environ.get("NEURO_DATA_DIR") or _get_runtime_dir(), "data")
os.makedirs(DATA_DIR, exist_ok=True)
print(f"[DATA] Using data directory: {DATA_DIR}")
# The ceiling the heavy workers are measured against. Reported once at
# startup so an OOM kill later can be read against a known number.
print(f"[MEM]  Container memory limit: {describe_limit()}")


def _rel(path: str) -> Optional[str]:
    """Convert an absolute path to a path relative to DATA_DIR for storage."""
    if not path:
        return path
    try:
        rel = os.path.relpath(path, DATA_DIR).replace('\\', '/')
        if not rel.startswith('..'):
            return rel
    except ValueError:
        pass  # different drive on Windows
    return path


def _abs(path: str) -> Optional[str]:
    """Resolve a stored (possibly relative) path to an absolute path."""
    if not path:
        return path
    if os.path.isabs(path):
        return path  # already absolute (legacy data)
    return os.path.join(DATA_DIR, path)


@app.on_event("startup")
async def startup():
    await init_db()

    # Reap orphaned exports. _export_mni_background runs in-process, so a row
    # still marked "exporting" at startup belongs to a worker that died mid-run
    # (crash, restart, cloud instance recycle) -- nothing will ever finish it or
    # move it off that status, and start_mni_export refuses to re-run while it
    # sits there. Mark it "error" so the user can retry.
    #
    # exported_at is cleared to match the failure path in _export_mni_background:
    # an interrupted re-export may have partially overwritten a previous good
    # export, so the old timestamp no longer describes what is on disk.
    #
    # NOTE: this assumes a single app instance. If the app is ever scaled out,
    # a starting instance would reap exports still running on its peers; that
    # redesign should move exports to a queue + worker with a heartbeat.
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(Reconstruction)
            .where(Reconstruction.export_status == "exporting")
            .values(export_status="error", exported_at=None)
        )
        if result.rowcount:
            await db.commit()
            print(f"[STARTUP] Reset {result.rowcount} orphaned export(s) to 'error'.")

    # Lightweight column migration: create_all does not ALTER existing tables, so
    # add seeg_recordings.content_hash (used for upload dedup) if it's missing.
    # SQLite only -- both PRAGMA and "ADD COLUMN" are SQLite spellings, and the
    # databases old enough to lack the column are all local files. A managed
    # database either gets the column from create_all or from Alembic.
    if IS_SQLITE:
        async with engine.begin() as conn:
            cols = await conn.run_sync(
                lambda c: [row[1] for row in c.exec_driver_sql("PRAGMA table_info(seeg_recordings)").fetchall()]
            )
            if cols and "content_hash" not in cols:
                await conn.exec_driver_sql("ALTER TABLE seeg_recordings ADD COLUMN content_hash VARCHAR")
                print("[STARTUP] Added seeg_recordings.content_hash column.")

    # Backfill content_hash for legacy rows by hashing the stored file, so
    # content-based upload dedup works retroactively for recordings uploaded
    # before the column existed.
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(SeegRecording).where(SeegRecording.content_hash.is_(None))
        )).scalars().all()
        backfilled = 0
        for r in rows:
            ap = _abs(r.stored_path)
            if ap and os.path.exists(ap):
                h = hashlib.sha256()
                with open(ap, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                r.content_hash = h.hexdigest()
                backfilled += 1
        if backfilled:
            await db.commit()
            print(f"[STARTUP] Backfilled content_hash for {backfilled} sEEG recording(s).")

    # One-time migration: convert any absolute paths stored in DB to relative.
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Reconstruction))
        recons = result.scalars().all()
        migrated = 0
        for r in recons:
            changed = False
            for attr in ('mri_path', 'ct_path', 'mesh_path'):
                old = getattr(r, attr)
                if not old or not os.path.isabs(old):
                    continue
                try:
                    rel = os.path.relpath(old, DATA_DIR).replace('\\', '/')
                    if not rel.startswith('..'):
                        setattr(r, attr, rel)
                        changed = True
                except ValueError:
                    pass
            if changed:
                migrated += 1
        if migrated:
            await db.commit()
            print(f"[STARTUP] Migrated {migrated} reconstruction(s) to relative paths.")

    # Create default admin user if none exists. The password comes from
    # ADMIN_PASSWORD so a cloud deploy never ships the well-known default;
    # local/desktop runs keep the old "changeme" behaviour.
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        if not result.scalar_one_or_none():
            admin = User(
                username="admin",
                hashed_password=hash_password(os.environ.get("ADMIN_PASSWORD") or "changeme"),
                role="admin"
            )
            db.add(admin)
            await db.commit()
            print("[STARTUP] Created default admin user.")


# ─── Pydantic Schemas ─────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    role: str
    username: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class ReconstructionResponse(BaseModel):
    id: int
    patient_id: str
    label: str
    status: str
    share_token: Optional[str]
    created_at: datetime
    updated_at: datetime
    is_complete: bool = False
    is_locked: bool = False
    registration_confirmed: bool = False

    class Config:
        from_attributes = True


class ShaftCreate(BaseModel):
    name: str
    label: Optional[str] = None
    electrode_type: str = "depth"
    color: str = "#00ff88"
    n_total_contacts: int = 12
    spacing_mm: float = 3.5
    grid_rows: Optional[int] = None
    grid_cols: Optional[int] = None
    contact_diameter_mm: float = 0.8
    contact_length_mm: float = 2.0
    shaft_diameter_mm: float = 0.5


class ContactCreate(BaseModel):
    contact_number: int
    x: float
    y: float
    z: float
    is_manual: bool = True
    is_world_mm: bool = False  # if True, x/y/z are already world mm (skip affine transform)


class ManualContact(BaseModel):
    contact_number: int
    position: List[float]  # [x, y, z] in world mm

class AutofillRequest(BaseModel):
    manual_contacts: List[ManualContact]
    n_total_contacts: int
    electrode_type: str = "depth"
    spacing_mm: float = 3.5   # kept for backwards compat, ignored
    grid_rows: Optional[int] = None
    grid_cols: Optional[int] = None
    hu_threshold: Optional[float] = None  # if set, snap autofilled contacts to CT blobs


# ─── Auth Routes ──────────────────────────────────────────────────────────────

@app.post("/api/auth/login", response_model=TokenResponse)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == form.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user.username})
    return {"access_token": token, "token_type": "bearer", "role": user.role, "username": user.username}


@app.post("/api/auth/register", dependencies=[Depends(require_admin)])
async def register(data: UserCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == data.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists")
    user = User(username=data.username, hashed_password=hash_password(data.password), role=data.role)
    db.add(user)
    await db.commit()
    return {"message": f"User '{data.username}' created with role '{data.role}'"}


@app.get("/api/auth/me")
async def me(current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"username": current_user.username, "role": current_user.role}


# ─── Reconstruction Routes ────────────────────────────────────────────────────

@app.get("/api/reconstructions")
async def list_reconstructions(
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all reconstructions with electrode counts. Requires login."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Login required")
    result = await db.execute(
        select(Reconstruction)
        .where(Reconstruction.deleted_at == None)
        .order_by(Reconstruction.created_at.desc())
    )
    recons = result.scalars().all()

    out = []
    for recon in recons:
        try:
            shafts_result = await db.execute(
                select(ElectrodeShaft).where(ElectrodeShaft.reconstruction_id == recon.id)
            )
            shafts = shafts_result.scalars().all()
            shafts_data = []
            for shaft in shafts:
                contacts_result = await db.execute(
                    select(ElectrodeContact)
                    .where(ElectrodeContact.shaft_id == shaft.id)
                    .order_by(ElectrodeContact.contact_number)
                )
                contacts = contacts_result.scalars().all()
                shafts_data.append({
                    "id": shaft.id,
                    "name": shaft.name,
                    "label": getattr(shaft, "label", None),
                    "electrode_type": shaft.electrode_type,
                    "color": shaft.color,
                    "visible": shaft.visible,
                    "n_total_contacts": getattr(shaft, "n_total_contacts", 12),
                    "spacing_mm": getattr(shaft, "spacing_mm", 3.5),
                    "grid_rows": getattr(shaft, "grid_rows", None),
                    "grid_cols": getattr(shaft, "grid_cols", None),
                    "contact_diameter_mm": getattr(shaft, "contact_diameter_mm", 0.8),
                    "contact_length_mm": getattr(shaft, "contact_length_mm", 2.0),
                    "shaft_diameter_mm": getattr(shaft, "shaft_diameter_mm", 0.5),
                    "contacts": [
                        {"contact_number": c.contact_number, "x_mm": c.x_mm, "y_mm": c.y_mm, "z_mm": c.z_mm,
                         "x": c.x, "y": c.y, "z": c.z, "is_manual": c.is_manual}
                        for c in contacts
                    ]
                })
        except Exception as e:
            print(f"[WARN] Error loading shafts for recon {recon.id}: {e}")
            shafts_data = []
        out.append({
            "id": recon.id,
            "patient_id": recon.patient_id,
            "label": recon.label,
            "status": recon.status,
            "is_complete": getattr(recon, "is_complete", False) or False,
            "is_locked": getattr(recon, "is_locked", False) or False,
            "share_token": recon.share_token,
            "created_at": recon.created_at,
            "updated_at": recon.updated_at,
            "has_mri": recon.mri_path is not None and os.path.exists(_abs(recon.mri_path) or ""),
            "has_mesh": recon.mesh_path is not None and os.path.exists(_abs(recon.mesh_path) or ""),
            "has_ct": recon.ct_path is not None,
            "has_registration": (
                os.path.exists(os.path.join(os.path.dirname(_abs(recon.ct_path)), "ct_to_mri.npy"))
                if recon.ct_path else False
            ),
            "registration_deterministic": _read_reg_deterministic(_abs(recon.ct_path)) if recon.ct_path else None,
            "registration_candidates": _read_candidates(_abs(recon.ct_path)) if recon.ct_path else [],
            "awaiting_basin_selection": bool(_read_candidates(_abs(recon.ct_path))) if recon.ct_path else False,
            "registration_confirmed": getattr(recon, "registration_confirmed", False) or False,
            "export_status": getattr(recon, "export_status", "none") or "none",
            "exported_at": getattr(recon, "exported_at", None),
            "electrode_shafts": shafts_data,
        })
    return out


@app.post("/api/reconstructions", response_model=ReconstructionResponse)
async def create_reconstruction(
    patient_id: str = Form(...),
    label: str = Form(...),
    mri_file: UploadFile = File(...),
    mri_modality: str = Form("t1"),
    ct_file: Optional[UploadFile] = File(None),
    ct_preregistered: bool = Form(False),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Upload NIfTI files and kick off mesh extraction in background."""
    mri_modality = mri_modality.lower()
    if mri_modality not in ("t1", "t2"):
        raise HTTPException(status_code=400, detail="mri_modality must be 't1' or 't2'")
    recon_dir = os.path.join(DATA_DIR, f"recon_{uuid.uuid4().hex[:8]}")
    os.makedirs(recon_dir, exist_ok=True)

    # Save MRI file
    mri_path = os.path.join(recon_dir, "mri.nii.gz")
    with open(mri_path, "wb") as f:
        f.write(await mri_file.read())

    ct_path = None
    if ct_file:
        ct_path = os.path.join(recon_dir, "ct.nii.gz")
        with open(ct_path, "wb") as f:
            f.write(await ct_file.read())

    recon = Reconstruction(
        patient_id=patient_id,
        label=label,
        mri_path=_rel(mri_path),
        ct_path=_rel(ct_path),
        created_by=current_user.id,
        status="processing",
        share_token=uuid.uuid4().hex,
    )
    db.add(recon)
    await db.commit()
    await db.refresh(recon)

    # Run mesh extraction in background
    background_tasks.add_task(_extract_mesh_background, recon.id, mri_path, recon_dir, ct_path, ct_preregistered, mri_modality)

    return recon


@app.post("/api/reconstructions/{recon_id}/files")
async def upload_reconstruction_files(
    recon_id: int,
    mri_file: Optional[UploadFile] = File(None),
    mri_modality: str = Form("t1"),
    ct_file: Optional[UploadFile] = File(None),
    ct_preregistered: bool = Form(False),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Upload or replace MRI/CT files for an existing reconstruction and re-run processing."""
    mri_modality = mri_modality.lower()
    if mri_modality not in ("t1", "t2"):
        raise HTTPException(status_code=400, detail="mri_modality must be 't1' or 't2'")
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconstruction not found")

    if recon.mri_path:
        recon_dir = os.path.dirname(_abs(recon.mri_path))
    elif recon.mesh_path:
        recon_dir = os.path.dirname(_abs(recon.mesh_path))
    else:
        recon_dir = os.path.join(DATA_DIR, f"recon_{uuid.uuid4().hex[:8]}")
    os.makedirs(recon_dir, exist_ok=True)

    mri_path = _abs(recon.mri_path) if recon.mri_path else None
    ct_path  = _abs(recon.ct_path)  if recon.ct_path  else None

    if mri_file:
        mri_path = os.path.join(recon_dir, "mri.nii.gz")
        with open(mri_path, "wb") as f:
            f.write(await mri_file.read())
        # Invalidate MRI volume cache so new file is loaded
        _mri_volume_cache.pop(mri_path, None)

    if ct_file:
        ct_path = os.path.join(recon_dir, "ct.nii.gz")
        with open(ct_path, "wb") as f:
            f.write(await ct_file.read())

    await db.execute(
        update(Reconstruction)
        .where(Reconstruction.id == recon_id)
        .values(
            mri_path=_rel(mri_path) if mri_path else recon.mri_path,
            ct_path=_rel(ct_path)   if ct_path  else recon.ct_path,
            status="processing",
            updated_at=datetime.utcnow(),
        )
    )
    await db.commit()

    if mri_path:
        background_tasks.add_task(_extract_mesh_background, recon_id, mri_path, recon_dir, ct_path, ct_preregistered, mri_modality)

    return {"status": "processing"}


# ── Registration mode metadata (sidecar next to ct_to_mri.npy) ──────────────────
# We record whether the stored transform came from the fast multithreaded path or
# the deterministic single-threaded path. A JSON sidecar avoids a DB migration
# (the app uses SQLAlchemy create_all, no Alembic) and travels with the transform.

def _reg_meta_path(ct_abs: str) -> str:
    """Path of the registration-mode sidecar for a given CT path."""
    return os.path.join(os.path.dirname(ct_abs), "ct_to_mri.meta.json")


def _write_reg_meta(ct_abs: str, threads: int, deterministic: bool) -> None:
    try:
        with open(_reg_meta_path(ct_abs), "w") as f:
            json.dump({
                "threads": int(threads),
                "deterministic": bool(deterministic),
                "updated_at": datetime.utcnow().isoformat(),
            }, f)
    except Exception as e:
        print(f"[REG] could not write registration meta: {e}")


def _read_reg_deterministic(ct_abs: Optional[str]) -> bool:
    """True if the stored transform is deterministic (or unknown/legacy → assume
    deterministic, since the historical default was single-threaded)."""
    if not ct_abs:
        return True
    p = _reg_meta_path(ct_abs)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return bool(json.load(f).get("deterministic", True))
        except Exception:
            pass
    return True


async def _run_registration(recon_id: int, mri_path: str, ct_abs: str,
                            ct_preregistered: bool, threads: int):
    """Register CT→MRI for a recon, write the mode sidecar, and (re)generate the
    masked CT. Shared by the initial pipeline (fast, multithreaded) and the
    deterministic re-run endpoint (threads=1). Manages the 'registering'→'ready'
    status transition and resets any prior manual confirmation."""
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Reconstruction)
            .where(Reconstruction.id == recon_id)
            .values(status="registering", registration_confirmed=False, updated_at=datetime.utcnow())
        )
        await db.commit()
    try:
        from services.registration import (
            register_ct_to_mri, get_transform_path, preprocess_ct, get_masked_ct_path,
        )
        transform_path = get_transform_path(ct_abs)
        loop = asyncio.get_event_loop()
        if ct_preregistered:
            np.save(transform_path, np.eye(4))
            _write_reg_meta(ct_abs, threads=1, deterministic=True)  # identity is trivially reproducible
            print(f"[REG] CT pre-registered — identity transform saved for recon {recon_id}")
        else:
            # threads passed positionally (4th arg) to avoid functools.partial
            await loop.run_in_executor(
                None, register_ct_to_mri, mri_path, ct_abs, transform_path, threads
            )
            _write_reg_meta(ct_abs, threads=threads, deterministic=(threads == 1))
            print(f"[REG] Registration complete for recon {recon_id} (threads={threads})")
        # Preprocess CT to strip table/air regardless of registration path
        masked_ct_path = get_masked_ct_path(ct_abs)
        await loop.run_in_executor(None, preprocess_ct, ct_abs, masked_ct_path)
    except Exception as e:
        print(f"[REG] Registration failed for recon {recon_id}: {e}")
    finally:
        async with AsyncSessionLocal() as db2:
            await db2.execute(
                update(Reconstruction)
                .where(Reconstruction.id == recon_id)
                .values(status="ready", updated_at=datetime.utcnow())
            )
            await db2.commit()


# ── Multi-start "precise" registration: candidate-basin storage ─────────────────
# The precise re-run enumerates the distinct MI basins (no metric can rank them)
# and stores one candidate transform per basin as sidecar files next to
# ct_to_mri.npy, plus a candidates.json summary, for the reviewer to pick from.

def _candidates_dir(ct_abs: str) -> str:
    return os.path.join(os.path.dirname(ct_abs), "ct_to_mri.candidates")


def _candidates_json(ct_abs: str) -> str:
    return os.path.join(os.path.dirname(ct_abs), "ct_to_mri.candidates.json")


def _read_candidates(ct_abs: Optional[str]):
    """Return the list of candidate-basin summaries [{idx,size,spread_mm,metric}]
    if a precise run is awaiting selection, else []."""
    if not ct_abs:
        return []
    p = _candidates_json(ct_abs)
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f).get("basins", [])
        except Exception:
            pass
    return []


def _clear_candidates(ct_abs: str):
    import shutil
    d = _candidates_dir(ct_abs)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    j = _candidates_json(ct_abs)
    if os.path.exists(j):
        try:
            os.remove(j)
        except Exception:
            pass


async def _run_multistart_registration(recon_id: int, mri_path: str, ct_abs: str):
    """Precise re-run: run the jittered multi-start, cluster into <=2 basins, and
    store one candidate transform per basin for human selection. If only one basin
    is found, apply it directly (like a normal registration). Never auto-picks
    between multiple basins — the reviewer chooses in the fusion viewer."""
    from database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Reconstruction).where(Reconstruction.id == recon_id)
            .values(status="registering", registration_confirmed=False, updated_at=datetime.utcnow())
        )
        await db.commit()
    try:
        from services.registration import run_multistart, get_transform_path
        transform_path = get_transform_path(ct_abs)
        _clear_candidates(ct_abs)
        loop = asyncio.get_event_loop()
        basins = await loop.run_in_executor(None, run_multistart, mri_path, ct_abs)

        if len(basins) <= 1:
            # Single basin — apply directly, no picker needed.
            np.save(transform_path, basins[0]["transform"])
            _write_reg_meta(ct_abs, threads=8, deterministic=False)
            print(f"[MULTISTART] recon {recon_id}: single basin, applied directly")
        else:
            # Multiple basins — persist candidates for the reviewer to choose.
            cdir = _candidates_dir(ct_abs)
            os.makedirs(cdir, exist_ok=True)
            summary = []
            for i, b in enumerate(basins):
                np.save(os.path.join(cdir, f"cand{i}.npy"), b["transform"])
                summary.append({"idx": i, "size": int(b["size"]),
                                "spread_mm": round(float(b["spread_mm"]), 3),
                                "metric": (round(float(b["metric"]), 5) if b["metric"] is not None else None)})
            with open(_candidates_json(ct_abs), "w") as f:
                json.dump({"basins": summary, "created_at": datetime.utcnow().isoformat()}, f)
            print(f"[MULTISTART] recon {recon_id}: {len(basins)} basins -> awaiting selection")
    except Exception as e:
        print(f"[MULTISTART] failed for recon {recon_id}: {e}")
    finally:
        async with AsyncSessionLocal() as db2:
            await db2.execute(
                update(Reconstruction).where(Reconstruction.id == recon_id)
                .values(status="ready", updated_at=datetime.utcnow())
            )
            await db2.commit()


async def _extract_mesh_background(recon_id: int, mri_path: str, recon_dir: str, ct_path: str = None, ct_preregistered: bool = False, mri_modality: str = "t1"):
    """Background task: extract brain mesh from MRI NIfTI, then register CT if available."""
    from database import AsyncSessionLocal
    import hashlib

    mesh_path = os.path.join(recon_dir, "mesh.json")

    # In a frozen exe, antspynet (deep-learning skull stripping) is unavailable.
    # Reuse the first clean mesh found in another reconstruction folder so the
    # brain looks as good as in dev. Coregistration always runs fresh below.
    def _find_any_mesh():
        for entry in os.scandir(DATA_DIR):
            if not entry.is_dir() or entry.path == recon_dir:
                continue
            candidate = os.path.join(entry.path, "mesh.json")
            if os.path.exists(candidate):
                return candidate
        return None

    try:
        loop = asyncio.get_event_loop()
        import sys as _sys
        if getattr(_sys, 'frozen', False):
            existing_mesh = await loop.run_in_executor(None, _find_any_mesh)
            if existing_mesh:
                import shutil as _shutil
                _shutil.copy2(existing_mesh, mesh_path)
                print(f"[MESH] Reused existing mesh from {os.path.dirname(existing_mesh)}")
            else:
                await loop.run_in_executor(None, extract_brain_mesh_isolated, mri_path, mesh_path, None, mri_modality)
        else:
            await loop.run_in_executor(None, extract_brain_mesh_isolated, mri_path, mesh_path, None, mri_modality)
        status = "ready"
    except Exception as e:
        import traceback
        print(f"[MESH ERROR] Mesh extraction failed for recon {recon_id}:")
        traceback.print_exc()
        status = "error"

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Reconstruction)
            .where(Reconstruction.id == recon_id)
            .values(status=status, mesh_path=_rel(mesh_path) if status == "ready" else None,
                    updated_at=datetime.utcnow())
        )
        await db.commit()

    # If mesh extraction succeeded and a CT exists, run registration.
    # Initial pass uses the FAST multithreaded path for speed; the result is
    # human-reviewed in the fusion viewer and can be re-run deterministically
    # (single-threaded) from the review panel if it looks off.
    if status == "ready":
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
            recon = result.scalar_one_or_none()
            ct_path_val = recon.ct_path if recon else None
        if ct_path_val and os.path.exists(_abs(ct_path_val)):
            fast_threads = min(8, os.cpu_count() or 1)
            await _run_registration(
                recon_id, mri_path, _abs(ct_path_val), ct_preregistered, threads=fast_threads
            )



@app.get("/api/reconstructions/deleted")
async def list_deleted_reconstructions(
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all soft-deleted reconstructions."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Login required")
    result = await db.execute(
        select(Reconstruction)
        .where(Reconstruction.deleted_at != None)
        .order_by(Reconstruction.deleted_at.desc())
    )
    recons = result.scalars().all()
    return [
        {
            "id": recon.id,
            "patient_id": recon.patient_id,
            "label": recon.label,
            "status": recon.status,
            "is_complete": getattr(recon, "is_complete", False) or False,
            "created_at": recon.created_at,
            "deleted_at": recon.deleted_at,
        }
        for recon in recons
    ]

@app.get("/api/reconstructions/{recon_id}")
async def get_reconstruction(
    recon_id: int,
    token: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get reconstruction by ID. Accessible via share token or login."""
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconstruction not found")

    # Allow access via share token or logged-in user
    if not current_user and recon.share_token != token:
        raise HTTPException(status_code=403, detail="Access denied")

    # Load electrode shafts + contacts
    shafts_result = await db.execute(
        select(ElectrodeShaft).where(ElectrodeShaft.reconstruction_id == recon_id)
    )
    shafts = shafts_result.scalars().all()

    shafts_data = []
    for shaft in shafts:
        contacts_result = await db.execute(
            select(ElectrodeContact)
            .where(ElectrodeContact.shaft_id == shaft.id)
            .order_by(ElectrodeContact.contact_number)
        )
        contacts = contacts_result.scalars().all()
        shafts_data.append({
            "id": shaft.id,
            "name": shaft.name,
            "label": getattr(shaft, "label", None),
            "electrode_type": shaft.electrode_type,
            "color": shaft.color,
            "visible": shaft.visible,
            "n_total_contacts": getattr(shaft, "n_total_contacts", 12),
            "spacing_mm": getattr(shaft, "spacing_mm", 3.5),
            "grid_rows": getattr(shaft, "grid_rows", None),
            "grid_cols": getattr(shaft, "grid_cols", None),
            "contact_diameter_mm": getattr(shaft, "contact_diameter_mm", 0.8),
            "contact_length_mm": getattr(shaft, "contact_length_mm", 2.0),
            "shaft_diameter_mm": getattr(shaft, "shaft_diameter_mm", 0.5),
            "contacts": [
                {
                    "contact_number": c.contact_number,
                    "x_mm": c.x_mm, "y_mm": c.y_mm, "z_mm": c.z_mm,
                    "x": c.x, "y": c.y, "z": c.z,
                    "is_manual": c.is_manual,
                }
                for c in contacts
            ]
        })

    return {
        "id": recon.id,
        "patient_id": recon.patient_id,
        "label": recon.label,
        "status": recon.status,
        "is_complete": getattr(recon, "is_complete", False) or False,
        "is_locked": getattr(recon, "is_locked", False) or False,
        "share_token": recon.share_token,
        "created_at": recon.created_at,
        "updated_at": recon.updated_at,
        "has_mri": recon.mri_path is not None and os.path.exists(_abs(recon.mri_path) or ""),
        "has_mesh": recon.mesh_path is not None and os.path.exists(_abs(recon.mesh_path) or ""),
        "has_ct": recon.ct_path is not None,
        "has_registration": (
            os.path.exists(os.path.join(os.path.dirname(_abs(recon.ct_path)), "ct_to_mri.npy"))
            if recon.ct_path else False
        ),
        "registration_deterministic": _read_reg_deterministic(_abs(recon.ct_path)) if recon.ct_path else None,
        "registration_candidates": _read_candidates(_abs(recon.ct_path)) if recon.ct_path else [],
        "awaiting_basin_selection": bool(_read_candidates(_abs(recon.ct_path))) if recon.ct_path else False,
        "registration_confirmed": getattr(recon, "registration_confirmed", False) or False,
        "export_status": getattr(recon, "export_status", "none") or "none",
        "exported_at": getattr(recon, "exported_at", None),
        "electrode_shafts": shafts_data,
    }


@app.patch("/api/reconstructions/{recon_id}/status")
async def update_reconstruction_status(
    recon_id: int,
    is_complete: Optional[bool] = Body(None, embed=True),
    is_locked: Optional[bool] = Body(None, embed=True),
    # require_editor, not Optional[get_current_user]: this endpoint mutates
    # complete/lock state and cascades to export_status (unlocking marks an
    # existing MNI export stale). It previously declared current_user as
    # Optional and never checked it, so it accepted unauthenticated requests.
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconstruction not found")
    if is_complete is not None:
        recon.is_complete = is_complete
        # Auto-lock when marked complete
        if is_complete:
            recon.is_locked = True
        else:
            # Unlocking re-opens editing, so any existing MNI export no longer
            # reflects the reconstruction (contacts may move). Mark it stale so the
            # UI offers a re-run instead of a download of outdated coordinates.
            # Mirrors the registration_confirmed reset when registration re-runs.
            if getattr(recon, "export_status", "none") == "exported":
                recon.export_status = "stale"
    if is_locked is not None:
        recon.is_locked = is_locked
    await db.commit()
    return {
        "is_complete": recon.is_complete,
        "is_locked": recon.is_locked,
        "export_status": getattr(recon, "export_status", "none") or "none",
    }


@app.patch("/api/reconstructions/{recon_id}/registration-confirm")
async def confirm_registration(
    recon_id: int,
    confirmed: bool = Body(..., embed=True),
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Manually mark (or un-mark) a reconstruction's CT-MRI registration as visually reviewed and correct."""
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconstruction not found")
    recon.registration_confirmed = confirmed
    await db.commit()
    return {"registration_confirmed": recon.registration_confirmed}


@app.post("/api/reconstructions/{recon_id}/reregister")
async def reregister(
    recon_id: int,
    mode: str = "precise",
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Re-run CT→MRI registration when a reviewer judges the fast result poor.

    mode='precise' (default): jittered multi-start, enumerate the distinct MI basins
      into up to 2 candidates for the reviewer to choose (see /registration-candidates).
    mode='deterministic': single-threaded reproducible re-run that overwrites
      ct_to_mri.npy directly (legacy; note determinism != correctness on ambiguous cases).

    Runs in the background; the recon flips to 'registering' then back to 'ready'.
    """
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconstruction not found")
    if recon.status == "registering":
        raise HTTPException(status_code=409, detail="A registration is already in progress")
    if not recon.ct_path or not os.path.exists(_abs(recon.ct_path)):
        raise HTTPException(status_code=400, detail="No CT available to register")
    if not recon.mri_path or not os.path.exists(_abs(recon.mri_path)):
        raise HTTPException(status_code=400, detail="No MRI available to register against")

    mri_abs = _abs(recon.mri_path)
    ct_abs = _abs(recon.ct_path)
    # Flip to "registering" synchronously so the client's poll always observes the
    # transition (the background task also sets this, but not before we return).
    await db.execute(
        update(Reconstruction)
        .where(Reconstruction.id == recon_id)
        .values(status="registering", registration_confirmed=False, updated_at=datetime.utcnow())
    )
    await db.commit()
    if mode == "deterministic":
        background_tasks.add_task(_run_registration, recon_id, mri_abs, ct_abs, False, 1)
    else:
        background_tasks.add_task(_run_multistart_registration, recon_id, mri_abs, ct_abs)
    return {"status": "registering", "mode": mode}


@app.post("/api/reconstructions/{recon_id}/registration-candidates/{idx}/select")
async def select_registration_candidate(
    recon_id: int,
    idx: int,
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Apply the reviewer-chosen candidate basin from a precise re-run: copy
    cand{idx}.npy -> ct_to_mri.npy, record the mode sidecar, and clear the
    candidates. The reviewer still confirms via the normal Confirm button."""
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconstruction not found")
    if not recon.ct_path:
        raise HTTPException(status_code=400, detail="No CT for this reconstruction")
    ct_abs = _abs(recon.ct_path)
    cand = os.path.join(_candidates_dir(ct_abs), f"cand{idx}.npy")
    if not os.path.exists(cand):
        raise HTTPException(status_code=404, detail=f"Candidate {idx} not found")

    from services.registration import get_transform_path
    np.save(get_transform_path(ct_abs), np.load(cand))
    _write_reg_meta(ct_abs, threads=8, deterministic=False)
    _clear_candidates(ct_abs)
    await db.execute(
        update(Reconstruction).where(Reconstruction.id == recon_id)
        .values(registration_confirmed=False, updated_at=datetime.utcnow())
    )
    await db.commit()
    return {"selected": idx, "awaiting_basin_selection": False}


# ── MNI export pipeline ─────────────────────────────────────────────────────────

@app.post("/api/reconstructions/{recon_id}/export")
async def start_mni_export(
    recon_id: int,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """
    Kick off the MNI152 export pipeline (MRI/CT/electrodes → MNI space).
    Only available once the reconstruction is marked complete. Re-runnable.
    """
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconstruction not found")
    if not (getattr(recon, "is_complete", False) or False):
        raise HTTPException(status_code=409, detail="Reconstruction must be marked complete before export")
    if not recon.mesh_path or not os.path.exists(_abs(recon.mesh_path) or ""):
        raise HTTPException(status_code=409, detail="Brain mesh not available yet")
    if getattr(recon, "export_status", "none") == "exporting":
        raise HTTPException(status_code=409, detail="Export already in progress")

    recon.export_status = "exporting"
    await db.commit()
    background_tasks.add_task(_export_mni_background, recon_id)
    return {"export_status": "exporting"}


async def _export_mni_background(recon_id: int):
    """Background task: register MRI/CT/electrodes into MNI152 space."""
    from database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
            recon = result.scalar_one_or_none()
            if not recon:
                return
            mri_abs = _abs(recon.mri_path) if recon.mri_path else None
            mesh_abs = _abs(recon.mesh_path) if recon.mesh_path else None
            ct_abs = _abs(recon.ct_path) if recon.ct_path else None

            # Gather placed contacts with their shaft name
            shafts_result = await db.execute(
                select(ElectrodeShaft).where(ElectrodeShaft.reconstruction_id == recon_id)
            )
            shafts = shafts_result.scalars().all()
            contacts = []
            for shaft in shafts:
                c_result = await db.execute(
                    select(ElectrodeContact)
                    .where(ElectrodeContact.shaft_id == shaft.id)
                    .where(ElectrodeContact.x_mm != None)
                    .order_by(ElectrodeContact.contact_number)
                )
                for c in c_result.scalars().all():
                    contacts.append({
                        "shaft_name": shaft.name,
                        "contact_number": c.contact_number,
                        "x_mm": c.x_mm, "y_mm": c.y_mm, "z_mm": c.z_mm,
                    })

        if not mri_abs or not os.path.exists(mri_abs):
            raise RuntimeError("MRI not available for export")
        if not mesh_abs or not os.path.exists(mesh_abs):
            raise RuntimeError("Brain mesh not available for export")

        recon_dir = os.path.dirname(mesh_abs)
        with open(mesh_abs) as f:
            mesh_center = json.load(f).get("center", [0.0, 0.0, 0.0])

        from services.registration import get_transform_path
        ct_to_mri_npy = get_transform_path(ct_abs) if ct_abs else None

        from services.mni_registration import export_reconstruction_to_mni
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, export_reconstruction_to_mni,
            recon_dir, mri_abs, ct_abs, ct_to_mri_npy, contacts, mesh_center,
        )
        status = "exported"
        print(f"[MNI] Export succeeded for recon {recon_id}")
    except Exception:
        import traceback
        print(f"[MNI ERROR] Export failed for recon {recon_id}:")
        traceback.print_exc()
        status = "error"

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(Reconstruction)
            .where(Reconstruction.id == recon_id)
            .values(export_status=status,
                    exported_at=datetime.utcnow() if status == "exported" else None,
                    updated_at=datetime.utcnow())
        )
        await db.commit()


@app.get("/api/reconstructions/{recon_id}/export/download")
async def download_mni_export(
    recon_id: int,
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Download the MNI export artifacts as a zip."""
    import zipfile

    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconstruction not found")
    if not recon.mesh_path:
        raise HTTPException(status_code=404, detail="No export available")
    export_dir = os.path.join(os.path.dirname(_abs(recon.mesh_path)), "export")
    if not os.path.isdir(export_dir):
        raise HTTPException(status_code=404, detail="No export available — run the export first")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in sorted(os.listdir(export_dir)):
            if fn.startswith("_"):
                continue  # skip intermediates
            full = os.path.join(export_dir, fn)
            if os.path.isfile(full):
                zf.write(full, arcname=fn)
    buf.seek(0)
    safe_pid = "".join(ch for ch in (recon.patient_id or "recon") if ch.isalnum() or ch in "-_")
    fname = f"{safe_pid}_mni_export.zip"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── sEEG functional mapping ─────────────────────────────────────────────────
# Upload NeurosEEGRead h5 files and render per-electrode band activity on a
# brain surface. Fully parallel to the reconstruction pipeline: activity comes
# from the h5, coordinates from this reconstruction (joined to channels by name).

def _recon_dir_for(recon: Reconstruction) -> Optional[str]:
    """Resolve the on-disk data dir for a reconstruction (mesh or MRI based)."""
    for p in (recon.mesh_path, recon.mri_path):
        ap = _abs(p) if p else None
        if ap:
            return os.path.dirname(ap)
    return None


def _replace_with_retry(src: str, dst: str, attempts: int = 8, delay: float = 0.15):
    """
    os.replace with a short retry.

    Windows can transiently deny a move (WinError 5) when the file is briefly
    locked by an antivirus scan or a lingering read handle. A few short retries
    clear those without failing the request.
    """
    import time
    last = None
    for _ in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            last = e
            time.sleep(delay)
    raise last


async def _gather_native_contacts(db: AsyncSession, recon_id: int) -> list:
    """Placed contacts with shaft name + mesh-centered mm, for the name-join."""
    shafts_result = await db.execute(
        select(ElectrodeShaft).where(ElectrodeShaft.reconstruction_id == recon_id)
    )
    contacts = []
    for shaft in shafts_result.scalars().all():
        c_result = await db.execute(
            select(ElectrodeContact)
            .where(ElectrodeContact.shaft_id == shaft.id)
            .where(ElectrodeContact.x_mm != None)
            .order_by(ElectrodeContact.contact_number)
        )
        for c in c_result.scalars().all():
            contacts.append({
                "shaft_name": shaft.name, "contact_number": c.contact_number,
                "x_mm": c.x_mm, "y_mm": c.y_mm, "z_mm": c.z_mm,
            })
    return contacts


@app.post("/api/reconstructions/{recon_id}/seeg")
async def upload_seeg(
    recon_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Upload a NeurosEEGRead h5 recording and associate it with the reconstruction."""
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconstruction not found")

    recon_dir = _recon_dir_for(recon) or os.path.join(DATA_DIR, f"recon_{uuid.uuid4().hex[:8]}")
    seeg_dir = os.path.join(recon_dir, "seeg")
    os.makedirs(seeg_dir, exist_ok=True)

    raw = await file.read()
    content_hash = hashlib.sha256(raw).hexdigest()
    safe_name = os.path.basename(file.filename or "recording.h5")

    # Validate against a temp file first so a bad upload is never stored; parse it,
    # then move it into place only if it reads.
    tmp = os.path.join(seeg_dir, f".tmp_{uuid.uuid4().hex[:8]}.h5")
    with open(tmp, "wb") as f:
        f.write(raw)
    try:
        from services.seeg_service import parse_seeg_h5
        task = parse_seeg_h5(tmp)["attrs"].get("task")
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=f"Not a readable NeurosEEGRead h5: {e}")

    # Always store under a fresh filename and point the DB row at it -- never
    # overwrite an existing file in place. os.replace onto a file that is briefly
    # locked (antivirus scan, a lingering read handle) raises WinError 5 on Windows;
    # writing a new file sidesteps that. Superseded files are garbage-collected below.
    dest = os.path.join(seeg_dir, f"{uuid.uuid4().hex[:8]}_{safe_name}")
    _replace_with_retry(tmp, dest)

    # Dedup: an existing recording with identical content (sha256) or the same
    # original filename is treated as the same recording -- reuse one row and drop
    # the rest, consolidating any duplicates that piled up previously too.
    existing = (await db.execute(
        select(SeegRecording).where(SeegRecording.reconstruction_id == recon_id)
    )).scalars().all()
    matches = [r for r in existing
               if r.content_hash == content_hash or r.filename == safe_name]

    if matches:
        keep, extras = matches[0], matches[1:]
        for r in extras:
            await db.delete(r)
        keep.task = task
        keep.filename = safe_name
        keep.content_hash = content_hash
        keep.uploaded_at = datetime.utcnow()
        keep.stored_path = _rel(dest)
        rec = keep
    else:
        rec = SeegRecording(
            reconstruction_id=recon_id, task=task, filename=safe_name,
            stored_path=_rel(dest), content_hash=content_hash,
        )
        db.add(rec)

    await db.commit()
    await db.refresh(rec)

    # Garbage-collect files no longer referenced by any surviving row for this
    # reconstruction (superseded duplicates + stray temp files). Best-effort: a
    # file still locked now is retried on the next upload, so this self-heals.
    remaining = (await db.execute(
        select(SeegRecording).where(SeegRecording.reconstruction_id == recon_id)
    )).scalars().all()
    referenced = {os.path.basename(_abs(r.stored_path)) for r in remaining if r.stored_path}
    try:
        for fn in os.listdir(seeg_dir):
            full = os.path.join(seeg_dir, fn)
            if not os.path.isfile(full) or fn in referenced:
                continue
            if fn.startswith(".tmp_") or fn.lower().endswith((".h5", ".hdf5")):
                try:
                    os.remove(full)
                except OSError:
                    pass
    except OSError:
        pass

    # Drop band-envelope caches (<h5>.<band>.npz) whose source recording is gone.
    env_cache = os.path.join(seeg_dir, ".envcache")
    if os.path.isdir(env_cache):
        try:
            for fn in os.listdir(env_cache):
                if not any(fn.startswith(h5 + ".") for h5 in referenced):
                    try:
                        os.remove(os.path.join(env_cache, fn))
                    except OSError:
                        pass
        except OSError:
            pass

    return {"id": rec.id, "task": rec.task, "filename": rec.filename,
            "replaced": bool(matches),
            "uploaded_at": rec.uploaded_at.isoformat() if rec.uploaded_at else None}


@app.get("/api/reconstructions/{recon_id}/seeg")
async def list_seeg(
    recon_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List sEEG recordings uploaded for this reconstruction."""
    result = await db.execute(
        select(SeegRecording)
        .where(SeegRecording.reconstruction_id == recon_id)
        .order_by(SeegRecording.uploaded_at.desc())
    )
    return [
        {"id": r.id, "task": r.task, "filename": r.filename,
         "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None}
        for r in result.scalars().all()
    ]


class SeegActivityRequest(BaseModel):
    band: str = "high_gamma"
    mode: str = "trial"                              # 'trial' | 'scroll'
    # Alignment event for trial epochs: 'stimulus' (start_time) | 'response' (response_onset).
    align: str = "stimulus"
    # Peri-event display window [start_ms, end_ms] relative to the align event (start < 0 < end).
    window_ms: Optional[List[float]] = None
    # Baseline window [start_ms, end_ms], both <= 0, ALWAYS relative to stimulus onset.
    baseline_ms: Optional[List[float]] = None
    # False = activation map only (skip the slow raw-voltage read; ``raw`` empty).
    include_raw: bool = True


@app.post("/api/reconstructions/{recon_id}/seeg/{rec_id}/activity")
async def compute_seeg_activity(
    recon_id: int,
    rec_id: int,
    req: SeegActivityRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Compute band activity for a recording and join it to this reconstruction's
    contacts. Returns the display matrix plus native and (if exported) MNI coords.
    """
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Reconstruction not found")

    rec_result = await db.execute(select(SeegRecording).where(SeegRecording.id == rec_id))
    rec = rec_result.scalar_one_or_none()
    if not rec or rec.reconstruction_id != recon_id:
        raise HTTPException(status_code=404, detail="Recording not found")
    h5_path = _abs(rec.stored_path)
    if not h5_path or not os.path.exists(h5_path):
        raise HTTPException(status_code=404, detail="Recording file missing on disk")

    from services.seeg_service import (
        compute_activity, parse_seeg_h5, join_channels_to_contacts,
        BANDS, DEFAULT_WINDOW_MS,
    )
    if req.band not in BANDS:
        raise HTTPException(status_code=400, detail=f"band must be one of {sorted(BANDS)}")
    if req.mode not in ("trial", "scroll"):
        raise HTTPException(status_code=400, detail="mode must be 'trial' or 'scroll'")

    window = None
    baseline = None
    if req.mode == "trial":
        if req.align not in ("stimulus", "response"):
            raise HTTPException(status_code=400, detail="align must be 'stimulus' or 'response'")
        window = tuple(req.window_ms) if req.window_ms else DEFAULT_WINDOW_MS
        if len(window) != 2 or not (window[0] < 0 < window[1]):
            raise HTTPException(status_code=400,
                                detail="window_ms must be [start, end] with start < 0 < end")
        if req.baseline_ms is not None:
            baseline = tuple(req.baseline_ms)
            if len(baseline) != 2 or not (baseline[0] < baseline[1] <= 0):
                raise HTTPException(status_code=400,
                                    detail="baseline_ms must be [start, end] with start < end <= 0")

    # Signal processing is CPU-bound; keep the event loop responsive.
    loop = asyncio.get_event_loop()
    try:
        activity = await loop.run_in_executor(
            None, lambda: compute_activity(
                h5_path, mode=req.mode, band=req.band, window_ms=window,
                baseline_ms=baseline, align=req.align, include_raw=req.include_raw)
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    meta = await loop.run_in_executor(None, parse_seeg_h5, h5_path)
    native = await _gather_native_contacts(db, recon_id)
    join = join_channels_to_contacts(meta["channels"], native, None)

    return {
        **activity,
        "coords_native": join["coords_native"],
        "matched": join["matched"],
        "unmatched_channels": join["unmatched_channels"],
        "unmatched_contacts": join["unmatched_contacts"],
        "attrs": meta["attrs"],
    }


@app.get("/api/reconstructions/{recon_id}/mri-slice")
async def get_mri_slice(
    recon_id: int,
    axis: str = "axial",       # axial | sagittal | coronal
    slice_idx: int = -1,       # -1 = auto middle
    token: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a single MRI slice as a PNG image, plus metadata."""
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Not found")
    if not recon.mri_path:
        raise HTTPException(status_code=404, detail="MRI not uploaded yet")
    mri_abs = _abs(recon.mri_path)
    if not os.path.exists(mri_abs):
        raise HTTPException(status_code=404, detail="MRI not uploaded yet")

    import asyncio
    loop = asyncio.get_event_loop()
    result_data = await loop.run_in_executor(
        None, _render_slice, mri_abs, axis, slice_idx
    )
    (png_bytes, shape, world_coord, voxel_size_mm, count, actual_idx, inv_affine,
     vol_shape, px_w_mm, px_h_mm, plane_normal, plane_offset) = result_data

    return FastAPIResponse(
        content=png_bytes,
        media_type="image/png",
        headers={
            "X-Slice-Index": str(actual_idx),
            "X-Slice-Count": str(count),
            "X-Slice-Width": str(shape[1]),
            "X-Slice-Height": str(shape[0]),
            # Plane centre — for display only. To test whether a world point is on
            # this slice, use the plane headers below; on an oblique volume this
            # value is only correct at the middle of the image.
            "X-Slice-World-Coord": str(world_coord),
            # Exact slice plane: |normal · P - offset| = mm from P to this slice.
            "X-Slice-Plane-Normal": json.dumps(plane_normal),
            "X-Slice-Plane-Offset": str(plane_offset),
            "X-Voxel-Size-Mm": str(voxel_size_mm),
            "X-Display-Px-Width-Mm": str(px_w_mm),
            "X-Display-Px-Height-Mm": str(px_h_mm),
            "X-Volume-Inv-Affine": json.dumps(inv_affine),
            "X-Volume-Shape": json.dumps(vol_shape),
        }
    )


@app.get("/api/reconstructions/{recon_id}/structure-slice")
async def get_structure_slice(
    recon_id: int,
    axis: str = "axial",
    slice_idx: int = -1,
    visible: Optional[str] = None,   # comma-separated structure keys; omit = all
    token: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return an RGBA PNG overlay of brain structure labels at the given MRI slice position."""
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Not found")
    mri_abs = _abs(recon.mri_path) if recon.mri_path else None
    if not mri_abs or not os.path.exists(mri_abs):
        raise HTTPException(status_code=404, detail="MRI not available")

    recon_dir  = os.path.dirname(mri_abs)
    label_path = os.path.join(recon_dir, "structures_cortical.nii.gz")

    visible_keys = set(visible.split(",")) if visible else None

    loop = asyncio.get_event_loop()
    png_bytes = await loop.run_in_executor(
        None, _render_structure_slice, mri_abs, label_path, axis, slice_idx, visible_keys
    )
    if png_bytes is None:
        raise HTTPException(status_code=404, detail="Structure labels not available for this reconstruction")

    return FastAPIResponse(content=png_bytes, media_type="image/png")


@app.get("/api/reconstructions/{recon_id}/fusion-slice")
async def get_fusion_slice(
    recon_id: int,
    axis: str = "axial",
    slice_idx: int = -1,
    candidate: Optional[int] = None,
    token: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return a grayscale PNG of the CT resampled into the MRI slice plane, for
    visual registration QA. Pixel-aligned with /mri-slice at the same axis/slice_idx —
    the frontend composites the two directly on top of each other.

    When `candidate` is set, render that precise-mode candidate basin
    (ct_to_mri.candidates/cand{idx}.npy) instead of the applied ct_to_mri.npy, so
    the reviewer can compare basins before selecting one.
    """
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Not found")
    if not current_user and recon.share_token != token:
        raise HTTPException(status_code=403, detail="Access denied")
    if not recon.ct_path or not recon.mri_path:
        raise HTTPException(status_code=404, detail="MRI or CT not available")

    mri_abs = _abs(recon.mri_path)
    ct_abs = _abs(recon.ct_path)
    if not mri_abs or not os.path.exists(mri_abs) or not ct_abs or not os.path.exists(ct_abs):
        raise HTTPException(status_code=404, detail="MRI or CT file missing on disk")

    from services.registration import load_transform, get_transform_path
    if candidate is not None:
        transform_path = os.path.join(_candidates_dir(ct_abs), f"cand{candidate}.npy")
        if not os.path.exists(transform_path):
            raise HTTPException(status_code=404, detail=f"Candidate {candidate} not available")
    else:
        transform_path = get_transform_path(ct_abs)
        if not os.path.exists(transform_path):
            raise HTTPException(status_code=404, detail="No registration transform available yet")
    transform = load_transform(transform_path)

    loop = asyncio.get_event_loop()
    try:
        png_bytes, actual_idx, count = await loop.run_in_executor(
            None, _render_fusion_slice, mri_abs, ct_abs, transform, axis, slice_idx
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fusion render failed: {e}")

    return FastAPIResponse(
        content=png_bytes,
        media_type="image/png",
        headers={
            "X-Slice-Index": str(actual_idx),
            "X-Slice-Count": str(count),
        }
    )


@app.post("/api/reconstructions/{recon_id}/prerender-slices")
async def prerender_slices(
    recon_id: int,
    axis: str = "axial",
    token: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Background task: pre-render all slices for one axis into the PNG cache."""
    import asyncio
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    mri_abs = _abs(recon.mri_path) if recon and recon.mri_path else None
    if not mri_abs or not os.path.exists(mri_abs):
        return {"status": "skipped"}

    mri_path = mri_abs

    async def _prerender():
        loop = asyncio.get_event_loop()
        vol = await loop.run_in_executor(None, _get_mri_volume, mri_path)
        ax = {"sagittal": 0, "coronal": 1, "axial": 2}.get(axis, 2)
        n = vol["data"].shape[ax]
        # Render in chunks to avoid blocking the event loop too long
        for i in range(n):
            if (axis, i) not in vol["png_cache"]:
                await loop.run_in_executor(None, _render_slice, mri_path, axis, i)

    asyncio.create_task(_prerender())
    return {"status": "started", "axis": axis}


@app.get("/api/reconstructions/{recon_id}/structures")
async def get_structures(
    recon_id: int,
    token: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return subcortical structure meshes aligned to this reconstruction's brain mesh."""
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Not found")
    if not current_user and recon.share_token != token:
        raise HTTPException(status_code=403, detail="Access denied")
    mesh_abs = _abs(recon.mesh_path) if recon.mesh_path else None
    if not mesh_abs or not os.path.exists(mesh_abs):
        raise HTTPException(status_code=404, detail="Brain mesh not ready yet")

    from services.structure_extractor import (
        extract_all_structures_isolated, _load_cached_structures)
    recon_dir = os.path.dirname(mesh_abs)

    loop = asyncio.get_event_loop()
    # Runs in a child process: parcellation is large enough to be OOM-killed, and
    # in-process that takes the whole server down with it.
    extraction_unavailable = False
    try:
        structures = await loop.run_in_executor(
            None, extract_all_structures_isolated, mesh_abs, recon_dir, _abs(recon.mri_path)
        )
    except ImportError as e:
        print(f"[STRUCT] Structure extraction unavailable: {e}")
        structures = {}
        extraction_unavailable = True
    except Exception as e:
        # A worker that died (OOM) or failed outright. Do NOT fall through to
        # borrowing: showing another patient's anatomy on this reconstruction
        # would be worse than showing none.
        print(f"[STRUCT] Structure extraction failed for recon {recon.id}: {e}")
        structures = {}

    # If antspynet/tensorflow is unavailable, borrow a pre-computed cache from
    # another reconstruction. Cache only -- never compute another patient's scan.
    if not structures and extraction_unavailable:
        other_result = await db.execute(
            select(Reconstruction)
            .where(Reconstruction.id != recon.id)
            .where(Reconstruction.mesh_path.isnot(None))
        )
        for other in other_result.scalars().all():
            other_mesh_abs = _abs(other.mesh_path) if other.mesh_path else None
            if not other_mesh_abs or not os.path.exists(other_mesh_abs):
                continue
            other_dir = os.path.dirname(other_mesh_abs)
            try:
                borrowed = await loop.run_in_executor(
                    None, _load_cached_structures, other_dir
                )
                if borrowed:
                    print(f"[STRUCT] Borrowed {len(borrowed)} structures from recon {other.id}")
                    structures = borrowed
                    break
            except Exception:
                continue

    return structures

@app.get("/api/reconstructions/{recon_id}/mesh")
async def get_mesh(
    recon_id: int,
    token: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return mesh geometry JSON for Three.js rendering."""
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Not found")
    if not current_user and recon.share_token != token:
        raise HTTPException(status_code=403, detail="Access denied")
    mesh_abs = _abs(recon.mesh_path) if recon.mesh_path else None
    if not mesh_abs or not os.path.exists(mesh_abs):
        raise HTTPException(status_code=404, detail="Mesh not ready yet")

    with open(mesh_abs) as f:
        return JSONResponse(json.load(f))


@app.get("/api/reconstructions/{recon_id}/share-link")
async def get_share_link(
    recon_id: int,
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Not found")
    return {"share_url": f"/view/{recon_id}?token={recon.share_token}"}


# ─── Electrode Routes ─────────────────────────────────────────────────────────

@app.post("/api/reconstructions/{recon_id}/shafts")
async def create_shaft(
    recon_id: int,
    data: ShaftCreate,
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Create a new electrode shaft for a reconstruction."""
    shaft = ElectrodeShaft(
        reconstruction_id=recon_id,
        name=data.name,
        label=data.label,
        electrode_type=data.electrode_type,
        color=data.color,
        n_total_contacts=data.n_total_contacts,
        spacing_mm=data.spacing_mm,
        grid_rows=data.grid_rows,
        grid_cols=data.grid_cols,
        contact_diameter_mm=data.contact_diameter_mm,
        contact_length_mm=data.contact_length_mm,
        shaft_diameter_mm=data.shaft_diameter_mm,
    )
    db.add(shaft)
    await db.commit()
    await db.refresh(shaft)
    return {"id": shaft.id, "name": shaft.name, "label": shaft.label, "electrode_type": shaft.electrode_type, "color": shaft.color}


@app.post("/api/shafts/{shaft_id}/contacts")
async def add_contact(
    shaft_id: int,
    data: ContactCreate,
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Add or update a single contact on a shaft. x,y,z are CT voxel coords."""
    result = await db.execute(select(ElectrodeShaft).where(ElectrodeShaft.id == shaft_id))
    shaft = result.scalar_one_or_none()
    if not shaft:
        raise HTTPException(status_code=404, detail="Shaft not found")

    # Get CT affine to convert voxel -> world
    recon_result = await db.execute(
        select(Reconstruction).where(Reconstruction.id == shaft.reconstruction_id)
    )
    recon = recon_result.scalar_one_or_none()
    x_mm, y_mm, z_mm = data.x, data.y, data.z  # fallback

    if data.is_world_mm:
        # Coords already in world mm — use directly
        x_mm, y_mm, z_mm = data.x, data.y, data.z
    elif recon and recon.ct_path and os.path.exists(_abs(recon.ct_path)):
        import nibabel as nib
        import numpy as np
        affine = nib.load(_abs(recon.ct_path)).affine
        world = voxel_to_world([data.x, data.y, data.z], affine)
        x_mm, y_mm, z_mm = world

    # Check if this contact number already exists (update it)
    existing = await db.execute(
        select(ElectrodeContact)
        .where(ElectrodeContact.shaft_id == shaft_id)
        .where(ElectrodeContact.contact_number == data.contact_number)
    )
    contact = existing.scalar_one_or_none()
    if contact:
        contact.x = data.x; contact.y = data.y; contact.z = data.z
        contact.x_mm = x_mm; contact.y_mm = y_mm; contact.z_mm = z_mm
        contact.is_manual = data.is_manual
    else:
        contact = ElectrodeContact(
            shaft_id=shaft_id,
            contact_number=data.contact_number,
            x=data.x, y=data.y, z=data.z,
            x_mm=x_mm, y_mm=y_mm, z_mm=z_mm,
            is_manual=data.is_manual,
        )
        db.add(contact)

    await db.commit()
    return {"message": "Contact saved", "x_mm": x_mm, "y_mm": y_mm, "z_mm": z_mm}


@app.delete("/api/reconstructions/shafts/{shaft_id}")
async def delete_shaft(
    shaft_id: int,
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ElectrodeShaft).where(ElectrodeShaft.id == shaft_id))
    shaft = result.scalar_one_or_none()
    if not shaft:
        raise HTTPException(status_code=404, detail="Shaft not found")
    await db.delete(shaft)
    await db.commit()
    return {"message": "Shaft deleted"}


@app.post("/api/reconstructions/{recon_id}/snap-to-blob")
async def snap_contact_to_blob(
    recon_id: int,
    world_pos: List[float] = Body(..., embed=True),
    threshold: float = Body(..., embed=True),
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Given a click position in Three.js world space, find the centroid of the
    connected CT blob at that location and return it as the snapped position.
    """
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon or not recon.ct_path:
        raise HTTPException(status_code=404, detail="No CT for this reconstruction")

    mesh_center = [0.0, 0.0, 0.0]
    if recon.mesh_path and os.path.exists(_abs(recon.mesh_path)):
        try:
            with open(_abs(recon.mesh_path)) as f:
                mesh_json = json.load(f)
            mesh_center = mesh_json.get("center", [0.0, 0.0, 0.0])
        except Exception:
            pass

    import traceback
    loop = asyncio.get_event_loop()
    ct_abs = _abs(recon.ct_path)

    # Load registration transform if available
    transform = None
    try:
        from services.registration import load_transform, get_transform_path
        tp = get_transform_path(ct_abs)
        if os.path.exists(tp):
            transform = load_transform(tp)
    except Exception:
        pass

    try:
        snapped = await loop.run_in_executor(
            None,
            lambda: snap_to_blob_centroid(ct_abs, world_pos, mesh_center, threshold, transform=transform)
        )
        return {"snapped_position": snapped}
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[SNAP ERROR]\n{tb}")
        raise HTTPException(status_code=500, detail=f"Snap failed: {str(e)}")


@app.post("/api/shafts/{shaft_id}/init-contacts")
async def init_contacts(
    shaft_id: int,
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """
    Create empty placeholder contacts for all N slots on a shaft.
    Contacts have no position yet (x_mm=None) — they get positions
    as the fellow clicks on the CT.
    """
    result = await db.execute(select(ElectrodeShaft).where(ElectrodeShaft.id == shaft_id))
    shaft = result.scalar_one_or_none()
    if not shaft:
        raise HTTPException(status_code=404, detail="Shaft not found")

    n = getattr(shaft, 'n_total_contacts', 12) or 12

    # Only create contacts that don't already exist
    existing_result = await db.execute(
        select(ElectrodeContact).where(ElectrodeContact.shaft_id == shaft_id)
    )
    existing_numbers = {c.contact_number for c in existing_result.scalars().all()}

    for i in range(1, n + 1):
        if i not in existing_numbers:
            db.add(ElectrodeContact(
                shaft_id=shaft_id,
                contact_number=i,
                x=0, y=0, z=0,
                x_mm=None, y_mm=None, z_mm=None,
                is_manual=False,
            ))

    await db.commit()
    return {"message": f"Initialized {n} contacts", "n": n}


@app.post("/api/shafts/{shaft_id}/autofill")
async def autofill_shaft(
    shaft_id: int,
    data: AutofillRequest,
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """
    Given 3+ manually placed contacts (world mm coords),
    fit a spline and return predicted positions for all contacts.
    Also saves them to the database.
    """
    result = await db.execute(select(ElectrodeShaft).where(ElectrodeShaft.id == shaft_id))
    shaft = result.scalar_one_or_none()
    if not shaft:
        raise HTTPException(status_code=404, detail="Shaft not found")

    manual = [{"contact_number": c.contact_number, "position": c.position} for c in data.manual_contacts]
    predicted = autofill_contacts(
        manual_contacts=manual,
        electrode_type=data.electrode_type,
        n_total_contacts=data.n_total_contacts,
        spacing_mm=data.spacing_mm,
        grid_rows=data.grid_rows or 1,
        grid_cols=data.grid_cols or 1,
    )

    # Track range of manually placed contacts — only snap interpolated contacts,
    # not extrapolated ones beyond the manual range (those may be in the bolt)
    manual_numbers = {c.contact_number for c in data.manual_contacts}
    manual_min = min(manual_numbers)
    manual_max = max(manual_numbers)

    # Optionally snap predicted positions to blob centroids
    recon_result = await db.execute(
        select(Reconstruction).where(Reconstruction.id == shaft.reconstruction_id)
    )
    recon = recon_result.scalar_one_or_none()
    has_ct = recon and recon.ct_path and os.path.exists(_abs(recon.ct_path))

    if has_ct:
        mesh_center = [0.0, 0.0, 0.0]
        if recon.mesh_path and os.path.exists(_abs(recon.mesh_path)):
            try:
                with open(_abs(recon.mesh_path)) as f2:
                    mj = json.load(f2)
                mesh_center = mj.get("center", [0.0, 0.0, 0.0])
            except Exception:
                pass

    loop = asyncio.get_event_loop()

    # Save all predicted contacts
    for c in predicted:
        pos = c["position"]
        contact_num = c["contact_number"]
        is_interpolated = not c.get("is_manual", False) and manual_min <= contact_num <= manual_max
        if has_ct and is_interpolated and data.hu_threshold is not None:
            try:
                pos = await loop.run_in_executor(
                    None,
                    lambda p=pos: snap_to_blob_centroid(
                        _abs(recon.ct_path), p, mesh_center, data.hu_threshold, search_radius_mm=6.0
                    )
                )
            except Exception:
                pass  # fall back to spline position if snap fails

        existing = await db.execute(
            select(ElectrodeContact)
            .where(ElectrodeContact.shaft_id == shaft_id)
            .where(ElectrodeContact.contact_number == c["contact_number"])
        )
        contact = existing.scalar_one_or_none()
        if contact:
            contact.x_mm = pos[0]; contact.y_mm = pos[1]; contact.z_mm = pos[2]
            contact.is_manual = c.get("is_manual", False)
        else:
            contact = ElectrodeContact(
                shaft_id=shaft_id,
                contact_number=c["contact_number"],
                x=pos[0], y=pos[1], z=pos[2],
                x_mm=pos[0], y_mm=pos[1], z_mm=pos[2],
                is_manual=c.get("is_manual", False),
            )
            db.add(contact)

    await db.commit()
    return {"predicted_contacts": predicted}


class ShaftUpdate(BaseModel):
    visible: Optional[bool] = None
    color: Optional[str] = None
    label: Optional[str] = None
    name: Optional[str] = None
    contact_diameter_mm: Optional[float] = None
    contact_length_mm: Optional[float] = None
    shaft_diameter_mm: Optional[float] = None

@app.patch("/api/shafts/{shaft_id}")
async def update_shaft(
    shaft_id: int,
    data: ShaftUpdate,
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ElectrodeShaft).where(ElectrodeShaft.id == shaft_id))
    shaft = result.scalar_one_or_none()
    if not shaft:
        raise HTTPException(status_code=404, detail="Not found")
    if data.visible is not None: shaft.visible = data.visible
    if data.color is not None: shaft.color = data.color
    if data.label is not None: shaft.label = data.label
    if data.name is not None: shaft.name = data.name
    if data.contact_diameter_mm is not None: shaft.contact_diameter_mm = data.contact_diameter_mm
    if data.contact_length_mm is not None: shaft.contact_length_mm = data.contact_length_mm
    if data.shaft_diameter_mm is not None: shaft.shaft_diameter_mm = data.shaft_diameter_mm
    await db.commit()
    return {"message": "Updated"}


@app.delete("/api/shafts/{shaft_id}/contacts/{contact_number}")
async def delete_contact(
    shaft_id: int,
    contact_number: int,
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ElectrodeContact)
        .where(ElectrodeContact.shaft_id == shaft_id)
        .where(ElectrodeContact.contact_number == contact_number)
    )
    contact = result.scalar_one_or_none()
    if contact:
        await db.delete(contact)
        await db.commit()
    return {"message": "Deleted"}


# ─── Delete / Trash Routes ────────────────────────────────────────────────────

@app.patch("/api/reconstructions/{recon_id}/soft-delete")
async def soft_delete_reconstruction(
    recon_id: int,
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Move reconstruction to trash (sets deleted_at timestamp)."""
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Not found")
    recon.deleted_at = datetime.utcnow()
    await db.commit()
    return {"message": "Moved to trash"}


@app.get("/api/reconstructions/deleted")
async def list_deleted_reconstructions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all soft-deleted reconstructions."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Login required")
    result = await db.execute(
        select(Reconstruction)
        .where(Reconstruction.deleted_at != None)
        .order_by(Reconstruction.deleted_at.desc())
    )
    recons = result.scalars().all()
    return [
        {
            "id": recon.id,
            "patient_id": recon.patient_id,
            "label": recon.label,
            "status": recon.status,
            "is_complete": getattr(recon, "is_complete", False) or False,
            "created_at": recon.created_at,
            "deleted_at": recon.deleted_at,
        }
        for recon in recons
    ]



@app.patch("/api/reconstructions/{recon_id}/restore")
async def restore_reconstruction(
    recon_id: int,
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Restore a soft-deleted reconstruction back to In Progress."""
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Not found")
    recon.deleted_at = None
    recon.is_complete = False
    recon.is_locked = False
    await db.commit()
    return {"message": "Restored"}

@app.delete("/api/reconstructions/{recon_id}/permanent")
async def permanently_delete_reconstruction(
    recon_id: int,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a reconstruction and all its data files."""
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Not found")

    # Delete data folder if it exists
    import shutil
    for path in [recon.mri_path, recon.ct_path, recon.mesh_path]:
        if path and os.path.exists(_abs(path)):
            recon_dir = os.path.dirname(_abs(path))
            if os.path.isdir(recon_dir):
                try:
                    shutil.rmtree(recon_dir)
                except Exception as e:
                    print(f"[DELETE] Could not remove dir {recon_dir}: {e}")
            break  # All files are in the same folder — only need to delete once

    await db.delete(recon)
    await db.commit()
    return {"message": "Permanently deleted"}




@app.get("/api/reconstructions/{recon_id}/ct-threshold-mesh")
async def get_ct_threshold_mesh(
    recon_id: int,
    threshold: float = Query(-200.0, ge=-1000, le=5000),
    ceiling: Optional[float] = Query(None, ge=-1000, le=5000),
    token: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return a surface mesh of CT voxels within the HU window (`threshold`,
    `ceiling`]. `ceiling` is optional — omit it for a floor-only (open-top)
    threshold. The user adjusts the window interactively until only electrode
    metal is visible, then clicks on the mesh to place contacts.
    Results are cached per (threshold, ceiling) value to avoid redundant work.
    """
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Not found")
    if not current_user and recon.share_token != token:
        raise HTTPException(status_code=403, detail="Access denied")
    ct_abs = _abs(recon.ct_path) if recon.ct_path else None
    if not ct_abs or not os.path.exists(ct_abs):
        raise HTTPException(status_code=400, detail="No CT file for this reconstruction")

    # Get brain mesh center for alignment
    mesh_center = [0.0, 0.0, 0.0]
    if recon.mesh_path and os.path.exists(_abs(recon.mesh_path)):
        with open(_abs(recon.mesh_path)) as f:
            mesh_data = json.load(f)
            mesh_center = mesh_data.get("center", [0.0, 0.0, 0.0])

    cache_dir = os.path.join(os.path.dirname(ct_abs), "ct_cache")

    # Load registration transform if available
    transform = None
    try:
        from services.registration import load_transform, get_transform_path
        tp = get_transform_path(ct_abs)
        if os.path.exists(tp):
            transform = load_transform(tp)
            print(f"[CT MESH] Using registered transform")
        else:
            print(f"[CT MESH] No transform found - displaying unregistered CT")
    except Exception as e:
        print(f"[CT MESH] Could not load transform: {e}")

    # Run in thread pool (CPU-bound)
    loop = asyncio.get_event_loop()
    mesh_result = await loop.run_in_executor(
        None,
        lambda: build_threshold_mesh(ct_abs, mesh_center, threshold, cache_dir, transform,
                                     hu_ceiling=ceiling)
    )

    return JSONResponse(mesh_result)


@app.get("/api/reconstructions/{recon_id}/ct-histogram")
async def get_ct_histogram(
    recon_id: int,
    bins: int = Query(128, ge=16, le=512),
    token: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return a histogram of CT HU intensities for the threshold-slider background,
    plus the CT's actual HU data range. Cached per CT.
    """
    result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
    recon = result.scalar_one_or_none()
    if not recon:
        raise HTTPException(status_code=404, detail="Not found")
    if not current_user and recon.share_token != token:
        raise HTTPException(status_code=403, detail="Access denied")
    ct_abs = _abs(recon.ct_path) if recon.ct_path else None
    if not ct_abs or not os.path.exists(ct_abs):
        raise HTTPException(status_code=400, detail="No CT file for this reconstruction")

    cache_dir = os.path.join(os.path.dirname(ct_abs), "ct_cache")
    loop = asyncio.get_event_loop()
    hist = await loop.run_in_executor(
        None,
        lambda: compute_ct_histogram(ct_abs, cache_dir=cache_dir, bins=bins)
    )
    return JSONResponse(hist)


# ─── Serve React frontend (added for standalone .exe build) ──────────────────
# This block serves the React build folder when running as a PyInstaller bundle.
# In normal dev mode (npm start on port 3000), this folder won't exist and the
# block is safely skipped.

_FRONTEND_BUILD = os.path.join(
    sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__)),
    "frontend_build"
)

if os.path.isdir(_FRONTEND_BUILD):
    _static_dir = os.path.join(_FRONTEND_BUILD, "static")
    if os.path.isdir(_static_dir):
        app.mount("/static", StaticFiles(directory=_static_dir), name="react-static")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_react(full_path: str):
        """Catch-all: serve index.html so React Router handles client-side navigation."""
        return FileResponse(os.path.join(_FRONTEND_BUILD, "index.html"))
