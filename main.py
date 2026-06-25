"""
AegisCoder -- top-level entry point.

Usage:
  uv run python main.py               -- desktop app (pywebview window)
  uv run python main.py --engine-only -- engine only (for dev / subprocess use)

The desktop app (default) opens a native window via ui/shell.py, which
spawns the engine as a child process and points the window at it.

The engine-only mode starts FastAPI + uvicorn directly. Used by:
  - The shell.py subprocess call (sets AEGISCODER_SUBPROCESS env var)
  - scripts/Run-Dev.ps1 for browser-based development
"""
import os
import sys
from engine.app import create_app, serve

def _is_engine_only() -> bool:
    return (
        "--engine-only" in sys.argv
        or os.environ.get("AEGISCODER_SUBPROCESS") == "1"
    )


def _run_engine_only() -> None:
    from engine.app import serve
    serve()


def _run_desktop() -> None:
    from ui.shell import launch
    launch()


def main() -> None:
    if _is_engine_only():
        _run_engine_only()
        return

    try:
        import webview as _webview_check  # noqa: F401
    except ImportError:
        print(
            "[INFO] pywebview not installed -- running in browser mode.\n"
            "       Open http://127.0.0.1:8765 in your browser.\n"
            "       Run: uv add pywebview   to get the desktop window."
        )
        _run_engine_only()
        return

    _run_desktop()


if __name__ == "__main__":
    main()