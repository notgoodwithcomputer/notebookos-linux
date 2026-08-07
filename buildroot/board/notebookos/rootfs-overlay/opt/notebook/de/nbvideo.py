"""Real video playback: picture and sound, in a widget the app can pack.

The Video Editor's Play button has been a slideshow. It ran a GLib clock over
the storyboard and, once a second, spawned an ffmpeg process to pull ONE frame —
about 1fps, and silent, on a machine whose whole point is making things
(ROADMAP #29). Export was always real; only playback was pretend.

Everything this needs is already on the image: `libgstplayback.so` (playbin),
`libgstlibav.so` (the decoders), `libgstisomp4.so` and `libgstmatroska.so`
(demuxers), `libgstvideoconvertscale.so`, `libgstgtk.so` (gtksink, which hands
back a real GtkWidget) and `libgstalsa.so`. Nothing new ships for this.

WHY A SEPARATE MODULE. video.py is 4700 lines and its preview stage is a
carefully-built 16:9 box that must not change size when a frame arrives. Keeping
the pipeline out here means the player can be tested on its own — opened,
played, seeked, torn down, against a real file — without constructing the
editor, which is what makes it possible to prove the thing works before wiring
a transport to it.

DEGRADES, NEVER RAISES. Exactly the contract sequencer.AudioOut keeps: if
GStreamer is missing, or gtksink is not built, or the file will not open, then
`available` stays False and every method is a no-op. A build host without the
plugins gets a still preview and a working editor, not a traceback.
"""
import os

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gtk, Gst, GLib, GObject
    Gst.init(None)
    GST_OK = True
except Exception:                                              # noqa: BLE001
    GST_OK = False
    Gtk = Gst = GLib = GObject = None


NS = 1000000000.0          # GStreamer counts in nanoseconds


def _uri(path):
    try:
        return Gst.filename_to_uri(os.path.abspath(path))
    except Exception:                                          # noqa: BLE001
        return "file://" + os.path.abspath(path)


class Playback:
    """One playbin feeding a GTK widget.

    Create it once, pack `widget` into the preview stage, then `open()` a clip
    and `play()`. `on_eos` fires on the main loop when a clip runs out, which is
    what lets a caller step to the next one on the storyboard.
    """

    def __init__(self, on_eos=None, on_error=None):
        self.available = False
        self.failed = False
        self.widget = None
        self._on_eos = on_eos
        self._on_error = on_error
        self._play = None
        self._path = None
        self._rate = 1.0
        self._bus_ids = []

        if not GST_OK:
            self.failed = True
            self.widget = Gtk.Box() if Gtk else None
            return
        try:
            # gtksink over gtkglsink: the target hardware is second-hand
            # laptops, most of which fall to software rendering because the
            # kernel tree carries no AMD or Nouveau source. A GL sink there is
            # slower than the plain one, not faster.
            sink = Gst.ElementFactory.make("gtksink", "vsink")
            play = Gst.ElementFactory.make("playbin", "player")
            if sink is None or play is None:
                raise RuntimeError("playbin/gtksink not built")
            self.widget = sink.props.widget
            play.set_property("video-sink", sink)
            bus = play.get_bus()
            bus.add_signal_watch()
            self._bus_ids = [
                bus.connect("message::eos", self._bus_eos),
                bus.connect("message::error", self._bus_error),
            ]
            self._bus = bus
            self._play = play
            self.available = True
        except Exception:                                      # noqa: BLE001
            self.failed = True
            self._play = None
            if self.widget is None and Gtk is not None:
                self.widget = Gtk.Box()

    # ---- transport -------------------------------------------------------
    def open(self, path, at=0.0, play=False, rate=1.0):
        """Load `path` and park at `at` seconds. True when it is ready."""
        if not self.available or not path or not os.path.isfile(path):
            return False
        try:
            self._play.set_state(Gst.State.NULL)
            self._play.set_property("uri", _uri(path))
            self._path = path
            # PAUSED first, and WAIT for it. A seek issued before the pipeline
            # has pre-rolled is silently dropped, which is how a trimmed clip
            # ends up playing from its start: the trim-in looks applied because
            # the call returned True.
            self._play.set_state(Gst.State.PAUSED)
            self._play.get_state(3 * Gst.SECOND)
            self._rate = 1.0
            if at > 0 or abs(float(rate) - 1.0) > 1e-6:
                self.seek(at, rate=rate)
            if play:
                self.play()
            return True
        except Exception:                                      # noqa: BLE001
            return False

    def play(self):
        if not self.available:
            return False
        try:
            self._play.set_state(Gst.State.PLAYING)
            return True
        except Exception:                                      # noqa: BLE001
            return False

    def pause(self):
        if not self.available:
            return False
        try:
            self._play.set_state(Gst.State.PAUSED)
            return True
        except Exception:                                      # noqa: BLE001
            return False

    def stop(self):
        """Back to NULL. Safe to call twice, and safe after teardown."""
        if self._play is None:
            return
        try:
            self._play.set_state(Gst.State.NULL)
        except Exception:                                      # noqa: BLE001
            pass
        self._path = None

    def seek(self, seconds, rate=None):
        """Jump to `seconds`. `rate` sets playback speed and is remembered.

        seek_simple() cannot carry a rate, so a clip with a speed on it has to
        go through the full seek. That matters: the editor stores a per-clip
        speed and EXPORT honours it, so a preview that always ran at 1x would
        show something the finished file will not do."""
        if not self.available:
            return False
        if rate is not None:
            try:
                self._rate = float(rate) or 1.0
            except Exception:                                  # noqa: BLE001
                self._rate = 1.0
        pos = max(0, int(seconds * NS))
        flags = Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT
        try:
            if abs(self._rate - 1.0) < 1e-6:
                return bool(self._play.seek_simple(Gst.Format.TIME, flags, pos))
            return bool(self._play.seek(
                self._rate, Gst.Format.TIME, flags,
                Gst.SeekType.SET, pos, Gst.SeekType.NONE, 0))
        except Exception:                                      # noqa: BLE001
            return False

    # ---- where are we ----------------------------------------------------
    def position(self):
        """Seconds into the open clip, or 0.0 when that cannot be answered."""
        if not self.available:
            return 0.0
        try:
            ok, pos = self._play.query_position(Gst.Format.TIME)
            return (pos / NS) if ok and pos >= 0 else 0.0
        except Exception:                                      # noqa: BLE001
            return 0.0

    def duration(self):
        if not self.available:
            return 0.0
        try:
            ok, dur = self._play.query_duration(Gst.Format.TIME)
            return (dur / NS) if ok and dur > 0 else 0.0
        except Exception:                                      # noqa: BLE001
            return 0.0

    def has_video(self):
        """True when the open file actually carries a picture. An audio-only
        clip on the storyboard is legal and must not be reported as broken."""
        if not self.available or self._play is None:
            return False
        try:
            return int(self._play.get_property("n-video")) > 0
        except Exception:                                      # noqa: BLE001
            return False

    # ---- bus -------------------------------------------------------------
    def _bus_eos(self, _bus, _msg):
        if self._on_eos:
            try:
                self._on_eos()
            except Exception:                                  # noqa: BLE001
                pass

    def _bus_error(self, _bus, msg):
        # The GError's own text is developer English and never translated, so
        # the caller gets the domain to choose a sentence from, exactly as
        # music._play_failure does.
        domain = ""
        try:
            err, _dbg = msg.parse_error()
            domain = getattr(err, "domain", "") or ""
        except Exception:                                      # noqa: BLE001
            pass
        self.stop()
        if self._on_error:
            try:
                self._on_error(domain)
            except Exception:                                  # noqa: BLE001
                pass

    def teardown(self):
        """Release everything. The bus watch is dropped FIRST: a message still
        queued on the main loop would otherwise reach a handler whose window is
        already gone, which is the shape of half the lifecycle bugs in this
        tree."""
        # Guarded on _bus_ids, not just wrapped in a try: a second teardown
        # calling remove_signal_watch() on a bus that no longer has one prints
        # a GStreamer-CRITICAL. It is harmless in itself, and that is the
        # problem — noise on stderr is how a real CRITICAL goes unread.
        if self._bus_ids:
            try:
                for cid in self._bus_ids:
                    self._bus.disconnect(cid)
                self._bus.remove_signal_watch()
            except Exception:                                  # noqa: BLE001
                pass
            self._bus_ids = []
        self._on_eos = self._on_error = None
        self.stop()
        self._play = None
        self.available = False
