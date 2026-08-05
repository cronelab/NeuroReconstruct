"""
Run once to add second-MRI parcellation columns to the reconstructions table.
  cd backend
  python migrate_mri2.py

Safe to run multiple times — existing columns are skipped. Existing rows keep
mri2_path = NULL and parcellation_source = 'main', so single-MRI reconstructions
behave exactly as before.
"""
import asyncio, sys
sys.path.insert(0, '.')
from database import engine
from sqlalchemy import text

async def migrate():
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE reconstructions ADD COLUMN mri2_path VARCHAR"))
            print("Added column: mri2_path")
        except Exception as e:
            print(f"  Column 'mri2_path' already exists (skipping): {e}")
        try:
            await conn.execute(text("ALTER TABLE reconstructions ADD COLUMN parcellation_source VARCHAR DEFAULT 'main'"))
            print("Added column: parcellation_source")
        except Exception as e:
            print(f"  Column 'parcellation_source' already exists (skipping): {e}")
        await conn.execute(text("UPDATE reconstructions SET parcellation_source = 'main' WHERE parcellation_source IS NULL"))
        print("Backfilled null parcellation_source to 'main'")
    print("Migration complete.")

asyncio.run(migrate())
