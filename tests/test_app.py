import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from video_annotating_interface.app import AnnotationStore


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "sample.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
    for i in range(5):
        writer.write(np.full((48, 64, 3), i * 35, np.uint8))
    writer.release()
    return path


def test_points_boxes_absence_and_resume(video, tmp_path):
    output = tmp_path / "labels.json"
    suggestions = tmp_path / "suggestions.json"
    suggestions.write_text(
        json.dumps(
            {
                "format": "video-annotating-interface-suggestions-v1",
                "frames": {"0": [{"type": "point", "x": 12, "y": 14}]},
            }
        )
    )
    store = AnnotationStore(video, output, suggestions)
    assert store.state()["suggestions"]["0"][0]["x"] == 12
    assert store.state()["frames"] == {}
    assert store.frame_jpeg(4).startswith(b"\xff\xd8")
    objects = [
        {"type": "point", "x": 12.345, "y": 14.2},
        {"type": "box", "x": 20, "y": 10, "width": 12, "height": 18},
    ]
    assert store.set_frame(0, "annotated", objects)["objects"][0]["x"] == 12.35
    store.set_absent_range(1, 3)
    store.clear_frame(2)
    store.close()
    saved = json.loads(output.read_text())
    assert saved["format"] == "video-annotating-interface-v1"
    assert set(saved["frames"]) == {"0", "1", "3"}
    assert saved["frames"]["0"]["objects"][1]["type"] == "box"
    resumed = AnnotationStore(video, output)
    assert resumed.state()["frames"] == saved["frames"]
    resumed.close()


def test_invalid_input_does_not_change_saved_labels(video, tmp_path):
    output = tmp_path / "labels.json"
    store = AnnotationStore(video, output)
    store.set_frame(0, "absent", [])
    original = output.read_bytes()
    for status, objects in [
        ("annotated", []),
        ("absent", [{"type": "point", "x": 1, "y": 2}]),
        ("annotated", [{"type": "box", "x": 60, "y": 1, "width": 8, "height": 3}]),
        ("annotated", [{"type": "point", "x": float("nan"), "y": 2}]),
    ]:
        with pytest.raises(ValueError):
            store.set_frame(1, status, objects)
        assert output.read_bytes() == original
    with pytest.raises(ValueError):
        store.set_absent_range(0, 10)
    assert output.read_bytes() == original
    store.close()


def test_plain_point_suggestions_skip_missing_coordinates(video, tmp_path):
    suggestions = tmp_path / "suggestions.json"
    suggestions.write_text(
        json.dumps(
            [
                {"frame": 0, "x": 5, "y": 6, "confidence": 0.8},
                {"frame": 1, "x": None, "y": None},
            ]
        )
    )
    store = AnnotationStore(video, tmp_path / "labels.json", suggestions)
    assert store.state()["suggestions"] == {
        "0": [{"type": "point", "x": 5.0, "y": 6.0}],
    }
    store.close()


def test_resume_latest_save_and_legacy(video, tmp_path):
    output = tmp_path / "labels.json"
    store = AnnotationStore(video, output)
    store.set_frame(4, "absent", [])
    store.set_frame(1, "absent", [])
    store.close()
    resumed = AnnotationStore(video, output)
    assert resumed.state()["resume_frame"] == 1
    resumed.close()
    data = json.loads(output.read_text())
    del data["last_annotated_frame"]
    output.write_text(json.dumps(data))
    resumed = AnnotationStore(video, output)
    assert resumed.state()["resume_frame"] == 4
    resumed.close()


def test_file_selection_server(video, tmp_path):
    import threading
    from urllib.request import Request, urlopen

    from video_annotating_interface.app import create_server

    server = create_server(port=0, workspace=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert b"Choose local files" in urlopen(base).read()
        payload = {"video": str(video), "output": str(tmp_path / "labels.json")}
        req = Request(
            base + "/api/open",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert json.load(urlopen(req))["resume_frame"] == 0
        assert json.load(urlopen(base + "/api/state"))["video"]["frame_count"] == 5
        assert b"Choose local files" not in urlopen(base).read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_directory_queue_natural_order_and_relative_paths(tmp_path):
    from video_annotating_interface.app import directory_project

    videos = tmp_path / "videos"
    suggestions = tmp_path / "suggestions"
    videos.mkdir()
    suggestions.mkdir()
    for name in ("clip10.mp4", "clip2.mov", "clip1.mp4"):
        (videos / name).touch()
    (videos / "nested").mkdir()
    (videos / "nested" / "clip1.mp4").touch()
    (suggestions / "clip2.suggestions.json").write_text("{}")
    payload = {
        "video_directory": str(videos),
        "suggestions_directory": str(suggestions),
        "output_directory": str(tmp_path / "results"),
    }
    path, project = directory_project(payload)
    assert path.name == "annotation-project.json"
    assert [Path(item["video"]).name for item in project["items"]] == [
        "clip1.mp4",
        "clip2.mov",
        "clip10.mp4",
    ]
    assert project["items"][1]["suggestions"] == str(suggestions / "clip2.suggestions.json")
    assert not path.exists(), "Preview must not write files"
    _, recursive = directory_project({**payload, "recursive": True})
    assert len(recursive["items"]) == 4
    assert Path(recursive["items"][-1]["output"]).relative_to(tmp_path / "results") == Path(
        "nested/clip1.mp4.annotations.json"
    )


def test_directory_queue_rejects_ambiguous_suggestions(tmp_path):
    from video_annotating_interface.app import directory_project

    (tmp_path / "clip.mp4").touch()
    (tmp_path / "clip.json").write_text("{}")
    (tmp_path / "clip.suggestions.json").write_text("{}")
    with pytest.raises(ValueError, match="Ambiguous"):
        directory_project(
            {
                "video_directory": str(tmp_path),
                "suggestions_directory": str(tmp_path),
                "output_directory": str(tmp_path / "out"),
            }
        )


def test_project_rejects_cross_video_input_overwrite(tmp_path):
    from video_annotating_interface.app import PROJECT_FORMAT, validate_project

    source = str(tmp_path / "suggestions.json")
    with pytest.raises(ValueError, match="differ"):
        validate_project(
            {
                "format": PROJECT_FORMAT,
                "index": 0,
                "items": [
                    {"video": str(tmp_path / "one.mp4"), "output": source, "suggestions": None},
                    {
                        "video": str(tmp_path / "two.mp4"),
                        "output": str(tmp_path / "two.json"),
                        "suggestions": source,
                    },
                ],
            },
            tmp_path / "project.json",
        )


def test_queue_completion_uses_all_reviewed_frames(video, tmp_path):
    from video_annotating_interface.app import PROJECT_FORMAT, project_state

    output = tmp_path / "labels.json"
    store = AnnotationStore(video, output)
    store.set_frame(store.count - 1, "absent", [])
    project = {
        "format": PROJECT_FORMAT,
        "index": 0,
        "items": [{"video": str(video), "output": str(output), "suggestions": None}],
    }
    path = tmp_path / "project.json"
    assert project_state(project, path)["items"][0]["status"] == "in-progress"
    store.set_absent_range(0, store.count - 1)
    assert project_state(project, path)["items"][0]["status"] == "complete"
    store.close()


def test_failed_frame_save_does_not_change_progress(video, tmp_path, monkeypatch):
    store = AnnotationStore(video, tmp_path / "labels.json")
    store.set_frame(0, "absent", [])
    previous = store.state()

    def fail_write():
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write", fail_write)
    with pytest.raises(OSError, match="disk full"):
        store.set_frame(4, "absent", [])
    assert store.state() == previous
    store.close()


def test_project_switch_failure_and_restart_resume(video, tmp_path, monkeypatch):
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from video_annotating_interface import app

    second = tmp_path / "sample2.mp4"
    second.write_bytes(video.read_bytes())
    output = tmp_path / "results"
    server = app.create_server(port=0, workspace=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(server, route, payload):
        request = Request(
            f"http://127.0.0.1:{server.server_port}{route}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            return json.load(response)

    def state(server):
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/state") as response:
            return json.load(response)

    def fail_project_write(*args):
        raise OSError("Cannot save project")

    project_path = output / "annotation-project.json"
    try:
        payload = {"video_directory": str(tmp_path), "output_directory": str(output)}
        post(server, "/api/project/create", payload)
        assert state(server)["video"]["name"] == video.name
        with pytest.raises(HTTPError):
            post(server, "/api/project/create", payload)
        with monkeypatch.context() as patch:
            patch.setattr(app, "write_project", fail_project_write)
            with pytest.raises(HTTPError):
                post(server, "/api/project/select", {"index": 1})
        assert state(server)["video"]["name"] == video.name
        assert json.loads(project_path.read_text())["index"] == 0
        post(server, "/api/project/select", {"index": 1})
        post(server, "/api/frame", {"frame": 3, "status": "absent", "objects": []})
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    resumed = app.create_server(port=0, workspace=tmp_path)
    thread = threading.Thread(target=resumed.serve_forever, daemon=True)
    thread.start()
    try:
        post(resumed, "/api/project/load", {"path": str(project_path)})
        assert state(resumed)["project"] == {"index": 1, "count": 2}
        assert state(resumed)["video"]["name"] == second.name
        assert state(resumed)["resume_frame"] == 3
    finally:
        resumed.shutdown()
        resumed.server_close()
        thread.join()
