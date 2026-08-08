#!/usr/bin/env python3
"""xclipd — keep copied material alive after its X11 owner disappears.

X11 selections are promises, not storage: the application which owns
CLIPBOARD supplies the bytes when another application asks for them.  Closing
that application therefore used to close the promise too.  This small daemon
remembers the most recent text and image and supplies it only after X reports
that the real owner has gone away.  It never competes with a live application
and deliberately leaves PRIMARY (the select-to-copy clipboard) alone.

The decision core has no GTK dependency so the ownership rules and limits can
be tested on build machines without an X server.
"""
import fcntl
import os
import signal


TEXT_CAP = 1024 * 1024       # 1 MiB UTF-8: ample for ordinary copying.
IMAGE_CAP = 16 * 1024 * 1024  # decoded RGBA-sized pixels, not compressed bytes
LOCK_PATH = "/tmp/notebook-xclipd.lock"

TAKE_SNAPSHOT = "take-snapshot"
ASSERT_OWNERSHIP = "assert-ownership"
IGNORE = "ignore"


def cap_text(text, limit=TEXT_CAP):
    """Return text capped to at most *limit* UTF-8 bytes, on a codepoint edge."""
    if text is None:
        return None
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", "ignore")


def image_fits(width, height, channels=4, limit=IMAGE_CAP):
    """Whether a decoded image is small enough to retain."""
    return (width >= 0 and height >= 0 and channels > 0
            and width * height * channels <= limit)


class ClipboardCore:
    """Display-free ownership state machine used by the GTK shell."""

    def __init__(self):
        self.owner_live = False
        self.have_snapshot = False
        self.self_claim_pending = False

    def event(self, kind, has_content=False):
        if kind == "self-owner-change":
            self.owner_live = True
            self.self_claim_pending = False
            return IGNORE
        if kind == "owner-appeared":
            self.owner_live = True
            return TAKE_SNAPSHOT
        if kind == "snapshot-taken":
            self.have_snapshot = bool(has_content)
            return IGNORE
        if kind == "owner-vanished":
            self.owner_live = False
            if self.have_snapshot:
                self.self_claim_pending = True
                return ASSERT_OWNERSHIP
            return IGNORE
        raise ValueError("unknown clipboard event: %s" % kind)


def acquire_instance_lock(path=LOCK_PATH):
    """Hold and return the machine lock fd, or None when another copy has it."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        try:
            os.close(fd)
        except (OSError, UnboundLocalError):
            pass
        return None


class ClipboardDaemon:
    def __init__(self, Gtk, Gdk):
        self.Gtk = Gtk
        self.Gdk = Gdk
        self.core = ClipboardCore()
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self.text = None
        self.image = None
        self.in_handler = False
        self.clipboard.connect("owner-change", self._owner_change)

    def _owner_change(self, _clipboard, event):
        if self.in_handler:
            return
        self.in_handler = True
        try:
            # set_text/set_image emits an asynchronous echo.  Consume precisely
            # that echo rather than reading back and snapshotting our own data.
            if self.core.self_claim_pending:
                self.core.event("self-owner-change")
                return
            owner = getattr(event, "owner", None)
            if owner is None:
                if self.core.event("owner-vanished") == ASSERT_OWNERSHIP:
                    self._serve()
            elif self.core.event("owner-appeared") == TAKE_SNAPSHOT:
                self._snapshot()
        finally:
            self.in_handler = False

    def _snapshot(self):
        text = cap_text(self.clipboard.wait_for_text())
        image = self.clipboard.wait_for_image()
        if image is not None:
            channels = max(4, image.get_n_channels())
            if not image_fits(image.get_width(), image.get_height(), channels):
                image = None
        self.text, self.image = text, image
        self.core.event("snapshot-taken", text is not None or image is not None)

    def _serve(self):
        # GTK can advertise both values in succession; store asks the X server's
        # clipboard persistence facility to retain all advertised targets too.
        if self.text is not None:
            self.clipboard.set_text(self.text, -1)
        if self.image is not None:
            self.clipboard.set_image(self.image)
        self.clipboard.store()


def main():
    lock_fd = acquire_instance_lock()
    if lock_fd is None:
        return
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk  # noqa: E402

    if not Gtk.init_check()[0]:
        os.close(lock_fd)
        return
    ClipboardDaemon(Gtk, Gdk)
    signal.signal(signal.SIGTERM, lambda *_args: Gtk.main_quit())
    try:
        Gtk.main()
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    main()
