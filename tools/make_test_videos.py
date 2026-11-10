#!/usr/bin/env python3
"""Create tiny real videos for manual testing, without touching mounted media."""

import argparse
import subprocess
from pathlib import Path


def make_video(path, seconds=40, color="teal"):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-n",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x180:r=10",
            "-t",
            str(seconds),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", default=".test-data/videos")
    args = parser.parse_args()
    root = Path(args.destination).resolve()
    make_video(root / "Example course/01 - Introduction.mp4", 40, "teal")
    make_video(root / "Example course/Chapter 1/02 - Practice.mp4", 60, "slateblue")
    make_video(root / "Another course/01 - Short lesson.mp4", 12, "coral")
    print(
        f"Add these folders in Video Watcher:\n{root / 'Example course'}\n{root / 'Another course'}"
    )
