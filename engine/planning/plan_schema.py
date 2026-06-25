"""
AegisCoder -- plan data models.

A Plan is created by the Architect from a user prompt.
It contains an ordered list of PlanSteps.
Each step goes through a lifecycle: pending -> approved/rejected -> running -> done/failed.

These are plain dataclasses so they JSON-serialize cleanly for
the WebSocket protocol and for disk persistence.
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

StepStatus = Literal["pending", "approved", "rejected", "running", "done", "failed", "skipped"]
PlanStatus = Literal["draft", "approved", "running", "done", "failed", "stopped"]


@dataclass
class PlanStep:
    id: int                                   # 0-indexed position in the plan
    description: str                          # what this step will do (human readable)
    status: StepStatus = "pending"
    error: str = ""                           # populated on failure
    files_changed: list[str] = field(default_factory=list)
    deletion_warning: bool = False            # True if deletion guard fired on this step
    deletion_ratio: float = 0.0


@dataclass
class Plan:
    id: str                                   # uuid
    prompt: str                               # the original user request
    project_path: str
    steps: list[PlanStep] = field(default_factory=list)
    status: PlanStatus = "draft"
    auto_run: bool = False
    created_at: float = field(default_factory=time.time)

    # ----------------------------------------------------------------
    # Convenience helpers
    # ----------------------------------------------------------------

    @staticmethod
    def new(prompt: str, project_path: str, auto_run: bool = False) -> "Plan":
        return Plan(
            id=str(uuid.uuid4())[:8],
            prompt=prompt,
            project_path=project_path,
            auto_run=auto_run,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "project_path": self.project_path,
            "status": self.status,
            "auto_run": self.auto_run,
            "created_at": self.created_at,
            "steps": [
                {
                    "id": s.id,
                    "description": s.description,
                    "status": s.status,
                    "error": s.error,
                    "files_changed": s.files_changed,
                    "deletion_warning": s.deletion_warning,
                    "deletion_ratio": s.deletion_ratio,
                }
                for s in self.steps
            ],
        }

    def approved_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == "approved"]

    def pending_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == "pending"]