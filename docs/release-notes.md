This release adds a compact category timeline and temporal event annotation.

- Create single-frame events and intervals, with optional point or box positions.
- Confirm or reject event suggestions independently from spatial frame review.
- Edit categories and interval boundaries directly in event rows, with keyboard shortcuts.
- Keep the video viewport stable as label lists grow.
- Open the file picker in the application workspace by default.
- Save the current frame before exiting through **Exit application / Завершить**.

Temporal events use JSON format v3. Existing v1/v2 files remain readable; output is upgraded only when a temporal event is saved or edited. Older application versions cannot read v3 files.

Download the archive for your operating system under **Assets**, extract it, and launch:

- **Windows x64:** `video-annotating-interface.exe`
- **Linux x64:** `video-annotating-interface`
- **macOS Apple Silicon:** `Open.command`

Python installation is not required. Files stay on your computer. Existing annotation output resumes saved progress. Use **Exit application / Завершить** to save and stop the application.

The `.sha256` files contain archive checksums. The applications are unsigned. Source code archives contain source, not the application.
