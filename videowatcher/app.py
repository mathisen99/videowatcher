"""Small native GTK 4 / libadwaita interface; no web runtime."""

import csv
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from .core import Store, Tracker, coverage, inside, scan_folders
from .mpris import Monitor
from .statistics import (
    HISTORY_HEADERS,
    SUMMARY_HEADERS,
    csv_safe,
    history_export,
    summarize,
    summary_export,
)

UI_ASSETS = Path(__file__).resolve().parent / "ui"


def art(filename, size):
    paintable = Gtk.IconPaintable.new_for_file(
        Gio.File.new_for_path(str(UI_ASSETS / filename)), size, 1
    )
    widget = Gtk.Image.new_from_paintable(paintable)
    widget.set_pixel_size(size)
    return widget


def date_text(value):
    return (
        datetime.fromisoformat(value).astimezone().strftime("%d %b %Y, %H:%M:%S") if value else "—"
    )


def time_text(seconds, precise=False):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if precise:
        return (f"{minutes // 60}h " if minutes >= 60 else "") + f"{minutes % 60}m {seconds % 60}s"
    return f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"


def facts(items):
    grid = Gtk.Grid(column_spacing=24, row_spacing=10)
    grid.add_css_class("facts-grid")
    for index, (name, value) in enumerate(items):
        grid.attach(label(name, "dim-label", xalign=0, valign=Gtk.Align.START), 0, index, 1, 1)
        grid.attach(
            label(str(value), xalign=0, selectable=True, wrap=True, hexpand=True), 1, index, 1, 1
        )
    return grid


def label(text, css=None, **kwargs):
    widget = Gtk.Label(label=text, **kwargs)
    if css:
        widget.add_css_class(css)
    return widget


def button(text, callback, css=None):
    widget = Gtk.Button(label=text)
    widget.connect("clicked", lambda _: callback())
    if css:
        widget.add_css_class(css)
    return widget


def margins(widget, size):
    for side in ("top", "bottom", "start", "end"):
        getattr(widget, "set_margin_" + side)(size)
    return widget


class Application(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="io.github.mathisen99.VideoWatcher",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.add_main_option(
            "background",
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Track without opening the window",
            None,
        )
        self.background = False
        self.window = None

    def do_handle_local_options(self, options):
        self.background = options.contains("background")
        return -1

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        data = Path(
            os.environ.get(
                "VIDEOWATCHER_DATA_DIR",
                str(
                    Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
                    / "videowatcher"
                ),
            )
        )
        self.store = Store(data / "history.sqlite3")
        self.tracker = Tracker(self.store)
        self.hold()
        self.window = Window(self)
        self.monitor = Monitor(self.tracker, self.window.schedule_reload, self.window.set_status)
        self.window.refresh()

    def do_activate(self):
        if not self.background or not self.store.folders():
            self.window.present()
        self.background = False

    def do_shutdown(self):
        if hasattr(self, "monitor"):
            self.monitor.close()
        if self.window:
            self.window.cleanup()
        if hasattr(self, "store"):
            self.store.db.close()
        Adw.Application.do_shutdown(self)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(
            application=app, title="Video Watcher", default_width=1180, default_height=780
        )
        self.app, self.store = app, app.store
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="folder-scan")
        self.scanning = False
        self.scan_again = False
        self.closed = False
        self.monitors = []
        self.reload_source = 0
        self.scan_source = 0
        self.selected_folder = None
        self.rows = {}
        self.rendered_rows = {}
        self.dialogs = set()
        self.add_css_class("night-watch")
        self.connect("close-request", self.close_window)

        css = Gtk.CssProvider()
        css.load_from_path(str(UI_ASSETS / "midnight.css"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.actions = {}
        for name, callback in (
            ("add-folder", self.choose_folder),
            ("folders", self.manage_folders),
            ("refresh", self.refresh),
            ("statistics", self.show_statistics),
            ("export", self.export),
            ("settings", self.settings),
            ("background", self.hide_window),
            ("quit", self.app.quit),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _parameter, callback=callback: callback())
            self.add_action(action)
            self.actions[name] = action
        app.set_accels_for_action("win.add-folder", ["<Primary>o"])
        app.set_accels_for_action("win.refresh", ["F5"])
        app.set_accels_for_action("win.settings", ["<Primary>comma"])
        app.set_accels_for_action("win.quit", ["<Primary>q"])

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(outer)
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Box())
        brand = Gtk.Box(spacing=10)
        brand.append(art("cat-mark.svg", 28))
        brand.append(label("Video Watcher", "brand-name"))
        header.pack_start(brand)
        menu = Gio.Menu()
        for entries in (
            (
                ("Add Folder…", "add-folder"),
                ("Manage Folders…", "folders"),
                ("Refresh Library", "refresh"),
            ),
            (("Statistics", "statistics"), ("Export CSV…", "export")),
            (("Settings…", "settings"), ("Hide and Keep Tracking", "background")),
        ):
            section = Gio.Menu()
            for title, name in entries:
                section.append(title, "win." + name)
            menu.append_section(None, section)
        self.menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu"
        )
        header.pack_end(self.menu_button)
        outer.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        outer.append(body)
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, width_request=210)
        sidebar.add_css_class("sidebar")
        side_heading = label("YOUR COURSES", "eyebrow", xalign=0)
        side_heading.add_css_class("sidebar-heading")
        sidebar.append(side_heading)
        self.folder_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.folder_list.add_css_class("navigation-sidebar")
        self.folder_list.connect("row-selected", self.folder_selected)
        folder_scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        folder_scroll.set_child(self.folder_list)
        sidebar.append(folder_scroll)
        companion = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        companion.add_css_class("sidebar-note")
        companion.append(label("NINE LIVES.", "eyebrow", xalign=0))
        companion.append(label("One course at a time.", xalign=0))
        companion.append(
            label(
                "Your quiet study buddy.\nEverything stays on this device.",
                "dim-label",
                xalign=0,
                wrap=True,
            )
        )
        sidebar.append(companion)
        body.append(sidebar)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, hexpand=True)
        content.add_css_class("main-content")
        body.append(content)
        hero = Gtk.Box(spacing=20)
        introduction = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=7, hexpand=True, valign=Gtk.Align.CENTER
        )
        eyebrow = label("THE BLACK CAT EDITION", "eyebrow", xalign=0)
        eyebrow.add_css_class("amber")
        introduction.append(eyebrow)
        title_box = Gtk.Box(spacing=12)
        self.heading = label(
            "Your library", "hero-title", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END
        )
        title_box.append(self.heading)
        insights = button("Statistics", self.show_statistics, "quiet-button")
        insights.set_valign(Gtk.Align.CENTER)
        title_box.append(insights)
        introduction.append(title_box)
        self.subtitle = label(
            "Your courses. Your pace. Your place, remembered.", "hero-subtitle", xalign=0, wrap=True
        )
        introduction.append(self.subtitle)
        hero.append(introduction)
        hero.append(art("midnight-cat.svg", 142))
        content.append(hero)

        stats = Gtk.Box(spacing=12, homogeneous=True)
        self.stats, self.stat_hints = [], []
        for title, icon in (
            ("VIDEOS", "video-x-generic-symbolic"),
            ("COMPLETED", "emblem-ok-symbolic"),
            ("REWATCHES", "media-playlist-repeat-symbolic"),
            ("TIME WATCHED", "document-open-recent-symbolic"),
        ):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            card.add_css_class("summary")
            caption = Gtk.Box(spacing=8)
            caption.append(Gtk.Image.new_from_icon_name(icon))
            caption.append(label(title, "stat-label", xalign=0))
            card.append(caption)
            value = label("0", "stat-number", xalign=0)
            self.stats.append(value)
            card.append(value)
            hint = label("", "stat-hint", xalign=0)
            self.stat_hints.append(hint)
            card.append(hint)
            stats.append(card)
        content.append(stats)

        progress_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        progress_card.add_css_class("course-card")
        progress_header = Gtk.Box()
        progress_header.append(label("COURSE PROGRESS", "eyebrow", xalign=0, hexpand=True))
        self.progress_percent = label("0%", "progress-percent")
        progress_header.append(self.progress_percent)
        progress_card.append(progress_header)
        self.course_progress = Gtk.ProgressBar()
        progress_card.append(self.course_progress)
        self.progress_detail = label("", "course-detail", xalign=0)
        progress_card.append(self.progress_detail)
        content.append(progress_card)

        controls = Gtk.Box(spacing=10)
        self.search = Gtk.SearchEntry(hexpand=True, placeholder_text="Find a lesson…")
        self.search.connect("search-changed", lambda _: self.render_library())
        controls.append(self.search)
        self.filter = Gtk.DropDown.new_from_strings(
            ["All videos", "Not started", "In progress", "Completed"]
        )
        self.filter.connect("notify::selected", lambda *_: self.render_library())
        self.filter.set_tooltip_text("Filter by progress")
        controls.append(self.filter)
        self.sort = Gtk.DropDown.new_from_strings(["Name", "Recently watched", "Watch time"])
        self.sort.set_tooltip_text("Sort videos")
        self.sort.connect("notify::selected", lambda *_: self.render_library())
        controls.append(self.sort)
        content.append(controls)

        self.stack = Gtk.Stack(vexpand=True)
        content.append(self.stack)
        self.empty = Adw.StatusPage(
            title="A fresh chapter awaits",
            description="Choose a course folder, then watch in VLC.\nI'll keep your place.",
        )
        self.empty.set_paintable(
            Gtk.IconPaintable.new_for_file(
                Gio.File.new_for_path(str(UI_ASSETS / "cat-mark.svg")), 96, 1
            )
        )
        self.empty.set_child(button("Choose a folder", self.choose_folder, "suggested-action"))
        self.stack.add_named(self.empty, "empty")
        self.model = Gtk.StringList.new([])
        self.selection = Gtk.NoSelection(model=self.model)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.setup_row)
        factory.connect("bind", self.bind_row)
        self.list = Gtk.ListView(model=self.selection, factory=factory, single_click_activate=True)
        self.list.add_css_class("library-list")
        self.list.connect("activate", lambda _, index: self.details(self.model.get_string(index)))
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_child(self.list)
        self.stack.add_named(scrolled, "videos")
        self.count_label = label("", "library-caption", xalign=0)
        content.append(self.count_label)

        status_bar = Gtk.Box(spacing=8)
        status_bar.add_css_class("status-bar")
        self.status_dot = Gtk.Image.new_from_icon_name("media-record-symbolic")
        self.status_dot.set_pixel_size(10)
        self.status_dot.add_css_class("status-dot")
        status_bar.append(self.status_dot)
        self.status = label(
            "Connecting to VLC…",
            "status-line",
            xalign=0,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END,
        )
        status_bar.append(self.status)
        status_bar.append(label("LOCAL HISTORY  ·  NO CLOUD", "status-note"))
        outer.append(status_bar)
        self.rebuild_folders()
        self.reload()

    def close_window(self, *_):
        self.app.quit()
        return False

    def hide_window(self, *_):
        """Background mode is explicit; the window close button always quits."""
        self.set_visible(False)

    def set_status(self, text):
        self.status.set_label(text)
        self.status.set_tooltip_text(text)
        if text.startswith("Playing"):
            self.status_dot.add_css_class("playing")
        else:
            self.status_dot.remove_css_class("playing")

    def schedule_reload(self):
        if not self.reload_source:
            self.reload_source = GLib.timeout_add(750, self.reload)

    def reload(self):
        self.reload_source = 0
        self.rows = {r["path"]: r for r in self.store.library()}
        self.render_library()
        return False

    def rebuild_folders(self):
        while (child := self.folder_list.get_first_child()) is not None:
            self.folder_list.remove(child)
        selected = None
        for folder in [None] + self.store.folders():
            row = Gtk.ListBoxRow()
            row.folder = folder
            box = Gtk.Box(spacing=10)
            box.append(
                Gtk.Image.new_from_icon_name(
                    "folder-videos-symbolic" if folder else "view-grid-symbolic"
                )
            )
            box.append(
                label(
                    Path(folder).name or folder if folder else "All videos",
                    xalign=0,
                    ellipsize=Pango.EllipsizeMode.END,
                    max_width_chars=20,
                )
            )
            row.set_child(margins(box, 9))
            row.set_tooltip_text(folder or "Every monitored folder")
            self.folder_list.append(row)
            if folder == self.selected_folder:
                selected = row
        self.folder_list.select_row(selected or self.folder_list.get_row_at_index(0))

    def folder_selected(self, _, row):
        if row:
            self.selected_folder = row.folder
            self.heading.set_label(Path(row.folder).name if row.folder else "Your library")
            self.heading.set_tooltip_text(row.folder or "All monitored videos")
            self.subtitle.set_label(
                "Every lesson is a little progress."
                if row.folder
                else "Your courses. Your pace. Your place, remembered."
            )
            self.render_library()

    def scope_rows(self):
        return [
            row
            for row in self.rows.values()
            if not self.selected_folder or inside(row["path"], self.selected_folder)
        ]

    def render_library(self):
        rows = sorted(
            self.scope_rows(),
            key=lambda row: [
                int(part) if part.isdigit() else part.casefold()
                for part in re.split(r"(\d+)", row["path"])
            ],
        )
        summary = summarize(rows)
        values = [
            str(summary["videos"]),
            str(summary["completed"]),
            str(summary["rewatches"]),
            time_text(summary["seconds"]),
        ]
        for widget, value in zip(self.stats, values):
            widget.set_label(value)
        hints = [
            "Includes subfolders"
            if self.selected_folder
            else f"In {len(self.store.folders())} course folders",
            f"{summary['remaining']} left to complete",
            "Additional full watches",
            f"{summary['sessions']} watch sessions",
        ]
        for widget, hint in zip(self.stat_hints, hints):
            widget.set_label(hint)
        self.course_progress.set_fraction(summary["fraction"])
        self.progress_percent.set_label(f"{summary['fraction']:.0%}")
        self.progress_detail.set_label(
            f"{summary['completed']} of {summary['videos']} completed"
            f" · {summary['in_progress']} in progress · {summary['not_started']} not started"
        )
        query = self.search.get_text().casefold()
        mode = self.filter.get_selected()
        filtered = [
            r
            for r in rows
            if query in r["path"].casefold()
            and (
                mode == 0
                or mode == 1
                and r["plays"] == 0
                or mode == 2
                and r["plays"] > 0
                and r["completed_count"] == 0
                or mode == 3
                and r["completed_count"] > 0
            )
        ]
        if self.sort.get_selected() == 1:
            filtered.sort(key=lambda row: row["last_seen"] or "", reverse=True)
        elif self.sort.get_selected() == 2:
            filtered.sort(key=lambda row: row["seconds"], reverse=True)
        # Gtk.ListView virtualizes rows, including very large course libraries.
        paths = [r["path"] for r in filtered]
        old_paths = [self.model.get_string(i) for i in range(self.model.get_n_items())]
        if paths != old_paths:
            self.model.splice(0, self.model.get_n_items(), paths)
        else:
            # Refresh only changed rows, preserving the scroll position during playback.
            for index, row in enumerate(filtered):
                if row != self.rendered_rows.get(row["path"]):
                    self.model.splice(index, 1, [row["path"]])
        self.rendered_rows = {r["path"]: dict(r) for r in filtered}
        if not self.store.folders():
            self.empty.set_title("A fresh chapter awaits")
            self.empty.set_description(
                "Choose a course folder, then watch in VLC.\nI'll keep your place."
            )
        elif not rows:
            self.empty.set_title("No videos here yet")
            self.empty.set_description(
                "Add videos to this folder or choose another one.\nIf this is a media mount, connect it and press Refresh."
            )
        elif not filtered:
            self.empty.set_title("No matching videos")
            self.empty.set_description("Try another search or filter.")
        self.stack.set_visible_child_name("videos" if filtered else "empty")
        self.count_label.set_label(f"{len(filtered)} videos shown · Select a video for its history")

    def setup_row(self, factory, item):
        box = Gtk.Box(spacing=16)
        box.add_css_class("video-row")
        tile = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        tile.add_css_class("video-tile")
        play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        play_icon.set_hexpand(True)
        tile.append(play_icon)
        box.append(tile)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        title = label("", "video-title", xalign=0, ellipsize=Pango.EllipsizeMode.END)
        location = label("", "video-location", xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        names.append(title)
        names.append(location)
        box.append(names)
        progress_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=7,
            width_request=125,
            valign=Gtk.Align.CENTER,
        )
        state = label("", "video-state", xalign=0)
        progress = Gtk.ProgressBar()
        progress_box.append(state)
        progress_box.append(progress)
        box.append(progress_box)
        details = label("", "video-history", xalign=1, width_chars=21)
        box.append(details)
        arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
        arrow.add_css_class("row-arrow")
        box.append(arrow)
        item.widgets = (title, location, state, progress, details)
        item.set_child(box)

    def bind_row(self, factory, item):
        path = item.get_item().get_string()
        row = self.rows[path]
        title, location, state, progress, details = item.widgets
        title.set_label(Path(path).stem)
        roots = [f for f in self.store.folders() if inside(path, f)]
        root = self.selected_folder or min(roots, key=len, default=str(Path(path).parent))
        relative_parent = Path(path).relative_to(root).parent
        course = str(relative_parent) if str(relative_parent) != "." else Path(root).name
        location.set_label(f"{course}  ·  {Path(path).suffix.lstrip('.').upper()}")
        item.get_child().set_tooltip_text(path)
        fraction = (
            min(1, coverage(json.loads(row["ranges"])) / row["duration"]) if row["duration"] else 0
        )
        for style in ("state-complete", "state-started", "state-new"):
            state.remove_css_class(style)
        if row["completed_count"]:
            state.set_label("Completed" if row["cycle_done"] else f"Rewatching · {fraction:.0%}")
            state.add_css_class("state-complete" if row["cycle_done"] else "state-started")
        elif row["plays"]:
            state.set_label(f"In progress · {fraction:.0%}" if row["duration"] else "In progress")
            state.add_css_class("state-started")
        else:
            state.set_label("Not started")
            state.add_css_class("state-new")
        progress.set_fraction(1.0 if row["cycle_done"] else fraction)
        count = row["completed_count"]
        if row["last_seen"]:
            last = (
                datetime.fromisoformat(row["last_seen"]).astimezone().strftime("%d %b %Y · %H:%M")
            )
            plays, repeats = row["plays"], max(0, count - 1)
            details.set_label(
                f"{last}\n{plays} session{'s' if plays != 1 else ''} · "
                f"{repeats} rewatch{'es' if repeats != 1 else ''}"
            )
        else:
            details.set_label("—\nReady when you are")
        details.set_tooltip_text(
            f"Last watched: {date_text(row['last_seen'])}\n"
            f"{time_text(row['seconds'], precise=True)} watched · {count} completions"
        )

    def choose_folder(self):
        chooser = Gtk.FileChooserNative(
            title="Choose a course folder",
            transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            accept_label="Monitor folder",
            cancel_label="Cancel",
        )

        def response(dialog, result):
            if result == Gtk.ResponseType.ACCEPT:
                file = dialog.get_file()
                try:
                    if not file or not file.get_path():
                        raise ValueError("Choose a local folder or a mounted network folder.")
                    self.store.add_folder(file.get_path())
                    self.rebuild_folders()
                    self.refresh()
                except (OSError, ValueError) as error:
                    self.message("Could not add folder", str(error))
            dialog.destroy()

        chooser.connect("response", response)
        chooser.show()

    def refresh(self):
        if self.closed:
            return
        if self.scanning:
            self.scan_again = True
            return
        self.scanning = True
        self.actions["refresh"].set_enabled(False)
        self.count_label.set_label("Looking for videos…")
        folders = self.store.folders()
        future = self.worker.submit(scan_folders, folders)
        future.add_done_callback(lambda done: GLib.idle_add(self.scan_finished, done, folders))

    def scan_finished(self, future, folders):
        if self.closed:
            return False
        self.scanning = False
        self.actions["refresh"].set_enabled(True)
        try:
            videos, directories, errors = future.result()
        except OSError as error:
            self.message("Could not scan folders", str(error))
            return False
        if folders != self.store.folders():
            self.refresh()
            return False
        self.store.add_videos(videos)
        for monitor in self.monitors:
            monitor.cancel()
        self.monitors = []
        for directory in directories:
            try:
                monitor = Gio.File.new_for_path(directory).monitor_directory(
                    Gio.FileMonitorFlags.NONE, None
                )
                monitor.connect("changed", self.folder_changed)
                self.monitors.append(monitor)
            except GLib.Error:
                errors.append(f"Automatic folder updates unavailable: {directory}. Use Refresh.")
        self.reload()
        if errors:
            self.count_label.set_label(f"{len(errors)} folder warning(s) · {errors[0]}")
            self.count_label.set_tooltip_text("\n".join(errors))
        else:
            self.count_label.set_tooltip_text(None)
        if self.scan_again:
            self.scan_again = False
            self.refresh()
        return False

    def folder_changed(self, *_):
        if not self.scan_source:
            self.scan_source = GLib.timeout_add_seconds(3, self.rescan)

    def rescan(self):
        self.scan_source = 0
        self.refresh()
        return False

    def dialog(self, title, width=600):
        window = Gtk.Window(
            title=title, transient_for=self, modal=True, default_width=width, default_height=250
        )
        window.add_css_class("night-watch")
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(label(title, "heading"))
        outer.append(header)
        scroll = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            max_content_height=650,
            propagate_natural_height=True,
        )
        box = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16), 24)
        scroll.set_child(box)
        outer.append(scroll)
        window.set_child(outer)
        self.dialogs.add(window)
        window.connect("close-request", lambda w: self.dialogs.discard(w) or False)
        window.present()
        return window, box

    def message(self, title, text):
        window, box = self.dialog(title, 480)
        box.append(label(text, wrap=True, xalign=0, selectable=True))
        box.append(button("OK", window.close, "suggested-action"))

    def details(self, path):
        row = self.rows[path]
        _dialog, box = self.dialog("Video history", 680)
        box.append(label(Path(path).stem, "title-2", xalign=0, wrap=True))
        box.append(label(path, "dim-label", xalign=0, wrap=True, selectable=True))
        box.append(
            facts(
                [
                    (
                        "Completions / rewatches",
                        f"{row['completed_count']} / {max(0, row['completed_count'] - 1)}",
                    ),
                    ("Watch sessions", row["plays"]),
                    ("Time actually watched", time_text(row["seconds"], precise=True)),
                    (
                        "Average session",
                        time_text(row["seconds"] / row["plays"], precise=True)
                        if row["plays"]
                        else "—",
                    ),
                    (
                        "Longest session",
                        time_text(row["longest_session"], precise=True) if row["plays"] else "—",
                    ),
                    (
                        "Video length",
                        time_text(row["duration"], precise=True)
                        if row["duration"]
                        else "Known after playback",
                    ),
                    ("First watched", date_text(row["first_seen"])),
                    ("Last watched", date_text(row["last_seen"])),
                    ("First completed", date_text(row["first_completed"])),
                    ("Last completed", date_text(row["last_completed"])),
                ]
            )
        )
        play = button("Open in VLC", lambda: self.play(path), "suggested-action")
        play.set_sensitive(Path(path).is_file())
        box.append(play)
        if not Path(path).is_file():
            box.append(
                label(
                    "File unavailable. Reconnect its drive or restore its original location; your history is kept.",
                    wrap=True,
                    xalign=0,
                )
            )
        box.append(Gtk.Separator())
        box.append(label("Watch history", "title-3", xalign=0))
        box.append(
            label(
                "Local dates and times. Watch time excludes pauses.\nA session is a qualified visit, not necessarily a completed watch.",
                "dim-label",
                wrap=True,
                xalign=0,
            )
        )
        box.append(button("Export watch history", lambda: self.export_history([path], _dialog)))
        history = self.store.history(path)
        if not history:
            box.append(
                label(
                    "No watch history yet. Brief accidental opens are ignored.", wrap=True, xalign=0
                )
            )
        for session in history:
            box.append(self.session_card(session))

    def session_card(self, session, show_video=False):
        card = margins(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5), 4)
        card.add_css_class("session-card")
        if show_video:
            title = label(Path(session["path"]).stem, "heading", xalign=0, wrap=True)
            title.set_tooltip_text(session["path"])
            card.append(title)
        text = (
            f"Started: {date_text(session['started'])}\n"
            f"Last playback: {date_text(session['last_seen'])}\n"
            f"{time_text(session['seconds'], precise=True)} actually watched"
        )
        if session["completed"]:
            text += f" · Completed {date_text(session['completed'])}"
        card.append(label(text, xalign=0, wrap=True, selectable=True))
        return card

    def show_statistics(self):
        self.reload()
        rows = self.scope_rows()
        summary = summarize(rows)
        _dialog, box = self.dialog("Viewing statistics", 700)
        scope = self.selected_folder or "All monitored folders"
        box.append(label(scope, "title-3", xalign=0, wrap=True, selectable=True))
        box.append(
            label(
                f"{summary['completed']} of {summary['videos']} videos completed ({summary['fraction']:.0%})",
                "title-2",
                xalign=0,
                wrap=True,
            )
        )
        box.append(
            facts(
                [
                    ("Videos left to complete", summary["remaining"]),
                    (
                        "In progress / not started",
                        f"{summary['in_progress']} / {summary['not_started']}",
                    ),
                    ("Time actually watched", time_text(summary["seconds"], precise=True)),
                    ("Watch sessions", summary["sessions"]),
                    ("Total completions", summary["completions"]),
                    ("Rewatches", summary["rewatches"]),
                    (
                        "Average session",
                        time_text(summary["average_session"], precise=True)
                        if summary["sessions"]
                        else "—",
                    ),
                    (
                        "Longest session",
                        time_text(summary["longest_session"], precise=True)
                        if summary["sessions"]
                        else "—",
                    ),
                    ("First watched", date_text(summary["first_seen"])),
                    ("Most recent playback", date_text(summary["last_seen"])),
                ]
            )
        )
        box.append(
            label(
                "Totals include all recorded history in this folder, including subfolders.\nRewatches count additional completed passes. Dates and times are local.",
                "dim-label",
                wrap=True,
                xalign=0,
            )
        )
        paths = [row["path"] for row in rows]
        box.append(button("Export watch history", lambda: self.export_history(paths, _dialog)))
        box.append(Gtk.Separator())
        box.append(label("Recent activity · latest 30 sessions", "title-3", xalign=0))
        activity = self.store.activity(paths)
        if not activity:
            box.append(label("No watch sessions yet.", "dim-label", xalign=0))
        for session in activity:
            box.append(self.session_card(session, show_video=True))

    def play(self, path):
        try:
            subprocess.Popen(
                ["vlc", "--", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError as error:
            self.message("Could not open VLC", str(error))

    def manage_folders(self):
        dialog, box = self.dialog("Monitored folders")
        box.append(
            label(
                "Removing a folder stops tracking it. Its videos and saved history are kept; add it again to restore the view.",
                wrap=True,
                xalign=0,
            )
        )
        for folder in self.store.folders():
            row = Gtk.Box(spacing=12)
            row.append(label(folder, xalign=0, wrap=True, hexpand=True))

            def remove(folder=folder, row=row):
                self.store.remove_folder(folder)
                if self.selected_folder == folder:
                    self.selected_folder = None
                box.remove(row)
                self.rebuild_folders()
                self.reload()
                self.refresh()

            row.append(button("Remove", remove))
            box.append(row)
        box.append(button("Add folder", lambda: (dialog.close(), self.choose_folder())))

    def settings(self):
        dialog, box = self.dialog("Tracking settings", 540)
        box.append(label("Ignore accidental opens", "title-3", xalign=0))
        box.append(
            label(
                "Only save a session after this much actual playback. For short videos, the threshold is reduced to half the video’s playing time.",
                wrap=True,
                xalign=0,
            )
        )
        spin = Gtk.SpinButton.new_with_range(5, 120, 5)
        spin.set_value(float(self.store.setting("minimum_seconds", "30")))
        box.append(spin)
        box.append(label("Seconds of playback before a session counts", "dim-label", xalign=0))
        box.append(
            label(
                "Completion means 90% of the unique content watched, possibly across several sessions. Seeking and paused time do not count. A rewatch means another completed pass.",
                wrap=True,
                xalign=0,
            )
        )
        box.append(
            label(
                "Keep Video Watcher running while watching. The window's × stops tracking. To hide the window and keep tracking, choose Hide and Keep Tracking from the menu. Playback from before the app was started cannot be recovered.",
                wrap=True,
                xalign=0,
            )
        )
        box.append(
            label(
                "VLC must have its D-Bus control interface enabled (normally on by default). In VLC: Tools → Preferences → Show settings: All → Interface → Control interfaces → D-Bus control interface. Restart VLC after changing it.",
                wrap=True,
                xalign=0,
            )
        )

        def save():
            self.store.set_setting("minimum_seconds", spin.get_value_as_int())
            dialog.close()

        box.append(button("Save", save, "suggested-action"))

    def export(self):
        self.reload()
        self.save_csv(
            "Export viewing summary",
            "video-watcher.csv",
            SUMMARY_HEADERS,
            list(summary_export(self.scope_rows())),
        )

    def export_history(self, paths, parent):
        self.save_csv(
            "Export watch history",
            "video-watcher-history.csv",
            HISTORY_HEADERS,
            list(history_export(self.store.activity(paths, limit=None))),
            parent,
        )

    def save_csv(self, title, filename, headers, rows, parent=None):
        chooser = Gtk.FileChooserNative(
            title=title,
            transient_for=parent or self,
            action=Gtk.FileChooserAction.SAVE,
            accept_label="Export",
            cancel_label="Cancel",
        )
        chooser.set_current_name(filename)

        def response(dialog, result):
            if result == Gtk.ResponseType.ACCEPT:
                file = dialog.get_file()
                try:
                    if not file or not file.get_path():
                        raise ValueError("Choose a local destination.")
                    with open(file.get_path(), "w", newline="", encoding="utf-8") as output:
                        writer = csv.writer(output)
                        writer.writerow(headers)
                        writer.writerows([csv_safe(value) for value in row] for row in rows)
                except (OSError, ValueError) as error:
                    self.message("Export failed", str(error))
            dialog.destroy()

        chooser.connect("response", response)
        chooser.show()

    def cleanup(self):
        self.closed = True
        for source in (self.reload_source, self.scan_source):
            if source:
                GLib.source_remove(source)
        for monitor in self.monitors:
            monitor.cancel()
        self.worker.shutdown(wait=False, cancel_futures=True)
