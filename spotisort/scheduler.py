"""A single background worker that owns every run.

This replaces the old cron setup. One thread means runs can never overlap (the
crontab fired every minute regardless of how long a sort took), the interval is
configurable at runtime, output lands on the container's stdout, and the next run
time is a value we can actually show in the UI.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, job: Callable[[], Dict[str, Any]], interval_minutes: int,
                 on_finish: Optional[Callable[[Dict[str, Any]], None]] = None):
        self._job = job
        self._on_finish = on_finish
        self._interval = max(1, interval_minutes) * 60
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._next_run = 0.0
        self._running = False
        self._run_requested = False
        self.last_run: Optional[Dict[str, Any]] = None

    # -- control -----------------------------------------------------------

    def start(self, run_now: bool = False) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._next_run = time.time() + (0 if run_now else self._interval)
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger(self) -> bool:
        """Ask for a run as soon as the worker is free. False if one is in flight."""
        with self._lock:
            if self._running:
                return False
            self._run_requested = True
        self._wake.set()
        return True

    def set_interval(self, minutes: int) -> None:
        with self._lock:
            self._interval = max(1, minutes) * 60
            self._next_run = time.time() + self._interval
        self._wake.set()

    def reschedule_now(self) -> None:
        """Push the next run one full interval out from this moment."""
        with self._lock:
            self._next_run = time.time() + self._interval
        self._wake.set()

    # -- state -------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def next_run_at(self) -> float:
        return self._next_run

    @property
    def interval_minutes(self) -> int:
        return int(self._interval // 60)

    # -- worker ------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                requested = self._run_requested
                delay = 0.0 if requested else max(0.0, self._next_run - time.time())

            if delay > 0:
                # Event.wait blocks instead of spinning; the old entrypoint's
                # `while True` with no sleep pegged a core forever.
                self._wake.wait(timeout=min(delay, 30.0))
                self._wake.clear()
                continue
            self._wake.clear()

            with self._lock:
                self._run_requested = False
                self._running = True
            try:
                result = self._job()
                self.last_run = result
                if self._on_finish:
                    self._on_finish(result)
            except Exception:  # noqa: BLE001 - a failed run must not kill the worker
                log.exception("scheduled run failed")
                self.last_run = {
                    "started_at": time.time(),
                    "finished_at": time.time(),
                    "duration": 0,
                    "moves": 0,
                    "ok": False,
                    "playlists": [],
                    "error": "run failed, see container logs",
                }
            finally:
                with self._lock:
                    self._running = False
                    self._next_run = time.time() + self._interval
