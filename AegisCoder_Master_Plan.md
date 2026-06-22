# AegisCoder Master Plan

A local, unlimited-token, Codex-style coding agent with its own desktop app and UI, built on the Ollama + Aider foundation already running on this machine.

Last updated: June 21, 2026

---

## 1. Vision

A desktop app (its own window, not a terminal session) that works like Codex: you give it a prompt for a project, it plans the work, then either you approve each step or it runs hands-off start to finish, editing real files in a real project folder. No API costs, no token limits, no usage caps -- everything runs on local hardware through Ollama and Aider. It should be able to start its own dependencies (Ollama, the Aider engine) without you manually opening a terminal, support multiple projects like Codex's repo-scoped sessions, and -- after the overnight incident -- never be able to take the whole system down again, no matter how hard it's working.

---

## 2. Existing Foundation (already built -- do not lose any of this)

Everything below already exists and works on this machine. AegisCoder is built *on top of* this, not as a replacement for it.

**Location:** `C:\Coding\AI\`

**The master setup script:** `CluadesLocalCodingSetup.ps1` is the source of truth for the whole environment. It is not a one-time installer you run and discard -- it's meant to be re-run any time the environment needs to be rebuilt or the helper scripts need to be regenerated. It currently:
- Checks prerequisites (admin rights, memory, internet)
- Sets up `C:\Coding\AI\` and the required environment variables (`OLLAMA_HOST=127.0.0.1:11434`, `OLLAMA_API_BASE=http://127.0.0.1:11434`, `AIDER_ANALYTICS=false`)
- Verifies/installs Ollama, pulls the base model, and builds a custom model profile via a generated Modelfile
- Installs Aider through `uv` (Python 3.11), now with proper permanent PATH persistence (see Section 3)
- Generates three helper scripts and a README via heredocs: `Test-Ollama.ps1`, `Start-Aider.ps1`, `Start-Aider-Here.ps1`

**Ollama models currently installed:**
- `local-code:7b` -- custom model profile, built from a generated Modelfile (`Modelfile.local-code-7b`)
- `qwen2.5-coder:7b` -- base coding model
- `qwen2.5:7b`
- `nomic-embed-text:latest`

**Aider:** installed via `uv tool install --python 3.11 aider-chat`, currently version 0.86.2, executable shimmed into `%USERPROFILE%\.local\bin`.

**Hardware:** Ryzen 5 7430U, 16 GB RAM, 12 logical cores, Windows 11 Pro, no dedicated GPU (so inference is CPU-bound -- slower is expected and accepted). Hyper-V is active and running a separate VM (`MC-SecondAcc`) for a second Minecraft account, which matters for the resource-governance design in Section 4.

---

## 3. Hard Constraints Carried Forward From Past Work

These aren't optional preferences -- they caused real, confirmed breakage before and apply to every script touched in this project:

- **ASCII-only in all PowerShell scripts.** No em dashes, checkmarks, box-drawing characters, or any other non-ASCII symbol. Non-ASCII characters got mangled into mojibake on this machine and broke PowerShell's parser outright (manifesting as phantom "missing string terminator" and "missing closing brace" errors). All four existing scripts were cleaned to strict ASCII, BOM-stripped, with normalized `\r\n` line endings. Any new script must follow the same rule.
- **PATH persistence for uv-installed tools is mandatory, not optional.** `Install-AiderWithUv` was patched to call `uv tool update-shell` plus a manual `[Environment]::SetEnvironmentVariable` fallback, because the original version only set `$env:Path` for the current session, which silently broke `aider` in any new terminal window.
- **Maximum failsafes, safety backups before destructive changes, and clear error messaging** -- an established working preference, now elevated to a hard requirement for this project given the overnight incident below.

---

## 4. The Overnight Incident and What It Means for the Design

Two things went wrong in the same session that directly shaped this plan:

**1. System-wide instability.** The setup was left running unattended overnight and the system was disturbed badly enough that the clock drifted by two hours. The most likely mechanism: sustained near-100% CPU usage for hours prevented normal OS housekeeping (time sync) from running, and/or starved the Hyper-V VM of scheduling cycles badly enough to desync its clock -- VM clock drift under host CPU starvation is a well-known failure mode. Whatever the exact mechanism, the underlying cause is the same: nothing was capping how much of the system Ollama/Aider were allowed to consume, and there was no limit on how long an unattended process could keep running.

**2. A file got overwritten, losing existing code.** Research into Aider and Ollama's actual behavior turned up two concrete, documented causes, either of which (likely both) explains this:

- **Ollama silently truncates context it wasn't told to expect.** Ollama's local API defaults to a tiny 2,048-token context window, and unlike most LLM servers it does not error when a request exceeds it -- it silently drops the oldest content to make the request fit, with no warning. *Confirmed via `ollama show local-code:7b`: this model's Modelfile already sets `num_ctx 8192` explicitly, so it was not sitting on the silent 2k default.* 8192 is reasonable but can still get tight once a large file, conversation history, and any repo-map context are combined -- worth a modest bump for headroom, but this is no longer the prime suspect.
- **Aider defaults to the riskiest edit format for unrecognized local models.** For models it doesn't specifically recognize (which includes custom local Ollama models like `local-code:7b`), Aider falls back to the "whole file" edit format -- the model has to retype the *entire* file, changed and unchanged code alike. A 7B model under load can get lazy, confused, or run out of steam and return only the new code, and because "whole" mode replaces the entire file with whatever comes back, that wipes out everything else. Given the context-window finding above, *this is now the leading suspect* for what actually happened.

Both of these are fixable with explicit configuration rather than relying on defaults, and both are addressed directly in the architecture below.

---

## 5. Architecture Decisions

### 5.1 Engine: keep Aider, drive it as a library

Recommendation, with reasoning: build on Aider rather than a from-scratch agent loop. The hardest part of a coding agent isn't the chat loop, it's safely turning model output into correct file edits -- picking relevant files, generating valid diffs, applying them without corruption, handling a weaker model's flakier output, rolling back on failure. Aider has years of refinement on exactly that, including edit formats specifically tuned for weaker/local models, and it already commits every accepted edit to git automatically. AegisCoder will import Aider's `Coder` class directly in Python (not shell out to the CLI), with a custom `InputOutput` implementation so all of Aider's prompts and output are captured programmatically instead of expecting an interactive terminal.

### 5.2 Edit safety, configured explicitly from day one

- `edit_format` forced to `"diff"` (search/replace blocks) -- never left to auto-select "whole". Diff format only ever touches the lines that actually change, so there's no mechanism for it to drop unrelated code, and a malformed diff fails cleanly with an error instead of wiping the file.
- `num_ctx` set explicitly and generously (8k+, scaled up for larger files) in the Modelfile and on every API call -- never left at Ollama's silent 2k default.
- A custom large-deletion guard: before any edit is applied, the engine checks what fraction of the existing file the diff would remove. If a change would strip an unusually large portion of existing code, it pauses and flags that specific step for confirmation -- even in Auto-Run mode -- instead of auto-applying a possible wipeout. Everything else in Auto-Run still proceeds hands-off.

### 5.3 UI: native desktop window, Python end-to-end

`pywebview` for the actual window (native OS window, no Electron/Node toolchain, no Rust/Tauri toolchain -- stays inside the Python stack already in use), backed by a local FastAPI server running everything in async background tasks/threads, with an HTML/CSS/JS frontend rendered inside the window. Packages down to a single `.exe` via PyInstaller later, with a Start Menu shortcut.

### 5.4 Process isolation -- the actual fix for "freeze the app, not the system"

Three separate OS processes, not one monolithic app:
1. `ollama.exe serve` -- Ollama's own server
2. The engine (FastAPI + Aider) -- the part doing the actual thinking/editing
3. The UI shell (`pywebview`) -- the part you see

If the engine hangs or gets killed for exceeding its resource limits, the UI process stays alive and shows "Engine unresponsive -- restarting" instead of the whole window freezing. This separation is what makes it possible to contain a runaway to just the engine, never the OS.

### 5.5 Resource governance -- hard caps, not suggestions

- **Windows Job Objects** (via `pywin32`) wrap the engine and Ollama process trees with real, OS-enforced CPU and memory ceilings. If exceeded, Windows kills *that job* -- nothing else on the system is affected.
- **Ollama's own throttle settings**: `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`, and a capped `num_thread` so Ollama never claims all 12 logical cores -- a few are always left free for the OS and everything else running (including the Hyper-V VM).
- **Below Normal process priority** so Windows' scheduler always favors the desktop and other apps first.
- Net effect: worst case, the model just runs slower. The desktop, the VM, and everything else stay responsive. This was explicitly accepted as an acceptable tradeoff ("I don't care if it takes longer").

### 5.6 Watchdog and bounded retries

- Every inference call gets a hard timeout. If exceeded, the watchdog kills that specific call, logs it, and marks the step failed in the plan -- it does not hang indefinitely.
- Any automatic retry logic is capped at a small fixed number of attempts with backoff. No unbounded retry loop is ever allowed to exist -- this is the most likely actual mechanism behind the overnight runaway.
- A lightweight health-check loop pings Ollama periodically; if it's unresponsive, the engine can restart it automatically.

### 5.7 Unattended runtime budget

A configurable "auto-pause after N hours with no input" setting (conservative default, e.g. 2 hours). After the budget is hit, the app pauses itself and waits rather than continuing to run all night unsupervised. You can always resume manually.

### 5.8 Crash-safe state, always

Every plan step and diff is written to disk -- and committed to git, which Aider already does per edit -- before moving to the next step. If the app is killed or the machine reboots, reopening a project resumes exactly where it left off. Nothing is lost.

### 5.9 Undo: a checkpoint timeline, not just one undo button

Since Aider already commits every accepted edit to git, "undo" is really "revert that commit." The UI surfaces this as a timeline of checkpoints -- every edit shows up as its own entry with a one-click "revert to here" -- rather than a single linear undo, so any individual change can be rolled back without losing later ones.

### 5.10 Autonomy dial: Manual vs Auto-Run

Two modes, selectable per project, both built on the exact same safety foundation:
- **Manual** -- the Architect model proposes a plan, you review/approve/edit/reject each step before the Editor phase executes it.
- **Auto-Run** -- give it a prompt and walk away. Plan and execution happen without per-step approval, but every other safety net stays active: forced diff format, git checkpoint per edit, the large-deletion guard, a live activity feed so you can see what happened even though you didn't approve each step, and a single Stop button to abort at any point.

### 5.11 Plan -> Approve/Auto-Run -> Execute pipeline

Built on Aider's existing Architect/Editor two-model mode, which already splits "propose a solution" from "turn it into actual file edits." AegisCoder intercepts the Architect's proposed plan before it reaches the Editor phase, structures it into a numbered checklist (the Architect is prompted to output discrete steps, not free prose), and gates the Editor phase behind either manual approval or the Auto-Run safety checks above.

### 5.12 Projects, Codex-style

- A `projects.json` registry in `%APPDATA%\AegisCoder\` -- name, folder path, last opened, model.
- Each project gets its own state folder (`%APPDATA%\AegisCoder\projects\<id>\`) holding chat history, plan history, and execution state, independent of every other project.
- Switching projects swaps Aider's working directory and reloads that project's history into the UI -- the same mental model as Codex's repo-scoped sessions.
- Per-project overrides (resource budget, autonomy mode) are designed into the schema from the start even if not all are exposed in the UI in v1.

### 5.13 Startup sequence

UI shell launches first -> it spawns the engine process -> the engine health-checks Ollama and, if it isn't already running, starts it itself with all resource caps applied at launch (not added after the fact) -> the engine attaches Aider to whichever project is currently active. You should never need to manually open a terminal to start Ollama or Aider again.

---

## 6. Build Phases

Safety is built in from the first line of engine code, not bolted on after a working UI exists.

1. **Engine skeleton** -- drive Aider headless via its Python API against `local-code:7b`, with `edit_format=diff` and an explicit generous `num_ctx` set from the start. Prove a prompt can go in and a streamed response can come out through our own code, no terminal involved.
2. **Safety & resource governor** -- process separation (Ollama / engine / UI as distinct processes), Job Object CPU/memory caps, Below Normal priority, watchdog with per-call timeouts and bounded retries, crash-safe state persisted after every step, unattended runtime budget.
3. **Plan -> Approve/Auto-Run -> Execute pipeline** -- Architect/Editor split with structured plan output, manual approval gate, Auto-Run mode, and the large-deletion guard.
4. **Projects system** -- multi-project registry, per-project history and state, project switching.
5. **Desktop UI** (`pywebview`) -- chat panel, plan checklist, diff viewer with the checkpoint/undo timeline, file tree, project picker, autonomy mode dial, settings (resource limits, runtime budget).
6. **Packaging** -- single PyInstaller `.exe`, auto-launches Ollama on app start, Start Menu shortcut.

---

## 7. Open Items for Future Sessions

- Exact resource limit defaults: how many of the 12 logical cores to leave free, and the specific memory ceiling for the Job Object
- Exact default for the unattended runtime budget (currently proposed: 2 hours)
- Exact threshold for the large-deletion guard (e.g., flag any edit removing more than ~40% of a file's existing lines)
- Whether to add automatic lint/test execution as an extra correctness check after each Auto-Run edit
- ~~Whether to check and fix `num_ctx` in the existing `Modelfile.local-code-7b`~~ -- checked via `ollama show local-code:7b`: already set to `8192`, not the silent 2k default. Modest headroom increase still worth considering, but no longer urgent or suspected as the primary cause.

---

## 8. What Stays Exactly As-Is

To be explicit about what this project does *not* touch: `CluadesLocalCodingSetup.ps1` and its generated helper scripts (`Test-Ollama.ps1`, `Start-Aider.ps1`, `Start-Aider-Here.ps1`) remain fully functional as a manual CLI fallback. Ollama, the installed models, and the Aider installation are the foundation AegisCoder is built on top of, not replaced.

`C:\Coding\AI\` is the environment/tooling layer (Ollama setup, Modelfiles, Aider install) and stays exactly where it is. AegisCoder itself lives in a separate location, `C:\Coding\Python Projects\AegisCoder\`, alongside other standalone software projects rather than inside the environment-setup folder -- it only ever talks to that environment over the local network (Ollama's API at `127.0.0.1:11434`) and through the `aider` Python package installed via `uv`, so it has no file-path coupling to `C:\Coding\AI\` and is unaffected if that folder is ever rebuilt or reset.
