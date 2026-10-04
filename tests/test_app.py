import json

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
