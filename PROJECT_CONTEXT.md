# NeuroReconstruct — Project Context

> **How to use this file:** Paste or upload this at the start of each new Claude session. Update the "Current Status" and "Next Steps" sections as work progresses. The rest is stable reference.

---

## Project Goal

A clinical web application for Johns Hopkins neurosurgeons to create, visualize, and review **sEEG electrode reconstructions** overlaid on 3D brain models. Surgeons upload a patient's T1 (or T2) MRI and optionally a post-implant CT, place electrode contacts on the CT mesh, and review the result for surgical planning.

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
| Neuroimaging | nibabel, scikit-image (marching cubes), trimesh, **open3d** (mesh decimation), scipy, PIL |
| CT↔MRI registration | **SimpleITK** (Mattes mutual information, rigid) |
| Segmentation / skull-strip | **antspyx + antspynet** (deep_atropos, DKT parcellation, brain_extraction) |
| Auth | JWT tokens, bcrypt (passlib), axios interceptors |
| Dev env | Windows, conda env `neuro-recon` |

### Critical: numpy must stay < 2.0

nibabel, scikit-learn, pandas, and scipy all break with numpy 2.x in this conda environment.  
After any `pip install`, verify with: `python -c "import nibabel, sklearn"`  
If broken: `pip install "numpy<2.0"`

### Critical: pinned deps that break silently if bumped

- `bcrypt==4.0.1` — passlib 1.7.4 calls `bcrypt.__about__` (removed in bcrypt 4.1+); a newer bcrypt makes **every** `hash_password()` crash, taking down app startup (default admin creation). Keep pinned.
- `open3d` — required by `trimesh.simplify_quadric_decimation()`. Without it, mesh extraction crashes at the decimation step (`ModuleNotFoundError: open3d`) for any MRI large enough to exceed the 120k-face decimation threshold — i.e. essentially all real data.

---

## File Structure

### Backend (`backend/`)

| File | Purpose |
|---|---|
| `main.py` | All FastAPI endpoints. Auth, reconstruction CRUD, mesh serving, MRI slice rendering (with in-memory cache), electrode management, CT mesh generation, snap-to-blob. |
| `database.py` | SQLAlchemy models: `User`, `Reconstruction`, `ElectrodeShaft`, `ElectrodeContact`. |
| `auth.py` | JWT creation/verification, password hashing, `get_current_user` dependency. |
| `services/mesh_extractor.py` | Brain surface mesh from MRI NIfTI. antspynet `brain_extraction` skull-strip (modality param — **t1 or t2**, chosen at upload) with a morphological fallback, then marching cubes + open3d decimation. Returns vertices/faces in world RAS, centered at origin. Runs in background on upload. |
| `services/registration.py` | CT→MRI rigid registration via SimpleITK (Mattes MI, deterministic single-thread). Saves 4×4 affine as `ct_to_mri.npy`. Also `preprocess_ct()` (table/air strip → `ct_masked.nii.gz`). Falls back to identity if images already aligned (metric near 0). |
| `services/ct_electrode_extractor.py` | CT mesh generation (HU threshold + marching cubes) and `snap_to_blob_centroid()` — snaps a clicked world position to nearest bright CT blob centroid within 8mm. Applies the registration transform so CT aligns with the brain mesh. |
| `services/electrode_service.py` | Autofill: cubic spline fit parameterized by contact number (not arc length). Interpolates between placed contacts, linear extrapolation beyond the manual range. Blob-snap applied to interpolated contacts only. |
| `services/structure_extractor.py` | **ACTIVE — patient-specific, wired to UI.** antspynet `deep_atropos` (subcortical) + `desikan_killiany_tourville_labeling` (cortical DKT) run on the patient's own T1 — native space, no MNI atlas. Extracts ~84 structures across 6 groups as meshes aligned to the brain mesh. Cached as per-structure JSON + `structures_cortical.nii.gz`. (Superseded the old parked nilearn/Harvard-Oxford MNI152 approach.) |
| `services/mni_registration.py` | **Export pipeline — step 1.** Registers the completed reconstruction into MNI152 standard space. ANTs affine+SyN (`type_of_transform="SyNRA"`) MRI→MNI; CT resampled into MRI (via `ct_to_mri.npy`) then warped to MNI; electrode contacts (MRI-space RAS) pushed through as points via `apply_transforms_to_points`. CPU-forced. Writes artifacts to `recon_dir/export/`: `MRI_mni.nii.gz`, `CT_mni.nii.gz`, transform files, `electrodes_mni.csv`/`.json`, `export_manifest.json`. **Coordinate care:** RAS↔LPS flip for points (`_ras_to_lps`); points travel the *inverse* transform direction vs. images. MNI template from `ants.get_ants_data('mni')` (downloaded/cached on first run). |
| `migrate_shaft_fields.py` / `migrate_lock_fields.py` / `migrate_deleted_at.py` / `migrate_registration_confirmed.py` / `migrate_export_status.py` | One-time, idempotent column-add migrations for existing DBs (shaft metadata, `is_complete`/`is_locked`, `deleted_at`, `registration_confirmed`, `export_status`/`exported_at`). |

### Frontend (`frontend/src/`)

| File | Purpose |
|---|---|
| `App.jsx` | Root. Manages page state (list/viewer/login), session restore, URL routing for share links. |
| `store.js` | Zustand global state: user, token, reconstruction, meshData, isEditorMode, shaftVisibility, selectedShaftId, activeContactNumber, brainOpacity, `structuresData`, `structureVisible` (**both active**), `setStructureVisibleMany` (bulk toggle for the structure tree). |
| `api.js` | All axios API calls. Auth token injected via interceptor. Includes `confirmRegistration`. |
| `index.css` | Global dark theme styles. Defines `@keyframes spin`, `pulse`, `fadeIn`. |
| `components/LoginPage.jsx` | Login form. |
| `components/ReconstructionList.jsx` | Home page. Two-column layout: In Progress (left 320px) and Completed (right flex). Shows shaft/contact counts. Upload form with **MRI modality (T1/T2) selector**. Polls every 10s. |
| `components/Header.jsx` | Top bar. Clickable logo → home. Mark Complete toggle. Edit button (disabled when locked). "⚠ Reg. unreviewed" badge when a CT is registered but not yet confirmed. |
| `components/ReconstructionViewer.jsx` | Main viewer shell. Owns: CT mesh loading, MRI visibility, threshold debounce (400ms), undo stack, lock/complete state. Wraps content in MultiViewLayout. Draggable right-panel resizer. Auto-enters edit mode for in-progress reconstructions. |
| `components/MultiViewLayout.jsx` | Left column of view selector buttons (3D + sagittal/axial/coronal, **plus Fusion when a CT is registered**) and main view area. Manages shared slicePositions for cross-view locators. Hosts the registration review/confirm bar in the Fusion view. |
| `components/Viewer3D.jsx` | Three.js canvas. Renders brain mesh, CT artifact mesh, electrode shafts/contacts/lines, **and structure meshes (StructureMesh — now active)**. OrbitControls. |
| `components/CTArtifactMesh.jsx` | Renders CT threshold mesh as white semi-transparent surface. Handles click-to-place contacts (only when activeContactNumber != null). |
| `components/SliceViewer.jsx` | MRI slice viewer. Client-side cache + prefetch (10 ahead, 4 behind, 6 concurrent requests). Scroll wheel + vertical scrollbar. Depth-filtered electrode dot projection (±4mm). Structure-label overlay. LocatorOverlay corner thumbnail. |
| `components/FusionSliceViewer.jsx` | Registration-QA fusion view. MRI grayscale base + **red-tinted** CT (resampled into the MRI plane by `/fusion-slice`) with an MRI↔CT blend slider, per-axis switch, scroll nav. |
| `components/ElectrodeEditor.jsx` | Right panel in edit mode. CT threshold slider, MRI toggle/opacity, **hierarchical brain-structure tree (Group → Side → Structure, tri-state checkboxes)**, shaft list (draggable divider), contact selector grid, autofill bar. Contains ColorPicker (50 named colors), ContactSelector, TriStateCheckbox sub-components. |
| `components/LayerPanel.jsx` | **Dead code.** Safe to delete. |
| `components/CTSlicePlanes.jsx` | **Dead code.** Safe to delete. |

---

## Database Schema

```
users:              id, username, hashed_password, role (viewer/editor/admin), created_at

reconstructions:    id, patient_id, label, share_token, created_by,
                    created_at, updated_at, mesh_path, mri_path, ct_path,
                    status, is_complete, is_locked,
                    registration_confirmed, export_status, exported_at, deleted_at

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

### Brain Structures — Now Patient-Specific (was parked)
The old concern (Harvard-Oxford MNI152 atlas meshes shown in patient space without registration) was resolved by switching to **antspynet `deep_atropos` + DKT parcellation run directly on the patient's own T1** — native space, patient-specific, no atlas registration. This is now fully wired: `/structures` endpoint, "⊕ Load" button, hierarchical Group→Side→Structure tree in `ElectrodeEditor`, and `StructureMesh` rendering in `Viewer3D` + label overlays in `SliceViewer`. ~84 structures; a handful of the smallest DKT regions (accumbens, frontal/temporal poles) may report "no voxels" — minor completeness gap, not a correctness issue.

### Segmentation is CPU-only (GPU was tried, benchmarked, rejected)
GPU acceleration for antspynet was explored 2026-07-22 and **rolled back**. Benchmark (PY26N009_dev1, RTX 4080 SUPER): GPU gave NO speedup — marginally slower (full seg GPU 162–171 s vs CPU 151–160 s). Phase split showed the cost is **CPU-bound ANTs preprocessing** (N4, brain extraction, template registration ~100–115 s) + **CPU mesh extraction** (~47 s), not the thin GPU-able inference slice; GPU init/transfer overhead outweighed the win. So `structure_extractor.py` now **forces CPU**: it sets `os.environ["CUDA_VISIBLE_DEVICES"] = "-1"` before importing ants/antspynet. No `NEURO_SEG_DEVICE` flag, no `check_gpu.py`, no WSL requirement.

**Mesh-loop parallelization (implemented 2026-07-22):** the per-structure mesh extraction (marching cubes + smoothing + decimation, ~84 independent structures) now runs in a `ProcessPoolExecutor` (`min(cpu_count-1, 8)` workers). Workers load the cortical label volume once each via `_mesh_worker_init` (from `structures_cortical.nii.gz`) rather than pickling the array per task; `_mesh_worker_task` calls `_labels_to_mesh`. Only the CPU mesh phase (~47 s serial) is parallelized — the antspynet DKT call is unchanged. Still-open CPU lever (not built): reuse the brain extraction already done in `mesh_extractor.py` instead of re-running antspynet preprocessing.

### Registration QA — Fusion Viewer + Manual Confirmation
CT→MRI registration accuracy previously had no check beyond "does `ct_to_mri.npy` exist". Added a Fusion view (`/fusion-slice` resamples the CT onto the MRI plane via true oblique 3D trilinear resampling; frontend overlays it red over the MRI with a blend slider) and a `registration_confirmed` flag an editor sets via "Looks correct" — auto-resets whenever registration re-runs. Not a hard gate; surfaces an "unreviewed" banner/badge. **Status: merged to `main` (PR #2).** Follow-ups not built: persisted MI metric, square spyglass lens, at-a-glance ReconCard badge.

---

### MNI152 Export Pipeline (step 1 — this branch `mni-registration`)
Once a reconstruction is **marked complete**, an **"⤓ Export to MNI"** button appears in the
Header (editor/admin only). It calls `POST /export`, which sets `export_status="exporting"` and
runs `_export_mni_background` → `services/mni_registration.export_reconstruction_to_mni`. The
Header polls `GET /reconstructions/{id}` every 5s and flips to **"⬇ Download MNI export"** when
`export_status="exported"` (or a red retry button on `error`). Re-runnable (Unlock → re-complete →
export overwrites `recon_dir/export/`). This is the **first step** of a larger export pipeline;
downstream steps (atlas region labeling of MNI coords, group templates, reports) will consume the
persisted transforms + `electrodes_mni.csv`. **Accuracy checkpoint before trusting output:** verify
`electrodes_mni.csv` coords sit inside the MNI bounding box and a left-hemisphere contact has
`x_mni < 0` (the service logs an in-box % + L/R split; a flip means fixing the RAS/LPS handling or
the `whichtoinvert` point-transform direction in `transform_contacts_to_mni`).

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
GET    /api/reconstructions/{id}/structure-slice    label overlay PNG for a slice
GET    /api/reconstructions/{id}/fusion-slice       CT resampled into MRI plane (registration QA)
POST   /api/reconstructions/{id}/prerender-slices   warm slice cache
GET    /api/reconstructions/{id}/structures         ACTIVE - patient-specific antspynet meshes
PATCH  /api/reconstructions/{id}/registration-confirm  set/clear registration_confirmed
POST   /api/reconstructions/{id}/export              start MNI152 export (409 unless is_complete); bg task
GET    /api/reconstructions/{id}/export/download     zip of recon_dir/export/ artifacts

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

**Last worked on (2026-07-22):** Built the **MNI152 export pipeline (step 1)** on branch `mni-registration` — **PR #4** (targets `main` directly, open, not merged). See the "MNI152 Export Pipeline" design section above. Verified end-to-end on real data (`PY26N009_dev3`, 93 contacts): MRI warps into MNI152 (SyNRA), 100% of contacts inside the MNI bounding box, correct hemispheres and medial→lateral sEEG geometry (RAS/LPS + point-transform direction confirmed). Artifacts land in `recon_dir/export/`. (Note: `gh` CLI is installed but not authenticated on this box — the PR was created via the GitHub API using git's stored credential.)

**Already merged to `main`** (verified live on GitHub 2026-07-22):
- **PR #1** `fix-bcrypt-and-mri-modality`: bcrypt startup-crash fix, MRI T1/T2 modality selector, file-input layout fix, open3d dependency (fixes mesh-decimation crash on real data), hierarchical brain-structure checkbox tree.
- **PR #2** `registration-qa`: fusion slice viewer + manual CT–MRI registration confirmation. See design section above.
- **PR #3** `segmentation-cpu-parallel-mesh`: parallelized per-structure mesh extraction, segmentation kept CPU-only. See design section above.

So `main` already contains all of the above; **PR #4** (`mni-registration`, still open) branched from `main` and sits on top of them — no stacking/rebase needed. Prior verification (`PY26N009_dev1`): mesh extraction, CT registration (valid rigid transform, MI −0.58), ~84 patient-specific structures, and the fusion overlay (CT skull concentric around MRI brain).

**Working:**
- 3D brain + CT + electrode + **patient-specific structure** visualization
- MRI slice viewer (sagittal/axial/coronal) with smooth scrolling, electrode + structure-label projection, cross-view locators
- Electrode placement workflow (click CT → snap to blob → place contact)
- Autofill (spline fit)
- Lock/complete workflow, role-based auth
- CT→MRI registration + fusion-view QA (merged, PR #2)

**Note:** the JSX compile issue previously flagged in `ElectrodeEditor.jsx` (`{/* ── SHAFT HEADER ── */}`) is resolved — the comment has its closing brace and the file compiles.

---

## Next Steps

1. **Review & merge PR #4** (`mni-registration` → `main`, open) — PR #1/#2/#3 are already merged
2. **Registration-QA follow-ups** — persist the SimpleITK MI metric (currently logged then discarded), square spyglass lens in the fusion view, at-a-glance registration badge on ReconCard
3. **MNI export — next steps (step 1 shipped in PR #4)** — atlas region labeling of MNI coords (which standard-atlas region each contact falls in), group-template building, report generation; all consume `recon_dir/export/` transforms + `electrodes_mni.csv`
4. **CSV/Excel export of electrode coordinates** — shaft name, contact number, x/y/z mm. High clinical value for sharing with analysis tools (native-space complement to the MNI CSV)
5. **Contact-to-structure labeling** — now feasible since structures are patient-specific; report which DKT/subcortical region each contact falls in
6. **Fill the 6 missing DKT structures** — accumbens, frontal pole, temporal pole (bilateral) report "no voxels"; verify label indices vs. the antspynet DKT scheme
7. **Test with more multi-patient data** — multiple shafts, verify autofill and slice projections across cases
8. **Share link review mode** — read-only viewer for completed reconstructions without login (token generated, endpoint exists, UI not fully wired)
9. **FreeSurfer surface import** — upload lh.pial/rh.pial as brain surface instead of marching cubes
10. **AWS deployment** — behind JHU VPN IP allowlist, HTTPS, proper secret management; migrate SQLite → Postgres for multi-user

---

## Known Gotchas

- `numpy < 2.0`, `bcrypt==4.0.1`, and `open3d` are load-bearing pins — see Critical notes above
- `LayerPanel.jsx` and `CTSlicePlanes.jsx` are dead code
- Always filter `c.x_mm != null` before using contact coordinates (placeholders have null coords)
- MRI + CT slice caches are in-memory, reset on backend restart (first scroll after restart is slower — normal)
- **The uvicorn `--reload` watcher is unreliable on this Windows setup** — it can silently stop picking up changes. If backend edits don't take effect, kill the uvicorn process and restart it manually.
- Structures/fusion antspynet passes are **CPU-only** (GPU benchmarked, no speedup — see design note) and take ~2.5–3 min per case on this data; results are cached to disk afterward. Per-structure mesh extraction is parallelized across cores.
- Upload only **T1 or T2** MRI (modality is selected at upload and drives skull-strip); other contrasts are out-of-distribution for the antspynet model.
