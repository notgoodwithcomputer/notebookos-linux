#!/usr/bin/env python3
"""
AppWindow — the shared base for every Notebook OS application.

Per the design language, an app is a full-screen surface beneath a menu bar:
the brand snail logo, the app name, the app's own menus, and a right-side
clock / date cluster. This base draws that chrome natively (GTK) and exposes a
content area for the app to fill. Esc or the logo returns to the Finder.

Subclass it, set `app_name` and `menus`, and pack widgets into `self.content`.
Override `menu_items(name)` to give a menu real dropdown actions (call
super().menu_items(name) to keep the built-in File/Edit/app defaults).
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

import time
import sys
import os
import re
import math
import json
import tempfile

from nbi18n import _t  # noqa: E402  (shared translation layer)
import nbcommands  # noqa: E402  (canonical command labels/shortcuts/grouping)

# Single source of truth for the release version, shared with the ISO
# builder / installer. /etc/os-release (when present) is authoritative; this
# constant is the fallback for hosts / dev checkouts without that file.
NB_VERSION = "1.0"

# The layout grid, runtime copy (docs/PAPER-PHYSICS.md §E3). The build-side
# canonical values live in tools/design_tokens.py, which does not ship on the
# image, so apps read them here; tools/grid_check.py fails the build when the
# two copies (or shell.py's strut) disagree. Values are px.
GRID_UNIT = 4
MARGIN = 24            # window edge -> content
RAIL = 240             # the one sidebar width (exceptions: grid_check.py)
GUTTER = 24            # rail <-> field
HAIRLINE = 1
MEASURE_READ = 640     # maximum prose measure
MEASURE_FORM = 1040    # maximum form measure
PANEL_H = 46           # must equal shell.py's strut reservation
THIRD_PANE_MIN_W = 1366  # below this a third pane collapses into the rail


def canvas_h(screen_h):
    """Vertical layout budget: screen minus the panel strut — 722 on a
    768 panel, not the 740 an older note here claimed."""
    return screen_h - PANEL_H


def clamp_to_work(x, y, w, h, work):
    """Pure clamp of a w×h rectangle's origin into a workarea tuple
    (wx, wy, ww, wh). A rectangle larger than the area pins to the area's
    origin — the top-left is the part a menu must never lose."""
    wx, wy, ww, wh = work
    return (int(min(max(x, wx), wx + max(0, ww - w))),
            int(min(max(y, wy), wy + max(0, wh - h))))


def popup_at(menu, event=None, widget=None, anchor="pointer"):
    """Pop a Gtk.Menu that stays on the working screen.

    GTK's own edge flipping is correct on every host probe we have (three
    popup shapes, scale 1 and 2 — test-batch, 2026-08-07), yet a bottom-row
    menu still escaped on the guest. Rather than trust the stack, the mapped
    menu's toplevel is verified one idle after map against its monitor's
    workarea — the top edge never above the panel strut — and re-moved only
    if an edge escaped. Defense in depth: on a correct stack the clamp is an
    identity and nothing moves; on a broken one the menu is corrected before
    the user can read it. Returns the menu."""
    if anchor == "widget-sw" and widget is not None:
        menu.popup_at_widget(widget, Gdk.Gravity.SOUTH_WEST,
                             Gdk.Gravity.NORTH_WEST, event)
    else:
        menu.popup_at_pointer(event)

    state = {}

    def _verify(m):
        try:
            win = m.get_toplevel().get_window() or m.get_window()
            if win is None:
                return False
            x, y = win.get_root_origin()
            w, h = win.get_width(), win.get_height()
            mon = Gdk.Display.get_default().get_monitor_at_window(win)
            wa = mon.get_workarea()
            top = max(wa.y, PANEL_H)
            cx, cy = clamp_to_work(
                x, y, w, h,
                (wa.x, top, wa.width, wa.height - (top - wa.y)))
            if (cx, cy) != (x, y):
                win.move(cx, cy)
        except Exception:                                         # noqa: BLE001
            pass
        return False

    def _on_map(m):
        GLib.idle_add(_verify, m)
        hid = state.pop("hid", None)
        if hid is not None:
            try:
                m.disconnect(hid)
            except Exception:                                     # noqa: BLE001
                pass
        return False

    state["hid"] = menu.connect_after("map", _on_map)
    return menu


_CSS_DONE = False
# Decoded-once cache for the brand snail. logo.png is a 796x364 RGBA PNG;
# new_from_file_at_scale decodes the FULL source before downscaling to the
# 34x16 menu-bar icon, so on the CPU-only software renderer that decode is a
# real cost. Cache it at module scope and fill the Gtk.Image off the
# first-paint critical path (see _menubar) so the window maps first and the
# logo blits an idle tick later. False = tried and failed, don't retry.
_LOGO_PB = None


_REAPED_TMP = set()
_BACKED_UP = set()


def _reap_stale_tmp(d):
    """Remove abandoned .nbw-*.tmp files (a kill between mkstemp and replace
    leaks one). Once per directory per process — this is tidying, not a job
    worth a directory scan on every autosave."""
    if d in _REAPED_TMP:
        return
    _REAPED_TMP.add(d)
    try:
        cutoff = time.time() - 3600
        for f in os.listdir(d):
            if f.startswith(".nbw-") and f.endswith(".tmp"):
                p = os.path.join(d, f)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.unlink(p)
                except OSError:
                    pass
    except OSError:
        pass


def _payload_weight(obj):
    """How much user content a parsed store holds, as a count of leaves that
    actually say something. Only ever used to compare two versions of the SAME
    store, so the absolute number means nothing — all that matters is that
    losing records lowers it.

    A zero counts as nothing on purpose. An app's blank default is mostly
    zeroes — 2048 writes a 4x4 board of them and a best score of 0 — and
    counting those made an untouched board outweigh a store full of the user's
    text, which is the exact comparison this feeds."""
    if isinstance(obj, dict):
        return sum(_payload_weight(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return sum(_payload_weight(v) for v in obj)
    if obj is None or obj is False or obj == "" or obj == 0:
        return 0
    return 1


def _record_counts(obj, path="", out=None, depth=0):
    """How many records sit in each list inside a parsed store, keyed by the
    STRUCTURAL place that list lives: "classes", "lectures/*/ranges", ...

    _payload_weight answers "how much is in here" as ONE number for the whole
    store, and that turned out to be the wrong question. See _bak_would_shrink.

    List elements collapse to a single "*" step rather than their index, and the
    counts at a path are SUMMED. Two reasons, both learned the hard way:
    per-index keys ("lectures[0]/ranges", "lectures[1]/ranges", ...) make the
    map as large as the store — 80,000 keys and 600ms on a heavily formatted
    term, where the summed form costs 3ms — and they also make a mere REORDER of
    a list look like loss at every index that moved. A sum only falls when
    records actually went missing, which is the only thing being asked.

    Only DICT elements are descended into. A list of lists is a list of values,
    not of records — Writer's and Academics' formatting spans are [start, end,
    tag] triples, and Tasks' project list is [name, colour] pairs — and losing
    any of them already shows up in the count of the list that holds them.
    Walking into them instead cost 240,000 extra visits and 200ms on one term of
    heavily formatted notes, to learn nothing that the parent count did not
    already say. `depth` bounds a store nested far deeper than any app writes."""
    if out is None:
        out = {}
    if depth > 12:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list, tuple)):
                _record_counts(v, "%s/%s" % (path, k) if path else str(k),
                               out, depth + 1)
    elif isinstance(obj, (list, tuple)):
        out[path] = out.get(path, 0) + len(obj)
        child = (path + "/*") if path else "*"
        for v in obj:
            if isinstance(v, dict):
                _record_counts(v, child, out, depth + 1)
    return out


def _loses_records(new, old):
    """True when any list in `old` comes back SHORTER in `new` — i.e. records
    the user had are missing, whatever else grew."""
    try:
        a, b = _record_counts(new), _record_counts(old)
    except Exception:
        return False
    return any(a.get(k, 0) < n for k, n in b.items())


def _bak_would_shrink(bak, raw):
    """True when refreshing `bak` with `raw` would replace a fuller copy of the
    user's work with an emptier one, in which case the caller must keep the one
    already on disk.

    THE BUG THIS EXISTS FOR — loss on the SECOND open, which the one-open tests
    could not see: a store that is valid JSON in a shape the app no longer
    recognises reads as "no data". The app opens blank and its close-time save
    writes that blankness over the store — but the refresh in preserve_damaged
    has just copied the user's real bytes to <store>.bak, so nothing is lost
    yet, and every open+close test passes.

    Then the person opens the app a second time. That is a fresh process with an
    empty _BACKED_UP, and the store parses now (it holds the blank state), so
    the refresh runs again and writes the BLANK store over the .bak — the only
    remaining copy. Two opens and two closes, no user action at all, and an
    address book, a diary or a manuscript is gone for good.

    Keeping the fuller copy is always the safe direction. The worst it can cost
    is a hidden .bak older than someone expected; the alternative costs the only
    copy of their work.

    WEIGHT ALONE IS NOT ENOUGH, and this is not theoretical — it was measured in
    Academics. _payload_weight collapses the whole store to one number, so a
    store that LOST records can still outweigh the copy that has them whenever
    the app writes more fields per record than it reads back. Academics'
    _save_to_disk decorates every homework record with a derived "course" (and
    every class with a derived "name") that its loader ignores, so 200 saved
    assignments weighed 604 and the 260 the user actually had weighed 523. The
    guard saw growth, refreshed the .bak, and 60 assignments went from
    recoverable to gone on the second open.

    So ask the question that actually matters — "are there fewer records than
    there were?" — alongside the weight, and keep the old copy if EITHER says
    this one is poorer. A store that legitimately shrinks (the user cleared
    their completed homework) simply keeps a slightly older recovery copy, which
    is the outcome this whole mechanism exists to provide."""
    try:
        with open(bak, "r", encoding="utf-8") as fh:
            old = fh.read()
    except OSError:
        return False                  # no previous backup, nothing to protect
    try:
        new_obj, old_obj = json.loads(raw), json.loads(old)
    except (ValueError, UnicodeDecodeError):
        return False                  # an unreadable .bak is not worth keeping
    if _loses_records(new_obj, old_obj):
        return True
    return _payload_weight(new_obj) < _payload_weight(old_obj)


def preserve_damaged(path):
    """Move an unreadable store aside so its bytes survive being overwritten.

    THE BUG THIS EXISTS FOR: an app whose store fails to parse starts from empty
    and then, on its very next save (often the destroy-time flush), writes that
    empty state over the file. Opening and closing the app was enough to destroy
    a journal that still plainly contained the user's text. The write side was
    hardened long ago; the read side never was, and that is where every loss we
    found actually happened. There is no network and no cloud here — the file
    under the user's home IS the only copy, so it must never be replaced by
    something we could not read in the first place.

    Returns the quarantine path, or None when there was nothing to preserve
    (missing or healthy — the overwhelmingly common case, which costs one
    parse of a small file). A ZERO-BYTE store is damage, not absence: even an
    empty app state serialises as two bytes, so 0 bytes can only mean a
    truncated write or a disk-full create. It used to be waved through here
    ("nothing to preserve"), which silently erased the one fact the person
    could still learn — that the store was found emptied, and when. The
    durability matrix (task 031) caught it; the empty file now takes the
    same quarantine path as any other unreadable store."""
    try:
        if not os.path.isfile(path):
            return None
        if os.path.getsize(path) == 0:
            raise ValueError("zero-byte store")
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        json.loads(raw)
        # Parses fine, so this is a normal save. Keep ONE previous-good copy
        # before it is replaced. That covers the losses a damage check cannot
        # see: a store that is valid JSON but the wrong shape reads as "no
        # data", and the app then saves its empty state straight over the
        # user's real file. Detecting that generically is not possible — an
        # empty result is indistinguishable from a user who deleted
        # everything — but a previous version always makes it recoverable.
        # ONCE per file per process: the backup is "the version from before
        # this session touched it". Refreshing it on every save destroys the
        # thing it exists for — the first save after a bad read correctly
        # preserves the user's real data, and the second would then overwrite
        # that backup with the empty state we were trying to protect against.
        # It also keeps autosave to a single extra write per file per run.
        # ...and never refresh it with LESS than it already holds, or the second
        # open of a wrong-shape store overwrites the copy the first open just
        # saved. See _bak_would_shrink.
        if path not in _BACKED_UP:
            _BACKED_UP.add(path)
            if not _bak_would_shrink(path + ".bak", raw):
                try:
                    with open(path + ".bak", "w", encoding="utf-8") as bh:
                        bh.write(raw)
                except OSError:
                    pass                 # a backup is a bonus, never a blocker
        return None
    except (OSError, ValueError, UnicodeDecodeError):
        pass
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = "%s.damaged-%s" % (path, stamp)
    n = 2
    while os.path.exists(dest):
        dest = "%s.damaged-%s-%d" % (path, stamp, n)
        n += 1
    try:
        os.replace(path, dest)
        return dest
    except OSError:
        return None


def quarantine_unrecognized(path):
    """Move a store the app could not read any of its own content out of aside,
    AT LOAD TIME, before the app's first save can replace it. Returns the
    quarantine path, or None when there was nothing to move. Never raises.

    preserve_damaged covers the store that fails to PARSE. It deliberately
    cannot cover this one: valid JSON in a shape the app does not recognise
    parses perfectly, and only the app knows its own shape. The app then opens
    on its blank default and the close-time flush writes that blankness over the
    user's file.

    THE .bak IS NOT ENOUGH HERE, and the measurement that says otherwise is
    reading the FIRST open only. preserve_damaged keeps one previous-good copy
    guarded by _bak_would_shrink, which compares _payload_weight — a count of
    non-empty leaves. An app's blank default is not empty: Writer's is a page
    size, an orientation and four margins (weight 7), Screenplay's is a default
    title (1), Novel's is a seeded "Chapter 1" (5). A wrong-shape store holding
    one long paragraph of the user's prose in an unexpected key weighs 1. So the
    blank OUTWEIGHS the work, the guard sees no regression, and the SECOND open
    writes the blank state over the .bak — the only remaining copy. Two opens,
    two closes, no user action at all. Measured across all four writing apps:
    eight damaged shapes were destroyed on cycle 2 with the .bak guard in place.

    This is the shared implementation of the private `_quarantine` helpers
    sequencer.py, mealplanner.py and language.py each grew for the same reason.
    The <name>.damaged-<timestamp> name is preserve_damaged's, so there is one
    recovery convention on the disk and not two."""
    try:
        if not os.path.isfile(path):
            return None
        if os.path.getsize(path) == 0:
            raise ValueError("zero-byte store")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = "%s.damaged-%s" % (path, stamp)
        n = 2
        while os.path.exists(dest):
            dest = "%s.damaged-%s-%d" % (path, stamp, n)
            n += 1
        os.replace(path, dest)
        return dest
    except OSError:
        return None


def _fsync_dir(d):
    """Durably record a rename. fsync on the file persists its contents; the
    directory entry the rename created needs its own fsync, or a power cut in
    the commit window can roll the store back to the previous version."""
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def atomic_write_text(path, text):
    """The plain-text twin of atomic_write_json, for the Save paths that write a
    document rather than a store (Writer's .txt, Screenplay's .fountain).

    Those used open(path,"w"), which truncates the destination before a single
    new byte arrives: a save that could not complete left an 11k finished
    document as 8k of nothing, with the original gone. Same temp + fsync +
    replace guarantee as the JSON writer."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    _reap_stale_tmp(d)
    fd, tmp = tempfile.mkstemp(prefix=".nbw-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(d)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_failure_reason(exc, path=None):
    """A sentence a person can act on, for a save that did not happen.

    A silent failed save is the worst outcome this OS can produce: the app
    carries on showing work that is no longer anywhere, and the file keeps
    whatever the last write that DID succeed put there -- so the first thing
    you entered survives and everything after it appears to vanish on close.
    Every caller that ignores a False from its save is one of these waiting to
    happen; this at least lets the ones that ask say something true."""
    import errno as _errno
    no = getattr(exc, "errno", None)
    if no == _errno.ENOSPC:
        return _t("The disk is full, so this could not be saved.")
    if no in (_errno.EROFS, _errno.EACCES, _errno.EPERM):
        return _t("This location is read-only, so this could not be saved.")
    if no == _errno.EDQUOT:
        return _t("There is no room left for new files, so this could not "
                  "be saved.")
    return _t("This could not be saved.")


def atomic_write_json(path, obj, indent=None, ensure_ascii=True):
    """Serialise `obj` as JSON to `path` crash-safely, shared by every app that
    auto-persists user data.

    A bare open(path,"w")+json.dump truncates the destination BEFORE the new
    bytes stream in, so a kill / power-loss mid-write leaves the user's ONLY copy
    truncated and the next json.load raises — silently wiping the whole document.
    Instead write a fresh same-directory temp file, flush + fsync it to durable
    storage, then os.replace(tmp, path): on one filesystem that rename is atomic,
    so a crash leaves either the old complete file or the new complete file,
    never a half-written one. Raises on failure (after removing the temp file) so
    callers keep their own try/except and status reporting."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    _reap_stale_tmp(d)
    # Never overwrite bytes we could not read. Doing this HERE rather than in
    # each app's load path covers all ~22 persistence sites at once, including
    # any added later, and cannot be forgotten by a new app.
    preserve_damaged(path)
    fd, tmp = tempfile.mkstemp(prefix=".nbw-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=indent, ensure_ascii=ensure_ascii)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(d)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# =====================================================================
#  Typing typography (shared by the writing apps)
# =====================================================================
# A keyboard only carries the typewriter marks, so a page typed on one is set
# with "straight" quotes and hyphen-hyphen dashes - the first thing that gives
# an amateur page away. Fixed as the writer types, the way every word processor
# does it: a quote opens after a space, a line start or an opening bracket, and
# closes otherwise. Writer, Novel and Journal each carried a private copy of
# this rule, and they had already drifted (only Writer's treated a non-breaking
# space as an opening context). One implementation now.
SMART_QUOTES = {'"': ("“", "”"), "'": ("‘", "’")}
# The non-breaking space is written \u00a0 rather than typed: as a literal
# character it is invisible in an editor, which is exactly how the three private
# copies came to disagree about whether it was in this set at all.
OPENS_AFTER = " \t\n\u00a0([{—–“‘/"


def smart_replacement(prev_char, text):
    """The typographic form of `text` typed after `prev_char`, or None to leave
    it alone. `prev_char` is "" at the very start of the text."""
    if len(text) != 1:
        return None                       # a paste, not a keystroke
    if text in SMART_QUOTES:
        opening, closing = SMART_QUOTES[text]
        return opening if (prev_char == "" or prev_char in OPENS_AFTER) \
            else closing
    if text == "-" and prev_char == "-":
        return "—"                   # -- becomes an em dash
    return None


# =====================================================================
#  Undo / redo — the shared checkpoint history
# =====================================================================
# GTK3's TextBuffer has no undo of its own (can_undo/undo arrived in GTK4), so
# every editor here has to keep its own. Writer proved the shape: rather than
# recording individual edit operations - which cannot express "the chapter list
# changed" or "the whole document was replaced" - snapshot the document state
# the app ALREADY serialises for its autosave, on a debounce while typing and
# immediately before anything structural. One mechanism then covers typing,
# formatting, deleting a chapter/entry/lecture and File > Open alike, because
# all of them end in a different serialised state.
#
# THE BUG THIS EXISTS FOR: Journal, Novel, Academic Notes and Screenplay had no
# undo at all. A select-all-then-type in Journal replaced years of diary
# entries, and there is no network and no cloud on this machine - the file
# under the user's home is the only copy. Ctrl+Z is not a nicety here.
#
# COST, measured, do not re-derive: a snapshot is one serialise (Writer 1ms on
# a 40,000-word document, Novel 1ms on a 31-chapter/90,000-word manuscript) and
# it only ever runs on the 600ms debounce or a structural edit, never on the
# keystroke path. Memory is the real risk - Writer measured 61 snapshots of
# that document at 12MB peak RSS - so two things bound it: consecutive
# snapshots SHARE their unchanged strings (see _share_strings), and the depth
# is capped against a byte budget as well as a count.

_UNDO_LIMIT = 100          # Writer's measured cap; deeper is not useful
_UNDO_MIN = 8              # never fewer than this, however large the document
_UNDO_BUDGET = 12 << 20    # bytes of snapshot text to keep, worst case
_UNDO_DEBOUNCE = 600       # ms of quiet that ends one "typing" undo step


def _share_and_weigh(new, old):
    """Point `new`'s strings at the equal ones in `old`, and return
    `(new, bytes)` - the bytes this snapshot ADDS over the one before it.

    A snapshot re-serialises the WHOLE document, so a 30-chapter manuscript
    produces 30 fresh copies of its text every time even though the writer only
    touched one chapter. Sharing the untouched ones makes each further
    checkpoint cost roughly what actually changed: measured on a 31-chapter,
    90,000-word manuscript, 200 settled checkpoints cost 0.3MB rather than the
    100MB the same history would have taken re-copying the book each time.

    Only strings are shared, never containers: strings are immutable, so an
    alias cannot be edited out from under an older snapshot, whereas a shared
    list or dict could be. `new` is the structure the app just built for us, so
    editing it in place here is safe.

    The weight it returns is what the depth budget is charged, so the budget
    tracks what the history really holds instead of the document's full size.
    An estimate is enough for that: the user's text dominates by orders of
    magnitude, so strings are measured and containers counted as a constant.
    `old=None` weighs the whole thing, which is what the first snapshot wants."""
    if isinstance(new, str):
        if isinstance(old, str) and new == old:
            return old, 0
        return new, len(new) + 48
    # Containers are never shared, so their own overhead is charged every time.
    # It is not negligible: a journal snapshot is hundreds of small dicts whose
    # text is entirely shared, and counting them as free let the history grow
    # past its budget. These are CPython's actual shapes, near enough.
    if isinstance(new, list):
        total = 56 + 16 * len(new)
        for i, v in enumerate(new):
            ov = old[i] if isinstance(old, list) and i < len(old) else None
            new[i], w = _share_and_weigh(v, ov)
            total += w
        return new, total
    if isinstance(new, dict):
        total = 64 + 32 * len(new)
        for k, v in list(new.items()):
            ov = old.get(k) if isinstance(old, dict) else None
            new[k], w = _share_and_weigh(v, ov)
            total += w
        return new, total
    return new, 32


class UndoHistory:
    """Checkpoint undo/redo over a document the app can serialise.

    Wire it up with the two functions the app already has:

        self.undo = nbapp.UndoHistory(self._serialize, self._restore_snapshot)

    then, from the buffer's "changed" handler, `self.undo.touch()` (cheap - it
    only re-arms a timer), and around anything structural:

        self.undo.checkpoint("Delete Chapter")   # before
        ...the destructive edit...
        self.undo.commit()                       # after

    `checkpoint` flushes any half-finished typing step so the edit becomes its
    own step, and remembers what to call it; `commit` records the result. Both
    calls are needed because a structural edit often changes no text buffer at
    all, so nothing would otherwise capture the state it left behind.

    A snapshot is whatever the app's serialiser returns. Keys starting with "_"
    are treated as volatile (Writer's "_caret" is the reason): they ride along
    and are handed back on restore, but two states differing only in those are
    the same state, so moving the cursor never consumes an undo step.

    The restore callback receives a stored snapshot and must NOT keep a
    reference to it or to anything inside it that it will later mutate - the
    history keeps that object for as long as the step exists."""

    def __init__(self, snapshot, restore, typing_label="Typing"):
        self._snapshot = snapshot
        self._restore = restore
        self._typing_label = typing_label
        self._hist = []            # [[state, label], ...] oldest first
        self._hi = -1              # index of the state currently on screen
        self._timer = None         # pending typing checkpoint
        self._label = None         # what the edit in flight should be called
        self.busy = False          # True while restoring (apps guard on this)
        # The state that is on disk, for is_dirty(). Held BY VALUE rather than
        # as a history index, so trimming an old step or dropping a redo tail
        # cannot silently re-point it at the wrong document.
        self._saved = None
        self._saved_known = False

    # -- taking checkpoints --
    def _take(self):
        return self._snapshot()

    @staticmethod
    def _stable(state):
        """The part of a snapshot that decides whether it is a NEW state."""
        if not isinstance(state, dict):
            return state
        return {k: v for k, v in state.items() if not k.startswith("_")}

    def _push(self, label):
        if self.busy:
            return
        try:
            state = self._take()
        except Exception:
            return                 # a snapshot must never break an edit
        prev = self._hist[self._hi][0] if self._hi >= 0 else None
        state, added = _share_and_weigh(state, prev)
        if prev is not None and self._stable(state) == self._stable(prev):
            # Same document, so this is not a step of its own - keep the newer
            # volatile part (the caret) and leave the depth alone.
            self._hist[self._hi][0] = state
            return
        del self._hist[self._hi + 1:]          # a new edit drops the redo tail
        self._hist.append([state, label])
        self._hi = len(self._hist) - 1
        self._trim(added)

    def _trim(self, added):
        """Bound the history by count AND by bytes, so a long document keeps a
        useful number of steps without the history outgrowing the document.

        `added` is what THIS step cost after string sharing, which is the right
        thing to budget against: editing one chapter of a book adds a chapter,
        not a book, and charging the full document would cut the history to a
        handful of steps for memory that is never actually used."""
        limit = max(_UNDO_MIN, min(_UNDO_LIMIT, _UNDO_BUDGET // max(added, 1)))
        while len(self._hist) > limit:
            self._hist.pop(0)
            self._hi -= 1

    def reset(self):
        """Forget everything and take a fresh baseline. For the paths that hand
        the app a different document (first load, File > Open on a NEW file)
        where the previous document's steps would be meaningless."""
        self.cancel()
        self._hist = []
        self._hi = -1
        self._label = None
        self._saved = None
        self._saved_known = False
        self._push(None)

    # -- where the document last matched the file --
    def mark_saved(self):
        """The document on screen has just been written to disk.

        Apps flip a _file_dirty flag on every edit and clear it on save, which
        is only half true: undo the edit and the page matches the file again,
        but the flag stays set, so Close and New keep demanding a Discard
        confirm for work that is not at risk — and a user who is told that
        often enough stops reading it. Recording the saved state here lets
        is_dirty() answer relative to the file instead of "anything ever
        happened"."""
        self.flush()               # a save lands the sentence being typed
        if self._hi >= 0:
            self._saved = self._stable(self._hist[self._hi][0])
            self._saved_known = True
        else:
            self._saved = None
            self._saved_known = False

    def is_dirty(self):
        """True when the document differs from the last mark_saved() point.

        Errs towards dirty: typing still inside the debounce, a history that
        has never been marked, and any state it cannot compare all count as
        unsaved, because over-reporting costs one confirmation dialog and
        under-reporting costs the work."""
        if self._timer is not None:
            return True
        if not self._saved_known:
            return self._hi > 0
        if self._hi < 0:
            return True
        return self._stable(self._hist[self._hi][0]) != self._saved

    def touch(self):
        """An ordinary edit happened. Re-arms the typing checkpoint; a burst of
        typing therefore collapses into ONE undo step that ends when the
        keyboard goes quiet. Deliberately trivial - this is on the keystroke
        path of every writing app in the OS."""
        if self.busy:
            return
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(_UNDO_DEBOUNCE, self._fire)

    def _fire(self):
        self._timer = None
        self._push(self._typing_label)
        return False

    def flush(self):
        """Land any half-finished typing step right now."""
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = None
            self._push(self._typing_label)

    def checkpoint(self, label=None):
        """About to do something structural: close the typing step so the edit
        stands alone, and remember what to call it. `label` may carry the
        trailing ellipsis of the menu item it came from ("Delete Entry...") -
        it is trimmed for display, so the caller can pass the label it already
        has instead of maintaining a second copy of the wording."""
        self.flush()
        self._label = label

    def commit(self):
        """The structural edit is done - record the state it left behind.

        Any typing checkpoint the edit itself armed (an insert does emit
        "changed") is dropped rather than flushed: the state being pushed here
        already includes it, and letting it fire later would relabel this step
        as ordinary typing."""
        self.cancel()
        label, self._label = self._label, None
        self._push(label)

    def cancel(self):
        """Drop a pending typing checkpoint (window closing)."""
        if self._timer:
            try:
                GLib.source_remove(self._timer)
            except Exception:
                pass
            self._timer = None

    # -- moving through the history --
    def can_undo(self):
        return self._hi > 0 or self._timer is not None

    def can_redo(self):
        return self._hi < len(self._hist) - 1

    def _label_at(self, i):
        if not (0 <= i < len(self._hist)):
            return None
        label = self._hist[i][1]
        if not label:
            return None
        # Translate first, THEN trim: the catalogs are keyed on the menu label
        # the caller passed, ellipsis and all. Only spaces and the ellipsis are
        # stripped - a full stop can be part of a name ("Paren.").
        return _t(label).rstrip(" …")

    def undo_label(self):
        """What Ctrl+Z would undo, already translated, or None."""
        if self._timer is not None:
            return _t(self._typing_label)
        return self._label_at(self._hi)

    def redo_label(self):
        return self._label_at(self._hi + 1)

    def undo(self):
        # Anything still inside the typing debounce is landed first, so undo
        # steps back over the sentence just typed instead of ignoring it (and
        # so redo can bring it back).
        self.flush()
        if self._hi <= 0:
            return False
        self._hi -= 1
        return self._apply()

    def redo(self):
        if self._hi >= len(self._hist) - 1:
            return False
        self._hi += 1
        return self._apply()

    def _apply(self):
        self.busy = True
        try:
            self._restore(self._hist[self._hi][0])
        finally:
            self.busy = False
        return True


def undo_menu_items(hist):
    """The Undo / Redo pair for an app's Edit menu.

    Each names the action it would reverse ("Undo Delete Chapter") so the
    history is legible rather than a leap of faith, and greys out with a
    callback of None when there is nothing to reverse - the convention
    menu_items() already uses. Shared so all four editors word it identically.

    The wording and the accelerators come from nbcommands, so the Undo and
    Redo labels are spelled in exactly one place in the OS."""
    return [
        nbcommands.dynamic_item("edit.undo", hist.undo_label(),
                                hist.can_undo(), hist.undo),
        nbcommands.dynamic_item("edit.redo", hist.redo_label(),
                                hist.can_redo(), hist.redo),
    ]


def undo_keys(hist, ev):
    """True when `ev` is an undo/redo shortcut, which has now been performed.

    Ctrl+Z, plus BOTH redo conventions - Ctrl+Shift+Z (what this OS prints in
    its menus) and Ctrl+Y (what a user arriving from Windows will try first).
    Deliberately a free function rather than base-class key handling: nbapp is
    shared by all 28 apps and several of them are not editors."""
    if not (ev.state & Gdk.ModifierType.CONTROL_MASK):
        return False
    shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
    if ev.keyval in (Gdk.KEY_z, Gdk.KEY_Z):
        hist.redo() if shift else hist.undo()
        return True
    if ev.keyval in (Gdk.KEY_y, Gdk.KEY_Y) and not shift:
        hist.redo()
        return True
    return False


def _logo_pixbuf():
    global _LOGO_PB
    if _LOGO_PB is None:
        try:
            from gi.repository import GdkPixbuf
            _LOGO_PB = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                "/opt/notebook/logo.png", 34, 16, True)
        except Exception:
            _LOGO_PB = False
    return _LOGO_PB or None

# The desktop home (Finder + widget column) hides itself whenever a fullscreen
# app owns the screen; it watches /tmp/nb-app-active. Every app inherits
# AppWindow, so owning the flag HERE means every app — however it was launched
# (from the Finder/shell, or directly by the session, e.g. the auto-launched
# installer) — correctly hides the home. Ref-counted across processes via a
# per-PID marker dir, so closing one of several open apps doesn't prematurely
# reveal the home, and a crashed app's stale marker is reaped on the next check.
# Both live in /tmp on purpose: it is a tmpfs, so a reboot clears it. That is
# what keeps a RECYCLED pid from being mistaken for a live app and refusing to
# launch it. The names carry the NB_HOME they belong to, so a test running an
# app against a sandbox home cannot collide with a real one -- on the machine
# NB_HOME is exported once by session.sh, so every app agrees on one directory
# and the single-instance guard still sees its siblings.
def _app_scope():
    home = os.environ.get("NB_HOME") or ""
    if home in ("", "/root"):
        return ""
    import hashlib
    return "-" + hashlib.md5(home.encode("utf-8", "replace")).hexdigest()[:8]


_APP_SCOPE = _app_scope()
_APP_FLAG = "/tmp/nb-app-active" + _APP_SCOPE
_APP_DIR = "/tmp/nb-apps" + _APP_SCOPE
# Public aliases: the desktop and the Finder watch these, and must never
# re-derive the paths themselves or the two halves can disagree.
APP_FLAG = _APP_FLAG
APP_DIR = _APP_DIR


def _refresh_app_flag():
    try:
        alive = False
        for name in os.listdir(_APP_DIR):
            if name.endswith(".mapped"):
                # the launch-continuity beacon (see AppWindow._map_beacon):
                # reaped here with its process, exactly like the pid marker
                if not os.path.isdir("/proc/" + name[:-len(".mapped")]):
                    try:
                        os.remove(os.path.join(_APP_DIR, name))
                    except OSError:
                        pass
                continue
            if not name.isdigit():
                continue
            if os.path.isdir("/proc/" + name):
                alive = True
            else:
                try:
                    os.remove(os.path.join(_APP_DIR, name))
                except OSError:
                    pass
        if alive:
            open(_APP_FLAG, "w").close()
        elif os.path.exists(_APP_FLAG):
            os.remove(_APP_FLAG)
    except Exception:
        pass


def _register_app(win=None):
    try:
        os.makedirs(_APP_DIR, exist_ok=True)
        # The marker carries the MODULE NAME, not just the pid, so
        # claim_single_instance() below can tell whether the app already open is
        # this one or a different one.
        with open(os.path.join(_APP_DIR, str(os.getpid())), "w") as _fh:
            _fh.write(_app_module_name(win) + "\n")
        _refresh_app_flag()
    except Exception:
        pass


def _app_module_name(win=None):
    """Which app this is, e.g. "academics".

    Taken from the WINDOW CLASS's module, not from sys.argv[0]: the same app
    can be started as academics.py, through a wrapper, or with a different
    working directory, and all of those must count as the same app. argv[0]
    says something different in each case, which would let a second copy
    through exactly when it matters.
    """
    if win is not None:
        try:
            mod = type(win).__module__
            if mod and mod != "__main__":
                return mod
        except Exception:
            pass
    try:
        return os.path.splitext(os.path.basename(sys.argv[0]))[0] or "?"
    except Exception:
        return "?"


def claim_single_instance(win=None):
    """Exit at once if this app is ALREADY open in another process.

    THE BUG THIS EXISTS FOR — a lost update that read exactly like deletion.
    Nothing stopped a second copy of an app being started, and there are two
    routes to every app that has a desktop tile: the Finder AND the tile. Open
    Academics, add a class, then click the Classes tile: a SECOND Academics
    starts and reads the store as it is at that moment. Keep working in the
    first, then close the second -- with Esc or the snail logo, both of which
    simply close -- and the second writes ITS stale model over the file. Every
    class, assignment or entry added after the second copy opened is gone, and
    the one thing that survives is whatever had been saved before it started.

    That is why it never happened in Cookbook or Novel: they have no tile, so
    there is only one way in and rarely two copies.

    This is a single-screen appliance where the front app is fullscreen: two
    copies of one app is never something anybody asked for, and the older copy
    is the one already on screen. So the newcomer stands down. It exits with
    os._exit so no atexit hook, no destroy handler and above all no SAVE can
    run on the way out -- a tidy shutdown here is precisely what would destroy
    the data.
    """
    me = _app_module_name(win)
    mine = str(os.getpid())
    try:
        names = os.listdir(_APP_DIR)
    except OSError:
        return
    for name in names:
        if not name.isdigit() or name == mine:
            continue
        if not os.path.isdir("/proc/" + name):
            continue                      # dead; _refresh_app_flag reaps it
        try:
            with open(os.path.join(_APP_DIR, name)) as fh:
                if fh.read().strip() == me:
                    os._exit(0)
        except OSError:
            continue


def _unregister_app():
    try:
        os.remove(os.path.join(_APP_DIR, str(os.getpid())))
    except OSError:
        pass
    _refresh_app_flag()


def day_ordinal(day):
    """Days since 1970-01-01 for a "YYYY-MM-DD" key, or None if it is not one.

    For asking whether two dates are consecutive, which is how anything built
    on a run of days (a streak, a gap in a log) has to be counted.

    Plain civil-date arithmetic — the standard days-from-civil algorithm — for
    two reasons this OS has already paid for. time.strptime() pulls in the
    stdlib `calendar` module, which the Calendar app's calendar.py shadows on
    PYTHONPATH, so it crashes the app that calls it. And stepping back a day by
    subtracting 86400 from a timestamp skips or repeats a day across a
    daylight-saving change, twice a year, in whichever direction hurts.
    """
    try:
        y, m, d = (int(p) for p in str(day).split("-"))
    except (TypeError, ValueError):
        return None
    if not 1 <= m <= 12:
        return None
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def screen_size():
    """The primary monitor's real pixel size. Real hardware panels are NOT
    assumed to be 1920x1080 — a fixed assumption pushes full-screen scrims and
    centred dialogs off a smaller panel. Modal overlays should size to the live
    window allocation and fall back to THIS, never to a hardcoded 1920x1080."""
    try:
        d = Gdk.Display.get_default()
        mon = d.get_primary_monitor() or d.get_monitor(0)
        g = mon.get_geometry()
        if g.width > 1 and g.height > 1:
            return g.width, g.height
    except Exception:
        pass
    return 1920, 1080


def os_release_field(key):
    """Return one field (e.g. "VERSION", "PRETTY_NAME") from /etc/os-release,
    or None if the file or key is absent. Values are unquoted per the
    freedesktop os-release format."""
    try:
        with open("/etc/os-release") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def nb_version():
    """Release version: /etc/os-release VERSION if present, else NB_VERSION."""
    return os_release_field("VERSION") or NB_VERSION


def nb_pretty_name():
    """Pretty OS name: /etc/os-release PRETTY_NAME if present, else the plain
    product name. The release number is deliberately NOT part of the name the
    user sees — it lives in os-release VERSION for anything that needs it."""
    return os_release_field("PRETTY_NAME") or "Notebook OS"


# =====================================================================
#  Accessibility — one text size and one contrast choice, obeyed OS-wide
# =====================================================================
# Settings offered "Large text" and "High contrast" long before this, and both
# only ever restyled the Settings window: the CSS they installed was scoped to
# .setlabel / .setvalue / .settitle, class names that exist in no other app.
# Somebody who turns large text on because they cannot read comfortably needs
# it in Writer, in the Finder and in Journal — not in the one window where they
# set it. Every app imports nbapp before it builds anything, so this module is
# the single place a preference can reach all of them with no per-app code.
#
# There are two problems to solve and they need different tools.
#
# TEXT SIZE. The apps carry ~640 hand-tuned `font-size: Npx` declarations, and
# GTK cannot reach them from the outside. `px` is absolute — unlike `pt` it
# ignores gtk-xft-dpi entirely (measured: at 1.3x dpi a `34px` rule still
# computes 34px) — and a blanket `* { font-size: ... }` rule at USER priority
# overrides every app rule at once, flattening a 34px serif title and an 11px
# caption to the same size. That is why the old code refused to write one. So
# the numbers are rewritten as each sheet is loaded, in _a11y_css.
#
# What that rewrite does is RAISE A FLOOR, not apply a multiplier, and the
# choice was made by measuring rather than by taste. Every app has to keep
# laying out inside 1024x722 (the smallest panel we support: 768 minus the 46px
# desktop panel shell.py actually strut-reserves — docs/PAPER-PHYSICS.md
# §E3.6; an earlier revision of this note said 28px/740, which was wrong and
# hid an overflow), because GTK cannot shrink a window below its minimum and
# the overflow is simply unreachable on that hardware. Measured across all 28
# apps with tools/appshot.py:
#
#     multiplier 1.05  fits          multiplier 1.10  Cookbook 1037 wide
#     multiplier 1.15  Cookbook 1043 wide, 1.25 also GBA SDK 1056
#     floor 15px       fits (widest Cookbook 1004, tallest Video 725)
#     floor 16px       GBA SDK 1026 wide
#
# So the ceiling for a multiplier is 1.05 — five percent, which nobody can see,
# and it is the WORST five percent to spend: a multiplier inflates the 34px
# display titles and 20px headings that set each app's minimum width, while
# handing the 11px caption that is actually hard to read only 15% at a step
# that does not fit anyway. The floor spends the whole budget where the problem
# is. 465 of the 641 declarations in the OS are below 15px, so it lifts 73% of
# all styled text — 11px -> 15px (+36%), 12px -> +25%, 13px (the single biggest
# bucket, 134 uses) -> +15% — and leaves the display sizes, which no one is
# struggling to read, exactly as the designer set them. It costs Cookbook, the
# tightest app in the OS, four pixels of width.
#
# Text that NO css sizes — the Finder's file list, entry fields, tree views,
# tooltips — is covered separately by a `window { font-size: 15px; }` rule at
# SETTINGS priority (400). font-size inherits, so every unstyled descendant
# picks it up, while an app's own rule at APPLICATION priority (600) still
# wins where it exists. Deliberately anchored on `window` and not on `*`: `*`
# also matches the child label of a styled button, which would cut inheritance
# everywhere and undo the point of the setting in the first place.
#
# CONTRAST. Measured against WCAG 2.1, ink #1A1916 on paper #FCFBF8 is
# 16.99:1 — AAA wants 7:1, so the main text is already 2.4x past the strictest
# bar, and the old toggle's "make it #000000" bought 16.99 -> 20.29, a change
# nobody can see. The real failures are the quiet tiers the design uses to
# recede: #9A9484 (95 uses) is 2.92:1, #A39D8F 2.61:1, #8A857A 3.55:1 — all
# below the 4.5:1 AA floor, several below even the 3:1 large-text floor, and
# most of them set at 11-12px. The hairlines are 1.4-1.8:1 against a 3:1
# requirement for control boundaries (WCAG 1.4.11). So high contrast here
# deepens exactly those: each replacement is the ORIGINAL tone blended toward
# the OS's own ink until it clears the bar, so nothing new enters the palette —
# the quiet tier lands at 7.05:1 and the muted tier at 9.5:1 (both measured on
# #F4F2EC card, the stricter of the two surfaces), still ranked below ink's
# 15.7:1 so the hierarchy survives. The signage red is left alone: it passes AA
# at 5.12:1 and it is the one brand colour.
#
# Both transforms are identity at the defaults, so a machine where nobody has
# touched the setting behaves exactly as before.

# The floor, in px. 16 is the true wall (the GBA SDK lands 2px past 1024 there);
# 15 keeps ~20px of width and ~15px of height in hand for the languages whose
# strings run longer than English, which is the margin a hard 1024 panel needs.
TEXT_MIN = 15

# High contrast, as three tones. Derived, not invented: see the note above.
_HC_QUIET = b"#55514A"     # the recede-into-the-page tier -> 7.05:1 on card
_HC_MUTED = b"#413E38"     # the secondary-text tier       -> 9.52:1 on card
_HC_HAIR = b"#8F8C81"      # borders and rules             -> 3.01:1 on card

# Keyed on the tones the apps actually use (a sweep of every `color:` and
# border in de/*.py); anything not listed — ink, the signage red, the pale
# tones used as text ON a dark fill — is deliberately left untouched.
_HC_TEXT = {
    b"#9A9484": _HC_QUIET, b"#A39D8F": _HC_QUIET, b"#8A857A": _HC_QUIET,
    b"#9A958A": _HC_QUIET, b"#A79F8E": _HC_QUIET, b"#B9B4A8": _HC_QUIET,
    b"#B0AB9D": _HC_QUIET,
    b"#6E695E": _HC_MUTED, b"#79736A": _HC_MUTED, b"#57534B": _HC_MUTED,
    # Both of these are HAIRLINE tones, and every one of their six `color:`
    # uses in the OS is a 1px vertical separator in a toolbar (.fsep, .vsep,
    # .msep, .navsep) rather than type. They take the line tone, so high
    # contrast firms those rules up instead of drawing six near-black bars
    # through the toolbars.
    b"#D7D2C5": _HC_HAIR, b"#C9C4B6": _HC_HAIR,
    # hair-firm. It is a LINE tone and _HC_LINE already covers it there, but it
    # is reachable as TEXT too: the design-token pass conforms the drift tones
    # #B9B4A8 and #B0AB9D onto it, and both of those were TEXT keys above.
    # Without this entry a caption that used to be boosted would quietly stop
    # being boosted the moment it was conformed -- an accessibility regression
    # produced by a tidying change, which is the worst kind. As text on paper it
    # is about 2.3:1, so it belongs in the quiet tier.
    b"#B3AD9E": _HC_QUIET,
}
_HC_LINE = {
    b"#C9C4B6": _HC_HAIR, b"#C4BFB1": _HC_HAIR, b"#D7D2C5": _HC_HAIR,
    b"#B3AD9E": _HC_HAIR, b"#DED4C2": _HC_HAIR, b"#DDD8CB": _HC_HAIR,
    b"#D9D4C7": _HC_HAIR, b"#E0D8C4": _HC_HAIR, b"#D5D0C3": _HC_HAIR,
}
# `border-radius` and friends take lengths, not colours — never treat their
# values as a border colour.
_BORDER_METRIC = (b"border-radius", b"border-width", b"border-style",
                  b"border-spacing", b"border-image")

_FONT_PX = re.compile(rb"(font-size\s*:\s*)([0-9]*\.?[0-9]+)px")
_RULE = re.compile(rb"([^{}]*)\{([^{}]*)\}")
_DECL = re.compile(rb"([-a-zA-Z]+)\s*:\s*([^;{}]*)")
_HEX6 = re.compile(rb"#[0-9A-Fa-f]{6}")
# A greyed-out control says "not available" with exactly the pale tone this
# would otherwise deepen, so darkening it makes every disabled menu item,
# format button and check button in the OS look live. WCAG agrees: 1.4.3
# exempts inactive components from the contrast minimum. Six rules in de/ and
# shell.py depend on this, nbapp's own .nbmenu-item:disabled among them.
# Selectors whose colours high contrast must LEAVE ALONE, because their whole
# job is to look unavailable. Boosting them makes a greyed-out control read as
# live, which is worse than low contrast: WCAG 1.4.3 exempts inactive controls
# precisely because "you cannot use this" has to stay legible AS a state.
#
# The CLASS-based entries matter as much as the pseudo-classes. GTK only gives
# :disabled to widgets made insensitive, but this OS also dims things by adding
# a class -- illustrator's `.dim` group (7 rules), `.pipoff`, `.chip-off`,
# `.disabled`. That was harmless while those rules used off-palette tones no
# mapping table knew about; the design-token pass conformed them onto MAPPED
# tokens, at which point high contrast started rewriting them and an unavailable
# group would have rendered as though it were available. Found by a conformance
# agent reading its own diff, which is exactly the sort of second-order breakage
# a value-only change is not supposed to be able to cause.
_HC_SKIP = (b":disabled", b":insensitive",
            b".dim", b".disabled", b".pipoff", b".chip-off")

_A11Y = None                # (text floor px, contrast), read once from the store
_A11Y_SHEETS = []           # [(provider, original css)] for a live re-style
_A11Y_BASE = None           # our own SETTINGS-priority provider
_A11Y_LOAD = None           # Gtk's own load_from_data, before we wrapped it
_NAME_LOAD = None           # Gtk's own set_tooltip_text, before we wrapped it


def a11y_prefs():
    """(minimum text size in px, high contrast) as chosen in Settings; a
    minimum of 0 means "leave the type alone".

    Read once per process from the same store Settings writes, and the keys are
    the two the page has always saved — anyone who turned Large text on before
    this existed simply starts getting it. On the import path of every app, so
    it is one open() of a small file: no subprocess, no display connection and
    no GTK call."""
    global _A11Y
    if _A11Y is None:
        floor, contrast = 0, False
        try:
            home = os.environ.get("NB_HOME") or os.path.expanduser("~")
            with open(os.path.join(home, ".config", "notebook",
                                   "settings.json")) as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                contrast = bool(data.get("high_contrast", False))
                floor = TEXT_MIN if data.get("large_text") else 0
        except Exception:
            pass                      # no store, unreadable, wrong shape: plain
        _A11Y = (floor, contrast)
    return _A11Y


def _a11y_css(data, floor, contrast):
    """One app stylesheet, rewritten for the current accessibility settings.

    Colour substitution is PROPERTY-SCOPED, which is most of the safety
    argument: only a `color:` value is looked up in the text table and only a
    `border*` value in the line table, so a quiet tone used as a FILL (the muted
    progress bars in Settings and System Monitor, a scrollbar slider, a
    separator drawn as a 1px box) is never darkened into something else, and the
    pale tones that are text ON a dark fill are not in the tables at all. It is
    also SELECTOR-scoped, for the disabled states — see _HC_SKIP."""
    try:
        if floor:
            def _size(m):
                px = float(m.group(2))
                if px >= floor:
                    return m.group(0)
                return m.group(1) + str(floor).encode() + b"px"
            data = _FONT_PX.sub(_size, data)
        if contrast:
            def _decl(m):
                prop = m.group(1).lower()
                if prop == b"color":
                    table = _HC_TEXT
                elif prop.startswith(b"border") and prop not in _BORDER_METRIC:
                    table = _HC_LINE
                else:
                    return m.group(0)
                val = _HEX6.sub(
                    lambda h: table.get(h.group(0).upper(), h.group(0)),
                    m.group(2))
                return m.group(1) + b": " + val

            def _rule(m):
                sel, body = m.group(1), m.group(2)
                if any(s in sel for s in _HC_SKIP):
                    return m.group(0)
                return sel + b"{" + _DECL.sub(_decl, body) + b"}"
            data = _RULE.sub(_rule, data)
    except Exception:
        pass          # a stylesheet must load even if this cannot rewrite it
    return data


def _a11y_relevant(data):
    """True when `data` carries anything the settings can change. Sheets that
    do not (Writer builds one per colour swatch) are not worth remembering."""
    if b"font-size" in data:
        return True
    up = data.upper()
    return any(k in up for k in _HC_TEXT) or any(k in up for k in _HC_LINE)


def _a11y_hook():
    """Filter every stylesheet the OS loads through _a11y_css.

    Interception rather than an extra CSS layer, because no CSS layer can
    express "raise this rule if it is below 15px and otherwise leave it" — see
    the note above. It is installed at import so it also covers the sheets
    loaded outside an AppWindow (the Finder's, the desktop panel's, the widget
    column's), and it remembers the original bytes so Settings can restyle its
    own window the instant the switch is flipped, instead of asking the user to
    imagine the result."""
    global _A11Y_LOAD
    if _A11Y_LOAD is not None:
        return
    _A11Y_LOAD = original = Gtk.CssProvider.load_from_data

    def load_from_data(self, data, *args):
        if isinstance(data, (bytes, bytearray)):
            data = bytes(data)
            if _a11y_relevant(data):
                _A11Y_SHEETS.append((self, data))
                floor, contrast = a11y_prefs()
                data = _a11y_css(data, floor, contrast)
        return original(self, data, *args)

    Gtk.CssProvider.load_from_data = load_from_data


def name_control(widget, name):
    """Give a control a name assistive technology can read, and return it.

    For the controls a tooltip cannot cover: a custom DrawingArea, an icon-only
    button whose picture is its whole meaning but which has no tooltip, an image
    that is the label of something else. `Gtk.Label`-bearing widgets already
    name themselves from their text and need no call."""
    try:
        acc = widget.get_accessible()
        if acc is not None:
            acc.set_name(name)
    except Exception:                                             # noqa: BLE001
        pass                    # naming is never worth failing a window over
    return widget


def _name_hook():
    """Make every tooltip in the OS an accessible name as well.

    MEASURED: a Gtk.Button holding only a Gtk.Image reports
    `get_accessible().get_name() == None` however carefully its tooltip is
    written — GTK maps a tooltip to the ATK *description*, and the name comes
    from the label the button does not have. So the Finder toolbar, the media
    transport, the Illustrator dock and the sequencer's ~250 icon-only buttons
    were all reachable, all documented on hover, and all anonymous to anything
    reading the interface aloud. Constitution VII §2 asks for the tooltip AND
    the name; every app in the tree had written the tooltip and none had ever
    called get_accessible().

    Installed as a wrapper at import, the same technique (and for the same
    reason) as _a11y_hook: it fixes all of them in one place instead of asking
    28 apps to remember a second call, and it covers the surfaces that never
    build an AppWindow. A widget that already has a name — anything with a
    label, which GTK names for free — keeps it; this only fills the blanks."""
    global _NAME_LOAD
    if _NAME_LOAD is not None:
        return
    _NAME_LOAD = original = Gtk.Widget.set_tooltip_text

    def set_tooltip_text(self, text, *args):
        result = original(self, text, *args)
        if text:
            try:
                acc = self.get_accessible()
                if acc is not None and not (acc.get_name() or "").strip():
                    acc.set_name(text)
            except Exception:                                     # noqa: BLE001
                pass
        return result

    Gtk.Widget.set_tooltip_text = set_tooltip_text


def _default_font_px():
    """GTK's default font size in pixels — what text nobody styled comes out
    at. Here that is "Sans 10" = 13.3px, comfortably under the floor; read
    rather than assumed so raising a floor can never quietly SHRINK the base
    font if that default is ever changed."""
    try:
        name = Gtk.Settings.get_default().get_property("gtk-font-name")
        pt = float(name.rsplit(" ", 1)[1])
        return pt * 96.0 / 72.0            # CSS px are 96dpi by definition
    except Exception:
        return 0.0


def _a11y_base():
    """Our own sheet, for what the apps never style themselves.

    SETTINGS priority (400) sits above the Papertone theme (200) and below every
    app's own CSS (600), so this sets the floor without overriding a single
    hand-tuned rule."""
    global _A11Y_BASE
    floor, contrast = a11y_prefs()
    if not floor and not contrast and _A11Y_BASE is None:
        return                        # the default machine: nothing to install
    parts = []
    if floor and _default_font_px() < floor:
        # On `window`, not `*`: font-size inherits, so the toplevel is where a
        # floor reaches every unstyled descendant without cutting inheritance
        # underneath a widget the app HAS styled.
        parts.append("window { font-size: %dpx; }" % floor)
    if contrast:
        line = _HC_HAIR.decode()
        parts.append(
            "button, entry, spinbutton, spinbutton button, combobox button,"
            " check, radio, switch, scale slider, scrollbar slider,"
            " scrollbar.overlay-indicator slider"
            " { border-color: %s; }" % line)
        # Separators and scrollbar sliders are drawn as a fill, not a border.
        parts.append("separator { background-color: %s; }" % line)
        parts.append("scrollbar slider,"
                     " scrollbar.overlay-indicator:not(.dragging):not(.hovering)"
                     " slider { background: %s; }" % line)
    try:
        screen = Gdk.Screen.get_default()
        if screen is None:
            return
        if _A11Y_BASE is not None:
            Gtk.StyleContext.remove_provider_for_screen(screen, _A11Y_BASE)
            _A11Y_BASE = None
        if not parts:
            return
        prov = Gtk.CssProvider()
        # Gtk's own loader: this sheet is already written for the current
        # settings, so it must not be filtered again — and it must not join
        # the re-style register, where a toggle would keep resurrecting the
        # version it just replaced.
        _A11Y_LOAD(prov, "\n".join(parts).encode())
        Gtk.StyleContext.add_provider_for_screen(
            screen, prov, Gtk.STYLE_PROVIDER_PRIORITY_SETTINGS)
        _A11Y_BASE = prov
    except Exception:
        pass


def a11y_set(large, contrast):
    """Apply a new choice to THIS process, live.

    Settings calls it so the window the user is looking at changes under their
    hand — the only honest way to offer a text size, since the whole point is
    whether they can read it. Other running apps cannot be reached (there is no
    session bus here); they read the store when they next start, which is what
    the Settings page says."""
    global _A11Y
    _A11Y = (TEXT_MIN if large else 0, bool(contrast))
    for prov, css in list(_A11Y_SHEETS):
        try:
            # Gtk's own loader, NOT the wrapper: re-entering it here would
            # rewrite an already-rewritten sheet and register it a second time.
            _A11Y_LOAD(prov, _a11y_css(css, _A11Y[0], _A11Y[1]))
        except Exception:
            pass
    _a11y_base()


_a11y_hook()
_name_hook()
# Also at import, not only from install_css: the desktop panel, the widget
# column and the Finder style themselves without ever constructing an
# AppWindow, and a person who needs larger text needs it on the desktop most
# of all. Both calls are no-ops on a machine at the defaults.
_a11y_base()
APP_CSS = b"""
.nbapp { background: #FCFBF8; }
.nbapp .menubar { background: #F4F2EC; border-bottom: 1px solid #C9C4B6;
                  min-height: 46px; }
.nbapp .menubar * { font-family: "Nimbus Sans","Helvetica",sans-serif;
                    color: #1A1916; }
/* 6px, matching the `menuitem` radius the Papertone theme sets. This CSS loads
   at APPLICATION priority and therefore WINS over the theme, so a radius left
   at the old 2px here would have quietly kept the menu bar square while every
   other control in the OS moved to the new geometry -- the one strip of chrome
   visible in every app, on every screen, disagreeing with all of it. */
.nbapp .menuitem { padding: 4px 8px; border-radius: 6px; font-size: 15px;
                   background: transparent; border: 1px solid #F4F2EC;
                   box-shadow: none; }
.nbapp .menuitem:hover { background: #EAE3D2; color: #1A1916; }
.nbapp .menuitem.open { background: #EAE3D2; color: #1A1916; }
.nbapp .appname { font-weight: 700; }
.nbapp .menuitem.logo { padding: 3px 8px; }
.nbapp .clock { font-weight: 600; font-size: 14px; }
.nbapp .date  { font-size: 14px; color: #6E695E; }
/* dropdown menu (drawn inside the app window via an overlay, so it needs no
   separate popup window - reliable on the no-compositor stack). Warm-paper
   card with a darker-beige border and a beige selection - never black, per the
   design language - matching shell.py's system menu. */
/* The dropdown card. The shadow was `3px 3px 0` -- a HARD offset with no blur,
   cast down-and-right, which is the drop shadow of a 1990s word processor and
   the only place in the OS that lit the interface from the top-LEFT. Replaced
   with the paper elevation the theme defines for menus: two layers, warm ink,
   straight down, low alpha.
   Kept deliberately TIGHT (10px rather than the theme's 28px ambient) because
   this menu is drawn as an overlay INSIDE the app window rather than in its own
   popup window, so anything it casts is clipped to its own allocation -- a wide
   ambient blur would simply be cut off and read as a grey edge.
   NOTE: this whole block lives inside a BYTES literal, so it must stay ASCII
   and must never contain a triple quote. An em dash here is a SyntaxError that
   takes EVERY app down with it; ascii_css_check.py caught exactly that while
   this comment was being written, and then the comment itself broke the file a
   second time by quoting the delimiter it was warning about. */
.nbmenu { background: #F8F7F2; border: 1px solid #C9C4B6; padding: 5px;
          border-radius: 12px;
          box-shadow: 0 1px 2px rgba(26,25,22,0.10),
                      0 6px 10px rgba(26,25,22,0.12); }
.nbmenu-item { font-family: "Nimbus Sans","Helvetica",sans-serif;
               font-size: 14px; color: #1A1916; padding: 6px 20px 6px 12px;
               min-width: 190px; background: transparent; border: none;
               box-shadow: none; border-radius: 6px; }
.nbmenu-item:hover { background: #EAE3D2; color: #1A1916; }
.nbmenu-item:disabled { color: #9A9484; }
.nbmenu-sep { background: #D7D2C5; min-height: 1px; margin: 4px 10px; }
.nbabout { background: #FCFBF8; border: 1px solid #C9C4B6; padding: 30px 40px; }
.nbabout .a-name { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                   font-size: 24px; font-weight: 500; letter-spacing: 0.01em;
                   color: #1A1916; }
.nbabout .a-sub  { font-size: 13px; color: #6E695E; }
"""


def apply_direction():
    """Mirror the whole process for a right-to-left language.

    GTK reverses container packing, widget order and alignment for every widget
    built after this, so the Finder's sidebar moves to the right, breadcrumbs
    and table columns reverse, and labels align to the reading edge — all from
    one call, because the apps pack with pack_start/pack_end rather than
    absolute sides. Pango already handles bidi INSIDE a string, so Yiddish text
    lays out correctly whatever the container direction is; this is about the
    furniture around it.

    Called from install_css so every app inherits it without a code change.
    What it does NOT reach is anything drawn by hand with cairo — a draw
    handler gets a plain surface and no notion of direction — so custom canvases
    keep their left-to-right geometry and need their own attention."""
    try:
        import nbi18n
        if nbi18n.current_lang() in nbi18n.RTL:
            Gtk.Widget.set_default_direction(Gtk.TextDirection.RTL)
    except Exception:
        pass


# Direction belongs at import for exactly the same reason, and it was a real
# bug that it was not: apply_direction() was only reached from install_css(),
# but finder.py has its OWN install_css and never calls nbapp's — so under a
# right-to-left language the Finder alone kept a left-to-right layout while
# every other app mirrored. It LOOKED half-mirrored because Pango still lays
# text out RTL inside each label; only the containers stayed put. The desktop
# panel, the widget column and the splash have the same shape.
apply_direction()

class PaperSwitch(Gtk.Switch):
    """A GtkSwitch that paints itself in the papertone language.

    WHY SUBCLASS RATHER THAN BUILD ONE. GTK3's switch draws small "I" and "O"
    rocker marks inside its track. They are a skeuomorph of a physical rocker,
    they are the only glyphs in the interface nobody chose, and at 22px they are
    illegible anyway -- but they cannot be removed with CSS. Four candidate
    rules were rendered and measured (slider `color: transparent`,
    `-gtk-icon-source: none` on both nodes, `font-size: 0`); every one of them
    left the marks on screen, because the switch draws them itself.

    Writing a toggle from scratch would mean re-implementing state, the
    `state-set` and `notify::active` signals, keyboard activation, focus and
    accessibility -- and the three call sites in this OS use two DIFFERENT
    signals between them, so any mismatch would silently stop a setting from
    saving. Overriding do_draw changes the pixels and nothing else: every bit of
    behaviour is still GtkSwitch's.

    Deliberately not animated. GtkSwitch's own slide position is private, so the
    knob snaps -- which suits an interface whose motion rule is that nothing
    moves decoratively (see the MOTION section of the Papertone theme)."""

    # Papertone, matching the theme's switch rule so the two cannot drift.
    _TRACK_OFF = (0xDE / 255, 0xD4 / 255, 0xC2 / 255)
    _TRACK_ON = (0xC8 / 255, 0x34 / 255, 0x1E / 255)
    _EDGE_OFF = (0xC9 / 255, 0xC4 / 255, 0xB6 / 255)
    _EDGE_ON = (0xB1 / 255, 0x2D / 255, 0x19 / 255)
    _KNOB = (0xFC / 255, 0xFB / 255, 0xF8 / 255)
    _DISABLED = (0xEC / 255, 0xE8 / 255, 0xDE / 255)

    def do_draw(self, cr):
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        if w < 4 or h < 4:
            return True
        on = self.get_active()
        sensitive = self.get_sensitive()

        inset = 0.5                      # half a pixel: hairlines land crisp
        r = (h - 2 * inset) / 2.0
        cr.save()
        cr.set_line_width(1.0)

        # --- track: a pill -------------------------------------------------
        cr.new_sub_path()
        cr.arc(inset + r, inset + r, r, math.pi / 2, 3 * math.pi / 2)
        cr.arc(w - inset - r, inset + r, r, 3 * math.pi / 2, math.pi / 2)
        cr.close_path()
        if not sensitive:
            cr.set_source_rgb(*self._DISABLED)
        else:
            cr.set_source_rgb(*(self._TRACK_ON if on else self._TRACK_OFF))
        cr.fill_preserve()
        cr.set_source_rgb(*(self._EDGE_ON if on and sensitive
                            else self._EDGE_OFF))
        cr.stroke()

        # --- knob ----------------------------------------------------------
        kr = max(2.0, r - 2.0)
        kx = (w - inset - r) if on else (inset + r)
        cr.arc(kx, inset + r, kr, 0, 2 * math.pi)
        cr.set_source_rgb(*self._KNOB)
        cr.fill_preserve()
        cr.set_source_rgb(*self._EDGE_OFF)
        cr.stroke()

        # --- keyboard focus -------------------------------------------------
        # Only when focus is VISIBLE, i.e. reached by keyboard rather than by a
        # click -- the same rule the theme's global outline follows.
        if self.has_visible_focus():
            cr.set_source_rgb(*self._TRACK_ON)
            cr.set_line_width(2.0)
            cr.new_sub_path()
            cr.arc(inset + r, inset + r, r - 2, math.pi / 2, 3 * math.pi / 2)
            cr.arc(w - inset - r, inset + r, r - 2, 3 * math.pi / 2, math.pi / 2)
            cr.close_path()
            cr.stroke()

        cr.restore()
        return True                      # fully drawn; do not chain up


def _apply_motion_policy():
    """Turn GTK's animations on everywhere Reduced Motion has not said no.

    The Papertone theme defines a small motion vocabulary (90ms state feedback,
    140ms for a surface arriving) — see the MOTION section of its gtk.css. All of
    it runs through GTK's animation machinery, which this switch controls.

    This switch USED to be gated on NB_ACCEL, on the argument that a 90ms
    transition is six frames and a software renderer drops them unevenly — the
    control lurches and arrives late, reading as a struggling computer.
    PAPER-PHYSICS §0.5 Amendment 1 reverses that conclusion: the motion
    language is part of what the OS IS, most target machines (second-hand
    AMD/NVIDIA laptops) are on the software path, and shipping them a still OS
    was the wrong resolution of the conflict. The cost argument is answered
    where the cost lives — damage-limited drawing (Article F), short damped
    settles rather than long floats, and the frame-pacing gate that measures
    BOTH paths — never by switching the language off. NB_ACCEL remains a
    render-path fact (compositor gating, frame budgets); it is no longer a
    motion input.

    Reduced Motion (Settings > Accessibility) is now the ONE switch, and it is
    a person's choice rather than a probe's. nbmotion owns that preference so
    the shared motion engine and GTK's own animations cannot disagree; it is
    imported here rather than at module scope because this is on the import
    path of every app and a missing nbmotion must not be fatal."""
    reduced = False
    try:
        import nbmotion
        reduced = nbmotion.reduced_motion()
    except Exception:                                             # noqa: BLE001
        pass                     # no engine, no preference: motion stays on
    try:
        Gtk.Settings.get_default().set_property("gtk-enable-animations",
                                                not reduced)
    except Exception:                                             # noqa: BLE001
        # Never fatal: a missing setting must not stop an app from opening.
        pass


def install_css():
    global _CSS_DONE
    if _CSS_DONE:
        return
    apply_direction()
    # The accessibility floor (see TEXT_STEPS): a no-op at the defaults, and
    # placed here so every app gets it from the one call it already makes.
    _a11y_base()
    _apply_motion_policy()
    prov = Gtk.CssProvider()
    prov.load_from_data(APP_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), prov,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _CSS_DONE = True


def nudge_paint():
    """Deprecated no-op — the persistent xflushd.py daemon (started by
    session.sh) flushes every freshly-mapped window's first paint. Kept so
    existing callers stay valid."""
    return


SEP = ("-", None)   # a separator entry for menu_items()


def force_opaque_visual(win):
    """Pin a window to the screen's SYSTEM (opaque, 24-bit) visual.

    With a compositor running, GTK may give a toplevel the screen's RGBA
    (32-bit) visual. Every pixel the app does not explicitly paint then has
    alpha 0 and the compositor shows BLACK through it — which is how Writer and
    Novel came up with black canvases on real hardware while looking correct
    under QEMU, whose virtio-gpu screen exposes no RGBA visual for GTK to pick.

    Nothing in this design language is translucent, so the alpha channel buys us
    nothing and costs us this bug class. Pinning the system visual makes an
    unpainted pixel opaque paper instead of a hole. Safe with or without a
    compositor, and a no-op if the system visual cannot be resolved."""
    try:
        screen = win.get_screen() or Gdk.Screen.get_default()
        if screen is None:
            return
        vis = screen.get_system_visual()
        if vis is not None:
            win.set_visual(vis)
    except Exception:
        pass


class AppWindow(Gtk.Window):
    app_name = "App"
    # Sensible default; every real app sets its own. Only menus the base can
    # actually populate (File -> Close, Edit -> clipboard) are listed here, so
    # a bare subclass never shows an empty, do-nothing menu button.
    menus = ("File", "Edit")

    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        # FIRST, before this window costs anything and — critically — before
        # the subclass below loads a single store: if this app is already open
        # in another process, stand down. See claim_single_instance().
        claim_single_instance(self)
        install_css()
        # A fullscreen app owns the screen — hide the desktop home while we run.
        _register_app(self)
        self.connect("destroy", lambda *_: _unregister_app())
        self.set_decorated(False)
        # Opaque visual BEFORE the window is realised (set_visual only takes
        # effect pre-realise) — see force_opaque_visual.
        force_opaque_visual(self)
        self.get_style_context().add_class("nbapp")
        self.fullscreen()
        # Default to the REAL panel size, never a literal 1920x1080: on this
        # no-compositor stack a fullscreen() request can fail to apply, and a
        # hardcoded 1920x1080 default would then overflow a smaller panel
        # (1366x768, 1280x800, ...). screen_size() returns the live primary
        # monitor pixels (and only falls back to 1920x1080 as a last resort).
        _dw, _dh = screen_size()
        self.set_default_size(_dw, _dh)
        self.connect("map-event", self._assert_fullscreen)

        self._menu_buttons = {}     # menu name -> its Gtk.Button
        self._menu_layer = None     # open-dropdown overlay layer, if any
        self._menu_open = None      # name of the currently open menu

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.pack_start(self._menubar(), False, False, 0)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content.set_hexpand(True)
        self.content.set_vexpand(True)
        root.pack_start(self.content, True, True, 0)

        # overlay lets us draw dropdown menus INSIDE the window (no popup window)
        self._overlay = Gtk.Overlay()
        self._overlay.add(root)
        self.add(self._overlay)

        # Chinese Pinyin input method (Ctrl+Space). It intercepts keys at the
        # toplevel, so create it BEFORE the app's own key handler below — its
        # handler runs first and, when composing, keeps raw pinyin out of the
        # focused text widget. Guarded so a load failure never breaks an app.
        try:
            import nbpinyin
            self._pinyin = nbpinyin.PinyinIME(self)
        except Exception:
            self._pinyin = None

        # Press-and-hold accent palette (hold "e" -> é è ê ë ...). Connected
        # AFTER the Pinyin IME so a Chinese composition keeps the keystroke, and
        # BEFORE self._on_key so Esc closes an open palette instead of the app.
        # Guarded the same way: a load failure must never break an app.
        try:
            import nbdiacritics
            self._diacritics = nbdiacritics.DiacriticsPicker(self)
        except Exception:
            self._diacritics = None

        self.connect("key-press-event", self._on_key)

    def _assert_fullscreen(self, *_):
        self.fullscreen()
        # The launch-continuity beacon (G1): the Finder holds the screen
        # until this app's first map, watching for <pid>.mapped in the shared
        # app directory. Written once; reaped with the pid marker by
        # _refresh_app_flag when the process is gone.
        if not getattr(self, "_map_beacon_done", False):
            self._map_beacon_done = True
            try:
                os.makedirs(_APP_DIR, exist_ok=True)
                with open(os.path.join(_APP_DIR,
                                       "%d.mapped" % os.getpid()), "w"):
                    pass
            except Exception:                                     # noqa: BLE001
                pass
        return False

    # -- chrome --
    def _menubar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class("menubar")

        left = Gtk.Box(spacing=0)
        left.set_margin_start(14)
        # brand snail logo — the SAME asset the shell panel draws, so the logo
        # persists across every app's menu bar. A click still returns to the
        # Finder, exactly as the old red logo dot did.
        logo = Gtk.Button(); logo.set_relief(Gtk.ReliefStyle.NONE)
        logo.get_style_context().add_class("menuitem")
        logo.get_style_context().add_class("logo")
        logo.set_tooltip_text(_t("Back to Finder"))
        img = Gtk.Image()
        # Reserve the icon's box up front so the deferred decode below cannot
        # reflow the menu bar, then fill the pixbuf on an idle tick — AFTER the
        # window has mapped and painted its first frame. Decoding the source
        # PNG synchronously here would block that first paint on every launch;
        # loading it idle keeps the logo visually present without paying its
        # cost on the critical path.
        img.set_size_request(34, 16)

        def _fill_logo(_img=img):
            pb = _logo_pixbuf()
            if pb is not None:
                _img.set_from_pixbuf(pb)
            return False
        GLib.idle_add(_fill_logo)
        logo.add(img)
        logo.connect("clicked", lambda *_: self.close())
        left.pack_start(logo, False, False, 0)

        # app-name button (its own menu: About / Close)
        nb = Gtk.Button(label=_t(self.app_name)); nb.set_relief(Gtk.ReliefStyle.NONE)
        nb.get_style_context().add_class("menuitem")
        nb.get_style_context().add_class("appname")
        nb.connect("clicked", self._on_menu_click, self.app_name)
        left.pack_start(nb, False, False, 0)
        self._menu_buttons[self.app_name] = nb

        for m in self.menus:
            b = Gtk.Button(label=_t(m)); b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("menuitem")
            b.connect("clicked", self._on_menu_click, m)
            left.pack_start(b, False, False, 0)
            self._menu_buttons[m] = b
        bar.pack_start(left, False, False, 0)

        right = Gtk.Box(spacing=18)
        right.set_margin_end(20)
        self._date = Gtk.Label(); self._date.get_style_context().add_class("date")
        right.pack_end(self._date, False, False, 0)
        self._clock = Gtk.Label(); self._clock.get_style_context().add_class("clock")
        right.pack_end(self._clock, False, False, 0)
        bar.pack_end(right, False, False, 0)

        # cache the last strings so the once-a-second tick only touches a label
        # when its text actually changes (the clock changes each minute, the
        # date each day). set_text() unconditionally re-lays-out and queues a
        # resize even for identical text, so on this CPU-only software renderer
        # skipping the no-op keeps the timer from doing needless work forever.
        self._clock_txt = self._date_txt = None
        self._tick(); GLib.timeout_add_seconds(1, self._tick)
        return bar

    def _tick(self):
        now = time.localtime()
        clk = time.strftime("%H:%M", now)
        dat = time.strftime("%a %-d %b", now)
        if clk != self._clock_txt:
            self._clock_txt = clk
            self._clock.set_text(clk)
        if dat != self._date_txt:
            self._date_txt = dat
            self._date.set_text(dat)
        return True

    # -- dropdown menus --
    def _on_menu_click(self, button, name):
        if self._menu_open == name:
            self._close_menu()
        else:
            self._open_menu(name, button)

    def menu_items(self, name):
        """Return a list of (label, callback) entries for menu `name`.
        callback=None with label '-' is a separator; callback=None otherwise is
        a disabled item. Subclasses override and may call super().menu_items().

        The three menus the base class owns are built from nbcommands, so the
        labels, the ellipses, the accelerator text and the group separators
        cannot drift from the rest of the OS. Nothing here prints Ctrl+W or
        Ctrl+Q: _on_key suppresses both in the terminal, and an accelerator
        that is only sometimes bound must not be promised in a shared menu."""
        if name == self.app_name:
            return nbcommands.app_menu(self.app_name, self._about, self.close)
        if name == "File":
            return nbcommands.file_menu(self.close)
        if name == "Edit":
            return nbcommands.edit_menu(lambda: self._edit("cut"),
                                        lambda: self._edit("copy"),
                                        lambda: self._edit("paste"),
                                        lambda: self._edit("all"))
        return []

    def _open_menu(self, name, button):
        self._close_menu()
        items = self.menu_items(name)
        if not items:
            return
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        menu.get_style_context().add_class("nbmenu")
        for label, cb in items:
            if label == "-" and cb is None:
                sep = Gtk.Box(); sep.get_style_context().add_class("nbmenu-sep")
                menu.pack_start(sep, False, False, 0)
                continue
            # Translate the item HERE. _menubar already does _t() on the menu
            # TITLES, so without this a Spanish or French system showed
            # translated menu names opening entirely English dropdowns — in
            # every app in the OS. The catalogs have carried these keys
            # (including the shortcut column, "New    Ctrl+N" ->
            # "Nouveau    Ctrl+N") all along; nothing ever applied them.
            label = _t(label)
            it = Gtk.Button(label=label); it.set_relief(Gtk.ReliefStyle.NONE)
            it.get_style_context().add_class("nbmenu-item")
            child = it.get_child()
            child.set_xalign(0.0)
            if "✓" in label:
                # The shipped Nimbus Sans has no U+2713, so a plain tick renders as
                # a tofu box on real hardware; pin just the tick to DejaVu Sans
                # (the fix shell.py uses) so the active-item marker shows a real
                # check. Any app using the "✓ " menu convention benefits.
                child.set_markup(GLib.markup_escape_text(label).replace(
                    "✓", '<span face="DejaVu Sans">✓</span>'))
            if cb is None:
                it.set_sensitive(False)
            else:
                it.connect("clicked", lambda _b, fn=cb: (self._close_menu(), fn()))
            menu.pack_start(it, False, False, 0)

        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        alloc = self.get_allocation()
        _sw, _sh = screen_size()
        # Scrim spans the LIVE window (falling back to the real panel size,
        # never a literal 1920x1080), so it covers the whole screen at any
        # resolution instead of overflowing / under-covering a smaller panel.
        W = alloc.width if alloc.width > 1 else _sw
        H = alloc.height if alloc.height > 1 else _sh
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event", self._scrim_press)
        layer.put(scrim, 0, 0)
        try:
            bx = button.translate_coordinates(self._overlay, 0, 0)[0]
        except Exception:
            bx = 14
        # wrap in an EventBox so the dropdown gets its OWN GdkWindow — a
        # windowless Box drawn onto the parent surface never blits on this
        # no-compositor stack (same reason shell.py wraps its panel menus).
        menu_win = Gtk.EventBox()
        menu_win.add(menu)
        layer.put(menu_win, max(bx, 0), 46)
        self._overlay.add_overlay(layer)
        layer.show_all()
        # Keep the dropdown fully on-screen. Anchored under its button a wide or
        # tall subclass menu could run off the right or bottom edge of a small
        # panel; clamp its position to the live window using its measured
        # natural size so it never clips off-screen at any resolution.
        _min, nat = menu_win.get_preferred_size()
        mw = nat.width if nat.width > 1 else 0
        mh = nat.height if nat.height > 1 else 0
        x = min(max(bx, 0), max(W - mw, 0))
        y = 46 if (46 + mh) <= H else max(H - mh, 0)
        layer.move(menu_win, x, y)
        # keep the open dropdown IN FRONT of the app content. The Gtk.Overlay
        # already stacks overlay children above the content, but on this
        # no-compositor stack we raise the layer's (and the menu's) GdkWindow
        # to the top of the sibling stack so the popup always paints on top.
        try:
            lw = layer.get_window()
            if lw is not None:
                lw.raise_()
            mw = menu_win.get_window()
            if mw is not None:
                mw.raise_()
        except Exception:
            pass
        self._menu_layer = layer
        self._menu_open = name
        button.get_style_context().add_class("open")

    def _scrim_press(self, _w, ev):
        # The scrim covers the whole window, including the menu-bar row. A click
        # that lands on a menu-bar button should switch menus on the FIRST click
        # (as shell.py's panel does), not merely close the open one — otherwise
        # the scrim swallows it and the user has to click twice.
        for name, b in self._menu_buttons.items():
            xy = b.translate_coordinates(self._overlay, 0, 0)
            if xy is None:
                continue
            a = b.get_allocation()
            if xy[0] <= ev.x < xy[0] + a.width and xy[1] <= ev.y < xy[1] + a.height:
                if name == self._menu_open:
                    self._close_menu()
                else:
                    self._open_menu(name, b)
                return True
        self._close_menu()
        return True

    def _close_menu(self, *_):
        if self._menu_layer is not None:
            self._overlay.remove(self._menu_layer)
            self._menu_layer = None
        if self._menu_open is not None:
            b = self._menu_buttons.get(self._menu_open)
            if b is not None:
                b.get_style_context().remove_class("open")
            self._menu_open = None

    # -- Edit actions on the focused widget --
    def _edit(self, action):
        w = self.get_focus()
        try:
            if isinstance(w, Gtk.TextView):
                if action == "all":
                    w.emit("select-all", True)
                else:
                    w.emit(action + "-clipboard")
            elif isinstance(w, Gtk.Editable):
                if action == "cut":
                    w.cut_clipboard()
                elif action == "copy":
                    w.copy_clipboard()
                elif action == "paste":
                    w.paste_clipboard()
                elif action == "all":
                    w.select_region(0, -1)
        except Exception:
            pass

    # -- About overlay --
    def _about(self):
        self._close_menu()
        alloc = self.get_allocation()
        _sw, _sh = screen_size()
        W = alloc.width if alloc.width > 1 else _sw
        H = alloc.height if alloc.height > 1 else _sh
        layer = Gtk.Fixed()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event", lambda *a: (self._close_about(), True)[1])
        layer.put(scrim, 0, 0)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class("nbabout")
        nm = Gtk.Label(label=_t(self.app_name)); nm.get_style_context().add_class("a-name")
        nm.set_xalign(0.5)
        # Neutral, technical identity line: app name above, OS + version below.
        sub = Gtk.Label(label=nb_pretty_name())
        sub.get_style_context().add_class("a-sub"); sub.set_xalign(0.5)
        card.pack_start(nm, False, False, 0)
        card.pack_start(sub, False, False, 0)
        card_win = Gtk.EventBox()   # own GdkWindow so it blits (see _open_menu)
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        # Center on the actual window using the card's measured natural size, so
        # a long app name stays centered at any resolution (not a fixed 1920x1080).
        _min, nat = card_win.get_preferred_size()
        cw = nat.width if nat.width > 1 else 340
        ch = nat.height if nat.height > 1 else 140
        layer.move(card_win, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        self._about_layer = layer

    def _close_about(self):
        layer = getattr(self, "_about_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._about_layer = None
            return True
        return False

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            # Esc dismisses the About card first, then an open menu, and only
            # quits the app if neither overlay is showing.
            if self._close_about():
                return True
            if self._menu_open is not None:
                self._close_menu()
                return True
            self.close()
            return True
        # Ctrl+W / Ctrl+Q are the universal "close window" / "quit" accelerators a
        # user habituated to other desktops reaches for. The chrome is centralized,
        # so handling them here gives every app the shortcut for free. Placed AFTER
        # the Esc/overlay handling above, and skipped when a live shell owns Ctrl:
        # in the terminal Ctrl+W is readline word-rubout and Ctrl+Q is flow control,
        # so those keys must reach the shell, not tear the window down. (terminal.py
        # sets self.term only when a VTE shell is present; every other app leaves it
        # absent, so this guard is a no-op for them.)
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and ev.keyval in (Gdk.KEY_w, Gdk.KEY_W, Gdk.KEY_q, Gdk.KEY_Q)
                and getattr(self, "term", None) is None):
            self.close()
            return True
        return False


def run(app_cls):
    """Standalone entry: create the app window, run the GTK loop."""
    win = app_cls()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
