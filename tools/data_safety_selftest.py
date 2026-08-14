#!/usr/bin/env python3
"""
DATA SAFETY selftest — the one runner for "can this OS lose the owner's work?"

This machine has no network and no cloud.  Every document, ledger, photo and
half-written chapter exists in exactly one place, so a persistence bug here is
not an inconvenience, it is permanent loss.  Everything below is written from
that premise: an app that raises is annoying, an app that quietly starts from
empty and then saves over the user's real file is unrecoverable.

Five sections, run in order, one command:

  1. ATOMIC WRITER    nbapp.atomic_write_json under every hostile condition —
                      unserialisable payload, read-only directory, full disk,
                      SIGKILL between the fsync and the rename, four processes
                      writing the same file at once.  The user's previous file
                      must survive every one of them.

  2. WRITE PATHS      a static sweep of de/*.py for user-data writes that
                      bypass the atomic helper.  A bare open(path,"w")
                      truncates the destination BEFORE the new bytes arrive, so
                      a crash or a full disk mid-write destroys the old version
                      of a file the user may have spent a week on.

  3. CORRUPT STORE    the headline.  For each app: put a damaged store on disk
                      that still plainly CONTAINS the user's text, launch the
                      app, close it, and ask whether those bytes still exist
                      anywhere.  An app that cannot parse its store must not
                      overwrite it — a file it refuses to read is still the
                      only copy, and a human (or a later release) can often
                      recover the text from it.  Falling back to empty and then
                      saving that empty state over the original is the single
                      most destructive thing any app here can do.

  4. SHARED STORES    tasks.json / calendar.json / calendars.json are written
                      by more than one module (the Tasks app, the desktop
                      widget column, the Calendar app).  Whole-file replace
                      with no re-read means the last writer wins and everything
                      the other one added since it loaded is gone.

  5. RECORD LOSS      section 3 asks what happens to a store the app cannot
                      read; this asks what happens to one it CAN, when there is
                      a lot in it.  Academics' loader ended `return out[:200]`,
                      so a student with 260 assignments lost 60 of them to an
                      open and a close -- nothing damaged, nothing clicked.
                      Plant a lot of records, open and close twice, and count.

Every case that touches disk runs in its OWN SUBPROCESS against a throwaway
NB_HOME, which is both faithful (apps are separate processes on the guest) and
safe (the caller's real home is never touched).  Subprocess isolation is also
load-bearing for correctness: a module that declares __gtype_name__ cannot be
re-imported into the same process, so an in-process harness reports a GType
collision instead of the app's real behaviour.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  python3 tools/data_safety_selftest.py

Options:
  --section N[,N...]   run only these sections (1-5)
  --quiet              only failures and the summary
"""
import argparse
import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile

DE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "..", "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
DE = os.path.normpath(DE)
if DE not in sys.path:
    sys.path.insert(0, DE)

MARK = "USERDATA-MARKER-9Q7"      # the user's text, planted in a damaged store

results = []      # (ok, name)
_quiet = False


def check(name, ok, note=""):
    results.append((bool(ok), name))
    if not ok or not _quiet:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           ("  [%s]" % note) if note else ""))


def note(text):
    if not _quiet:
        print(text)


# ==========================================================================
#  Section 1 — the atomic writer under attack
# ==========================================================================
GOOD = {"chapters": [{"title": "Chapter One",
                      "body": "a week of the user's real work"}]}


def _strays(d):
    return sorted(f for f in os.listdir(d) if f.startswith(".nbw-"))


def _child(code, *args, **kw):
    """Run `code` in a fresh interpreter with de/ importable."""
    return subprocess.run([sys.executable, "-c", code, *args],
                          capture_output=True, text=True, timeout=120, **kw)


def section_atomic():
    note("--- 1. atomic writer -------------------------------------------")
    import nbapp

    root = tempfile.mkdtemp(prefix="ds_atomic_")

    def fresh(name):
        d = os.path.join(root, name)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "app.json")
        nbapp.atomic_write_json(p, GOOD)
        return d, p

    def intact(p):
        try:
            return json.load(open(p)) == GOOD
        except Exception:
            return False

    try:
        # -- the happy path still works
        d, p = fresh("baseline")
        check("atomic: round-trips a document", intact(p))
        check("atomic: leaves no temp file", not _strays(d), ",".join(_strays(d)))

        deep = os.path.join(root, "never", "existed", "app.json")
        try:
            nbapp.atomic_write_json(deep, GOOD)
            made = os.path.exists(deep)
        except Exception:
            made = False
        check("atomic: creates a missing config directory", made)

        # WHO THE FINISHED FILE IS FOR. A replace-based writer delivers the
        # destination's contents and must not quietly redecide its permissions:
        # mkstemp makes its draft 0600, and inheriting that turned an exported
        # 0644 document into a 0600 one on the second export — the file a person
        # exports is usually the one they mean to hand to somebody.
        import stat as _stat
        os.umask(0o022)
        mode = lambda p: _stat.S_IMODE(os.stat(p).st_mode)

        d2 = os.path.join(root, "modes")
        os.makedirs(d2, exist_ok=True)
        fresh_doc = os.path.join(d2, "new.pdf")
        nbapp.atomic_write_via(fresh_doc, lambda draft: open(draft, "wb").write(b"pdf"))
        check("atomic: a new document is readable like any other file",
              mode(fresh_doc) == 0o644, oct(mode(fresh_doc)))

        again = os.path.join(d2, "again.pdf")
        open(again, "wb").write(b"first")
        os.chmod(again, 0o644)
        nbapp.atomic_write_via(again, lambda draft: open(draft, "wb").write(b"second"))
        check("atomic: exporting again does not tighten the file already there",
              mode(again) == 0o644, oct(mode(again)))

        # ...and the reverse, which is the same law: a file the person locked
        # down must not be flung open by a save.
        private = os.path.join(d2, "private.txt")
        nbapp.atomic_write_text(private, "first")
        os.chmod(private, 0o600)
        nbapp.atomic_write_text(private, "second")
        check("atomic: saving does not widen a file the owner restricted",
              mode(private) == 0o600, oct(mode(private)))

        store = os.path.join(d2, "store.json")
        nbapp.atomic_write_json(store, {"a": 1})
        check("atomic: a new app store stays private",
              mode(store) == 0o600, oct(mode(store)))

        # -- the payload cannot be serialised (a bug in the app's model)
        d, p = fresh("unserialisable")
        try:
            nbapp.atomic_write_json(p, {"bad": object()})
            raised = False
        except Exception:
            raised = True
        check("atomic: unserialisable payload raises", raised)
        check("atomic: ...previous document survives it", intact(p))
        check("atomic: ...no temp file left", not _strays(d), ",".join(_strays(d)))

        # -- the config directory is read-only
        d, p = fresh("readonly")
        os.chmod(d, 0o500)
        try:
            nbapp.atomic_write_json(p, {"replacement": True})
            raised = False
        except Exception:
            raised = True
        os.chmod(d, 0o700)
        check("atomic: read-only directory raises", raised)
        check("atomic: ...previous document survives it", intact(p))
        check("atomic: ...no temp file left", not _strays(d), ",".join(_strays(d)))

        # -- the disk is full.  RLIMIT_FSIZE in a child gives a real write
        #    failure part-way through the stream, which is what ENOSPC does.
        d, p = fresh("diskfull")
        out = _child(
            "import signal,sys,resource\n"
            "sys.path.insert(0, %r)\n"
            "import nbapp\n"
            "signal.signal(signal.SIGXFSZ, signal.SIG_IGN)\n"
            "resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))\n"
            "try:\n"
            "    nbapp.atomic_write_json(%r, {'pad': 'x' * 200000})\n"
            "    print('NORAISE')\n"
            "except Exception as e:\n"
            "    print('RAISED')\n" % (DE, p))
        check("atomic: a full disk raises instead of truncating",
              out.stdout.strip() == "RAISED", out.stdout.strip())
        check("atomic: ...previous document survives it", intact(p))
        check("atomic: ...no temp file left", not _strays(d), ",".join(_strays(d)))

        # -- killed between the fsync and the rename (power cut / OOM kill)
        d, p = fresh("killed")
        _child(
            "import os,sys,signal\n"
            "sys.path.insert(0, %r)\n"
            "import nbapp\n"
            "os.replace = lambda a, b: os.kill(os.getpid(), signal.SIGKILL)\n"
            "nbapp.atomic_write_json(%r, {'clobbered': True})\n" % (DE, p))
        check("atomic: SIGKILL before the rename leaves the old file", intact(p))

        # -- four processes writing the same store at once
        d, p = fresh("race")
        racer = ("import sys\n"
                 "sys.path.insert(0, %r)\n"
                 "import nbapp\n"
                 "for i in range(120):\n"
                 "    nbapp.atomic_write_json(%r, {'who': sys.argv[1], 'n': i,\n"
                 "                                 'pad': 'y' * 4000})\n"
                 % (DE, p))
        procs = [subprocess.Popen([sys.executable, "-c", racer, str(i)])
                 for i in range(4)]
        torn = 0
        for _ in range(400):
            try:
                json.load(open(p))
            except Exception:
                torn += 1
        for q in procs:
            q.wait()
        check("atomic: concurrent writers never expose a half-written file",
              torn == 0, "%d torn reads" % torn)
        check("atomic: no temp files survive the race",
              not _strays(d), ",".join(_strays(d)))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ==========================================================================
#  Section 2 — user-data writes that bypass the atomic helper
# ==========================================================================
# Files these modules write that are NOT the user's documents: build output,
# system configuration, logs, scratch.  Losing one costs a rebuild, not work.
_NOT_USER_DATA = {
    "gbabuild.py",      # generated C + the .gba image, rebuilt from source
    "installer.py",     # writes the freshly-installed system, not $HOME
    "nbmediakeys.py",   # sysfs backlight
    "nbaudio.py",       # generated ALSA routing (temp+fsync+replace), not $HOME
    "nbgame.py",        # debug log
    "xrootbg.py", "xflushd.py", "xflush.py", "xnudge.py",
}
# Specific writes inside user-facing modules that are NOT an irreplaceable
# document being overwritten in place.  Each needs a reason: anything new that
# is not listed here fails this section on purpose, so a future truncating
# write of somebody's only copy has to be argued for rather than slipped in.
_ALLOWED = [
    # The tablet-mode daemon's files are RUNTIME STATE, not user work: the
    # /tmp/nb-tablet-mode flag other processes poll, and its own tmp+rename
    # sidecar. Both are rebuilt on the next fold event; losing them costs one
    # lid flip. (The test-batch lane's 047-048 residue finally recorded —
    # this row was owed since 2026-08-08.)
    ("xtabletd.py", "self.inhibited_file", "runtime flag in /tmp"),
    ("xtabletd.py", 'open(tmp, "w", encoding="ascii")',
     "tmp + rename of the same /tmp runtime flag"),
    # Every write the Disc Burner makes goes into a scratch directory it just
    # created with mkdtemp and deletes when the burn ends — the menu PNGs, the
    # spumux/dvdauthor XML, and the subpicture-muxed menu stream. None of them
    # can land on a file a person owns: the destination is a directory that did
    # not exist a moment earlier and whose name it chose. The person's own
    # songs and videos are only ever READ; the disc is the output.
    ("burner.py", "surf.write_to_png(path)", "menu art into its own scratch dir"),
    ("burner.py", '"spumux.xml"', "generated input in its own scratch dir"),
    ("burner.py", '"dvdauthor.xml"', "generated input in its own scratch dir"),
    ("burner.py", "open(menu_sub,", "muxed menu stream in its own scratch dir"),
    ("settings.py", "/etc/hostname", "system config, not user work"),
    # The per-phase build log the SDK appends to while a ROM compiles. Append
    # mode truncates nothing, and the file is a diagnostic record of what the
    # toolchain just did — the project it was built from is the user's work and
    # is written elsewhere, atomically.
    ("gbasdk.py", "gbasdk-build.log", "append-only build log, not user work"),
    # Installing a package writes every payload to a ".nbpkg-tmp" sibling and
    # os.replace()s it into place, then registers the app the same way, LAST, so
    # a half-written install is never registered. This is the atomic pattern the
    # rest of this check exists to require, not an exception to it.
    ("nbpkg_install.py", 'open(tmp, "wb")', "temp + os.replace of a payload"),
    ("nbpkg_install.py", 'open(tmp, "w", encoding="utf-8")',
     "temp + os.replace of the app register"),
    ("finder.py", "origins", "trash put-back sidecar, rebuilt on next trash"),
    ("finder.py", "nbapp.APP_FLAG", "runtime flag in /tmp"),
    # gbasdk hands off to the emulator the same way the Finder hands off to
    # an app, and writes the identical runtime flag. Same pattern, same
    # reason, same allowance.
    ("gbasdk.py", "nbapp.APP_FLAG", "runtime flag in /tmp"),
    ("finder.py", 'open(d, "wb")', "chunked copy into a NEW destination file"),
    # Cover art pulled out of a music file and cached so playing a track does
    # not decode it again. Temp + os.replace into $NB_HOME/.cache; the source
    # of truth is the audio file itself, so losing it costs one re-read.
    ("music.py", 'pb.savev(tmp', "extracted cover art cache, regenerable"),
    # The flattened export goes to "<path>.new" and is moved into place, so a
    # failed export never replaces the previous PNG with a partial one.
    # Was "write_to_png(tmp)", where tmp was `path + ".new"` — atomic for the
    # destination, but a draft name a person can already own: saving
    # drawing.png destroyed a real drawing.png.new beside it, and a failed
    # save deleted it. Now drafts under nbapp.atomic_write_via's unguessable
    # temp, so the only file at risk is the one being saved.
    ("illustrator.py", "write_to_png(draft)", "draft of nbapp.atomic_write_via"),
    # Animation's PNG-frames export: each frame lands in a mkstemp file in the
    # destination folder and is os.replace()d into its numbered name, so a
    # cancelled or failed export never leaves a truncated frame. The frames are
    # a derived product, regenerable from the project store.
    ("animation.py", "write_to_png(tmp)", "temp + os.replace of the export"),
    # A fresh mkstemp scratch frame handed to ffmpeg, deleted after the export.
    ("video.py", "surf.write_to_png(p)", "mkstemp scratch frame, never $HOME"),
    ("shell.py", "/tmp/nb-ready", "runtime flag in /tmp"),
    ("media.py", "VIDEO_FULL_FLAG", "runtime flag in /tmp, holds this pid"),
    ("video.py", "_exp_err_file", "ffmpeg stderr scratch"),
    ("gbaemu.py", "_log_path()", "append-only emulator log"),
    ("accounting.py", "DAMAGED_FILE", "writes the quarantine copy itself"),
    # Was allowed as a "regenerable CSV export of live data" — true, but it
    # truncated last month's sheet before writing a byte. It now renders into
    # nbapp.atomic_write_via's DRAFT, so `dest` here is a temp file in the
    # destination's directory and the export it replaces is untouched until
    # the rows are all written.
    ("accounting.py", 'open(dest, "w"', "writes the atomic writer's draft"),
    # First-run setup writes machine configuration, not documents. The shadow
    # write is a temp + os.replace (the safe pattern this check exists to
    # enforce); the other two are small system files that the installer writes
    # the same way, and neither holds anything a person authored.
    ("firstrun.py", "shadow_tmp", "temp + chmod 600 + os.replace of /etc/shadow"),
    ("firstrun.py", "HOSTNAME_FILE", "system config, not user work"),
    ("firstrun.py", "USER_NAME_FILE", "the display name, system config"),
    ("firstrun.py", "XKB_CONF", "system config, not user work"),
    # Writing an image to a USB stick IS this app's function, and the target is
    # a raw BLOCK DEVICE (/dev/sdX), not a file. The safe pattern this section
    # enforces -- write a temp file, os.replace it into position -- has no
    # meaning for a device node: there is nothing to rename onto, and a partial
    # write cannot be avoided by any file-level trick. Erasing the chosen disk
    # is the operation the user asked for, not an accident to be guarded
    # against here; the guarding that DOES apply lives in the app and is real
    # (_system_disks() excludes anything mounted, in use as swap, or backing a
    # loop device; _is_usb() checks the sysfs device link rather than the
    # unreliable `removable` flag; and the disk has to be confirmed BY NAME).
    # Listed rather than left failing because a data-safety gate that is
    # permanently red for a legitimate case stops being read at all -- which is
    # the one outcome that would let a real truncating write through.
    ("usbwriter.py", 'd["node"], "wb"', "raw block device; writing it is the app"),
]


# Methods that create or truncate a file WITHOUT going through open().  This
# check used to look for open(..., 'w') alone, so a module could overwrite
# somebody's only copy through any of these and stay green -- the gap was found
# when a raw open() here was rewritten as pb.savev() and the check did not
# notice the write had moved.  Each takes its destination path as the first
# argument.
_PATH_WRITERS = ("savev", "save_to_callbackv", "write_to_png")


def _buffer_names(tree):
    """Names bound to an in-memory buffer (io.BytesIO()/StringIO()).

    The _PATH_WRITERS methods take either a path OR a file-like object, and
    rendering to a BytesIO to hand the bytes to a pixbuf loader is the common
    idiom here. That touches no file at all, so it must not be reported as a
    truncating write."""
    names = set()
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)):
            continue
        f = n.value.func
        who = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if who in ("BytesIO", "StringIO"):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def _write_calls(path):
    """Yield (lineno, snippet) for every file-creating call in `path`.

    That means open(..., 'w'|'wb'|'w+'|'a') AND the pixbuf/cairo methods in
    _PATH_WRITERS, which truncate their destination just as thoroughly."""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except SyntaxError as e:
        return [("SYNTAX", str(e))]
    src = open(path, encoding="utf-8").read().splitlines()
    buffers = _buffer_names(tree)
    hits = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        if isinstance(n.func, ast.Attribute) and n.func.attr in _PATH_WRITERS:
            dest = n.args[0] if n.args else None
            if isinstance(dest, ast.Name) and dest.id in buffers:
                continue                      # renders to memory, not a file
            line = src[n.lineno - 1].strip() if n.lineno <= len(src) else ""
            hits.append((n.lineno, line))
            continue
        if not (isinstance(n.func, ast.Name) and n.func.id == "open"):
            continue
        mode = ""
        if len(n.args) > 1 and isinstance(n.args[1], ast.Constant):
            mode = str(n.args[1].value)
        for kw in n.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = str(kw.value.value)
        if "w" in mode or "a" in mode:
            line = src[n.lineno - 1].strip() if n.lineno <= len(src) else ""
            hits.append((n.lineno, line))
    return hits


def section_write_paths():
    note("--- 2. write paths --------------------------------------------")
    offenders = []
    unparsable = []
    for fn in sorted(os.listdir(DE)):
        if not fn.endswith(".py") or fn in _NOT_USER_DATA:
            continue
        for lineno, line in _write_calls(os.path.join(DE, fn)):
            if lineno == "SYNTAX":
                unparsable.append((fn, line))
                continue
            if any(a[0] == fn and a[1] in line for a in _ALLOWED):
                continue
            offenders.append("%s:%d  %s" % (fn, lineno, line[:70]))
    # nbapp/nbi18n hand-roll their own writers on purpose; they ARE the
    # helpers, and both already do temp + replace.
    offenders = [o for o in offenders
                 if not o.startswith(("nbapp.py", "nbi18n.py"))]

    for fn, err in unparsable:
        check("write-paths: %s parses" % fn, False, err[:80])

    for o in offenders:
        check("write-paths: truncating write of user data - " + o, False)
    if not offenders and not unparsable:
        check("write-paths: every user-data write is crash-safe", True)

    # Two apps used to hand-roll their own atomic writer, which meant they
    # silently missed anything added to the shared one (they bypassed the
    # damaged-store quarantine for exactly that reason). DELEGATING to
    # nbapp.atomic_write_json is the better outcome, so accept either: a
    # self-contained implementation that is genuinely atomic, or a wrapper that
    # hands off to the shared writer.
    for fn in ("accounting.py", "journal.py"):
        src = open(os.path.join(DE, fn), encoding="utf-8").read()
        if "def _atomic_write_json" not in src:
            check("write-paths: %s has no private writer (uses the shared one)"
                  % fn, True)
            continue
        seg = src[src.index("def _atomic_write_json"):]
        seg = seg[:seg.index("\n\n\n")] if "\n\n\n" in seg else seg
        delegates = "nbapp.atomic_write_json" in seg
        selfcontained = ("mkstemp" in seg and "os.fsync" in seg
                         and "os.replace" in seg and "os.unlink" in seg)
        check("write-paths: %s's private writer is safe (%s)"
              % (fn, "delegates" if delegates else "self-contained"),
              delegates or selfcontained)


# ==========================================================================
#  Section 3 — a damaged store must never be overwritten
# ==========================================================================
# app -> the file under $NB_HOME/.config/notebook that holds the user's WORK.
#
# Only stores whose contents cannot be reproduced are listed.  A calculator's
# degrees flag, a 2048 high score, the Finder's view mode, a terminal font size,
# the Settings preferences and the Maps viewport are all regenerable in seconds
# and are deliberately out of scope here — config_resilience_selftest.py already
# proves those apps open cleanly on a damaged file, which is all they owe.
# What is listed below is the stuff that exists nowhere else on earth.
STORES = [
    ("academics", "academics.json", "lecture notes, timetable and homework"),
    ("accounting", "accounting.json", "the ledger"),
    ("bills", "bills.json",         "every bill and how it gets paid"),
    ("calendar", "calendar.json",   "every appointment"),
    ("contacts", "contacts.json",   "every person they know"),
    ("cookbook", "cookbook.json",   "their recipes"),
    ("ebook", "ebook.json",         "the library and reading positions"),
    ("gbasdk", "gbasdk.json",       "a game project"),
    ("journal", "journal.json",     "years of diary entries"),
    ("music", "music.json",         "hand-built playlists"),
    ("novel", "novel.json",         "the manuscript"),
    ("screenplay", "screenplay.json", "the script"),
    ("sequencer", "sequencer.json", "compositions"),
    ("tasks", "tasks-app.json",     "the task list"),
    ("video", "video.json",         "the edit timeline"),
    ("writer", "writer.json",       "the document in progress"),
    # These were only ever covered by config_resilience_selftest, which proves
    # the window CONSTRUCTS on a bad store and nothing at all about whether the
    # close-time save then wrote over it.  Construction is not survival: the
    # 13-app data-loss bug of record passed a construct-only test the whole
    # time.  Every store a person can put their own work into belongs here.
    ("illustrator", "illustrator.json", "their drawings"),
    ("maps", "maps.json",           "saved places"),
    ("media", "media.json",         "playback positions"),
    ("workout", "workout.json",     "the training log and streaks"),
    ("mealplanner", "mealplanner.json", "the week's meal plan"),
    ("language", "language.json",   "course progress, XP and streaks"),
    ("widgetsettings", "widgets.json", "the desktop tile layout"),
    ("finder", "finder.json",       "Finder places and view settings"),
    ("settings", "settings.json",   "every system preference"),
    ("terminal", "terminal.json",   "shell history"),
    ("calculator", "calculator.json", "the calculation tape"),
    ("g2048", "g2048.json",         "the game in progress and best score"),
    ("gbaemu", "gbaemu.json",       "the ROM library and save slots"),
]

# Three ways a store goes bad on a machine with one copy of everything: a
# half-written file from a power cut under an older non-atomic build, a file
# whose shape a later release changed, and a file a bad block turned to noise.
# In all three the user's words are still sitting there in plain text.
DAMAGE = {
    "truncated": lambda: (
        '{"entries": [{"title": "' + MARK + '", "body": "half of a chapter'),
    "wrong-shape": lambda: json.dumps(
        {k: MARK for k in ("entries", "items", "tasks", "chapters", "body",
                           "tx", "recipes", "people", "events", "notes")}),
    "not-json": lambda: "\x00\x01binary noise " + MARK + " more noise\x00",
}


def _worker_open_close(app, home, cfgname, mode):
    """--worker-store body.  Plant a damaged store carrying MARK, launch the
    app the way the Finder does, close it the way Esc does, then report whether
    the user's bytes still exist anywhere under NB_HOME."""
    cfgdir = os.path.join(home, ".config", "notebook")
    os.makedirs(cfgdir, exist_ok=True)
    path = os.path.join(cfgdir, cfgname)
    with open(path, "w") as fh:
        fh.write(DAMAGE[mode]())

    os.environ["NB_HOME"] = home
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    import inspect

    mod = __import__(app)
    cls = None
    for _n, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            cls = c
            break
    if cls is None:
        print("NOCLASS")
        return 2

    def pump():
        for _ in range(6):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)

    win = cls()
    pump()
    win.destroy()
    pump()

    found = []
    for r, _d, files in os.walk(home):
        for f in files:
            try:
                with open(os.path.join(r, f), "rb") as fh:
                    if MARK.encode() in fh.read():
                        found.append(os.path.relpath(os.path.join(r, f), home))
            except OSError:
                pass
    print("SURVIVES" if found else "DESTROYED", ";".join(found))
    return 0


def section_corrupt_store():
    note("--- 3. a damaged store must never be overwritten ---------------")
    root = tempfile.mkdtemp(prefix="ds_store_")
    try:
        for app, cfgname, what in STORES:
            for mode in DAMAGE:
                home = os.path.join(root, app, mode)
                os.makedirs(home, exist_ok=True)
                r = subprocess.run(
                    [sys.executable, os.path.abspath(__file__),
                     "--worker-store", app, home, cfgname, mode],
                    capture_output=True, text=True, timeout=180,
                    env=dict(os.environ, NB_HOME=home))
                out = (r.stdout or "").strip().splitlines()
                verdict = out[-1] if out else ""
                nm = "store: %s keeps a %s store (%s)" % (app, mode, what)
                if r.returncode != 0 or not verdict:
                    err = (r.stderr or "").strip().splitlines()
                    check(nm, False,
                          "app did not launch: " + (err[-1][:90] if err else "?"))
                elif verdict.startswith("SURVIVES"):
                    check(nm, True)
                else:
                    check(nm, False, "destroyed by open+close, no user action")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ==========================================================================
#  Section 4 — stores written by more than one module
# ==========================================================================
SHARED = {
    "tasks.json": ("tasks.py", "widgets.py"),
    "calendar.json": ("calendar.py", "tasks.py", "widgets.py"),
    "calendars.json": ("calendar.py", "tasks.py"),
}


def _writers_of(store):
    """Which de/*.py modules write `store`, by following the constant its path
    is bound to (TASKS_FILE = .../tasks.json, then atomic_write_json(TASKS_FILE...)."""
    out = set()
    for fn in sorted(os.listdir(DE)):
        if not fn.endswith(".py"):
            continue
        try:
            src = open(os.path.join(DE, fn), encoding="utf-8").read()
        except OSError:
            continue
        consts = set(re.findall(
            r'^([A-Z_]+)\s*=\s*os\.path\.join\([^)]*["\']' + re.escape(store)
            + r'["\']\s*\)', src, re.M))
        for c in consts:
            if re.search(r'atomic_write_json\(\s*' + c + r'\b', src):
                out.add(fn)
    return out


def section_shared_stores():
    note("--- 4. stores with more than one writer ------------------------")
    for store, expected in SHARED.items():
        writers = _writers_of(store)
        if len(writers) <= 1:
            check("shared: %s has a single writer" % store, True,
                  ",".join(sorted(writers)) or "none")
            continue
        # More than one module replaces the whole file.  That is only safe if
        # each one re-reads the file immediately before writing, so it cannot
        # drop what the other added since it loaded.
        unsafe = []
        for fn in sorted(writers):
            src = open(os.path.join(DE, fn), encoding="utf-8").read()
            # A read-modify-write helper names both a reader and the write in
            # one function; tasks.py._append_calendar_event is the model, and
            # widgets.py._toggle_task is the same pattern under a different
            # verb. Matching the verb keeps this cheap and readable — testing
            # "reads somewhere in the same function" instead sounds more
            # principled but flags a store's OWNER, which legitimately writes
            # its whole in-memory model and is the source of truth while it
            # runs. Add verbs here as new read-modify-write helpers appear.
            # Look INSIDE the read-modify-write helper's own body for both a
            # read and a write. Searching forward from `def` for the next
            # atomic_write_json is order-dependent and gave a false FAIL for
            # widgets.py._toggle_task, whose write helper (_save_tasks) is
            # defined earlier in the file. Restricting the scan to the
            # verb-named helper keeps a store's OWNER out of it — an owner
            # legitimately writes its whole in-memory model.
            # The write may be a call to the module's own save helper rather
            # than atomic_write_json directly — widgets.py._toggle_task calls
            # self._save_tasks(), whose definition sits EARLIER in the file, so
            # looking only for atomic_write_json after the `def` never found it.
            rmw = re.search(
                r'def [_a-z]*(append|merge|reconcile|toggle)[_a-z]*\(.*?'
                r'(atomic_write_json|self\._save)', src, re.S)
            if not rmw:
                unsafe.append(fn)
        check("shared: %s is written by %d modules without a re-read"
              % (store, len(writers)),
              not unsafe,
              "whole-file replace in " + ",".join(unsafe) if unsafe else "")


# ==========================================================================
#  Section 5 — a HEALTHY store must not lose records either
# ==========================================================================
# Section 3 asks what happens to a store the app cannot read.  This asks the
# question nobody had asked: what happens to a store it CAN read, when there is
# simply a lot in it.
#
# THE BUG THIS EXISTS FOR.  Academics' loader ended `return out[:200]`.  A
# student with 260 assignments opened the app; the loader silently dropped 60 of
# them and the close-time save wrote the surviving 200 straight over the store.
# Nothing was damaged, nothing was clicked, and the app looked entirely normal.
#
# The .bak did not save them either, and that part generalises to every app: a
# cap is applied at LOAD time, but the SAVE writes derived fields the loader
# never reads back (Academics decorates each assignment with its "course"), so
# the truncated store OUTWEIGHED the full one, _bak_would_shrink saw growth, and
# the second open overwrote the last copy.  Two opens, two closes, gone.
#
# So: plant a lot of records, open and close TWICE, and count.  The fixture is
# validated by the app's own model on the first open — if the app does not
# report loading all N, the fixture is wrong and this fails loudly rather than
# passing vacuously.
N_RECORDS = 260          # comfortably past the 200-record cap that used to exist


def _rec_academics(n):
    return {"classes": [{"label": "Chem", "color": "#4A5E73", "room": "",
                         "instructor": "", "meets": []}],
            "lectures": [], "active": -1,
            "homework": [{"title": "Assignment %03d" % i, "cls": 0,
                          "due": "", "done": False, "note": ""}
                         for i in range(n)]}


def _rec_journal(n):
    return {"active": 0,
            "entries": [{"title": "Day %03d" % i, "text": "entry %d" % i}
                        for i in range(n)]}


def _rec_tasks(n):
    return {"projects": [],
            "tasks": [{"title": "Task %03d" % i, "done": False}
                      for i in range(n)]}


def _rec_accounting(n):
    return {"opening": 0,
            "tx": [{"date": "1 Jan", "desc": "Item %03d" % i, "amt": -1.0}
                   for i in range(n)]}


def _rec_cookbook(n):
    return {"cats": ["All"], "active_cat": 0, "sel": 0,
            "recipes": [{"title": "Recipe %03d" % i, "cat": None, "desc": "",
                         "time": "", "makes": "", "effort": "", "ing": "",
                         "steps": "", "photo": ""} for i in range(n)]}


def _rec_contacts(n):
    return {"people": [{"name": "Person %03d" % i, "role": "", "phone": "",
                        "email": "", "address": "", "bday": "", "notes": ""}
                       for i in range(n)]}


def _rec_calendar(n):
    return [{"id": "e%d" % i, "date": "2026-08-%02d" % ((i % 28) + 1),
             "start": 9.0, "end": 10.0, "title": "Event %03d" % i,
             "cal": "Personal"} for i in range(n)]


def _rec_bills(n):
    return {"bills": [{"id": "b%d" % i, "payee": "Payee %03d" % i,
                       "account": "44-%03d" % i, "amount": 1000 + i,
                       "due": "2026-08-11", "every": 1, "method": "mail",
                       "address": "PO Box %d" % i, "phone": "", "note": "",
                       "lead": 5, "paid": []} for i in range(n)]}


def _rec_workout(n):
    return {"goal": 0, "days": {}, "goals": {},
            "exercises": [{"id": i, "name": "Lift %03d" % i, "sets": 3,
                           "reps": 10} for i in range(n)]}


# app -> (store file, fixture builder, how to count records in the app's model,
#         how to count them in the parsed file)
RECORD_STORES = {
    "academics":  ("academics.json",  _rec_academics,
                   lambda w: len(w.homework),  lambda d: len(d["homework"])),
    "journal":    ("journal.json",    _rec_journal,
                   lambda w: len(w.entries),   lambda d: len(d["entries"])),
    "tasks":      ("tasks-app.json",  _rec_tasks,
                   lambda w: len(w.tasks),     lambda d: len(d["tasks"])),
    "accounting": ("accounting.json", _rec_accounting,
                   lambda w: len(w.tx),        lambda d: len(d["tx"])),
    "cookbook":   ("cookbook.json",   _rec_cookbook,
                   lambda w: len(w.recipes),   lambda d: len(d["recipes"])),
    "contacts":   ("contacts.json",   _rec_contacts,
                   lambda w: len(w.people),    lambda d: len(d["people"])),
    "calendar":   ("calendar.json",   _rec_calendar,
                   lambda w: len(w.events),    lambda d: len(d)),
    "bills":      ("bills.json",      _rec_bills,
                   lambda w: len(w.bills),     lambda d: len(d["bills"])),
    "workout":    ("workout.json",    _rec_workout,
                   lambda w: len(w.data["exercises"]),
                   lambda d: len(d["exercises"])),
}


def _worker_records(app, home, cfgname):
    """--worker-records body. Open the app the way the Finder does, report how
    many records its own model actually holds, then close it the way Esc does
    (which is what triggers the save that used to do the damage)."""
    os.environ["NB_HOME"] = home
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    import inspect

    mod = __import__(app)
    cls = None
    for _n, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            cls = c
            break
    if cls is None:
        print("NOCLASS")
        return 2

    def pump():
        for _ in range(6):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)

    win = cls()
    pump()
    loaded = RECORD_STORES[app][2](win)
    win.destroy()
    pump()
    print("LOADED:%d" % loaded)
    return 0


def section_record_loss():
    note("--- 5. a healthy store must not lose records -------------------")
    root = tempfile.mkdtemp(prefix="ds_rec_")
    try:
        for app in sorted(RECORD_STORES):
            cfgname, build, _model, count = RECORD_STORES[app]
            home = os.path.join(root, app)
            cfgdir = os.path.join(home, ".config", "notebook")
            os.makedirs(cfgdir, exist_ok=True)
            path = os.path.join(cfgdir, cfgname)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(build(N_RECORDS), fh)

            loaded = []
            failed = None
            for _cycle in (1, 2):
                r = subprocess.run(
                    [sys.executable, os.path.abspath(__file__),
                     "--worker-records", app, home, cfgname],
                    capture_output=True, text=True, timeout=180,
                    env=dict(os.environ, NB_HOME=home))
                out = [l for l in (r.stdout or "").splitlines()
                       if l.startswith("LOADED:")]
                if r.returncode != 0 or not out:
                    err = (r.stderr or "").strip().splitlines()
                    failed = "app did not launch: " + (err[-1][:90] if err else "?")
                    break
                loaded.append(int(out[0].split(":", 1)[1]))

            nm = "records: %s keeps all %d on open+close" % (app, N_RECORDS)
            if failed:
                check(nm, False, failed)
                continue
            # The fixture has to actually reach the app, or this proves nothing.
            if loaded[0] != N_RECORDS:
                check(nm, False,
                      "the app loaded %d of %d — records dropped AT LOAD"
                      % (loaded[0], N_RECORDS))
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    on_disk = count(json.load(fh))
            except Exception as exc:
                check(nm, False, "store unreadable after close: %s" % exc)
                continue
            check(nm, on_disk == N_RECORDS,
                  "%d of %d left on disk after two open/close cycles"
                  % (on_disk, N_RECORDS))

            # ...and the one recovery copy must never be refreshed with less
            # than it already holds. This is what actually turned Academics'
            # truncation from recoverable into permanent.
            bak = path + ".bak"
            nmb = "records: %s's .bak is never refreshed with fewer" % app
            if not os.path.exists(bak):
                check(nmb, True, "no .bak written")
                continue
            try:
                with open(bak, encoding="utf-8") as fh:
                    in_bak = count(json.load(fh))
            except Exception as exc:
                check(nmb, False, ".bak unreadable: %s" % exc)
                continue
            check(nmb, in_bak >= N_RECORDS,
                  ".bak holds %d, the user had %d" % (in_bak, N_RECORDS))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ==========================================================================
SECTIONS = {1: section_atomic, 2: section_write_paths,
            3: section_corrupt_store, 4: section_shared_stores,
            5: section_record_loss}


def main():
    global _quiet
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--section", default="1,2,3,4,5")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    _quiet = args.quiet

    want = [int(s) for s in args.section.split(",") if s.strip()]
    for n in want:
        SECTIONS[n]()

    bad = [nm for ok, nm in results if not ok]
    print("")
    print("%d checks, %d passed, %d FAILED" %
          (len(results), len(results) - len(bad), len(bad)))
    if bad:
        print("RESULT: SOME FAILED")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker-store":
        raise SystemExit(_worker_open_close(*sys.argv[2:6]))
    if len(sys.argv) > 1 and sys.argv[1] == "--worker-records":
        raise SystemExit(_worker_records(*sys.argv[2:5]))
    raise SystemExit(main())
