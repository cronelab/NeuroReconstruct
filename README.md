# NeuroReconstruct — sEEG / ECoG Brain Viewer

A web-based tool for creating and reviewing virtual 3D brain reconstructions with surgically implanted sEEG depth electrodes and ECoG grids/strips. Built for clinical research use at Johns Hopkins.

---

## Setup from Scratch

### 1. Create the conda environment

```bash
conda create -n neuro-recon python=3.11
conda activate neuro-recon
pip install -r backend/requirements.txt
```

Then install the neuroimaging packages (not in requirements.txt due to size):

```bash
pip install antspyx antspynet SimpleITK
pip install "numpy<2.0"   # pin after installing — antspynet may upgrade it
```

Verify the install:

```bash
python -c "import nibabel, sklearn, ants, SimpleITK; print('OK')"
```

### 2. Install frontend dependencies

```bash
cd frontend
npm install
```

### 3. Create the first admin user

```bash
cd backend
conda activate neuro-recon
python create_admin.py
```

This creates an `admin` user with password `changeme`. Change the password after first login.

### 4. Build the standalone exe (Windows only)

```bash
cd backend
conda activate neuro-recon
pyinstaller neuro_recon.spec --noconfirm
```

Output: `backend/dist/NeuroReconstruct.exe`. Copy this file plus `brain_viewer.db` and `data/` into a `dist_demo/` folder for distribution.

### 5. Database migrations (existing installs only)

Only needed if upgrading an existing `brain_viewer.db` from a version prior to mid-2025. A fresh install creates all columns automatically.

```bash
cd backend
python migrate_shaft_fields.py   # adds electrode shaft metadata columns
python migrate_lock_fields.py    # adds is_complete / is_locked columns
python migrate_deleted_at.py     # adds soft-delete support
```

All three are safe to run multiple times — they skip columns that already exist.

---

## Quick Start (Dev)

**Prerequisites:** Python 3.10+, Node.js 18+, conda (recommended)

```bash
# Backend — from /backend
conda activate neuro-recon
uvicorn main:app --reload

# Frontend — from /frontend (separate terminal)
npm start
```

App: http://localhost:3000
API docs: http://localhost:8000/docs
Default credentials: `admin` / `changeme`

> **Note:** `numpy < 2.0` is required. After any `pip install`, verify with `python -c "import nibabel, sklearn"`. If broken: `pip install "numpy<2.0"`

---

## Standalone Demo (Windows only)

The dev environment runs on any OS (Mac, Linux, Windows). The standalone exe is Windows-only — Mac/Linux users should run the backend and frontend directly instead.

A pre-built Windows executable is available as `dist_demo/`:

```
dist_demo/
├── NeuroReconstruct.exe   # Self-contained server + frontend
├── brain_viewer.db        # Pre-populated database
└── data/                  # Reconstruction folders
```

Double-click `NeuroReconstruct.exe`. The app opens automatically in your browser. No Python or Node.js installation required.

> Requires Visual C++ Redistributable (pre-installed on most Windows machines).

---

## Workflow

1. Log in and click **New Reconstruction**
2. Upload a de-identified T1 MRI (`.nii` or `.nii.gz`) and post-implant CT
3. Brain mesh and CT-to-MRI co-registration run automatically in the background (~2–5 min)
4. Open the reconstruction → CT artifact mesh appears in the 3D viewer
5. Create an electrode shaft (name, type, number of contacts, spacing)
6. Select a contact number → click on the CT mesh to place it (snaps to nearest bright blob)
7. Place 2+ contacts → use **Autofill** to interpolate the remaining contacts via spline fit
8. Load brain substructures to visualize hippocampus, amygdala, thalamus, etc.
9. Mark as Complete → switches to MRI view with electrode overlay

---

## Architecture

### Backend (`backend/`)

| File | Purpose |
|---|---|
| `main.py` | FastAPI app. All endpoints: auth, reconstruction CRUD, mesh serving, MRI slice rendering (with in-memory cache), electrode management, CT mesh generation, snap-to-blob, brain structures. |
| `database.py` | SQLAlchemy async models: `User`, `Reconstruction`, `ElectrodeShaft`, `ElectrodeContact`. SQLite via aiosqlite. |
| `auth.py` | JWT creation/verification, bcrypt password hashing, `get_current_user` FastAPI dependency. |
| `launcher.py` | PyInstaller entry point. Sets `ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=1` before any imports to ensure deterministic registration. |
| `neuro_recon.spec` | PyInstaller spec for the standalone exe. Bundles targeted DLLs and data files. |

#### Services (`backend/services/`)

| File | Purpose |
|---|---|
| `mesh_extractor.py` | Extracts brain surface mesh from T1 MRI NIfTI. Uses **antspynet** for deep-learning skull stripping when available; falls back to morphological thresholding (erosion r=8mm, Gaussian σ=0.5, parenchyma refinement). Mesh produced via marching cubes, returned as vertices/faces in world RAS coords centered at origin. |
| `registration.py` | CT-to-MRI rigid registration via **SimpleITK**. Mattes mutual information optimizer, REGULAR (deterministic) sampling, 4-resolution pyramid, GEOMETRY centroid initialization. Saves result as `ct_to_mri.npy` (4×4 affine matrix). Single-threaded for reproducibility. |
| `structure_extractor.py` | Patient-specific brain structure segmentation via **antspynet**. Segments hippocampus, amygdala, thalamus, putamen, caudate, pallidum, and cortical parcellation. Results cached as `structures.json` + `structures_cortical.nii.gz`. Falls back to borrowing cached structures from any other reconstruction if antspynet is unavailable. |
| `ct_electrode_extractor.py` | CT mesh generation (HU threshold + marching cubes). `snap_to_blob_centroid()` snaps a clicked world position to the nearest bright CT blob centroid within 8mm. |
| `electrode_service.py` | Autofill: cubic spline fit parameterized by contact number. Interpolates between placed contacts, linear extrapolation beyond the manual range. Blob-snap applied to interpolated contacts only. |

### Frontend (`frontend/src/`)

| File | Purpose |
|---|---|
| `App.jsx` | Root component. Manages page state (list / viewer / login), session restore from localStorage, URL routing for share links. |
| `store.js` | Zustand global state: `user`, `token`, `reconstruction`, `meshData`, `isEditorMode`, `shaftVisibility`, `selectedShaftId`, `activeContactNumber`, `brainOpacity`, `structuresData`, `structureVisible`. |
| `api.js` | All axios API calls. Auth token injected via request interceptor. 401 responses trigger automatic logout. |

#### Components

| Component | Purpose |
|---|---|
| `LoginPage.jsx` | Login form with JWT auth. |
| `ReconstructionList.jsx` | Home page. Two-column layout: In Progress (left) and Completed (right). Shows shaft/contact counts. Upload form for MRI+CT. Polls status every 10s. |
| `Header.jsx` | Top bar. Logo → home. Mark Complete / Unlock toggle. Edit button (disabled when locked). |
| `ReconstructionViewer.jsx` | Main viewer shell. Owns CT mesh loading, MRI visibility toggle, CT threshold slider with 400ms debounce, undo stack, lock/complete state. Wraps content in MultiViewLayout. Draggable right-panel resizer. Auto-enters edit mode for in-progress reconstructions. |
| `MultiViewLayout.jsx` | Four-panel layout: column of view-selector buttons (3D + sagittal / axial / coronal) on left, main view area on right. Manages shared `slicePositions` for cross-view locator lines. |
| `Viewer3D.jsx` | Three.js canvas. Renders brain mesh, CT artifact mesh, electrode shafts / contacts / lines. OrbitControls (rotate / pan / zoom). Renders structure meshes when loaded. |
| `CTArtifactMesh.jsx` | Renders CT threshold mesh as white semi-transparent surface. Handles click-to-place contacts (only when `activeContactNumber != null`). |
| `SliceViewer.jsx` | MRI slice viewer for sagittal / axial / coronal planes. Client-side cache + prefetch (10 ahead, 4 behind, 6 concurrent requests). Scroll wheel + scrollbar navigation. Depth-filtered electrode dot projection (±4mm). LocatorOverlay corner thumbnail. |
| `ElectrodeEditor.jsx` | Right panel in edit mode. CT threshold slider, MRI toggle/opacity, shaft list (draggable divider between shaft list and contact grid), contact selector grid, autofill bar. Contains ColorPicker (50 named colors) and ContactSelector sub-components. |

---

## Data Flow

### New Reconstruction Upload
```
User uploads MRI + CT
  → Backend saves files to data/recon_<hash>/
  → Background task 1: skull strip MRI → marching cubes → mesh.json
  → Background task 2: SimpleITK registration → ct_to_mri.npy
  → Background task 3: CT preprocessing (HU masking) → ct_masked.nii.gz
  → status set to "ready"
```

### Electrode Placement
```
User clicks CT mesh in 3D viewer
  → CTArtifactMesh captures Three.js world position
  → POST /snap-to-blob: world pos → CT voxel → nearest bright blob centroid → world pos
  → POST /contacts: saves world pos as x_mm/y_mm/z_mm
  → Contact appears in 3D viewer and MRI slice projections
```

### MRI Slice Rendering
```
SliceViewer requests slice image
  → GET /mri-slice?axis=axial&slice_idx=90
  → Backend: loads NIfTI volume (cached in memory), applies percentile windowing, returns PNG
  → Frontend: caches PNG, prefetches adjacent slices
  → Electrode dots projected onto slice (contacts within ±4mm of plane)
```

### Brain Structure Loading
```
User clicks "Load" button
  → GET /structures
  → Backend: runs antspynet segmentation (or borrows from cached reconstruction)
  → Returns structure meshes as vertices/faces in world RAS space
  → Viewer3D renders each structure as a semi-transparent mesh
```

---

## Coordinate System

- Brain mesh centered at origin. The offset (`meshData.center`) is stored in `mesh.json`.
- Contacts stored in mesh-centered world space (mm): `x_mm`, `y_mm`, `z_mm`.
- For MRI slice projection: add `meshData.center` to contact coords → RAS world coords.
- CT registration: `ct_to_mri.npy` is a 4×4 affine (RAS space, nibabel convention).
- ANTs/ITK work in LPS internally — coordinate conversion happens at the boundary in `registration.py` and `structure_extractor.py`.

---

## Database Schema

```
users:              id, username, hashed_password, role, created_at

reconstructions:    id, patient_id, label, share_token, created_by,
                    created_at, updated_at, mesh_path, mri_path, ct_path,
                    transform_path, status, is_complete, is_locked

electrode_shafts:   id, reconstruction_id, name, label,
                    electrode_type, color, visible,
                    n_total_contacts, spacing_mm, grid_rows, grid_cols,
                    contact_diameter_mm, contact_length_mm, shaft_diameter_mm

electrode_contacts: id, shaft_id, contact_number, x, y, z,
                    x_mm, y_mm, z_mm, is_manual
```

> Contacts with `x_mm = NULL` are unplaced placeholders. Always filter `c.x_mm != null` before using coordinates.

---

## User Roles

| Role | View | Create / Edit | Manage Users |
|---|---|---|---|
| viewer | ✓ | ✗ | ✗ |
| editor | ✓ | ✓ | ✗ |
| admin | ✓ | ✓ | ✓ |

Register users via the API: `POST /api/auth/register`

---

## Electrode Types

| Type | Autofill Method | Min Manual Points |
|---|---|---|
| Depth | 3D cubic spline, parameterized by contact number | 2 |
| Strip | Same algorithm, larger spacing | 2 |
| Grid | Bilinear interpolation from corner contacts | 3 |

---

## 3D Viewer Controls

| Input | Action |
|---|---|
| Left drag | Rotate |
| Right drag | Pan |
| Scroll wheel | Zoom |

---

## HIPAA / Data Security

- Upload only **de-identified** NIfTI files. The NIfTI format does not carry DICOM PHI fields, but confirm de-identification at the DICOM→NIfTI conversion step.
- All uploaded files and the SQLite database live in `backend/data/` — restrict access to this directory.
- For cloud deployment: contact JH Research Computing for available HIPAA-compliant infrastructure (AWS/Azure enterprise agreements available through JH).

---

## Known Limitations

- `numpy < 2.0` required in the conda environment
- antspynet / tensorflow cannot be bundled in the PyInstaller exe — structures are borrowed from cached reconstructions in demo mode
- Registration in the exe may differ slightly from dev due to bundled DLL numerical differences (known PyInstaller limitation)
- SQLite is sufficient for single-lab use; migrate to Postgres before multi-site deployment

---

## Roadmap

- [ ] CSV/Excel export of electrode coordinates (shaft, contact, x/y/z mm)
- [ ] Share link read-only viewer (token exists in DB, UI not fully wired)
- [ ] Cloud deployment (JH Research Computing / AWS)
- [ ] Postgres migration for multi-user cloud deployment
- [ ] Contact-to-atlas labeling (report which structure each contact falls within)
- [ ] FreeSurfer surface import (upload lh.pial/rh.pial instead of marching cubes)
