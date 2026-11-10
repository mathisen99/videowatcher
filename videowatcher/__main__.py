import sys


def main():
    try:
        from .app import Application
    except (ImportError, ValueError) as error:
        print(
            f"Video Watcher needs Python GObject, GTK 4 and libadwaita.\n{error}\nSee README.md for installation instructions.",
            file=sys.stderr,
        )
        return 1
    from gi.repository import Gdk, Gtk

    Gtk.init_check()
    if Gdk.Display.get_default() is None:
        print(
            "Video Watcher could not connect to a display. Run it from your GNOME or X11 desktop session.",
            file=sys.stderr,
        )
        return 1
    return Application().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
