#!/usr/bin/env python3
"""user_content_verbatim — ONE check for the whole "the catalog ate the user's
own words" defect class.

nbi18n translates the widget tree: every Gtk.Label, Button, MenuItem, window
title, tooltip, Frame/Expander label and ComboBoxText row is looked up in the
interface catalog when the window is shown, and replaced on an exact match.
That is right for chrome and WRONG for content, because a person names things
with ordinary words. A list called "Home" on a French install drew "Accueil";
a manuscript called "Notes" on a Spanish install was saved as "Notas". When the
app also READS that widget back to build what it saves, the file itself ends up
holding somebody else's word.

nbi18n.set_verbatim(widget, text) is the remedy. This suite proves it is in
place, app by app, by DRIVING each real app TWICE under the same non-English
language: once naming things with a word the catalog knows ("Home", "Work",
"Body", "Save"), and once with a made-up word the catalog has never heard of.
The made-up run says how many places the app is SUPPOSED to show that name.
The catalog run must show its name in exactly as many. Anything the translate
layer rewrote is a place the two counts disagree.

Counting, not "is it on screen anywhere", is the whole point: the first version
of this suite asked only whether the typed name appeared SOMEWHERE, and a
sabotaged copy of Tasks — its sidebar list name deliberately put back on a
translatable set_text — passed it, because the header still showed the name
correctly one row above the broken one. A per-app check that goes green on a
half-broken app is worse than no check.

On top of the counts it asserts the store on disk holds the same bytes the
person typed, so the round trip (type -> file -> screen) is exact.

Every covered app is named in RECIPES below, and every one of them is DRIVEN:
an app that cannot be built, or whose recipe stops putting the name on screen,
is a FAILURE here, never a skip. A skipped line reads as DID NOT RUN to
run_all_gates, and a class-wide check that quietly covers nothing is worse than
no check.

OUT_OF_REACH names the remaining apps that carry user text and why this suite
cannot drive them — each one needs something no offscreen drive can supply (a
mounted volume, a CUPS queue, a real disk, a file under /etc). They are listed
in the output as an explicit boundary, not passed off as covered.

Run:  tools/guestrun.sh python3 tools/user_content_verbatim_selftest.py
      tools/guestrun.sh python3 tools/user_content_verbatim_selftest.py fr ru
"""
import os
import sys
import json
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.environ.get("NB_DRIVE_DE") or os.path.join(
    ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

DEFAULT_LANGS = ("fr", "ru")

# The made-up names. Nothing in any catalog may match one, and the suite
# refuses to run if one ever does — a sentinel that got translated would make
# the counts agree for the wrong reason and turn this whole check vacuous.
SENTINELS = ["Zorbeth", "Quillan", "Vandrel", "Mokrith"]

# Apps that carry user text, are fixed, and cannot be driven from here. Stated
# so the boundary of this check is on the page rather than implied by absence.
OUT_OF_REACH = [
    ("packages", "needs the volume label of a stick mounted under /media"),
    ("settings", "needs the hostname in /proc, the account in /etc/passwd, "
                 "and a CUPS queue"),
    ("nbprint", "needs a CUPS queue named by the user"),
    ("maps", "needs an .nbm2 map pack with a place table"),
    ("installer", "needs real disks, and it runs before there is a session"),
    ("login", "needs the full name in /etc/notebookos-user"),
    ("language", "course data is authored, not typed; its fix is a read-back "
                 "(the answer is graded from the model, not from the tiles) "
                 "and language_*_selftest plays every exercise"),
    ("screenplay", "its fix is a read-back too — the save chip compared its "
                   "own translated markup against an English constant — and "
                   "there is no user name on a translatable widget to count"),
]


# ---------------------------------------------------------------------------
#  The surface the translate layer can reach.
#
#  Deliberately NOT everything on screen: a Gtk.Entry, a TextView buffer and a
#  ListStore cell are never translated, so counting them would let an app pass
#  because the name survived somewhere nbi18n was never going to touch it.
#  Only the widgets nbi18n._install_auto_translate() actually patches or walks
#  are collected here.
# ---------------------------------------------------------------------------
def surface(root, window=None):
    from gi.repository import Gtk
    out = []
    stack = [root]
    # `keep` holds every wrapper the walk has finished with. Without it the
    # walk visited a fraction of the tree: PyGObject hands back a NEW Python
    # wrapper per get_children() call, a wrapper dropped after its turn is
    # freed at once, and CPython hands the next widget the SAME id — so an
    # id()-keyed "already seen" set started rejecting widgets it had never
    # looked at. The whole app read as nine labels of a mini calendar.
    keep = []
    seen = set()
    while stack:
        w = stack.pop()
        if id(w) in seen:
            continue
        seen.add(id(w))
        keep.append(w)
        try:
            if isinstance(w, Gtk.Label):
                out.append(("Label", w.get_text() or ""))
            if isinstance(w, Gtk.Button):
                out.append(("Button", w.get_label() or ""))
            if isinstance(w, Gtk.MenuItem):
                out.append(("MenuItem", w.get_label() or ""))
            if isinstance(w, (Gtk.Frame, Gtk.Expander)):
                out.append(("Frame", w.get_label() or ""))
            if isinstance(w, Gtk.ComboBoxText):
                m = w.get_model()
                if m is not None:
                    for row in m:
                        out.append(("Combo", row[0] or ""))
            if isinstance(w, Gtk.TreeView):
                for col in w.get_columns():
                    out.append(("Column", col.get_title() or ""))
            tip = w.get_tooltip_text()
            if tip:
                out.append(("Tooltip", tip))
            # set_tooltip_markup is the verbatim route for hover text (nbi18n
            # patches set_tooltip_text and not this), so a suite that only
            # read the plain form could not see the remedy at all.
            tipm = w.get_tooltip_markup()
            if tipm:
                out.append(("TooltipMarkup", tipm))
        except Exception:                                         # noqa: BLE001
            pass
        try:
            if isinstance(w, Gtk.MenuItem) and w.get_submenu() is not None:
                stack.append(w.get_submenu())
            if isinstance(w, Gtk.Notebook):
                for page in w.get_children():
                    lbl = w.get_tab_label(page)
                    if lbl is not None:
                        stack.append(lbl)
            if isinstance(w, Gtk.Container):
                stack.extend(w.get_children())
        except Exception:                                         # noqa: BLE001
            pass
    if window is not None:
        try:
            out.append(("WindowTitle", window.get_title() or ""))
        except Exception:                                         # noqa: BLE001
            pass
    return [(k, t) for k, t in out if t]


def cfg(home, name):
    return os.path.join(home, ".config", "notebook", name)


def read_store(*paths):
    """The bytes on disk, concatenated — what the user's file actually says."""
    out = []
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                out.append(fh.read())
        except OSError:
            pass
    return "\n".join(out)


# ---------------------------------------------------------------------------
#  Recipes. One per app that puts a user-typed name on a translatable widget.
#
#  seed(d, N) creates the content the way a person does — through the app's own
#  dialogs, entries and commit paths, never by poking a label. N is the list of
#  names to type; the suite calls the same recipe once with the catalog words
#  declared in `names` and once with made-up ones, so a recipe must NEVER
#  hard-code the word it types. store(home) returns the bytes on disk.
# ---------------------------------------------------------------------------
RECIPES = {}


def recipe(app, names, store, module=None, cls="", pre=None, model=None,
           reopen=False, after=None):
    """pre(home, N) runs BEFORE the window is built — for content a person made
    in an earlier session (the app reads its store at launch) or that arrives
    from another app's file. seed(d, N) is the in-session typing.

    store(home) reads the bytes on disk. A couple of apps keep nothing on disk
    (a burn queue lives for one session), so model(drive) may stand in for it,
    reading the app's own record. One of the two must produce the name or the
    check fails: a name that is neither saved nor held is not a round trip.

    reopen=True closes the window once the content is made and builds the app
    again on the same Home, so what gets looked at is what a person sees the
    NEXT time they open it — the far end of the round trip, and the end the
    Novel defect actually broke. Apps that keep nothing between sessions (a
    burn queue, a folder listing, an open photo) are looked at in place.
    after(d, N) is the navigation a person would do on that second open — the
    app remembers a view, and a name that only shows on another one has to be
    walked to rather than assumed."""
    def deco(fn):
        RECIPES[app] = {"app": app, "module": module or app, "cls": cls,
                        "names": list(names), "seed": fn, "store": store,
                        "pre": pre, "model": model, "reopen": reopen,
                        "after": after}
        return fn
    return deco


def write_cfg(home, name, data):
    d = os.path.join(home, ".config", "notebook")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# -- Tasks: a list name and a task title ------------------------------------
@recipe("tasks", ["Home", "Work"],
        lambda h: read_store(cfg(h, "tasks-app.json"), cfg(h, "tasks.json")), reopen=True)
def _tasks(d, N):
    d.app._on_new_list(None)
    d.pump(0.2)
    d.app._nl_entry.grab_focus()
    d.type(N[0])                          # New List -> the list's name
    d.app._nl_create()
    d.pump(0.3)
    d.app.draft.grab_focus()
    d.type(N[1])                          # the quick-add field -> a task title
    d.key("Return")
    d.pump(0.3)



# -- Academics: a class name, a lecture title, an assignment ----------------
def _academics_after(d, N):
    # Academics opens on the view it was left in; the class rail and the
    # assignment list are two views away from the notes canvas.
    d.app._set_view("homework")
    d.pump(0.2)
    d.app._set_view("schedule")


@recipe("academics", ["Home", "Work", "Reading"],
        lambda h: read_store(cfg(h, "academics.json")), reopen=True,
        after=_academics_after)
def _academics(d, N):
    a = d.app
    a.classes.append({"label": N[0], "name": N[0], "color": "#9A7B4F",
                      "room": "", "instructor": "", "meets": []})
    a.lectures.append({"cls": 0, "num": "01", "title": N[1], "date": "",
                       "meta": "", "notes": "", "ranges": []})
    a.homework.append({"title": N[2], "cls": 0, "due": "", "done": False,
                       "note": ""})
    a._save_to_disk()
    a._refresh_sidebar()
    a._refresh_homework()
    d.pump(0.2)


# -- Bills: the payee -------------------------------------------------------
@recipe("bills", ["Home"], lambda h: read_store(cfg(h, "bills.json")), reopen=True)
def _bills(d, N):
    a = d.app
    a.bills.append({"id": "b1", "payee": N[0], "account": "", "amount": 8420,
                    "due": "2026-09-15", "dom": 15, "every": 1,
                    "method": "mail", "address": "", "phone": "",
                    "note": "", "lead": 0, "paid": []})
    a.sel = "b1"
    a._save()
    a._refresh()
    d.pump(0.2)


# -- Accounting: a ledger description --------------------------------------
@recipe("accounting", ["Home"], lambda h: read_store(cfg(h, "accounting.json")), reopen=True)
def _accounting(d, N):
    a = d.app
    a._reveal_form()                      # the form a person opens to add
    d.pump(0.2)
    a.f_desc.grab_focus()
    d.type(N[0])                          # the description field
    a.f_amt.set_text("12.50")
    a._on_add()
    d.pump(0.3)


# -- Workout: the exercise's name -------------------------------------------
@recipe("workout", ["Home"], lambda h: read_store(cfg(h, "workout.json")), reopen=True)
def _workout(d, N):
    a = d.app
    a.data["exercises"].append({"id": "e1", "name": N[0], "sets": 3,
                                "reps": 10})
    a.sel = 0
    a._save()
    a._refresh()
    d.pump(0.2)


# -- Cookbook: a category and a recipe title --------------------------------
@recipe("cookbook", ["Home", "Work"],
        lambda h: read_store(cfg(h, "cookbook.json")), reopen=True)
def _cookbook(d, N):
    a = d.app
    a.cats.append(N[0])
    a.rebuild_chips()
    a.new_recipe()
    a.recipes[a.sel]["title"] = N[1]
    a.recipes[a.sel]["cat"] = N[0]
    a.rebuild_list()
    a._refresh_editor()
    a._save_state()
    d.pump(0.2)


# -- Meal planner: the dish in a slot ---------------------------------------
@recipe("mealplanner", ["Home"],
        lambda h: read_store(cfg(h, "mealplanner.json")), reopen=True)
def _mealplanner(d, N):
    import mealplanner
    d.app._set_slot(mealplanner._today_key(), "dinner",
                    mealplanner.KIND_NOTE, N[0])
    d.pump(0.2)


# -- Calendar: an event title, and a calendar the user made -----------------
@recipe("calendar", ["Home", "Work"],
        lambda h: read_store(cfg(h, "calendar.json"),
                             cfg(h, "calendars.json")), reopen=True)
def _calendar(d, N):
    from datetime import date
    a = d.app
    a.calendars.append({"name": N[1], "color": "#4A5E73"})
    a._save_calendars()
    a._populate_cal_list()
    day = date.today()
    a._new_event(day, {"start": 9.0, "end": 10.0, "title": N[0],
                       "cal": N[1], "location": "", "notes": "",
                       "all_day": False})
    a._save_events()
    a.sel = day
    a.cur_y, a.cur_m = day.year, day.month
    a.view = "month"
    a._refresh()
    d.pump(0.2)


# -- Journal: the entry title, derived from the first line typed ------------
@recipe("journal", ["Home"], lambda h: read_store(cfg(h, "journal.json")), reopen=True)
def _journal(d, N):
    a = d.app
    a.new_entry()
    a.body.get_buffer().set_text(N[0] + "\nA second line, for the preview.")
    a._save_current()
    a._refresh_list()
    a._persist()
    d.pump(0.2)


# -- Contacts: the person's name --------------------------------------------
@recipe("contacts", ["Home"], lambda h: read_store(cfg(h, "contacts.json")), reopen=True)
def _contacts(d, N):
    import contacts as C
    a = d.app
    a.people.append(C.normalize_person({"name": N[0], "role": ""},
                                       len(a.people)))
    a.active = len(a.people) - 1
    a.editing = False
    a._save()
    a._rebuild_list()
    a._rebuild_detail()
    d.pump(0.2)


# -- Music: a playlist the listener named -----------------------------------
@recipe("music", ["Home"], lambda h: read_store(cfg(h, "music.json")), reopen=True)
def _music(d, N):
    a = d.app
    a._create_playlist(N[0])
    a._save()
    d.pump(0.2)


# -- Novel: the manuscript title and a chapter heading ----------------------
@recipe("novel", ["Home", "Work"],
        lambda h: read_store(cfg(h, "novel.json")), reopen=True)
def _novel(d, N):
    a = d.app
    a._set_title(N[0])
    a.chapters[a.active]["title"] = N[1]
    a._refresh_chapter_list()
    a._save_state()
    d.pump(0.2)


# -- Ebook: a book on the shelf, titled by its filename ---------------------
def _ebook_pre(home, N):
    import os as _os
    docs = _os.path.join(home, "Documents")
    _os.makedirs(docs, exist_ok=True)
    open(_os.path.join(docs, N[0] + ".pdf"), "wb").write(b"%PDF-1.4\n%%EOF\n")
    write_cfg(home, "ebook.json",
              {"books": [{"path": _os.path.join(docs, N[0] + ".pdf"),
                          "title": N[0], "fmt": "PDF", "pos": 0,
                          "frac": 0.0, "total": 0, "author": ""}],
               "open": _os.path.join(docs, N[0] + ".pdf")})


@recipe("ebook", ["Home"], lambda h: read_store(cfg(h, "ebook.json")),
        pre=_ebook_pre)
def _ebook(d, N):
    d.pump(0.3)


# -- Writer: the running header the writer typed ----------------------------
@recipe("writer", ["Home"], lambda h: read_store(cfg(h, "writer.json")), reopen=True)
def _writer(d, N):
    a = d.app
    a._header = N[0]
    a._refresh_hf_labels()
    a._save_autosave()
    d.pump(0.2)


# -- Sequencer: a track name on the tape head -------------------------------
@recipe("sequencer", ["Home"], lambda h: read_store(cfg(h, "sequencer.json")), reopen=True)
def _sequencer(d, N):
    a = d.app
    a.tracks[0]["name"] = N[0]
    a._sync_controls()
    a._save()
    d.pump(0.2)


# -- Burner: the queue names the files it will write ------------------------
#    Nothing is persisted — a burn queue is one session long — so the queue
#    itself is the record the screen has to agree with.
@recipe("burner", ["Home"], None,
        model=lambda d: json.dumps(d.app.items))
def _burner(d, N):
    a = d.app
    a.items.append({"path": "/tmp/%s.wav" % N[0], "name": N[0],
                    "seconds": 120})
    a._refresh()
    d.pump(0.2)


# -- Video: the words on a title card ---------------------------------------
@recipe("video", ["Home"], lambda h: read_store(cfg(h, "video.json")), reopen=True)
def _video(d, N):
    import video as V
    a = d.app
    a._insert_clip(V._new_title(N[0], "", 3), 0, "Add Title Card")
    a._save_project()
    d.pump(0.2)


# -- Widgets: the desktop board shows eight other apps' records -------------
def _widgets_pre(home, N):
    write_cfg(home, "tasks.json", [{"text": N[0], "done": False}])


@recipe("widgets", ["Home"], lambda h: read_store(cfg(h, "tasks.json")),
        pre=_widgets_pre)
def _widgets(d, N):
    d.pump(0.4)


# -- GBA SDK: the project's own name ----------------------------------------
@recipe("gbasdk", ["Home"], lambda h: read_store(cfg(h, "gbasdk.json")), reopen=True)
def _gbasdk(d, N):
    a = d.app
    a.proj["name"] = N[0]
    a._render_tree()
    a._save_autosave()
    d.pump(0.2)



# -- Finder: a folder the user made -----------------------------------------
def _finder_pre(home, N):
    import os as _os
    _os.makedirs(_os.path.join(home, N[0]), exist_ok=True)


@recipe("finder", ["Home"], None, cls="Finder", pre=_finder_pre,
        model=lambda d: "\n".join(sorted(os.listdir(d.home))))
def _finder(d, N):
    d.app.load(N[0])                      # open the folder the user made
    d.pump(0.3)


# -- Comics: a layer named in the .comic file -------------------------------
@recipe("comics", ["Home"], None,
        model=lambda d: "\n".join(
            ly.name for ly in d.app.doc.pages[d.app.doc.active]["layers"]))
def _comics(d, N):
    a = d.app
    a.doc.pages[a.doc.active]["layers"][0].name = N[0]
    a._refresh()
    d.pump(0.2)


# -- Composer: a track renamed in the track picker --------------------------
@recipe("composer", ["Home"], lambda h: read_store(cfg(h, "composer.json")), reopen=True)
def _composer(d, N):
    a = d.app
    a.editor.song["tracks"][a.editor.track]["name"] = N[0]
    a._refresh_tracks()
    a._save_session()
    d.pump(0.2)


# -- GBA emulator: a ROM named by its file ----------------------------------
def _gbaemu_pre(home, N):
    import os as _os
    docs = _os.path.join(home, "Documents")
    _os.makedirs(docs, exist_ok=True)
    with open(_os.path.join(docs, N[0] + ".gba"), "wb") as fh:
        fh.write(b"\0" * 4096)


@recipe("gbaemu", ["Home"], None, pre=_gbaemu_pre,
        model=lambda d: "\n".join(m["name"] for m in d.app._roms))
def _gbaemu(d, N):
    a = d.app
    a._apply_scan(a._scan_roms(apply=False) or [])
    a._render_library()
    d.pump(0.3)


# -- Animation: a layer the animator named ----------------------------------
@recipe("animation", ["Home"], None,
        model=lambda d: json.dumps([l["name"] for l
                                    in d.app.doc.scenes[0]["layers"]]))
def _animation(d, N):
    a = d.app
    a.doc.scenes[0]["layers"][0]["name"] = N[0]
    a._refresh_layers()
    d.pump(0.2)



# -- Media: the photo's own filename in the Info panel -----------------------
def _media_pre(home, N):
    import os as _os
    import zlib as _zlib
    import struct as _struct
    pics = _os.path.join(home, "Pictures")
    _os.makedirs(pics, exist_ok=True)

    def chunk(tag, data):
        return (_struct.pack(">I", len(data)) + tag + data
                + _struct.pack(">I", _zlib.crc32(tag + data) & 0xFFFFFFFF))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", _struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", _zlib.compress(b"\x00\xff\x00\x00\xff\x00\x00"
                                           b"\x00\xff\x00\x00\xff\x00\x00"))
           + chunk(b"IEND", b""))
    # No extension on purpose: an extension is the only thing that has been
    # keeping a photo called "Home" out of the catalog, and it is not a rule.
    with open(_os.path.join(pics, N[0]), "wb") as fh:
        fh.write(png)


@recipe("media", ["Home"], None, pre=_media_pre,
        model=lambda d: "\n".join(sorted(os.listdir(
            os.path.join(d.home, "Pictures")))))
def _media(d, N):
    d.app._display(os.path.join(d.home, "Pictures", N[0]))
    d.pump(0.4)


# -- USB Writer: the disk image the user picked -----------------------------
def _usbwriter_pre(home, N):
    import os as _os
    docs = _os.path.join(home, "Documents")
    _os.makedirs(docs, exist_ok=True)
    with open(_os.path.join(docs, N[0]), "wb") as fh:
        fh.write(b"\0" * 2048)


@recipe("usbwriter", ["Home"], None, pre=_usbwriter_pre,
        model=lambda d: str((d.app.image or {}).get("path", "")))
def _usbwriter(d, N):
    import nbpicker
    want = os.path.join(d.home, "Documents", N[0])
    nbpicker.open_file = lambda *a, **k: want    # the picker's answer
    d.app._on_pick()                             # the app's own handler
    d.pump(0.3)


# -- Shell: the notification centre shows other apps' words ------------------
def _shell_pre(home, N):
    import nbnotify
    os.environ["NB_HOME"] = home
    nbnotify.post(N[0], N[0], app="tasks", app_name="Tasks")


@recipe("shell", ["Home"], None, cls="Panel", pre=_shell_pre,
        model=lambda d: json.dumps([r.get("title") for r in
                                    __import__("nbnotify").load()]))
def _shell(d, N):
    # The tray itself is a positioned popup and needs a mapped panel button to
    # anchor to, which an offscreen holder cannot give it. The CARD is the part
    # that carries other apps' words, so the real builder is called and its
    # card is put in the panel's own tree — the same show_all walk then runs
    # over it, which is the step that used to translate the rows.
    import nbnotify
    card = d.app._notify_card(nbnotify.load())
    d.child.add(card)                    # the panel's root is a Gtk.Fixed
    card.show_all()
    d.pump(0.3)


# ---------------------------------------------------------------------------
def drive_one(rec, names):
    """Build the app, run the recipe, return (surface, store)."""
    sys.path.insert(0, HERE)
    import appdrive
    home = tempfile.mkdtemp(prefix="nbverbatim-%s-" % rec["app"])
    try:
        os.environ["NB_HOME"] = home
        os.makedirs(os.path.join(home, ".config", "notebook"), exist_ok=True)
        if rec.get("pre"):
            rec["pre"](home, names)
        d = appdrive.Drive(rec["module"], cls=rec["cls"], home=home)
        rec["seed"](d, names)
        d.pump(0.2)
        record = rec["store"](home) if rec["store"] else ""
        if rec.get("model"):
            record = "\n".join([record, str(rec["model"](d))])
        if rec.get("reopen"):
            # Close it and open it again on the same Home: the round trip is
            # only exact if the name survives the trip THROUGH the store.
            d.close()
            d = appdrive.Drive(rec["module"], cls=rec["cls"], home=home)
            d.pump(0.3)
            if rec.get("after"):
                rec["after"](d, names)
                d.pump(0.2)
        surf = surface(d.child, d.app)
        d.close()
        return surf, record
    finally:
        shutil.rmtree(home, ignore_errors=True)


def child(lang, apps):
    """Run every recipe under one language, twice per app. One flushed JSON
    line per run, so a crash names the app and variant it crashed in instead
    of losing the whole language."""
    import nbi18n
    if nbi18n.lang() != lang:
        print(json.dumps({"fatal": "NB_LANG=%s but nbi18n.lang()=%s"
                          % (lang, nbi18n.lang())}), flush=True)
        return 2
    for word in SENTINELS:
        if nbi18n._lookup(word) is not None:
            print(json.dumps({"fatal": "sentinel %r is a catalog word in %s "
                              "— pick another" % (word, lang)}), flush=True)
            return 2
    for app in apps:
        rec = RECIPES[app]
        for variant in ("made-up", "catalog"):
            names = (SENTINELS[:len(rec["names"])] if variant == "made-up"
                     else rec["names"])
            key = "%s/%s" % (app, variant)
            print(json.dumps({"run": key, "phase": "start"}), flush=True)
            try:
                surf, store = drive_one(rec, names)
                print(json.dumps({"run": key, "app": app, "variant": variant,
                                  "names": names, "surface": surf,
                                  "store": store}), flush=True)
            except Exception as exc:                              # noqa: BLE001
                import traceback
                print(json.dumps({"run": key, "app": app, "variant": variant,
                                  "names": names,
                                  "error": "%s: %s" % (type(exc).__name__, exc),
                                  "trace": traceback.format_exc()[-1400:]}),
                      flush=True)
    return 0


def run_lang(lang, apps):
    env = dict(os.environ, NB_LANG=lang, NB_DRIVE_DE=DE,
               PYTHONPATH=os.pathsep.join(
                   [DE, HERE, os.environ.get("PYTHONPATH", "")]))
    try:
        r = subprocess.run([sys.executable, "-u", os.path.abspath(__file__),
                            "--child", lang] + list(apps),
                           env=env, capture_output=True, text=True,
                           timeout=2400)
    except subprocess.TimeoutExpired:
        return None, "the %s run did not finish in 40 minutes" % lang, ""
    got, started = {}, []
    for line in r.stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if "fatal" in rec:
            return None, rec["fatal"], r.stderr[-1500:]
        if rec.get("phase") == "start":
            started.append(rec["run"])
        else:
            got[rec["run"]] = rec
    for key in started:
        if key not in got:
            got[key] = {"run": key, "app": key.split("/")[0],
                        "variant": key.split("/")[1],
                        "error": "the drive died here (no result line); "
                                 "stderr tail: %s" % r.stderr[-600:]}
    return got, None, r.stderr[-1500:]


def counts(surf, name):
    """How many translatable widgets carry this name, exactly and embedded."""
    exact = sum(1 for _k, t in surf if t.strip() == name)
    inside = sum(1 for _k, t in surf if name in t)
    return exact, inside


def main(argv):
    langs = [a for a in argv if not a.startswith("-")] or list(DEFAULT_LANGS)
    apps = sorted(RECIPES)
    print("user_content_verbatim — %d apps x %d languages, each driven twice "
          "(made-up names, then catalog words)" % (len(apps), len(langs)))
    print("apps covered: %s" % ", ".join(apps))
    for name, why in OUT_OF_REACH:
        print("NOT DRIVEN  %-11s %s" % (name, why))
    print()

    runs = {}
    for lang in langs:
        got, err, _stderr = run_lang(lang, apps)
        if got is None:
            print("FAIL %s: %s" % (lang, err))
            print("\nRESULT: SOME FAILED (the %s run could not start)" % lang)
            return 1
        runs[lang] = got

    checks = 0
    fails = []
    for app in apps:
        rec = RECIPES[app]
        for lang in langs:
            checks += 1
            base = runs[lang].get("%s/made-up" % app)
            real = runs[lang].get("%s/catalog" % app)
            bad = []
            for got, what in ((base, "made-up"), (real, "catalog")):
                if got is None:
                    bad.append("the %s drive never reported — this app is "
                               "covered but was not exercised" % what)
                elif "error" in got:
                    bad.append("CANNOT DRIVE (%s names): %s"
                               % (what, got["error"]))
            if not bad:
                for i, word in enumerate(rec["names"]):
                    sent = base["names"][i]
                    # Widgets whose whole text IS the name. A name inside a
                    # longer sentence is a different (already-defended) shape:
                    # _t("Delete %s") is translated before the name goes in,
                    # so a composed line's count moves with the wording rather
                    # than with this defect, and counting it here only makes
                    # the comparison noisy.
                    be, bi = counts(base["surface"], sent)
                    re_, ri = counts(real["surface"], word)
                    if be == 0:
                        bad.append(
                            "%r reaches NO translatable widget even when it "
                            "cannot be translated — the recipe no longer puts "
                            "this name on screen, so nothing here is being "
                            "checked" % sent)
                    elif re_ != be:
                        shown = sorted({t for _k, t in real["surface"]
                                        if t and len(t) < 40})
                        bad.append(
                            "%r is on %d widget(s) but the same drive puts %r "
                            "on %d — the catalog rewrote %d of them (screen "
                            "says: %s)"
                            % (word, re_, sent, be, be - re_, shown[:18]))
                    if word not in (real.get("store") or ""):
                        bad.append("%r is not in the store on disk — the app "
                                   "never saved what was typed" % word)
            if bad:
                fails.append("%s/%s: %s" % (app, lang, bad[0]))
                print("FAIL %-12s %-3s" % (app, lang))
                for b in bad:
                    print("       %s" % b)
                for got in (base, real):
                    if got and got.get("trace"):
                        print(got["trace"])
            else:
                print("ok   %-12s %-3s  %s verbatim on every widget that "
                      "shows it, and in the store"
                      % (app, lang, ", ".join(repr(n) for n in rec["names"])))

    print()
    if fails:
        print("RESULT: SOME FAILED — %d of %d app/language checks"
              % (len(fails), checks))
        return 1
    print("RESULT: %d checks, 0 failed" % checks)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        sys.exit(child(sys.argv[2], sys.argv[3:]))
    sys.exit(main(sys.argv[1:]))
