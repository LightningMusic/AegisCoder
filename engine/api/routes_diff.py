"""
AegisCoder -- diff and checkpoint endpoints.

Every edit Aider makes is committed to git automatically (auto_commits=True).
These endpoints expose that git history as a checkpoint timeline so the UI
can show an undo button for every edit.

Revert = `git revert <hash>` (creates a new commit undoing that change,
never force-pushes or rewrites history). This is the safest approach --
it works even if you've made further changes after the one you want to undo.

See master plan section 5.9.
"""
import logging
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class RevertRequest(BaseModel):
    project_path: str
    commit_hash: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/diff/checkpoints")
async def get_checkpoints(project_path: str, limit: int = 30):
    """
    Return the last `limit` git commits for the project as a checkpoint list.
    Each entry has: hash, short_hash, message, date, files_changed.
    Only commits authored by Aider (message starts with "aider:") are included,
    so the user's own manual commits don't clutter the undo timeline.
    """
    path = Path(project_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Project path not found")

    if not (path / ".git").exists():
        return {"checkpoints": [], "message": "Project has no git history yet"}

    try:
        result = subprocess.run(
            [
                "git", "log",
                f"--max-count={limit}",
                "--pretty=format:%H|%h|%s|%ad|%an",
                "--date=short",
                "--diff-filter=M",   # modified files only
            ],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="git log timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="git not found on PATH")

    checkpoints = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        full_hash, short_hash, message, date, author = parts

        # Get files changed in this commit
        files_result = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", full_hash],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=5,
        )
        files_changed = [f for f in files_result.stdout.strip().splitlines() if f]

        checkpoints.append({
            "hash": full_hash,
            "short_hash": short_hash,
            "message": message,
            "date": date,
            "author": author,
            "files_changed": files_changed,
        })

    return {"checkpoints": checkpoints}


@router.get("/diff/preview")
async def preview_diff(project_path: str, commit_hash: str):
    """
    Return the unified diff for a specific commit so the UI can show
    exactly what changed before the user decides to revert.
    """
    path = Path(project_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Project path not found")

    try:
        result = subprocess.run(
            ["git", "show", "--unified=3", commit_hash],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=400, detail=f"git show failed: {result.stderr}")
        return {"diff": result.stdout}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="git show timed out")


@router.post("/diff/revert")
async def revert_commit(req: RevertRequest):
    """
    Revert a specific commit using `git revert --no-edit`.
    This creates a new commit that undoes the specified one --
    it never rewrites history, so it is always safe.
    """
    path = Path(req.project_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Project path not found")

    if not (path / ".git").exists():
        raise HTTPException(status_code=400, detail="Project has no git repository")

    # Safety: only allow reverting short or full 40-char hashes
    h = req.commit_hash.strip()
    if not h or len(h) > 40 or not all(c in "0123456789abcdefABCDEF" for c in h):
        raise HTTPException(status_code=400, detail="Invalid commit hash")

    try:
        result = subprocess.run(
            ["git", "revert", "--no-edit", h],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail=f"git revert failed: {result.stderr.strip()}",
            )
        log.info("Reverted commit %s in %s", h, path)
        return {
            "ok": True,
            "message": f"Reverted {h[:8]}. A new commit has been created to undo this change.",
            "output": result.stdout.strip(),
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="git revert timed out")