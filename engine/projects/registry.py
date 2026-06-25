"""
AegisCoder -- project registry.

Stores the list of known projects in:
  %APPDATA%\\AegisCoder\\projects.json

This is separate from per-project state (chat history, plan history)
which lives in each project's own state folder.
"""
import json
import logging
import os
import time
from pathlib import Path

from engine.projects.models import Project

log = logging.getLogger(__name__)

_APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
_ROOT = _APPDATA / "AegisCoder"
_REGISTRY_FILE = _ROOT / "projects.json"


def _ensure_dir():
    _ROOT.mkdir(parents=True, exist_ok=True)


def load_all() -> list[Project]:
    """Return all registered projects, sorted by last_opened descending."""
    _ensure_dir()
    if not _REGISTRY_FILE.exists():
        return []
    try:
        data = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
        projects = [Project.from_dict(d) for d in data]
        return sorted(projects, key=lambda p: p.last_opened, reverse=True)
    except Exception as exc:
        log.warning("Could not load projects.json: %s", exc)
        return []


def save_all(projects: list[Project]):
    _ensure_dir()
    _REGISTRY_FILE.write_text(
        json.dumps([p.to_dict() for p in projects], indent=2),
        encoding="utf-8",
    )


def add(project: Project):
    """Register a new project. No-op if path already registered."""
    projects = load_all()
    if any(p.path == project.path for p in projects):
        log.info("Project already registered: %s", project.path)
        return
    projects.append(project)
    save_all(projects)
    log.info("Registered project: %s (%s)", project.name, project.path)


def remove(project_id: str):
    """Unregister a project by id. Does not delete the project folder."""
    projects = [p for p in load_all() if p.id != project_id]
    save_all(projects)


def get_by_id(project_id: str) -> Project | None:
    return next((p for p in load_all() if p.id == project_id), None)


def get_by_path(path: str) -> Project | None:
    resolved = str(Path(path).resolve())
    return next((p for p in load_all() if str(Path(p.path).resolve()) == resolved), None)


def touch(project_id: str):
    """Update last_opened timestamp for a project."""
    projects = load_all()
    for p in projects:
        if p.id == project_id:
            p.last_opened = time.time()
    save_all(projects)


def update(updated: Project):
    """Replace a project entry (by id) with updated values."""
    projects = load_all()
    for i, p in enumerate(projects):
        if p.id == updated.id:
            projects[i] = updated
            save_all(projects)
            return
    log.warning("update(): project %s not found in registry", updated.id)