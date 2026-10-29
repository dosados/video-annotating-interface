"""Local HTTP server and durable annotation store."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import webbrowser
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import cv2

FORMAT = "video-annotating-interface-v1"
SUGGESTIONS_FORMAT = "video-annotating-interface-suggestions-v1"
FORMAT_V2 = "video-annotating-interface-v2"
SUGGESTIONS_FORMAT_V2 = "video-annotating-interface-suggestions-v2"
FORMAT_V3 = "video-annotating-interface-v3"
SUGGESTIONS_FORMAT_V3 = "video-annotating-interface-suggestions-v3"
CATEGORIES_FORMAT = "video-annotating-interface-categories-v1"
DEFAULT_CATEGORIES = []
LEGACY_CATEGORY = {"id": "object", "name": "Объект", "kind": "entity", "color": "#ffdb66"}


def validate_categories(value: object) -> list[dict]:
    if not isinstance(value, list) or not 0 <= len(value) <= 100:
        raise ValueError("Choose up to 100 label types")
    result, ids = [], set()
    for category in value:
        if not isinstance(category, dict) or set(category) != {"id", "name", "kind", "color"}:
            raise ValueError("Label types need id, name, kind and color")
        key, name = category["id"], category["name"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", key):
            raise ValueError("Invalid label type id")
        if key in ids:
            raise ValueError("Label type ids must be unique")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("Label type name must contain 1–100 characters")
        if category["kind"] not in ("entity", "event"):
            raise ValueError("Label type kind must be entity or event")
        if not isinstance(category["color"], str) or not re.fullmatch(
            r"#[0-9a-fA-F]{6}", category["color"]
        ):
            raise ValueError("Label type color must be #RRGGBB")
        ids.add(key)
        result.append({**category, "name": name.strip()})
    return result


def validate_object(obj: object, width: int, height: int, categories: list | None = None) -> dict:
    if not isinstance(obj, dict):
        raise TypeError("Object must be a JSON object")
    kind = obj.get("type")
    fields = ("x", "y") if kind == "point" else ("x", "y", "width", "height")
    if kind not in {"point", "box"}:
        raise ValueError("Object type must be point or box")
    expected = {"type", *fields}
    if set(obj) not in (expected, expected | {"category_id"}):
        raise ValueError(f"{kind} needs geometry and optional category_id")
    category = obj.get("category_id", "object")
    if "category_id" in obj and (
        not isinstance(category, str)
        or category
        not in {c["id"] for c in (categories if categories is not None else DEFAULT_CATEGORIES)}
    ):
        raise ValueError("Unknown label type")
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
    result = {"type": kind, **{field: round(float(obj[field]), 2) for field in fields}}
    if "category_id" in obj:
        result["category_id"] = category
    return result


def validate_events(value, count, width, height, categories, *, suggestions=False):
    if not isinstance(value, list) or len(value) > 10000:
        raise ValueError("Events must be a list of at most 10000 entries")
    kinds = {c["id"]: c["kind"] for c in categories}
    result, ids = [], set()
    for event in value:
        if not isinstance(event, dict) or not {"id", "category_id", "start", "end"} <= event.keys():
            raise ValueError("Events need id, category_id, start and end")
        if set(event) - {"id", "category_id", "start", "end", "geometry", "status"}:
            raise ValueError("Unknown event fields")
        key = event["id"]
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,95}", key)
            or key in ids
        ):
            raise ValueError("Event ids must be unique stable identifiers")
        if not isinstance(event["category_id"], str) or kinds.get(event["category_id"]) != "event":
            raise ValueError("Events require an event category")
        start, end = event["start"], event["end"]
        if (
            any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end))
            or not 0 <= start <= end < count
        ):
            raise ValueError("Event interval is outside the video")
        status = event.get("status", "suggested" if suggestions else "confirmed")
        if status not in (
            ("suggested",) if suggestions else ("suggested", "confirmed", "rejected")
        ):
            raise ValueError("Invalid event review status")
        clean = {
            "id": key,
            "category_id": event["category_id"],
            "start": start,
            "end": end,
            "status": status,
        }
        geometry = event.get("geometry")
        if geometry is not None:
            if not isinstance(geometry, dict) or "category_id" in geometry:
                raise ValueError("Event geometry must be an untyped point or box")
            clean["geometry"] = validate_object(geometry, width, height)
        ids.add(key)
        result.append(clean)
    return result


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
        self.frame_cache: OrderedDict[int, bytes] = OrderedDict()
        self.frame_cache_bytes = 0
        self.decode_lock = threading.RLock()
        self.frame_readers = threading.Condition()
        self.active_frame_readers = 0
        self.frames_closed = False
        self.frame_cache_lock = threading.RLock()
        self.prefetch_wakeup = threading.Event()
        self.prefetch_stop = threading.Event()
        self.prefetch_thread = None
        self.prefetch_request = None
        self.last_requested_frame = None
        self.labels: dict[str, dict] = {}
        self.format = FORMAT
        self.deleted_categories = set()
        self.events = []
        self.event_suggestions = []
        self.categories = [dict(category) for category in DEFAULT_CATEGORIES]
        try:
            if self.output.exists():
                saved = json.loads(self.output.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    raise TypeError("Output must be a JSON object")
                if (
                    saved.get("format") not in (FORMAT, FORMAT_V2, FORMAT_V3)
                    or saved.get("video") != self.identity
                ):
                    raise ValueError("Output file has a different format or video identity")
                deleted = saved.get("deleted_categories", [])
                if not isinstance(deleted, list) or any(
                    not isinstance(key, str)
                    or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", key)
                    for key in deleted
                ):
                    raise ValueError("Invalid deleted category ids")
                self.deleted_categories = set(deleted)
                self.format = saved["format"]
                if self.format in (FORMAT_V2, FORMAT_V3):
                    self.categories = validate_categories(saved.get("categories"))
                if self.deleted_categories & {c["id"] for c in self.categories}:
                    raise ValueError("Deleted types must not remain in the catalog")
                if not isinstance(saved.get("frames"), dict):
                    raise ValueError("Output frames must be an object")
                for key, label in saved["frames"].items():
                    if not isinstance(label, dict):
                        raise TypeError("Frame label must be a JSON object")
                    frame = self._frame(int(key))
                    if key != str(frame):
                        raise ValueError("Frame keys must be canonical integer strings")
                    self.labels[key] = self._label(label.get("status"), label.get("objects"))
            if self.format == FORMAT_V3:
                self.events = validate_events(
                    saved.get("events", []), self.count, self.width, self.height, self.categories
                )
            self.last_annotated_frame = max(map(int, self.labels), default=0)
            if self.output.exists() and "last_annotated_frame" in saved:
                self.last_annotated_frame = self._frame(saved["last_annotated_frame"])
            self.suggestions = self._read_suggestions(suggestions) if suggestions else {}
            self._ensure_legacy_category()
            if self.deleted_categories:
                self.categories = [
                    c for c in self.categories if c["id"] not in self.deleted_categories
                ]
                self.suggestions = {
                    key: [
                        o
                        for o in objects
                        if o.get("category_id", "object") not in self.deleted_categories
                    ]
                    for key, objects in self.suggestions.items()
                }
                self.event_suggestions = [
                    e
                    for e in self.event_suggestions
                    if e["category_id"] not in self.deleted_categories
                ]
            if self.format in (FORMAT_V2, FORMAT_V3):
                self._upgrade()
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
        clean = [validate_object(obj, self.width, self.height, self.categories) for obj in objects]
        if (status == "annotated") != bool(clean):
            raise ValueError("Annotated frames need objects; absent frames must have none")
        return {"status": status, "objects": clean}

    def _read_suggestions(self, path: Path) -> dict[str, list[dict]]:
        source = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(source, dict) and source.get("format") in (
            SUGGESTIONS_FORMAT,
            SUGGESTIONS_FORMAT_V2,
            SUGGESTIONS_FORMAT_V3,
        ):
            if source["format"] in (SUGGESTIONS_FORMAT_V2, SUGGESTIONS_FORMAT_V3):
                incoming = validate_categories(source.get("categories"))
                if self.format in (FORMAT_V2, FORMAT_V3):
                    known = {c["id"]: c for c in self.categories}
                    for category in incoming:
                        if (
                            category["id"] in known
                            and known[category["id"]]["kind"] != category["kind"]
                        ):
                            raise ValueError("Suggestion label type conflicts with output")
                        if category["id"] not in known:
                            self.categories.append(category)
                    self.categories = validate_categories(self.categories)
                else:
                    self.categories = incoming
                if self.format != FORMAT_V3:
                    self.format = FORMAT_V2
                if source["format"] == SUGGESTIONS_FORMAT_V3:
                    self.event_suggestions = validate_events(
                        source.get("events", []),
                        self.count,
                        self.width,
                        self.height,
                        self.categories,
                        suggestions=True,
                    )
            rows = source.get("frames", {})
            if not isinstance(rows, dict):
                raise ValueError("Suggestion frames must be an object")
            return {
                str(self._frame(int(key))): [
                    validate_object(obj, self.width, self.height, self.categories)
                    for obj in objects
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
                        {
                            "type": "point",
                            "x": row["x"],
                            "y": row["y"],
                            **({"category_id": row["category_id"]} if "category_id" in row else {}),
                        },
                        self.width,
                        self.height,
                        self.categories,
                    )
                )
            return result
        raise ValueError("Unsupported suggestions format")

    def _ensure_legacy_category(self) -> None:
        objects = [obj for label in self.labels.values() for obj in label["objects"]]
        objects.extend(obj for group in self.suggestions.values() for obj in group)
        if any("category_id" not in obj for obj in objects) and not any(
            c["id"] == "object" for c in self.categories
        ):
            self.categories = [*self.categories, dict(LEGACY_CATEGORY)]

    def _upgrade(self) -> None:
        self._ensure_legacy_category()
        if self.format != FORMAT_V3:
            self.format = FORMAT_V2
        self.labels = {
            key: {
                **label,
                "objects": [
                    {**obj, "category_id": obj.get("category_id", "object")}
                    for obj in label["objects"]
                ],
            }
            for key, label in self.labels.items()
        }
        self.suggestions = {
            key: [{**obj, "category_id": obj.get("category_id", "object")} for obj in objects]
            for key, objects in self.suggestions.items()
        }

    def set_categories(self, categories: object) -> list[dict]:
        clean = validate_categories(categories)
        with self.lock:
            ids = {c["id"] for c in clean}
            used = {
                obj.get("category_id", "object")
                for label in self.labels.values()
                for obj in label["objects"]
            }
            used.update(
                obj.get("category_id", "object")
                for objects in self.suggestions.values()
                for obj in objects
            )
            used.update(event["category_id"] for event in self.events + self.event_suggestions)
            event_types = {event["category_id"] for event in self.events + self.event_suggestions}
            if any(c["id"] in event_types and c["kind"] != "event" for c in clean):
                raise ValueError("Categories used by events must remain event types")
            if not used <= ids:
                raise ValueError("Cannot remove label types used by annotations or suggestions")
            previous = (
                self.categories,
                self.format,
                self.labels,
                self.suggestions,
                self.deleted_categories,
            )
            self.deleted_categories = self.deleted_categories - ids
            self.categories = clean
            self._upgrade()
            try:
                self._write()
            except OSError:
                (
                    self.categories,
                    self.format,
                    self.labels,
                    self.suggestions,
                    self.deleted_categories,
                ) = previous
                raise
            return self.categories

    def delete_category(self, category_id):
        """Remove a track and its labels in one durable transaction."""
        with self.lock:
            if not isinstance(category_id, str) or category_id not in {
                c["id"] for c in self.categories
            }:
                raise ValueError("Unknown label type")
            previous = (
                self.categories,
                self.labels,
                self.suggestions,
                self.events,
                self.event_suggestions,
                self.deleted_categories,
                self.format,
            )
            self._upgrade()
            self.labels = {
                key: {"status": "annotated" if objects else "absent", "objects": objects}
                for key, label in self.labels.items()
                for objects in [[o for o in label["objects"] if o["category_id"] != category_id]]
            }
            self.suggestions = {
                key: [o for o in objects if o["category_id"] != category_id]
                for key, objects in self.suggestions.items()
            }
            self.events = [e for e in self.events if e["category_id"] != category_id]
            self.event_suggestions = [
                e for e in self.event_suggestions if e["category_id"] != category_id
            ]
            self.categories = [c for c in self.categories if c["id"] != category_id]
            self.deleted_categories = self.deleted_categories | {category_id}
            try:
                self._write()
            except OSError:
                (
                    self.categories,
                    self.labels,
                    self.suggestions,
                    self.events,
                    self.event_suggestions,
                    self.deleted_categories,
                    self.format,
                ) = previous
                raise
            return self.state()

    def delete_spatial_marks(self, category_id, start, end):
        """Remove only this type from a displayed spatial run, preserving other types."""
        start, end = self._frame(start), self._frame(end)
        with self.lock:
            if start > end or category_id not in {c["id"] for c in self.categories}:
                raise ValueError("Invalid spatial mark range or type")
            previous = self.labels, self.format, self.suggestions, self.categories
            self._upgrade()
            keys = set(self.labels) | set(self.suggestions)
            for key in keys:
                if not start <= int(key) <= end:
                    continue
                objects = (
                    self.labels[key]["objects"] if key in self.labels else self.suggestions[key]
                )
                remaining = [o for o in objects if o["category_id"] != category_id]
                if len(remaining) != len(objects):
                    self.labels[key] = {
                        "status": "annotated" if remaining else "absent",
                        "objects": remaining,
                    }
            try:
                self._write()
            except OSError:
                self.labels, self.format, self.suggestions, self.categories = previous
                raise
            return self.state()

    def state(self) -> dict:
        with self.lock:
            return {
                "format": self.format,
                "categories": self.categories,
                "video": self.identity,
                "fps": self.fps,
                "frames": dict(self.labels),
                "suggestions": self.suggestions,
                "events": self.events,
                "event_suggestions": self.event_suggestions,
                "resume_frame": self.last_annotated_frame,
            }

    def set_events(self, events):
        with self.lock:
            clean = validate_events(events, self.count, self.width, self.height, self.categories)
            previous = self.events, self.format, self.labels, self.suggestions, self.categories
            self.events = clean
            self._upgrade()
            self.format = FORMAT_V3
            try:
                self._write()
            except OSError:
                self.events, self.format, self.labels, self.suggestions, self.categories = previous
                raise
            return self.events

    def promote_event(self, frame, index, event_id):
        """Move a legacy spatial event onto the timeline in one atomic save."""
        key = str(self._frame(frame))
        with self.lock:
            objects = (
                self.labels[key]["objects"] if key in self.labels else self.suggestions.get(key, [])
            )
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not 0 <= index < len(objects)
            ):
                raise ValueError("Invalid annotation index")
            obj = objects[index]
            event = {
                "id": event_id,
                "category_id": obj.get("category_id", "object"),
                "start": frame,
                "end": frame,
                "status": "confirmed",
                "geometry": {
                    field: value for field, value in obj.items() if field != "category_id"
                },
            }
            # Validate before touching the existing frame.
            validate_events(
                [*self.events, event], self.count, self.width, self.height, self.categories
            )
            remaining = [obj for i, obj in enumerate(objects) if i != index]
            previous = self.labels, self.last_annotated_frame
            self.labels = {
                **self.labels,
                key: self._label("annotated" if remaining else "absent", remaining),
            }
            self.last_annotated_frame = frame
            try:
                self.set_events([*self.events, event])
            except OSError:
                self.labels, self.last_annotated_frame = previous
                raise
            return self.labels[key]

    def _cached_frame(self, frame):
        with self.frame_cache_lock:
            data = self.frame_cache.get(frame)
            if data is not None:
                self.frame_cache.move_to_end(frame)
            return data

    def _cache_frame(self, frame, data):
        with self.frame_cache_lock:
            if self.prefetch_stop.is_set():
                return
            limit = 32 * 1024 * 1024
            if len(data) > limit:
                return
            previous = self.frame_cache.pop(frame, None)
            self.frame_cache_bytes -= len(previous) if previous is not None else 0
            self.frame_cache[frame] = data
            self.frame_cache_bytes += len(data)
            while self.frame_cache_bytes > limit or len(self.frame_cache) > 96:
                _, removed = self.frame_cache.popitem(last=False)
                self.frame_cache_bytes -= len(removed)

    @staticmethod
    def _encode_frame(image):
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 94])
        if not ok:
            raise ValueError("Cannot encode frame")
        return encoded.tobytes()

    def _schedule_prefetch(self, frame):
        with self.frame_cache_lock:
            if self.prefetch_stop.is_set():
                return
            previous = self.last_requested_frame
            direction = -1 if previous is not None and frame < previous else 1
            self.last_requested_frame = frame
            self.prefetch_request = (frame, direction)
            if self.prefetch_thread is None:
                self.prefetch_thread = threading.Thread(target=self._prefetch_loop, daemon=True)
                self.prefetch_thread.start()
            self.prefetch_wakeup.set()

    def _prefetch_loop(self):
        # This decoder never moves the foreground decoder's position or takes the annotation lock.
        capture = cv2.VideoCapture(str(self.video))
        next_frame = -1
        try:
            while not self.prefetch_stop.is_set():
                self.prefetch_wakeup.wait()
                self.prefetch_wakeup.clear()
                if self.prefetch_stop.is_set():
                    return
                with self.frame_cache_lock:
                    at, direction = self.prefetch_request
                indices = [
                    at + direction * i for i in range(1, 17) if 0 <= at + direction * i < self.count
                ]
                if not indices:
                    continue
                # Reverse seeking decodes a contiguous window forward once, rather than
                # repeatedly seeking to a keyframe for every preceding frame.
                low, high = min(indices), max(indices)
                for index in sorted(indices):
                    if self.prefetch_stop.is_set():
                        return
                    with self.frame_cache_lock:
                        latest, latest_direction = self.prefetch_request
                    if latest_direction != direction or not low - 1 <= latest <= high + 1:
                        break
                    if self._cached_frame(index) is not None:
                        continue
                    try:
                        if index != next_frame:
                            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                        ok, image = capture.read()
                        next_frame = index + 1
                        if not ok:
                            break
                        self._cache_frame(index, self._encode_frame(image))
                    except (cv2.error, ValueError):
                        break
        finally:
            capture.release()

    def frame_jpeg(self, frame: int) -> bytes:
        self._frame(frame)
        data = self._cached_frame(frame)
        if data is None:
            with self.decode_lock:
                # Prefetch may finish while another foreground request is decoding.
                data = self._cached_frame(frame)
                if data is None:
                    if frame != self.next_frame:
                        self.capture.set(cv2.CAP_PROP_POS_FRAMES, frame)
                    ok, image = self.capture.read()
                    if not ok:
                        raise ValueError(f"Cannot decode frame {frame}")
                    self.next_frame = frame + 1
                    data = self._encode_frame(image)
                    self._cache_frame(frame, data)
        self._schedule_prefetch(frame)
        return data

    def _write(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_name(self.output.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(
                {
                    "format": self.format,
                    **(
                        {"categories": self.categories}
                        if self.format in (FORMAT_V2, FORMAT_V3)
                        else {}
                    ),
                    "video": self.identity,
                    "fps": self.fps,
                    **(
                        {"deleted_categories": sorted(self.deleted_categories)}
                        if self.deleted_categories
                        else {}
                    ),
                    "frames": self.labels,
                    **({"events": self.events} if self.format == FORMAT_V3 else {}),
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

    def set_frames(self, frames):
        """Save only displayed outgoing frames as one atomic batch."""
        if not isinstance(frames, list) or not 1 <= len(frames) <= 32:
            raise ValueError("A batch needs 1–32 frames")
        with self.lock:
            clean = []
            for row in frames:
                if not isinstance(row, dict) or set(row) != {"frame", "status", "objects"}:
                    raise ValueError("Each batch frame needs frame, status and objects")
                clean.append(
                    (self._frame(row["frame"]), self._label(row["status"], row["objects"]))
                )
            if len({at for at, _ in clean}) != len(clean):
                raise ValueError("Duplicate batch frame")
            previous = (
                self.format,
                self.labels,
                self.suggestions,
                self.last_annotated_frame,
                self.categories,
            )
            objects = [obj for _, label in clean for obj in label["objects"]]
            if any("category_id" not in obj for obj in objects) and not any(
                c["id"] == "object" for c in self.categories
            ):
                self.categories = [*self.categories, dict(LEGACY_CATEGORY)]
            typed = self.format in (FORMAT_V2, FORMAT_V3) or any(
                "category_id" in obj for obj in objects
            )
            if typed:
                self._upgrade()
            else:
                self.labels = dict(self.labels)
            result = {}
            for at, label in clean:
                if typed:
                    label = {
                        **label,
                        "objects": [
                            {**obj, "category_id": obj.get("category_id", "object")}
                            for obj in label["objects"]
                        ],
                    }
                result[str(at)] = label
            self.labels.update(result)
            self.last_annotated_frame = clean[-1][0]
            try:
                self._write()
            except OSError:
                (
                    self.format,
                    self.labels,
                    self.suggestions,
                    self.last_annotated_frame,
                    self.categories,
                ) = previous
                raise
            return result

    def set_frame(self, frame: int, status: str, objects: list) -> dict:
        key = str(self._frame(frame))
        with self.lock:
            label = self._label(status, objects)
            previous = (
                self.format,
                self.labels,
                self.suggestions,
                self.last_annotated_frame,
                self.categories,
            )
            if any("category_id" not in obj for obj in label["objects"]) and not any(
                c["id"] == "object" for c in self.categories
            ):
                self.categories = [*self.categories, dict(LEGACY_CATEGORY)]
            if self.format in (FORMAT_V2, FORMAT_V3) or any(
                "category_id" in obj for obj in label["objects"]
            ):
                if self.format not in (FORMAT_V2, FORMAT_V3):
                    self._upgrade()
                else:
                    self.labels = dict(self.labels)
                label = {
                    **label,
                    "objects": [
                        {**obj, "category_id": obj.get("category_id", "object")}
                        for obj in label["objects"]
                    ],
                }
            else:
                self.labels = dict(self.labels)
            self.labels[key] = label
            self.last_annotated_frame = frame
            try:
                self._write()
            except OSError:
                (
                    self.format,
                    self.labels,
                    self.suggestions,
                    self.last_annotated_frame,
                    self.categories,
                ) = previous
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

    def begin_frame_read(self):
        with self.frame_readers:
            if self.frames_closed:
                raise ValueError("Video session is closed")
            self.active_frame_readers += 1

    def end_frame_read(self):
        with self.frame_readers:
            self.active_frame_readers -= 1
            self.frame_readers.notify_all()

    def close(self) -> None:
        if hasattr(self, "frame_readers"):
            with self.frame_readers:
                self.frames_closed = True
                while self.active_frame_readers:
                    self.frame_readers.wait()
        if hasattr(self, "prefetch_stop"):
            self.prefetch_stop.set()
            self.prefetch_wakeup.set()
            if self.prefetch_thread is not None:
                self.prefetch_thread.join()
        self.capture.release()
        if hasattr(self, "frame_cache"):
            with self.frame_cache_lock:
                self.frame_cache.clear()
                self.frame_cache_bytes = 0


def list_files(path: Path, kind: str, show_hidden: bool = False) -> dict:
    """List local folders for the browser's input/output chooser."""
    directory = path.expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Choose a directory")
    extensions = {".mp4", ".mov", ".mkv", ".avi", ".webm"} if kind == "video" else {".json"}
    entries = []
    for item in directory.iterdir():
        if not show_hidden and item.name.startswith("."):
            continue
        try:
            is_directory = item.is_dir()
            if is_directory or item.suffix.lower() in extensions:
                entries.append({"name": item.name, "path": str(item), "directory": is_directory})
        except OSError:
            continue
    entries.sort(key=lambda row: (not row["directory"], row["name"].lower()))
    return {"path": str(directory), "parent": str(directory.parent), "entries": entries}


def preview_jpeg(path: Path) -> bytes:
    """Decode a small first-frame preview without opening an annotation session."""
    source = path.expanduser().resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in {
        ".mp4",
        ".mov",
        ".mkv",
        ".avi",
        ".webm",
    }:
        raise ValueError("Choose a supported video file")
    capture = cv2.VideoCapture(str(source))
    try:
        ok, frame = capture.read()
        if not ok:
            raise ValueError("Cannot decode video preview")
        height, width = frame.shape[:2]
        scale = min(320 / width, 180 / height, 1)
        if scale < 1:
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            frame = cv2.resize(frame, size)
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            raise ValueError("Cannot encode video preview")
        return encoded.tobytes()
    finally:
        capture.release()


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
    return {
        "format": PROJECT_FORMAT,
        "items": clean,
        "index": index,
        "categories": validate_categories(project.get("categories", [])),
    }


def catalog_with_categories(project: dict, categories: list, *, replace=False) -> dict:
    """Catalog entries are independent from each video's selected label types."""
    catalog = {c["id"]: c for c in project.get("categories", [])}
    for category in validate_categories(categories):
        if replace or category["id"] not in catalog:
            catalog[category["id"]] = category
    return {**project, "categories": validate_categories(list(catalog.values()))}


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
                    saved.get("format") not in (FORMAT, FORMAT_V2, FORMAT_V3)
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
                categories = (
                    validate_categories(saved.get("categories"))
                    if saved["format"] in (FORMAT_V2, FORMAT_V3)
                    else DEFAULT_CATEGORIES
                )
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
                        validate_object(obj, identity["width"], identity["height"], categories)
                if saved["format"] == FORMAT_V3:
                    validate_events(
                        saved.get("events", []),
                        total,
                        identity["width"],
                        identity["height"],
                        categories,
                    )
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
                if route.path == "/api/frame":
                    values = parse_qs(route.query).get("index", [])
                    if len(values) != 1:
                        raise ValueError("One frame index is required")
                    index = int(values[0])
                    active = current_store()
                    active.begin_frame_read()
                    try:
                        data = active.frame_jpeg(index)
                    finally:
                        active.end_frame_read()
                    self.send(data, "image/jpeg")
                    return
                with session_lock:
                    if route.path == "/":
                        self.send(html if store else setup_html, "text/html; charset=utf-8")
                    elif route.path == "/shutdown.js":
                        self.send(
                            (static / "shutdown.js").read_bytes(), "text/javascript; charset=utf-8"
                        )
                    elif route.path == "/queue":
                        self.send(queue_html, "text/html; charset=utf-8")
                    elif route.path == "/api/project/categories":
                        if project is None:
                            raise ValueError("Open a directory project first")
                        self.json({"categories": project.get("categories", [])})
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
                                "input_directory": str(workspace),
                                "output_directory": str(workspace / "outputs"),
                                "active": store is not None,
                            }
                        )
                    elif route.path == "/api/files":
                        query = parse_qs(route.query)
                        self.json(
                            list_files(
                                Path(query.get("path", [str(workspace)])[0]),
                                query.get("kind", ["video"])[0],
                                query.get("show_hidden", ["false"])[0] == "true",
                            )
                        )
                    elif route.path == "/api/preview":
                        values = parse_qs(route.query).get("path", [])
                        if len(values) != 1 or not values[0]:
                            raise ValueError("One video path is required")
                        self.send(preview_jpeg(Path(values[0])), "image/jpeg")
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
                limit = 524288 if self.path == "/api/frames" else 32768
                if not 0 < length <= limit:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise TypeError("Request must be an object")
                with session_lock:
                    if self.path == "/api/shutdown":
                        self.json({"ok": True})
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                    elif self.path == "/api/project/preview":
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
                            candidate = catalog_with_categories(candidate, replacement.categories)
                            write_project(path, candidate)
                        except (OSError, ValueError, TypeError):
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
                    elif self.path == "/api/frames":
                        frames = current_store().set_frames(payload.get("frames"))
                        self.json({"ok": True, "frames": frames})
                    elif self.path == "/api/frame":
                        label = current_store().set_frame(
                            payload.get("frame"), payload.get("status"), payload.get("objects")
                        )
                        self.json({"ok": True, "label": label})
                    elif self.path == "/api/categories/delete":
                        result = current_store().delete_category(payload.get("category_id"))
                        self.json({"ok": True, **result})
                    elif self.path == "/api/marks/delete":
                        result = current_store().delete_spatial_marks(
                            payload.get("category_id"), payload.get("start"), payload.get("end")
                        )
                        self.json({"ok": True, **result})
                    elif self.path == "/api/events/promote":
                        label = current_store().promote_event(
                            payload.get("frame"), payload.get("index"), payload.get("id")
                        )
                        self.json({"ok": True, "label": label, "events": current_store().events})
                    elif self.path == "/api/events":
                        events = current_store().set_events(payload.get("events"))
                        self.json({"ok": True, "events": events})
                    elif self.path == "/api/project/categories/add":
                        if project is None:
                            raise ValueError("Open a directory project first")
                        category_id = payload.get("category_id")
                        category = next(
                            (c for c in project.get("categories", []) if c["id"] == category_id),
                            None,
                        )
                        if category is None:
                            raise ValueError("Choose a type from the directory catalog")
                        current = current_store()
                        if any(c["id"] == category_id for c in current.categories):
                            raise ValueError("This type is already added to this video")
                        categories = current.set_categories([*current.categories, category])
                        self.json({"ok": True, "categories": categories})
                    elif self.path == "/api/categories":
                        clean = validate_categories(payload.get("categories"))
                        if project is not None:
                            previous_types = {c["id"]: c for c in current_store().categories}
                            changed = [c for c in clean if previous_types.get(c["id"]) != c]
                            candidate = catalog_with_categories(project, changed, replace=True)
                            # Persist the reusable definition before attaching it to a video.
                            # If video saving fails the catalog can still be used for retry.
                            write_project(project_path, candidate)
                            project = candidate
                        categories = current_store().set_categories(clean)
                        self.json({"ok": True, "categories": categories})
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
