"""Utilities for timing and resource usage metrics."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

try:
    import psutil
except Exception:  # pragma: no cover - psutil might be unavailable
    psutil = None  # type: ignore

from .config import data_root

_METRICS_DIR = data_root() / "cache"
_METRICS_DIR.mkdir(parents=True, exist_ok=True)
_METRICS_FILE = _METRICS_DIR / "metrics.jsonl"


@dataclass
class TimedResult:
    """Container with timing result information."""

    name: str
    seconds: float = 0.0


@contextmanager
def timer(name: str) -> Iterator[TimedResult]:
    """Measure execution time inside a context manager."""

    start = time.perf_counter()
    result = TimedResult(name=name)
    try:
        yield result
    finally:
        result.seconds = time.perf_counter() - start
        log_event("timer", name=name, seconds=result.seconds)


def peak_rss_mb() -> float:
    """Return the resident set size peak in megabytes."""

    if psutil is None:
        return 0.0
    process = psutil.Process(os.getpid())
    info = process.memory_info()
    rss = getattr(info, "rss", 0)
    peak = getattr(info, "peak_wset", rss)
    return max(rss, peak) / (1024 * 1024)


def log_event(event: str, **payload: Any) -> None:
    """Append a JSON event to the metrics log and stdout."""

    record = {
        "timestamp": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
        "event": event,
        **payload,
    }
    line = json.dumps(record, ensure_ascii=False)
    print(line)
    with _METRICS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_recent_events(limit: int = 100) -> list[dict[str, Any]]:
    """Read the latest events from the metrics log."""

    if not _METRICS_FILE.exists():
        return []
    lines = _METRICS_FILE.read_text(encoding="utf-8").splitlines()
    events = []
    for line in lines[-limit:]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events
