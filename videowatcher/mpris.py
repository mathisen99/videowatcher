"""VLC discovery and asynchronous MPRIS sampling on the GLib main loop."""

import time

from gi.repository import Gio, GLib

from .core import Sample, local_path

PLAYER = "org.mpris.MediaPlayer2.Player"
OBJECT = "/org/mpris/MediaPlayer2"
PREFIX = "org.mpris.MediaPlayer2.vlc"
POLL_INTERVAL_MS = 250


def is_vlc(name):
    return name == PREFIX or name.startswith(PREFIX + ".")


class Monitor:
    def __init__(self, tracker, changed=lambda: None, status=lambda text: None):
        self.tracker, self.changed, self.status = tracker, changed, status
        self.bus = None
        self.players = {}  # name -> unique owner, guards stale asynchronous replies
        self.pending = set()
        self.epochs = {}
        self.subscriptions = []
        self.closed = False
        self.timer = GLib.timeout_add(POLL_INTERVAL_MS, self.poll)
        self.connect()

    def connect(self):
        Gio.bus_get(Gio.BusType.SESSION, None, self.connected)

    def connected(self, source, result):
        if self.closed:
            return
        try:
            self.bus = Gio.bus_get_finish(result)
        except GLib.Error:
            self.status(
                "Cannot connect to the desktop session bus. Restart Video Watcher in your desktop session."
            )
            return
        self.subscriptions.append(
            self.bus.signal_subscribe(
                "org.freedesktop.DBus",
                "org.freedesktop.DBus",
                "NameOwnerChanged",
                "/org/freedesktop/DBus",
                None,
                Gio.DBusSignalFlags.NONE,
                self.owner_changed,
            )
        )
        self.subscriptions.append(
            self.bus.signal_subscribe(
                None, PLAYER, "Seeked", OBJECT, None, Gio.DBusSignalFlags.NONE, self.seeked
            )
        )
        self.subscriptions.append(
            self.bus.signal_subscribe(
                None,
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                OBJECT,
                PLAYER,
                Gio.DBusSignalFlags.NONE,
                self.properties_changed,
            )
        )
        self.call(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListNames",
            None,
            self.listed,
        )

    def call(self, name, path, interface, method, params, callback):
        self.bus.call(
            name,
            path,
            interface,
            method,
            params,
            None,
            Gio.DBusCallFlags.NONE,
            1500,
            None,
            callback,
        )

    def listed(self, bus, result):
        try:
            names = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            self.status("Could not discover VLC. Reopen Video Watcher to retry.")
            return
        for name in names:
            if is_vlc(name):
                self.call(
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus",
                    "GetNameOwner",
                    GLib.Variant("(s)", (name,)),
                    lambda bus, result, name=name: self.resolved(bus, result, name),
                )
        self.update_status()

    def resolved(self, bus, result, name):
        try:
            self.players[name] = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            return
        self.update_status()
        self.poll()

    def owner_changed(self, bus, sender, path, interface, signal, params):
        name, _old, new = params.unpack()
        if not is_vlc(name):
            return
        self.tracker.disconnect(name)
        self.epochs[name] = self.epochs.get(name, 0) + 1
        if new:
            self.players[name] = new
        else:
            self.players.pop(name, None)
        self.update_status()
        self.poll()

    def seeked(self, bus, sender, path, interface, signal, params):
        for name, owner in list(self.players.items()):
            if owner == sender:
                position = params.unpack()[0] / 1_000_000
                session = self.tracker.sessions.get(name)
                if session and session.done and position < 3:
                    self.tracker.disconnect(name)
                else:
                    self.tracker.discontinuity(name)
                self.epochs[name] = self.epochs.get(name, 0) + 1

    def properties_changed(self, bus, sender, path, interface, signal, params):
        _, properties, invalidated = params.unpack()
        # A pause still has a final position to read. Keep the preceding sample
        # so its last fraction of playback is accounted for; never bridge seeks.
        if {"PlaybackStatus", "Metadata", "Rate"}.intersection(set(properties) | set(invalidated)):
            for name, owner in list(self.players.items()):
                if owner == sender:
                    if properties.get("PlaybackStatus") == "Stopped":
                        self.tracker.disconnect(name)
                    elif "Rate" in properties or invalidated:
                        self.tracker.discontinuity(name)
                    self.epochs[name] = self.epochs.get(name, 0) + 1
            self.poll()

    def update_status(self):
        if self.players:
            self.status("VLC connected · watching for videos in your folders")
        else:
            self.status("Ready · open a video in VLC to start tracking")

    def poll(self):
        if self.closed:
            return False
        if self.bus is None:
            return True
        for name, owner in list(self.players.items()):
            if name in self.pending:
                continue
            self.pending.add(name)
            epoch = self.epochs.get(name, 0)
            self.call(
                name,
                OBJECT,
                "org.freedesktop.DBus.Properties",
                "GetAll",
                GLib.Variant("(s)", (PLAYER,)),
                lambda bus, result, name=name, owner=owner, epoch=epoch: self.sampled(
                    bus, result, name, owner, epoch
                ),
            )
        return True

    def sampled(self, bus, result, name, owner, epoch):
        self.pending.discard(name)
        try:
            props = bus.call_finish(result).unpack()[0]
        except GLib.Error:
            self.tracker.discontinuity(name)
            if name in self.players:
                self.status("VLC is not responding · retrying automatically")
            return
        if self.closed or self.players.get(name) != owner or self.epochs.get(name, 0) != epoch:
            return
        metadata = props.get("Metadata", {})
        path = local_path(metadata.get("xesam:url", ""))
        sample = None
        if path and "Position" in props:
            sample = Sample(
                path,
                props["Position"] / 1_000_000,
                metadata.get("mpris:length", 0) / 1_000_000,
                props.get("PlaybackStatus", "Stopped"),
                props.get("Rate", 1.0),
            )
        if self.tracker.sample(name, sample, time.monotonic()):
            self.changed()
        if sample and self.tracker.store.monitored(path):
            verb = "Playing" if sample.status == "Playing" else sample.status
            self.status(f"{verb} · {path.rsplit('/', 1)[-1]}")
        else:
            self.update_status()

    def close(self):
        self.closed = True
        GLib.source_remove(self.timer)
        if self.bus:
            for subscription in self.subscriptions:
                self.bus.signal_unsubscribe(subscription)
