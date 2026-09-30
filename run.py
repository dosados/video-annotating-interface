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
    parser.add_argument("video", nargs="?", type=Path,
                        help="Video file; omitted only when input/ contains exactly one video")
    parser.add_argument("--output", type=Path, help="Annotation JSON path")
    parser.add_argument("--suggestions", type=Path, help="Optional JSON suggestions")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    video = args.video
    if video is None:
        candidates = [path for path in (ROOT / "input").iterdir()
                      if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm"}]
        if len(candidates) != 1:
            parser.error("Pass a video path or put exactly one video in input/")
        video = candidates[0]
    output = args.output or ROOT / "output" / f"{video.stem}.annotations.json"
    try:
        serve(video, output, args.suggestions, port=args.port,
              open_browser=not args.no_browser)
    except KeyboardInterrupt:
        print("\nStopped")
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
