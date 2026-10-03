# Input and output formats

This document is the format contract for people and tools that read or write annotation files. All frame numbers are **zero-based integers**. Coordinates are floating-point pixel positions in the original decoded video frame, with `(0, 0)` at the top left.

## Input video

OpenCV must be able to decode the file and report a positive frame count, width, height, and frame rate. Common choices are MP4, MOV, MKV, AVI, and WebM. Frame numbering follows OpenCV's decoded frames. The application serves one video at a time.

## Optional suggestions

The preferred format is an object with `format` and `frames`. `frames` maps frame number strings to arrays of point and box objects:

```json
{
  "format": "video-annotating-interface-suggestions-v1",
  "frames": {
    "0": [{"type": "point", "x": 120.5, "y": 80.25}],
    "1": [{"type": "box", "x": 100, "y": 60, "width": 45, "height": 30}]
  }
}
```

A plain array of per-frame points is also accepted. Extra fields such as `confidence` or `source` are ignored; rows with null coordinates are skipped:

```json
[
  {"frame": 0, "x": 120.5, "y": 80.25, "confidence": 0.8},
  {"frame": 1, "x": null, "y": null}
]
```

When you leave a displayed frame, its current objects are saved to output, including unchanged suggestions. Removing all objects records absence. Frames that have never been visited remain unreviewed. Each object must fit inside the frame. A point has exactly `type`, `x`, and `y`; a box has exactly `type`, `x`, `y`, `width`, and `height`. Box width and height must be positive. At most 100 objects can be saved on one frame.

## Output annotations

The output file is a single JSON object:

```json
{
  "format": "video-annotating-interface-v1",
  "video": {
    "name": "my-video.mp4",
    "size_bytes": 1234567,
    "frame_count": 1800,
    "width": 1920,
    "height": 1080
  },
  "fps": 60.0,
  "frames": {
    "0": {"status": "annotated", "objects": [{"type": "point", "x": 120.5, "y": 80.25}]},
    "1": {"status": "annotated", "objects": [{"type": "box", "x": 100, "y": 60, "width": 45, "height": 30}]},
    "2": {"status": "absent", "objects": []}
  }
}
```

Only reviewed frames appear in `frames`. Missing frame keys are **unreviewed**; they do not mean absent. `annotated` requires at least one object. `absent` requires an empty object list. Multiple points and boxes can coexist in the same `objects` array. Numbers are saved to two decimal places.

The `video` identity guards against accidentally resuming labels on another file. The file is rewritten atomically after each edit: a temporary JSON file is flushed and replaced over the destination. Keep the output on a writable local filesystem.

## API behavior for automation

The local server returns the current state at `GET /api/state` and a JPEG at `GET /api/frame?index=N`. Mutations use JSON requests: `POST /api/frame` with `{"frame":N,"status":"annotated","objects":[...]}` or an `absent` label, `POST /api/clear` with `{"frame":N}`, and `POST /api/absent-range` with `{"start":N,"end":M}`. The range endpoint accepts at most 10,001 frames per request and replaces labels in the range. The server binds to loopback only; these endpoints are intended for local use.
