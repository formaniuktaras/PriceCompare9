"""Build the silver layer using Polars lazy computation."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from .io_parquet import SILVER_DIR, scan_bronze_glob
from .metrics import log_event, peak_rss_mb, timer


def build_silver(data_root: str | Path | None = None) -> Path:
    """Collect bronze data into the silver layer."""

    with timer("build_silver") as t:
        try:
            lf = (
                scan_bronze_glob()
                .select(
                    [
                        pl.col("sku"),
                        pl.col("supplier"),
                        pl.col("brand_norm"),
                        pl.col("name_clean"),
                        pl.col("price").cast(pl.Float64),
                        pl.col("qty").cast(pl.Int64),
                        pl.col("currency"),
                    ]
                )
                .with_columns(
                    [
                        pl.col("brand_norm").cast(pl.Categorical),
                        pl.col("supplier").cast(pl.Categorical),
                        pl.col("currency").cast(pl.Categorical),
                        pl.concat_str(
                            [
                                pl.col("brand_norm").cast(pl.Utf8),
                                pl.col("name_clean"),
                            ],
                            separator="|",
                        ).alias("pid_raw"),
                    ]
                )
            )
            df = lf.collect(streaming=True)
        except Exception as exc:  # pragma: no cover - empty bronze layer
            log_event("silver_build", status="empty", reason=str(exc))
            raise
        out_path = SILVER_DIR / "offers.parquet"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out_path, compression="zstd", statistics=True)

    log_event(
        "silver_build",
        rows=df.height,
        output=str(out_path),
        duration=t.seconds,
        peak_rss_mb=peak_rss_mb(),
    )
    return out_path
