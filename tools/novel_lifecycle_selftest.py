#!/usr/bin/env python3
"""Novel's manuscript lifecycle: what survives a save, a reload and a reopen.

    python3 tools/novel_lifecycle_selftest.py

Needs no display. Every check here runs against Novel._parse_state, which is
the single gate every load goes through — session recovery on launch
(_load_state) and File > Open (_do_open_path) both hand their decoded JSON to
it, and _restore then rebuilds the whole editable model out of the dict it
returns. Anything _parse_state drops is therefore not just missing from the
screen: _restore leaves the live model at its default, and the next autosave
serialises that default straight over the stored value.

The fault this covers is exactly that. The author name, set through
File > Author... and written by _serialize, was never read back out: the state
dict carried title, parts, chapters, active and doc_path, so _restore's
`state.get("author", "")` always found nothing. A writer's name lived until the
window closed, came back blank, and one 900ms debounce later the copy on disk
was blank too.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.path.abspath(os.path.join(HERE, "..", "buildroot", "board",
                                 "notebookos", "rootfs-overlay", "opt",
                                 "notebook", "de")),
    "/opt/notebook/de",
]
DE = next((p for p in _CANDIDATES if os.path.isdir(p)), _CANDIDATES[0])
if DE not in sys.path:
    sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME",
                      tempfile.mkdtemp(prefix="nbhome-novel-lifecycle-"))

import novel                                              # noqa: E402

FAILED = []
AUTHOR = "Ada Marchetti"


def check(cond, what):
    print("%-68s %s" % (what, "ok" if cond else "FAIL"))
    if not cond:
        FAILED.append(what)


def parse(doc):
    """_parse_state as the loaders reach it. It touches nothing on self, so it
    runs unbound — no window, no display, no GTK buffers."""
    return novel.Novel._parse_state(None, doc)


def document(**over):
    """What _serialize() writes for a one-chapter manuscript with an author."""
    doc = {
        "title": "The Lantern Keeper",
        "author": AUTHOR,
        "active": 0,
        "doc_path": None,
        "parts": [{"name": ""}],
        "chapters": [{"num": "1", "title": "The Harbour",
                      "body": "The Harbour\nShe kept the lantern trimmed.",
                      "ranges": {}, "part": 0}],
    }
    doc.update(over)
    return doc


def restored_author(state):
    """The value _restore() lands in self._author, verbatim from its source:

        _a = state.get("author", "")
        self._author = _a if isinstance(_a, str) else ""
    """
    a = state.get("author", "")
    return a if isinstance(a, str) else ""


def main():
    # ---- 1. the load gate carries the author ------------------------------
    state = parse(document())
    check(state is not None, "a serialized manuscript parses back")
    check(restored_author(state) == AUTHOR,
          "reopening the app restores the author, not a blank name")

    # ---- 2. ...so the next autosave cannot erase it ------------------------
    # The loss was never the missing label; it was the write that followed.
    # _save_state() serialises the live model, so an author _restore left empty
    # goes to disk empty and the stored name is gone for good.
    saved_again = dict(document(), author=restored_author(state))
    check(saved_again["author"] == AUTHOR,
          "...so the autosave after a reload writes the name back, not \"\"")
    check(restored_author(parse(saved_again)) == AUTHOR,
          "...and a second open/close cycle still has it")

    # ---- 3. File > Open of a manuscript with an author ---------------------
    # Same gate, plus the doc_path the open path stamps on afterwards.
    opened = parse(document(doc_path="/tmp/lantern.json"))
    check(restored_author(opened) == AUTHOR,
          "File > Open of another manuscript brings its author across")
    check(opened.get("doc_path") == "/tmp/lantern.json",
          "...and still binds the file it was opened from")

    # ---- 4. a manuscript with no author stays authorless -------------------
    # An empty author prints nothing on the cover; it must not become a
    # placeholder, and an older file that predates the field must still load.
    no_author = document()
    del no_author["author"]
    st = parse(no_author)
    check(st is not None and restored_author(st) == "",
          "a file written before authors existed loads with an empty author")
    check(restored_author(parse(document(author=""))) == "",
          "an author cleared on purpose stays cleared")

    # ---- 5. a damaged author field cannot break the load ------------------
    # Every other field here is validated before use; the author is user text
    # from a file on disk, so a wrong type must degrade, never raise.
    for bad in (123, None, ["Ada"], {"name": "Ada"}, True):
        st = parse(document(author=bad))
        check(st is not None and isinstance(restored_author(st), str),
              "author %r loads as a string instead of raising"
              % (bad,))

    # ---- 6. the rest of the model still round-trips ------------------------
    st = parse(document(title="The Lantern Keeper", active=0))
    check(st["title"] == "The Lantern Keeper", "the title round-trips")
    check(len(st["chapters"]) == 1
          and st["chapters"][0]["title"] == "The Harbour",
          "the chapter round-trips")
    check(st["parts"] == [{"name": ""}], "the part list round-trips")
    check(parse({"chapters": []}) is None and parse("nonsense") is None,
          "a document that is not a manuscript is still refused")

    print()
    if FAILED:
        print("novel lifecycle selftest: %d FAILED" % len(FAILED))
        for f in FAILED:
            print("  - %s" % f)
        return 1
    print("novel lifecycle selftest: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
