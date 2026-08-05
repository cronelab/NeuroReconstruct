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

Run `build_demo.bat` from the project root. It builds the React frontend, bundles the
backend with PyInstaller, and assembles a ready-to-ship `dist_demo/` folder:

```bash
build_demo.bat
```

The script must run with the `neuro-recon` conda environment available — it activates the
env and then verifies `pyinstaller` is on PATH, exiting immediately with instructions if
not. If `conda` has never been initialized for your shell, run it from an Anaconda Prompt
or run `conda init cmd.exe` once.

To build only the executable, without assembling the demo folder:

```bash
cd backend
conda activate neuro-recon
pyinstaller neuro_recon.spec --noconfirm
```

Output: `backend/dist/NeuroReconstruct.exe` (~218 MB). The spec resolves its bundled DLLs
from `sys.prefix`, so it picks up whichever conda env is active.

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
├── NeuroReconstruct.exe   # Self-contained server + frontend (~218 MB)
├── brain_viewer.db        # Pre-populated database
└── data/                  # Reconstruction folders
```

Double-click `NeuroReconstruct.exe`. The app opens automatically in your browser at
`http://127.0.0.1:8000`. No Python or Node.js installation required.

`brain_viewer.db` and `data/` must stay next to the .exe — the app writes alongside the
executable, not into its temporary extraction directory.

If port 8000 is already in use — a dev server, another copy of the demo — the app probes
upward and starts on the first free port (8001, 8002, …), printing the port it chose and
opening the browser there. No configuration needed.

To pin a specific port instead, set `NEURO_PORT`. This is honoured verbatim: if that port
is taken the app reports the conflict rather than moving to another one.

```bash
set NEURO_PORT=8010
NeuroReconstruct.exe
```

Each CT HU threshold the user tries is cached under `<recon>/ct_cache/` as a
`ct_threshold_*.json` mesh. That cache is bounded by a least-recently-used policy
so it can't grow without limit — by default the 16 most-recently-used meshes are
kept, capped at 512 MB total. Override with `NEURO_CT_CACHE_MAX_FILES` and
`NEURO_CT_CACHE_MAX_MB` for large multi-patient deployments.

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
| `launcher.py` | PyInstaller entry point. Sets `ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS=1` before any imports to ensure deterministic registration, and `NEURO_DATA_DIR` before importing `database.py` so the DB lands next to the .exe. Serves on `NEURO_PORT` if set, otherwise the first free port from 8000 upward. |
| `neuro_recon.spec` | PyInstaller spec for the standalone exe. Resolves bundled DLLs from `sys.prefix`, and excludes antspynet/TensorFlow (see Known Limitations). |

#### Services (`backend/services/`)

| File | Purpose |
|---|---|
| `mesh_extractor.py` | Extracts brain surface mesh from T1 MRI NIfTI. Uses **antspynet** for deep-learning skull stripping when available; falls back to morphological thresholding (erosion r=8mm, Gaussian σ=0.5, parenchyma refinement). Mesh produced via marching cubes, returned as vertices/faces in world RAS coords centered at origin. |
| `registration.py` | CT-to-MRI rigid registration via **SimpleITK**. Mattes mutual information optimizer, REGULAR (deterministic) sampling, 4-resolution pyramid, GEOMETRY centroid initialization. Saves result as `ct_to_mri.npy` (4×4 affine matrix). Single-threaded for reproducibility. |
| `structure_extractor.py` | Patient-specific brain structure segmentation via **antspynet**. Segments hippocampus, amygdala, thalamus, putamen, caudate, pallidum, and cortical parcellation. Results cached as `structures.json` + `structures_cortical.nii.gz`. Falls back to borrowing cached structures from any other reconstruction if antspynet is unavailable. |
| `ct_electrode_extractor.py` | CT mesh generation (HU threshold + marching cubes). `snap_to_blob_centroid()` snaps a clicked world position to the nearest bright CT blob centroid within 8mm. |
| `electrode_service.py` | Autofill: cubic spline fit parameterized by contact number. Interpolates between placed contacts, linear extrapolation beyond the manual range. Blob-snap applied to interpolated contacts only. |
| `mni_registration.py` | Results-export pipeline. Registers the patient MRI to MNI152 via **ANTs** affine + SyN (`SyNRA`), warps the CT into MNI using the existing `ct_to_mri.npy`, and maps electrode contacts (RAS points, LPS-flipped for ANTs) into MNI space. Writes `MRI_mni.nii.gz`, `CT_mni.nii.gz`, transforms, `electrodes_mni.csv`/`.json`, and `export_manifest.json` to `<recon>/export/`. CPU-only, deterministic. |
| `contact_labeling.py` | Labels each contact with the patient-specific DKT structure it sits in — or, for white-matter contacts, a plurality vote among structure voxels within a 2mm radius (reports structure, group, `distance_mm`, `vote_share`, `voxel_content`), leaving genuinely off-structure contacts unassigned. Native MRI space (no atlas warp); uses the cached `structures_cortical.nii.gz`. Writes `electrodes_structures.csv`. |

### Results export

Completing a reconstruction can trigger a **results export** (`_export_mni_background`
in `main.py`, tracked by `Reconstruction.export_status`). It runs the MNI
normalization (`mni_registration.py`) and contact-to-structure labeling
(`contact_labeling.py`), producing a `<recon>/export/` folder that the
`/export/download` endpoint zips for the user:

| Artifact | Contents |
|---|---|
| `electrodes_mni.csv` / `.json` | Per-contact MNI152 coordinates (JSON also keeps native mm for traceability). |
| `electrodes_structures.csv` | Per-contact anatomical structure (or nearest, with distance and vote share). |
| `MRI_mni.nii.gz`, `CT_mni.nii.gz` | Patient MRI and CT warped into MNI152 space. |
| `mri_to_mni_*`, `mni_to_mri_invwarp.nii.gz` | Forward/inverse ANTs transforms, retained for re-runs. |
| `export_manifest.json` | Template, ANTs version, transform type, contact counts, phase timings. |

> MNI registration is CPU-heavy (SyN is multi-minute) but uses plain `ants`,
> which **is** bundled in the standalone exe — so MNI coordinate export runs
> there. Only the contact-structure labeling step depends on a DKT segmentation:
> if structures were never computed for a case, that step needs antspynet (absent
> in the exe) and is skipped, though `electrodes_mni.csv` is still produced.

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
- Structure segmentation runs on **CPU only** (GPU was benchmarked and gave no speedup — the pipeline is bottlenecked by CPU-bound ANTs preprocessing + mesh extraction, not GPU-able inference). First computation for a case takes a couple of minutes; results are cached to disk afterward. The per-structure mesh extraction is parallelized across CPU cores.
- antspynet / tensorflow are deliberately **excluded** from the PyInstaller exe — they accounted for roughly 310 MB of a 529 MB bundle and the frozen build never reaches them. The exe therefore cannot compute new skull strips or structure segmentations: it borrows a donor mesh and cached structures from the reconstructions shipped alongside it. Every antspynet import in `services/` is function-local and guarded, so the missing module degrades to these fallbacks rather than raising. CT-to-MRI coregistration is SimpleITK-only and runs fresh in the exe as normal.
- Registration in the exe may differ slightly from dev due to bundled DLL numerical differences (known PyInstaller limitation)
- SQLite is sufficient for single-lab use; migrate to Postgres before multi-site deployment

---

## Roadmap

- [x] CSV export of electrode coordinates — shipped via the results export: `electrodes_mni.csv` (MNI coords) and `electrodes_structures.csv` (shaft, contact, structure). Backed by MNI normalization in `services/mni_registration.py`.
- [x] Contact-to-atlas labeling — shipped: `electrodes_structures.csv` reports the DKT structure each contact sits in (or the nearest one, with distance and vote share). See `services/contact_labeling.py`.
- [ ] Share link read-only viewer (token exists in DB, UI not fully wired)
- [ ] Cloud deployment (JH Research Computing / AWS)
- [ ] Postgres migration for multi-user cloud deployment
- [ ] FreeSurfer surface import (upload lh.pial/rh.pial instead of marching cubes)
