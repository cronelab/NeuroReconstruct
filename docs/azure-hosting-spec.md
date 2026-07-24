# Azure Hosting Request — NeuroReconstruct

**Application:** NeuroReconstruct — Python 3 / FastAPI backend (SQLAlchemy async ORM) + React frontend. Single research web app, internal users, low concurrency.

## Requested resources

| Resource | Requested SKU / configuration | Purpose |
|---|---|---|
| **App Service** | Premium v3 **P2V4** (2 vCPU / 8 GB), Linux. **Always On = enabled.** Autoscale not required — please keep at **1 instance**. | Hosts FastAPI backend + serves React build |
| **Azure SQL Database** | **Single database**, General Purpose **Serverless, Gen5, 1–2 vCore** (min 0.5 vCore), auto-pause enabled. *DTU-model alternative: Standard S0 (10 DTU).* Max size **10 GB**. | Application metadata only (see below) |
| **Storage account** | Standard LRS, Hot tier. **Azure Files share** mounted to the App Service. Start ~**256 GB**, expandable. | All imaging files: source MRI/CT, processed volumes, and MNI export artifacts |

### Why Always On, and why a single instance

Long-running jobs (MRI→MNI registration, mesh extraction) run as in-process background tasks dispatched from an HTTP request that returns immediately. Consequences:

- **Always On** must be enabled so the worker is not unloaded while a background job is still running.
- These jobs do **not** survive an instance recycle or a scale event, and there is no retry. Please keep the plan at **one instance** and avoid autoscale rules. (A queue + worker redesign is on our roadmap; not required for initial deployment.)

## Database details (why the SQL SKU is small)

The database holds **metadata only** — four tables: `users`, `reconstructions`, `electrode_shafts`, `electrode_contacts` (labels, coordinates, status flags, and file *paths*). Total footprint is a few MB even at hundreds of patients. **All imaging data — source, processed, and exported — is stored as files on the Azure Files share, not in SQL.** The DB stores only relative path strings.

- Engine: currently SQLite; will migrate to **Azure SQL Database** (Microsoft SQL Server / T-SQL) using SQLAlchemy + `aioodbc` and **ODBC Driver 18 for SQL Server** (please ensure the driver is available/installable on the App Service image).
- **Alternative if preferred by IT:** Azure Database for **PostgreSQL Flexible Server** (Burstable **B1ms** or **B2s**) is an equally valid, often cheaper fit with cleaner Python async support (`asyncpg`). Please advise which engine your team prefers to support.

## Persistent storage — required configuration

The App Service local disk is **not** a valid location for this data: files written under the deployment directory are replaced on every deploy and lost on instance recycle. Please provision an **Azure Files share** mounted to the App Service (e.g. at `/mounts/neurodata`).

The application resolves its data root from the **`NEURO_DATA_DIR`** environment variable and writes everything beneath it — both the database and all imaging files.

### Required app settings

| Setting | Value |
|---|---|
| `NEURO_DATA_DIR` | Mount path of the Azure Files share, e.g. `/mounts/neurodata` |
| `WEBSITES_ENABLE_APP_SERVICE_STORAGE` | `true` |

### On-disk layout (per reconstruction)

```
$NEURO_DATA_DIR/
├── brain_viewer.db                  # only if staying on SQLite; removed after Azure SQL migration
└── data/recon_<id>/
    ├── mri.nii.gz                   # source pre-op T1
    ├── ct.nii.gz                    # source post-implant CT
    ├── ct_to_mri.npy                # co-registration transform (4x4 matrix, ~128 B)
    ├── ct_masked.nii.gz             # preprocessed CT
    ├── mesh.json, structures_cortical.nii.gz, structures/, ct_cache/
    └── export/                      # MNI export artifacts
        ├── MRI_mni.nii.gz, CT_mni.nii.gz
        ├── mri_to_mni_warp.nii.gz, mri_to_mni_affine.mat, mni_to_mri_invwarp.nii.gz
        └── electrodes_mni.csv/.json, electrodes_structures.csv, export_manifest.json
```

### Capacity sizing

Approximately **150 MB per case** without MNI export, or **~350 MB per case** with export enabled. The requested 256 GB covers several hundred cases with export; please advise if the share can be expanded in place as our caseload grows.

## Networking / security (please confirm your standard)

- Private endpoints / VNet integration for SQL and Storage as per departmental policy.
- Azure AD (Entra) auth for the SQL admin where possible.
- TLS enforced; SQL firewall restricted to the App Service subnet.
