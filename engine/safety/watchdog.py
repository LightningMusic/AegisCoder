"""
AegisCoder -- Ollama health watchdog.

Runs as a background daemon thread and periodically pings Ollama.
If Ollama stops responding, the watchdog attempts to restart it.
The engine continues running during the restart attempt and resumes
normally once Ollama is back. If Ollama cannot be restarted after
MAX_RESTART_ATTEMPTS the watchdog gives up and logs an error --
the user will see Ollama errors on their next prompt rather than
a silent hang.

This is separate from the per-call timeout in aider_bridge.py, which
handles the case where Ollama is running but a specific call is hanging.
The watchdog handles the case where Ollama has crashed entirely.

See master plan section 5.6.
"""
import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

# How often (in seconds) to ping Ollama
HEALTH_CHECK_INTERVAL = 15

# Seconds to wait between restart attempts
RESTART_COOLDOWN = 10

# Maximum number of consecutive restart attempts before giving up
MAX_RESTART_ATTEMPTS = 3


class OllamaWatchdog:
    """
    Background thread that monitors Ollama and restarts it if needed.

    Usage:
        watchdog = OllamaWatchdog(is_running_fn, restart_fn)
        watchdog.start()
        ...
        watchdog.stop()
    """

    def __init__(
        self,
        is_running_fn: Callable[[], bool],
        restart_fn: Callable[[], bool],
        on_down: Callable[[], None] | None = None,
        on_recovered: Callable[[], None] | None = None,
    ):
        """
        Args:
            is_running_fn:  Returns True if Ollama is healthy.
            restart_fn:     Tries to restart Ollama. Returns True if successful.
            on_down:        Optional callback fired when Ollama is detected down.
            on_recovered:   Optional callback fired when Ollama comes back up.
        """
        self._is_running = is_running_fn
        self._restart = restart_fn
        self._on_down = on_down
        self._on_recovered = on_recovered

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._consecutive_failures = 0
        self._ollama_was_down = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self):
        """Start the watchdog thread. Safe to call multiple times."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="aegis-watchdog",
        )
        self._thread.start()
        log.info("Ollama watchdog started (interval=%ds)", HEALTH_CHECK_INTERVAL)

    def stop(self):
        """Stop the watchdog thread. Blocks until it exits."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        log.info("Ollama watchdog stopped")

    @property
    def is_healthy(self) -> bool:
        """True if Ollama was alive on the last check."""
        return self._consecutive_failures == 0

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _loop(self):
        while not self._stop_event.is_set():
            self._check()
            self._stop_event.wait(timeout=HEALTH_CHECK_INTERVAL)

    def _check(self):
        if self._is_running():
            if self._ollama_was_down:
                log.info("Ollama recovered")
                self._consecutive_failures = 0
                self._ollama_was_down = False
                if self._on_recovered:
                    try:
                        self._on_recovered()
                    except Exception:
                        pass
            return

        # Ollama is not responding
        self._consecutive_failures += 1
        log.warning(
            "Ollama health check failed (consecutive failures: %d)",
            self._consecutive_failures,
        )

        if not self._ollama_was_down:
            self._ollama_was_down = True
            if self._on_down:
                try:
                    self._on_down()
                except Exception:
                    pass

        if self._consecutive_failures > MAX_RESTART_ATTEMPTS:
            log.error(
                "Ollama did not recover after %d restart attempts. "
                "Manual intervention required.",
                MAX_RESTART_ATTEMPTS,
            )
            return

        log.info(
            "Attempting Ollama restart (attempt %d/%d)...",
            self._consecutive_failures,
            MAX_RESTART_ATTEMPTS,
        )

        time.sleep(RESTART_COOLDOWN)

        try:
            success = self._restart()
            if success:
                log.info("Ollama restarted successfully")
                self._consecutive_failures = 0
                self._ollama_was_down = False
                if self._on_recovered:
                    try:
                        self._on_recovered()
                    except Exception:
                        pass
            else:
                log.warning("Ollama restart attempt failed")
        except Exception as exc:
            log.exception("Exception during Ollama restart: %s", exc)