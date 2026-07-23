"""
AegisCoder Ollama manager.

Responsible for:
  - Checking if Ollama is already running (health ping)
  - Starting the Ollama server if it is not running, with resource
    constraints applied at launch -- not bolted on after the fact
  - Waiting for Ollama to become ready before the engine proceeds
  - Providing a health check the engine can call periodically

Resource constraints applied at startup (see master plan section 5.5):
  OLLAMA_NUM_PARALLEL=1        -- one inference at a time, no queuing
  OLLAMA_MAX_LOADED_MODELS=1   -- one model in VRAM/RAM at a time
  num_thread in OLLAMA_HOST env -- capped via the Modelfile, not here
  CREATE_NO_WINDOW              -- no orphan console window on Windows
"""
import logging
import os
import subprocess
import time

import httpx

from engine.config import (
    OLLAMA_API_BASE,
    OLLAMA_STARTUP_TIMEOUT_SECONDS,
)

log = logging.getLogger(__name__)

# Holds the Popen handle if we started Ollama ourselves.
# None if it was already running when the engine launched.
_ollama_proc: subprocess.Popen | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_generation_healthy() -> bool:
    """Check a real generation only when no app session is using Ollama."""
    try:
        # Avoid a false watchdog failure when OLLAMA_NUM_PARALLEL=1 is busy.
        from engine.aider_bridge import get_active_generation_count
        if get_active_generation_count() > 0:
            log.info("Skipping deep Ollama health probe while generation is active")
            return is_running()
        response = httpx.post(
            f"{OLLAMA_API_BASE}/api/generate",
            json={"model": os.getenv("MODEL_NAME", "local-code:7b"), "prompt": "ping", "stream": False,
                  "options": {"num_predict": 1}},
            timeout=5.0,
        )
        healthy = response.status_code == 200
        if not healthy:
            log.warning("Deep Ollama health check returned HTTP %s", response.status_code)
        return healthy
    except Exception as exc:
        log.warning("Deep Ollama health check failed: %s", exc)
        return False

def is_running() -> bool:
    """
    Returns True if the Ollama API is responding to health checks.
    Safe to call frequently -- uses a short timeout so it never blocks.
    """
    try:
        r = httpx.get(f"{OLLAMA_API_BASE}/api/tags", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def start() -> bool:
    """
    Start the Ollama server as a background process with resource constraints.
    Returns True if the server was started successfully, False on failure.
    Does nothing and returns True if Ollama is already running.
    """
    global _ollama_proc

    if is_running():
        log.info("Ollama already running -- skipping start")
        return True

    log.info("Starting Ollama with resource constraints...")

    env = {
        **os.environ,
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        # OLLAMA_HOST is already set from the existing setup script
    }

    try:
        _ollama_proc = subprocess.Popen(
            ["ollama", "serve"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Windows: do not create a visible console window
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        log.info("Ollama started (PID %d)", _ollama_proc.pid)
        return True
    except FileNotFoundError:
        log.error(
            "ollama executable not found on PATH. "
            "Ensure the setup script has been run and PATH is current."
        )
        return False
    except Exception as exc:
        log.exception("Unexpected error starting Ollama: %s", exc)
        return False


def wait_for_ready(timeout: int = OLLAMA_STARTUP_TIMEOUT_SECONDS) -> bool:
    """
    Poll the Ollama health endpoint until it responds or the timeout expires.
    Returns True if Ollama became ready within the timeout window.
    """
    log.info("Waiting for Ollama to become ready (timeout %ds)...", timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_running():
            log.info("Ollama is ready")
            return True
        time.sleep(1.0)

    log.error("Ollama did not become ready within %ds", timeout)
    return False


def ensure_running() -> bool:
    """
    Start Ollama if needed and wait for it to be ready.
    This is the single call the engine makes at startup.
    Returns True if Ollama is available and accepting requests.
    """
    if is_running():
        log.info("Ollama health check passed")
        return True

    started = start()
    if not started:
        return False

    return wait_for_ready()


def stop():
    """
    Terminate the Ollama process if we started it.
    Called on engine shutdown -- does not touch an externally-started Ollama.
    """
    global _ollama_proc
    if _ollama_proc is not None:
        log.info("Stopping Ollama (PID %d)...", _ollama_proc.pid)
        try:
            _ollama_proc.terminate()
            _ollama_proc.wait(timeout=10)
            log.info("Ollama stopped cleanly")
        except subprocess.TimeoutExpired:
            log.warning("Ollama did not stop in time -- killing")
            _ollama_proc.kill()
        except Exception as exc:
            log.warning("Error stopping Ollama: %s", exc)
        finally:
            _ollama_proc = None

def force_kill():
    """
    Force-kill ALL ollama processes on the system, not just one we started
    ourselves. Needed because ensure_running()'s health check only hits
    /api/tags, which stays responsive even when the generation worker is
    completely wedged -- so the soft stop()/ensure_running() cycle can
    silently no-op forever against a hung server.
    """
    global _ollama_proc
    log.warning("Force-killing all Ollama processes (hard restart)")
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "ollama.exe"],
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["taskkill", "/F", "/IM", "ollama_llama_server.exe"],
            capture_output=True,
            timeout=10,
        )
    except Exception as exc:
        log.warning("taskkill failed (may just mean nothing was running): %s", exc)
    _ollama_proc = None
    # Give Windows a moment to fully release the port before we relaunch
    time.sleep(2.0)


def hard_restart() -> bool:
    """
    Unconditionally kill every Ollama process, then start fresh.
    Unlike ensure_running(), this does NOT trust the /api/tags health
    check as proof that Ollama is actually usable -- it always kills first.
    """
    force_kill()
    started = start()
    if not started:
        return False
    return wait_for_ready()