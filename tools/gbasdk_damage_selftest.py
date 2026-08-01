#!/usr/bin/env python3
"""
Open the GBA SDK on a DAMAGED project store, click through every editor, close
it, and prove the game somebody made survived.

  DISPLAY=:0 PYTHONPATH=<overlay>/opt/notebook/de \
  python3 tools/gbasdk_damage_selftest.py [case]

THE BUG CLASS THIS EXISTS FOR (it has cost this OS user data in eight other
apps — see tools/store_damage_selftest.py): a loader that is all-or-nothing. One
malformed record — a resource list stored as an object, a frame that is not a
list, a reference to something that no longer exists — makes the loader give up
and return empty defaults; the app opens blank; and the close-time autosave then
writes that blankness over the user's only copy. gbasdk autosaves after every
single edit AND again on destroy, so it has the shortest fuse of any app here.

Three things are demanded of every case:

  1. the window OPENS. A project file that stops the window being built is
     unrecoverable on a machine whose owner has no shell and no way to reach
     ~/.config/notebook, so the app is simply dead from then on.
  2. nothing is LOST. Whatever units of work were on disk when the app opened
     must still be on disk (or in a recoverable .bak / .damaged- copy) when it
     closes. A malformed record costs itself and nothing more.
  3. no editor, and neither half of the compiler front end, raises on it.

ONE CASE PER PROCESS. nbapp keeps a module-level _BACKED_UP set, so a store is
backed up once per file per PROCESS. Running the cases in one process makes
cases 2..n look like they got no backup — a lie the first version of the
academics test told. The driver below re-invokes itself per case.
"""
import os
import sys
import json
import shutil
import tempfile


# case -> (damage the good project in place, what the case is really asking).
# A spec of None means "this file is not JSON at all": nothing may be kept, but
# the bytes must be moved aside rather than overwritten.
CASES = {
    # ---- valid JSON, wrong shape ----
    "sprites-as-object":
        (lambda p: p.update(sprites={"spr_player": p["sprites"][0]}),
         "a resource list stored as an object"),
    "objects-as-object":
        (lambda p: p.update(objects={"obj_player": p["objects"][0]}),
         "the object list stored as an object"),
    "rooms-as-object":
        (lambda p: p.update(rooms={"rm_world": p["rooms"][0]}),
         "the room list stored as an object"),
    "no-objects-key":
        (lambda p: p.pop("objects"),
         "the one key the loader sniffs for is missing"),
    "sprite-not-dict":
        (lambda p: p["sprites"].insert(0, "spr_player"),
         "one resource is a string"),
    "frames-not-list":
        (lambda p: p["sprites"][0].update(frames=5),
         "a sprite's frames are a number"),
    "frame-not-list":
        (lambda p: p["sprites"][0].update(frames=[7]),
         "one animation frame is a number"),
    "frame-short":
        (lambda p: p["sprites"][0].update(
            frames=[p["sprites"][0]["frames"][0][:10]]),
         "a frame holds fewer pixels than the sprite's size"),
    "event-not-dict":
        (lambda p: p["objects"][0]["events"].insert(0, "step"),
         "an event is a string"),
    "actions-not-list":
        (lambda p: p["objects"][0]["events"][0].update(actions="lots"),
         "an event's actions are a string"),
    "action-not-dict":
        (lambda p: p["objects"][0]["events"][0]["actions"].insert(0, "move"),
         "an action is a string"),
    "children-not-list":
        (lambda p: p["objects"][0]["events"][-1]["actions"][1].update(
            children="nope"),
         "a container action's children are a string"),
    "instance-no-x":
        (lambda p: p["rooms"][0]["instances"].insert(0, {"object": "obj_coin"}),
         "a placed object has no position"),
    "instance-not-dict":
        (lambda p: p["rooms"][0]["instances"].insert(0, "obj_coin"),
         "a placed object is a string"),
    "tiles-wrong-length":
        (lambda p: p["rooms"][0].update(tiles=[1, 2, 3]),
         "the room's tile layer is the wrong length"),
    "tiles-out-of-range":
        (lambda p: p["rooms"][0].update(
            tiles=[9999] * len(p["rooms"][0]["tiles"])),
         "every tile cell points past the end of the tile set"),
    "room-dims-strings":
        (lambda p: p["rooms"][0].update(w="wide", h=None, speed="fast"),
         "the room's size is not a number"),
    "start-room-not-string":
        (lambda p: p.update(start_room={"id": "rm_world"}),
         "the start-room flag is an object"),
    # ---- valid shape, dead references ----
    "dangling-sprite":
        (lambda p: p.update(sprites=[]),
         "the sprite every object wears was deleted"),
    "dangling-object":
        (lambda p: p.update(objects=[]),
         "the objects the rooms place were deleted"),
    "dangling-start-room":
        (lambda p: p.update(start_room="rm_gone"),
         "the start room no longer exists"),
    "dangling-sound":
        (lambda p: p.update(sounds=[]),
         "the sound a Play Sound action names was deleted"),
    "dangling-tileset":
        (lambda p: p.update(tilesets=[]),
         "the tile set a room is painted with was deleted"),
    # ---- not JSON at all ----
    "not-json": (None, "the file cannot be parsed"),
    "truncated": (None, "the file was cut off mid-write"),
    "empty-file": (None, "the file is zero bytes"),
}


def good_project():
    """A complete little game: the SDK's own example, plus a sound and a couple
    more events so every resource type has something to lose."""
    import gbasdk
    p = gbasdk.GbaSdk._example_project(None)
    p["sounds"] = [{"id": "snd_coin", "tempo": 8, "loop": False, "steps": 16,
                    "lead": [60, 0, 64, 0] * 4, "bass": [0] * 16}]
    p["objects"][0]["events"].append(
        {"type": "keypress", "key": "a", "actions": [
            {"kind": "play_sound", "sound": "snd_coin"},
            {"kind": "if_chance", "percent": "50",
             "children": [{"kind": "add_score", "value": "1"}]}]})
    return p


def units(proj):
    """How much of the user's game is present — the number this test protects.
    Everything they drew, placed, chose or wired counts as one unit."""
    if not isinstance(proj, dict):
        return 0
    n = 0
    for key in ("sprites", "tilesets", "sounds", "objects", "rooms"):
        lst = proj.get(key)
        if not isinstance(lst, list):
            continue
        n += len(lst)
        for r in lst:
            if not isinstance(r, dict):
                continue
            for sub in ("frames", "tiles", "instances"):
                if isinstance(r.get(sub), list):
                    n += len(r[sub])
            if isinstance(r.get("events"), list):
                for ev in r["events"]:
                    n += 1
                    if isinstance(ev, dict) and isinstance(ev.get("actions"), list):
                        n += len(ev["actions"])
    return n


def run_case(case):
    HOME = tempfile.mkdtemp(prefix="gbasdk-dmg-")
    cfgdir = os.path.join(HOME, ".config", "notebook")
    os.makedirs(cfgdir)
    os.environ["NB_HOME"] = HOME
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    import nbapp
    # Stand clear of the single-instance lock, which would os._exit this
    # process the moment a developer had the real gbasdk open.
    nbapp._APP_DIR = os.path.join(HOME, "nb-apps")
    os.makedirs(nbapp._APP_DIR)
    import gbasdk
    import gbabuild

    def pump(n=400):
        i = 0
        while Gtk.events_pending() and i < n:
            Gtk.main_iteration_do(False)
            i += 1

    store = os.path.join(cfgdir, "gbasdk.json")
    spec, why = CASES[case]
    good = good_project()
    if spec is None:
        raw = {"not-json": '{ "sprites": [ oh dear',
               "truncated": json.dumps(good)[:len(json.dumps(good)) // 2],
               "empty-file": ""}[case]
        with open(store, "w") as fh:
            fh.write(raw)
        baseline = 0
    else:
        p = json.loads(json.dumps(good))
        spec(p)
        with open(store, "w") as fh:
            json.dump(p, fh)
        baseline = units(p)          # what was on disk when the app opened

    notes = []
    opened = True
    w = None
    try:
        w = gbasdk.GbaSdk()
        pump()
    except Exception as e:
        opened = False
        notes.append("WINDOW WOULD NOT OPEN: %s: %s" % (type(e).__name__, e))
    if w is not None:
        for kind in ("sprite", "tileset", "sound", "object", "room"):
            try:
                lst = w._res(kind)
                if isinstance(lst, list) and lst:
                    w._select_resource(kind, 0)
                    pump()
            except Exception as e:
                notes.append("%s editor raised %s" % (kind, type(e).__name__))
        try:                    # the event/action sheet is the deepest reader
            if isinstance(w._res("object"), list) and w._res("object"):
                w._select_resource("object", 0)
                for i in range(4):
                    w._select_event(i)
                    pump()
        except Exception as e:
            notes.append("event sheet raised %s" % type(e).__name__)
        try:
            gbabuild.generate_c(w.proj)
        except Exception as e:
            notes.append("generate_c raised %s" % type(e).__name__)
        try:
            gbabuild.check_project(w.proj)
        except Exception as e:
            notes.append("check_project raised %s" % type(e).__name__)
        try:
            w.destroy()
            pump()
        except Exception as e:
            notes.append("closing raised %s" % type(e).__name__)

    extras = sorted(f for f in os.listdir(cfgdir) if f != "gbasdk.json")
    aside = [f for f in extras if ".damaged-" in f]
    try:
        after = json.load(open(store))
    except Exception:
        after = None
    got = units(after)
    recover = 0
    for f in extras:
        try:
            with open(os.path.join(cfgdir, f)) as fh:
                recover = max(recover, units(json.load(fh)))
        except Exception:
            pass

    note = ""
    if case == "empty-file":
        # A zero-byte store holds nothing to preserve; what matters is that the
        # app opens on it and does not fall over.
        ok = opened and not notes
        reason = "an empty store must simply open as a new project"
    elif spec is None:
        ok = bool(aside)
        reason = "an unreadable store must be moved aside, not overwritten"
    else:
        kept = max(got, recover)
        ok = opened and not notes and kept >= baseline
        reason = "%s — %d of %d units survived" % (why, kept, baseline)
        if ok and got < baseline:
            # It survived, but ONLY because nbapp keeps one previous-good copy.
            # The app itself threw the project away and told the user nothing,
            # and on this appliance nobody can reach a .bak by hand.
            note = "  (ONLY the .bak saved it: the app kept %d of %d itself)" % (
                got, baseline)
    print("%-4s %-24s opened=%-5s kept %4d/%-4d recoverable=%4d aside=%s%s%s"
          % ("PASS" if ok else "FAIL", case, opened, got, baseline, recover,
             extras or "NONE", note,
             "" if ok else "\n     <- " + reason
             + "".join("\n        " + n for n in notes)))
    shutil.rmtree(HOME, ignore_errors=True)
    return ok


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        if sys.argv[1] not in CASES:
            print("unknown case %r; known: %s"
                  % (sys.argv[1], ", ".join(sorted(CASES))))
            raise SystemExit(2)
        raise SystemExit(0 if run_case(sys.argv[1]) else 1)
    import subprocess
    bad = 0
    for case in CASES:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), case],
                           capture_output=True, text=True, timeout=300)
        out = (r.stdout or "").rstrip()
        if not out:
            out = "FAIL %-24s produced no output at all  %s" % (
                case, "; ".join((r.stderr or "").strip().splitlines()[-2:]))
        print(out)
        if not out.startswith("PASS"):
            bad += 1
    print("\nRESULT: %s" % ("ALL PASS" if not bad else "%d of %d FAILED"
                            % (bad, len(CASES))))
    raise SystemExit(0 if not bad else 1)
