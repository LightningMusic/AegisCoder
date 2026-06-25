"""
AegisCoder -- desktop UI shell (Phase 5).

This is the entry point when running as a desktop application.
It owns the window lifecycle and the engine child process.

Startup sequence (see master plan section 5.13):
  1. Spawn the FastAPI engine as a child subprocess
  2. Poll the /api/health endpoint until the engine is ready
  3. Open a pywebview window pointed at the engine's local URL
  4. When the window closes, terminate the engine child cleanly

The window is the only thing the user sees -- no terminal, no console.
The engine runs in the background and streams data to the window via
the local WebSocket.

In dev mode (scripts/Run-Dev.ps1) the engine is launched directly
via main.py and the window is not used -- the browser can be used
instead to iterate on the UI faster.
"""
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# ENGINE_PORT is read lazily inside launch() to avoid importing engine.config
# at module level (which would trigger module-level side effects)
_ENGINE_READY_TIMEOUT = 30
_ENGINE_POLL_INTERVAL = 0.5


def _find_main() -> str:
    """Return the path to main.py relative to this file's location."""
    here = Path(__file__).parent.parent
    main_py = here / "main.py"
    if not main_py.exists():
        raise FileNotFoundError(f"Cannot find main.py at {main_py}")
    return str(main_py)


def _spawn_engine() -> subprocess.Popen:
    """
    Launch the FastAPI engine in a separate process.
    Uses the same Python interpreter that is running this shell,
    which means it inherits the same venv and installed packages.
    """
    python = sys.executable
    main_py = _find_main()

    env = {**os.environ, "AEGISCODER_SUBPROCESS": "1"}

    log.info("Spawning engine: %s %s", python, main_py)
    proc = subprocess.Popen(
        [python, main_py, "--engine-only"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log.info("Engine process started (PID %d)", proc.pid)
    return proc


def _wait_for_engine(engine_url: str, timeout: int = _ENGINE_READY_TIMEOUT) -> bool:
    """Poll /api/health until the engine is responding or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{engine_url}/api/health", timeout=2.0)
            if r.status_code == 200:
                log.info("Engine ready at %s", engine_url)
                return True
        except Exception:
            pass
        time.sleep(_ENGINE_POLL_INTERVAL)
    log.error("Engine did not become ready within %ds", timeout)
    return False


def launch() -> None:
    """
    Main entry point for the desktop app.
    Spawns the engine, waits for it, opens the pywebview window.

    webview and engine.config are imported here (not at module top level)
    so that Pylance can always resolve this function even when pywebview
    is not yet installed, and to avoid module-level import side effects.
    """
    import webview  # pywebview -- installed by AegisCoderSetup.ps1
    from engine.config import ENGINE_PORT

    engine_url = f"http://127.0.0.1:{ENGINE_PORT}"

    engine_proc = _spawn_engine()

    try:
        ready = _wait_for_engine(engine_url)
        if not ready:
            # Show an error window rather than silently failing
            webview.create_window(
                "AegisCoder -- Startup Error",
                html=(
                    "<body style='background:#0d1117;color:#f85149;"
                    "font-family:sans-serif;padding:40px'>"
                    "<h2>Engine failed to start</h2>"
                    "<p>Check logs/engine.log for details.</p>"
                    "</body>"
                ),
                width=500,
                height=300,
            )
            webview.start()
            return

        window = webview.create_window(
            title="AegisCoder",
            url=engine_url,
            width=1280,
            height=800,
            min_size=(800, 600),
            # Allow the JS inside the window to call Python via window.pywebview.api
            # (unused in Phase 5, reserved for Phase 6 drag-drop project open)
        )

        log.info("Opening AegisCoder window")
        webview.start(debug=False)
        log.info("Window closed by user")

    finally:
        log.info("Terminating engine (PID %d)...", engine_proc.pid)
        try:
            engine_proc.terminate()
            engine_proc.wait(timeout=10)
            log.info("Engine terminated cleanly")
        except subprocess.TimeoutExpired:
            engine_proc.kill()
            log.warning("Engine killed (did not stop in time)")
        except Exception as exc:
            log.warning("Error stopping engine: %s", exc)