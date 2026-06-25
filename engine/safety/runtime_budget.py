"""
AegisCoder -- Unattended runtime budget.

Tracks the last time the user interacted with the engine and pauses
Auto-Run sessions after RUNTIME_BUDGET_HOURS of no user activity.

This is the mechanism that prevents the overnight runaway that drifted
the system clock. The app will not run all night on its own -- it parks
itself and waits for you to come back.

See master plan section 5.7.

How it works:
  - Every time a prompt is received from the UI, the budget is reset via
    record_activity().
  - A background thread checks every minute whether the budget has expired.
  - If it has, on_budget_exceeded() is called and Auto-Run is suspended.
  - Calling record_activity() at any point resumes normal operation.
  - Manual mode (user approving each step) is never paused -- this only
    applies to Auto-Run.
"""
import logging
import threading
import time
from typing import Callable

from engine.config import RUNTIME_BUDGET_HOURS

log = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60


class RuntimeBudget:
    """
    Tracks user activity and fires a callback when the budget is exceeded.

    Usage:
        budget = RuntimeBudget(on_exceeded=lambda: print("pausing"))
        budget.start()
        budget.record_activity()  # call every time user sends a prompt
        ...
        budget.stop()
    """

    def __init__(self, on_exceeded: Callable[[], None] | None = None):
        self._on_exceeded = on_exceeded
        self._last_activity = time.monotonic()
        self._exceeded = False
        self._paused = False          # True while budget is exceeded
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self):
        """Start the background budget checker."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="aegis-runtime-budget",
        )
        self._thread.start()
        log.info(
            "Runtime budget started: auto-pause after %.1f hour(s) of inactivity",
            RUNTIME_BUDGET_HOURS,
        )

    def stop(self):
        """Stop the background checker."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def record_activity(self):
        """
        Call this whenever the user sends a prompt or interacts with the app.
        Resets the budget timer and resumes if previously paused.
        """
        with self._lock:
            self._last_activity = time.monotonic()
            if self._paused:
                log.info("User activity detected -- runtime budget reset, resuming")
                self._paused = False
                self._exceeded = False

    @property
    def is_paused(self) -> bool:
        """True if Auto-Run is currently suspended due to budget expiry."""
        with self._lock:
            return self._paused

    @property
    def seconds_since_activity(self) -> float:
        with self._lock:
            return time.monotonic() - self._last_activity

    @property
    def budget_seconds(self) -> float:
        return RUNTIME_BUDGET_HOURS * 3600

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.budget_seconds - self.seconds_since_activity)

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _loop(self):
        while not self._stop_event.is_set():
            self._check()
            self._stop_event.wait(timeout=CHECK_INTERVAL_SECONDS)

    def _check(self):
        with self._lock:
            elapsed = time.monotonic() - self._last_activity
            budget = RUNTIME_BUDGET_HOURS * 3600

            if elapsed >= budget and not self._paused:
                self._paused = True
                self._exceeded = True
                log.warning(
                    "Runtime budget exceeded (%.1f hours of inactivity). "
                    "Auto-Run suspended. Waiting for user activity to resume.",
                    elapsed / 3600,
                )
                if self._on_exceeded:
                    try:
                        self._on_exceeded()
                    except Exception as exc:
                        log.warning("on_exceeded callback raised: %s", exc)