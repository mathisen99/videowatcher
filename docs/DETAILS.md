# Video Watcher

The short installation instructions are in [the README](../README.md).

## Installer options

`install.sh` defaults to installing native system dependencies and enabling login startup. Run it as your normal desktop user; it uses sudo only for package installation. It downloads the current `main` source archive before installing anything, so rerunning the one-line command installs the latest update without a separate release step.

For a local checkout, use `bash install.sh --source "$PWD"`. Options: `--no-autostart` skips creating a login entry, `--skip-deps` uses already-installed dependencies, and `--prefix DIRECTORY` stages under `DIRECTORY/{share,bin,config}` for testing. On an existing install, `--no-autostart` leaves any existing login entry unchanged; remove it as described below to disable it.

Debian 13's GTK, libadwaita, Python bindings, VLC, and codecs are available in its normal repositories; no extra repository is added. The GitHub Checks workflow runs the tests and staged installer in a Debian 13 container. Arch uses its separate FFmpeg and D-Bus VLC plugins. Fedora uses the codecs available in its enabled repositories. Atomic desktops and distributions without apt, pacman or dnf need a manual installation.

A small native Linux app that remembers which course videos you have watched in VLC. Python, GTK 4, libadwaita and SQLite; no account, server or web runtime.

Add one or more folders, including mounted media folders. Subfolders are included. Watch files normally in VLC, or open them from their history view. The app shows progress, completed videos, rewatch counts, time watched and dated sessions. Search and filter large course libraries; export a CSV summary.

## Viewing statistics

- The main view shows completion progress for all videos or the selected course folder, with counts of unfinished and unstarted videos. Each video shows its session count and most recent playback time. Sort by name, recent viewing, or total watch time.
- **Statistics** shows videos remaining, total watch sessions, completions and rewatches, total actual watch time, average and longest session, first watch date, and most recent playback. The latest 30 sessions appear below the totals.
- Select a video for its first/last watch dates, first/last completion dates, length, total/average/longest watch time, and every recorded session. Each session shows its start, last observed playback, actual time watched, and completion date when applicable. Paused time is excluded from watch time.
- Dates and times are displayed in your local timezone, to the second. A watch session means a visit that passed the accidental-open threshold; it does not necessarily mean the video was completed.
- **Export CSV** exports the selected folder's summary, including subfolders (or all monitored folders). **Export watch history** in Statistics or a video's history exports individual sessions. Exports include exact UTC timestamps. Search and status filters do not change folder-level statistics or exports.

These statistics work with existing saved history; no reset is needed. Unknown video lengths are left unknown until VLC reports them, so the app does not invent a remaining-time estimate for unwatched lessons.

## Run

Requires Python 3.10+, GTK 4.6+, libadwaita 1.1+, PyGObject and VLC with MPRIS/D-Bus support. Install the native VLC package for the most predictable file-path integration. Sandboxed VLC builds are not yet verified.

On Arch:

```sh
sudo pacman -S python-gobject gtk4 libadwaita vlc vlc-plugin-dbus vlc-plugin-ffmpeg
```

On Ubuntu / Debian / Mint:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 vlc
```

On Fedora, with VLC already installed from your usual software source:

```sh
sudo dnf install python3-gobject gtk4 libadwaita vlc vlc-plugin-ffmpeg
```

From this directory:

```sh
python3 run.py
```

To install a launcher for your user and start tracking at each desktop login:

```sh
python3 install.py --autostart
```

Then open **Video Watcher** from the application launcher. No root access is needed for this installation. Omit `--autostart` to install only the launcher. Re-run the installer after updating the source, with Video Watcher quit.

GTK selects Wayland or X11 automatically. To explicitly check either backend from its corresponding desktop session:

```sh
GDK_BACKEND=wayland python3 run.py
GDK_BACKEND=x11 python3 run.py
```

The window's **×** closes the app and stops tracking. **☰ → Hide and Keep Tracking** explicitly hides the window while keeping monitoring active. Launch the app again to reopen the same instance. It must be running during playback; earlier VLC playback cannot be reconstructed. Login autostart can still launch it hidden. Before upgrading from a version older than 0.4, use that version's **Quit** button.

The black-cat theme uses native GTK widgets and bundled SVG art. **Add Folder**, **Manage Folders**, **Refresh**, **Export**, and **Settings** are in the hamburger menu. Keyboard shortcuts: Ctrl+O adds a folder, F5 refreshes, Ctrl+, opens settings, and Ctrl+Q exits. The theme requires no downloaded fonts or images.

## What counts as watching?

- A session is ignored until **30 seconds of actual playback**, adjustable to 5–120 seconds in Settings. For a short video, this is reduced to half its playing time. Brief separate opens do not accumulate toward this threshold.
- A completed pass requires **90% unique content coverage**, which can accumulate across qualified sessions and app restarts. Jumping to the end cannot complete a lesson. Repeating one section does not fill in unwatched sections.
- Pauses, detected seeks, stopped playback and suspend gaps do not earn credit. Playback speed is respected: content progress advances faster at 2×, but time watched measures elapsed watching time.
- Another completed pass is a **rewatch**. Merely reopening a completed video records a new qualified session, not a full rewatch. Each completion keeps its own date.
- Completion dates appear in local time. The database and CSV timestamps use UTC.
- Unknown-duration files can accumulate watch time but cannot be automatically marked complete until VLC reports a duration.

Tracking is conservative and samples VLC every 250 milliseconds while it is present. Pausing preserves the last played fraction of a second, and duplicate position readings do not lose watch time. A small amount of playback at start/stop/seek transitions can still be missed, particularly for extremely short clips. Completed progress bars show 100%. It observes playback, not whether someone is paying attention. Two VLC instances playing the same file are counted once; the first tracked instance owns that file until stopped or closed.

## Folders and data

Folder discovery runs off the UI thread. Directory-change notifications trigger updates, and **Refresh** rescans manually. Use Refresh after reconnecting a removable or network mount; some network filesystems do not report changes. Hidden subdirectories and symlinks are not traversed. MP4, MKV, WebM, AVI, MOV and other common video extensions are recognized.

History is stored locally at `~/.local/share/videowatcher/history.sqlite3` (or under `$XDG_DATA_HOME`). Removing a monitored folder keeps its history and never changes its video files. Missing videos remain listed so their history is retained. Adding the same folder again restores the view. Identity is the canonical file path: moving or renaming a video creates a new entry; automatic rename/content matching is not implemented.

Quit before backing up the database. `VIDEOWATCHER_DATA_DIR=/some/folder python3 run.py` uses an isolated data location for testing. Do not point it at the same database from two different desktop sessions at once.

The installer copies the app under `~/.local/share/videowatcher/app`, creates `~/.local/bin/video-watcher`, and adds an application entry and icon. To disable login startup, remove `~/.config/autostart/io.github.mathisen99.VideoWatcher.desktop`. These locations respect XDG data/config overrides, except the conventional `~/.local/bin` launcher. Removing the installed app and desktop entries does not require deleting the history database.

## VLC troubleshooting

The bottom status line distinguishes ready, connected, playing and connection problems. If VLC is open but never connects, enable **D-Bus control interface** in VLC under **Tools → Preferences → Show settings: All → Interface → Control interfaces**, save, and restart VLC. On Arch, install `vlc-plugin-dbus` as well. Both applications must run as the same desktop user on the same session bus. No VLC configuration is silently changed.

## Verification

Run the accounting and adapter tests:

```sh
python3 -m unittest discover -s tests -v
```

Generate small real video fixtures (requires FFmpeg):

```sh
python3 tools/make_test_videos.py
VIDEOWATCHER_DATA_DIR="$PWD/.test-data/manual-history" python3 run.py
```

Add the printed folders. Open a lesson in VLC for two seconds and close it: no watch session should appear. Play most of the 40-second lesson: it should complete. Repeat it: the rewatch count should become one. Check pausing, seeking, two folders, removing/re-adding folders, restarting the app mid-course, and inspecting completion dates.

An isolated real-VLC integration test is also provided:

```sh
dbus-run-session -- python3 tools/test_live_vlc.py
```

This creates temporary media/history and launches its own headless VLC instances on that private bus. It also checks a 12-second video paused at 10 seconds, resumed to the end, and watched twice. It does not alter your normal VLC preferences or library. It requires session-bus socket access and takes about two minutes. The graphical smoke test is `python3 tools/test_ui.py` from a desktop session.

The development sandbox could run the accounting/adapter tests and generate media, but denied display and D-Bus socket access. Live VLC and visual Wayland/X11 verification therefore need to run outside that sandbox; they have not been claimed as passed.

## Design references

- [MPRIS playback properties and seek events](https://specifications.freedesktop.org/mpris/latest/Player_Interface.html)
- [VLC's D-Bus implementation](https://github.com/videolan/vlc/blob/master/modules/control/dbus/dbus.c)
- [GTK's native Wayland backend](https://docs.gtk.org/gtk4/wayland.html)
- [PyGObject installation](https://gnome.pages.gitlab.gnome.org/pygobject/getting_started.html)
- [Arch's VLC D-Bus plugin](https://archlinux.org/packages/extra/x86_64/vlc-plugin-dbus/)
