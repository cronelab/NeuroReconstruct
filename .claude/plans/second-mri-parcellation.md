# Optional Second MRI for Parcellation — Implementation Plan

## Context / goal

Let a user upload a second T1 MRI (e.g. a cleaner pre-op scan) and drive the
cortical/subcortical parcellation from **it** instead of the main reconstruction
MRI, while keeping everything in the main MRI's coordinate frame. Motivation: a
dedicated pre-op T1 can yield a better antspynet DKT parcellation than the
reconstruction MRI.

**Confirmed design decisions (2026-08-05):**
- **Registration: affine-only** (no nonlinear warp).
- **Scope: parcellation only** — the brain mesh and all contact coordinates stay
  derived from the main MRI; only `structures_cortical.nii.gz` changes source.
- **Strictly opt-in parcellation** — the default parcellation source is always the
  **main MRI**; the 2nd MRI is used for parcellation **only** when the user
  explicitly selects it (source toggle / endpoint).
- **Registration precomputed in the background** — when a 2nd MRI is uploaded, its
  **affine MRI→main registration runs in the background, alongside the CT→MRI
  registration** (in `_extract_mesh_background`), persisting the transform so a
  later opt-in is fast (just DKT + label warp). This background registration does
  **not** run parcellation or change the parcellation source.

## Key insight — one integration point

Every structure consumer reads a single file: `structures_cortical.nii.gz` (the
DKT label volume, in the main MRI world-RAS frame):
- `extract_all_structures()` builds the per-structure meshes from it,
- `_render_structure_slice()` builds the 2D overlay from it,
- `contact_labeling.label_contacts()` labels contacts from it.

And `extract_all_structures()` **skips DKT entirely if that file already exists**
([structure_extractor.py:431](../../backend/services/structure_extractor.py)). So
the whole feature reduces to: **produce `structures_cortical.nii.gz` from the 2nd
MRI (native parcellation + label warp into the main grid), drop it in `recon_dir`,
and let the existing pipeline run untouched.** No changes to mesh extraction, the
2D overlay, contact labeling, or the coordinate math.

## Approach (native parcellate → label warp)

**Phase A — background, at upload (registration only):**
1. Register **MRI2 → main MRI** with ANTs **affine-only**
   (`type_of_transform="Affine"`) — deterministic and fast; no nonlinear warp.
   Runs in the background **alongside CT→MRI registration** (`_extract_mesh_background`,
   main.py:939, next to the `_run_registration` call at main.py:998). Persist the
   affine transform (`mri2_to_main_affine.mat`). Reuse the ANTs pattern in
   `services/mni_registration.py:register_mri_to_mni` (swap the transform type;
   drop the SyN warp files).

**Phase B — on explicit user opt-in (parcellation):**
2. Run `desikan_killiany_tourville_labeling` on **native MRI2** (pristine image —
   no pre-resampling).
3. Warp the DKT **label volume** onto the main MRI grid with **label-safe
   interpolation** (`ants.apply_transforms(..., interpolator="genericLabel")`)
   using the precomputed Phase-A transform, `fixed=` main MRI. Save as
   `structures_cortical.nii.gz` (main affine).
4. Existing `extract_all_structures()` sees the cached label volume and proceeds.

Critical: never linear-interpolate labels (it averages label *indices* into
garbage). Post-warp, `np.unique()` on the volume must stay integer-valued.

## Backend changes

### 1. Data model (`database.py` + migration)
- Add `mri2_path` (String, nullable) and `parcellation_source`
  (String, default `"main"`; values `main` | `secondary`) to `Reconstruction`.
- New `backend/migrate_mri2.py` mirroring the existing `migrate_*.py` pattern
  (idempotent `ALTER TABLE ... ADD COLUMN`, skip if present). `create_all` covers
  fresh installs. Existing rows get `mri2_path = NULL` and
  `parcellation_source = "main"`, so they behave exactly as before.

### 2. Upload (`main.py`)
- `create_reconstruction` and `upload_reconstruction_files`: add optional
  `mri2_file: UploadFile = File(None)` (+ `mri2_modality`, must be `t1` for DKT);
  save as `recon_dir/mri2.nii.gz`; set `mri2_path`.
- **Upload triggers background registration only** — saving `mri2.nii.gz` kicks off
  the Phase-A background affine MRI→main registration, but does **not** run
  parcellation or change `parcellation_source` (stays `"main"`). Pass `mri2_path`
  down into `_extract_mesh_background` so the registration is scheduled there.

### 3. New service `services/secondary_parcellation.py`
- `register_mri2_to_main(recon_dir, main_mri, mri2)` — **Phase A (background)**:
  ANTs **affine** MRI2→main; persist `mri2_to_main_affine.mat`. Plain `ants`
  (available in the exe), CPU-only + deterministic.
- `build_cortical_labels_from_secondary(recon_dir, main_mri, mri2, out_label_path)`
  — **Phase B (opt-in)**: DKT on native MRI2, then `apply_transforms` the label
  volume with `genericLabel` onto the main grid using the persisted affine, write
  `structures_cortical.nii.gz`; log world-extent (reuse the `[STRUCT DEBUG]` extent
  print) to confirm it lands inside the brain-mesh bounds. If the transform is not
  present yet (registration still running/failed), compute it first. `antspynet`
  needed only here (dev/conda only — same limitation as current structure computation).

### 4. Orchestration + source toggle
- **Background registration wiring:** thread `mri2_path` into
  `_extract_mesh_background` (main.py:939); after mesh extraction, alongside the CT
  `_run_registration` call (main.py:998), schedule `register_mri2_to_main` on the
  executor when `mri2_path` is set. Registration precomputes here; parcellation does not.
- Add a thin orchestrator (e.g. `ensure_cortical_labels(recon)`): if
  `parcellation_source == "secondary"` and `mri2_path` exists and
  `structures_cortical.nii.gz` is absent/stale, call
  `build_cortical_labels_from_secondary` first (reusing the precomputed transform);
  otherwise no-op (main MRI path). Then the existing `extract_all_structures()` runs.
- Call it from the `GET /structures` handler (before `extract_all_structures`),
  and from the MNI-export step-4 fallback that generates structures.
- New endpoint `POST /reconstructions/{id}/parcellation-source` to set
  `main`/`secondary` and trigger a rebuild.

### 5. Cache invalidation
- When the source changes or `mri2` is (re)uploaded, delete
  `structures_cortical.nii.gz` and `structures/*.json` so they regenerate. (Same
  cache the viewer/overlay/labeling read — must be cleared together.)
- Note: `structures.json` and the DKT volume are keyed only by directory today, so
  switching source without clearing would silently serve the old parcellation.

## Backward compatibility (existing 1-MRI reconstructions)

Existing reconstructions with only one MRI must keep working identically — this is a
hard requirement, and the design guarantees it:
- After `migrate_mri2.py`, existing rows have `mri2_path = NULL` and
  `parcellation_source = "main"` → the default (and only) path they ever take.
- Every new behavior is **guarded**: MRI→MRI registration only schedules when
  `mri2_path` is set; `ensure_cortical_labels` only diverges when
  `parcellation_source == "secondary"` *and* `mri2_path` exists. With `mri2_path`
  NULL, all of it is a no-op and `structures_cortical.nii.gz` is still produced from
  the main MRI exactly as today.
- `GET /structures`, the 2D overlay, `contact_labeling`, and MNI export are
  unchanged — they read the same `structures_cortical.nii.gz` regardless of source.
- New request fields are optional (`mri2_file = File(None)`); existing frontend/API
  calls that omit them are unaffected. Already-cached structures are not invalidated
  (cache is only cleared on an explicit source change or 2nd-MRI (re)upload).
- Reprocessing/re-running an old reconstruction (no 2nd MRI) skips Phase A entirely.
- Housekeeping: add `migrate_mri2.py` to the README "Database migrations (existing
  installs only)" section.

## Frontend changes (`frontend/src/`)
- Upload form (`ReconstructionList.jsx`) + per-recon file upload: optional
  "Second MRI (pre-op, for parcellation)" slot.
- A parcellation-source toggle (main vs. second MRI) in the editor/structure panel,
  wired to the new endpoint; show which source produced the current structures. The
  "second MRI" option is only enabled once a 2nd MRI has been uploaded, and defaults
  to "main" — selecting it is the explicit, user-driven trigger to (re)build from
  the 2nd MRI.
- `api.js`: multipart field for `mri2_file`; call for the source endpoint.

## Coordinate correctness (must-verify)
- Warped labels land on the **main MRI grid with the main affine**; the brain mesh
  center in `mesh.json` is unchanged, so `extract_all_structures` centering is
  correct with no edits.
- Registration is **affine-only** (per decision): fast, deterministic, and safe on
  differing anatomy, but it will **not** correct genuine brain shift / mass-effect
  differences between the two scans — acceptable for rigidly-comparable pre-op vs.
  reference T1s. (Nonlinear SyN could be revisited later if alignment proves
  insufficient.) Keep ANTs CPU-only + deterministic (as in `mni_registration`).

## Edge cases
- No 2nd MRI, or `source == main` → current behavior exactly.
- Registration or DKT failure → fall back to main-MRI parcellation with a logged
  warning; never leave the recon without structures.
- 2nd MRI not T1 → reject (DKT needs T1).
- Standalone exe → DKT (antspynet) unavailable, so secondary parcellation can't be
  computed there; behaves like today's structure computation (borrow donor).

## Verification
1. **Self-registration sanity:** feed a copy of the main MRI as MRI2; warped labels
   should closely match direct main-MRI parcellation (Dice high, extents equal).
2. **Label integrity:** `np.unique(structures_cortical.nii.gz)` stays integer
   (confirms `genericLabel`, not linear).
3. **Alignment:** `[STRUCT DEBUG]` world extent lands inside brain-mesh bounds;
   in the 3D viewer the structure meshes sit inside the brain mesh (same check used
   for the ec4dda8a alignment investigation); spot-check a few contact labels vs.
   the hover tooltip.
4. **End-to-end in browser preview:** upload a 2nd MRI, toggle source, load
   structures, confirm alignment + no console/network errors; re-toggle to main and
   confirm cache regenerates.

## Rough effort
Backend: DB+migration (S), upload (S), new service (M), orchestration+toggle (M),
cache invalidation (S). Frontend: upload slot + toggle (M). Bulk of the risk is
registration quality and getting label-safe interpolation right — both contained
in the one new service.
