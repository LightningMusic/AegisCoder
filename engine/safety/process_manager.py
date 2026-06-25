"""
AegisCoder -- process manager.

Owns the lifecycle of the three isolated OS processes described in the
master plan (section 5.4):
  1. Ollama server    -- managed by ollama_manager.py
  2. Engine (self)    -- this process; Job Object applied at startup
  3. UI shell         -- pywebview window (Phase 4); spawned as a child

In Phase 1 and 2 there is no separate UI process -- the UI is served as
static files by the engine's FastAPI server. This module still provides
the apply_self_limits() call that the engine calls at startup to put
itself inside a Job Object and set process priority.

In Phase 4 this module will also spawn and monitor the pywebview child.

See master plan sections 5.4 and 5.5.
"""
import logging
import os

from engine.config import MAX_MEMORY_MB
from engine.safety.job_object import apply as apply_job_object

log = logging.getLogger(__name__)


def apply_self_limits():
    """
    Apply OS-level resource limits to the current engine process.

    This must be called as early as possible in the engine's startup
    sequence, before any significant work is done.

    What this does:
      - Sets process priority to BELOW_NORMAL (psutil)
      - Creates a Windows Job Object capping memory to MAX_MEMORY_MB
        and assigns this process (and all future children) to it

    If pywin32 is not available the Job Object step is skipped with a
    warning -- the priority step still applies.
    """
    log.info(
        "Applying self-limits: priority=BELOW_NORMAL, memory_cap=%dMB",
        MAX_MEMORY_MB,
    )
    apply_job_object(MAX_MEMORY_MB)
    log.info("Self-limits applied (PID %d)", os.getpid())