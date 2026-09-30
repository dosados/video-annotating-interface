# Repository guide

Read `docs/formats.md` before changing JSON input, output, or API behavior. Preserve zero-based frame numbers, original-frame pixel coordinates, the distinction between absent and unreviewed, and atomic saving. Keep the application local and dependency-light. Use `python run.py` for manual checks and `pytest` for behavior changes.
