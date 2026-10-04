# Video Annotating Interface

A local browser interface for marking points, boxes, and absent objects in video frames. It does not require an account or upload videos. It saves each change to a JSON file on your computer.

## Install and run

Python 3.10 or newer is required. From this directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python run.py input/my-video.mp4
```

On Windows, activate the environment with `.venv\Scripts\activate` and use `python run.py input\my-video.mp4`.

The browser opens at `http://127.0.0.1:8766/`. Stop the server with `Ctrl+C`. `python run.py` opens a file selection screen for the video, optional suggestions, and output JSON. Choose an existing output to resume, or a new file to start. By default, labels go to `output/<video-name>.annotations.json`.

Options:

```bash
python run.py path/to/video.mp4 \
  --suggestions input/suggestions.json \
  --output output/labels.json \
  --port 8766
```

The same video and output path resume earlier work at the last saved frame. Older files resume at the highest saved frame. The application checks the video's name, byte size, dimensions, and frame count before loading existing labels.

## Annotate

- Choose **Point** and click a frame, or choose **Box** and drag its corners. You can add multiple objects per frame.
- Enter exact coordinates in the right panel when needed. Coordinates use the original video's pixel grid.
- Choose **Absent** when the target object is not visible. **Remove all** removes all objects and records absence.
- Use the frame buttons, timeline, frame number, or arrow keys to move through the video. Zoom helps with small objects.
- Suggestions appear in cyan and are saved when you leave the frame. Left click replaces the nearest point within 40 original-video pixels, independent of zoom, or adds a new point outside that radius. Other points and boxes are preserved. Right click near an object removes it. The wheel zooms around the cursor inside a fixed viewport. Hold Space and drag with the left mouse button to pan the image. Release Space to annotate again. Zoom and pan are preserved across frames.
- The range tool marks many frames absent after showing how many existing labels it will replace.

The interface saves after each edit and reports save failures. A/D move one frame backward/forward; W/S move by the configured Step. Arrow keys provide the same navigation. X marks absence and C accepts suggestions. For other key bindings, see the panel inside the application.

## Files

`input/` is a convenient place for videos and optional suggestion JSON. `output/` is the default location for annotation JSON. Both folders ignore user files in Git. The exact formats, validation rules, and examples are in [docs/formats.md](docs/formats.md).

The server listens only on `127.0.0.1`. It is designed for one local user and does not expose an authenticated remote service.

## Build an executable

Build on the target operating system. Python is bundled in the executable.

```bash
pip install -e ".[build]"
make build
```

Without Make: `python -m PyInstaller --clean --noconfirm video-annotating-interface.spec`. Find the executable in `dist/` (`.exe` on Windows). Launch it and select local files in the browser. Keep its console open during annotation; stop with Ctrl+C. Closing the browser does not stop the server. Inputs and outputs remain external files. The default output directory is `~/Documents/VideoAnnotations/output/`.

Build Linux, Windows, and macOS separately on each target OS. The interface uses the installed browser.

### Three platform builds

`.github/workflows/build.yml` builds and tests Linux x86_64, Windows x86_64 and macOS Apple Silicon (arm64) on their respective runners. Push to `main`, or open **Actions → Build applications → Run workflow**. Download the matching archive from the run's **Artifacts**, then extract it. Windows starts with the `.exe`; macOS with `Open.command`; Linux with `video-annotating-interface`. Each archive includes a SHA-256 checksum. Artifacts are retained for 30 days.

The macOS build targets Apple Silicon; Intel Macs need a separate x86_64 build. These builds are unsigned. Windows/macOS may require the user to explicitly allow launching a downloaded application. Linux builds require compatible system libraries; the CI build uses Ubuntu 22.04. No installer or Python setup is required on the user's computer.

Run `python scripts/smoke_executable.py` after building to verify bundled file selection, video decoding, saving and resume without opening a browser. Package locally with `python scripts/package.py --target linux-x86_64` (or the matching Windows/macOS target).
