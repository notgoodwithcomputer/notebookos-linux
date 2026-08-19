#!/usr/bin/env python3
"""
accent_selftest — the signage red means exactly ONE thing per screen.

Notebook OS has one accent colour (#C8341E). It is legible only while it says
one thing at a time: Papertone already legislates the button case
(.suggested-action is INK, .destructive-action is ACCENT), and the apps have to
hold the same line for their own painted state. Three apps had drifted:

  * Music said SELECTED (sidebar), NOW PLAYING (track row) and ENGAGED
    (shuffle/repeat chips) in the same red on one window, and painted a
    non-destructive "name your playlist" prompt in the destructive red;
  * Workout marked TODAY with the 3px accent edge on the same screen as
    "GOAL MET" - and the 3px accent edge means SELECTED in seven other apps;
  * the Sequencer said RECORD-ARMED, SOLOED and PLAYHEAD POSITION at once.

This locks the outcome in, both in the stylesheets and in the live menus:
Undo/Redo wording, the house ellipsis on a menu entry that opens a confirm,

    DISPLAY=:0 PYTHONPATH=<de> python3 tools/accent_selftest.py
"""
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
sys.path.insert(0, DE)

ACCENT = "#C8341E"
INK = "#1A1916"

_pass = _fail = 0


def check(name, ok, detail=""):
    global _pass, _fail
    if ok:
        _pass += 1
        print("PASS %s" % name)
    else:
        _fail += 1
        print("FAIL %s%s" % (name, ("  -- " + detail) if detail else ""))


def src(mod):
    with open(os.path.join(DE, mod + ".py"), encoding="utf-8") as fh:
        return fh.read()


def rule(text, selector):
    """The declaration block of the first CSS rule whose selector list contains
    `selector`, so a colour can be asserted against ONE rule and not the file.

    Matches the selector where it is FOLLOWED by a declaration block, so a
    mention of the class inside a CSS comment (the comments here name the
    rules they are about) is not mistaken for the rule itself."""
    i = -1
    for hit in re.finditer(re.escape(selector) + r"[^{}\n]*\{", text):
        i = hit.start()
        break
    if i < 0:
        return None
    j = text.find("{", i)
    k = text.find("}", j)
    if j < 0 or k < 0:
        return None
    return text[j:k + 1]


# ---------------------------------------------------------------- stylesheets
m = src("music")
check("music: the sidebar SELECTION keeps the accent edge",
      ACCENT in (rule(m, ".viewrow.active") or ""))
check("music: the NOW PLAYING row is ink, not the accent",
      ACCENT not in (rule(m, ".songlist row.playing") or "x" + ACCENT))
check("music: the now-playing title is ink, not the accent",
      ACCENT not in (rule(m, ".songlist row.playing .s-title") or "x" + ACCENT))
check("music: an engaged shuffle/repeat is an ink chip",
      INK in (rule(m, ".togglebtn:checked") or "")
      and ACCENT not in (rule(m, ".togglebtn:checked") or "x" + ACCENT))
check("music: the destructive confirm keeps the accent",
      ACCENT in (rule(m, ".mdlg-primary") or ""))
check("music: a non-destructive primary is ink (.mdlg-ink)",
      INK in (rule(m, ".mdlg-ink") or ""))
check("music: the name prompt uses the ink primary",
      re.search(r"_prompt_name\(self.*?mdlg-ink", m, re.S) is not None)
check("music: the delete confirm uses the accent primary",
      re.search(r"def _confirm\(self.*?mdlg-primary", m, re.S) is not None)

w = src("workout")
check("workout: TODAY is marked in ink, not the accent",
      ACCENT not in (rule(w, ".wo-day.today") or "x" + ACCENT))
check("workout: GOAL MET keeps the accent",
      ACCENT in (rule(w, ".wo-hit") or ""))
check("workout: the selected card stays quiet",
      ACCENT not in (rule(w, ".wo-card.sel") or "x" + ACCENT))

s = src("sequencer")
check("sequencer: an armed track's REC chip keeps the accent",
      ACCENT in (rule(s, ".armbtn.on") or ""))
check("sequencer: Solo is an ink chip, not the accent",
      ACCENT not in (rule(s, ".sbtn.on") or "x" + ACCENT))
check("sequencer: Mute is an ink chip",
      INK in (rule(s, ".mbtn.on") or ""))
check("sequencer: the armed lane's edge is ink, not the accent",
      ACCENT not in (rule(s, ".trackhead.armed") or "x" + ACCENT))
check("sequencer: the playhead is drawn in ink",
      re.search(r"# The playhead is a POSITION.*?set_source_rgb\(\*INK\)",
                s, re.S) is not None)

i = src("illustrator")
# Both prompts and the status chip now word it the same way, and all three go
# through _t(), so the count is >= 2 rather than exactly 2.
check("illustrator: one heading for unsaved work",
      "Discard changes?" not in i and i.count('_t("Unsaved changes")') >= 2,
      "found %d '_t(\"Unsaved changes\")'" % i.count('_t("Unsaved changes")'))
check("illustrator: mixed swatches are named, never a hex tooltip",
      "set_tooltip_text(mix_name(hex_))" in i)

# --------------------------------------------------------------- live widgets
# Per-PROCESS home: a shared NB_HOME also shares nbapp's single-instance marker
# dir, and the copy that loses that race is os._exit(0)ed with no output and
# exit status 0 -- a silent false pass.
os.environ.setdefault("NB_HOME", os.path.join(
    os.environ.get("TMPDIR", "/tmp"),
    "accent-selftest-home-%d" % os.getpid()))
os.makedirs(os.environ["NB_HOME"], exist_ok=True)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402,F401

import illustrator  # noqa: E402
import sequencer  # noqa: E402
import video  # noqa: E402

# The palette is now a 112-swatch hue x value grid, so a mixed colour is named
# after the nearest of those rather than after one of the sixteen old chrome
# pigments ("Steel").
check("illustrator: a mix is named in the palette's vocabulary",
      illustrator.mix_name("#3A7FBF") == "Azure",
      illustrator.mix_name("#3A7FBF"))

ill = illustrator.Illustrator()
labels = [l for l, _a in ill.menu_items("Edit")]
check("illustrator: Redo prints the house shortcut",
      "Redo    Ctrl+Shift+Z" in labels, str(labels))
ill._add_layer()
check("illustrator: Undo names the action it takes back",
      [l for l, _a in ill.menu_items("Edit")][0] == "Undo New Layer    Ctrl+Z",
      str([l for l, _a in ill.menu_items("Edit")][0]))

seq = sequencer.Sequencer()
labels = [l for l, _a in seq.menu_items("Edit")]
check("sequencer: Redo prints the house shortcut",
      "Redo    Ctrl+Shift+Z" in labels, str(labels))
seq._arm_all(True)
check("sequencer: Undo names the action it takes back",
      [l for l, _a in seq.menu_items("Edit")][0]
      == "Undo Arm All Tracks    Ctrl+Z")
# The item was renamed "Clear All Takes…" -> "Remove Every Clip…" (function
# first, per the UI text mandate), and then the ellipsis came OFF: commit
# 8ddfd945 retired the "are you sure?" in favour of undo, and
# confirm_undo_adversarial_selftest now FORBIDS that confirm by name. An
# ellipsis promises a dialog, so an item that no longer asks must not carry
# one — that is the contract here, and it is the opposite of what this check
# asserted while the app was already right.
_track = [l for l, _a in seq.menu_items("Track")]
check("sequencer: Remove Every Clip is offered", "Remove Every Clip" in _track,
      str(_track))
check("sequencer: ...and promises no dialog, because undo is the contract",
      "Remove Every Clip…" not in _track, str(_track))
# The shipped wording is "Not saved to a file" (sequencer._update_proj), and
# that is the key present in all 17 catalogs. "No project saved yet" was an
# earlier draft and exists nowhere in the tree any more.
check("sequencer: the empty project note names no menu path",
      "Not saved to a file" == seq.proj_lbl.get_text(),
      seq.proj_lbl.get_text())

vid = video.VideoEditor()
labels = [l for l, _a in vid.menu_items("Edit")]
check("video: Undo/Redo come from the shared builder",
      labels[:2] == ["Undo    Ctrl+Z", "Redo    Ctrl+Shift+Z"], str(labels[:2]))
vid._menu_add_title()
check("video: Undo names the action it takes back",
      [l for l, _a in vid.menu_items("Edit")][0]
      == "Undo Add Title Card    Ctrl+Z")
# Same law, same retirement: video's delete is undoable ("Undo Delete Clip"),
# so the label must not promise a card that no longer appears.
_clip = [l for l, _a in vid.menu_items("Clip")]
check("video: Delete Clip is offered", "Delete Clip" in _clip, str(_clip))
check("video: ...and promises no dialog, because undo is the contract",
      "Delete Clip…" not in _clip, str(_clip))

# ------------------------------------------------------------------ catalogs
# "Clear Conversation…" / "Clear conversation?" were BitChat chrome. BitChat
# was removed on 2026-07-28 and the strings are gone from every source file and
# from all 17 catalogs, so requiring them here only ever fails.
# The LIVE labels, not retired ones: "Clear All Takes…" is still in the
# catalogs as a dead key, so checking it proved nothing about what ships.
NEW_CHROME = ("Remove Every Clip…", "Delete Clip…",
              "Not saved to a file", "%s armed")
missing = []
for code in "de el eo es fr hi it ja ko nl pl pt ru sr tr yi zh".split():
    with open(os.path.join(DE, "lang_%s.json" % code), encoding="utf-8") as fh:
        cat = json.load(fh)
    for key in NEW_CHROME:
        if key not in cat:
            missing.append("%s:%s" % (code, key))
check("i18n: every new string is in all 17 catalogs", not missing,
      ", ".join(missing[:6]))

print("\n%d checks, %d passed, %d failed"
      % (_pass + _fail, _pass, _fail))
print("RESULT:", "ALL PASS" if not _fail else "FAILURES")
sys.exit(1 if _fail else 0)
