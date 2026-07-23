"""
Run once to add MNI-export tracking columns to the reconstructions table.
  cd backend
  python migrate_export_status.py
"""
import asyncio, sys
sys.path.insert(0, '.')
from database import engine
from sqlalchemy import text

async def migrate():
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE reconstructions ADD COLUMN export_status VARCHAR DEFAULT 'none'"))
            print("Added column: export_status")
        except Exception as e:
            print(f"  Column 'export_status' already exists (skipping): {e}")
        try:
            await conn.execute(text("ALTER TABLE reconstructions ADD COLUMN exported_at DATETIME"))
            print("Added column: exported_at")
        except Exception as e:
            print(f"  Column 'exported_at' already exists (skipping): {e}")
        await conn.execute(text("UPDATE reconstructions SET export_status = 'none' WHERE export_status IS NULL"))
        print("Backfilled null export_status to 'none'")
    print("Migration complete.")

asyncio.run(migrate())
