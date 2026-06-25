"""
AegisCoder -- per-project state persistence.

Each project gets its own state folder at:
  %APPDATA%\\AegisCoder\\projects\\<project_id>\\

Inside that folder:
  chat_history.json   -- list of {role, content, timestamp} dicts
  plan_history.json   -- list of plan to_dict() snapshots

If the engine dies or the machine reboots, the next open picks up where
it left off. See master plan section 5.8.
"""
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

_APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
_ROOT = _APPDATA / "AegisCoder" / "projects"


def _project_dir(project_id: str) -> Path:
    d = _ROOT / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------

def load_chat(project_id: str) -> list[dict]:
    path = _project_dir(project_id) / "chat_history.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not load chat history for %s: %s", project_id, exc)
        return []


def append_chat(project_id: str, role: str, content: str):
    history = load_chat(project_id)
    history.append({"role": role, "content": content, "timestamp": time.time()})
    path = _project_dir(project_id) / "chat_history.json"
    path.write_text(json.dumps(history, indent=2), encoding="utf-8")


def clear_chat(project_id: str):
    path = _project_dir(project_id) / "chat_history.json"
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Plan history
# ---------------------------------------------------------------------------

def load_plans(project_id: str) -> list[dict]:
    path = _project_dir(project_id) / "plan_history.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not load plan history for %s: %s", project_id, exc)
        return []


def save_plan(project_id: str, plan_dict: dict):
    plans = load_plans(project_id)
    # Replace if same plan id already exists, else append
    plans = [p for p in plans if p.get("id") != plan_dict.get("id")]
    plans.append(plan_dict)
    # Keep last 50 plans to avoid unbounded growth
    plans = plans[-50:]
    path = _project_dir(project_id) / "plan_history.json"
    path.write_text(json.dumps(plans, indent=2), encoding="utf-8")


def clear_all(project_id: str):
    """Wipe all state for a project (does not delete the project folder itself)."""
    for fname in ("chat_history.json", "plan_history.json"):
        path = _project_dir(project_id) / fname
        if path.exists():
            path.unlink()