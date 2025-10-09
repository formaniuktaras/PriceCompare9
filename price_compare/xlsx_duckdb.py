"""Conversion helpers using DuckDB's excel extension."""

from __future__ import annotations

from pathlib import Path

import duckdb


def xlsx_to_parquet(xlsx_path: str | Path, out_parquet: str | Path, sheet: str | None = None) -> str:
    """Convert an XLSX sheet into a Parquet file using DuckDB."""

    xlsx_path = Path(xlsx_path)
    out_parquet = Path(out_parquet)
    out_parquet.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    try:
        con.execute("INSTALL excel; LOAD excel;")
        sheet_expr = f", sheet='{sheet}'" if sheet else ""
        con.execute(
            f"""
            COPY (
                SELECT * FROM read_excel('{xlsx_path.as_posix()}'{sheet_expr}, open_mode='ro')
            ) TO '{out_parquet.as_posix()}' (FORMAT 'parquet', COMPRESSION 'ZSTD');
            """
        )
    finally:
        con.close()
    return out_parquet.as_posix()
