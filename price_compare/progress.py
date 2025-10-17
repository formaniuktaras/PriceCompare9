"""Thread-safe progress tracking utilities for long-running operations."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Optional


class OperationCancelledError(RuntimeError):
    """Raised when a tracked operation is cancelled by the user."""


@dataclass(frozen=True)
class ProgressSnapshot:
    """Immutable snapshot of the current progress state."""

    done: int
    total: int
    left: int
    fraction: float
    is_paused: bool
    is_cancelled: bool
    is_finished: bool
    ips: float
    eta_seconds: Optional[float]


class ProgressTracker:
    """Tracks progress for background operations with pause/cancel support."""

    def __init__(self, total: int = 0) -> None:
        self._lock = threading.Lock()
        self._total = max(int(total), 0)
        self._done = 0
        self._start_time = time.monotonic()
        self._paused_since: float | None = None
        self._paused_duration = 0.0
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._cancelled = False
        self._finished = False
        self._last_sample_time = self._start_time
        self._last_sample_done = 0
        self._smoothed_ips = 0.0

    # ------------------------------------------------------------------
    # Control methods
    # ------------------------------------------------------------------
    def reset(self, total: int = 0) -> None:
        """Reset the tracker to a new total and restart timing."""

        with self._lock:
            self._total = max(int(total), 0)
            self._done = 0
            self._start_time = time.monotonic()
            self._paused_since = None
            self._paused_duration = 0.0
            self._finished = False
            self._cancelled = False
            self._pause_event.set()
            self._last_sample_time = self._start_time
            self._last_sample_done = 0
            self._smoothed_ips = 0.0

    def set_total(self, total: int) -> None:
        with self._lock:
            self._total = max(int(total), 0)
            if self._done > self._total:
                self._done = self._total

    def add_total(self, amount: int) -> None:
        if not amount:
            return
        with self._lock:
            self._total = max(self._total + int(amount), 0)
            if self._done > self._total:
                self._done = self._total

    def advance(self, amount: int = 1) -> None:
        """Advance progress by ``amount`` units, respecting pause/cancel."""

        if amount <= 0:
            self.wait_if_paused()
            self.raise_if_cancelled()
            return
        self.wait_if_paused()
        self.raise_if_cancelled()
        with self._lock:
            self._done += amount
            if self._total and self._done > self._total:
                self._done = self._total

    def wait_if_paused(self) -> None:
        """Block while paused, waking promptly when resumed or cancelled."""

        while True:
            if self._cancelled:
                raise OperationCancelledError()
            if self._pause_event.wait(timeout=0.1):
                break

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise OperationCancelledError()

    def toggle_pause(self) -> None:
        with self._lock:
            if self._cancelled or self._finished:
                return
            if self._pause_event.is_set():
                self._pause_event.clear()
                self._paused_since = time.monotonic()
            else:
                self._pause_event.set()
                if self._paused_since is not None:
                    self._paused_duration += time.monotonic() - self._paused_since
                self._paused_since = None

    def resume(self) -> None:
        with self._lock:
            if not self._pause_event.is_set():
                self._pause_event.set()
                if self._paused_since is not None:
                    self._paused_duration += time.monotonic() - self._paused_since
                self._paused_since = None

    def cancel(self) -> None:
        with self._lock:
            if self._cancelled or self._finished:
                return
            self._cancelled = True
            self._pause_event.set()

    def mark_finished(self) -> None:
        with self._lock:
            self._finished = True
            if self._total and self._done < self._total:
                self._done = self._total

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def snapshot(self) -> ProgressSnapshot:
        with self._lock:
            done = max(self._done, 0)
            total = max(self._total, 0)
            finished = self._finished or (total > 0 and done >= total)
            cancelled = self._cancelled
            paused = not self._pause_event.is_set()
            paused_since = self._paused_since
            paused_duration = self._paused_duration
            start = self._start_time
            last_sample_time = self._last_sample_time
            last_sample_done = self._last_sample_done
            smoothed_ips = self._smoothed_ips
            now = time.monotonic()

            if paused and paused_since is not None:
                effective_paused = paused_duration + (now - paused_since)
            else:
                effective_paused = paused_duration
            elapsed = max(now - start - effective_paused, 0.0)

            delta_time = max(now - last_sample_time, 1e-9)
            delta_done = max(done - last_sample_done, 0)
            instant_ips = (delta_done / delta_time) if delta_time > 0 else 0.0

            if instant_ips > 0.0:
                if smoothed_ips <= 0.0:
                    smoothed_ips = instant_ips
                else:
                    smoothing = 0.35
                    smoothed_ips = (smoothing * instant_ips) + ((1.0 - smoothing) * smoothed_ips)
            elif now - last_sample_time > 1.0:
                smoothed_ips = 0.0

            self._last_sample_time = now
            self._last_sample_done = done
            self._smoothed_ips = smoothed_ips

            ips = smoothed_ips if smoothed_ips > 0.0 else ((done / elapsed) if elapsed > 0 else 0.0)
            left = max(total - done, 0)
            eta_seconds: Optional[float]
            if finished or ips <= 0:
                eta_seconds = None
            else:
                eta_seconds = left / ips if total else None

            fraction = (done / total) if total > 0 else (1.0 if finished else 0.0)
            if fraction > 1.0:
                fraction = 1.0

            return ProgressSnapshot(
                done=done,
                total=total,
                left=left,
                fraction=fraction,
                is_paused=paused,
                is_cancelled=cancelled,
                is_finished=finished,
                ips=ips,
                eta_seconds=eta_seconds,
            )

    # Convenience properties -------------------------------------------------
    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def finished(self) -> bool:
        return self._finished

