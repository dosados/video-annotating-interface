import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from video_annotating_interface.app import AnnotationStore

TEST_CATEGORIES = [
    {"id": "object", "name": "Предмет", "kind": "entity", "color": "#ffdb66"},
    {"id": "interaction", "name": "Взаимодействие", "kind": "event", "color": "#ff8b78"},
    {"id": "arrival", "name": "Появление", "kind": "event", "color": "#8ee3ad"},
]


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
    saved = json.loads(output.read_text(encoding="utf-8"))
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
    data = json.loads(output.read_text(encoding="utf-8"))
    del data["last_annotated_frame"]
    output.write_text(json.dumps(data))
    resumed = AnnotationStore(video, output)
    assert resumed.state()["resume_frame"] == 4
    resumed.close()


def test_shutdown_server(tmp_path):
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from video_annotating_interface.app import create_server

    server = create_server(port=0, workspace=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for page in ("/", "/setup", "/queue"):
            with urlopen(base + page) as response:
                assert b'id="shutdown"' in response.read()
        request = Request(
            base + "/api/shutdown",
            data=b"{}",
            headers={"Content-Type": "application/json", "Origin": "http://example.com"},
        )
        with pytest.raises(HTTPError):
            urlopen(request)
        assert thread.is_alive()
        request = Request(
            base + "/api/shutdown", data=b"{}", headers={"Content-Type": "application/json"}
        )
        with urlopen(request) as response:
            assert json.load(response) == {"ok": True}
        thread.join(timeout=3)
        assert not thread.is_alive()
    finally:
        server.shutdown()
        server.server_close()


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
        assert json.load(urlopen(base + "/api/setup"))["input_directory"] == str(tmp_path)
        assert json.load(urlopen(base + "/api/files"))["path"] == str(tmp_path)
        # An input subfolder must not change the initial project directory.
        (tmp_path / "input").mkdir()
        assert json.load(urlopen(base + "/api/setup"))["input_directory"] == str(tmp_path)
        assert json.load(urlopen(base + "/api/setup"))["output_directory"] == str(
            tmp_path / "outputs"
        )
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
        assert json.loads(project_path.read_text(encoding="utf-8"))["index"] == 0
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


def test_typed_labels_upgrade_legacy_without_losing_frames(video, tmp_path):
    from video_annotating_interface.app import FORMAT_V2

    output = tmp_path / "labels.json"
    store = AnnotationStore(video, output)
    store.set_frame(0, "annotated", [{"type": "point", "x": 12, "y": 14}])
    store.set_frame(2, "absent", [])
    original = output.read_bytes()
    store.close()
    store = AnnotationStore(video, output)
    assert output.read_bytes() == original, "Opening legacy data must not rewrite it"
    store.set_categories(TEST_CATEGORIES)
    store.set_frame(
        1,
        "annotated",
        [
            {"type": "point", "x": 12, "y": 14, "category_id": "object"},
            {"type": "point", "x": 12, "y": 14, "category_id": "interaction"},
            {"type": "box", "x": 1, "y": 2, "width": 4, "height": 5, "category_id": "arrival"},
        ],
    )
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["format"] == FORMAT_V2
    assert saved["frames"]["0"]["objects"][0]["category_id"] == "object"
    assert saved["frames"]["2"] == {"status": "absent", "objects": []}
    assert "3" not in saved["frames"]
    store.close()
    store = AnnotationStore(video, output)
    assert store.state()["frames"] == saved["frames"]
    store.close()


def test_custom_types_and_protect_used_types(video, tmp_path):

    output = tmp_path / "labels.json"
    store = AnnotationStore(video, output)
    categories = [
        *TEST_CATEGORIES,
        {"id": "serve", "name": "Подача", "kind": "event", "color": "#abcdef"},
    ]
    store.set_categories(categories)
    assert store.state()["frames"] == {}, "Configuring types must not review a frame"
    store.set_frame(2, "annotated", [{"type": "point", "x": 3, "y": 4, "category_id": "serve"}])
    before = output.read_bytes()
    with pytest.raises(ValueError, match="used"):
        store.set_categories(TEST_CATEGORIES)
    with pytest.raises(ValueError, match="Unknown"):
        store.set_frame(
            3, "annotated", [{"type": "point", "x": 3, "y": 4, "category_id": "unknown"}]
        )
    assert output.read_bytes() == before
    store.close()
    store = AnnotationStore(video, output)
    assert store.categories == categories
    assert store.labels["2"]["objects"][0]["category_id"] == "serve"
    store.close()


@pytest.mark.parametrize(
    "change",
    [
        lambda c: None,
        lambda c: c + [c[0]],
        lambda c: [{**c[0], "color": "red"}, *c[1:]],
        lambda c: [{**c[0], "kind": "unknown"}, *c[1:]],
        lambda c: [{**c[0], "name": "   "}, *c[1:]],
        lambda c: [{**c[0], "id": "invalid id"}, *c[1:]],
    ],
)
def test_invalid_type_settings_are_atomic(video, tmp_path, change):

    store = AnnotationStore(video, tmp_path / "labels.json")
    before = store.state()
    with pytest.raises(ValueError):
        store.set_categories(change(TEST_CATEGORIES))
    assert store.state() == before
    assert not store.output.exists()
    store.close()


def test_v2_suggestions_merge_catalog_and_remain_unreviewed(video, tmp_path):
    from video_annotating_interface.app import FORMAT_V2, SUGGESTIONS_FORMAT_V2

    suggestions = tmp_path / "suggestions.json"
    categories = [
        *TEST_CATEGORIES,
        {"id": "serve", "name": "Подача", "kind": "event", "color": "#abcdef"},
    ]
    suggestions.write_text(
        json.dumps(
            {
                "format": SUGGESTIONS_FORMAT_V2,
                "categories": categories,
                "frames": {
                    "1": [{"type": "point", "x": 3, "y": 4, "category_id": "serve"}],
                    "2": [],
                },
            }
        )
    )
    output = tmp_path / "labels.json"
    store = AnnotationStore(video, output, suggestions)
    assert store.format == FORMAT_V2
    assert store.labels == {}
    assert not output.exists()
    assert store.suggestions["2"] == []
    with pytest.raises(ValueError, match="used"):
        store.set_categories(TEST_CATEGORIES)
    store.set_frame(0, "absent", [])
    store.close()
    store = AnnotationStore(video, output, suggestions)
    assert len(store.categories) == 4
    assert set(store.labels) == {"0"}
    store.close()


def test_type_upgrade_rolls_back_after_write_failure(video, tmp_path, monkeypatch):

    store = AnnotationStore(video, tmp_path / "labels.json")
    store.set_frame(0, "annotated", [{"type": "point", "x": 3, "y": 4}])
    before = store.state()
    original = store.output.read_bytes()

    def fail():
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write", fail)
    with pytest.raises(OSError):
        store.set_frame(
            1, "annotated", [{"type": "point", "x": 3, "y": 4, "category_id": "object"}]
        )
    assert store.state() == before
    with pytest.raises(OSError):
        store.set_categories(TEST_CATEGORIES)
    assert store.state() == before
    assert store.output.read_bytes() == original
    store.close()


def test_queue_reads_v2_catalog(video, tmp_path):
    from video_annotating_interface.app import PROJECT_FORMAT, project_state

    output = tmp_path / "labels.json"
    store = AnnotationStore(video, output)
    categories = [
        *TEST_CATEGORIES,
        {"id": "serve", "name": "Подача", "kind": "event", "color": "#abcdef"},
    ]
    store.set_categories(categories)
    store.set_frame(0, "annotated", [{"type": "point", "x": 3, "y": 4, "category_id": "serve"}])
    store.close()
    project = {
        "format": PROJECT_FORMAT,
        "index": 0,
        "items": [{"video": str(video), "output": str(output), "suggestions": None}],
    }
    assert project_state(project, tmp_path / "project.json")["items"][0]["status"] == "in-progress"
    data = json.loads(output.read_text(encoding="utf-8"))
    data["frames"]["0"]["objects"][0]["category_id"] = "unknown"
    output.write_text(json.dumps(data))
    assert project_state(project, tmp_path / "project.json")["items"][0]["status"] == "error"


def test_empty_catalog_and_no_reserved_category(video, tmp_path):
    store = AnnotationStore(video, tmp_path / "labels.json")
    assert store.categories == []
    categories = [
        {"id": "door_open", "name": "Дверь открылась", "kind": "event", "color": "#123456"}
    ]
    store.set_categories(categories)
    assert store.categories == categories
    store.set_categories([])
    store.close()
    resumed = AnnotationStore(video, tmp_path / "labels.json")
    assert resumed.categories == []
    resumed.set_categories(categories)
    resumed.set_frame(
        0, "annotated", [{"type": "point", "x": 3, "y": 4, "category_id": "door_open"}]
    )
    assert resumed.labels["0"]["objects"][0]["category_id"] == "door_open"
    resumed.close()


def test_existing_tennis_v2_keeps_user_catalog(video, tmp_path):
    output = tmp_path / "labels.json"
    store = AnnotationStore(video, output)
    categories = [{"id": "ball", "name": "Мяч", "kind": "entity", "color": "#123456"}]
    store.set_categories(categories)
    store.set_frame(0, "annotated", [{"type": "point", "x": 3, "y": 4, "category_id": "ball"}])
    store.close()
    before = output.read_bytes()
    store = AnnotationStore(video, output)
    assert store.categories == categories
    assert store.labels["0"]["objects"][0]["category_id"] == "ball"
    assert before == output.read_bytes()
    store.close()


def test_temporal_events_roundtrip_and_review(video, tmp_path):
    from video_annotating_interface.app import FORMAT_V3, SUGGESTIONS_FORMAT_V3

    suggestions = tmp_path / "events.json"
    event = {"id": "bounce_1", "category_id": "interaction", "start": 1, "end": 3}
    suggestions.write_text(
        json.dumps(
            {"format": SUGGESTIONS_FORMAT_V3, "categories": TEST_CATEGORIES, "events": [event]}
        ),
        encoding="utf-8",
    )
    output = tmp_path / "labels.json"
    store = AnnotationStore(video, output, suggestions)
    assert not output.exists()
    assert store.labels == {}
    assert store.event_suggestions == [{**event, "status": "suggested"}]
    # Saving spatial absence neither accepts nor removes temporal suggestions.
    store.set_frame(1, "absent", [])
    assert store.events == []
    edited = {**event, "category_id": "arrival", "status": "suggested"}
    store.set_events([edited])
    assert store.format == FORMAT_V3
    assert set(store.labels) == {"1"}
    store.close()
    store = AnnotationStore(video, output, suggestions)
    assert store.events == [edited]
    store.set_events(
        [{**edited, "status": "confirmed", "geometry": {"type": "point", "x": 4, "y": 6}}]
    )
    store.set_categories(TEST_CATEGORIES)
    store.set_frame(2, "absent", [])
    store.clear_frame(2)
    assert store.format == FORMAT_V3
    assert store.events[0]["status"] == "confirmed"
    assert store.events[0]["geometry"]["x"] == 4
    store.set_events([{**edited, "status": "rejected"}])
    store.close()
    store = AnnotationStore(video, output, suggestions)
    assert store.events == [{**edited, "status": "rejected"}]
    assert set(store.labels) == {"1"}
    store.close()


def test_temporal_event_validation_and_atomic_rollback(video, tmp_path, monkeypatch):
    store = AnnotationStore(video, tmp_path / "labels.json")
    store.set_categories(TEST_CATEGORIES)
    event = {"id": "hit", "category_id": "interaction", "start": 1, "end": 3, "status": "confirmed"}
    store.set_events([event])
    original = store.output.read_bytes()
    before = store.state()
    for patch in [
        {"start": True},
        {"start": -1},
        {"end": 5},
        {"end": 0},
        {"category_id": "object"},
        {"category_id": "missing"},
        {"id": ""},
        {"status": "unknown"},
        {"geometry": {"type": "point", "x": 99, "y": 0}},
    ]:
        with pytest.raises(ValueError):
            store.set_events([{**event, **patch}])
        assert store.output.read_bytes() == original
    with pytest.raises(ValueError):
        store.set_events([event, event])
    with pytest.raises(ValueError):
        store.set_categories([c for c in TEST_CATEGORIES if c["id"] != "interaction"])
    with pytest.raises(ValueError):
        store.set_categories(
            [{**c, "kind": "entity"} if c["id"] == "interaction" else c for c in TEST_CATEGORIES]
        )

    def fail():
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write", fail)
    with pytest.raises(OSError):
        store.set_events([{**event, "end": 4}])
    assert store.state() == before
    assert store.output.read_bytes() == original
    store.close()


def test_temporal_event_api_and_queue(video, tmp_path):
    import threading
    from urllib.request import Request, urlopen

    from video_annotating_interface.app import PROJECT_FORMAT, create_server, project_state

    output = tmp_path / "labels.json"
    server = create_server(video, output, port=0, workspace=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        for route, payload in [
            ("/api/categories", {"categories": TEST_CATEGORIES}),
            (
                "/api/events",
                {"events": [{"id": "hit", "category_id": "interaction", "start": 0, "end": 0}]},
            ),
        ]:
            with urlopen(
                Request(
                    base + route,
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"},
                )
            ) as response:
                assert json.load(response)["ok"]
        with urlopen(base + "/api/state") as response:
            state = json.load(response)
        assert state["events"][0]["status"] == "confirmed"
        assert state["frames"] == {}
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
    project = {
        "format": PROJECT_FORMAT,
        "index": 0,
        "items": [{"video": str(video), "output": str(output), "suggestions": None}],
    }
    assert project_state(project, tmp_path / "project.json")["items"][0]["status"] == "in-progress"
    saved = json.loads(output.read_text(encoding="utf-8"))
    saved["events"][0]["end"] = 99
    output.write_text(json.dumps(saved), encoding="utf-8")
    assert project_state(project, tmp_path / "project.json")["items"][0]["status"] == "error"


def test_promote_legacy_event_is_atomic(video, tmp_path, monkeypatch):
    store = AnnotationStore(video, tmp_path / "labels.json")
    store.set_categories(TEST_CATEGORIES)
    objects = [
        {"type": "point", "x": 4, "y": 6, "category_id": "interaction"},
        {"type": "point", "x": 7, "y": 8, "category_id": "object"},
    ]
    store.set_frame(1, "annotated", objects)
    before = store.state()
    original = store.output.read_bytes()
    with pytest.raises(ValueError):
        store.promote_event(1, 1, "invalid_entity")
    assert store.state() == before
    with monkeypatch.context() as patch:

        def fail():
            raise OSError("disk full")

        patch.setattr(store, "_write", fail)
        with pytest.raises(OSError):
            store.promote_event(1, 0, "hit")
    assert store.state() == before
    assert store.output.read_bytes() == original
    label = store.promote_event(1, 0, "hit")
    assert label["objects"] == [objects[1]]
    assert store.events[0]["geometry"] == {"type": "point", "x": 4.0, "y": 6.0}
    assert store.events[0]["start"] == store.events[0]["end"] == 1
    store.close()
    resumed = AnnotationStore(video, tmp_path / "labels.json")
    assert len(resumed.events) == 1
    assert len(resumed.labels["1"]["objects"]) == 1
    resumed.close()


def test_file_picker_hidden_entries(tmp_path):
    from video_annotating_interface.app import list_files

    for name in ("visible.mp4", ".hidden.mp4", "labels.json", ".labels.json"):
        (tmp_path / name).touch()
    (tmp_path / ".private").mkdir()
    (tmp_path / "folder").mkdir()
    names = lambda kind, hidden=False: {
        entry["name"] for entry in list_files(tmp_path, kind, hidden)["entries"]
    }
    assert names("video") == {"folder", "visible.mp4"}
    assert names("video", True) == {"folder", ".private", "visible.mp4", ".hidden.mp4"}
    assert names("json") == {"folder", "labels.json"}
    assert ".labels.json" in names("json", True)


def test_preview_endpoint_without_annotation_session(video, tmp_path):
    import threading
    from urllib.error import HTTPError
    from urllib.parse import urlencode
    from urllib.request import urlopen

    from video_annotating_interface.app import create_server

    server = create_server(port=0, workspace=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    (tmp_path / ".hidden").mkdir()
    broken = tmp_path / "broken.mp4"
    broken.touch()
    try:
        assert ".hidden" not in {
            e["name"] for e in json.load(urlopen(base + "/api/files"))["entries"]
        }
        assert ".hidden" in {
            e["name"] for e in json.load(urlopen(base + "/api/files?show_hidden=true"))["entries"]
        }
        with urlopen(base + "/api/preview?" + urlencode({"path": str(video)})) as response:
            assert response.headers["Content-Type"] == "image/jpeg"
            frame = cv2.imdecode(np.frombuffer(response.read(), dtype=np.uint8), cv2.IMREAD_COLOR)
            assert frame.shape[:2] == (48, 64)
        for path in (str(broken), str(tmp_path), ""):
            with pytest.raises(HTTPError) as error:
                urlopen(base + "/api/preview?" + urlencode({"path": path}))
            assert error.value.code == 400
        assert json.load(urlopen(base + "/api/setup"))["active"] is False
        assert not (tmp_path / "outputs").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_frame_cache_reuses_jpeg_without_reviewing(video, tmp_path):
    store = AnnotationStore(video, tmp_path / "annotations.json")
    try:
        first = store.frame_jpeg(0)
        store.frame_jpeg(1)
        assert store.frame_jpeg(0) is first
        decoder_position = store.next_frame
        store.frame_jpeg(0)
        assert store.next_frame == decoder_position, "Cache hits must not change decoder position"
        assert store.frame_jpeg(2).startswith(b"\xff\xd8")
        assert store.frame_cache_bytes == sum(map(len, store.frame_cache.values()))
        assert store.state()["frames"] == {}
        assert not store.output.exists()
    finally:
        store.close()
    assert not store.frame_cache
    assert store.frame_cache_bytes == 0


def test_delete_track_persists_source_suppression_and_rolls_back(video, tmp_path, monkeypatch):
    source = tmp_path / "suggestions.json"
    point = lambda category: {"type": "point", "x": 12, "y": 14, "category_id": category}
    source.write_text(
        json.dumps(
            {
                "format": "video-annotating-interface-suggestions-v3",
                "categories": TEST_CATEGORIES,
                "frames": {
                    "0": [point("object"), point("interaction")],
                    "1": [point("interaction")],
                },
                "events": [{"id": "hit", "category_id": "interaction", "start": 0, "end": 2}],
            }
        )
    )
    output = tmp_path / "labels.json"
    store = AnnotationStore(video, output, source)
    try:
        store.set_frame(0, "annotated", [point("object"), point("interaction")])
        store.set_frame(2, "annotated", [point("interaction")])
        before, disk = store.state(), output.read_bytes()
        with monkeypatch.context() as patch:

            def fail():
                raise OSError("disk full")

            patch.setattr(store, "_write", fail)
            with pytest.raises(OSError):
                store.delete_category("interaction")
        assert store.state() == before
        assert store.deleted_categories == set()
        assert output.read_bytes() == disk
        result = store.delete_category("interaction")
        assert result["frames"]["0"]["objects"] == [point("object")]
        assert result["frames"]["2"] == {"status": "absent", "objects": []}
        assert "1" not in result["frames"], "Deleting a track must not review unseen frames"
        assert not result["event_suggestions"]
        assert json.loads(output.read_text())["deleted_categories"] == ["interaction"]
    finally:
        store.close()
    reopened = AnnotationStore(video, output, source)
    try:
        assert "interaction" not in {c["id"] for c in reopened.categories}
        assert not reopened.event_suggestions
        assert reopened.suggestions["1"] == []
        assert "1" not in reopened.labels
        reopened.set_categories(TEST_CATEGORIES)
        assert not reopened.deleted_categories
    finally:
        reopened.close()


def test_delete_spatial_run_preserves_other_types_and_events(video, tmp_path, monkeypatch):
    store = AnnotationStore(video, tmp_path / "labels.json")
    try:
        store.set_categories(TEST_CATEGORIES)
        objects = [
            {"type": "point", "x": 12, "y": 14, "category_id": c} for c in ("object", "interaction")
        ]
        store.set_frame(0, "annotated", objects)
        store.set_frame(1, "annotated", [objects[0]])
        store.set_events(
            [
                {
                    "id": "hit",
                    "category_id": "interaction",
                    "start": 0,
                    "end": 2,
                    "status": "confirmed",
                }
            ]
        )
        before = store.state()
        with monkeypatch.context() as patch:
            patch.setattr(store, "_write", lambda: (_ for _ in ()).throw(OSError("disk full")))
            with pytest.raises(OSError):
                store.delete_spatial_marks("object", 0, 1)
        assert store.state() == before
        result = store.delete_spatial_marks("object", 0, 1)
        assert result["frames"]["0"]["objects"] == [objects[1]]
        assert result["frames"]["1"] == {"status": "absent", "objects": []}
        assert result["events"] == before["events"]
        assert "2" not in result["frames"]
    finally:
        store.close()


def test_directory_catalog_is_shared_but_video_tracks_are_independent(video, tmp_path, monkeypatch):
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from video_annotating_interface import app

    (tmp_path / "sample2.mp4").write_bytes(video.read_bytes())
    output = tmp_path / "results"
    project_path = output / "annotation-project.json"
    category = TEST_CATEGORIES[1]
    server = app.create_server(port=0, workspace=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def get(route):
        with urlopen(base + route) as response:
            return json.load(response)

    def post(route, payload):
        request = Request(
            base + route,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            return json.load(response)

    try:
        post(
            "/api/project/create",
            {"video_directory": str(tmp_path), "output_directory": str(output)},
        )
        assert get("/api/project/categories")["categories"] == []
        post("/api/categories", {"categories": [category]})
        assert json.loads(project_path.read_text())["categories"] == [category]
        assert get("/api/state")["frames"] == {}
        mark = {"type": "point", "x": 12, "y": 14, "category_id": category["id"]}
        post("/api/frame", {"frame": 0, "status": "annotated", "objects": [mark]})
        first_output = output / (video.name + ".annotations.json")
        first_disk = first_output.read_bytes()
        post("/api/project/select", {"index": 1})
        assert get("/api/state")["categories"] == [], (
            "A fresh video must not inherit directory tracks"
        )
        assert get("/api/project/categories")["categories"] == [category]
        with pytest.raises(HTTPError):
            post("/api/project/categories/add", {"category_id": "missing"})
        with monkeypatch.context() as patch:

            def fail(*args):
                raise OSError("disk full")

            patch.setattr(app.AnnotationStore, "_write", fail)
            with pytest.raises(HTTPError):
                post("/api/project/categories/add", {"category_id": category["id"]})
        assert get("/api/state")["categories"] == []
        post("/api/project/categories/add", {"category_id": category["id"]})
        assert get("/api/state")["categories"] == [category]
        assert get("/api/state")["frames"] == {}, "Attaching a type does not review a frame"
        with pytest.raises(HTTPError):
            post("/api/project/categories/add", {"category_id": category["id"]})
        post("/api/categories/delete", {"category_id": category["id"]})
        assert get("/api/state")["categories"] == []
        assert get("/api/project/categories")["categories"] == [category], (
            "Local track deletion keeps reusable definition"
        )
        assert first_output.read_bytes() == first_disk, (
            "Deleting a directory track must not alter other videos"
        )
        post("/api/project/select", {"index": 0})
        assert get("/api/state")["frames"]["0"]["objects"] == [mark]
        assert get("/api/state")["categories"] == [category], (
            "Returning to a video restores its selected tracks"
        )
        post("/api/project/load", {"path": str(project_path)})
        assert get("/api/project/categories")["categories"] == [category]
        assert get("/api/state")["categories"] == [category]
        with monkeypatch.context() as patch:

            def fail(*args):
                raise OSError("catalog is read-only")

            patch.setattr(app, "write_project", fail)
            with pytest.raises(HTTPError):
                post("/api/categories", {"categories": [category, TEST_CATEGORIES[0]]})
        assert get("/api/state")["categories"] == [category]
        assert get("/api/project/categories")["categories"] == [category]
        post("/api/open", {"video": str(video), "output": str(tmp_path / "single.json")})
        with pytest.raises(HTTPError):
            get("/api/project/categories")
        with pytest.raises(HTTPError):
            post("/api/project/categories/add", {"category_id": category["id"]})
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_prefetch_uses_independent_decoder_and_closes_cleanly(tmp_path, monkeypatch):
    import threading

    video = tmp_path / "long.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 15, (64, 48))
    for index in range(80):
        writer.write(np.full((48, 64, 3), index * 3, np.uint8))
    writer.release()
    store = AnnotationStore(video, tmp_path / "labels.json")
    buffered = threading.Event()
    target = [16]
    cache_frame = store._cache_frame

    def record(frame, data):
        cache_frame(frame, data)
        if frame == target[0]:
            buffered.set()

    monkeypatch.setattr(store, "_cache_frame", record)
    try:
        store.frame_jpeg(0)
        assert buffered.wait(3), "Forward frames must be prefetched without browser requests"
        foreground_position = store.next_frame
        for index in range(1, 16):
            assert store.frame_jpeg(index).startswith(b"\xff\xd8")
        assert store.next_frame == foreground_position, (
            "Prefetch must not move the foreground decoder"
        )
        target[0] = 76
        buffered.clear()
        store.frame_jpeg(60)
        assert buffered.wait(3)
        target[0] = 58
        buffered.clear()
        store.frame_jpeg(59)
        assert buffered.wait(3), "Reverse navigation must buffer a contiguous preceding window"
        foreground_position = store.next_frame
        for index in range(58, 44, -1):
            store.frame_jpeg(index)
        assert store.next_frame == foreground_position
        with store.frame_cache_lock:
            assert store.frame_cache_bytes == sum(map(len, store.frame_cache.values()))
            assert store.frame_cache_bytes <= 32 * 1024 * 1024
        assert store.labels == {}
        assert not store.output.exists(), "Buffering must never save annotations"
    finally:
        store.close()
    assert not store.prefetch_thread.is_alive()
    assert not store.frame_cache


def test_frame_batches_save_once_and_roll_back_atomically(video, tmp_path, monkeypatch):
    store = AnnotationStore(video, tmp_path / "labels.json")
    try:
        store.set_categories(TEST_CATEGORIES)
        store.set_events(
            [
                {
                    "id": "hit",
                    "category_id": "interaction",
                    "start": 0,
                    "end": 3,
                    "status": "confirmed",
                }
            ]
        )
        original_write = store._write
        writes = []

        def counted_write():
            writes.append(1)
            original_write()

        monkeypatch.setattr(store, "_write", counted_write)
        batch = [
            {
                "frame": 0,
                "status": "annotated",
                "objects": [{"type": "point", "x": 12, "y": 14, "category_id": "object"}],
            },
            {"frame": 1, "status": "absent", "objects": []},
        ]
        result = store.set_frames(batch)
        assert len(writes) == 1
        assert result["0"]["objects"][0]["x"] == 12
        assert result["1"] == {"status": "absent", "objects": []}
        assert "2" not in store.labels
        assert store.events[0]["id"] == "hit"
        before, disk = store.state(), store.output.read_bytes()
        for invalid in (
            [],
            batch + [batch[0]],
            [*batch, {"frame": 9, "status": "absent", "objects": []}],
        ):
            with pytest.raises(ValueError):
                store.set_frames(invalid)
            assert store.state() == before
            assert store.output.read_bytes() == disk

        def fail():
            raise OSError("disk full")

        monkeypatch.setattr(store, "_write", fail)
        with pytest.raises(OSError):
            store.set_frames([{"frame": 3, "status": "absent", "objects": []}])
        assert store.state() == before
        assert store.output.read_bytes() == disk
    finally:
        store.close()


def test_frame_request_does_not_wait_for_batch_disk_write(video, tmp_path, monkeypatch):
    import threading
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    from video_annotating_interface import app

    entered, release = threading.Event(), threading.Event()
    original_write = app.AnnotationStore._write

    def slow_write(store):
        entered.set()
        assert release.wait(3)
        original_write(store)

    monkeypatch.setattr(app.AnnotationStore, "_write", slow_write)
    server = app.create_server(video, tmp_path / "labels.json", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    errors = []

    def save():
        try:
            request = Request(
                base + "/api/frames",
                data=json.dumps(
                    {"frames": [{"frame": 0, "status": "absent", "objects": []}]}
                ).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request) as response:
                assert json.load(response)["frames"]["0"]["status"] == "absent"
        except (OSError, ValueError, AssertionError) as error:
            errors.append(error)

    saving = threading.Thread(target=save)
    saving.start()
    try:
        assert entered.wait(2)
        with urlopen(base + "/api/frame?" + urlencode({"index": 2}), timeout=1) as response:
            assert response.read().startswith(b"\xff\xd8"), (
                "JPEG delivery must continue while disk saving is blocked"
            )
    finally:
        release.set()
        saving.join(3)
        server.shutdown()
        server.server_close()
        thread.join()
    assert not errors


def test_video_close_waits_for_active_frame_reader(video, tmp_path):
    import threading

    store = AnnotationStore(video, tmp_path / "labels.json")
    store.begin_frame_read()
    closed = threading.Event()

    def close():
        store.close()
        closed.set()

    closing = threading.Thread(target=close)
    closing.start()
    try:
        assert not closed.wait(0.02)
        assert store.frame_jpeg(0).startswith(b"\xff\xd8")
    finally:
        store.end_frame_read()
        closing.join(3)
    assert closed.is_set()
    with pytest.raises(ValueError):
        store.begin_frame_read()
