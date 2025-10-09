"""Helpers for writing and scanning parquet files in the bronze/silver/gold layers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import polars as pl

from .config import data_root


BRONZE_DIR = data_root() / "bronze"
SILVER_DIR = data_root() / "silver"
GOLD_DIR = data_root() / "gold"

for directory in (BRONZE_DIR, SILVER_DIR, GOLD_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def save_parquet(df: pl.DataFrame, supplier: str, date_str: str | None, name: str) -> Path:
    """Save a dataframe into the bronze layer with consistent naming."""

    date_str = date_str or datetime.utcnow().date().isoformat()
    out_dir = BRONZE_DIR / supplier / f"date={date_str}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.parquet"
    df.write_parquet(out_path, compression="zstd", statistics=True)
    return out_path


def scan_bronze_glob() -> pl.LazyFrame:
    """Scan all bronze parquet files lazily."""

    pattern = str(BRONZE_DIR / "*" / "date=*" / "**" / "*.parquet")
    return pl.scan_parquet(pattern)
