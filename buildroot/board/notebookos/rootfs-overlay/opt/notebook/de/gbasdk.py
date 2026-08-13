#!/usr/bin/env python3
"""
GBA SDK — a game maker for people who have never written a line of code, whose
output is a real Game Boy Advance cartridge image. The things you make (Sprites,
Tile sets, Sounds, Objects, Rooms) sit in an asset browser down the left, each
one showing what it actually IS; the centre is one editor per kind of thing: a
pixel canvas for a sprite, a piano roll for a sound, an events-and-actions sheet
for an object, and a placement grid for a room. Build & Export builds the whole
project to a real .gba with the bundled arm-none-eabi toolchain (see
de/gbabuild.py + /opt/notebook/gbaruntime) and saves it for the user to play in
the GBA Emulator app, or on a real console or flashcart (Notebook OS runs one
app at a time, so there is no emulator launch from in here).

The project persists to $NB_HOME/.config/notebook/gbasdk.json (session recovery)
and to named .gbaproj files under Documents.

DESIGN NOTES that the rest of this file is built to keep:

  * Every editor is the SAME SHAPE — a head (what you are looking at, what it
    is, and the actions that apply to it), a hairline, then one tool row, then
    the work surface. They were written at different times and each had invented
    its own layout; a person who learns one pane now knows all five.
  * The work surface is the hero. Canvases expand to the pane and centre
    themselves; the chrome around them is one row deep.
  * Nothing is reachable only with a mouse. Every canvas takes focus and has a
    keyboard cursor (arrows move, Space/Return acts), every list reorders from
    the keyboard, and every shortcut is printed in a menu — see _KEYS.
  * Colour is never the only signal: the selected tool, the current paint
    colour, the active sound channel and the start room all say so in words as
    well.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, Pango, PangoCairo, GLib  # noqa: E402

import copy
import unicodedata
import os
import re
import sys
import json
import shutil
import struct
import subprocess
import tempfile

import nbapp
import nbpicker
import nbicons
import gbabuild
import gbaworkspace
import gbahelp
from nbi18n import _t  # noqa: E402

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
CFG_FILE = os.path.join(CFG_DIR, "gbasdk.json")
# The app was called "GBA IDE" until 2026-07-29 and autosaved to gbaide.json.
# Renaming the store would have silently orphaned an existing project -- the
# app would open EMPTY on a machine that had one, which is exactly the kind of
# quiet loss this OS keeps being bitten by. Read the old name when the new one
# is absent; the next autosave writes gbasdk.json, and the old file is left
# alone rather than deleted.
LEGACY_CFG_FILE = os.path.join(CFG_DIR, "gbaide.json")
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


def _acc(label, key):
    """`label` with the key that performs it, four spaces before the key, per
    docs/MENU-CONVENTIONS.md.

    Composed at runtime rather than written out as "Pen    P", so the WORD stays
    the catalog key it already is in all seventeen languages and the shortcut
    itself — which is not language — needs no translation."""
    return "%s    %s" % (_t(label), key)


def _layout(cr, text, size, bold=False):
    """A Pango layout for `text` at `size` px in the interface face.

    Canvas text goes through Pango, never cairo's toy font API: select_font_face
    + show_text does no per-character fallback, so the moment a translated
    string or a note name reaches outside the base face it draws nothing at all.
    (tools/toyfont_check.py enforces this.)"""
    layout = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription("Nimbus Sans")
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    fd.set_absolute_size(size * Pango.SCALE)
    layout.set_font_description(fd)
    layout.set_text(text, -1)
    return layout


def _show_text(cr, x, y, text, size, bold=False):
    """Draw `text` with its baseline at y."""
    layout = _layout(cr, text, size, bold)
    cr.move_to(x, y - layout.get_baseline() / Pango.SCALE)
    PangoCairo.show_layout(cr, layout)


def _eyebrow(text, margin_top=0):
    """A small letter-spaced caption over a group of things, as the rest of the
    OS writes them (Academics' sidebar, the Homework sections).

    Passed UPPER CASE on purpose: GTK cannot upper-case in CSS, and nbi18n's
    look-up falls back to the sentence-case key and upper-cases the translation,
    so "SPRITES" is translated in all seventeen languages for free."""
    lbl = Gtk.Label(label=text, xalign=0)
    lbl.get_style_context().add_class("eyebrow")
    lbl.set_margin_top(margin_top)
    return lbl


def _pad(s, width):
    """`s` padded to `width` COLUMNS of a monospace font, not characters.

    A CJK glyph occupies two columns, so "%-22s" lines up in Latin and Cyrillic
    and goes ragged in Japanese and Chinese — the padding counts characters and
    the terminal draws widths. Only matters where text shares a fixed grid with
    numbers, which in this app is the costing pane."""
    n = sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
            for c in s)
    return s + " " * max(0, width - n)


def _group_label(text):
    """A caption that starts a new group of controls on an editor's tool row,
    set off by a margin rather than by padding spaces inside the string (which
    every translation would then have to reproduce)."""
    lbl = Gtk.Label(label=text)
    lbl.get_style_context().add_class("toolcap")
    lbl.set_margin_start(12)
    return lbl


# A tile set is authored at 8, 16 or 32 pixels square. The GBA's background
# hardware only knows 8x8 tiles, so a 16 or 32 px tile is stored as ONE picture
# here and split into (size/8)^2 hardware tiles on the way out -- block
# row-major, which is the order gbabuild emits and the order a room tilemap
# expects. Everything downstream (room tilemaps, charblock indices) therefore
# still counts in hardware 8x8 cells, and an old 8x8 project is unchanged.
TILE_SIZES = (8, 16, 32)


def ts_size(ts):
    """A tile set's pixel size, coerced to one this app can actually draw."""
    try:
        n = int((ts or {}).get("size", 8))
    except (TypeError, ValueError):
        return 8
    return n if n in TILE_SIZES else 8


def split_tile(tile, size):
    """One authored tile -> its (size/8)^2 hardware 8x8 tiles, block row-major.

    Block order matters: index = base + by * (size/8) + bx, and the room paint
    and the compiler both rely on it. Missing pixels read as TRANSPARENT so a
    short or damaged tile degrades to holes rather than raising."""
    n = max(1, size // 8)
    px = list(tile or [])
    out = []
    for by in range(n):
        for bx in range(n):
            cell = []
            for j in range(8):
                row = (by * 8 + j) * size + bx * 8
                for i in range(8):
                    k = row + i
                    cell.append(px[k] if k < len(px) else TRANSPARENT)
            out.append(cell)
    return out


def _icon_button(icon, tip, cb, cls="quietbtn", size=13, color=None):
    """A button that is only a picture — so it always carries a tooltip.

    An unlabelled button with no tooltip is invisible to a screen reader and a
    guess to everyone else; the OS ran an audit that took 31 of them to 0 and
    this app is not going to put one back."""
    b = Gtk.Button()
    b.set_relief(Gtk.ReliefStyle.NONE)
    b.get_style_context().add_class(cls)
    b.add(nbicons.image(icon, size, color or MUTED))
    b.set_tooltip_text(tip)
    b.connect("clicked", lambda _w: cb())
    return b


def _focused(w):
    """True when `w` is where the keyboard is pointing.

    has_focus() alone is False whenever the WINDOW is not the active one (and in
    an offscreen render, always), which hid every focus ring and every keyboard
    cursor in this app the moment the user clicked another window. is_focus() is
    the question we actually mean: is this the focus widget of its toplevel."""
    try:
        return bool(w.has_focus() or w.is_focus())
    except Exception:
        return False


def _rule(top=0, bottom=0):
    """The hairline under a pane's head."""
    r = Gtk.Box()
    r.get_style_context().add_class("hairline")
    r.set_size_request(-1, 1)
    r.set_margin_top(top)
    r.set_margin_bottom(bottom)
    return r


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
    # r=31 b=31 is 0x7C1F, which IS the transparent sentinel: full magenta was
    # therefore unpaintable, and its swatch drew the erase cross. One step off
    # full blue is the same colour to the eye and is a colour again.
    ("Magenta", _col(31, 0, 30)), ("Pink", _col(31, 16, 24)),
    ("Rose", _col(31, 8, 15)), ("Brown", _col(16, 10, 4)),
    ("Tan", _col(26, 20, 12)), ("Skin", _col(31, 24, 18)),
    ("D.Skin", _col(22, 14, 10)),
]

# Valid GBA OBJ sizes (w, h): square, wide, and tall.
SPRITE_SIZES = [(8, 8), (16, 16), (32, 32), (64, 64),
                (16, 8), (32, 8), (32, 16), (64, 32),
                (8, 16), (8, 32), (16, 32), (32, 64)]

# The five kinds of thing a project is made of, in the order they are made:
# (kind, plural heading, the name of the action that makes one, the word for one
# of them). The browser, the New menu and every pane head read from here, so a
# sixth kind is one row rather than five edits that can disagree.
KINDS = (
    ("sprite", "SPRITES", "New Sprite", "Sprite"),
    ("tileset", "TILESETS", "New Tile Set", "Tile set"),
    ("sound", "SOUNDS", "New Sound", "Sound"),
    ("object", "OBJECTS", "New Object", "Object"),
    ("room", "ROOMS", "New Room", "Room"),
    # File-scope C. An Execute Code action emits its text INSIDE an event
    # function, so it cannot define a function, a lookup table or a constant --
    # which meant an interrupt handler, the thing the runtime's own API asks
    # for, could not be written from the tool at all. A script is where those
    # live: emitted once, before every object, visible to all of them.
    ("script", "SCRIPTS", "New Script", "Script"),
    # Rows of data with named columns, emitted as a C struct array. What a game
    # of any size is mostly MADE of -- species, moves, items, dialogue keys --
    # and the thing that otherwise ends up as a thousand-line script nobody can
    # edit without reading it all.
    ("table", "TABLES", "New Table", "Table"),
)


class _SoundUnsupported(ValueError):
    """A WAV this importer refuses on purpose, carrying a message meant for
    the author. Distinct from a decoder failure so the two can be told apart
    at the catch site: one is worth showing, the other is an errno and a
    filesystem path."""


class _MidiUnsupported(ValueError):
    """A MIDI refusal whose translated text is safe to show to the author."""


def _midi_vlq(data, pos):
    value = 0
    for _unused in range(4):
        if pos >= len(data):
            raise _MidiUnsupported(_t("This is not a readable MIDI file."))
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7f)
        if not byte & 0x80:
            return value, pos
    raise _MidiUnsupported(_t("This is not a readable MIDI file."))


def _midi_track(data):
    """Return standard note events without depending on Composer internals."""
    pos = tick = 0
    running = None
    active = {}
    notes = []
    name = None
    tempo = None
    channels = set()
    while pos < len(data):
        delta, pos = _midi_vlq(data, pos)
        tick += delta
        if pos >= len(data):
            raise _MidiUnsupported(_t("This is not a readable MIDI file."))
        status = data[pos]
        if status < 0x80:
            if running is None:
                raise _MidiUnsupported(_t("This is not a readable MIDI file."))
            status = running
        else:
            pos += 1
            running = status if status < 0xf0 else None
        if status == 0xff:
            if pos >= len(data):
                raise _MidiUnsupported(_t("This is not a readable MIDI file."))
            kind = data[pos]
            pos += 1
            size, pos = _midi_vlq(data, pos)
            payload = data[pos:pos + size]
            pos += size
            if len(payload) != size:
                raise _MidiUnsupported(_t("This is not a readable MIDI file."))
            if kind == 0x03:
                name = payload.decode("utf-8", "replace")
            elif kind == 0x51 and size == 3 and int.from_bytes(payload, "big"):
                tempo = int(round(60000000.0 / int.from_bytes(payload, "big")))
            elif kind == 0x2f:
                break
            continue
        if status in (0xf0, 0xf7):
            size, pos = _midi_vlq(data, pos)
            if pos + size > len(data):
                raise _MidiUnsupported(_t("This is not a readable MIDI file."))
            pos += size
            continue
        hi, channel = status & 0xf0, status & 0x0f
        need = 1 if hi in (0xc0, 0xd0) else 2
        values = data[pos:pos + need]
        pos += need
        if len(values) != need:
            raise _MidiUnsupported(_t("This is not a readable MIDI file."))
        channels.add(channel)
        if hi == 0x90 and values[1]:
            active.setdefault((channel, values[0]), []).append((tick, values[1]))
        elif hi in (0x80, 0x90):
            waiting = active.get((channel, values[0]), [])
            if waiting:
                start, velocity = waiting.pop(0)
                notes.append({"start": start, "duration": max(1, tick - start),
                              "pitch": values[0], "velocity": velocity,
                              "channel": channel})
    return {"name": name, "tempo": tempo, "notes": notes,
            "channels": channels}


def _midi_pitch(pitch):
    original = pitch
    while pitch < PITCH_LO:
        pitch += 12
    while pitch > PITCH_HI:
        pitch -= 12
    return pitch, pitch != original


def _midi_drum(pitch):
    if pitch in (35, 36):
        return 1                    # kick
    if pitch in (37, 38, 39, 40):
        return 2                    # snare / clap
    if 42 <= pitch <= 59:
        return 3                    # hats and cymbals
    return 4                        # toms and other percussion


def _midi_to_sound(raw, name="Song"):
    """Reduce an SMF 0/1 to the SDK's lead/bass/drum tracker resource.

    One row is a sixteenth note. The existing engine has two sequenced melodic
    lanes and one noise lane; allocation and collision order are deliberately
    stable so dropped notes can be reported and reproduced.
    """
    if raw[:4] != b"MThd" or len(raw) < 14:
        raise _MidiUnsupported(_t("This is not a readable MIDI file."))
    header_size = struct.unpack(">I", raw[4:8])[0]
    if header_size < 6 or len(raw) < 8 + header_size:
        raise _MidiUnsupported(_t("This is not a readable MIDI file."))
    fmt, count, division = struct.unpack(">HHH", raw[8:14])
    if fmt not in (0, 1) or not count or not division or division & 0x8000:
        raise _MidiUnsupported(
            _t("Use a Standard MIDI file in format 0 or 1 with musical timing."))
    pos = 8 + header_size
    parsed = []
    for _unused in range(count):
        if pos + 8 > len(raw) or raw[pos:pos + 4] != b"MTrk":
            raise _MidiUnsupported(_t("This is not a readable MIDI file."))
        size = struct.unpack(">I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + size]
        if len(body) != size:
            raise _MidiUnsupported(_t("This is not a readable MIDI file."))
        parsed.append(_midi_track(body))
        pos += 8 + size
    tempo = next((track["tempo"] for track in parsed if track["tempo"]), 120)
    row_ticks = division / 4.0
    sources = []
    for ti, track in enumerate(parsed):
        by_channel = sorted({n["channel"] for n in track["notes"]})
        for channel in by_channel:
            notes = [n for n in track["notes"] if n["channel"] == channel]
            if notes:
                label = track["name"] or _t("Track %d") % (ti + 1)
                if len(by_channel) > 1:
                    label += " ch %d" % (channel + 1)
                sources.append({"name": label, "channel": channel,
                                "notes": sorted(notes, key=lambda n:
                                                (n["start"], n["pitch"],
                                                 n["duration"]))})
    if not sources:
        raise _MidiUnsupported(_t("This MIDI file has no notes to import."))
    last_row = max(int(round((n["start"] + n["duration"]) / row_ticks))
                   for source in sources for n in source["notes"])
    steps = 8 if last_row <= 8 else 16 if last_row <= 16 else 32
    lanes = {"lead": [0] * steps, "bass": [0] * steps, "drum": [0] * steps}
    lane_starts = {"lead": set(), "bass": set(), "drum": set()}
    melodic = iter(("lead", "bass"))
    reports = []
    transposed = 0
    for source in sources:
        lane = "drum" if source["channel"] == 9 else next(melodic, None)
        kept = dropped = 0
        for note in source["notes"]:
            start = int(round(note["start"] / row_ticks))
            duration = max(1, int(round(note["duration"] / row_ticks)))
            if lane is None or start >= steps or start in lane_starts[lane]:
                dropped += 1
                continue
            if lane == "drum":
                value = _midi_drum(note["pitch"])
            else:
                value, moved = _midi_pitch(note["pitch"])
                transposed += int(moved)
            for row in range(start, min(steps, start + duration)):
                lanes[lane][row] = value
            lane_starts[lane].add(start)
            kept += 1
        reports.append({"name": source["name"], "kept": kept,
                        "dropped": dropped, "lane": lane})
    total_kept = sum(r["kept"] for r in reports)
    total_dropped = sum(r["dropped"] for r in reports)
    voices = sum(bool(any(lanes[lane])) for lane in ("lead", "bass", "drum"))
    sound = {"id": "", "name": name, "tempo": max(1, min(30,
             int(round(900.0 / tempo)))), "loop": True, "steps": steps,
             "lead": lanes["lead"], "bass": lanes["bass"],
             "drum": lanes["drum"], "kind": 0, "duty": 0, "vol": 0,
             "decay": 0, "prio": 0}
    detail = "; ".join(_t("%s: %d kept/%d dropped") %
                       (r["name"], r["kept"], r["dropped"]) for r in reports)
    summary = (_t("%d voices used; %d notes kept, %d dropped; %d transposed; %s.")
               % (voices, total_kept, total_dropped, transposed, detail))
    return sound, {"voices": voices, "kept": total_kept,
                   "dropped": total_dropped, "transposed": transposed,
                   "tracks": reports, "summary": summary}


def _find_empty_label(has_resources, query):
    """Truthful empty copy for Find in Project.

    An empty project has not failed to match a query; it has nothing available
    to search yet.  ``query`` is accepted to make that policy explicit at the
    call site and to keep the helper useful if the no-match copy later includes
    the search text.
    """
    if not has_resources:
        return "Project has no resources yet."
    return "No results"

# What a column can hold. Deliberately few: every one of these has an obvious C
# type and an obvious cell editor, and a type that has neither becomes a column
# nobody can fill in.
COLUMN_TYPES = (
    ("int", "Number", "s32"),
    ("text", "Text", "const char*"),
    ("bool", "Yes/No", "u8"),
)
COLUMN_C = {k: c for k, _lbl, c in COLUMN_TYPES}

# Said on every work surface, so it is one sentence to translate rather than four.
KEYS_HINT = "Or use the arrow keys and Space."

# The painting tools, with the key that selects each one. Drawn icons, not
# characters: the shipped face has no tool glyphs (see nbicons).
TOOLS = (
    ("pen", "pencil", "Pen", "P"),
    ("fill", "fill", "Fill", "F"),
    ("erase", "eraser", "Eraser", "E"),
    ("pick", "picker", "Pick", "I"),
)

# The action palette, grouped. It was one flat scroll of thirty-nine buttons in
# no stated order, which is unreadable however well each button is named; these
# are the groups the source list was already informally commented into.
ACTION_GROUPS = (
    ("MOTION", ("move_fixed", "set_hspeed", "set_vspeed", "move_toward",
                "set_gravity", "jump_to", "jump_relative", "wrap", "glide", "input_lock")),
    ("INSTANCES", ("create_instance", "destroy_self", "destroy_object",
                   "change_sprite", "set_image_speed")),
    ("VARIABLES", ("set_var", "add_var")),
    ("FLOW", ("if_var", "if_collision", "if_chance", "repeat", "exit_event",
              "set_alarm", "goto_room")),
    ("SCORE", ("set_score", "add_score", "if_score", "set_lives", "add_lives",
               "if_lives", "set_health", "add_health", "if_health")),
    ("SOUND", ("play_sound", "stop_sound")),
    ("TEXT", ("say", "menu", "draw_text", "draw_number", "clear_text")),
    ("ADVANCED", ("save_game", "load_game", "pal_cycle_start",
                  "pal_cycle_stop", "obj_window", "execute_code")),
)

# Event kinds offered on an object (Game-Maker-style).
EVENT_KINDS = [
    ("create", "Create"), ("step", "Step"), ("no_health", "No Health"),
    ("destroy", "Destroy"),
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
    # Cutscene motion. By hand this is a start, a target, a frame count and a
    # division per axis per frame -- four of an instance's twelve variables
    # spent on arithmetic the engine can do for nothing.
    ("glide", "Glide To", [("x", "X", "int"), ("y", "Y", "int"),
                           ("frames", "Over frames", "int")]),
    ("input_lock", "Lock Input", [("on", "State", ["on", "off"])]),
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
    # Dialogue. One row, because typewriter text written by hand is a timer, a
    # cursor, a page counter and a wait-for-button -- five of the twelve
    # variables an instance has.
    ("say", "Say", [("text", "Message", "str")]),
    # A menu spans frames and an action does not, so this one names a variable
    # and the answer arrives in it: the chosen line's number, or -2 if the
    # player backed out. An If Variable in the Step event does the rest.
    ("menu", "Show Menu", [("a", "Line 1", "str"), ("b", "Line 2", "str"),
                           ("c", "Line 3", "str"), ("d", "Line 4", "str"),
                           ("var", "Answer in", "str")]),
    # --- save games (SRAM: score/lives/health + global.* vars) ---
    ("save_game", "Save Game", []),
    ("load_game", "Load Game", []),
    # --- scripting (a curated subset of C; see docs/GBA-SDK-SPEC.md) ---
    # Two languages, chosen rather than guessed. Script is the small C-like
    # subset the drag-drop actions themselves lower to, with its own friendly
    # errors and its bare `x` / `score` names. C is the text handed to the
    # compiler untouched, which is the only way an action can reach a hardware
    # register, a script's own function or anything else the subset has no word
    # for. Detecting which one was written would be a guess that is wrong
    # silently; a chooser is wrong loudly, if at all.
    ("execute_code", "Execute Code", [("lang", "Language", ["Script", "C"]),
                                      ("code", "Code", "code")]),
]

# Piano-roll pitch range for the sound composer (C3 .. B5).
PITCH_LO, PITCH_HI = 48, 83
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
ACTION_LABEL = {a[0]: a[1] for a in ACTION_DEFS}
ACTION_PARAMS = {a[0]: a[2] for a in ACTION_DEFS}
# These palette conveniences deliberately lower to the compiler's existing raw-C
# action.  Keeping them out of ACTION_DEFS means saved projects contain only an
# action kind gbabuild already understands.
ACTION_PRESETS = {
    "pal_cycle_start": ("Start Palette Cycle", "rt_pal_cycle({obj}, 0, 16, 8);"),
    "pal_cycle_stop": ("Stop Palette Cycle", "rt_pal_cycle_stop(-1);"),
    "obj_window": ("Set OBJ Window",
                   "rt_set_objwin(self, 1);\nrt_window_obj(1);")}
ACTION_LABEL.update({k: v[0] for k, v in ACTION_PRESETS.items()})
# One-line help shown as a tooltip on each action in the palette.
ACTION_TIPS = {
    "move_fixed": "Set speed in a fixed direction",
    "set_hspeed": "Set horizontal speed", "set_vspeed": "Set vertical speed",
    "jump_to": "Teleport to an x, y position",
    "jump_relative": "Move by an x, y offset",
    "wrap": "Wrap around the screen edges",
    "glide": "Move smoothly to a point over a number of frames",
    "input_lock": "Stop or restart the player's control",
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
    "say": "Show a message in a panel, a character at a time",
    "menu": "Offer a list to choose from; the answer lands in a variable",
    "save_game": "Save score / lives / health + globals to the cartridge",
    "load_game": "Load the saved game from the cartridge",
    "pal_cycle_start": "Cycle a range of this object's palette colours",
    "pal_cycle_stop": "Stop one palette cycle, or use -1 to stop all",
    "obj_window": "Use this object as a window stencil and choose inside layers",
    "execute_code": "Run code written by hand, for anything the actions above cannot express",
}
# What a new script opens as. It compiles, so the first build after making one
# succeeds, and it demonstrates the two things a script is for that an Execute
# Code action cannot do: a file-scope table and a function.
SCRIPT_STARTER = """\
/* File-scope C: functions, tables and constants, emitted once and visible to
   every object. Called from an Execute Code action or from another script. */

static const s16 wobble[8] = { 0, 1, 2, 1, 0, -1, -2, -1 };

s16 wobble_at(s32 frame)
{
    return wobble[frame & 7];
}
"""

CONTAINER_ACTIONS = {"if_var", "if_collision", "if_chance", "repeat",
                     "if_score", "if_lives", "if_health"}  # carry nested children


def _u16(color):
    return int(color) & 0xFFFF



def _count_if(lost, condition):
    """Count a discarded value and return the empty replacement.

    Written as a helper so the counting sits ON the line that throws data away
    rather than three lines above it, which is where it stops being done."""
    if condition:
        lost[0] += 1
    return {}


def _count_none(lost, value):
    if value is not None:
        lost[0] += 1
    return None


def _count_str(lost, value):
    if value is not None:
        lost[0] += 1
    return ""


class GbaSdk(nbapp.AppWindow):
    app_name = "GBA SDK"
    menus = ("File", "Edit", "View", "Resource", "Build", "Help")

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
        self._snd_view = "roll"     # "roll" or "score"; one pattern, two readings
        self._snd_btns = {}
        self._snd_cur = [0, 60]     # keyboard cursor in the piano roll: step, pitch
        self._spr_cur = [0, 0]      # keyboard cursor on the pixel canvas
        self._tile_cur = [0, 0]     # keyboard cursor on the tile canvas
        self._paint_stroke = None   # (canvas, x, y, erase) during pointer drag
        self._room_cur = [0, 0]     # keyboard cursor in the room, in 8px cells
        self._room_zoom = 2         # room pixels per screen pixel: 1, 2, 4 or "fit"
        self._tree_busy = False     # guards the browser's selection round-trip
        self._layout_save_timer = None  # coalesces workspace-only autosaves
        self._heads = {}            # kind -> (title label, subtitle label)
        self._pane_focus = {}       # kind -> the widget Return from the browser lands on
        self._tool_btns = {}
        # One per pixel editor: _paint_column is built twice, so keeping a single
        # widget in an attribute would have left the sprite pane's colour name and
        # colour count frozen at whatever the tile pane last set.
        self._colour_names = []
        self._colour_counts = []

        self._load_note = 0
        self._new_project()
        self._load_autosave()
        # Undo covers the WHOLE project, because every editor edits one document:
        # a pixel, a note, an action and a placed instance are all a change to
        # self.proj. Painting calls touch() (a burst of pixels collapses into one
        # step); structural edits bracket themselves with checkpoint/commit.
        self.undo = nbapp.UndoHistory(self._snapshot, self._restore,
                                      typing_label=_t("Paint"))

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_vexpand(True)
        self.content.pack_start(self._toolbar(), False, False, 0)
        self.content.pack_start(body, True, True, 0)

        body.pack_start(self._resource_browser(), False, False, 0)
        # centre editor stack
        # The workspace, not a Stack: several editors open at once, in tabs and
        # splits. Phase 1 of docs/GBA-SDK-SPEC.md — every later subsystem (the
        # score editor, data tables, the emulator pane) registers here, and the
        # panes themselves know nothing about where they sit.
        self._editor_stack = gbaworkspace.Workspace(
            on_change=self._save_layout_soon)
        for pid, title, build, closable in (
                ("welcome", "Start", self._welcome_pane, False),
                ("sprite", "Sprite", self._sprite_pane, True),
                ("tileset", "Tiles", self._tileset_pane, True),
                ("sound", "Sound", self._sound_pane, True),
                ("object", "Object", self._object_pane, True),
                ("room", "Room", self._room_pane, True),
                ("script", "Script", self._script_pane, True),
                ("palette", "Palettes", self._palette_pane, True),
                ("table", "Table", self._table_pane, True),
                ("world", "World", self._world_pane, True),
                # Help is a pane like any other, so it opens BESIDE the editor
                # it describes rather than over it. A reference that covers the
                # work it explains is one that gets closed to do the work.
                ("help", "Help", self._help_pane, True)):
            self._editor_stack.register(pid, _t(title), build(), closable)
        body.pack_start(self._editor_stack, True, True, 0)

        self._render_tree()
        self._editor_stack.show("welcome")
        self._restore_layout()
        # Say which tool is held from the very first frame: without this all four
        # buttons read as "not chosen" until one was clicked.
        self._set_tool(self._spr_tool)
        if self._load_note:
            self._flash(_t("Part of this project could not be read and was left "
                           "out. The file as it was is kept beside it."))
        self.connect("key-press-event", self._on_sdk_key)
        self.connect("destroy", self._on_destroy)
        self.undo.reset()

    # ================= model =================
    # ---- workspace layout ---------------------------------------------------
    def _save_layout_soon(self):
        """Keep the arrangement with the project. Guarded: the workspace fires
        this while it is still being built, before the store exists.

        Switching editors can emit several workspace changes together.  The
        project model is updated immediately, but its (potentially large) JSON
        is written once after that burst instead of once per selected editor.
        """
        try:
            self.proj["layout"] = self._editor_stack.layout()
        except Exception:                                   # noqa: BLE001
            pass
        if self._layout_save_timer is not None:
            GLib.source_remove(self._layout_save_timer)
        self._layout_save_timer = GLib.timeout_add(250,
                                                   self._flush_layout_save)

    def _flush_layout_save(self):
        self._layout_save_timer = None
        self._save_autosave()
        return False

    def _restore_layout(self):
        desc = (self.proj or {}).get("layout")
        if isinstance(desc, dict):
            self._editor_stack.set_layout(desc)

    def _split_pane(self, orientation):
        if not self._editor_stack.split(orientation):
            self._flash(_t("A pane splits when its group holds two or more "
                           "editors"))

    def _close_pane(self):
        cur = self._editor_stack._active.current
        if cur and cur != "welcome":
            self._editor_stack.close(cur)

    def _reset_layout(self):
        self._editor_stack.reset(keep="welcome")

    def _new_project(self):
        self.proj = {"name": "Game", "save_type": "sram",
                     "sprites": [], "sounds": [],
                     "tilesets": [], "objects": [], "rooms": [],
                     "scripts": [], "tables": [], "start_room": None}

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
        """One bar, one action.

        It used to also carry New Sprite / New Object / New Room, which put four
        competing buttons round the app's single most important one and repeated
        what the browser beside them does better (creating a thing belongs with
        the list of things, where the result appears). The red is now the only
        thing in the bar that can be pressed."""
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.get_style_context().add_class("sdkbar")
        run = Gtk.Button()
        run.set_relief(Gtk.ReliefStyle.NONE)
        run.get_style_context().add_class("runbtn")
        rh = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        rh.pack_start(nbicons.image("cartridge", 13, "#FCFBF8"),
                      False, False, 0)
        rh.pack_start(Gtk.Label(label=_acc("Build & Export", "Ctrl+B")),
                      False, False, 0)
        run.add(rh)
        run.set_tooltip_text(_t("Build the project into a .gba file"))
        run.connect("clicked", lambda *_: self._file_export())
        bar.pack_start(run, False, False, 0)

        bar.pack_start(Gtk.Box(), True, True, 0)
        self._status = Gtk.Label(label="", xalign=1)
        self._status.get_style_context().add_class("sdkstatus")
        # END, not MIDDLE. MIDDLE is right for a path or a file name, where
        # the head and the tail both carry meaning; on a SENTENCE it eats the
        # verb and leaves two halves that do not join up -- Russian rendered
        # "выб… ровать»". END keeps the sentence readable from the start.
        # 52 chars truncated even the English message (63); the longest
        # translation of it is 98 (Yiddish).
        self._status.set_ellipsize(Pango.EllipsizeMode.END)
        self._status.set_max_width_chars(64)
        bar.pack_end(self._status, False, False, 0)
        return bar

    # ================= the asset browser =================
    def _resource_browser(self):
        """The list of everything in the project, down the left.

        This was five headings and a stack of identical lines of text: in a game
        maker, where the whole point is that you DREW the thing, an asset you
        cannot recognise without opening it is the app's central failure. Every
        row now carries a picture of itself (a sprite's first frame, a tile set's
        tiles, a sound's notes, a room's map) and a line saying what it is, and
        the whole thing is a GtkListBox so the arrow keys walk it."""
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.set_size_request(232, -1)
        col.get_style_context().add_class("browser")

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        head.get_style_context().add_class("browserhead")
        head.pack_start(_eyebrow(_t("PROJECT")), False, False, 0)
        self._proj_label = Gtk.Label(xalign=0)
        self._proj_label.get_style_context().add_class("browsertitle")
        self._proj_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        # Search. A project at the scale this suite is specified for (10,000+
        # assets, see docs/GBA-SDK-SPEC.md) cannot be navigated by scrolling,
        # and the browser is the only way in to any of them.
        self._search = Gtk.SearchEntry()
        nbicons.style_search_entry(self._search)
        self._search.set_placeholder_text(_t("Search"))
        self._search.connect("search-changed", lambda *_: self._render_tree())
        head.pack_start(self._proj_label, False, False, 0)
        self._search.set_margin_top(6)
        head.pack_start(self._search, False, False, 0)
        col.pack_start(head, False, False, 0)
        col.pack_start(_rule(), False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._tree_body = Gtk.ListBox()
        self._tree_body.get_style_context().add_class("assetlist")
        self._tree_body.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._tree_body.connect("row-selected", self._on_tree_select)
        self._tree_body.connect("row-activated", self._on_tree_activate)
        scroll.add(self._tree_body)
        col.pack_start(scroll, True, True, 0)
        return col

    # What one of a kind of thing is, in one line under its name. Kept here so
    # the browser row and the pane head cannot drift apart.
    def _describe(self, kind, r):
        try:
            if kind == "sprite":
                out = _t("%d × %d px") % (int(r.get("w", 16)), int(r.get("h", 16)))
                n = len(r.get("frames") or [])
                if n > 1:
                    out += "  ·  " + _t("%d frames") % n
                return out
            if kind == "tileset":
                # not "%d tile%s" with a hard-coded "s": that read "1 tiles",
                # and nbi18n cannot translate a key carrying both %d and %s
                n = len(r.get("tiles") or [])
                return (_t("1 tile") if n == 1
                        else _t("%d tiles") % n)
            if kind == "sound":
                return _t("%d steps") % int(r.get("steps", 16))
            if kind == "object":
                evs = r.get("events") or []
                if not evs:
                    # "No events" belongs to the diary apps (de "Keine
                    # Termine"). This row counts a game OBJECT's events, so
                    # it needs its own key -- the third and last homograph
                    # gbasdk shared with Calendar/Tasks.
                    return _t("No object events")
                # "1 event", not "1 events": the hard-coded "s" was the last
                # of this family (tiles, frames and steps are already split)
                n = len(evs)
                return _t("1 event") if n == 1 else _t("%d events") % n
            if kind == "room":
                out = _t("%d × %d px") % (int(r.get("w", 240)), int(r.get("h", 160)))
                n = len(r.get("instances") or [])
                if n:
                    out += "  ·  " + (_t("1 object") if n == 1
                                       else _t("%d objects") % n)
                return out
        except Exception:
            pass
        return ""

    def _render_tree(self):
        """Rebuild the browser. Never raises: a damaged resource must cost its
        own row's picture, not the whole list."""
        self._tree_busy = True
        try:
            for c in self._tree_body.get_children():
                self._tree_body.remove(c)
            self._proj_label.set_text(self.proj.get("name") or _t("Game"))
            selected = None
            q = ""
            try:
                q = (self._search.get_text() or "").strip().lower()
            except AttributeError:
                q = ""          # the browser is built before the field exists
            for kind, heading, newlabel, _one in KINDS:
                self._tree_body.add(self._group_row(kind, heading, newlabel))
                # (index, resource) pairs, and the index is ALWAYS the one
                # into the project's own list. Filtering must not renumber
                # them: self._sel and _asset_row both address a resource by
                # that index, so a filtered list numbered 0..n would open and
                # select the wrong asset the moment anything was typed.
                items = list(enumerate(self._res(kind)))
                if q:
                    # Match on the name the row shows, so what is typed and
                    # what is matched are the same string.
                    items = [(i, r) for i, r in items
                             if q in str(r.get("id", "")).lower()]
                if not items:
                    self._tree_body.add(
                        self._empty_row(_t("No match") if q else None))
                    continue
                # Folders. A resource carries an optional "folder" name; no
                # folder means the top of its kind. Kept as a plain key rather
                # than a tree so an older project loads unchanged and a folder
                # that loses its last asset simply stops existing.
                loose = [(i, r) for i, r in items if not r.get("folder")]
                named = {}
                for i, r in items:
                    f = r.get("folder")
                    if f:
                        named.setdefault(str(f), []).append((i, r))
                for i, r in loose:
                    row = self._asset_row(kind, i, r)
                    self._tree_body.add(row)
                    if self._sel == (kind, i):
                        selected = row
                for folder in sorted(named):
                    shut = self._folder_shut(kind, folder)
                    self._tree_body.add(
                        self._folder_row(kind, folder, len(named[folder]),
                                         shut))
                    # A search looks INSIDE closed folders — hiding a match
                    # behind a fold is the one thing a search must not do.
                    if shut and not q:
                        continue
                    for i, r in named[folder]:
                        row = self._asset_row(kind, i, r)
                        self._tree_body.add(row)
                        if self._sel == (kind, i):
                            selected = row
            self._tree_body.show_all()
            if selected is not None:
                self._tree_body.select_row(selected)
            else:
                self._tree_body.unselect_all()
        finally:
            self._tree_busy = False

    def _folder_shut(self, kind, folder):
        return "%s/%s" % (kind, folder) in (self.proj.get("shut_folders") or [])

    def _toggle_folder(self, kind, folder):
        key = "%s/%s" % (kind, folder)
        shut = list(self.proj.get("shut_folders") or [])
        if key in shut:
            shut.remove(key)
        else:
            shut.append(key)
        self.proj["shut_folders"] = shut
        self._save_autosave()
        self._render_tree()

    def _folder_row(self, kind, folder, count, shut):
        """A folder inside a kind: its name, how many are in it, and whether it
        is open. Indented under the kind's heading so the two levels read as
        levels rather than as two lists."""
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("folderrow")
        box = Gtk.Box(spacing=6)
        car = Gtk.Label(label="\u25b8" if shut else "\u25be")
        car.get_style_context().add_class("caret")
        box.pack_start(car, False, False, 0)
        lab = Gtk.Label(label=folder, xalign=0)
        lab.get_style_context().add_class("foldername")
        lab.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(lab, True, True, 0)
        n = Gtk.Label(label=str(count))
        n.get_style_context().add_class("foldercount")
        box.pack_end(n, False, False, 0)
        btn.add(box)
        btn.set_tooltip_text(_t("Open or close this folder"))
        btn.connect("clicked",
                    lambda _b, k=kind, f=folder: self._toggle_folder(k, f))
        row.add(btn)
        return row

    def _move_to_folder(self):
        """Resource ▸ Move to Folder… — an empty name puts it back at the top
        of its kind, which is the only way out of a folder."""
        r = self._sel_res()
        if not r:
            return
        self._prompt_text(_t("Move to folder"), r.get("folder", ""),
                          self._commit_folder)

    def _commit_folder(self, name):
        r = self._sel_res()
        if not r:
            return
        self.undo.checkpoint(_t("Move to folder"))
        name = (name or "").strip()[:40]
        if name:
            r["folder"] = name
        else:
            r.pop("folder", None)
        self._save_autosave()
        self._render_tree()
        self.undo.commit()

    def _group_row(self, kind, heading, newlabel):
        """A heading, its count, and the button that adds one."""
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.get_style_context().add_class("grouphead")
        box.pack_start(_eyebrow(heading), True, True, 0)
        n = len(self._res(kind))
        if n:
            cnt = Gtk.Label(label=str(n), xalign=1)
            cnt.get_style_context().add_class("groupcount")
            box.pack_start(cnt, False, False, 0)
        # A bare "+" said nothing and named nothing; this is the plus glyph the
        # rest of the OS draws, and it says which kind of thing it makes.
        box.pack_end(_icon_button("plus", _t(newlabel),
                                  lambda k=kind: self._add_resource(k),
                                  cls="addbtn", size=12), False, False, 0)
        row.add(box)
        return row

    def _empty_row(self, text=None):
        """The line under a heading with nothing in it. `text` distinguishes a
        kind that is empty from one whose contents the search has filtered
        out — otherwise a search that matches nothing reads as a project that
        has lost its assets."""
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        row.set_activatable(False)
        lbl = Gtk.Label(label=text or _t("Empty"), xalign=0)
        lbl.get_style_context().add_class("emptyrow")
        row.add(lbl)
        return row

    def _asset_row(self, kind, index, r):
        row = Gtk.ListBoxRow()
        row.kind, row.index = kind, index
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        box.get_style_context().add_class("assetrow")
        thumb = Gtk.DrawingArea()
        thumb.set_size_request(30, 30)
        thumb.set_valign(Gtk.Align.CENTER)
        thumb.connect("draw", self._draw_asset_thumb, kind, r)
        box.pack_start(thumb, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=r.get("id", "?"), xalign=0)
        name.get_style_context().add_class("assetname")
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        text.pack_start(name, False, False, 0)
        sub = Gtk.Label(label=self._describe(kind, r), xalign=0)
        sub.get_style_context().add_class("assetsub")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        text.pack_start(sub, False, False, 0)
        box.pack_start(text, True, True, 0)
        # The room the game starts in is a fact about the project that was
        # readable nowhere except by opening each room and looking at a tick box.
        if kind == "room" and self.proj.get("start_room") == r.get("id"):
            badge = Gtk.Label(label=_t("Start"))
            badge.get_style_context().add_class("startbadge")
            badge.set_valign(Gtk.Align.CENTER)
            box.pack_end(badge, False, False, 0)
        row.add(box)
        row.set_tooltip_text("%s  ·  %s" % (r.get("id", "?"),
                                            self._describe(kind, r)))
        return row

    def _on_tree_select(self, _box, row):
        if self._tree_busy or row is None:
            return
        kind, index = getattr(row, "kind", None), getattr(row, "index", None)
        if kind is not None:
            self._select_resource(kind, index)

    def _on_tree_activate(self, _box, row):
        """Return (or a double click) on a row moves into the editor, so the
        keyboard route is browse -> Return -> work, and Shift+Tab comes back."""
        w = self._pane_focus.get(getattr(row, "kind", None))
        if w is not None:
            w.grab_focus()

    # ---- the pictures in the browser ----
    def _draw_asset_thumb(self, w, cr, kind, r):
        aw = w.get_allocated_width()
        ah = w.get_allocated_height()
        cr.set_source_rgb(0.99, 0.98, 0.97)
        cr.rectangle(0, 0, aw, ah)
        cr.fill()
        try:
            if kind == "sprite":
                self._thumb_pixels(cr, aw, ah, r.get("frames", [[]])[0],
                                   int(r.get("w", 16)), int(r.get("h", 16)))
            elif kind == "object":
                spr = self._sprite_by_id(r.get("sprite"))
                if spr and spr.get("frames"):
                    self._thumb_pixels(cr, aw, ah, spr["frames"][0],
                                       int(spr.get("w", 16)), int(spr.get("h", 16)))
                else:
                    self._thumb_no_picture(cr, aw, ah)
            elif kind == "tileset":
                self._thumb_tiles(cr, aw, ah, r.get("tiles") or [])
            elif kind == "sound":
                self._thumb_sound(cr, aw, ah, r)
            elif kind == "room":
                self._thumb_room(cr, aw, ah, r)
        except Exception:
            pass
        cr.set_source_rgb(0.79, 0.77, 0.71)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, aw - 1, ah - 1)
        cr.stroke()
        return False

    def _thumb_pixels(self, cr, aw, ah, px, sw, sh):
        if not px or sw <= 0 or sh <= 0:
            return
        cell = min((aw - 4.0) / sw, (ah - 4.0) / sh)
        ox = (aw - sw * cell) / 2.0
        oy = (ah - sh * cell) / 2.0
        for j in range(sh):
            for i in range(sw):
                k = j * sw + i
                if k >= len(px) or px[k] == TRANSPARENT:
                    continue
                cr.set_source_rgb(*self._c15(px[k]))
                cr.rectangle(ox + i * cell, oy + j * cell, cell + 0.6, cell + 0.6)
                cr.fill()

    def _thumb_no_picture(self, cr, aw, ah):
        """An object with no sprite yet: say so in shape, not in colour."""
        cr.set_source_rgb(0.68, 0.66, 0.59)
        cr.set_line_width(1)
        cr.set_dash([2.0, 2.0])
        cr.rectangle(7.5, 7.5, aw - 15, ah - 15)
        cr.stroke()
        cr.set_dash([])

    def _thumb_tiles(self, cr, aw, ah, tiles):
        """Up to four of the set's tiles, in a two-by-two — a tile SET reads as
        several tiles, never as one picture."""
        if not tiles:
            return
        cell = (min(aw, ah) - 4) / 2.0
        for n in range(min(4, len(tiles))):
            gx = 2 + (n % 2) * cell
            gy = 2 + (n // 2) * cell
            cr.save()
            cr.translate(gx, gy)
            self._draw_tile_grid(cr, tiles[n], cell / 8.0, False, 8)
            cr.restore()

    def _thumb_sound(self, cr, aw, ah, s):
        """A sound's shape: its notes as a little step pattern, drawn as columns
        standing on the floor of the chip so a tune reads as a tune."""
        cols = max(1, int(s.get("steps", 16)))
        span = max(1.0, float(PITCH_HI - PITCH_LO))
        bw = max(1.0, (aw - 6) / float(cols) - 0.5)
        for chan, shade in (("bass", 0.60), ("lead", 0.14)):
            seq = s.get(chan) or []
            cr.set_source_rgb(shade, shade, shade)
            for c, note in enumerate(seq[:cols]):
                if not note:
                    continue
                x = 3 + (aw - 6) * c / float(cols)
                h = 3 + (ah - 8) * ((note - PITCH_LO) / span)
                cr.rectangle(x, ah - 3 - h, bw, h)
                cr.fill()

    def _thumb_room(self, cr, aw, ah, rm):
        rw = max(16, int(rm.get("w", 240)))
        rh = max(16, int(rm.get("h", 160)))
        sc = min((aw - 2.0) / rw, (ah - 2.0) / rh)
        ox = (aw - rw * sc) / 2.0
        oy = (ah - rh * sc) / 2.0
        cr.set_source_rgb(*self._c15(gbabuild._rgb15(rm.get("bg"), 0)))
        cr.rectangle(ox, oy, rw * sc, rh * sc)
        cr.fill()
        tm = rm.get("tiles")
        all_tiles = self._all_tiles()
        if isinstance(tm, list) and tm and all_tiles:
            cw = max(1, rw // 8)
            for ci, v in enumerate(tm):
                if not v or v > len(all_tiles):
                    continue
                avg = self._tile_average(all_tiles[v - 1])
                if avg == TRANSPARENT:      # an empty tile paints nothing
                    continue
                cr.set_source_rgb(*self._c15(avg))
                cr.rectangle(ox + (ci % cw) * 8 * sc, oy + (ci // cw) * 8 * sc,
                             8 * sc + 0.6, 8 * sc + 0.6)
                cr.fill()
        for it in rm.get("instances", []):
            cr.set_source_rgb(0.98, 0.98, 0.96)
            cr.rectangle(ox + it.get("x", 0) * sc - 1, oy + it.get("y", 0) * sc - 1,
                         2.4, 2.4)
            cr.fill()

    @staticmethod
    def _tile_average(tile):
        """One 15-bit colour standing for a whole tile, for map thumbnails."""
        r = g = b = n = 0
        for c in tile:
            if c == TRANSPARENT:
                continue
            r += c & 31
            g += (c >> 5) & 31
            b += (c >> 10) & 31
            n += 1
        if not n:
            return TRANSPARENT
        return _col(r // n, g // n, b // n)

    def _add_resource(self, kind):
        self.undo.checkpoint(_t(dict((k, n) for k, _h, n, _o in KINDS)[kind]))
        lst = self._res(kind)
        if kind == "sprite":
            rid = self._uid("spr", lst)
            lst.append({"id": rid, "w": 16, "h": 16, "ox": 8, "oy": 8,
                        "anim_speed": 0, "frames": [[TRANSPARENT] * 256]})
        elif kind == "sound":
            rid = self._uid("snd", lst)
            lst.append({"id": rid, "tempo": 8, "loop": True, "steps": 16,
                        "lead": [0] * 16, "bass": [0] * 16,
                        "drum": [0] * 16, "kind": 0, "duty": 0, "vol": 0,
                        "decay": 0, "prio": 0})
        elif kind == "object":
            rid = self._uid("obj", lst)
            lst.append({"id": rid, "sprite": None, "visible": True,
                        "solid": False, "tilecol": 1, "depth": 0,
                        "bb_inset": 0, "hurt_frames": 0, "events": []})
        elif kind == "script":
            rid = self._uid("scr", lst)
            lst.append({"id": rid, "code": SCRIPT_STARTER})
        elif kind == "table":
            rid = self._uid("tbl", lst)
            # One column and one row: an empty grid offers nothing to click,
            # and "add a column" is a worse first step than "change this one".
            lst.append({"id": rid,
                        "columns": [{"name": "name", "type": "text"}],
                        "rows": [[""]]})
        elif kind == "tileset":
            rid = self._uid("ts", lst)
            size = int(getattr(self, "_new_tileset_size", 8) or 8)
            if size not in TILE_SIZES:
                size = 8
            lst.append({"id": rid, "size": size,
                        "tiles": [[TRANSPARENT] * (size * size)]})
        else:
            rid = self._uid("rm", lst)
            lst.append({"id": rid, "w": 240, "h": 160, "speed": 60,
                        "bg": "#101820", "instances": [],
                        "tiles": [0] * ((240 // 8) * (160 // 8))})
        if kind == "room":
            # The FIRST room becomes the start room — and so does the next one
            # made after the start room was deleted, which used to leave a
            # project with rooms and no start at all.
            self._fix_start_room()
        self._save_autosave()
        self._render_tree()
        self._select_resource(kind, len(lst) - 1)
        self.undo.commit()

    def _rename_resource(self):
        r = self._sel_res()
        if not r:
            return
        self._prompt_text(_t("Rename"), r.get("id", ""),
                          lambda v: self._do_rename(v))

    # Which action parameters name which kind of resource. ACTION_DEFS is the one
    # true list of them, so this is derived from it rather than written out again:
    # an action added there is covered here the day it appears, which is exactly
    # what did NOT happen — rename knew about play_sound and nothing else, so
    # renaming an object left Create Instance, Destroy Object and If Collision
    # pointing at a name that no longer existed.
    _REF_SPECS = {"obj": "object", "room": "room", "snd": "sound", "spr": "sprite"}

    @classmethod
    def _ref_params(cls, kind):
        """[(action kind, parameter key)] naming resources of `kind`."""
        want = {"object": "obj", "room": "room", "sound": "snd",
                "sprite": "spr"}.get(kind)
        out = []
        if not want:
            return out
        for akind, _label, params in ACTION_DEFS:
            for key, _lbl, spec in params:
                if spec == want:
                    out.append((akind, key))
        return out

    def _walk_refs(self, kind, visit, with_context=False):
        """Call visit(container, key, value) at every place in the project that
        names a resource of `kind` — one walker for rename, for counting and for
        cleaning up after a delete, so the three can never disagree again."""
        pairs = dict(self._ref_params(kind))

        def found(container, key, value, context):
            if with_context:
                visit(container, key, value, context)
            else:
                visit(container, key, value)

        def acts(lst, owner_obj, event_index, path=()):
            for ai, a in enumerate(lst or []):
                if not isinstance(a, dict):
                    continue
                key = pairs.get(a.get("kind"))
                if key is not None and key in a:
                    found(a, key, a.get(key), {"site": "action",
                          "owner": owner_obj, "event": event_index,
                          "action": path + (ai,)})
                acts(a.get("children"), owner_obj, event_index, path + (ai,))

        for o in self.proj.get("objects", []):
            if not isinstance(o, dict):
                continue
            if kind == "sprite":
                found(o, "sprite", o.get("sprite"),
                      {"site": "object", "owner": o})
            for ei, ev in enumerate(o.get("events", []) or []):
                if not isinstance(ev, dict):
                    continue
                # A Collision event's target is a reference too, and the compiler
                # drops the WHOLE event body when it dangles.
                if kind == "object" and ev.get("type") == "collision":
                    found(ev, "object", ev.get("object"),
                          {"site": "event", "owner": o, "event": ei})
                acts(ev.get("actions"), o, ei)
        for rm in self.proj.get("rooms", []):
            if not isinstance(rm, dict):
                continue
            if kind == "object":
                for it in rm.get("instances", []) or []:
                    if isinstance(it, dict):
                        found(it, "object", it.get("object"),
                              {"site": "placement", "owner": rm,
                               "instance": rm.get("instances", []).index(it)})
            if kind == "room":
                # A door names the room it leads to. Doors were added after
                # this walker and never joined it, so renaming a room left
                # every door into it pointing at a name that no longer existed
                # — and the delete confirm said "used 0 times" while a door
                # used it. Exactly the failure the comment above _REF_SPECS
                # describes, one resource kind later.
                for wp in rm.get("warps", []) or []:
                    if isinstance(wp, dict):
                        found(wp, "room", wp.get("room"),
                              {"site": "warp", "owner": rm})
        if kind == "room":
            found(self.proj, "start_room", self.proj.get("start_room"),
                  {"site": "project", "owner": self.proj})

    @staticmethod
    def _match_snippet(value, query, width=76):
        """One compact line around a case-insensitive match."""
        text = " ".join(str(value).split())
        at = text.casefold().find(query.casefold())
        if at < 0 or len(text) <= width:
            return text
        start = max(0, at - width // 3)
        end = min(len(text), start + width)
        return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")

    def _project_search(self, query):
        """Return navigation-ready matches across every authored text surface."""
        q = str(query or "").strip().casefold()
        if not q:
            return []
        out = []
        def add(kind, owner, value, **where):
            if q in str(value or "").casefold():
                rec = {"kind": kind, "owner": owner,
                       "snippet": self._match_snippet(value, q)}
                rec.update(where); out.append(rec)
        for kind, plural in (("sprite", "sprites"), ("tileset", "tilesets"),
                             ("sound", "sounds"), ("object", "objects"),
                             ("room", "rooms"), ("script", "scripts"),
                             ("table", "tables")):
            for ri, resource in enumerate(self.proj.get(plural, []) or []):
                if not isinstance(resource, dict):
                    continue
                rid = resource.get("id", "?")
                add(kind, rid, rid, resource=ri)
                if resource.get("name") and resource.get("name") != rid:
                    add(kind, rid, resource["name"], resource=ri)
                if kind == "script":
                    add("Script body", rid, resource.get("code", ""), resource=ri)
                if kind == "table":
                    for rowi, row in enumerate(resource.get("rows", []) or []):
                        for coli, cell in enumerate(row or []):
                            add("Table cell", rid, cell, resource=ri,
                                row=rowi, column=coli)
                if kind == "object":
                    for ei, ev in enumerate(resource.get("events", []) or []):
                        def walk(actions, path=()):
                            for ai, action in enumerate(actions or []):
                                if not isinstance(action, dict):
                                    continue
                                apath = path + (ai,)
                                for key, value in action.items():
                                    if key not in ("kind", "children") and isinstance(value, str):
                                        add("Action", rid, value, resource=ri,
                                            event=ei, action=apath)
                                walk(action.get("children"), apath)
                        walk(ev.get("actions"))
        return out

    def _where_used(self, kind, rid):
        """Reverse-reference records, deliberately sourced only from _walk_refs."""
        out = []
        def collect(_container, _key, value, context):
            if value != rid:
                return
            owner = context.get("owner") or {}
            site = context.get("site", "reference")
            label = {"placement": "Room placement", "action": "Action reference",
                     "event": "Collision event", "object": "Object sprite",
                     "warp": "Room warp", "project": "Project setting"}.get(site, site)
            rec = {"kind": label, "owner": owner.get("id", _t("Project")),
                   "snippet": rid, "ref_kind": kind}
            rec.update(context); out.append(rec)
        self._walk_refs(kind, collect, with_context=True)
        return out

    def _activate_project_result(self, result):
        """Land a search/reference record using the normal editor loaders."""
        kind = result.get("ref_kind")
        owner = result.get("owner")
        if result.get("site") == "placement":
            kind = "room"
        if kind is None:
            k = result.get("kind")
            kind = {"Script body": "script", "Table cell": "table",
                    "Action": "object"}.get(k, k)
        plural = {"sprite": "sprites", "tileset": "tilesets", "sound": "sounds",
                  "object": "objects", "room": "rooms", "script": "scripts",
                  "table": "tables"}.get(kind)
        index = result.get("resource")
        if plural and index is None:
            index = next((i for i, r in enumerate(self.proj.get(plural, []))
                          if r is owner or r.get("id") == (owner.get("id") if isinstance(owner, dict) else owner)), None)
        if index is None or plural is None:
            return False
        self._select_resource(kind, index)
        if kind == "object" and "event" in result:
            self._select_event(result["event"])
            path = result.get("action") or ()
            self._sel_action = path[-1] if path else None
            self._focus_action_path(path)
        elif kind == "table" and "row" in result:
            self._tbl_view.set_cursor(Gtk.TreePath.new_from_indices([result["row"]]))
        elif kind == "room" and "instance" in result:
            instances = self._sel_res().get("instances", [])
            if 0 <= result["instance"] < len(instances):
                placed = instances[result["instance"]]
                self._room_cur = [max(0, int(placed.get("x", 0)) // 8),
                                  max(0, int(placed.get("y", 0)) // 8)]
                self._room_canvas.grab_focus()
            self._room_canvas.queue_draw()
        return True

    def _focus_action_path(self, path):
        """Focus a top-level or nested action card by its authored index path."""
        if not path:
            return
        event = self._cur_event() or {}
        actions = event.get("actions") or []
        for index in path[:-1]:
            if not (0 <= index < len(actions)):
                return
            actions = actions[index].get("children") or []
        target = path[-1]
        def descend(widget):
            if (getattr(widget, "act_list", None) is actions
                    and getattr(widget, "act_index", None) == target):
                widget.grab_focus()
                return True
            get_children = getattr(widget, "get_children", None)
            return any(descend(child) for child in (get_children() if get_children else []))
        descend(self._action_list)

    def _do_rename(self, newid):
        r = self._sel_res()
        if not r or not newid:
            return
        newid = gbabuild._cid(newid)
        old = r.get("id")
        if newid == old:
            return
        kind = self._sel[0]
        # Two resources of a kind with the same name collapse every reference to
        # them onto one, which cannot be undone by renaming back.
        if any(other is not r and other.get("id") == newid
               for other in self._res(kind)):
            self._flash(_t("There is already something called “%s”.") % newid)
            return
        self.undo.checkpoint(_t("Rename"))
        r["id"] = newid

        def repoint(container, key, value):
            if value == old:
                container[key] = newid
        self._walk_refs(kind, repoint)
        self._save_autosave()
        self._render_tree()
        self._refresh_editor()
        self.undo.commit()

    # What each kind of resource is called when it is about to be destroyed.
    # Written out per kind rather than pasted into one sentence: "Delete this
    # %s?" cannot be translated, because the article and the ending change with
    # the noun in most of the seventeen languages this ships in.
    _DELETE_HEADS = {
        "sprite": "Delete this sprite?",
        "tileset": "Delete this tile set?",
        "sound": "Delete this sound?",
        "object": "Delete this object?",
        "room": "Delete this room?",
    }

    def _refs_to(self, kind, rid):
        """How many other parts of the game point at this resource.

        Deleting a sprite an object wears, or an object a room places, quietly
        breaks that other thing — which is exactly the consequence a beginner
        cannot see from the browser, so the confirm says it. It used to count one
        site per kind and no more, so a delete that would break five things said
        "used 1 time"; it now counts every site the walker knows about."""
        n = [0]

        def tally(_container, _key, value):
            if value == rid:
                n[0] += 1
        self._walk_refs(kind, tally)
        if kind == "tileset":
            # A tile set is referenced by the room CELLS painted with its tiles,
            # which is a range of the combined tile list rather than a name.
            lo, hi = self._tileset_range(rid)
            for rm in self.proj.get("rooms", []):
                for v in (rm.get("tiles") or []):
                    if isinstance(v, int) and lo < v <= hi:
                        n[0] += 1
        return n[0]

    def _tileset_range(self, rid):
        """(lo, hi) of the 1-based combined tile indices belonging to tile set
        `rid`: cells with lo < v <= hi are painted with one of its tiles."""
        lo = 0
        for ts in self.proj.get("tilesets", []):
            per = max(1, ts_size(ts) // 8) ** 2      # hardware tiles per tile
            count = len(ts.get("tiles") or []) * per
            if ts.get("id") == rid:
                return lo, lo + count
            lo += count
        return 0, 0

    def _forget_refs(self, kind, rid):
        """Clean up after a delete: leave nothing in the project pointing at a
        resource that is gone, and leave a note of what it WAS so the export can
        still name it. Both halves matter — a reference to nothing makes the
        compiler drop whole events in silence, and scrubbing the name without a
        note would lose the only clue the author has about what broke."""
        for rm in self.proj.get("rooms", []):
            insts = rm.get("instances")
            if kind == "object" and isinstance(insts, list):
                # An instance of an object that no longer exists cannot be
                # anything, so it goes rather than being blanked.
                rm["instances"] = [it for it in insts
                                   if not (isinstance(it, dict)
                                           and it.get("object") == rid)]

        def clear(container, key, value):
            if value != rid:
                return
            if container is self.proj:              # the start-room flag
                container[key] = None
                return
            container[key] = ""
            container["_was"] = rid
        self._walk_refs(kind, clear)
        if kind == "tileset":
            self._forget_tiles(rid)

    def _resize_tiles(self, ts, old, new):
        """Re-number every room's painted tiles around a tile set that is about
        to change size.

        A cell inside this set is moved to the FIRST hardware tile of whichever
        authored tile it was showing: an 8px tile becoming 16px now spans four
        cells rather than one, so the painted map cannot be preserved exactly,
        and pointing at the right TILE is the honest approximation. Cells after
        this set shift by the difference, which is exact."""
        per_old = max(1, old // 8) ** 2
        per_new = max(1, new // 8) ** 2
        if per_old == per_new:
            return
        n = len(ts.get("tiles") or [])
        lo, _hi = self._tileset_range(ts.get("id"))
        count_old = n * per_old
        delta = n * per_new - count_old
        for rm in self.proj.get("rooms", []):
            tm = rm.get("tiles")
            if not isinstance(tm, list):
                continue
            out = []
            for v in tm:
                if not isinstance(v, int) or v <= lo:
                    out.append(v if isinstance(v, int) else 0)
                elif v <= lo + count_old:
                    t = (v - lo - 1) // per_old
                    out.append(lo + t * per_new + 1)
                else:
                    out.append(v + delta)
            rm["tiles"] = out

    def _forget_tiles(self, rid):
        """Re-number every room's painted tiles around a deleted tile set.

        The rooms index ONE combined list of all the tile sets' tiles, so
        deleting a set used to shift every later tile down: a level painted in
        grass came back painted in whatever now sat at that index."""
        lo, hi = self._tileset_range(rid)
        gone = hi - lo
        if gone <= 0:
            return
        for rm in self.proj.get("rooms", []):
            tm = rm.get("tiles")
            if not isinstance(tm, list):
                continue
            out = []
            for v in tm:
                if not isinstance(v, int) or v <= lo:
                    out.append(v if isinstance(v, int) else 0)
                elif v <= hi:
                    out.append(0)                   # painted with a tile that went
                else:
                    out.append(v - gone)
            rm["tiles"] = out

    def _delete_resource(self):
        if not self._sel:
            return
        kind, i = self._sel
        lst = self._res(kind)
        if not (0 <= i < len(lst)):
            return
        # Deleting was immediate and unrecoverable: one menu click destroyed a
        # sprite somebody had drawn pixel by pixel, with nothing to undo it.
        # Ask first, name the thing, and say what else it will break.
        rid = lst[i].get("id", "")
        lines = [_t("“%s” is removed. Edit then Undo restores it, until the "
                    "app is closed.") % rid]
        used = self._refs_to(kind, rid)
        if used:
            # Two whole sentences, not one key with "%d time%s": nbi18n
            # rejects a key whose format specs it cannot match, so the "%s"
            # plural trick left this sentence in ENGLISH in all sixteen
            # languages while the sentence beside it was translated.
            if used == 1:
                lines.append(_t("It is used once elsewhere. Those parts will "
                                "stop working."))
            else:
                lines.append(_t("It is used %d times elsewhere. Those parts "
                                "will stop working.") % used)
        if not self._confirm(_t(self._DELETE_HEADS.get(kind, "Delete this?")),
                             " ".join(lines), _t("Delete")):
            return
        self.undo.checkpoint(_t("Delete"))
        self._forget_refs(kind, rid)
        del lst[i]
        if kind == "room":
            self._fix_start_room()
        self._sel = None
        self._tile_pb_cache.clear()
        self._save_autosave()
        self._render_tree()
        self._editor_stack.show("welcome")
        self.undo.commit()

    def _fix_start_room(self):
        """A game has to start somewhere. Deleting the start room used to leave
        the flag naming a room that was gone: no room showed the tick, no new
        room ever claimed it, and the export quietly started at room 0."""
        ids = [r.get("id") for r in self.proj.get("rooms", [])
               if isinstance(r, dict)]
        if self.proj.get("start_room") not in ids:
            self.proj["start_room"] = ids[0] if ids else None

    def _select_resource(self, kind, index):
        self._sel = (kind, index)
        self._sel_event = None
        self._sel_action = None
        self._render_tree()
        self._refresh_editor()

    def _refresh_editor(self):
        r = self._sel_res()
        if not r or not self._sel:
            self._editor_stack.show("welcome")
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
        elif kind == "script":
            self._load_script_editor()
        elif kind == "table":
            self._load_table_editor()
        else:
            self._load_room_editor()
        self._update_head(kind)
        self._update_colour_count()
        self._editor_stack.show(kind)

    # ================= the shape every editor shares =================
    def _pane(self, kind, one, resource=True):
        """The scaffold each of the five editors is built in.

        The panes were written months apart and each had invented its own
        layout: one led with a hint, one with a combo box, one with three rows of
        settings, and none of them told you WHICH thing you were editing — that
        was readable only from the highlight in the browser. They now all open
        the same way: the thing's name, what it is, the two actions that apply to
        anything (rename, delete), a hairline, one tool row, then the work.

        Returns (pane, tools, body): pack controls into `tools` and the work
        surface into `body`."""
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        pane.get_style_context().add_class("editpane")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(xalign=0)
        title.get_style_context().add_class("panetitle")
        title.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        title.set_max_width_chars(28)
        titles.pack_start(title, False, False, 0)
        sub = Gtk.Label(xalign=0)
        sub.get_style_context().add_class("panesub")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.set_max_width_chars(40)
        titles.pack_start(sub, False, False, 0)
        head.pack_start(titles, True, True, 0)
        self._heads[kind] = (title, sub)

        # Packed so they read Rename, then Delete: pack_end fills from the right,
        # so the destructive one goes first and ends up on the outside.
        #
        # Only on a pane that EDITS a resource. Both act on whatever is selected
        # in the browser, so on a pane that shows the project as a whole they
        # would delete something the pane never mentioned -- a destructive
        # button aimed at a target the author cannot see.
        if resource:
            dele = Gtk.Button(label=_t("Delete…"))
            dele.get_style_context().add_class("quietbtn")
            dele.set_valign(Gtk.Align.CENTER)
            dele.connect("clicked", lambda *_: self._delete_resource())
            head.pack_end(dele, False, False, 0)
            ren = Gtk.Button(label=_acc("Rename", "F2"))
            ren.get_style_context().add_class("quietbtn")
            ren.set_valign(Gtk.Align.CENTER)
            ren.connect("clicked", lambda *_: self._rename_resource())
            head.pack_end(ren, False, False, 0)
        pane.pack_start(head, False, False, 0)
        pane.pack_start(_rule(top=14, bottom=12), False, False, 0)

        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tools.get_style_context().add_class("toolrow")
        pane.pack_start(tools, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        body.set_margin_top(12)
        pane.pack_start(body, True, True, 0)
        return pane, tools, body

    def _update_head(self, kind):
        """Retitle the open pane. `one` comes from KINDS so "Sprite · 16 × 16 px"
        is composed at run time out of words the catalogs already carry."""
        pair = self._heads.get(kind)
        r = self._sel_res()
        if not pair or r is None:
            return
        one = dict((k, o) for k, _h, _n, o in KINDS).get(kind, "")
        pair[0].set_text(r.get("id", "?"))
        desc = self._describe(kind, r)
        pair[1].set_text("%s  ·  %s" % (_t(one), desc) if desc else _t(one))

    # ================= welcome pane =================
    def _welcome_pane(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        img = nbicons.image("cartridge", 56, FAINT)
        box.pack_start(img, False, False, 0)
        t = Gtk.Label(label=_t("Make a Game Boy Advance game"))
        t.get_style_context().add_class("welcometitle")
        box.pack_start(t, False, False, 0)
        s = Gtk.Label(
            label=_t("A Sprite is a picture. An Object gives a sprite "
                     "behaviour. A Room places objects on screen. "
                     "Build & Play runs it."))
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
        # Both ways in, side by side: read one that works, or start your own.
        # The empty state used to offer only the example, so "and how do I begin
        # my own?" was answered by a "+" in the margin.
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_halign(Gtk.Align.CENTER)
        ex = Gtk.Button(label=_t("Open the example game"))
        # Secondary (paper) treatment, NOT the red .runbtn: the toolbar's
        # Build & Export is the app's one red accent, and two red buttons on
        # screen at once (there, and here in the empty state) fought each other.
        ex.get_style_context().add_class("exbtn")
        ex.connect("clicked", lambda *_: self._file_example())
        row.pack_start(ex, False, False, 0)
        mine = Gtk.Button(label=_acc("New Sprite", "Ctrl+1"))
        mine.get_style_context().add_class("exbtn")
        mine.set_tooltip_text(_t("Draw a sprite"))
        mine.connect("clicked", lambda *_: self._add_resource("sprite"))
        row.pack_start(mine, False, False, 0)
        box.pack_start(row, False, False, 6)

        # A third way in, quieter than the other two. The course is the only
        # documentation on this machine and it was reachable only from a menu
        # nobody opens before they have a reason to.
        learn = Gtk.Button(label=_t("Learn to make one"))
        learn.set_relief(Gtk.ReliefStyle.NONE)
        learn.get_style_context().add_class("quietbtn")
        learn.set_halign(Gtk.Align.CENTER)
        learn.set_tooltip_text(
            _t("A course that starts at Actions and ends in C"))
        learn.connect("clicked", lambda *_: self._open_help("c01"))
        box.pack_start(learn, False, False, 2)
        return box

    # ================= sprite editor =================
    def _sprite_pane(self):
        pane, tools, body = self._pane("sprite", "Sprite")

        tools.pack_start(Gtk.Label(label=_t("Size")), False, False, 0)
        self._spr_size = Gtk.ComboBoxText()
        for w, h in SPRITE_SIZES:
            self._spr_size.append("%dx%d" % (w, h), "%d×%d" % (w, h))
        self._spr_size.connect("changed", self._on_sprite_size)
        tools.pack_start(self._spr_size, False, False, 0)

        tools.pack_start(_group_label(_t("Tool")), False, False, 0)
        tools.pack_start(self._tool_bar(), False, False, 0)

        tools.pack_start(_group_label(_t("Animation")), False, False, 0)
        self._spr_anim = Gtk.SpinButton()
        self._spr_anim.set_adjustment(Gtk.Adjustment(
            lower=0, upper=64, step_increment=1, value=0))
        self._spr_anim.set_numeric(True)
        # A beginner's game-making app: say what the number DOES to the
        # picture, not what the runtime multiplies it by. (It is still
        # frames-per-step times 16 — see gbaruntime/runtime.h.)
        self._spr_anim.set_tooltip_text(
            _t("How quickly this sprite's pictures change while the game "
               "runs. 16 is a new picture every step, 8 is every other step, "
               "and 0 holds one picture still."))
        self._spr_anim.connect("value-changed", self._on_anim_speed)
        tools.pack_start(self._spr_anim, False, False, 0)
        # A drawn play triangle. It used to be the character "▶", which the
        # interface face does not have: it came out of a fallback font at the
        # wrong weight, and on a machine without that fallback, not at all.
        self._play_btn = Gtk.ToggleButton()
        self._play_btn.set_relief(Gtk.ReliefStyle.NONE)
        self._play_btn.add(nbicons.image("play", 12, MUTED))
        self._play_btn.set_tooltip_text(_acc("Preview the animation", "Space"))
        self._play_btn.get_style_context().add_class("iconbtn")
        self._play_btn.connect("toggled", self._on_play_toggle)
        tools.pack_start(self._play_btn, False, False, 0)

        body.pack_start(self._paint_column(), False, False, 0)

        self._spr_canvas = Gtk.DrawingArea()
        # The canvas is the point of the pane, so it takes the room that is
        # going spare instead of sitting in a 320px box with white all round it,
        # and centres itself in whatever it gets (see _canvas_geom).
        self._spr_canvas.set_size_request(240, 240)
        self._spr_canvas.set_hexpand(True)
        self._spr_canvas.set_vexpand(True)
        self._spr_canvas.set_can_focus(True)
        self._spr_canvas.set_tooltip_text(
            "%s %s" % (_t("Click to paint."), _t(KEYS_HINT)))
        self._spr_canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                                    | Gdk.EventMask.BUTTON_RELEASE_MASK
                                    | Gdk.EventMask.BUTTON1_MOTION_MASK
                                    | Gdk.EventMask.BUTTON3_MOTION_MASK)
        self._spr_canvas.connect("draw", self._draw_sprite_canvas)
        self._spr_canvas.connect("button-press-event", self._on_sprite_paint)
        self._spr_canvas.connect("motion-notify-event", self._on_sprite_paint)
        self._spr_canvas.connect("button-release-event", self._on_paint_release)
        self._spr_canvas.connect("key-press-event", self._on_canvas_key)
        self._spr_canvas.connect("focus-in-event", self._redraw_cb)
        self._spr_canvas.connect("focus-out-event", self._redraw_cb)
        body.pack_start(self._spr_canvas, True, True, 0)
        self._pane_focus["sprite"] = self._spr_canvas

        fr = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        fr.set_size_request(130, -1)
        fr.pack_start(_eyebrow(_t("FRAMES")), False, False, 0)
        fhead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        # All five drawn icons, all with tooltips naming what they do and the key
        # that does it. Reordering frames was impossible before, which for an
        # animation is not a missing nicety but a missing feature.
        for icon, cb, tip in (
                ("plus", self._add_frame, _t("Add a blank frame")),
                ("duplicate", self._dup_frame, _t("Duplicate this frame")),
                ("up", self._frame_up, _acc("Move Up", "Alt+Up")),
                ("down", self._frame_down, _acc("Move Down", "Alt+Down")),
                ("trash", self._del_frame, _t("Delete this frame"))):
            fhead.pack_start(_icon_button(icon, tip, cb, cls="iconbtn", size=12),
                             False, False, 0)
        fr.pack_start(fhead, False, False, 0)
        fsw = Gtk.ScrolledWindow()
        fsw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        fsw.set_vexpand(True)
        self._frame_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        fsw.add(self._frame_list)
        fr.pack_start(fsw, True, True, 0)
        body.pack_start(fr, False, False, 0)
        return pane

    def _seg_apply(self, pairs, chosen):
        """Show `chosen` as the one that is on, across a row of pick-one buttons.

        Gtk.ToggleButton.set_active emits BOTH "toggled" AND "clicked", so a
        setter that updates its siblings from inside a "clicked" handler calls
        ITSELF for every button in the row — two buttons ping-pong until the
        recursion limit and the editor wedges. Blocking each button's own handler
        while the row is updated is the fix: not a guard that swallows the second
        call, but no second call at all."""
        for _key, b in pairs:
            hid = getattr(b, "_sdk_hid", None)
            if hid is not None:
                b.handler_block(hid)
        try:
            for key, b in pairs:
                on = (key == chosen)
                if b.get_active() != on:
                    b.set_active(on)
                ctx = b.get_style_context()
                if on:
                    ctx.add_class("on")
                else:
                    ctx.remove_class("on")
                img = getattr(b, "tool_image", None)
                if img is not None:
                    # The pixbuf carries its own colour, so the chosen tool's
                    # icon is re-rendered in paper to sit on the ink field.
                    nbicons.set_image(img,
                        b.tool_icon, 14, "#FCFBF8" if on else MUTED)
        finally:
            for _key, b in pairs:
                hid = getattr(b, "_sdk_hid", None)
                if hid is not None:
                    b.handler_unblock(hid)

    def _tool_bar(self):
        """The painting tools, as a row of pictures that show which one is on.

        They were three radio buttons in a row of labels, indistinguishable from
        the settings beside them; and "which tool am I holding" is the one thing
        a pixel editor must never leave you guessing about. The chosen one is
        drawn in reverse (ink field, paper icon) so it reads without relying on
        colour at all, and each carries its shortcut in its tooltip.

        Built once per pixel editor, and both copies are kept in step — the tool
        is one piece of state, so the sprite pane and the tile pane must never
        disagree about which one is held."""
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        seg.get_style_context().add_class("toolseg")
        for tid, icon, label, key in TOOLS:
            b = Gtk.ToggleButton()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("toolbtn")
            img = nbicons.image(icon, 14, MUTED)
            b.add(img)
            b.tool_icon = icon
            b.tool_image = img
            b.set_tooltip_text(_acc(label, key))
            b._sdk_hid = b.connect("clicked", lambda _w, t=tid: self._set_tool(t))
            self._tool_btns.setdefault(tid, []).append(b)
            seg.pack_start(b, False, False, 0)
        return seg

    # A Game Boy Advance sprite may hold 15 colours plus transparent, and all the
    # tile sets in a game share one set of 15. Going over is one click away in a
    # palette of 33, and the cost used to be paid silently in the ROM: the 16th
    # colour of a sprite came out as a HOLE in the picture, and a 16th tile colour
    # came out as some other colour. The limit is real hardware, so the editor's
    # job is to make it visible while the person is drawing.
    COLOUR_LIMIT = 15

    def _colours_used(self):
        """(count, limit) for whatever is open, or None where it does not apply.

        A sprite is counted across ALL its frames, because they share one
        hardware palette bank; tiles are counted across every tile set, because
        the background palette is shared by all of them."""
        if self._sel and self._sel[0] == "sprite":
            s = self._cur_sprite()
            if not s:
                return None
            seen = set()
            for fr in s.get("frames") or []:
                for c in fr:
                    if c != TRANSPARENT:
                        seen.add(c)
            return len(seen), self.COLOUR_LIMIT
        if self._sel and self._sel[0] == "tileset":
            seen = set()
            for tile in self._all_tiles():
                for c in tile:
                    if c != TRANSPARENT:
                        seen.add(c)
            return len(seen), self.COLOUR_LIMIT
        return None

    def _update_colour_count(self):
        """Keep the "colours used" line honest after every stroke."""
        used = self._colours_used()
        for lbl in self._colour_counts:
            if used is None:
                lbl.set_text("")
                continue
            n, limit = used
            ctx = lbl.get_style_context()
            if n > limit:
                # The one place in this app where red is right: the artwork will
                # not come out of the compiler looking like what is on screen.
                ctx.add_class("over")
                lbl.set_text(_t("%d colours — %d too many") % (n, n - limit))
                lbl.set_tooltip_text(
                    _t("A Game Boy Advance can show 15 colours here. The extra "
                       "ones will come out wrong in the game — take some out."))
            else:
                ctx.remove_class("over")
                lbl.set_text(_t("%d of %d colours") % (n, limit))
                lbl.set_tooltip_text(
                    _t("A Game Boy Advance can show 15 colours here."))

    def _paint_column(self):
        """The current colour, said in three ways, over the palette.

        Built once per pixel editor. Every widget it makes that has to be kept
        (the well, the colour name, the count) is remembered in a LIST, because
        the sprite pane and the tile pane each get their own copy and both have
        to stay true."""
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.set_size_request(136, -1)
        col.pack_start(_eyebrow(_t("COLOUR")), False, False, 0)
        well = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        swatch = Gtk.DrawingArea()
        swatch.set_size_request(30, 30)
        swatch.set_valign(Gtk.Align.CENTER)
        swatch.connect("draw", self._draw_colour_well)
        well.pack_start(swatch, False, False, 0)
        # The NAME as well as the swatch: a swatch alone is colour used as the
        # only signal, which fails anyone who cannot separate two of them.
        name = Gtk.Label(xalign=0)
        name.get_style_context().add_class("colourname")
        name.set_valign(Gtk.Align.CENTER)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        self._colour_names.append(name)
        well.pack_start(name, True, True, 0)
        col.pack_start(well, False, False, 0)
        count = Gtk.Label(xalign=0)
        count.get_style_context().add_class("colourcount")
        count.set_line_wrap(True)
        count.set_max_width_chars(20)
        self._colour_counts.append(count)
        col.pack_start(count, False, False, 0)
        col.pack_start(self._palette_flow(), True, True, 0)
        self._set_paint(self._paint_color)
        return col

    def _draw_colour_well(self, w, cr):
        aw = w.get_allocated_width()
        ah = w.get_allocated_height()
        self._paint_swatch(cr, aw, ah, self._paint_color)
        cr.set_source_rgb(0.10, 0.10, 0.09)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, aw - 1, ah - 1)
        cr.stroke()
        return False

    def _paint_swatch(self, cr, aw, ah, color):
        """A colour, or the erase pattern, filling aw x ah."""
        if color == TRANSPARENT:
            for j in range(int(ah / 5) + 1):
                for i in range(int(aw / 5) + 1):
                    shade = 0.97 if (i + j) % 2 == 0 else 0.90
                    cr.set_source_rgb(shade, shade, shade)
                    cr.rectangle(i * 5, j * 5, 5, 5)
                    cr.fill()
        else:
            cr.set_source_rgb(*self._c15(color))
            cr.rectangle(0, 0, aw, ah)
            cr.fill()

    def _draw_swatch(self, w, cr, color):
        a = w.get_allocated_width()
        b = w.get_allocated_height()
        self._paint_swatch(cr, a, b, color)
        # Ink, not red: red in this OS means an alert or today, and a selection
        # ring is neither. A double ring (ink outside, paper inside) stays
        # visible on top of every one of the 15-bit colours underneath it.
        if color == self._paint_color:
            cr.set_line_width(2)
            cr.set_source_rgb(0.10, 0.10, 0.09)
            cr.rectangle(1, 1, a - 2, b - 2)
            cr.stroke()
            cr.set_source_rgba(1, 1, 1, 0.85)
            cr.set_line_width(1)
            cr.rectangle(2.5, 2.5, a - 5, b - 5)
            cr.stroke()
        return False

    def _c15(self, color):
        return ((color & 31) / 31.0, ((color >> 5) & 31) / 31.0,
                ((color >> 10) & 31) / 31.0)

    def _set_paint(self, color):
        self._paint_color = color
        name = next((n for n, c in PALETTE if c == color), None)
        for lbl in self._colour_names:
            lbl.set_text(_t(name) if name else "")
        self.queue_draw()

    def _redraw_cb(self, w, _ev=None):
        """Repaint on focus in/out, so the focus ring appears and disappears."""
        w.queue_draw()
        return False

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
        self.undo.checkpoint(_t("Size"))
        w, h = (int(v) for v in aid.split("x"))
        ow, oh = s.get("w", 16), s.get("h", 16)
        s["frames"] = [self._resize_frame(fr, ow, oh, w, h)
                       for fr in (s.get("frames") or [[TRANSPARENT] * (ow * oh)])]
        s["w"], s["h"] = w, h
        s["ox"], s["oy"] = w // 2, h // 2
        self._spr_cur = [min(self._spr_cur[0], w - 1), min(self._spr_cur[1], h - 1)]
        self._save_autosave()
        self._render_frame_list()
        self._render_tree()
        self._update_head("sprite")
        self._spr_canvas.queue_draw()
        self.undo.commit()

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
        """Choose a tool and say so on all four buttons.

        Safe to call from a button's own "clicked" handler: set_active emits
        "toggled", which nothing here listens to, so it cannot re-enter."""
        self._spr_tool = tool
        self._seg_apply([(tid, b) for tid, btns in self._tool_btns.items()
                         for b in btns], tool)

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

    def _frame_done(self, label):
        """Finish a change to the frame list: save, redraw everything that shows
        a frame count or a picture, and land one undo step."""
        self._save_autosave()
        self._render_frame_list()
        self._render_tree()
        self._update_head("sprite")
        self._update_colour_count()
        self._spr_canvas.queue_draw()
        self.undo.commit()
        self._flash(label)

    def _add_frame(self):
        s = self._cur_sprite()
        if not s:
            return
        self.undo.checkpoint(_t("Add a blank frame"))
        s["frames"].append([TRANSPARENT] * (s.get("w", 16) * s.get("h", 16)))
        self._sel_frame = len(s["frames"]) - 1
        self._frame_done("")

    def _dup_frame(self):
        s = self._cur_sprite()
        if not s or not s.get("frames"):
            return
        self.undo.checkpoint(_t("Duplicate"))
        s["frames"].insert(self._sel_frame + 1, list(s["frames"][self._sel_frame]))
        self._sel_frame += 1
        self._frame_done("")

    def _del_frame(self):
        s = self._cur_sprite()
        if not s or len(s.get("frames", [])) <= 1:
            # The last frame is the sprite itself, so there is nothing to delete
            # — which the button used to signal by doing nothing at all.
            self._flash(_t("A sprite needs at least one frame."))
            return
        self.undo.checkpoint(_t("Delete"))
        del s["frames"][self._sel_frame]
        self._sel_frame = max(0, self._sel_frame - 1)
        self._frame_done("")

    def _frame_up(self):
        self._move_frame(-1)

    def _frame_down(self):
        self._move_frame(1)

    def _move_frame(self, delta):
        """Reorder the animation. A flip-book you cannot reorder is a flip-book
        you have to delete and redraw to fix."""
        s = self._cur_sprite()
        if not s:
            return
        frames = s.get("frames") or []
        i = self._sel_frame
        j = i + delta
        if not (0 <= i < len(frames) and 0 <= j < len(frames)):
            return
        self.undo.checkpoint(_t("Move Up") if delta < 0 else _t("Move Down"))
        frames[i], frames[j] = frames[j], frames[i]
        self._sel_frame = j
        self._frame_done("")

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
            da.set_size_request(104, 52)
            da.connect("draw", self._draw_frame_thumb, i)
            # A focusable button, not a bare EventBox: the frames of an animation
            # have to be reachable and choosable with the keyboard like anything
            # else, and a button is what gives them a focus ring for free.
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("framebtn")
            if i == self._sel_frame:
                btn.get_style_context().add_class("on")
            btn.add(da)
            btn.set_tooltip_text("%s %d" % (_t("Frame"), i + 1))
            btn.connect("clicked", lambda _w, ix=i: self._select_frame(ix))
            self._frame_list.pack_start(btn, False, False, 0)
        self._frame_list.show_all()

    def _draw_frame_thumb(self, w, cr, i):
        aw = w.get_allocated_width()
        ah = w.get_allocated_height()
        s = self._cur_sprite()
        cr.set_source_rgb(0.99, 0.98, 0.97)
        cr.rectangle(0, 0, aw, ah)
        cr.fill()
        if not s or i >= len(s.get("frames", [])):
            return False
        sw, sh = s.get("w", 16), s.get("h", 16)
        px = s["frames"][i]
        cell = min((aw - 22.0) / max(1, sw), (ah - 8.0) / max(1, sh))
        ox = 20 + (aw - 22 - sw * cell) / 2.0
        oy = (ah - sh * cell) / 2.0
        for y in range(sh):
            for x in range(sw):
                c = px[y * sw + x] if y * sw + x < len(px) else TRANSPARENT
                if c == TRANSPARENT:
                    continue
                cr.set_source_rgb(*self._c15(c))
                cr.rectangle(ox + x * cell, oy + y * cell, cell + 0.7, cell + 0.7)
                cr.fill()
        # The frame's NUMBER, so the strip can be talked about ("frame 3") and
        # the order is legible without counting down the column.
        cr.set_source_rgb(0.43, 0.41, 0.37)
        _show_text(cr, 5, ah / 2.0 + 4, str(i + 1), 10.5,
                   bold=(i == self._sel_frame))
        cr.set_source_rgb(0.79, 0.77, 0.71)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, aw - 1, ah - 1)
        cr.stroke()
        if i == self._sel_frame:
            cr.set_source_rgb(0.10, 0.10, 0.09)
            cr.set_line_width(2)
            cr.rectangle(1, 1, aw - 2, ah - 2)
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
    def _canvas_geom(widget, sw, sh):
        """(cell, ox, oy) for a sw x sh pixel grid centred in `widget`.

        Whole-number cells wherever there is room for them, so a painted pixel
        is a square of identical size to its neighbours rather than one that is
        a fraction wider because of where the rounding fell."""
        aw = widget.get_allocated_width()
        ah = widget.get_allocated_height()
        cell = min(aw / max(1, sw), ah / max(1, sh))
        if cell >= 3:
            cell = float(int(cell))
        cell = max(1.0, cell)
        return cell, (aw - sw * cell) / 2.0, (ah - sh * cell) / 2.0

    def _draw_pixel_grid(self, w, cr, px, sw, sh, cursor=None):
        """The shared body of both pixel canvases: a centred, checkerboarded,
        gridded pixel field with a keyboard cursor.

        The sprite canvas and the tile canvas had two copies of this, drawn from
        the top-left corner of their allocation, which is why one of them looked
        stretched whenever the pane was not exactly square."""
        aw = w.get_allocated_width()
        ah = w.get_allocated_height()
        cr.set_source_rgb(0.99, 0.98, 0.97)
        cr.rectangle(0, 0, aw, ah)
        cr.fill()
        if px is None:
            return None
        cell, ox, oy = self._canvas_geom(w, sw, sh)
        cr.save()
        cr.translate(ox, oy)
        for j in range(sh):
            for i in range(sw):
                c = px[j * sw + i] if j * sw + i < len(px) else TRANSPARENT
                if c == TRANSPARENT:
                    shade = 0.985 if (i + j) % 2 == 0 else 0.945
                    cr.set_source_rgb(shade, shade, shade)
                else:
                    cr.set_source_rgb(*self._c15(c))
                cr.rectangle(i * cell, j * cell, cell + 0.7, cell + 0.7)
                cr.fill()
        # A grid at three pixels a cell is a grey wash, not a grid.
        if cell >= 7:
            cr.set_source_rgba(0, 0, 0, 0.10)
            cr.set_line_width(1)
            for k in range(sw + 1):
                cr.move_to(k * cell, 0)
                cr.line_to(k * cell, sh * cell)
            for k in range(sh + 1):
                cr.move_to(0, k * cell)
                cr.line_to(sw * cell, k * cell)
            cr.stroke()
        cr.set_source_rgb(0.66, 0.64, 0.58)
        cr.set_line_width(1)
        cr.rectangle(-0.5, -0.5, sw * cell + 1, sh * cell + 1)
        cr.stroke()
        # The keyboard cursor, drawn ink-over-paper so it is visible on top of
        # any of the 32768 colours it may be sitting on.
        if cursor is not None and _focused(w):
            cx, cy = cursor
            cr.set_line_width(2)
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.rectangle(cx * cell - 1, cy * cell - 1, cell + 2, cell + 2)
            cr.stroke()
            cr.set_source_rgb(0.10, 0.10, 0.09)
            cr.rectangle(cx * cell + 1, cy * cell + 1, cell - 2, cell - 2)
            cr.stroke()
        cr.restore()
        if _focused(w):
            cr.set_source_rgb(0.10, 0.10, 0.09)
            cr.set_line_width(2)
            cr.rectangle(1, 1, aw - 2, ah - 2)
            cr.stroke()
        return cell, ox, oy

    def _draw_sprite_canvas(self, w, cr):
        px, sw, sh = self._spr_edit_px()
        cur = self._spr_cur if self._spr_play is None else None
        self._draw_pixel_grid(w, cr, px, sw, sh, cur)
        return False

    def _paint_at(self, frame, sw, sh, i, j, erase=False):
        """Apply the current tool at pixel (i, j). True when something changed."""
        idx = j * sw + i
        if idx >= len(frame):
            return False
        want = TRANSPARENT if (erase or self._spr_tool == "erase") \
            else self._paint_color
        if self._spr_tool == "pick" and not erase:
            self._set_paint(frame[idx])
            return False
        if self._spr_tool == "fill" and not erase:
            if frame[idx] == want:
                return False
            self._flood_fill(frame, sw, sh, i, j, want)
            return True
        if frame[idx] == want:
            return False
        frame[idx] = want
        return True

    def _paint_pointer(self, canvas, ev, frame, sw, sh):
        """Map a pointer event and paint it, interpolating sparse drag events.

        GTK normally delivers motion here only for a held button because the
        canvases request BUTTON[13]_MOTION_MASK.  Checking the state as well is
        important for synthetic/tablet events and prevents a late queued motion
        from leaving an accidental pixel after release.
        """
        motion = ev.type == Gdk.EventType.MOTION_NOTIFY
        held = ev.state & (Gdk.ModifierType.BUTTON1_MASK |
                           Gdk.ModifierType.BUTTON3_MASK)
        if motion and not held:
            self._paint_stroke = None
            return None, False
        cell, ox, oy = self._canvas_geom(canvas, sw, sh)
        i = int((ev.x - ox) // cell)
        j = int((ev.y - oy) // cell)
        if not (0 <= i < sw and 0 <= j < sh):
            return None, False
        erase = bool(getattr(ev, "button", 1) == 3 or
                     (ev.state & Gdk.ModifierType.BUTTON3_MASK))
        points = [(i, j)]
        previous = self._paint_stroke
        if (motion and previous and previous[0] is canvas and
                previous[3] == erase and self._spr_tool in ("pen", "erase")):
            x0, y0 = previous[1], previous[2]
            dx, dy = i - x0, j - y0
            steps = max(abs(dx), abs(dy))
            if steps:
                points = [(round(x0 + dx * n / steps),
                           round(y0 + dy * n / steps))
                          for n in range(1, steps + 1)]
        self._paint_stroke = (canvas, i, j, erase)
        changed = False
        for x, y in points:
            changed = self._paint_at(frame, sw, sh, x, y, erase) or changed
        return (i, j), changed

    def _on_paint_release(self, _canvas, _ev):
        """End one pointer stroke and land its debounced undo snapshot."""
        self._paint_stroke = None
        self.undo.flush()
        return True

    def _on_sprite_paint(self, w, ev):
        if self._spr_play is not None:      # don't edit while previewing
            return False
        w.grab_focus()
        s = self._cur_sprite()
        frame = self._cur_frame()
        if s is None or frame is None:
            return False
        sw, sh = s.get("w", 16), s.get("h", 16)
        point, changed = self._paint_pointer(w, ev, frame, sw, sh)
        if point is None:
            return True
        i, j = point
        self._spr_cur = [i, j]
        if changed:
            self.undo.touch()
            self._save_autosave()
            self._render_tree()
            self._update_colour_count()
        w.queue_draw()
        if ev.type != Gdk.EventType.MOTION_NOTIFY:
            self._render_frame_list()
        return True

    def _on_canvas_key(self, w, ev):
        """Paint with the keyboard.

        A pixel editor you can only use with a mouse is a pixel editor half the
        people who might use it cannot use at all. Arrows move the cursor, Space
        or Return paints with the current tool, Shift+arrow draws as it moves,
        Backspace and Delete clear a pixel, and the tool letters (see TOOLS)
        switch tool without leaving the canvas."""
        if w is self._spr_canvas:
            s = self._cur_sprite()
            frame = self._cur_frame()
            if s is None or frame is None:
                return False
            sw, sh = s.get("w", 16), s.get("h", 16)
            cur = self._spr_cur
        else:
            frame = self._cur_tile()
            if frame is None:
                return False
            sw = sh = ts_size(self._cur_tileset())
            cur = self._tile_cur
        step = {Gdk.KEY_Left: (-1, 0), Gdk.KEY_Right: (1, 0),
                Gdk.KEY_Up: (0, -1), Gdk.KEY_Down: (0, 1)}.get(ev.keyval)
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        changed = False
        if step is not None:
            cur[0] = max(0, min(sw - 1, cur[0] + step[0]))
            cur[1] = max(0, min(sh - 1, cur[1] + step[1]))
            if shift:
                changed = self._paint_at(frame, sw, sh, cur[0], cur[1])
        elif ev.keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            changed = self._paint_at(frame, sw, sh, cur[0], cur[1])
        elif ev.keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete):
            changed = self._paint_at(frame, sw, sh, cur[0], cur[1], erase=True)
        elif ev.keyval in (Gdk.KEY_Home,):
            cur[0] = cur[1] = 0
        elif ev.keyval in (Gdk.KEY_End,):
            cur[0], cur[1] = sw - 1, sh - 1
        else:
            return False
        if changed:
            self.undo.touch()
            self._save_autosave()
            self._render_tree()
            self._update_colour_count()
            if w is self._spr_canvas:
                self._render_frame_list()
            else:
                self._render_tile_list()
        w.queue_draw()
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

        It has to SCROLL: a bare FlowBox reports a minimum width of one column,
        so GTK then asks for one row per colour — taller than a laptop panel,
        which pushed the whole app off the bottom of the screen. Pinning it to a
        fixed four-wide column that scrolls keeps the editors laying out at any
        window size.

        Every swatch is a Gtk.Button rather than an EventBox, which is what makes
        the palette reachable at all without a mouse — Tab walks into it and the
        arrow keys walk along it — and 28px square rather than 26 so it is a
        target on a 1024-wide panel."""
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_size_request(130, 120)
        sw.set_propagate_natural_height(True)
        sw.set_valign(Gtk.Align.START)
        pal = Gtk.FlowBox()
        pal.set_min_children_per_line(4)
        pal.set_max_children_per_line(4)
        pal.set_selection_mode(Gtk.SelectionMode.NONE)
        pal.set_valign(Gtk.Align.START)
        pal.set_row_spacing(2)
        pal.set_column_spacing(2)
        for name, color in PALETTE:
            da = Gtk.DrawingArea()
            da.set_size_request(24, 24)
            da.connect("draw", self._draw_swatch, color)
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("swatch")
            b.add(da)
            b.set_tooltip_text(_t(name))
            b.connect("clicked", lambda _w, c=color: self._set_paint(c))
            pal.add(b)
        sw.add(pal)
        return sw

    # ================= tile set editor =================
    def _tileset_pane(self):
        pane, tools, body = self._pane("tileset", "Tile set")
        tools.pack_start(Gtk.Label(label=_t("Tool")), False, False, 0)
        tools.pack_start(self._tool_bar(), False, False, 0)
        tools.pack_start(Gtk.Label(label=_t("Size")), False, False, 0)
        self._ts_size_combo = Gtk.ComboBoxText()
        for n in TILE_SIZES:
            self._ts_size_combo.append_text("%d × %d" % (n, n))
        self._ts_size_combo.set_tooltip_text(
            _t("Pixel size of every tile in this set"))
        self._ts_size_combo.connect("changed", self._on_tileset_size)
        tools.pack_start(self._ts_size_combo, False, False, 0)

        # Which tiles are walls and floors. The runtime has read this since it
        # was written; nothing could set it, so tile collision did nothing in
        # any built game.
        self._ts_solid = Gtk.CheckButton(label=_t("Solid"))
        self._ts_solid.set_margin_start(14)
        self._ts_solid.set_tooltip_text(
            _t("Objects cannot pass through this tile"))
        self._ts_solid.connect("toggled", self._on_tile_solid)
        tools.pack_start(self._ts_solid, False, False, 0)

        self._ts_auto = Gtk.CheckButton(label=_t("Auto-tile"))
        self._ts_auto.set_margin_start(10)
        self._ts_auto.set_tooltip_text(
            _t("This tile and the 15 after it are one terrain, picked to fit "
               "its neighbours"))
        self._ts_auto.connect("toggled", self._on_tile_auto)
        tools.pack_start(self._ts_auto, False, False, 0)

        hint = Gtk.Label(
            xalign=0,
            label=_t("A room places these in Tiles mode."))
        hint.set_line_wrap(True)
        hint.set_max_width_chars(52)
        hint.set_margin_start(14)
        hint.get_style_context().add_class("panehint")
        tools.pack_start(hint, True, True, 0)

        body.pack_start(self._paint_column(), False, False, 0)
        self._tile_canvas = Gtk.DrawingArea()
        self._tile_canvas.set_size_request(200, 200)
        self._tile_canvas.set_hexpand(True)
        self._tile_canvas.set_vexpand(True)
        self._tile_canvas.set_can_focus(True)
        self._tile_canvas.set_tooltip_text(
            "%s %s" % (_t("Click to paint."), _t(KEYS_HINT)))
        self._tile_canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                                     | Gdk.EventMask.BUTTON_RELEASE_MASK
                                     | Gdk.EventMask.BUTTON1_MOTION_MASK
                                     | Gdk.EventMask.BUTTON3_MOTION_MASK)
        self._tile_canvas.connect("draw", self._draw_tile_canvas)
        self._tile_canvas.connect("button-press-event", self._on_tile_paint)
        self._tile_canvas.connect("motion-notify-event", self._on_tile_paint)
        self._tile_canvas.connect("button-release-event", self._on_paint_release)
        self._tile_canvas.connect("key-press-event", self._on_canvas_key)
        self._tile_canvas.connect("focus-in-event", self._redraw_cb)
        self._tile_canvas.connect("focus-out-event", self._redraw_cb)
        body.pack_start(self._tile_canvas, True, True, 0)
        self._pane_focus["tileset"] = self._tile_canvas

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.set_size_request(150, -1)
        right.pack_start(_eyebrow(_t("TILES")), False, False, 0)
        addrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        for icon, cb, tip in (("plus", self._add_tile, _t("Add Tile")),
                              ("duplicate", self._dup_tile, _t("Duplicate")),
                              ("trash", self._del_tile, _t("Delete"))):
            addrow.pack_start(_icon_button(icon, tip, cb, cls="iconbtn", size=12),
                              False, False, 0)
        right.pack_start(addrow, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._tile_list = Gtk.FlowBox()
        self._tile_list.set_max_children_per_line(3)
        self._tile_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._tile_list.set_valign(Gtk.Align.START)
        self._tile_list.set_row_spacing(4)
        self._tile_list.set_column_spacing(4)
        scroll.add(self._tile_list)
        right.pack_start(scroll, True, True, 0)
        body.pack_start(right, False, False, 0)
        return pane

    def _load_tileset_editor(self):
        self._sel_tile = 0
        self._tile_cur = [0, 0]
        self._sync_tileset_size()      # the combo shows THIS set's size
        self._sync_tile_solid()
        self._sync_tile_auto()
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
            da.set_size_request(34, 34)
            da.connect("draw", self._draw_tile_thumb, i)
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("framebtn")
            if i == self._sel_tile:
                btn.get_style_context().add_class("on")
            btn.add(da)
            flags = self._tile_solid_flags(ts)
            if i < len(flags) and flags[i]:
                btn.get_style_context().add_class("solidtile")
                btn.set_tooltip_text("%s \u00b7 %s"
                                     % (_t("Tile %d") % (i + 1), _t("Solid")))
            else:
                btn.set_tooltip_text(_t("Tile %d") % (i + 1))
            btn.connect("clicked", lambda _w, ix=i: self._select_tile(ix))
            self._tile_list.add(btn)
        self._tile_list.show_all()

    def _select_tile(self, i):
        self._sel_tile = i
        self._sync_tile_solid()
        self._sync_tile_auto()
        self._sync_tile_auto()
        self._render_tile_list()
        self._tile_canvas.queue_draw()

    def _tile_solid_flags(self, ts=None):
        """The solid list for a tile set, grown to match its tiles.

        Grown here rather than assumed: a tile added after the flag existed has
        no entry, and reading past the end would either raise or silently treat
        a real tile as not solid."""
        ts = ts if ts is not None else self._sel_res()
        if not isinstance(ts, dict):
            return []
        n = len(ts.get("tiles") or [])
        flags = ts.get("solid")
        if not isinstance(flags, list):
            flags = []
        if len(flags) != n:
            flags = ([bool(v) for v in flags] + [False] * n)[:n]
            ts["solid"] = flags
        return flags

    def _sync_tile_solid(self):
        if not self._sel or self._sel[0] != "tileset":
            return
        flags = self._tile_solid_flags()
        self._suspend = True
        try:
            self._ts_solid.set_active(
                bool(flags[self._sel_tile])
                if 0 <= self._sel_tile < len(flags) else False)
        finally:
            self._suspend = False

    def _sync_tile_auto(self):
        if not self._sel or self._sel[0] != "tileset":
            return
        ts = self._sel_res() or {}
        self._suspend = True
        try:
            self._ts_auto.set_active(ts.get("auto_base") == self._sel_tile)
        finally:
            self._suspend = False

    def _on_tile_auto(self, btn):
        if self._suspend or not self._sel or self._sel[0] != "tileset":
            return
        ts = self._sel_res()
        if not ts:
            return
        if not btn.get_active():
            self.undo.checkpoint(_t("Auto-tile"))
            ts.pop("auto_base", None)
            self._save_autosave()
            self._render_tile_list()
            self.undo.commit()
            return
        have = len(ts.get("tiles") or []) - self._sel_tile
        if have < self.AUTO_VARIANTS:
            # Refuse and say the number. Accepting it would declare a run that
            # _auto_runs quietly ignores, which is a setting that reads as on
            # and does nothing.
            self._suspend = True
            try:
                btn.set_active(False)
            finally:
                self._suspend = False
            self._flash(_t("An auto-tile needs %d tiles from here; there are %d")
                        % (self.AUTO_VARIANTS, max(0, have)))
            return
        self.undo.checkpoint(_t("Auto-tile"))
        ts["auto_base"] = self._sel_tile
        self._save_autosave()
        self._render_tile_list()
        self.undo.commit()

    def _on_tile_solid(self, btn):
        if self._suspend or not self._sel or self._sel[0] != "tileset":
            return
        flags = self._tile_solid_flags()
        if not (0 <= self._sel_tile < len(flags)):
            return
        self.undo.checkpoint(_t("Solid"))
        flags[self._sel_tile] = btn.get_active()
        self._save_autosave()
        self._render_tile_list()
        self.undo.commit()

    def _tile_done(self):
        self._save_autosave()
        self._render_tile_list()
        self._render_tree()
        self._update_head("tileset")
        self._update_colour_count()
        self._tile_canvas.queue_draw()
        self.undo.commit()

    def _on_tileset_size(self, combo):
        """Change the pixel size of every tile in the open set.

        Artwork is REPOSITIONED, never resampled: a tile keeps its top-left
        corner, growing pads with transparent and shrinking crops. Resampling
        pixel art invents pixels the artist did not draw, which is the one thing
        a pixel editor must not do.

        Shrinking throws away the pixels outside the new square, so it asks
        first -- and it is a single undo step either way."""
        i = combo.get_active()                 # index, never get_active_text():
        if i < 0:                              # that returns the TRANSLATION
            return
        ts = self._cur_tileset()
        if not ts:
            return
        new = TILE_SIZES[i]
        old = ts_size(ts)
        if new == old or getattr(self, "_ts_size_syncing", False):
            return
        if new < old and not self._confirm(
                _t("Make the tiles smaller?"),
                _t("Every tile in this set is cropped to %(new)d × %(new)d "
                   "pixels. Anything drawn outside that square is lost.")
                % {"new": new},
                _t("Crop")):
            self._sync_tileset_size()          # put the combo back
            return
        self.undo.checkpoint(_t("Tile Size"))
        out = []
        for t in ts.get("tiles") or []:
            px = list(t or [])
            grid = [[TRANSPARENT] * new for _ in range(new)]
            for j in range(min(old, new)):
                for i2 in range(min(old, new)):
                    k = j * old + i2
                    if k < len(px):
                        grid[j][i2] = px[k]
            out.append([c for row in grid for c in row])
        # Rooms index ONE combined list of every set's hardware tiles, so
        # changing this set's size changes how many entries it occupies and
        # shifts every LATER set along — the same corruption _forget_tiles
        # exists to prevent on delete, arriving by a different door. Renumber
        # before the size actually changes, while the old geometry is still
        # readable.
        self._resize_tiles(ts, old, new)
        ts["size"] = new
        ts["tiles"] = out or [[TRANSPARENT] * (new * new)]
        self._sel_tile = min(self._sel_tile, max(0, len(ts["tiles"]) - 1))
        self._tile_cur = [0, 0]
        self._tile_pb_cache.clear()
        self._tile_done()

    def _sync_tileset_size(self):
        """Point the size combo at the open set without firing a resize."""
        combo = getattr(self, "_ts_size_combo", None)
        ts = self._cur_tileset()
        if combo is None or not ts:
            return
        self._ts_size_syncing = True
        try:
            combo.set_active(TILE_SIZES.index(ts_size(ts)))
        finally:
            self._ts_size_syncing = False

    def _add_tile(self):
        ts = self._cur_tileset()
        if not ts:
            return
        self.undo.checkpoint(_t("Add Tile"))
        n = ts_size(ts)
        ts.setdefault("tiles", []).append([TRANSPARENT] * (n * n))
        self._sel_tile = len(ts["tiles"]) - 1
        self._tile_done()

    def _dup_tile(self):
        ts = self._cur_tileset()
        if not ts or not ts.get("tiles"):
            return
        self.undo.checkpoint(_t("Duplicate"))
        ts["tiles"].insert(self._sel_tile + 1, list(ts["tiles"][self._sel_tile]))
        self._sel_tile += 1
        self._tile_done()

    def _del_tile(self):
        ts = self._cur_tileset()
        if not ts or len(ts.get("tiles", [])) <= 1:
            self._flash(_t("A tile set needs at least one tile."))
            return
        self.undo.checkpoint(_t("Delete"))
        del ts["tiles"][self._sel_tile]
        self._sel_tile = max(0, self._sel_tile - 1)
        self._tile_done()

    def _draw_tile_grid(self, cr, tile, cell, checker, n=8):
        """Paint an n x n tile at `cell` pixels per tile-pixel (n = 8/16/32)."""
        for j in range(n):
            for i in range(n):
                c = tile[j * n + i] if j * n + i < len(tile) else TRANSPARENT
                if c == TRANSPARENT:
                    if not checker:
                        continue
                    shade = 0.985 if (i + j) % 2 == 0 else 0.945
                    cr.set_source_rgb(shade, shade, shade)
                else:
                    cr.set_source_rgb(*self._c15(c))
                cr.rectangle(i * cell, j * cell, cell + 0.7, cell + 0.7)
                cr.fill()

    def _draw_tile_thumb(self, w, cr, i):
        aw = w.get_allocated_width()
        ah = w.get_allocated_height()
        ts = self._cur_tileset()
        cr.set_source_rgb(0.99, 0.98, 0.97)
        cr.rectangle(0, 0, aw, ah)
        cr.fill()
        if not ts or i >= len(ts.get("tiles", [])):
            return False
        n = ts_size(ts)
        cell = (min(aw, ah) - 4) / float(n)
        cr.save()
        cr.translate((aw - cell * n) / 2.0, (ah - cell * n) / 2.0)
        self._draw_tile_grid(cr, ts["tiles"][i], cell, False, n)
        cr.restore()
        cr.set_source_rgb(0.79, 0.77, 0.71)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, aw - 1, ah - 1)
        cr.stroke()
        if i == self._sel_tile:
            cr.set_source_rgb(0.10, 0.10, 0.09)
            cr.set_line_width(2)
            cr.rectangle(1, 1, aw - 2, ah - 2)
            cr.stroke()
        return False

    def _draw_tile_canvas(self, w, cr):
        n = ts_size(self._cur_tileset())
        self._draw_pixel_grid(w, cr, self._cur_tile(), n, n, self._tile_cur)
        return False

    def _on_tile_paint(self, w, ev):
        w.grab_focus()
        tile = self._cur_tile()
        if tile is None:
            return False
        n = ts_size(self._cur_tileset())
        point, changed = self._paint_pointer(w, ev, tile, n, n)
        if point is None:
            return True
        i, j = point
        self._tile_cur = [i, j]
        if changed:
            self.undo.touch()
            self._save_autosave()
            self._render_tile_list()
            self._render_tree()
            self._update_colour_count()
        w.queue_draw()
        return True

    def _tile_entries(self):
        """The AUTHORED tiles: (base, size, picture) where `base` is the 1-based
        hardware index the tile's top-left 8x8 occupies.

        The room strip offers these rather than `_all_tiles()`: after tiles grew
        past 8x8 the hardware list holds (size/8)^2 entries per tile, and showing
        those would ask the user to place a 32x32 tile one sixteenth at a time."""
        out = []
        v = 1
        for ts in self.proj.get("tilesets", []):
            n = ts_size(ts)
            per = max(1, n // 8) ** 2
            for t in ts.get("tiles", []) or []:
                out.append((v, n, t))
                v += per
        return out

    # ---- auto-tiling ----
    # Sixteen variants of one terrain, chosen by which of the four orthogonal
    # neighbours are the same terrain. Bit 0 north, 1 east, 2 south, 3 west, so
    # variant 0 is an isolated block and variant 15 is fully enclosed.
    #
    # This is an AUTHORING feature only: what lands in the room's tilemap is an
    # ordinary tile index, so the cartridge pays nothing for it and the runtime
    # never learns it happened.
    AUTO_VARIANTS = 16

    def _auto_runs(self):
        """Every declared run, as (bases, span).

        `bases` holds the sixteen hardware indices in mask order. A run is
        declared by marking one tile and needs the fifteen after it in the same
        set; a set with too few tiles left declares nothing rather than running
        past its own end into the next set's tiles."""
        runs = []
        v = 1
        for ts in self.proj.get("tilesets", []):
            n = ts_size(ts)
            per = max(1, n // 8) ** 2
            tiles = ts.get("tiles", []) or []
            starts = [v + i * per for i in range(len(tiles))]
            v += per * len(tiles)
            ab = ts.get("auto_base")
            if not isinstance(ab, int):
                continue
            if not (0 <= ab and ab + self.AUTO_VARIANTS <= len(tiles)):
                continue
            runs.append((starts[ab:ab + self.AUTO_VARIANTS], n))
        return runs

    def _auto_run_of(self, base):
        """The run `base` belongs to, or None."""
        for bases, span in self._auto_runs():
            if base in bases:
                return bases, span
        return None

    def _auto_mask(self, tm, cw, ch, cx, cy, bases, step):
        """Which orthogonal neighbours are the same terrain.

        Outside the room counts as the SAME terrain, so a field running off the
        edge is drawn as continuing rather than being outlined against nothing
        -- the alternative puts a coastline around every level."""
        mask = 0
        for bit, (dx, dy) in enumerate(((0, -1), (1, 0), (0, 1), (-1, 0))):
            nx, ny = cx + dx * step, cy + dy * step
            if not (0 <= nx < cw and 0 <= ny < ch):
                mask |= 1 << bit
                continue
            if tm[ny * cw + nx] in bases:
                mask |= 1 << bit
        return mask

    def _auto_fit(self, tm, cw, ch, cx, cy, bases, span):
        """Re-pick the variant at (cx, cy) from its neighbours. True if changed."""
        step = max(1, span // 8)
        if not (0 <= cx < cw and 0 <= cy < ch):
            return False
        if tm[cy * cw + cx] not in bases:
            return False
        want = bases[self._auto_mask(tm, cw, ch, cx, cy, bases, step)]
        if tm[cy * cw + cx] == want:
            return False
        return self._stamp(tm, cw, ch, cx, cy, want, step)

    def _stamp(self, tm, cw, ch, cx, cy, base, step):
        """Write one authored tile, which is step x step hardware cells."""
        changed = False
        for by in range(step):
            for bx in range(step):
                x, y = cx + bx, cy + by
                if not (0 <= x < cw and 0 <= y < ch):
                    continue
                v = base + by * step + bx
                if tm[y * cw + x] != v:
                    tm[y * cw + x] = v
                    changed = True
        return changed

    def _tile_span(self, base):
        """The pixel size of the authored tile starting at hardware index
        `base`, or 8 when the index is not a tile start (an old project, or a
        cell painted before the tile was resized)."""
        for v, n, _t in self._tile_entries():
            if v == base:
                return n
        return 8

    def _all_tiles(self):
        """Every tileset's tiles concatenated, matching the compiler's combined
        BG tile set. A room tilemap value v (1-based) indexes this list at v-1."""
        out = []
        for ts in self.proj.get("tilesets", []):
            n = ts_size(ts)
            for t in ts.get("tiles", []):
                out.extend(split_tile(t, n))
        return out

    # ================= sound composer =================
    SND_CELLW = 22
    SND_CELLH = 12

    def _cur_sound(self):
        r = self._sel_res()
        return r if (self._sel and self._sel[0] == "sound") else None

    SND_GUTTER = 40
    SND_RULER = 16

    def _sound_pane(self):
        pane, tools, body = self._pane("sound", "Sound")
        tools.pack_start(Gtk.Label(label=_t("Tempo")), False, False, 0)
        self._snd_tempo = Gtk.SpinButton.new_with_range(1, 30, 1)
        self._snd_tempo.set_tooltip_text(_t("How fast the tune plays"))
        self._snd_tempo.connect("value-changed", self._on_snd_tempo)
        tools.pack_start(self._snd_tempo, False, False, 0)
        tools.pack_start(_group_label(_t("Steps")), False, False, 0)
        self._snd_steps = Gtk.ComboBoxText()
        for n in ("8", "16", "32"):
            self._snd_steps.append(n, n)
        self._snd_steps.connect("changed", self._on_snd_steps)
        tools.pack_start(self._snd_steps, False, False, 0)
        # Timbre. All four reach the runtime, which has read them since they
        # were written and until now was always handed zero.
        tools.pack_start(_group_label(_t("Sound")), False, False, 0)
        self._snd_kind = Gtk.ComboBoxText()
        for k, lbl in (("0", "Music"), ("1", "Effect")):
            self._snd_kind.append(k, _t(lbl))
        self._snd_kind.set_tooltip_text(
            _t("An effect layers over the music; music replaces it"))
        self._snd_kind.connect("changed", self._on_snd_setting, "kind")
        tools.pack_start(self._snd_kind, False, False, 0)

        self._snd_duty = Gtk.ComboBoxText()
        for k, lbl in (("0", "50%"), ("1", "12.5%"), ("2", "25%"),
                       ("3", "50%"), ("4", "75%")):
            self._snd_duty.append(k, _t("Width") + " " + lbl)
        self._snd_duty.set_tooltip_text(_t("Square wave width; thinner is reedier"))
        self._snd_duty.connect("changed", self._on_snd_setting, "duty")
        tools.pack_start(self._snd_duty, False, False, 0)

        self._snd_vol = Gtk.ComboBoxText()
        self._snd_vol.append("0", _t("Full"))
        for v in range(1, 16):
            self._snd_vol.append(str(v), str(v))
        self._snd_vol.set_tooltip_text(_t("Volume of this sound"))
        self._snd_vol.connect("changed", self._on_snd_setting, "vol")
        tools.pack_start(self._snd_vol, False, False, 0)

        self._snd_decay = Gtk.ComboBoxText()
        self._snd_decay.append("0", _t("Hold"))
        for v in range(1, 8):
            self._snd_decay.append(str(v), _t("Pluck") + " " + str(v))
        self._snd_decay.set_tooltip_text(
            _t("Whether a note holds or fades after it is struck"))
        self._snd_decay.connect("changed", self._on_snd_setting, "decay")
        tools.pack_start(self._snd_decay, False, False, 0)

        self._snd_prio = Gtk.ComboBoxText()
        for v in range(8):
            self._snd_prio.append(str(v), _t("Priority") + " " + str(v))
        self._snd_prio.set_tooltip_text(
            _t("A playing effect is only replaced by one of equal or higher "
               "priority"))
        self._snd_prio.connect("changed", self._on_snd_setting, "prio")
        tools.pack_start(self._snd_prio, False, False, 0)

        # Roll or staff. The same pattern, read two ways: the grid is for
        # placing steps, the staff is for reading what was placed as music.
        # Neither is a different document, so this is a view and not a mode.
        self._snd_score = Gtk.ToggleButton(label=_t("Score"))
        self._snd_score.set_relief(Gtk.ReliefStyle.NONE)
        self._snd_score.get_style_context().add_class("toolbtn")
        self._snd_score.set_tooltip_text(_t("Read the pattern as notation"))
        self._snd_score.connect("toggled", self._on_snd_score)
        tools.pack_end(self._snd_score, False, False, 0)

        self._snd_loop = Gtk.CheckButton(label=_t("Loop"))
        self._snd_loop.set_margin_start(12)
        self._snd_loop.set_tooltip_text(_t("Play the tune over and over"))
        self._snd_loop.connect("toggled", self._on_snd_loop)
        tools.pack_start(self._snd_loop, False, False, 0)
        # Which channel you are writing on. Two identical buttons used to leave
        # this readable only from which colour of note came out brighter.
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        seg.get_style_context().add_class("toolseg")
        for ch, lbl, key in (("lead", "Lead", "1"), ("bass", "Bass", "2"),
                             ("drum", "Drums", "3")):
            b = Gtk.ToggleButton(label=_t(lbl))
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("toolbtn")
            b.get_style_context().add_class("wide")
            b.set_tooltip_text(_acc(lbl, key))
            b._sdk_hid = b.connect("clicked",
                                   lambda _w, c=ch: self._pick_channel(c))
            self._snd_btns[ch] = b
            seg.pack_start(b, False, False, 0)
        tools.pack_end(seg, False, False, 0)
        tools.pack_end(_group_label(_t("Channel")), False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._snd_canvas = Gtk.DrawingArea()
        self._snd_canvas.set_can_focus(True)
        self._snd_canvas.set_tooltip_text(
            "%s %s" % (_t("Click the grid to place a note on the selected "
                          "channel. Click a note again to clear it."),
                       _t(KEYS_HINT)))
        self._snd_canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._snd_canvas.connect("draw", self._draw_sound)
        self._snd_canvas.connect("button-press-event", self._on_snd_click)
        self._snd_canvas.connect("key-press-event", self._on_snd_key)
        self._snd_canvas.connect("focus-in-event", self._redraw_cb)
        self._snd_canvas.connect("focus-out-event", self._redraw_cb)
        scroll.add(self._snd_canvas)
        body.pack_start(scroll, True, True, 0)
        self._pane_focus["sound"] = self._snd_canvas
        return pane

    def _pick_channel(self, ch):
        self._snd_chan = ch
        self._seg_apply(list(self._snd_btns.items()), ch)
        self._snd_canvas.queue_draw()

    def _on_snd_score(self, btn):
        self._snd_view = "score" if btn.get_active() else "roll"
        s = self._cur_sound()
        if s:
            self._size_sound_canvas(s)
        self._snd_canvas.queue_draw()

    # ---- notation ----
    # A pattern is one note per step, so every note is the same length: the
    # staff shows evenly spaced notes rather than inventing durations the model
    # does not carry. Guessing them would draw a rhythm nobody wrote.
    STAFF_GAP = 7          # pixels between staff lines
    STAFF_TOP = 26         # top line of the treble staff
    STAFF_SPLIT = 82       # top line of the bass staff
    # semitone within an octave -> (staff step 0..6, needs a sharp)
    _PITCH_STEP = {0: (0, 0), 1: (0, 1), 2: (1, 0), 3: (1, 1), 4: (2, 0),
                   5: (3, 0), 6: (3, 1), 7: (4, 0), 8: (4, 1), 9: (5, 0),
                   10: (5, 1), 11: (6, 0)}

    def _staff_pos(self, pitch):
        """A diatonic staff position: seven letters per octave, not twelve
        semitones. C sharp and D share a line, and the sharp is what tells them
        apart; spacing by semitone makes a chromatic run look like a scale."""
        octave, semi = divmod(int(pitch), 12)
        return octave * 7 + self._PITCH_STEP[semi][0]

    def _staff_y(self, pitch, bass):
        """Where a pitch sits, and whether it needs a sharp.

        Staff position is DIATONIC -- seven letters per octave, not twelve
        semitones -- so C sharp and D share a line and the sharp is what tells
        them apart. Spacing by semitone instead is the mistake that makes a
        chromatic run look like a straight line."""
        pos = self._staff_pos(pitch)
        sharp = self._PITCH_STEP[int(pitch) % 12][1]
        # Bottom line of each staff, as MIDI notes: E4 (64) on the treble,
        # G2 (43) on the bass. Run through the SAME function rather than
        # written out in octaves and letters -- MIDI puts C4 at 60, so
        # divmod(64, 12) says octave 5, and hand-deriving it is an off-by-one
        # that puts every note an octave out.
        base_pos = self._staff_pos(43 if bass else 64)
        top = self.STAFF_SPLIT if bass else self.STAFF_TOP
        bottom = top + 4 * self.STAFF_GAP
        return bottom - (pos - base_pos) * (self.STAFF_GAP / 2.0), sharp

    def _load_sound_editor(self):
        s = self._cur_sound()
        if not s:
            return
        self._suspend = True
        self._snd_tempo.set_value(s.get("tempo", 8))
        self._snd_loop.set_active(bool(s.get("loop", True)))
        self._snd_steps.set_active_id(str(s.get("steps", 16)))
        self._suspend = False
        self._sync_snd_settings()
        self._pick_channel(self._snd_chan)
        self._size_sound_canvas(s)
        self._snd_canvas.queue_draw()

    def _size_sound_canvas(self, s):
        rows = PITCH_HI - PITCH_LO + 1
        cols = s.get("steps", 16)
        self._snd_canvas.set_size_request(
            cols * self.SND_CELLW + self.SND_GUTTER + 2,
            (self.STAFF_SPLIT + 4 * self.STAFF_GAP + 40)
            if getattr(self, "_snd_view", "roll") == "score"
            else rows * self.SND_CELLH + self.SND_RULER + 2)

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
        self.undo.checkpoint(_t("Steps"))
        n = int(combo.get_active_id() or 16)
        for ch in ("lead", "bass"):
            seq = list(s.get(ch, []))
            seq = (seq + [0] * n)[:n]
            s[ch] = seq
        s["steps"] = n
        self._snd_cur[0] = min(self._snd_cur[0], n - 1)
        self._save_autosave()
        self._size_sound_canvas(s)
        self._render_tree()
        self._update_head("sound")
        self._snd_canvas.queue_draw()
        self.undo.commit()

    def _draw_sharp(self, cr, x, y):
        """A sharp sign, drawn rather than typed.

        U+266F is not in Nimbus Sans -- only the CJK fallback carries it, so
        typing it renders as a box or in a face that does not match the text
        beside it. Drawing it also lets it scale with the staff instead of with
        the interface font."""
        g = self.STAFF_GAP
        cr.set_line_width(1.0)
        for dx in (-1.6, 1.6):
            cr.move_to(x + dx, y - g * 0.85)
            cr.line_to(x + dx, y + g * 0.75)
        cr.stroke()
        cr.set_line_width(1.6)
        for dy in (-g * 0.28, g * 0.28):
            cr.move_to(x - 3.4, y + dy + 1.1)
            cr.line_to(x + 3.4, y + dy - 1.1)
        cr.stroke()
        cr.set_line_width(1.2)

    def _draw_score(self, w, cr, s):
        """The same pattern, read as notation.

        Two staves braced together: lead on the treble, bass on the bass. Drums
        are not pitched, so they get a one-line percussion staff under the two
        rather than being forced onto a pitch they do not have."""
        cw = self.SND_CELLW
        gut = self.SND_GUTTER
        cols = s.get("steps", 16)
        aw = w.get_allocated_width()
        ah = w.get_allocated_height()
        cr.set_source_rgb(0.99, 0.98, 0.97)
        cr.rectangle(0, 0, aw, ah)
        cr.fill()

        width = gut + cols * cw
        cr.set_line_width(1)
        for top in (self.STAFF_TOP, self.STAFF_SPLIT):
            cr.set_source_rgb(0.35, 0.34, 0.31)
            for i in range(5):
                y = top + i * self.STAFF_GAP + 0.5
                cr.move_to(gut - 18, y)
                cr.line_to(width, y)
            cr.stroke()
        # the brace joining them, and the barline at the front
        cr.set_source_rgb(0.20, 0.19, 0.17)
        cr.set_line_width(2)
        cr.move_to(gut - 18, self.STAFF_TOP)
        cr.line_to(gut - 18, self.STAFF_SPLIT + 4 * self.STAFF_GAP)
        cr.stroke()
        cr.set_line_width(1)
        # gut - 18 is the brace; the labels sit clear of it, not under it
        cr.set_source_rgb(0.42, 0.40, 0.36)
        _show_text(cr, 2, self.STAFF_TOP + 3 * self.STAFF_GAP, _t("Lead"), 8)
        _show_text(cr, 2, self.STAFF_SPLIT + 3 * self.STAFF_GAP, _t("Bass"), 8)

        drum_y = self.STAFF_SPLIT + 4 * self.STAFF_GAP + 20
        cr.set_source_rgb(0.35, 0.34, 0.31)
        cr.move_to(gut - 18, drum_y + 0.5)
        cr.line_to(width, drum_y + 0.5)
        cr.stroke()
        _show_text(cr, 2, drum_y + 4, _t("Drums"), 8)

        # bar lines every four steps, which is what makes a pattern readable
        cr.set_source_rgba(0, 0, 0, 0.18)
        for c in range(0, cols + 1, 4):
            x = gut + c * cw + 0.5
            cr.move_to(x, self.STAFF_TOP)
            cr.line_to(x, self.STAFF_SPLIT + 4 * self.STAFF_GAP)
        cr.stroke()

        for name, bass in (("lead", False), ("bass", True)):
            seq = list(s.get(name) or [])
            live = (self._snd_chan == name)
            for c in range(min(cols, len(seq))):
                pitch = seq[c]
                if not pitch:
                    continue
                y, sharp = self._staff_y(pitch, bass)
                x = gut + c * cw + cw / 2.0
                top = self.STAFF_SPLIT if bass else self.STAFF_TOP
                bottom = top + 4 * self.STAFF_GAP
                # Ledger lines, or a note off the staff floats unreadably.
                cr.set_source_rgba(0.35, 0.34, 0.31, 1.0 if live else 0.45)
                yy = bottom + self.STAFF_GAP
                while yy <= y + 0.1:
                    cr.move_to(x - 7, yy + 0.5)
                    cr.line_to(x + 7, yy + 0.5)
                    yy += self.STAFF_GAP
                yy = top - self.STAFF_GAP
                while yy >= y - 0.1:
                    cr.move_to(x - 7, yy + 0.5)
                    cr.line_to(x + 7, yy + 0.5)
                    yy -= self.STAFF_GAP
                cr.stroke()
                cr.set_source_rgba(0.10, 0.10, 0.09, 1.0 if live else 0.40)
                cr.save()
                cr.translate(x, y)
                cr.scale(1.25, 1.0)
                cr.arc(0, 0, self.STAFF_GAP / 2.0 - 0.4, 0, 6.2832)
                cr.restore()
                cr.fill()
                # stem: down above the middle line, up below it, as written
                mid = top + 2 * self.STAFF_GAP
                cr.set_line_width(1.2)
                if y < mid:
                    cr.move_to(x - 4.2, y)
                    cr.line_to(x - 4.2, y + 22)
                else:
                    cr.move_to(x + 4.2, y)
                    cr.line_to(x + 4.2, y - 22)
                cr.stroke()
                if sharp:
                    self._draw_sharp(cr, x - 13, y)

        seq = list(s.get("drum") or [])
        live = (self._snd_chan == "drum")
        for c in range(min(cols, len(seq))):
            if not seq[c]:
                continue
            x = gut + c * cw + cw / 2.0
            cr.set_source_rgba(0.70, 0.34, 0.10, 1.0 if live else 0.40)
            cr.move_to(x - 4, drum_y - 4)
            cr.line_to(x + 4, drum_y + 4)
            cr.move_to(x + 4, drum_y - 4)
            cr.line_to(x - 4, drum_y + 4)
            cr.set_line_width(2)
            cr.stroke()
        return False

    def _draw_sound(self, w, cr):
        """The piano roll.

        Three things it did not do before: it had no keyboard down the side (so
        "which line is a C" was a guess), it told the two channels apart by
        colour alone, and it drew its octave labels through cairo's toy font API,
        which cannot fall back to another face for a character the base face is
        missing. Notes are now FILLED for the lead and HOLLOW for the bass, so
        the two read apart in a photograph, in greyscale, and to anyone who does
        not separate blue from green."""
        s = self._cur_sound()
        if s and getattr(self, "_snd_view", "roll") == "score":
            return self._draw_score(w, cr, s)
        cw, ch = self.SND_CELLW, self.SND_CELLH
        gut, rul = self.SND_GUTTER, self.SND_RULER
        rows = PITCH_HI - PITCH_LO + 1
        aw = w.get_allocated_width()
        ah = w.get_allocated_height()
        cr.set_source_rgb(0.99, 0.98, 0.97)
        cr.rectangle(0, 0, aw, ah)
        cr.fill()
        if not s:
            return False
        cols = s.get("steps", 16)
        grid_w = cols * cw
        # Row shading first, so the notes and the keyboard sit on top of it.
        for r in range(rows):
            pitch = PITCH_HI - r
            black = (pitch % 12) in (1, 3, 6, 8, 10)
            y = rul + r * ch
            cr.set_source_rgb(*((0.94, 0.93, 0.90) if black
                                else (0.99, 0.98, 0.97)))
            cr.rectangle(gut, y, grid_w, ch)
            cr.fill()
        # A keyboard, drawn as one: full-width white keys with a hairline between
        # them, then the black keys laid over the top at two thirds the width.
        # Drawn as alternating full-width bars it read as a barcode.
        cr.set_source_rgb(1.0, 1.0, 0.99)
        cr.rectangle(2, rul, gut - 5, rows * ch)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.16)
        cr.set_line_width(1)
        for r in range(rows + 1):
            pitch = PITCH_HI - r
            if (pitch % 12) in (0, 5) or r in (0, rows):
                cr.move_to(2, rul + r * ch + 0.5)
                cr.line_to(gut - 3, rul + r * ch + 0.5)
        cr.stroke()
        for r in range(rows):
            pitch = PITCH_HI - r
            y = rul + r * ch
            if (pitch % 12) in (1, 3, 6, 8, 10):
                cr.set_source_rgb(0.20, 0.19, 0.17)
                cr.rectangle(2, y + 1, (gut - 5) * 0.66, ch - 2)
                cr.fill()
            elif pitch % 12 == 0:        # name every C, the one landmark
                cr.set_source_rgb(0.32, 0.31, 0.28)
                _show_text(cr, gut - 22, y + ch - 3, "C%d" % (pitch // 12 - 1), 9)
        cr.set_source_rgba(0, 0, 0, 0.20)
        cr.set_line_width(1)
        cr.rectangle(2.5, rul + 0.5, gut - 6, rows * ch - 1)
        cr.stroke()
        # the step ruler: beats, so a bar can be counted
        cr.set_source_rgb(0.43, 0.41, 0.37)
        for c in range(0, cols, 4):
            _show_text(cr, gut + c * cw + 3, rul - 4, str(c + 1), 9)
        # grid
        for c in range(cols + 1):
            cr.set_source_rgba(0, 0, 0, 0.26 if c % 4 == 0 else 0.10)
            cr.set_line_width(1)
            cr.move_to(gut + c * cw, rul)
            cr.line_to(gut + c * cw, rul + rows * ch)
            cr.stroke()
        cr.set_source_rgba(0, 0, 0, 0.07)
        for r in range(rows + 1):
            cr.move_to(gut, rul + r * ch)
            cr.line_to(gut + grid_w, rul + r * ch)
        cr.stroke()
        # notes: the channel you are not on sits behind, at half strength
        for name, filled, col in (("bass", False, (0.20, 0.38, 0.30)),
                                  ("lead", True, (0.16, 0.26, 0.55))):
            seq = s.get(name, [])
            live = (name == self._snd_chan)
            for c in range(min(cols, len(seq))):
                note = seq[c]
                if not note or note < PITCH_LO or note > PITCH_HI:
                    continue
                x = gut + c * cw + 1.5
                y = rul + (PITCH_HI - note) * ch + 1.5
                cr.set_source_rgba(col[0], col[1], col[2], 1.0 if live else 0.4)
                cr.rectangle(x, y, cw - 3, ch - 3)
                if filled:
                    cr.fill()
                else:
                    cr.set_line_width(2)
                    cr.stroke()
        # Drums, on the top four rows. Drawn in every channel so a beat laid
        # down is visible while the melody over it is being written -- the roll
        # is one picture of the whole sound, not three pictures of a third of it.
        drum_live = (self._snd_chan == "drum")
        seq = list(s.get("drum") or [])
        for r, (which, name) in enumerate(self.DRUMS):
            y = rul + r * ch
            if drum_live:
                cr.set_source_rgba(0.55, 0.30, 0.10, 0.10)
                cr.rectangle(gut, y, cols * cw, ch)
                cr.fill()
                cr.set_source_rgb(0.42, 0.36, 0.30)
                _show_text(cr, 4, y + ch - 3, _t(name), 9)
            for c in range(min(cols, len(seq))):
                if seq[c] != which:
                    continue
                cr.set_source_rgba(0.70, 0.34, 0.10,
                                   1.0 if drum_live else 0.35)
                cr.rectangle(gut + c * cw, y, cw - 3, ch - 3)
                cr.fill()

        # the keyboard cursor
        if _focused(w):
            c = max(0, min(cols - 1, self._snd_cur[0]))
            p = max(PITCH_LO, min(PITCH_HI, self._snd_cur[1]))
            x = gut + c * cw
            y = rul + (PITCH_HI - p) * ch
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.set_line_width(3)
            cr.rectangle(x, y, cw, ch)
            cr.stroke()
            cr.set_source_rgb(0.10, 0.10, 0.09)
            cr.set_line_width(1.5)
            cr.rectangle(x + 1, y + 1, cw - 2, ch - 2)
            cr.stroke()
            # the note under the cursor, said in words
            cr.set_source_rgb(0.10, 0.10, 0.09)
            _show_text(cr, gut + 4, rul - 4,
                       "%s%d" % (NOTE_NAMES[p % 12], p // 12 - 1), 9, bold=True)
        return False

    # The four noise sounds, in the order the runtime reads them. Row 0 is the
    # top row of the roll, which is where a drum machine puts the crash.
    DRUMS = ((4, "Crash"), (3, "Hat"), (2, "Snare"), (1, "Kick"))

    def _drum_at(self, pitch):
        """Which drum a roll row means, or None if the row is not a drum row.

        Drums are four kinds rather than a pitch range, so they occupy the top
        four rows of the same grid; reusing the roll keeps one set of click
        maths, one ruler and one set of keys instead of a second editor that
        behaves almost the same."""
        row = PITCH_HI - pitch
        if 0 <= row < len(self.DRUMS):
            return self.DRUMS[row][0]
        return None

    def _snd_toggle(self, col, pitch):
        s = self._cur_sound()
        if not s:
            return
        cols = s.get("steps", 16)
        if self._snd_chan == "drum":
            which = self._drum_at(pitch)
            if which is None or not (0 <= col < cols):
                return
            seq = (list(s.get("drum") or []) + [0] * cols)[:cols]
            seq[col] = 0 if seq[col] == which else which
            s["drum"] = seq
            self.undo.touch()
            self._save_autosave()
            self._render_tree()
            self._snd_canvas.queue_draw()
            return
        if not (0 <= col < cols and PITCH_LO <= pitch <= PITCH_HI):
            return
        seq = list(s.get(self._snd_chan, []))
        seq = (seq + [0] * cols)[:cols]
        seq[col] = 0 if seq[col] == pitch else pitch
        s[self._snd_chan] = seq
        self.undo.touch()
        self._save_autosave()
        self._render_tree()
        self._snd_canvas.queue_draw()

    def _on_snd_setting(self, combo, key):
        if self._suspend:
            return
        s = self._cur_sound()
        if not s:
            return
        val = combo.get_active_id()
        if val is None:
            return
        self.undo.checkpoint(_t("Sound"))
        s[key] = int(val)
        self._save_autosave()
        self.undo.commit()

    def _on_obj_setting(self, combo, key):
        if self._suspend:
            return
        o = self._cur_object()
        if not o:
            return
        val = combo.get_active_id()
        if val is None:
            return
        self.undo.checkpoint(_t("Object"))
        o[key] = int(val)
        self._save_autosave()
        self.undo.commit()

    def _sync_obj_settings(self):
        o = self._cur_object()
        if not o:
            return
        self._suspend = True
        try:
            self._obj_tilecol.set_active_id(
                str(gbabuild._int(o.get("tilecol"), 0)))
            self._obj_depth.set_active_id(str(gbabuild._int(o.get("depth"), 0)))
            self._obj_hurt_frames.set_value(
                min(255, max(0, gbabuild._int(o.get("hurt_frames"), 0))))
        finally:
            self._suspend = False

    def _sync_snd_settings(self):
        s = self._cur_sound()
        if not s:
            return
        self._suspend = True
        try:
            for combo, key in ((self._snd_kind, "kind"),
                               (self._snd_duty, "duty"),
                               (self._snd_vol, "vol"),
                               (self._snd_decay, "decay"),
                               (self._snd_prio, "prio")):
                combo.set_active_id(str(gbabuild._int(s.get(key), 0)))
        finally:
            self._suspend = False

    def _on_snd_click(self, w, ev):
        w.grab_focus()
        s = self._cur_sound()
        if not s:
            return False
        c = int((ev.x - self.SND_GUTTER) // self.SND_CELLW)
        r = int((ev.y - self.SND_RULER) // self.SND_CELLH)
        pitch = PITCH_HI - r
        if not (0 <= c < s.get("steps", 16)
                and PITCH_LO <= pitch <= PITCH_HI):
            return True
        self._snd_cur = [c, pitch]
        self._snd_toggle(c, pitch)
        return True

    def _on_snd_key(self, w, ev):
        s = self._cur_sound()
        if not s:
            return False
        cols = s.get("steps", 16)
        cur = self._snd_cur
        if ev.keyval == Gdk.KEY_Left:
            cur[0] = max(0, cur[0] - 1)
        elif ev.keyval == Gdk.KEY_Right:
            cur[0] = min(cols - 1, cur[0] + 1)
        elif ev.keyval == Gdk.KEY_Up:
            cur[1] = min(PITCH_HI, cur[1] + 1)
        elif ev.keyval == Gdk.KEY_Down:
            cur[1] = max(PITCH_LO, cur[1] - 1)
        elif ev.keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._snd_toggle(cur[0], cur[1])
            return True
        elif ev.keyval in (Gdk.KEY_1, Gdk.KEY_KP_1):
            self._pick_channel("lead")
            return True
        elif ev.keyval in (Gdk.KEY_2, Gdk.KEY_KP_2):
            self._pick_channel("bass")
            return True
        else:
            return False
        w.queue_draw()
        return True

    # ================= object editor =================
    # ================= world =================
    WORLD_W, WORLD_H, WORLD_GAP = 108, 62, 34

    def _world_pane(self):
        """Every room, and the doors between them.

        A grid, not a force-directed layout. A graph that rearranges itself
        when a room is added is a graph nobody can navigate twice: the point
        here is finding the room that has no way back, and that needs the
        picture to stay where it was put."""
        pane, tools, body = self._pane("world", "World", resource=False)
        title, sub = self._heads["world"]
        title.set_text(_t("World"))
        sub.set_text(_t("Rooms and the doors between them"))

        self._world_count = Gtk.Label(xalign=0.0)
        self._world_count.get_style_context().add_class("sdkstatus")
        tools.pack_start(self._world_count, False, False, 0)

        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sc.set_vexpand(True)
        self._world_canvas = Gtk.DrawingArea()
        self._world_canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._world_canvas.connect("draw", self._draw_world)
        self._world_canvas.connect("button-press-event", self._on_world_click)
        sc.add(self._world_canvas)
        body.pack_start(sc, True, True, 0)
        return pane

    def _world_boxes(self):
        """(index, room, x, y) for every room, in project order."""
        rooms = self._res("room")
        span = self.WORLD_W + self.WORLD_GAP
        avail = self._world_canvas.get_allocated_width()
        if avail < span:
            avail = 600
        per = max(1, int(avail // span))
        out = []
        for i, r in enumerate(rooms):
            out.append((i, r,
                        14 + (i % per) * (self.WORLD_W + self.WORLD_GAP),
                        14 + (i // per) * (self.WORLD_H + self.WORLD_GAP)))
        return out

    def _load_world(self):
        rooms = self._res("room")
        doors = sum(len(r.get("warps") or []) for r in rooms)
        dead = sum(1 for r in rooms for w in (r.get("warps") or [])
                   if not self._room_by_id(w.get("room")))
        txt = _t("%d rooms, %d doors") % (len(rooms), doors)
        if dead == 1:
            txt += "  \u00b7  " + _t("1 leads nowhere")
        elif dead:
            txt += "  \u00b7  " + (_t("%d lead nowhere") % dead)
        self._world_count.set_text(txt)
        rows = (len(rooms) + 3) // 4 + 1
        self._world_canvas.set_size_request(
            -1, 28 + rows * (self.WORLD_H + self.WORLD_GAP))
        self._world_canvas.queue_draw()

    def _room_by_id(self, rid):
        for i, r in enumerate(self._res("room")):
            if r.get("id") == rid:
                return i
        return None

    def _draw_world(self, w, cr):
        cr.set_source_rgb(0.99, 0.98, 0.97)
        cr.rectangle(0, 0, w.get_allocated_width(), w.get_allocated_height())
        cr.fill()
        boxes = self._world_boxes()
        at = {i: (x, y) for i, _r, x, y in boxes}
        start = self.proj.get("start_room")

        # Doors first, so a box always sits on top of the lines into it.
        for i, r, x, y in boxes:
            for wp in r.get("warps") or []:
                j = self._room_by_id(wp.get("room"))
                if j is None or j not in at:
                    # A door to nothing is drawn as a stub rather than left
                    # out: an absent line looks like a room with no exits.
                    cr.set_source_rgb(0.78, 0.20, 0.12)
                    cr.set_line_width(1.5)
                    cr.move_to(x + self.WORLD_W, y + self.WORLD_H / 2)
                    cr.line_to(x + self.WORLD_W + 14, y + self.WORLD_H / 2)
                    cr.stroke()
                    self._world_cross(cr, x + self.WORLD_W + 18,
                                      y + self.WORLD_H / 2)
                    continue
                tx, ty = at[j]
                cr.set_source_rgba(0.20, 0.19, 0.17, 0.45)
                cr.set_line_width(1.4)
                cr.move_to(x + self.WORLD_W / 2, y + self.WORLD_H / 2)
                cr.line_to(tx + self.WORLD_W / 2, ty + self.WORLD_H / 2)
                cr.stroke()

        for i, r, x, y in boxes:
            sel = self._sel == ("room", i)
            cr.set_source_rgb(*self._c15(gbabuild._rgb15(r.get("bg"), 0)))
            cr.rectangle(x, y, self.WORLD_W, self.WORLD_H)
            cr.fill()
            cr.set_source_rgb(0.10, 0.10, 0.09) if sel else \
                cr.set_source_rgba(0.20, 0.19, 0.17, 0.55)
            cr.set_line_width(2.5 if sel else 1)
            cr.rectangle(x + 0.5, y + 0.5, self.WORLD_W - 1, self.WORLD_H - 1)
            cr.stroke()
            cr.set_source_rgb(0.99, 0.98, 0.97)
            _show_text(cr, x + 7, y + 16, r.get("id") or "?", 10, bold=True)
            if r.get("id") == start:
                _show_text(cr, x + 7, y + self.WORLD_H - 7, _t("start"), 8)
        return False

    def _world_cross(self, cr, x, y):
        cr.set_line_width(1.5)
        cr.move_to(x - 4, y - 4); cr.line_to(x + 4, y + 4)
        cr.move_to(x + 4, y - 4); cr.line_to(x - 4, y + 4)
        cr.stroke()

    def _on_world_click(self, w, ev):
        for i, _r, x, y in self._world_boxes():
            if x <= ev.x < x + self.WORLD_W and y <= ev.y < y + self.WORLD_H:
                # Selecting here opens the room editor, because the reason to
                # find a room on this map is to go and change it.
                self._select_resource("room", i)
                return True
        return True

    # ================= play =================
    def _file_play(self):
        """Build, then hand the ROM to the GBA Emulator and come back.

        Notebook OS runs one app at a time, so an emulator INSIDE this window
        is not available -- the spec says so and wishing otherwise does not
        change it. What can be fixed is the walk: exporting, closing, finding
        the file and opening it by hand is six steps between a change and
        seeing it, which is the loop that decides whether a game gets finished.

        The project is SAVED FIRST. This window hides while the emulator owns
        the screen, and anything unsaved at that moment is one crash away from
        being gone."""
        if not self.proj.get("objects") or not self.proj.get("rooms"):
            self._flash(_t("A game needs an object and a room before it runs"))
            return
        problems = gbabuild.check_project(self.proj)
        self._save_autosave()
        outdir = os.path.join(tempfile.gettempdir(), "nbgba-play")
        self._flash(_t("Compiling…"))
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        try:
            ok, rom, log = gbabuild.build_rom(copy.deepcopy(self.proj), outdir)
        except Exception as exc:                            # noqa: BLE001
            ok, rom, log = False, None, str(exc)
        self._last_log = log or ""
        if not ok or not rom:
            # Stay visible and say so. Hiding behind an emulator that never
            # opened is how a build failure looks like the machine freezing.
            self._flash(_t("This game did not build. Build \u25b8 Build "
                           "Details says why."))
            return
        if problems:
            self._flash(_t("Playing, with one thing that will not work")
                        if len(problems) == 1 else
                        _t("Playing, with %d things that will not work")
                        % len(problems))
        self._launch_emulator(rom)

    def _launch_emulator(self, rom):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "gbaemu.py")
        if not os.path.exists(script):
            self._flash(_t("The GBA Emulator is not on this machine"))
            return
        env = dict(os.environ,
                   PYTHONPATH=os.path.dirname(os.path.abspath(__file__)))
        try:
            proc = subprocess.Popen(["python3", script, rom], env=env)
        except OSError:
            self._flash(_t("The GBA Emulator would not start"))
            return
        # Same hand-off Finder uses: the flag file tells the desktop's widget
        # column to stand down too, or it draws over the emulator.
        try:
            open(nbapp.APP_FLAG, "w").close()
        except Exception:                                   # noqa: BLE001
            pass
        self.hide()
        GLib.child_watch_add(proc.pid, self._emulator_exited)

    def _emulator_exited(self, _pid, _status):
        try:
            os.unlink(nbapp.APP_FLAG)
        except Exception:                                   # noqa: BLE001
            pass
        self.show()
        self.present()

    # ================= budget =================
    def _show_budget(self):
        """What this project costs against what the console has.

        Shown BEFORE a build rather than after: a game that will not fit is
        otherwise found out at link time, by an error naming a section rather
        than an asset."""
        try:
            rep = gbabuild.budget_report(self.proj)
        except Exception as exc:                            # noqa: BLE001
            self._flash(_t("The budget could not be worked out: %s")
                        % str(exc)[:60])
            return
        out = []
        for l in rep["lines"]:
            cap = l["cap"]
            # A percentage of 32 MB is not a fact anybody can act on; bytes are.
            # The line names and notes come from gbabuild as English literals
            # and were printed raw, so this pane showed "Sprite tiles" in every
            # language while everything around it was translated.
            if l["unit"] == "bytes":
                out.append("%s %s" % (_pad(_t(l["name"]), 22),
                                      _t("%d KB") % (l["used"] // 1024)))
            else:
                pct = (l["used"] * 100 // cap) if cap else 0
                out.append("%s %6d / %-6d  %3d%%%s"
                           % (_pad(_t(l["name"]), 22), l["used"], cap, pct,
                              "   " + _t("OVER") if l["over"] else ""))
            if l["note"]:
                out.append("    (%s)" % _t(l["note"]))
            for w in l["worst"]:
                if not w["cost"]:
                    continue
                out.append("    %6d  %s%s" % (w["cost"], w["name"],
                                              "  " + w["detail"]
                                              if w["detail"] else ""))
            out.append("")
        if rep["problems"]:
            out.append(_t("Problems"))
            out += ["    " + p for p in rep["problems"]]
        self._show_log("\n".join(out))

    # ================= tables =================
    def _table_pane(self):
        """Rows and columns, edited in place.

        A grid rather than a form: a table is read by comparing rows, and a
        form shows one row at a time. Rebuilt on every structural change --
        adding a column changes every row's shape, and reusing the model across
        that is how a grid ends up showing one table's data under another's
        headings."""
        pane, tools, body = self._pane("table", "Table")
        for label, cb, tip in (
                ("Add Row", self._table_add_row, "A new row at the end"),
                ("Add Column", self._table_add_col, "A new column at the right"),
                ("Delete Row", self._table_del_row, "Remove the selected row")):
            b = Gtk.Button(label=_t(label))
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("quietbtn")
            b.set_tooltip_text(_t(tip))
            b.connect("clicked", lambda _w, f=cb: f())
            tools.pack_start(b, False, False, 0)

        self._tbl_count = Gtk.Label(xalign=0.0)
        self._tbl_count.get_style_context().add_class("sdkstatus")
        self._tbl_count.set_margin_start(10)
        tools.pack_start(self._tbl_count, False, False, 0)

        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sc.set_vexpand(True)
        self._tbl_view = Gtk.TreeView()
        self._tbl_view.set_enable_search(False)
        sc.add(self._tbl_view)
        body.pack_start(sc, True, True, 0)
        self._pane_focus["table"] = self._tbl_view
        return pane

    def _load_table_editor(self):
        t = self._sel_res()
        if not t or not self._sel or self._sel[0] != "table":
            return
        cols = t.get("columns") or []
        view = self._tbl_view
        for c in list(view.get_columns()):
            view.remove_column(c)
        store = Gtk.ListStore(*([str] * len(cols)))
        for row in t.get("rows") or []:
            store.append([str(row[i]) if i < len(row) else ""
                          for i in range(len(cols))])
        view.set_model(store)
        for i, c in enumerate(cols):
            rend = Gtk.CellRendererText()
            rend.set_property("editable", True)
            rend.connect("edited", self._on_table_edit, i)
            # The heading says the type, because a column that silently drops
            # "45kg" from a Number cell is a column that has to be explained.
            label = "%s  (%s)" % (c.get("name") or "?",
                                  _t(dict((k, lbl) for k, lbl, _c
                                          in COLUMN_TYPES).get(
                                      c.get("type"), "Text")))
            col = Gtk.TreeViewColumn(label, rend, text=i)
            col.set_resizable(True)
            col.set_min_width(90)
            view.append_column(col)
        self._tbl_count.set_text(
            _t("%d rows, %d columns") % (len(t.get("rows") or []), len(cols)))

    def _on_table_edit(self, _rend, path, text, col):
        t = self._sel_res()
        if not t:
            return
        try:
            r = int(path)
        except Exception:                                   # noqa: BLE001
            return
        rows = t.get("rows") or []
        cols = t.get("columns") or []
        if not (0 <= r < len(rows) and 0 <= col < len(cols)):
            return
        self.undo.checkpoint(_t("Edit Table"))
        rows[r][col] = self._table_value(text, cols[col].get("type"))
        self._save_autosave()
        self.undo.commit()
        self._load_table_editor()

    @staticmethod
    def _table_value(text, kind):
        """A typed cell value from what was typed.

        A Number column keeps a number even when the text is not one: storing
        the text would build a C initialiser that does not compile, and the
        error would name the generated file rather than the cell."""
        if kind == "int":
            return gbabuild._int(text, 0)
        if kind == "bool":
            return str(text).strip().lower() in ("1", "yes", "true", "y")
        return str(text)

    def _table_add_row(self):
        t = self._sel_res()
        if not t or not self._sel or self._sel[0] != "table":
            return
        cols = t.get("columns") or []
        self.undo.checkpoint(_t("Add Row"))
        t.setdefault("rows", []).append(
            ["" if c.get("type") == "text" else 0 for c in cols])
        self._save_autosave()
        self.undo.commit()
        self._load_table_editor()

    def _table_add_col(self):
        t = self._sel_res()
        if not t or not self._sel or self._sel[0] != "table":
            return
        cols = t.setdefault("columns", [])
        name = "col%d" % (len(cols) + 1)
        self.undo.checkpoint(_t("Add Column"))
        cols.append({"name": name, "type": "text"})
        # Every existing row gains a cell. Leaving them short would make the
        # grid and the generator disagree about the table's width.
        for row in t.get("rows") or []:
            row.append("")
        self._save_autosave()
        self.undo.commit()
        self._load_table_editor()

    def _table_del_row(self):
        t = self._sel_res()
        if not t or not self._sel or self._sel[0] != "table":
            return
        sel = self._tbl_view.get_selection()
        model, it = sel.get_selected() if sel else (None, None)
        if it is None:
            self._flash(_t("Select a row first"))
            return
        r = int(model.get_path(it).to_string())
        rows = t.get("rows") or []
        if not (0 <= r < len(rows)):
            return
        self.undo.checkpoint(_t("Delete Row"))
        rows.pop(r)
        self._save_autosave()
        self.undo.commit()
        self._load_table_editor()

    # ================= palettes =================
    SWATCH = 15
    SW_GAP = 2

    def _palette_pane(self):
        """What the build will do with this project's colours.

        The allocator already refused to overflow and already reported it -- but
        only in a build log, after the fact, one sprite at a time. It never said
        how much room was left, which sprites were sharing a bank, or which one
        was about to cost the sixteenth. That is the difference the spec names
        between hiding the constraint well and hiding it badly."""
        pane, tools, body = self._pane("palette", "Palettes", resource=False)
        # Nothing selects this pane, so its head has to name itself.
        title, sub = self._heads["palette"]
        title.set_text(_t("Palettes"))
        sub.set_text(_t("Sprite colour sets"))
        self._pal_summary = Gtk.Label(xalign=0.0)
        self._pal_summary.get_style_context().add_class("sdkstatus")
        tools.pack_start(self._pal_summary, False, False, 0)
        refresh = Gtk.Button(label=_t("Refresh"))
        refresh.set_relief(Gtk.ReliefStyle.NONE)
        refresh.get_style_context().add_class("quietbtn")
        refresh.connect("clicked", lambda *_: self._load_palette_pane())
        tools.pack_end(refresh, False, False, 0)

        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.set_vexpand(True)
        self._pal_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sc.add(self._pal_body)
        body.pack_start(sc, True, True, 0)
        return pane

    def _load_palette_pane(self):
        for ch in self._pal_body.get_children():
            self._pal_body.remove(ch)
        try:
            rep = gbabuild.palette_report(self.proj)
        except Exception as exc:                            # noqa: BLE001
            # A report must never be the thing that stops the app: it is read
            # while a project is half-built, which is when it is most useful.
            lab = Gtk.Label(label=str(exc)[:90], xalign=0.0)
            lab.get_style_context().add_class("sdkstatus")
            self._pal_body.pack_start(lab, False, False, 12)
            self._pal_body.show_all()
            return

        self._pal_summary.set_text(
            _t("%d of 16 colour sets \u00b7 %d of 240 colours")
            % (rep["used"], rep["total"]))

        if not rep["banks"]:
            self._pal_body.pack_start(self._actions_empty(
                _t("No sprite is painted, so no colour set is in use.")),
                True, True, 0)
            self._pal_body.show_all()
            return

        for prob in rep["problems"]:
            warn = Gtk.Label(label="\u26a0  " + prob, xalign=0.0)
            warn.get_style_context().add_class("palwarn")
            warn.set_line_wrap(True)
            warn.set_max_width_chars(70)
            warn.set_margin_start(14)
            warn.set_margin_end(14)
            warn.set_margin_top(8)
            self._pal_body.pack_start(warn, False, False, 0)

        for b in rep["banks"]:
            self._pal_body.pack_start(self._palette_bank_row(b), False, False, 0)

        self._pal_body.pack_start(_rule(12, 6), False, False, 0)
        self._pal_body.pack_start(_eyebrow(_t("SPRITES")), False, False, 0)
        for sp in rep["sprites"]:
            self._pal_body.pack_start(self._palette_sprite_row(sp),
                                      False, False, 0)
        self._pal_body.show_all()

    def _palette_bank_row(self, b):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_start(14)
        row.set_margin_end(14)
        row.set_margin_top(7)
        num = Gtk.Label(label="%d" % b["index"], xalign=1.0)
        num.get_style_context().add_class("palnum")
        num.set_size_request(20, -1)
        row.pack_start(num, False, False, 0)

        da = Gtk.DrawingArea()
        w = 16 * (self.SWATCH + self.SW_GAP)
        da.set_size_request(w, self.SWATCH + 2)
        da.connect("draw", self._draw_bank, b["colours"])
        row.pack_start(da, False, False, 0)

        free = Gtk.Label(label=_t("%d free") % b["free"], xalign=0.0)
        free.get_style_context().add_class("palfree" if b["free"] > 2
                                           else "palwarn")
        free.set_size_request(52, -1)
        row.pack_start(free, False, False, 0)

        who = Gtk.Label(label=", ".join(b["sprites"]) or "\u2014", xalign=0.0)
        who.get_style_context().add_class("sdkstatus")
        who.set_ellipsize(Pango.EllipsizeMode.END)
        who.set_max_width_chars(30)
        row.pack_start(who, True, True, 0)
        return row

    def _draw_bank(self, _w, cr, colours):
        """Sixteen swatches. Index 0 is not black -- it is transparent, and
        drawing it as a colour is how an author comes to believe a bank holds
        16 usable colours when it holds 15."""
        step = self.SWATCH + self.SW_GAP
        for i in range(16):
            x = i * step
            if i == 0:
                cr.set_source_rgb(0.93, 0.92, 0.89)
                cr.rectangle(x, 1, self.SWATCH, self.SWATCH)
                cr.fill()
                cr.set_source_rgb(0.72, 0.70, 0.66)
                cr.set_line_width(1)
                cr.move_to(x + 3, 4)
                cr.line_to(x + self.SWATCH - 3, self.SWATCH - 2)
                cr.move_to(x + self.SWATCH - 3, 4)
                cr.line_to(x + 3, self.SWATCH - 2)
                cr.stroke()
                continue
            colour = colours[i] if i < len(colours) else 0
            if colour:
                cr.set_source_rgb(*self._c15(colour))
                cr.rectangle(x, 1, self.SWATCH, self.SWATCH)
                cr.fill()
                cr.set_source_rgb(0.79, 0.77, 0.72)
                cr.set_line_width(1)
                cr.rectangle(x + 0.5, 1.5, self.SWATCH - 1, self.SWATCH - 1)
                cr.stroke()
            else:
                cr.set_source_rgb(0.97, 0.96, 0.94)
                cr.rectangle(x, 1, self.SWATCH, self.SWATCH)
                cr.fill()
        return False

    def _palette_sprite_row(self, sp):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_start(14)
        row.set_margin_end(14)
        row.set_margin_top(5)
        name = Gtk.Label(label=sp["name"], xalign=0.0)
        name.get_style_context().add_class("palname")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(20)
        name.set_size_request(140, -1)
        row.pack_start(name, False, False, 0)

        # nbi18n cannot pick a plural from a "%d colour(s)" key, so these are
        # two whole strings -- the same rule the rest of the OS follows.
        cnt = Gtk.Label(
            label=(_t("1 colour") if sp["colours"] == 1
                   else _t("%d colours") % sp["colours"]), xalign=0.0)
        cnt.get_style_context().add_class("palwarn" if sp["over"]
                                          else "sdkstatus")
        cnt.set_size_request(78, -1)
        row.pack_start(cnt, False, False, 0)

        combo = Gtk.ComboBoxText()
        combo.append("auto", _t("Any set"))
        for n in range(16):
            combo.append(str(n), _t("Set %d") % n)
        combo.set_active_id(str(self._res("sprite")[sp["index"]].get("pal_bank"))
                            if sp["pinned"] else "auto")
        combo.set_tooltip_text(
            _t("Pin this sprite to one colour set so it can share tiles with "
               "another sprite in the same set"))
        combo.connect("changed", self._on_pin_bank, sp["index"])
        row.pack_start(combo, False, False, 0)

        got = Gtk.Label(label=_t("now in set %d") % sp["bank"], xalign=0.0)
        got.get_style_context().add_class("sdkstatus")
        row.pack_start(got, False, False, 0)
        return row

    def _on_pin_bank(self, combo, index):
        if self._suspend:
            return
        lst = self._res("sprite")
        if not (0 <= index < len(lst)):
            return
        want = combo.get_active_id()
        self.undo.checkpoint(_t("Pin Colour Set"))
        if want in (None, "auto"):
            lst[index].pop("pal_bank", None)
        else:
            lst[index]["pal_bank"] = int(want)
        self._save_autosave()
        self.undo.commit()
        self._load_palette_pane()

    # ================= script =================
    def _script_pane(self):
        """A plain C editor. No syntax colouring: the compiler is the authority
        on whether the text is C, and a highlighter that disagrees with it
        teaches the wrong thing."""
        pane, tools, body = self._pane("script", "Script")
        # A label, not a sentence: what this pane holds and who can see it.
        hint = Gtk.Label(label=_t("File scope \u00b7 visible to every object"),
                         xalign=0.0)
        hint.get_style_context().add_class("sdkstatus")
        tools.pack_start(hint, False, False, 0)

        ref = Gtk.Button(label=_t("Engine Calls"))
        ref.set_relief(Gtk.ReliefStyle.NONE)
        ref.get_style_context().add_class("quietbtn")
        ref.connect("clicked", lambda *_: self._open_help("eng_instances"))
        tools.pack_end(ref, False, False, 0)

        self._script_view = Gtk.TextView()
        self._script_view.set_monospace(True)
        self._script_view.set_left_margin(12)
        self._script_view.set_right_margin(12)
        self._script_view.set_top_margin(10)
        self._script_view.set_bottom_margin(10)
        self._script_view.get_buffer().connect("changed", self._on_script_edit)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sc.set_vexpand(True)
        sc.add(self._script_view)
        body.pack_start(sc, True, True, 0)
        self._pane_focus["script"] = self._script_view
        return pane

    def _load_script_editor(self):
        r = self._sel_res()
        if not r:
            return
        self._suspend = True
        try:
            self._script_view.get_buffer().set_text(r.get("code") or "")
        finally:
            self._suspend = False

    def _on_script_edit(self, buf):
        if self._suspend:
            return
        r = self._sel_res()
        if not r or not self._sel or self._sel[0] != "script":
            return
        start, end = buf.get_bounds()
        r["code"] = buf.get_text(start, end, False)
        self._save_autosave()

    # ================= help =================
    def _help_pane(self):
        """The reference and the course, built once and kept.

        get_project is passed as a callable, not as the project: the pane is
        registered at startup and has to read whatever project is open when a
        checkpoint is drawn, including one opened later."""
        self._help = gbahelp.HelpPane(get_project=lambda: self.proj,
                                      on_insert=self._insert_code)
        return self._help

    def _insert_code(self, code, scope="event"):
        """Put a Help recipe where it compiles.

        A recipe that declares a function or a file-scope table only compiles
        as a script, because an Execute Code action is emitted INSIDE an event
        function. Routing it by scope rather than by where the cursor happens
        to be is the difference between a working example and a brace error
        that names none of the decision that caused it.

        Returns False rather than raising when there is nowhere to put it, so
        the Help pane can say which of the two it was: a recipe that vanishes
        on press is indistinguishable from one that failed."""
        if scope == "script":
            self.undo.checkpoint(_t("New Script"))
            lst = self._res("script")
            lst.append({"id": self._uid("scr", lst), "code": code})
            self._save_autosave()
            self._render_tree()
            self._select_resource("script", len(lst) - 1)
            self.undo.commit()
            try:
                self._help.refresh()
            except Exception:                               # noqa: BLE001
                pass
            return True
        ev = self._cur_event()
        if ev is None:
            return False
        self.undo.checkpoint(_t("Add Action"))
        ev.setdefault("actions", []).append(
            # Recipes are C. Inserting one as Script would hand C to the
            # subset compiler, which rejects it and replaces the whole block
            # with a comment -- a recipe that vanishes on its first build.
            {"kind": "execute_code", "lang": "C", "code": code})
        self._sel_action = len(ev["actions"]) - 1
        self._save_autosave()
        self._render_actions()
        self.undo.commit()
        # The course counts what is in the project, so a checkpoint satisfied
        # by this insert has to be re-read rather than waiting for the topic to
        # be opened again.
        try:
            self._help.refresh()
        except Exception:                                   # noqa: BLE001
            pass
        return True

    def _open_world(self):
        self._editor_stack.show("world")
        self._load_world()

    def _open_help(self, tid=None):
        self._editor_stack.show("help")
        if tid:
            self._help.show_topic(tid)
        else:
            self._help.refresh()

    def _show_event_c(self):
        """The C this event compiles to.

        The teaching device the whole course is built on (spec Part 0): the
        author reads their own work one level down, without leaving the level
        they work at."""
        o = self._sel_res()
        ev = self._cur_event()
        if not o or ev is None:
            self._flash(_t("Select an event first"))
            return
        try:
            code, problems = gbabuild.preview_event_c(self.proj, o, ev)
        except Exception as exc:                            # noqa: BLE001
            self._flash(_t("This event could not be generated: %s") % exc)
            return
        self._show_c_window(o, ev, code, problems)

    def _show_c_window(self, obj, ev, code, problems):
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_default_size(560, 460)
        try:
            name = gbabuild._Gen._event_name(ev)
        except Exception:                                   # noqa: BLE001
            name = str(ev.get("type") or "Event")
        dlg.set_title("%s \u00b7 %s" % (obj.get("name") or "Object", name))
        box = dlg.get_content_area()
        box.set_spacing(0)

        head = Gtk.Label(label=_t("Generated C"), xalign=0.0)
        head.get_style_context().add_class("dlghead")
        head.set_margin_start(16)
        head.set_margin_top(14)
        head.set_margin_bottom(2)
        box.pack_start(head, False, False, 0)
        sub = Gtk.Label(
            label=_t("The text handed to the compiler for this event."),
            xalign=0.0)
        sub.get_style_context().add_class("dlgsub")
        sub.set_margin_start(16)
        sub.set_margin_bottom(10)
        box.pack_start(sub, False, False, 0)

        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_monospace(True)
        tv.set_left_margin(12)
        tv.set_top_margin(8)
        tv.get_buffer().set_text(code)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sc.add(tv)
        sc.set_margin_start(16)
        sc.set_margin_end(16)
        box.pack_start(sc, True, True, 0)

        if problems:
            # Problems belong here rather than only in a build log: this is
            # where the author is already looking at the code that produced them.
            for text in problems[:4]:
                lab = Gtk.Label(label="\u26a0  " + text, xalign=0.0)
                lab.get_style_context().add_class("dlgwarn")
                lab.set_line_wrap(True)
                lab.set_max_width_chars(64)
                lab.set_margin_start(16)
                lab.set_margin_end(16)
                lab.set_margin_top(8)
                box.pack_start(lab, False, False, 0)

        learn = dlg.add_button(_t("Explain This"), 1)
        learn.get_style_context().add_class("suggested-action")
        close = dlg.add_button(_t("Close"), Gtk.ResponseType.CLOSE)
        close.get_style_context().add_class("default")
        dlg.show_all()
        resp = dlg.run()
        dlg.destroy()
        if resp == 1:
            self._open_help("c01")

    def _object_pane(self):
        pane, tools, body = self._pane("object", "Object")
        tools.pack_start(Gtk.Label(label=_t("Sprite")), False, False, 0)
        self._obj_sprite = Gtk.ComboBoxText()
        self._obj_sprite.set_tooltip_text(
            _t("The picture this object wears on screen"))
        self._obj_sprite.connect("changed", self._on_obj_sprite)
        tools.pack_start(self._obj_sprite, False, False, 0)
        # What stops this object. tilecol is the field the runtime checks
        # BEFORE it looks at the tile layer at all -- with it 0 an instance
        # moves straight through a solid floor, however the tiles are marked.
        tools.pack_start(Gtk.Label(label=_t("Stopped by")), False, False, 0)
        self._obj_tilecol = Gtk.ComboBoxText()
        for k, lbl in (("0", "Nothing"), ("1", "Solid tiles"),
                       ("2", "Tiles and solid objects")):
            self._obj_tilecol.append(k, _t(lbl))
        self._obj_tilecol.set_tooltip_text(
            _t("Whether solid tiles and solid objects block this one"))
        self._obj_tilecol.connect("changed", self._on_obj_setting, "tilecol")
        tools.pack_start(self._obj_tilecol, False, False, 0)

        tools.pack_start(_group_label(_t("Depth")), False, False, 0)
        self._obj_depth = Gtk.ComboBoxText()
        for d in range(8):
            self._obj_depth.append(str(d), str(d))
        self._obj_depth.set_tooltip_text(
            _t("Drawing layer; 0 is in front of everything"))
        self._obj_depth.connect("changed", self._on_obj_setting, "depth")
        tools.pack_start(self._obj_depth, False, False, 0)

        tools.pack_start(_group_label(_t("Mercy frames")), False, False, 0)
        self._obj_hurt_frames = Gtk.SpinButton.new_with_range(0, 255, 1)
        self._obj_hurt_frames.set_width_chars(3)
        self._obj_hurt_frames.set_tooltip_text(
            _t("Invincible frames after this object loses health; 0 disables"))
        self._obj_hurt_frames.connect("value-changed", self._on_obj_hurt_frames)
        tools.pack_start(self._obj_hurt_frames, False, False, 0)

        self._obj_visible = Gtk.CheckButton(label=_t("Visible"))
        self._obj_visible.set_margin_start(12)
        self._obj_visible.set_tooltip_text(
            _t("Untick to keep this object working but out of sight"))
        self._obj_visible.connect("toggled", self._on_obj_visible)
        tools.pack_start(self._obj_visible, False, False, 0)

        # three columns: events | actions | the palette they come from
        ec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ec.set_size_request(148, -1)
        # "EVENTS" is a homograph: the catalogs read it as calendar
        # appointments (de TERMINE, ja 予定, ko 일정). Name what this
        # list actually holds so it cannot share their key.
        ec.pack_start(_eyebrow(_t("OBJECT EVENTS")), False, False, 0)
        # Calendar owns "Add Event" for a diary appointment (de
        # "Termin hinzufügen"). This adds an event to a game OBJECT,
        # so it needs its own key -- same collision as OBJECT EVENTS.
        addev = Gtk.Button(label=_t("Add Object Event"))
        addev.set_relief(Gtk.ReliefStyle.NONE)
        addev.get_style_context().add_class("quietbtn")
        addev.set_tooltip_text(_t("When should this object do something?"))
        addev.connect("clicked", lambda *_: self._add_event())
        ec.pack_start(addev, False, False, 0)
        esc = Gtk.ScrolledWindow()
        esc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        esc.set_vexpand(True)
        self._event_list = Gtk.ListBox()
        self._event_list.get_style_context().add_class("assetlist")
        self._event_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._event_list.connect("row-selected", self._on_event_select)
        esc.add(self._event_list)
        ec.pack_start(esc, True, True, 0)
        body.pack_start(ec, False, False, 0)
        self._pane_focus["object"] = self._event_list

        ac = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ac.set_hexpand(True)
        ac.pack_start(_eyebrow(_t("ACTIONS")), False, False, 0)
        asc = Gtk.ScrolledWindow()
        asc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        asc.set_vexpand(True)
        self._action_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        asc.add(self._action_list)
        ac.pack_start(asc, True, True, 0)
        body.pack_start(ac, True, True, 0)

        # The palette. It was thirty-nine identical buttons in one scroll with no
        # stated order — findable only by reading every one of them. Same buttons,
        # under the headings the list was already grouped by in the source.
        pc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pc.set_size_request(158, -1)
        pc.pack_start(_eyebrow(_t("ADD ACTION")), False, False, 0)
        psc = Gtk.ScrolledWindow()
        psc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        psc.set_vexpand(True)
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for heading, kinds in ACTION_GROUPS:
            pbox.pack_start(_eyebrow(_t(heading), margin_top=8), False, False, 0)
            for kind in kinds:
                b = Gtk.Button(label=_t(ACTION_LABEL.get(kind, kind)))
                b.set_relief(Gtk.ReliefStyle.NONE)
                b.get_style_context().add_class("palbtn")
                b.set_halign(Gtk.Align.FILL)
                b.set_tooltip_text(_t(ACTION_TIPS.get(kind,
                                                      ACTION_LABEL.get(kind, kind))))
                b.connect("clicked", lambda _w, k=kind: self._add_action(k))
                pbox.pack_start(b, False, False, 0)
        psc.add(pbox)
        pc.pack_start(psc, True, True, 0)
        body.pack_start(pc, False, False, 0)
        return pane

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
        self._sync_obj_settings()
        if self._sel_event is None and o.get("events"):
            self._sel_event = 0
        self._render_events()
        self._render_actions()

    def _on_obj_sprite(self, combo):
        if self._suspend:
            return
        o = self._cur_object()
        if o is not None:
            self.undo.checkpoint(_t("Sprite"))
            o["sprite"] = combo.get_active_id() or None
            self._save_autosave()
            self._render_tree()
            self.undo.commit()

    def _on_obj_visible(self, chk):
        if self._suspend:
            return
        o = self._cur_object()
        if o is not None:
            self.undo.checkpoint(_t("Visible"))
            o["visible"] = bool(chk.get_active())
            self._save_autosave()
            self.undo.commit()

    def _on_obj_hurt_frames(self, spin):
        if self._suspend:
            return
        o = self._cur_object()
        if o is not None:
            self.undo.checkpoint(_t("Mercy frames"))
            o["hurt_frames"] = min(255, max(0, spin.get_value_as_int()))
            self._save_autosave()
            self.undo.commit()

    def _add_event(self):
        o = self._cur_object()
        if not o:
            return
        items = [(k, lbl) for k, lbl in EVENT_KINDS]
        # _t() on the labels: every one of these is already a key in all
        # seventeen catalogs, and the dialog picks by INDEX, so translating them
        # cannot break the choice it returns.
        self._choose(_t("Add Object Event"), [_t(lbl) for _k, lbl in items],
                     lambda i: self._do_add_event(items[i][0]))

    def _do_add_event(self, kind):
        o = self._cur_object()
        if not o:
            return
        self.undo.checkpoint(_t("Add Object Event"))
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
        self._render_tree()
        self._update_head("object")
        self.undo.commit()

    def _event_label(self, ev):
        """What an event is called in the list. Composed from words the catalogs
        already carry, with the key or object name left as itself."""
        t = ev.get("type")
        if t == "key":
            return "%s: %s" % (_t("Key"), ev.get("key", "?"))
        if t == "keypress":
            return "%s: %s" % (_t("Press"), ev.get("key", "?"))
        if t == "keyrelease":
            return "%s: %s" % (_t("Release"), ev.get("key", "?"))
        if t == "alarm":
            return "%s %s" % (_t("Alarm"), ev.get("alarm", "?"))
        if t == "collision":
            return "%s: %s" % (_t("Collision"), (ev.get("object") or "?"))
        names = {"create": "Create", "step": "Step", "draw": "Draw",
                 "no_health": "No Health",
                 "destroy": "Destroy"}
        return _t(names.get(t, t.capitalize() if t else "?"))

    def _render_events(self):
        self._events_busy = True
        try:
            for c in self._event_list.get_children():
                self._event_list.remove(c)
            o = self._cur_object()
            if not o:
                return
            events = o.get("events", [])
            if not events:
                row = Gtk.ListBoxRow()
                row.set_selectable(False)
                row.set_activatable(False)
                lbl = Gtk.Label(label=_t("No object events"), xalign=0)
                lbl.get_style_context().add_class("emptyrow")
                row.add(lbl)
                self._event_list.add(row)
                self._event_list.show_all()
                return
            chosen = None
            for i, ev in enumerate(events):
                row = Gtk.ListBoxRow()
                row.index = i
                rb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                rb.get_style_context().add_class("evrow")
                lbl = Gtk.Label(label=self._event_label(ev), xalign=0)
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
                rb.pack_start(lbl, True, True, 0)
                # The delete button now says what it deletes. It was a bare "x"
                # with no tooltip on every row of the list.
                rb.pack_end(_icon_button("trash", _t("Delete Event"),
                                         lambda ix=i: self._del_event(ix),
                                         cls="iconbtn", size=11),
                            False, False, 0)
                n = len(ev.get("actions") or [])
                if n:
                    cnt = Gtk.Label(label=str(n))
                    cnt.get_style_context().add_class("groupcount")
                    rb.pack_end(cnt, False, False, 0)
                row.add(rb)
                self._event_list.add(row)
                if i == self._sel_event:
                    chosen = row
            self._event_list.show_all()
            if chosen is not None:
                self._event_list.select_row(chosen)
        finally:
            self._events_busy = False

    def _on_event_select(self, _box, row):
        if getattr(self, "_events_busy", False) or row is None:
            return
        i = getattr(row, "index", None)
        if i is not None:
            self._select_event(i)

    def _select_event(self, i):
        self._sel_event = i
        self._sel_action = None
        self._render_events()
        self._render_actions()

    def _del_event(self, i):
        o = self._cur_object()
        if o and 0 <= i < len(o["events"]):
            self.undo.checkpoint(_t("Delete Event"))
            del o["events"][i]
            self._sel_event = None
            self._save_autosave()
            self._render_events()
            self._render_actions()
            self._render_tree()
            self._update_head("object")
            self.undo.commit()

    def _cur_event(self):
        o = self._cur_object()
        if o and self._sel_event is not None and \
                0 <= self._sel_event < len(o.get("events", [])):
            return o["events"][self._sel_event]
        return None

    def _default_param(self, spec):
        """What a parameter starts as.

        A resource picker used to start as "", and an empty id compiles to
        NOTHING — so ten of the forty actions were silent no-ops the moment they
        were added, even in a project with exactly one sound to play. Start them
        on the first real resource of the kind they want."""
        if isinstance(spec, list):
            return spec[0]
        if spec == "int":
            return "0"
        opts = self._param_options(spec)
        return opts[0] if opts else ""

    def _param_options(self, spec):
        """The ids a picker of this kind can offer, in project order."""
        key = {"obj": "objects", "room": "rooms", "snd": "sounds",
               "spr": "sprites"}.get(spec)
        if not key:
            return []
        own = [r.get("id") for r in self.proj.get(key, [])
               if isinstance(r, dict) and r.get("id")]
        if spec == "snd":
            # The project's own sounds first, then the twelve the runtime
            # carries. Offering them here is what lets a project with no sounds
            # in it make a noise at all.
            return own + [k for k, _lbl, _m in gbabuild.BUILTIN_SFX]
        return own

    def _add_action(self, kind):
        ev = self._cur_event()
        if ev is None:
            # The same sentence as the one in _show_event_c, and it used to be
            # a second key differing only by a full stop — two catalog entries
            # for one situation, which German had already drifted into two
            # different wordings.
            self._flash(_t("Select an event first"))
            return
        if kind in ACTION_PRESETS:
            code = ACTION_PRESETS[kind][1]
            if "{obj}" in code:
                try:
                    obj = self.proj["objects"].index(self._cur_object())
                except (ValueError, TypeError):
                    obj = 0
                code = code.format(obj=obj)
            act = {"kind": "execute_code", "lang": "C",
                   "code": code}
        else:
            act = {"kind": kind}
        for key, _lbl, spec in ACTION_PARAMS.get(kind, []):
            act[key] = self._default_param(spec)
        if kind in CONTAINER_ACTIONS:
            act["children"] = []
        self.undo.checkpoint(_t("Add Action"))
        ev["actions"].append(act)
        self._sel_action = len(ev["actions"]) - 1
        self._save_autosave()
        self._render_actions()
        self.undo.commit()

    def _render_actions(self):
        for c in self._action_list.get_children():
            self._action_list.remove(c)
        ev = self._cur_event()
        if ev is None:
            self._action_list.pack_start(self._actions_empty(
                _t("Select an event, then add actions from the palette.")), True, True, 0)
            self._action_list.show_all()
            return
        acts = ev.setdefault("actions", [])
        if not acts:
            self._action_list.pack_start(self._actions_empty(
                _t("Click an action on the right to say what happens.")),
                True, True, 0)
            self._action_list.show_all()
            return
        for i, act in enumerate(acts):
            self._action_list.pack_start(self._action_card(act, i, acts),
                                         False, False, 0)
        self._action_list.show_all()

    @staticmethod
    def _actions_empty(text):
        """The empty state of the action sheet: centred in the space it has, the
        way every other empty state in the OS is, rather than one grey line in
        the top corner of a large empty box."""
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        wrap.set_valign(Gtk.Align.CENTER)
        wrap.set_halign(Gtk.Align.CENTER)
        lbl = Gtk.Label(label=text)
        lbl.set_line_wrap(True)
        lbl.set_max_width_chars(34)
        lbl.set_justify(Gtk.Justification.CENTER)
        lbl.get_style_context().add_class("panehint")
        wrap.pack_start(lbl, False, False, 0)
        return wrap

    def _action_card(self, act, i, parent, depth=0):
        """One action, as a card that can be focused, reordered and deleted.

        The card takes focus, so Alt+Up / Alt+Down reorder the run of actions
        from the keyboard — the arrow buttons were the only way to do it, which
        made the order of a behaviour a mouse-only property of the game."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.get_style_context().add_class("actcard")
        card.set_can_focus(True)
        card.act_list, card.act_index = parent, i
        card.connect("key-press-event", self._on_action_key)
        card.connect("focus-in-event", self._on_action_focus)
        card.connect("focus-out-event", self._redraw_cb)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # A number, so a run of actions can be counted and talked about, and so
        # the order is visible when the cards are all the same height.
        num = Gtk.Label(label=str(i + 1))
        num.get_style_context().add_class("actnum")
        head.pack_start(num, False, False, 0)
        t = Gtk.Label(label=_t(ACTION_LABEL.get(act.get("kind"),
                                                act.get("kind"))), xalign=0)
        t.get_style_context().add_class("actname")
        head.pack_start(t, True, True, 0)
        # Drawn arrows, not font "↑/↓" (the sans body face lacks them and would
        # render tofu boxes) — and all three now say what they do, with the key
        # that does it. They were three unnamed icon buttons per card.
        head.pack_end(_icon_button(
            "trash", _t("Delete this action"),
            lambda ix=i, p=parent: self._del_action(p, ix),
            cls="iconbtn", size=11), False, False, 0)
        head.pack_end(_icon_button(
            "down", _acc("Move Down", "Alt+Down"),
            lambda ix=i, p=parent: self._move_action(p, ix, 1),
            cls="iconbtn", size=11), False, False, 0)
        head.pack_end(_icon_button(
            "up", _acc("Move Up", "Alt+Up"),
            lambda ix=i, p=parent: self._move_action(p, ix, -1),
            cls="iconbtn", size=11), False, False, 0)
        card.pack_start(head, False, False, 0)
        # params form
        for key, label, spec in ACTION_PARAMS.get(act.get("kind"), []):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            pl = Gtk.Label(label=_t(label), xalign=0)
            pl.set_size_request(78, -1)
            pl.get_style_context().add_class("paramlbl")
            row.pack_start(pl, False, False, 0)
            widget = self._param_widget(act, key, spec)
            # A number does not need six hundred pixels of box: only free text
            # and code stretch.
            grow = spec in ("str", "code")
            row.pack_start(widget, grow, grow, 0)
            card.pack_start(row, False, False, 0)
        # container: nested actions run when the condition/loop holds
        if act.get("kind") in CONTAINER_ACTIONS:
            kids = act.setdefault("children", [])
            kidbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            kidbox.set_margin_start(14)
            kidbox.set_margin_top(2)
            kidbox.get_style_context().add_class("actchildren")
            for ci, child in enumerate(kids):
                kidbox.pack_start(self._action_card(child, ci, kids, depth + 1),
                                  False, False, 0)
            addk = Gtk.Button(label=_t("Add Action"))
            addk.set_relief(Gtk.ReliefStyle.NONE)
            addk.get_style_context().add_class("quietbtn")
            addk.set_halign(Gtk.Align.START)
            addk.set_tooltip_text(_t("Add an action inside this one"))
            addk.connect("clicked", lambda _w, k=kids: self._add_action_into(k))
            kidbox.pack_start(addk, False, False, 0)
            card.pack_start(kidbox, False, False, 0)
        return card

    def _on_action_focus(self, w, _ev=None):
        self._sel_action = getattr(w, "act_index", None)
        return False

    def _on_action_key(self, w, ev):
        """Alt+Up / Alt+Down reorder; Delete removes."""
        lst = getattr(w, "act_list", None)
        i = getattr(w, "act_index", None)
        if lst is None or i is None:
            return False
        alt = bool(ev.state & Gdk.ModifierType.MOD1_MASK)
        if alt and ev.keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self._move_action(lst, i, -1)
            return True
        if alt and ev.keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self._move_action(lst, i, 1)
            return True
        if ev.keyval == Gdk.KEY_Delete:
            self._del_action(lst, i)
            return True
        return False

    def _add_action_into(self, lst):
        items = [(k, lbl) for k, lbl, _p in ACTION_DEFS]
        self._choose(_t("Add Action"), [_t(lbl) for _k, lbl in items],
                     lambda i: self._do_add_action_into(lst, items[i][0]))

    def _do_add_action_into(self, lst, kind):
        if kind in ACTION_PRESETS:
            act = {"kind": "execute_code", "lang": "C",
                   "code": ACTION_PRESETS[kind][1]}
        else:
            act = {"kind": kind}
        for key, _lbl, spec in ACTION_PARAMS.get(kind, []):
            act[key] = self._default_param(spec)
        if kind in CONTAINER_ACTIONS:
            act["children"] = []
        self.undo.checkpoint(_t("Add Action"))
        lst.append(act)
        self._save_autosave()
        self._render_actions()
        self.undo.commit()

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
            opts = list(spec) if isinstance(spec, list) \
                else self._param_options(spec)
            # A built-in effect's id is a routing token, not a name. Showing
            # "sfx:coin" in a picker asks the reader to decode it.
            sfx_label = {k: lbl for k, lbl, _m in gbabuild.BUILTIN_SFX}
            for opt in opts:
                combo.append(opt, _t(sfx_label[opt]) if opt in sfx_label
                             else opt)
            want = str(act.get(key) or "")
            if want not in opts:
                # Either it was never chosen, or what it named has been deleted.
                # Say which: a note of what it USED to be is worth more than a
                # blank box, and choosing anything clears the note.
                was = act.get("_was")
                if was:
                    combo.append(was, "%s (%s)" % (was, _t("missing")))
                    want = was
                elif opts:
                    want = opts[0]
                    act[key] = want     # heal it, rather than compile to nothing
            combo.set_active_id(want)
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
        if act.get("_was") and value:
            act.pop("_was", None)       # it points at something real again
        self.undo.touch()
        self._save_autosave()

    def _move_action(self, lst, i, delta):
        j = i + delta
        if 0 <= i < len(lst) and 0 <= j < len(lst):
            self.undo.checkpoint(_t("Move Up") if delta < 0 else _t("Move Down"))
            lst[i], lst[j] = lst[j], lst[i]
            self._sel_action = j
            self._save_autosave()
            self._render_actions()
            self.undo.commit()
            self._focus_action(j)

    def _del_action(self, lst, i):
        if 0 <= i < len(lst):
            self.undo.checkpoint(_t("Delete"))
            del lst[i]
            self._save_autosave()
            self._render_actions()
            self.undo.commit()
            self._focus_action(min(i, len(lst) - 1))

    def _focus_action(self, i):
        """Put focus back on the card that just moved, so a run of Alt+Up keeps
        working instead of dropping the keyboard on the floor after one press."""
        if i is None or i < 0:
            return
        for child in self._action_list.get_children():
            if getattr(child, "act_index", None) == i:
                child.grab_focus()
                return

    # ================= room editor =================
    def _room_pane(self):
        pane, tools, body = self._pane("room", "Room")
        # What you are laying down. Two loose radio buttons said "Objects Tiles"
        # in the middle of a row of other settings; this is the same segmented
        # control the tools and the sound channels use, so a mode is a mode
        # everywhere in the app.
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        seg.get_style_context().add_class("toolseg")
        self._room_mode_btns = {}
        for mode, label, key in (("objects", "Objects", "1"),
                                 ("tiles", "Tiles", "2"),
                                 ("warps", "Doors", "3")):
            b = Gtk.ToggleButton(label=_t(label))
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("toolbtn")
            b.get_style_context().add_class("wide")
            b.set_tooltip_text(_acc(label, key))
            b._sdk_hid = b.connect("clicked",
                                   lambda _w, m=mode: self._set_room_mode(m))
            self._room_mode_btns[mode] = b
            seg.pack_start(b, False, False, 0)
        tools.pack_start(seg, False, False, 0)

        self._room_place_cap = _group_label(_t("Place"))
        tools.pack_start(self._room_place_cap, False, False, 0)
        self._room_obj = Gtk.ComboBoxText()
        self._room_obj.connect("changed", self._on_room_obj)
        tools.pack_start(self._room_obj, False, False, 0)

        # Where a door leads. Chosen BEFORE the door is drawn, because a door
        # with no destination is the one thing a warp cannot be -- the
        # generator drops it and reports it, which is a poor way to find out.
        self._room_warp_cap = _group_label(_t("Leads to"))
        tools.pack_start(self._room_warp_cap, False, False, 0)
        self._room_warp_to = Gtk.ComboBoxText()
        self._room_warp_to.set_tooltip_text(
            _t("The room a door placed here opens into"))
        self._room_warp_to.connect("changed", self._on_warp_dest)
        tools.pack_start(self._room_warp_to, False, False, 0)

        tools.pack_start(_group_label(_t("Zoom")), False, False, 0)
        zseg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        zseg.get_style_context().add_class("toolseg")
        self._zoom_btns = {}
        # A room may be up to 1024 px across; at the old fixed 2x there was no
        # way to see one whole. Fit is the answer to "show me the level".
        # 1x / 2x / 4x say what they are; only Fit needs explaining.
        for z, label, tip in ((1, "1×", None), (2, "2×", None), (4, "4×", None),
                              ("fit", _t("Fit"), _t("Show the whole room"))):
            b = Gtk.ToggleButton(label=label)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("toolbtn")
            b.get_style_context().add_class("wide")
            if tip:
                b.set_tooltip_text(tip)
            b._sdk_hid = b.connect("clicked",
                                   lambda _w, zz=z: self._set_room_zoom(zz))
            self._zoom_btns[z] = b
            zseg.pack_start(b, False, False, 0)
        tools.pack_start(zseg, False, False, 0)

        self._room_start = Gtk.CheckButton(label=_t("Start room"))
        self._room_start.set_margin_start(12)
        self._room_start.set_tooltip_text(
            _t("The room the game opens in"))
        self._room_start.connect("toggled", self._on_room_start)
        tools.pack_start(self._room_start, False, False, 0)

        clr = Gtk.Button(label=_t("Clear"))
        clr.set_relief(Gtk.ReliefStyle.NONE)
        clr.get_style_context().add_class("quietbtn")
        clr.set_tooltip_text(_t("Take everything out of this room"))
        clr.connect("clicked", lambda *_: self._room_clear())
        tools.pack_end(clr, False, False, 0)

        # left of the canvas: the room's own settings, and (in Tiles mode) the
        # tiles to paint with. Both used to be extra rows stacked above the
        # canvas, which pushed the room itself into the bottom third of the pane.
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        side.set_size_request(148, -1)
        side.pack_start(_eyebrow(_t("ROOM")), False, False, 0)
        self._room_w = self._dim_spin(16, 1024, 16, "w")
        self._room_h = self._dim_spin(16, 1024, 16, "h")
        self._room_speed = self._dim_spin(1, 60, 1, "speed")
        for label, sp, tip in (
                ("Width", self._room_w, None), ("Height", self._room_h, None),
                ("Speed", self._room_speed,
                 _t("Steps per second the game runs at"))):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            cap = Gtk.Label(label=_t(label), xalign=0)
            cap.get_style_context().add_class("paramlbl")
            cap.set_size_request(52, -1)
            if tip:
                sp.set_tooltip_text(tip)
            row.pack_start(cap, False, False, 0)
            row.pack_start(sp, True, True, 0)
            side.pack_start(row, False, False, 0)

        self._room_tile_head = _eyebrow(_t("TILES"), margin_top=10)
        self._room_tile_head.set_no_show_all(True)
        side.pack_start(self._room_tile_head, False, False, 0)
        self._room_tile_scroll = Gtk.ScrolledWindow()
        self._room_tile_scroll.set_policy(Gtk.PolicyType.NEVER,
                                          Gtk.PolicyType.AUTOMATIC)
        self._room_tile_scroll.set_no_show_all(True)
        self._room_tile_scroll.set_vexpand(True)
        self._room_tile_flow = Gtk.FlowBox()
        self._room_tile_flow.set_max_children_per_line(3)
        self._room_tile_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self._room_tile_flow.set_valign(Gtk.Align.START)
        self._room_tile_flow.set_row_spacing(4)
        self._room_tile_flow.set_column_spacing(4)
        self._room_tile_scroll.add(self._room_tile_flow)
        side.pack_start(self._room_tile_scroll, True, True, 0)
        body.pack_start(side, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._room_hint = Gtk.Label(xalign=0)
        self._room_hint.set_line_wrap(True)
        self._room_hint.set_max_width_chars(56)
        self._room_hint.get_style_context().add_class("panehint")
        right.pack_start(self._room_hint, False, False, 0)
        self._room_canvas = Gtk.DrawingArea()
        self._room_canvas.set_size_request(240, 160)   # resized to the room on load
        self._room_canvas.set_halign(Gtk.Align.CENTER)
        self._room_canvas.set_valign(Gtk.Align.CENTER)
        self._room_canvas.set_can_focus(True)
        self._room_canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                                     | Gdk.EventMask.BUTTON1_MOTION_MASK
                                     | Gdk.EventMask.BUTTON3_MOTION_MASK)
        self._room_canvas.connect("draw", self._draw_room)
        self._room_canvas.connect("button-press-event", self._on_room_click)
        self._room_canvas.connect("motion-notify-event", self._on_room_motion)
        self._room_canvas.connect("key-press-event", self._on_room_key)
        self._room_canvas.connect("focus-in-event", self._redraw_cb)
        self._room_canvas.connect("focus-out-event", self._redraw_cb)
        self._room_scroll = Gtk.ScrolledWindow()
        self._room_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                                     Gtk.PolicyType.AUTOMATIC)
        self._room_scroll.set_vexpand(True)
        self._room_scroll.add(self._room_canvas)
        self._room_scroll.connect("size-allocate", self._on_room_resize)
        right.pack_start(self._room_scroll, True, True, 0)
        body.pack_start(right, True, True, 0)
        self._pane_focus["room"] = self._room_canvas
        return pane

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
        self.undo.checkpoint(_t("Size"))
        ow = max(16, int(rm.get("w", 240)))
        oh = max(16, int(rm.get("h", 160)))
        rm[key] = int(spin.get_value())
        if key in ("w", "h"):
            self._reshape_tilemap(rm, ow, oh)
        self._save_autosave()
        self._resize_room_canvas()
        self._render_tree()
        self._update_head("room")
        self._room_canvas.queue_draw()
        self.undo.commit()

    def _room_geom(self):
        """(scale, ox, oy): room pixels per screen pixel, and where the room sits
        inside the canvas.

        At a fixed zoom the canvas is asked for exactly the room's size, so the
        offsets are zero. "Fit" instead lets the canvas fill the viewport and
        works the scale out from the size it was GIVEN — deliberately, because
        deriving the canvas's size request from its own allocation is a layout
        feedback loop, and the first version of this did exactly that and hung."""
        rm = self._cur_room()
        rw = max(16, int(rm.get("w", 240))) if rm else 240
        rh = max(16, int(rm.get("h", 160))) if rm else 160
        aw = self._room_canvas.get_allocated_width()
        ah = self._room_canvas.get_allocated_height()
        if self._room_zoom == "fit":
            sc = min((aw - 4) / float(rw), (ah - 4) / float(rh))
            scale = float(int(sc)) if sc >= 1 else max(0.125,
                                                      round(sc * 8) / 8.0)
        else:
            scale = float(self._room_zoom if isinstance(self._room_zoom, int)
                          else 2)
        return (scale, max(0.0, (aw - rw * scale) / 2.0),
                max(0.0, (ah - rh * scale) / 2.0))

    def _room_scale(self):
        return self._room_geom()[0]

    def _set_room_zoom(self, z):
        self._room_zoom = z
        self._seg_apply(list(self._zoom_btns.items()), z)
        self._resize_room_canvas()
        self._room_canvas.queue_draw()

    def _on_room_resize(self, *_a):
        # Fit re-reads the allocation every time it draws, so a resize only needs
        # a repaint. Nothing here may touch the size request.
        if self._room_zoom == "fit":
            self._room_canvas.queue_draw()

    def _resize_room_canvas(self):
        rm = self._cur_room()
        if not rm:
            return
        if self._room_zoom == "fit":
            self._room_canvas.set_size_request(-1, -1)
            self._room_canvas.set_hexpand(True)
            self._room_canvas.set_vexpand(True)
            self._room_canvas.set_halign(Gtk.Align.FILL)
            self._room_canvas.set_valign(Gtk.Align.FILL)
            return
        scale = int(self._room_zoom if isinstance(self._room_zoom, int) else 2)
        w = max(16, int(rm.get("w", 240)))
        h = max(16, int(rm.get("h", 160)))
        self._room_canvas.set_hexpand(False)
        self._room_canvas.set_vexpand(False)
        self._room_canvas.set_halign(Gtk.Align.CENTER)
        self._room_canvas.set_valign(Gtk.Align.CENTER)
        self._room_canvas.set_size_request(w * scale, h * scale)

    def _set_room_mode(self, mode):
        """Objects or tiles — and the pane says which, in words, in the hint, and
        by showing only the palette that mode can use. The tile strip used to sit
        there in Objects mode too, offering tiles that a click would ignore."""
        self._room_mode = mode
        self._seg_apply(list(self._room_mode_btns.items()), mode)
        tiles = (mode == "tiles")
        warps = (mode == "warps")
        self._room_tile_head.set_visible(tiles)
        self._room_tile_scroll.set_visible(tiles)
        self._room_place_cap.set_visible(not tiles and not warps)
        self._room_obj.set_visible(not tiles and not warps)
        self._room_warp_cap.set_visible(warps)
        self._room_warp_to.set_visible(warps)
        if warps:
            self._sync_warp_dest()
        # Joined with the middot these hints already use inside themselves: the
        # first clause carries no full stop, so a plain space ran the two
        # sentences together ("...rub it out Or use the arrow keys").
        if warps:
            hint = _t("Click to put a door here · right-click to remove")
        elif tiles:
            hint = _t("Click to paint the chosen tile · right-click to rub it out")
        else:
            hint = _t("Click to place the selected object · right-click to remove")
        self._room_hint.set_text("%s · %s" % (hint, _t(KEYS_HINT)))
        self._room_canvas.queue_draw()

    def _render_room_tile_palette(self):
        for c in self._room_tile_flow.get_children():
            self._room_tile_flow.remove(c)
        entries = ([(0, 8, None)]
                   + [(v, n, t) for v, n, t in self._tile_entries()])
        for v, tn, tile in entries:
            da = Gtk.DrawingArea()
            da.set_size_request(34, 34)
            da.connect("draw", self._draw_room_tile_swatch, v, tile, tn)
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.get_style_context().add_class("framebtn")
            if v == self._room_tile:
                btn.get_style_context().add_class("on")
            btn.add(da)
            btn.set_tooltip_text(_t("Erase") if v == 0 else _t("Tile %d") % v)
            btn.connect("clicked", lambda _w, iv=v: self._pick_room_tile(iv))
            self._room_tile_flow.add(btn)
        self._room_tile_flow.show_all()

    def _draw_room_tile_swatch(self, w, cr, v, tile, tn=8):
        cr.set_source_rgb(0.86, 0.84, 0.78)
        cr.paint()
        if tile is not None:
            # the swatch is a fixed 32px well, so a 32x32 tile draws at 1:1 and
            # an 8x8 one is magnified 4x -- both fill the same square
            self._draw_tile_grid(cr, tile, 32.0 / max(1, tn), False, tn)
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

    def _reshape_tilemap(self, rm, ow, oh):
        """Carry a room's painted tiles across a resize.

        The layer is one flat list whose row length is the room's width in
        8px cells, so widening a room used to slide every row sideways and turn
        a built level into diagonal confetti. This copies cell by cell."""
        old = rm.get("tiles")
        if not isinstance(old, list) or not old:
            return
        ocw, och = max(1, ow // 8), max(1, oh // 8)
        ncw = max(1, max(16, int(rm.get("w", 240))) // 8)
        nch = max(1, max(16, int(rm.get("h", 160))) // 8)
        new = [0] * (ncw * nch)
        for y in range(min(och, nch)):
            for x in range(min(ocw, ncw)):
                i = y * ocw + x
                if i < len(old):
                    new[y * ncw + x] = old[i]
        rm["tiles"] = new

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
        self._suspend = False
        self._set_room_mode(self._room_mode)
        self._set_room_zoom(self._room_zoom)
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
        if not rm:
            return
        if chk.get_active():
            self.undo.checkpoint(_t("Start room"))
            self.proj["start_room"] = rm.get("id")
            self._save_autosave()
            self._render_tree()          # the Start badge moves with it
            self.undo.commit()
            return
        # Un-ticking used to do nothing at all: the box cleared, the room stayed
        # the start room, and the two disagreed for ever. A game has to start
        # somewhere, so say so and put the tick back.
        if self.proj.get("start_room") == rm.get("id"):
            self._suspend = True
            chk.set_active(True)
            self._suspend = False
            self._flash(_t("A game has to start somewhere. Tick another room to "
                           "move the start there."))

    def _room_clear(self):
        rm = self._cur_room()
        if not rm:
            return
        # Clear used to empty the room on the spot with no question asked and
        # nothing to undo. It asks now, and it is one undo step either way.
        if not (rm.get("instances") or [n for n in (rm.get("tiles") or []) if n]):
            self._flash(_t("This room is already empty."))
            return
        if not self._confirm(_t("Clear this room?"),
                             _t("Removes every instance from the room. "
                                "The room is kept."),
                             _t("Clear")):
            return
        self.undo.checkpoint(_t("Clear"))
        rm["instances"] = []
        if isinstance(rm.get("tiles"), list):
            rm["tiles"] = [0] * len(rm["tiles"])
        self._save_autosave()
        self._render_tree()
        self._update_head("room")
        self._room_canvas.queue_draw()
        self.undo.commit()

    def _sprite_by_id(self, sid):
        for s in self.proj["sprites"]:
            if s["id"] == sid:
                return s
        return None

    def _draw_room(self, w, cr):
        rm = self._cur_room()
        # Paper outside the room, the room's own colour inside it. Painting the
        # WHOLE canvas dark turned every spare pixel of a fitted view into a
        # black slab, which read as the room being bigger than it is.
        aw = w.get_allocated_width()
        ah = w.get_allocated_height()
        cr.set_source_rgb(0.99, 0.98, 0.97)
        cr.rectangle(0, 0, aw, ah)
        cr.fill()
        if not rm:
            return False
        scale, ox, oy = self._room_geom()
        cr.translate(ox, oy)
        rw = max(16, int(rm.get("w", 240)))
        rh = max(16, int(rm.get("h", 160)))
        bg = gbabuild._rgb15(rm.get("bg"), 0)
        cr.set_source_rgb(*self._c15(bg))
        cr.rectangle(0, 0, rw * scale, rh * scale)
        cr.fill()
        # BG tile layer. The pixbuf cache is keyed on a WHOLE-number scale, so a
        # fitted, fractional zoom draws the tiles as rectangles instead.
        tm = rm.get("tiles")
        if isinstance(tm, list) and tm:
            all_tiles = self._all_tiles()
            cw = max(1, rw // 8)
            whole = float(scale).is_integer() and scale >= 1
            for ci, v in enumerate(tm):
                if not v or v > len(all_tiles):
                    continue
                tx = (ci % cw) * 8 * scale
                ty = (ci // cw) * 8 * scale
                if whole:
                    pb = self._tile_pixbuf(all_tiles[v - 1], int(scale))
                    Gdk.cairo_set_source_pixbuf(cr, pb, tx, ty)
                    cr.paint()
                else:
                    avg = self._tile_average(all_tiles[v - 1])
                    if avg == TRANSPARENT:
                        continue
                    cr.set_source_rgb(*self._c15(avg))
                    cr.rectangle(tx, ty, 8 * scale + 0.7, 8 * scale + 0.7)
                    cr.fill()
        for it in rm.get("instances", []):
            o = next((ob for ob in self.proj["objects"]
                      if ob["id"] == it.get("object")), None)
            spr = self._sprite_by_id(o.get("sprite")) if o else None
            x = it.get("x", 0) * scale
            y = it.get("y", 0) * scale
            if spr:
                self._blit_sprite_preview(cr, spr, it.get("x", 0),
                                          it.get("y", 0), scale)
            else:
                # An object with no picture: a dashed outline saying "something
                # is here but you have not drawn it yet". It was a solid red
                # square, which read as an error and spent the app's one alert
                # colour on an ordinary state.
                cr.set_source_rgb(0.86, 0.85, 0.80)
                cr.set_line_width(1)
                cr.set_dash([3.0, 3.0])
                cr.rectangle(x - 8 * scale / 2, y - 8 * scale / 2,
                             8 * scale, 8 * scale)
                cr.stroke()
                cr.set_dash([])
        # grid, only while it can be seen as a grid
        if scale >= 1:
            cr.set_source_rgba(1, 1, 1, 0.08)
            cr.set_line_width(1)
            for gx in range(0, rw + 1, 16):
                cr.move_to(gx * scale, 0)
                cr.line_to(gx * scale, rh * scale)
            for gy in range(0, rh + 1, 16):
                cr.move_to(0, gy * scale)
                cr.line_to(rw * scale, gy * scale)
            cr.stroke()
        cr.set_source_rgb(0.66, 0.64, 0.58)
        cr.set_line_width(1)
        cr.rectangle(-0.5, -0.5, rw * scale + 1, rh * scale + 1)
        cr.stroke()
        # What the player will actually see: one screenful, 240x160.
        cr.set_source_rgba(1, 1, 1, 0.55)
        cr.set_line_width(2)
        cr.rectangle(1, 1, min(240, rw) * scale - 2, min(160, rh) * scale - 2)
        cr.stroke()
        # Doors, drawn in every mode. A warp placed and then invisible is
        # state the author has to remember, and it is the one thing in a room
        # that has no picture of its own.
        for wp in rm.get("warps") or []:
            wx = gbabuild._int(wp.get("x")) * scale
            wy = gbabuild._int(wp.get("y")) * scale
            ww = max(1, gbabuild._int(wp.get("w"), 8)) * scale
            wh = max(1, gbabuild._int(wp.get("h"), 8)) * scale
            cr.set_source_rgba(0.78, 0.20, 0.12, 0.30)
            cr.rectangle(wx, wy, ww, wh)
            cr.fill()
            cr.set_source_rgb(0.78, 0.20, 0.12)
            cr.set_line_width(1.5)
            cr.rectangle(wx + 0.75, wy + 0.75, ww - 1.5, wh - 1.5)
            cr.stroke()
            # A door with no destination cannot exist in a build, so it is
            # marked here rather than left looking like the others.
            if not wp.get("room"):
                cr.move_to(wx + 2, wy + 2)
                cr.line_to(wx + ww - 2, wy + wh - 2)
                cr.move_to(wx + ww - 2, wy + 2)
                cr.line_to(wx + 2, wy + wh - 2)
                cr.stroke()

        if _focused(w):
            cx, cy = self._room_cur
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.set_line_width(3)
            cr.rectangle(cx * 8 * scale, cy * 8 * scale, 8 * scale, 8 * scale)
            cr.stroke()
            cr.set_source_rgb(0.10, 0.10, 0.09)
            cr.set_line_width(1.5)
            cr.rectangle(cx * 8 * scale + 1, cy * 8 * scale + 1,
                         8 * scale - 2, 8 * scale - 2)
            cr.stroke()
        return False

    def _blit_sprite_preview(self, cr, spr, cx, cy, scale):
        # Width AND height. Reading only "w" drew every one of the twelve sprite
        # sizes as a square, so a 16x32 character previewed in the room as a
        # 16x16 block and nothing lined up with what the game would show.
        sw = gbabuild._int(spr.get("w"), 16) or 16
        sh = gbabuild._int(spr.get("h"), sw) or sw
        frames = spr.get("frames")
        px = frames[0] if isinstance(frames, list) and frames else []
        if not isinstance(px, (list, tuple)):
            px = []
        ox = gbabuild._int(spr.get("ox"), sw // 2)
        oy = gbabuild._int(spr.get("oy"), sh // 2)
        for j in range(sh):
            for i in range(sw):
                idx = j * sw + i
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
        w.grab_focus()
        rm = self._cur_room()
        if not rm:
            return False
        scale, ox, oy = self._room_geom()
        erase = (getattr(ev, "button", 1) == 3)
        cx = int((ev.x - ox) / scale) // 8
        cy = int((ev.y - oy) / scale) // 8
        self._room_cur = [max(0, cx), max(0, cy)]
        if self._room_mode == "tiles":
            if self._paint_room_tile(rm, cx, cy, erase=erase):
                self._room_touched()
            w.queue_draw()
            return True
        if self._room_mode == "warps":
            if self._place_or_remove_warp(rm, cx, cy, erase):
                self._room_touched()
                # The map is a view of the doors, so it cannot be stale while
                # a door is being placed with it open beside the room.
                try:
                    self._load_world()
                except Exception:                           # noqa: BLE001
                    pass
            w.queue_draw()
            return True
        if self._place_or_remove(rm, cx, cy, erase):
            self._room_touched()
        w.queue_draw()
        return True

    # ---- doors ----
    def _sync_warp_dest(self):
        """The destination list, without the room being edited in it.

        A door back into its own room is legal and useless: it fires the moment
        the traveller arrives on it, so the room reloads forever. Leaving it out
        of the list is cheaper than explaining that afterwards."""
        rooms = self._res("room")
        here = self._sel[1] if self._sel and self._sel[0] == "room" else -1
        want = getattr(self, "_warp_dest", None)
        self._suspend = True
        try:
            self._room_warp_to.remove_all()
            first = None
            for i, r in enumerate(rooms):
                if i == here:
                    continue
                rid = r.get("id")
                self._room_warp_to.append(rid, rid)
                if first is None:
                    first = rid
            if want not in [r.get("id") for i, r in enumerate(rooms)
                            if i != here]:
                want = first
            self._warp_dest = want
            if want:
                self._room_warp_to.set_active_id(want)
        finally:
            self._suspend = False

    def _on_warp_dest(self, combo):
        if self._suspend:
            return
        self._warp_dest = combo.get_active_id()

    def _place_or_remove_warp(self, rm, cx, cy, erase):
        """Put a one-cell door at (cx, cy), or take away the one under it."""
        warps = rm.setdefault("warps", [])
        x, y = cx * 8, cy * 8
        for i, wp in enumerate(warps):
            wx = gbabuild._int(wp.get("x"))
            wy = gbabuild._int(wp.get("y"))
            ww = max(1, gbabuild._int(wp.get("w"), 8))
            wh = max(1, gbabuild._int(wp.get("h"), 8))
            if wx <= x < wx + ww and wy <= y < wy + wh:
                if erase:
                    self.undo.checkpoint(_t("Remove Door"))
                    warps.pop(i)
                    self.undo.commit()
                    return True
                return False
        if erase:
            return False
        dest = getattr(self, "_warp_dest", None)
        if not dest:
            # Every other room is gone, or this is the only one. Say so rather
            # than placing a door the build will drop and report.
            self._flash(_t("A door needs another room to lead to"))
            return False
        self.undo.checkpoint(_t("Add Door"))
        warps.append({"x": x, "y": y, "w": 8, "h": 8, "room": dest,
                      # The middle of the destination's bottom edge is a
                      # defensible default; a door with no arrival point would
                      # drop the traveller at 0,0, in the corner, inside a wall.
                      "tx": 120, "ty": 140})
        self.undo.commit()
        return True

    def _place_or_remove(self, rm, cx, cy, erase):
        """Put an instance in cell (cx, cy), or take the nearest one out."""
        rw = max(16, int(rm.get("w", 240)))
        rh = max(16, int(rm.get("h", 160)))
        gx = max(0, min(rw, cx * 8 + 8))
        gy = max(0, min(rh, cy * 8 + 8))
        if erase:
            best, bd = None, 1e9
            for k, it in enumerate(rm.get("instances", [])):
                d = (it.get("x", 0) - gx) ** 2 + (it.get("y", 0) - gy) ** 2
                if d < bd:
                    bd, best = d, k
            if best is not None and bd < 256:
                del rm["instances"][best]
                return True
            return False
        if not self._room_place:
            # Placing nothing used to be a silent no-op on every click.
            self._flash(_t("Make an Object first, then place it here."))
            return False
        rm.setdefault("instances", []).append(
            {"object": self._room_place, "x": gx, "y": gy})
        return True

    def _room_touched(self):
        self.undo.touch()
        self._save_autosave()
        self._render_tree()
        self._update_head("room")

    def _paint_room_tile(self, rm, cx, cy, erase=False):
        """Place the chosen tile at cell (cx, cy).

        A tile bigger than 8x8 occupies a (size/8)^2 BLOCK of hardware cells and
        is written as consecutive indices in the same block row-major order the
        compiler emits. The block is SNAPPED to its own grid so a level made of
        32x32 tiles lines up instead of overlapping by a few pixels wherever the
        pointer happened to be."""
        tm, cw, ch = self._room_tilemap(rm)
        run = None if erase else self._auto_run_of(self._room_tile)
        if run is not None:
            return self._paint_auto(tm, cw, ch, cx, cy, run)
        span = 1 if erase else max(1, self._tile_span(self._room_tile) // 8)
        if span > 1:
            cx -= cx % span
            cy -= cy % span
        changed = False
        for by in range(span):
            for bx in range(span):
                x, y = cx + bx, cy + by
                if not (0 <= x < cw and 0 <= y < ch):
                    continue
                v = 0 if erase else self._room_tile + by * span + bx
                if tm[y * cw + x] != v:
                    tm[y * cw + x] = v
                    changed = True
        return changed

    def _paint_auto(self, tm, cw, ch, cx, cy, run):
        """Lay a terrain and re-fit it and its neighbours.

        The neighbours matter as much as the cell: a tile placed beside an
        existing one changes what THAT one should look like, and a tool that
        only fits the cell under the pointer leaves a seam behind every stroke
        -- which reads as auto-tiling that half works."""
        bases, span = run
        step = max(1, span // 8)
        if step > 1:
            cx -= cx % step
            cy -= cy % step
        if not (0 <= cx < cw and 0 <= cy < ch):
            return False
        changed = self._stamp(tm, cw, ch, cx, cy, bases[0], step)
        # Fit the new cell first, then its four neighbours against it.
        if self._auto_fit(tm, cw, ch, cx, cy, bases, span):
            changed = True
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            if self._auto_fit(tm, cw, ch, cx + dx * step, cy + dy * step,
                              bases, span):
                changed = True
        return changed

    def _on_room_motion(self, w, ev):
        rm = self._cur_room()
        if self._room_mode != "tiles" or not rm:
            return False
        scale, ox, oy = self._room_geom()
        erase = bool(ev.state & Gdk.ModifierType.BUTTON3_MASK)
        if self._paint_room_tile(rm, int((ev.x - ox) / scale) // 8,
                                 int((ev.y - oy) / scale) // 8, erase=erase):
            self._room_touched()
            w.queue_draw()
        return True

    def _on_room_key(self, w, ev):
        """Lay out a level from the keyboard: arrows move a cell cursor, Space
        places (a tile or an object), Delete takes away."""
        rm = self._cur_room()
        if not rm:
            return False
        cw = max(1, max(16, int(rm.get("w", 240))) // 8)
        ch = max(1, max(16, int(rm.get("h", 160))) // 8)
        cur = self._room_cur
        step = {Gdk.KEY_Left: (-1, 0), Gdk.KEY_Right: (1, 0),
                Gdk.KEY_Up: (0, -1), Gdk.KEY_Down: (0, 1)}.get(ev.keyval)
        if step is not None:
            cur[0] = max(0, min(cw - 1, cur[0] + step[0]))
            cur[1] = max(0, min(ch - 1, cur[1] + step[1]))
            if ev.state & Gdk.ModifierType.SHIFT_MASK \
                    and self._room_mode == "tiles" \
                    and self._paint_room_tile(rm, cur[0], cur[1]):
                self._room_touched()
        elif ev.keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            done = (self._paint_room_tile(rm, cur[0], cur[1])
                    if self._room_mode == "tiles"
                    else self._place_or_remove(rm, cur[0], cur[1], False))
            if done:
                self._room_touched()
        elif ev.keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            done = (self._paint_room_tile(rm, cur[0], cur[1], erase=True)
                    if self._room_mode == "tiles"
                    else self._place_or_remove(rm, cur[0], cur[1], True))
            if done:
                self._room_touched()
        elif ev.keyval in (Gdk.KEY_1, Gdk.KEY_KP_1):
            self._set_room_mode("objects")
        elif ev.keyval in (Gdk.KEY_2, Gdk.KEY_KP_2):
            self._set_room_mode("tiles")
        else:
            return False
        w.queue_draw()
        return True

    # ================= build & run =================
    def _show_log(self, log):
        # "Log" is what a developer calls this. What it is, to the person
        # who just pressed Build, is the detail behind what happened.
        dlg = Gtk.Dialog(title=_t("Build Details"), transient_for=self,
                         modal=True)
        dlg.set_default_size(640, 420)
        tv = Gtk.TextView(); tv.set_editable(False); tv.set_monospace(True)
        tv.get_buffer().set_text(log or "")
        sc = Gtk.ScrolledWindow(); sc.add(tv); sc.set_vexpand(True)
        dlg.get_content_area().pack_start(sc, True, True, 0)
        dlg.add_button(_t("Close"), Gtk.ResponseType.CLOSE)
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
        head.pack_start(nbicons.image(icon, 26, MUTED), False, False, 0)
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
            sb.get_style_context().add_class("quietbtn")
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
            return _t("The compiler is not installed.")
        if "no space left" in low:
            return _t("The disk is full, so the game file could not be written.")
        if "could not write generated source" in low:
            return _t("The working files for the build could not be written.")
        if "could not turn this project into code" in low:
            # The app's own generator failed, not the project and not the
            # disk. Stay distinct from the generic message below, so nobody
            # goes looking for disk space.
            return _t("The project could not be turned into code.")
        return _t("The compiler stopped part-way through.")

    def _flash(self, text):
        try:
            self._status.set_text(text)
        except Exception:
            pass

    # ================= small dialogs =================
    def _confirm(self, heading, body, ok_label):
        """Modal are-you-sure. Returns True only if the confirming button was
        pressed. Cancel is the DEFAULT response, so a reflexive Enter can never
        destroy anything, and it is the button that holds focus when the dialog
        opens."""
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        area = dlg.get_content_area()
        area.set_margin_top(16); area.set_margin_bottom(10)
        area.set_margin_start(20); area.set_margin_end(20)
        head = Gtk.Label(label=heading, xalign=0)
        head.get_style_context().add_class("dlghead")
        area.pack_start(head, False, False, 0)
        msg = Gtk.Label(label=body, xalign=0)
        msg.set_line_wrap(True)
        # width_chars sets the measure; max_width_chars alone only caps it and
        # leaves GTK free to size the card down to a cramped column.
        msg.set_width_chars(40)
        msg.set_max_width_chars(42)
        area.pack_start(msg, False, False, 10)
        cancel = dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        ok = dlg.add_button(ok_label, Gtk.ResponseType.OK)
        ok.get_style_context().add_class("destructive-action")
        dlg.set_default_response(Gtk.ResponseType.CANCEL)
        dlg.show_all()
        cancel.grab_focus()
        resp = dlg.run()
        dlg.destroy()
        return resp == Gtk.ResponseType.OK

    def _prompt_text(self, title, initial, cb):
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        area = dlg.get_content_area()
        area.set_margin_top(16); area.set_margin_bottom(10)
        area.set_margin_start(20); area.set_margin_end(20)
        # State the question INSIDE the dialog. With only the window title to
        # go on this was a bare text box with no indication of what it wanted,
        # and every other confirm/prompt in the OS carries its own heading
        # rather than trusting the window manager to draw one.
        head = Gtk.Label(label=title, xalign=0)
        head.get_style_context().add_class("dlghead")
        area.pack_start(head, False, False, 0)
        e = Gtk.Entry(); e.set_text(initial); e.set_activates_default(True)
        area.pack_start(e, False, False, 12)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button("OK", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        dlg.set_default(ok)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            cb(e.get_text().strip())
        dlg.destroy()

    def _choose(self, title, labels, cb):
        dlg = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dlg.set_default_size(240, 320)
        # Say what the list is for. Without this the dialog was a bare list of
        # names with a Cancel button and nothing stating the question.
        head = Gtk.Label(label=title, xalign=0)
        head.get_style_context().add_class("dlghead")
        head.set_margin_top(14); head.set_margin_bottom(10)
        head.set_margin_start(16); head.set_margin_end(16)
        dlg.get_content_area().pack_start(head, False, False, 0)
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

    # ---- reading a project back: the shape nothing may assume ----
    #
    # THE DEFECT THIS EXISTS FOR: every reader in the app trusted the file. A
    # resource list stored as an OBJECT instead of a list, or one resource stored
    # as a string, and the window did not open AT ALL — AttributeError inside
    # __init__ — which on this appliance, with no shell and no file manager for
    # the config directory, means the app is dead for good and the game with it.
    # A dozen more shapes opened the window and then threw inside an editor or
    # the compiler. All of it is now coerced at ONE point, on the way in, and
    # anything that cannot be understood is dropped and COUNTED so the person
    # can be told rather than left to wonder.
    @staticmethod
    def _records(v):
        """A list of resources: a list as it is, an object as its values in file
        order (the shape a hand-edited or half-written store often has), and
        anything else as nothing."""
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            return list(v.values())
        return []

    @staticmethod
    def _ints(v, n, fill=0, lo=None, hi=None):
        """`v` as exactly n whole numbers."""
        out = []
        src = v if isinstance(v, (list, tuple)) else []
        for i in range(n):
            x = gbabuild._int(src[i], fill) if i < len(src) else fill
            if lo is not None and x < lo:
                x = fill
            if hi is not None and x > hi:
                x = fill
            out.append(x)
        return out

    def _sane_project(self, data):
        """(project, dropped): `data` in the shape every editor and the compiler
        expect. Never raises."""
        lost = [0]

        def keep(rec):
            if isinstance(rec, dict):
                return True
            lost[0] += 1
            return False

        if not isinstance(data, dict):
            return None, 0
        # A project is recognised by holding any of the things a project holds,
        # not by one particular key: requiring "objects" threw away a whole game
        # whose only fault was a missing key.
        if not any(k in data for k in ("sprites", "tilesets", "sounds",
                                       "objects", "rooms", "scripts",
                                       "tables", "start_room", "name")):
            return None, 0
        out = {"name": data.get("name") if isinstance(data.get("name"), str)
               else _t("Game")}
        save_type = str(data.get("save_type") or "sram").strip().lower()
        out["save_type"] = save_type if save_type in (
            "sram", "flash64", "flash128") else "sram"

        sprites = []
        for rec in self._records(data.get("sprites")):
            if not keep(rec):
                continue
            w = gbabuild._int(rec.get("w"), 16) or 16
            h = gbabuild._int(rec.get("h"), w) or w
            w = min(64, max(8, w))
            h = min(64, max(8, h))
            frames = [self._ints(fr, w * h, TRANSPARENT)
                      for fr in self._records(rec.get("frames"))]
            sprites.append({
                "id": self._safe_id(rec.get("id"), "spr", sprites),
                "w": w, "h": h,
                "ox": gbabuild._int(rec.get("ox"), w // 2),
                "oy": gbabuild._int(rec.get("oy"), h // 2),
                "anim_speed": gbabuild._int(rec.get("anim_speed"), 0),
                "frames": frames or [[TRANSPARENT] * (w * h)]})
        out["sprites"] = sprites

        tilesets = []
        for rec in self._records(data.get("tilesets")):
            if not keep(rec):
                continue
            # A tile set records the size it was painted at. Anything else --
            # a missing key from a project made before tiles could grow, or a
            # damaged value -- reads as 8, which is what those tiles are.
            size = gbabuild._int(rec.get("size"), 8)
            size = size if size in TILE_SIZES else 8
            tiles = [self._ints(t, size * size, TRANSPARENT)
                     for t in self._records(rec.get("tiles"))]
            tiles = tiles or [[TRANSPARENT] * (size * size)]
            # Which tiles are walls and floors. One flag per tile, padded and
            # trimmed to the tile count -- a shorter list from an older project
            # means "the rest are not solid", which is how they behaved before
            # the flag existed.
            raw_solid = rec.get("solid")
            if raw_solid is not None and not isinstance(raw_solid, list):
                lost[0] += 1          # solid flags were there and unreadable
            solid = [bool(v) for v in raw_solid] if isinstance(raw_solid, list) \
                else []
            solid = (solid + [False] * len(tiles))[:len(tiles)]
            # The first of sixteen variants of one terrain. Dropped rather
            # than clamped when it no longer has fifteen tiles after it: a run
            # that runs past the end of the set would paint another terrain's
            # tiles, which looks like corruption.
            ab = rec.get("auto_base")
            entry = {
                "id": self._safe_id(rec.get("id"), "ts", tilesets),
                "size": size,
                "solid": solid,
                "tiles": tiles}
            if isinstance(ab, int) and 0 <= ab and ab + 16 <= len(tiles):
                entry["auto_base"] = ab
            elif ab is not None:
                lost[0] += 1          # a run with no room left to finish
            tilesets.append(entry)
        out["tilesets"] = tilesets

        sounds = []
        for rec in self._records(data.get("sounds")):
            if not keep(rec):
                continue
            steps = gbabuild._int(rec.get("steps"), 16)
            steps = steps if steps in (8, 16, 32) else 16
            sounds.append({
                "id": self._safe_id(rec.get("id"), "snd", sounds),
                "tempo": min(30, max(1, gbabuild._int(rec.get("tempo"), 8))),
                "loop": bool(rec.get("loop", True)),
                "steps": steps,
                "lead": self._ints(rec.get("lead"), steps),
                "bass": self._ints(rec.get("bass"), steps),
                # The noise channel and the four timbre settings. Every one of
                # them was already read by the runtime and never written by the
                # generator, so no built game has ever had percussion, and
                # every sound effect stopped the music instead of layering.
                "drum": self._ints(rec.get("drum"), steps, 0, 0, 4),
                "kind": 1 if gbabuild._int(rec.get("kind"), 0) else 0,
                "duty": min(4, max(0, gbabuild._int(rec.get("duty"), 0))),
                "vol": min(15, max(0, gbabuild._int(rec.get("vol"), 0))),
                "decay": min(7, max(0, gbabuild._int(rec.get("decay"), 0))),
                # 0 = anything may interrupt this, which is what every sound
                # did before priority existed.
                "prio": min(7, max(0, gbabuild._int(rec.get("prio"), 0))),
                # Sampled audio, signed 8-bit at 16384 Hz. Kept out of the
                # record entirely when absent, so a pattern sound does not
                # carry an empty list through every save.
                **({"pcm": [min(127, max(-128, gbabuild._int(v, 0)))
                            for v in rec["pcm"]]}
                   if isinstance(rec.get("pcm"), list) and rec["pcm"]
                   else _count_if(lost, rec.get("pcm") is not None
                                  and not isinstance(rec.get("pcm"), list)))})
        out["sounds"] = sounds

        def sane_actions(v, depth=0):
            acts = []
            if depth > 8:
                return acts
            for a in (v if isinstance(v, list) else []):
                if not isinstance(a, dict) or not isinstance(a.get("kind"), str):
                    lost[0] += 1
                    continue
                act = {k: val for k, val in a.items() if k != "children"}
                if a.get("kind") in CONTAINER_ACTIONS or "children" in a:
                    act["children"] = sane_actions(a.get("children"), depth + 1)
                acts.append(act)
            return acts

        objects = []
        for rec in self._records(data.get("objects")):
            if not keep(rec):
                continue
            events = []
            for ev in self._records(rec.get("events")):
                if not isinstance(ev, dict):
                    lost[0] += 1
                    continue
                e = dict(ev)
                e["type"] = ev.get("type") if isinstance(ev.get("type"), str) \
                    else "step"
                e["actions"] = sane_actions(ev.get("actions"))
                events.append(e)
            spr = rec.get("sprite")
            obj = {
                "id": self._safe_id(rec.get("id"), "obj", objects),
                "sprite": spr if isinstance(spr, str) and spr else None,
                "visible": rec.get("visible") is not False,
                "solid": bool(rec.get("solid")),
                # 0 keeps the behaviour a project had before these existed:
                # move freely, draw in front, use the whole sprite as the box.
                "tilecol": min(2, max(0, gbabuild._int(rec.get("tilecol"), 0))),
                "depth": min(7, max(0, gbabuild._int(rec.get("depth"), 0))),
                "bb_inset": min(64, max(0,
                                        gbabuild._int(rec.get("bb_inset"), 0))),
                "hurt_frames": min(255, max(0,
                    gbabuild._int(rec.get("hurt_frames"), 0))),
                "events": events}
            if isinstance(rec.get("_was"), str) and rec.get("_was"):
                obj["_was"] = rec["_was"]     # what its sprite USED to be
            objects.append(obj)
        out["objects"] = objects

        rooms = []
        for rec in self._records(data.get("rooms")):
            if not keep(rec):
                continue
            w = min(1024, max(16, gbabuild._int(rec.get("w"), 240)))
            h = min(1024, max(16, gbabuild._int(rec.get("h"), 160)))
            insts = []
            for it in self._records(rec.get("instances")):
                if not isinstance(it, dict):
                    lost[0] += 1
                    continue
                obj = it.get("object")
                insts.append({"object": obj if isinstance(obj, str) else "",
                              "x": gbabuild._int(it.get("x"), 0),
                              "y": gbabuild._int(it.get("y"), 0)})
            # Doorways. A warp whose target room was deleted keeps its target
            # NAME so the reference can be repaired -- the generator reports it
            # rather than the loader quietly discarding the door.
            warps = []
            if rec.get("warps") is not None and not isinstance(
                    rec.get("warps"), (list, tuple)):
                lost[0] += 1          # doors were there and unreadable
            for wp in self._records(rec.get("warps")):
                if not isinstance(wp, dict):
                    lost[0] += 1
                    continue
                room_id = wp.get("room")
                warps.append({
                    "x": gbabuild._int(wp.get("x"), 0),
                    "y": gbabuild._int(wp.get("y"), 0),
                    "w": max(1, gbabuild._int(wp.get("w"), 16)),
                    "h": max(1, gbabuild._int(wp.get("h"), 16)),
                    "room": room_id if isinstance(room_id, str) else "",
                    "tx": gbabuild._int(wp.get("tx"), 0),
                    "ty": gbabuild._int(wp.get("ty"), 0)})
            cells = (w // 8) * (h // 8)
            tiles = rec.get("tiles")
            rooms.append({
                "id": self._safe_id(rec.get("id"), "rm", rooms),
                "w": w, "h": h,
                "speed": min(60, max(1, gbabuild._int(rec.get("speed"), 60))),
                "bg": rec.get("bg") if isinstance(rec.get("bg"), str)
                else "#101820",
                "instances": insts,
                "tiles": self._ints(tiles, cells, 0)
                if isinstance(tiles, list) else [0] * cells,
                # The parallax layer: a fixed 32x32 repeating map, because the
                # hardware wraps it and a different size would not tile.
                "far": self._ints(rec.get("far"), 1024, 0)
                if isinstance(rec.get("far"), list)
                else _count_none(lost, rec.get("far")),
                "far_div": min(8, max(1, gbabuild._int(rec.get("far_div"), 2))),
                "edge_open": bool(rec.get("edge_open")),
                "warps": warps})
        out["rooms"] = rooms

        tables = []
        for rec in self._records(data.get("tables")):
            if not keep(rec):
                continue
            cols = []
            for c in self._records(rec.get("columns")):
                if not isinstance(c, dict):
                    lost[0] += 1
                    continue
                nm = c.get("name")
                ty = c.get("type")
                cols.append({
                    "name": nm if isinstance(nm, str) and nm.strip()
                    else "col%d" % (len(cols) + 1),
                    "type": ty if ty in COLUMN_C else "text"})
            if rec.get("columns") is not None and not cols:
                lost[0] += 1          # headings were there and unreadable
            if not cols:
                cols = [{"name": "name", "type": "text"}]
            # Rows are padded and trimmed to the columns. A row longer than the
            # header means a column was deleted; a row shorter means one was
            # added. Both are ordinary edits, and neither should cost the row.
            rows = []
            for r in self._records(rec.get("rows")):
                if not isinstance(r, list):
                    lost[0] += 1
                    continue
                row = list(r[:len(cols)])
                while len(row) < len(cols):
                    row.append("" if cols[len(row)]["type"] == "text" else 0)
                rows.append(row)
            tables.append({
                "id": self._safe_id(rec.get("id"), "tbl", tables),
                "columns": cols,
                "rows": rows})
        out["tables"] = tables

        scripts = []
        for rec in self._records(data.get("scripts")):
            if not keep(rec):
                continue
            code = rec.get("code")
            scripts.append({
                "id": self._safe_id(rec.get("id"), "scr", scripts),
                "name": rec.get("name") if isinstance(rec.get("name"), str)
                else None,
                "folder": rec.get("folder") if isinstance(rec.get("folder"),
                                                          str) else None,
                "code": code if isinstance(code, str)
                else _count_str(lost, code)})
        out["scripts"] = scripts

        start = data.get("start_room")
        ids = [r["id"] for r in rooms]
        out["start_room"] = start if (isinstance(start, str) and start in ids) \
            else (ids[0] if ids else None)
        return out, lost[0]

    @staticmethod
    def _safe_id(v, prefix, sofar):
        """A usable, unique id for a resource whose own is missing or damaged."""
        rid = gbabuild._cid(v, prefix) if isinstance(v, str) and v.strip() \
            else ""
        taken = {r.get("id") for r in sofar}
        if not rid or rid in taken:
            n = len(sofar) + 1
            rid = "%s_%d" % (prefix, n)
            while rid in taken:
                n += 1
                rid = "%s_%d" % (prefix, n)
        return rid

    def _load_autosave(self):
        path = CFG_FILE
        if not os.path.exists(path) and os.path.exists(LEGACY_CFG_FILE):
            path = LEGACY_CFG_FILE
        try:
            with open(path) as fh:
                data = json.load(fh)
        except Exception:
            return
        try:
            proj, lost = self._sane_project(data)
        except Exception:
            return
        if proj is None:
            return
        self.proj = proj
        # Something was dropped. The previous behaviour was to say nothing at
        # all, which left a person looking at a game with a piece missing and no
        # idea why; nbapp keeps one .bak, but nobody on this machine can reach it.
        self._load_note = lost

    def _on_destroy(self, *_):
        if self._layout_save_timer is not None:
            GLib.source_remove(self._layout_save_timer)
            self._layout_save_timer = None
        self._save_autosave()
        return False

    # ================= undo =================
    def _snapshot(self):
        """The whole project, plus the selection as volatile state.

        Keys beginning with "_" ride along but do not make a snapshot a NEW
        state (see nbapp.UndoHistory), so moving the selection never uses up an
        undo step, and undoing lands you back where you were working."""
        return {"proj": copy.deepcopy(self.proj),
                "_sel": self._sel, "_event": self._sel_event,
                "_frame": self._sel_frame, "_tile": self._sel_tile}

    def _restore(self, state):
        self.proj = copy.deepcopy(state.get("proj") or {})
        for k in ("sprites", "sounds", "tilesets", "objects", "rooms"):
            self.proj.setdefault(k, [])
        self._sel = state.get("_sel")
        self._sel_event = state.get("_event")
        self._sel_frame = state.get("_frame") or 0
        self._sel_tile = state.get("_tile") or 0
        self._tile_pb_cache.clear()
        self._save_autosave()
        self._render_tree()
        if self._sel_res() is not None:
            self._refresh_editor()
        else:
            self._sel = None
            self._render_tree()
            self._editor_stack.show("welcome")

    # ================= keyboard =================
    # Every shortcut the app binds. The per-pane ones are printed in the tooltip
    # of the control they work on (see _acc); these are printed in the menus.
    def _on_sdk_key(self, _w, ev):
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(ev.state & Gdk.ModifierType.SHIFT_MASK)
        if nbapp.undo_keys(self.undo, ev):
            return True
        if ctrl and shift and ev.keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self._find_in_project()
            return True
        if ctrl and ev.keyval in (Gdk.KEY_b, Gdk.KEY_B):
            self._file_export()
            return True
        if ctrl:
            for n, (kind, _h, _new, _one) in enumerate(KINDS):
                if ev.keyval in (getattr(Gdk, "KEY_%d" % (n + 1)),
                                 getattr(Gdk, "KEY_KP_%d" % (n + 1))):
                    self._add_resource(kind)
                    return True
        if ev.keyval == Gdk.KEY_F2:
            self._rename_resource()
            return True
        focus = self.get_focus()
        # Never take a key off a text field: p in a variable name is a p.
        if isinstance(focus, (Gtk.Entry, Gtk.TextView)):
            return False
        if ev.keyval == Gdk.KEY_Delete and self._focus_in_browser(focus):
            self._delete_resource()
            return True
        if focus in (getattr(self, "_spr_canvas", None),
                     getattr(self, "_tile_canvas", None)):
            for tid, _icon, _label, key in TOOLS:
                if ev.keyval == getattr(Gdk, "KEY_%s" % key.lower()):
                    self._set_tool(tid)
                    return True
        return False

    def _focus_in_browser(self, w):
        while w is not None:
            if w is self._tree_body:
                return True
            w = w.get_parent()
        return False

    # ================= menus =================
    _WS_MENU = "View"

    def _results_dialog(self, title, results, query=False):
        """Compact searchable result list; 620x500 stays usable at 1024x740."""
        dlg = Gtk.Dialog(title=_t(title), transient_for=self, modal=True)
        dlg.set_default_size(620, 500)
        area = dlg.get_content_area()
        area.set_spacing(8); area.set_margin_top(12); area.set_margin_bottom(8)
        area.set_margin_start(14); area.set_margin_end(14)
        entry = None
        if query:
            entry = Gtk.SearchEntry()
            entry.set_placeholder_text(_t("Search project"))
            area.pack_start(entry, False, False, 0)
        store = Gtk.ListStore(str, str, str, object)
        view = Gtk.TreeView(model=store)
        for n, head in enumerate((_t("Kind"), _t("Owner"), _t("Matched text"))):
            cell = Gtk.CellRendererText()
            cell.set_property("ellipsize", Pango.EllipsizeMode.END)
            view.append_column(Gtk.TreeViewColumn(head, cell, text=n))
        has_resources = any(self.proj.get(kind) for kind, *_rest in KINDS)
        empty = Gtk.Label(label=_t(_find_empty_label(has_resources, "")))
        empty.get_style_context().add_class("emptyrow")
        stack = Gtk.Stack(); stack.add_named(view, "results"); stack.add_named(empty, "empty")
        sc = Gtk.ScrolledWindow(); sc.add(stack); sc.set_vexpand(True)
        area.pack_start(sc, True, True, 0)
        current = [list(results)]
        def fill(rows):
            current[0] = rows; store.clear()
            for rec in rows:
                store.append([_t(str(rec["kind"])), str(rec["owner"]),
                              rec["snippet"], rec])
            query_text = entry.get_text() if entry is not None else ""
            empty.set_text(_t(_find_empty_label(has_resources, query_text)))
            stack.set_visible_child_name("results" if rows else "empty")
        fill(current[0])
        def activate(_view, path, _column):
            rec = store[path][3]
            dlg.response(Gtk.ResponseType.OK)
            self._activate_project_result(rec)
        view.connect("row-activated", activate)
        if entry is not None:
            entry.connect("search-changed", lambda e: fill(self._project_search(e.get_text())))
        dlg.add_button(_t("Close"), Gtk.ResponseType.CLOSE)
        dlg.show_all()
        if entry is not None:
            entry.grab_focus()
        dlg.run(); dlg.destroy()

    def _find_in_project(self):
        self._results_dialog("Find in Project", [], query=True)

    def _show_where_used(self):
        selected = self._sel_res()
        if not selected or not self._sel:
            return
        self._results_dialog("Where Is This Used",
                             self._where_used(self._sel[0], selected.get("id")))

    def menu_items(self, name):
        if name == self._WS_MENU:
            ws = self._editor_stack
            open_now = set(ws.open_ids())
            items = []
            for pid, label in (("sprite", "Sprite"), ("tileset", "Tiles"),
                               ("sound", "Sound"), ("object", "Object"),
                               ("room", "Room"), ("script", "Script"),
                               ("table", "Table"), ("palette", "Palettes"),
                               ("world", "World"), ("help", "Help")):
                mark = "\u2713 " if pid in open_now else "    "
                items.append((mark + _t(label),
                              lambda p=pid: self._editor_stack.show(p)))
            items += [
                nbapp.SEP,
                ("Split Right",
                 (lambda: self._split_pane(Gtk.Orientation.HORIZONTAL))
                 if ws.can_split() else None),
                ("Split Down",
                 (lambda: self._split_pane(Gtk.Orientation.VERTICAL))
                 if ws.can_split() else None),
                ("Close This Pane",
                 self._close_pane
                 if ws._active.current not in (None, "welcome") else None),
                nbapp.SEP,
                nbapp.SEP,
                ("World Map", self._open_world),
                nbapp.SEP,
                ("Reset Layout", self._reset_layout),
            ]
            return items
        if name == "File":
            return [
                ("New Project", self._file_new),
                ("Project Settings…", self._project_settings),
                ("Open Example Game", self._file_example),
                (_t("Import MIDI…"), self._import_midi),
                ("Import Sound\u2026", self._import_wav),
                ("Open Project…", self._file_open),
                nbapp.SEP,
                ("Save Project As…", self._file_save_as),
                (_acc("Build & Play", "Ctrl+R"), self._file_play),
                (_acc("Build & Export…", "Ctrl+B"), self._file_export),
                ("Export for a Link Cable…", self._file_export_mb),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Edit":
            # The app had no undo at all: a mis-aimed fill wiped a drawing and
            # the only way back was to paint it again.
            return nbapp.undo_menu_items(self.undo) + [nbapp.SEP] \
                + super().menu_items("Edit") + [nbapp.SEP,
                   (_acc("Find in Project…", "Ctrl+Shift+F"), self._find_in_project)]
        if name == "Resource":
            # Rename and Delete both act on the selected resource and did
            # nothing at all with none selected — a live-looking item that
            # silently ignored the click. They now grey out instead. Delete
            # stops and asks (it names the resource and what else will break),
            # so it carries the ellipsis that promises the question.
            picked = self._sel_res() is not None
            items = []
            for n, (kind, _h, new, _one) in enumerate(KINDS):
                items.append((_acc(new, "Ctrl+%d" % (n + 1)),
                              lambda k=kind: self._add_resource(k)))
            return items + [
                nbapp.SEP,
                (_acc("Rename…", "F2"), self._rename_resource if picked else None),
                ("Move to Folder…", self._move_to_folder if picked else None),
                ("Where Is This Used?", self._show_where_used
                 if picked and self._sel[0] in ("sprite", "sound", "object",
                                                "script", "table") else None),
                (_acc("Delete…", "Del"), self._delete_resource if picked else None),
            ]
        if name == "Help":
            done, total = 0, 0
            try:
                done, total = gbahelp.course_progress(self.proj)
            except Exception:                               # noqa: BLE001
                pass
            ev = self._cur_event()
            return [
                (_acc("Reference", "F1"), lambda: self._open_help(None)),
                ("Course in C  (%d/%d)" % (done, total),
                 lambda: self._open_help("c01")),
                nbapp.SEP,
                ("Show C for This Event", self._show_event_c if ev else None),
                nbapp.SEP,
                ("Recipes", lambda: self._open_help("r_platform")),
                ("Actions", lambda: self._open_help("act_motion")),
                ("Engine Calls", lambda: self._open_help("eng_instances")),
                ("Hardware", lambda: self._open_help("reg_interrupts")),
            ]
        if name == "Build":
            return [
                (_acc("Build & Export…", "Ctrl+B"), self._file_export),
                ("What This Game Costs…", self._show_budget),
                ("Build Details…",
                 lambda: self._show_log(getattr(self, "_last_log", "")
                                        or _t("The build log is empty."))),
            ]
        return super().menu_items(name)

    def _project_settings(self):
        """Project-wide cartridge settings."""
        dlg = Gtk.Dialog(title=_t("Project Settings"), transient_for=self,
                         modal=True)
        area = dlg.get_content_area()
        area.set_margin_top(16); area.set_margin_bottom(10)
        area.set_margin_start(20); area.set_margin_end(20)
        head = Gtk.Label(label=_t("Project Settings"), xalign=0)
        head.get_style_context().add_class("dlghead")
        area.pack_start(head, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.pack_start(Gtk.Label(label=_t("Save memory"), xalign=0),
                       True, True, 0)
        combo = Gtk.ComboBoxText()
        for key, label in (("sram", "SRAM"), ("flash64", "Flash 64 KB"),
                           ("flash128", "Flash 128 KB")):
            combo.append(key, _t(label))
        combo.set_active_id(self.proj.get("save_type") or "sram")
        self._project_save_type = combo
        combo.set_tooltip_text(_t("Cartridge memory used for saved games"))
        row.pack_start(combo, False, False, 0)
        area.pack_start(row, False, False, 12)
        dlg.add_button(_t("Cancel"), Gtk.ResponseType.CANCEL)
        ok = dlg.add_button(_t("Apply"), Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            self.undo.checkpoint(_t("Project Settings"))
            self.proj["save_type"] = combo.get_active_id() or "sram"
            self._save_autosave()
            self.undo.commit()
        dlg.destroy()

    def _project_has_work(self):
        """True when the open project holds something the user made.

        A brand-new, untouched project is not worth asking about; a project with
        rooms, sprites or events in it is."""
        p = getattr(self, "proj", None) or {}
        for key in ("rooms", "sprites", "tiles", "events", "scripts", "sounds"):
            try:
                if p.get(key):
                    return True
            except AttributeError:
                return False
        return False

    def _ok_to_discard(self, heading):
        """Ask before replacing the open project. gbasdk AUTOSAVES, so the
        moment the model is replaced the old project is gone from disk too --
        there is no unsaved-changes state to fall back on and no undo across a
        project swap. Every other document app in this OS confirms first."""
        if not self._project_has_work():
            return True
        return self._confirm(
            heading,
            _t("The open project will be replaced. This cannot be undone."),
            _t("Replace"))

    def _file_new(self):
        if not self._ok_to_discard(_t("Start a new project?")):
            return
        self._new_project()
        self._sel = None
        self._save_autosave()
        self._render_tree()
        self._editor_stack.show("welcome")

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
            # The same normal form a NEW project has and the loader normalises
            # to. Without it the example round-trips unequal to itself: saved
            # without the key, read back with its default.
            "save_type": "sram",
            "sprites": [
                {"id": "spr_player", "w": 16, "h": 16, "ox": 8, "oy": 8,
                 "anim_speed": 0, "frames": [player]},
                {"id": "spr_coin", "w": 16, "h": 16, "ox": 8, "oy": 8,
                 "anim_speed": 0, "frames": [coin]}],
            "sounds": [],
            # The wall tile is solid and the grass is not, which is what
            # makes the example demonstrate tile collision rather than merely
            # contain a picture of a wall.
            "tilesets": [{"id": "ts_world", "size": 8,
                          "solid": [False, True],
                          "tiles": [grass, wall]}],
            "objects": [
                {"id": "obj_player", "sprite": "spr_player", "visible": True,
                 "solid": False, "tilecol": 1, "depth": 0, "bb_inset": 0,
                 "hurt_frames": 0,
                 "events":
                     [{"type": "step", "actions": step_actions}] + key_events},
                {"id": "obj_coin", "sprite": "spr_coin", "visible": True,
                 "solid": False, "tilecol": 0, "depth": 0, "bb_inset": 0,
                 "hurt_frames": 0,
                 "events": [
                     {"type": "collision", "object": "obj_player", "actions": [
                         {"kind": "add_score", "value": "10"},
                         {"kind": "destroy_self"}]}]}],
            "rooms": [
                {"id": "rm_world", "w": 240, "h": 160, "speed": 60,
                 "bg": "#0C2818", "tiles": tm,
                 "far": None, "far_div": 2, "edge_open": False, "warps": [],
                 "instances": [
                     {"object": "obj_player", "x": 120, "y": 90},
                     {"object": "obj_coin", "x": 48, "y": 48},
                     {"object": "obj_coin", "x": 190, "y": 56},
                     {"object": "obj_coin", "x": 80, "y": 128},
                     {"object": "obj_coin", "x": 176, "y": 120}]}],
            "start_room": "rm_world",
            # Empty, not absent. A template that omits a kind writes projects
            # a later load has to migrate, and the migration is where an older
            # file quietly changes shape.
            "scripts": [],
            "tables": [],
        }

    def _file_example(self):
        if not self._ok_to_discard(_t("Load the example game?")):
            return
        self.proj = self._example_project()
        self._path = None
        self._sel = None
        self._sel_event = None
        self._sel_action = None
        self._save_autosave()
        self._render_tree()
        self._select_resource("room", 0)
        self._flash("Loaded the example game — Build & Play to try it")

    PCM_RATE = 16384          # the runtime's only rate; see rt_pcm_play
    PCM_MAX_SECONDS = 8       # 16 KB per second of ROM

    def _import_midi(self):
        path = nbpicker.open_file(self, title=_t("Import MIDI"),
                                  start_dir=os.path.join(HOME, "Documents"),
                                  patterns=("*.mid", "*.midi"))
        if not path:
            return
        try:
            with open(path, "rb") as source:
                sound, report = _midi_to_sound(
                    source.read(), os.path.splitext(os.path.basename(path))[0])
        except _MidiUnsupported as exc:
            self._flash(str(exc))
            return
        except Exception:                                  # noqa: BLE001
            # Decoder details and paths are not repair instructions. Corrupt
            # and unfamiliar inputs get one stable, translated refusal.
            self._flash(_t("This is not a readable MIDI file."))
            return
        sounds = self._res("sound")
        self.undo.checkpoint(_t("Import MIDI"))
        sound["id"] = self._uid("snd", sounds)
        sounds.append(sound)
        self._save_autosave()
        self._render_tree()
        self._select_resource("sound", len(sounds) - 1)
        self.undo.commit()
        self._flash(report["summary"])

    def _import_wav(self):
        """Bring a .wav in as a sampled sound.

        Converted here rather than on the cartridge: the GBA has no resampler,
        and its timer period IS the sample rate. Doing it at import means one
        rate everywhere and no way to get "it plays too fast"."""
        path = nbpicker.open_file(self, title=_t("Import Sound"),
                                  start_dir=os.path.join(HOME, "Documents"),
                                  patterns=("*.wav",))
        if not path:
            return
        try:
            data, secs = self._read_wav(path)
        except _SoundUnsupported as exc:
            # OUR OWN refusal, already phrased for the author and already
            # translated. A blanket handler swallowed this and said "could not
            # be read" instead -- which is true and useless, because the most
            # common reason a WAV is rejected here is that it is 24- or 32-bit,
            # which is what most audio tools export by DEFAULT, and the author
            # can fix it in one export. Hiding an internal error must not cost
            # the one message that tells somebody what to do.
            self._flash(str(exc))
            return
        except Exception:                                  # noqa: BLE001
            # A decoder exception, on the other hand, carries a raw errno and
            # the source's absolute path. Neither helps repair the WAV, and
            # neither belongs on screen.
            self._flash(_t("This file could not be read as a sound."))
            return
        if not data:
            self._flash(_t("That sound is empty"))
            return
        lst = self._res("sound")
        self.undo.checkpoint(_t("Import Sound"))
        rid = self._uid("snd", lst)
        lst.append({"id": rid, "tempo": 8, "loop": False, "steps": 16,
                    "lead": [0] * 16, "bass": [0] * 16, "drum": [0] * 16,
                    "kind": 1, "duty": 0, "vol": 0, "decay": 0, "pcm": data})
        self._save_autosave()
        self._render_tree()
        self._select_resource("sound", len(lst) - 1)
        self.undo.commit()
        self._flash(_t("Imported %d seconds, %d KB of cartridge")
                    % (int(secs) or 1, max(1, len(data) // 1024)))

    def _read_wav(self, path):
        """(signed 8-bit samples at PCM_RATE, seconds).

        Nearest-neighbour resampling and a channel average: both are audibly
        crude and both are the right trade here, because the destination is an
        8-bit FIFO on a handheld speaker."""
        import wave
        with wave.open(path, "rb") as w:
            nch = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate() or self.PCM_RATE
            nframes = w.getnframes()
            cap = int(rate * self.PCM_MAX_SECONDS)
            raw = w.readframes(min(nframes, cap))
        if width not in (1, 2):
            raise _SoundUnsupported(_t("only 8- and 16-bit WAV files"))
        vals = []
        step = width * nch
        for i in range(0, len(raw) - step + 1, step):
            acc = 0
            for c in range(nch):
                o = i + c * width
                if width == 1:
                    acc += raw[o] - 128          # 8-bit WAV is UNSIGNED
                else:
                    v = raw[o] | (raw[o + 1] << 8)
                    if v >= 0x8000:
                        v -= 0x10000
                    acc += v >> 8
            vals.append(max(-128, min(127, acc // nch)))
        if not vals:
            return [], 0
        # Nearest neighbour to the one rate the hardware plays.
        n_out = max(16, int(len(vals) * self.PCM_RATE / float(rate)))
        out = [vals[min(len(vals) - 1, int(i * rate / float(self.PCM_RATE)))]
               for i in range(n_out)]
        while len(out) % 4:
            out.append(0)
        return out, len(out) / float(self.PCM_RATE)

    def _file_open(self):
        try:
            os.makedirs(PROJ_DIR, exist_ok=True)
        except Exception:
            pass
        path = nbpicker.open_file(self, title="Open Project", start_dir=PROJ_DIR,
                                  patterns=("*.gbaproj", "*.json"))
        if not path:
            return
        try:
            # Three things can be picked and all three are one project: a
            # bundle directory, the project.json inside one, or a single-file
            # project saved before bundles existed.
            data = None
            root = path
            self._bundle_lost = 0
            if os.path.isdir(path):
                data = self._bundle_read(path)
            elif os.path.basename(path) == self.BUNDLE_MARK:
                root = os.path.dirname(path)
                data = self._bundle_read(root)
            if data is None:
                if not os.path.isfile(path):
                    raise ValueError("not a project")
                root = path
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            proj, lost = self._sane_project(data)
            if proj is None:
                raise ValueError("not a GBA SDK project")
            # A whole part file that would not read is a bigger loss than a
            # damaged record inside one, and it has to reach the same warning.
            lost += getattr(self, "_bundle_lost", 0)
        except Exception:
            self._flash(_t("That file was not made by GBA SDK."))
            return
        self.proj = proj
        self.undo.reset()
        if lost:
            self._flash(_t("Part of this project could not be read and was left "
                           "out. The file as it was is kept beside it."))
        self._path = root
        self._sel = None
        self._save_autosave()
        self._render_tree()
        self._editor_stack.show("welcome")

    # ---- the project bundle -------------------------------------------------
    # A project was one JSON blob. docs/GBA-SDK-SPEC.md plans for 10,000+
    # assets and a 16 MB ROM, and a single document at that size is slow to
    # write, impossible to diff and all-or-nothing to lose. A bundle is a
    # DIRECTORY: project.json for the settings, one file per kind of asset.
    #
    # Additive on purpose. The old single file is still read (see _file_open),
    # every existing .gbaproj keeps working, and nothing about the in-memory
    # project changes — this is a storage layout, not a format change.
    BUNDLE_PARTS = ("sprites", "tilesets", "sounds", "objects",
                    "rooms", "scripts", "tables")
    BUNDLE_MARK = "project.json"

    def _bundle_write(self, dirpath):
        """Write the project as a bundle at `dirpath`.

        Written to a sibling .part directory and renamed over the top, because
        six files are not six atomic writes: a failure part-way through a
        direct write leaves a project whose settings and assets disagree, which
        is worse than a failed save."""
        part = dirpath.rstrip(os.sep) + ".part"
        old = dirpath.rstrip(os.sep) + ".old"
        shutil.rmtree(part, ignore_errors=True)
        os.makedirs(part, exist_ok=True)
        try:
            head = {k: v for k, v in self.proj.items()
                    if k not in self.BUNDLE_PARTS}
            head["_bundle"] = 1
            nbapp.atomic_write_json(os.path.join(part, self.BUNDLE_MARK), head,
                                    indent=2)
            for key in self.BUNDLE_PARTS:
                nbapp.atomic_write_json(os.path.join(part, key + ".json"),
                                        self.proj.get(key) or [], indent=2)
        except Exception:
            # Nothing has touched the saved project yet, so the only repair
            # needed is not to leave half a bundle lying beside it looking
            # like one.
            shutil.rmtree(part, ignore_errors=True)
            raise

        shutil.rmtree(old, ignore_errors=True)
        moved = False
        try:
            if os.path.isdir(dirpath):
                os.rename(dirpath, old)
                moved = True
            os.rename(part, dirpath)
        except Exception:
            # THE WINDOW THIS WHOLE DANCE EXISTS FOR. Between moving the saved
            # project aside and moving the new one in, the project does not
            # exist at its own path -- and if the second rename fails, it stays
            # that way: the author's work is sitting in a .old directory they
            # have no reason to look in. Put it back.
            if moved and not os.path.isdir(dirpath):
                try:
                    os.rename(old, dirpath)
                except OSError:
                    pass                # the .old copy is still there to find
            shutil.rmtree(part, ignore_errors=True)
            raise
        shutil.rmtree(old, ignore_errors=True)

    def _bundle_read(self, dirpath):
        """Reassemble a bundle into one project dict, or None if it is not one.
        A missing part file yields an empty list for that kind rather than
        failing the whole load — losing one kind is recoverable, losing the
        project is not."""
        mark = os.path.join(dirpath, self.BUNDLE_MARK)
        if not os.path.isfile(mark):
            return None
        try:
            with open(mark, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            # A damaged marker used to raise straight out of here, past a
            # caller that had been told this returns None for "not a bundle".
            # An unreadable directory is not a bundle; saying so lets the open
            # fail with a sentence instead of a traceback.
            return None
        if not isinstance(data, dict):
            return None
        data.pop("_bundle", None)
        # A part that cannot be read is an empty kind AND a loss worth
        # reporting. It used to be only the first: an unreadable tables.json
        # took every table in the project with it, in silence, and the next
        # save wrote the emptiness back over the file that still had them.
        self._bundle_lost = 0
        for key in self.BUNDLE_PARTS:
            path = os.path.join(dirpath, key + ".json")
            existed = os.path.exists(path)
            try:
                with open(path, encoding="utf-8") as fh:
                    got = json.load(fh)
                if isinstance(got, list):
                    data[key] = got
                else:
                    data[key] = []
                    self._bundle_lost += 1
            except (OSError, ValueError):
                data[key] = []
                # A part that is absent is a kind the project never had. A part
                # that is THERE and unreadable is data that has gone missing,
                # and only the second is worth alarming anybody about.
                if existed:
                    self._bundle_lost += 1
        return data

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
        # The picker hands back a file path; the bundle is a directory AT that
        # path. A project saved before this still opens — only new saves take
        # the new shape.
        try:
            self._bundle_write(path)
            self._path = path
            self._flash("Saved %s" % os.path.basename(path))
        except Exception:
            self._flash("Save failed.")

    def _file_export_mb(self):
        """A link-cable image, for a console with no cartridge."""
        return self._file_export(multiboot=True)

    def _file_export(self, multiboot=False):
        """Build the project into a .gba file the user chooses — no emulator.

        The ROM plays in the bundled GBA Emulator and also boots on real
        hardware: gbabuild writes the cartridge boot logo and header checksum
        the console's BIOS checks (see gbabuild.NINTENDO_LOGO)."""
        if not self.proj.get("objects") or not self.proj.get("rooms"):
            self._card("cartridge", _t("Cannot build"),
                       [_t("A game needs at least one object and one room.")],
                       bullets=[_t("Draw a sprite"),
                                _t("Make an Object and give it that sprite."),
                                _t("Make a Room and click to place the object "
                                   "in it.")])
            return
        # A line of code the compiler cannot read used to become a silent
        # nothing: the game built, that action did nothing, and the author was
        # never told. Say so BEFORE asking where to save it.
        problems = gbabuild.check_project(self.proj)
        # A project that exceeds what the console HAS is the other way a build
        # succeeds and the game is wrong. budget_report knew about it, but only
        # the "What This Game Costs" menu item ever asked — so a game with more
        # sprite tiles than OBJ VRAM exported cleanly and glitched on hardware,
        # with the one place that could have said so never consulted. The
        # numbers go in the same gate as the code problems, because they have
        # the same consequence: it builds, and it does not do what was meant.
        try:
            for ln in gbabuild.budget_report(self.proj)["lines"]:
                if ln.get("over"):
                    if ln.get("unit") == "bytes":
                        used = _t("%d KB") % (ln["used"] // 1024)
                        cap = _t("%d KB") % (ln["cap"] // 1024)
                    else:
                        used, cap = "%d" % ln["used"], "%d" % ln["cap"]
                    problems.append(
                        _t("Too big: %(what)s uses %(used)s, and the "
                           "console has room for %(cap)s")
                        % {"what": _t(ln["name"]), "used": used, "cap": cap})
        except Exception:                                   # noqa: BLE001
            pass                # a costing that fails must not block a build
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
        ext = ".mb" if multiboot else ".gba"
        path = nbpicker.save_file(
            self, title=_t("Send Over a Link Cable") if multiboot
            else "Build & Export", start_dir=PROJ_DIR,
            suggested_name=(self.proj.get("name") or "game") + ext,
            patterns=("*" + ext,), default_ext=ext)
        if not path:
            return
        self._flash(_t("Compiling…"))
        outdir = os.path.join(tempfile.gettempdir(), "nbgba-export")
        # A COPY. The compiler has no business writing to the open document, and
        # the app autosaves whatever it finds there — which is how private build
        # keys ended up inside every saved .gbaproj.
        ok, gba, log = gbabuild.build_rom(copy.deepcopy(self.proj), outdir,
                                          multiboot=multiboot)
        self._last_log = log
        if not ok:
            self._flash(_t("Export failed"))
            self._card("cartridge", _t("The game could not be built"),
                       [self._failure_reason(log),
                        _t("Nothing was saved. The project is unchanged.")],
                       secondary=(_t("Show the Details"),
                                  lambda: self._show_log(log)))
            return
        try:
            shutil.copyfile(gba, path)
        except Exception as e:
            self._flash(_t("Export failed"))
            # The build itself worked; what failed was writing the file out.
            # Say that as a sentence, and never hand the reader the raw
            # exception — it names a path and an errno, neither of which is
            # something they can do anything with.
            self._card("cartridge", _t("The game could not be saved"),
                       [_t("The game was built, but it could not be written "
                           "to %s. The folder may be full, or on a stick that "
                           "cannot be written to. Try saving it somewhere "
                           "else.") % os.path.basename(path)])
            return
        name = os.path.basename(path)
        self._flash(_t("Exported %s") % name)
        # The whole point of the app just happened — say where the game is and
        # what to do with it, including on real hardware (gbabuild writes the
        # cartridge boot logo, so this ROM really does start on a console).
        # A build that SUCCEEDS can still have had something to say. The
        # generated C compiles without warnings, so a warning here comes from
        # an Execute Code action — the author's own C — and one of them is
        # `if (x = 3)`, which gcc catches as "suggest parentheses around
        # assignment used as truth value". That is the classic C mistake, in an
        # app whose Help pane teaches C, and it was going straight into the log
        # where nobody looks.
        warned = self._warning_count(log)
        said = [_t("%s — %.1f kB, saved in Documents.")
                % (name, os.path.getsize(path) / 1024.0)]
        if warned:
            said.append(_t("The compiler had one remark.")
                        if warned == 1
                        else _t("The compiler had %d remarks.")
                        % warned)
        self._card(
            "cartridge", _t("Build finished"), said,
            secondary=((_t("Show the Details"), lambda: self._show_log(log))
                       if warned else None),
            bullets=[
                _t("The GBA Emulator app runs the file."),
                _t("For a Game Boy Advance console, copy the file to a USB "
                   "stick, then to a flashcart."),
            ])

    @staticmethod
    def _warning_count(log):
        """How many things the compiler remarked on, from a build that worked.

        Counts `: warning:` rather than the word anywhere: a build log echoes
        its own command line, and `-Wall` in that line is not a warning. That
        exact confusion cost a wrong reading once already."""
        return sum(1 for ln in (log or "").split("\n") if ": warning:" in ln)

    # ================= css =================
    def _install_css(self):
        # ASCII ONLY inside this literal: a non-ASCII character in a b"""..."""
        # CSS block breaks the build (tools/ascii_css_check.py, twice bitten).
        #
        # The palette is Papertone's: paper #FCFBF8, panel #F4F2EC, ink #1A1916,
        # hairline #C9C4B6, muted #6E695E. The one signage red #C8341E is spent
        # ONLY on the primary Build & Export button and on alerts; every
        # selection, hover and chosen-state in here is ink or panel, which is why
        # the red frames that used to ring the selected swatch, frame, tile and
        # tile-set thumbnail are gone.
        css = b"""
        * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .sdkbar { background: #F1EEE6; border-bottom: 1px solid #C9C4B6;
                  padding: 8px 14px; }
        .runbtn { background: #C8341E; color: #FCFBF8; border-radius: 8px;
                  padding: 6px 14px; font-weight: 600; box-shadow: none;
                  border: none; }
        .runbtn:hover { background: #B12D19; }
        .sdkstatus { font-size: 12px; color: #6E695E; }
        /* secondary empty-state CTA - inviting but paper, so the red Compile &
           Export up in the toolbar stays the single accent on screen. */
        .exbtn { background: #FCFBF8; color: #1A1916; border: 1px solid #C9C4B6;
                 border-radius: 8px; padding: 8px 18px; font-weight: 600;
                 box-shadow: none; }
        .exbtn:hover { background: #F1EEE6; }

        /* --- shared furniture --- */
        .hairline { background: #C9C4B6; }
        .eyebrow { font-size: 10px; letter-spacing: 0.13em; color: #6E695E;
                   font-weight: 700; }
        .quietbtn { border: 1px solid #C9C4B6; background: #FCFBF8;
                    color: #1A1916; border-radius: 8px; padding: 4px 10px;
                    font-size: 12px; box-shadow: none; }
        .quietbtn:hover { background: #F1EEE6; }
        .iconbtn { border: 1px solid transparent; background: transparent;
                   border-radius: 8px; padding: 3px 4px; box-shadow: none;
                   min-width: 24px; min-height: 24px; }
        .iconbtn:hover { background: #EFEBE0; border-color: #D7D2C5; }
        .iconbtn:checked { background: #EAE3D2; border-color: #C9C4B6; }
        .addbtn { border: 1px solid transparent; background: transparent;
                  border-radius: 8px; padding: 2px 4px; box-shadow: none;
                  min-width: 24px; min-height: 24px; }
        .addbtn:hover { background: #EAE3D2; border-color: #C9C4B6; }
        /* Focus is drawn everywhere, on ink, and never removed: a keyboard user
           who cannot see where they are cannot use any of this. */
        button:focus, listbox row:focus, .swatch:focus, .framebtn:focus,
        .actcard:focus, .toolbtn:focus {
            outline: 2px solid #1A1916; outline-offset: -2px; }

        /* --- the asset browser --- */
        .browser { background: #F1EEE6; border-right: 1px solid #C9C4B6; }
        .browserhead { padding: 12px 14px 10px; }
        .browsertitle { font-size: 15px; font-weight: 600; color: #1A1916; }
        .assetlist { background: transparent; }
        .assetlist row { background: transparent; border: none;
                         padding: 0; }
        /* A selected row is painted with a background IMAGE by the stock theme,
           so a colour-only rule leaves it Adwaita blue. */
        .assetlist row:selected { background-image: none;
                                  background-color: #EAE3D2;
                                  box-shadow: inset 3px 0 0 #1A1916; }
        .assetlist row:selected .assetname { color: #1A1916; }
        .grouphead { padding: 12px 12px 3px; }
        .groupcount { font-size: 10px; font-weight: 700; color: #6E695E; }
        .assetrow { padding: 5px 12px 5px 12px; }
        .assetname { font-size: 13px; color: #1A1916; }
        .assetsub { font-size: 11px; color: #6E695E; }
        .folderrow { padding: 2px 8px 2px 16px; background: transparent;
                     border: none; box-shadow: none; }
        .folderrow:hover { background: #F1EEE6; }
        .foldername { font-family: "Nimbus Sans","Helvetica",sans-serif;
                      font-size: 12px; color: #3A362E; }
        .foldercount { font-family: "Nimbus Sans","Helvetica",sans-serif;
                       font-size: 11px; color: #9A9484; }
        .emptyrow { font-size: 11px; color: #6E695E; padding: 3px 12px 5px 12px; }
        .startbadge { font-size: 10px; font-weight: 700; color: #1A1916;
                      background: #EAE3D2; border: 1px solid #C9C4B6;
                      border-radius: 4px; padding: 1px 5px; }

        /* --- the shape every editor shares --- */
        .editpane { padding: 18px 22px 16px; background: #FCFBF8; }
        .panetitle { font-size: 20px; font-weight: 700; color: #1A1916; }
        .panesub { font-size: 12px; color: #6E695E; }
        .panehint { font-size: 12px; color: #6E695E; }
        .toolrow { }
        .toolcap { font-size: 11px; letter-spacing: 0.08em; color: #6E695E;
                   font-weight: 700; }
        .welcometitle { font-size: 20px; font-weight: 700; color: #1A1916; }
        /* 13px #9A9484 on paper is a 2.8:1 contrast ratio - under the 4.5:1 that
           body text needs, and this class carries the app's explanations. */
        .welcomesub { font-size: 13px; color: #6E695E; }

        /* --- segmented controls (tools, sound channel, room mode, zoom) --- */
        .toolseg { border: 1px solid #C9C4B6; border-radius: 8px;
                   background: #FCFBF8; }
        .toolbtn { background: transparent; border: none; box-shadow: none;
                   border-radius: 8px; padding: 3px 6px; min-width: 28px;
                   min-height: 26px; color: #1A1916; font-size: 12px; }
        .toolbtn.wide { padding: 3px 10px; }
        .toolbtn:hover { background: #EFEBE0; }
        /* The chosen one is reversed out - a difference in VALUE, not in hue. */
        /* The colour set on a GtkButton does NOT reach its internal label
           node, so the reversed-out state needs the label named explicitly --
           without it the chosen tool was a black rectangle with invisible
           text. */
        .toolbtn.on, .toolbtn.on label { color: #FCFBF8; }
        .toolbtn.on { background: #1A1916; }
        .toolbtn.on:hover { background: #3A362E; }

        /* --- palette --- */
        .swatch { padding: 2px; border: 1px solid transparent; box-shadow: none;
                  background: transparent; border-radius: 4px;
                  min-width: 28px; min-height: 28px; }
        .swatch:hover { border-color: #1A1916; }
        .colourname { font-size: 12px; color: #1A1916; }
        .colourcount { font-size: 11px; color: #6E695E; }
        /* Red is the alert colour and this is an alert: the art will not come
           out of the compiler looking like the art on screen. */
        .colourcount.over { color: #C8341E; font-weight: 700; }
        .framebtn { padding: 2px; border: 1px solid transparent;
                    background: transparent; box-shadow: none;
                    border-radius: 4px; }
        .framebtn:hover { border-color: #C9C4B6; background: #F1EEE6; }
        .framebtn.on { background: #EFEBE0; }

        /* --- object editor --- */
        .evrow { padding: 6px 8px; }
        .assetlist row:selected .evrow { color: #1A1916; }
        .actcard { background: #F4F2EC; border: 1px solid #D7D2C5;
                   border-radius: 12px; padding: 8px 10px; }
        .actcard:focus { background: #EFEBE0; }
        .actchildren { border-left: 2px solid #D7D2C5; padding-left: 10px; }
        .actname { font-size: 13px; font-weight: 600; color: #1A1916; }
        .actnum { font-size: 10px; font-weight: 700; color: #6E695E;
                  min-width: 14px; }
        .paramlbl { font-size: 11px; color: #6E695E; }
        .paramentry { font-size: 12px; padding: 2px 6px; border: 1px solid #C9C4B6;
                      border-radius: 8px; background: #FCFBF8; box-shadow: none; }
        .palbtn { border: 1px solid #D7D2C5; background: #FCFBF8; color: #1A1916;
                  border-radius: 8px; padding: 4px 8px; font-size: 12px;
                  box-shadow: none; }
        .palbtn:hover { background: #EFEBE0; border-color: #1A1916; }

        /* build result card (success / problems / failure) */
        .resultcard { background: #FCFBF8; border: 1px solid #C9C4B6;
                      padding: 22px 26px 18px; }
        .resulttitle { font-size: 20px; font-weight: 700; color: #1A1916; }
        .resultbody { font-size: 13px; color: #6E695E; }
        .resultnum { font-size: 11px; font-weight: 700; color: #6E695E;
                     background: #F1EEE6; border: 1px solid #D7D2C5;
                     border-radius: 100px; min-width: 18px; min-height: 18px; }
        /* Show C dialog. .dlghead is Papertone's; these two are the lines
           under it. */
        .dlgsub { font-size: 12px; color: #6E695E; }
        /* A solid tile is marked in the strip: a flag with no visible effect
           is state the author has to remember instead of read. */
        .solidtile { border: 2px solid #1A1916; border-radius: 4px; }
        .palnum { font-size: 11px; font-weight: 700; color: #6E695E; }
        .palname { font-size: 12px; color: #1A1916; }
        .palfree { font-size: 11px; color: #6E695E; }
        .palwarn { font-size: 11px; color: #8A3A1E; }
        .dlgwarn { font-size: 12px; color: #8A3A1E; }
        """
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css + gbaworkspace.CSS + gbahelp.CSS)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            pass


if __name__ == "__main__":
    nbapp.run(GbaSdk)
