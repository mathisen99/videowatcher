"""Summaries and exports derived from existing history; no inferred watch dates."""

from pathlib import Path


def summarize(rows):
    rows = list(rows)
    count = len(rows)
    completed = sum(row["completed_count"] > 0 for row in rows)
    sessions = sum(row["plays"] for row in rows)
    seconds = sum(row["seconds"] for row in rows)
    first_dates = [row["first_seen"] for row in rows if row["first_seen"]]
    last_dates = [row["last_seen"] for row in rows if row["last_seen"]]
    return {
        "videos": count,
        "completed": completed,
        "remaining": count - completed,
        "not_started": sum(row["plays"] == 0 for row in rows),
        "in_progress": sum(row["plays"] > 0 and row["completed_count"] == 0 for row in rows),
        "fraction": completed / count if count else 0,
        "sessions": sessions,
        "completions": sum(row["completed_count"] for row in rows),
        "rewatches": sum(max(0, row["completed_count"] - 1) for row in rows),
        "seconds": seconds,
        "average_session": seconds / sessions if sessions else 0,
        "longest_session": max((row["longest_session"] for row in rows), default=0),
        "first_seen": min(first_dates, default=None),
        "last_seen": max(last_dates, default=None),
    }


SUMMARY_HEADERS = [
    "Video",
    "Path",
    "Sessions",
    "Completions",
    "Rewatches",
    "Seconds watched",
    "Average session seconds",
    "Longest session seconds",
    "Video duration seconds",
    "First watched (UTC)",
    "Last watched (UTC)",
    "First completed (UTC)",
    "Last completed (UTC)",
]
HISTORY_HEADERS = [
    "Video",
    "Path",
    "Session started (UTC)",
    "Last playback (UTC)",
    "Seconds watched",
    "Completed (UTC)",
]


def summary_export(rows):
    for row in rows:
        yield [
            Path(row["path"]).name,
            row["path"],
            row["plays"],
            row["completed_count"],
            max(0, row["completed_count"] - 1),
            round(row["seconds"], 2),
            round(row["seconds"] / row["plays"], 2) if row["plays"] else 0,
            round(row["longest_session"], 2),
            row["duration"] or "",
            row["first_seen"],
            row["last_seen"],
            row["first_completed"],
            row["last_completed"],
        ]


def history_export(sessions):
    for session in sessions:
        yield [
            Path(session["path"]).name,
            session["path"],
            session["started"],
            session["last_seen"],
            round(session["seconds"], 2),
            session["completed"],
        ]


def csv_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value
