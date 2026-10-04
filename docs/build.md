# Build

Build on the target operating system:

```bash
pip install -e ".[build]"
make build
```

Without Make: `python -m PyInstaller --clean --noconfirm video-annotating-interface.spec`. The executable is in `dist/`.

Check the executable with `python scripts/smoke_executable.py`. Package it with `python scripts/package.py --target linux-x86_64`, `windows-x86_64`, or `macos-arm64`, matching the host OS and architecture.

## GitHub Actions

Push to `main` or run **Actions → Build applications → Run workflow** to build and test Linux x64, Windows x64, and macOS Apple Silicon. Workflow artifacts are retained for 30 days.

Push a version tag such as `v0.1.1` to publish a release after all three builds and tests pass. Releases include the application archives and SHA-256 checksums.

Builds are unsigned. Linux CI uses Ubuntu 22.04 and requires compatible system libraries on the user's machine. Intel Macs require a separate x86_64 build.
