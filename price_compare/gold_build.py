"""Build the gold layer aggregates using DuckDB."""

from __future__ import annotations

import duckdb
import polars as pl

from .io_parquet import GOLD_DIR, SILVER_DIR
from .metrics import log_event, peak_rss_mb, timer


def build_gold_summary() -> Path:
    """Aggregate offers to produce the gold summary parquet."""

    silver_path = SILVER_DIR / "offers.parquet"
    if not silver_path.exists():
        raise FileNotFoundError("Silver layer not built yet")

    with timer("build_gold") as t:
        con = duckdb.connect()
        try:
            arrow_tbl = con.execute(
                """
                SELECT pid_raw,
                       COUNT(DISTINCT sku) AS sku_count,
                       COUNT(DISTINCT supplier) AS supplier_count,
                       AVG(price) AS avg_price,
                       MIN(price) AS min_price,
                       MAX(price) AS max_price
                FROM read_parquet(?)
                GROUP BY pid_raw
                """,
                [silver_path.as_posix()],
            ).arrow()
        finally:
            con.close()
        df = pl.from_arrow(arrow_tbl)
        out_path = GOLD_DIR / "summary.parquet"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out_path, compression="zstd", statistics=True)

    log_event(
        "gold_build",
        rows=df.height,
        output=str(out_path),
        duration=t.seconds,
        peak_rss_mb=peak_rss_mb(),
    )
    return out_path
