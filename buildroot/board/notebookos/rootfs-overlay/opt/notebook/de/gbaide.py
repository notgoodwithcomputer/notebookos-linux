#!/usr/bin/env python3
"""
GBA IDE — a Game-Maker-8-style game maker that exports real Game Boy Advance
ROMs. Resources (Sprites, Objects, Rooms) sit in a tree on the left; the centre
is a context editor: a pixel canvas for sprites, a Game-Maker events+actions
sheet for objects, and a placement grid for rooms. Compile & Export builds the
whole project to a real .gba with the bundled arm-none-eabi toolchain (see
de/gbabuild.py + /opt/notebook/gbaruntime) and saves it for the user to play in
the GBA Emulator app, or on a real console or flashcart (Notebook OS runs one
app at a time, so there is no in-IDE emulator launch).

The project persists to $NB_HOME/.config/notebook/gbaide.json (session recovery)
and to named .gbaproj files under Documents.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, Pango, GLib  # noqa: E402

import os
import sys
import json
import shutil
import tempfile

import nbapp
import nbpicker
import nbicons
import gbabuild
from nbi18n import _t  # noqa: E402

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
CFG_FILE = os.path.join(CFG_DIR, "gbaide.json")
PROJ_DIR = os.path.join(HOME, "Documents")

INK = "#1A1916"
MUTED = "#6E695E"
FAINT = "#9A9484"
RED = "#C8341E"

# A compact GBA-friendly 15-bit palette for the sprite editor (plus transparent).
TRANSPARENT = 0x7C1F
def _col(r, g, b):
    """A 15-bit BGR555 colour from 0..31 R/G/B components."""
    return (r & 31) | ((g & 31) << 5) | ((b & 31) << 10)


def _group_label(text):
    """A caption that starts a new group of controls on an editor's top row,
    set off by a margin rather than by padding spaces inside the string (which
    every translation would then have to reproduce)."""
    lbl = Gtk.Label(label=text)
    lbl.set_margin_start(14)
    return lbl


# A broad GBA-friendly 15-bit palette to paint with. Any one sprite still uses at
# most 15 colours (GBA 4bpp), but each sprite gets its OWN 16-colour hardware
# palette bank, so a game can show many colours at once (see gbabuild).
PALETTE = [
    ("Erase", TRANSPARENT),
    ("Black", _col(0, 0, 0)), ("D.Grey", _col(7, 7, 7)),
    ("Grey", _col(15, 15, 15)), ("L.Grey", _col(23, 23, 23)),
    ("White", _col(31, 31, 31)),
    ("D.Red", _col(15, 0, 0)), ("Red", _col(31, 0, 0)),
    ("Coral", _col(31, 12, 10)), ("Orange", _col(31, 17, 0)),
    ("Amber", _col(31, 24, 4)), ("Yellow", _col(31, 31, 0)),
    ("Gold", _col(27, 21, 0)), ("Olive", _col(14, 16, 0)),
    ("D.Green", _col(0, 13, 0)), ("Green", _col(0, 24, 0)),
    ("Lime", _col(15, 31, 8)), ("Mint", _col(18, 31, 22)),
    ("Teal", _col(0, 20, 18)), ("Cyan", _col(0, 31, 31)),
    ("Sky", _col(12, 22, 31)), ("Azure", _col(4, 14, 31)),
    ("Blue", _col(0, 0, 31)), ("Navy", _col(0, 0, 15)),
    ("Indigo", _col(12, 0, 24)), ("Purple", _col(20, 0, 28)),
    ("Magenta", _col(31, 0, 31)), ("Pink", _col(31, 16, 24)),
    ("Rose", _col(31, 8, 15)), ("Brown", _col(16, 10, 4)),
    ("Tan", _col(26, 20, 12)), ("Skin", _col(31, 24, 18)),
    ("D.Skin", _col(22, 14, 10)),
]

# Valid GBA OBJ sizes (w, h): square, wide, and tall.
SPRITE_SIZES = [(8, 8), (16, 16), (32, 32), (64, 64),
                (16, 8), (32, 8), (32, 16), (64, 32),
                (8, 16), (8, 32), (16, 32), (32, 64)]

# Event kinds offered on an object (Game-Maker-style).
EVENT_KINDS = [
    ("create", "Create"), ("step", "Step"), ("destroy", "Destroy"),
    ("key:left", "Key ◄"), ("key:right", "Key ►"), ("key:up", "Key ▲"),
    ("key:down", "Key ▼"), ("key:a", "Key A"), ("key:b", "Key B"),
    ("key:l", "Key L"), ("key:r", "Key R"),
    ("keypress:a", "Press A"), ("keypress:b", "Press B"),
    ("keypress:start", "Press Start"), ("keypress:up", "Press ▲"),
    ("keyrelease:a", "Release A"),
    ("alarm:0", "Alarm 0"), ("alarm:1", "Alarm 1"),
    ("alarm:2", "Alarm 2"), ("alarm:3", "Alarm 3"),
    ("collision", "Collision"),
]

# The action palette — Game-Maker's drag-drop action toolbox, one row each.
# params: (key, label, kind) where kind is "int", a direction list, or ("obj"/
# "room"/"var") resolved against the project.
DIRS = ["left", "right", "up", "down", "upleft", "upright", "downleft",
        "downright", "stop"]
CMP = ["==", "!=", "<", ">", "<=", ">="]
ACTION_DEFS = [
    ("move_fixed", "Move Fixed", [("dir", "Direction", DIRS),
                                  ("speed", "Speed", "int")]),
    ("set_hspeed", "Set H-Speed", [("value", "Value", "int")]),
    ("set_vspeed", "Set V-Speed", [("value", "Value", "int")]),
    ("jump_to", "Jump To", [("x", "X", "int"), ("y", "Y", "int")]),
    ("jump_relative", "Jump By", [("x", "X", "int"), ("y", "Y", "int")]),
    ("wrap", "Wrap Screen", []),
    ("create_instance", "Create Instance", [("object", "Object", "obj"),
                                             ("x", "X", "int"), ("y", "Y", "int")]),
    ("destroy_self", "Destroy Self", []),
    ("set_var", "Set Variable", [("var", "Name", "str"), ("value", "Value", "int")]),
    ("add_var", "Add To Variable", [("var", "Name", "str"), ("value", "Value", "int")]),
    ("if_var", "If Variable", [("var", "Name", "str"), ("op", "Op", CMP),
                               ("value", "Value", "int")]),
    ("if_collision", "If Collision", [("object", "Object", "obj")]),
    ("goto_room", "Go To Room", [("room", "Room", "room")]),
    ("play_sound", "Play Sound", [("sound", "Sound", "snd")]),
    ("stop_sound", "Stop Sound", []),
    # --- alarms & control flow ---
    ("set_alarm", "Set Alarm", [("alarm", "Alarm", ["0", "1", "2", "3"]),
                                ("steps", "Steps", "int")]),
    ("if_chance", "If Chance %", [("percent", "Percent", "int")]),
    ("repeat", "Repeat", [("count", "Times", "int")]),
    ("exit_event", "Exit Event", []),
    # --- physics / motion ---
    ("set_gravity", "Set Gravity", [("value", "Down accel", "int")]),
    ("move_toward", "Move Toward", [("x", "X", "int"), ("y", "Y", "int"),
                                    ("speed", "Speed", "int")]),
    # --- instance / sprite ---
    ("change_sprite", "Change Sprite", [("sprite", "Sprite", "spr")]),
    ("set_image_speed", "Anim Speed", [("value", "×16 frame/step", "int")]),
    ("destroy_object", "Destroy Object", [("object", "Object", "obj")]),
    # --- score / lives / health (global game state) ---
    ("set_score", "Set Score", [("value", "Value", "int")]),
    ("add_score", "Add Score", [("value", "Value", "int")]),
    ("if_score", "If Score", [("op", "Op", CMP), ("value", "Value", "int")]),
    ("set_lives", "Set Lives", [("value", "Value", "int")]),
    ("add_lives", "Add Lives", [("value", "Value", "int")]),
    ("if_lives", "If Lives", [("op", "Op", CMP), ("value", "Value", "int")]),
    ("set_health", "Set Health", [("value", "Value", "int")]),
    ("add_health", "Add Health", [("value", "Value", "int")]),
    ("if_health", "If Health", [("op", "Op", CMP), ("value", "Value", "int")]),
    # --- text / dialogue ---
    ("draw_text", "Draw Text", [("text", "Text", "str"), ("x", "X", "int"),
                                ("y", "Y", "int")]),
    ("draw_number", "Draw Number", [("value", "Value", "int"), ("x", "X", "int"),
                                    ("y", "Y", "int")]),
    ("clear_text", "Clear Text", []),
    # --- save games (SRAM: score/lives/health + global.* vars) ---
    ("save_game", "Save Game", []),
    ("load_game", "Load Game", []),
    # --- GML scripting ---
    ("execute_code", "Execute Code", [("code", "GML", "code")]),
]

# Piano-roll pitch range for the sound composer (C3 .. B5).
PITCH_LO, PITCH_HI = 48, 83
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
ACTION_LABEL = {a[0]: a[1] for a in ACTION_DEFS}
ACTION_PARAMS = {a[0]: a[2] for a in ACTION_DEFS}
# One-line help shown as a tooltip on each action in the palette.
ACTION_TIPS = {
    "move_fixed": "Set speed in a fixed direction",
    "set_hspeed": "Set horizontal speed", "set_vspeed": "Set vertical speed",
    "jump_to": "Teleport to an x, y position",
    "jump_relative": "Move by an x, y offset",
    "wrap": "Wrap around the screen edges",
    "create_instance": "Spawn an instance of an object",
    "destroy_self": "Destroy this instance",
    "set_var": "Set a custom variable", "add_var": "Add to a custom variable",
    "if_var": "Run the nested actions if a variable compares true",
    "if_collision": "Run the nested actions while touching an object",
    "goto_room": "Switch to another room",
    "play_sound": "Play a sound", "stop_sound": "Stop all sound",
    "set_alarm": "Start a countdown; fires the Alarm event when it hits 0",
    "if_chance": "Run the nested actions with a percent chance",
    "repeat": "Run the nested actions a number of times",
    "exit_event": "Stop running the rest of this event",
    "set_gravity": "Add downward acceleration each step",
    "move_toward": "Steer toward an x, y point at a speed",
    "change_sprite": "Change this instance's sprite",
    "set_image_speed": "Set the animation speed",
    "destroy_object": "Destroy every instance of an object",
    "set_score": "Set the score", "add_score": "Add to the score",
    "if_score": "Run the nested actions if the score compares true",
    "set_lives": "Set lives", "add_lives": "Add to lives",
    "if_lives": "Run the nested actions if lives compares true",
    "set_health": "Set health", "add_health": "Add to health",
    "if_health": "Run the nested actions if health compares true",
    "draw_text": "Draw text at a screen position",
    "draw_number": "Draw a number at a screen position",
    "clear_text": "Clear all on-screen text",
    "save_game": "Save score / lives / health + globals to the cartridge",
    "load_game": "Load the saved game from the cartridge",
    "execute_code": "Run custom GML code",
}
CONTAINER_ACTIONS = {"if_var", "if_collision", "if_chance", "repeat",
                     "if_score", "if_lives", "if_health"}  # carry nested children


def _u16(color):
    return int(color) & 0xFFFF


class GbaIde(nbapp.AppWindow):
    app_name = "GBA IDE"
    menus = ("File", "Edit", "Resource", "Build")

    def __init__(self):
        super().__init__()
        self._install_css()
        self._path = None
        self._suspend = False
        self._sel = None            # (kind, index) selected resource
        self._sel_event = None      # selected event index in the object editor
        self._sel_action = None     # selected action index
        self._paint_color = TRANSPARENT
        self._room_place = None     # object id being placed in the room editor
        self._sel_frame = 0         # selected animation frame in the sprite editor
        self._spr_tool = "pen"      # sprite tool: pen | fill | pick
        self._spr_play = None       # animation-preview timer id, or None
        self._spr_preview = 0       # frame shown while previewing
        self._sel_tile = 0          # selected tile in the tileset editor
        self._room_mode = "objects"  # room editor mode: "objects" | "tiles"
        self._room_tile = 1         # tileset tile index being painted (1-based)
        self._tile_pb_cache = {}    # (tuple(tile), scale) -> GdkPixbuf, for fast map draw
        self._snd_chan = "lead"     # active channel in the sound composer
        self._snd_btns = {}

        self._new_project()
        self._load_autosave()

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_vexpand(True)
        self.content.pack_start(self._toolbar(), False, False, 0)
        self.content.pack_start(body, True, True, 0)

        body.pack_start(self._resource_tree(), False, False, 0)
        # centre editor stack
        self._editor_stack = Gtk.Stack()
        self._editor_stack.set_hexpand(True)
        self._editor_stack.set_vexpand(True)
        self._editor_stack.add_named(self._welcome_pane(), "welcome")
        self._editor_stack.add_named(self._sprite_pane(), "sprite")
        self._editor_stack.add_named(self._tileset_pane(), "tileset")
        self._editor_stack.add_named(self._sound_pane(), "sound")
        self._editor_stack.add_named(self._object_pane(), "object")
        self._editor_stack.add_named(self._room_pane(), "room")
        body.pack_start(self._editor_stack, True, True, 0)

        self._render_tree()
        self._editor_stack.set_visible_child_name("welcome")
        self.connect("destroy", self._on_destroy)

    # ================= model =================
    def _new_project(self):
        self.proj = {"name": "Game", "sprites": [], "sounds": [],
                     "tilesets": [], "objects": [], "rooms": [],
                     "start_room": None}

    def _uid(self, prefix, existing):
        n = 1
        ids = {r.get("id") for r in existing}
        while "%s_%d" % (prefix, n) in ids:
            n += 1
        return "%s_%d" % (prefix, n)

    def _res(self, kind):
        return self.proj.get(kind + "s", [])

    def _sel_res(self):
        if not self._sel:
            return None
        kind, i = self._sel
        lst = self._res(kind)
        return lst[i] if 0 <= i < len(lst) else None

    # ================= toolbar =================
    def _toolbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.get_style_context().add_class("idebar")
        run = Gtk.Button()
        run.set_relief(Gtk.ReliefStyle.NONE)
        run.get_style_context().add_class("runbtn")
        rh = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        rh.pack_start(Gtk.Image.new_from_pixbuf(nbicons.pixbuf("cartridge", 13, "#FCFBF8")),
                      False, False, 0)
        rh.pack_start(Gtk.Label(label=_t("Compile & Export")), False, False, 0)
        run.add(rh)
        run.set_tooltip_text("Compile the project to a .gba game file you can play "
                             "in the GBA Emulator, or on a real Game Boy Advance")
        run.connect("clicked", lambda *_: self._file_export())
        bar.pack_start(run, False, False, 0)

        for label, cb in (("New Sprite", lambda: self._add_resource("sprite")),
                          ("New Object", lambda: self._add_resource("object")),
                          ("New Room", lambda: self._add_resource("room"))):
            b = Gtk.Button(label=label)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("idebtn")
            b.connect("clicked", lambda _w, c=cb: c())
            bar.pack_start(b, False, False, 0)

        bar.pack_start(Gtk.Box(), True, True, 0)
        self._status = Gtk.Label(label="", xalign=1)
        self._status.get_style_context().add_class("idestatus")
        self._status.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        bar.pack_end(self._status, False, False, 0)
        return bar

    # ================= resource tree =================
    def _resource_tree(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.set_size_request(210, -1)
        col.get_style_context().add_class("restree")
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._tree_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll.add(self._tree_body)
        col.pack_start(scroll, True, True, 0)
        return col

    def _render_tree(self):
        for c in self._tree_body.get_children():
            self._tree_body.remove(c)
        for kind, icon, label in (("sprite", "media", "Sprites"),
                                  ("tileset", "mappin", "Tilesets"),
                                  ("sound", "music", "Sounds"),
                                  ("object", "gamepad", "Objects"),
                                  ("room", "desktop", "Rooms")):
            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            head.get_style_context().add_class("treehead")
            head.pack_start(Gtk.Image.new_from_pixbuf(
                nbicons.pixbuf(icon, 14, MUTED)), False, False, 0)
            hl = Gtk.Label(label=label, xalign=0)
            hl.get_style_context().add_class("treeheadlbl")
            head.pack_start(hl, True, True, 0)
            add = Gtk.Button(label="+")
            add.set_relief(Gtk.ReliefStyle.NONE)
            add.get_style_context().add_class("treeadd")
            add.connect("clicked", lambda _w, k=kind: self._add_resource(k))
            head.pack_end(add, False, False, 0)
            self._tree_body.pack_start(head, False, False, 0)
            for i, r in enumerate(self._res(kind)):
                row = Gtk.EventBox()
                row.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
                rb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                rb.get_style_context().add_class("treerow")
                if self._sel == (kind, i):
                    rb.get_style_context().add_class("treesel")
                lbl = Gtk.Label(label=r.get("id", "?"), xalign=0)
                lbl.get_style_context().add_class("treeitem")
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                rb.pack_start(lbl, True, True, 0)
                row.add(rb)
                row.connect("button-press-event",
                            lambda _w, e, k=kind, ix=i: self._on_tree_click(k, ix, e))
                self._tree_body.pack_start(row, False, False, 0)
        self._tree_body.show_all()

    def _on_tree_click(self, kind, index, event):
        self._select_resource(kind, index)
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            self._rename_resource()
        return True

    def _add_resource(self, kind):
        lst = self._res(kind)
        if kind == "sprite":
            rid = self._uid("spr", lst)
            lst.append({"id": rid, "w": 16, "h": 16, "ox": 8, "oy": 8,
                        "anim_speed": 0, "frames": [[TRANSPARENT] * 256]})
        elif kind == "sound":
            rid = self._uid("snd", lst)
            lst.append({"id": rid, "tempo": 8, "loop": True, "steps": 16,
                        "lead": [0] * 16, "bass": [0] * 16})
        elif kind == "object":
            rid = self._uid("obj", lst)
            lst.append({"id": rid, "sprite": None, "visible": True,
                        "solid": False, "events": []})
        elif kind == "tileset":
            rid = self._uid("ts", lst)
            lst.append({"id": rid, "tiles": [[TRANSPARENT] * 64]})
        else:
            rid = self._uid("rm", lst)
            lst.append({"id": rid, "w": 240, "h": 160, "speed": 60,
                        "bg": "#101820", "instances": []})
            if self.proj.get("start_room") is None:
                self.proj["start_room"] = rid
        self._save_autosave()
        self._render_tree()
        self._select_resource(kind, len(lst) - 1)

    def _rename_resource(self):
        r = self._sel_res()
        if not r:
            return
        self._prompt_text("Rename", r.get("id", ""), lambda v: self._do_rename(v))

    def _do_rename(self, newid):
        r = self._sel_res()
        if not r or not newid:
            return
        newid = gbabuild._cid(newid)
        old = r.get("id")
        # keep references (sprite/object/room by id) consistent
        r["id"] = newid
        for o in self.proj["objects"]:
            if o.get("sprite") == old:
                o["sprite"] = newid
        for rm in self.proj["rooms"]:
            for it in rm.get("instances", []):
                if it.get("object") == old:
                    it["object"] = newid
        if self.proj.get("start_room") == old:
            self.proj["start_room"] = newid
        # keep play_sound action references consistent

        def fix(actions):
            for a in actions or []:
                if a.get("kind") == "play_sound" and a.get("sound") == old:
                    a["sound"] = newid
                fix(a.get("children"))
        for o in self.proj["objects"]:
            for ev in o.get("events", []):
                fix(ev.get("actions"))
        self._save_autosave()
        self._render_tree()
        self._refresh_editor()

    def _delete_resource(self):
        if not self._sel:
            return
        kind, i = self._sel
        lst = self._res(kind)
        if 0 <= i < len(lst):
            del lst[i]
            self._sel = None
            self._save_autosave()
            self._render_tree()
            self._editor_stack.set_visible_child_name("welcome")

    def _select_resource(self, kind, index):
        self._sel = (kind, index)
        self._sel_event = None
        self._sel_action = None
        self._render_tree()
        self._refresh_editor()

    def _refresh_editor(self):
        r = self._sel_res()
        if not r or not self._sel:
            self._editor_stack.set_visible_child_name("welcome")
            return
        kind = self._sel[0]
        if kind == "sprite":
            self._load_sprite_editor()
        elif kind == "tileset":
            self._load_tileset_editor()
        elif kind == "sound":
            self._load_sound_editor()
        elif kind == "object":
            self._load_object_editor()
        else:
            self._load_room_editor()
        self._editor_stack.set_visible_child_name(kind)

    # ================= welcome pane =================
    def _welcome_pane(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        img = Gtk.Image.new_from_pixbuf(nbicons.pixbuf("cartridge", 56, FAINT))
        box.pack_start(img, False, False, 0)
        t = Gtk.Label(label=_t("Make a Game Boy Advance game"))
        t.get_style_context().add_class("welcometitle")
        box.pack_start(t, False, False, 0)
        s = Gtk.Label(
            label=_t("Start with a Sprite — the picture you'll see on screen. "
                     "Give it an Object so it can move and react to you, then "
                     "place it in a Room. When you're ready, Compile & Export "
                     "turns it into a game you can play in the GBA Emulator."))
        # wrap to a measure rather than baking "\n" into the string: hard line
        # breaks land in the wrong places the moment this is translated. halign
        # CENTER is what makes max_width_chars bind (a box child is FILL by
        # default and would stretch to the whole window).
        s.set_line_wrap(True)
        s.set_max_width_chars(56)
        s.set_halign(Gtk.Align.CENTER)
        s.set_justify(Gtk.Justification.CENTER)
        s.get_style_context().add_class("welcomesub")
        box.pack_start(s, False, False, 0)
        ex = Gtk.Button(label=_t("Open the example game"))
        # Secondary (paper) treatment, NOT the red .runbtn: the toolbar's
        # Compile & Export is the app's one red accent, and two red buttons on
        # screen at once (there, and here in the empty state) fought each other.
        ex.get_style_context().add_class("exbtn")
        ex.set_halign(Gtk.Align.CENTER)
        ex.connect("clicked", lambda *_: self._file_example())
        box.pack_start(ex, False, False, 6)
        return box

    # ================= sprite editor =================
    def _sprite_pane(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("editpane")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top.pack_start(Gtk.Label(label=_t("Size")), False, False, 0)
        self._spr_size = Gtk.ComboBoxText()
        for w, h in SPRITE_SIZES:
            self._spr_size.append("%dx%d" % (w, h), "%d×%d" % (w, h))
        self._spr_size.connect("changed", self._on_sprite_size)
        top.pack_start(self._spr_size, False, False, 0)
        top.pack_start(_group_label(_t("Tool")), False, False, 0)
        toolref = None
        for tid, lbl, tip in (("pen", "Pen", "Paint pixels (drag to draw)"),
                              ("fill", "Fill", "Flood-fill a region"),
                              ("pick", "Pick", "Pick a colour from the canvas")):
            b = Gtk.RadioButton.new_with_label_from_widget(toolref, lbl)
            toolref = toolref or b
            b.set_tooltip_text(tip)
            b.connect("toggled", lambda _w, t=tid: self._set_tool(t))
            top.pack_start(b, False, False, 0)
        top.pack_start(_group_label(_t("Anim")), False, False, 0)
        self._spr_anim = Gtk.SpinButton()
        self._spr_anim.set_adjustment(Gtk.Adjustment(
            lower=0, upper=64, step_increment=1, value=0))
        self._spr_anim.set_numeric(True)
        self._spr_anim.set_tooltip_text(
            _t("Animation speed: sub-frames advanced per game step (×16); 0 = off"))
        self._spr_anim.connect("value-changed", self._on_anim_speed)
        top.pack_start(self._spr_anim, False, False, 0)
        self._play_btn = Gtk.ToggleButton(label="▶")
        self._play_btn.set_tooltip_text(_t("Preview the animation"))
        self._play_btn.get_style_context().add_class("idebtn")
        self._play_btn.connect("toggled", self._on_play_toggle)
        top.pack_start(self._play_btn, False, False, 0)
        box.pack_start(top, False, False, 0)

        mid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        mid.pack_start(self._palette_flow(), False, False, 0)

        self._spr_canvas = Gtk.DrawingArea()
        self._spr_canvas.set_size_request(320, 320)
        self._spr_canvas.set_halign(Gtk.Align.START)
        self._spr_canvas.set_valign(Gtk.Align.START)
        self._spr_canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                                    | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self._spr_canvas.connect("draw", self._draw_sprite_canvas)
        self._spr_canvas.connect("button-press-event", self._on_sprite_paint)
        self._spr_canvas.connect("motion-notify-event", self._on_sprite_paint)
        mid.pack_start(self._spr_canvas, False, False, 0)

        fr = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        fr.set_size_request(118, -1)
        fhead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        fhead.pack_start(Gtk.Label(label=_t("Frames")), True, True, 0)
        # "duplicate" is a drawn icon, not a character: no shipped font has ⧉
        # (U+29C9) and it rendered as a box printing its own codepoint.
        for lbl, cb, tip in (("+", self._add_frame, "Add a blank frame"),
                             ("duplicate", self._dup_frame, "Duplicate this frame"),
                             ("×", self._del_frame, "Delete this frame")):
            if lbl == "duplicate":
                b = Gtk.Button()
                b.add(Gtk.Image.new_from_pixbuf(nbicons.pixbuf(lbl, 12, MUTED)))
            else:
                b = Gtk.Button(label=lbl)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("treeadd")
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _w, c=cb: c())
            fhead.pack_end(b, False, False, 0)
        fr.pack_start(fhead, False, False, 0)
        fsw = Gtk.ScrolledWindow()
        fsw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        fsw.set_vexpand(True)
        self._frame_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        fsw.add(self._frame_list)
        fr.pack_start(fsw, True, True, 0)
        mid.pack_start(fr, False, False, 0)
        box.pack_start(mid, True, True, 0)
        return box

    def _draw_swatch(self, w, cr, color):
        a = w.get_allocated_width(); b = w.get_allocated_height()
        if color == TRANSPARENT:
            cr.set_source_rgb(0.9, 0.9, 0.9); cr.paint()
            cr.set_source_rgb(0.7, 0.2, 0.2); cr.set_line_width(2)
            cr.move_to(3, 3); cr.line_to(a - 3, b - 3); cr.stroke()
        else:
            r, g, bl = self._c15(color)
            cr.set_source_rgb(r, g, bl); cr.paint()
        if color == self._paint_color:
            cr.set_source_rgb(0.78, 0.20, 0.12); cr.set_line_width(3)
            cr.rectangle(1.5, 1.5, a - 3, b - 3); cr.stroke()
        return False

    def _c15(self, color):
        return ((color & 31) / 31.0, ((color >> 5) & 31) / 31.0,
                ((color >> 10) & 31) / 31.0)

    def _set_paint(self, color):
        self._paint_color = color
        self.queue_draw()

    def _cur_sprite(self):
        r = self._sel_res()
        return r if (self._sel and self._sel[0] == "sprite") else None

    def _load_sprite_editor(self):
        s = self._cur_sprite()
        if not s:
            return
        self._stop_preview()
        self._sel_frame = 0
        self._suspend = True
        self._spr_size.set_active_id("%dx%d" % (s.get("w", 16), s.get("h", 16)))
        self._spr_anim.set_value(int(s.get("anim_speed", 0)))
        self._suspend = False
        self._render_frame_list()
        self._spr_canvas.queue_draw()

    def _on_sprite_size(self, combo):
        if self._suspend:
            return
        s = self._cur_sprite()
        aid = combo.get_active_id()
        if not s or not aid:
            return
        w, h = (int(v) for v in aid.split("x"))
        ow, oh = s.get("w", 16), s.get("h", 16)
        s["frames"] = [self._resize_frame(fr, ow, oh, w, h)
                       for fr in (s.get("frames") or [[TRANSPARENT] * (ow * oh)])]
        s["w"], s["h"] = w, h
        s["ox"], s["oy"] = w // 2, h // 2
        self._save_autosave()
        self._render_frame_list()
        self._spr_canvas.queue_draw()

    @staticmethod
    def _resize_frame(px, ow, oh, w, h):
        out = [TRANSPARENT] * (w * h)
        px = list(px or [])
        for y in range(min(oh, h)):
            for x in range(min(ow, w)):
                si = y * ow + x
                if si < len(px):
                    out[y * w + x] = px[si]
        return out

    def _set_tool(self, tool):
        self._spr_tool = tool

    def _on_anim_speed(self, spin):
        if self._suspend:
            return
        s = self._cur_sprite()
        if s:
            s["anim_speed"] = int(spin.get_value())
            self._save_autosave()

    # ---- animation frames ----
    def _cur_frame(self):
        s = self._cur_sprite()
        if not s:
            return None
        frames = s.setdefault("frames", [[TRANSPARENT] * (s.get("w", 16)
                                                          * s.get("h", 16))])
        if not frames:
            frames.append([TRANSPARENT] * (s.get("w", 16) * s.get("h", 16)))
        if self._sel_frame >= len(frames):
            self._sel_frame = len(frames) - 1
        return frames[self._sel_frame]

    def _add_frame(self):
        s = self._cur_sprite()
        if not s:
            return
        s["frames"].append([TRANSPARENT] * (s.get("w", 16) * s.get("h", 16)))
        self._sel_frame = len(s["frames"]) - 1
        self._save_autosave()
        self._render_frame_list()
        self._spr_canvas.queue_draw()

    def _dup_frame(self):
        s = self._cur_sprite()
        if not s or not s.get("frames"):
            return
        s["frames"].insert(self._sel_frame + 1, list(s["frames"][self._sel_frame]))
        self._sel_frame += 1
        self._save_autosave()
        self._render_frame_list()
        self._spr_canvas.queue_draw()

    def _del_frame(self):
        s = self._cur_sprite()
        if not s or len(s.get("frames", [])) <= 1:
            return
        del s["frames"][self._sel_frame]
        self._sel_frame = max(0, self._sel_frame - 1)
        self._save_autosave()
        self._render_frame_list()
        self._spr_canvas.queue_draw()

    def _select_frame(self, i):
        self._stop_preview()
        self._sel_frame = i
        self._render_frame_list()
        self._spr_canvas.queue_draw()

    def _render_frame_list(self):
        for c in self._frame_list.get_children():
            self._frame_list.remove(c)
        s = self._cur_sprite()
        if not s:
            return
        for i in range(len(s.get("frames", []))):
            da = Gtk.DrawingArea()
            da.set_size_request(96, 44)
            # keep the thumb at its own size: the draw handler paints its whole
            # allocation, so a stretched DrawingArea smears its backing colour
            # across the column instead of reading as a 96x44 frame chip.
            da.set_halign(Gtk.Align.CENTER)
            da.connect("draw", self._draw_frame_thumb, i)
            ev = Gtk.EventBox()
            ev.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            ev.add(da)
            ev.connect("button-press-event",
                       lambda _w, _e, ix=i: (self._select_frame(ix), True)[1])
            self._frame_list.pack_start(ev, False, False, 0)
        self._frame_list.show_all()

    def _draw_frame_thumb(self, w, cr, i):
        s = self._cur_sprite()
        cr.set_source_rgb(0.82, 0.80, 0.74)
        cr.paint()
        if not s or i >= len(s.get("frames", [])):
            return False
        sw, sh = s.get("w", 16), s.get("h", 16)
        px = s["frames"][i]
        cell = min(96.0 / sw, 40.0 / sh)
        ox = (96 - sw * cell) / 2
        for y in range(sh):
            for x in range(sw):
                c = px[y * sw + x] if y * sw + x < len(px) else TRANSPARENT
                if c == TRANSPARENT:
                    continue
                cr.set_source_rgb(*self._c15(c))
                cr.rectangle(ox + x * cell, 2 + y * cell, cell + 1, cell + 1)
                cr.fill()
        if i == self._sel_frame:
            cr.set_source_rgb(0.78, 0.20, 0.12)
            cr.set_line_width(2)
            cr.rectangle(1, 1, 94, 42)
            cr.stroke()
        return False

    # ---- animation preview ----
    def _on_play_toggle(self, btn):
        if btn.get_active():
            self._spr_preview = 0
            self._spr_play = GLib.timeout_add(140, self._preview_tick)
        else:
            self._stop_preview()

    def _stop_preview(self):
        if self._spr_play is not None:
            GLib.source_remove(self._spr_play)
            self._spr_play = None
        if hasattr(self, "_play_btn"):
            self._play_btn.set_active(False)

    def _preview_tick(self):
        s = self._cur_sprite()
        if not s or not s.get("frames"):
            self._spr_play = None
            return False
        self._spr_preview = (self._spr_preview + 1) % len(s["frames"])
        self._spr_canvas.queue_draw()
        return True

    def _spr_edit_px(self):
        """The pixels the canvas shows/edits: the preview frame while playing,
        else the selected frame. Returns (px, w, h) or (None, 0, 0)."""
        s = self._cur_sprite()
        if not s or not s.get("frames"):
            return None, 0, 0
        i = self._spr_preview if self._spr_play is not None else self._sel_frame
        if i >= len(s["frames"]):
            i = 0
        return s["frames"][i], s.get("w", 16), s.get("h", 16)

    @staticmethod
    def _spr_cell(widget, sw, sh):
        aw = widget.get_allocated_width(); ah = widget.get_allocated_height()
        return min(aw / max(1, sw), ah / max(1, sh))

    def _draw_sprite_canvas(self, w, cr):
        cr.set_source_rgb(0.86, 0.84, 0.78); cr.paint()
        px, sw, sh = self._spr_edit_px()
        if px is None:
            return False
        cell = self._spr_cell(w, sw, sh)
        for j in range(sh):
            for i in range(sw):
                c = px[j * sw + i] if j * sw + i < len(px) else TRANSPARENT
                if c == TRANSPARENT:
                    shade = 0.9 if (i + j) % 2 == 0 else 0.82
                    cr.set_source_rgb(shade, shade, shade)
                else:
                    cr.set_source_rgb(*self._c15(c))
                cr.rectangle(i * cell, j * cell, cell + 1, cell + 1); cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.12); cr.set_line_width(1)
        for k in range(sw + 1):
            cr.move_to(k * cell, 0); cr.line_to(k * cell, sh * cell)
        for k in range(sh + 1):
            cr.move_to(0, k * cell); cr.line_to(sw * cell, k * cell)
        cr.stroke()
        return False

    def _on_sprite_paint(self, w, ev):
        if self._spr_play is not None:      # don't edit while previewing
            return False
        s = self._cur_sprite()
        frame = self._cur_frame()
        if s is None or frame is None:
            return False
        sw, sh = s.get("w", 16), s.get("h", 16)
        cell = self._spr_cell(w, sw, sh)
        i = int(ev.x // cell); j = int(ev.y // cell)
        if not (0 <= i < sw and 0 <= j < sh):
            return True
        idx = j * sw + i
        if idx >= len(frame):
            return True
        thumb = ev.type != Gdk.EventType.MOTION_NOTIFY
        if self._spr_tool == "pick":
            self._set_paint(frame[idx])
        elif self._spr_tool == "fill":
            self._flood_fill(frame, sw, sh, i, j, self._paint_color)
            self._save_autosave(); w.queue_draw()
            if thumb:
                self._render_frame_list()
        elif frame[idx] != self._paint_color:
            frame[idx] = self._paint_color
            self._save_autosave(); w.queue_draw()
            if thumb:
                self._render_frame_list()
        return True

    @staticmethod
    def _flood_fill(px, w, h, i, j, new):
        target = px[j * w + i]
        if target == new:
            return
        stack = [(i, j)]
        while stack:
            x, y = stack.pop()
            if not (0 <= x < w and 0 <= y < h):
                continue
            k = y * w + x
            if px[k] != target:
                continue
            px[k] = new
            stack.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))

    # ================= tileset editor =================
    def _cur_tileset(self):
        r = self._sel_res()
        return r if (self._sel and self._sel[0] == "tileset") else None

    def _cur_tile(self):
        ts = self._cur_tileset()
        if not ts:
            return None
        tiles = ts.setdefault("tiles", [])
        if not tiles:
            tiles.append([TRANSPARENT] * 64)
        if self._sel_tile >= len(tiles):
            self._sel_tile = len(tiles) - 1
        return tiles[self._sel_tile]

    def _palette_flow(self):
        """The shared paint palette, used by both the sprite and tile editors.

        It has to SCROLL. PALETTE is 65 colours and a bare FlowBox reports a
        minimum width of one column, so GTK then asks for 65 rows — ~920px of
        height, taller than a 768px laptop panel, which pushed the whole IDE
        off the bottom of the screen. Pinning it to a fixed three-wide column
        that scrolls keeps the editors laying out at any window size."""
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_size_request(98, -1)
        # take exactly the height the swatches need when the window can spare it
        # (otherwise the palette sat in a tall empty white box), and scroll when
        # it cannot.
        sw.set_propagate_natural_height(True)
        sw.set_valign(Gtk.Align.START)
        pal = Gtk.FlowBox()
        pal.set_min_children_per_line(3)
        pal.set_max_children_per_line(3)
        pal.set_selection_mode(Gtk.SelectionMode.NONE)
        pal.set_valign(Gtk.Align.START)
        for name, color in PALETTE:
            da = Gtk.DrawingArea()
            da.set_size_request(26, 26)
            da.connect("draw", self._draw_swatch, color)
            ev = Gtk.EventBox()
            ev.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            ev.add(da)
            ev.set_tooltip_text(name)
            ev.connect("button-press-event",
                       lambda _w, _e, c=color: (self._set_paint(c), True)[1])
            pal.add(ev)
        sw.add(pal)
        return sw

    def _tileset_pane(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.get_style_context().add_class("editpane")
        hint = Gtk.Label(xalign=0, label="Paint 8×8 tiles, then paint them into "
                         "a room's Tiles mode to build a level.")
        hint.get_style_context().add_class("welcomesub")
        box.pack_start(hint, False, False, 0)
        mid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        mid.pack_start(self._palette_flow(), False, False, 0)
        self._tile_canvas = Gtk.DrawingArea()
        self._tile_canvas.set_size_request(288, 288)     # 8px * 36
        # as the sprite canvas does: keep it square. Left to FILL it stretches
        # down the pane and its backing colour reads as a big empty slab.
        self._tile_canvas.set_halign(Gtk.Align.START)
        self._tile_canvas.set_valign(Gtk.Align.START)
        self._tile_canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                                     | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self._tile_canvas.connect("draw", self._draw_tile_canvas)
        self._tile_canvas.connect("button-press-event", self._on_tile_paint)
        self._tile_canvas.connect("motion-notify-event", self._on_tile_paint)
        mid.pack_start(self._tile_canvas, False, False, 0)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.set_size_request(160, -1)
        addrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for lbl, cb in (("Add Tile", self._add_tile), ("Delete", self._del_tile)):
            b = Gtk.Button(label=lbl)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("idebtn")
            b.connect("clicked", lambda _w, c=cb: c())
            addrow.pack_start(b, False, False, 0)
        right.pack_start(addrow, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._tile_list = Gtk.FlowBox()
        self._tile_list.set_max_children_per_line(4)
        self._tile_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._tile_list.set_valign(Gtk.Align.START)
        scroll.add(self._tile_list)
        right.pack_start(scroll, True, True, 0)
        mid.pack_start(right, False, False, 0)
        box.pack_start(mid, True, True, 0)
        return box

    def _load_tileset_editor(self):
        self._sel_tile = 0
        self._render_tile_list()
        self._tile_canvas.queue_draw()

    def _render_tile_list(self):
        for c in self._tile_list.get_children():
            self._tile_list.remove(c)
        ts = self._cur_tileset()
        if not ts:
            return
        for i, _tile in enumerate(ts.get("tiles", [])):
            da = Gtk.DrawingArea()
            da.set_size_request(32, 32)
            # as in _render_frame_list: pin the size so the thumb's background
            # does not smear across the whole flow-box cell
            da.set_halign(Gtk.Align.CENTER)
            da.set_valign(Gtk.Align.CENTER)
            da.connect("draw", self._draw_tile_thumb, i)
            ev = Gtk.EventBox()
            ev.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            ev.add(da)
            ev.connect("button-press-event",
                       lambda _w, _e, ix=i: (self._select_tile(ix), True)[1])
            self._tile_list.add(ev)
        self._tile_list.show_all()

    def _select_tile(self, i):
        self._sel_tile = i
        self._render_tile_list()
        self._tile_canvas.queue_draw()

    def _add_tile(self):
        ts = self._cur_tileset()
        if not ts:
            return
        ts.setdefault("tiles", []).append([TRANSPARENT] * 64)
        self._sel_tile = len(ts["tiles"]) - 1
        self._save_autosave()
        self._render_tile_list()
        self._tile_canvas.queue_draw()

    def _del_tile(self):
        ts = self._cur_tileset()
        if not ts or len(ts.get("tiles", [])) <= 1:
            return
        del ts["tiles"][self._sel_tile]
        self._sel_tile = max(0, self._sel_tile - 1)
        self._save_autosave()
        self._render_tile_list()
        self._tile_canvas.queue_draw()

    def _draw_tile_grid(self, cr, tile, cell, checker):
        for j in range(8):
            for i in range(8):
                c = tile[j * 8 + i] if j * 8 + i < len(tile) else TRANSPARENT
                if c == TRANSPARENT:
                    if not checker:
                        continue
                    shade = 0.9 if (i + j) % 2 == 0 else 0.82
                    cr.set_source_rgb(shade, shade, shade)
                else:
                    cr.set_source_rgb(*self._c15(c))
                cr.rectangle(i * cell, j * cell, cell + 1, cell + 1)
                cr.fill()

    def _draw_tile_thumb(self, w, cr, i):
        ts = self._cur_tileset()
        cr.set_source_rgb(0.86, 0.84, 0.78)
        cr.paint()
        if not ts or i >= len(ts.get("tiles", [])):
            return False
        self._draw_tile_grid(cr, ts["tiles"][i], 32 / 8.0, False)
        if i == self._sel_tile:
            cr.set_source_rgb(0.78, 0.20, 0.12)
            cr.set_line_width(3)
            cr.rectangle(1.5, 1.5, 29, 29)
            cr.stroke()
        return False

    def _draw_tile_canvas(self, w, cr):
        tile = self._cur_tile()
        aw = w.get_allocated_width()
        ah = w.get_allocated_height()
        cr.set_source_rgb(0.86, 0.84, 0.78)
        cr.paint()
        if tile is None:
            return False
        cell = min(aw, ah) / 8.0
        self._draw_tile_grid(cr, tile, cell, True)
        cr.set_source_rgba(0, 0, 0, 0.12)
        cr.set_line_width(1)
        for k in range(9):
            cr.move_to(k * cell, 0)
            cr.line_to(k * cell, 8 * cell)
            cr.move_to(0, k * cell)
            cr.line_to(8 * cell, k * cell)
        cr.stroke()
        return False

    def _on_tile_paint(self, w, ev):
        tile = self._cur_tile()
        if tile is None:
            return False
        aw = w.get_allocated_width()
        ah = w.get_allocated_height()
        cell = min(aw, ah) / 8.0
        i = int(ev.x // cell)
        j = int(ev.y // cell)
        if 0 <= i < 8 and 0 <= j < 8:
            idx = j * 8 + i
            if tile[idx] != self._paint_color:
                tile[idx] = self._paint_color
                self._save_autosave()
                w.queue_draw()
                self._render_tile_list()
        return True

    def _all_tiles(self):
        """Every tileset's tiles concatenated, matching the compiler's combined
        BG tile set. A room tilemap value v (1-based) indexes this list at v-1."""
        out = []
        for ts in self.proj.get("tilesets", []):
            for t in ts.get("tiles", []):
                out.append(t)
        return out

    # ================= sound composer =================
    SND_CELLW = 22
    SND_CELLH = 12

    def _cur_sound(self):
        r = self._sel_res()
        return r if (self._sel and self._sel[0] == "sound") else None

    def _sound_pane(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("editpane")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        top.pack_start(Gtk.Label(label=_t("Tempo")), False, False, 0)
        self._snd_tempo = Gtk.SpinButton.new_with_range(1, 30, 1)
        self._snd_tempo.connect("value-changed", self._on_snd_tempo)
        top.pack_start(self._snd_tempo, False, False, 0)
        self._snd_loop = Gtk.CheckButton(label=_t("Loop"))
        self._snd_loop.connect("toggled", self._on_snd_loop)
        top.pack_start(self._snd_loop, False, False, 0)
        top.pack_start(Gtk.Label(label=_t("Steps")), False, False, 0)
        self._snd_steps = Gtk.ComboBoxText()
        for n in ("8", "16", "32"):
            self._snd_steps.append(n, n)
        self._snd_steps.connect("changed", self._on_snd_steps)
        top.pack_start(self._snd_steps, False, False, 0)
        # channel selector
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        seg.get_style_context().add_class("seg")
        for ch, lbl in (("lead", "Lead"), ("bass", "Bass")):
            b = Gtk.ToggleButton(label=lbl)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("segbtn")
            b.connect("toggled", self._on_snd_chan, ch)
            self._snd_btns[ch] = b
            seg.pack_start(b, False, False, 0)
        top.pack_end(seg, False, False, 0)
        box.pack_start(top, False, False, 0)
        hint = Gtk.Label(
            label=_t("Click the grid to place notes on the selected channel · "
                     "click a note again to clear it · Compile & Export to "
                     "hear it."),
            xalign=0)
        # MUST wrap. A non-wrapping label demands its full width as the pane's
        # minimum, and a Gtk.Stack is as wide as its widest page — so this hint
        # set the whole window's minimum even while the Sounds page was hidden.
        # English needs 889 of the 1024 budget, leaving 135px, so any language
        # whose sentence runs longer pushed the IDE off a 1024 panel (Yiddish
        # 1131, Greek 1159). Wrapping makes the length a translator's business
        # again instead of a layout failure.
        hint.set_line_wrap(True)
        hint.set_max_width_chars(64)
        hint.get_style_context().add_class("welcomesub")
        box.pack_start(hint, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._snd_canvas = Gtk.DrawingArea()
        self._snd_canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._snd_canvas.connect("draw", self._draw_sound)
        self._snd_canvas.connect("button-press-event", self._on_snd_click)
        scroll.add(self._snd_canvas)
        box.pack_start(scroll, True, True, 0)
        return box

    def _load_sound_editor(self):
        s = self._cur_sound()
        if not s:
            return
        self._suspend = True
        self._snd_tempo.set_value(s.get("tempo", 8))
        self._snd_loop.set_active(bool(s.get("loop", True)))
        self._snd_steps.set_active_id(str(s.get("steps", 16)))
        for ch, b in self._snd_btns.items():
            b.set_active(ch == self._snd_chan)
        self._suspend = False
        self._size_sound_canvas(s)
        self._snd_canvas.queue_draw()

    def _size_sound_canvas(self, s):
        rows = PITCH_HI - PITCH_LO + 1
        cols = s.get("steps", 16)
        self._snd_canvas.set_size_request(cols * self.SND_CELLW + 30,
                                          rows * self.SND_CELLH)

    def _on_snd_tempo(self, spin):
        if self._suspend:
            return
        s = self._cur_sound()
        if s:
            s["tempo"] = int(spin.get_value())
            self._save_autosave()

    def _on_snd_loop(self, chk):
        if self._suspend:
            return
        s = self._cur_sound()
        if s:
            s["loop"] = bool(chk.get_active())
            self._save_autosave()

    def _on_snd_steps(self, combo):
        if self._suspend:
            return
        s = self._cur_sound()
        if not s:
            return
        n = int(combo.get_active_id() or 16)
        for ch in ("lead", "bass"):
            seq = list(s.get(ch, []))
            seq = (seq + [0] * n)[:n]
            s[ch] = seq
        s["steps"] = n
        self._save_autosave()
        self._size_sound_canvas(s)
        self._snd_canvas.queue_draw()

    def _on_snd_chan(self, btn, ch):
        if self._suspend:
            return
        if btn.get_active():
            self._snd_chan = ch
            self._suspend = True
            for c, b in self._snd_btns.items():
                b.set_active(c == ch)
            self._suspend = False
            self._snd_canvas.queue_draw()

    def _draw_sound(self, w, cr):
        s = self._cur_sound()
        cw, ch = self.SND_CELLW, self.SND_CELLH
        gutter = 30
        rows = PITCH_HI - PITCH_LO + 1
        cr.set_source_rgb(0.98, 0.97, 0.95)
        cr.paint()
        if not s:
            return False
        cols = s.get("steps", 16)
        # piano rows (white/black keys) + labels
        for r in range(rows):
            pitch = PITCH_HI - r
            is_black = (pitch % 12) in (1, 3, 6, 8, 10)
            if is_black:
                cr.set_source_rgb(0.90, 0.88, 0.83)
            else:
                cr.set_source_rgb(0.98, 0.97, 0.95)
            cr.rectangle(gutter, r * ch, cols * cw, ch)
            cr.fill()
            if pitch % 12 == 0:      # C rows: label octave
                cr.set_source_rgb(0.6, 0.58, 0.52)
                cr.select_font_face("Nimbus Sans", 0, 0)
                cr.set_font_size(8)
                cr.move_to(3, r * ch + ch - 2)
                cr.show_text("C%d" % (pitch // 12 - 1))
        # grid
        cr.set_source_rgba(0, 0, 0, 0.10)
        cr.set_line_width(1)
        for c in range(cols + 1):
            lw = 0.28 if c % 4 == 0 else 0.10
            cr.set_source_rgba(0, 0, 0, lw)
            cr.move_to(gutter + c * cw, 0)
            cr.line_to(gutter + c * cw, rows * ch)
            cr.stroke()
        cr.set_source_rgba(0, 0, 0, 0.08)
        for r in range(rows + 1):
            cr.move_to(gutter, r * ch); cr.line_to(gutter + cols * cw, r * ch)
        cr.stroke()
        # notes: bass (green) then lead (blue); active channel brighter
        for ch_name, col in (("bass", (0.25, 0.55, 0.30)),
                             ("lead", (0.20, 0.35, 0.72))):
            seq = s.get(ch_name, [])
            dim = 0.45 if ch_name != self._snd_chan else 1.0
            for c in range(min(cols, len(seq))):
                note = seq[c]
                if not note or note < PITCH_LO or note > PITCH_HI:
                    continue
                r = PITCH_HI - note
                cr.set_source_rgba(col[0], col[1], col[2], dim)
                cr.rectangle(gutter + c * cw + 1, r * ch + 1, cw - 2, ch - 2)
                cr.fill()
        return False

    def _on_snd_click(self, w, ev):
        s = self._cur_sound()
        if not s:
            return False
        gutter = 30
        cw, chh = self.SND_CELLW, self.SND_CELLH
        cols = s.get("steps", 16)
        c = int((ev.x - gutter) // cw)
        r = int(ev.y // chh)
        if c < 0 or c >= cols:
            return True
        pitch = PITCH_HI - r
        if pitch < PITCH_LO or pitch > PITCH_HI:
            return True
        seq = list(s.get(self._snd_chan, []))
        seq = (seq + [0] * cols)[:cols]
        seq[c] = 0 if seq[c] == pitch else pitch
        s[self._snd_chan] = seq
        self._save_autosave()
        w.queue_draw()
        return True

    # ================= object editor =================
    def _object_pane(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("editpane")
        # sprite assignment
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.pack_start(Gtk.Label(label=_t("Sprite")), False, False, 0)
        self._obj_sprite = Gtk.ComboBoxText()
        self._obj_sprite.connect("changed", self._on_obj_sprite)
        top.pack_start(self._obj_sprite, False, False, 0)
        self._obj_visible = Gtk.CheckButton(label=_t("Visible"))
        self._obj_visible.connect("toggled", self._on_obj_visible)
        top.pack_start(self._obj_visible, False, False, 0)
        box.pack_start(top, False, False, 0)
        # three columns: events | actions | palette
        cols = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cols.set_vexpand(True)
        # events column
        ec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        ec.set_size_request(150, -1)
        el = Gtk.Label(label=_t("EVENTS"), xalign=0)
        el.get_style_context().add_class("colhead")
        ec.pack_start(el, False, False, 0)
        addev = Gtk.Button(label=_t("+ Add Event"))
        addev.set_relief(Gtk.ReliefStyle.NONE)
        addev.get_style_context().add_class("idebtn")
        addev.connect("clicked", lambda *_: self._add_event())
        ec.pack_start(addev, False, False, 0)
        esc = Gtk.ScrolledWindow(); esc.set_policy(Gtk.PolicyType.NEVER,
                                                   Gtk.PolicyType.AUTOMATIC)
        esc.set_vexpand(True)
        self._event_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        esc.add(self._event_list)
        ec.pack_start(esc, True, True, 0)
        cols.pack_start(ec, False, False, 0)
        # actions column
        ac = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        ac.set_hexpand(True)
        al = Gtk.Label(label=_t("ACTIONS"), xalign=0)
        al.get_style_context().add_class("colhead")
        ac.pack_start(al, False, False, 0)
        asc = Gtk.ScrolledWindow(); asc.set_policy(Gtk.PolicyType.NEVER,
                                                   Gtk.PolicyType.AUTOMATIC)
        asc.set_vexpand(True)
        self._action_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        asc.add(self._action_list)
        ac.pack_start(asc, True, True, 0)
        cols.pack_start(ac, True, True, 0)
        # palette column
        pc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pc.set_size_request(160, -1)
        pl = Gtk.Label(label=_t("ADD ACTION"), xalign=0)
        pl.get_style_context().add_class("colhead")
        pc.pack_start(pl, False, False, 0)
        psc = Gtk.ScrolledWindow(); psc.set_policy(Gtk.PolicyType.NEVER,
                                                   Gtk.PolicyType.AUTOMATIC)
        psc.set_vexpand(True)
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for kind, label, _params in ACTION_DEFS:
            b = Gtk.Button(label=label)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("palbtn")
            b.set_halign(Gtk.Align.FILL)
            b.set_tooltip_text(ACTION_TIPS.get(kind, label))
            b.connect("clicked", lambda _w, k=kind: self._add_action(k))
            pbox.pack_start(b, False, False, 0)
        psc.add(pbox)
        pc.pack_start(psc, True, True, 0)
        cols.pack_start(pc, False, False, 0)
        box.pack_start(cols, True, True, 0)
        return box

    def _cur_object(self):
        r = self._sel_res()
        return r if (self._sel and self._sel[0] == "object") else None

    def _load_object_editor(self):
        o = self._cur_object()
        if not o:
            return
        self._suspend = True
        self._obj_sprite.remove_all()
        self._obj_sprite.append("", "(none)")
        for s in self.proj["sprites"]:
            self._obj_sprite.append(s["id"], s["id"])
        self._obj_sprite.set_active_id(o.get("sprite") or "")
        self._obj_visible.set_active(o.get("visible", True) is not False)
        self._suspend = False
        if self._sel_event is None and o.get("events"):
            self._sel_event = 0
        self._render_events()
        self._render_actions()

    def _on_obj_sprite(self, combo):
        if self._suspend:
            return
        o = self._cur_object()
        if o is not None:
            o["sprite"] = combo.get_active_id() or None
            self._save_autosave()

    def _on_obj_visible(self, chk):
        if self._suspend:
            return
        o = self._cur_object()
        if o is not None:
            o["visible"] = bool(chk.get_active())
            self._save_autosave()

    def _add_event(self):
        o = self._cur_object()
        if not o:
            return
        items = [(k, lbl) for k, lbl in EVENT_KINDS]
        self._choose("Add Event", [lbl for _k, lbl in items],
                     lambda i: self._do_add_event(items[i][0]))

    def _do_add_event(self, kind):
        o = self._cur_object()
        if not o:
            return
        ev = {"actions": []}
        if kind == "collision":
            ev["type"] = "collision"
            ev["object"] = (self.proj["objects"][0]["id"]
                            if self.proj["objects"] else None)
        elif kind.startswith("key:"):
            ev["type"] = "key"; ev["key"] = kind.split(":", 1)[1]
        elif kind.startswith("keypress:"):
            ev["type"] = "keypress"; ev["key"] = kind.split(":", 1)[1]
        elif kind.startswith("keyrelease:"):
            ev["type"] = "keyrelease"; ev["key"] = kind.split(":", 1)[1]
        elif kind.startswith("alarm:"):
            ev["type"] = "alarm"; ev["alarm"] = int(kind.split(":", 1)[1])
        else:
            ev["type"] = kind
        o["events"].append(ev)
        self._sel_event = len(o["events"]) - 1
        self._sel_action = None
        self._save_autosave()
        self._render_events()
        self._render_actions()

    def _event_label(self, ev):
        t = ev.get("type")
        if t == "key":
            return "Key: %s" % ev.get("key", "?")
        if t == "keypress":
            return "Press: %s" % ev.get("key", "?")
        if t == "keyrelease":
            return "Release: %s" % ev.get("key", "?")
        if t == "alarm":
            return "Alarm %s" % ev.get("alarm", "?")
        if t == "collision":
            return "Collide: %s" % (ev.get("object") or "?")
        names = {"create": "Create", "step": "Step", "draw": "Draw",
                 "destroy": "Destroy"}
        return names.get(t, t.capitalize() if t else "?")

    def _render_events(self):
        for c in self._event_list.get_children():
            self._event_list.remove(c)
        o = self._cur_object()
        if not o:
            return
        for i, ev in enumerate(o.get("events", [])):
            row = Gtk.EventBox()
            row.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            rb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            rb.get_style_context().add_class("evrow")
            if i == self._sel_event:
                rb.get_style_context().add_class("evsel")
            lbl = Gtk.Label(label=self._event_label(ev), xalign=0)
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            rb.pack_start(lbl, True, True, 0)
            dl = Gtk.Button(label="×")
            dl.set_relief(Gtk.ReliefStyle.NONE)
            dl.get_style_context().add_class("evdel")
            dl.connect("clicked", lambda _w, ix=i: self._del_event(ix))
            rb.pack_end(dl, False, False, 0)
            row.add(rb)
            row.connect("button-press-event",
                        lambda _w, _e, ix=i: (self._select_event(ix), True)[1])
            self._event_list.pack_start(row, False, False, 0)
        self._event_list.show_all()

    def _select_event(self, i):
        self._sel_event = i
        self._sel_action = None
        self._render_events()
        self._render_actions()

    def _del_event(self, i):
        o = self._cur_object()
        if o and 0 <= i < len(o["events"]):
            del o["events"][i]
            self._sel_event = None
            self._save_autosave()
            self._render_events()
            self._render_actions()

    def _cur_event(self):
        o = self._cur_object()
        if o and self._sel_event is not None and \
                0 <= self._sel_event < len(o.get("events", [])):
            return o["events"][self._sel_event]
        return None

    def _add_action(self, kind):
        ev = self._cur_event()
        if ev is None:
            self._flash("Select an event first.")
            return
        act = {"kind": kind}
        for key, _lbl, spec in ACTION_PARAMS.get(kind, []):
            if isinstance(spec, list):
                act[key] = spec[0]
            elif spec == "int":
                act[key] = "0"
            else:
                act[key] = ""
        if kind in CONTAINER_ACTIONS:
            act["children"] = []
        ev["actions"].append(act)
        self._save_autosave()
        self._render_actions()

    def _render_actions(self):
        for c in self._action_list.get_children():
            self._action_list.remove(c)
        ev = self._cur_event()
        if ev is None:
            hint = Gtk.Label(label="Select or add an event, then click actions "
                                   "from the palette to build its behaviour.")
            hint.set_line_wrap(True); hint.set_xalign(0)
            hint.get_style_context().add_class("welcomesub")
            self._action_list.pack_start(hint, False, False, 0)
            self._action_list.show_all()
            return
        acts = ev.setdefault("actions", [])
        for i, act in enumerate(acts):
            self._action_list.pack_start(self._action_card(act, i, acts),
                                         False, False, 0)
        self._action_list.show_all()

    def _action_card(self, act, i, parent):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.get_style_context().add_class("actcard")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        t = Gtk.Label(label=ACTION_LABEL.get(act.get("kind"), act.get("kind")),
                      xalign=0)
        t.get_style_context().add_class("actname")
        head.pack_start(t, True, True, 0)
        # drawn arrows, not font "↑/↓" (the sans body font lacks them and would
        # render tofu boxes — same reason packages.py draws its sort arrows)
        up = Gtk.Button(); up.set_relief(Gtk.ReliefStyle.NONE)
        up.get_style_context().add_class("evdel")
        up.set_image(Gtk.Image.new_from_pixbuf(nbicons.pixbuf("up", 11, MUTED)))
        up.connect("clicked", lambda _w, ix=i, p=parent: self._move_action(p, ix, -1))
        dn = Gtk.Button(); dn.set_relief(Gtk.ReliefStyle.NONE)
        dn.get_style_context().add_class("evdel")
        dn.set_image(Gtk.Image.new_from_pixbuf(
            nbicons.pixbuf("up", 11, MUTED).flip(False)))
        dn.connect("clicked", lambda _w, ix=i, p=parent: self._move_action(p, ix, 1))
        dl = Gtk.Button(label="×"); dl.set_relief(Gtk.ReliefStyle.NONE)
        dl.get_style_context().add_class("evdel")
        dl.connect("clicked", lambda _w, ix=i, p=parent: self._del_action(p, ix))
        head.pack_end(dl, False, False, 0)
        head.pack_end(dn, False, False, 0)
        head.pack_end(up, False, False, 0)
        card.pack_start(head, False, False, 0)
        # params form
        for key, label, spec in ACTION_PARAMS.get(act.get("kind"), []):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            pl = Gtk.Label(label=label, xalign=0)
            pl.set_size_request(70, -1)
            pl.get_style_context().add_class("paramlbl")
            row.pack_start(pl, False, False, 0)
            row.pack_start(self._param_widget(act, key, spec), True, True, 0)
            card.pack_start(row, False, False, 0)
        # container: nested actions run when the condition/loop holds
        if act.get("kind") in CONTAINER_ACTIONS:
            kids = act.setdefault("children", [])
            kidbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            kidbox.set_margin_start(16)
            kidbox.get_style_context().add_class("actchildren")
            for ci, child in enumerate(kids):
                kidbox.pack_start(self._action_card(child, ci, kids),
                                  False, False, 0)
            addk = Gtk.Button(label=_t("+ action inside"))
            addk.set_relief(Gtk.ReliefStyle.NONE)
            addk.get_style_context().add_class("idebtn")
            addk.connect("clicked", lambda _w, k=kids: self._add_action_into(k))
            kidbox.pack_start(addk, False, False, 0)
            card.pack_start(kidbox, False, False, 0)
        return card

    def _add_action_into(self, lst):
        items = [(k, lbl) for k, lbl, _p in ACTION_DEFS]
        self._choose("Add Action", [lbl for _k, lbl in items],
                     lambda i: self._do_add_action_into(lst, items[i][0]))

    def _do_add_action_into(self, lst, kind):
        act = {"kind": kind}
        for key, _lbl, spec in ACTION_PARAMS.get(kind, []):
            act[key] = spec[0] if isinstance(spec, list) else (
                "0" if spec == "int" else "")
        if kind in CONTAINER_ACTIONS:
            act["children"] = []
        lst.append(act)
        self._save_autosave()
        self._render_actions()

    def _param_widget(self, act, key, spec):
        if spec == "code":
            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            sw.set_size_request(-1, 120)
            sw.set_hexpand(True)
            tv = Gtk.TextView()
            tv.get_style_context().add_class("gmlcode")
            tv.set_monospace(True)
            buf = tv.get_buffer()
            buf.set_text(str(act.get(key, "")))
            buf.connect("changed", lambda b, a=act, k=key: self._set_param(
                a, k, b.get_text(b.get_start_iter(), b.get_end_iter(), False)))
            sw.add(tv)
            return sw
        if isinstance(spec, list) or spec in ("obj", "room", "snd", "spr"):
            combo = Gtk.ComboBoxText()
            if spec == "obj":
                opts = [o["id"] for o in self.proj["objects"]]
            elif spec == "room":
                opts = [r["id"] for r in self.proj["rooms"]]
            elif spec == "snd":
                opts = [s["id"] for s in self.proj["sounds"]]
            elif spec == "spr":
                opts = [s["id"] for s in self.proj["sprites"]]
            else:
                opts = spec
            for opt in opts:
                combo.append(opt, opt)
            combo.set_active_id(str(act.get(key, opts[0] if opts else "")))
            combo.connect("changed",
                          lambda c, a=act, k=key: self._set_param(a, k, c.get_active_id()))
            return combo
        e = Gtk.Entry()
        e.get_style_context().add_class("paramentry")
        e.set_text(str(act.get(key, "")))
        if spec == "int":
            e.set_width_chars(8)
        e.connect("changed", lambda w, a=act, k=key: self._set_param(a, k, w.get_text()))
        return e

    def _set_param(self, act, key, value):
        act[key] = value if value is not None else ""
        self._save_autosave()

    def _move_action(self, lst, i, delta):
        j = i + delta
        if 0 <= i < len(lst) and 0 <= j < len(lst):
            lst[i], lst[j] = lst[j], lst[i]
            self._save_autosave()
            self._render_actions()

    def _del_action(self, lst, i):
        if 0 <= i < len(lst):
            del lst[i]
            self._save_autosave()
            self._render_actions()

    # ================= room editor =================
    def _room_pane(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("editpane")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._mode_obj_btn = Gtk.RadioButton.new_with_label_from_widget(None, "Objects")
        self._mode_tile_btn = Gtk.RadioButton.new_with_label_from_widget(
            self._mode_obj_btn, "Tiles")
        self._mode_obj_btn.connect("toggled", self._on_room_mode)
        top.pack_start(self._mode_obj_btn, False, False, 0)
        top.pack_start(self._mode_tile_btn, False, False, 0)
        top.pack_start(_group_label("· " + _t("Place")), False, False, 0)
        self._room_obj = Gtk.ComboBoxText()
        self._room_obj.connect("changed", self._on_room_obj)
        top.pack_start(self._room_obj, False, False, 0)
        self._room_start = Gtk.CheckButton(label=_t("Start room"))
        self._room_start.connect("toggled", self._on_room_start)
        top.pack_start(self._room_start, False, False, 0)
        clr = Gtk.Button(label=_t("Clear"))
        clr.set_relief(Gtk.ReliefStyle.NONE)
        clr.get_style_context().add_class("idebtn")
        clr.connect("clicked", lambda *_: self._room_clear())
        top.pack_end(clr, False, False, 0)
        box.pack_start(top, False, False, 0)

        # Room settings — size in pixels + game speed (steps/sec). A room larger
        # than the 240x160 screen is authored here; the yellow outline marks the
        # visible screenful (the runtime shows the top-left today and will scroll
        # once the mode-0 camera lands).
        srow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        srow.get_style_context().add_class("roomset")
        self._room_w = self._dim_spin(16, 1024, 16, "w")
        self._room_h = self._dim_spin(16, 1024, 16, "h")
        self._room_speed = self._dim_spin(1, 60, 1, "speed")
        for lbl, sp in (("Width", self._room_w), ("Height", self._room_h),
                        ("Speed", self._room_speed)):
            srow.pack_start(Gtk.Label(label=lbl), False, False, 0)
            srow.pack_start(sp, False, False, 0)
        box.pack_start(srow, False, False, 0)

        # tile palette (used in Tiles mode): an erase swatch + every tileset tile
        tp = Gtk.ScrolledWindow()
        tp.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        tp.set_min_content_height(42)
        self._room_tile_flow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                       spacing=4)
        tp.add(self._room_tile_flow)
        box.pack_start(tp, False, False, 0)

        hint = Gtk.Label(
            label=_t("Click to place the selected object · right-click to remove"),
            xalign=0)
        hint.get_style_context().add_class("welcomesub")
        box.pack_start(hint, False, False, 0)
        self._room_canvas = Gtk.DrawingArea()
        self._room_canvas.set_size_request(480, 320)     # resized to the room on load
        self._room_canvas.set_halign(Gtk.Align.START)
        self._room_canvas.set_valign(Gtk.Align.START)
        self._room_canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                                     | Gdk.EventMask.BUTTON1_MOTION_MASK
                                     | Gdk.EventMask.BUTTON3_MOTION_MASK)
        self._room_canvas.connect("draw", self._draw_room)
        self._room_canvas.connect("button-press-event", self._on_room_click)
        self._room_canvas.connect("motion-notify-event", self._on_room_motion)
        cscroll = Gtk.ScrolledWindow()
        cscroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        cscroll.set_vexpand(True)
        cscroll.add(self._room_canvas)
        box.pack_start(cscroll, True, True, 0)
        return box

    def _dim_spin(self, lo, hi, step, key):
        adj = Gtk.Adjustment(lower=lo, upper=hi, step_increment=step,
                             page_increment=step * 4, value=lo)
        sp = Gtk.SpinButton()
        sp.set_adjustment(adj)
        sp.set_numeric(True)
        sp.connect("value-changed", self._on_room_dim, key)
        return sp

    def _on_room_dim(self, spin, key):
        if self._suspend:
            return
        rm = self._cur_room()
        if not rm:
            return
        rm[key] = int(spin.get_value())
        self._save_autosave()
        self._resize_room_canvas()
        self._room_canvas.queue_draw()

    def _resize_room_canvas(self):
        rm = self._cur_room()
        if not rm:
            return
        scale = 2
        w = max(16, int(rm.get("w", 240)))
        h = max(16, int(rm.get("h", 160)))
        self._room_canvas.set_size_request(w * scale, h * scale)

    def _on_room_mode(self, _btn):
        self._room_mode = "tiles" if self._mode_tile_btn.get_active() else "objects"

    def _render_room_tile_palette(self):
        for c in self._room_tile_flow.get_children():
            self._room_tile_flow.remove(c)
        entries = [(0, None)] + list(enumerate(self._all_tiles(), start=1))
        for v, tile in entries:
            da = Gtk.DrawingArea()
            da.set_size_request(32, 32)
            da.connect("draw", self._draw_room_tile_swatch, v, tile)
            ev = Gtk.EventBox()
            ev.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            ev.add(da)
            ev.set_tooltip_text(_t("Erase") if v == 0 else "Tile %d" % v)
            ev.connect("button-press-event",
                       lambda _w, _e, iv=v: (self._pick_room_tile(iv), True)[1])
            self._room_tile_flow.pack_start(ev, False, False, 0)
        self._room_tile_flow.show_all()

    def _draw_room_tile_swatch(self, w, cr, v, tile):
        cr.set_source_rgb(0.86, 0.84, 0.78)
        cr.paint()
        if tile is not None:
            self._draw_tile_grid(cr, tile, 32 / 8.0, False)
        else:
            cr.set_source_rgb(0.7, 0.2, 0.2)
            cr.set_line_width(2)
            cr.move_to(5, 5)
            cr.line_to(27, 27)
            cr.stroke()
        if v == self._room_tile:
            cr.set_source_rgb(0.78, 0.20, 0.12)
            cr.set_line_width(3)
            cr.rectangle(1.5, 1.5, 29, 29)
            cr.stroke()
        return False

    def _pick_room_tile(self, v):
        self._room_tile = v
        self._render_room_tile_palette()

    def _room_tilemap(self, rm):
        """The room's BG tile layer, created/resized to (w/8)*(h/8) cells."""
        cw = max(16, int(rm.get("w", 240))) // 8
        ch = max(16, int(rm.get("h", 160))) // 8
        cells = cw * ch
        tm = rm.get("tiles")
        if not isinstance(tm, list) or len(tm) != cells:
            old = tm if isinstance(tm, list) else []
            rm["tiles"] = tm = (old + [0] * cells)[:cells]
        return tm, cw, ch

    def _tile_pixbuf(self, tile, scale):
        """A cached scaled pixbuf of an 8x8 tile (transparent where TRANSPARENT),
        so painting a big map blits cached images instead of thousands of rects."""
        key = (tuple(tile), scale)
        pb = self._tile_pb_cache.get(key)
        if pb is not None:
            return pb
        from gi.repository import GdkPixbuf, GLib
        n = 8 * scale
        buf = bytearray(n * n * 4)
        for pj in range(8):
            for pi in range(8):
                col = tile[pj * 8 + pi] if pj * 8 + pi < len(tile) else TRANSPARENT
                if col == TRANSPARENT:
                    continue
                r = (col & 31) << 3
                g = ((col >> 5) & 31) << 3
                b = ((col >> 10) & 31) << 3
                for sy in range(scale):
                    row = (pj * scale + sy) * n
                    for sx in range(scale):
                        o = (row + pi * scale + sx) * 4
                        buf[o] = r
                        buf[o + 1] = g
                        buf[o + 2] = b
                        buf[o + 3] = 255
        pb = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(bytes(buf)), GdkPixbuf.Colorspace.RGB, True, 8,
            n, n, n * 4)
        self._tile_pb_cache[key] = pb
        return pb

    def _cur_room(self):
        r = self._sel_res()
        return r if (self._sel and self._sel[0] == "room") else None

    def _load_room_editor(self):
        rm = self._cur_room()
        if not rm:
            return
        self._suspend = True
        self._room_obj.remove_all()
        for o in self.proj["objects"]:
            self._room_obj.append(o["id"], o["id"])
        if self.proj["objects"]:
            self._room_obj.set_active(0)
            self._room_place = self.proj["objects"][0]["id"]
        self._room_start.set_active(self.proj.get("start_room") == rm.get("id"))
        self._room_w.set_value(int(rm.get("w", 240)))
        self._room_h.set_value(int(rm.get("h", 160)))
        self._room_speed.set_value(int(rm.get("speed", 60)))
        self._mode_obj_btn.set_active(self._room_mode != "tiles")
        self._mode_tile_btn.set_active(self._room_mode == "tiles")
        self._suspend = False
        self._render_room_tile_palette()
        self._resize_room_canvas()
        self._room_canvas.queue_draw()

    def _on_room_obj(self, combo):
        if not self._suspend:
            self._room_place = combo.get_active_id()

    def _on_room_start(self, chk):
        if self._suspend:
            return
        rm = self._cur_room()
        if rm and chk.get_active():
            self.proj["start_room"] = rm.get("id")
            self._save_autosave()

    def _room_clear(self):
        rm = self._cur_room()
        if rm:
            rm["instances"] = []
            self._save_autosave()
            self._room_canvas.queue_draw()

    def _sprite_by_id(self, sid):
        for s in self.proj["sprites"]:
            if s["id"] == sid:
                return s
        return None

    def _draw_room(self, w, cr):
        rm = self._cur_room()
        cr.set_source_rgb(0.1, 0.1, 0.13); cr.paint()
        if not rm:
            return False
        scale = 2
        rw = max(16, int(rm.get("w", 240)))
        rh = max(16, int(rm.get("h", 160)))
        bg = gbabuild._rgb15(rm.get("bg"), 0)
        cr.set_source_rgb(*self._c15(bg)); cr.rectangle(0, 0, rw * scale, rh * scale)
        cr.fill()
        # BG tile layer
        tm = rm.get("tiles")
        if isinstance(tm, list) and tm:
            all_tiles = self._all_tiles()
            cw = rw // 8
            for ci, v in enumerate(tm):
                if not v or v > len(all_tiles):
                    continue
                pb = self._tile_pixbuf(all_tiles[v - 1], scale)
                Gdk.cairo_set_source_pixbuf(cr, pb, (ci % cw) * 8 * scale,
                                            (ci // cw) * 8 * scale)
                cr.paint()
        for it in rm.get("instances", []):
            o = next((ob for ob in self.proj["objects"]
                      if ob["id"] == it.get("object")), None)
            spr = self._sprite_by_id(o.get("sprite")) if o else None
            x = it.get("x", 0) * scale; y = it.get("y", 0) * scale
            if spr:
                self._blit_sprite_preview(cr, spr, it.get("x", 0),
                                          it.get("y", 0), scale)
            else:
                cr.set_source_rgb(0.78, 0.2, 0.12)
                cr.rectangle(x - 8, y - 8, 16, 16); cr.fill()
        # grid
        cr.set_source_rgba(1, 1, 1, 0.08); cr.set_line_width(1)
        for gx in range(0, rw + 1, 16):
            cr.move_to(gx * scale, 0); cr.line_to(gx * scale, rh * scale)
        for gy in range(0, rh + 1, 16):
            cr.move_to(0, gy * scale); cr.line_to(rw * scale, gy * scale)
        cr.stroke()
        # visible-screen (240x160) viewport outline
        cr.set_source_rgba(1, 0.85, 0.2, 0.55); cr.set_line_width(2)
        cr.rectangle(1, 1, min(240, rw) * scale - 2, min(160, rh) * scale - 2)
        cr.stroke()
        return False

    def _blit_sprite_preview(self, cr, spr, cx, cy, scale):
        n = spr.get("w", 16)
        px = spr.get("frames", [[]])[0] if spr.get("frames") else []
        ox = spr.get("ox", n // 2); oy = spr.get("oy", n // 2)
        for j in range(n):
            for i in range(n):
                idx = j * n + i
                if idx >= len(px):
                    continue
                c = px[idx]
                if c == TRANSPARENT:
                    continue
                cr.set_source_rgb(*self._c15(c))
                cr.rectangle((cx - ox + i) * scale, (cy - oy + j) * scale,
                             scale, scale)
                cr.fill()

    def _on_room_click(self, w, ev):
        rm = self._cur_room()
        if not rm:
            return False
        scale = 2
        rw = max(16, int(rm.get("w", 240)))
        rh = max(16, int(rm.get("h", 160)))
        if self._room_mode == "tiles":
            if self._paint_room_tile(rm, ev.x, ev.y, scale, erase=(ev.button == 3)):
                w.queue_draw()
            return True
        gx = int(ev.x // scale); gy = int(ev.y // scale)
        # snap to 8px grid, clamped inside the room
        gx = max(0, min(rw, (gx // 8) * 8 + 8))
        gy = max(0, min(rh, (gy // 8) * 8 + 8))
        if ev.button == 3:   # remove nearest instance
            best = None; bd = 1e9
            for k, it in enumerate(rm["instances"]):
                d = (it["x"] - gx) ** 2 + (it["y"] - gy) ** 2
                if d < bd:
                    bd = d; best = k
            if best is not None and bd < 256:
                del rm["instances"][best]
        elif self._room_place:
            rm["instances"].append({"object": self._room_place, "x": gx, "y": gy})
        self._save_autosave()
        w.queue_draw()
        return True

    def _paint_room_tile(self, rm, ex, ey, scale, erase=False):
        tm, cw, ch = self._room_tilemap(rm)
        cx = int(ex // scale) // 8
        cy = int(ey // scale) // 8
        if 0 <= cx < cw and 0 <= cy < ch:
            v = 0 if erase else self._room_tile
            if tm[cy * cw + cx] != v:
                tm[cy * cw + cx] = v
                self._save_autosave()
                return True
        return False

    def _on_room_motion(self, w, ev):
        rm = self._cur_room()
        if self._room_mode != "tiles" or not rm:
            return False
        erase = bool(ev.state & Gdk.ModifierType.BUTTON3_MASK)
        if self._paint_room_tile(rm, ev.x, ev.y, 2, erase=erase):
            w.queue_draw()
        return True

    # ================= build & run =================
    def _show_log(self, log):
        dlg = Gtk.Dialog(title="Build Log", transient_for=self, modal=True)
        dlg.set_default_size(640, 420)
        tv = Gtk.TextView(); tv.set_editable(False); tv.set_monospace(True)
        tv.get_buffer().set_text(log or "")
        sc = Gtk.ScrolledWindow(); sc.add(tv); sc.set_vexpand(True)
        dlg.get_content_area().pack_start(sc, True, True, 0)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        dlg.show_all()
        dlg.run()
        dlg.destroy()

    def _card(self, icon, title, lines, primary=_t("Done"), secondary=None,
              bullets=None):
        """A paper result card for the end of a build.

        Exporting a game is the moment the whole app exists for, and a failed
        one is the moment a beginner most needs telling what to do — both used
        to be a 12px grey line in the corner of the toolbar that nobody reads.
        `lines` are sentences, `bullets` a numbered list of next steps, and
        `secondary` an optional (label, callback) second button. Returns True
        when the primary button was pressed."""
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.get_style_context().add_class("resultcard")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        head.pack_start(Gtk.Image.new_from_pixbuf(
            nbicons.pixbuf(icon, 26, MUTED)), False, False, 0)
        ht = Gtk.Label(label=title, xalign=0)
        ht.get_style_context().add_class("resulttitle")
        head.pack_start(ht, True, True, 0)
        box.pack_start(head, False, False, 0)
        for text in lines:
            lbl = Gtk.Label(label=text, xalign=0)
            lbl.set_line_wrap(True)
            lbl.set_max_width_chars(52)
            lbl.get_style_context().add_class("resultbody")
            box.pack_start(lbl, False, False, 0)
        for i, text in enumerate(bullets or []):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            num = Gtk.Label(label="%d" % (i + 1), xalign=0.5)
            num.get_style_context().add_class("resultnum")
            # sit the number on the FIRST line of a wrapped step, not centred
            # down the side of it
            num.set_valign(Gtk.Align.START)
            row.pack_start(num, False, False, 0)
            lbl = Gtk.Label(label=text, xalign=0)
            lbl.set_line_wrap(True)
            lbl.set_max_width_chars(48)
            lbl.get_style_context().add_class("resultbody")
            row.pack_start(lbl, True, True, 0)
            box.pack_start(row, False, False, 0)
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_halign(Gtk.Align.END)
        btns.set_margin_top(6)
        if secondary:
            sb = Gtk.Button(label=secondary[0])
            sb.set_relief(Gtk.ReliefStyle.NONE)
            sb.get_style_context().add_class("idebtn")
            sb.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.NO))
            btns.pack_start(sb, False, False, 0)
        ok = Gtk.Button(label=primary)
        ok.set_relief(Gtk.ReliefStyle.NONE)
        ok.get_style_context().add_class("runbtn")
        ok.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.OK))
        btns.pack_start(ok, False, False, 0)
        box.pack_start(btns, False, False, 0)
        dlg.get_content_area().add(box)
        # The card carries its own buttons, so the dialog's stock action area
        # would only add an empty grey strip under the paper. Hide it.
        try:
            area = dlg.get_action_area()
            area.set_no_show_all(True)
            area.hide()
        except Exception:
            pass
        # Esc must dismiss it: the card has no title bar to close.
        dlg.connect("key-press-event",
                    lambda _w, e: (dlg.response(Gtk.ResponseType.CANCEL) or True)
                    if e.keyval == Gdk.KEY_Escape else False)
        dlg.show_all()
        ok.grab_focus()
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.NO and secondary:
            secondary[1]()
        return resp == Gtk.ResponseType.OK

    @staticmethod
    def _failure_reason(log):
        """One plain sentence for why a build stopped, from its log."""
        low = (log or "").lower()
        if "isn't installed" in low or "no such file or directory: 'arm" in low:
            return _t("The game compiler is missing from this computer, so "
                      "nothing can be built.")
        if "no space left" in low:
            return _t("The disk is full, so the game file could not be written.")
        if "could not write generated source" in low:
            return _t("The working files for the build could not be written.")
        return _t("The compiler stopped part-way through. This is a fault in "
                  "the app rather than in your game.")

    def _flash(self, text):
        try:
            self._status.set_text(text)
        except Exception:
            pass

    # ================= small dialogs =================
    def _prompt_text(self, title, initial, cb):
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        e = Gtk.Entry(); e.set_text(initial); e.set_activates_default(True)
        dlg.get_content_area().pack_start(e, False, False, 12)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button("OK", Gtk.ResponseType.OK)
        dlg.set_default(ok)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            cb(e.get_text().strip())
        dlg.destroy()

    def _choose(self, title, labels, cb):
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dlg.set_default_size(240, 320)
        lb = Gtk.ListBox()
        for lbl in labels:
            r = Gtk.ListBoxRow()
            l = Gtk.Label(label=lbl, xalign=0); l.set_margin_start(8)
            l.set_margin_top(6); l.set_margin_bottom(6)
            r.add(l); lb.add(r)
        sc = Gtk.ScrolledWindow(); sc.add(lb); sc.set_vexpand(True)
        dlg.get_content_area().pack_start(sc, True, True, 0)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        lb.connect("row-activated",
                   lambda _b, row: (cb(row.get_index()), dlg.response(Gtk.ResponseType.OK)))
        dlg.show_all()
        dlg.run()
        dlg.destroy()

    # ================= persistence =================
    def _save_autosave(self):
        try:
            nbapp.atomic_write_json(CFG_FILE, self.proj)
        except Exception:
            pass

    def _load_autosave(self):
        try:
            with open(CFG_FILE) as fh:
                data = json.load(fh)
            if isinstance(data, dict) and "objects" in data:
                self.proj = data
                for k in ("sprites", "sounds", "objects", "rooms"):
                    self.proj.setdefault(k, [])
        except Exception:
            pass

    def _on_destroy(self, *_):
        self._save_autosave()
        return False

    # ================= menus =================
    def menu_items(self, name):
        if name == "File":
            return [
                ("New Project", self._file_new),
                ("Open Example Game", self._file_example),
                ("Open Project…", self._file_open),
                nbapp.SEP,
                ("Save Project As…", self._file_save_as),
                ("Compile & Export…", self._file_export),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Resource":
            return [
                ("New Sprite", lambda: self._add_resource("sprite")),
                ("New Sound", lambda: self._add_resource("sound")),
                ("New Object", lambda: self._add_resource("object")),
                ("New Room", lambda: self._add_resource("room")),
                nbapp.SEP,
                ("Rename…", self._rename_resource),
                ("Delete", self._delete_resource),
            ]
        if name == "Build":
            return [
                ("Compile & Export…", self._file_export),
                ("Build Log…", lambda: self._show_log(getattr(self, "_last_log", "")
                                                      or "Nothing built yet.")),
            ]
        return super().menu_items(name)

    def _file_new(self):
        self._new_project()
        self._sel = None
        self._save_autosave()
        self._render_tree()
        self._editor_stack.set_visible_child_name("welcome")

    def _example_project(self):
        """A small, complete, buildable game — a player you drive with the D-pad
        around a tiled room collecting coins, with a live score read-out. Doubles
        as a template and a smoke test of the whole pipeline.

        It is built out of EVENTS AND ACTIONS, not code, on purpose: the example
        is the only thing most people will read before making their own game, so
        it has to show them the sheet they are going to work in. The movement
        used to be one Execute Code block, which taught nothing about the app —
        you opened the player and found a text editor. Every behaviour here is
        now a row someone can click, change and understand:

            obj_player  Step      Set H-Speed 0 / Set V-Speed 0
                                  Draw Text "Score:" / Draw Number score
                        Key <     Set H-Speed -2      (and one per direction)
            obj_coin    Collide   Add Score 10 / Destroy Self

        The Step event runs before the key events (see gbabuild.gen_objects), so
        zeroing the speed there and setting it in the key events is what makes
        the player stop when nothing is held."""
        def spr(fn, n=16):
            return [fn(i % n, i // n) for i in range(n * n)]

        def tile(fn):
            return [fn(i % 8, i // 8) for i in range(64)]

        player = spr(lambda x, y: (0x0000 if (x, y) in ((5, 6), (10, 6)) else
                                   (0x7FFF if y == 10 and 5 <= x <= 10 else
                                    (0x7C00 if 3 <= x < 13 and 3 <= y < 14
                                     else TRANSPARENT))))
        coin = spr(lambda x, y: (0x03FF if (x - 8) ** 2 + (y - 8) ** 2 < 28
                                 else TRANSPARENT))
        grass = tile(lambda x, y: 0x0180 if (x * 5 + y * 3) % 7 == 0 else 0x02E0)
        wall = tile(lambda x, y: 0x2F7B if (x % 4 == 0 or y % 4 == 0) else 0x0A9A)
        cw, ch = 30, 20
        tm = [2 if (tx in (0, cw - 1) or ty in (0, ch - 1)) else 1
              for ty in range(ch) for tx in range(cw)]
        # every step: stand still unless a direction is held, and draw the HUD
        step_actions = [
            {"kind": "set_hspeed", "value": "0"},
            {"kind": "set_vspeed", "value": "0"},
            {"kind": "draw_text", "text": "Score:", "x": "8", "y": "8"},
            {"kind": "draw_number", "value": "score", "x": "56", "y": "8"},
        ]
        # one event per direction on the D-pad
        move = [("left", "set_hspeed", "-2"), ("right", "set_hspeed", "2"),
                ("up", "set_vspeed", "-2"), ("down", "set_vspeed", "2")]
        key_events = [{"type": "key", "key": k,
                       "actions": [{"kind": act, "value": v}]}
                      for k, act, v in move]
        return {
            "name": "Example",
            "sprites": [
                {"id": "spr_player", "w": 16, "h": 16, "ox": 8, "oy": 8,
                 "frames": [player]},
                {"id": "spr_coin", "w": 16, "h": 16, "ox": 8, "oy": 8,
                 "frames": [coin]}],
            "sounds": [],
            "tilesets": [{"id": "ts_world", "tiles": [grass, wall]}],
            "objects": [
                {"id": "obj_player", "sprite": "spr_player", "visible": True,
                 "solid": False, "events":
                     [{"type": "step", "actions": step_actions}] + key_events},
                {"id": "obj_coin", "sprite": "spr_coin", "visible": True,
                 "solid": False, "events": [
                     {"type": "collision", "object": "obj_player", "actions": [
                         {"kind": "add_score", "value": "10"},
                         {"kind": "destroy_self"}]}]}],
            "rooms": [
                {"id": "rm_world", "w": 240, "h": 160, "speed": 60,
                 "bg": "#0C2818", "tiles": tm, "instances": [
                     {"object": "obj_player", "x": 120, "y": 90},
                     {"object": "obj_coin", "x": 48, "y": 48},
                     {"object": "obj_coin", "x": 190, "y": 56},
                     {"object": "obj_coin", "x": 80, "y": 128},
                     {"object": "obj_coin", "x": 176, "y": 120}]}],
            "start_room": "rm_world",
        }

    def _file_example(self):
        self.proj = self._example_project()
        self._path = None
        self._sel = None
        self._sel_event = None
        self._sel_action = None
        self._save_autosave()
        self._render_tree()
        self._select_resource("room", 0)
        self._flash("Loaded the example game — Compile & Export it to a .gba to play")

    def _file_open(self):
        try:
            os.makedirs(PROJ_DIR, exist_ok=True)
        except Exception:
            pass
        path = nbpicker.open_file(self, title="Open Project", start_dir=PROJ_DIR,
                                  patterns=("*.gbaproj", "*.json"))
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path) as fh:
                data = json.load(fh)
            if not (isinstance(data, dict) and "objects" in data):
                raise ValueError("not a GBA IDE project")
        except Exception:
            self._flash("Not a GBA IDE project.")
            return
        self.proj = data
        for k in ("sprites", "sounds", "objects", "rooms"):
            self.proj.setdefault(k, [])
        self._path = path
        self._sel = None
        self._save_autosave()
        self._render_tree()
        self._editor_stack.set_visible_child_name("welcome")

    def _file_save_as(self):
        try:
            os.makedirs(PROJ_DIR, exist_ok=True)
        except Exception:
            pass
        path = nbpicker.save_file(self, title="Save Project As", start_dir=PROJ_DIR,
                                  suggested_name=(self.proj.get("name") or "game")
                                  + ".gbaproj", patterns=("*.gbaproj",),
                                  default_ext=".gbaproj")
        if not path:
            return
        try:
            nbapp.atomic_write_json(path, self.proj, indent=2)
            self._path = path
            self._flash("Saved %s" % os.path.basename(path))
        except Exception:
            self._flash("Save failed.")

    def _file_export(self):
        """Compile the project to a .gba file the user chooses — no emulator.

        The ROM plays in the bundled GBA Emulator and also boots on real
        hardware: gbabuild writes the cartridge boot logo and header checksum
        the console's BIOS checks (see gbabuild.NINTENDO_LOGO)."""
        if not self.proj.get("objects") or not self.proj.get("rooms"):
            self._card("cartridge", _t("Nothing to compile yet"),
                       [_t("A game needs at least one object to put on screen "
                           "and one room to put it in.")],
                       bullets=[_t("Draw a Sprite — the picture you will see."),
                                _t("Make an Object and give it that sprite."),
                                _t("Make a Room and click to place the object "
                                   "in it.")])
            return
        # A line of code the compiler cannot read used to become a silent
        # nothing: the game built, that action did nothing, and the author was
        # never told. Say so BEFORE asking where to save it.
        problems = gbabuild.check_project(self.proj)
        if problems:
            go_on = [False]
            # two whole keys rather than an "%d thing%s" plural slot: _t()
            # returns English whenever a translation's placeholders differ from
            # the source's, and most languages do not form a plural with an -s
            self._card(
                "cartridge",
                _t("One thing to fix") if len(problems) == 1
                else _t("%d things to fix") % len(problems),
                [_t("These lines of code will be skipped, so the game will not "
                    "do what you meant:")] + problems[:6],
                primary=_t("Go Back and Fix"),
                secondary=(_t("Export Anyway"),
                           lambda: go_on.__setitem__(0, True)))
            if not go_on[0]:
                return
        try:
            os.makedirs(PROJ_DIR, exist_ok=True)
        except Exception:
            pass
        path = nbpicker.save_file(
            self, title="Compile & Export", start_dir=PROJ_DIR,
            suggested_name=(self.proj.get("name") or "game") + ".gba",
            patterns=("*.gba",), default_ext=".gba")
        if not path:
            return
        self._flash(_t("Compiling…"))
        outdir = os.path.join(tempfile.gettempdir(), "nbgba-export")
        ok, gba, log = gbabuild.build_rom(self.proj, outdir)
        self._last_log = log
        if not ok:
            self._flash(_t("Export failed"))
            self._card("cartridge", _t("The game could not be built"),
                       [self._failure_reason(log),
                        _t("Nothing was saved, and your project is untouched.")],
                       secondary=(_t("Show the Details"),
                                  lambda: self._show_log(log)))
            return
        try:
            shutil.copyfile(gba, path)
        except Exception as e:
            self._flash(_t("Export failed"))
            self._card("cartridge", _t("The game could not be saved"),
                       [_t("It compiled, but writing %s failed: %s")
                        % (os.path.basename(path), e)])
            return
        name = os.path.basename(path)
        self._flash(_t("Exported %s") % name)
        # The whole point of the app just happened — say where the game is and
        # what to do with it, including on real hardware (gbabuild writes the
        # cartridge boot logo, so this ROM really does start on a console).
        self._card(
            "cartridge", _t("Your game is ready"),
            [_t("%s — %.1f kB, saved in your Documents folder.")
             % (name, os.path.getsize(path) / 1024.0)],
            bullets=[
                _t("To play it now, close the IDE and open the GBA Emulator "
                   "app, then choose this file."),
                _t("To play it on a real Game Boy Advance, copy it onto a USB "
                   "stick with the Finder, then onto a flashcart."),
            ])

    # ================= css =================
    def _install_css(self):
        css = b"""
        * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .idebar { background: #F1EEE6; border-bottom: 1px solid #C9C4B6;
                  padding: 8px 14px; }
        .runbtn { background: #C8341E; color: #FCFBF8; border-radius: 3px;
                  padding: 6px 14px; font-weight: 600; box-shadow: none;
                  border: none; }
        .runbtn:hover { background: #B12D19; }
        /* secondary empty-state CTA - inviting but paper, so the red Compile &
           Export up in the toolbar stays the single accent on screen. */
        .exbtn { background: #FCFBF8; color: #1A1916; border: 1px solid #C4BFB1;
                 border-radius: 3px; padding: 8px 18px; font-weight: 600;
                 box-shadow: none; }
        .exbtn:hover { background: #F1EEE6; }
        .idebtn { border: 1px solid #C9C4B6; background: #FCFBF8; color: #1A1916;
                  border-radius: 2px; padding: 5px 10px; font-size: 12.5px;
                  box-shadow: none; }
        .idebtn:hover { background: #F4F2EC; }
        .idestatus { font-size: 12px; color: #6E695E; }
        .restree { background: #F1EEE6; border-right: 1px solid #C9C4B6; }
        .treehead { padding: 10px 12px 4px; }
        .treeheadlbl { font-size: 10.5px; letter-spacing: 0.12em; color: #6E695E;
                       font-weight: 700; }
        .treeadd { padding: 0 8px; color: #6E695E; font-size: 16px;
                   box-shadow: none; }
        .treerow { padding: 5px 12px 5px 24px; }
        .treerow.treesel { background: #FBEFEC; box-shadow: inset 3px 0 0 #C8341E; }
        .treeitem { font-size: 13px; color: #1A1916; }
        .editpane { padding: 16px 18px; background: #FCFBF8; }
        .welcometitle { font-size: 17px; font-weight: 600; color: #6E695E; }
        .welcomesub { font-size: 13px; color: #9A9484; }
        .colhead { font-size: 10.5px; letter-spacing: 0.12em; color: #9A9484;
                   font-weight: 700; margin-bottom: 4px; }
        .evrow { padding: 6px 8px; border-radius: 2px; }
        .evrow.evsel { background: #FBEFEC; }
        .evdel { padding: 0 6px; color: #9A9484; box-shadow: none; font-size: 13px; }
        .actcard { background: #F4F2EC; border: 1px solid #D7D2C5;
                   border-radius: 3px; padding: 8px 10px; }
        .actname { font-size: 13px; font-weight: 600; color: #1A1916; }
        .paramlbl { font-size: 11.5px; color: #6E695E; }
        .paramentry { font-size: 12.5px; padding: 2px 6px; border: 1px solid #CFC9BA;
                      border-radius: 2px; background: #FCFBF8; box-shadow: none; }
        .palbtn { border: 1px solid #D7D2C5; background: #FCFBF8; color: #1A1916;
                  border-radius: 2px; padding: 5px 8px; font-size: 12px;
                  box-shadow: none; }
        .palbtn:hover { background: #FBEFEC; border-color: #C8341E; }
        .colhead { margin-top: 2px; }
        /* build result card (success / problems / failure) */
        .resultcard { background: #FCFBF8; border: 1px solid #C9C4B6;
                      padding: 22px 26px 18px; }
        .resulttitle { font-size: 19px; font-weight: 700; color: #1A1916; }
        .resultbody { font-size: 13px; color: #6E695E; }
        .resultnum { font-size: 11px; font-weight: 700; color: #6E695E;
                     background: #F1EEE6; border: 1px solid #D7D2C5;
                     border-radius: 9px; min-width: 18px; min-height: 18px; }
        """
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            pass


if __name__ == "__main__":
    nbapp.run(GbaIde)
