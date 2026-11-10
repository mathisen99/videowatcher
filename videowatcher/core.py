"""Playback accounting, independent of GTK and D-Bus."""

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mkv",
        ".webm",
        ".avi",
        ".mov",
        ".m4v",
        ".wmv",
        ".flv",
        ".mpeg",
        ".mpg",
        ".ogv",
        ".ts",
        ".mts",
        ".m2ts",
        ".3gp",
        ".vob",
    }
)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_path(uri):
    """Never mistake a remote URL for a local monitored file."""
    parts = urlsplit(uri)
    if parts.scheme != "file" or parts.netloc not in ("", "localhost"):
        return None
    try:
        return str(Path(unquote(parts.path)).resolve())
    except (OSError, ValueError):
        return None


def inside(path, folder):
    return Path(path).is_relative_to(folder)


def merge_ranges(ranges):
    result = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if result and start <= result[-1][1] + 0.05:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def coverage(ranges):
    return sum(end - start for start, end in merge_ranges(ranges))


def scan_folders(folders):
    """Run in a worker. No symlink traversal and no access to video contents."""
    videos, directories, errors = set(), set(), []
    for folder in folders:
        if not os.path.isdir(folder):
            errors.append(f"Folder unavailable: {folder}")
            continue
        for root, dirs, files in os.walk(folder, onerror=lambda e: errors.append(str(e))):
            directories.add(root)
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and not os.path.islink(os.path.join(root, d))
            ]
            for name in files:
                candidate = Path(root, name)
                if candidate.suffix.lower() in VIDEO_EXTENSIONS and not candidate.is_symlink():
                    videos.add(str(candidate))
    return sorted(videos), sorted(directories), errors


class Store:
    def __init__(self, filename):
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(filename)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS folders(path TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS videos(
                path TEXT PRIMARY KEY, duration REAL NOT NULL DEFAULT 0,
                ranges TEXT NOT NULL DEFAULT '[]', cycle_done INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sessions(
                id INTEGER PRIMARY KEY, path TEXT NOT NULL REFERENCES videos(path),
                started TEXT NOT NULL, last_seen TEXT NOT NULL,
                seconds REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS completions(
                id INTEGER PRIMARY KEY, path TEXT NOT NULL REFERENCES videos(path),
                completed TEXT NOT NULL, session_id INTEGER REFERENCES sessions(id)
            );
            CREATE INDEX IF NOT EXISTS sessions_path ON sessions(path);
            CREATE INDEX IF NOT EXISTS completions_path ON completions(path);
        """)

    def folders(self):
        return [r[0] for r in self.db.execute("SELECT path FROM folders ORDER BY path")]

    def add_folder(self, path):
        path = str(Path(path).expanduser().resolve())
        if not os.path.isdir(path):
            raise ValueError("Choose an existing folder.")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO folders VALUES (?)", (path,))
        return path

    def remove_folder(self, path):
        with self.db:
            self.db.execute("DELETE FROM folders WHERE path=?", (path,))

    def monitored(self, path):
        return Path(path).suffix.lower() in VIDEO_EXTENSIONS and any(
            inside(path, folder) for folder in self.folders()
        )

    def add_videos(self, paths):
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO videos(path) VALUES (?)", ((p,) for p in paths)
            )

    def setting(self, key, default):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))

    def video(self, path):
        return self.db.execute("SELECT * FROM videos WHERE path=?", (path,)).fetchone()

    def library(self):
        rows = self.db.execute("""
            SELECT v.*, COALESCE(s.plays,0) AS plays, COALESCE(s.seconds,0) AS seconds,
                   s.first_seen, s.last_seen, COALESCE(s.longest_session,0) AS longest_session,
                   COALESCE(c.completed_count,0) AS completed_count,
                   c.first_completed, c.last_completed
            FROM videos v
            LEFT JOIN (SELECT path,COUNT(*) plays,SUM(seconds) seconds,MIN(started) first_seen,
                              MAX(last_seen) last_seen,MAX(seconds) longest_session
                       FROM sessions GROUP BY path) s ON s.path=v.path
            LEFT JOIN (SELECT path,COUNT(*) completed_count,MIN(completed) first_completed,
                              MAX(completed) last_completed
                       FROM completions GROUP BY path) c ON c.path=v.path
            ORDER BY v.path COLLATE NOCASE
        """).fetchall()
        folders = self.folders()
        return [dict(r) for r in rows if any(inside(r["path"], f) for f in folders)]

    def history(self, path):
        return self.db.execute(
            """
            SELECT s.*,c.completed FROM sessions s
            LEFT JOIN completions c ON c.session_id=s.id
            WHERE s.path=? ORDER BY s.id DESC
        """,
            (path,),
        ).fetchall()

    def activity(self, paths, limit=30):
        """Recent qualified sessions within a library/folder, without SQL parameter limits."""
        paths = set(paths)
        if not paths or limit == 0:
            return []
        result = []
        cursor = self.db.execute("""
            SELECT s.*,c.completed FROM sessions s
            LEFT JOIN completions c ON c.session_id=s.id
            ORDER BY s.last_seen DESC,s.id DESC
        """)
        for row in cursor:
            if row["path"] in paths:
                result.append(dict(row))
                if limit is not None and len(result) >= limit:
                    break
        cursor.close()
        return result

    def save(self, session, ranges, completed):
        now = utc_now()
        with self.db:
            if session.id is None:
                session.id = self.db.execute(
                    "INSERT INTO sessions(path,started,last_seen,seconds) VALUES (?,?,?,?)",
                    (session.path, session.started, now, session.seconds),
                ).lastrowid
            else:
                self.db.execute(
                    "UPDATE sessions SET seconds=?,last_seen=? WHERE id=?",
                    (session.seconds, now, session.id),
                )
            self.db.execute(
                "UPDATE videos SET duration=?,ranges=?,cycle_done=? WHERE path=?",
                (session.duration, json.dumps(ranges), int(session.done), session.path),
            )
            if completed:
                self.db.execute(
                    "INSERT INTO completions(path,completed,session_id) VALUES (?,?,?)",
                    (session.path, now, session.id),
                )


@dataclass
class Sample:
    path: str
    position: float
    duration: float
    status: str = "Playing"
    rate: float = 1.0


@dataclass
class Session:
    path: str
    duration: float
    base: list
    started: str = field(default_factory=utc_now)
    ranges: list = field(default_factory=list)
    seconds: float = 0.0
    id: int | None = None
    done: bool = False
    last_position: float = 0.0


class Tracker:
    """Conservative accounting: no credit for seeks, pauses or suspend gaps.

    Each qualified session contributes unique watched ranges to the current pass.
    An unfinished pass survives restarts; a completed pass resets on the next play.
    """

    def __init__(self, store):
        self.store = store
        self.sessions = {}
        self.previous = {}

    def disconnect(self, player):
        self.sessions.pop(player, None)
        self.previous.pop(player, None)

    def discontinuity(self, player):
        self.previous.pop(player, None)

    def sample(self, player, sample, now):
        if sample is None or sample.status == "Stopped" or not self.store.monitored(sample.path):
            self.disconnect(player)
            return False
        # A file playing in two VLC windows still represents one watch session.
        if any(key != player and value.path == sample.path for key, value in self.sessions.items()):
            self.disconnect(player)
            return False
        session = self.sessions.get(player)
        previous = self.previous.get(player)
        # VLC can loop/restart the same file without changing metadata.
        replay = (
            session
            and session.done
            and session.last_position > sample.position + 2
            and sample.position < 3
        )
        if session is None or session.path != sample.path or replay:
            self.store.add_videos([sample.path])
            video = self.store.video(sample.path)
            base = [] if video["cycle_done"] else json.loads(video["ranges"])
            session = Session(sample.path, sample.duration, base)
            self.sessions[player] = session
            previous = None
        session.duration = sample.duration or session.duration
        session.last_position = sample.position
        if not previous:
            self.previous[player] = (sample, now)
            return False
        last, then = previous
        elapsed, delta = now - then, sample.position - last.position
        if (
            sample.status == last.status == "Playing"
            and sample.rate == last.rate
            and delta == 0
            and 0 < elapsed <= 6
        ):
            # VLC updates its position in steps. Keep the time of the last
            # distinct position instead of losing time on duplicate readings.
            return False
        self.previous[player] = (sample, now)
        expected = elapsed * last.rate
        if (
            last.status != "Playing"
            or sample.status not in ("Playing", "Paused")
            or not 0 < elapsed <= 6
            or last.rate <= 0
            or delta <= 0
            or delta - expected > max(0.65, expected * 0.25)
            or (sample.status == "Playing" and expected - delta > max(0.65, expected * 0.25))
        ):
            return False
        # Never credit more than the observed wall time or the position advance.
        session.seconds += min(elapsed, delta / last.rate)
        end = min(sample.position, session.duration) if session.duration > 0 else sample.position
        session.ranges = merge_ranges(session.ranges + [[max(0, last.position), end]])
        threshold = float(self.store.setting("minimum_seconds", "30"))
        if session.duration > 0:
            threshold = min(threshold, session.duration * 0.5 / max(1, sample.rate))
        if session.seconds + 1e-6 < threshold:
            return False
        ranges = merge_ranges(session.base + session.ranges)
        completed = (
            not session.done
            and session.duration > 0
            and coverage(ranges) / session.duration >= 0.9 - 1e-6
        )
        session.done |= completed
        self.store.save(session, ranges, completed)
        return True
