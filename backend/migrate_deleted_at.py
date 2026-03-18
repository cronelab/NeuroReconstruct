"""
One-time migration: adds deleted_at column to reconstructions table.
Run from /backend:  python migrate_deleted_at.py
"""
import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "brain_viewer.db")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Check if column already exists
cursor.execute("PRAGMA table_info(reconstructions)")
cols = [row[1] for row in cursor.fetchall()]

if "deleted_at" not in cols:
    cursor.execute("ALTER TABLE reconstructions ADD COLUMN deleted_at DATETIME NULL")
    conn.commit()
    print("✓ Added deleted_at column to reconstructions table")
else:
    print("✓ deleted_at column already exists — nothing to do")

conn.close()
