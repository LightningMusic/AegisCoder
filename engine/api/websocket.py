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

ERROR HANDLING NOTE:
  Each individual message is handled inside its own try/except. A failure
  processing one message (bad JSON, an Aider exception, a timeout) is
  reported back to the client as an {"type": "error"} event -- it does NOT
  close the socket. Only a genuine WebSocketDisconnect ends the connection.
  This is what keeps the UI connected during long-running work instead of
  dropping on the first transient hiccup.
"""
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from engine.aider_bridge import get_session
from engine.planning.architect import generate_plan
from engine.planning.executor import (
    Executor,
    ActiveExecution,
    get_active_execution,
    register_active_execution,
)
from engine.api.routes_plan import store_plan, get_plan

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/chat")
async def chat_socket(websocket: WebSocket):
    await websocket.accept()
    log.info("WebSocket client connected from %s", websocket.client)

    # Grab the runtime budget from app state if available
    budget = getattr(websocket.app.state, "runtime_budget", None)

    # Start a keepalive task to send silent pings every 20 seconds
    async def keepalive():
        try:
            while True:
                await asyncio.sleep(20)
                await websocket.send_json({"type": "ping", "content": "keepalive"})
        except Exception:
            pass

    keepalive_task = asyncio.create_task(keepalive())

    # Initialized here so the outer except WebSocketDisconnect handler can
    # always reference it, even if the client disconnects before sending
    # a single message.
    project_path: str = ""

    try:
        while True:
            # ----------------------------------------------------------
            # Receive one message. A malformed frame is reported but does
            # NOT close the socket.
            # ----------------------------------------------------------
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                log.warning("Malformed WS message: %s", exc)
                try:
                    await websocket.send_json({"type": "error", "content": f"Bad message: {exc}"})
                except Exception:
                    pass
                continue

            # ----------------------------------------------------------
            # Handle the message. Any failure here is reported to the
            # client and the loop continues -- it never kills the socket.
            # ----------------------------------------------------------
            try:
                project_path = data.get("project_path", "").strip()
                if not project_path:
                    await websocket.send_json({"type": "error", "content": "No project_path provided"})
                    continue

                if budget:
                    budget.record_activity()

                mode = data.get("mode", "")
                action = data.get("action", "")

                # ------------------------------------------------------
                # JOIN -- attach to an ongoing execution or confirm session
                # ------------------------------------------------------
                if action == "join":
                    active_ex = get_active_execution(project_path)
                    if active_ex:
                        active_ex.websockets.add(websocket)
                        for event in active_ex.event_buffer:
                            await websocket.send_json(event)
                    else:
                        await websocket.send_json({"type": "status", "content": "Attached to project session"})
                    continue

                # ------------------------------------------------------
                # STOP
                # ------------------------------------------------------
                if action == "stop":
                    active_ex = get_active_execution(project_path)
                    if active_ex:
                        active_ex.executor.stop()
                        await websocket.send_json({"type": "status", "content": "Stop signal sent"})
                    else:
                        session = get_session(project_path)
                        session.reset()
                        await websocket.send_json({"type": "status", "content": "Session reset"})
                    await websocket.send_json({"type": "done", "content": ""})
                    continue

                # ------------------------------------------------------
                # EXECUTE already-approved plan
                # ------------------------------------------------------
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

                    active_ex = get_active_execution(project_path)
                    if active_ex:
                        active_ex.websockets.add(websocket)
                        for event in active_ex.event_buffer:
                            await websocket.send_json(event)
                        continue

                    executor = Executor(plan, runtime_budget=budget)
                    active_ex = ActiveExecution(project_path, executor)
                    active_ex.websockets.add(websocket)
                    register_active_execution(project_path, active_ex)
                    active_ex.task = asyncio.create_task(active_ex.run())
                    continue

                # ------------------------------------------------------
                # CHAT / PLAN / AUTORUN (all need a prompt)
                # ------------------------------------------------------
                prompt = data.get("prompt", "").strip()
                if not prompt:
                    await websocket.send_json({"type": "error", "content": "Empty prompt"})
                    continue

                log.info("Mode=%s | project=%s | prompt_len=%d", mode or "chat", project_path, len(prompt))

                # -- CHAT -- direct to Aider, no planning pass
                if mode == "chat" or mode == "":
                    session = get_session(project_path)
                    from engine.projects import registry, state
                    proj = registry.get_by_path(project_path)
                    if proj:
                        state.append_chat(proj.id, "user", prompt)

                    response_tokens = []
                    async for chunk in session.send(prompt):
                        await websocket.send_json(chunk)
                        if chunk.get("type") == "token":
                            response_tokens.append(chunk.get("content", ""))

                    if proj and response_tokens:
                        state.append_chat(proj.id, "agent", "".join(response_tokens))
                    continue

                # -- PLAN -- generate plan, return it, wait for user to approve
                if mode == "plan":
                    await websocket.send_json({"type": "status", "content": "Planning..."})
                    try:
                        plan = await generate_plan(prompt, project_path, auto_run=False)
                    except Exception as exc:
                        await websocket.send_json({"type": "error", "content": str(exc)})
                        await websocket.send_json({"type": "done", "content": ""})
                        continue

                    store_plan(plan)
                    await websocket.send_json({"type": "plan_ready", "content": plan.to_dict()})
                    await websocket.send_json({"type": "done", "content": ""})
                    continue

                # -- AUTORUN -- plan then execute automatically
                if mode == "autorun":
                    await websocket.send_json({"type": "status", "content": "Planning (Auto-Run mode)..."})
                    try:
                        plan = await generate_plan(prompt, project_path, auto_run=True)
                    except Exception as exc:
                        await websocket.send_json({"type": "error", "content": str(exc)})
                        await websocket.send_json({"type": "done", "content": ""})
                        continue

                    store_plan(plan)
                    await websocket.send_json({"type": "plan_ready", "content": plan.to_dict()})

                    active_ex = get_active_execution(project_path)
                    if active_ex:
                        active_ex.websockets.add(websocket)
                        for event in active_ex.event_buffer:
                            await websocket.send_json(event)
                        continue

                    executor = Executor(plan, runtime_budget=budget)
                    active_ex = ActiveExecution(project_path, executor)
                    active_ex.websockets.add(websocket)
                    register_active_execution(project_path, active_ex)
                    active_ex.task = asyncio.create_task(active_ex.run())
                    continue

                await websocket.send_json({
                    "type": "error",
                    "content": f"Unknown mode '{mode}'. Use: chat, plan, autorun",
                })

            except WebSocketDisconnect:
                raise
            except Exception as exc:
                log.exception("Error handling WS message (connection stays open)")
                try:
                    await websocket.send_json({"type": "error", "content": f"Internal error: {exc}"})
                    await websocket.send_json({"type": "done", "content": ""})
                except Exception:
                    pass
                continue

    except WebSocketDisconnect:
        log.info("WebSocket client disconnected")
        if project_path:
            try:
                active_ex = get_active_execution(project_path)
                if active_ex:
                    active_ex.websockets.discard(websocket)
            except Exception:
                pass
    finally:
        keepalive_task.cancel()