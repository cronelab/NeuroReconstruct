"""
Run once to add is_complete and is_locked columns to reconstructions table.
  cd backend
  python migrate_lock_fields.py
"""
import asyncio, sys
sys.path.insert(0, '.')
from database import engine
from sqlalchemy import text

async def migrate():
    async with engine.begin() as conn:
        for col, default in [('is_complete', '0'), ('is_locked', '0')]:
            try:
                await conn.execute(text(f"ALTER TABLE reconstructions ADD COLUMN {col} BOOLEAN DEFAULT {default}"))
                print(f"✓ Added column: {col}")
            except Exception as e:
                print(f"  Column '{col}' already exists (skipping): {e}")
        # Set existing nulls to 0
        await conn.execute(text("UPDATE reconstructions SET is_complete = 0 WHERE is_complete IS NULL"))
        await conn.execute(text("UPDATE reconstructions SET is_locked = 0 WHERE is_locked IS NULL"))
        print("✓ Backfilled nulls to 0")
    print("Migration complete.")

asyncio.run(migrate())
