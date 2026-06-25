"""
AegisCoder -- plan REST endpoints.

These handle plan lifecycle operations that happen outside the WebSocket
stream (approval decisions, stop requests, deletion confirmations).
Plan generation and execution streaming happen over the WebSocket.
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from engine.planning import executor as exec_registry
from engine.planning.plan_schema import Plan

log = logging.getLogger(__name__)
router = APIRouter()

# In-memory plan store -- keyed by plan_id.
# In Phase 4 this gets persisted into per-project state on disk.
_plans: dict[str, Plan] = {}


def store_plan(plan: Plan):
    _plans[plan.id] = plan


def get_plan(plan_id: str) -> Plan:
    plan = _plans.get(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")
    return plan


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ApproveRequest(BaseModel):
    step_ids: list[int]           # step ids to approve; empty = approve all


class RejectRequest(BaseModel):
    step_id: int


class StopRequest(BaseModel):
    project_path: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/plan/{plan_id}")
async def get_plan_route(plan_id: str):
    """Return the current state of a plan."""
    return get_plan(plan_id).to_dict()


@router.post("/plan/{plan_id}/approve")
async def approve_steps(plan_id: str, req: ApproveRequest):
    """
    Approve specific steps (or all steps if step_ids is empty).
    The WebSocket executor will pick these up when it runs.
    """
    plan = get_plan(plan_id)
    ids_to_approve = set(req.step_ids) if req.step_ids else {s.id for s in plan.steps}

    approved = []
    for step in plan.steps:
        if step.id in ids_to_approve and step.status == "pending":
            step.status = "approved"
            approved.append(step.id)

    log.info("Plan %s: approved steps %s", plan_id, approved)
    return {"ok": True, "approved": approved, "plan": plan.to_dict()}


@router.post("/plan/{plan_id}/reject/{step_id}")
async def reject_step(plan_id: str, step_id: int):
    """Reject a single step."""
    plan = get_plan(plan_id)
    for step in plan.steps:
        if step.id == step_id:
            step.status = "rejected"
            log.info("Plan %s: step %d rejected", plan_id, step_id)
            return {"ok": True, "plan": plan.to_dict()}
    raise HTTPException(status_code=404, detail=f"Step {step_id} not found in plan {plan_id}")


@router.post("/plan/{plan_id}/confirm-deletion/{step_id}")
async def confirm_deletion(plan_id: str, step_id: int):
    """
    User has confirmed they want to proceed with a large-deletion step.
    Releases the executor's pause for that step.
    """
    plan = get_plan(plan_id)
    ex = exec_registry.get(plan.project_path)
    if ex:
        ex.confirm_deletion()
        log.info("Plan %s step %d: deletion confirmed by user", plan_id, step_id)
        return {"ok": True}
    return {"ok": False, "message": "No active executor for this project"}


@router.post("/plan/{plan_id}/stop")
async def stop_plan(plan_id: str, req: StopRequest):
    """Stop the currently executing plan."""
    plan = get_plan(plan_id)
    ex = exec_registry.get(req.project_path)
    if ex:
        ex.stop()
        plan.status = "stopped"
        log.info("Plan %s: stop requested", plan_id)
        return {"ok": True, "message": "Stop signal sent"}
    return {"ok": False, "message": "No active executor found"}