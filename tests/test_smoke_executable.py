"""Check that smoke-test cleanup stops the Windows bootloader's whole tree."""

import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


def test_windows_stops_tree_before_waiting(monkeypatch):
    namespace = runpy.run_path(str(Path(__file__).parents[1] / "scripts/smoke_executable.py"))
    stop = namespace["stop_process"]
    events = []
    run = Mock(side_effect=lambda *args, **kwargs: events.append("taskkill"))
    monkeypatch.setitem(stop.__globals__, "os", SimpleNamespace(name="nt"))
    monkeypatch.setitem(stop.__globals__, "subprocess", SimpleNamespace(run=run))
    process = Mock(pid=123)
    process.poll.return_value = None
    process.wait.side_effect = lambda **kwargs: events.append("wait")
    stop(process)
    assert events == ["taskkill", "wait"]
    assert run.call_args.args[0] == ["taskkill", "/PID", "123", "/T", "/F"]
    assert run.call_args.kwargs["check"] is True
    process.terminate.assert_not_called()
