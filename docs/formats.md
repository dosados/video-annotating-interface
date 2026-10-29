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

Frame JPEG requests prefetch up to 16 neighboring frames into a bounded 32 MiB
JPEG cache using a separate background decoder. Its position does not affect the
foreground decoder; reverse prefetch decodes contiguous windows forward. Buffering
stops and the decoder closes when the video session closes. The browser separately
bounds its decoded image cache to roughly 48 MiB (at least two frames).
Navigation overlaps next-image loading with the outgoing atomic save.
Fetching or prefetching never reviews or saves a frame. Single-step navigation keeps the last
image visible until the next image is ready and the outgoing frame saves successfully.
Only displayed frames are saved; frames skipped by a manual seek stay unreviewed.
Held arrow keys start after 450 ms and present steps at the source video FPS,
synchronized with browser animation frames using fractional deadlines (for example,
25 or 29.97 FPS). At Step 1 this matches normal video speed in either direction;
larger steps move proportionally faster. A decoded lookahead prepares images
independently from annotation saving. Playback never skips intermediate held steps
or catches up in a burst; missing images keep the last frame visible until ready.
Outgoing displayed frames are saved in small atomic batches, normally every 150 ms.
`POST /api/frames` accepts `{"frames":[{"frame":0,"status":"absent","objects":[]}]}`
with 1–32 unique frames and the normal label schema, and returns `ok` and a `frames`
map of the saved labels. Validation or writing failures roll back the whole batch.
Frame requests use a separate decoder lock and do not wait for annotation writes;
session closure waits for active frame readers before releasing video resources.

Releasing the key stops presentation immediately and flushes pending batches.
Save failure stops playback, retaining unsaved snapshots in browser memory; any
subsequent mutation must retry that flush successfully first. Leaving or shutting
down through the application waits for saving; closing the tab with pending frames
triggers the unsaved-changes prompt. Single presses keep their existing step sizes
and immediate outgoing-frame save.


The local server returns the current state at `GET /api/state` and a JPEG at `GET /api/frame?index=N`. Mutations use JSON requests: `POST /api/frame` with `{"frame":N,"status":"annotated","objects":[...]}` or an `absent` label, `POST /api/clear` with `{"frame":N}`, and `POST /api/absent-range` with `{"start":N,"end":M}`. The range endpoint accepts at most 10,001 frames per request and replaces labels in the range. The server binds to loopback only; these endpoints are intended for local use.

## Resume and file selection

Output may contain `last_annotated_frame`, the zero-based frame index of the latest save. Opening the output restores it. Older files restore the highest saved frame, or frame 0 when empty. `GET /api/state` includes `resume_frame`.

With no CLI video, `/` serves file selection. `GET /api/setup` returns default directories. `GET /api/files?path=DIR&kind=video` lists folders and supported videos; `kind=json` lists JSON files. Dot-prefixed files and folders are hidden by default;
`show_hidden=true` includes them. `POST /api/open` accepts local `video`, `output`, and optional `suggestions` paths. Existing output is validated before switching. Output must be JSON and distinct from inputs.

The initial input directory and the default `/api/files` path are the application
workspace: the repository directory when running `run.py`, or the executable's
directory in a packaged application. Existing paths entered in a field still
open their own parent directory. The output default remains `outputs/` in the workspace.

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
nonempty, fixed ordered queue. Annotation JSON accepts v1, v2 and v3 as described below. Completion is
computed from reviewed frame keys, never from `last_annotated_frame` alone. Files with invalid
output labels are displayed as errors. Unopened videos without output have unknown frame
counts until opened. Queue metadata preview does not decode videos. The UI lazily
requests `GET /api/preview?path=VIDEO` for a first-frame JPEG thumbnail (at most
320×180 pixels). This releases its decoder after each request, does not open an
annotation session, and does not write or mark any frames reviewed. Invalid or
undecodable videos return an error; the UI shows a placeholder. Full video identity is checked on opening.

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
In v2, both kinds are spatial annotations on one frame, drawn as a point or a box;
v2 events are instantaneous, not time intervals. For temporal events, see v3 below. No relation between separate objects
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
cannot open v2 output.

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

## Application shutdown

`POST /api/shutdown` with `{}` returns `{"ok": true}` and stops the local server.
It uses the same Host, Origin and JSON checks as other mutations. The normal server
cleanup releases the video and closes the listening socket. API callers must save
any pending frame first. The **Exit application / Завершить** button is available
on setup, queue and editor pages; the editor waits for the current frame save
before requesting shutdown and stays open if saving fails. The browser tab remains
open with a stopped message. Closing a browser tab alone does not stop the server.

## Temporal events and v3 (experimental event editor)

The event editor adds `video-annotating-interface-v3`. It retains v2 categories,
video identity, fps and spatial `frames`, and adds an independent `events` array.
Only saving/editing a temporal event upgrades output to v3. Opening a file does
not rewrite it. v1/v2 files remain readable. Earlier application versions cannot
read v3 output. Spatial event labels in old files remain frame objects; the `↔`
action moves one onto the event timeline atomically, retaining its coordinates.

```json
{
  "format": "video-annotating-interface-v3",
  "video": {"name": "clip.mp4", "size_bytes": 1234567, "frame_count": 1800, "width": 1920, "height": 1080},
  "fps": 60.0,
  "categories": [
    {"id": "bounce", "name": "Удар об пол", "kind": "event", "color": "#ff8b78"}
  ],
  "frames": {},
  "events": [
    {"id": "bounce_42", "category_id": "bounce", "start": 42, "end": 42, "status": "confirmed"},
    {"id": "contact_80", "category_id": "bounce", "start": 80, "end": 84, "status": "suggested", "geometry": {"type": "point", "x": 525.5, "y": 620.25}}
  ]
}
```

Event `start` and `end` are inclusive, zero-based original frame numbers;
`0 <= start <= end < frame_count`. Equal values mean an instantaneous event.
Each event requires a unique stable `id` matching `[a-zA-Z][a-zA-Z0-9_-]{0,95}`
and a `category_id` whose kind is `event`. Up to 10,000 events are allowed.
`geometry` is optional (or null); when present it is a point or box in original
frame pixels, without its own category. For an interval it is one fixed location,
not an interpolated trajectory. The event category supplies the name and color
in the timeline, event list and optional video marker.

Review status is `suggested`, `confirmed` or `rejected`. Editing a suggestion
preserves `suggested` until explicitly confirmed. Rejected events remain in output
as review decisions, hidden from the editor. Event ids link decisions to source
suggestions: saved entries override source suggestions with the same id, so a
rejected suggestion does not reappear on reopening. Producers must keep ids stable
and unique for a video. Consumers should select `confirmed` for final event labels.

Temporal suggestions use `video-annotating-interface-suggestions-v3`, the same
`categories` and `events` fields, and optional spatial `frames` (default `{}`).
Incoming events may omit `status` or use `suggested`; confirmed/rejected statuses
are not accepted as predictions. For example:

```json
{
  "format": "video-annotating-interface-suggestions-v3",
  "categories": [
    {"id": "bounce", "name": "Удар об пол", "kind": "event", "color": "#ff8b78"}
  ],
  "events": [{"id": "bounce_42", "category_id": "bounce", "start": 42, "end": 42}]
}
```

`GET /api/state` adds `events` (saved overrides) and `event_suggestions` (source
predictions). `POST /api/events` with `{"events": [...]}` replaces saved overrides
atomically and returns `{"ok": true, "events": [...]}`. Validation/save failures
leave memory and output unchanged. `POST /api/events/promote` with `frame`, spatial
object `index` and a new event `id` moves a legacy event object into the timeline
in one atomic write, returning `label` and `events` with `ok`. It requires an event
category. Other frame objects are kept; removing the last object records spatial
absence on that frame.

Spatial frame review and temporal event review are independent. Visiting/saving a
frame does not confirm temporal suggestions. Creating an interval never marks its
frames reviewed. `absent`, Empty (X), Clear (Delete), absence ranges and Accept (C)
apply only to frame objects; temporal events and their review status are preserved.
Queue completion continues to measure reviewed spatial frames, not event coverage.
Categories referenced by any event or event suggestion cannot be deleted or
changed to `entity`. Legacy frame-only suggestions keep their original review
behavior: leaving a frame saves its displayed spatial suggestions.


## Timeline deletion

Right-clicking a track title opens a menu; choosing **Удалить** deletes its type and all spatial and temporal marks
for the current video. `POST /api/categories/delete` accepts `{"category_id":"id"}`.
This explicit operation can remove a used type; ordinary catalog updates still reject
removing types in use. The deletion saves atomically and rolls back all state on error.
Other types and previously unreviewed frames are preserved. A reviewed frame whose
last object was removed remains reviewed with `status: "absent"`.

Output may include `deleted_categories`, an array of removed type IDs. It prevents
source suggestions from recreating deleted types or their marks when reopening.
Source files are unchanged. Explicitly reimporting a removed type clears its ID from
this list. Older versions do not honor this suppression list.

Choosing **Удалить** from a temporal mark's context menu records `status: "rejected"` using `/api/events`,
so source predictions do not reappear. Choosing **Удалить** from a spatial run's context menu removes that
type's objects in its inclusive frame range, preserving other objects. The endpoint
`POST /api/marks/delete` accepts `category_id`, `start`, `end`; affected frames are
saved as reviewed, including absence if no objects remain. Temporal events are kept.
Both deletion endpoints return `ok: true` and the updated state from `/api/state`.


## Directory type catalog

Directory projects store a shared `categories` array in `annotation-project.json`
(the output directory). It uses the same validated type schema as video output;
older projects without this field start with an empty catalog. Opening a video
registers its existing types in the catalog without attaching catalog-only types
to that video. Creating/importing/updating types through `/api/categories` also
saves their definitions in the project catalog. Project saves remain atomic.
The catalog is saved before video output; if the subsequent video save fails,
the reusable definition remains in the catalog for retry, and video state rolls back.

`GET /api/project/categories` returns the project's `categories`.
`POST /api/project/categories/add` accepts `{"category_id":"id"}` and copies that
definition to the current video's categories, returning `ok` and `categories`.
Unknown IDs and already attached types are rejected. Adding a type saves its
selection without reviewing any frames. Types are selected independently per
video, including videos with no marks yet. Existing output restores its selected
types; a fresh video starts without catalog-only tracks. Suggestions retain their
existing type behavior. The directory picker button lists all reusable types and
indicates those already added to this video.

Deleting a track in directory mode removes its type and all marks from this video
only. Its definition remains available in the project catalog for other videos
or explicit reattachment. Deleting or renaming a local type does not rewrite the
annotation files of other queue items. Single-video mode has no directory catalog.
