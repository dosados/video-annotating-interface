## Directory types and annotation workflow

- Directory projects keep a reusable label catalog in `annotation-project.json`.
- Use **Types in directory / Типы директории** to add selected types to the current video. New videos do not inherit catalog-only tracks; reopening a video restores its selected types.
- Right-click a track or timeline mark to open a menu, then choose **Удалить**. Track deletion affects only the current video's annotations and preserves the reusable directory type and other videos.
- Keep the selected annotation tool when switching types; the default is **Point**.

## Seeking and file selection

- Held arrows follow the video's FPS, in either direction, after a 450 ms delay. Single presses retain their frame step.
- Decode neighboring frames in a separate background decoder and use bounded RAM caches. Keep the last image visible while loading and present prepared frames on browser animation frames.
- Save outgoing displayed frames in atomic batches, independently from image delivery. Release stops movement and flushes pending saves; failed saves retain snapshots for retry.
- Add video thumbnails and a scrollable height limit to **Preview queue**; show filenames instead of full paths.
- Add **Show hidden files / Показывать скрытые** to the file chooser, disabled by default.

Existing v1/v2/v3 annotations remain readable. Deleted track IDs are stored to prevent source suggestions from restoring them; older application versions do not honor this suppression field.

Download the archive for your operating system under **Assets**, extract it, and launch:

- **Windows x64:** `video-annotating-interface.exe`
- **Linux x64:** `video-annotating-interface`
- **macOS Apple Silicon:** `Open.command`

Python installation is not required. Files stay on your computer. Use **Exit application / Завершить** to save and stop the application. The `.sha256` files contain archive checksums. Source code archives contain source, not the application.
