"""Command line entry-point for the ETL pipeline."""

from __future__ import annotations

import argparse
import multiprocessing as mp
from typing import Iterable

from .config import AppConfig, load_config
from .gold_build import build_gold_summary
from .metrics import read_recent_events
from .queue_import import run_import_batch
from .silver_build import build_silver


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pc", description="Price comparison ETL")
    parser.add_argument("--config", default=None, help="Path to config.yaml")

    sub = parser.add_subparsers(dest="command", required=True)

    import_parser = sub.add_parser("import", help="Import supplier files into bronze")
    import_parser.add_argument(
        "--max-workers", type=int, default=None, help="Override parallel worker count"
    )

    sub.add_parser("build-silver", help="Build the silver layer")
    sub.add_parser("build-gold", help="Build the gold aggregates")

    status_parser = sub.add_parser("status", help="Show recent metrics events")
    status_parser.add_argument("--limit", type=int, default=20, help="Number of events to show")

    return parser


def _load_config(path: str | None) -> AppConfig:
    return load_config(path)


def _run_import(cfg: AppConfig, max_workers: int | None) -> None:
    jobs = list(cfg.iter_supplier_jobs())
    results = run_import_batch(jobs, max_workers=max_workers)
    events = read_recent_events(limit=10)
    batch_event = next((e for e in reversed(events) if e.get("event") == "import_batch"), {})
    processed = batch_event.get("processed", len(results))
    skipped = batch_event.get("skipped", 0)
    print(
        f"Imported {processed} file(s); skipped {skipped}; new parquet files: {len(results)}"
    )


def _run_build_silver() -> None:
    out_path = build_silver(None)
    print(f"Silver layer written to {out_path}")


def _run_build_gold() -> None:
    out_path = build_gold_summary()
    print(f"Gold summary written to {out_path}")


def _run_status(limit: int) -> None:
    events = read_recent_events(limit=limit)
    if not events:
        print("No metrics recorded yet.")
        return
    for event in events:
        print(event)


def main(argv: Iterable[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    cfg = _load_config(args.config)

    if args.command == "import":
        _run_import(cfg, args.max_workers)
    elif args.command == "build-silver":
        _run_build_silver()
    elif args.command == "build-gold":
        _run_build_gold()
    elif args.command == "status":
        _run_status(args.limit)
    else:  # pragma: no cover - argparse ensures command
        parser.error(f"Unknown command {args.command}")


if __name__ == "__main__":
    mp.freeze_support()
    main()
