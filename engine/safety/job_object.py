"""
AegisCoder -- Windows Job Object resource governor.

A Job Object is an OS-level container that enforces hard resource limits on
a process and all of its children. If the limit is exceeded Windows kills the
job -- nothing outside the job is affected. This is the mechanism that keeps
AegisCoder from taking the whole system down during a long unattended run.

What we enforce here:
  - ProcessMemoryLimit: the engine process tree cannot exceed MAX_MEMORY_MB.
    If it does, Windows raises an access-denied error on the next allocation
    and the process crashes cleanly rather than paging the whole system out.
  - Process priority: BELOW_NORMAL so Windows always schedules the desktop,
    the Hyper-V VM, and everything else ahead of us.
  - CPU rate control via NUM_THREAD on Ollama's side (handled in
    ollama_manager.py via OLLAMA_NUM_PARALLEL + num_thread env vars).

Graceful fallback:
  If pywin32 is not installed or the call fails for any reason, we log a
  warning and continue without the Job Object. The engine still works --
  it just loses the hard memory cap. Process priority is set separately
  via psutil so it works even without pywin32.

See master plan sections 5.4 and 5.5.
"""
import logging
import os

log = logging.getLogger(__name__)

_job_handle: int | None = None


def apply(max_memory_mb: int):
    """
    Apply resource limits to the current process.
    Safe to call multiple times -- subsequent calls are no-ops.

    Args:
        max_memory_mb: Maximum RSS memory for this process tree in megabytes.
    """
    global _job_handle

    if _job_handle is not None:
        return

    _apply_priority()
    _apply_job_object(max_memory_mb)


# ---------------------------------------------------------------------------
# Priority (psutil -- works without pywin32)
# ---------------------------------------------------------------------------

def _apply_priority():
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        log.info("Process priority set to BELOW_NORMAL")
    except AttributeError:
        # psutil.BELOW_NORMAL_PRIORITY_CLASS only exists on Windows
        pass
    except Exception as exc:
        log.warning("Could not set process priority: %s", exc)


# ---------------------------------------------------------------------------
# Job Object (pywin32 -- Windows only)
# ---------------------------------------------------------------------------

def _apply_job_object(max_memory_mb: int):
    global _job_handle

    try:
        import win32job
        import win32process
        import win32api
        import win32con
    except ImportError:
        log.warning(
            "pywin32 not available -- Job Object memory cap not enforced. "
            "Install pywin32 to enable hard memory limits."
        )
        return

    try:
        hJob = win32job.CreateJobObject(None, "AegisCoder-Engine")

        # CreateJobObject returns None on failure (pywin32 stubs allow this)
        if hJob is None:
            raise RuntimeError("CreateJobObject returned None -- cannot apply memory cap")

        # Read current limits, update the memory field, write back.
        # Always read before write -- SetInformationJobObject replaces
        # the whole struct, so missing fields get zeroed.
        info = win32job.QueryInformationJobObject(
            hJob, win32job.JobObjectExtendedLimitInformation
        )

        max_bytes = max_memory_mb * 1024 * 1024
        info["ProcessMemoryLimit"] = max_bytes
        info["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY

        win32job.SetInformationJobObject(
            hJob, win32job.JobObjectExtendedLimitInformation, info
        )

        # Assign the current process (and all future children) to this job
        handle = win32api.OpenProcess(
            win32con.PROCESS_ALL_ACCESS, False, os.getpid()
        )
        win32job.AssignProcessToJobObject(hJob, handle)

        _job_handle = hJob
        log.info(
            "Job Object applied: memory cap = %d MB (%d bytes)",
            max_memory_mb,
            max_bytes,
        )

    except Exception as exc:
        log.warning("Could not apply Job Object: %s -- continuing without it", exc)