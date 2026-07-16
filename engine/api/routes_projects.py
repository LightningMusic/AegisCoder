"""
AegisCoder -- project management endpoints.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from engine.projects import registry, state
from engine.projects.models import Project

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateProjectRequest(BaseModel):
    name: str
    path: str
    model: str = "local-code:7b"
    auto_run_default: bool = False


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    model: str | None = None
    auto_run_default: bool | None = None


class AddHistoryRequest(BaseModel):
    role: str
    content: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/projects")
async def list_projects():
    """List all registered projects, most recently opened first."""
    return {"projects": [p.to_dict() for p in registry.load_all()]}


@router.post("/projects")
async def create_project(req: CreateProjectRequest):
    """Register a new project folder."""
    path = Path(req.path)
    if not path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Path does not exist: {req.path}",
        )
    existing = registry.get_by_path(req.path)
    if existing:
        return {"project": existing.to_dict(), "created": False}

    project = Project.new(name=req.name, path=str(path.resolve()))
    project.model = req.model
    project.auto_run_default = req.auto_run_default
    registry.add(project)
    log.info("Created project: %s at %s", project.name, project.path)
    return {"project": project.to_dict(), "created": True}


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    project = registry.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project": project.to_dict()}


@router.patch("/projects/{project_id}")
async def update_project(project_id: str, req: UpdateProjectRequest):
    project = registry.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if req.name is not None:
        project.name = req.name
    if req.model is not None:
        project.model = req.model
    if req.auto_run_default is not None:
        project.auto_run_default = req.auto_run_default
    registry.update(project)
    return {"project": project.to_dict()}


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """
    Unregister a project. Does NOT delete the project folder on disk --
    only removes it from AegisCoder's registry.
    """
    registry.remove(project_id)
    return {"ok": True}


@router.post("/projects/{project_id}/open")
async def open_project(project_id: str):
    """Touch the last_opened timestamp when the user switches to a project."""
    project = registry.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    registry.touch(project_id)
    return {"project": project.to_dict()}


@router.get("/projects/{project_id}/history")
async def get_history(project_id: str):
    """Return chat and plan history for a project."""
    return {
        "chat": state.load_chat(project_id),
        "plans": state.load_plans(project_id),
    }


@router.post("/projects/{project_id}/history")
async def add_history(project_id: str, req: AddHistoryRequest):
    """Append a message to the project's chat history."""
    state.append_chat(project_id, req.role, req.content)
    return {"ok": True}


@router.delete("/projects/{project_id}/history")
async def clear_history(project_id: str):
    """Wipe chat and plan history for a project."""
    state.clear_all(project_id)
    return {"ok": True}