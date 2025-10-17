"""Tkinter window helpers for displaying determinate progress."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, TypeVar

import tkinter as tk
from tkinter import ttk

from .progress import OperationCancelledError, ProgressTracker

T = TypeVar("T")


def _default_master(master: tk.Misc | None) -> tk.Misc:
    resolved = master or tk._get_default_root()  # type: ignore[attr-defined]
    if resolved is None:
        raise RuntimeError("Tkinter root window is not initialized")
    return resolved


def _format_eta(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    remaining = max(int(seconds), 0)
    if remaining < 1:
        return "<1 с"
    minutes, secs = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} год")
    if minutes:
        parts.append(f"{minutes} хв")
    if secs or not parts:
        parts.append(f"{secs} с")
    return " ".join(parts)


def _format_speed(ips: float) -> str:
    if ips <= 0:
        return "0"
    if ips >= 100:
        return f"{ips:.0f}"
    if ips >= 10:
        return f"{ips:.1f}"
    if ips >= 1:
        return f"{ips:.2f}"
    return f"{ips:.3f}"


class ProgressWindow(tk.Toplevel):
    """A modeless determinate progress dialog with pause/cancel controls."""

    def __init__(
        self,
        master: tk.Misc,
        tracker: ProgressTracker,
        *,
        title: str,
        poll_interval_ms: int = 350,
    ) -> None:
        super().__init__(master)
        windowing_system = self.tk.call("tk", "windowingsystem")
        if windowing_system != "win32":
            self.transient(master)
        else:
            try:
                self.wm_attributes("-topmost", True)
                self.after(200, lambda: self.wm_attributes("-topmost", False))
            except tk.TclError:
                pass
        self.title(title)
        self.resizable(False, False)
        self._tracker = tracker
        self._poll_interval = poll_interval_ms
        self._after_id: str | None = None
        self._closing = False

        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        self.status_var = tk.StringVar(value="Опрацьовано 0/0. Залишилось 0. ETA: — ~0 ел/с")
        status_label = ttk.Label(
            container,
            textvariable=self.status_var,
            justify=tk.LEFT,
            width=76,
            anchor=tk.W,
        )
        status_label.pack(fill=tk.X)

        self.progress = ttk.Progressbar(
            container,
            mode="determinate",
            length=320,
            maximum=100,
        )
        self.progress.pack(fill=tk.X, pady=(12, 8))

        buttons = ttk.Frame(container)
        buttons.pack(fill=tk.X)

        self._pause_text = tk.StringVar(value="Пауза")
        self.pause_button = ttk.Button(
            buttons,
            textvariable=self._pause_text,
            command=self._toggle_pause,
            width=16,
        )
        self.pause_button.grid(row=0, column=0, padx=(0, 6))

        self.cancel_button = ttk.Button(
            buttons,
            text="Скасувати",
            command=self._cancel,
            width=16,
        )
        self.cancel_button.grid(row=0, column=1, padx=(0, 6))

        ttk.Button(buttons, text="Згорнути", command=self._minimize, width=16).grid(
            row=0, column=2
        )

        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        buttons.columnconfigure(2, weight=1)

        self.protocol("WM_DELETE_WINDOW", self._minimize)

        self.update_idletasks()
        try:
            master.update_idletasks()
            width = self.winfo_width()
            height = self.winfo_height()
            master_width = master.winfo_width() or width
            master_height = master.winfo_height() or height
            x = master.winfo_rootx() + max((master_width - width) // 2, 0)
            y = master.winfo_rooty() + max((master_height - height) // 2, 0)
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

        self._refresh()

    # ------------------------------------------------------------------
    # UI actions
    # ------------------------------------------------------------------
    def _toggle_pause(self) -> None:
        self._tracker.toggle_pause()
        snapshot = self._tracker.snapshot()
        self._pause_text.set("Продовжити" if snapshot.is_paused else "Пауза")

    def _cancel(self) -> None:
        self._tracker.cancel()
        self.pause_button.state(["disabled"])
        self.cancel_button.state(["disabled"])

    def _minimize(self) -> None:
        try:
            self.wm_state("iconic")
        except tk.TclError:
            try:
                self.withdraw()
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    def _schedule_refresh(self) -> None:
        if self._closing:
            return
        self._after_id = self.after(self._poll_interval, self._refresh)

    def _refresh(self) -> None:
        self._after_id = None
        try:
            snapshot = self._tracker.snapshot()
        except Exception:
            return

        self.progress["value"] = min(max(snapshot.fraction * 100, 0.0), 100.0)
        eta_text = _format_eta(snapshot.eta_seconds)
        ips_text = _format_speed(snapshot.ips)
        base_text = (
            f"Опрацьовано {snapshot.done}/{snapshot.total}. "
            f"Залишилось {snapshot.left}. ETA: {eta_text} ~{ips_text} ел/с"
        )
        if snapshot.is_cancelled:
            base_text = "Скасовано. " + base_text
        self.status_var.set(base_text)

        if snapshot.is_paused:
            self._pause_text.set("Продовжити")
        else:
            self._pause_text.set("Пауза")

        if snapshot.is_finished or snapshot.is_cancelled:
            self.pause_button.state(["disabled"])
            self.cancel_button.state(["disabled"])
        elif snapshot.is_paused:
            self.pause_button.state(["!disabled"])
            self.cancel_button.state(["!disabled"])

        if not self._closing and not snapshot.is_finished and not snapshot.is_cancelled:
            self._schedule_refresh()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        try:
            self.destroy()
        except tk.TclError:
            pass


def init_window(
    tracker: ProgressTracker,
    *,
    master: tk.Misc | None = None,
    title: str = "Прогрес",
    poll_interval_ms: int = 350,
) -> ProgressWindow:
    """Initialize and return a progress window bound to ``tracker``."""

    resolved_master = _default_master(master)
    window = ProgressWindow(
        resolved_master,
        tracker,
        title=title,
        poll_interval_ms=poll_interval_ms,
    )
    return window


def run_with_worker_pool(
    *,
    master: tk.Misc,
    title: str,
    tracker: ProgressTracker,
    job: Callable[[ThreadPoolExecutor, ProgressTracker], T],
    on_success: Callable[[T], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    on_cancel: Callable[[], None] | None = None,
    max_workers: int | None = None,
    poll_interval_ms: int = 350,
) -> None:
    """Run ``job`` in background threads while updating a progress window."""

    window = init_window(
        tracker,
        master=master,
        title=title,
        poll_interval_ms=poll_interval_ms,
    )

    worker_count = max_workers or max(1, os.cpu_count() or 1)

    def _finalize(callback: Optional[Callable[[], None]], *, mark_finished: bool) -> None:
        if mark_finished:
            try:
                tracker.mark_finished()
            except Exception:
                pass
        try:
            window.close()
        finally:
            if callback:
                callback()

    def _handle_success(result: T) -> None:
        def _callback() -> None:
            if on_success:
                on_success(result)

        _finalize(_callback, mark_finished=True)

    def _handle_error(exc: Exception) -> None:
        def _callback() -> None:
            if on_error:
                on_error(exc)

        _finalize(_callback, mark_finished=False)

    def _handle_cancel() -> None:
        def _callback() -> None:
            if on_cancel:
                on_cancel()

        _finalize(_callback, mark_finished=False)

    def _worker_thread() -> None:
        try:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                result = job(executor, tracker)
        except OperationCancelledError:
            master.after(0, _handle_cancel)
        except Exception as exc:  # pragma: no cover - background thread
            master.after(0, lambda: _handle_error(exc))
        else:
            master.after(0, lambda: _handle_success(result))

    thread = threading.Thread(target=_worker_thread, daemon=True)
    thread.start()

