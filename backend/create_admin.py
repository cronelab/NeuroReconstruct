import asyncio
from sqlalchemy import select
from database import init_db, AsyncSessionLocal, User
from auth import hash_password

async def create_admin():
    await init_db()
    async with AsyncSessionLocal() as db:
        # Check if admin already exists
        result = await db.execute(select(User).where(User.username == "admin"))
        existing = result.scalar_one_or_none()
        if existing:
            print("Admin user already exists — deleting and recreating...")
            await db.delete(existing)
            await db.commit()

        admin = User(
            username="admin",
            hashed_password=hash_password("changeme"),
            role="admin"
        )
        db.add(admin)
        await db.commit()
        print("✓ Admin user created successfully")
        print("  Username: admin")
        print("  Password: changeme")

asyncio.run(create_admin())