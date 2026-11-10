#!/usr/bin/env python3
"""Install for the current user, without root or a Python package download."""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def desktop_quote(value):
    # Desktop Entry Exec is not a shell; it has its own quoting rules.
    value = str(value).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    # General desktop-entry string unescaping runs before Exec argument parsing.
    return '"' + value.replace("\\", "\\\\") + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--autostart", action="store_true", help="Also start tracking at desktop login"
    )
    parser.add_argument(
        "--prefix", type=Path, help="Stage under PREFIX/{share,bin,config}, e.g. for testing"
    )
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    data = (
        args.prefix / "share"
        if args.prefix
        else Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    )
    config = (
        args.prefix / "config"
        if args.prefix
        else Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    )
    data, config = data.resolve(), config.resolve()
    target = data / "videowatcher/app"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source / "videowatcher",
        target / "videowatcher",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(source / "run.py", target / "run.py")
    shutil.copy2(source / "README.md", target / "README.md")
    if (source / "docs").is_dir():
        shutil.copytree(source / "docs", target / "docs", dirs_exist_ok=True)
    icon = data / "icons/hicolor/scalable/apps/io.github.mathisen99.VideoWatcher.svg"
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "assets/io.github.mathisen99.VideoWatcher.svg", icon)
    command = f"{desktop_quote(sys.executable)} {desktop_quote(target / 'run.py')}"
    desktop = (
        "[Desktop Entry]\nType=Application\nName=Video Watcher\n"
        "Comment=Remember your VLC course progress\n"
        f"Exec={command}\nIcon=io.github.mathisen99.VideoWatcher\n"
        "Terminal=false\nCategories=AudioVideo;Video;\nStartupNotify=true\n"
    )
    applications = data / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    (applications / "io.github.mathisen99.VideoWatcher.desktop").write_text(desktop)
    binaries = args.prefix.resolve() / "bin" if args.prefix else Path.home() / ".local/bin"
    binaries.mkdir(parents=True, exist_ok=True)
    launcher = binaries / "video-watcher"
    launcher.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(target / "run.py"))} "$@"\n'
    )
    launcher.chmod(0o755)
    if args.autostart:
        autostart = config / "autostart"
        autostart.mkdir(parents=True, exist_ok=True)
        (autostart / "io.github.mathisen99.VideoWatcher.desktop").write_text(
            desktop.replace(f"Exec={command}\n", f"Exec={command} --background\n")
            + "X-GNOME-Autostart-enabled=true\n"
        )
    for utility, location in (
        ("update-desktop-database", applications),
        ("gtk-update-icon-cache", data / "icons/hicolor"),
    ):
        executable = shutil.which(utility)
        if executable:
            subprocess.run(
                [executable, str(location)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    print(f"Installed to {target}\nOpen Video Watcher from your application launcher.")
    if args.autostart:
        print("Tracking will also start at login.")


if __name__ == "__main__":
    main()
