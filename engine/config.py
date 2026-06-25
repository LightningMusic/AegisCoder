"""
AegisCoder engine configuration.
All tuneable values live here. Nothing is scattered across modules.

SAFETY NOTES (see master plan section 5.2 and 5.5):
  - EDIT_FORMAT must always be "diff". Never change it to "whole".
    "whole" mode lets the model rewrite entire files and a 7B model
    under load will silently drop existing code. "diff" only touches
    lines that actually change and fails cleanly if the block does not
    match -- it cannot wipe a file.
  - NUM_CTX must always be set explicitly. Ollama's silent default is
    2048 tokens, which is too small for real files plus conversation
    history. It truncates silently with no error.
  - NUM_THREAD caps Ollama to 8 of the 12 available cores, leaving
    4 free for the OS, the Hyper-V VM, and everything else. The model
    runs slower but the system stays responsive.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Ollama connection
# ---------------------------------------------------------------------------
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "127.0.0.1:11434")
OLLAMA_API_BASE = f"http://{OLLAMA_HOST}"

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
# The model name as registered in Ollama
MODEL_NAME = os.getenv("MODEL_NAME", "local-code:7b")

# The prefix Aider (via litellm) expects for Ollama models
AIDER_MODEL = f"ollama/{MODEL_NAME}"

# Context window token budget.
# The Modelfile already sets num_ctx=8192 in Ollama, but we also set it here
# so Aider's internal prompt budgeting agrees with what the server actually has.
# 16384 gives comfortable headroom for a medium-size file + history + repo-map.
NUM_CTX = int(os.getenv("NUM_CTX", "16384"))

# ---------------------------------------------------------------------------
# Edit safety -- DO NOT CHANGE EDIT_FORMAT
# ---------------------------------------------------------------------------
# "diff" = search/replace blocks. Only touched lines change. Cannot wipe a file.
# "whole" = model must retype the entire file. 7B models drop code silently.
EDIT_FORMAT = "diff"

# ---------------------------------------------------------------------------
# Resource limits (enforced by OS Job Objects in Phase 2)
# For Phase 1 these govern Ollama startup env vars only.
# Hardware: Ryzen 5 7430U, 12 logical cores, 16 GB RAM.
# ---------------------------------------------------------------------------
NUM_THREAD = int(os.getenv("NUM_THREAD", "8"))   # 8 of 12 cores -- 4 kept free
MAX_MEMORY_MB = int(os.getenv("MAX_MEMORY_MB", "8192"))  # 8 GB ceiling

# ---------------------------------------------------------------------------
# Engine server
# ---------------------------------------------------------------------------
# REMOTE_ACCESS_ENABLED: set to true in .env to allow connections from
# other devices (phone via Tailscale). When false, only localhost works.
REMOTE_ACCESS_ENABLED = os.getenv("REMOTE_ACCESS_ENABLED", "false").lower() == "true"

# Bind to all interfaces when remote access is on so Tailscale can reach us.
# Stays on 127.0.0.1 when off -- never exposed to the internet either way.
ENGINE_HOST = "0.0.0.0" if REMOTE_ACCESS_ENABLED else "127.0.0.1"
ENGINE_PORT = int(os.getenv("ENGINE_PORT", "8765"))

# ACCESS_TOKEN: the PIN/password the mobile app must supply.
# Generate one with: scripts/Setup-Remote.ps1
# Leave blank to deny all remote connections (safe default).
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")

# ---------------------------------------------------------------------------
# Timeouts and retry limits
# Every inference call is killed if it exceeds INFERENCE_TIMEOUT_SECONDS.
# No unbounded retry loops -- see master plan section 5.6.
# ---------------------------------------------------------------------------
INFERENCE_TIMEOUT_SECONDS = int(os.getenv("INFERENCE_TIMEOUT_SECONDS", "180"))
OLLAMA_STARTUP_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_STARTUP_TIMEOUT_SECONDS", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = float(os.getenv("RETRY_BACKOFF_SECONDS", "5.0"))

# ---------------------------------------------------------------------------
# Unattended runtime budget (Phase 2 enforces this)
# App auto-pauses after this many hours with no user interaction.
# ---------------------------------------------------------------------------
RUNTIME_BUDGET_HOURS = float(os.getenv("RUNTIME_BUDGET_HOURS", "2.0"))

# ---------------------------------------------------------------------------
# Large-deletion guard (Phase 3 enforces this)
# If a diff would remove more than this fraction of a file's existing lines,
# the engine pauses and asks for confirmation even in Auto-Run mode.
# ---------------------------------------------------------------------------
DELETION_GUARD_THRESHOLD = float(os.getenv("DELETION_GUARD_THRESHOLD", "0.40"))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
LOG_DIR = os.getenv("LOG_DIR", "logs")
DATA_DIR = os.getenv("DATA_DIR", "data")