import queue
import threading
import time
from pathlib import Path

import pytest

from engine.aider_bridge import AegisIO, BridgeSession, MSG_WARNING


class FakeCoder:
    def __init__(self):
        self.added = []
        self.dropped = []
    def add_rel_fname(self, name):
        self.added.append(name)
    def drop_rel_fname(self, name):
        self.dropped.append(name)


def test_relevant_file_matching_and_fallbacks(tmp_path):
    (tmp_path / "main.py").write_text("x")
    (tmp_path / "readme.md").write_text("x")
    session = BridgeSession(str(tmp_path))
    assert session._resolve_relevant_files("update MAIN.py") == ["main.py"]
    assert set(session._resolve_relevant_files("make an app")) == {"main.py", "readme.md"}

    big = tmp_path / "big"
    big.mkdir()
    for index in range(21):
        path = big / f"file_{index}.py"
        path.write_text(str(index))
        time.sleep(0.002)
    result = session._resolve_relevant_files("no named files here")
    assert len(result) == 10
    assert session._out_queue.get_nowait()["type"] == MSG_WARNING


def test_discarded_io_rejects_all_overridden_paths(tmp_path):
    session = BridgeSession(str(tmp_path))
    io = AegisIO(queue.Queue(), session)
    io._discarded = True
    calls = [
        lambda: io.print("x"), lambda: io.tool_output("x"),
        lambda: io.tool_error("x"), lambda: io.tool_warning("x"),
        lambda: io.confirm_ask("x"), lambda: io.prompt_ask("x"),
        lambda: io.get_input("", [], [], []), lambda: io.write_text("x.txt", "x"),
    ]
    for call in calls:
        with pytest.raises(RuntimeError, match="discarded"):
            call()


def test_write_text_waits_for_confirmation_then_writes(tmp_path):
    target = tmp_path / "large.py"
    target.write_text("".join(f"line {i}\n" for i in range(10)))
    session = BridgeSession(str(tmp_path))
    io = AegisIO(queue.Queue(), session)
    worker = threading.Thread(target=lambda: io.write_text(str(target), "kept\n"))
    worker.start()
    warning = io._q.get(timeout=2)
    assert warning["type"] == "deletion_warning"
    assert target.read_text().startswith("line")
    session.confirm_deletion()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert target.read_text() == "kept\n"


def test_lru_prunes_over_context_budget(tmp_path):
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("x" * 20000)
    session = BridgeSession(str(tmp_path))
    session._coder = FakeCoder()
    for name in ("a.py", "b.py", "c.py"):
        session.add_file_to_context(name)
    assert session.file_lru == ["b.py", "c.py"]
    assert session._coder.dropped == ["a.py"]

def test_write_text_rejects_when_confirmation_times_out(tmp_path):
    class TimedOutEvent:
        def clear(self): pass
        def set(self): pass
        def wait(self, timeout):
            assert timeout == 60.0
            return False
    target = tmp_path / "large.py"
    original = "".join(f"line {i}\n" for i in range(10))
    target.write_text(original)
    session = BridgeSession(str(tmp_path))
    session.deletion_approved_event = TimedOutEvent()
    io = AegisIO(queue.Queue(), session)
    with pytest.raises(RuntimeError, match="timed out"):
        io.write_text(str(target), "kept\n")
    assert target.read_text() == original
