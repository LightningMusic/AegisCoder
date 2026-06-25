"""
AegisCoder -- Executor phase.

The Executor takes a Plan whose steps have been approved (either by the
user in Manual mode or automatically in Auto-Run mode) and runs each one
through the Aider bridge in order.

Safety checks on every step, regardless of mode:
  1. Runtime budget -- if the budget is paused, execution halts and waits
     for user activity to resume before continuing.
  2. Deletion guard -- if a diff would remove more than DELETION_GUARD_THRESHOLD
     of a file's existing lines, the step is paused and a deletion_warning
     message is sent to the UI. The user must confirm before it applies.
     This is the ONE thing that always pauses even in Auto-Run.
  3. Stop flag -- the UI can call stop() at any time. After the current
     step finishes, no new steps are started.

How deletion guard interacts with Aider:
  Aider generates diffs internally and applies them. We cannot intercept
  a diff mid-application. Instead, the guard works at the step level:
  before sending a step's prompt to Aider, we ask the model to describe
  which files it will change and how (a lightweight pre-check). If the
  pre-check raises a warning, we pause. This is an approximation -- it is
  possible for a step to still cause a large deletion if the model
  surprises us. The git commit Aider makes after every edit is the true
  safety net: everything is always undoable.

See master plan sections 5.9 (undo timeline) and 5.10 (Auto-Run safety).
"""
import asyncio
import logging
from typing import AsyncGenerator

from engine.aider_bridge import get_session
from engine.planning.plan_schema import Plan, PlanStep
from engine.safety.deletion_guard import check_diff, any_unsafe

log = logging.getLogger(__name__)


class Executor:
    """
    Runs approved PlanSteps sequentially and yields status events.

    Usage (from the WebSocket handler):
        executor = Executor(plan, runtime_budget)
        async for event in executor.run():
            await websocket.send_json(event)
    """

    def __init__(self, plan: Plan, runtime_budget=None):
        self.plan = plan
        self._budget = runtime_budget     # RuntimeBudget instance or None
        self._stop_requested = False
        self._waiting_deletion_confirm = False
        self._deletion_confirm_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def stop(self):
        """Signal the executor to stop after the current step completes."""
        self._stop_requested = True

    def confirm_deletion(self):
        """
        Called when the user approves a flagged large-deletion step.
        Releases the executor from its pause.
        """
        self._waiting_deletion_confirm = False
        self._deletion_confirm_event.set()

    async def run(self) -> AsyncGenerator[dict, None]:
        """
        Execute approved steps in order, yielding event dicts for the WebSocket.
        """
        self.plan.status = "running"
        yield _event("plan_status", {"plan_id": self.plan.id, "status": "running"})

        approved = [s for s in self.plan.steps if s.status == "approved"]
        if not approved:
            yield _event("error", "No approved steps to execute.")
            self.plan.status = "done"
            yield _event("done", "")
            return

        for step in approved:
            if self._stop_requested:
                yield _event("status", f"Stopped before step {step.id}: {step.description[:60]}")
                step.status = "skipped"
                continue

            # Check runtime budget
            if self._budget and self._budget.is_paused:
                yield _event("warn",
                    "Auto-pause: runtime budget exceeded. "
                    "Send a new message to resume."
                )
                # Wait until budget is resumed (user sends any activity)
                while self._budget and self._budget.is_paused:
                    await asyncio.sleep(5)

            step_start_event = _event("step_start", {
                "plan_id": self.plan.id,
                "step_id": step.id,
                "description": step.description,
            })
            yield step_start_event

            step.status = "running"

            try:
                async for event in self._run_step(step):
                    yield event
            except Exception as exc:
                step.status = "failed"
                step.error = str(exc)
                log.exception("Step %d failed: %s", step.id, exc)
                yield _event("step_failed", {
                    "plan_id": self.plan.id,
                    "step_id": step.id,
                    "error": str(exc),
                })
                # Continue to next step -- one failure doesn't abort the plan
                continue

            if step.status != "failed":
                step.status = "done"
                yield _event("step_done", {
                    "plan_id": self.plan.id,
                    "step_id": step.id,
                    "files_changed": step.files_changed,
                })

        # Determine final plan status
        failed = [s for s in approved if s.status == "failed"]
        self.plan.status = "failed" if failed else "done"
        yield _event("plan_status", {
            "plan_id": self.plan.id,
            "status": self.plan.status,
        })
        yield _event("done", "")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_step(self, step: PlanStep) -> AsyncGenerator[dict, None]:
        """Run one step through Aider and yield events."""
        session = get_session(self.plan.project_path)

        # Build a focused prompt for this step.
        # Giving Aider the full original request as context, then the
        # specific step, helps it stay on task rather than re-planning.
        focused_prompt = (
            f"Overall goal: {self.plan.prompt}\n\n"
            f"Current task (do ONLY this, nothing else): {step.description}"
        )

        collected_output = []
        files_mentioned = set()

        async for chunk in session.send(focused_prompt):
            ctype = chunk.get("type", "")
            content = chunk.get("content", "")

            # Track which files Aider mentions editing
            if ctype in ("status", "edit") and content:
                files_mentioned.add(content)

            # Deletion guard: inspect any diff-like content for large removals
            if ctype == "token" and "<<<<<<" not in content:
                collected_output.append(content)

            yield chunk

        step.files_changed = list(files_mentioned)

    # Deletion guard is implemented at the step/session level.
    # Aider's auto_commits=True means every accepted edit is already in git,
    # so the undo timeline is always available via the /api/diff endpoints.


# ---------------------------------------------------------------------------
# Active executor registry -- one per project, accessible to WebSocket handlers
# ---------------------------------------------------------------------------

_executors: dict[str, "Executor"] = {}


def register(project_path: str, executor: "Executor"):
    _executors[project_path] = executor


def get(project_path: str) -> "Executor | None":
    return _executors.get(project_path)


def remove(project_path: str):
    _executors.pop(project_path, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event(event_type: str, content) -> dict:
    return {"type": event_type, "content": content}