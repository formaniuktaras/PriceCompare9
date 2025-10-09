"""Worker utilities for importing supplier price lists into the bronze layer."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import polars as pl

from .io_parquet import save_parquet
from .metrics import log_event, peak_rss_mb, timer
from .xlsx_duckdb import xlsx_to_parquet

SCHEMA = {
    "sku": pl.Utf8,
    "name": pl.Utf8,
    "brand": pl.Utf8,
    "price": pl.Float64,
    "qty": pl.Int64,
    "currency": pl.Utf8,
}

def _prepare_lazy_frame(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Select the minimal required columns from the lazy frame."""

    schema = lf.schema
    selections = []
    for column, dtype in SCHEMA.items():
        if column in schema:
            selections.append(pl.col(column).cast(dtype).alias(column))
        else:
            selections.append(pl.lit(None).cast(dtype).alias(column))
    return (
        lf.select(selections)
        .filter(pl.col("price").is_not_null() & (pl.col("price") > 0))
        .with_columns(pl.col("qty").fill_null(0).cast(pl.Int64))
    )


def _normalise_frame(df: pl.DataFrame) -> pl.DataFrame:
    """Apply lightweight normalisation suitable for the bronze layer."""

    return df.with_columns(
        [
            pl.col("brand")
            .str.to_lowercase()
            .str.strip()
            .cast(pl.Categorical)
            .alias("brand_norm"),
            pl.col("name")
            .str.replace_all(r"\s+", " ")
            .str.strip()
            .alias("name_clean"),
            pl.col("currency")
            .str.to_lowercase()
            .str.strip()
            .cast(pl.Categorical)
            .alias("currency"),
        ]
    )


def import_csv_to_bronze(supplier: str, path: Path) -> Path:
    """Import a CSV file into the bronze layer."""

    path = Path(path)
    source_bytes = path.stat().st_size
    date_str = datetime.utcnow().date().isoformat()
    with timer("import_csv") as t:
        lf = pl.scan_csv(
            path,
            has_header=True,
            dtypes=SCHEMA,
            try_parse_dates=True,
            infer_schema_length=2000,
        )
        df = _prepare_lazy_frame(lf).collect(streaming=True)
        df = _normalise_frame(df)
        df = df.with_columns(pl.lit(supplier).alias("supplier"))
        parquet_path = save_parquet(df, supplier, date_str, path.stem)
    output_bytes = parquet_path.stat().st_size
    log_event(
        "import",  # JSON log for monitoring
        supplier=supplier,
        source=str(path),
        format="csv",
        rows=df.height,
        input_bytes=source_bytes,
        output_bytes=output_bytes,
        duration=t.seconds,
        peak_rss_mb=peak_rss_mb(),
        parquet=str(parquet_path),
    )
    return parquet_path


def import_xlsx_to_bronze(supplier: str, path: Path) -> Path:
    """Import an XLSX file into the bronze layer via DuckDB conversion."""

    path = Path(path)
    source_bytes = path.stat().st_size
    date_str = datetime.utcnow().date().isoformat()
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_parquet = Path(tmpdir) / "converted.parquet"
        xlsx_to_parquet(path, temp_parquet)
        with timer("import_xlsx") as t:
            lf = pl.scan_parquet(temp_parquet)
            df = _prepare_lazy_frame(lf).collect(streaming=True)
            df = _normalise_frame(df)
            df = df.with_columns(pl.lit(supplier).alias("supplier"))
            parquet_path = save_parquet(df, supplier, date_str, path.stem)
    output_bytes = parquet_path.stat().st_size
    log_event(
        "import",
        supplier=supplier,
        source=str(path),
        format="xlsx",
        rows=df.height,
        input_bytes=source_bytes,
        output_bytes=output_bytes,
        duration=t.seconds,
        peak_rss_mb=peak_rss_mb(),
        parquet=str(parquet_path),
    )
    return parquet_path
