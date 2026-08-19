#!/usr/bin/env python3
"""
Display-free regression test for Contacts' handling of a malformed persisted
record (contacts.py). Separate from contacts_selftest.py, which builds real
windows and needs a DISPLAY; this one touches no widget hierarchy and runs
anywhere.

THE BUG THIS PINS
-----------------
A card's "color" is pasted straight into the avatar's stylesheet:

    label { background:<color>; color:#fff; ... }

Gtk.CssProvider.load_from_data RAISES on anything it cannot parse, and that
call sits on the launch path (_avatar -> _contact_row -> _rebuild_list, run
from Contacts.__init__). So one card in contacts.json whose colour is not a
colour — "8A857A" with the hash lost, a truncated "#GG", any hand edit — took
Contacts down at startup, every launch, with no message and the whole address
book unreachable behind it. _normalize_person accepted any non-empty string.

Run as:
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  NB_HOME=/tmp/ct python3 contacts_record_selftest.py
"""
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))

# Pin NB_HOME before importing the app: contacts.py resolves CONTACTS_FILE at
# import time, and an unset NB_HOME points it at the caller's own real address
# book. Nothing here writes, but nothing here should be able to.
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="contacts-record-"))

import contacts as mod  # noqa: E402

results = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    results.append(bool(ok))


def avatar_css_raises(color):
    """Build the avatar stylesheet the app builds for `color`, on a stand-in
    object holding only the cache _avatar_css touches — no widgets, no display.
    Returns the exception, or None when the CSS parsed."""
    holder = types.SimpleNamespace(_avatar_css_cache={})
    try:
        mod.Contacts._avatar_css(holder, color, 34, 13)
        return None
    except Exception as exc:
        return exc


# Colours a corrupted / hand-edited contacts.json realistically carries. Each
# one made Gtk.CssProvider raise before the fix.
BAD_COLORS = [
    "8A857A",        # the hash lost in an edit
    "#GG8A85",       # truncated / mistyped hex
    "banana",        # not a colour name
    "0x8A857A",      # a colour written the way a programmer writes one
    "}",             # a fragment of the surrounding CSS
    "  ",            # whitespace only
]

# Spellings GTK reads fine; the fix must not throw these away, or reopening the
# book would silently repaint everybody's avatar.
GOOD_COLORS = ["#8A857A", "#6E7B57", "red", "rgb(10,20,30)"]


def main():
    # --- 1. The premise: these values really do break the stylesheet ----
    # A green gate has to be provable able to go red — if load_from_data
    # stopped raising, the rest of this file would pass while testing nothing.
    check("premise-bad-color-breaks-css",
          all(avatar_css_raises(c) is not None for c in BAD_COLORS))

    # --- 2. A malformed colour is replaced by a palette one -------------
    for i, bad in enumerate(BAD_COLORS):
        person = mod.Contacts._normalize_person(
            {"name": "Alice Test", "phone": "555-0100", "color": bad}, i)
        check("normalize-drops-bad-color-%r" % bad,
              person["color"] in mod.AVATAR_COLORS)
        check("avatar-css-survives-%r" % bad,
              avatar_css_raises(person["color"]) is None)
        # The card itself is untouched: the colour is decoration, the number
        # is the reason the record exists.
        check("normalize-keeps-fields-%r" % bad,
              person["name"] == "Alice Test" and person["phones"] == [
                  {"label": "mobile", "value": "555-0100"}])

    # --- 3. A readable colour is kept exactly as written ----------------
    for i, good in enumerate(GOOD_COLORS):
        person = mod.Contacts._normalize_person({"name": "B", "color": good}, i)
        check("normalize-keeps-good-color-%r" % good, person["color"] == good)

    # --- 4. The whole load path: one poisoned card cannot stop the book -
    # _load_people is what __init__ calls; every card it returns has to be
    # renderable, or the app dies before it draws.
    book = [
        {"name": "Alice", "color": "#8A857A"},
        {"name": "Mallory", "color": "8A857A"},   # the poisoned record
        {"name": "Bob"},                          # no colour at all
    ]
    loaded = [mod.Contacts._normalize_person(p, i) for i, p in enumerate(book)]
    check("load-keeps-every-card", [p["name"] for p in loaded]
          == ["Alice", "Mallory", "Bob"])
    check("load-every-avatar-renders",
          all(avatar_css_raises(p["color"]) is None for p in loaded))

    ok = all(results)
    print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
