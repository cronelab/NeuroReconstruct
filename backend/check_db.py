import asyncio
from database import init_db, AsyncSessionLocal, User, DATABASE_URL
from sqlalchemy import select

async def check():
    print(f"Database path: {DATABASE_URL}")
    print("Running init_db()...")
    await init_db()
    print("init_db() complete")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        users = result.scalars().all()
        if not users:
            print("NO USERS IN DATABASE")
        for u in users:
            print(f"id={u.id} username={u.username} role={u.role}")

asyncio.run(check())