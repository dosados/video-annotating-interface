"""Verify the bundled server, file selection, annotation saving and resume."""

import json
import os
import queue
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
executable = (
    ROOT
    / "dist"
    / ("video-annotating-interface.exe" if os.name == "nt" else "video-annotating-interface")
)


def stop_process(process):
    """Stop the executable and its PyInstaller child before deleting test files."""
    if os.name == "nt" and process.poll() is None:
        # Onefile has a bootloader parent and an application child. TerminateProcess
        # on the parent alone leaves the child holding the video open on Windows.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    elif process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def main():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        video = folder / "sample.mp4"
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
        assert writer.isOpened(), "Cannot create test video"
        for i in range(5):
            writer.write(np.full((48, 64, 3), i * 35, np.uint8))
        writer.release()
        output = folder / "annotations.json"
        log = folder / "stderr.log"
        with log.open("w") as errors:
            process = subprocess.Popen(
                [str(executable), "--no-browser", "--port", "0"],
                cwd=folder,
                stdout=subprocess.PIPE,
                stderr=errors,
                text=True,
            )
            try:
                lines = queue.Queue()
                threading.Thread(
                    target=lambda: lines.put(process.stdout.readline()), daemon=True
                ).start()
                line = lines.get(timeout=90).strip()
                assert line.startswith("Open http://"), (line, log.read_text())
                base = line.split(" ", 1)[1]

                def get(path=""):
                    return urlopen(base + path, timeout=20)

                def post(path, payload):
                    request = Request(
                        base + path,
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    return json.load(urlopen(request, timeout=20))

                assert b"Choose local files" in get().read()
                files = {"video": str(video), "output": str(output)}
                assert post("api/open", files)["reviewed_frames"] == 0
                assert get("api/frame?index=2").read().startswith(b"\xff\xd8")
                post(
                    "api/frame",
                    {
                        "frame": 2,
                        "status": "annotated",
                        "objects": [{"type": "point", "x": 12, "y": 14}],
                    },
                )
                assert json.loads(output.read_text())["last_annotated_frame"] == 2
                assert post("api/open", files)["resume_frame"] == 2
                assert json.load(get("api/state"))["frames"]["2"]["objects"][0]["x"] == 12
                print("Bundled application: file selection, decoding, saving and resume passed")
            finally:
                stop_process(process)
                if process.stdout:
                    process.stdout.close()


if __name__ == "__main__":
    main()
