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
