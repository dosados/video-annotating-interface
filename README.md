# Video Annotating Interface

Annotate points, boxes, and object absence in video frames. Runs locally in your browser, with automatic saving and optional model suggestions. No account or video uploads.

## Download and run

Download the archive for your system, extract it, and launch:

| System | Download | Launch |
|---|---|---|
| Windows x64 | [ZIP](https://github.com/dosados/video-annotating-interface/releases/latest/download/video-annotating-interface-windows-x86_64.zip) | `video-annotating-interface.exe` |
| Linux x64 | [TAR.GZ](https://github.com/dosados/video-annotating-interface/releases/latest/download/video-annotating-interface-linux-x86_64.tar.gz) | `video-annotating-interface` |
| macOS Apple Silicon | [TAR.GZ](https://github.com/dosados/video-annotating-interface/releases/latest/download/video-annotating-interface-macos-arm64.tar.gz) | `Open.command` |

Python installation is not required. [All releases](https://github.com/dosados/video-annotating-interface/releases). Builds are unsigned; your OS may ask you to allow opening the application.

In the browser, select:

1. **Video** — the video to annotate.
2. **Suggestions JSON** — optional preliminary labels.
3. **Output annotations JSON** — a new file, or an existing result to resume.

Click **Open annotation**. Keep the console open while working. Stop the application with `Ctrl+C` in the console.

## Directory queue

Select **Directory**, choose video and output directories, and optionally a suggestions directory.
Enable **Include subdirectories** if needed. Click **Preview queue** to inspect naturally sorted
videos, matched suggestions, result paths and existing progress, then **Create project and open
first video**. Preview does not decode every video or write files.

For `clip.mp4`, suggestions may be named `clip.mp4.suggestions.json`,
`clip.suggestions.json` or `clip.json`. Only one matching file is allowed. Missing suggestions
are fine. Videos sharing a stem, such as `clip.mp4` and `clip.mov`, require suggestions named
with the full video filename. Subdirectories are mirrored in suggestions and output.
Results are named `clip.mp4.annotations.json`, keeping the extension to avoid collisions.

Use **Next video** to move through the queue or **Queue** to open any item. **Open another…**
returns to file selection. These actions save the displayed frame first, including unchanged
suggestions, and wait for a successful save. An opening error leaves the current video active.
At the end of a video, the editor shows how many frames remain unreviewed and offers
**Go to first unreviewed**. A video is complete only when every frame has a saved label.
You may move to another video while leaving gaps for later.

The output directory contains `annotation-project.json`, which saves the queue and current
video atomically. To continue after restarting, choose **Directory**, browse to that file under
**Resume project JSON**, and click **Resume**. Existing projects are not overwritten by Create.
The queue is fixed when created; new files are not silently added. Project paths are absolute,
so keep the inputs and results at their original locations.

## Annotate

| Action | Control |
|---|---|
| Add a point or move a nearby point | Left click in **Point** mode |
| Remove an object | Right click near it |
| Draw a box | Select **Box**, then drag |
| Previous / next frame | `A` / `D` or `←` / `→` |
| Move by the configured Step | `W` / `S` or `↑` / `↓` |
| Zoom around the cursor | Mouse wheel |
| Pan the zoomed image | Hold `Space` and drag |
| Mark object absence | **Absent** or `X` |

Multiple objects can be marked on each frame. Exact coordinates can be entered in the side panel. Zoom and pan persist across frames.

The absence range tool marks every frame in the selected range as absent, replacing existing labels there.

## Save and resume

Edits save automatically to the selected output JSON. Unchanged suggestions are saved when you move to another frame. No Save button is needed; check for save errors before closing.

To continue later, open the same video and output file. The interface restores the last saved frame. Input suggestions are never overwritten.

[Input and output formats](docs/formats.md)

## Run from source

Requires Python 3.10+:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python run.py
```

On Windows, activate with `.venv\Scripts\activate`. The browser opens at `http://127.0.0.1:8766/`.

[Build instructions](docs/build.md)
