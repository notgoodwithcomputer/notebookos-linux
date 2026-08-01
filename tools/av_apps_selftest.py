#!/usr/bin/env python3
"""av_apps_selftest -- the defects found in the media apps, each pinned so it
cannot come back.

    DISPLAY=:0 python3 tools/av_apps_selftest.py

Everything here was a real bug in a shipped-candidate tree, found by driving the
real windows rather than by reading them. In order of what it cost the user:

  1. THE SEQUENCER COULD NOT RECORD ON ORDINARY HARDWARE. The Input menu
     addressed each microphone as "hw:<card>,<device>", which hands the app the
     hardware's own format with no conversion, and a capture PCM essentially
     never offers one channel -- a HyperX USB microphone and an Intel HDA analog
     input both publish "CHANNELS: 2" and nothing else. A take is mono, so
     arecord answered "Channels count non available" and exited at once: the
     take simply never appeared.

  2. MAPS WOULD NOT OPEN AT ALL for a half-copied pack. NBM2's header read is
     struct.unpack, which raises struct.error -- not the ValueError _open_map
     caught -- and the place index is lzma, which raises LZMAError. Both fire
     during window construction, so a truncated file in the Maps folder meant a
     traceback and no window: no way in to remove it or pick another map.

  3. TWO OPEN+CLOSE CYCLES DESTROYED A DAMAGED PROJECT, in the Video Editor and
     the Sequencer, with no user action at all. Both read a store that is valid
     JSON of a shape they do not recognise as "no data", opened blank, and wrote
     that blankness back on close. nbapp's one .bak only survives the second
     open when the blank state weighs LESS than what it replaced, and neither
     app's blank state does. Each now checks the shape and quarantines instead.

  4. THE PREVIEW WENT BLANK on a clip trimmed near the end of its own source.
     The export clamps the in-point inside the source; the preview did not, so
     it asked ffmpeg for a frame past the end, got a zero-byte PNG, and showed
     nothing -- for a clip that would render perfectly.

  5. THE MASTER FADER WAS NOT THE MASTER. The metronome click was voiced at a
     fixed level, and a recorded take was played by aplay, which has no volume:
     pulling Master to zero silenced the synth lanes and left the click and
     every recording at full volume.

  6. OPENING A FOREIGN FILE adopted it. Both apps' File > Open accepted any dict
     with the right KEY, whatever the value held, so a foreign JSON document
     blanked the project, overwrote session recovery, and left the path set so
     the next Save clobbered the user's file.

The damage cases run ONE OPEN PER PROCESS on purpose: nbapp._BACKED_UP is module
state, so cycling inside one process is exactly the lie this file exists to
catch. Everything writes into a throwaway NB_HOME and nothing is ever played.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
MAPS_SRC = os.path.abspath(os.path.join(
    HERE, "..", "buildroot", "board", "notebookos", "rootfs-overlay", "opt",
    "notebook", "maps", "monaco.nbm2"))
sys.path.insert(0, DE)

PASS = []
FAIL = []
MARK = "USERDATA-MARKER-AV7"
CYCLES = 3


def check(ok, name, detail=""):
    (PASS if ok else FAIL).append(name)
    print("%-4s %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  [%s]" % detail) if detail and not ok else ""))
    return ok


def section(t):
    print("\n== %s ==" % t)


# --------------------------------------------------------------- subprocess run
# claim_single_instance() calls os._exit(0) when it finds a live registration in
# the shared /tmp/nb-apps, which would end a worker with no output and status 0
# -- a silent false pass. _APP_DIR is repointed per process.
WORKER = r'''
import os, sys, inspect
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import nbapp
nbapp._APP_DIR = os.path.join(os.environ["NB_HOME"], "nb-apps-%d" % os.getpid())
os.makedirs(nbapp._APP_DIR, exist_ok=True)
mod = __import__(sys.argv[1])
cls = None
for _n, c in inspect.getmembers(mod, inspect.isclass):
    if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
        cls = c
        break


def pump(n=8):
    for _ in range(n):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


g = {"mod": mod, "cls": cls, "pump": pump, "os": os, "sys": sys, "nbapp": nbapp}
exec(compile(sys.argv[2], "<probe>", "exec"), g)
'''


def drive(app, home, body, timeout=180):
    """Run `body` against a freshly built window of `app` in its own process."""
    env = dict(os.environ, NB_HOME=home, PYTHONPATH=DE,
               DISPLAY=os.environ.get("DISPLAY", ":0"))
    return subprocess.run([sys.executable, "-c", WORKER, app, body],
                          capture_output=True, text=True, timeout=timeout,
                          env=env)


OPEN_CLOSE = ("app = cls()\npump()\napp.destroy()\npump()\nprint('RAN')\n")


def survivors(home):
    hits = []
    for root, _d, files in os.walk(home):
        for f in files:
            p = os.path.join(root, f)
            try:
                with open(p, "rb") as fh:
                    if MARK.encode() in fh.read():
                        hits.append(os.path.relpath(p, home))
            except OSError:
                pass
    return sorted(hits)


# ---------------------------------------------------- 1. the store damage cases
GOOD_VIDEO = {
    "version": 2,
    "bin": [{"path": "/x/%s.png" % MARK, "name": "%s.png" % MARK,
             "kind": "image", "dur": 4, "srcdur": 0.0}],
    "clips": [{"media": 0, "kind": "image", "start": 0.0, "duration": 4,
               "title": MARK, "transition": None, "effect": "sepia",
               "volume": 1.0, "mute": False, "afade": False, "vfade": False,
               "kenburns": "in", "speed": 1.0, "cardtext": "", "cardsub": ""}],
    "music": None, "size": [1280, 720]}

GOOD_SEQ = {
    "version": 2, "bpm": 132, "capture_device": None, "metronome": True,
    "length": 60.0, "pitch": 0, "master": 80,
    "tracks": [{"name": MARK, "input": "Synth", "armed": False, "muted": False,
                "solo": False, "gain": 80, "clips": [[0.0, 4.0]]}]
    + [{"name": "Track %d" % i, "input": "Synth", "armed": False,
        "muted": False, "solo": False, "gain": 80, "clips": []}
       for i in range(2, 9)]}


def damage_shapes(good):
    """Plausible reshapes of a healthy store, each still valid JSON."""
    out = [("healthy", good)]
    d = json.loads(json.dumps(good))
    for k, v in list(d.items()):
        if isinstance(v, list):
            d[k] = {str(i): x for i, x in enumerate(v)}
    out.append(("list-as-object", d))
    d = json.loads(json.dumps(good))
    for k, v in list(d.items()):
        if isinstance(v, list) and v:
            d[k] = [json.dumps(x) for x in v]
    out.append(("record-as-string", d))
    out.append(("keys-renamed", {("nb_" + k): v for k, v in good.items()}))
    # a store whose payload weighs LESS than the app's blank default, which is
    # the case nbapp._bak_would_shrink cannot rescue
    out.append(("tiny-wrong-shape", {"note": MARK}))
    out.append(("root-is-list", [good]))
    out.append(("root-is-string", MARK))
    return out


def test_damage(root):
    section("a damaged project survives three open+close cycles")
    for app, cfg, good in (("video", "video.json", GOOD_VIDEO),
                           ("sequencer", "sequencer.json", GOOD_SEQ)):
        for label, payload in damage_shapes(good):
            home = os.path.join(root, "dmg-%s-%s" % (app, label))
            cdir = os.path.join(home, ".config", "notebook")
            os.makedirs(cdir, exist_ok=True)
            with open(os.path.join(cdir, cfg), "w") as fh:
                json.dump(payload, fh)
            lost = None
            for n in range(1, CYCLES + 1):
                r = drive(app, home, OPEN_CLOSE)
                if "RAN" not in r.stdout:
                    tail = (r.stderr or "").strip().splitlines()
                    lost = "did not launch: %s" % (tail[-1][:80] if tail else "?")
                    break
                if not survivors(home):
                    lost = "destroyed by open+close #%d" % n
                    break
            check(lost is None, "%s: %s" % (app, label), lost or "")


# -------------------------------------------------------------- 2. Maps packs
def make_packs(dest):
    raw = open(MAPS_SRC, "rb").read()
    body = bytearray(raw)
    for i in range(len(body) // 2, len(body)):
        body[i] = 0
    return {
        "good": raw,
        "header-truncated": raw[:20],
        "directory-truncated": raw[:80],
        "zero-bytes": b"",
        "not-a-pack": b"hello world" * 20,
        "payload-zeroed": bytes(body),
        "payload-missing": raw[:len(raw) // 4],
    }


MAPS_PROBE = r'''
import cairo
app = cls()
pump()
surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 900, 600)
app.canvas.set_size_request(900, 600)
pump()
app._draw(app.canvas, cairo.Context(surf))
app._search.set_text("Monaco")
app._do_search()
for f in (0.7, 1.4):
    app._zoom(f)
app._fit()
app._on_scroll(app.canvas, type("E", (), {"direction": None, "x": 10.0, "y": 10.0})())
pump()
print("OPENED", bool(app.pack), "EMPTY", bool(app._empty))
app.destroy()
pump()
print("RAN")
'''


def test_maps(root):
    section("Maps opens, draws and searches whatever is in the Maps folder")
    if not os.path.isfile(MAPS_SRC):
        check(False, "the bundled map pack is present", MAPS_SRC)
        return
    for label, blob in make_packs(root).items():
        home = os.path.join(root, "maps-%s" % label)
        os.makedirs(os.path.join(home, "maps"), exist_ok=True)
        with open(os.path.join(home, "maps", "%s.nbm2" % label), "wb") as fh:
            fh.write(blob)
        r = drive("maps", home, MAPS_PROBE)
        ok = "RAN" in r.stdout
        tail = (r.stderr or "").strip().splitlines()
        check(ok, "maps: %s" % label,
              tail[-1][:100] if tail else "no window")


# ------------------------------------------- 3. in-process checks (no window)
def test_capture_devices():
    section("the Sequencer records through a device that accepts a mono take")
    import sequencer
    devs = sequencer.capture_devices()
    if not devs:
        check(True, "no capture hardware on this host, nothing to check")
        return
    raw = [d for d, _l in devs if d.startswith("hw:")]
    check(not raw, "no microphone is addressed as a raw 'hw:' device",
          "raw: %s" % raw)
    check(all(d.startswith("plughw:") or d == "default" for d, _l in devs),
          "every microphone goes through ALSA's conversion layer",
          str([d for d, _l in devs]))
    # and the difference is real on this machine's own hardware
    if shutil.which("arecord"):
        with tempfile.TemporaryDirectory() as td:
            dev = devs[0][0]
            wav = os.path.join(td, "t.wav")
            r = subprocess.run(
                ["arecord", "-D", dev, "-f", sequencer.CAP_FMT,
                 "-r", str(sequencer.CAP_RATE), "-c", "1", "-t", "wav",
                 "-d", "1", wav],
                capture_output=True, text=True, timeout=20)
            got = os.path.getsize(wav) if os.path.exists(wav) else 0
            check(r.returncode == 0 and got > 1024,
                  "...and a one-second take really is captured from %s" % dev,
                  (r.stderr or "").strip().splitlines()[-1][:90]
                  if r.returncode else "%d bytes" % got)


def test_tone_engine_retry():
    section("a refused sound device is retried, then settles")
    import sequencer
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    calls = {"n": 0}
    real = Gst.parse_launch

    def always_fail(*a, **k):
        calls["n"] += 1
        raise RuntimeError("no sink")

    Gst.parse_launch = always_fail
    try:
        e = sequencer.ToneEngine()
        for _ in range(6):
            e.start()
        check(calls["n"] == sequencer.ToneEngine.RETRIES,
              "six presses of Play build at most RETRIES pipelines",
              "built %d" % calls["n"])
        check(e.failed and not e.available,
              "...and the window then reports no sound")

        calls["n"] = 0

        def fail_once(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("busy")
            return real(*a, **k)

        Gst.parse_launch = fail_once
        f = sequencer.ToneEngine()
        first = f.start()
        second = f.start()
        check(not first and second and f.available,
              "a device that was busy once works on the next press",
              "first=%s second=%s" % (first, second))
        f.shutdown()
    finally:
        Gst.parse_launch = real


def test_master_fader():
    section("the Master fader is the master")
    import sequencer

    heard = []

    class Spy:
        available = True
        failed = False

        def note(self, f, d, a, perc=False):
            heard.append(round(a, 4))

        def silence(self):
            pass

        def start(self):
            return True

        def shutdown(self):
            pass

    app = sequencer.Sequencer.__new__(sequencer.Sequencer)
    app.engine = Spy()
    app.metronome = True
    app.transport = "play"
    app.pitch = 0
    app.pos = 0.0
    app.tracks = []
    app._vu_note = {}
    for m, want in ((100, True), (0, False)):
        heard[:] = []
        app.master = m
        app._fire_beat(0, 0.5)
        louder = any(a > 0.0 for a in heard)
        check(louder is want,
              "master=%d %s the metronome click" % (m, "voices" if want
                                                    else "silences"),
              str(heard))

    # and a recorded take answers to the faders
    seen = []
    real_popen = subprocess.Popen

    class FakeProc:
        def __init__(self, cmd, **kw):
            import io
            seen.append(cmd)
            self.stdout = io.BytesIO()
            self.returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    subprocess.Popen = FakeProc
    try:
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "take.wav")
            with open(wav, "wb") as fh:
                fh.write(b"RIFF" + b"\0" * 2048)
            p = sequencer.Player()
            seen[:] = []
            p.play(wav, gain=1.0)
            check(len(seen) == 1 and seen[0][0] == "aplay",
                  "a take at unity gain still plays in one process", str(seen))
            seen[:] = []
            p.play(wav, gain=0.5)
            check(any("volume=0.500" in " ".join(c) for c in seen),
                  "a take at half gain is played at half gain", str(seen))
            seen[:] = []
            p.play(wav, gain=0.0)
            check(not seen, "a fader at zero plays nothing at all", str(seen))
    finally:
        subprocess.Popen = real_popen


def test_preview_seek():
    section("the preview asks for a frame the clip actually has")
    import video
    app = video.VideoEditor.__new__(video.VideoEditor)
    app._bin = [{"path": "/x/clip.mp4", "name": "clip.mp4", "kind": "video",
                 "dur": 6, "srcdur": 6.0}]
    app._srcdur_cache = {}
    for start in (0.0, 2.0, 5.9, 6.0, 999.0):
        clip = video._new_clip(0, "video", 4)
        clip["start"] = start
        t = app._clip_seek(clip)
        exp = app._render_start(clip, app._clip_dur(clip), 1.0)
        check(0.0 <= t < 6.0,
              "a clip trimmed to %ss previews inside its source" % start,
              "asked for %.2fs of a 6s file" % t)
        check(abs(t - exp) < 0.51,
              "...and previews the frame the export will start on",
              "preview %.2f vs export %.2f" % (t, exp))


def test_open_refuses_foreign():
    section("File > Open refuses a file that is not this app's project")
    import video
    import sequencer
    cases = [
        ("a dict with a clips KEY but string records",
         {"clips": ["a", "b"], "bin": []}),
        ("another app's document", {"entries": [{"text": "my diary"}]}),
        ("a bare list", [1, 2, 3]),
        ("a bare string", "hello"),
        ("tracks holding strings", {"tracks": ["a", "b"]}),
        ("tracks holding numbers", {"tracks": [1, 2]}),
    ]
    for label, payload in cases:
        check(not video.VideoEditor._is_project(payload),
              "Video Editor refuses %s" % label)
        check(not sequencer.Sequencer._is_project(payload),
              "Sequencer refuses %s" % label)
    check(video.VideoEditor._is_project(GOOD_VIDEO),
          "...and still accepts its own project")
    check(video.VideoEditor._is_project(
        {"bin": [], "clips": [], "version": 2, "size": [1280, 720]}),
        "...including a brand-new empty one")
    check(video.VideoEditor._is_project({"bin": [], "slots": [None, None]}),
          "...and a v1 store with sparse slots")
    check(sequencer.Sequencer._is_project(GOOD_SEQ),
          "Sequencer still accepts its own project")
    check(sequencer.Sequencer._is_project(
        {"tracks": [{"name": "Track 1", "input": "Synth", "clips": []}]}),
        "...including a brand-new empty one")


def main():
    os.environ.setdefault("DISPLAY", ":0")
    root = tempfile.mkdtemp(prefix="av_apps_selftest_")
    # nothing may touch the developer's real home
    os.environ["NB_HOME"] = os.path.join(root, "home")
    os.makedirs(os.environ["NB_HOME"], exist_ok=True)
    try:
        test_capture_devices()
        test_tone_engine_retry()
        test_master_fader()
        test_preview_seek()
        test_open_refuses_foreign()
        test_damage(root)
        test_maps(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\n%d checks, %d passed, %d FAILED"
          % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    if FAIL:
        print("RESULT: SOME FAILED")
        for n in FAIL:
            print("  - %s" % n)
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
