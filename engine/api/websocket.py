"""
AegisCoder -- WebSocket handler.

All real-time communication between the frontend and engine travels here.
The frontend opens one persistent connection and sends JSON messages.
The engine streams responses back as they arrive.

Incoming message shapes (client -> server):

  Chat mode (direct prompt to Aider, no planning step):
    {"mode": "chat", "prompt": "...", "project_path": "..."}

  Plan mode (generate a plan, do NOT execute yet):
    {"mode": "plan", "prompt": "...", "project_path": "..."}

  Auto-run mode (generate plan + execute automatically):
    {"mode": "autorun", "prompt": "...", "project_path": "..."}

  Execute approved steps (after user reviewed the plan):
    {"action": "execute", "plan_id": "...", "project_path": "..."}

  Stop the current execution:
    {"action": "stop", "project_path": "..."}

Outgoing message shapes (server -> client):

  {"type": "token",        "content": "..."}  -- AI text chunk
  {"type": "status",       "content": "..."}  -- Aider / engine status
  {"type": "warning",      "content": "..."}  -- Non-fatal warning
  {"type": "error",        "content": "..."}  -- Error
  {"type": "edit",         "content": "..."}  -- File was edited
  {"type": "plan_ready",   "content": {...}}  -- Plan object ready for approval
  {"type": "plan_status",  "content": {...}}  -- Plan status changed
  {"type": "step_start",   "content": {...}}  -- A step began executing
  {"type": "step_done",    "content": {...}}  -- A step completed
  {"type": "step_failed",  "content": {...}}  -- A step failed
  {"type": "done",         "content": ""}     -- This request is fully complete
"""
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from engine.aider_bridge import get_session
from engine.planning.architect import generate_plan
from engine.planning.executor import Executor, register as register_executor, remove as remove_executor
from engine.api.routes_plan import store_plan, get_plan

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/chat")
async def chat_socket(websocket: WebSocket):
    await websocket.accept()
    log.info("WebSocket client connected from %s", websocket.client)

    # Grab the runtime budget from app state if available
    budget = getattr(websocket.app.state, "runtime_budget", None)

    try:
        while True:
            data = await websocket.receive_json()

            project_path = data.get("project_path", "").strip()
            if not project_path:
                await websocket.send_json({"type": "error", "content": "No project_path provided"})
                continue

            # Record user activity for the runtime budget
            if budget:
                budget.record_activity()

            mode   = data.get("mode", "")
            action = data.get("action", "")

            # ----------------------------------------------------------
            # STOP
            # ----------------------------------------------------------
            if action == "stop":
                ex = None
                try:
                    from engine.planning.executor import get as get_executor
                    ex = get_executor(project_path)
                except Exception:
                    pass
                if ex:
                    ex.stop()
                    await websocket.send_json({"type": "status", "content": "Stop signal sent"})
                else:
                    # Also reset the aider session so a hung call unblocks
                    session = get_session(project_path)
                    session.reset()
                    await websocket.send_json({"type": "status", "content": "Session reset"})
                await websocket.send_json({"type": "done", "content": ""})
                continue

            # ----------------------------------------------------------
            # EXECUTE already-approved plan
            # ----------------------------------------------------------
            if action == "execute":
                plan_id = data.get("plan_id", "")
                if not plan_id:
                    await websocket.send_json({"type": "error", "content": "No plan_id provided"})
                    continue
                try:
                    plan = get_plan(plan_id)
                except Exception as exc:
                    await websocket.send_json({"type": "error", "content": str(exc)})
                    continue

                executor = Executor(plan, runtime_budget=budget)
                register_executor(project_path, executor)
                try:
                    async for event in executor.run():
                        await websocket.send_json(event)
                finally:
                    remove_executor(project_path)
                continue

            # ----------------------------------------------------------
            # CHAT / PLAN / AUTORUN  (all need a prompt)
            # ----------------------------------------------------------
            prompt = data.get("prompt", "").strip()
            if not prompt:
                await websocket.send_json({"type": "error", "content": "Empty prompt"})
                continue

            log.info("Mode=%s | project=%s | prompt_len=%d", mode or "chat", project_path, len(prompt))

            # ----------------------------------------------------------
            # CHAT -- direct to Aider, no planning pass
            # ----------------------------------------------------------
            if mode == "chat" or mode == "":
                session = get_session(project_path)
                async for chunk in session.send(prompt):
                    await websocket.send_json(chunk)
                continue

            # ----------------------------------------------------------
            # PLAN -- generate plan, return it, wait for user to approve
            # ----------------------------------------------------------
            if mode == "plan":
                await websocket.send_json({
                    "type": "status",
                    "content": "Planning...",
                })
                try:
                    plan = await generate_plan(prompt, project_path, auto_run=False)
                except Exception as exc:
                    await websocket.send_json({"type": "error", "content": str(exc)})
                    await websocket.send_json({"type": "done", "content": ""})
                    continue

                store_plan(plan)
                await websocket.send_json({
                    "type": "plan_ready",
                    "content": plan.to_dict(),
                })
                await websocket.send_json({"type": "done", "content": ""})
                continue

            # ----------------------------------------------------------
            # AUTORUN -- plan then execute automatically
            # ----------------------------------------------------------
            if mode == "autorun":
                await websocket.send_json({
                    "type": "status",
                    "content": "Planning (Auto-Run mode)...",
                })
                try:
                    plan = await generate_plan(prompt, project_path, auto_run=True)
                except Exception as exc:
                    await websocket.send_json({"type": "error", "content": str(exc)})
                    await websocket.send_json({"type": "done", "content": ""})
                    continue

                store_plan(plan)
                await websocket.send_json({
                    "type": "plan_ready",
                    "content": plan.to_dict(),
                })

                executor = Executor(plan, runtime_budget=budget)
                register_executor(project_path, executor)
                try:
                    async for event in executor.run():
                        await websocket.send_json(event)
                finally:
                    remove_executor(project_path)
                continue

            await websocket.send_json({
                "type": "error",
                "content": f"Unknown mode '{mode}'. Use: chat, plan, autorun",
            })

    except WebSocketDisconnect:
        log.info("WebSocket client disconnected")
    except Exception as exc:
        log.exception("Unhandled WebSocket error")
        try:
            await websocket.send_json({"type": "error", "content": f"Internal error: {exc}"})
        except Exception:
            pass