#!/usr/bin/env python3
"""Exercise the actual widgets with isolated synthetic data on a desktop display."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from videowatcher.app import Application, Gdk, GLib, Gtk
    from videowatcher.core import Sample, Store, Tracker

    Gtk.init_check()
    if Gdk.Display.get_default() is None:
        raise SystemExit("A working Wayland or X11 display is required for this test.")
    with tempfile.TemporaryDirectory(prefix="videowatcher-ui-") as temporary:
        root = Path(temporary)
        os.environ["VIDEOWATCHER_DATA_DIR"] = str(root / "data")
        folder = root / "Example course"
        folder.mkdir()
        store = Store(root / "data/history.sqlite3")
        store.add_folder(folder)
        paths = [str(folder / f"{i:02} - Example lesson.mp4") for i in range(1, 301)]
        for path in paths:
            Path(path).touch()
        store.add_videos(paths)
        tracker = Tracker(store)
        for position in range(96):
            tracker.sample("demo", Sample(paths[0], position, 100), position)
        store.db.close()
        app = Application()
        app.set_application_id("io.github.mathisen99.VideoWatcher.UITest")
        failures = []

        def check():
            try:
                window = app.window
                assert window.model.get_n_items() == 300
                window.details(paths[0])
                window.show_statistics()
                assert window.course_progress.get_fraction() == 1 / 300
                window.sort.set_selected(1)
                assert window.model.get_string(0) == paths[0]
                window.settings()
                window.manage_folders()
                window.search.set_text("Example lesson")
                window.render_library()
                assert window.model.get_n_items() == 300
                window.filter.set_selected(3)
                assert window.model.get_n_items() == 1
                window.filter.set_selected(0)
                window.store.remove_folder(str(folder))
                window.rebuild_folders()
                window.reload()
                assert window.stack.get_visible_child_name() == "empty"
                window.hide_window()
            except Exception as error:
                failures.append(error)
            finally:
                for dialog in list(app.window.dialogs):
                    dialog.close()
                app.quit()
            return False

        GLib.timeout_add_seconds(3, check)
        app.run([sys.argv[0]])
        if failures:
            raise failures[0]
        print(
            "PASS: library, history, statistics, sorting, settings, folders, search, filters and empty state"
        )


if __name__ == "__main__":
    main()
