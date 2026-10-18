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

When you leave a displayed frame, its current objects are saved to output, including unchanged suggestions. Removing all objects records absence. Frames that have never been visited remain unreviewed. Each object must fit inside the frame. In legacy v1 a point has exactly `type`, `x`, and `y`; a box has exactly `type`, `x`, `y`, `width`, and `height`. Box width and height must be positive. At most 100 objects can be saved on one frame.

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

## Resume and file selection

Output may contain `last_annotated_frame`, the zero-based frame index of the latest save. Opening the output restores it. Older files restore the highest saved frame, or frame 0 when empty. `GET /api/state` includes `resume_frame`.

With no CLI video, `/` serves file selection. `GET /api/setup` returns default directories. `GET /api/files?path=DIR&kind=video` lists folders and supported videos; `kind=json` lists JSON files. `POST /api/open` accepts local `video`, `output`, and optional `suggestions` paths. Existing output is validated before switching. Output must be JSON and distinct from inputs.

## Directory projects

A directory project is separate from annotation output. It uses absolute local paths:

```json
{
  "format": "video-annotating-interface-project-v1",
  "index": 0,
  "items": [
    {
      "video": "/data/videos/clip.mp4",
      "suggestions": "/data/suggestions/clip.mp4.suggestions.json",
      "output": "/data/results/clip.mp4.annotations.json"
    }
  ]
}
```

`index` is the zero-based current queue position. `suggestions` may be null. `items` is a
nonempty, fixed ordered queue. Annotation JSON accepts v1 and v2 as described below. Completion is
computed from reviewed frame keys, never from `last_annotated_frame` alone. Files with invalid
output labels are displayed as errors. Unopened videos without output have unknown frame
counts until opened; preview does not decode them. Full video identity is checked on opening.

Directory scanning supports MP4, MOV, MKV, AVI and WebM, sorts naturally, and optionally
includes subdirectories. For `sub/clip.mp4`, suggestions candidates are
`sub/clip.mp4.suggestions.json`, `sub/clip.suggestions.json` and `sub/clip.json` in the suggestions
directory. Multiple matches are rejected. Shared stems require full-filename suggestions.
The output is `sub/clip.mp4.annotations.json` under the output directory. All outputs must be
distinct and must not overwrite any project, video or suggestions file, including temporary
save paths. Projects are atomically saved as `annotation-project.json` in the output directory.

- `POST /api/project/preview`: accepts `video_directory`, `output_directory`, optional
  `suggestions_directory` and boolean `recursive`; returns queue paths and progress without writing.
- `POST /api/project/create`: accepts the same fields, refuses an existing project, opens the
  first video and saves the project.
- `POST /api/project/load`: accepts `{"path":"/data/results/annotation-project.json"}`,
  validates the project and resumes its selected video and saved frame.
- `POST /api/project/select`: accepts `{"index":1}`, opens that queue item, then persists the
  selection. If opening or project saving fails, the previous session stays active.
- `GET /api/project`: returns `path`, `index` and `items` with `reviewed`, `total` (null when
  unknown), and `status` (`new`, `in-progress`, `complete`, `error`); returns empty items when
  no project is open.

`GET /api/state` adds `project`, either null or an object with `index` and `count`.
`/queue` serves the queue page. Opening a single video via `/api/open` leaves project mode.
Before navigating from the editor to setup, queue or another video, the UI saves the displayed
frame with `/api/frame` and waits for success. API callers must perform that save themselves;
the server cannot infer unsaved displayed suggestions. Visiting a frame alone does not mark it
reviewed. Missing keys remain unreviewed; an explicitly saved empty frame means absent.


## Label types and v2 JSON

The editor has a **Label** selector and **Label types / Типы меток** settings.
New annotations start with an empty catalog. All types are created by the user in the UI; there are no predefined or mandatory domain labels.
Names and colors are editable, and users can add arbitrary entities or events.
Both kinds are spatial annotations on one frame, drawn as a point or a box;
events are instantaneous, not time intervals. No relation between separate objects
is implied. An event and an entity may share coordinates without replacing each other.
Point correction and right-click deletion apply only to the active label type.

New typed results use `video-annotating-interface-v2`. The envelope retains
`video`, `fps`, `frames`, and optional `last_annotated_frame` from v1 and adds
`categories`. Each point or box adds `category_id`:

```json
{
  "format": "video-annotating-interface-v2",
  "video": {"name": "clip.mp4", "size_bytes": 1234567, "frame_count": 1800, "width": 1920, "height": 1080},
  "fps": 60.0,
  "categories": [
    {"id": "object", "name": "Предмет", "kind": "entity", "color": "#ffdb66"},
    {"id": "interaction", "name": "Взаимодействие", "kind": "event", "color": "#ff8b78"},
    {"id": "appearance", "name": "Появление", "kind": "event", "color": "#8ee3ad"}
  ],
  "frames": {
    "42": {"status": "annotated", "objects": [
      {"type": "point", "category_id": "object", "x": 525.5, "y": 620.25},
      {"type": "point", "category_id": "appearance", "x": 525.5, "y": 620.25}
    ]},
    "43": {"status": "absent", "objects": []}
  },
  "last_annotated_frame": 43
}
```

Catalog rules: 0–100 entries; unique stable `id` values matching
`[a-zA-Z][a-zA-Z0-9_-]{0,63}`; a nonblank name of at most 100 characters;
`kind` is `entity` or `event`; `color` is a six-digit hexadecimal `#RRGGBB`.
Every referenced category must exist. No category IDs are reserved or mandatory.
An unused category may be deleted, including the last one. Categories referenced
by any saved object or loaded suggestion cannot be removed. Renaming a category keeps its ID and annotations.
Untyped legacy objects are assigned the generic `object` category (display name
«Объект»), created only when untyped data is actually present. It can be renamed;
it can be removed once no annotations or suggestions reference it. Explicit IDs
and catalogs from existing v2 files are preserved.

Old v1 outputs and suggestions remain readable, including plain per-frame lists.
Opening a file does not rewrite it. Saving a typed object or changing the catalog
atomically upgrades output to v2 and assigns `object` to legacy objects. A caller
saving only untyped objects may continue producing v1. Old versions of the app
cannot open v2 output; retain backups when sharing files with older installations.

Missing frame keys still mean unreviewed. `absent` means a reviewed frame with
**no objects of any type**. Neither `annotated` nor an omitted category establishes
per-category review or per-category absence. **No labels**, **Remove all types**,
and the absence-range action affect every type; they do not merely remove the
selected type. Accepting suggestions never replaces an already reviewed frame.

## Typed suggestions

Use the same catalog and object schema with a different envelope:

```json
{
  "format": "video-annotating-interface-suggestions-v2",
  "categories": [
    {"id": "object", "name": "Предмет", "kind": "entity", "color": "#ffdb66"},
    {"id": "appearance", "name": "Появление", "kind": "event", "color": "#8ee3ad"}
  ],
  "frames": {
    "42": [{"type": "point", "category_id": "appearance", "x": 525.5, "y": 620.25}],
    "43": []
  }
}
```

Suggestions never mark frames reviewed by themselves. Empty arrays apply only to
the explicitly listed frames; missing frames do not imply absence. Output category
names/colors take precedence for matching IDs; new suggestion categories are merged.
Conflicting `kind` values for the same ID are rejected. Directory matching rules
and `<video-filename>.suggestions.json` names are unchanged, so existing archives work.

## Catalog import/export and API

The settings panel exports/imports an independent UTF-8 JSON file:

```json
{
  "format": "video-annotating-interface-categories-v1",
  "categories": [
    {"id": "object", "name": "Предмет", "kind": "entity", "color": "#ffdb66"},
    {"id": "interaction", "name": "Взаимодействие", "kind": "event", "color": "#ff8b78"}
  ]
}
```

Catalog settings belong to each video's output, not to the directory project.
Use export/import to reuse a catalog for another video. Import replaces the
catalog after validation; it does not import annotations or review a frame.
`POST /api/categories` accepts `{"categories": [...]}` and returns
`{"ok": true, "categories": [...]}`. Changes use the same atomic output saving
and rollback on write errors as frame edits. `GET /api/state` always includes
`categories`, even when a legacy output has not yet been upgraded. `/api/frame`
objects accept `category_id`; coordinates, frame numbering and status rules remain
unchanged. Queue progress understands both v1 and v2 outputs.


An empty catalog is valid (`"categories": []`). The editor opens the settings
panel and asks the user to create a type before placing spatial annotations.
Creating a type requires a name, entity/event kind, and color; IDs are generated
by the UI and do not encode any sport or domain. An empty catalog or changing
settings never marks a frame reviewed. Legacy v1 API clients may still submit
untyped objects; these acquire the generic `object` catalog entry as needed.
