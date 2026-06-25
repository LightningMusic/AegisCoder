"""
AegisCoder -- Architect phase.

The Architect takes the user's prompt and asks the local model to produce
a structured plan before any code is touched. This gives you (in Manual
mode) or the Executor (in Auto-Run mode) a clear list of discrete steps
to work through, rather than the model attempting everything in one shot.

Why a separate Architect pass instead of just sending the prompt straight
to Aider?
  - A 7B model given a big, open-ended task in one shot tends to either
    produce an overwhelming response or stop halfway through.
  - Breaking it into a plan + step-by-step execution lets the model handle
    one focused thing at a time, which significantly improves accuracy.
  - It gives you a chance to review and reject steps before any file is
    touched (Manual mode) or at least see what's coming (Auto-Run mode).

The Architect uses a tightly constrained system prompt to get JSON output.
If the model returns malformed JSON (common with 7B models under load),
the parser falls back to extracting a numbered list from plain text, and
if that also fails, the entire response is treated as a single step.
This means the plan always has at least one step regardless of model output.
"""
import json
import logging
import re
import time

import httpx

from engine.config import (
    AIDER_MODEL,
    INFERENCE_TIMEOUT_SECONDS,
    NUM_CTX,
    OLLAMA_API_BASE,
)
from engine.planning.plan_schema import Plan, PlanStep

log = logging.getLogger(__name__)

# Model name without the "ollama/" prefix for direct API calls
_RAW_MODEL = AIDER_MODEL.replace("ollama/", "")

ARCHITECT_SYSTEM = """You are a software planning assistant. The user will describe a coding task.
Your job is to break it into a numbered list of concrete, focused implementation steps.

RULES:
- Output ONLY a JSON array of strings. No preamble, no explanation, no markdown fences.
- Each string is one step. Steps should be short and specific (one clear action each).
- Maximum 10 steps. If the task is simple, fewer steps is better.
- Do not include steps for "test", "deploy", or "document" unless explicitly asked.
- Do not repeat yourself.

EXAMPLE OUTPUT:
["Add a Config class to config.py with host and port fields",
 "Update main.py to import Config and pass it to the server constructor",
 "Add a --config CLI argument that loads from a JSON file"]"""


async def generate_plan(
    prompt: str,
    project_path: str,
    auto_run: bool = False,
) -> Plan:
    """
    Ask the Architect model to produce a plan for the given prompt.
    Returns a Plan with steps populated.
    Raises RuntimeError if Ollama is unreachable.
    """
    plan = Plan.new(prompt=prompt, project_path=project_path, auto_run=auto_run)
    log.info("Architect generating plan | project=%s | prompt_len=%d", project_path, len(prompt))

    raw_text = await _call_model(prompt)
    steps_text = _parse_steps(raw_text)

    plan.steps = [PlanStep(id=i, description=s) for i, s in enumerate(steps_text)]

    if auto_run:
        # In auto-run mode all steps start as approved -- the Executor
        # will re-evaluate each one through the deletion guard before applying.
        for step in plan.steps:
            step.status = "approved"

    log.info(
        "Architect produced %d steps (auto_run=%s)",
        len(plan.steps),
        auto_run,
    )
    return plan


# ---------------------------------------------------------------------------
# Ollama API call (direct httpx -- no Aider wrapper needed for planning)
# ---------------------------------------------------------------------------

async def _call_model(prompt: str) -> str:
    """
    Send the architect prompt to Ollama and return the raw text response.
    Uses a short timeout -- planning should be fast.
    """
    payload = {
        "model": _RAW_MODEL,
        "messages": [
            {"role": "system", "content": ARCHITECT_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        "stream": False,
        "options": {
            "num_ctx": NUM_CTX,
            "temperature": 0.2,   # lower temp for more predictable structured output
        },
    }

    timeout = min(INFERENCE_TIMEOUT_SECONDS, 90)   # planning should not need 3 min

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{OLLAMA_API_BASE}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
            return data["message"]["content"]
    except httpx.TimeoutException:
        raise RuntimeError(
            f"Architect timed out after {timeout}s. "
            "Ollama may be busy -- try again."
        )
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"Ollama returned HTTP {exc.response.status_code}")
    except Exception as exc:
        raise RuntimeError(f"Architect call failed: {exc}")


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_steps(text: str) -> list[str]:
    """
    Try to extract a list of step strings from the model's response.
    Three attempts, most strict to most lenient.
    """
    # 1. JSON array
    steps = _try_json(text)
    if steps:
        return steps

    # 2. Numbered list (e.g. "1. Do this\n2. Do that")
    steps = _try_numbered_list(text)
    if steps:
        return steps

    # 3. Fallback: treat entire response as one step
    log.warning("Architect response could not be parsed as structured plan -- using as single step")
    return [text.strip()[:500]]


def _try_json(text: str) -> list[str]:
    # Strip markdown fences if present
    text = re.sub(r"```json?\s*", "", text)
    text = re.sub(r"```", "", text)
    text = text.strip()

    # Find the first '[' and last ']'
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []

    try:
        result = json.loads(text[start : end + 1])
        if isinstance(result, list) and all(isinstance(s, str) for s in result):
            return [s.strip() for s in result if s.strip()]
    except json.JSONDecodeError:
        pass
    return []


def _try_numbered_list(text: str) -> list[str]:
    pattern = re.compile(r"^\s*\d+[\.\)]\s+(.+)$", re.MULTILINE)
    matches = pattern.findall(text)
    return [m.strip() for m in matches if m.strip()]