"""
AegisCoder -- project data models.
"""
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Project:
    id: str
    name: str
    path: str
    model: str = "local-code:7b"
    auto_run_default: bool = False
    created_at: float = field(default_factory=time.time)
    last_opened: float = field(default_factory=time.time)

    @staticmethod
    def new(name: str, path: str) -> "Project":
        return Project(
            id=str(uuid.uuid4())[:8],
            name=name,
            path=path,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "model": self.model,
            "auto_run_default": self.auto_run_default,
            "created_at": self.created_at,
            "last_opened": self.last_opened,
        }

    @staticmethod
    def from_dict(d: dict) -> "Project":
        return Project(
            id=d["id"],
            name=d["name"],
            path=d["path"],
            model=d.get("model", "local-code:7b"),
            auto_run_default=d.get("auto_run_default", False),
            created_at=d.get("created_at", time.time()),
            last_opened=d.get("last_opened", time.time()),
        )