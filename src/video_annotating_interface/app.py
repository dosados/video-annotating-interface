"""Local HTTP server and durable annotation store."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import cv2

FORMAT = "video-annotating-interface-v1"
SUGGESTIONS_FORMAT = "video-annotating-interface-suggestions-v1"


def validate_object(obj: object, width: int, height: int) -> dict:
    if not isinstance(obj, dict):
        raise TypeError("Object must be a JSON object")
    kind = obj.get("type")
    fields = ("x", "y") if kind == "point" else ("x", "y", "width", "height")
    if kind not in {"point", "box"}:
        raise ValueError("Object type must be point or box")
    if set(obj) != {"type", *fields}:
        raise ValueError(f"{kind} must contain only type and {', '.join(fields)}")
    values = [obj[field] for field in fields]
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in values
    ):
        raise ValueError("Coordinates must be finite numbers")
    x, y = values[:2]
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError("Object origin is outside the video frame")
    if kind == "box" and not (
        values[2] > 0 and values[3] > 0 and x + values[2] <= width and y + values[3] <= height
    ):
        raise ValueError("Box must have positive size and fit inside the video frame")
    return {"type": kind, **{field: round(float(obj[field]), 2) for field in fields}}


class AnnotationStore:
    def __init__(self, video: Path, output: Path, suggestions: Path | None = None):
        self.video = video.resolve(strict=True)
        self.output = output.resolve()
        self.capture = cv2.VideoCapture(str(self.video))
        if not self.capture.isOpened():
            raise ValueError(f"Cannot open video: {self.video}")
        self.count = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = float(self.capture.get(cv2.CAP_PROP_FPS))
        self.width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        final_frame_ok = False
        for _ in range(min(self.count, 3)):
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, self.count - 1)
            ok, _ = self.capture.read()
            if ok:
                final_frame_ok = True
                break
            self.count -= 1
        if not final_frame_ok or min(self.count, self.fps, self.width, self.height) <= 0:
            self.close()
            raise ValueError("Video metadata or final frame is invalid")
        self.identity = {
            "name": self.video.name,
            "size_bytes": self.video.stat().st_size,
            "frame_count": self.count,
            "width": self.width,
            "height": self.height,
        }
        self.lock = threading.RLock()
        self.next_frame = -1
        self.labels: dict[str, dict] = {}
        try:
            if self.output.exists():
                saved = json.loads(self.output.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    raise TypeError("Output must be a JSON object")
                if saved.get("format") != FORMAT or saved.get("video") != self.identity:
                    raise ValueError("Output file has a different format or video identity")
                if not isinstance(saved.get("frames"), dict):
                    raise ValueError("Output frames must be an object")
                for key, label in saved["frames"].items():
                    if not isinstance(label, dict):
                        raise TypeError("Frame label must be a JSON object")
                    frame = self._frame(int(key))
                    if key != str(frame):
                        raise ValueError("Frame keys must be canonical integer strings")
                    self.labels[key] = self._label(label.get("status"), label.get("objects"))
            self.last_annotated_frame = max(map(int, self.labels), default=0)
            if self.output.exists() and "last_annotated_frame" in saved:
                self.last_annotated_frame = self._frame(saved["last_annotated_frame"])
            self.suggestions = self._read_suggestions(suggestions) if suggestions else {}
        except Exception:
            self.close()
            raise

    def _frame(self, frame: object) -> int:
        if isinstance(frame, bool) or not isinstance(frame, int) or not 0 <= frame < self.count:
            raise ValueError("Frame index is out of range")
        return frame

    def _label(self, status: object, objects: object) -> dict:
        if status not in {"annotated", "absent"} or not isinstance(objects, list):
            raise ValueError("Status must be annotated or absent, with an objects list")
        if len(objects) > 100:
            raise ValueError("Too many objects on one frame")
        clean = [validate_object(obj, self.width, self.height) for obj in objects]
        if (status == "annotated") != bool(clean):
            raise ValueError("Annotated frames need objects; absent frames must have none")
        return {"status": status, "objects": clean}

    def _read_suggestions(self, path: Path) -> dict[str, list[dict]]:
        source = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(source, dict) and source.get("format") == SUGGESTIONS_FORMAT:
            rows = source.get("frames")
            if not isinstance(rows, dict):
                raise ValueError("Suggestion frames must be an object")
            return {
                str(self._frame(int(key))): [
                    validate_object(obj, self.width, self.height) for obj in objects
                ]
                for key, objects in rows.items()
            }
        if isinstance(source, list):
            # A plain per-frame point list is convenient for model outputs.
            result: dict[str, list[dict]] = {}
            for row in source:
                if not isinstance(row, dict):
                    raise TypeError("Suggestion rows must be objects")
                frame = self._frame(row.get("frame"))
                if row.get("x") is None or row.get("y") is None:
                    continue
                result.setdefault(str(frame), []).append(
                    validate_object(
                        {"type": "point", "x": row["x"], "y": row["y"]}, self.width, self.height
                    )
                )
            return result
        raise ValueError("Unsupported suggestions format")

    def state(self) -> dict:
        with self.lock:
            return {
                "format": FORMAT,
                "video": self.identity,
                "fps": self.fps,
                "frames": dict(self.labels),
                "suggestions": self.suggestions,
                "resume_frame": self.last_annotated_frame,
            }

    def frame_jpeg(self, frame: int) -> bytes:
        self._frame(frame)
        with self.lock:
            if frame != self.next_frame:
                self.capture.set(cv2.CAP_PROP_POS_FRAMES, frame)
            ok, image = self.capture.read()
            if not ok:
                raise ValueError(f"Cannot decode frame {frame}")
            self.next_frame = frame + 1
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 94])
        if not ok:
            raise ValueError("Cannot encode frame")
        return encoded.tobytes()

    def _write(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_name(self.output.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(
                {
                    "format": FORMAT,
                    "video": self.identity,
                    "fps": self.fps,
                    "frames": self.labels,
                    "last_annotated_frame": self.last_annotated_frame,
                },
                stream,
                ensure_ascii=False,
                indent=2,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.output)

    def set_frame(self, frame: int, status: str, objects: list) -> dict:
        key = str(self._frame(frame))
        label = self._label(status, objects)
        with self.lock:
            previous = self.labels.get(key)
            previous_frame = self.last_annotated_frame
            self.labels[key] = label
            self.last_annotated_frame = frame
            try:
                self._write()
            except OSError:
                if previous is None:
                    self.labels.pop(key, None)
                else:
                    self.labels[key] = previous
                self.last_annotated_frame = previous_frame
                raise
        return label

    def clear_frame(self, frame: int) -> None:
        key = str(self._frame(frame))
        with self.lock:
            previous = self.labels.get(key)
            previous_frame = self.last_annotated_frame
            self.labels.pop(key, None)
            self.last_annotated_frame = max(map(int, self.labels), default=0)
            try:
                self._write()
            except OSError:
                if previous is not None:
                    self.labels[key] = previous
                self.last_annotated_frame = previous_frame
                raise

    def set_absent_range(self, start: int, end: int) -> int:
        self._frame(start)
        self._frame(end)
        if end < start or end - start > 10000:
            raise ValueError("Range must contain 1–10001 frames")
        with self.lock:
            previous = dict(self.labels)
            previous_frame = self.last_annotated_frame
            for frame in range(start, end + 1):
                self.labels[str(frame)] = {"status": "absent", "objects": []}
            self.last_annotated_frame = end
            try:
                self._write()
            except OSError:
                self.labels = previous
                self.last_annotated_frame = previous_frame
                raise
        return end - start + 1

    def close(self) -> None:
        self.capture.release()


def list_files(path: Path, kind: str) -> dict:
    """List local folders for the browser's input/output chooser."""
    directory = path.expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Choose a directory")
    extensions = {".mp4", ".mov", ".mkv", ".avi", ".webm"} if kind == "video" else {".json"}
    entries = []
    for item in directory.iterdir():
        try:
            is_directory = item.is_dir()
            if is_directory or item.suffix.lower() in extensions:
                entries.append({"name": item.name, "path": str(item), "directory": is_directory})
        except OSError:
            continue
    entries.sort(key=lambda row: (not row["directory"], row["name"].lower()))
    return {"path": str(directory), "parent": str(directory.parent), "entries": entries}


def open_store(video: str, output: str, suggestions: str | None = None) -> AnnotationStore:
    if not video or not output:
        raise ValueError("Choose a video and an output JSON file")
    source, destination = Path(video).expanduser(), Path(output).expanduser()
    suggestion_path = Path(suggestions).expanduser() if suggestions else None
    if destination.suffix.lower() != ".json":
        raise ValueError("Output filename must end in .json")
    inputs = [source.resolve()] + ([suggestion_path.resolve()] if suggestion_path else [])
    if (
        destination.resolve() in inputs
        or destination.with_name(destination.name + ".tmp").resolve() in inputs
    ):
        raise ValueError("Output and temporary file must differ from the input files")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not os.access(destination.parent, os.W_OK) or (
        destination.exists() and not os.access(destination, os.W_OK)
    ):
        raise ValueError("Output location is not writable")
    return AnnotationStore(source, destination, suggestion_path)


PROJECT_FORMAT = "video-annotating-interface-project-v1"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}


def write_project(path: Path, project: dict) -> None:
    """Persist queue selection without changing annotation files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(project, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate_project(project: object, path: Path) -> dict:
    if not isinstance(project, dict) or project.get("format") != PROJECT_FORMAT:
        raise ValueError("Choose an annotation project JSON")
    items = project.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Project must contain videos")
    index = project.get("index", 0)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(items):
        raise ValueError("Invalid project index")
    clean, inputs, outputs = [], set(), set()
    for item in items:
        if not isinstance(item, dict):
            raise TypeError("Invalid project entry")
        resolved = {}
        for key in ("video", "output", "suggestions"):
            value = item.get(key)
            if key == "suggestions" and value is None:
                resolved[key] = None
                continue
            if not isinstance(value, str) or not value or not Path(value).is_absolute():
                raise ValueError("Project paths must be absolute")
            resolved[key] = str(Path(value).resolve())
        output = Path(resolved["output"])
        if output.suffix.lower() != ".json" or output in outputs:
            raise ValueError("Output paths must be distinct JSON files")
        outputs.add(output)
        inputs.add(Path(resolved["video"]))
        if resolved["suggestions"]:
            inputs.add(Path(resolved["suggestions"]))
        clean.append(resolved)
    reserved = {path.resolve(), path.with_name(path.name + ".tmp").resolve()}
    temporary_outputs = {value.with_name(value.name + ".tmp").resolve() for value in outputs}
    if (outputs | temporary_outputs) & (inputs | reserved) or reserved & inputs:
        raise ValueError("Project and output paths must differ from all inputs")
    return {"format": PROJECT_FORMAT, "items": clean, "index": index}


def directory_project(payload: dict) -> tuple[Path, dict]:
    video_value = payload.get("video_directory")
    if not isinstance(video_value, str) or not video_value.strip():
        raise ValueError("Choose a video directory")
    video_dir = Path(video_value).expanduser().resolve(strict=True)
    output_value = payload.get("output_directory")
    if not video_dir.is_dir() or not isinstance(output_value, str) or not output_value.strip():
        raise ValueError("Choose video and output directories")
    output_dir = Path(output_value).expanduser().resolve()
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError("Output location must be a directory")
    suggestion_value = payload.get("suggestions_directory")
    suggestions_dir = (
        Path(suggestion_value).expanduser().resolve(strict=True) if suggestion_value else None
    )
    if suggestions_dir and not suggestions_dir.is_dir():
        raise ValueError("Suggestions location must be a directory")
    recursive = payload.get("recursive", False)
    if not isinstance(recursive, bool):
        raise TypeError("Recursive option must be boolean")
    videos = [
        file
        for file in (video_dir.rglob("*") if recursive else video_dir.iterdir())
        if file.is_file() and file.suffix.lower() in VIDEO_EXTENSIONS
    ]

    def natural_key(file):
        relative = file.relative_to(video_dir)
        name = relative.with_suffix("").as_posix()
        return (
            [
                (1, int(part)) if part.isdigit() else (0, part.casefold())
                for part in re.split(r"(\d+)", name)
            ],
            relative.suffix.casefold(),
            relative.as_posix(),
        )

    videos.sort(key=natural_key)
    stem_counts = {}
    for video in videos:
        key = (video.parent, video.stem.casefold())
        stem_counts[key] = stem_counts.get(key, 0) + 1
    items = []
    for video in videos:
        relative = video.relative_to(video_dir)
        # Keep the extension: clip.mp4 and clip.mov must not share an output.
        output = output_dir / relative.parent / (relative.name + ".annotations.json")
        candidates = []
        if suggestions_dir:
            folder = suggestions_dir / relative.parent
            candidates = [
                folder / (relative.name + ".suggestions.json"),
                folder / (relative.stem + ".suggestions.json"),
                folder / (relative.stem + ".json"),
            ]
            candidates = list(dict.fromkeys(file for file in candidates if file.is_file()))
        if len(candidates) > 1:
            raise ValueError(f"Ambiguous suggestions for {relative}: choose one matching JSON")
        if (
            candidates
            and candidates[0].name in {relative.stem + ".suggestions.json", relative.stem + ".json"}
            and stem_counts[(video.parent, video.stem.casefold())] > 1
        ):
            raise ValueError(f"Use {relative.name}.suggestions.json for videos sharing a stem")
        items.append(
            {
                "video": str(video.resolve()),
                "output": str(output.resolve()),
                "suggestions": str(candidates[0].resolve()) if candidates else None,
            }
        )
    path = output_dir / "annotation-project.json"
    return path, validate_project({"format": PROJECT_FORMAT, "items": items, "index": 0}, path)


def project_state(project: dict, path: Path, active: AnnotationStore | None = None) -> dict:
    rows = []
    for index, item in enumerate(project["items"]):
        row = {**item, "index": index, "reviewed": 0, "total": None, "status": "new"}
        try:
            if not Path(item["video"]).is_file():
                raise ValueError("Video file is missing")
            if item["suggestions"] and not Path(item["suggestions"]).is_file():
                raise ValueError("Suggestions file is missing")
            if (
                active
                and active.video == Path(item["video"])
                and active.output == Path(item["output"])
            ):
                row.update(reviewed=len(active.labels), total=active.count)
                row["status"] = "complete" if len(active.labels) == active.count else "in-progress"
            elif Path(item["output"]).exists():
                saved = json.loads(Path(item["output"]).read_text(encoding="utf-8"))
                identity = saved["video"]
                video = Path(item["video"])
                if (
                    saved.get("format") != FORMAT
                    or identity["name"] != video.name
                    or identity["size_bytes"] != video.stat().st_size
                ):
                    raise ValueError("Output belongs to another video or format")
                total = identity["frame_count"]
                frames = saved["frames"]
                if (
                    isinstance(total, bool)
                    or not isinstance(total, int)
                    or total <= 0
                    or not isinstance(frames, dict)
                ):
                    raise ValueError("Invalid output metadata")
                for key, label in frames.items():
                    if str(int(key)) != key or not 0 <= int(key) < total:
                        raise ValueError("Invalid frame key")
                    objects = label["objects"]
                    if (
                        not isinstance(objects, list)
                        or len(objects) > 100
                        or label["status"] not in {"absent", "annotated"}
                        or (label["status"] == "annotated") != bool(objects)
                    ):
                        raise ValueError("Invalid frame label")
                    for obj in objects:
                        validate_object(obj, identity["width"], identity["height"])
                row.update(reviewed=len(frames), total=total)
                row["status"] = "complete" if len(frames) == total else "in-progress"
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            row.update(status="error", error=str(error))
        rows.append(row)
    return {"path": str(path), "index": project["index"], "items": rows}


def create_server(
    video: Path | None = None,
    output: Path | None = None,
    suggestions: Path | None = None,
    *,
    port: int = 8766,
    workspace: Path | None = None,
) -> ThreadingHTTPServer:
    workspace = (workspace or Path.cwd()).resolve()
    store = (
        open_store(str(video), str(output), str(suggestions) if suggestions else None)
        if video and output
        else None
    )
    project = None
    project_path = None
    session_lock = threading.RLock()
    static = Path(__file__).parent / "static"
    html = (static / "index.html").read_bytes()
    setup_html = (static / "setup.html").read_bytes()
    queue_html = (static / "queue.html").read_bytes()

    def current_store():
        if store is None:
            raise ValueError("Choose files before annotating")
        return store

    class Handler(BaseHTTPRequestHandler):
        def send(self, body: bytes, mime: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def json(self, value: dict, status: int = 200) -> None:
            self.send(
                json.dumps(value, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
                status,
            )

        def check_host(self):
            if self.headers.get("Host") not in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }:
                raise ValueError("Invalid host")

        def do_GET(self) -> None:
            route = urlsplit(self.path)
            try:
                self.check_host()
                with session_lock:
                    if route.path == "/":
                        self.send(html if store else setup_html, "text/html; charset=utf-8")
                    elif route.path == "/queue":
                        self.send(queue_html, "text/html; charset=utf-8")
                    elif route.path == "/api/project":
                        self.json(
                            project_state(project, project_path, store)
                            if project
                            else {"items": []}
                        )
                    elif route.path == "/setup":
                        self.send(setup_html, "text/html; charset=utf-8")
                    elif route.path == "/api/setup":
                        self.json(
                            {
                                "input_directory": str(
                                    workspace / "input"
                                    if (workspace / "input").is_dir()
                                    else Path.home()
                                ),
                                "output_directory": str(workspace / "outputs"),
                                "active": store is not None,
                            }
                        )
                    elif route.path == "/api/files":
                        query = parse_qs(route.query)
                        self.json(
                            list_files(
                                Path(query.get("path", [str(Path.home())])[0]),
                                query.get("kind", ["video"])[0],
                            )
                        )
                    elif route.path == "/api/state":
                        self.json(
                            {
                                **current_store().state(),
                                "project": {
                                    "index": project["index"],
                                    "count": len(project["items"]),
                                }
                                if project
                                else None,
                            }
                        )
                    elif route.path == "/api/frame":
                        values = parse_qs(route.query).get("index", [])
                        if len(values) != 1:
                            raise ValueError("One frame index is required")
                        self.send(current_store().frame_jpeg(int(values[0])), "image/jpeg")
                    else:
                        self.json({"error": "Not found"}, 404)
            except (ValueError, TypeError, OSError, cv2.error) as error:
                self.json({"error": str(error)}, 400)

        def do_POST(self) -> None:
            nonlocal store, project, project_path
            try:
                self.check_host()
                origin = self.headers.get("Origin")
                if origin and origin != f"http://{self.headers.get('Host')}":
                    raise ValueError("Invalid origin")
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                    raise ValueError("JSON request required")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise TypeError("Request must be an object")
                with session_lock:
                    if self.path == "/api/project/preview":
                        path, candidate = directory_project(payload)
                        self.json(project_state(candidate, path))
                    elif self.path in {
                        "/api/project/create",
                        "/api/project/load",
                        "/api/project/select",
                    }:
                        if self.path == "/api/project/create":
                            path, candidate = directory_project(payload)
                            if path.exists():
                                raise ValueError(
                                    "Project already exists. Resume it or choose another output directory"
                                )
                        elif self.path == "/api/project/load":
                            path = Path(payload.get("path", "")).expanduser().resolve(strict=True)
                            candidate = validate_project(
                                json.loads(path.read_text(encoding="utf-8")), path
                            )
                        else:
                            if project is None:
                                raise ValueError("Open a directory project first")
                            path, candidate = project_path, dict(project)
                            index = payload.get("index")
                            if (
                                isinstance(index, bool)
                                or not isinstance(index, int)
                                or not 0 <= index < len(candidate["items"])
                            ):
                                raise ValueError("Invalid queue index")
                            candidate["index"] = index
                        item = candidate["items"][candidate["index"]]
                        replacement = open_store(item["video"], item["output"], item["suggestions"])
                        try:
                            write_project(path, candidate)
                        except OSError:
                            replacement.close()
                            raise
                        previous, store = store, replacement
                        project, project_path = candidate, path
                        if previous:
                            previous.close()
                        self.json({"ok": True})
                    elif self.path == "/api/open":
                        replacement = open_store(
                            payload.get("video"), payload.get("output"), payload.get("suggestions")
                        )
                        previous, store = store, replacement
                        project, project_path = None, None
                        if previous:
                            previous.close()
                        self.json(
                            {
                                "ok": True,
                                "resume_frame": store.last_annotated_frame,
                                "reviewed_frames": len(store.labels),
                            }
                        )
                    elif self.path == "/api/frame":
                        label = current_store().set_frame(
                            payload.get("frame"), payload.get("status"), payload.get("objects")
                        )
                        self.json({"ok": True, "label": label})
                    elif self.path == "/api/clear":
                        current_store().clear_frame(payload.get("frame"))
                        self.json({"ok": True})
                    elif self.path == "/api/absent-range":
                        count = current_store().set_absent_range(
                            payload.get("start"), payload.get("end")
                        )
                        self.json({"ok": True, "count": count})
                    else:
                        self.json({"error": "Not found"}, 404)
            except (ValueError, KeyError, TypeError, OSError, cv2.error) as error:
                self.json({"error": str(error)}, 400)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    original_close = server.server_close

    def close():
        original_close()
        with session_lock:
            if store:
                store.close()

    server.server_close = close
    return server


def serve(
    video: Path | None = None,
    output: Path | None = None,
    suggestions: Path | None = None,
    *,
    port: int = 8766,
    open_browser: bool = True,
    workspace: Path | None = None,
) -> None:
    with create_server(video, output, suggestions, port=port, workspace=workspace) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Open {url}", flush=True)
        if output:
            print(f"Saving to {output.resolve()}", flush=True)
        if open_browser:
            webbrowser.open(url)
        server.serve_forever()
