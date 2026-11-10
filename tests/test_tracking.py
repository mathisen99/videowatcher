import json
import tempfile
import unittest
from pathlib import Path

from videowatcher.core import Sample, Store, Tracker, coverage, local_path, scan_folders


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / "Course"
        self.folder.mkdir()
        self.path = str(self.folder / "Lesson 1.mp4")
        Path(self.path).touch()
        self.filename = self.root / "history.sqlite3"
        self.store = Store(self.filename)
        self.store.add_folder(self.folder)
        self.tracker = Tracker(self.store)
        self.clock = 0

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def sample(
        self, position, duration=100, status="Playing", rate=1, dt=1, player="vlc", path=None
    ):
        self.clock += dt
        self.tracker.sample(
            player, Sample(path or self.path, position, duration, status, rate), self.clock
        )

    def play(self, start, end, duration=100, player="vlc", rate=1):
        for position in range(start, end + 1):
            self.sample(position, duration, player=player, rate=rate, dt=1 / rate)

    def row(self):
        return self.store.library()[0]

    def test_accidental_open_is_completely_ignored(self):
        self.play(0, 4)
        self.tracker.disconnect("vlc")
        self.assertEqual(self.row()["plays"], 0)
        self.assertEqual(self.row()["ranges"], "[]")

    def test_brief_opens_do_not_accumulate_to_threshold(self):
        for _ in range(10):
            self.play(0, 5)
            self.tracker.disconnect("vlc")
        self.assertEqual(self.row()["plays"], 0)

    def test_pause_seek_and_suspend_do_not_count(self):
        self.play(0, 35)
        self.sample(35, status="Paused", dt=200)
        self.sample(35, status="Playing")
        self.sample(80)
        self.sample(90, dt=60)
        self.assertEqual(self.row()["seconds"], 35)
        self.assertEqual(self.row()["completed_count"], 0)

    def test_unique_coverage_prevents_skipping_and_repeating_one_section(self):
        self.play(0, 35)
        self.play(0, 35)
        self.play(90, 100)
        self.assertEqual(self.row()["completed_count"], 0)
        self.assertAlmostEqual(coverage(json.loads(self.row()["ranges"])), 45)

    def test_completion_and_rewatch_have_separate_dates(self):
        self.play(0, 95)
        self.assertEqual(self.row()["completed_count"], 1)
        self.assertTrue(self.row()["last_completed"])
        self.tracker.disconnect("vlc")
        self.play(0, 95)
        self.assertEqual(self.row()["completed_count"], 2)
        self.assertEqual(self.row()["plays"], 2)
        self.assertEqual(len(self.store.history(self.path)), 2)

    def test_same_file_loop_starts_new_pass(self):
        self.play(0, 100)
        self.play(0, 95)
        self.assertEqual(self.row()["completed_count"], 2)

    def test_same_file_loop_after_metadata_signal_starts_new_pass(self):
        self.play(0, 100)
        self.tracker.discontinuity("vlc")
        self.play(0, 95)
        self.assertEqual(self.row()["completed_count"], 2)

    def test_resume_survives_process_restart(self):
        self.play(0, 45)
        self.store.db.close()
        self.store = Store(self.filename)
        self.tracker = Tracker(self.store)
        self.play(45, 90)
        self.assertEqual(self.row()["completed_count"], 1)
        self.assertEqual(self.row()["plays"], 2)

    def test_short_video_can_complete(self):
        self.play(0, 9, duration=10)
        self.assertEqual(self.row()["completed_count"], 1)

    def test_double_speed_counts_content_but_actual_watch_time(self):
        self.play(0, 90, rate=2)
        self.assertEqual(self.row()["completed_count"], 1)
        self.assertEqual(self.row()["seconds"], 45)

    def test_cached_vlc_position_does_not_lose_watch_time(self):
        # VLC's cached position may repeat even though playback is advancing.
        for step in range(161):
            self.sample((step // 2) * 0.5, dt=0.25)
        self.assertAlmostEqual(self.row()["seconds"], 40)

    def test_short_video_loop_records_two_completions(self):
        for _ in range(2):
            for step in range(47):
                self.sample(0.15 + step * 0.25, duration=12, dt=0.25)
        self.assertEqual(self.row()["completed_count"], 2)

    def test_unknown_duration_records_time_without_completion(self):
        self.play(0, 95, duration=0)
        self.assertEqual(self.row()["completed_count"], 0)
        self.assertEqual(self.row()["seconds"], 95)

    def test_unmonitored_file_is_ignored(self):
        self.sample(0, path=str(self.root / "Course-other" / "bad.mp4"))
        self.sample(50, path=str(self.root / "Course-other" / "bad.mp4"))
        self.assertEqual(self.store.library(), [])

    def test_same_file_in_two_players_does_not_double_count(self):
        for i in range(96):
            self.sample(i, player="one", dt=0.5)
            self.sample(i, player="two", dt=0.5)
        self.assertEqual(self.row()["completed_count"], 1)
        self.assertEqual(self.row()["plays"], 1)

    def test_remove_and_readd_keeps_history(self):
        self.play(0, 95)
        self.store.remove_folder(str(self.folder))
        self.assertEqual(self.store.library(), [])
        self.store.add_folder(self.folder)
        self.assertEqual(self.row()["completed_count"], 1)

    def test_nested_folder_scan_deduplicates_and_ignores_symlinks(self):
        child = self.folder / "Chapter"
        child.mkdir()
        (child / "clip.MKV").touch()
        (child / "notes.txt").touch()
        (child / "loop").symlink_to(self.folder, target_is_directory=True)
        videos, directories, errors = scan_folders([str(self.folder), str(child)])
        self.assertEqual(len(videos), 2)
        self.assertEqual(len(directories), 2)
        self.assertEqual(errors, [])

    def test_encoded_uri_and_remote_rejection(self):
        self.assertEqual(local_path(Path(self.path).as_uri()), self.path)
        self.assertIsNone(local_path("https://example.com/video.mp4"))
        self.assertIsNone(local_path("file://remote/course/video.mp4"))


if __name__ == "__main__":
    unittest.main()
