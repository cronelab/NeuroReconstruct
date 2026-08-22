"""
One-shot copy of the application database from the local SQLite file into a
target database (Azure SQL in production).

Row counts are tiny -- the whole point of this script is that IDs must survive
the trip:

  * electrode_shafts.reconstruction_id and electrode_contacts.shaft_id are
    foreign keys.
  * reconstructions.mesh_path anchors the recon_* folders on the file share.

So rows are inserted parent-first with their original primary keys, which on SQL
Server means bracketing each table in SET IDENTITY_INSERT.

Run the schema first (`python -m alembic upgrade head` against the target), then:

    # dry run -- reads only, reports what would be copied
    python scripts/migrate_sqlite_to_azure.py --dry-run

    # real run, target taken from DATABASE_URL
    python scripts/migrate_sqlite_to_azure.py

    # skip the _dev* scratch reconstructions and everything hanging off them
    python scripts/migrate_sqlite_to_azure.py --exclude-dev

The target URL is the application's async URL; the sync driver is substituted
here the same way migrations/env.py does it.
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import DateTime, create_engine, func, select, text  # noqa: E402

# Parent-first. Reversing this list gives a safe delete order.
TABLE_ORDER = ["users", "reconstructions", "electrode_shafts",
               "electrode_contacts", "seeg_recordings"]


def sync_url(url: str) -> str:
    return (url.replace("+aiosqlite", "")
               .replace("+aioodbc", "+pyodbc")
               .replace("+asyncpg", "+psycopg2"))


def coerce(value, column):
    """SQLite hands back DATETIME columns as strings; SQL Server wants datetimes."""
    if value is None or not isinstance(column.type, DateTime) or isinstance(value, datetime):
        return value
    text_val = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text_val, fmt)
        except ValueError:
            continue
    raise ValueError(f"unparseable datetime {value!r} in {column.name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", help="path to the SQLite file (default: backend/brain_viewer.db)")
    ap.add_argument("--target", help="target URL (default: $DATABASE_URL)")
    ap.add_argument("--dry-run", action="store_true", help="read and report, write nothing")
    ap.add_argument("--exclude-dev", action="store_true",
                    help="skip reconstructions whose patient_id contains _dev, and their children")
    ap.add_argument("--force", action="store_true", help="proceed even if the target has rows")
    args = ap.parse_args()

    import database  # imported late so --help works without a driver installed

    source = args.source or os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "brain_viewer.db")
    target = args.target or os.environ.get("DATABASE_URL")
    if not target:
        sys.exit("No target: pass --target or set DATABASE_URL")
    if not os.path.exists(source):
        sys.exit(f"Source not found: {source}")

    print(f"source : {source}")
    print(f"target : {sync_url(target).split('://')[0]}://... ({'DRY RUN' if args.dry_run else 'WILL WRITE'})\n")

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    engine = create_engine(sync_url(target))
    meta = database.Base.metadata
    is_mssql = engine.dialect.name == "mssql"

    # Which reconstructions travel, and therefore which children.
    skipped_recons = set()
    if args.exclude_dev:
        skipped_recons = {r["id"] for r in src.execute("SELECT id, patient_id FROM reconstructions")
                          if "_dev" in (r["patient_id"] or "")}
        if skipped_recons:
            print(f"excluding {len(skipped_recons)} _dev reconstruction(s): {sorted(skipped_recons)}\n")
    skipped_shafts = set()
    if skipped_recons:
        skipped_shafts = {r["id"] for r in src.execute("SELECT id, reconstruction_id FROM electrode_shafts")
                          if r["reconstruction_id"] in skipped_recons}

    def keep(table, row):
        if table == "reconstructions":
            return row["id"] not in skipped_recons
        if table in ("electrode_shafts", "seeg_recordings"):
            return row["reconstruction_id"] not in skipped_recons
        if table == "electrode_contacts":
            return row["shaft_id"] not in skipped_shafts
        return True

    with engine.begin() as conn:
        # Refuse to merge into a populated target -- duplicate PKs would fail
        # halfway and leave a partial copy.
        if not args.dry_run and not args.force:
            for name in TABLE_ORDER:
                n = conn.execute(select(func.count()).select_from(meta.tables[name])).scalar()
                if n:
                    sys.exit(f"Target table {name} already has {n} row(s). Use --force to override.")

        summary = []
        for name in TABLE_ORDER:
            table = meta.tables[name]
            cols = [c for c in table.columns]
            rows = [{c.name: coerce(r[c.name], c) for c in cols}
                    for r in src.execute(f"SELECT * FROM {name}") if keep(name, r)]
            summary.append((name, len(rows)))
            if not rows:
                print(f"{name:<22} 0 rows")
                continue
            if args.dry_run:
                print(f"{name:<22} {len(rows)} rows (not written)")
                continue

            # Preserve primary keys through the copy.
            if is_mssql:
                conn.execute(text(f"SET IDENTITY_INSERT {name} ON"))
            conn.execute(table.insert(), rows)
            if is_mssql:
                conn.execute(text(f"SET IDENTITY_INSERT {name} OFF"))
            print(f"{name:<22} {len(rows)} rows copied")

    if args.dry_run:
        print("\nDry run complete -- nothing written.")
        return

    # ── Verification ────────────────────────────────────────────────────────
    print("\nverifying...")
    ok = True
    with engine.connect() as conn:
        for name, expected in summary:
            got = conn.execute(select(func.count()).select_from(meta.tables[name])).scalar()
            flag = "OK " if got == expected else "BAD"
            ok &= got == expected
            print(f"  {flag} {name:<22} expected {expected}, found {got}")

        r, s, c = (meta.tables[t] for t in ("reconstructions", "electrode_shafts", "electrode_contacts"))
        orphan_shafts = conn.execute(
            select(func.count()).select_from(s.outerjoin(r, s.c.reconstruction_id == r.c.id))
            .where(r.c.id.is_(None))).scalar()
        orphan_contacts = conn.execute(
            select(func.count()).select_from(c.outerjoin(s, c.c.shaft_id == s.c.id))
            .where(s.c.id.is_(None))).scalar()
        print(f"  {'OK ' if not orphan_shafts else 'BAD'} orphaned shafts:   {orphan_shafts}")
        print(f"  {'OK ' if not orphan_contacts else 'BAD'} orphaned contacts: {orphan_contacts}")
        ok &= not orphan_shafts and not orphan_contacts

        # Every mesh_path must resolve under the data root, or the app will show
        # reconstructions it cannot open.
        data_dir = os.path.join(os.environ.get("NEURO_DATA_DIR") or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))), "data")
        missing = [row.mesh_path for row in conn.execute(
            select(r.c.mesh_path).where(r.c.mesh_path.isnot(None)).where(r.c.deleted_at.is_(None)))
            if not os.path.exists(os.path.join(data_dir, row.mesh_path))]
        print(f"  {'OK ' if not missing else '!! '} mesh files present under {data_dir}"
              + (f" -- MISSING {len(missing)}: {missing[:3]}" if missing else ""))

    print("\nMIGRATION OK" if ok else "\nMIGRATION COMPLETED WITH ERRORS -- see above")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
