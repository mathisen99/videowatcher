#!/usr/bin/env python3
"""Run with: dbus-run-session -- python3 tools/test_live_vlc.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gi.repository import Gio, GLib
from make_test_videos import make_video

from videowatcher.core import Store, Tracker
from videowatcher.mpris import OBJECT, PLAYER, Monitor


def pump(seconds):
    loop = GLib.MainLoop()
    GLib.timeout_add(int(seconds * 1000), lambda: (loop.quit(), False)[1])
    loop.run()


def main():
    # Refuse the usual desktop bus: this test must not see or control personal VLC.
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not address or "/run/user/" in address:
        raise SystemExit("Run on a private bus: dbus-run-session -- python3 tools/test_live_vlc.py")
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    with tempfile.TemporaryDirectory(prefix="videowatcher-live-") as temporary:
        root = Path(temporary)
        video = root / "course/01 - Test lesson.mp4"
        make_video(video, 60)
        store = Store(root / "history.sqlite3")
        store.add_folder(video.parent)
        tracker = Tracker(store)
        monitor = Monitor(tracker, status=lambda message: None)
        processes = []

        def launch(media=video, rate=2):
            process = subprocess.Popen(
                [
                    "vlc",
                    "--ignore-config",
                    "--no-one-instance",
                    "--intf",
                    "dummy",
                    "--extraintf",
                    "dbus",
                    "--vout",
                    "dummy",
                    "--aout",
                    "dummy",
                    "--no-audio",
                    "--no-video-title-show",
                    "--rate",
                    str(rate),
                    "--play-and-exit",
                    "--",
                    str(media),
                ],
                stdout=subprocess.DEVNULL,
                stderr=log,
            )
            processes.append(process)
            return process

        def stop(process):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            pump(1)

        with open(root / "vlc.log", "w+") as log:
            try:
                process = launch()
                pump(3)
                if not monitor.players or not tracker.sessions:
                    raise AssertionError("VLC did not expose local playback over MPRIS")
                name = next(iter(monitor.players))
                bus.call_sync(
                    name, OBJECT, PLAYER, "Pause", None, None, Gio.DBusCallFlags.NONE, 2000, None
                )
                pump(1)
                before = next(iter(tracker.sessions.values())).seconds
                pump(2)
                assert next(iter(tracker.sessions.values())).seconds == before, "Pause counted"
                stop(process)
                assert store.library()[0]["plays"] == 0, "Accidental open counted"
                print("PASS: brief open ignored and pause excluded", flush=True)
                for pass_number in (1, 2):
                    process = launch()
                    pump(33)
                    stop(process)
                    row = store.library()[0]
                    assert row["completed_count"] == pass_number, dict(row)
                    print(f"PASS: completion {pass_number}", flush=True)
                assert len(store.history(str(video))) == 2
                print("PASS: two dated sessions and one rewatch", flush=True)
                short_video = video.parent / "02 - Short lesson.mp4"
                make_video(short_video, 12)
                for pass_number in (1, 2):
                    process = launch(short_video, rate=1)
                    for _ in range(180):
                        pump(0.1)
                        samples = list(tracker.previous.values())
                        if samples and samples[0][0].position >= 9.8:
                            break
                    else:
                        raise AssertionError("Short clip did not reach the pause position")
                    name = next(iter(monitor.players))
                    bus.call_sync(
                        name,
                        OBJECT,
                        PLAYER,
                        "Pause",
                        None,
                        None,
                        Gio.DBusCallFlags.NONE,
                        2000,
                        None,
                    )
                    pump(3)
                    bus.call_sync(
                        name, OBJECT, PLAYER, "Play", None, None, Gio.DBusCallFlags.NONE, 2000, None
                    )
                    pump(4)
                    stop(process)
                    row = next(row for row in store.library() if row["path"] == str(short_video))
                    assert row["completed_count"] == pass_number, dict(row)
                    print(
                        f"PASS: 12-second clip paused at 10s, completion {pass_number}", flush=True
                    )
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read()[-6000:], file=sys.stderr)
                raise
            finally:
                for process in processes:
                    stop(process)
                monitor.close()
                store.db.close()


if __name__ == "__main__":
    main()
