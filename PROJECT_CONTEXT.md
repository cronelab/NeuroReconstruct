# NeuroReconstruct — Project Context

> **How to use this file:** Paste or upload this at the start of each new Claude session. Update the "Current Status" and "Next Steps" sections as work progresses. The rest is stable reference.

---

## Project Goal

A clinical web application for Johns Hopkins neurosurgeons to create, visualize, and review **sEEG electrode reconstructions** overlaid on 3D brain models. Surgeons upload a patient's T1 MRI (and optionally a post-implant CT), place electrode contacts on the CT mesh, and review the result for surgical planning.

**Core principle: accuracy over features.** Anything that could mislead (e.g. template-space anatomy displayed in patient space) should be hidden rather than shown with imperfect data.

---

## Running the App

```bash
# Backend — from /backend
uvicorn main:app --reload

# Frontend — from /frontend
npm start
```

Default credentials: `admin` / `changeme`  
Data dir: `backend/data/recon_<hash>/` per reconstruction  
Database: `backend/brain_viewer.db` (SQLite, created automatically)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React, Three.js r128, Zustand, axios |
| Backend | Python 3.11, FastAPI, SQLite (SQLAlchemy async) |
| Neuroimaging | nibabel, scikit-image (marching cubes), trimesh, PIL |
| Auth | JWT tokens, axios interceptors |
| Dev env | Windows, conda env `neuro-recon` |

### Critical: numpy must stay < 2.0

nibabel, scikit-learn, pandas, and scipy all break with numpy 2.x in this conda environment.  
After any `pip install`, verify with: `python -c "import nibabel, sklearn"`  
If broken: `pip install "numpy<2.0"`

---

## File Structure

### Backend (`backend/`)

| File | Purpose |
|---|---|
| `main.py` | All FastAPI endpoints. Auth, reconstruction CRUD, mesh serving, MRI slice rendering (with in-memory cache), electrode management, CT mesh generation, snap-to-blob. |
| `database.py` | SQLAlchemy models: `User`, `Reconstruction`, `ElectrodeShaft`, `ElectrodeContact`. |
| `auth.py` | JWT creation/verification, password hashing, `get_current_user` dependency. |
| `services/mesh_extractor.py` | Extracts brain surface mesh from T1 MRI NIfTI via marching cubes. Returns vertices/faces in world RAS coords, centered at origin. Runs in background on upload. |
| `services/ct_electrode_extractor.py` | CT mesh generation (HU threshold + marching cubes) and `snap_to_blob_centroid()` — snaps a clicked world position to nearest bright CT blob centroid within 8mm. |
| `services/electrode_service.py` | Autofill: cubic spline fit parameterized by contact number (not arc length). Interpolates between placed contacts, linear extrapolation beyond the manual range. Blob-snap applied to interpolated contacts only. |
| `services/structure_extractor.py` | **PARKED. Do not wire to UI.** Harvard-Oxford atlas mesh extraction via nilearn. Produces MNI152-space structures — not patient-specific, misleading for surgical use without registration. |
| `migrate_shaft_fields.py` | One-time migration: adds shaft metadata columns to existing DB. |
| `migrate_lock_fields.py` | One-time migration: adds `is_complete` and `is_locked` columns. |

### Frontend (`frontend/src/`)

| File | Purpose |
|---|---|
| `App.jsx` | Root. Manages page state (list/viewer/login), session restore, URL routing for share links. |
| `store.js` | Zustand global state: user, token, reconstruction, meshData, isEditorMode, shaftVisibility, selectedShaftId, activeContactNumber, brainOpacity. Also holds `structuresData`/`structureVisible` (unused, parked). |
| `api.js` | All axios API calls. Auth token injected via interceptor. |
| `index.css` | Global dark theme styles. Defines `@keyframes spin`, `pulse`, `fadeIn`. |
| `components/LoginPage.jsx` | Login form. |
| `components/ReconstructionList.jsx` | Home page. Two-column layout: In Progress (left 320px) and Completed (right flex). Shows shaft/contact counts. Upload form. Polls every 10s. |
| `components/Header.jsx` | Top bar. Clickable logo → home. Mark Complete toggle. Edit button (disabled when locked). |
| `components/ReconstructionViewer.jsx` | Main viewer shell. Owns: CT mesh loading, MRI visibility, threshold debounce (400ms), undo stack, lock/complete state. Wraps content in MultiViewLayout. Draggable right-panel resizer. Auto-enters edit mode for in-progress reconstructions. |
| `components/MultiViewLayout.jsx` | Four-panel layout: left column of view selector buttons (3D + sagittal/axial/coronal), main view area. Manages shared slicePositions for cross-view locators. |
| `components/Viewer3D.jsx` | Three.js canvas. Renders brain mesh, CT artifact mesh, electrode shafts/contacts/lines. OrbitControls. StructureMesh component exists but is never called (parked). |
| `components/CTArtifactMesh.jsx` | Renders CT threshold mesh as white semi-transparent surface. Handles click-to-place contacts (only when activeContactNumber != null). |
| `components/SliceViewer.jsx` | MRI slice viewer. Client-side cache + prefetch (10 ahead, 4 behind, 6 concurrent requests). Scroll wheel + vertical scrollbar. Depth-filtered electrode dot projection (±4mm). LocatorOverlay corner thumbnail. |
| `components/ElectrodeEditor.jsx` | Right panel in edit mode. CT threshold slider, MRI toggle/opacity, shaft list (draggable divider), contact selector grid, autofill bar. Contains ColorPicker (50 named colors) and ContactSelector sub-components. |
| `components/LayerPanel.jsx` | **Dead code.** Safe to delete. |
| `components/CTSlicePlanes.jsx` | **Dead code.** Safe to delete. |

---

## Database Schema

```
users:              id, username, hashed_password, role (viewer/editor/admin), created_at

reconstructions:    id, patient_id, label, share_token, created_by,
                    created_at, updated_at, mesh_path, mri_path, ct_path,
                    status, is_complete, is_locked

electrode_shafts:   id, reconstruction_id, name, label,
                    electrode_type (depth/strip/grid), color, visible,
                    n_total_contacts, spacing_mm, grid_rows, grid_cols,
                    contact_diameter_mm, contact_length_mm, shaft_diameter_mm

electrode_contacts: id, shaft_id, contact_number, x, y, z,
                    x_mm, y_mm, z_mm, is_manual
```

- Contacts with `x_mm = NULL` are unplaced placeholder slots. Always filter with `c.x_mm != null` before using coordinates.
- `x/y/z` = raw voxel coords; `x_mm/y_mm/z_mm` = world coords relative to mesh center (mm).

---

## Key Design Decisions

### Coordinate System
- Brain mesh is centered at origin (mesh center subtracted during extraction and stored in mesh JSON as `center`)
- Contacts stored in mesh-centered world space (mm)
- For MRI slice projection: add `meshData.center` to contact coords to get RAS world coords
- CT snapping pipeline: Three.js world → CT voxel → blob centroid → back to Three.js world

### Electrode Placement Workflow
1. Create shaft → N placeholder contacts auto-generated via `POST /api/shafts/{id}/init-contacts`
2. User selects a contact number in the grid → `activeContactNumber` set in store
3. Click CT mesh → position snapped to nearest blob centroid (8mm search radius) → saved
4. After 2+ contacts placed: autofill available (spline/linear fit, blob-snap on interpolated only)

### Lock / Complete States
- `is_complete=false` → "In Progress" → auto-enters edit mode on open, CT visible, MRI hidden
- `is_complete=true` → "Completed" → locked on open, MRI visible, CT hidden by default
- Mark Complete: sets both flags true, switches to MRI view
- Unlock (= Mark In-Progress): sets `is_complete=false`, auto-enters edit mode, hides MRI

### MRI Slice Performance
- Backend: NIfTI loaded once per path into `_mri_volume_cache` (volume array + per-axis percentile stats + PNG cache)
- `POST /api/reconstructions/{id}/prerender-slices` warms PNG cache in background on viewer open
- Frontend: client-side Map cache, prefetch 10 ahead / 4 behind, max 6 concurrent requests

### Brain Structures — Deliberately Parked
The Harvard-Oxford atlas produces MNI152-space meshes. Displaying these in patient space without registration is dangerous for surgical planning. The backend code (`structure_extractor.py`) and frontend dead code (`structuresData` in store, `StructureMesh` in Viewer3D) are left in place but not wired to any UI. Revisit only when ANTsPy or SynthSeg registration pipeline is implemented.

---

## API Endpoints (Key)

```
POST   /api/auth/login
GET    /api/auth/me

GET    /api/reconstructions                         list with shaft/contact counts
POST   /api/reconstructions                         upload MRI+CT, triggers mesh extraction
GET    /api/reconstructions/{id}                    single with all shafts
PATCH  /api/reconstructions/{id}/status             set is_complete, is_locked
GET    /api/reconstructions/{id}/mesh               brain surface mesh JSON
GET    /api/reconstructions/{id}/ct-mesh            CT threshold mesh JSON
GET    /api/reconstructions/{id}/mri-slice          ?axis=axial&slice_idx=90
POST   /api/reconstructions/{id}/prerender-slices   warm slice cache
GET    /api/reconstructions/{id}/structures         PARKED - MNI atlas meshes

POST   /api/shafts                                  create shaft
PATCH  /api/shafts/{id}                             update shaft fields
DELETE /api/reconstructions/shafts/{id}             delete shaft + contacts
POST   /api/shafts/{id}/init-contacts               create N placeholder contacts
POST   /api/shafts/{id}/contacts                    add/update single contact
DELETE /api/shafts/{id}/contacts/{num}              delete single contact
POST   /api/shafts/{id}/autofill                    run spline fit
POST   /api/reconstructions/{id}/snap-to-blob       snap world pos to CT blob
```

---

## Current Status

*(Update this section each session)*

**Last worked on:** Attempted to add brain substructures visualization using nilearn Harvard-Oxford atlas. Abandoned because atlas is in MNI152 space and not patient-specific — would be misleading for surgical planning. Removed the UI, left backend code parked.

**Compile issue:** `ElectrodeEditor.jsx` had a JSX syntax error introduced during structures removal (regex ate a closing `}` from a comment). Multiple repair attempts were made. If the file still fails to compile on your machine, the error is around the `{/* ── SHAFT HEADER ── */}` comment — verify it has the closing `}`.

**Everything else working:**
- 3D brain + CT + electrode visualization
- MRI slice viewer (sagittal/axial/coronal) with smooth scrolling, electrode projection, cross-view locators
- Electrode placement workflow (click CT → snap to blob → place contact)
- Autofill (spline fit)
- Lock/complete workflow
- Role-based auth

---

## Next Steps

1. **Confirm ElectrodeEditor.jsx compiles** — check browser console after npm start
2. **CSV/Excel export of electrode coordinates** — shaft name, contact number, x/y/z mm. High clinical value for sharing with analysis tools (MNI coords would require registration first).
3. **Test with real multi-patient data** — multiple shafts, verify autofill and slice projections
4. **Share link review mode** — read-only viewer for completed reconstructions without login (token generated, endpoint exists, UI not fully wired)
5. **ANTsPy/SynthSeg registration** — prerequisite for brain substructures and contact-to-atlas labeling
6. **FreeSurfer surface import** — upload lh.pial/rh.pial as brain surface instead of marching cubes
7. **AWS deployment** — behind JHU VPN IP allowlist, HTTPS, proper secret management

---

## Known Gotchas

- `numpy < 2.0` required — see Critical note above
- `LayerPanel.jsx` and `CTSlicePlanes.jsx` are dead code
- `structure_extractor.py` is parked dead code — do not wire to UI
- Always filter `c.x_mm != null` before using contact coordinates (placeholders have null coords)
- MRI slice cache is in-memory, resets on backend restart (first scroll after restart is slower — normal)
- The `structures` endpoint at `/api/reconstructions/{id}/structures` exists in `main.py` but should not be called until patient-specific segmentation is available
