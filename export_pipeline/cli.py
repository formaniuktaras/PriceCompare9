"""Command line interface for the export pipeline."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from price_compare.config import AppConfig, load_config

from .channels import staging_to_prom
from .sheets import ensure_sheet, write_df
from .staging import build_staging_df
from .writers import to_csv, to_priceua_xml, to_xlsx

LOGGER = logging.getLogger(__name__)
DEFAULT_CHANNEL = "prom"
EXPORT_SHEET = "Export_Staging"
PROM_SHEET = "Export_Prom"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export normalized product data")
    parser.add_argument("--config", default=None, help="Path to config.yaml")

    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Export data for a channel")
    export_parser.add_argument(
        "--channel",
        choices=[DEFAULT_CHANNEL],
        required=True,
        help="Export channel to generate",
    )
    export_parser.add_argument(
        "--format",
        choices=["csv", "xlsx", "xml"],
        nargs="+",
        required=True,
        help="Output format(s) to generate",
    )
    export_parser.add_argument(
        "--selling-type",
        default="r",
        help="Selling type value for Price.ua XML (default: r)",
    )

    return parser


def _resolve_formats(raw_formats: Iterable[str]) -> list[str]:
    formats = []
    for fmt in raw_formats:
        if fmt not in {"csv", "xlsx", "xml"}:
            raise ValueError(f"Unsupported format {fmt}")
        formats.append(fmt)
    return formats


def _candidate_raw_sources(cfg: AppConfig) -> list[Path]:
    project_root = Path(__file__).resolve().parent.parent
    candidates: list[Path] = []
    csv_candidate = cfg.data_root / "export_raw.csv"
    parquet_candidate = cfg.data_root / "export_raw.parquet"
    candidates.extend([parquet_candidate, csv_candidate])
    candidates.append(project_root / "tests" / "data" / "sample_raw.csv")
    return candidates


def get_raw_df(cfg: AppConfig) -> pd.DataFrame:
    """Return the raw dataframe used to populate staging."""

    for path in _candidate_raw_sources(cfg):
        if not path.exists():
            continue
        if path.suffix == ".parquet":
            try:
                return pd.read_parquet(path)
            except Exception as exc:  # pragma: no cover - optional dependency
                LOGGER.warning("Failed to read parquet source %s: %s", path, exc)
                continue
        if path.suffix in {".csv", ".tsv"}:
            return pd.read_csv(path)
    raise FileNotFoundError(
        "Unable to locate raw export dataset. Provide export_raw.csv under data_root "
        "or configure get_raw_df to pull from your pipeline."
    )


def _filter_excluded(staging_df: pd.DataFrame) -> pd.DataFrame:
    if "channel_exclude" not in staging_df.columns:
        return staging_df
    mask = staging_df["channel_exclude"].fillna(False)
    if mask.dtype == object:
        mask = mask.astype(str).str.lower().isin({"1", "true", "yes"})
    return staging_df.loc[~mask].reset_index(drop=True)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


def _export_paths(output_dir: Path, channel: str, formats: Iterable[str]) -> dict[str, Path]:
    ts = _timestamp()
    paths: dict[str, Path] = {}
    for fmt in formats:
        if fmt in {"csv", "xlsx"}:
            paths[fmt] = output_dir / f"{channel}_export_{ts}.{fmt}"
        elif fmt == "xml":
            paths[fmt] = output_dir / f"priceua_{ts}.xml"
    return paths


def _write_formats(prom_df: pd.DataFrame, paths: dict[str, Path], selling_type: str) -> list[Path]:
    created: list[Path] = []
    for fmt, path in paths.items():
        if fmt == "csv":
            to_csv(prom_df, path)
        elif fmt == "xlsx":
            to_xlsx(prom_df, path)
        elif fmt == "xml":
            to_priceua_xml(prom_df, path, selling_type=selling_type)
        else:  # pragma: no cover - guarded by parser
            raise ValueError(f"Unsupported format {fmt}")
        created.append(path.resolve())
    return created


def _update_sheets(cfg: AppConfig, staging_df: pd.DataFrame, prom_df: pd.DataFrame) -> None:
    if not cfg.sheets:
        LOGGER.info("Sheets configuration missing; skipping Google Sheets updates")
        return

    spreadsheet_id = cfg.sheets.spreadsheet_id
    ensure_sheet(spreadsheet_id, EXPORT_SHEET, list(staging_df.columns))
    write_df(spreadsheet_id, EXPORT_SHEET, staging_df)

    ensure_sheet(spreadsheet_id, PROM_SHEET, list(prom_df.columns))
    write_df(spreadsheet_id, PROM_SHEET, prom_df)


def _run_export(cfg: AppConfig, channel: str, formats: Iterable[str], selling_type: str) -> list[Path]:
    if channel != DEFAULT_CHANNEL:
        raise ValueError(f"Unsupported channel {channel}")

    raw_df = get_raw_df(cfg)
    staging_df = build_staging_df(raw_df)
    staging_df = _filter_excluded(staging_df)

    prom_df = staging_to_prom(staging_df)

    _update_sheets(cfg, staging_df, prom_df)

    output_dir = cfg.exports.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = _export_paths(output_dir, channel, formats)
    created = _write_formats(prom_df, paths, selling_type)
    return created


def main(argv: Iterable[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)

    cfg = load_config(args.config)

    if args.command == "export":
        formats = _resolve_formats(args.format)
        created = _run_export(cfg, args.channel, formats, args.selling_type)
        for path in created:
            print(path)
    else:  # pragma: no cover - argparse ensures command
        parser.error(f"Unknown command {args.command}")


if __name__ == "__main__":
    main()
