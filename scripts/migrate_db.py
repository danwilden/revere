#!/usr/bin/env python3
"""Migrate market data from backend/data/market.duckdb to data/market.duckdb.

Run this after:
  1. Stopping the Jupyter kernel that holds the lock on data/market.duckdb
  2. Stopping the uvicorn API server

Then run:
  .venv/bin/python scripts/migrate_db.py

After migration, restart the API server from the project root:
  .venv/bin/python -m uvicorn apps.api.main:app --reload
"""
import duckdb
from pathlib import Path

SRC = Path(__file__).parent.parent / "backend" / "data" / "market.duckdb"
DST = Path(__file__).parent.parent / "data" / "market.duckdb"
DST.parent.mkdir(parents=True, exist_ok=True)

TABLES = ["bars_1m", "bars_agg", "features", "feature_runs", "regime_labels"]

print(f"Source: {SRC}")
print(f"Destination: {DST}")

if not SRC.exists():
    print("ERROR: Source database does not exist.")
    raise SystemExit(1)

src = duckdb.connect(str(SRC), read_only=True)
dst = duckdb.connect(str(DST))

# Ensure schema exists in destination
from backend.data.duckdb_store import _DDL
dst.execute(_DDL)

for table in TABLES:
    src_count = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if src_count == 0:
        print(f"  {table}: 0 rows — skipping")
        continue
    print(f"  Migrating {table}: {src_count} rows...", end="", flush=True)
    rows = src.execute(f"SELECT * FROM {table}").fetchdf()
    dst.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM rows")
    dst_count = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f" done ({dst_count} in destination)")

src.close()
dst.close()
print("\nMigration complete.")
