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

        def guarded(*a, _n=_name, _real=getattr(w, _name)):
            w._depth[_n] = w._depth.get(_n, 0) + 1
            w._maxdepth[_n] = max(w._maxdepth.get(_n, 0), w._depth[_n])
            try:
                if w._depth[_n] > 3:
                    raise RuntimeError("%s re-entered itself" % _n)
                return _real(*a)
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
check("every resource type survives close and reopen intact",
      after == before,
      [k for k in set(list(before) + list(after)) if before.get(k) != after.get(k)])
for kind in ("sprites", "tilesets", "sounds", "objects", "rooms"):
    check("  ... %s round-trip" % kind, after.get(kind) == before.get(kind))
check("the start room survives", after.get("start_room") == before.get("start_room"))

# .gbaproj save + open round trip
proj_path = os.path.join(HOME, "Documents", "mine.gbaproj")
nbpicker.save_file = lambda *a, **k: proj_path
nbpicker.open_file = lambda *a, **k: proj_path
w._file_save_as()
check("Save Project As writes a .gbaproj", os.path.isfile(proj_path))
saved = json.load(open(proj_path))
check("...containing the whole project", saved == before,
      [k for k in set(list(saved) + list(before)) if saved.get(k) != before.get(k)])
w._file_new()
w._file_open()
pump()
check("Open Project reads it back whole", w.proj == before,
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
                      toolchain_dir=TOOLCHAIN:
                      _real_build(model, outdir, runtime_dir, toolchain_dir))
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
check("exporting does not leave build keys in the saved project",
      not [k for k in w.proj["objects"][0] if k.startswith("_")],
      sorted(k for k in w.proj["objects"][0] if k.startswith("_")))
w.destroy()

w = app()
w.proj = {"name": "Empty", "sprites": [], "sounds": [], "tilesets": [],
          "objects": [], "rooms": [], "start_room": None}
w._cards = []
w._file_export()
check("exporting an empty project explains what a game needs instead of failing",
      w._cards and "compile" in w._cards[-1][0].lower(), w._cards)
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

# ===================================================================== end
print("\n%d/%d checks passed" % (sum(RESULTS), len(RESULTS)))
if FAILED:
    print("\nFAILED:")
    for name in FAILED:
        print("  - " + name)
shutil.rmtree(HOME, ignore_errors=True)
sys.exit(0 if all(RESULTS) else 1)
