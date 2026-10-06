"""Package the executable built on this operating system."""

import argparse
import hashlib
import platform
import shutil
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument(
    "--target", required=True, choices=["linux-x86_64", "windows-x86_64", "macos-arm64"]
)
args = parser.parse_args()
expected = {
    "linux-x86_64": ("Linux", "x86_64"),
    "windows-x86_64": ("Windows", "amd64"),
    "macos-arm64": ("Darwin", "arm64"),
}
system, machine = expected[args.target]
if platform.system() != system or platform.machine().lower() != machine:
    parser.error(
        f"{args.target} requires {system}/{machine}; this host is {platform.system()}/{platform.machine()}"
    )
name = "video-annotating-interface-" + args.target
folder = ROOT / "build" / "packages" / name
folder.mkdir(parents=True, exist_ok=True)
binary = "video-annotating-interface" + (".exe" if system == "Windows" else "")
shutil.copy2(ROOT / "dist" / binary, folder / binary)
if system == "Darwin":
    launcher = folder / "Open.command"
    launcher.write_text(
        '#!/bin/sh\ncd "$(dirname "$0")" || exit 1\nexec ./video-annotating-interface\n'
    )
    launcher.chmod(0o755)
(folder / "START.txt").write_text(
    "Video Annotating Interface\n\n"
    + ("Double-click Open.command.\n" if system == "Darwin" else f"Launch {binary}.\n")
    + "Choose video, optional suggestions JSON and output annotations JSON in the browser.\n"
    "Choose an existing output to resume. Edits save automatically.\n"
    "For a folder, choose Directory and preview the queue. Resume with annotation-project.json.\n"
    "Keep the console open while annotating. Stop with Ctrl+C.\n"
    "Python is bundled. Files stay on your computer.\n",
    encoding="utf-8",
)
shutil.copy2(ROOT / "LICENSE", folder / "LICENSE")
destination = ROOT / "dist" / "packages"
destination.mkdir(parents=True, exist_ok=True)
archive = destination / (name + (".zip" if system == "Windows" else ".tar.gz"))
if system == "Windows":
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as stream:
        for file in sorted(folder.iterdir()):
            stream.write(file, arcname=name + "/" + file.name)
else:
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(folder, arcname=name)
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n")
print(archive)
