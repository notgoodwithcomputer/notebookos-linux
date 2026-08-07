#!/usr/bin/env python3
"""
Headless selftest for the GBA SDK — the game maker in de/gbasdk.py.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=<overlay>/opt/notebook/de \
  python3 tools/gbasdk_selftest.py

WHY THIS EXISTS: gbasdk is the largest app in the OS (six editors, a code
generator and an on-device compiler) and until now its only coverage was six
checks on the New/Open confirm dialogs. A regression anywhere else — a rename
that breaks a reference, an action that stops emitting code, a resource type
that stops round-tripping through the autosave — would ship unnoticed.

WHAT IT DRIVES: the REAL window and the REAL handlers. Dialogs are the only
thing replaced: _prompt_text / _choose / _confirm / _card are the app's four
modal front doors, and each is swapped for a stub that answers as a person
would, so the code path behind it (rename, add-event, add-action, delete,
export) runs exactly as it does on the guest. Canvas edits go through the real
button-press handlers with synthesised Gdk events whose coordinates are derived
from the widget's own cell size, so the hit-testing maths is under test too.

A FAILING CHECK IS A REAL DEFECT, not a broken harness. The suite is expected
to be red while the defects it found are still open; the summary at the bottom
names the ones that live in files this worker does not own (gbasdk.py,
gbabuild.py) so they can be routed. Nothing here writes outside a throwaway
NB_HOME.

Companion suites:
  tools/gbasdk_damage_selftest.py   damaged-store survival, one case per process
  tools/gbaemu_selftest.py          SDK export -> GBA Emulator handoff
"""
import ast
import os
import re
import sys
import json
import shutil
import tempfile

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

HOME = tempfile.mkdtemp(prefix="gbasdk-selftest-")
os.makedirs(os.path.join(HOME, ".config", "notebook"))
os.makedirs(os.path.join(HOME, "Documents"))
os.environ["NB_HOME"] = HOME

import nbapp                                               # noqa: E402
# Stand clear of the single-instance lock. nbapp exits the process outright
# (os._exit, deliberately) when the same app is already open elsewhere, and a
# developer with the real gbasdk running would otherwise see this whole suite
# vanish with a silent exit 0 and no checks.
nbapp._APP_DIR = os.path.join(HOME, "nb-apps")
os.makedirs(nbapp._APP_DIR)

import nbpicker                                            # noqa: E402
import gbasdk                                              # noqa: E402
import gbabuild                                            # noqa: E402
import gbahelp                                             # noqa: E402

CFG = os.path.join(HOME, ".config", "notebook", "gbasdk.json")
OVERLAY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "buildroot", "board", "notebookos", "rootfs-overlay")
RUNTIME = os.path.join(OVERLAY, "opt", "notebook", "gbaruntime")
TOOLCHAIN = os.path.join(OVERLAY, "opt", "gba-toolchain")

RESULTS = []
FAILED = []

# Every "pick one of these" control in the app sets the other buttons' state
# from inside its own handler, which is the shape that re-enters. Named here so
# each one is checked and so a new one is one line away from being covered.
SEGMENTED = ("_set_tool", "_pick_channel", "_set_room_mode", "_set_room_zoom")


def check(name, cond, detail=""):
    ok = bool(cond)
    RESULTS.append(ok)
    if not ok:
        FAILED.append(name)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "   <- %s" % (detail,)))
    return ok


def section(title):
    print("\n--- %s" % title)


def pump(n=400):
    i = 0
    while Gtk.events_pending() and i < n:
        Gtk.main_iteration_do(False)
        i += 1


def btn(x, y, b=1, motion=False):
    """A synthesised pointer event for a canvas handler."""
    e = Gdk.EventButton()
    e.type = (Gdk.EventType.MOTION_NOTIFY if motion
              else Gdk.EventType.BUTTON_PRESS)
    e.x = float(x)
    e.y = float(y)
    e.button = b
    e.state = (Gdk.ModifierType.BUTTON3_MASK if (motion and b == 3) else 0)
    return e


def app(project=None, keep=False):
    """A fresh app window with its dialogs stubbed out.

    `keep=False` clears the autosave first, so a section starts from an empty
    project instead of inheriting whatever the previous section left on disk.
    `keep=True` is for the persistence checks, where reading back the last
    session IS the thing under test.

    `_answer` / `_pick` / `_yes` drive the stubs: set them before calling the
    method whose dialog you want answered."""
    if not keep:
        for f in os.listdir(os.path.dirname(CFG)):
            os.unlink(os.path.join(os.path.dirname(CFG), f))
    w = gbasdk.GbaSdk()
    # The editors live in a Gtk.Stack, and a Stack will not switch to a child
    # that has never been shown — so without this every _refresh_editor() is a
    # no-op and the visible child stays None. Showing the window's CHILD rather
    # than the window keeps the toplevel off the developer's screen.
    child = w.get_child()
    if child is not None:
        child.show_all()
    pump()
    w._answer = ""            # text _prompt_text returns
    w._pick = 0               # index _choose activates
    w._yes = True             # what _confirm returns
    w._asked = []             # every confirm heading seen
    w._cards = []             # every result card (title, lines)

    def prompt(_title, _initial, cb):
        if w._answer is not None:
            cb(w._answer)
    def choose(_title, labels, cb):
        if w._pick is not None and 0 <= w._pick < len(labels):
            cb(w._pick)
    def confirm(heading, _body, _ok):
        w._asked.append(heading)
        return w._yes
    def card(_icon, title, lines, primary=None, secondary=None, bullets=None):
        w._cards.append((title, list(lines)))
        return True
    w._prompt_text = prompt
    w._choose = choose
    w._confirm = confirm
    w._card = card

    # A method that calls itself for ever cannot be tested around — it takes the
    # whole run down with a wall of RecursionErrors. Cap the depth of each
    # segmented control's setter instead and record how deep it got, so re-entry
    # is reported as one failed check rather than a hung suite. Nothing is
    # masked: a healthy setter never reaches depth 2.
    w._depth = {}
    w._maxdepth = {}
    for _name in SEGMENTED:
        if not hasattr(w, _name):
            continue

        def guarded(*a, _n=_name, _real=getattr(w, _name), **kw):
            w._depth[_n] = w._depth.get(_n, 0) + 1
            w._maxdepth[_n] = max(w._maxdepth.get(_n, 0), w._depth[_n])
            try:
                if w._depth[_n] > 3:
                    raise RuntimeError("%s re-entered itself" % _n)
                return _real(*a, **kw)
            finally:
                w._depth[_n] -= 1
        setattr(w, _name, guarded)
    if project is not None:
        w.proj = json.loads(json.dumps(project))
        w._sel = None
        w._render_tree()
    return w


def rename(w, kind, index, newid):
    """The real rename path, answered as a person would answer the dialog."""
    w._select_resource(kind, index)
    w._answer = newid
    w._rename_resource()
    pump()


def delete(w, kind, index, yes=True):
    w._select_resource(kind, index)
    w._yes = yes
    w._asked = []
    w._delete_resource()
    pump()


def canvas_geom(w, widget, sw, sh):
    """(cell, ox, oy) for a pixel canvas, whichever helper the app has.

    The pixel canvases were drawn from the top-left corner and are now centred
    in their allocation, so the geometry helper changed shape. Ask the app for
    it either way rather than assuming a layout — the point of clicking through
    the real handler is that the hit-testing maths is under test."""
    if hasattr(w, "_canvas_geom"):
        return tuple(w._canvas_geom(widget, sw, sh))
    return (w._spr_cell(widget, sw, sh), 0.0, 0.0)


def spr_click(w, i, j, tool="pen", b=1):
    """Click sprite pixel (i, j) through the real paint handler."""
    s = w._cur_sprite()
    cell, ox, oy = canvas_geom(w, w._spr_canvas, s.get("w", 16), s.get("h", 16))
    w._set_tool(tool)
    w._on_sprite_paint(w._spr_canvas,
                       btn(ox + (i + 0.5) * cell, oy + (j + 0.5) * cell, b))


def tile_click(w, i, j, b=1):
    """Click tile pixel (i, j) through the real tile paint handler."""
    cell, ox, oy = canvas_geom(w, w._tile_canvas, 8, 8)
    w._on_tile_paint(w._tile_canvas,
                     btn(ox + (i + 0.5) * cell, oy + (j + 0.5) * cell, b))


def snd_click(w, step, pitch):
    """Click the piano roll at (step, pitch) through the real handler.

    The roll's gutter and ruler are the app's own constants — read them off it
    rather than hard-coding a layout, or a redesigned roll reports as a broken
    sound editor."""
    gut = float(getattr(w, "SND_GUTTER", 30))
    rul = float(getattr(w, "SND_RULER", 0))
    return w._on_snd_click(
        w._snd_canvas,
        btn(gut + (step + 0.5) * w.SND_CELLW,
            rul + (gbasdk.PITCH_HI - pitch + 0.5) * w.SND_CELLH))


def room_click(w, x, y, b=1):
    w._on_room_click(w._room_canvas, btn(x * 2, y * 2, b))


# A project that touches every reference site the model has, so a rename or a
# delete has something to break in each of them.
def wired_project():
    return {
        "name": "Wired",
        "sprites": [{"id": "spr_a", "w": 16, "h": 16, "ox": 8, "oy": 8,
                     "anim_speed": 0, "frames": [[gbasdk.TRANSPARENT] * 256]}],
        "sounds": [{"id": "snd_a", "tempo": 8, "loop": True, "steps": 16,
                    "lead": [0] * 16, "bass": [0] * 16}],
        "tilesets": [{"id": "ts_a", "tiles": [[0x03E0] * 64, [0x7C00] * 64]}],
        "objects": [{"id": "obj_a", "sprite": "spr_a", "visible": True,
                     "solid": False, "events": [
                         {"type": "step", "actions": [
                             {"kind": "create_instance", "object": "obj_a",
                              "x": "8", "y": "8"},
                             {"kind": "destroy_object", "object": "obj_a"},
                             {"kind": "change_sprite", "sprite": "spr_a"},
                             {"kind": "goto_room", "room": "rm_a"},
                             {"kind": "play_sound", "sound": "snd_a"},
                             {"kind": "if_collision", "object": "obj_a",
                              "children": [{"kind": "add_score", "value": "5"}]},
                         ]},
                         {"type": "collision", "object": "obj_a", "actions": [
                             {"kind": "add_score", "value": "10"}]},
                     ]}],
        "rooms": [{"id": "rm_a", "w": 240, "h": 160, "speed": 60,
                   "bg": "#101820", "instances": [{"object": "obj_a",
                                                   "x": 24, "y": 24}],
                   "tiles": [2] + [0] * 599}],
        "start_room": "rm_a",
    }


def refs_in(proj):
    """Every id a project points AT, gathered from the model itself, so a test
    can ask 'did anything keep pointing at the old name?'"""
    out = []
    for o in proj.get("objects", []):
        out.append(("object.sprite", o.get("sprite")))
        for ev in o.get("events", []):
            if ev.get("type") == "collision":
                out.append(("collision-event.object", ev.get("object")))
            def walk(acts):
                for a in acts or []:
                    for key in ("object", "sprite", "room", "sound"):
                        if key in a:
                            out.append(("%s.%s" % (a.get("kind"), key), a[key]))
                    walk(a.get("children"))
            walk(ev.get("actions"))
    for rm in proj.get("rooms", []):
        for it in rm.get("instances", []):
            out.append(("instance.object", it.get("object")))
        # A door names the room it leads to. This enumerator missed them for
        # the same reason the app's own walker did — doors arrived later — so
        # the harness could not have caught the bug it was there to catch.
        for wp in rm.get("warps", []) or []:
            out.append(("door.room", wp.get("room")))
    out.append(("start_room", proj.get("start_room")))
    return out


# ============================================================ what this drives
# The suite calls the app's own methods, so a rename inside gbasdk.py turns
# every later section into a traceback with no summary. Say so up front instead:
# a name on this list that has gone is a job for whoever renamed it, not a
# defect in the app.
NEEDED = [
    "_res", "_add_resource", "_select_resource", "_refresh_editor",
    "_render_tree", "_rename_resource", "_do_rename", "_refs_to",
    "_delete_resource", "_sel_res", "_all_tiles", "_room_tilemap",
    "_cur_sprite", "_cur_room", "_cur_event", "_set_paint",
    "_set_tool", "_on_sprite_paint", "_on_tile_paint", "_on_snd_click",
    "_on_room_click", "_on_room_motion", "_blit_sprite_preview",
    "_add_frame", "_dup_frame", "_del_frame", "_select_frame",
    "_add_tile", "_del_tile", "_select_tile", "_add_event", "_do_add_event",
    "_event_label", "_select_event", "_del_event", "_add_action",
    "_add_action_into", "_move_action", "_del_action", "_param_widget",
    "_pick_room_tile", "_room_clear", "_file_new", "_file_open",
    "_file_save_as", "_file_export", "_file_example", "_example_project",
    "_save_autosave", "_load_autosave", "_failure_reason", "menu_items",
]
# either name for the pixel-canvas geometry helper will do
NEEDED.append(("_canvas_geom", "_spr_cell"))
_gone = [n for n in NEEDED
         if not (any(hasattr(gbasdk.GbaSdk, a) for a in n)
                 if isinstance(n, tuple) else hasattr(gbasdk.GbaSdk, n))]
_gone = [" or ".join(n) if isinstance(n, tuple) else n for n in _gone]
if _gone:
    print("THIS SUITE IS OUT OF DATE WITH gbasdk.py — it drives these methods "
          "and they are gone:\n  %s\nRename them here too and re-run; nothing "
          "was tested." % "\n  ".join(_gone))
    shutil.rmtree(HOME, ignore_errors=True)
    sys.exit(2)

# ===================================================================== 1
section("resources: add / select / defaults")
w = app()
KINDS = ("sprite", "tileset", "sound", "object", "room")
for kind in KINDS:
    w._add_resource(kind)
pump()
check("adding one of every resource type fills the tree",
      [len(w._res(k)) for k in KINDS] == [1] * 5,
      [len(w._res(k)) for k in KINDS])
check("a new sprite is a 16x16 one-frame picture",
      w.proj["sprites"][0]["w"] == 16
      and len(w.proj["sprites"][0]["frames"]) == 1
      and len(w.proj["sprites"][0]["frames"][0]) == 256)
check("a new tileset starts with one 8x8 tile",
      len(w.proj["tilesets"][0]["tiles"]) == 1
      and len(w.proj["tilesets"][0]["tiles"][0]) == 64)
check("a new sound is 16 steps on two channels",
      len(w.proj["sounds"][0]["lead"]) == 16
      and len(w.proj["sounds"][0]["bass"]) == 16)
check("a new object has no events yet", w.proj["objects"][0]["events"] == [])
check("the first room becomes the start room",
      w.proj["start_room"] == w.proj["rooms"][0]["id"])
for kind in KINDS:
    w._select_resource(kind, 0)
    pump()
    if not check("selecting a %s shows the %s editor" % (kind, kind),
                 w._editor_stack.get_visible_child_name() == kind,
                 w._editor_stack.get_visible_child_name()):
        break
ids = [r["id"] for k in KINDS for r in w._res(k)]
for kind in KINDS:
    w._add_resource(kind)
ids2 = [r["id"] for k in KINDS for r in w._res(k)]
check("a second resource of each type gets its own id",
      len(set(ids2)) == len(ids2), ids2)
check("adding a resource writes the autosave at once", os.path.isfile(CFG))
w._sel = None
check("with nothing selected the editor falls back to the welcome pane",
      (w._refresh_editor(),
       w._editor_stack.get_visible_child_name())[1] == "welcome")
w.destroy()

# ==================================================================== 1b
section("segmented controls: pick-one rows")
w = app(wired_project())
w._select_resource("sprite", 0)
pump()
ARGS = {"_set_tool": ("fill", "pen"), "_pick_channel": ("bass", "lead"),
        "_set_room_mode": ("tiles", "objects"), "_set_room_zoom": (1, 2)}
present = [n for n in SEGMENTED if hasattr(w, n)]
check("the app still has the pick-one rows this section covers", bool(present),
      SEGMENTED)
for name in present:
    wedged = ""
    for arg in ARGS[name]:
        try:
            getattr(w, name)(arg)
        except RuntimeError as e:
            wedged = str(e)
        except Exception as e:                # a wrong argument is not the point
            wedged = wedged or ""
    depth = w._maxdepth.get(name, 0)
    check("%s: choosing one option does not send the app into a loop" % name,
          not wedged and depth <= 1,
          "re-entered itself (depth %d). set_active() on a Gtk.ToggleButton "
          "emits BOTH toggled AND clicked, and these buttons are connected to "
          "clicked, so the setter calls itself for every button, for ever — "
          "the editor wedges in a RecursionError storm" % depth)
w.destroy()

# ===================================================================== 2
section("rename: the references that must follow it")
w = app(wired_project())
rename(w, "object", 0, "hero")
pump()
stale = [site for site, val in refs_in(w.proj) if val == "obj_a"]
check("renaming an object re-points the room instances that place it",
      "instance.object" not in stale, stale)
check("renaming an object re-points a Collision EVENT that targets it",
      "collision-event.object" not in stale, stale)
check("renaming an object re-points Create Instance",
      "create_instance.object" not in stale, stale)
check("renaming an object re-points Destroy Object",
      "destroy_object.object" not in stale, stale)
check("renaming an object re-points a nested If Collision",
      "if_collision.object" not in stale, stale)

rename(w, "sprite", 0, "picture")
stale = [site for site, val in refs_in(w.proj) if val == "spr_a"]
check("renaming a sprite re-points the object that wears it",
      "object.sprite" not in stale, stale)
check("renaming a sprite re-points Change Sprite",
      "change_sprite.sprite" not in stale, stale)

rename(w, "room", 0, "level")
stale = [site for site, val in refs_in(w.proj) if val == "rm_a"]
check("renaming a room re-points the start-room flag",
      "start_room" not in stale, stale)
check("renaming a room re-points Go To Room",
      "goto_room.room" not in stale, stale)

rename(w, "sound", 0, "chime")
stale = [site for site, val in refs_in(w.proj) if val == "snd_a"]
check("renaming a sound re-points Play Sound",
      "play_sound.sound" not in stale, stale)

# ---- doors, which reference a room the same way everything else does ----
# Renaming a room used to leave every door into it pointing at a name that no
# longer existed, and the delete confirm said "used 0 times" while a door used
# it. Both because room warps were never added to the reference walker.
def two_rooms():
    return {"name": "Doors", "sprites": [], "sounds": [], "tilesets": [],
            "objects": [], "start_room": "rm_a",
            "rooms": [{"id": "rm_a", "w": 240, "h": 160, "instances": [],
                       "tiles": [], "warps": [{"x": 0, "y": 0, "w": 8, "h": 8,
                                               "room": "rm_b"}]},
                      {"id": "rm_b", "w": 240, "h": 160, "instances": [],
                       "tiles": [], "warps": []}]}

section("a door follows the room it leads to")
wd = app(two_rooms())
check("a door counts as a use of the room it leads to",
      wd._refs_to("room", "rm_b") == 1, wd._refs_to("room", "rm_b"))
rename(wd, "room", 1, "rm_cave")
door = wd.proj["rooms"][0]["warps"][0]
check("renaming a room re-points the doors that lead to it",
      door.get("room") == "rm_cave", door)
check("...leaving no door pointing nowhere",
      not [w_ for r in wd.proj["rooms"] for w_ in (r.get("warps") or [])
           if w_.get("room") not in [x["id"] for x in wd.proj["rooms"]]],
      wd.proj["rooms"])
wd.destroy()

wd = app(two_rooms())
wd._forget_refs("room", "rm_b")
del wd.proj["rooms"][1]
door = wd.proj["rooms"][0]["warps"][0]
check("deleting a room empties the doors that led to it",
      door.get("room") == "", door)
check("...and the door still remembers where it went",
      door.get("_was") == "rm_b", door)
wd.destroy()

w2 = app(wired_project())
w2._add_resource("sprite")            # spr_1 alongside spr_a
rename(w2, "sprite", 1, "spr_a")
sids = [s["id"] for s in w2.proj["sprites"]]
check("renaming cannot give two resources the same name",
      len(set(sids)) == len(sids), sids)
rename(w2, "sprite", 0, "")
check("an empty new name is refused", w2.proj["sprites"][0]["id"] != "",
      w2.proj["sprites"][0]["id"])
rename(w2, "sprite", 0, "my sprite!")
check("a name with spaces and punctuation becomes a safe id",
      w2.proj["sprites"][0]["id"] == gbabuild._cid("my sprite!"),
      w2.proj["sprites"][0]["id"])
w2.destroy()

# a rename must survive the reload, not just the model in memory
w._save_autosave()
reloaded = json.load(open(CFG))
check("a rename is persisted", [s["id"] for s in reloaded["sprites"]] == ["picture"],
      [s["id"] for s in reloaded["sprites"]])
w.destroy()

# ===================================================================== 3
section("delete: what it warns about, and what it leaves behind")
w = app(wired_project())
check("a sprite's use count counts the object AND the Change Sprite action",
      w._refs_to("sprite", "spr_a") == 2, w._refs_to("sprite", "spr_a"))
check("an object's use count counts the instance, the actions and the event",
      w._refs_to("object", "obj_a") == 5, w._refs_to("object", "obj_a"))
check("a room's use count counts the start flag and Go To Room",
      w._refs_to("room", "rm_a") == 2, w._refs_to("room", "rm_a"))
check("a sound's use count counts Play Sound",
      w._refs_to("sound", "snd_a") == 1, w._refs_to("sound", "snd_a"))
check("a tileset's use count counts the room cells painted with its tiles",
      w._refs_to("tileset", "ts_a") >= 1, w._refs_to("tileset", "ts_a"))

delete(w, "sprite", 0, yes=False)
check("Delete asks before it destroys anything", len(w._asked) == 1, w._asked)
check("...and Cancel keeps the sprite", len(w.proj["sprites"]) == 1)
delete(w, "sprite", 0, yes=True)
check("confirming Delete removes the sprite", w.proj["sprites"] == [])
check("...and nothing is left selected", w._sel is None)
dangling = [site for site, val in refs_in(w.proj) if val == "spr_a"]
check("deleting a sprite leaves no reference pointing at it",
      not dangling, dangling)
check("the app survives a delete with references outstanding",
      (w._refresh_editor(), True)[1])
problems = gbabuild.check_project(w.proj)
check("Compile & Export warns that a reference was left dangling",
      any("spr_a" in p for p in problems),
      problems or "check_project() reported nothing")

w3 = app(wired_project())
delete(w3, "tileset", 0, yes=True)
tiles_left = len(w3._all_tiles())
painted = max(w3.proj["rooms"][0]["tiles"])
check("deleting a tileset does not leave rooms painted with tiles that are gone",
      painted <= tiles_left, "room still paints tile %d of %d" % (painted, tiles_left))
w3.destroy()

w4 = app(wired_project())
delete(w4, "room", 0, yes=True)
check("deleting the start room clears the start-room flag",
      w4.proj.get("start_room") in (None, "") or
      w4.proj["start_room"] in [r["id"] for r in w4.proj["rooms"]],
      w4.proj.get("start_room"))
w4._add_resource("room")
check("...so the next room made becomes the start room",
      w4.proj["start_room"] == w4.proj["rooms"][-1]["id"],
      w4.proj.get("start_room"))
w4.destroy()
w.destroy()

# ===================================================================== 4
section("events")
w = app(wired_project())
w._select_resource("object", 0)
before = len(w.proj["objects"][0]["events"])
for i, (kind, label) in enumerate(gbasdk.EVENT_KINDS):
    w._pick = i
    w._add_event()
pump()
evs = w.proj["objects"][0]["events"]
check("every event in the Add Event list can be added",
      len(evs) == before + len(gbasdk.EVENT_KINDS),
      "%d of %d" % (len(evs) - before, len(gbasdk.EVENT_KINDS)))
added = evs[before:]
check("every added event carries a type and an actions list",
      all(isinstance(e.get("type"), str) and isinstance(e.get("actions"), list)
          for e in added))
check("every added event gets a label that is not the fallback '?'",
      all(w._event_label(e) not in ("?", "") for e in added),
      [w._event_label(e) for e in added if w._event_label(e) in ("?", "")])
keyed = [e for e in added if e["type"] in ("key", "keypress", "keyrelease")]
check("every key event names a key the compiler knows",
      all(gbabuild.KEY_MACRO.get(str(e.get("key")).lower()) for e in keyed),
      [e.get("key") for e in keyed
       if not gbabuild.KEY_MACRO.get(str(e.get("key")).lower())])
alarms = [e for e in added if e["type"] == "alarm"]
check("every alarm event is in the 0..3 range the runtime has",
      all(0 <= int(e.get("alarm", -1)) < 4 for e in alarms),
      [e.get("alarm") for e in alarms])
n = len(evs)
w._select_event(3)
w._del_event(3)
check("deleting an event removes exactly one", len(w.proj["objects"][0]["events"]) == n - 1)
w._del_event(999)
check("deleting an event that is not there does nothing",
      len(w.proj["objects"][0]["events"]) == n - 1)
w._sel_event = None
w._add_action("destroy_self")
check("adding an action with no event selected says so, and adds nothing",
      "event" in (w._status.get_text() or "").lower(),
      w._status.get_text())
w.destroy()

# ===================================================================== 5
section("actions: add, params, nesting, reorder, delete")
w = app(wired_project())
w._select_resource("object", 0)
w._select_event(0)
ev = w._cur_event()
ev["actions"] = []
for kind, _label, _params in gbasdk.ACTION_DEFS:
    w._add_action(kind)
pump()
check("every action in the palette can be added",
      len(ev["actions"]) == len(gbasdk.ACTION_DEFS),
      "%d of %d" % (len(ev["actions"]), len(gbasdk.ACTION_DEFS)))
check("every added action carries every parameter its form shows",
      all(all(k in a for k, _l, _s in gbasdk.ACTION_PARAMS.get(a["kind"], []))
          for a in ev["actions"]))
check("every container action is born with a children list",
      all(isinstance(a.get("children"), list) for a in ev["actions"]
          if a["kind"] in gbasdk.CONTAINER_ACTIONS))

# The point of an action is the code it makes. An action a beginner adds and
# does not touch must either DO something or be reported as unfinished --
# silently emitting nothing is the failure mode check_project() exists for.
silent = []
for kind, label, params in gbasdk.ACTION_DEFS:
    ev["actions"] = []
    w._add_action(kind)
    c = gbabuild.generate_c(w.proj)
    body = c.split("static void obj_a_step(Instance* self) {")[1].split("\n}")[0]
    lines = [l.strip() for l in body.splitlines()
             if l.strip() and l.strip() != "(void)self;"]
    if not lines and kind != "execute_code":     # empty code really is nothing
        silent.append(label)
check("no action is a silent no-op the moment it is added",
      not silent, "%d actions emit nothing and raise no problem: %s"
      % (len(silent), ", ".join(silent)))

ev["actions"] = []
w._add_action("if_var")
w._add_action("set_score")
w._add_action("add_score")
outer = ev["actions"][0]
w._pick = [k for k, _l, _p in gbasdk.ACTION_DEFS].index("add_lives")
w._add_action_into(outer["children"])
pump()
check("an action can be added INSIDE a container action",
      [a["kind"] for a in outer["children"]] == ["add_lives"],
      outer.get("children"))
w._pick = [k for k, _l, _p in gbasdk.ACTION_DEFS].index("repeat")
w._add_action_into(outer["children"])
w._pick = [k for k, _l, _p in gbasdk.ACTION_DEFS].index("add_health")
w._add_action_into(outer["children"][1]["children"])
check("containers nest two deep",
      [a["kind"] for a in outer["children"][1]["children"]] == ["add_health"],
      outer["children"][1].get("children"))

kinds = [a["kind"] for a in ev["actions"]]
w._move_action(ev["actions"], 2, -1)
check("moving an action up swaps it with the one above",
      [a["kind"] for a in ev["actions"]] == [kinds[0], kinds[2], kinds[1]],
      [a["kind"] for a in ev["actions"]])
w._move_action(ev["actions"], 0, -1)
check("moving the top action up does nothing",
      [a["kind"] for a in ev["actions"]] == [kinds[0], kinds[2], kinds[1]])
w._move_action(ev["actions"], 2, 1)
check("moving the last action down does nothing",
      [a["kind"] for a in ev["actions"]] == [kinds[0], kinds[2], kinds[1]])
w._move_action(outer["children"], 1, -1)
check("a nested action reorders inside its own list",
      [a["kind"] for a in outer["children"]] == ["repeat", "add_lives"],
      [a["kind"] for a in outer["children"]])
w._del_action(outer["children"], 0)
check("a nested action can be deleted",
      [a["kind"] for a in outer["children"]] == ["add_lives"])
n = len(ev["actions"])
w._del_action(ev["actions"], 1)
check("deleting an action removes exactly one", len(ev["actions"]) == n - 1)
w._del_action(ev["actions"], 99)
check("deleting an action that is not there does nothing", len(ev["actions"]) == n - 1)

# parameter editing through the real widgets
ev["actions"] = []
w._add_action("jump_to")
act = ev["actions"][0]
row = w._param_widget(act, "x", "int")
row.set_text("64")
check("typing in a number parameter reaches the model", act["x"] == "64", act)
combo = w._param_widget(act, "x", ["a", "b", "c"])
combo.set_active_id("b")
check("choosing from a list parameter reaches the model", act["x"] == "b", act)
ev["actions"] = []
w._add_action("play_sound")
snd = w._param_widget(ev["actions"][0], "sound", "snd")
check("a resource dropdown offers the project's resources",
      snd.get_active_id() is not None or snd.get_model().iter_n_children(None) > 0,
      "no options offered")
check("a resource dropdown opens with a real resource chosen, not blank",
      snd.get_active_id() in [s["id"] for s in w.proj["sounds"]],
      "shows %r; the action will compile to nothing"
      % (snd.get_active_id(),))
w.destroy()

# ===================================================================== 6
section("sprite editor")
w = app()
w._add_resource("sprite")
pump()
s = w.proj["sprites"][0]
red = gbasdk.PALETTE[7][1]
white = gbasdk.PALETTE[5][1]
w._set_paint(red)
spr_click(w, 0, 0)
spr_click(w, 3, 2)
check("the pen paints the pixel that was clicked",
      [i for i, v in enumerate(s["frames"][0]) if v != gbasdk.TRANSPARENT]
      == [0, 2 * 16 + 3],
      [i for i, v in enumerate(s["frames"][0]) if v != gbasdk.TRANSPARENT])
w._set_paint(white)
spr_click(w, 8, 8, tool="fill")
filled = sum(1 for v in s["frames"][0] if v == white)
check("the fill tool floods the region it was clicked in, not the whole frame",
      filled == 254, filled)
w._set_paint(gbasdk.TRANSPARENT)
spr_click(w, 0, 0, tool="pick")
check("the pick tool takes the colour under the cursor",
      w._paint_color == red, hex(w._paint_color))
n_before = sum(1 for v in s["frames"][0] if v != gbasdk.TRANSPARENT)
spr_click(w, 99, 99)
check("clicking outside the picture changes nothing",
      sum(1 for v in s["frames"][0] if v != gbasdk.TRANSPARENT) == n_before)

w._add_frame()
check("Add Frame adds a blank frame and selects it",
      len(s["frames"]) == 2 and w._sel_frame == 1
      and all(v == gbasdk.TRANSPARENT for v in s["frames"][1]))
w._select_frame(0)
w._dup_frame()
check("Duplicate Frame copies the selected frame next to it",
      len(s["frames"]) == 3 and s["frames"][1] == s["frames"][0]
      and w._sel_frame == 1)
w._del_frame()
check("Delete Frame removes it", len(s["frames"]) == 2)
w._select_frame(0)
w._del_frame()
w._del_frame()
check("the last frame cannot be deleted", len(s["frames"]) == 1)

pixels = list(s["frames"][0])
w._suspend = False
w._spr_size.set_active_id("32x32")
pump()
check("changing the size re-shapes every frame",
      s["w"] == 32 and s["h"] == 32
      and all(len(f) == 32 * 32 for f in s["frames"]),
      (s["w"], s["h"], [len(f) for f in s["frames"]]))
check("...and keeps the pixels already drawn",
      [s["frames"][0][j * 32 + i] for j in range(16) for i in range(16)] == pixels)
check("...and re-centres the origin", (s["ox"], s["oy"]) == (16, 16))
w._spr_size.set_active_id("8x8")
pump()
check("shrinking a sprite keeps the top-left corner",
      len(s["frames"][0]) == 64 and s["frames"][0][0] == pixels[0])
check("every size the editor offers is one the GBA can show",
      all(sz in gbabuild._Gen.OBJ_DIMS for sz in gbasdk.SPRITE_SIZES),
      [sz for sz in gbasdk.SPRITE_SIZES if sz not in gbabuild._Gen.OBJ_DIMS])
w._spr_anim.set_value(8)
check("the animation speed reaches the model", s["anim_speed"] == 8, s.get("anim_speed"))
w.destroy()

# ===================================================================== 7
section("tileset editor")
w = app()
w._add_resource("tileset")
pump()
ts = w.proj["tilesets"][0]
w._set_paint(red)
tile_click(w, 0, 0)
painted = [i for i, v in enumerate(ts["tiles"][0]) if v != gbasdk.TRANSPARENT]
check("painting a tile changes one of its 64 pixels", len(painted) == 1, painted)
w._add_tile()
check("Add Tile adds a blank 8x8 tile and selects it",
      len(ts["tiles"]) == 2 and w._sel_tile == 1
      and len(ts["tiles"][1]) == 64)
w._del_tile()
check("Delete Tile removes it", len(ts["tiles"]) == 1)
w._del_tile()
check("the last tile cannot be deleted", len(ts["tiles"]) == 1)

# ---- a size change re-numbers the rooms, like a delete does ----
# Rooms index ONE combined list of every set's hardware tiles. A 16px tile
# occupies four entries where an 8px tile occupies one, so changing a set's
# size shifts every LATER set along — the same corruption _forget_tiles
# prevents on delete, arriving through the size combo instead.
def two_sets():
    b8 = [0] * 64
    return {"name": "TS", "sprites": [], "sounds": [], "objects": [],
            "start_room": "rm",
            "tilesets": [{"id": "ts_A", "size": 8, "tiles": [b8, b8]},
                         {"id": "ts_C", "size": 8, "tiles": [b8, b8, b8]}],
            "rooms": [{"id": "rm", "w": 240, "h": 160, "instances": [],
                       "warps": [], "tiles": [1, 2, 3, 4, 5]}]}

wt = app(two_sets())
wt._resize_tiles(wt.proj["tilesets"][0], 8, 16)
wt.proj["tilesets"][0]["size"] = 16
wt.proj["tilesets"][0]["tiles"] = [[0] * 256, [0] * 256]
lo, hi = wt._tileset_range("ts_C")
cells = wt.proj["rooms"][0]["tiles"]
check("growing a tile set keeps the LATER set's cells pointing at it",
      all(lo < v <= hi for v in cells[2:]), (cells, (lo, hi)))
check("...and its own cells still name the tile they showed",
      cells[:2] == [1, 5], cells)
wt.destroy()

wt = app(two_sets())
wt.proj["tilesets"][0].update({"size": 16, "tiles": [[0] * 256, [0] * 256]})
wt.proj["rooms"][0]["tiles"] = [1, 5, 9, 10, 11]
wt._resize_tiles(wt.proj["tilesets"][0], 16, 8)
wt.proj["tilesets"][0].update({"size": 8, "tiles": [[0] * 64, [0] * 64]})
check("shrinking it again puts every cell back where it started",
      wt.proj["rooms"][0]["tiles"] == [1, 2, 3, 4, 5],
      wt.proj["rooms"][0]["tiles"])
wt.destroy()

w.destroy()

# ===================================================================== 8
section("sound composer")
w = app()
w._add_resource("sound")
pump()
snd = w.proj["sounds"][0]
w._snd_chan = "lead"
snd_click(w, 0, 60)
check("clicking the piano roll writes a note on the active channel",
      snd["lead"][0] == 60, snd["lead"][:4])
snd_click(w, 0, 60)
check("clicking the same note again clears it", snd["lead"][0] == 0, snd["lead"][:4])
w._snd_chan = "bass"
snd_click(w, 0, 48)
check("the bass channel is written separately",
      snd["bass"][0] == 48 and snd["lead"][0] == 0, (snd["lead"][0], snd["bass"][0]))
w._on_snd_click(w._snd_canvas, btn(2, 2))
check("clicking the pitch gutter writes nothing", snd["bass"][0] == 48)
w._suspend = False
w._snd_steps.set_active_id("32")
pump()
check("lengthening a sound keeps the notes already written",
      snd["steps"] == 32 and len(snd["lead"]) == 32 and snd["bass"][0] == 48,
      (snd.get("steps"), len(snd["lead"]), snd["bass"][:2]))
w._snd_tempo.set_value(12)
check("the tempo reaches the model", snd["tempo"] == 12, snd.get("tempo"))
w._snd_loop.set_active(False)
check("the loop switch reaches the model", snd["loop"] is False, snd.get("loop"))
w.destroy()

# ===================================================================== 9
section("room editor")
w = app(wired_project())
w._select_resource("room", 0)
pump()
rm = w.proj["rooms"][0]
rm["instances"] = []
w._room_mode = "objects"
w._room_place = "obj_a"
room_click(w, 40, 40)
check("clicking the room places the chosen object",
      [(i["object"], i["x"], i["y"]) for i in rm["instances"]] == [("obj_a", 48, 48)],
      rm["instances"])
room_click(w, 48, 48, b=3)
check("right-clicking removes the instance under the cursor", rm["instances"] == [])
room_click(w, 200, 140, b=3)
check("right-clicking empty floor removes nothing and does not crash",
      rm["instances"] == [])
room_click(w, 10, 10)
room_click(w, 100, 100)
check("two objects can be placed", len(rm["instances"]) == 2)
w._room_clear()
check("Clear empties the room", rm["instances"] == [])

w._room_mode = "tiles"
w._room_tile = 2
w._on_room_click(w._room_canvas, btn(0, 0))
tm, cw, ch = w._room_tilemap(rm)
check("painting in Tiles mode writes the tile map, not an instance",
      tm[0] == 2 and rm["instances"] == [], (tm[0], rm["instances"]))
w._on_room_motion(w._room_canvas, btn(16, 0, motion=True))
check("dragging paints the cells it passes over", w._room_tilemap(rm)[0][1] == 2,
      w._room_tilemap(rm)[0][:4])
w._on_room_click(w._room_canvas, btn(0, 0, b=3))
check("right-clicking in Tiles mode erases the cell", w._room_tilemap(rm)[0][0] == 0)
w._pick_room_tile(1)
check("picking from the tile palette changes what is painted", w._room_tile == 1)

w._suspend = False
w._room_w.set_value(320)
pump()
check("changing the room width reaches the model", rm["w"] == 320, rm.get("w"))
tm, cw, ch = w._room_tilemap(rm)
check("...and the tile map is re-shaped to the new size",
      len(tm) == (320 // 8) * (rm["h"] // 8), (len(tm), cw, ch))
w._room_speed.set_value(30)
check("changing the game speed reaches the model", rm["speed"] == 30)

w._add_resource("room")
w._select_resource("room", 1)
pump()
w._room_start.set_active(True)
check("ticking Start room moves the start flag to this room",
      w.proj["start_room"] == w.proj["rooms"][1]["id"], w.proj.get("start_room"))
w._room_start.set_active(False)
check("un-ticking Start room does not leave the tick-box lying",
      w.proj.get("start_room") != w.proj["rooms"][1]["id"]
      or w._room_start.get_active(),
      "box is clear but %r is still the start room"
      % (w.proj.get("start_room"),))

# What the room shows must be the sprite the game will show. A tall or wide
# sprite is one of the twelve sizes the editor offers, so the preview has to
# read both dimensions.
import cairo                                                 # noqa: E402
tall = {"id": "spr_tall", "w": 16, "h": 32, "ox": 8, "oy": 16,
        "frames": [[gbasdk.PALETTE[7][1]] * (16 * 32)]}
surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 240, 160)
w._blit_sprite_preview(cairo.Context(surf), tall, 60, 80, 1)
surf.flush()
data, stride = surf.get_data(), surf.get_stride()
lit = [(x, y) for y in range(160) for x in range(240)
       if data[y * stride + x * 4 + 3]]
box = ((max(x for x, _y in lit) - min(x for x, _y in lit) + 1,
        max(y for _x, y in lit) - min(y for _x, y in lit) + 1) if lit else (0, 0))
check("a tall sprite is previewed at its real height in the room",
      box == (16, 32), "painted %dx%d for a 16x32 sprite" % box)
w.destroy()

# ==================================================================== 10
section("undo")
w = app()
hist = getattr(w, "undo", None)
check("the app has an undo history",
      hist is not None,
      "NO UNDO ANYWHERE: a pixel editor, a tile map and a piano roll with no "
      "way back, and Delete Resource says so in its own confirm text")
check("Edit menu offers Undo",
      any("undo" in str(it[0]).lower() for it in (w.menu_items("Edit") or [])
          if isinstance(it, tuple)),
      [it[0] for it in (w.menu_items("Edit") or []) if isinstance(it, tuple)])
if hist is not None:
    # Everything in this app edits ONE document, so one history has to cover
    # all six editors. Each case below makes an edit through the real handler,
    # undoes it, and asks for the document back exactly as it was.
    w._add_resource("sprite")
    w._add_resource("object")
    w._add_resource("room")
    pump()
    hist.flush()

    def undo_restores(label, select, edit, read):
        """Make one edit, undo it, and demand the document back unchanged.

        `select` runs first and again after the undo: restoring a document is
        entitled to drop the selection, and the next case must not then be
        editing nothing."""
        def snap():
            try:
                return json.loads(json.dumps(read()))
            except Exception as e:
                return "unreadable: %s" % e
        select()
        pump()
        before = snap()
        edit()
        pump()
        hist.flush()
        changed = snap() != before
        did = hist.undo()
        pump()
        after = snap()
        check("undo takes back %s" % label,
              changed and did and after == before,
              "the edit %s, undo returned %s, and the document %s"
              % ("did not happen" if not changed else "happened", did,
                 "came back" if after == before else "did NOT come back"))
        select()
        pump()

    sel_sprite = lambda: w._select_resource("sprite", 0)
    undo_restores("a painted pixel", sel_sprite,
                  lambda: (w._set_paint(gbasdk.PALETTE[7][1]), spr_click(w, 4, 4)),
                  lambda: w.proj["sprites"][0]["frames"])
    undo_restores("a new frame", sel_sprite, w._add_frame,
                  lambda: w.proj["sprites"][0]["frames"])
    undo_restores("a rename", sel_sprite,
                  lambda: rename(w, "sprite", 0, "renamed_by_undo_test"),
                  lambda: [s["id"] for s in w.proj["sprites"]])
    undo_restores("a deleted resource",
                  lambda: w._select_resource("object", 0),
                  lambda: delete(w, "object", 0, yes=True),
                  lambda: w.proj["objects"])

    def sel_room_objects():
        w._select_resource("room", 0)
        w._room_mode = "objects"
        w._room_place = (w.proj["objects"][0]["id"] if w.proj["objects"]
                         else None)
    undo_restores("an object placed in a room", sel_room_objects,
                  lambda: room_click(w, 48, 48),
                  lambda: w.proj["rooms"][0]["instances"])
    w._add_resource("tileset")
    pump()
    hist.flush()

    def sel_room_tiles():
        w._select_resource("room", 0)
        w._room_mode = "tiles"
        w._room_tile = 1
    undo_restores("a painted room tile", sel_room_tiles,
                  lambda: w._on_room_click(w._room_canvas, btn(0, 0)),
                  lambda: list(w._room_tilemap(w.proj["rooms"][0])[0]))
    w._add_resource("sound")
    pump()
    hist.flush()
    undo_restores("a note in the piano roll",
                  lambda: w._select_resource("sound", 0),
                  lambda: snd_click(w, 0, 60),
                  lambda: w.proj["sounds"][0]["lead"])
    w._add_resource("object")
    w._select_resource("object", 0)
    w._do_add_event("step")
    pump()
    hist.flush()

    def sel_event():
        w._select_resource("object", 0)
        w._select_event(0)
    undo_restores("an added action", sel_event,
                  lambda: w._add_action("add_score"),
                  lambda: w.proj["objects"][0]["events"])
    check("redo puts the last undone edit back",
          hist.can_redo() and hist.redo(), "nothing to redo after an undo")
    for _ in range(400):
        if not hist.can_undo():
            break
        hist.undo()
    check("undo stops at the start of the session instead of running off the end",
          not hist.can_undo() and hist.undo() is False,
          "can_undo=%s" % hist.can_undo())
    check("undoing everything leaves a usable project, not a broken one",
          isinstance(w.proj, dict) and all(
              isinstance(w.proj.get(k), list)
              for k in ("sprites", "tilesets", "sounds", "objects", "rooms")),
          w.proj if not isinstance(w.proj, dict) else list(w.proj))
w.destroy()

# ==================================================================== 11
section("persistence")
w = app()
w._file_example()
pump()
# one edit of every kind on top of the example, so the round trip carries
# something from every editor
w._select_resource("sprite", 0)
w._set_paint(red)
spr_click(w, 1, 1)
w._add_frame()
w._add_resource("sound")
w._select_resource("sound", 0)
snd_click(w, 0, 55)
w._select_resource("tileset", 0)
w._add_tile()
w._select_resource("object", 0)
w._select_event(0)
w._add_action("add_score")
w._select_resource("room", 0)
w._room_mode = "tiles"
w._room_tile = 1
w._on_room_click(w._room_canvas, btn(0, 0))
pump()
before = json.loads(json.dumps(w.proj))
w.destroy()
pump()
w = app(keep=True)
after = w.proj
# The workspace records which panes are open in proj["layout"]. It is a real
# part of the document (a project carries its own arrangement) but it is a VIEW
# preference, not game data, so it is compared separately from the assets.
before.pop("layout", None)
after = {k: v for k, v in after.items() if k != "layout"}
check("every resource type survives close and reopen intact",
      after == before,
      [k for k in set(list(before) + list(after)) if before.get(k) != after.get(k)])
for kind in ("sprites", "tilesets", "sounds", "objects", "rooms", "scripts"):
    check("  ... %s round-trip" % kind, after.get(kind) == before.get(kind))

# A project written before scripts existed must gain the key EMPTY, and must
# lose nothing doing it. Reading is the side that has destroyed data in this OS
# before, so a new kind is checked against an old file the day it is added.
_old_shape = {"name": "Before Scripts", "sprites": [], "tilesets": [],
              "sounds": [], "objects": [{"id": "obj_1", "events": []}],
              "rooms": [{"id": "rm_1", "w": 240, "h": 160}],
              "start_room": "rm_1"}
_mig, _mig_lost = w._sane_project(json.loads(json.dumps(_old_shape)))
check("a project from before scripts still loads", _mig is not None)
check("...losing nothing", _mig_lost == 0)
check("...and gains an empty scripts list", _mig.get("scripts") == [])
# Same question for the fields Phase 8 added. A project made before them must
# gain them at their old-behaviour values, not lose its rooms to a stricter
# validator -- reading is the side that has destroyed data in this OS before.
_mig8, _lost8 = w._sane_project({
    "name": "Before Phase 8", "sprites": [], "sounds": [], "objects": [],
    "tilesets": [{"id": "ts_1", "size": 8, "tiles": [[0] * 64, [0] * 64]}],
    "rooms": [{"id": "rm_1", "w": 240, "h": 160}], "start_room": "rm_1"})
check("a project from before solid tiles still loads", _mig8 is not None)
check("...losing nothing", _lost8 == 0)
check("...and every tile defaults to not solid",
      _mig8["tilesets"][0]["solid"] == [False, False])
check("...with no parallax layer", _mig8["rooms"][0]["far"] is None)
check("...and a closed room edge", _mig8["rooms"][0]["edge_open"] is False)
check("...and no doorways", _mig8["rooms"][0]["warps"] == [])

# Warps. A door pointing at a deleted room must be REPORTED, not dropped in
# silence -- a door that goes nowhere is indistinguishable from one placed in
# the wrong spot, and only one of those is the author's mistake.
_warp_proj = {"name": "W", "sounds": [], "sprites": [], "objects": [],
              "tilesets": [], "scripts": [], "start_room": "rm_a",
              "rooms": [
                  {"id": "rm_a", "w": 240, "h": 160, "bg": "#000000",
                   "instances": [], "tiles": None,
                   "warps": [{"x": 100, "y": 0, "w": 16, "h": 8,
                              "room": "rm_b", "tx": 120, "ty": 140},
                             {"x": 0, "y": 0, "w": 8, "h": 8,
                              "room": "rm_gone", "tx": 0, "ty": 0}]},
                  {"id": "rm_b", "w": 240, "h": 160, "bg": "#000000",
                   "instances": [], "tiles": None, "warps": []}]}
_wc = gbabuild.generate_c(_warp_proj)
check("a warp reaches the generated C", "room_0_warps" in _wc)
check("...naming the destination by index",
      "{ 100, 0, 16, 8, 1, 120, 140 }," in _wc, _wc[_wc.find("nb_Warp"):][:120])
check("a warp to a deleted room is reported",
      any("doorway" in p for p in gbabuild.check_project(_warp_proj)),
      str(gbabuild.check_project(_warp_proj))[:140])
check("...and is not emitted",
      _wc.count("{ 0, 0, 8, 8,") == 0)
# The editor. Warps existed in the model and the runtime with no way to place
# one -- the same unreachable-feature shape as tile collision, which is the bug
# this phase started by fixing.
_dr = app()
_dr._file_example()
_dr._add_resource("room")
_dr._select_resource("room", 0)
pump()
for _mode, _place, _tile, _warp in (("objects", True, False, False),
                                    ("tiles", False, True, False),
                                    ("warps", False, False, True)):
    _dr._set_room_mode(_mode)
    pump()
    check("%s mode shows only its own palette" % _mode,
          _dr._room_obj.get_visible() is _place
          and _dr._room_tile_scroll.get_visible() is _tile
          and _dr._room_warp_to.get_visible() is _warp,
          "place=%s tiles=%s warps=%s" % (_dr._room_obj.get_visible(),
                                          _dr._room_tile_scroll.get_visible(),
                                          _dr._room_warp_to.get_visible()))

_dr._set_room_mode("warps")
pump()
check("a destination is chosen before a door is drawn",
      bool(getattr(_dr, "_warp_dest", None)))
# A door back into its own room fires the instant the traveller lands on it,
# so the room reloads forever. It is left out of the list rather than explained
# afterwards.
_dest_ids = [_dr._room_warp_to.get_model()[i][1]
             for i in range(len(_dr._room_warp_to.get_model()))]
check("the room being edited is not offered as a destination",
      _dr._cur_room().get("id") not in _dest_ids, str(_dest_ids))

_dr._on_room_click(_dr._room_canvas, btn(60, 60))
pump()
_w0 = _dr._cur_room().get("warps") or []
check("clicking places a door", len(_w0) == 1, str(_w0))
check("...with a destination", bool(_w0[0].get("room")) if _w0 else False)
check("...and an arrival point that is not the corner",
      (_w0[0].get("tx"), _w0[0].get("ty")) != (0, 0) if _w0 else False)
_dr._on_room_click(_dr._room_canvas, btn(60, 60))
check("clicking the same cell does not stack a second door",
      len(_dr._cur_room().get("warps") or []) == 1)
_dr._on_room_click(_dr._room_canvas, btn(60, 60, b=3))
check("right-click removes it", (_dr._cur_room().get("warps") or []) == [])
_dr.destroy()
pump()

check("a room with no warps carries a null pointer",
      ", 0, 0 }," in re.search(r"const nb_Room nb_rooms\[\].*?};",
                               _wc, re.S).group(0))
check("...with its objects intact", len(_mig.get("objects") or []) == 1)
check("the start room survives", after.get("start_room") == before.get("start_room"))

# .gbaproj save + open round trip
proj_path = os.path.join(HOME, "Documents", "mine.gbaproj")
nbpicker.save_file = lambda *a, **k: proj_path
nbpicker.open_file = lambda *a, **k: proj_path
w._file_save_as()
# A project is a BUNDLE now — a directory holding project.json and one file per
# kind — so that a 10,000-asset project is not one document to rewrite on every
# save. Reading it back is _bundle_read, not json.load.
check("Save Project As writes a .gbaproj bundle", os.path.isdir(proj_path))
check("...holding one file per kind",
      sorted(os.listdir(proj_path))
      == sorted([gbasdk.GbaSdk.BUNDLE_MARK]
                + [k + ".json" for k in gbasdk.GbaSdk.BUNDLE_PARTS]))
saved = w._bundle_read(proj_path) or {}
saved.pop("layout", None)
check("...containing the whole project", saved == before,
      [k for k in set(list(saved) + list(before)) if saved.get(k) != before.get(k)])
w._file_new()
w._file_open()
pump()
check("Open Project reads it back whole",
      {k: v for k, v in w.proj.items() if k != "layout"} == before,
      [k for k in set(list(w.proj) + list(before)) if w.proj.get(k) != before.get(k)])
check("opening a project keeps its tilesets",
      len(w.proj.get("tilesets") or []) == len(before.get("tilesets") or []))
bad = os.path.join(HOME, "Documents", "notaproject.gbaproj")
open(bad, "w").write("{}")
nbpicker.open_file = lambda *a, **k: bad
kept = json.loads(json.dumps(w.proj))
w._file_open()
check("opening a file that is not a project keeps the open one and says so",
      w.proj == kept and "GBA SDK" in (w._status.get_text() or ""),
      w._status.get_text())
w.destroy()

# ==================================================================== 12
section("code generator")
proj = wired_project()
c = gbabuild.generate_c(proj)
check("the generator emits C for a wired project", bool(c.strip()))
check("it includes the runtime header", '#include "runtime.h"' in c)
check("its braces balance", c.count("{") == c.count("}"),
      (c.count("{"), c.count("}")))
for sym in ("nb_obj_palette", "nb_obj_tiles", "nb_sprites", "nb_sprite_count",
            "nb_bg_palette", "nb_bg_tiles", "nb_sounds", "nb_sound_count",
            "nb_objects", "nb_object_count", "nb_rooms", "nb_room_count",
            "nb_start_room"):
    check("  it defines %s" % sym, sym in c)
check("it counts the project's resources",
      ("const int nb_sprite_count = 1;" in c
       and "const int nb_object_count = 1;" in c
       and "const int nb_room_count = 1;" in c
       and "const int nb_sound_count = 1;" in c),
      [l for l in c.splitlines() if "_count = " in l])
check("it emits a step function for an object that has events",
      "static void obj_a_step(Instance* self)" in c)
check("it wires the room's instance into the room table",
      "room_0_insts" in c and "{ 0, 24, 24 }" in c,
      [l for l in c.splitlines() if "room_0_insts" in l])
check("it emits the room's tile layer", "room_0_tiles" in c)
check("the start room is the one the project names",
      "const int nb_start_room = 0;" in c)
check("an empty project still generates compilable tables",
      all(s in gbabuild.generate_c({}) for s in
          ("nb_sprite_count = 0", "nb_object_count = 0", "nb_room_count = 0")))
check("the generator does not scribble private keys into the user's project",
      not [k for k in proj["objects"][0] if k.startswith("_")],
      sorted(k for k in proj["objects"][0] if k.startswith("_")))

# nothing a user can type may become C
evil = wired_project()
evil["objects"][0]["id"] = 'obj"; evil(); //'
evil["objects"][0]["events"][0]["actions"] = [
    {"kind": "draw_text", "text": '"); evil(); rt_draw_text(0,0,"', "x": "0", "y": "0"},
    {"kind": "draw_text", "text": "*/ evil(); /*", "x": "0", "y": "0"},
    {"kind": "set_hspeed", "value": "3; evil()"},
]
ec = gbabuild.generate_c(evil)
# A hostile string is allowed to APPEAR — inside a C string literal it is inert
# text. What must never happen is it appearing as a statement of its own.
loose = [l.strip() for l in ec.splitlines()
         if l.strip().startswith("evil") or l.strip().startswith("*/")]
check("a quote in a text action cannot close the C string and start a statement",
      not loose, loose)
check("...and the hostile text is escaped where it does appear",
      '\\"); evil' in ec, [l for l in ec.splitlines() if "evil" in l][:2])
# The one place model text really does land in a COMMENT is a GML error report.
cmt = wired_project()
cmt["objects"][0]["events"][0]["actions"] = [
    {"kind": "execute_code", "code": "*/ evil(); /* @"}]
cc = gbabuild.generate_c(cmt)
check("a comment-closer in Execute Code cannot end the comment it is reported in",
      "*/ evil" not in cc,
      [l for l in cc.splitlines() if "evil" in l][:2])
check("a number field that is not a number becomes 0",
      "self->hspeed = 0;" in ec,
      [l for l in ec.splitlines() if "hspeed" in l])
check("an id full of punctuation becomes a legal C identifier",
      "static void obj_____evil_______step" in ec or
      all(ch.isalnum() or ch == "_" for ch in
          ec.split("static void ")[1].split("(")[0]),
      ec.split("static void ")[1].split("(")[0] if "static void " in ec else "")

# The hardware limits. A GBA sprite holds 15 colours and the shared background
# palette holds 15 — but the paint palette offers 33, so going over is one
# click away and the cost is paid in the ROM, not on the canvas. Whatever the
# compiler does about it, the author has to be TOLD, exactly as they are told
# about a line of GML it could not use.
paint = [c for _n, c in gbasdk.PALETTE if c != gbasdk.TRANSPARENT][:20]
many = wired_project()
many["sprites"][0]["frames"] = [[paint[i % 20] for i in range(256)]]
gen = gbabuild._Gen(many)
gen.generate()
lost = [c for c in paint if gen._spr_cmap.get(0, {}).get(c, 0) == 0]
check("a sprite painted in more than 15 colours is reported before export",
      not lost or gbabuild.check_project(many),
      "%d of the 20 colours become transparent HOLES in the game and nothing "
      "says so" % len(lost))
manytiles = wired_project()
manytiles["tilesets"][0]["tiles"] = [[paint[i % 20] for i in range(64)]]
gen = gbabuild._Gen(manytiles)
gen.generate()
folded = [c for c in paint if gen._bg_cmap.get(c) in (None, 1)]
check("tiles painted in more than 15 colours are reported before export",
      len(folded) <= 1 or gbabuild.check_project(manytiles),
      "%d tile colours are silently replaced with another colour"
      % len(folded))

# bad GML must be reported, not silently swallowed
gml = wired_project()
gml["objects"][0]["events"][0]["actions"] = [
    {"kind": "execute_code", "code": "hspee = 3;\nif ( { }"}]
problems = gbabuild.check_project(gml)
check("a mistake in Execute Code is reported before the export",
      bool(problems), problems)
check("...naming the object and event it is in",
      any("obj_a" in p for p in problems), problems)

# ==================================================================== 13
section("compile & export")
gcc = gbabuild.find_gcc(TOOLCHAIN)
if not gcc:
    print("SKIP  no arm-none-eabi-gcc under %s — cannot compile for real" % TOOLCHAIN)
else:
    outdir = os.path.join(HOME, "build")
    ok, gba, log = gbabuild.build_rom(gbasdk.GbaSdk._example_project(None), outdir,
                                      runtime_dir=RUNTIME, toolchain_dir=TOOLCHAIN)
    check("the example game compiles to a real .gba", ok, log[-1200:])
    if ok:
        data = open(gba, "rb").read()
        check("the ROM is a plausible cartridge size",
              4096 <= len(data) <= 32 * 1024 * 1024, len(data))
        check("the ROM carries the Nintendo boot logo a console checks",
              data[4:0xA0] == gbabuild.NINTENDO_LOGO)
        s = 0
        for i in range(0xA0, 0xBD):
            s = (s + data[i]) & 0xFF
        check("the ROM header checksum is right",
              data[0xBD] == (-(0x19 + s)) & 0xFF)
    # a project using every action must still compile
    big = wired_project()
    ev = big["objects"][0]["events"][0]
    ev["actions"] = []
    for kind, _l, params in gbasdk.ACTION_DEFS:
        a = {"kind": kind}
        for key, _lbl, spec in params:
            if isinstance(spec, list):
                a[key] = spec[0]
            elif spec == "int":
                a[key] = "1"
            elif spec == "obj":
                a[key] = "obj_a"
            elif spec == "spr":
                a[key] = "spr_a"
            elif spec == "room":
                a[key] = "rm_a"
            elif spec == "snd":
                a[key] = "snd_a"
            elif spec == "code":
                a[key] = "x = x + 1;\nif (x > 100) { x = 0; }"
            else:
                a[key] = "v"
        if kind in gbasdk.CONTAINER_ACTIONS:
            a["children"] = [{"kind": "add_score", "value": "1"}]
        ev["actions"].append(a)
    ok2, gba2, log2 = gbabuild.build_rom(big, os.path.join(HOME, "build2"),
                                         runtime_dir=RUNTIME,
                                         toolchain_dir=TOOLCHAIN)
    check("a game using every action in the palette compiles", ok2, log2[-1500:])
    check("...with no compiler warnings",
          "warning:" not in (log2 or ""),
          [l for l in (log2 or "").splitlines() if "warning:" in l][:6])
    # the injection project must compile too: a hostile string must be inert C,
    # not broken C
    ok3, _g3, log3 = gbabuild.build_rom(evil, os.path.join(HOME, "build3"),
                                        runtime_dir=RUNTIME,
                                        toolchain_dir=TOOLCHAIN)
    check("a project full of hostile strings still compiles", ok3, log3[-1200:])

# a build with no compiler must fail with something a person can act on
ok4, _g4, log4 = gbabuild.build_rom(wired_project(), os.path.join(HOME, "build4"),
                                    runtime_dir=RUNTIME,
                                    toolchain_dir=os.path.join(HOME, "nope"))
check("a missing compiler is reported, not crashed on", ok4 is False)
w = app()
reason = w._failure_reason(log4)
check("...and turned into a sentence about the compiler, not the disk",
      "compiler" in reason.lower(), reason)
w.destroy()

# Compile & Export end to end, through the real menu action
w = app()
w._file_example()
pump()
rom = os.path.join(HOME, "Documents", "Example.gba")
nbpicker.save_file = lambda *a, **k: rom
# build_rom binds the runtime and toolchain paths as DEFAULT ARGUMENTS, so they
# are fixed at import; setting the module globals here would do nothing. Point
# the whole function at the in-tree overlay instead.
_real_build = gbabuild.build_rom
gbabuild.build_rom = (lambda model, outdir, runtime_dir=RUNTIME,
                      toolchain_dir=TOOLCHAIN, **kw:
                      _real_build(model, outdir, runtime_dir, toolchain_dir,
                                  **kw))
w._file_export()
pump()
if gcc:
    check("Compile & Export saves the .gba where the user chose",
          os.path.isfile(rom) and os.path.getsize(rom) > 4096,
          w._cards)
    check("...and says so in plain words",
          w._cards and any(k in w._cards[-1][0].lower()
                       for k in ("ready", "finished", "done")), w._cards)
    check("...and the exported game lands under the user's Home, where the "
          "emulator looks", rom.startswith(HOME))
# ---- the costing pane lines up in every script ----
# "%-22s" pads by CHARACTERS; a monospace terminal draws CJK glyphs two columns
# wide, so the numbers went ragged in Japanese and Chinese the moment the line
# names became translatable.
def _cols(s):
    import unicodedata as _u
    return sum(2 if _u.east_asian_width(c) in ("W", "F") else 1 for c in s)
check("padding counts columns, so a CJK name reaches the same width",
      _cols(gbasdk._pad("スプライトタイル", 22))
      == _cols(gbasdk._pad("背景タイル", 22)) == 22,
      (_cols(gbasdk._pad("スプライトタイル", 22)),
       _cols(gbasdk._pad("背景タイル", 22))))
check("...and a Latin name is padded exactly as before",
      gbasdk._pad("Sprite tiles", 22) == "%-22s" % "Sprite tiles")
check("...and a name already at the width is not truncated",
      gbasdk._pad("x" * 30, 22) == "x" * 30)

# ---- a misspelt variable name is no longer silent ----
# The script language gives a slot to any identifier it does not recognise, so
# `wobble = 7` needs no declaration — and `hspee = 2` compiled to a variable
# nothing reads while the object sat still. A slot lives on the instance and
# nothing outside the object can see it, so set-and-never-read and
# read-and-never-set are both certainly mistakes.
def _scripted(code):
    return {"name": "S", "tilesets": [], "sounds": [], "sprites": [],
            "start_room": "rm",
            "objects": [{"id": "obj_player", "sprite": "", "visible": True,
                         "solid": False, "events": [
                             {"type": "step", "actions": [
                                 {"kind": "execute_code", "lang": "Script",
                                  "code": code}]}]}],
            "rooms": [{"id": "rm", "w": 240, "h": 160, "speed": 60,
                       "bg": "#000",
                       "instances": [{"object": "obj_player", "x": 8, "y": 8}],
                       "warps": [], "tiles": [0] * 600}]}

_p = gbabuild.check_project(_scripted("hspee = 2"))
check("a misspelt built-in is reported instead of compiling to nothing",
      _p and "hspee" in _p[0], _p)
check("...and the name it was probably meant to be is offered",
      _p and "hspeed" in _p[0], _p)
check("reading a variable that is never set is reported too",
      any("nosuchthing" in x
          for x in gbabuild.check_project(_scripted("hspeed = nosuchthing"))),
      gbabuild.check_project(_scripted("hspeed = nosuchthing")))
check("a variable the author invents, sets and reads is left alone",
      not gbabuild.check_project(_scripted("wobble = 7\nhspeed = wobble")),
      gbabuild.check_project(_scripted("wobble = 7\nhspeed = wobble")))
check("a name far from any built-in gets no guess",
      "Did you mean" not in " ".join(
          gbabuild.check_project(_scripted("hspeed = nosuchthing"))))
check("the shipped example project raises none of this",
      not gbabuild.check_project(gbasdk.GbaSdk._example_project(None)),
      gbabuild.check_project(gbasdk.GbaSdk._example_project(None)))

# ---- every message the generator can produce is reachable ----
# Enumerating the templates and trying to trigger each one found three that
# could not be reached at all, and two more buried under noise the variable
# audit was adding. A message nobody can ever see is a promise the tool does
# not keep.
_r = lambda c: " | ".join(gbabuild.check_project(_scripted(c)))
check("assigning to a literal says so, rather than blaming the =",
      "not something you can assign to" in _r("1 = 2"), _r("1 = 2"))
check("assigning to a call's result says so too",
      "cannot assign to the result" in _r("abs(1) = 2"), _r("abs(1) = 2"))
check("a global read but never set anywhere is reported",
      "never set anywhere" in _r("hspeed = global.nope"),
      _r("hspeed = global.nope"))
check("a global that IS set is not",
      not _r("global.g = 1\nhspeed = global.g"),
      _r("global.g = 1\nhspeed = global.g"))
check("global is not mistaken for a variable called \"global\"",
      "reads global" not in _r("global. = 1"), _r("global. = 1"))
check("an unknown array is named as an array, not as a variable",
      "unknown array foo" in _r("foo[0] = 1") and "sets foo" not in _r("foo[0] = 1"),
      _r("foo[0] = 1"))
check("a bare call is still read as a call, not a broken assignment",
      not _r("instance_destroy()"), _r("instance_destroy()"))
check("a one-letter name gets no did-you-mean",
      "Did you mean" not in _r("c = 1"), _r("c = 1"))

# ---- a construct the language does not have names the one it does ----
# `for (i = 0; i < 3; i = i + 1)` parsed as a call to a function named `for`
# and was reported as "expected ) here, found =" — a complaint about a bracket,
# when what the author needs is which loop to write instead.
_nh = lambda c: gbabuild.check_project(_scripted(c))
_for = _nh("for (i = 0; i < 3; i = i + 1) { hspeed = i }")
check("a for loop is told which loops this language has",
      _for and "repeat" in _for[0] and "while" in _for[0], _for)
check("switch is pointed at if and else",
      any("if and else" in x for x in _nh("switch (x) { }")), _nh("switch (x) { }"))
check("an unsupported word is not also reported as an unset variable",
      len(_nh("function foo() { }")) == 1, _nh("function foo() { }"))
check("the loops the language does have still compile",
      not _nh("repeat (3) { hspeed = hspeed + 1 }")
      and not _nh("while (x < 3) { x = x + 1 }"),
      (_nh("repeat (3) { hspeed = hspeed + 1 }"),
       _nh("while (x < 3) { x = x + 1 }")))

# ---- the failure sentence depends on the log staying English ----
# _failure_reason picks the sentence an author reads by matching phrases in the
# BUILD LOG. Those phrases come from gbabuild, which now translates its problem
# messages — and if these three were ever translated too, every failure would
# fall through to the generic "the compiler stopped part-way through". The
# coupling is invisible from either side, so it is pinned here.
_fr = gbasdk.GbaSdk._failure_reason
_bsrc_txt = open(os.path.join(OVERLAY, "opt", "notebook", "de", "gbabuild.py"),
                 encoding="utf-8").read()
_phrases = re.findall(r'"([^"]+)" in low', _bsrc_txt) or []
_fnsrc = _bsrc_txt  # gbabuild's text, searched for the phrases _failure_reason wants
_sdk = open(os.path.join(OVERLAY, "opt", "notebook", "de", "gbasdk.py"),
            encoding="utf-8").read()
_want = re.findall(r'if "([^"]+)" in low', _sdk)
check("_failure_reason matches on more than one phrase", len(_want) >= 3, _want)
_absent = [w for w in _want if w.lower() not in _fnsrc.lower()
           and w.lower() not in "no space left on device"]
check("every phrase it matches is still produced by gbabuild",
      not _absent, _absent)
check("a toolchain failure names the toolchain, not the compiler stopping",
      _fr("The GBA toolchain (arm-none-eabi-gcc) isn't installed.")
      != _fr("unrecognised noise"),
      _fr("The GBA toolchain (arm-none-eabi-gcc) isn't installed."))
check("a generator failure is distinct from a disk failure",
      _fr("Could not turn this project into code: KeyError")
      != _fr("Could not write generated source: [Errno 13] Permission denied"))
check("a full disk is named as a full disk",
      "full" in _fr("cc1: fatal error: no space left on device").lower(),
      _fr("cc1: fatal error: no space left on device"))

# ---- every line the costing pane can show is translatable ----
# The line names appear for any project, so they were noticed and translated.
# "Sampled audio", its note, and the per-asset details appear only when a
# project HAS that kind of content — so they were missed, and would have shown
# in English inside an otherwise translated pane. This builds a project that
# exercises every line rather than sampling an empty one.
_everything = {
    "name": "All", "start_room": "rm",
    "sprites": [{"id": "s", "w": 16, "h": 16, "ox": 8, "oy": 8,
                 "anim_speed": 0,
                 "frames": [[0x0400] * 256, [0x0401] * 256]}],
    "tilesets": [{"id": "ts", "size": 8, "tiles": [[1] * 64, [2] * 64]}],
    "sounds": [{"id": "sn", "tempo": 8, "loop": False, "steps": 16,
                "lead": [0] * 16, "bass": [0] * 16, "pcm": [0] * 32768}],
    "objects": [{"id": "o", "sprite": "s", "visible": True, "solid": False,
                 "events": []}],
    "rooms": [{"id": "rm", "w": 240, "h": 160, "speed": 60, "bg": "#000",
               "instances": [{"object": "o", "x": 8, "y": 8}],
               "warps": [], "tiles": [1] + [0] * 599}]}
_rep = gbabuild.budget_report(_everything)
_cat = set(json.load(open(os.path.join(OVERLAY, "opt", "notebook",
                                       "de", "lang_es.json"),
                         encoding="utf-8")))
check("the costing pane exercises every line it has",
      len(_rep["lines"]) >= 5, [l["name"] for l in _rep["lines"]])
# Names and notes are translated by the pane at display, so they must be keys.
# The per-asset DETAILS are translated inside gbabuild and then filled with
# asset names and numbers, so the finished string is not a key and must not be
# looked up as one — the templates are checked instead, read from the source.
_nn = ([l["name"] for l in _rep["lines"]]
       + [l["note"] for l in _rep["lines"] if l["note"]])
check("every line name and note the costing pane shows is a catalog key",
      not [t for t in _nn if t not in _cat], [t for t in _nn if t not in _cat])

_bfn = next(n for n in ast.walk(ast.parse(
    open(os.path.join(OVERLAY, "opt", "notebook", "de", "gbabuild.py"),
         encoding="utf-8").read()))
    if isinstance(n, ast.FunctionDef) and n.name == "budget_report")
_tmpl = {n.args[0].value for n in ast.walk(_bfn)
         if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_t"
         and n.args and isinstance(n.args[0], ast.Constant)}
check("the costing pane asks to translate its per-asset details",
      len(_tmpl) >= 4, sorted(_tmpl))
check("...and every one of those templates is a catalog key",
      not [t for t in _tmpl if t not in _cat],
      [t for t in _tmpl if t not in _cat])

# Checking only the strings that ARE wrapped cannot notice one that stopped
# being wrapped — it just leaves the set. So: every prose-looking literal in
# budget_report must reach a reader translated by ONE of the two routes this
# module uses. Line names and notes are handed out unwrapped and translated by
# the pane, so being a catalog key is enough; details are interpolated here and
# so are wrapped. Either is fine; neither is not. Dict keys and the docstring
# are exempt.
_wrapped = {id(n.args[0]) for n in ast.walk(_bfn)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_t"
            and n.args and isinstance(n.args[0], ast.Constant)}
_keys = {id(k) for n in ast.walk(_bfn) if isinstance(n, ast.Dict)
         for k in n.keys if isinstance(k, ast.Constant)}
_docs = {id(_bfn.body[0].value)} if (_bfn.body
         and isinstance(_bfn.body[0], ast.Expr)
         and isinstance(_bfn.body[0].value, ast.Constant)) else set()
_bare = []
for _n in ast.walk(_bfn):
    if not (isinstance(_n, ast.Constant) and isinstance(_n.value, str)):
        continue
    if id(_n) in _wrapped or id(_n) in _keys or id(_n) in _docs:
        continue
    _v = _n.value.strip()
    # prose = two or more words with real letters in them
    if len(_v.split()) >= 2 and sum(c.isalpha() for c in _v) >= 4 \
            and _v not in _cat:
        _bare.append(_v[:60])
check("no prose in the costing pane escapes the translator",
      not _bare, _bare)

# ---- the costing pane and the problem list agree about the same fact ----
# 16 colour sets exist. A 17th sprite folds onto set 0 and is drawn in another
# sprite's colours; check_project said so, and the costing pane reported
# "16 / 16" and called the project fine, because it was counting the sets that
# EXIST rather than the ones the project ASKED for. Two diagnostics
# contradicting each other, with the reassuring one easier to reach.
def _many_sprites(n):
    def spr(i):
        px = [(0x0400 + i * 32 + j) for j in range(14)] + [0x7FFF] * 242
        return {"id": "s%d" % i, "w": 16, "h": 16, "ox": 8, "oy": 8,
                "anim_speed": 0, "frames": [px]}
    return {"name": "P", "tilesets": [], "sounds": [], "start_room": "rm",
            "sprites": [spr(i) for i in range(n)],
            "objects": [{"id": "o", "sprite": "s0", "visible": True,
                         "solid": False, "events": []}],
            "rooms": [{"id": "rm", "w": 240, "h": 160, "speed": 60,
                       "bg": "#000",
                       "instances": [{"object": "o", "x": 8, "y": 8}],
                       "warps": [], "tiles": [0] * 600}]}

def _pal_line(n):
    return [l for l in gbabuild.budget_report(_many_sprites(n))["lines"]
            if "colour sets" in l["name"]][0]

check("sixteen sprites fit the sixteen colour sets",
      _pal_line(16)["used"] == 16 and not _pal_line(16)["over"], _pal_line(16))
check("a seventeenth is counted, not quietly folded onto set 0",
      _pal_line(17)["used"] == 17 and _pal_line(17)["over"], _pal_line(17))
check("the costing pane and the problem list never disagree",
      all(bool(_pal_line(n)["over"])
          == bool(gbabuild.check_project(_many_sprites(n)))
          for n in (8, 16, 17, 20)),
      [(n, _pal_line(n)["over"],
        len(gbabuild.check_project(_many_sprites(n)))) for n in (8, 16, 17, 20)])

# ---- an alarm that does not exist is named, not written past ----
_al = lambda c: gbabuild.check_project(_scripted(c))
check("setting an alarm past the last one is reported",
      any("no alarm 9" in x for x in _al("alarm[9] = 30")), _al("alarm[9] = 30"))
check("...and says how many there are", 
      any("0 to 3" in x for x in _al("alarm[9] = 30")), _al("alarm[9] = 30"))
check("a negative alarm is reported too",
      any("no alarm -1" in x for x in _al("alarm[-1] = 1")), _al("alarm[-1] = 1"))
check("the alarms that exist are left alone",
      not _al("alarm[0] = 30") and not _al("alarm[3] = 5"),
      (_al("alarm[0] = 30"), _al("alarm[3] = 5")))
check("an alarm index the generator cannot evaluate is left to the compiler",
      not _al("alarm[1+2] = 1"), _al("alarm[1+2] = 1"))
check("reading an alarm does not look like an unset variable",
      not _al("x = alarm[0]\nhspeed = x"), _al("x = alarm[0]\nhspeed = x"))

# ---- a build that WORKED can still have had something to say ----
# The generated C compiles clean, so a warning comes from an Execute Code
# action — the author's own C. `if (x = 3)` is the classic C mistake, gcc
# catches it, and it was going into a log nobody opens.
check("a log's own -Wall command line is not counted as a warning",
      gbasdk.GbaSdk._warning_count("gcc -Wall -O2 foo.c\nlinking\n") == 0)
check("a real warning line is counted",
      gbasdk.GbaSdk._warning_count(
          "a.c:3:5: warning: unused variable 'x' [-Wunused-variable]\n") == 1)
check("...and several are counted separately",
      gbasdk.GbaSdk._warning_count(
          "a.c:1: warning: one\nb.c:2: warning: two\n") == 2)

if gcc:
    wc = app({"name": "W", "tilesets": [], "sounds": [], "sprites": [],
              "start_room": "rm",
              "objects": [{"id": "o", "sprite": "", "visible": True,
                           "solid": False, "events": [
                               {"type": "step", "actions": [
                                   {"kind": "execute_code", "lang": "C",
                                    "code": "if (nb_score = 3) { }"}]}]}],
              "rooms": [{"id": "rm", "w": 240, "h": 160, "speed": 60,
                         "bg": "#000",
                         "instances": [{"object": "o", "x": 8, "y": 8}],
                         "warps": [], "tiles": [0] * 600}]})
    nbpicker.save_file = lambda *a, **k: os.path.join(HOME, "Documents", "W.gba")
    wc._cards = []
    wc._file_export()
    _said = " ".join(l for _t_, ls in wc._cards for l in ls).lower()
    check("a build that compiled with a warning says the compiler had a remark",
          "remark" in _said, wc._cards)
    wc.destroy()

# ---- both ways a build can fail have a card, and they are different ----
# Enumerating every card the app can show found five; three were covered. These
# two are the failure paths, which are exactly the ones nobody exercises by
# hand. They must also stay distinct: "could not be built" and "could not be
# saved" send an author to different places.
_wf = app()
_wf._file_example()
pump()
_cards_seen = []
_wf._card = lambda icon, title, lines, **kw: _cards_seen.append(title) or True
_wf._confirm = lambda *a, **k: True
nbpicker.save_file = lambda *a, **k: os.path.join(HOME, "Documents", "f.gba")
_real_build2 = gbabuild.build_rom
try:
    gbabuild.build_rom = lambda *a, **k: (False, None, "cc1: fatal error: boom")
    _cards_seen.clear(); _wf._file_export(); pump()
    _failed = list(_cards_seen)
    gbabuild.build_rom = lambda *a, **k: (True, "/nonexistent/never.gba", "")
    _cards_seen.clear(); _wf._file_export(); pump()
    _unsaved = list(_cards_seen)
finally:
    gbabuild.build_rom = _real_build2
check("a build that fails shows a card rather than nothing", bool(_failed), _failed)
check("a build that works but cannot be written out shows one too",
      bool(_unsaved), _unsaved)
check("...and the two are not the same card", _failed != _unsaved,
      (_failed, _unsaved))
_wf.destroy()

# ---- a project bigger than the console is said so before it is written ----
# budget_report knew, but only the "What This Game Costs" menu item ever asked
# it — so a game with more sprite tiles than OBJ VRAM exported cleanly and
# glitched on hardware. It now joins the same gate as the code problems, which
# offers Go Back and Fix / Export Anyway.
def _big(nframes):
    return {"name": "B", "tilesets": [], "sounds": [], "start_room": "rm",
            "sprites": [{"id": "big", "w": 64, "h": 64, "ox": 0, "oy": 0,
                         "anim_speed": 0, "frames": [[1] * 4096] * nframes}],
            "objects": [{"id": "o", "sprite": "big", "visible": True,
                         "solid": False, "events": []}],
            "rooms": [{"id": "rm", "w": 240, "h": 160, "speed": 60,
                       "bg": "#000",
                       "instances": [{"object": "o", "x": 8, "y": 8}],
                       "warps": [], "tiles": [0] * 600}]}

wb = app(_big(17))                      # 17 x 64 tiles = 1088, cap is 1024
wb._yes = False                         # "Go Back and Fix"
wb._cards = []
wb._file_export()
_said = " ".join(l for _t_, ls in wb._cards for l in ls).lower()
check("exporting a game too big for the console says which budget is over",
      "sprite tiles" in _said, wb._cards)
check("...with the number and the room the console actually has",
      "1088" in _said and "1024" in _said, wb._cards)
wb.destroy()

wb = app(_big(16))                      # exactly the cap: not over
wb._cards = []
wb._file_export()
_said = " ".join(l for _t_, ls in wb._cards for l in ls).lower()
check("a project that exactly fits is not called over",
      "sprite tiles" not in _said, wb._cards)
wb.destroy()

check("exporting does not leave build keys in the saved project",
      not [k for k in w.proj["objects"][0] if k.startswith("_")],
      sorted(k for k in w.proj["objects"][0] if k.startswith("_")))
w.destroy()

w = app()
w.proj = {"name": "Empty", "sprites": [], "sounds": [], "tilesets": [],
          "objects": [], "rooms": [], "start_room": None}
w._cards = []
w._file_export()
# Anchored on the EXPLANATION, not on the verb in the title: this check used
# to look for "compile" and went red when the app settled on one word for the
# action ("Build"), which was a rename, not a regression.
check("exporting an empty project explains what a game needs instead of failing",
      bool(w._cards)
      and any("object" in ln.lower() and "room" in ln.lower()
              for ln in w._cards[-1][1]), w._cards)
w.destroy()

# ============================================ the cartridge header it exports
section("the exported cartridge names itself")
# The header's 12-byte title at 0xA0 is what a flashcart menu lists a game
# under. It was never written, so every game a person exported showed up as a
# blank row, indistinguishable from every other one they had made. It is also
# covered by the checksum at 0xBD, so it has to be written BEFORE that is
# computed or a ROM with a title would not boot at all.
check("a plain name becomes the cartridge title",
      gbabuild._rom_title("Kettle Quest") == b"KETTLE QUEST",
      gbabuild._rom_title("Kettle Quest"))
check("a name too long for the field is cut to 12 bytes, not rejected",
      gbabuild._rom_title("A Very Long Game Name Indeed") == b"A VERY LONG ",
      gbabuild._rom_title("A Very Long Game Name Indeed"))
check("an accent is folded rather than truncating the title at it",
      gbabuild._rom_title("Café Racer 2") == b"CAFE RACER 2",
      gbabuild._rom_title("Café Racer 2"))
check("a name with nothing the field can carry leaves it empty, not broken",
      gbabuild._rom_title("日本のゲーム") == b"",
      gbabuild._rom_title("日本のゲーム"))
check("an empty name is allowed", gbabuild._rom_title("") == b"")

# _gbafix on a synthetic ROM: title, logo and a checksum that covers the title
_rom = os.path.join(HOME, "header-probe.gba")
with open(_rom, "wb") as _fh:
    _fh.write(bytes(0x200))
gbabuild._gbafix(_rom, "Kettle Quest")
_d = open(_rom, "rb").read()
check("the boot logo the console checks is written",
      _d[0x04:0xA0] == gbabuild.NINTENDO_LOGO)
check("the title is written into the header",
      _d[0xA0:0xAC] == b"KETTLE QUEST", _d[0xA0:0xAC])
_s = 0
for _b in _d[0xA0:0xBD]:
    _s = (_s - _b) & 0xFF
check("the header checksum covers the title that was just written",
      _d[0xBD] == ((_s - 0x19) & 0xFF), (_d[0xBD], (_s - 0x19) & 0xFF))
# and a ROM with no name still gets a valid header
gbabuild._gbafix(_rom, "")
_d = open(_rom, "rb").read()
_s = 0
for _b in _d[0xA0:0xBD]:
    _s = (_s - _b) & 0xFF
check("an unnamed game still gets a valid header",
      _d[0x04:0xA0] == gbabuild.NINTENDO_LOGO
      and _d[0xBD] == ((_s - 0x19) & 0xFF))

# ---- tile sets at 8, 16 and 32 px ------------------------------------------
# A tile bigger than 8x8 is ONE picture in the editor and (size/8)^2 hardware
# tiles in the ROM. If those two disagree about block order, large tiles look
# perfect in the editor and come out scrambled on the console -- which no
# existing check would have caught.
for _n in (8, 16, 32):
    _per = (_n // 8) ** 2
    _tile = [(y * _n + x) & 0x7FFF for y in range(_n) for x in range(_n)]
    _blocks = gbasdk.split_tile(_tile, _n)
    check("a %d px tile splits into %d hardware tiles" % (_n, _per),
          len(_blocks) == _per, len(_blocks))
    _recon = [0] * (_n * _n)
    _bn = _n // 8
    for _b, _cell in enumerate(_blocks):
        _by, _bx = divmod(_b, _bn)
        for _j in range(8):
            for _i in range(8):
                _recon[(_by * 8 + _j) * _n + _bx * 8 + _i] = _cell[_j * 8 + _i]
    check("  ... and the blocks reassemble into the original picture",
          _recon == _tile)

# the compiler must emit the SAME order the editor drew
_cols = [0x1F, 0x3E0, 0x7C00, 0x7FFF]
_big = [_cols[((y // 8) * 4 + (x // 8)) % 4] for y in range(32) for x in range(32)]
_g = gbabuild._Gen({"sprites": [], "sounds": [], "objects": [], "rooms": [],
                    "tilesets": [{"id": "ts", "size": 32, "tiles": [_big]}]})
_g.gen_bg()
_src = "\n".join(_g.out)
_m = re.search(r"nb_bg_tiles\[\]\s*=\s*\{(.*?)\};", _src, re.S)
_vals = [int(v, 0) for v in _m.group(1).replace("\n", " ").split(",") if v.strip()]
check("a 32 px tile emits 16 hardware tiles into the charblock",
      len(_vals) == 16 + 16 * 16, len(_vals))
_inv = dict((v, k) for k, v in _g._bg_cmap.items())
_got = [_inv.get(_vals[16 + _b * 16] & 0xF) for _b in range(16)]
_want = [_cols[((_b // 4) * 4 + (_b % 4)) % 4] for _b in range(16)]
check("the compiler's block order matches the editor's", _got == _want,
      (_got[:4], _want[:4]))

# the charblock is finite, and a 32 px tile eats sixteen slots of it
_full = gbabuild._Gen({"tilesets": [{"id": "t", "size": 32,
                                     "tiles": [[0x1F] * 1024] * 33}]})
_full.gen_bg()
check("running out of background tiles is reported",
      any("512" in p for p in _full.problems), _full.problems[:1])
_fits = gbabuild._Gen({"tilesets": [{"id": "t", "size": 32,
                                     "tiles": [[0x1F] * 1024] * 20}]})
_fits.gen_bg()
check("  ... and a set that fits is not", not any("512" in p for p in _fits.problems))

# painting a big tile fills its block, snapped to its own grid
_w = app()
_w._file_example()
pump()
_w._new_tileset_size = 32
_w._add_resource("tileset")
pump()
_ents = _w._tile_entries()
_base = [v for v, n, _t in _ents if n == 32][0]
check("the room strip offers WHOLE tiles, not one eighth of one",
      len(_ents) == 3, [(v, n) for v, n, _t in _ents])
_w._select_resource("room", 0)
_w._room_mode = "tiles"
_w._room_tile = _base
pump()
_rm = _w.proj["rooms"][0]
_w._paint_room_tile(_rm, 5, 5)          # deliberately off the 4-cell grid
_tm, _cw, _ch = _w._room_tilemap(_rm)
_block = [[_tm[(4 + j) * _cw + (4 + i)] for i in range(4)] for j in range(4)]
check("a 32 px tile snaps to its own grid and fills 4x4 cells",
      _block == [[_base + j * 4 + i for i in range(4)] for j in range(4)], _block)
check("  ... and the cell the pointer was in is inside that block",
      _tm[5 * _cw + 5] != 0)
# an 8 px tile still paints exactly one cell. The example room already has a
# tilemap, so compare BEFORE and AFTER rather than assuming a neighbour is
# empty -- that assumption is what a first version of this check got wrong.
_w._room_tile = 1
# pick a cell that does NOT already hold tile 1, or the paint is a no-op and
# the check proves nothing (the first version of this check fell into exactly
# that: an empty diff read as "nothing painted")
_cell = next((k for k in range(len(_tm)) if _tm[k] != 1), 0)
_before = list(_tm)
_w._paint_room_tile(_rm, _cell % _cw, _cell // _cw)
_diff = [k for k in range(len(_tm)) if _tm[k] != _before[k]]
check("an 8 px tile still paints a single cell",
      _diff == [_cell] and _tm[_cell] == 1, (_diff[:5], _cell))
_w.destroy()
pump()

# a project made before tiles could grow still loads, as 8 px
_old = {"sprites": [], "sounds": [], "objects": [], "rooms": [],
        "tilesets": [{"id": "ts_old", "tiles": [[0x1F] * 64]}]}
_w = app()
_proj, _lost = _w._sane_project(_old)
check("a tile set with no size reads as 8 px",
      gbasdk.ts_size(_proj["tilesets"][0]) == 8)
check("  ... and keeps its 64 pixels",
      len(_proj["tilesets"][0]["tiles"][0]) == 64)
_bad = {"tilesets": [{"id": "t", "size": 12, "tiles": [[0x1F] * 64]}]}
_proj2, _ = _w._sane_project(_bad)
check("a size the hardware cannot use falls back to 8",
      gbasdk.ts_size(_proj2["tilesets"][0]) == 8)
_w.destroy()
pump()

# ============================================ workspace, browser, bundle =====
# Added with Phase 1/2 of docs/GBA-SDK-SPEC.md. The BUNDLE checks matter most:
# a project is the user's work, and a storage change that loses part of it is
# the one failure this app cannot recover from.
import gbaworkspace  # noqa: E402

_ws_app = gbasdk.GbaSdk()
_ws_app._file_example()
pump()
P = gbasdk.GbaSdk.BUNDLE_PARTS
_ws = _ws_app._editor_stack

check("the editors live in a workspace, not a single slot",
      isinstance(_ws, gbaworkspace.Workspace))
_ws.show("sprite"); _ws.show("room")
check("two editors can be open at once",
      set(("sprite", "room")) <= set(_ws.open_ids()))
check("a group holding two panes can be split", _ws.can_split())
check("it splits", _ws.split(Gtk.Orientation.HORIZONTAL))
check("...into two groups", len(_ws._groups()) == 2)
_saved = _ws.layout()
_ws.reset(keep="welcome")
check("reset returns to one group", len(_ws._groups()) == 1)
check("a saved layout restores", _ws.set_layout(_saved))
check("...with its splits", len(_ws._groups()) == 2)
_ws.close("room")
check("a pane closes", "room" not in _ws.open_ids())
check("...but stays registered, so its editor keeps its state",
      "room" in _ws.panes)
_ws.reset(keep="welcome")

# The browser must address the PROJECT's index, never the filtered one. The bug
# this exists for: filtering renumbered rows 0..n while _sel and _asset_row
# address a resource by its index into the project list, so with a filter
# active clicking a row opened the WRONG asset.
_ws_app._search.set_text("coin"); _ws_app._render_tree(); pump()
_rows = [(r.kind, r.index) for r in _ws_app._tree_body.get_children()
         if hasattr(r, "kind")]
_coin = [i for i, r in enumerate(_ws_app.proj.get("sprites") or [])
         if "coin" in str(r.get("id", "")).lower()]
check("the example project has an asset to match", bool(_coin))
check("a filtered row addresses its index in the PROJECT, not the filter",
      bool(_coin) and ("sprite", _coin[0]) in _rows)
_ws_app._search.set_text(""); _ws_app._render_tree(); pump()

_spr = _ws_app.proj.get("sprites") or []
if _spr:
    _spr[0]["folder"] = "Pickups"
    _ws_app._render_tree()
    check("a new folder starts open",
          not _ws_app._folder_shut("sprite", "Pickups"))
    _ws_app._toggle_folder("sprite", "Pickups")
    check("a folder closes", _ws_app._folder_shut("sprite", "Pickups"))
    _shut = [(r.kind, r.index) for r in _ws_app._tree_body.get_children()
             if hasattr(r, "kind")]
    check("...and hides what is in it", ("sprite", 0) not in _shut)
    _ws_app._search.set_text(str(_spr[0].get("id")))
    _ws_app._render_tree(); pump()
    _found = [(r.kind, r.index) for r in _ws_app._tree_body.get_children()
              if hasattr(r, "kind")]
    check("search looks INSIDE a closed folder", ("sprite", 0) in _found)
    _ws_app._search.set_text("")
    _ws_app._toggle_folder("sprite", "Pickups")
    _spr[0].pop("folder", None)
    _ws_app._render_tree()

_before = {k: len(_ws_app.proj.get(k) or []) for k in P}
_names = {k: [r.get("id") for r in (_ws_app.proj.get(k) or [])] for k in P}
_bd = tempfile.mkdtemp()
_bundle = os.path.join(_bd, "Game.gbaproj")

_ws_app._bundle_write(_bundle)
check("a bundle is a directory", os.path.isdir(_bundle))
check("it holds project.json and one file per kind",
      sorted(os.listdir(_bundle))
      == sorted([gbasdk.GbaSdk.BUNDLE_MARK] + [k + ".json" for k in P]))
check("no scratch directory is left behind",
      not any(x.endswith((".part", ".old")) for x in os.listdir(_bd)))

_got = _ws_app._bundle_read(_bundle)
check("every asset survives the round trip",
      {k: len(_got.get(k) or []) for k in P} == _before)
check("...in the same order, under the same names",
      {k: [r.get("id") for r in (_got.get(k) or [])] for k in P} == _names)
check("the bundle reads back IDENTICAL to the project in memory",
      _got == _ws_app.proj)
check("the format marker does not leak into the project", "_bundle" not in _got)

_ws_app._bundle_write(_bundle)
check("overwriting leaves one bundle", len(os.listdir(_bundle)) == len(P) + 1)
check("...and it still reads back whole",
      _ws_app._bundle_read(_bundle) == _ws_app.proj)

_legacy = os.path.join(_bd, "Old.gbaproj")
with open(_legacy, "w", encoding="utf-8") as _fh:
    json.dump(_ws_app.proj, _fh)
with open(_legacy, encoding="utf-8") as _fh:
    _old_proj, _old_lost = _ws_app._sane_project(json.load(_fh))
check("a single-file project from before bundles still loads",
      _old_proj is not None and not _old_lost)

os.remove(os.path.join(_bundle, "sounds.json"))
_hurt = _ws_app._bundle_read(_bundle)
check("a missing part yields an empty kind",
      _hurt is not None and _hurt.get("sounds") == [])
check("...and the other kinds are untouched",
      len(_hurt.get("sprites") or []) == _before["sprites"])
check("a directory that is not a bundle is refused",
      _ws_app._bundle_read(_bd) is None)

# A bundle is seven files. Losing one to corruption used to be silent AND
# total: an unreadable tables.json took every table with it, and the next save
# wrote the emptiness back over the file that still had them.
import shutil as _sh                                        # noqa: E402


def _bundle_damage(label, mutate):
    _d = os.path.join(_bd, "dmg_" + label)
    _sh.rmtree(_d, ignore_errors=True)
    _sh.copytree(_bundle, _d)
    mutate(_d)
    _ws_app._bundle_lost = 0
    try:
        _out = _ws_app._bundle_read(_d)
    except Exception as _e:                                 # noqa: BLE001
        return "RAISED %s" % type(_e).__name__, 0
    return _out, _ws_app._bundle_lost


for _lbl, _fn in (
        ("corrupt", lambda d: open(os.path.join(d, "sprites.json"), "w",
                                   encoding="utf-8").write("{oops")),
        ("nota_list", lambda d: open(os.path.join(d, "sprites.json"), "w",
                                     encoding="utf-8").write('{"a":1}')),
        ("empty", lambda d: open(os.path.join(d, "sprites.json"), "w",
                                 encoding="utf-8").write(""))):
    _o, _l = _bundle_damage(_lbl, _fn)
    check("a %s part is counted as lost" % _lbl, _l == 1, "lost=%s" % _l)
    check("...and the rest of the bundle still loads",
          isinstance(_o, dict) and bool(_o.get("rooms")), str(type(_o)))

# A part that is ABSENT is a kind the project never had -- an older bundle,
# saved before that kind existed. Alarming about it would cry wolf on every
# migration.
_o, _l = _bundle_damage("gone", lambda d: os.unlink(
    os.path.join(d, "tables.json")))
check("an absent part is migration, not damage", _l == 0, "lost=%s" % _l)

# A damaged MARKER used to raise straight out of a function documented to
# return None for "not a bundle", past a caller that believed it.
_o, _l = _bundle_damage("marker", lambda d: open(
    os.path.join(d, "project.json"), "w", encoding="utf-8").write("{bad"))
check("a damaged marker says 'not a bundle' rather than raising", _o is None,
      str(_o))

_o, _l = _bundle_damage("fine", lambda d: None)
check("an intact bundle reports nothing lost", _l == 0, "lost=%s" % _l)

# --- and the WRITE side, which is where a save can take the project with it ---
_wsave = os.path.join(_bd, "w.gbaproj")
_ws_app._bundle_write(_wsave)
_good = sorted(os.listdir(_wsave))
_rooms = len(json.load(open(os.path.join(_wsave, "rooms.json"),
                           encoding="utf-8")))
check("a bundle saves", len(_good) >= 8, str(_good))

# A part failing part-way through must not touch the saved project at all.
_realw = nbapp.atomic_write_json
_n = [0]


def _flaky(path, data, **kw):
    _n[0] += 1
    if _n[0] == 5:
        raise OSError(28, "No space left on device")
    return _realw(path, data, **kw)


_keep_rooms = _ws_app.proj.get("rooms")
nbapp.atomic_write_json = _flaky
_ws_app.proj["rooms"] = []
try:
    _ws_app._bundle_write(_wsave)
    _raised = False
except Exception:                                           # noqa: BLE001
    _raised = True
nbapp.atomic_write_json = _realw
_ws_app.proj["rooms"] = _keep_rooms
check("a save that fails part-way says so", _raised)
check("...and leaves the saved project untouched",
      sorted(os.listdir(_wsave)) == _good
      and len(json.load(open(os.path.join(_wsave, "rooms.json"),
                             encoding="utf-8"))) == _rooms)
check("...with no half-bundle beside it looking like one",
      not any(d.endswith(".part") for d in os.listdir(_bd)),
      str([d for d in os.listdir(_bd) if d.endswith(".part")]))

# THE WINDOW THE WHOLE DANCE EXISTS FOR: between moving the saved project aside
# and moving the new one in, it does not exist at its own path. If the second
# rename fails it stayed that way -- the author's work sitting in a .old
# directory they have no reason to look in.
_realr = os.rename


def _bad_rename(a, b):
    if str(a).endswith(".part"):
        raise OSError(13, "Permission denied")
    return _realr(a, b)


os.rename = _bad_rename
try:
    _ws_app._bundle_write(_wsave)
except Exception:                                           # noqa: BLE001
    pass
os.rename = _realr
check("a failed swap puts the project back where it was",
      os.path.isdir(_wsave), "the project is not at its own path")
check("...still whole",
      os.path.isdir(_wsave) and sorted(os.listdir(_wsave)) == _good,
      str(sorted(os.listdir(_wsave)) if os.path.isdir(_wsave) else None))
check("...and reads back",
      _ws_app._bundle_read(_wsave) is not None)
shutil.rmtree(_bd, ignore_errors=True)
_ws_app.destroy()
pump()

# ----------------------------------------------------------------- score
# The same pattern read as notation. The maths is the part that can regress
# silently: a staff position is DIATONIC (seven letters per octave), and MIDI
# puts C4 at 60 so divmod(64, 12) says octave 5 -- deriving the staff base by
# hand is an off-by-one that puts every note an octave out and still looks
# like music.
_sc = app()
_G, _T, _S = _sc.STAFF_GAP, _sc.STAFF_TOP, _sc.STAFF_SPLIT
# treble lines bottom to top: E4 G4 B4 D5 F5
for _p, _name, _line in ((64, "E4", 4), (67, "G4", 3), (71, "B4", 2),
                         (74, "D5", 1), (77, "F5", 0)):
    _y, _sh = _sc._staff_y(_p, False)
    check("treble %s sits on its line" % _name,
          abs(_y - (_T + _line * _G)) < 0.01,
          "%.1f vs %.1f" % (_y, _T + _line * _G))
# bass lines bottom to top: G2 B2 D3 F3 A3
for _p, _name, _line in ((43, "G2", 4), (47, "B2", 3), (50, "D3", 2),
                         (53, "F3", 1), (57, "A3", 0)):
    _y, _sh = _sc._staff_y(_p, True)
    check("bass %s sits on its line" % _name,
          abs(_y - (_S + _line * _G)) < 0.01,
          "%.1f vs %.1f" % (_y, _S + _line * _G))
check("middle C sits one ledger line below the treble",
      abs(_sc._staff_y(60, False)[0] - (_T + 5 * _G)) < 0.01)
# C sharp shares C's line and is told apart by the sharp, not by height.
check("a sharp shares the line of its natural",
      _sc._staff_y(61, False)[0] == _sc._staff_y(60, False)[0])
check("...and is marked as needing one", _sc._staff_y(61, False)[1] == 1)
check("a natural is not", _sc._staff_y(60, False)[1] == 0)
# Spacing by semitone instead of by letter makes a chromatic run look like a
# scale; an octave must be exactly seven staff steps.
check("an octave is seven staff steps, not twelve",
      abs((_sc._staff_y(60, False)[0] - _sc._staff_y(72, False)[0])
          - 7 * (_G / 2.0)) < 0.01)
check("every semitone has a staff position",
      all(n in _sc._PITCH_STEP for n in range(12)))
check("the score view is a VIEW, not a second document",
      getattr(_sc, "_snd_view", None) == "roll")
_sc.destroy()
pump()

# -------------------------------------------------------------- dialogue
# An action reaches the author through THREE tables, and an action present in
# only some of them is one the generator emits and the palette never offers --
# which is how Say stood for exactly one build.
for _act in ("say",):
    check("%s is in the action list" % _act, _act in gbasdk.ACTION_LABEL)
    check("...and in a palette group" ,
          any(_act in _g[1] for _g in gbasdk.ACTION_GROUPS))
    check("...and has a one-line tip", _act in gbasdk.ACTION_TIPS)

# Every action the palette offers must be one the generator can emit, and every
# action with parameters must have a label for each. Cheap, and it catches the
# half-registered action for good.
_palette = [a for _g, acts in gbasdk.ACTION_GROUPS for a in acts]
check("every palette action is defined",
      all(a in gbasdk.ACTION_LABEL for a in _palette),
      str([a for a in _palette if a not in gbasdk.ACTION_LABEL]))
check("every defined action is in a palette group",
      all(a in _palette for a in gbasdk.ACTION_LABEL),
      str([a for a in gbasdk.ACTION_LABEL if a not in _palette]))
check("every action has a tip",
      all(a in gbasdk.ACTION_TIPS for a in _palette),
      str([a for a in _palette if a not in gbasdk.ACTION_TIPS]))

# Show Menu: an action cannot wait for a choice, so it names a variable and the
# engine writes the answer there when the menu closes.
_mn = app()
_mn.proj["objects"] = [{"id": "o", "sprite": None, "visible": True,
                        "solid": False, "events": [
    {"type": "create", "actions": [
        {"kind": "menu", "a": "Fight", "b": "Bag", "c": "Run", "d": "",
         "var": "choice"}]},
    {"type": "step", "actions": [
        {"kind": "if_var", "var": "choice", "op": "==", "value": 0,
         "children": [{"kind": "say", "text": "You fight."}]}]}]}]
_mnc = gbabuild.generate_c(_mn.proj)
check("Show Menu opens a menu", "rt_menu_open_var(" in _mnc,
      str([l.strip() for l in _mnc.splitlines() if "menu" in l][:3]))
# The menu holds the pointer rather than copying the strings, so an array built
# inside an event function would be gone by the time it drew.
check("...from an array at file scope",
      "static const char *const nb_menu_1[] =" in _mnc)
check("...declared before the object that points at it",
      _mnc.index("nb_menu_1[] =") < _mnc.index("rt_menu_open_var("))
check("...holding only the lines that were filled in",
      '"Fight", "Bag", "Run" }' in _mnc,
      str([l for l in _mnc.splitlines() if "nb_menu_1" in l][:1]))
check("...and the answer goes to the variable named",
      ", self, 0);" in _mnc)
check("a complete Show Menu raises no problem",
      not gbabuild.check_project(_mn.proj),
      str(gbabuild.check_project(_mn.proj))[:120])

# Without somewhere to put the answer, the menu opens and the choice is thrown
# away -- which looks like the menu not working rather than a missing setting.
_mn2 = json.loads(json.dumps(_mn.proj))
_mn2["objects"][0]["events"][0]["actions"][0]["var"] = ""
check("a menu with nowhere to put the answer is reported",
      any("answer" in p for p in gbabuild.check_project(_mn2)),
      str(gbabuild.check_project(_mn2))[:140])
_mn3 = json.loads(json.dumps(_mn.proj))
for _k in ("a", "b", "c", "d"):
    _mn3["objects"][0]["events"][0]["actions"][0][_k] = ""
check("a menu with no lines is reported",
      any("nothing to choose" in p for p in gbabuild.check_project(_mn3)),
      str(gbabuild.check_project(_mn3))[:140])
_mn.destroy()
pump()

_dl = app()
_dl.proj["objects"] = [{"id": "o", "sprite": None, "visible": True,
                        "solid": False, "events": [
    {"type": "create", "actions": [
        {"kind": "say", "text": 'He said "go".{p}Then {v:0} left.'}]}]}]
_dlc = gbabuild.generate_c(_dl.proj)
check("Say compiles to the dialogue engine", "rt_say(" in _dlc,
      str([l.strip() for l in _dlc.splitlines() if "say" in l][:3]))
# Control codes are part of the AUTHOR'S text and must reach the cartridge
# unchanged; a generator that interpreted them would fight the runtime.
check("...with its control codes intact", "{p}" in _dlc and "{v:0}" in _dlc)
check("...and its quotes escaped", '\\"go\\"' in _dlc,
      str([l.strip() for l in _dlc.splitlines() if "go" in l][:2]))
check("...raising no problem", not gbabuild.check_project(_dl.proj))
_dl.destroy()
pump()

# ------------------------------------------------------------ damaged files
# The worst defect this OS has had was on the READ side: opening and closing an
# app destroyed a damaged store, with no user action at all. `lost` is what
# makes the app warn and keep the file as it was beside it, so a value that is
# thrown away WITHOUT being counted is that defect coming back.
_dm = app()


def _damaged(extra):
    _base = {"name": "D", "sprites": [], "tilesets": [], "sounds": [],
             "objects": [], "rooms": [], "scripts": [], "tables": []}
    _base.update(extra)
    return _dm._sane_project(_base)


for _label, _extra in (
        ("table headings", {"tables": [{"id": "t", "columns": "nope",
                                        "rows": []}]}),
        ("a script's code", {"scripts": [{"id": "s", "code": 42}]}),
        ("a room's doors", {"rooms": [{"id": "r", "warps": "nope"}]}),
        ("which tiles are solid",
         {"tilesets": [{"id": "ts", "size": 8, "tiles": [[0] * 64],
                        "solid": "yes"}]}),
        ("an auto-tile run with no room left",
         {"tilesets": [{"id": "ts", "size": 8, "tiles": [[0] * 64],
                        "auto_base": 99}]}),
        ("a sampled sound", {"sounds": [{"id": "s", "steps": 16,
                                         "pcm": "noise"}]}),
        ("a parallax layer", {"rooms": [{"id": "r", "far": "nope"}]})):
    _out, _lost = _damaged(_extra)
    check("damaged %s is COUNTED, not dropped in silence" % _label, _lost > 0,
          "lost=%d" % _lost)
    check("...and the project still loads" , _out is not None)

# The counter must not cry wolf either: a warning on every open trains people
# to ignore the one that matters.
_ok_out, _ok_lost = _damaged({
    "tables": [{"id": "t", "columns": [{"name": "a", "type": "int"}],
                "rows": [[1]]}],
    "scripts": [{"id": "s", "code": "void f(void){}"}],
    "rooms": [{"id": "r", "warps": [], "far": None}],
    "tilesets": [{"id": "ts", "size": 8, "tiles": [[0] * 64],
                  "solid": [False]}],
    "sounds": [{"id": "s", "steps": 16, "lead": [0] * 16, "bass": [0] * 16}]})
check("a healthy project reports nothing lost", _ok_lost == 0, str(_ok_lost))

# Nothing in here may raise: the loader runs before the window exists, and an
# exception there is an app that will not open on a file it could have repaired.
for _junk in ({"tables": None, "scripts": None, "rooms": None},
              {"tables": [{"id": "t", "columns": ["a", 3], "rows": ["x", 5]}]},
              {"rooms": [{"id": "r", "warps": [1, "x", None]}]},
              {"sounds": [{"id": "s", "steps": 16, "pcm": [1, "x", None, 999]}]},
              {"objects": [{"id": "o", "tilecol": "lots", "depth": -9,
                            "events": []}]}):
    try:
        _r, _l = _damaged(_junk)
        _raised = None
    except Exception as _exc:                               # noqa: BLE001
        _raised = "%s: %s" % (type(_exc).__name__, _exc)
    check("junk in %s does not raise" % list(_junk)[0], _raised is None,
          str(_raised))
_dm.destroy()
pump()

# And the whole chain, not just the counter: a damaged file must be NOTED, and
# the file as it was must survive beside the repaired one. The counter is what
# arms both -- before it counted these kinds, a damaged project was normalised
# on open and written back on close with no note and no copy.
_dpath = os.path.join(os.path.dirname(CFG), "gbasdk.json")
with open(_dpath, "w", encoding="utf-8") as _fh:
    json.dump({"name": "Mine", "sprites": [], "tilesets": [], "sounds": [],
               "objects": [], "tables": [],
               "rooms": [{"id": "r", "warps": "nope"}],
               "scripts": [{"id": "s", "code": 42}]}, _fh)
_was = open(_dpath, encoding="utf-8").read()
# nbapp backs a store up ONCE PER PROCESS, on purpose: the copy is meant to be
# "the version from before this session", and refreshing it on every save
# destroys the thing it exists for. This suite has already opened this path
# many times, so a fresh session has to be simulated rather than assumed.
nbapp._BACKED_UP.discard(_dpath)
_dm2 = app(keep=True)
pump()
check("a damaged project still opens", _dm2.proj is not None)
check("...and the app knows it was damaged",
      bool(getattr(_dm2, "_load_note", False)))
_dm2.destroy()
pump()
_baks = [f for f in os.listdir(os.path.dirname(CFG))
         if f.startswith("gbasdk.json") and f != "gbasdk.json"]
check("...so the file as it was is kept beside it", bool(_baks), str(_baks))
if _baks:
    _bak = os.path.join(os.path.dirname(CFG), _baks[0])
    check("...and the copy is the ORIGINAL, not the repaired one",
          open(_bak, encoding="utf-8").read() == _was)
    # The more subtle half: opening the repaired file again must NOT refresh
    # the backup, or the second open overwrites the copy the first one saved.
    _dm3 = app(keep=True)
    pump()
    _dm3.destroy()
    pump()
    check("...and a second open does not overwrite it",
          open(_bak, encoding="utf-8").read() == _was)

# ----------------------------------------------------------------- world
# Rooms and the doors between them. The reason to draw this at all is finding
# the room with no way back, so what it must get right is which doors exist.
_wd = app()
_wd._file_example()
_cave = json.loads(json.dumps(_wd.proj["rooms"][0]))
_cave["id"] = "rm_cave"
_wd.proj["rooms"].append(_cave)
_wd.proj["rooms"][0]["warps"] = [
    {"x": 0, "y": 0, "w": 8, "h": 8, "room": "rm_cave", "tx": 8, "ty": 8},
    {"x": 16, "y": 0, "w": 8, "h": 8, "room": "rm_gone", "tx": 0, "ty": 0}]
_wd._open_world()
pump()
check("the world map opens as a pane",
      _wd._editor_stack._active.current == "world")
check("...counting the rooms and the doors",
      "2 rooms, 2 doors" in _wd._world_count.get_text(),
      _wd._world_count.get_text())
# A door to a deleted room is the thing this view exists to surface.
check("...and saying how many lead nowhere",
      "nowhere" in _wd._world_count.get_text(), _wd._world_count.get_text())
check("...in the singular when there is one",
      "1 leads nowhere" in _wd._world_count.get_text(),
      _wd._world_count.get_text())

_wb = _wd._world_boxes()
check("every room gets a box", len(_wb) == 2, str(len(_wb)))
# A grid, not a force-directed layout: a graph that rearranges itself when a
# room is added is one nobody can navigate twice.
check("...laid out left to right", _wb[1][2] > _wb[0][2],
      str([(b[2], b[3]) for b in _wb]))
check("a room id resolves to its index",
      _wd._room_by_id("rm_cave") == 1 and _wd._room_by_id("rm_gone") is None)

_wd._on_world_click(_wd._world_canvas,
                    btn(_wb[1][2] + 5, _wb[1][3] + 5))
pump()
# The reason to find a room on a map is to go and change it.
check("clicking a room selects it", _wd._sel == ("room", 1), str(_wd._sel))

# Placing a door with the map open must not leave the map stale.
_wd._select_resource("room", 0)
_wd._set_room_mode("warps")
pump()
_before = _wd._world_count.get_text()
_wd._on_room_click(_wd._room_canvas, btn(60, 60))
pump()
check("placing a door updates the map", _wd._world_count.get_text() != _before,
      "%r -> %r" % (_before, _wd._world_count.get_text()))
_wd.destroy()
pump()

# ------------------------------------------------------------------ play
# Notebook OS runs one app at a time, so the emulator cannot live inside this
# window. What CAN be fixed is the walk between a change and seeing it, and
# the thing that must never happen is hiding behind an emulator that did not
# start -- a build failure would look like the machine freezing.
import subprocess as _sp                                   # noqa: E402
_pl = app()
_launched = []
_real_popen = _sp.Popen
gbasdk.subprocess.Popen = lambda argv, **kw: (
    _launched.append(argv) or type("P", (), {"pid": os.getpid()})())
_hidden = []
_pl.hide = lambda: _hidden.append(1)

_pl._new_project()
_pl._file_play()
pump()
check("a game with no object or room does not try to play",
      not _launched, str(_launched))
check("...and does not hide behind an emulator that never opened",
      not _hidden)

# A project that cannot compile must also stay visible.
_pl._file_example()
_bad = gbabuild.build_rom
gbabuild.build_rom = lambda *a, **k: (False, None, "deliberate failure")
_pl._file_play()
pump()
gbabuild.build_rom = _bad
check("a game that does not compile stays visible", not _hidden and not _launched)
check("...and keeps the log so Build Details can explain",
      "deliberate failure" in getattr(_pl, "_last_log", ""))

# A good build hands off, with the ROM as the argument.
gbabuild.build_rom = lambda m, o, **k: (True, os.path.join(o, "game.gba"), "ok")
_pl._file_play()
pump()
gbabuild.build_rom = _bad
gbabuild.build_rom = _real_build
check("a good build launches the emulator", len(_launched) == 1, str(_launched))
check("...on the ROM it just built",
      _launched and _launched[0][-1].endswith("game.gba"), str(_launched))
check("...through the emulator module",
      _launched and _launched[0][1].endswith("gbaemu.py"), str(_launched))
check("...and gets out of the way", bool(_hidden))
gbasdk.subprocess.Popen = _real_popen
_pl.destroy()
pump()

# ---------------------------------------------------------------- budget
# "Over by 40 tiles" is a fact nobody can act on. The report has to name the
# asset to blame, which is the whole difference between a number and a decision.
_bg = {"name": "B", "sounds": [], "objects": [], "scripts": [], "tables": [],
       "start_room": None,
       "sprites": [{"id": "b", "name": "Boss", "w": 64, "h": 64,
                    "frames": [[0] * 4096] * 20},
                   {"id": "h", "name": "Hero", "w": 16, "h": 16,
                    "frames": [[0] * 256] * 4}],
       "tilesets": [{"id": "t", "name": "World", "size": 16,
                     "tiles": [[0] * 256] * 40}],
       "rooms": [{"id": "r", "name": "Field", "w": 240, "h": 160,
                  "instances": [{"object": "o", "x": 0, "y": 0}] * 40}]}
_br = gbabuild.budget_report(_bg)
_by = {l["name"]: l for l in _br["lines"]}
check("the budget counts every frame of every sprite",
      _by["Sprite tiles"]["used"] == 20 * 64 + 4 * 4,
      str(_by["Sprite tiles"]["used"]))
check("...and knows it is over", _by["Sprite tiles"]["over"] is True)
# The point of the whole report.
check("...and names the sprite to blame",
      _by["Sprite tiles"]["worst"][0]["name"] == "Boss",
      str(_by["Sprite tiles"]["worst"][:1]))
check("...with enough detail to act on",
      "64x64" in _by["Sprite tiles"]["worst"][0]["detail"],
      str(_by["Sprite tiles"]["worst"][0]["detail"]))
check("...largest first",
      _by["Sprite tiles"]["worst"][0]["cost"]
      >= _by["Sprite tiles"]["worst"][-1]["cost"])
# A 16x16 tile is four hardware tiles; counting authored tiles would under-
# report by four and let a project sail past the limit.
check("a 16x16 tile set counts as four tiles each",
      _by["Background tiles"]["used"] == 40 * 4 + 1,
      str(_by["Background tiles"]["used"]))
check("instances are counted per room, not in total",
      _by["Objects in a room"]["used"] == 40,
      str(_by["Objects in a room"]["used"]))
check("a project within its limits is not marked over",
      not gbabuild.budget_report(
          {"sprites": [{"id": "s", "w": 8, "h": 8, "frames": [[0] * 64]}],
           "tilesets": [], "sounds": [], "objects": [], "rooms": [],
           "scripts": [], "tables": []})["over"])
check("an empty project has a budget too",
      isinstance(gbabuild.budget_report({}), dict))
_bmenu = [i[0] for i in app().menu_items("Build") if i is not nbapp.SEP]
check("the budget is reachable from the Build menu",
      any("Costs" in lab for lab in _bmenu), str(_bmenu))

# ---------------------------------------------------------------- tables
# Rows with named columns, emitted as a C struct array. What a game of any size
# is mostly made of, and otherwise a thousand-line script nobody can edit.
_tb = app()
_tb._new_project()
_tb._add_resource("table")
_tb._select_resource("table", 0)
pump()
_t0 = _tb._sel_res()
check("a new table has a column and a row",
      len(_t0["columns"]) == 1 and len(_t0["rows"]) == 1, str(_t0))
_tb._table_add_col()
_tb._table_add_col()
_tb._table_add_row()
pump()
# A column added to the header must be added to every row, or the grid and the
# generator disagree about how wide the table is.
check("adding a column widens every existing row",
      all(len(r) == len(_t0["columns"]) for r in _t0["rows"]),
      str([len(r) for r in _t0["rows"]]))

_t0["columns"][0] = {"name": "Name", "type": "text"}
_t0["columns"][1] = {"name": "Base HP", "type": "int"}
_t0["columns"][2] = {"name": "Can Fly", "type": "bool"}
_tb._on_table_edit(None, "0", "Bulbasaur", 0)
_tb._on_table_edit(None, "0", "45kg", 1)
_tb._on_table_edit(None, "0", "yes", 2)
check("a text cell keeps its text", _t0["rows"][0][0] == "Bulbasaur")
# Storing "45kg" in a Number column would build a C initialiser that does not
# compile, and the error would name the generated file rather than the cell.
check("a number cell keeps a number even when the text is not one",
      _t0["rows"][0][1] == 0, repr(_t0["rows"][0][1]))
check("a yes/no cell reads words as well as digits",
      _t0["rows"][0][2] is True, repr(_t0["rows"][0][2]))

_tc = gbabuild.generate_c(_tb.proj)
check("a table reaches the C as a struct", "typedef struct {" in _tc
      and "nb_row_" in _tc)
# C cannot ask an array its length once it has decayed to a pointer, and a game
# that hard-codes the row count reads past the end the first time a row is added.
check("...with a count beside it", re.search(r"const int nb_\w+_count = 2;", _tc)
      is not None, str([l for l in _tc.splitlines() if "_count" in l][:4]))
check("...names an author would write become C identifiers",
      "Base_HP" in _tc, str([l.strip() for l in _tc.splitlines()
                             if "s32" in l][:3]))
check("...and text is quoted and escaped", '"Bulbasaur"' in _tc)

# A quote in a cell must not end the C string literal early.
_t0["rows"][0][0] = 'say "hi"'
check("a quote in a cell is escaped",
      '\\"hi\\"' in gbabuild.generate_c(_tb.proj),
      str([l.strip() for l in gbabuild.generate_c(_tb.proj).splitlines()
           if "hi" in l][:2]))

# Round trip, and the shapes a real edit leaves behind.
_ragged = {"name": "R", "sprites": [], "tilesets": [], "sounds": [],
           "objects": [], "rooms": [], "scripts": [], "start_room": None,
           "tables": [{"id": "tbl_1",
                       "columns": [{"name": "a", "type": "int"},
                                   {"name": "b", "type": "text"}],
                       "rows": [[1], [1, "x", "extra"], [2, "y"]]}]}
_rp, _rlost = _tb._sane_project(_ragged)
check("a short row is padded to the columns",
      _rp["tables"][0]["rows"][0] == [1, ""], str(_rp["tables"][0]["rows"][0]))
check("...and a long one trimmed",
      _rp["tables"][0]["rows"][1] == [1, "x"], str(_rp["tables"][0]["rows"][1]))
check("...losing no rows", len(_rp["tables"][0]["rows"]) == 3)
check("...and no data", _rlost == 0)
# A table with no columns at all would emit a struct with no fields, which C
# rejects; it gains one rather than breaking the build.
_nocol, _ = _tb._sane_project({"name": "N", "sprites": [], "tilesets": [],
                               "sounds": [], "objects": [], "rooms": [],
                               "scripts": [], "start_room": None,
                               "tables": [{"id": "t", "columns": [],
                                           "rows": []}]})
check("a table with no columns gains one", len(_nocol["tables"][0]["columns"]) == 1)
_tb.destroy()
pump()

# ------------------------------------------------------------- multiboot
# A build target nobody can select is the same failure as a runtime feature
# nothing emits, which this project has now hit five times.
_mb_items = [i[0] for i in app().menu_items("File") if i is not nbapp.SEP]
check("a link-cable export is offered in the File menu",
      any("Link Cable" in lab for lab in _mb_items), str(_mb_items))
check("...and it is a separate entry from the cartridge export",
      len([lab for lab in _mb_items if "Export" in lab]) >= 2, str(_mb_items))
check("build_rom takes a multiboot option",
      "multiboot" in _real_build.__code__.co_varnames)
check("...and it changes the file that comes out",
      gbabuild.MULTIBOOT_MAX == 256 * 1024, str(gbabuild.MULTIBOOT_MAX))

# ------------------------------------------------------------------- PCM
# Sampled audio on Direct Sound A. The GBA has no resampler -- the timer period
# IS the sample rate -- so conversion happens on import and there is exactly
# one rate.
import math                                                # noqa: E402
import struct                                              # noqa: E402
import wave                                                # noqa: E402

_wavp = os.path.join(HOME, "tone.wav")
_ww = wave.open(_wavp, "wb")
_ww.setnchannels(2)
_ww.setsampwidth(2)
_ww.setframerate(44100)                 # none of the target format, on purpose
_ww.writeframes(b"".join(
    struct.pack("<hh", int(math.sin(i * 2 * math.pi * 440 / 44100) * 20000),
                int(math.sin(i * 2 * math.pi * 440 / 44100) * 20000))
    for i in range(44100 // 2)))
_ww.close()

_pc = app()
_pcm, _secs = _pc._read_wav(_wavp)
check("a 44.1 kHz stereo WAV converts to the one rate the hardware plays",
      abs(len(_pcm) - 8192) <= 4, "%d samples" % len(_pcm))
check("...taking half a second with it", abs(_secs - 0.5) < 0.01, str(_secs))
# 8-bit WAV is UNSIGNED and the FIFO is SIGNED; getting that backwards plays a
# loud buzz at the right pitch, which reads as a broken rate rather than a sign.
check("...as signed 8-bit", min(_pcm) < 0 < max(_pcm),
      "%d..%d" % (min(_pcm), max(_pcm)))
check("...within one byte", min(_pcm) >= -128 and max(_pcm) <= 127)
# The FIFO takes 32 bits at a time. A short final word would send whatever
# follows the array to the speaker.
check("...a whole number of 32-bit words", len(_pcm) % 4 == 0)
# A 440 Hz tone at 16384 Hz crosses zero about 10.7 times per 200 samples.
_signs = [1 if v > 0 else 0 for v in _pcm[:200]]
_flips = sum(1 for i in range(1, len(_signs)) if _signs[i] != _signs[i - 1])
check("...still sounding 440 Hz after resampling", 8 <= _flips <= 14,
      "%d zero crossings per 200 samples" % _flips)

_pc.proj["sounds"] = [{"id": "snd_s", "tempo": 8, "loop": False, "steps": 16,
                       "lead": [0] * 16, "bass": [0] * 16, "drum": [0] * 16,
                       "kind": 1, "duty": 0, "vol": 0, "decay": 0,
                       "pcm": _pcm}]
_pc.proj["objects"] = [{"id": "o", "sprite": None, "visible": True,
                        "solid": False, "events": [
                            {"type": "create", "actions": [
                                {"kind": "play_sound", "sound": "snd_s"}]}]}]
_pcc = gbabuild.generate_c(_pc.proj)
check("a sample reaches the cartridge as signed bytes",
      "static const signed char snd_pcm_0[]" in _pcc)
# Routing lives in the RUNTIME, not the generator: an action and a line of C
# calling rt_play_sound must get the same answer. Deciding it while generating
# meant a sampled sound played its empty pattern whenever it was reached from
# code -- one rule with two behaviours.
_srow = re.search(r"const nb_Sound nb_sounds\[\].*?};", _pcc, re.S).group(0)
_pf = [f.strip() for f in re.search(r"\{([^{}]*)\},", _srow).group(1).split(",")]
check("the sample travels in the sound, where the runtime finds it",
      _pf[11] == "snd_pcm_0", str(_pf[11:13]))
check("...with its length beside it", _pf[12] == str(len(_pcm)),
      "%s vs %d" % (_pf[12], len(_pcm)))
check("...so one Play Sound call serves both kinds",
      "rt_play_sound(0);" in _pcc)

# A sound with no sample still plays its pattern.
_pc.proj["sounds"][0].pop("pcm")
_pat = gbabuild.generate_c(_pc.proj)
check("a pattern sound still plays its pattern", "rt_play_sound(0);" in _pat)
check("...and emits no sample array", "snd_pcm_0" not in _pat)
_prow = re.search(r"const nb_Sound nb_sounds\[\].*?};", _pat, re.S).group(0)
_ppf = [f.strip() for f in re.search(r"\{([^{}]*)\},", _prow).group(1).split(",")]
check("...and points at no sample", _ppf[11] == "0" and _ppf[12] == "0",
      str(_ppf[11:13]))

# Round trip: a sample must survive save and load intact.
_rt, _lost = _pc._sane_project(json.loads(json.dumps(
    {"name": "P", "sprites": [], "tilesets": [], "objects": [], "rooms": [],
     "scripts": [], "start_room": None,
     "sounds": [{"id": "snd_s", "steps": 16, "pcm": _pcm[:64]}]})))
check("a sample survives a round trip", _rt["sounds"][0].get("pcm") == _pcm[:64],
      str(len(_rt["sounds"][0].get("pcm") or [])))
check("...losing nothing", _lost == 0)
# A pattern sound must not gain an empty list it then carries through every save.
_rt2, _ = _pc._sane_project({"name": "P", "sounds": [{"id": "s", "steps": 16}],
                             "sprites": [], "tilesets": [], "objects": [],
                             "rooms": [], "scripts": [], "start_room": None})
check("a pattern sound gains no empty sample list",
      "pcm" not in _rt2["sounds"][0], str(_rt2["sounds"][0].keys()))
_pc.destroy()
pump()

# ------------------------------------------------------- built-in effects
# The runtime carries twelve effects that need no data. Play Sound only ever
# offered the project's OWN sounds, so a new project could not make a noise
# until somebody had written a tune -- a long way from a first jump.
_sx = app()
_sx._new_project()
_sx._add_resource("object")
_sx._select_resource("object", 0)
pump()
_sxo = _sx._cur_object()
_sxo["events"] = [{"type": "create", "actions": []}]
_sx._sel_event = 0
_sx._add_action("play_sound")
pump()
_sxa = _sxo["events"][0]["actions"][0]
check("Play Sound on an empty project picks a built-in effect",
      str(_sxa.get("sound", "")).startswith("sfx:"), repr(_sxa.get("sound")))
_sx_opts = _sx._param_options("snd")
check("...and the picker offers all twelve",
      len([o for o in _sx_opts if o.startswith("sfx:")]) == 12,
      str(len(_sx_opts)))

_sxa["sound"] = "sfx:coin"
_sxc = gbabuild.generate_c(_sx.proj)
check("a built-in effect compiles to rt_sfx", "rt_sfx(NB_SFX_COIN);" in _sxc,
      str([l.strip() for l in _sxc.splitlines() if "sfx" in l.lower()][:3]))
check("...and raises no problem", not gbabuild.check_project(_sx.proj),
      str(gbabuild.check_project(_sx.proj))[:120])

# A project sound still wins, and a name matching neither is still reported --
# adding the built-ins must not turn a broken reference into silence.
_sx.proj["sounds"] = [{"id": "snd_1", "tempo": 6, "loop": True, "steps": 4,
                       "lead": [60] * 4, "bass": [0] * 4, "drum": [0] * 4,
                       "kind": 0, "duty": 0, "vol": 0, "decay": 0}]
_sxa["sound"] = "snd_1"
check("a project sound still compiles to rt_play_sound",
      "rt_play_sound(0);" in gbabuild.generate_c(_sx.proj))
_sxa["sound"] = "snd_gone"
check("a name that is neither is still reported",
      any("Play Sound" in p for p in gbabuild.check_project(_sx.proj)),
      str(gbabuild.check_project(_sx.proj))[:120])
_sx.destroy()
pump()

# ----------------------------------------------------------------- audio
# nb_Sound carries five appended fields the generator never emitted, so no
# built game has ever had percussion and every sound effect stopped the music
# instead of layering over it. Fourth instance tonight of a feature finished on
# one side of a seam and never connected on the other.
_au = app()
_au._new_project()
_au._add_resource("sound")
_au._select_resource("sound", 0)
pump()
_snd = _au._cur_sound()
check("a new sound carries a drum lane", isinstance(_snd.get("drum"), list))
check("...as long as the pattern", len(_snd["drum"]) == _snd.get("steps", 16))

_au._pick_channel("drum")
pump()
# Drums are four kinds on the top four rows, not pitches.
_au._snd_toggle(0, gbasdk.PITCH_HI)          # crash
_au._snd_toggle(4, gbasdk.PITCH_HI - 3)      # kick
check("the top row lays a crash", _snd["drum"][0] == 4, str(_snd["drum"][:5]))
check("the fourth row lays a kick", _snd["drum"][4] == 1, str(_snd["drum"][:5]))
_au._snd_toggle(0, gbasdk.PITCH_HI)
check("clicking the same cell clears it", _snd["drum"][0] == 0)
# A pitch row is not a drum row; a click there must do nothing rather than
# writing a drum number that means a different instrument.
_au._snd_toggle(1, gbasdk.PITCH_LO)
check("a pitch row lays no drum", _snd["drum"][1] == 0, str(_snd["drum"][:5]))

for _combo, _key, _val in ((_au._snd_kind, "kind", "1"),
                           (_au._snd_duty, "duty", "2"),
                           (_au._snd_vol, "vol", "10"),
                           (_au._snd_decay, "decay", "3")):
    _combo.set_active_id(_val)
    pump()
    check("%s reaches the sound" % _key, _snd.get(_key) == int(_val),
          str(_snd.get(_key)))

_ac = gbabuild.generate_c(_au.proj)
_srow = re.search(r"const nb_Sound nb_sounds\[\].*?};", _ac, re.S).group(0)
_sf = [f.strip() for f in re.search(r"\{([^{}]*)\},", _srow).group(1).split(",")]
check("every sound field reaches the C", len(_sf) == 13,
      "%d: %s" % (len(_sf), _sf))
check("...including the drum track", _sf[5].startswith("snd_drum"), str(_sf[5]))
check("...and kind, duty, volume and decay",
      _sf[6:10] == ["1", "2", "10", "3"], str(_sf[6:10]))
check("...and the priority", _sf[10] == "0", str(_sf[10]))
check("a drum array is emitted", "snd_drum_0" in _ac)

# A sound with no drums must not emit an array of zeroes for the runtime to
# walk; a null pointer is what "no percussion" means to it.
_quiet = json.loads(json.dumps(_au.proj))
_quiet["sounds"][0]["drum"] = [0] * 16
_qc = gbabuild.generate_c(_quiet)
check("a silent drum track emits no array", "snd_drum_0" not in _qc)
_qrow = re.search(r"const nb_Sound nb_sounds\[\].*?};", _qc, re.S).group(0)
check("...and the sound points at nothing", ", 0, 1, 2," in _qrow, _qrow[-70:])
_au.destroy()
pump()

# ------------------------------------------------------------- autotiles
# Sixteen variants of one terrain, picked from the four orthogonal neighbours.
# Authoring only: what lands in the tilemap is an ordinary tile index.
_at = app()
_at._new_project()
_at._add_resource("tileset")
_at._add_resource("room")
pump()
_ats = _at._res("tileset")[0]
_ats["tiles"] = [[0] * 64 for _ in range(16)]
_ats["auto_base"] = 0
_runs = _at._auto_runs()
check("a declared run is found", len(_runs) == 1, str(_runs)[:80])
check("...holding sixteen variants",
      len(_runs[0][0]) == 16 if _runs else False)

# A run needs fifteen tiles after its start. Declaring one on a set too short
# must yield NO run rather than one that reads past the end into another
# terrain's tiles -- which looks like corruption, not like a bad setting.
_ats["auto_base"] = 8
check("a run without room to finish is not declared", _at._auto_runs() == [])
_ats["auto_base"] = 0
pump()

_at._select_resource("room", 0)
pump()
_atrm = _at._cur_room()
_tm, _cw, _ch = _at._room_tilemap(_atrm)
_base = _at._auto_runs()[0][0][0]
_at._room_tile = _base
_at._paint_room_tile(_atrm, 5, 5)
check("an isolated cell takes variant 0", _tm[5 * _cw + 5] == _base,
      str(_tm[5 * _cw + 5] - _base))
_at._paint_room_tile(_atrm, 6, 5)
# Fitting the NEIGHBOURS matters as much as the cell: a tool that only fits the
# cell under the pointer leaves a seam behind every stroke.
check("placing beside one re-fits the older cell",
      _tm[5 * _cw + 5] - _base == 2, str(_tm[5 * _cw + 5] - _base))
check("...and the new one faces it",
      _tm[5 * _cw + 6] - _base == 8, str(_tm[5 * _cw + 6] - _base))
# Outside the room counts as the same terrain, or every level gets a coastline.
_at._paint_room_tile(_atrm, 0, 0)
check("the room edge counts as the same terrain",
      _tm[0] - _base == 9, str(_tm[0] - _base))
# Erasing must not go through the auto path.
_at._paint_room_tile(_atrm, 5, 5, erase=True)
check("erasing clears the cell", _tm[5 * _cw + 5] == 0)

# Persistence: a run whose set later loses tiles must be DROPPED on load, not
# clamped into a different terrain.
_short = {"name": "S", "sprites": [], "sounds": [], "objects": [], "rooms": [],
          "scripts": [], "start_room": None,
          "tilesets": [{"id": "ts_1", "size": 8, "auto_base": 0,
                        "tiles": [[0] * 64 for _ in range(4)]}]}
_sp, _ = _at._sane_project(_short)
check("a run with too few tiles is dropped on load",
      "auto_base" not in _sp["tilesets"][0], str(_sp["tilesets"][0].keys()))
_long = json.loads(json.dumps(_short))
_long["tilesets"][0]["tiles"] = [[0] * 64 for _ in range(16)]
_lp, _ = _at._sane_project(_long)
check("...and kept when it fits", _lp["tilesets"][0].get("auto_base") == 0)
_at.destroy()
pump()

# -------------------------------------------------------------- palettes
# 16 banks of 16, index 0 transparent. The allocator already refused to
# overflow; what was missing was any way to SEE the allocation before a build.
def _spr(name, colours, pin=None, size=8):
    px = [colours[i % len(colours)] for i in range(size * size)]
    d = {"id": "spr_" + name.lower(), "name": name, "w": size, "h": size,
         "ox": size // 2, "oy": size // 2, "anim_speed": 0, "frames": [px]}
    if pin is not None:
        d["pal_bank"] = pin
    return d


_C = [c for _n, c in gbasdk.PALETTE[1:]]
_pal_proj = {"name": "Pal", "tilesets": [], "sounds": [], "objects": [],
             "rooms": [], "scripts": [], "start_room": None,
             "sprites": [_spr("Hero", _C[:6]), _spr("Slime", _C[:4]),
                         _spr("Boss", _C[6:12])]}
_rep = gbabuild.palette_report(_pal_proj)
check("the report names every sprite", len(_rep["sprites"]) == 3)
check("sprites sharing colours share a bank",
      _rep["sprites"][0]["bank"] == _rep["sprites"][1]["bank"])
check("...and the bank lists both",
      set(_rep["banks"][0]["sprites"]) >= {"Hero", "Slime"})
# Hero's 6 and Boss's 6 are distinct, Slime's 4 are a subset of Hero's: 12 in
# one bank. Assert the INVARIANT rather than that arithmetic, which is the
# allocator's business and may improve.
check("free room is what is left of fifteen",
      all(b["free"] == 15 - b["used"] for b in _rep["banks"]))
check("a bank never holds more than fifteen",
      all(b["used"] <= 15 for b in _rep["banks"]))
check("every sprite is placed in a bank the report lists",
      {s["bank"] for s in _rep["sprites"]}
      <= {b["index"] for b in _rep["banks"]})

# The report must describe the allocation that SHIPS, not a second guess at it.
_g = gbabuild._Gen(_pal_proj)
_g._build_obj_palette()
check("the report agrees with the generator",
      [s["bank"] for s in _rep["sprites"]]
      == [_g._spr_bank[i] for i in range(3)])

# Pinning: the only way to say "these two are the same character, share tiles".
_pinned = json.loads(json.dumps(_pal_proj))
_pinned["sprites"][0]["pal_bank"] = 5
_rp = gbabuild.palette_report(_pinned)
check("a pinned sprite lands in the bank asked for",
      _rp["sprites"][0]["bank"] == 5)
check("...and an unpinned one is unaffected", _rp["sprites"][1]["bank"] == 0)

# A pin that cannot fit must SAY so. Silently ignoring it is the failure this
# whole pane exists to end -- a setting that reads as applied and is not.
_full = {"name": "Full", "tilesets": [], "sounds": [], "objects": [],
         "rooms": [], "scripts": [], "start_room": None,
         "sprites": [_spr("Big", _C[:14], pin=0), _spr("Also", _C[14:26], pin=0)]}
_rf = gbabuild.palette_report(_full)
check("a pin that does not fit is reported",
      any("pinned" in p for p in _rf["problems"]), str(_rf["problems"])[:140])
check("...and the sprite is still placed somewhere",
      _rf["sprites"][1]["bank"] is not None)

# Over fifteen colours, named by the name the author gave it -- not its id,
# which is what the message used to print and nobody can map back to a sprite.
_over = {"name": "Over", "tilesets": [], "sounds": [], "objects": [],
         "rooms": [], "scripts": [], "start_room": None,
         "sprites": [_spr("Boss", _C[:20])]}
_ro = gbabuild.palette_report(_over)
check("a sprite over 15 colours is reported",
      any("15" in p for p in _ro["problems"]), str(_ro["problems"])[:140])
check("...by the name the author gave it",
      any(p.startswith("Boss") for p in _ro["problems"]),
      str(_ro["problems"])[:140])
check("...and the count says how many are lost",
      _ro["sprites"][0]["over"] == 5)

# Running out of banks entirely.
_many = {"name": "Many", "tilesets": [], "sounds": [], "objects": [],
         "rooms": [], "scripts": [], "start_room": None,
         "sprites": [_spr("S%d" % n, _C[n * 2:n * 2 + 2] or _C[:2])
                     for n in range(16)]}
_rm = gbabuild.palette_report(_many)
check("sixteen banks is the ceiling", _rm["used"] <= 16)

# The pane. Its head must NOT carry Rename and Delete: both act on whatever is
# selected in the browser, so on a pane that shows the whole project they would
# destroy something the pane never mentioned.
_pal_app = app()
_pal_app.proj = json.loads(json.dumps(_pal_proj))
_pal_app._editor_stack.show("palette")
_pal_app._load_palette_pane()
pump()
check("Palettes opens as a pane",
      _pal_app._editor_stack._active.current == "palette")
check("...and says how much room is left",
      "16" in _pal_app._pal_summary.get_text())


def _labels(widget, out=None):
    out = [] if out is None else out
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_text())
    if isinstance(widget, Gtk.Button):
        out.append(widget.get_label() or "")
    if isinstance(widget, Gtk.Container):
        for ch in widget.get_children():
            _labels(ch, out)
    return out


_pal_text = _labels(_pal_app._editor_stack.panes["palette"].widget)
check("no Delete button on a pane with no resource",
      not any("Delete" in (t or "") for t in _pal_text),
      str([t for t in _pal_text if "Delete" in (t or "")]))
check("no Rename button either",
      not any("Rename" in (t or "") for t in _pal_text))
check("the pane names itself instead",
      any("Palettes" == (t or "") for t in _pal_text), str(_pal_text[:6]))

# Pinning through the UI must reach the model and survive a save.
_pal_app._sel = None
_combo = Gtk.ComboBoxText()
_combo.append("auto", "Any set")
for _n in range(16):
    _combo.append(str(_n), "Set %d" % _n)
_combo.set_active_id("7")
_pal_app._on_pin_bank(_combo, 0)
check("pinning through the pane reaches the sprite",
      _pal_app.proj["sprites"][0].get("pal_bank") == 7)
_combo.set_active_id("auto")
_pal_app._on_pin_bank(_combo, 0)
check("...and unpinning removes it rather than storing a sentinel",
      "pal_bank" not in _pal_app.proj["sprites"][0])
_pal_app.destroy()
pump()

# --------------------------------------------------------------- scripts
# File-scope C. The reason it exists: an Execute Code action is emitted inside
# an event function, so a handler with a static variable -- what the runtime's
# own interrupt API asks for -- could not be written anywhere in the tool.
_sc_app = app()
_sc_app._add_resource("script")
pump()
check("a new script is created", len(_sc_app._res("script")) == 1)
check("...with code that is not empty",
      bool((_sc_app._res("script")[0].get("code") or "").strip()))
_sc_app._select_resource("script", 0)
pump()
check("...and opens its own editor",
      _sc_app._editor_stack._active.current == "script")

_sc_app._res("script")[0]["code"] = "s16 dbl(s16 v) { return v * 2; }"
_sc_app.proj["objects"] = [{"id": "obj_s", "sprite": None, "visible": True,
                            "solid": False, "events": [
    {"type": "create", "actions": [
        {"kind": "execute_code", "lang": "C",
         "code": "self->x = dbl(20);"}]}]}]
_sc_c = gbabuild.generate_c(_sc_app.proj)
check("a script reaches the generated C", "s16 dbl(s16 v)" in _sc_c)
# C needs a declaration before its use, so this ordering is not cosmetic: get
# it backwards and every project calling a script fails to compile.
check("...before the object that calls it",
      _sc_c.index("s16 dbl(s16 v)") < _sc_c.index("dbl(20)"))
_sc_ok, _sc_problems = True, gbabuild.check_project(_sc_app.proj)
check("a project using a script has no build problems", not _sc_problems,
      str(_sc_problems)[:120])

# Two languages on one action, chosen rather than guessed.
_two = {"scripts": [{"id": "s1", "code": "s16 dbl(s16 v) { return v * 2; }"}],
        "objects": [{"id": "o", "events": [{"type": "create", "actions": [
            {"kind": "execute_code", "lang": "C",
             "code": "self->x = dbl(20);\nREG_BG0HOFS = 4;"},
            {"kind": "execute_code", "code": "x = 10; score += 5;"}]}]}],
        "rooms": [], "sprites": [], "sounds": [], "tilesets": []}
_two_c = gbabuild.generate_c(_two)
check("a C action reaches a hardware register", "REG_BG0HOFS = 4;" in _two_c)
check("...and a function its scripts define", "dbl(20)" in _two_c)
check("a Script action still lowers to the runtime", "nb_score += 5;" in _two_c)
check("the two languages coexist in one event",
      "self->x = dbl(20);" in _two_c and "self->x = 10;" in _two_c)
check("neither raises a problem", not gbabuild.check_project(_two),
      str(gbabuild.check_project(_two))[:120])

# The failure this replaces: C handed to the Script compiler is rejected as a
# whole and the row silently stops doing anything. It must still be REPORTED.
_wrong = json.loads(json.dumps(_two))
_wrong["objects"][0]["events"][0]["actions"][0].pop("lang")
check("C in a Script row is reported, not silently dropped",
      bool(gbabuild.check_project(_wrong)))

# A C block declares its own locals. Counting them as instance variables would
# spend slots on things that are not instance state -- and there are twelve.
_locals = {"objects": [{"id": "o2", "events": [{"type": "create", "actions": [
    {"kind": "execute_code", "lang": "C",
     "code": "\n".join("s16 v%d = %d;" % (n, n) for n in range(20))}]}]}],
    "rooms": [], "sprites": [], "sounds": [], "tilesets": [], "scripts": []}
check("C locals do not consume instance variable slots",
      not gbabuild.check_project(_locals),
      str(gbabuild.check_project(_locals))[:120])

# A recipe filed as script scope must land as a script, not as an action.
_before_scripts = len(_sc_app._res("script"))
check("a script recipe inserts as a script",
      _sc_app._insert_code("void f(void) {}", "script") is True
      and len(_sc_app._res("script")) == _before_scripts + 1)
_sc_app.proj["objects"] = [{"id": "obj_r", "sprite": None, "visible": True,
                            "solid": False,
                            "events": [{"type": "step", "actions": []}]}]
_sc_app._sel = ("object", 0)
_sc_app._sel_event = 0
check("an event recipe inserts as an action",
      _sc_app._insert_code("self->x += 1;", "event") is True)
check("...marked as C, because a recipe IS C",
      _sc_app.proj["objects"][0]["events"][0]["actions"][-1].get("lang") == "C")
_sc_app._sel = None
_sc_app._sel_event = None
check("an event recipe with no event selected is refused, not lost",
      _sc_app._insert_code("self->x = 1;", "event") is False)
_sc_app.destroy()
pump()

# ------------------------------------------------------------------ help
# The reference and the course are a PANE, not a separate window, and the
# course reads the live project. Both of those are wiring that a content-only
# test would never touch: the pane can be perfect and unreachable.
_h_app = gbasdk.GbaSdk()
pump()
_h_ids = set(_h_app._editor_stack.panes)
check("Help is registered as a pane", "help" in _h_ids)
_h_menu = _h_app.menu_items("Help")
_h_labels = [i[0] for i in _h_menu if i is not nbapp.SEP]
check("a Help menu exists", len(_h_labels) >= 4)
check("...and it offers the course",
      any("Course" in lab for lab in _h_labels))
check("...and the course label carries progress",
      any(re.search(r"\(\d+/\d+\)", lab) for lab in _h_labels))

_h_app._open_help("c01")
pump()
check("opening a topic shows the Help pane",
      _h_app._editor_stack._active.current == "help")
check("...on the topic asked for", _h_app._help._current == "c01")

# Show C is greyed until an event is selected. A live-looking item that does
# nothing on click is the failure mode this app has already had twice.
_h_app._sel = None
check("Show C is unavailable with nothing selected",
      dict((i[0], i[1]) for i in _h_app.menu_items("Help")
           if i is not nbapp.SEP).get("Show C for This Event") is None)

_h_app.proj["objects"].append({
    "id": "obj_t", "name": "Teach", "sprite": None,
    "events": [{"type": "step", "actions": [
        {"kind": "move_fixed", "dir": "right", "speed": 2}]}]})
_h_app._sel = ("object", len(_h_app.proj["objects"]) - 1)
_h_app._sel_event = 0
check("Show C is available with an event selected",
      _h_app._cur_event() is not None)
_c, _p = gbabuild.preview_event_c(_h_app.proj, _h_app._sel_res(),
                                  _h_app._cur_event())
check("...and it generates that event's C", "self->hspeed = 2;" in _c)

# The course counts the REAL project, so adding work moves it.
_before_done = gbahelp.course_progress(_h_app.proj)[0]
_h_app.proj["objects"][-1]["events"][0]["actions"].append(
    {"kind": "execute_code", "code": "s16 t = 0;\nif (t) rt_shake(4, 2);"})
_after_done = gbahelp.course_progress(_h_app.proj)[0]
check("the course tracks the open project", _after_done > _before_done)
_h_app.destroy()
pump()

# ===================================================================== end
print("\n%d/%d checks passed" % (sum(RESULTS), len(RESULTS)))
if FAILED:
    print("\nFAILED:")
    for name in FAILED:
        print("  - " + name)
shutil.rmtree(HOME, ignore_errors=True)
sys.exit(0 if all(RESULTS) else 1)
