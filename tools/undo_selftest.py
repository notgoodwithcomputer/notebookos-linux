#!/usr/bin/env python3
"""Headless test for Undo / Redo in the writing apps (Novel, Journal, Academic
Notes, Screenplay) — the checkpoint history nbapp.UndoHistory gives them.

Drives the REAL handlers (typing through the buffer, the menu callbacks, the
key handler) and inspects the model afterwards, so it fails if any of the
destructive paths stops being covered. No painting.

    DISPLAY=:0 PYTHONPATH=<de> NB_HOME=<scratch> python3 tools/undo_selftest.py

The cases it exists for, in order of how much work they lose:

* select-all-then-delete in Journal wiped a diary with no way back;
* Delete Chapter / Delete Entry / Delete Lecture / Delete Class threw the
  item away permanently, title and all;
* File > New and File > Open overwrite the session-recovery file, which for an
  unsaved document is the only copy there is;
* a formatting change emits no "changed" signal, so it has to be checkpointed
  by hand or it is invisible to the history.
"""
import os
import sys
import tempfile

# NB_HOME MUST BE PINNED BEFORE nbapp IS IMPORTED. nbapp computes its
# single-instance scope (_APP_DIR) at import time from NB_HOME; this file used
# to set it down in __main__, by which point the scope was already the unscoped
# /tmp/nb-apps shared with every app and every other unpinned suite. A marker
# there makes claim_single_instance() os._exit(0) this process: no output, exit
# status 0, a silent false pass. Per-process so two copies cannot collide.
os.environ.setdefault(
    "NB_HOME", tempfile.mkdtemp(prefix="nbhome-undo-selftest-"))

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
if DE not in sys.path:
    sys.path.insert(0, DE)

import nbapp                                                    # noqa: E402

ok = True
_app = ""


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + _app + ": " + name)
    if not cond:
        ok = False


def pump():
    n = 0
    while Gtk.events_pending() and n < 500:
        Gtk.main_iteration()
        n += 1


def text_of(buf):
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def type_in(buf, s):
    """Type `s` into `buf` the way a keyboard does, then let the app's typing
    checkpoint land (the history debounces, so it must be flushed)."""
    buf.insert_at_cursor(s)
    pump()


def type_chars(buf, s):
    """Type `s` one character at a time — the smart-quote / em-dash rule only
    fires for a single typed character, never for a paste."""
    for ch in s:
        buf.insert_at_cursor(ch)
    pump()


def wipe(buf):
    """Select all and delete — the accident this whole feature exists for."""
    buf.delete(buf.get_start_iter(), buf.get_end_iter())
    pump()


def ctrl(win, keyval, shift=False):
    """Send a real Ctrl(+Shift) key press through the app's own key handler."""
    ev = Gdk.EventKey()
    ev.type = Gdk.EventType.KEY_PRESS
    ev.keyval = keyval
    ev.state = Gdk.ModifierType.CONTROL_MASK
    if shift:
        ev.state |= Gdk.ModifierType.SHIFT_MASK
    win._on_key(win, ev)
    pump()


def menu_labels(win, name):
    return [lbl for lbl, _cb in win.menu_items(name)]


def undo_entry(win, name="Edit"):
    """The (label, callback) of the app's Undo menu item."""
    for lbl, cb in win.menu_items(name):
        if lbl.startswith("Undo"):
            return lbl, cb
    return None, None


def redo_entry(win, name="Edit"):
    for lbl, cb in win.menu_items(name):
        if lbl.startswith("Redo"):
            return lbl, cb
    return None, None


# =====================================================================
#  Journal — years of diary entries, and no undo at all before this
# =====================================================================
def test_journal():
    global _app
    _app = "journal"
    import journal
    w = journal.Journal()
    w.entries, w.active = [], -1
    w._refresh_list()
    w._load_active()
    w.undo.reset()
    pump()

    lbl, cb = undo_entry(w)
    check("Undo greyed out on an untouched journal", cb is None)

    w.new_entry()
    buf = w.body.get_buffer()
    type_in(buf, "Rain all afternoon. The pear tree came down.")
    w.undo.flush()
    kept = text_of(buf)

    # --- the accident: select all, delete ---
    wipe(buf)
    check("wipe emptied the entry", text_of(buf) == "")
    ctrl(w, Gdk.KEY_z)
    check("Ctrl+Z brings the wiped entry back", text_of(buf) == kept)
    ctrl(w, Gdk.KEY_z)          # and again: back past the typing
    check("second Ctrl+Z steps back past the typing", text_of(buf) == "")
    ctrl(w, Gdk.KEY_y)          # Ctrl+Y is the other redo convention
    check("Ctrl+Y redoes the typing", text_of(buf) == kept)
    ctrl(w, Gdk.KEY_z)
    ctrl(w, Gdk.KEY_z, shift=True)
    check("Ctrl+Shift+Z redoes too", text_of(buf) == kept)

    # --- deleting the entry itself ---
    w.new_entry()
    type_in(w.body.get_buffer(), "Second entry")
    w.undo.flush()
    check("two entries", len(w.entries) == 2)
    lbl, _cb = undo_entry(w)
    w._remove_active()
    check("entry deleted", len(w.entries) == 1)
    lbl, cb = undo_entry(w)
    check("Undo names the delete (%r)" % lbl, "Delete Entry" in lbl)
    cb()
    check("Undo restores the deleted entry", len(w.entries) == 2)
    check("...with its text", "Second entry" in w.entries[0]["text"])
    check("...and it is selected", w.active == 0)
    check("...and shown in the editor",
          "Second entry" in text_of(w.body.get_buffer()))
    lbl, cb = redo_entry(w)
    check("Redo names the delete (%r)" % lbl, "Delete Entry" in lbl)
    cb()
    check("Redo deletes it again", len(w.entries) == 1)

    # --- deleting the LAST entry drops to the empty state ---
    w._remove_active()
    check("empty state reached", w.entries == [] and w.active == -1)
    ctrl(w, Gdk.KEY_z)
    check("Undo comes back out of the empty state", len(w.entries) == 1)

    # --- formatting emits no "changed" signal; it must still checkpoint ---
    buf = w.body.get_buffer()
    buf.set_text("bold me")
    w.undo.flush()
    buf.select_range(buf.get_iter_at_offset(0), buf.get_iter_at_offset(4))
    w._toggle_tag("bold")
    tag = buf.get_tag_table().lookup("bold")
    check("bold applied", buf.get_start_iter().has_tag(tag))
    lbl, cb = undo_entry(w)
    check("Undo names the formatting (%r)" % lbl, "Formatting" in lbl)
    cb()
    check("Undo removes the formatting",
          not buf.get_start_iter().has_tag(tag))

    # --- a restored snapshot must not be editable from under the history ---
    # (the entries are dicts, and the app mutates the live ones in place)
    w.entries, w.active = [], -1
    w._refresh_list()
    w._load_active()
    w.undo.reset()
    w.new_entry()
    buf = w.body.get_buffer()
    for word in ("one", "two", "three"):
        buf.set_text(word)
        w.undo.flush()
    w.undo.undo()
    check("undo lands on the middle state", text_of(buf) == "two")
    buf.set_text("edited after undo")          # forks the history
    w.undo.flush()
    w.undo.undo()
    check("the older state survived being restored and re-edited",
          text_of(buf) == "two")
    w.undo.undo()
    check("...and so did the one before it", text_of(buf) == "one")

    # --- typography still applied on the shared code path (nbapp) ---
    buf.set_text("")
    type_chars(buf, 'He said "hello" -- and left')
    check("straight quotes became curly (nbapp.smart_replacement)",
          "“hello”" in text_of(buf))
    check("-- became an em dash", "—" in text_of(buf))

    # --- delete is undo, not a confirm (undo-replaces-confirmation) ---
    # cc4bda5e retired the delete confirmation entirely: _delete_active removes
    # the entry immediately and leaves a whole-journal undo checkpoint, per the
    # OS-wide decision that destruction gets undo rather than a modal. The old
    # assertion here wrapped _confirm and checked its wording; there is no
    # confirm to wrap now, so the contract to test is the new one — no confirm
    # fires, the entry is gone at once, and undo brings it back intact.
    while len(w.entries) < 2:
        w.new_entry()
    w.active = 0
    confirmed = []
    w._confirm = lambda *a, **k: confirmed.append(True) or True
    before = len(w.entries)
    doomed_title = w.entries[0].get("title")
    w._delete_active()
    check("delete removes the entry immediately, with no confirmation modal",
          len(w.entries) == before - 1 and not confirmed)
    w.undo.undo()
    check("undo restores the deleted entry",
          len(w.entries) == before
          and any(e.get("title") == doomed_title for e in w.entries))

    w.destroy()
    pump()


# =====================================================================
#  Novel — 30-chapter manuscripts; the delete took the chapter's title too
# =====================================================================
def test_novel():
    global _app
    _app = "novel"
    import novel
    w = novel.Novel()
    pump()
    # Novel restores the last session from novel.json, so start from a known
    # blank manuscript — otherwise a second run of this test inherits the
    # chapters the first one left behind and every count below is off.
    w._do_file_new()
    w.undo.reset()

    buf = w.chapters[w.active]["buffer"]
    type_in(buf, "The road bent north out of the valley.")
    w.undo.flush()
    kept = text_of(buf)
    wipe(buf)
    check("wipe emptied the chapter", text_of(buf) == "")
    ctrl(w, Gdk.KEY_z)
    check("Ctrl+Z brings the wiped chapter back",
          text_of(w.chapters[w.active]["buffer"]) == kept)

    # --- delete a chapter, with its title ---
    w._on_new_chapter()
    b2 = w.chapters[w.active]["buffer"]
    b2.set_text("They left before dawn.")
    # The title became its own control — manuscript content in an Entry in
    # the old opening-heading position — no longer a specially-tagged first
    # body line. Type it where the person does.
    w.chapter_title.set_text("A Winter Crossing")
    w._on_change(b2)
    w.undo.flush()
    check("two chapters", len(w.chapters) == 2)
    check("chapter titled from its title control",
          w.chapters[1]["title"] == "A Winter Crossing")
    w._delete_chapter(1)
    check("chapter deleted", len(w.chapters) == 1)
    lbl, cb = undo_entry(w)
    check("Undo names the delete (%r)" % lbl, "Delete Chapter" in lbl)
    cb()
    check("Undo restores the chapter", len(w.chapters) == 2)
    check("...with its title", w.chapters[1]["title"] == "A Winter Crossing")
    check("...with its text",
          "before dawn" in w._buffer_text(w.chapters[1]["buffer"]))
    check("...and the sidebar was rebuilt around it",
          len(w.rows_box.get_children()) >= 2)
    lbl, cb = redo_entry(w)
    cb()
    check("Redo deletes it again", len(w.chapters) == 1)
    ctrl(w, Gdk.KEY_z)
    check("Ctrl+Z restores it once more", len(w.chapters) == 2)

    # --- File > New replaces EVERYTHING (and the recovery file with it) ---
    before = len(w.chapters)
    w._do_file_new()
    check("File > New blanked the manuscript", len(w.chapters) == 1)
    lbl, cb = undo_entry(w)
    check("Undo names the new manuscript (%r)" % lbl, "New Manuscript" in lbl)
    cb()
    check("Undo brings the whole manuscript back", len(w.chapters) == before)
    check("...with chapter 2 intact",
          "before dawn" in w._buffer_text(w.chapters[1]["buffer"]))

    # --- parts. Also the aliasing trap: _restore used to ADOPT the snapshot's
    #     part list, which the next New Part then appended to in place ---
    w._commit_new_part("Book Two")
    check("part added", len(w.parts) == 2)
    ctrl(w, Gdk.KEY_z)
    check("Undo removes the part", len(w.parts) == 1)
    w._commit_new_part("Book Three")
    ctrl(w, Gdk.KEY_z)
    check("Undo removes the second part too (history not aliased)",
          len(w.parts) == 1)

    # --- typography still applied on the shared code path (nbapp) ---
    b = w.chapters[w.active]["buffer"]
    b.set_text("")
    type_chars(b, 'She said "no" -- twice')
    check("straight quotes became curly (nbapp.smart_replacement)",
          "“no”" in text_of(b))
    check("-- became an em dash", "—" in text_of(b))

    w.destroy()
    pump()


# =====================================================================
#  Academics — the whole class goes when its last lecture does
# =====================================================================
def test_academics():
    global _app
    _app = "academics"
    import academics
    w = academics.Academics()
    w.classes, w.lectures, w.active = [], [], -1
    w._refresh_sidebar()
    w._refresh_canvas()
    w.undo.reset()
    pump()

    w._new_class()
    w.title.set_text("Thermodynamics II")
    buf = w.body.get_buffer()
    type_in(buf, "Clausius inequality: the entropy of an isolated system.")
    w._sync_notes()
    w.undo.flush()
    kept = text_of(buf)

    wipe(buf)
    check("wipe emptied the note", text_of(buf) == "")
    ctrl(w, Gdk.KEY_z)
    check("Ctrl+Z brings the wiped note back",
          text_of(w.body.get_buffer()) == kept)

    # --- deleting the only lecture KEEPS its class ---
    # This used to assert the class went with it. That behaviour destroyed the
    # first thing every student does: create a class, press Esc, reopen, and it
    # had silently vanished because nothing referred to it yet. A class now owns
    # a room, a timetable and assignments, so it outlives its lectures and only
    # File > Delete Class removes it.
    check("one class, one lecture",
          len(w.classes) == 1 and len(w.lectures) == 1)
    w._confirm = lambda *_a: True          # answer the modal
    w._delete_lecture()
    check("the lecture is gone", w.lectures == [])
    check("...but the class it belonged to stays", len(w.classes) == 1)
    lbl, cb = undo_entry(w)
    check("Undo names the delete (%r)" % lbl, "Delete Lecture" in lbl)
    cb()
    check("Undo restores the lecture", len(w.lectures) == 1)
    check("...with its class still there", len(w.classes) == 1)
    check("...with its title", w.lectures[0]["title"] == "Thermodynamics II")
    check("...with its notes", "Clausius" in w.lectures[0]["notes"])
    check("...shown in the rebuilt canvas",
          "Clausius" in text_of(w.body.get_buffer()))

    # --- delete a whole class, lectures and all ---
    w._new_lecture()
    w._new_lecture()
    n = len(w.lectures)
    check("three lectures in the class", n == 3)
    w._delete_class()
    check("class deleted with all its lectures",
          w.lectures == [] and w.classes == [])
    lbl, cb = undo_entry(w)
    check("Undo names the class delete (%r)" % lbl, "Delete Class" in lbl)
    cb()
    check("Undo restores the class", len(w.classes) == 1)
    check("...and every lecture in it", len(w.lectures) == n)
    lbl, cb = redo_entry(w)
    cb()
    check("Redo deletes the class again", w.classes == [])

    w.destroy()
    pump()


# =====================================================================
#  Screenplay — New / Open replace the script and its recovery snapshot
# =====================================================================
def test_screenplay():
    global _app
    _app = "screenplay"
    import screenplay
    w = screenplay.Screenplay()
    pump()
    buf = w.body.get_buffer()
    buf.set_text("")
    w.undo.reset()

    type_in(buf, "INT. KITCHEN - NIGHT\nThe kettle starts to complain.")
    w.undo.flush()
    kept = text_of(buf)
    wipe(buf)
    check("wipe emptied the script", text_of(buf) == "")
    ctrl(w, Gdk.KEY_z)
    check("Ctrl+Z brings the wiped script back", text_of(buf) == kept)
    ctrl(w, Gdk.KEY_z, shift=True)
    check("Ctrl+Shift+Z wipes it again", text_of(buf) == "")
    ctrl(w, Gdk.KEY_z)

    # --- File > New throws the whole script away ---
    w._confirm = lambda *_a: True
    w._file_new()
    check("File > New blanked the script", text_of(w.body.get_buffer()) == "")
    lbl, cb = undo_entry(w)
    check("Undo names the new script (%r)" % lbl, "New Script" in lbl)
    cb()
    check("Undo brings the script back", text_of(w.body.get_buffer()) == kept)
    check("...and its title", w.scripttitle.get_text() != "")

    # --- element formatting is a tag-only change: no "changed" signal ---
    buf = w.body.get_buffer()
    buf.place_cursor(buf.get_start_iter())
    w._on_element(w._elbtns[2], 2)          # Character
    tag = buf.get_tag_table().lookup(w.EL_TAGS[2])
    check("element tag applied", buf.get_start_iter().has_tag(tag))
    lbl, cb = undo_entry(w)
    check("Undo names the element change (%r)" % lbl, "Element" in lbl)
    cb()
    check("Undo removes the element tag",
          not w.body.get_buffer().get_start_iter().has_tag(tag))

    w.destroy()
    pump()


# =====================================================================
#  The shared machinery itself
# =====================================================================
def test_history():
    global _app
    _app = "nbapp"
    state = {"body": "", "_caret": 0}
    seen = []

    # The restore callback APPLIES the snapshot, as every real app's does --
    # UndoHistory now compares the on-screen state against the history top
    # before stepping (so unrecorded edits become a step instead of being
    # stepped over), and a stand-in that only records without applying reads
    # as "the app changed something" on every undo.
    def restore(s):
        seen.append(s["body"])
        state.update(s)
    h = nbapp.UndoHistory(lambda: dict(state), restore)
    h.reset()
    check("nothing to undo at the baseline", not h.can_undo())
    check("nothing to redo at the baseline", not h.can_redo())

    for word in ("one", "two", "three"):
        state["body"] = word
        h.checkpoint("Typing")
        h.commit()
    check("three steps recorded", h.can_undo())
    h.undo()
    check("undo restored the previous state", seen[-1] == "two")
    h.undo()
    check("undo again", seen[-1] == "one")
    h.redo()
    check("redo goes forward", seen[-1] == "two")
    state["body"] = "four"
    h.checkpoint("Typing")
    h.commit()
    check("a new edit drops the redo tail", not h.can_redo())

    # a caret-only difference is not a step of its own
    depth = len(h._hist)
    state["_caret"] = 9
    h.checkpoint("Typing")
    h.commit()
    check("moving the cursor does not consume an undo step",
          len(h._hist) == depth)

    # strings are shared between consecutive snapshots, not re-copied
    big = "x" * 100000
    state["body"] = big
    h.checkpoint("Typing")
    h.commit()
    state["_caret"] = 1
    h.checkpoint("Typing")
    h.commit()
    a = h._hist[-1][0]["body"]
    check("an unchanged document is not copied per snapshot",
          a is h._hist[-2][0]["body"] or len(h._hist) == depth + 1)

    # the byte budget keeps a big document's history from ballooning
    h2 = nbapp.UndoHistory(lambda: {"body": "y" * 2000000 + str(len(h2._hist))},
                           lambda s: None)
    h2.reset()
    for i in range(30):
        h2.checkpoint("Typing")
        h2.commit()
    check("history depth is bounded on a huge document (%d steps, %d MB)"
          % (len(h2._hist), len(h2._hist) * 2),
          len(h2._hist) <= 8)
    check("...but never below the minimum useful depth", len(h2._hist) >= 2)

    # a serialiser that raises must not break the edit that triggered it
    h3 = nbapp.UndoHistory(lambda: (_ for _ in ()).throw(ValueError("boom")),
                           lambda s: None)
    try:
        h3.reset()
        h3.checkpoint("Typing")
        h3.commit()
        check("a failing serialiser is survivable", True)
    except Exception as e:
        check("a failing serialiser is survivable (%s)" % e, False)


if __name__ == "__main__":
    os.makedirs(os.environ["NB_HOME"], exist_ok=True)
    which = sys.argv[1:] or ["history", "journal", "novel", "academics",
                             "screenplay"]
    for name in which:
        globals()["test_" + name]()
    print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
    sys.exit(0 if ok else 1)
