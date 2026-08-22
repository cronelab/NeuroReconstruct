# Azure Deployment Runbook — NeuroReconstruct

Step-by-step deployment onto the resources JH Research IT provisioned in
`JH-RIT-CRONE-App-RG`. Companion to [azure-hosting-spec.md](azure-hosting-spec.md)
(what we asked for) and [azure-sql-migration.md](azure-sql-migration.md) (why the
database work is needed).

**Do the phases in order.** Each one ends in something you can check.

## The resources you have

| Thing | Name |
|---|---|
| Resource group | `JH-RIT-CRONE-App-RG` |
| Subscription | `5fe71151-fb6f-484b-81fc-9104d22a15ea` |
| App Service | `rit3845-neurorecon-APP` -> https://rit3845-neurorecon-app.azurewebsites.net |
| App Service Plan | `RIT-3845-neurorecon-ASP` (Premium v4 P2v4, 2 vCPU / 8 GB) |
| SQL Server | `rit3845neuroreconsql01.database.windows.net` (GP_S_Gen5_2 serverless, 0.5-2 vCore, 10 GB) |
| Storage account | `rit3845neuroreconst01` (Standard LRS, 256 GiB Azure Files) |
| Container registry | `rit3845neuroreconacr01.azurecr.io` |
| Reachable from | Internal Hopkins network only |

## How the pieces fit together

You build one Docker image containing the FastAPI backend, the compiled React
frontend, ANTs/TensorFlow, and the ODBC driver. That image is pushed to the
**container registry**. The **App Service** runs it. The image is stateless --
the database lives in **Azure SQL** and every imaging file lives on the **Azure
Files share**, mounted into the container at `/mounts/neurodata`.

Redeploying replaces the container. It must never touch data, which is why
nothing may be written inside the image.

---

## Phase 1 — Tools and discovery

You need the Azure CLI. You do **not** need Docker: images are built in Azure.

1. Install the Azure CLI (winget: `winget install -e --id Microsoft.AzureCLI`),
   then reopen your terminal.
2. `az login` -- sign in with your JHED, then
   `az account set --subscription 5fe71151-fb6f-484b-81fc-9104d22a15ea`
3. Run `scripts/azure_discover.ps1`. It answers the four things the email does
   not tell us:
   - Is the App Service plan **Linux**? (a container image will not run on Windows)
   - What is the **database name** on the SQL server?
   - What is the **file share name**, and is it already mounted?
   - Do you have **push rights** to the registry?

### Discovery results (run 2026-08-22)

| Question | Answer |
|---|---|
| Linux plan? | **Yes** — `kind: app,linux` |
| Always On | Already **on**, 1 worker |
| Managed identity | Already **enabled**, principal `533de2af-bf50-4c80-b78e-a22e6a087037` |
| App settings pre-created by IT | `AZURE_SQL_SERVER`, `AZURE_SQL_DATABASE`, `AZURE_SQL_AUTHENTICATION`, `NEURO_DATA_DIR`, `WEBSITES_ENABLE_APP_SERVICE_STORAGE`, `WEBSITES_VNET_ROUTE_ALL` (values not readable at current access) |
| Database name | **Unknown** — no read access on SQL |
| File share | **Unknown**, and **no share is mounted** (`storage-account list` is empty) |
| Registry push rights | **No** — `AuthorizationFailed` + 401 |

### Two blockers found

1. **RBAC is scoped to the App Service only.** `jkim605` gets
   `AuthorizationFailed` on the plan, SQL, storage, and the registry. Nothing in
   Phase 3 or 5 can proceed until this is fixed.
2. **The App Service is configured as code, not a container**
   (`LinuxFxVersion = PYTHON|3.12`). The built-in Python runtime cannot host this
   app — ANTs, TensorFlow, Open3D and ODBC Driver 18 all need root-installed
   system packages that an Oryx build cannot provide.

Both are IT-side changes. See [it-access-request.md](it-access-request.md) for the
email covering these plus the unmounted share and the SQL user.

**Also note:** `WEBSITES_VNET_ROUTE_ALL` means outbound traffic is VNet-routed,
so runtime internet egress is not guaranteed. That makes baking the model weights
into the image (`backend/warmup_models.py`) load-bearing rather than merely a
speed optimization — antspynet would otherwise try to download them at runtime.

---

## Phase 2 — Code changes  [DONE]

Already applied. All are env-var driven with fallbacks, so the local Windows
workflow is unchanged (verified end to end: React -> FastAPI -> SQLite).

| Change | File | Effect |
|---|---|---|
| Explicit `String` lengths on all models | `backend/database.py` | Azure SQL rejects `varchar(max)` as an index key (error 1919) -- this was a hard blocker |
| `DATABASE_URL` env + `pool_pre_ping` / `pool_recycle` | `backend/database.py` | Points at Azure SQL; survives serverless auto-pause and dropped idle connections |
| `SECRET_KEY` from env | `backend/auth.py` | Was a hardcoded literal -- JWTs were forgeable |
| `ADMIN_PASSWORD` from env | `backend/main.py` | Was auto-creating `admin` / `changeme` on first start |
| `CORS_ORIGINS` from env | `backend/main.py` | Adds the deployed hostname |

New files: `Dockerfile`, `.dockerignore`, `backend/requirements-cloud.txt`,
`backend/warmup_models.py`.

Still outstanding (Phase 5): Alembic baseline, and deleting the five obsolete
`migrate_*.py` scripts -- two of them `import sqlite3` and cannot run on Azure SQL.

---

## Phase 3 — Build the image

From the repo root:

```
az acr build --registry rit3845neuroreconacr01 --image neurorecon:1.0 .
```

This uploads the build context and builds **in Azure** -- important, because the
image is several GB (TensorFlow + ANTs + open3d) and you are on a VPN.

Expect **20-40 minutes** on the first build. Most of that is the model warm-up
(`backend/warmup_models.py`), which bakes the antspynet brain-extraction and DKT
weights plus the MNI152 template into the image. Without it, the first upload
after every container restart re-downloads hundreds of MB mid-request.

Check afterwards:

```
az acr repository show-tags -n rit3845neuroreconacr01 --repository neurorecon
```

---

## Phase 4 — Wire up the App Service

### 4a. Mount the file share

Create a share named `neurodata` in `rit3845neuroreconst01`, then mount it to the
App Service at **`/mounts/neurodata`**. IT may have done this already -- Phase 1
tells you.

### 4b. Application settings

| Setting | Value | Why |
|---|---|---|
| `NEURO_DATA_DIR` | `/mounts/neurodata` | data root -- the app writes everything below this |
| `DATABASE_URL` | `mssql+aioodbc://...` | see 4c |
| `SECRET_KEY` | a generated random string | JWT signing |
| `ADMIN_PASSWORD` | a real password | replaces `changeme` on first start |
| `CORS_ORIGINS` | `https://rit3845-neurorecon-app.azurewebsites.net` | |
| `WEBSITES_PORT` | `8000` | tells the front end which port the container listens on |
| `WEBSITES_CONTAINER_START_TIME_LIMIT` | `1800` | default is 230 s -- a multi-GB image will not pull and start in time |
| `WEBSITES_ENABLE_APP_SERVICE_STORAGE` | `true` | required for the mount |

Also set **Always On = on**, and leave the plan at **1 instance, no autoscale**.
Mesh extraction and MNI export are in-process background tasks: they do not
survive an instance recycle, and the startup reaper in `main.py` marks orphaned
jobs as failed on the assumption that it is the only instance running.

### 4c. Database connection

Preferred -- no password anywhere:

1. Turn on the App Service's system-assigned managed identity.
2. In the database: `CREATE USER [rit3845-neurorecon-APP] FROM EXTERNAL PROVIDER;`
   then grant `db_datareader`, `db_datawriter`, `db_ddladmin`.
3. Set `DATABASE_URL` to
   `mssql+aioodbc://rit3845neuroreconsql01.database.windows.net/<db>?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&Authentication=ActiveDirectoryMsi`

Fallback: a SQL login with the password in Key Vault (never committed).

Then let the App Service reach the server -- "Allow Azure services" on the SQL
firewall, the App Service outbound IPs, or VNet integration.

---

## Phase 5 — Move the data

### 5a. Schema  [Alembic in place]

Alembic is set up in `backend/` (revision `7a75b7caf589`, baseline schema). It
reads `DATABASE_URL` -- the same variable the app uses -- and swaps in the sync
driver, so there is nothing extra to configure. From `backend/`:

```
python -m alembic upgrade head
```

The five old `migrate_*.py` scripts have been deleted; they were historical and
had all been applied. The local SQLite database has been stamped at the baseline
revision, so Alembic treats it as current rather than trying to recreate it.

### 5b. Rows (~148 KB)

Use `backend/scripts/migrate_sqlite_to_azure.py`. It inserts parent-first with
`SET IDENTITY_INSERT` so primary keys survive -- necessary because
`electrode_shafts.reconstruction_id` and `electrode_contacts.shaft_id` are
foreign keys and `reconstructions.mesh_path` anchors the on-disk `recon_*`
folders. Run the schema first, then:

```
python scripts/migrate_sqlite_to_azure.py --dry-run
```

```
python scripts/migrate_sqlite_to_azure.py --exclude-dev
```

It refuses to write into a non-empty target, and verifies afterwards: per-table
row counts, orphaned shafts/contacts, and that every `mesh_path` resolves on the
share.

Current local contents: 1 user, 12 reconstructions (11 active), 141 shafts,
1340 contacts, 7 sEEG recordings. With `--exclude-dev`: 10 reconstructions,
120 shafts, 1122 contacts, 1 recording.

Tested SQLite -> SQLite locally; the `SET IDENTITY_INSERT` path only engages on
SQL Server and is exercised for the first time against the real database.

### 5c. Files (~11 GB)

Upload `backend/data/` to the share with `azcopy`. Before you start:

- **5.4 GB of the 11 GB is sEEG `.h5` recordings.**
- `recon_645376cf` alone is **5.5 GB** -- that is `PY26N005_dev1`, a dev copy.
  The `_dev*` reconstructions are scratch and probably should not be uploaded.

Excluding the dev copies brings the initial upload to roughly **5 GB**.

`main.py` already normalizes legacy absolute paths to relative at startup, so a
database moved to the share resolves against the new root without path rewriting.

---

## Phase 6 — Verify

In order -- each step exercises more of the stack than the last:

1. `https://rit3845-neurorecon-app.azurewebsites.net/docs` loads -> container is up.
2. Log in as `admin` -> Azure SQL round-trip works.
3. The reconstruction list is populated -> row migration worked.
4. Open a 3D view -> the file share is mounted and readable.
5. **Run one MNI export.** This is the real integration test: database, share,
   ANTs, and the baked model weights all at once.

Watch it with:

```
az webapp log tail --name rit3845-neurorecon-APP --resource-group JH-RIT-CRONE-App-RG
```

---

## Know before you start

**It will be slower than your workstation.** The P2v4 has **2 vCPU**; you develop
on 32 threads. From this repo's own timing notes: CT->MRI registration is 3-10.7
min single-threaded versus ~4.5-5x faster multithreaded, and DKT segmentation is
270 s at 1 thread vs 143 s at 32. So expect:

| Job | Local (32 threads) | Expected on P2v4 |
|---|---|---|
| MNI export | 30-47 s | ~2-4 min |
| New patient (skull strip + registration + segmentation) | ~5 min | ~15-25 min |

These are background jobs, so it is tolerable -- but the UI will feel slow
compared to local, and that is the hardware, not a bug.

**Azure Files is SMB**, much slower than local disk for the many small reads and
writes ANTs does. The Dockerfile sets `TMPDIR=/tmp` to keep scratch I/O on the
container's local disk; only finished artifacts go to the share.

**Background jobs are still fragile.** They do not survive a restart or a deploy,
and there is no retry. A queue + worker redesign remains on the roadmap.
