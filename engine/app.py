"""
AegisCoder engine -- FastAPI application factory and server launcher.

Startup sequence (see master plan section 5.13):
  1. FastAPI app is created with CORS configured for pywebview's null origin
  2. On startup event: Ollama is checked and started if needed
  3. Uvicorn serves the app on ENGINE_HOST:ENGINE_PORT
  4. On shutdown event: all Aider sessions and Ollama are cleaned up

All routes are registered here. New route modules are imported and
included with their prefix -- this file stays thin.
"""
import logging
import os
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi.staticfiles import StaticFiles
from pathlib import Path

from engine import ollama_manager
from engine.aider_bridge import close_all_sessions
from engine.api.routes_chat import router as chat_router
from engine.api.routes_diff import router as diff_router
from engine.api.routes_plan import router as plan_router
from engine.api.routes_projects import router as projects_router
from engine.api.websocket import router as ws_router
from engine.config import ENGINE_HOST, ENGINE_PORT, LOG_DIR, REMOTE_ACCESS_ENABLED
from engine.middleware.auth import TokenAuthMiddleware
from engine.safety.process_manager import apply_self_limits
from engine.safety.watchdog import OllamaWatchdog
from engine.safety.runtime_budget import RuntimeBudget

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "engine.log"), encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="AegisCoder Engine",
        version="0.1.0",
        description="Local Codex-style coding agent engine",
    )

    # CORS -- pywebview uses null origin in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Token auth -- required for non-localhost connections (mobile via Tailscale)
    app.add_middleware(TokenAuthMiddleware)

    # REST routes
    app.include_router(chat_router, prefix="/api")
    app.include_router(plan_router, prefix="/api")
    app.include_router(diff_router, prefix="/api")
    app.include_router(projects_router, prefix="/api")

    # WebSocket routes
    app.include_router(ws_router)

    # Serve the frontend as static files so the mobile browser can load it
    static_dir = Path(__file__).parent.parent / "ui" / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    # ------------------------------------------------------------------
    # Lifecycle events
    # ------------------------------------------------------------------

    # Safety systems -- instantiated here so they share the app lifetime
    _watchdog = OllamaWatchdog(
        is_running_fn=ollama_manager.is_running,
        restart_fn=ollama_manager.ensure_running,
        on_down=lambda: log.warning("Ollama went down -- watchdog will attempt restart"),
        on_recovered=lambda: log.info("Ollama recovered -- watchdog confirmed"),
    )
    _budget = RuntimeBudget(
        on_exceeded=lambda: log.warning(
            "Runtime budget exceeded -- Auto-Run will pause until user activity"
        )
    )

    # Expose to routes via app state so handlers can call record_activity()
    app.state.runtime_budget = _budget

    @app.on_event("startup")
    async def on_startup():
        log.info("AegisCoder engine starting up...")

        # Apply OS resource limits first, before doing any real work
        apply_self_limits()

        if REMOTE_ACCESS_ENABLED:
            log.info(
                "Remote access ENABLED -- engine binding to 0.0.0.0:%d. "
                "Ensure Tailscale is running and ACCESS_TOKEN is set.",
                ENGINE_PORT,
            )
        else:
            log.info("Remote access disabled -- localhost only")

        ready = ollama_manager.ensure_running()
        if ready:
            log.info("Ollama is available -- engine ready")
        else:
            log.warning(
                "Ollama is NOT available. "
                "The engine will start, but chat will fail until Ollama is running. "
                "Check that 'ollama' is on PATH and the setup script has been run."
            )

        _watchdog.start()
        _budget.start()

    @app.on_event("shutdown")
    async def on_shutdown():
        log.info("AegisCoder engine shutting down...")
        _watchdog.stop()
        _budget.stop()
        close_all_sessions()
        ollama_manager.stop()
        log.info("Shutdown complete")

    return app


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

def serve():
    """
    Launch the engine via uvicorn.
    Called from the top-level main.py.
    In Phase 4 this will be called in a child process by the pywebview shell.
    """
    log.info("Starting engine on %s:%d", ENGINE_HOST, ENGINE_PORT)
    app = create_app()
    uvicorn.run(
        app,
        host=ENGINE_HOST,
        port=ENGINE_PORT,
        log_level="info",
        # Disable uvicorn's default signal handlers so the parent process
        # (pywebview shell in Phase 4) can manage lifecycle cleanly.
        # In Phase 1 / dev mode this has no effect.
        use_colors=False,
    )