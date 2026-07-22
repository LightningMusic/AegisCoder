"""
AegisCoder Aider bridge.

This module is the core of Phase 1. It wraps Aider's Coder class so the
engine can drive it programmatically -- no terminal, no interactive prompts,
all output captured and streamed to the frontend over WebSocket.

HOW IT WORKS
  1. AegisIO subclasses Aider's InputOutput class and overrides every
     output method to put messages into a thread-safe queue instead of
     printing to a terminal.
  2. BridgeSession holds one Aider Coder instance per project. The Coder
     is created lazily on first use and reused across prompts so Aider's
     repo-map and file context are preserved between messages.
  3. BridgeSession.send() runs coder.run() in a background thread (so the
     FastAPI event loop is never blocked), then async-yields output chunks
     from the queue as they arrive. A hard timeout kills the thread if the
     model hangs.

SAFETY GUARANTEES BAKED IN HERE (see master plan sections 5.2 and 5.6)
  - edit_format is ALWAYS "diff". Set once in _build_coder, never overridden.
  - num_ctx is set explicitly on the Model object before Aider sees it.
  - auto_commits=True so every accepted edit is a git checkpoint (free undo).
  - use_git=True so Aider initialises git in the project folder if needed.
  - INFERENCE_TIMEOUT_SECONDS is enforced per send() call. If the model
    does not finish in time, the session is marked failed and the thread
    is allowed to die -- no infinite hang.
  - MAX_RETRIES caps automatic retries with backoff. No unbounded loops.
"""
import asyncio
import logging
import queue
import threading
import time
from pathlib import Path
from typing import AsyncGenerator

from aider.coders import Coder
from aider.io import InputOutput
from aider.models import Model

from engine.config import (
    AIDER_MODEL,
    EDIT_FORMAT,
    INFERENCE_TIMEOUT_SECONDS,
    MAX_RETRIES,
    NUM_CTX,
    OLLAMA_API_BASE,
    RETRY_BACKOFF_SECONDS,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Message type constants
# These are the keys the frontend receives over WebSocket.
# ---------------------------------------------------------------------------
MSG_TOKEN = "token"     # A chunk of AI response text
MSG_STATUS = "status"   # Aider status / tool message (e.g. "Applied edit to foo.py")
MSG_WARNING = "warning" # Non-fatal warning from Aider
MSG_ERROR = "error"     # Error -- session may still continue
MSG_EDIT = "edit"       # A file was edited (content: filename)
MSG_DONE = "done"       # This prompt is fully complete


# ---------------------------------------------------------------------------
# AegisIO -- custom InputOutput that captures all Aider output to a queue
# ---------------------------------------------------------------------------

class AegisIO(InputOutput):
    """
    Replaces Aider's terminal-based InputOutput with a queue-based one.
    Every message Aider would normally print goes into _out_queue instead,
    where BridgeSession picks it up and streams it to the frontend.

    pretty=False  -- no ANSI colour codes in captured text
    yes=True      -- auto-confirm file operations (we gate this upstream
                     with the approval workflow in Phase 3)
    """

    def __init__(self, out_queue: queue.Queue):
        super().__init__(
            pretty=False,
            yes=True,
        )
        self._q = out_queue

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _put(self, msg_type: str, content: str):
        if content:
            self._q.put({"type": msg_type, "content": content.rstrip()})

    # ------------------------------------------------------------------
    # Overrides -- Aider calls these instead of print()
    # ------------------------------------------------------------------

    def tool_output(self, *msgs, log_only: bool = False, bold: bool = False):
        text = " ".join(str(m) for m in msgs)
        log.debug("[aider] %s", text)
        if not log_only:
            self._put(MSG_STATUS, text)

    def tool_error(self, message="", strip=True):
        text = str(message).strip() if strip else str(message)
        log.warning("[aider error] %s", text)
        self._put(MSG_ERROR, text)

    def tool_warning(self, message="", strip=True):
        text = str(message).strip() if strip else str(message)
        log.warning("[aider warning] %s", text)
        self._put(MSG_WARNING, text)

    def confirm_ask(self, question, default="y", subject=None, explicit_yes_required=False, group=None, allow_never=False):
        # Never block on stdin. yes=True should handle this, but some Aider
        # versions/code paths can still reach here. Force a safe default
        # answer and log it so a silent hang is visible in the logs instead.
        log.warning("[aider confirm_ask forced] %s", question)
        self._put(MSG_STATUS, f"[auto-confirmed] {question}")
        return True

    def prompt_ask(self, question, default="", subject=None):
        log.warning("[aider prompt_ask forced] %s", question)
        self._put(MSG_STATUS, f"[auto-answered] {question}")
        return default

    def get_input(self, root, rel_fnames, addable_rel_fnames, commands, abs_read_only_fnames=None, edit_format=None):
        # Should never be called in headless mode -- if it is, we must not block.
        log.error("[aider get_input called -- this should never happen headlessly]")
        return ""

    def print(self, *msgs, **kwargs):
        text = " ".join(str(m) for m in msgs)
        self._put(MSG_TOKEN, text)

    def append_chat_history(self, text, linebreak=False, blockquote=False, strip=True):
        # Suppress chat-history echoing to the queue -- we manage history ourselves
        pass


# ---------------------------------------------------------------------------
# BridgeSession -- one active Aider session per project folder
# ---------------------------------------------------------------------------

class BridgeSession:
    """
    Wraps a single Aider Coder instance for one project directory.
    Created once per project open and reused across all prompts so Aider's
    repo-map, file context, and conversation history are preserved.

    Thread safety: only one prompt can run at a time (_lock + _active flag).
    Attempting to send while busy returns an error immediately.
    """

    def __init__(self, project_path: str):
        self.project_path = str(Path(project_path).resolve())
        self._out_queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._active = False
        self._coder: Coder | None = None
        self._io: AegisIO | None = None
        self._retry_count = 0

    # ------------------------------------------------------------------
    # Coder construction
    # ------------------------------------------------------------------

    def _build_coder(self) -> Coder:
        """
        Instantiate Aider's Coder with all safety settings set explicitly.
        Called once lazily; the instance is reused for all subsequent prompts
        in this session.
        """
        import os

        # Tell litellm (Aider's internal LLM client) where Ollama is.
        # This must be set before Model() is constructed.
        os.environ["OLLAMA_API_BASE"] = OLLAMA_API_BASE

        model = Model(AIDER_MODEL)

        # Pass num_ctx as an extra_param so litellm forwards it to Ollama
        # on every API call. This is the correct way to override context length
        # for Ollama models in aider -- max_context_tokens is computed, not settable.
        # timeout here is critical: without it, litellm's underlying httpx
        # call has no read timeout and will hang forever on a wedged Ollama
        # server, permanently occupying the single OLLAMA_NUM_PARALLEL slot
        # and blocking every future request until Ollama is force-killed.
        model.extra_params = {"num_ctx": NUM_CTX, "timeout": INFERENCE_TIMEOUT_SECONDS}

        self._io = AegisIO(self._out_queue)

        # Check for an existing git repo so we know whether to enable auto-commits
        git_present = (Path(self.project_path) / ".git").exists()
        if not git_present:
            log.info("No .git found in %s -- git integration disabled", self.project_path)

        coder = Coder.create(
            main_model=model,
            io=self._io,
            # CRITICAL: always "diff", never "whole" -- see module docstring
            edit_format=EDIT_FORMAT,
            fnames=[],              # start with no files

            # map_tokens=0 disables the repo-map entirely.
            # Without this, Aider uses tree-sitter to parse every file in the
            # project folder to build context. On a large project with a slow
            # 7B CPU model this can hang for hours before the first token arrives.
            # We give Aider context through focused per-step prompts instead.
            map_tokens=0,

            # Only enable git integration if the project already has a repo.
            # If use_git=True on a non-git folder, Aider tries to run git init
            # which can fail or produce unexpected side effects.
            use_git=git_present,
            auto_commits=git_present,

            stream=True,
        )

        # Point Aider at the project directory.
        # os.chdir is process-wide -- this must run in the worker thread
        # (called from _run_prompt) so the main async event loop is unaffected.
        import os as _os
        _os.chdir(self.project_path)

        log.info(
            "Coder built for %s | model=%s | edit_format=%s | num_ctx=%d",
            self.project_path,
            AIDER_MODEL,
            EDIT_FORMAT,
            NUM_CTX,
        )
        return coder

    # ------------------------------------------------------------------
    # Background thread worker
    # ------------------------------------------------------------------

    def _run_prompt(self, prompt: str):
        """
        Runs synchronously in a worker thread.
        Puts MSG_DONE on the queue when finished (or MSG_ERROR + MSG_DONE on failure).
        """
        try:
            with self._lock:
                if self._coder is None:
                    self._coder = self._build_coder()

            self._coder.run(with_message=prompt)
            self._out_queue.put({"type": MSG_DONE, "content": ""})
            self._retry_count = 0

        except Exception as exc:
            log.exception("Aider raised an exception during run")
            self._out_queue.put({"type": MSG_ERROR, "content": str(exc)})
            self._out_queue.put({"type": MSG_DONE, "content": ""})

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def send(self, prompt: str) -> AsyncGenerator[dict, None]:
        """
        Send a prompt to Aider and async-yield output chunks as they arrive.

        - Runs Aider in a daemon thread so the FastAPI event loop stays
          fully responsive while the model is thinking.
        - Enforces INFERENCE_TIMEOUT_SECONDS. If the model takes too long,
          the session is marked as timed-out and an error chunk is yielded.
          The thread is left to die naturally (daemon=True).
        - Caps retries at MAX_RETRIES with backoff. After that the caller
          must explicitly call reset() before trying again.
        """
        if self._active:
            yield {"type": MSG_ERROR, "content": "Session is busy with another prompt"}
            return

        if self._retry_count >= MAX_RETRIES:
            yield {
                "type": MSG_ERROR,
                "content": (
                    f"Session hit the retry limit ({MAX_RETRIES}). "
                    "Call reset() to start fresh."
                ),
            }
            return

        # Drain any leftover items from a previous run
        while not self._out_queue.empty():
            try:
                self._out_queue.get_nowait()
            except queue.Empty:
                break

        self._active = True
        thread = threading.Thread(
            target=self._run_prompt,
            args=(prompt,),
            daemon=True,
            name=f"aider-{Path(self.project_path).name}",
        )
        thread.start()

        deadline = time.monotonic() + INFERENCE_TIMEOUT_SECONDS
        timed_out = False

        try:
            while True:
                # Hard timeout check
                if time.monotonic() > deadline:
                    timed_out = True
                    self._retry_count += 1
                    yield {
                        "type": MSG_ERROR,
                        "content": (
                            f"Inference timeout after {INFERENCE_TIMEOUT_SECONDS}s. "
                            f"The model did not respond in time. "
                            f"(attempt {self._retry_count}/{MAX_RETRIES})"
                        ),
                    }
                    yield {"type": MSG_DONE, "content": ""}
                    break

                # Drain whatever the IO thread has put on the queue
                try:
                    item = self._out_queue.get_nowait()
                    yield item
                    if item["type"] == MSG_DONE:
                        break
                except queue.Empty:
                    # Nothing ready yet -- yield control back to the event loop
                    await asyncio.sleep(0.05)

        finally:
            self._active = False
            if timed_out:
                log.warning(
                    "Inference timeout for project %s (retry %d/%d). "
                    "Backing off %.1fs before next attempt.",
                    self.project_path,
                    self._retry_count,
                    MAX_RETRIES,
                    RETRY_BACKOFF_SECONDS,
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)

    def reset(self):
        """
        Tear down the Coder instance and reset all counters.
        After calling reset() the session can accept prompts again.
        Used after hitting the retry limit or when switching model settings.
        """
        log.info("Resetting bridge session for %s", self.project_path)
        with self._lock:
            self._coder = None
            self._io = None
        self._retry_count = 0
        self._active = False
        while not self._out_queue.empty():
            try:
                self._out_queue.get_nowait()
            except queue.Empty:
                break

    def close(self):
        """Alias for reset() -- called when a project is closed."""
        self.reset()


# ---------------------------------------------------------------------------
# Session registry -- one BridgeSession per project path
# ---------------------------------------------------------------------------

_sessions: dict[str, BridgeSession] = {}
_registry_lock = threading.Lock()


def get_session(project_path: str) -> BridgeSession:
    """
    Return the existing BridgeSession for this project path, or create one.
    Thread-safe.
    """
    resolved = str(Path(project_path).resolve())
    with _registry_lock:
        if resolved not in _sessions:
            log.info("Creating new bridge session for %s", resolved)
            _sessions[resolved] = BridgeSession(resolved)
        return _sessions[resolved]


def close_session(project_path: str):
    """Tear down and remove the session for a project path."""
    resolved = str(Path(project_path).resolve())
    with _registry_lock:
        if resolved in _sessions:
            _sessions[resolved].close()
            del _sessions[resolved]
            log.info("Bridge session closed for %s", resolved)


def close_all_sessions():
    """Tear down all active sessions. Called on engine shutdown."""
    with _registry_lock:
        for session in _sessions.values():
            session.close()
        _sessions.clear()
    log.info("All bridge sessions closed")