"""
Migration: add label, n_total_contacts, spacing_mm, grid_rows, grid_cols,
contact_diameter_mm, contact_length_mm, shaft_diameter_mm to electrode_shafts.
Safe to run multiple times — skips columns that already exist.
"""
import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "brain_viewer.db")

NEW_COLUMNS = [
    ("label",               "TEXT"),
    ("n_total_contacts",    "INTEGER DEFAULT 12"),
    ("spacing_mm",          "REAL DEFAULT 3.5"),
    ("grid_rows",           "INTEGER"),
    ("grid_cols",           "INTEGER"),
    ("contact_diameter_mm", "REAL DEFAULT 0.8"),
    ("contact_length_mm",   "REAL DEFAULT 2.0"),
    ("shaft_diameter_mm",   "REAL DEFAULT 0.5"),
]

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("PRAGMA table_info(electrode_shafts)")
existing = {row[1] for row in cur.fetchall()}

for col_name, col_type in NEW_COLUMNS:
    if col_name not in existing:
        cur.execute(f"ALTER TABLE electrode_shafts ADD COLUMN {col_name} {col_type}")
        print(f"  + Added column: {col_name}")
    else:
        print(f"  ✓ Already exists: {col_name}")

conn.commit()
conn.close()
print("Migration complete.")
