#!/usr/bin/env python3
"""
Delete a class while a lecture of ANOTHER class is open and half-typed, and
prove the open lecture's text does not land on top of an innocent one.

THE BUG THIS EXISTS FOR
-----------------------
The note view is rebuilt from scratch by _refresh_canvas: `self.body` is a NEW
GtkTextView holding the newly-active lecture's text. Until that rebuild runs,
the live buffer still belongs to the lecture that WAS open.

_delete_class_at deleted the class, reset `active` to 0, and then called
_save_to_disk -- and _save_to_disk opens with _capture_active(), which copies
the live buffer into `self.lectures[self.active]`. `active` was already 0 and
the buffer was still the outgoing lecture's, so the outgoing lecture's note
text and tag spans were written over lectures[0] and then flushed to disk.

From the user's chair: delete an old class, and a lecture in a class you never
touched comes back holding the wrong notes -- permanently. Not a repaint bug,
a write. _delete_lecture already had the right order (refresh, then save);
_delete_class_at did not.

Also covered: keystrokes still inside the 150ms notes debounce when Delete
Class is used belong to the lecture being typed in, and must survive there.

DISPLAY-FREE. No widget is constructed: the real methods are bound onto a stub
whose `body` is a plain object with the handful of buffer calls the capture
path makes, and whose _refresh_canvas does what the real one does that matters
here -- reload the buffer from the newly-active lecture. Only GLib (which needs
no X server) is used, for a genuinely pending debounce source.

  PYTHONPATH=<overlay>/opt/notebook/de python3 tools/academics_lifecycle_selftest.py
"""
import os, sys, json, tempfile

HOME = tempfile.mkdtemp(prefix="nbhome-lifecycle-")
os.environ["NB_HOME"] = HOME
os.environ.setdefault("GDK_BACKEND", "x11")

from gi.repository import GLib
import academics

AW = academics.Academics


class FakeIter:
    """Only enough of a GtkTextIter for _sync_notes' start/end pair."""
    def __init__(self, off):
        self.off = off

    def get_offset(self):
        return self.off


class FakeBuffer:
    """The three things the capture path asks a GtkTextBuffer for."""
    def __init__(self, text=""):
        self.text = text

    def set_text(self, text):
        self.text = text

    def get_start_iter(self):
        return FakeIter(0)

    def get_end_iter(self):
        return FakeIter(len(self.text))

    def get_text(self, start, end, _hidden):
        return self.text[start.get_offset():end.get_offset()]

    def get_char_count(self):
        return len(self.text)


class FakeView:
    """Text AND tag spans, because the real note view carries both: the canvas
    applies a lecture's saved spans onto the new buffer (_apply_ranges) and the
    capture path reads them straight back out."""
    def __init__(self, text="", ranges=None):
        self.buf = FakeBuffer(text)
        self.ranges = {k: [list(sp) for sp in v]
                       for k, v in (ranges or {}).items()}

    def get_buffer(self):
        return self.buf


class FakeLabel:
    def __init__(self):
        self.text = ""

    def set_text(self, t):
        self.text = t


class FakeUndo:
    def __init__(self):
        self.log = []

    def checkpoint(self, name):
        self.log.append(("checkpoint", name))

    def commit(self):
        self.log.append(("commit",))


class Harness:
    """The real _delete_class_at and the real capture/save path it drives."""
    _RANGE_TAGS = AW._RANGE_TAGS
    _delete_class_at = AW.__dict__["_delete_class_at"]
    _capture_active = AW.__dict__["_capture_active"]
    _sync_notes = AW.__dict__["_sync_notes"]
    _save_to_disk = AW.__dict__["_save_to_disk"]
    _class_label = AW.__dict__["_class_label"]
    _wordcount_text = AW.__dict__["_wordcount_text"]

    def __init__(self, classes, lectures, homework, active, buffer_text,
                 buffer_ranges=None):
        self.classes = classes
        self.lectures = lectures
        self.homework = homework
        self.active = active
        self.body = FakeView(buffer_text, buffer_ranges)
        self.wordlbl = FakeLabel()
        self.undo = FakeUndo()
        self._sel_block = -1
        self._may_empty = False
        self._damaged = {}
        self.canvas_refreshes = 0
        # A genuinely pending notes debounce, exactly as typing leaves one.
        self._notes_timer = GLib.timeout_add(150, lambda: False)

    # --- stubs for the parts that need a screen -------------------------
    def _capture_ranges(self):
        # Same contract as the real one -- the LIVE buffer's spans land on
        # lectures[active] -- without a GtkTextIter walk.
        if self.active < 0:
            return
        self.lectures[self.active]["ranges"] = {
            k: [list(sp) for sp in v] for k, v in self.body.ranges.items()}

    def _confirm(self, *_a, **_k):
        return True

    def _flash(self, *_a, **_k):
        pass

    def _refresh_schedule(self):
        pass

    def _refresh_homework(self, *_a, **_k):
        pass

    def _refresh_sidebar(self):
        pass

    def _refresh_canvas(self):
        # What the real one does that this test depends on: the note view is
        # rebuilt around the lecture `active` now points at.
        self.canvas_refreshes += 1
        if 0 <= self.active < len(self.lectures):
            cur = self.lectures[self.active]
            self.body = FakeView(cur.get("notes", ""), cur.get("ranges"))
        else:
            self.body = FakeView("")


def lec(cls, num, title, notes, ranges=None):
    return {"cls": cls, "num": num, "title": title, "date": "", "meta": "",
            "notes": notes, "ranges": ranges or {}}


def build():
    """Three classes. The open lecture is in class 2; class 1 is deleted."""
    classes = [
        {"label": "Organic Chemistry", "color": "#9A7B4F", "room": "D2210",
         "instructor": "Peraza", "meets": []},
        {"label": "Art History", "color": "#4A5E73", "room": "B110",
         "instructor": "Nkemdi", "meets": []},
        {"label": "Linear Algebra", "color": "#6E5A78", "room": "W7500",
         "instructor": "Iyer", "meets": []},
    ]
    lectures = [
        lec(0, "01", "Aromatics", "benzene ring stability",
            {"bold": [[0, 7]]}),
        lec(1, "01", "Quattrocento", "perspective"),
        lec(2, "01", "Eigenvalues", "char. polynomial"),
    ]
    homework = [{"title": "Problem set 4", "cls": 0, "due": "", "done": False,
                 "note": ""}]
    return classes, lectures, homework


FAILED = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


def case_open_lecture_survives_other_class_delete():
    print("delete a class while another class's lecture is open and half-typed")
    classes, lectures, homework = build()
    # Lecture index 2 (Linear Algebra) is open, and the student has typed past
    # the debounce: the buffer is ahead of the model.
    typed = "char. polynomial det(A - lambda I) = 0"
    typed_spans = {"italic": [[18, 38]]}
    h = Harness(classes, lectures, homework, active=2, buffer_text=typed,
                buffer_ranges=typed_spans)
    h._delete_class_at(1)                      # delete Art History

    # 1. The innocent first lecture must be untouched. THIS is the regression:
    #    the old order wrote the open lecture's buffer over lectures[0].
    check(h.lectures[0]["notes"] == "benzene ring stability",
          "lectures[0] notes intact (%r)" % h.lectures[0]["notes"][:40])
    check(h.lectures[0]["ranges"] == {"bold": [[0, 7]]},
          "lectures[0] tag spans intact (%r)" % (h.lectures[0]["ranges"],))
    # 2. The keystrokes inside the debounce belong to the lecture they were
    #    typed in, and it survived the delete, so they must be there.
    survivor = [l for l in h.lectures if l["title"] == "Eigenvalues"][0]
    check(survivor["notes"] == typed,
          "in-flight keystrokes kept on their own lecture (%r)"
          % survivor["notes"][:40])
    check(survivor["ranges"] == typed_spans,
          "in-flight tag spans kept on their own lecture (%r)"
          % (survivor["ranges"],))
    # 3. The delete itself still did its job.
    check([c["label"] for c in h.classes]
          == ["Organic Chemistry", "Linear Algebra"], "Art History removed")
    check([l["title"] for l in h.lectures] == ["Aromatics", "Eigenvalues"],
          "only the deleted class's lecture went")
    check([l["cls"] for l in h.lectures] == [0, 1],
          "surviving lectures reindexed onto their classes")
    check(h.canvas_refreshes == 1, "canvas rebuilt once")

    # 4. And what reached disk says the same thing.
    on_disk = json.load(open(academics.ACADEMICS_FILE))
    by_title = {l["title"]: l for l in on_disk["lectures"]}
    check(by_title["Aromatics"]["notes"] == "benzene ring stability",
          "persisted lectures[0] notes intact")
    check(by_title["Eigenvalues"]["notes"] == typed,
          "persisted in-flight keystrokes")


def case_delete_the_open_lectures_own_class():
    print("delete the class the open lecture belongs to")
    classes, lectures, homework = build()
    h = Harness(classes, lectures, homework, active=1,
                buffer_text="perspective", buffer_ranges={"italic": [[0, 11]]})
    h._delete_class_at(1)                      # the open lecture's own class

    check([l["title"] for l in h.lectures] == ["Aromatics", "Eigenvalues"],
          "the open lecture went with its class")
    check(h.lectures[0]["notes"] == "benzene ring stability",
          "lectures[0] notes intact (%r)" % h.lectures[0]["notes"][:40])
    check(h.lectures[0]["ranges"] == {"bold": [[0, 7]]},
          "lectures[0] tag spans intact (%r)" % (h.lectures[0]["ranges"],))
    check(h.active == 0, "active clamped onto a real lecture")


def case_last_class_leaves_nothing_open():
    print("delete every class")
    classes, lectures, homework = build()
    h = Harness(classes, lectures, homework, active=0, buffer_text="benzene")
    for _ in range(3):
        h._delete_class_at(0)
    check(h.classes == [] and h.lectures == [], "model emptied")
    check(h.active == -1, "nothing open")
    # An assignment outlives its class rather than being destroyed with it.
    check([hw["cls"] for hw in h.homework] == [-1],
          "the assignment survived, untied")
    on_disk = json.load(open(academics.ACADEMICS_FILE))
    check(len(on_disk["homework"]) == 1, "persisted assignment survived")


if __name__ == "__main__":
    case_open_lecture_survives_other_class_delete()
    case_delete_the_open_lectures_own_class()
    case_last_class_leaves_nothing_open()
    print()
    if FAILED:
        print("FAILED %d check%s" % (len(FAILED), "" if len(FAILED) == 1 else "s"))
        for m in FAILED:
            print("  - " + m)
        sys.exit(1)
    print("PASS")
