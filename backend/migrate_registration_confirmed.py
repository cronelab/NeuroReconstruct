"""
Run once to add registration_confirmed column to reconstructions table.
  cd backend
  python migrate_registration_confirmed.py
"""
import asyncio, sys
sys.path.insert(0, '.')
from database import engine
from sqlalchemy import text

async def migrate():
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE reconstructions ADD COLUMN registration_confirmed BOOLEAN DEFAULT 0"))
            print("Added column: registration_confirmed")
        except Exception as e:
            print(f"  Column 'registration_confirmed' already exists (skipping): {e}")
        await conn.execute(text("UPDATE reconstructions SET registration_confirmed = 0 WHERE registration_confirmed IS NULL"))
        print("Backfilled nulls to 0")
    print("Migration complete.")

asyncio.run(migrate())
