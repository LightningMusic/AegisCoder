"""Headless Aider bridge with idle-stall detection and write safety."""
import asyncio
import difflib
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import AsyncGenerator

import litellm
from aider.coders import Coder
from aider.io import InputOutput
from aider.models import Model

from engine.config import (AIDER_MODEL, EDIT_FORMAT, INFERENCE_IDLE_TIMEOUT_SECONDS,
                           INFERENCE_TIMEOUT_SECONDS, MAX_RETRIES, NUM_CTX,
                           OLLAMA_API_BASE)
from engine.safety.deletion_guard import any_unsafe, check_diff

log = logging.getLogger(__name__)
litellm.request_timeout = 3600.0  # distant circuit breaker; idle detection is authoritative

MSG_TOKEN = "token"
MSG_STATUS = "status"
MSG_WARNING = "warning"
MSG_ERROR = "error"
MSG_EDIT = "edit"
MSG_DONE = "done"

_active_generation_count = 0
_active_generation_lock = threading.Lock()

def _increment_active():
    global _active_generation_count
    with _active_generation_lock:
        _active_generation_count += 1

def _decrement_active():
    global _active_generation_count
    with _active_generation_lock:
        _active_generation_count = max(0, _active_generation_count - 1)

def get_active_generation_count() -> int:
    with _active_generation_lock:
        return _active_generation_count


class AegisIO(InputOutput):
    """Aider IO that never reads stdin and reports every signal as activity."""
    def __init__(self, out_queue: queue.Queue, session: "BridgeSession"):
        self._q = out_queue
        self.session = session
        self._discarded = False
        super().__init__(pretty=False, yes=True, fancy_input=False)

    def check_discarded(self):
        if self._discarded:
            log.warning("Discarded Aider IO used for %s", self.session.project_path)
            raise RuntimeError("Aider session was discarded/reset")
        self.session.touch()

    def _put(self, msg_type: str, content: str):
        if content:
            self._q.put({"type": msg_type, "content": str(content).rstrip()})

    def print(self, *msgs, **kwargs):
        self.check_discarded()
        self._put(MSG_TOKEN, " ".join(str(m) for m in msgs))

    def tool_output(self, *msgs, log_only=False, bold=False):
        self.check_discarded()
        text = " ".join(str(m) for m in msgs)
        log.debug("[aider] %s", text)
        if not log_only:
            self._put(MSG_STATUS, text)

    def tool_error(self, message="", strip=True):
        self.check_discarded()
        text = str(message).strip() if strip else str(message)
        log.warning("[aider error] %s", text)
        self._put(MSG_ERROR, text)

    def tool_warning(self, message="", strip=True):
        self.check_discarded()
        text = str(message).strip() if strip else str(message)
        log.warning("[aider warning] %s", text)
        self._put(MSG_WARNING, text)

    def confirm_ask(self, question, default="y", subject=None, explicit_yes_required=False,
                    group=None, allow_never=False):
        self.check_discarded()
        log.warning("[aider confirm_ask forced] %s", question)
        self._put(MSG_STATUS, f"[auto-confirmed] {question}")
        return True

    def prompt_ask(self, question, default="", subject=None):
        self.check_discarded()
        log.warning("[aider prompt_ask forced] %s", question)
        self._put(MSG_STATUS, f"[auto-answered] {question}")
        return default

    def get_input(self, root, rel_fnames, addable_rel_fnames, commands,
                  abs_read_only_fnames=None, edit_format=None):
        self.check_discarded()
        log.error("[aider get_input called -- forced empty headless response]")
        return ""

    def append_chat_history(self, text, linebreak=False, blockquote=False, strip=True):
        pass

    def write_text(self, filename, content):
        self.check_discarded()
        path = Path(filename)
        if not path.is_absolute():
            path = Path(self.session.project_path) / path
        if path.exists():
            old = path.read_text(encoding="utf-8", errors="replace")
            try:
                rel = path.relative_to(self.session.project_path).as_posix()
            except ValueError:
                rel = path.name
            diff = "".join(difflib.unified_diff(old.splitlines(keepends=True),
                                                   content.splitlines(keepends=True),
                                                   fromfile=f"a/{rel}", tofile=f"b/{rel}"))
            results = check_diff(diff, self.session.project_path)
            if any_unsafe(results):
                message = next(r.message for r in results if not r.safe)
                log.warning("Deletion blocked pending confirmation: %s", message)
                self._q.put({"type": "deletion_warning", "content": {
                    "plan_id": self.session.current_plan_id,
                    "step_id": self.session.current_step_id,
                    "message": message,
                    "project_path": self.session.project_path,
                }})
                self.session.deletion_approved_event.clear()
                self.session.awaiting_confirmation = True
                approved = self.session.deletion_approved_event.wait(timeout=60.0)
                self.session.awaiting_confirmation = False
                if self.session.is_stopped or not approved:
                    log.warning("Deletion rejected or timed out for %s", path)
                    raise RuntimeError("Deletion rejected or timed out")
                log.info("Deletion approved for %s", path)
        self.check_discarded()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.session.touch()


class BridgeSession:
    def __init__(self, project_path: str):
        self.project_path = str(Path(project_path).resolve())
        self._out_queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._lru_lock = threading.Lock()
        self._active = False
        self._thread: threading.Thread | None = None
        self._coder: Coder | None = None
        self._io: AegisIO | None = None
        self._retry_count = 0
        self.last_activity_time = time.monotonic()
        self.awaiting_confirmation = False
        self.deletion_approved_event = threading.Event()
        self.is_stopped = False
        self.current_plan_id = None
        self.current_step_id = None
        self.file_lru: list[str] = []

    def touch(self):
        self.last_activity_time = time.monotonic()

    def confirm_deletion(self):
        log.info("Deletion confirmation received for %s", self.project_path)
        self.deletion_approved_event.set()

    def _build_coder(self) -> Coder:
        os.environ["OLLAMA_API_BASE"] = OLLAMA_API_BASE
        model = Model(AIDER_MODEL)
        model.extra_params = {"num_ctx": NUM_CTX, "timeout": INFERENCE_TIMEOUT_SECONDS}
        self._io = AegisIO(self._out_queue, self)
        git_present = (Path(self.project_path) / ".git").exists()
        coder = Coder.create(main_model=model, io=self._io, edit_format=EDIT_FORMAT,
                             fnames=[], map_tokens=0, use_git=git_present,
                             auto_commits=git_present, stream=True)
        os.chdir(self.project_path)
        log.info("Coder built for %s | model=%s | edit_format=%s | num_ctx=%d",
                 self.project_path, AIDER_MODEL, EDIT_FORMAT, NUM_CTX)
        return coder

    def _resolve_relevant_files(self, prompt: str) -> list[str]:
        ignored = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
        root = Path(self.project_path)
        files = [p for p in root.rglob("*") if p.is_file() and not any(x.lower() in ignored for x in p.relative_to(root).parts)]
        prompt_lower = prompt.lower()
        matched = [p.relative_to(root).as_posix() for p in files
                   if p.name.lower() in prompt_lower or p.relative_to(root).as_posix().lower() in prompt_lower]
        if matched:
            return matched
        if len(files) <= 20:
            return [p.relative_to(root).as_posix() for p in files]
        self._out_queue.put({"type": MSG_WARNING, "content": "No files matched. Falling back to the 10 most recently modified files."})
        return [p.relative_to(root).as_posix() for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:10]]

    def add_file_to_context(self, rel_fname: str):
        if self._coder is None:
            return
        root = Path(self.project_path)
        with self._lru_lock:
            if rel_fname in self.file_lru:
                self.file_lru.remove(rel_fname)
            else:
                self._coder.add_rel_fname(rel_fname)
                log.info("Auto-added %s to Aider context", rel_fname)
            self.file_lru.append(rel_fname)
            def chars():
                return sum(len((root / name).read_text(encoding="utf-8", errors="replace"))
                           for name in self.file_lru if (root / name).is_file())
            while chars() > 40000 and len(self.file_lru) > 1:
                removed = self.file_lru.pop(0)
                self._coder.drop_rel_fname(removed)
                log.info("Pruned %s from Aider context LRU", removed)

    def _run_prompt(self, prompt: str):
        try:
            with self._lock:
                if self._coder is None:
                    self._coder = self._build_coder()
            matched = self._resolve_relevant_files(prompt)
            for filename in matched:
                self.add_file_to_context(filename)
            if matched and self._io:
                self._io._put(MSG_STATUS, f"Added files to session: {', '.join(matched)}")
            self._coder.run(with_message=prompt)
            self._out_queue.put({"type": MSG_DONE, "content": ""})
            self._retry_count = 0
        except Exception as exc:
            log.exception("Aider raised an exception during run")
            self._out_queue.put({"type": MSG_ERROR, "content": str(exc)})
            self._out_queue.put({"type": MSG_DONE, "content": ""})

    async def send(self, prompt: str) -> AsyncGenerator[dict, None]:
        if self._thread is not None and self._thread.is_alive():
            yield {"type": MSG_ERROR, "content": "Session is busy with another prompt"}
            return
        if self._retry_count >= MAX_RETRIES:
            yield {"type": MSG_ERROR, "content": f"Session hit the retry limit ({MAX_RETRIES}). Call reset() to start fresh."}
            return
        while not self._out_queue.empty():
            try: self._out_queue.get_nowait()
            except queue.Empty: break
        self.is_stopped = False
        self._active = True
        self.last_activity_time = time.monotonic()
        self._thread = threading.Thread(target=self._run_prompt, args=(prompt,), daemon=True,
                                        name=f"aider-{Path(self.project_path).name}")
        _increment_active()
        self._thread.start()
        try:
            while True:
                if (not self.awaiting_confirmation and
                    time.monotonic() - self.last_activity_time > INFERENCE_IDLE_TIMEOUT_SECONDS):
                    self._retry_count += 1
                    log.error("Session for %s stalled: no activity for %ds", self.project_path, INFERENCE_IDLE_TIMEOUT_SECONDS)
                    yield {"type": MSG_ERROR, "content": f"No response from the model for {INFERENCE_IDLE_TIMEOUT_SECONDS}s -- this looks stalled, not just slow. Aborting."}
                    yield {"type": MSG_DONE, "content": ""}
                    break
                try:
                    item = self._out_queue.get_nowait()
                    yield item
                    if item["type"] == MSG_DONE:
                        break
                except queue.Empty:
                    await asyncio.sleep(0.05)
        finally:
            self._active = False
            _decrement_active()

    def reset(self):
        log.info("Resetting bridge session for %s", self.project_path)
        with self._lock:
            if self._io is not None:
                self._io._discarded = True
                log.info("Discarded old Aider IO for %s", self.project_path)
            self.is_stopped = True
            self.deletion_approved_event.set()
            self._coder = None
            self._io = None
        self._retry_count = 0
        self._active = False
        while not self._out_queue.empty():
            try: self._out_queue.get_nowait()
            except queue.Empty: break

    def close(self):
        self.reset()

_sessions: dict[str, BridgeSession] = {}
_registry_lock = threading.Lock()

def get_session(project_path: str) -> BridgeSession:
    resolved = str(Path(project_path).resolve())
    with _registry_lock:
        if resolved not in _sessions:
            log.info("Creating new bridge session for %s", resolved)
            _sessions[resolved] = BridgeSession(resolved)
        return _sessions[resolved]

def close_session(project_path: str):
    resolved = str(Path(project_path).resolve())
    with _registry_lock:
        session = _sessions.pop(resolved, None)
        if session:
            session.is_stopped = True
            session.deletion_approved_event.set()
            session.close()
            log.info("Bridge session closed for %s", resolved)

def close_all_sessions():
    with _registry_lock:
        for session in _sessions.values():
            session.is_stopped = True
            session.deletion_approved_event.set()
            session.close()
        _sessions.clear()
    log.info("All bridge sessions closed")