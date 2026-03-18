"""
Brain Reconstruction Viewer - FastAPI Backend
Handles: auth, reconstruction CRUD, NIfTI mesh extraction, electrode management
"""

import os
import sys
import uuid
import json
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

from database import init_db, get_db, AsyncSessionLocal, User, Reconstruction, ElectrodeShaft, ElectrodeContact
from auth import (
    verify_password, hash_password, create_access_token,
    get_current_user, require_editor, require_admin
)
from services.mesh_extractor import extract_brain_mesh, get_nifti_affine, voxel_to_world
import numpy as np
from PIL import Image
from fastapi.responses import Response as FastAPIResponse

# ── In-memory NIfTI slice cache ─────────────────────────────────────────────
_mri_volume_cache: dict = {}  # mri_path -> {"data", "affine", "slices": {axis: [png_bytes, ...]}}

def _get_mri_volume(mri_path: str):
    """Load and canonicalize NIfTI once; cache the float array."""
    if mri_path not in _mri_volume_cache:
        import nibabel as nib
        img = nib.load(mri_path)
        img_ras = nib.as_closest_canonical(img)
        data = img_ras.get_fdata()
        # Pre-compute per-axis normalization stats (percentile over whole volume)
        axis_stats = {}
        for ax, name in [(0, "sagittal"), (1, "coronal"), (2, "axial")]:
            flat = data.ravel()
            nonzero = flat[flat > 0]
            vmin = float(np.percentile(nonzero, 2)) if len(nonzero) else 0.0
            vmax = float(np.percentile(nonzero, 98)) if len(nonzero) else 1.0
            axis_stats[name] = (vmin, vmax)
        _mri_volume_cache[mri_path] = {
            "data": data,
            "affine": img_ras.affine,
            "axis_stats": axis_stats,
            "png_cache": {},  # (axis, slice_idx) -> png_bytes
        }
    return _mri_volume_cache[mri_path]

def _render_slice(mri_path: str, axis: str, slice_idx: int):
    """Return (png_bytes, shape, world_coord, voxel_size_mm, count, actual_idx), cached."""
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
    world_coord = float(affine[ax, 3] + affine[ax, ax] * slice_idx)
    voxel_size_mm = float(voxel_sizes[ax])

    inv_affine = np.linalg.inv(affine)
    result = (png_bytes, sl_uint8.shape, world_coord, voxel_size_mm, n, slice_idx,
              inv_affine.flatten().tolist(), list(data.shape))
    vol["png_cache"][key] = result  # cache the full tuple
    return result


from services.electrode_service import autofill_contacts
from services.ct_electrode_extractor import build_threshold_mesh, snap_to_blob_centroid

# ── Structure overlay slice cache ─────────────────────────────────────────────
_struct_overlay_cache: dict = {}  # label_path -> {"data", "affine", "png_cache": {(axis,idx): bytes}}

def _get_label_volume(label_path: str):
    """Load DKT label NIfTI (RAS canonical) and cache it."""
    if label_path not in _struct_overlay_cache:
        import nibabel as nib
        img = nib.load(label_path)
        img_ras = nib.as_closest_canonical(img)
        _struct_overlay_cache[label_path] = {
            "data": np.round(img_ras.get_fdata()).astype(np.int32),
            "affine": img_ras.affine,
        }
    return _struct_overlay_cache[label_path]

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

    world_coord = float(mri_aff[ax, 3] + mri_aff[ax, ax] * slice_idx)

    # Find the DKT voxel index that corresponds to this world coordinate
    step = float(laff[ax, ax])
    if abs(step) < 1e-6:
        return None
    dkt_idx = int(round((world_coord - float(laff[ax, 3])) / step))
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
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://127.0.0.1:8000", "http://localhost:8000"],
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

DATA_DIR = os.path.join(_get_runtime_dir(), "data")
os.makedirs(DATA_DIR, exist_ok=True)


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

    # Create default admin user if none exists
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        if not result.scalar_one_or_none():
            admin = User(
                username="admin",
                hashed_password=hash_password("changeme"),
                role="admin"
            )
            db.add(admin)
            await db.commit()


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
            "electrode_shafts": shafts_data,
        })
    return out


@app.post("/api/reconstructions", response_model=ReconstructionResponse)
async def create_reconstruction(
    patient_id: str = Form(...),
    label: str = Form(...),
    mri_file: UploadFile = File(...),
    ct_file: Optional[UploadFile] = File(None),
    ct_preregistered: bool = Form(False),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Upload NIfTI files and kick off mesh extraction in background."""
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
    background_tasks.add_task(_extract_mesh_background, recon.id, mri_path, recon_dir, ct_path, ct_preregistered)

    return recon


@app.post("/api/reconstructions/{recon_id}/files")
async def upload_reconstruction_files(
    recon_id: int,
    mri_file: Optional[UploadFile] = File(None),
    ct_file: Optional[UploadFile] = File(None),
    ct_preregistered: bool = Form(False),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    """Upload or replace MRI/CT files for an existing reconstruction and re-run processing."""
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
        background_tasks.add_task(_extract_mesh_background, recon_id, mri_path, recon_dir, ct_path, ct_preregistered)

    return {"status": "processing"}


async def _extract_mesh_background(recon_id: int, mri_path: str, recon_dir: str, ct_path: str = None, ct_preregistered: bool = False):
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
                await loop.run_in_executor(None, extract_brain_mesh, mri_path, mesh_path, None)
        else:
            await loop.run_in_executor(None, extract_brain_mesh, mri_path, mesh_path, None)
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

    # If mesh extraction succeeded and a CT exists, run registration
    if status == "ready":
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Reconstruction).where(Reconstruction.id == recon_id))
            recon = result.scalar_one_or_none()
            if recon and recon.ct_path and os.path.exists(_abs(recon.ct_path)):
                # Mark as registering so the UI shows spinner and blocks access
                await db.execute(
                    update(Reconstruction)
                    .where(Reconstruction.id == recon_id)
                    .values(status="registering", updated_at=datetime.utcnow())
                )
                await db.commit()
                try:
                    from services.registration import register_ct_to_mri, get_transform_path
                    import numpy as _np
                    ct_abs = _abs(recon.ct_path)
                    transform_path = get_transform_path(ct_abs)
                    if ct_preregistered:
                        # CT already in MRI space — save identity, skip computation
                        _np.save(transform_path, _np.eye(4))
                        print(f"[REG] CT marked as pre-registered — identity transform saved for recon {recon_id}")
                    else:
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None, register_ct_to_mri, mri_path, ct_abs, transform_path
                        )
                        print(f"[REG] Registration complete for recon {recon_id}")
                    # Preprocess CT to strip table/air regardless of registration path
                    from services.registration import preprocess_ct, get_masked_ct_path
                    masked_ct_path = get_masked_ct_path(ct_abs)
                    await loop.run_in_executor(
                        None, preprocess_ct, ct_abs, masked_ct_path
                    )
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
        "electrode_shafts": shafts_data,
    }


@app.patch("/api/reconstructions/{recon_id}/status")
async def update_reconstruction_status(
    recon_id: int,
    is_complete: Optional[bool] = Body(None, embed=True),
    is_locked: Optional[bool] = Body(None, embed=True),
    current_user: Optional[User] = Depends(get_current_user),
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
    if is_locked is not None:
        recon.is_locked = is_locked
    await db.commit()
    return {"is_complete": recon.is_complete, "is_locked": recon.is_locked}


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
    png_bytes, shape, world_coord, voxel_size_mm, count, actual_idx, inv_affine, vol_shape = result_data

    return FastAPIResponse(
        content=png_bytes,
        media_type="image/png",
        headers={
            "X-Slice-Index": str(actual_idx),
            "X-Slice-Count": str(count),
            "X-Slice-Width": str(shape[1]),
            "X-Slice-Height": str(shape[0]),
            "X-Slice-World-Coord": str(world_coord),
            "X-Voxel-Size-Mm": str(voxel_size_mm),
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

    from services.structure_extractor import extract_all_structures
    recon_dir = os.path.dirname(mesh_abs)

    loop = asyncio.get_event_loop()
    try:
        structures = await loop.run_in_executor(
            None, extract_all_structures, mesh_abs, recon_dir, _abs(recon.mri_path)
        )
    except (ImportError, Exception) as e:
        print(f"[STRUCT] Structure extraction unavailable: {e}")
        structures = {}

    # If no structures found, borrow from any other reconstruction that has a
    # pre-computed structures cache (used when antspynet/tensorflow is unavailable).
    if not structures:
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
                    None, extract_all_structures, other_mesh_abs, other_dir, _abs(other.mri_path)
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
    token: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return a surface mesh of all CT voxels above `threshold` HU.
    The user adjusts the threshold interactively until only electrode
    metal is visible, then clicks on the mesh to place contacts.
    Results are cached per threshold value to avoid redundant processing.
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
            print(f"[CT MESH] No transform found — displaying unregistered CT")
    except Exception as e:
        print(f"[CT MESH] Could not load transform: {e}")

    # Run in thread pool (CPU-bound)
    loop = asyncio.get_event_loop()
    mesh_result = await loop.run_in_executor(
        None,
        lambda: build_threshold_mesh(ct_abs, mesh_center, threshold, cache_dir, transform)
    )

    return JSONResponse(mesh_result)


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
