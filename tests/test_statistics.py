import tempfile
import unittest
from pathlib import Path

from videowatcher.core import Store, inside
from videowatcher.statistics import (
    HISTORY_HEADERS,
    SUMMARY_HEADERS,
    csv_safe,
    history_export,
    summarize,
    summary_export,
)


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.course = self.root / "Course"
        self.other = self.root / "Other"
        self.course.mkdir()
        self.other.mkdir()
        self.filename = self.root / "history.sqlite3"
        self.store = Store(self.filename)
        self.store.add_folder(self.course)
        self.store.add_folder(self.other)
        self.paths = [str(self.course / f"{number}.mp4") for number in range(3)]
        self.outside = str(self.other / "other.mp4")
        self.store.add_videos(self.paths + [self.outside])
        with self.store.db:
            for day, path, seconds, completed in (
                (1, self.paths[0], 100, False),
                (2, self.paths[0], 200, True),
                (3, self.paths[0], 300, True),
                (4, self.paths[1], 80, False),
                (5, self.outside, 2000, False),
            ):
                started = f"2026-11-{day:02}T12:00:00+00:00"
                last = f"2026-11-{day:02}T13:00:00+00:00"
                session = self.store.db.execute(
                    "INSERT INTO sessions(path,started,last_seen,seconds) VALUES (?,?,?,?)",
                    (path, started, last, seconds),
                ).lastrowid
                if completed:
                    self.store.db.execute(
                        "INSERT INTO completions(path,completed,session_id) VALUES (?,?,?)",
                        (path, last, session),
                    )

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def course_rows(self):
        return [row for row in self.store.library() if inside(row["path"], str(self.course))]

    def test_course_statistics_do_not_double_count_completed_videos(self):
        stats = summarize(self.course_rows())
        self.assertEqual(stats["videos"], 3)
        self.assertEqual(stats["completed"], 1)
        self.assertEqual(stats["completions"], 2)
        self.assertEqual(stats["rewatches"], 1)
        self.assertEqual(stats["remaining"], 2)
        self.assertEqual(stats["in_progress"], 1)
        self.assertEqual(stats["not_started"], 1)
        self.assertAlmostEqual(stats["fraction"], 1 / 3)
        self.assertEqual(stats["seconds"], 680)
        self.assertEqual(stats["sessions"], 4)
        self.assertEqual(stats["average_session"], 170)
        self.assertEqual(stats["longest_session"], 300)

    def test_existing_history_supplies_first_and_last_dates(self):
        self.store.db.close()
        self.store = Store(self.filename)
        row = self.course_rows()[0]
        self.assertEqual(row["first_seen"], "2026-11-01T12:00:00+00:00")
        self.assertEqual(row["last_seen"], "2026-11-03T13:00:00+00:00")
        self.assertEqual(row["first_completed"], "2026-11-02T13:00:00+00:00")
        self.assertEqual(row["last_completed"], "2026-11-03T13:00:00+00:00")
        self.assertEqual(summarize(self.course_rows())["last_seen"], "2026-11-04T13:00:00+00:00")

    def test_recent_activity_is_scoped_sorted_and_limited(self):
        activity = self.store.activity(self.paths, limit=2)
        self.assertEqual(len(activity), 2)
        self.assertEqual(activity[0]["path"], self.paths[1])
        self.assertEqual(activity[1]["completed"], "2026-11-03T13:00:00+00:00")
        self.assertEqual(len(self.store.activity(self.paths, limit=None)), 4)
        self.assertEqual(self.store.activity([]), [])

    def test_exports_keep_individual_session_times_and_unknown_lengths(self):
        summaries = list(summary_export(self.course_rows()))
        self.assertTrue(all(len(row) == len(SUMMARY_HEADERS) for row in summaries))
        self.assertEqual(summaries[0][SUMMARY_HEADERS.index("Video duration seconds")], "")
        sessions = list(history_export(self.store.activity(self.paths, limit=None)))
        self.assertEqual(len(sessions), 4)
        self.assertTrue(all(len(row) == len(HISTORY_HEADERS) for row in sessions))
        self.assertEqual(sessions[0][2], "2026-11-04T12:00:00+00:00")
        self.assertEqual(sessions[0][3], "2026-11-04T13:00:00+00:00")
        self.assertEqual(sessions[0][4], 80)  # Actual watch time, not elapsed session span.
        self.assertEqual(csv_safe("=lesson.mp4"), "'=lesson.mp4")

    def test_empty_statistics_and_unwatched_dates(self):
        stats = summarize([])
        self.assertEqual(stats["fraction"], 0)
        self.assertEqual(stats["average_session"], 0)
        self.assertIsNone(stats["first_seen"])
        row = self.course_rows()[2]
        self.assertIsNone(row["first_seen"])
        self.assertIsNone(row["first_completed"])


if __name__ == "__main__":
    unittest.main()
