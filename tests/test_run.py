"""Default paths must follow the application, even when launched elsewhere."""

import runpy
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("frozen", [False, True])
def test_default_output_follows_application(monkeypatch, tmp_path, frozen):
    root = Path(__file__).resolve().parents[1]
    main = runpy.run_path(str(root / "run.py"))["main"]
    serve = Mock()
    monkeypatch.setitem(main.__globals__, "serve", serve)
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "video-annotating-interface"))
    monkeypatch.setattr(sys, "argv", ["run.py", str(tmp_path / "clip.mp4"), "--no-browser"])
    monkeypatch.chdir(tmp_path)

    main()

    workspace = tmp_path / "app" if frozen else root
    assert serve.call_args.kwargs["workspace"] == workspace
    assert serve.call_args.args[1] == workspace / "outputs" / "clip.annotations.json"
