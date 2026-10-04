"""Local HTTP server and durable annotation store."""

from __future__ import annotations

import json
import math
import os
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
        if self.output.exists():
            saved = json.loads(self.output.read_text(encoding="utf-8"))
            if saved.get("format") != FORMAT or saved.get("video") != self.identity:
                raise ValueError("Output file has a different format or video identity")
            if not isinstance(saved.get("frames"), dict):
                raise ValueError("Output frames must be an object")
            for key, label in saved["frames"].items():
                frame = self._frame(int(key))
                if key != str(frame):
                    raise ValueError("Frame keys must be canonical integer strings")
                self.labels[key] = self._label(label.get("status"), label.get("objects"))
        self.last_annotated_frame = max(map(int, self.labels), default=0)
        if self.output.exists() and "last_annotated_frame" in saved:
            self.last_annotated_frame = self._frame(saved["last_annotated_frame"])
        self.suggestions = self._read_suggestions(suggestions) if suggestions else {}

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
            self.labels[key] = label
            self.last_annotated_frame = frame
            self._write()
        return label

    def clear_frame(self, frame: int) -> None:
        key = str(self._frame(frame))
        with self.lock:
            self.labels.pop(key, None)
            self.last_annotated_frame = max(map(int, self.labels), default=0)
            self._write()

    def set_absent_range(self, start: int, end: int) -> int:
        self._frame(start)
        self._frame(end)
        if end < start or end - start > 10000:
            raise ValueError("Range must contain 1–10001 frames")
        with self.lock:
            for frame in range(start, end + 1):
                self.labels[str(frame)] = {"status": "absent", "objects": []}
            self.last_annotated_frame = end
            self._write()
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
    if destination.resolve() in inputs:
        raise ValueError("Output must differ from the input files")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not os.access(destination.parent, os.W_OK) or (
        destination.exists() and not os.access(destination, os.W_OK)
    ):
        raise ValueError("Output location is not writable")
    return AnnotationStore(source, destination, suggestion_path)


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
    session_lock = threading.RLock()
    static = Path(__file__).parent / "static"
    html = (static / "index.html").read_bytes()
    setup_html = (static / "setup.html").read_bytes()

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
                                "output_directory": str(workspace / "output"),
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
                        self.json(current_store().state())
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
            nonlocal store
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
                    if self.path == "/api/open":
                        replacement = open_store(
                            payload.get("video"), payload.get("output"), payload.get("suggestions")
                        )
                        previous, store = store, replacement
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
