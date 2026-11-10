import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from gi.repository import GLib

from videowatcher.core import Store, Tracker
from videowatcher.mpris import PLAYER, POLL_INTERVAL_MS, PREFIX, Monitor, is_vlc


class ReplyBus:
    def __init__(self, value=None, error=False):
        self.value, self.error = value, error

    def call_finish(self, result):
        if self.error:
            raise GLib.Error("Disconnected")
        return self.value


class MprisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "Lesson with spaces.mp4")
        self.store = Store(Path(self.tmp.name) / "history.sqlite3")
        self.store.add_folder(self.tmp.name)
        self.monitor = Monitor.__new__(Monitor)
        self.monitor.tracker = Tracker(self.store)
        self.monitor.players = {PREFIX: ":1.10"}
        self.monitor.epochs = {}
        self.monitor.pending = set()
        self.monitor.closed = False
        self.monitor.bus = None
        self.monitor.changed = lambda: None
        self.messages = []
        self.monitor.status = self.messages.append

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def response(self, epoch=0, status="Playing", uri=None, position=10, duration=100, now=None):
        reply = GLib.Variant(
            "(a{sv})",
            (
                {
                    "Metadata": GLib.Variant(
                        "a{sv}",
                        {
                            "xesam:url": GLib.Variant("s", uri or Path(self.path).as_uri()),
                            "mpris:length": GLib.Variant("x", round(duration * 1_000_000)),
                        },
                    ),
                    "Position": GLib.Variant("x", round(position * 1_000_000)),
                    "Rate": GLib.Variant("d", 1.0),
                    "PlaybackStatus": GLib.Variant("s", status),
                },
            ),
        )
        clock = (
            patch("videowatcher.mpris.time.monotonic", return_value=now)
            if now is not None
            else nullcontext()
        )
        with clock:
            self.monitor.sampled(ReplyBus(reply), None, PREFIX, ":1.10", epoch)

    def test_real_variant_shape_decodes(self):
        self.response()
        session = self.monitor.tracker.sessions[PREFIX]
        self.assertEqual(session.path, self.path)
        self.assertEqual(session.duration, 100)
        self.assertIn("Playing", self.messages[-1])

    def test_vlc_name_matching(self):
        self.assertTrue(is_vlc(PREFIX))
        self.assertTrue(is_vlc(PREFIX + ".instance123"))
        self.assertFalse(is_vlc(PREFIX + "other"))
        self.assertFalse(is_vlc("org.mpris.MediaPlayer2.firefox"))

    def test_seek_invalidates_inflight_sample(self):
        self.response()
        self.monitor.seeked(None, ":1.10", None, None, None, GLib.Variant("(x)", (80_000_000,)))
        self.response(epoch=0)
        self.assertNotIn(PREFIX, self.monitor.tracker.previous)
        self.response(epoch=1)
        self.assertIn(PREFIX, self.monitor.tracker.previous)

    def status_signal(self, status):
        self.monitor.properties_changed(
            None,
            ":1.10",
            None,
            None,
            None,
            GLib.Variant("(sa{sv}as)", (PLAYER, {"PlaybackStatus": GLib.Variant("s", status)}, [])),
        )

    def test_pause_keeps_final_played_interval_but_not_paused_time(self):
        self.response(position=8, now=8)
        self.status_signal("Paused")
        self.response(epoch=1, status="Paused", position=8.2, now=8.3)
        session = self.monitor.tracker.sessions[PREFIX]
        self.assertAlmostEqual(session.seconds, 0.2)
        self.response(epoch=1, status="Paused", position=8.2, now=12)
        self.assertAlmostEqual(session.seconds, 0.2)

    def test_short_video_paused_at_ten_seconds_completes_and_rewatches(self):
        # Exercise the signal/reply path at the monitor's actual polling cadence.
        # The former 1s timer and pause invalidation leave this below 90%.
        interval = POLL_INTERVAL_MS / 1000
        for pass_number in (1, 2):
            offset = pass_number * 100
            position = 0.15
            while position < 10:
                self.response(
                    epoch=self.monitor.epochs.get(PREFIX, 0),
                    position=position,
                    duration=12,
                    now=offset + position,
                )
                position += interval
            self.status_signal("Paused")
            epoch = self.monitor.epochs[PREFIX]
            self.response(epoch=epoch, status="Paused", position=10, duration=12, now=offset + 10.1)
            seconds = self.monitor.tracker.sessions[PREFIX].seconds
            self.response(epoch=epoch, status="Paused", position=10, duration=12, now=offset + 15)
            self.assertEqual(self.monitor.tracker.sessions[PREFIX].seconds, seconds)
            self.status_signal("Playing")
            epoch = self.monitor.epochs[PREFIX]
            position = 10
            while position < 12:
                self.response(
                    epoch=epoch, position=position, duration=12, now=offset + 5.1 + position
                )
                position += interval
            self.status_signal("Stopped")
            row = self.store.library()[0]
            self.assertEqual(row["completed_count"], pass_number)
            self.assertEqual(row["plays"], pass_number)
        self.assertEqual(len(self.store.history(self.path)), 2)

    def test_stop_discards_session(self):
        self.response()
        self.response(status="Stopped")
        self.assertEqual(self.monitor.tracker.sessions, {})

    def test_metadata_refresh_does_not_discard_played_interval(self):
        self.response(position=8, now=8)
        self.monitor.properties_changed(
            None,
            ":1.10",
            None,
            None,
            None,
            GLib.Variant("(sa{sv}as)", (PLAYER, {"Metadata": GLib.Variant("a{sv}", {})}, [])),
        )
        self.response(epoch=1, position=8.25, now=8.25)
        self.assertAlmostEqual(self.monitor.tracker.sessions[PREFIX].seconds, 0.25)

    def test_owner_change_discards_old_session_and_response(self):
        self.response()
        self.monitor.owner_changed(
            None, None, None, None, None, GLib.Variant("(sss)", (PREFIX, ":1.10", ":1.11"))
        )
        self.response()
        self.assertEqual(self.monitor.tracker.sessions, {})
        self.assertEqual(self.monitor.players[PREFIX], ":1.11")

    def test_remote_video_not_tracked(self):
        self.response(uri="https://example.com/video.mp4")
        self.assertEqual(self.monitor.tracker.sessions, {})

    def test_failure_does_not_bridge_unknown_playback(self):
        self.response()
        self.monitor.sampled(ReplyBus(error=True), None, PREFIX, ":1.10", 0)
        self.assertNotIn(PREFIX, self.monitor.tracker.previous)


if __name__ == "__main__":
    unittest.main()
