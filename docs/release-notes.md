The setup page is now titled **Annotate**. The active **Single video** or **Directory** mode is highlighted in blue. **Output annotations JSON** is filled immediately with `outputs/annotations.json` beside the executable; choosing a video updates the filename automatically while preserving a manually selected output path.

The default output folder is `outputs/` beside the executable (inside the project when running from source). You can choose another writable location. Existing results are not moved.

Download the archive for your operating system under **Assets**, extract it, and launch:

- **Windows x64:** `video-annotating-interface.exe`
- **Linux x64:** `video-annotating-interface`
- **macOS Apple Silicon:** `Open.command`

Python installation is not required. Choose your video, optional suggestions JSON and output JSON in the browser. An existing output resumes saved progress. Edits save automatically. Keep the console open while annotating; stop with Ctrl+C.

The applications are unsigned. The `.sha256` files contain archive checksums. `Source code` archives contain source, not the application.


Directory annotation now supports a persistent video queue, optional recursive scanning,
automatic suggestions matching and separate results for each video. Preview the queue before
creating it and resume later from `annotation-project.json`. The editor saves the current frame
before switching videos, shows gaps at the end of a video, and provides Queue, Next video and
Open another controls.
