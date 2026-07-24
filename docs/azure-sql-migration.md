# SQLite → Azure SQL Migration Scope

Scoping notes for moving the application database off SQLite as part of the Azure
deployment described in [azure-hosting-spec.md](azure-hosting-spec.md). Nothing here
is implemented yet.

**Estimated effort: ~3 days.**

## Context

The database holds metadata only — `users`, `reconstructions`, `electrode_shafts`,
`electrode_contacts`. All imaging data lives on the filesystem, referenced by relative
paths (`_rel()` / `_abs()` in `main.py`). Row counts are small; the difficulty is
schema portability and operational behavior, not data volume.

All application queries are portable SQLAlchemy ORM/Core (`select()`, `update()`).
Raw SQL appears only in the one-shot `migrate_*.py` scripts. There is no query
rewriting to do.

## 1. Blocker: unlengthed `String` columns

Every string column in `database.py` is a bare `Column(String)`. Compiling the current
metadata against the MSSQL dialect yields:

```sql
CREATE TABLE users (
    id INTEGER NOT NULL IDENTITY,
    username VARCHAR(max) NOT NULL,
    ...
    UNIQUE (username)          -- SQL Server rejects this
)
```

SQL Server cannot use a LOB type (`varchar(max)`) as an index key — index keys are
capped at 900 bytes, and the server raises error 1919. Two constraints trip it:

- `users.username` (unique)
- `reconstructions.share_token` (unique)

The DDL **compiles** offline without complaint; it fails only when executed against a
real server. Expect this to surface at first deploy, not during local development.

**Fix:** give every string column an explicit length — roughly 13 columns across the
four models. Suggested: `String(64)` for names/roles/tokens/statuses, `String(512)`
for the `*_path` columns, `String(16)` for `color`. Unlengthed `varchar(max)` is also
poor for storage and query planning even where it is legal, so this is worth doing for
all string columns, not just the two indexed ones.

**PostgreSQL compiles and runs the current schema unchanged** — unlengthed `VARCHAR`
is a normal indexable type there. If the engine choice is still open, this is a
concrete reason to prefer Azure Database for PostgreSQL Flexible Server.

## 2. Driver and connection string

| | Current | Target |
|---|---|---|
| Driver | `aiosqlite` | `aioodbc` + ODBC Driver 18 for SQL Server |
| URL | `sqlite+aiosqlite:///<path>` | `mssql+aioodbc://...?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes` |

Driver availability on the App Service Linux image is an open question for research IT
(flagged in the hosting spec). Credentials must come from App Service application
settings or Key Vault, never from a committed file.

## 3. Migration scripts

Five one-shot scripts exist, in two mutually incompatible styles:

| Script | Style | Status on Azure SQL |
|---|---|---|
| `migrate_deleted_at.py` | raw `import sqlite3` | will not run |
| `migrate_shaft_fields.py` | raw `import sqlite3` | will not run |
| `migrate_lock_fields.py` | SQLAlchemy `text()` | SQLite-flavored DDL |
| `migrate_export_status.py` | SQLAlchemy `text()` | SQLite-flavored DDL |
| `migrate_registration_confirmed.py` | SQLAlchemy `text()` | SQLite-flavored DDL |

The `text()` ones emit `BOOLEAN DEFAULT 0` and unlengthed `VARCHAR`, and detect
"column already exists" by catching a generic exception around `ALTER TABLE`.

These are historical and have all been applied to the live database. **Do not port
them.** Adopt Alembic, baseline it at the current schema, and delete all five. Azure
SQL also removes the option of leaning on `Base.metadata.create_all()` for schema
changes, so a real migration tool becomes necessary rather than optional.

## 4. Data migration

Small, but IDs must be preserved:

- `electrode_shafts.reconstruction_id` and `electrode_contacts.shaft_id` are foreign keys.
- `reconstructions.mesh_path` anchors the on-disk `recon_*` directories.

Requires `SET IDENTITY_INSERT <table> ON` per table and parent-first insert ordering
(`users` → `reconstructions` → `electrode_shafts` → `electrode_contacts`). One script
plus a verification pass: row counts per table, FK integrity, and a spot check that
every `mesh_path` still resolves on the mounted share.

Note that the startup routine in `main.py` already normalizes any legacy absolute
paths to relative, so a database moved to the share resolves correctly against the new
`NEURO_DATA_DIR` root without path rewriting.

## 5. Connection resilience

`create_async_engine` is currently called with defaults, which is fine for a local file
but not for a managed remote database:

- Azure SQL drops idle connections — needs `pool_pre_ping=True` and a `pool_recycle`
  shorter than the server's idle timeout.
- Transient faults (40197, 40501, 10928) need retry with backoff.
- **Serverless auto-pause** means the first request after an idle period pays a
  30–60 s cold start. Without pre-ping and retry the user gets an error rather than a
  slow response.

If that cold start is unacceptable (e.g. for demos), switch to provisioned General
Purpose 1 vCore, which does not auto-pause, and drop the serverless line from the
hosting spec.

## Effort breakdown

| Task | Estimate |
|---|---|
| Explicit `String` lengths + schema review | ½ day |
| Driver swap, connection string, settings wiring | ½ day |
| Alembic adoption + baseline, remove `migrate_*.py` | ½ day |
| Data migration script + verification | ½ day |
| Connection resilience (pre-ping, recycle, retry) | ¼ day |
| End-to-end testing against a real Azure SQL instance | 1 day |
| **Total** | **~3 days** |
