"""
HTTP REST endpoints for the engine.

/api/health  -- liveness check used by the UI shell to know the engine is ready
/api/session -- close or reset a project session without restarting the engine
"""
import logging

from fastapi import APIRouter
from pydantic import BaseModel

from engine import ollama_manager
from engine.aider_bridge import close_session, get_session

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    engine: str
    ollama: str


class SessionActionRequest(BaseModel):
    project_path: str
    action: str  # "close" or "reset"


class SessionActionResponse(BaseModel):
    ok: bool
    message: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health():
    """
    Returns engine and Ollama status.
    The UI shell polls this on startup to confirm the engine is ready before
    showing the main interface.
    """
    ollama_status = "ok" if ollama_manager.is_running() else "unavailable"
    return HealthResponse(engine="ok", ollama=ollama_status)


@router.post("/session", response_model=SessionActionResponse)
async def session_action(req: SessionActionRequest):
    """
    Close or reset the Aider session for a project path.

    close  -- tears down the session entirely (frees memory, next prompt
              creates a fresh Coder instance)
    reset  -- resets retry counters and clears the Coder instance but
              keeps the session entry alive (useful after a timeout)
    """
    if req.action == "close":
        close_session(req.project_path)
        return SessionActionResponse(ok=True, message="Session closed")

    if req.action == "reset":
        session = get_session(req.project_path)
        session.reset()
        return SessionActionResponse(ok=True, message="Session reset")

    return SessionActionResponse(
        ok=False,
        message=f"Unknown action '{req.action}'. Use 'close' or 'reset'.",
    )