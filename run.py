"""Start the local annotation interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from video_annotating_interface.app import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate video frames in a local browser")
    parser.add_argument(
        "video",
        nargs="?",
        type=Path,
        help="Optional video path; omitted to choose files in the browser",
    )
    parser.add_argument("--output", type=Path, help="Annotation JSON path")
    parser.add_argument("--suggestions", type=Path, help="Optional JSON suggestions")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    video = args.video
    workspace = (
        Path.home() / "Documents" / "VideoAnnotations" if getattr(sys, "frozen", False) else ROOT
    )
    output = args.output or (
        workspace / "output" / f"{video.stem}.annotations.json" if video else None
    )
    try:
        serve(
            video,
            output,
            args.suggestions,
            port=args.port,
            open_browser=not args.no_browser,
            workspace=workspace,
        )
    except KeyboardInterrupt:
        print("\nStopped")
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
