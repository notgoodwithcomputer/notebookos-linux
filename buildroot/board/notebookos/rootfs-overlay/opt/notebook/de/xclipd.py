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
    _TEXT_INFO = 1
    _IMAGE_INFO = 2

    def __init__(self, Gtk, Gdk, GLib):
        self.Gtk = Gtk
        self.Gdk = Gdk
        self.GLib = GLib
        self.core = ClipboardCore()
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self.text = None
        self.image = None
        self._snapshot_generation = 0
        self._snapshot_pending = {}
        self._serve_generation = 0
        self._snapshot_deadline_id = 0
        self.in_handler = False
        self.clipboard.connect("owner-change", self._owner_change)
        # Connecting does not synthesize an event for an owner that predates
        # this daemon (for example after xclipd restarts). Capture it now.
        self._snapshot()

    def _owner_change(self, _clipboard, event):
        if self.in_handler:
            return
        self.in_handler = True
        try:
            owner = getattr(event, "owner", None)
            if owner is None:
                action = self.core.event("owner-vanished")
                generation = self._snapshot_generation
                if generation in self._snapshot_pending:
                    # Replies already queued by the vanished owner can still
                    # arrive through X. Give that newest copy a short grace
                    # instead of invalidating it and immediately serving the
                    # previous owner's stale snapshot.
                    self._serve_generation = generation
                    self._cancel_snapshot_deadline()
                    self._snapshot_deadline_id = self.GLib.timeout_add(
                        120, self._snapshot_deadline, generation)
                elif action == ASSERT_OWNERSHIP:
                    self._serve()
            else:
                # set_with_data emits an asynchronous ownership echo. It is
                # safe to snapshot our own content; consuming the "next"
                # owner event is not safe because an external app may claim
                # CLIPBOARD before that echo is delivered. Always snapshot the
                # current non-null owner so the newest real copy cannot vanish.
                if self.core.self_claim_pending:
                    self.core.event("self-owner-change")
                action = self.core.event("owner-appeared")
                self._serve_generation = 0
                self._cancel_snapshot_deadline()
                if action == TAKE_SNAPSHOT:
                    self._snapshot()
        finally:
            self.in_handler = False

    def _snapshot(self):
        # Selection owners are other processes and may never answer. Never
        # block GTK's only event loop waiting for one: collect both formats
        # asynchronously and generation-check their callbacks so a late owner
        # cannot overwrite a newer copy.
        self._snapshot_generation += 1
        generation = self._snapshot_generation
        self._snapshot_pending.clear()
        self._snapshot_pending[generation] = {"text": ..., "image": ...}
        # Keep the last complete snapshot valid until both parts of this one
        # arrive. A short-lived or hung new owner must not erase good history.
        self.clipboard.request_text(self._snapshot_text_ready, generation)
        if hasattr(self.clipboard, "request_contents"):
            target = self.Gdk.atom_intern("image/png", False)
            self.clipboard.request_contents(
                target, self._snapshot_image_contents_ready, generation)
        else:  # display-free legacy fakes; real GTK always has request_contents
            self.clipboard.request_image(self._snapshot_image_ready, generation)

    def _snapshot_text_ready(self, _clipboard, text, generation):
        pending = self._snapshot_pending.get(generation)
        if pending is None:
            return
        pending["text"] = cap_text(text)
        self._finish_snapshot(generation)

    def _snapshot_image_ready(self, _clipboard, image, generation):
        pending = self._snapshot_pending.get(generation)
        if pending is None:
            return
        if image is not None:
            channels = max(4, image.get_n_channels())
            if not image_fits(image.get_width(), image.get_height(), channels):
                image = None
        pending["image"] = image
        self._finish_snapshot(generation)

    def _snapshot_image_contents_ready(self, _clipboard, selection, generation):
        pending = self._snapshot_pending.get(generation)
        if pending is None:
            return
        try:
            data = bytes(selection.get_data() or b"")
        except Exception:
            data = b""
        pending["image"] = self._decode_bounded_image(data)
        self._finish_snapshot(generation)

    @staticmethod
    def _decode_bounded_image(data):
        """Decode an offered PNG without allocating an oversized pixbuf."""
        if not data or len(data) > IMAGE_CAP:
            return None
        try:
            import gi
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf
            loader = GdkPixbuf.PixbufLoader.new_with_type("png")
            oversized = [False]

            def prepared(obj, width, height):
                if not image_fits(width, height, 4):
                    oversized[0] = True
                    # size-prepared fires before the pixel buffer is allocated.
                    # Force a harmless allocation, then discard the result.
                    obj.set_size(1, 1)

            loader.connect("size-prepared", prepared)
            loader.write(data)
            loader.close()
            return None if oversized[0] else loader.get_pixbuf()
        except Exception:
            return None

    def _finish_snapshot(self, generation):
        pending = self._snapshot_pending.get(generation)
        if (pending is None or generation != self._snapshot_generation
                or pending["text"] is ... or pending["image"] is ...):
            return
        self._snapshot_pending.pop(generation, None)
        new_text, new_image = pending["text"], pending["image"]
        # Owners may offer only HTML or another unsupported custom target. An
        # empty result must not erase the last complete text/image snapshot;
        # that snapshot is the fallback the grace period promises to serve.
        if new_text is not None or new_image is not None:
            self.text, self.image = new_text, new_image
        self.core.event("snapshot-taken",
                        self.text is not None or self.image is not None)
        if generation == getattr(self, "_serve_generation", 0):
            self._serve_generation = 0
            self._cancel_snapshot_deadline()
            if self.text is not None or self.image is not None:
                self._serve()

    def _cancel_snapshot_deadline(self):
        source_id = getattr(self, "_snapshot_deadline_id", 0)
        self._snapshot_deadline_id = 0
        if source_id:
            try:
                self.GLib.source_remove(source_id)
            except Exception:
                pass

    def _snapshot_deadline(self, generation):
        if generation != self._serve_generation:
            return False
        self._snapshot_deadline_id = 0
        self._serve_generation = 0
        pending = self._snapshot_pending.pop(generation, None)
        self._snapshot_generation += 1
        if pending is not None:
            # One selection target can reply while another owner request hangs.
            # Keep every format that did arrive rather than throwing the whole
            # new copy away and serving the previous owner's stale contents.
            completed = False
            if pending.get("text", ...) is not ...:
                self.text = pending["text"]
                completed = True
            if pending.get("image", ...) is not ...:
                self.image = pending["image"]
                completed = True
            if completed:
                # A snapshot generation is one clipboard item. Never combine
                # a newly arrived format with an unresolved format cached from
                # the previous owner (new image + old secret text, or vice
                # versa). Retain the previous complete snapshot only when the
                # new owner supplied no callback at all before the deadline.
                if pending.get("text", ...) is ...:
                    self.text = None
                if pending.get("image", ...) is ...:
                    self.image = None
                self.core.event("snapshot-taken",
                                self.text is not None or self.image is not None)
        if self.text is not None or self.image is not None:
            self._serve()
        return False

    def _serve(self):
        # Claim ownership ONCE with the union of targets. set_text followed by
        # set_image makes two separate ownership claims, so the second silently
        # discards the first format.
        targets = []
        if self.text is not None:
            for name in ("UTF8_STRING", "TEXT", "STRING",
                         "text/plain;charset=utf-8"):
                targets.append(self.Gtk.TargetEntry.new(
                    name, 0, self._TEXT_INFO))
        if self.image is not None:
            for name in ("image/png", "image/bmp", "image/jpeg"):
                targets.append(self.Gtk.TargetEntry.new(
                    name, 0, self._IMAGE_INFO))
        if not targets:
            return
        # Mark the ownership echo before making the X claim. This also covers
        # the first-ever snapshot, where owner-vanished could not set the flag
        # because no older snapshot existed yet.
        self.core.self_claim_pending = True
        try:
            claimed = self.clipboard.set_with_data(
                targets, self._serve_get, self._serve_clear, None)
            if claimed is False:
                self.core.self_claim_pending = False
                return False
            self.clipboard.set_can_store(targets)
            self.clipboard.store()
            return True
        except Exception:
            # A failed X claim must not poison classification of the next
            # external owner-change event as our own asynchronous echo.
            self.core.self_claim_pending = False
            return False

    def _serve_get(self, _clipboard, selection, info, _data):
        if info == self._TEXT_INFO and self.text is not None:
            selection.set_text(self.text, -1)
        elif info == self._IMAGE_INFO and self.image is not None:
            selection.set_pixbuf(self.image)

    @staticmethod
    def _serve_clear(_clipboard, _data):
        pass


def main():
    lock_fd = acquire_instance_lock()
    if lock_fd is None:
        return
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk, GLib  # noqa: E402

    if not Gtk.init_check()[0]:
        os.close(lock_fd)
        return
    ClipboardDaemon(Gtk, Gdk, GLib)
    signal.signal(signal.SIGTERM, lambda *_args: Gtk.main_quit())
    try:
        Gtk.main()
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    main()
