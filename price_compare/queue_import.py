"""Import queue handling with caching and threading."""

from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

try:
    from xxhash import xxh3_64  # type: ignore
except ImportError as exc:  # pragma: no cover - dependency issue
    raise RuntimeError("xxhash package is required for content hashing") from exc

from .config import data_root
from .import_worker import import_csv_to_bronze, import_xlsx_to_bronze
from .metrics import log_event

_CACHE_DIR = data_root() / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_PATH = _CACHE_DIR / "imports.sqlite"


@dataclass
class JobMetadata:
    """Metadata required to determine cache hits."""

    supplier: str
    path: Path
    size: int
    mtime: float
    content_hash: str


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS imports (
            supplier TEXT NOT NULL,
            path TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            content_hash TEXT NOT NULL,
            parquet_path TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            PRIMARY KEY (supplier, path)
        )
        """
    )
    conn.commit()


def _hash_file(path: Path, block_size: int = 1_048_576) -> str:
    hasher = xxh3_64()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(block_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _collect_metadata(supplier: str, path: Path) -> JobMetadata:
    stat = path.stat()
    return JobMetadata(
        supplier=supplier,
        path=path,
        size=stat.st_size,
        mtime=stat.st_mtime,
        content_hash=_hash_file(path),
    )


def _should_skip(conn: sqlite3.Connection, meta: JobMetadata) -> bool:
    cur = conn.execute(
        """
        SELECT size, mtime, content_hash FROM imports
        WHERE supplier = ? AND path = ?
        """,
        (meta.supplier, str(meta.path)),
    )
    row = cur.fetchone()
    if not row:
        return False
    size, mtime, content_hash = row
    return size == meta.size and mtime == meta.mtime and content_hash == meta.content_hash


def _register_result(conn: sqlite3.Connection, meta: JobMetadata, parquet_path: Path) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO imports (
            supplier, path, size, mtime, content_hash, parquet_path, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            meta.supplier,
            str(meta.path),
            meta.size,
            meta.mtime,
            meta.content_hash,
            str(parquet_path),
            datetime.utcnow().isoformat(timespec="seconds") + "Z",
        ),
    )
    conn.commit()


def _import_job(meta: JobMetadata) -> Path:
    suffix = meta.path.suffix.lower()
    if suffix == ".csv":
        return import_csv_to_bronze(meta.supplier, meta.path)
    if suffix in {".xlsx", ".xlsm"}:
        return import_xlsx_to_bronze(meta.supplier, meta.path)
    raise ValueError(f"Unsupported file type: {meta.path.suffix}")


def run_import_batch(jobs: Iterable[tuple[str, Path]], max_workers: int | None = None) -> list[Path]:
    """Run an import batch for the provided supplier jobs."""

    paths = list(jobs)
    if not paths:
        return []
    conn = sqlite3.connect(_CACHE_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    _ensure_schema(conn)

    try:
        to_process: list[JobMetadata] = []
        skipped = 0
        for supplier, file_path in paths:
            file_path = Path(file_path)
            if not file_path.exists():
                log_event("import_error", supplier=supplier, path=str(file_path), reason="missing")
                continue
            meta = _collect_metadata(supplier, file_path)
            if _should_skip(conn, meta):
                skipped += 1
                log_event("import_skip", supplier=supplier, path=str(file_path), reason="cache_hit")
                continue
            to_process.append(meta)

        if not to_process:
            log_event("import_batch", status="complete", processed=0, skipped=skipped)
            return []

        max_workers = max_workers or min(8, (os.cpu_count() or 1) * 2)
        results: list[Path] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_import_job, meta): meta for meta in to_process}
            for future in as_completed(futures):
                meta = futures[future]
                try:
                    parquet_path = future.result()
                except Exception as exc:  # pragma: no cover - runtime error
                    log_event(
                        "import_error",
                        supplier=meta.supplier,
                        path=str(meta.path),
                        reason=str(exc),
                    )
                    continue
                _register_result(conn, meta, parquet_path)
                results.append(parquet_path)

        log_event(
            "import_batch",
            status="complete",
            processed=len(results),
            skipped=skipped,
        )
        return results
    finally:
        conn.close()
