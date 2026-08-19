#!/usr/bin/env python3
"""
Language — a full offline language course in the Duolingo lineage.

Pick a course, work down a winding path of skills, and each lesson drills a
handful of generated exercises. A skill takes five crowns to finish, and the
lesson gets harder at every crown: the first pass teaches new words and asks
you to pick their meaning, the last asks you to type them from English with no
options on the screen. Hearts cost you for a wrong answer and come back with
time or with practice. A daily XP goal drives the streak. Every word you have
met carries a strength that decays if you leave it alone, and Practice pulls
back whatever has gone weakest.

There is no audio anywhere on this system, so the pronunciation exercises are
built on IPA instead of on recordings: every target word carries its
transcription, and the "which word is this?" round shows you nothing but the
transcription.

Courses are read from de/course_<code>.json -- compact vocab, phrase and
grammar-tip lists that the exercises are GENERATED from, so a course stays
small on disk yet plays like a full tree. See docs/LANGUAGE-COURSE-FORMAT.md
for the contract, and tools/language_content_check.py for what enforces it.

Progress -- crowns, XP, the streak, hearts, per-word strength, unit tests and
awards -- persists to $NB_HOME/.config/notebook/language.json.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, Pango, GLib  # noqa: E402

import os
import json
import copy
import math
import time
import random
import unicodedata
from datetime import date, timedelta

import nbapp
import nbicons
import nbtransitions
import nbi18n
from nbi18n import _t, ltr  # noqa: E402

DE_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
CFG_FILE = os.path.join(CFG_DIR, "language.json")

INK = "#1A1916"
MUTED = "#6E695E"
FAINT = "#9A9484"
GREEN = "#4F7A3A"
RED = "#C8341E"
GOLD = "#B8912E"
PAPER = "#FCFBF8"
RULE = "#C9C4B6"

CROWN_MAX = 5
STRENGTH_MAX = 4
HEARTS_MAX = 5
# One heart back every twenty minutes. Duolingo's own refill is half an hour a
# heart; twenty minutes is the same idea sized for a machine somebody sits at
# for one session rather than all day.
HEART_REFILL_S = 20 * 60
# A word loses a strength point for every three days it goes unpractised. This
# is the whole spaced-repetition model: Practice sorts by CURRENT strength, so
# decay is what makes an old skill float back to the top of the queue.
DECAY_S = 3 * 86400
GOALS = (10, 20, 30, 50)
DEFAULT_GOAL = 20

# Width of one exercise's reading column. Everything in a lesson -- the
# instruction, the prompt, the answer buttons, the word bank and the footer --
# lines up inside it, centred on the page, so a wide panel reads the same as a
# 1024 one instead of stretching a four-letter answer across the screen.
EX_COLUMN = 560

# The path. NODE is the skill circle; ROW_H is the vertical pitch between two
# nodes; AMP is how far the serpentine swings off centre. PATH_W has to hold
# a node at full swing PLUS its name label, and the whole thing has to fit the
# 1024x740 small-screen budget with the tree's own margins -- so 470, not the
# 600 that looked better on this desktop's 1920 panel and clipped on a laptop.
NODE = 74
# ROW_H has to clear the node (74) PLUS its two-line name and its level line,
# or a long skill name runs into the crown ring of the node below it.
ROW_H = 128
AMP = 96
PATH_W = 470

# Every unit takes one colour, cycled. Papertone is a paper-and-ink theme, so
# these are pigments rather than the screen primaries a bright learning app
# usually reaches for: the tree still reads as ten distinguishable stretches.
UNIT_COLORS = [
    ("#4F7A3A", "#DCE9CE"),   # green
    ("#3E6B8C", "#DCE6EE"),   # blue
    ("#B5623C", "#F4E1D6"),   # terracotta
    ("#7A4A6B", "#EDE0EA"),   # plum
    ("#2F6B5F", "#D8E8E3"),   # teal
    ("#96702A", "#F1E6CC"),   # ochre
]

# A mark for each of the forty skills in the standard curriculum. Keyed on the
# skill's English name, which every course shares (see the format doc), so a
# course file carries no icon names unless it wants to override one.
SKILL_ICONS = {
    # Phrases is a PHRASEBOOK, not a quotation mark: "quote" is authored small
    # in the 24x24 grid and came out as two specks in a 74px node.
    "Greetings": "speech", "People": "contacts", "Phrases": "journal",
    "Numbers": "number",
    "Food": "cookbook", "Drinks": "cup", "Colors": "palette",
    "Family": "family",
    "Verbs": "bolt", "Adjectives": "brush", "Questions": "question",
    "Negation": "nosign",
    "House": "home", "Objects": "box", "Clothing": "shirt", "Animals": "paw",
    "Days": "calendar", "Months": "leaf", "Time of Day": "clock",
    "Weather": "cloud",
    "Places": "mappin", "Directions": "compass", "Transport": "bus",
    "Travel": "plane",
    "Feelings": "heart", "Body": "body", "Health": "cross",
    "Describing": "eye",
    "School": "academic", "Work": "briefcase", "Big Numbers": "calculator",
    "Money": "coins",
    "Restaurant": "mealplanner", "Shopping": "cart", "Hobbies": "music",
    "Sports": "ball",
    "Nature": "tree", "City": "city", "Countries": "globe",
    "More Verbs": "repeat",
}

# Awards. Each is (key, mark, name, what it counts, the five tiers). Offline,
# so nothing here compares the learner to anybody else -- every one of them is
# a personal record against their own past.
AWARDS = [
    ("wildfire", "flame", "Wildfire", "Day streak", (3, 7, 14, 30, 100)),
    ("scholar", "ebook", "Scholar", "Words learned", (25, 50, 100, 250, 480)),
    ("sharpshooter", "target", "Sharpshooter", "Lessons with no mistakes",
     (5, 15, 40, 100, 250)),
    ("champion", "trophy", "Champion", "Units finished", (1, 3, 5, 10, 20)),
    ("sage", "star", "Sage", "Total XP", (100, 500, 1500, 5000, 15000)),
    ("regal", "crown", "Regal", "Crowns earned", (5, 20, 50, 120, 200)),
    ("explorer", "globe", "Explorer", "Courses started", (1, 2, 3, 4, 5)),
]


def _set_course_text(widget, text):
    """Put a word from the COURSE on a widget, exactly as the course wrote it.

    Course data is authored, not typed by the learner — but it is no more the
    interface's to rewrite than a name would be. Two things went wrong when it
    was: the English column of a vocabulary list came out in the interface
    language ("Monday" shown as "Lundi", so the flashcard taught nothing), and
    an answer assembled from word tiles was READ BACK off those tiles to grade
    it, so a correctly built sentence containing a catalog word would be marked
    wrong and cost a heart and the skill's crown."""
    value = str(text or "")
    nbi18n.set_verbatim(widget, value)
    try:
        child = widget.get_child()
    except Exception:
        child = None
    if isinstance(child, Gtk.Label):
        nbi18n.set_verbatim(child, value)


def _quarantine(path):
    """Move a store this app could not make sense of aside, under the same
    <name>.damaged-<timestamp> name nbapp.preserve_damaged uses. Never raises;
    returns whether the original is safely out of the replacement path."""
    try:
        if not os.path.lexists(path):
            return True
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = "%s.damaged-%s" % (path, stamp)
        n = 2
        while os.path.exists(dest):
            dest = "%s.damaged-%s-%d" % (path, stamp, n)
            n += 1
        os.replace(path, dest)
    except OSError:
        return False
    return True


def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _norm(s):
    """Normalise an answer for comparison: lowercase, no accents, collapse
    spaces, drop surrounding punctuation."""
    s = _strip_accents((s or "").strip().lower())
    out = []
    for ch in s:
        if ch.isalnum() or ch.isspace():
            out.append(ch)
    return " ".join("".join(out).split())


def _answer_norm(s, code=""):
    """Answer comparison with the target language's keyboard conventions."""
    raw = (s or "")
    if code == "zh":
        # In Pinyin, ü is a distinct vowel, conventionally typed as `v` on a
        # keyboard (and in this OS's own Pinyin IME). Accent stripping alone
        # turned lǜ into `lu`, accepting a different syllable and rejecting
        # the standard `lv`. NFC first also covers decomposed u+diaeresis.
        raw = unicodedata.normalize("NFC", raw).translate(str.maketrans({
            "ü": "v", "ǖ": "v", "ǘ": "v", "ǚ": "v", "ǜ": "v",
            "Ü": "v", "Ǖ": "v", "Ǘ": "v", "Ǚ": "v", "Ǜ": "v",
        }))
    return _norm(raw)


def _toks(s):
    """A sentence split into the tiles a word bank is built from.

    Edge punctuation is stripped, and not for tidiness: the tile carrying the
    sentence's comma or its opening inverted question mark was the ONLY tile
    with punctuation on it, so every word-bank and fill-in-the-blank question
    handed its first word away for free. The grader normalises punctuation out
    of both sides anyway, so nothing about scoring changes."""
    out = []
    for tok in (s or "").split():
        clean = tok.strip("!?.,;:¡¿—–\"'()")
        out.append(clean or tok)
    return out


def _today():
    return time.strftime("%Y-%m-%d")


def _yesterday():
    # Calendar arithmetic, not "24 hours ago": the local day before the
    # spring DST change is only 23 hours long.
    return (date.fromtimestamp(time.time()) - timedelta(days=1)).isoformat()


def load_courses():
    """Every de/course_*.json, validated into course dicts, sorted by name."""
    courses = []
    try:
        names = [f for f in os.listdir(DE_DIR)
                 if f.startswith("course_") and f.endswith(".json")]
    except OSError:
        names = []
    for fn in sorted(names):
        try:
            with open(os.path.join(DE_DIR, fn), encoding="utf-8") as fh:
                c = json.load(fh)
            if isinstance(c, dict) and c.get("code") and c.get("units"):
                # Course files are authored data, not an all-or-nothing
                # executable blob.  Keep every usable sibling when one row is
                # malformed: losing a word is honest; losing forty skills and
                # someone's route into their sunk progress is not.
                clean_units = []
                for unit in c.get("units", []):
                    if not isinstance(unit, dict):
                        continue
                    skills = []
                    for skill in unit.get("skills", []):
                        if not isinstance(skill, dict) or not isinstance(
                                skill.get("name"), str):
                            continue
                        skill = dict(skill)
                        skill["words"] = [row for row in skill.get("words", [])
                                          if _valid_course_row(
                                              row, ("t", "e", "ipa", "pos"))]
                        skill["phrases"] = [row for row in skill.get("phrases", [])
                                            if _valid_course_row(
                                                row, ("t", "e", "ipa"))]
                        skill["tips"] = [row for row in skill.get("tips", [])
                                         if _valid_course_row(row, ("h", "b"))]
                        skills.append(skill)
                    unit = dict(unit)
                    unit["skills"] = skills
                    clean_units.append(unit)
                c = dict(c)
                c["units"] = clean_units
                courses.append(c)
        except Exception:
            continue
    return courses


def _valid_course_row(row, required):
    return (isinstance(row, dict)
            and all(isinstance(row.get(key), str) and row.get(key).strip()
                    for key in required))


class Language(nbapp.AppWindow):
    app_name = "Language"
    menus = ("File",)

    def __init__(self):
        super().__init__()
        self._install_css()
        self.courses = load_courses()
        self.course = None
        self.progress = {}
        self._lesson = None       # active lesson state
        self._closed = False      # destroy ran: lesson callbacks must stop
        self._lesson_gen = 0      # bumped when a lesson's pending timers go stale
        self._lesson_sources = set()   # ids of pending owned one-shot timers
        self._graded = False      # this exercise has been answered
        self._check_btn = None    # the current exercise's Check / Continue
        self._check_id = 0
        self._quarantine_pending = False
        self._toast_id = 0
        self._course_scroll = 0.0
        self._syn_t = {}
        self._syn_e = {}
        self._load_progress()

        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        # The shared page-switch primitive owns the transition from here on. It
        # sets the type and duration on EVERY switch, so the direction follows
        # the order below: going deeper (home -> course -> lesson) slides
        # forward and coming back out slides back, instead of every switch
        # looking identical and the reader losing their place.
        #
        # This used to hard-code NONE -- right behaviour, wrong reason. The
        # problem was never the crossfade; it was running a frame-clock Stack
        # transition on the no-compositor swrast fallback, where the incoming
        # page never finishes fading in. nbmotion's policy resolves to instant
        # exactly there, and under Reduced Motion, so those machines still get
        # the instant switch they got before and only accelerated sessions
        # animate. One code path either way; see de/nbtransitions.py.
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._pager = nbtransitions.PageSwitcher(
            self.stack, order=["home", "course", "lesson", "page"],
            duration=nbtransitions.PAGE)
        self.content.pack_start(self.stack, True, True, 0)

        self.stack.add_named(self._home_page(), "home")
        self._course_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(self._course_holder, "course")
        self._lesson_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(self._lesson_holder, "lesson")
        self._page_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(self._page_holder, "page")
        # The first page is shown, not navigated to: there is nothing to have
        # come from, so it is stated as NONE rather than left to the default
        # crossfade. Routed through the pager anyway so it records where the
        # app starts -- otherwise the first real navigation has no origin to
        # measure a direction against and home -> course would fade, not slide.
        self._pager.switch("home", direction=nbtransitions.NONE)

        self.connect("destroy", self._on_destroy)
        self._day_rollover_id = GLib.timeout_add_seconds(
            30, self._check_day_rollover)

    # ==================================================================
    # owned lesson timers
    # ==================================================================
    # A lesson runs on delays: grade a finished matching round 250ms after the
    # last pair joins, wipe a wrong tile's colour after 400ms, move to the next
    # question 750ms after a correct answer, show the out-of-hearts page 900ms
    # after the last heart goes. Every one of those used to be a bare
    # GLib.timeout_add that nobody owned. Nothing cancelled them, so a delay
    # that outlived the thing it belonged to still fired: quit during the
    # out-of-hearts pause and the lesson's own ending replaced the course page
    # the quit had just returned you to, and closing the window inside the
    # 750ms advance ran a repaint over widgets that were being destroyed.
    #
    # A source scheduled here is owned twice over: its id is remembered so it
    # can be removed outright, and it carries the lesson generation it was
    # scheduled in, so a callback GLib has already queued -- one that a
    # source_remove is too late to stop -- still finds itself stale and does
    # nothing.

    def _lesson_later(self, ms, fn):
        """Run fn once, ms from now, only if this window is still open and no
        lesson boundary has passed in between. Returns the source id."""
        gen = self._lesson_gen
        holder = {}

        def fire():
            self._lesson_sources.discard(holder.get("id"))
            if self._closed or gen != self._lesson_gen:
                return False
            fn()
            return False

        holder["id"] = GLib.timeout_add(ms, fire)
        self._lesson_sources.add(holder["id"])
        return holder["id"]

    def _cancel_lesson_callbacks(self):
        """Retire every pending lesson timer. Bumping the generation FIRST is
        what makes this safe: source_remove cannot recall a callback GLib has
        already dispatched, and that callback checks the generation."""
        self._lesson_gen = getattr(self, "_lesson_gen", 0) + 1
        ids = getattr(self, "_lesson_sources", None) or set()
        self._lesson_sources = set()
        for sid in ids:
            try:
                GLib.source_remove(sid)
            except Exception:
                pass

    def _on_destroy(self, *_):
        # The one place that owns teardown. Destroy can arrive more than once
        # (Gtk emits it, and a caller may destroy an already-closed window), so
        # this is idempotent, and _closed is set FIRST: a callback that fires
        # between here and the last source_remove must drop out rather than
        # touch widgets that are being torn down. Progress is saved exactly
        # once -- a second save would re-run the quarantine bookkeeping.
        if getattr(self, "_closed", False):
            return
        self._closed = True
        rollover_id = getattr(self, "_day_rollover_id", 0)
        self._day_rollover_id = 0
        if rollover_id:
            try:
                GLib.source_remove(rollover_id)
            except Exception:
                pass
        self._cancel_lesson_callbacks()
        self._save_progress()

    # ==================================================================
    # progress store
    # ==================================================================
    def _pkey(self, ui, si):
        return "%s:%d:%d" % (self.course["code"], ui, si)

    def _crowns(self, ui, si):
        return self.progress.get("crowns", {}).get(self._pkey(ui, si), 0)

    def _add_crown(self, ui, si):
        cr = self.progress.setdefault("crowns", {})
        k = self._pkey(ui, si)
        before = cr.get(k, 0)
        cr[k] = min(CROWN_MAX, before + 1)
        return cr[k] > before

    @staticmethod
    def norm_progress(d):
        """Coerce a loaded progress file into the shape every reader here
        assumes.

        NOTHING re-validated these. The file is small, so it is exactly the kind
        a hand-edit or a half-finished write leaves in a foreign shape, and each
        wrong type broke something different and silently:

          * `xp` or `streak` as a string / list / object made the streak line's
            "%d XP" raise inside __init__ -- the app would not OPEN AT ALL, with
            no way back short of deleting the file from a terminal this OS does
            not really offer;
          * `crowns` as anything but an object made _crowns() raise the moment a
            course was opened;
          * `seen` as anything but a list made finishing a lesson raise on
            .append, so the lesson's progress was never recorded.

        A wrong type costs ITSELF (that one counter resets) and nothing else,
        and every other key in the file rides through untouched."""
        out = dict(d) if isinstance(d, dict) else {}
        known = {"xp", "streak", "day_xp", "hearts", "streak_day", "day",
                 "heart_time", "goal", "hearts_on", "crowns", "seen",
                 "strength", "tests", "stats", "awards", "_extra"}
        extra = out.get("_extra")
        extra = dict(extra) if isinstance(extra, dict) else {}
        for key in list(out):
            if key not in known:
                extra[key] = out.pop(key)
        out["_extra"] = extra
        for key in ("xp", "streak", "day_xp"):
            v = out.get(key)
            if (isinstance(v, bool) or not isinstance(v, (int, float))
                    or not math.isfinite(v)):
                try:
                    v = int(str(v).strip())      # "250" is still 250 XP
                    if not math.isfinite(v):
                        v = 0
                except (TypeError, ValueError, OverflowError):
                    v = 0
            out[key] = max(0, int(v))
        # Hearts are the one counter whose ABSENCE must not mean zero: a file
        # written before hearts existed, or one a hand-edit dropped the key
        # from, would otherwise open the app with a learner locked out of every
        # lesson and no way to see why.
        h = out.get("hearts")
        if (isinstance(h, bool) or not isinstance(h, (int, float))
                or not math.isfinite(h)):
            try:
                h = int(str(h).strip())
                if not math.isfinite(h):
                    h = HEARTS_MAX
            except (TypeError, ValueError, OverflowError):
                h = HEARTS_MAX
        out["hearts"] = max(0, min(HEARTS_MAX, int(h)))
        for key in ("streak_day", "day"):
            v = out.get(key)
            out[key] = v if isinstance(v, str) else ""
        ht = out.get("heart_time")
        if (isinstance(ht, bool) or not isinstance(ht, (int, float))
                or not math.isfinite(ht)):
            ht = 0
        out["heart_time"] = float(max(0, ht))
        goal = out.get("goal")
        out["goal"] = goal if goal in GOALS else DEFAULT_GOAL
        out["hearts_on"] = out.get("hearts_on") is not False

        crowns = out.get("crowns")
        if not isinstance(crowns, dict):
            crowns = {}
        clean = {}
        pending = [crowns]
        while pending:
            level = pending.pop()
            for k, v in level.items():
                if isinstance(v, dict):
                    # A wrapper key around the crown map, not a skill: descend
                    # rather than call a course's worth of crowns unreadable.
                    if len(pending) < 3:
                        pending.append(v)
                    continue
                if not isinstance(k, str):
                    continue
                try:
                    clean[k] = max(0, min(CROWN_MAX, int(v)))
                except (TypeError, ValueError, OverflowError):
                    continue
        out["crowns"] = clean

        seen = out.get("seen")
        if isinstance(seen, dict):
            seen = list(seen.keys())     # a set written as an object
        if not isinstance(seen, list):
            seen = []
        out["seen"] = [s for s in seen if isinstance(s, str)]

        # Per-word strength: {"es:hola": {"s": 3, "t": 1751000000.0}}. A row in
        # any other shape is dropped rather than allowed to raise inside the
        # Practice queue's sort, which runs on every course page.
        st = out.get("strength")
        clean_st = {}
        if isinstance(st, dict):
            for k, v in st.items():
                if not isinstance(k, str):
                    continue
                if isinstance(v, dict):
                    s, t = v.get("s"), v.get("t")
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    s, t = v, 0          # an older, plainer strength file
                else:
                    continue
                try:
                    s = max(0, min(STRENGTH_MAX, int(s)))
                    t = float(t) if isinstance(t, (int, float)) else 0.0
                except (TypeError, ValueError):
                    continue
                clean_st[k] = {"s": s, "t": max(0.0, t)}
        out["strength"] = clean_st

        tests = out.get("tests")
        out["tests"] = ({k: True for k, v in tests.items()
                         if isinstance(k, str) and v}
                        if isinstance(tests, dict) else {})

        stats = out.get("stats")
        clean_stats = {}
        for k in ("lessons", "perfect", "best_streak"):
            v = (stats or {}).get(k) if isinstance(stats, dict) else None
            if (isinstance(v, bool) or not isinstance(v, (int, float))
                    or not math.isfinite(v)):
                v = 0
            try:
                clean_stats[k] = max(0, int(v))
            except (TypeError, ValueError, OverflowError):
                clean_stats[k] = 0
        out["stats"] = clean_stats

        awards = out.get("awards")
        clean_awards = {}
        if isinstance(awards, dict):
            for k, v in awards.items():
                if not isinstance(k, str) or isinstance(v, bool):
                    continue
                try:
                    clean_awards[k] = max(0, min(5, int(v)))
                except (TypeError, ValueError):
                    continue
        out["awards"] = clean_awards
        return out

    def _load_progress(self):
        try:
            with open(CFG_FILE, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            d = None
        self.progress = self.norm_progress(d)
        # A store that PARSED but is not a progress file at all (a bare list,
        # some other app's) would otherwise be replaced by the destroy-time save
        # with a fresh, empty one. nbapp's generic quarantine cannot see it --
        # valid JSON of the wrong shape parses perfectly -- so move it aside on
        # the way past instead, the same way accounting.py and cookbook.py do.
        if ((d is not None and not isinstance(d, dict))
                or (d is None and os.path.lexists(CFG_FILE))):
            self._quarantine_pending = True
        self._roll_day()

    def _save_progress(self):
        try:
            if getattr(self, "_quarantine_pending", False):
                # Do not overwrite the only copy when permissions, a full
                # filesystem or a read-only mount prevents the protective
                # move. Keep pending raised so the next save retries after the
                # storage problem is fixed.
                if not _quarantine(CFG_FILE):
                    raise OSError("could not preserve unrecognized progress")
                self._quarantine_pending = False
            nbapp.atomic_write_json(CFG_FILE, self.progress)
            return True
        except Exception as exc:
            nbapp.note_save_failure(self, exc, CFG_FILE)
            return False

    def _roll_day(self):
        """Start today's XP ledger. The daily goal is what the streak is scored
        against, so the day counter has to turn over on its own -- otherwise a
        learner who leaves the app open past midnight is still filling
        yesterday's goal."""
        t = _today()
        if self.progress.get("day") != t:
            self.progress["day"] = t
            self.progress["day_xp"] = 0

    def _check_day_rollover(self):
        """Refresh daily-goal chrome when a session crosses local midnight."""
        if getattr(self, "_closed", False):
            return False
        before = self.progress.get("day")
        self._roll_day()
        if self.progress.get("day") == before:
            return True
        self._save_progress()
        try:
            where = self.stack.get_visible_child_name()
            if where == "home":
                self._refresh_home_stats()
            elif where == "course":
                self._render_course(keep_scroll=True)
            # Never rebuild an exercise in progress merely because midnight
            # passed; scoring already calls _roll_day before awarding XP.
        except Exception:
            pass
        return True

    # ---------------- XP, goal, streak ----------------
    def _goal(self):
        g = self.progress.get("goal")
        return g if g in GOALS else DEFAULT_GOAL

    def _day_xp(self):
        self._roll_day()
        return self.progress.get("day_xp", 0)

    def _award_xp(self, xp):
        """Bank XP and, the first time today's goal is met, extend the streak.

        The streak is scored on the GOAL, not on merely opening a lesson. That
        is the whole point of having a goal: 'I did something' is not a
        commitment, 'I did twenty XP' is."""
        self._roll_day()
        self.progress["xp"] = self.progress.get("xp", 0) + xp
        before = self.progress.get("day_xp", 0)
        after = before + xp
        self.progress["day_xp"] = after
        hit = before < self._goal() <= after
        if hit:
            today, last = _today(), self.progress.get("streak_day")
            if last != today:
                self.progress["streak"] = (
                    self.progress.get("streak", 0) + 1
                    if last == _yesterday() else 1)
                self.progress["streak_day"] = today
                st = self.progress.setdefault("stats", {})
                st["best_streak"] = max(st.get("best_streak", 0),
                                        self.progress["streak"])
        return hit

    def _streak(self):
        """The streak as of RIGHT NOW. A stored streak whose last qualifying day
        is older than yesterday is already broken; reporting it until the next
        lesson happens to notice would be a lie on the home screen."""
        last = self.progress.get("streak_day")
        if last in (_today(), _yesterday()):
            return self.progress.get("streak", 0)
        return 0

    # ---------------- hearts ----------------
    def _hearts(self):
        """Hearts held now, crediting whatever the refill clock has earned since
        the last check. Never returns more than HEARTS_MAX and never less than
        zero, whatever the file said."""
        if not self.progress.get("hearts_on", True):
            return HEARTS_MAX
        h = max(0, min(HEARTS_MAX, self.progress.get("hearts", HEARTS_MAX)))
        if h >= HEARTS_MAX:
            return HEARTS_MAX
        last = self.progress.get("heart_time", 0) or 0
        if not last:
            self.progress["heart_time"] = time.time()
            return h
        gained = int(max(0.0, time.time() - last) // HEART_REFILL_S)
        if gained:
            h = min(HEARTS_MAX, h + gained)
            self.progress["hearts"] = h
            # Advance the clock by what was SPENT, not to now: rounding the
            # remainder away would throw out up to twenty minutes of waiting
            # every time the course page repainted.
            self.progress["heart_time"] = (0 if h >= HEARTS_MAX
                                           else last + gained * HEART_REFILL_S)
        return h

    def _lose_heart(self):
        if not self.progress.get("hearts_on", True):
            return HEARTS_MAX
        h = max(0, self._hearts() - 1)
        if self.progress.get("hearts", HEARTS_MAX) >= HEARTS_MAX:
            self.progress["heart_time"] = time.time()
        self.progress["hearts"] = h
        return h

    def _fill_hearts(self):
        self.progress["hearts"] = HEARTS_MAX
        self.progress["heart_time"] = 0

    def _heart_wait(self):
        """Minutes until the next heart, or 0 when hearts are already full."""
        if self._hearts() >= HEARTS_MAX:
            return 0
        last = self.progress.get("heart_time", 0) or time.time()
        left = HEART_REFILL_S - (time.time() - last) % HEART_REFILL_S
        return max(1, int(math.ceil(left / 60.0)))

    # ---------------- word strength ----------------
    def _skey(self, code, term):
        return "%s:%s" % (code, _norm(term))

    def _item_skey(self, code, item):
        """Stable lexical identity; Mandarin homophones include their Hanzi."""
        base = self._skey(code, item.get("t", ""))
        note = _norm(item.get("note", ""))
        return "%s:%s" % (base, note) if code == "zh" and note else base

    def _term_is_unambiguous(self, code, term):
        course = next((c for c in self.courses if c.get("code") == code), None)
        if not course:
            return True
        identities = set()
        for unit in course.get("units", []):
            for skill in unit.get("skills", []):
                for item in list(skill.get("words", [])) + list(skill.get("phrases", [])):
                    if isinstance(item, dict) and _norm(item.get("t", "")) == _norm(term):
                        identities.add(self._item_skey(code, item))
        return len(identities) <= 1

    def _item_progress_key(self, code, item, table):
        key = self._item_skey(code, item)
        if key in table:
            return key
        legacy = self._skey(code, item.get("t", ""))
        if legacy in table and self._term_is_unambiguous(code, item.get("t", "")):
            return legacy
        return key

    def _strength(self, term, code=None, item=None):
        """A word's strength right now: what it was last set to, less one point
        for every DECAY_S it has sat untouched. This is the only thing that ever
        makes an old word come back round in Practice."""
        code = code or self.course["code"]
        table = self.progress.get("strength", {})
        key = (self._item_progress_key(code, item, table)
               if item is not None else self._skey(code, term))
        row = table.get(key)
        if not isinstance(row, dict):
            return 0
        s = row.get("s", 0)
        ts = row.get("t", 0) or 0
        try:
            decay = int(max(0.0, time.time() - ts) // DECAY_S)
        except (TypeError, ValueError):
            decay = 0
        return max(0, min(STRENGTH_MAX, int(s) - decay))

    def _bump_strength(self, term, ok, key=None):
        code = self.course["code"]
        k = key or self._skey(code, term)
        row = self.progress.get("strength", {}).get(k)
        cur = self._strength(term, code) if row is None else max(0, min(
            STRENGTH_MAX, int(row.get("s", 0))))
        new = min(STRENGTH_MAX, cur + 1) if ok else max(0, cur - 1)
        self.progress.setdefault("strength", {})[k] = {"s": new,
                                                       "t": time.time()}

    # ---------------- what is unlocked ----------------
    def _skill_open(self, ui, si):
        """A skill opens when the one before it has a crown. The first skill of
        the first unlocked unit is always open, so a fresh course is never a
        wall of grey."""
        if ui == 0 and si == 0:
            return True
        if not self._unit_open(ui):
            return False
        if si == 0:
            return True
        return self._crowns(ui, si - 1) > 0

    def _unit_open(self, ui):
        if ui == 0:
            return True
        if self.progress.get("tests", {}).get("%s:%d"
                                              % (self.course["code"], ui - 1)):
            return True
        prev = self.course["units"][ui - 1].get("skills", [])
        return all(self._crowns(ui - 1, i) > 0 for i in range(len(prev)))

    def _test_open(self, ui):
        """The unit test opens once every skill in the unit has been started.
        Passing it is what carries a learner who already knows the material
        past a unit without sitting twenty lessons."""
        skills = self.course["units"][ui].get("skills", [])
        return bool(skills) and all(self._crowns(ui, i) > 0
                                    for i in range(len(skills)))

    def _test_passed(self, ui):
        return bool(self.progress.get("tests", {}).get(
            "%s:%d" % (self.course["code"], ui)))

    def _unit_done(self, ui):
        skills = self.course["units"][ui].get("skills", [])
        return bool(skills) and all(self._crowns(ui, i) >= CROWN_MAX
                                    for i in range(len(skills)))

    # ---------------- counting, for cards and awards ----------------
    def _skill_keys(self, c):
        """The progress keys each skill of a course can carry, in path order,
        built once per course.

        Both key shapes go in, so reading this never walks the whole course to
        disambiguate a homophone the way _item_progress_key does -- a card only
        needs to know the skill was opened. Cached because _norm normalises
        every one of a course's 640 words: doing that for five cards on every
        home render cost more than drawing them. The course files are read once
        at startup and never change under it."""
        code = c.get("code", "")
        cache = getattr(self, "_skillkeys", None)
        if cache is None:
            cache = self._skillkeys = {}
        rows = cache.get(code)
        if rows is None:
            rows = []
            for unit in c.get("units", []):
                for skill in unit.get("skills", []):
                    keys = set()
                    for it in (list(skill.get("words") or [])
                               + list(skill.get("phrases") or [])):
                        if not isinstance(it, dict) or not it.get("t"):
                            continue
                        keys.add(self._skey(code, it["t"]))
                        keys.add(self._item_skey(code, it))
                    rows.append(keys)
            cache[code] = rows
        return rows

    def _course_progress(self, c):
        """(skills finished, skills started, crowns) for a course. Keyed off the
        course code directly, because the picker runs before any course is open
        and _pkey reads self.course.

        STARTED is counted from the words the skill has met, not from its crown.
        A crown needs a lesson with NO mistakes, so a learner who had finished
        lessons here still met a card reading "10 units - 40 skills" like the
        courses they had never opened, and an Explorer award reading "Courses
        started: 0"."""
        crowns = self.progress.get("crowns", {})
        if not isinstance(crowns, dict):
            crowns = {}
        code = c.get("code", "")
        seen = set(self.progress.get("seen", []))
        prefix = "%s:" % code
        # A course with no word met has no started skill either, and that is
        # every card on a fresh picker: don't index 640 words to find it out.
        rows = (self._skill_keys(c)
                if any(k.startswith(prefix) for k in seen) else None)
        done = started = total = 0
        i = -1
        for ui, unit in enumerate(c.get("units", [])):
            for si, skill in enumerate(unit.get("skills", [])):
                i += 1
                try:
                    v = int(crowns.get("%s%d:%d" % (prefix, ui, si), 0) or 0)
                except (TypeError, ValueError):
                    v = 0
                total += v
                if v >= CROWN_MAX:
                    done += 1
                if v > 0 or (rows is not None and i < len(rows)
                             and not seen.isdisjoint(rows[i])):
                    started += 1
        return done, started, total

    def _words_learned(self, code=None):
        seen = self.progress.get("seen", [])
        if code is None:
            return len(seen)
        return sum(1 for s in seen if s.startswith("%s:" % code))

    def _units_finished(self):
        """Units finished across every course, for the Champion award."""
        n = 0
        keep = self.course
        try:
            for c in self.courses:
                self.course = c
                for ui in range(len(c.get("units", []))):
                    if self._unit_done(ui):
                        n += 1
        finally:
            self.course = keep
        return n

    def _award_level(self, key):
        """How many of an award's five tiers are met, and the count it is
        scored on."""
        got = 0
        if key == "wildfire":
            n = max(self._streak(),
                    self.progress.get("stats", {}).get("best_streak", 0))
        elif key == "scholar":
            n = self._words_learned()
        elif key == "sharpshooter":
            n = self.progress.get("stats", {}).get("perfect", 0)
        elif key == "champion":
            n = self._units_finished()
        elif key == "sage":
            n = self.progress.get("xp", 0)
        elif key == "regal":
            n = sum(self._course_progress(c)[2] for c in self.courses)
        elif key == "explorer":
            n = sum(1 for c in self.courses if self._course_progress(c)[1])
        else:
            return 0, 0
        tiers = dict((k, t) for k, _m, _n, _w, t in AWARDS)[key]
        for t in tiers:
            if n >= t:
                got += 1
        return got, n

    # ==================================================================
    # small shared widgets
    # ==================================================================
    def _icon(self, name, size, color=INK):
        return nbicons.image(name, size, color)

    def _chip(self, icon, text, color=MUTED, css="statchip"):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        box.get_style_context().add_class(css)
        box.pack_start(self._icon(icon, 15, color), False, False, 0)
        lb = Gtk.Label(label=text)
        lb.get_style_context().add_class("statchiptext")
        box.pack_start(lb, False, False, 0)
        return box

    def _flat_button(self, label, css, action, icon=None, icolor=None):
        b = Gtk.Button()
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class(css)
        if icon:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.pack_start(self._icon(icon, 14, icolor or MUTED), False, False, 0)
            row.pack_start(Gtk.Label(label=label), False, False, 0)
            b.add(row)
        else:
            b.set_label(label)
        b.connect("clicked", lambda *_: action())
        return b

    def _hearts_row(self, size=16):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        if not self.progress.get("hearts_on", True):
            return row
        h = self._hearts()
        for i in range(HEARTS_MAX):
            row.pack_start(self._icon("heart", size,
                                      RED if i < h else "#D7D2C5"),
                           False, False, 0)
        if h < HEARTS_MAX:
            lb = Gtk.Label(label=_t("%d min") % self._heart_wait())
            lb.get_style_context().add_class("heartwait")
            row.pack_start(lb, False, False, 0)
        return row

    def _toast(self, text):
        """A line at the foot of the course page, gone in a few seconds. Used
        for the things that are an ANSWER to a tap rather than a screen of their
        own: why a locked node did nothing, that hearts are full again."""
        lbl = getattr(self, "_course_toast", None)
        if lbl is None:
            return
        lbl.set_text(text)
        lbl.get_style_context().add_class("toastshown")
        self._toast_id += 1
        mine = self._toast_id

        def clear():
            if mine == self._toast_id and lbl.get_parent() is not None:
                lbl.set_text("")
                lbl.get_style_context().remove_class("toastshown")
            return False
        # Own this delay just like lesson feedback: closing the window while a
        # toast is visible must cancel the source rather than retain the whole
        # window and call back into a label already being torn down.
        self._lesson_later(3200, clear)

    # ==================================================================
    # home: the course picker
    # ==================================================================
    def _home_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.get_style_context().add_class("homepage")

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head.set_margin_top(26)
        head.set_margin_bottom(6)
        t = Gtk.Label(label=_t("Learn a language"))
        t.get_style_context().add_class("hometitle")
        head.pack_start(t, False, False, 0)
        s = Gtk.Label(label=_t("Pronunciation shown in IPA"))
        s.get_style_context().add_class("homesub")
        head.pack_start(s, False, False, 0)
        self._home_stats = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                   spacing=10)
        self._home_stats.set_halign(Gtk.Align.CENTER)
        self._home_stats.set_margin_top(10)
        head.pack_start(self._home_stats, False, False, 0)
        outer.pack_start(head, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(3)
        flow.set_min_children_per_line(1)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_row_spacing(16)
        flow.set_column_spacing(16)
        flow.set_margin_start(28)
        flow.set_margin_end(28)
        flow.set_margin_top(18)
        flow.set_margin_bottom(24)
        # Cards keep their own height and a card-like width. Without these the
        # homogeneous FlowBox stretched every card to fill the scroller in both
        # directions: each course sat in the top inch of a 300px-tall panel, a
        # third of the screen wide. Three 210px columns, centred, at any size.
        flow.set_valign(Gtk.Align.START)
        flow.set_halign(Gtk.Align.CENTER)
        flow.set_size_request(3 * 210 + 2 * 16, -1)

        if not self.courses:
            # Courses ship with the system, so an empty picker means the course
            # files are gone, not that there is nothing to do yet. Say what the
            # screen is for and what actually restores it -- "No courses
            # installed." named the absence and left the reader with no move.
            empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            empty.set_valign(Gtk.Align.CENTER)
            t = Gtk.Label(label=_t("No courses installed"))
            t.get_style_context().add_class("hometitle")
            empty.pack_start(t, False, False, 0)
            s = Gtk.Label(label=_t(
                "Reinstall Notebook OS to restore the courses."))
            s.get_style_context().add_class("homesub")
            s.set_line_wrap(True)
            s.set_max_width_chars(46)
            s.set_justify(Gtk.Justification.CENTER)
            empty.pack_start(s, False, False, 0)
            outer.pack_start(empty, True, True, 0)
            self._refresh_home_stats()
            return outer

        self._card_flow = flow
        col.pack_start(flow, False, False, 0)

        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        foot.set_halign(Gtk.Align.CENTER)
        foot.set_margin_bottom(26)
        foot.pack_start(self._flat_button(_t("Awards"), "linkbtn",
                                          self._show_awards, "trophy"),
                        False, False, 0)
        foot.pack_start(self._flat_button(_t("Daily Goal…"), "linkbtn",
                                          self._pick_goal, "target"),
                        False, False, 0)
        col.pack_start(foot, False, False, 0)
        scroll.add(col)
        outer.pack_start(scroll, True, True, 0)
        self._fill_cards()
        self._refresh_home_stats()
        return outer

    def _fill_cards(self):
        """(Re)build the course cards. Called again whenever the picker comes
        back into view so a card's progress reflects the lesson just finished
        rather than the state at launch."""
        flow = getattr(self, "_card_flow", None)
        if flow is None:
            return
        for ch in flow.get_children():
            flow.remove(ch)
        for c in self.courses:
            flow.add(self._course_card(c))
        flow.show_all()

    def _refresh_home_stats(self):
        """Streak, today's goal and total crowns, as three chips under the
        title. A brand-new learner still sees them -- at zero they read as an
        empty scoreboard, which is what they are, rather than as a screen with
        something missing from it."""
        box = getattr(self, "_home_stats", None)
        if box is None:
            return
        for ch in box.get_children():
            box.remove(ch)
        streak = self._streak()
        box.pack_start(self._chip("flame", _t("%d day streak") % streak,
                                  GOLD if streak else "#B3AD9E"),
                       False, False, 0)
        day, goal = self._day_xp(), self._goal()
        box.pack_start(self._chip("target", _t("%d / %d XP today") % (day, goal),
                                  GREEN if day >= goal else MUTED),
                       False, False, 0)
        crowns = sum(self._course_progress(c)[2] for c in self.courses)
        box.pack_start(self._chip("crown", _t("%d crowns") % crowns,
                                  GOLD if crowns else "#B3AD9E"),
                       False, False, 0)
        box.show_all()

    def _show_home(self):
        # keep the stats and each card's progress current every time the picker
        # comes back into view
        self._refresh_home_stats()
        self._fill_cards()
        self._pager.switch("home")

    def _course_card(self, c):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        card.get_style_context().add_class("coursecard")
        card.set_size_request(210, 150)
        badge = Gtk.Label(label=(c.get("code", "?") or "?").upper()[:2])
        badge.get_style_context().add_class("codebadge")
        badge.set_halign(Gtk.Align.CENTER)
        # One pigment per course, taken from the same six the tree cycles. The
        # picker used to be five identical green discs with two letters in
        # them; a learner looking for the course they were half-way through was
        # reading text, not recognising a card.
        try:
            idx = self.courses.index(c)
        except ValueError:
            idx = 0
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(
                (".codebadge { background: %s; }"
                 % UNIT_COLORS[idx % len(UNIT_COLORS)][0]).encode("ascii"))
            badge.get_style_context().add_provider(
                prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        except Exception:
            pass
        card.pack_start(badge, False, False, 0)
        nm = Gtk.Label()
        _set_course_text(nm, c.get("name", "?"))
        nm.get_style_context().add_class("coursename")
        card.pack_start(nm, False, False, 0)
        sub = Gtk.Label(label=_t("from %s") % c.get("from", "English"))
        sub.get_style_context().add_class("coursefrom")
        card.pack_start(sub, False, False, 0)
        nskills = sum(len(u.get("skills", [])) for u in c.get("units", []))
        # What the card said before was the same three numbers for every course.
        # A returning learner could not tell which one they were half-way
        # through; the size of the course is what they need LAST.
        done, started, crowns = self._course_progress(c)
        if done or started:
            bar = Gtk.ProgressBar()
            bar.get_style_context().add_class("cardprog")
            bar.set_fraction(min(1.0, crowns
                                 / float(nskills * CROWN_MAX or 1)))
            bar.set_margin_top(6)
            card.pack_start(bar, False, False, 0)
            text = (_t("%d of %d skills finished") % (done, nskills) if done
                    else _t("One skill started") if started == 1
                    else _t("%d skills started") % started)
        else:
            text = _t("%d units · %d skills") % (len(c.get("units", [])),
                                                      nskills)
        meta = Gtk.Label(label=text)
        meta.get_style_context().add_class("coursemeta")
        meta.set_line_wrap(True)
        meta.set_justify(Gtk.Justification.CENTER)
        meta.set_max_width_chars(24)
        card.pack_start(meta, False, False, 0)
        evt = Gtk.Button()
        evt.set_relief(Gtk.ReliefStyle.NONE)
        evt.get_style_context().add_class("pathhit")
        evt.get_style_context().add_class("coursehit")
        evt.set_tooltip_text(_t("Open %s") % c.get("name", "?"))
        evt.add(card)
        evt.connect("clicked", lambda _w, co=c: self._open_course(co))
        return evt

    # ==================================================================
    # course: the path
    # ==================================================================
    def _open_course(self, c):
        self.course = c
        self._tok_code = None            # drop the other course's token cache
        self._syn_t, self._syn_e = self._synonyms(self._course_words())
        self._course_scroll = 0.0
        self._render_course()
        self._pager.switch("course")

    def _render_course(self, keep_scroll=False):
        want = self._course_scroll if keep_scroll else 0.0
        for ch in self._course_holder.get_children():
            self._course_holder.remove(ch)

        overlay = Gtk.Overlay()
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay.add(page)
        page.pack_start(self._course_bar(), False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._course_scroller = scroll
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        col.set_halign(Gtk.Align.CENTER)
        col.set_margin_bottom(40)
        gidx = 0
        for ui, unit in enumerate(self.course.get("units", [])):
            col.pack_start(self._unit_banner(ui, unit), False, False, 0)
            block, gidx = self._unit_path(ui, unit, gidx)
            col.pack_start(block, False, False, 0)
        scroll.add(col)
        page.pack_start(scroll, True, True, 0)

        toast = Gtk.Label(label="")
        toast.get_style_context().add_class("toast")
        toast.set_halign(Gtk.Align.CENTER)
        toast.set_valign(Gtk.Align.END)
        toast.set_margin_bottom(22)
        self._course_toast = toast
        overlay.add_overlay(toast)
        overlay.set_overlay_pass_through(toast, True)

        # The skill card and its scrim. Built once per course render and kept
        # hidden; showing it is what a tap on a node does.
        self._card_layer = self._build_card_layer()
        overlay.add_overlay(self._card_layer)

        self._course_holder.pack_start(overlay, True, True, 0)
        self._course_holder.show_all()
        self._card_layer.hide()
        adj = scroll.get_vadjustment()
        if want:
            # After show_all the scroller does not know its content height yet,
            # so setting the value now silently clamps it to zero. One idle
            # round later the adjustment is real.
            GLib.idle_add(lambda: (adj.set_value(min(
                want, max(0, adj.get_upper() - adj.get_page_size()))), False)[1])
        adj.connect("value-changed",
                    lambda a: setattr(self, "_course_scroll", a.get_value()))

    def _course_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.get_style_context().add_class("coursebar")
        back = self._flat_button(_t("Courses"), "backbtn", self._show_home,
                                 "back")
        bar.pack_start(back, False, False, 0)
        title = Gtk.Label()
        _set_course_text(title, self.course.get("name", ""))
        title.get_style_context().add_class("coursetitle")
        bar.pack_start(title, False, False, 0)
        # A course may carry one line about what it does and doesn't cover --
        # Mandarin teaches pinyin, not the characters. It was sitting unread in
        # the course file, so the learner met romanised Chinese with no
        # explanation; show it beside the title.
        if self.course.get("note"):
            note = Gtk.Label(label=self.course["note"])
            note.get_style_context().add_class("coursenote")
            note.set_ellipsize(Pango.EllipsizeMode.END)
            # A 40-character cap cut both shipped notes off mid-word at EVERY
            # width -- at 1366 with a third of the bar empty beside them -- so
            # the one line explaining that Mandarin teaches pinyin and not the
            # characters could not be read anywhere. The cap now clears the
            # longest of them; ellipsis still shortens it when the bar really is
            # crowded, and the tooltip carries the whole line either way.
            note.set_max_width_chars(70)
            note.set_tooltip_text(self.course["note"])
            bar.pack_start(note, False, False, 0)
        bar.pack_start(Gtk.Box(), True, True, 0)

        bar.pack_start(self._flat_button(_t("Practice"), "toolbtn",
                                         self._start_practice, "workout"),
                       False, False, 0)
        bar.pack_start(self._flat_button(_t("Vocabulary"), "toolbtn",
                                         self._show_vocab, "viewlist"),
                       False, False, 0)
        if self.course.get("alphabet"):
            bar.pack_start(self._flat_button(_t("Alphabet"), "toolbtn",
                                             self._show_alphabet, "quote"),
                           False, False, 0)
        bar.pack_start(Gtk.Box(), False, False, 4)
        hr = self._hearts_row()
        hr.set_valign(Gtk.Align.CENTER)
        bar.pack_end(hr, False, False, 0)
        bar.pack_end(self._chip("target",
                                _t("%d / %d XP") % (self._day_xp(),
                                                    self._goal()),
                                GREEN if self._day_xp() >= self._goal()
                                else MUTED), False, False, 0)
        bar.pack_end(self._chip("flame", "%d" % self._streak(),
                                GOLD if self._streak() else "#B3AD9E"),
                     False, False, 0)
        return bar

    def _unit_banner(self, ui, unit):
        ink, wash = UNIT_COLORS[ui % len(UNIT_COLORS)]
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.get_style_context().add_class("unitbanner")
        box.set_size_request(PATH_W - 20, -1)
        box.set_margin_top(22)
        box.set_margin_bottom(4)
        open_ = self._unit_open(ui)

        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t = Gtk.Label(label=unit.get("title", _t("Unit %d") % (ui + 1)),
                      xalign=0)
        t.get_style_context().add_class("unittitle")
        txt.pack_start(t, False, False, 0)
        sub = Gtk.Label(label=unit.get("subtitle", ""), xalign=0)
        sub.get_style_context().add_class("unitsub")
        sub.set_line_wrap(True)
        sub.set_max_width_chars(40)
        txt.pack_start(sub, False, False, 0)
        box.pack_start(txt, True, True, 0)

        if open_:
            b = self._flat_button(_t("Tips"), "unitbtn",
                                  lambda u=ui: self._show_unit_tips(u),
                                  "ebook", ink)
            b.set_valign(Gtk.Align.CENTER)
            box.pack_end(b, False, False, 0)
        else:
            lk = self._icon("lock", 17, "#9A9484")
            lk.set_valign(Gtk.Align.CENTER)
            box.pack_end(lk, False, False, 0)

        # The banner is the only place the unit's colour is stated outright, so
        # it is set per widget rather than from the stylesheet -- ten units,
        # six pigments, one CSS class. A LOCKED unit keeps its colour: greying
        # the banner too meant that below whatever the learner was working on,
        # the whole rest of the course was one undifferentiated grey column.
        # The lock at the end of the banner is what says locked.
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(
                (".unitbanner { background: %s; %s: 4px solid %s; }"
                 % (wash, self._lead_border(),
                    ink if open_ else "#C9C4B6")).encode("ascii"))
            box.get_style_context().add_provider(
                prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
            t.get_style_context().add_provider(prov, 0)
        except Exception:
            pass
        return box

    def _unit_path(self, ui, unit, gidx):
        """One unit's stretch of the path: the skill nodes on their serpentine,
        the checkpoint at the foot of it, and the trail drawn behind them."""
        skills = unit.get("skills", [])
        rows = len(skills) + 1                 # + the unit test
        ink, wash = UNIT_COLORS[ui % len(UNIT_COLORS)]

        pts = []
        for i in range(rows):
            # One continuous serpentine down the whole course, not a fresh
            # wiggle per unit: the global index is what keeps the trail from
            # jumping back to centre at every banner.
            x = PATH_W / 2.0 + AMP * math.sin(2 * math.pi * (gidx + i) / 6.0)
            pts.append((x, ROW_H * i + ROW_H / 2.0 + 6))
        height = int(ROW_H * rows + 30)

        overlay = Gtk.Overlay()
        da = Gtk.DrawingArea()
        da.set_size_request(PATH_W, height)
        states = [self._node_state(ui, si) for si in range(len(skills))]
        states.append("test-done" if self._test_passed(ui)
                      else "open" if self._test_open(ui) else "locked")
        crowns = [self._crowns(ui, si) for si in range(len(skills))] + [0]
        da.connect("draw", lambda _w, cr: self._draw_trail(
            cr, pts, states, crowns, ink))
        overlay.add(da)

        fixed = Gtk.Fixed()
        overlay.add_overlay(fixed)
        for si, skill in enumerate(skills):
            x, y = pts[si]
            fixed.put(self._skill_node(ui, si, skill, states[si], ink),
                      int(x - NODE / 2), int(y - NODE / 2))
            fixed.put(self._node_label(skill.get("name", "?"), crowns[si],
                                       states[si]),
                      int(x - 68), int(y + NODE / 2 + 7))
        x, y = pts[-1]
        fixed.put(self._test_node(ui, states[-1], ink), int(x - 52), int(y - 30))
        fixed.put(self._node_label(_t("Unit test"), 0, states[-1]),
                  int(x - 68), int(y + 36))
        return overlay, gidx + rows

    def _node_state(self, ui, si):
        if not self._skill_open(ui, si):
            return "locked"
        c = self._crowns(ui, si)
        if c >= CROWN_MAX:
            return "done"
        return "started" if c else "open"

    def _draw_trail(self, cr, pts, states, crowns, ink):
        """The trail between the nodes, and each node's crown ring.

        Drawn rather than built from widgets because a ring is an ARC of a
        circle: expressing 3 crowns out of 5 as GTK boxes means five little
        rectangles under the node, which is what this replaced and what made
        every skill look the same from across the room."""
        cr.set_line_cap(1)                       # round
        cr.set_line_join(1)
        # trail
        for i in range(len(pts) - 1):
            live = states[i] != "locked" and states[i + 1] != "locked"
            cr.set_source_rgba(*self._rgba(ink if live else "#D7D2C5",
                                           0.55 if live else 0.9))
            cr.set_line_width(7)
            cr.move_to(*pts[i])
            cr.line_to(*pts[i + 1])
            cr.stroke()
        # crown rings
        r = NODE / 2.0 + 6
        for i, (x, y) in enumerate(pts):
            if states[i] == "locked" or i >= len(crowns):
                continue
            n = crowns[i]
            cr.set_line_width(4)
            cr.set_source_rgba(*self._rgba("#DED4C2", 1.0))
            cr.arc(x, y, r, 0, 2 * math.pi)
            cr.stroke()
            if not n:
                continue
            cr.set_source_rgba(*self._rgba(GOLD if n >= CROWN_MAX else ink, 1.0))
            start = -math.pi / 2
            cr.arc(x, y, r, start, start + 2 * math.pi * n / float(CROWN_MAX))
            cr.stroke()

    @staticmethod
    def _lead_border():
        """"border-left" or "border-right", whichever is the LEADING edge.

        GTK3 CSS has no logical border properties, so an accent bar written as
        border-left stays on the left in a right-to-left language -- where it is
        no longer the edge the eye starts from, and reads as a bar hanging off
        the end of the card. Every other direction-sensitive thing in this app
        (the menu bar, the back button, label alignment, pack_end) is mirrored
        for free by GTK; these two rules are the exception."""
        try:
            rtl = (Gtk.Widget.get_default_direction()
                   == Gtk.TextDirection.RTL)
        except Exception:
            rtl = False
        return "border-right" if rtl else "border-left"

    @staticmethod
    def _rgba(hexstr, alpha=1.0):
        h = hexstr.lstrip("#")
        return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
                int(h[4:6], 16) / 255.0, alpha)

    def _skill_node(self, ui, si, skill, state, ink):
        circle = Gtk.Box()
        ctx = circle.get_style_context()
        ctx.add_class("skillnode")
        circle.set_size_request(NODE, NODE)
        # NODE x NODE plus border-radius 50% is only a CIRCLE if nothing
        # stretches it. The node sits in a Fixed, which gives a child its
        # natural size -- but both it and its event box default to halign FILL,
        # so every skill used to draw as a flat ellipse.
        circle.set_halign(Gtk.Align.CENTER)
        circle.set_valign(Gtk.Align.CENTER)
        name = skill.get("name", "?")
        icon = skill.get("icon") or SKILL_ICONS.get(name, "star")
        if state == "locked":
            # Grey, but still ITS OWN mark. Drawing a padlock in every locked
            # node made the nine units below the one in progress forty identical
            # circles -- the tree stopped saying anything about what was coming,
            # which is half of what a tree is for. The grey fill is what says
            # locked; the icon still says what the skill is.
            ctx.add_class("skilllocked")
            colour = "#9A9484"
        elif state == "done":
            ctx.add_class("skilldone")
            colour = PAPER
        elif state == "started":
            ctx.add_class("skillstarted")
            colour = ink
        else:
            colour = ink
        img = self._icon(icon, 34, colour)
        img.set_halign(Gtk.Align.CENTER)
        img.set_valign(Gtk.Align.CENTER)
        img.set_hexpand(True)
        img.set_vexpand(True)
        circle.pack_start(img, True, True, 0)
        if state in ("open", "started", "done"):
            prov = Gtk.CssProvider()
            try:
                fill = ink if state == "done" else PAPER
                prov.load_from_data(
                    (".skillnode { border-color: %s; background: %s; }"
                     % (ink, fill)).encode("ascii"))
                ctx.add_provider(prov,
                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
            except Exception:
                pass
        evt = Gtk.Button()
        evt.set_relief(Gtk.ReliefStyle.NONE)
        evt.get_style_context().add_class("pathhit")
        evt.set_halign(Gtk.Align.CENTER)
        evt.set_valign(Gtk.Align.CENTER)
        evt.set_tooltip_text(
            (_t("%s — locked; activate for requirements") % name)
            if state == "locked" else (_t("Open %s") % name))
        evt.add(circle)
        evt.connect("clicked",
                    lambda _w: self._tap_skill(ui, si, skill, state))
        return evt

    def _test_node(self, ui, state, ink):
        box = Gtk.Box()
        ctx = box.get_style_context()
        ctx.add_class("testnode")
        box.set_size_request(104, 60)
        if state == "locked":
            ctx.add_class("skilllocked")
            colour = "#9A9484"
            icon = "lock"
        elif state == "test-done":
            ctx.add_class("skilldone")
            colour = PAPER
            icon = "trophy"
        else:
            colour = ink
            icon = "trophy"
        img = self._icon(icon, 30, colour)
        img.set_hexpand(True)
        img.set_vexpand(True)
        box.pack_start(img, True, True, 0)
        if state != "locked":
            prov = Gtk.CssProvider()
            try:
                fill = ink if state == "test-done" else PAPER
                prov.load_from_data(
                    (".testnode { border-color: %s; background: %s; }"
                     % (ink, fill)).encode("ascii"))
                ctx.add_provider(prov,
                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
            except Exception:
                pass
        evt = Gtk.Button()
        evt.set_relief(Gtk.ReliefStyle.NONE)
        evt.get_style_context().add_class("pathhit")
        evt.set_tooltip_text(
            _t("Unit test — locked; activate for requirements")
            if state == "locked" else _t("Open unit test"))
        evt.add(box)
        evt.connect("clicked", lambda _w: self._tap_test(ui, state))
        return evt

    def _node_label(self, name, crowns, state):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        box.set_size_request(136, -1)
        nm = Gtk.Label(label=name)
        nm.get_style_context().add_class(
            "skillname" + (" skillnamelocked" if state == "locked" else ""))
        nm.set_line_wrap(True)
        nm.set_justify(Gtk.Justification.CENTER)
        nm.set_max_width_chars(15)
        nm.set_lines(2)
        nm.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(nm, False, False, 0)
        if crowns:
            cl = Gtk.Label(label=_t("Level %d") % crowns)
            cl.get_style_context().add_class("skilllevel")
            box.pack_start(cl, False, False, 0)
        return box

    # ---------------- taps on the path ----------------
    def _tap_skill(self, ui, si, skill, state):
        if state == "locked":
            self._toast(self._why_locked(ui, si))
            return
        self._show_skill_card(ui, si, skill)

    def _why_locked(self, ui, si):
        """Name the ONE thing that opens this node. 'Locked' is not an answer to
        a tap -- it is the observation the learner just made."""
        # What opens the next node is a CROWN, and a crown needs a lesson with
        # no mistakes. Naming only "a lesson" told a learner who had just
        # finished one to go and do the thing they had done.
        if not self._unit_open(ui):
            prev = self.course["units"][ui - 1]
            return _t("Finish a lesson with no mistakes in every skill of %s "
                      "first") % prev.get("title", _t("the unit before"))
        prev = self.course["units"][ui]["skills"][si - 1]
        return _t("Finish a lesson in %s with no mistakes first") \
            % prev.get("name", "")

    def _tap_test(self, ui, state):
        if state == "locked":
            self._toast(_t("Start every skill in this unit to open its test"))
            return
        self._show_test_card(ui)

    # ---------------- the card over the path ----------------
    def _build_card_layer(self):
        """The dimmed layer a skill card sits on, hidden until a node is tapped.

        set_no_show_all is not a nicety here. This layer is built during
        _render_course and then hidden -- but the app's window is shown with
        gtk_widget_show_all AFTER that, and show_all shows EVERY descendant,
        hidden or not. Without the flag the scrim came back up on its own and
        the whole course path rendered under a grey wash with nothing on it.

        The flag stops show_all from reaching this subtree at all, which means
        _open_card has to show the pieces itself -- plain show() is not blocked
        by the flag, only show_all() is."""
        layer = Gtk.Overlay()
        layer.set_no_show_all(True)
        scrim = Gtk.EventBox()
        scrim.get_style_context().add_class("scrim")
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.connect("button-press-event",
                      lambda *_: (self._hide_card(), True)[1])
        layer.add(scrim)
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        holder.set_halign(Gtk.Align.CENTER)
        holder.set_valign(Gtk.Align.CENTER)
        layer.add_overlay(holder)
        self._card_holder = holder
        self._card_scrim = scrim
        return layer

    def _hide_card(self):
        layer = getattr(self, "_card_layer", None)
        if layer is not None:
            layer.hide()

    def _open_card(self, card):
        holder = self._card_holder
        for ch in holder.get_children():
            holder.remove(ch)
        holder.pack_start(card, False, False, 0)
        card.show_all()
        holder.show()
        self._card_scrim.show()
        self._card_layer.show()

    def _card_shell(self, title, sub):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.get_style_context().add_class("skillcard")
        box.set_size_request(360, -1)
        t = Gtk.Label(label=title)
        t.get_style_context().add_class("cardtitle")
        t.set_line_wrap(True)
        t.set_justify(Gtk.Justification.CENTER)
        t.set_max_width_chars(28)
        box.pack_start(t, False, False, 0)
        if sub:
            s = Gtk.Label(label=sub)
            s.get_style_context().add_class("cardsub")
            s.set_line_wrap(True)
            s.set_justify(Gtk.Justification.CENTER)
            s.set_max_width_chars(38)
            box.pack_start(s, False, False, 0)
        return box

    def _show_skill_card(self, ui, si, skill):
        crowns = self._crowns(ui, si)
        name = skill.get("name", "?")
        ink, _wash = UNIT_COLORS[ui % len(UNIT_COLORS)]
        if crowns >= CROWN_MAX:
            sub = _t("Finished. Practice keeps it from going weak.")
        else:
            sub = _t("Level %d of %d") % (crowns + 1, CROWN_MAX)
        card = self._card_shell(name, sub)

        icon = self._icon(skill.get("icon") or SKILL_ICONS.get(name, "star"),
                          40, ink)
        icon.set_halign(Gtk.Align.CENTER)
        card.pack_start(icon, False, False, 0)
        card.reorder_child(icon, 0)

        pips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        pips.set_halign(Gtk.Align.CENTER)
        for i in range(CROWN_MAX):
            pips.pack_start(self._icon("crown", 17,
                                       GOLD if i < crowns else "#D7D2C5"),
                            False, False, 0)
        card.pack_start(pips, False, False, 0)

        words, phrases = self._skill_items(skill)
        seen_table = set(self.progress.get("seen", []))
        met = sum(1 for it in words + phrases
                  if self._item_progress_key(self.course["code"], it,
                                             seen_table) in seen_table)
        meta = Gtk.Label(label=_t("%d of %d words and phrases met")
                         % (met, len(words) + len(phrases)))
        meta.get_style_context().add_class("cardmeta")
        card.pack_start(meta, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.CENTER)
        row.set_margin_top(6)
        if skill.get("tips"):
            row.pack_start(self._flat_button(
                _t("Tips"), "cardsecond",
                lambda: (self._hide_card(),
                         self._show_skill_tips(ui, si))[-1], "ebook"),
                False, False, 0)
        # NOT _t("Start"): that key already ships meaning the START of a
        # range -- Japanese renders it as the noun "first", Chinese as
        # "starting point", Russian as "the beginning". A key is only as
        # reusable as its narrowest existing sense. Same reason the words
        # button says Vocabulary: "Words" was already taken by the word
        # COUNT in the text editors, and rendered as such in ja/ko/zh.
        # "Start lesson" until the skill has been TOUCHED, not until it has a
        # crown: a lesson finished with a mistake in it earns no crown, and the
        # card went back to offering a start as if it had never been sat.
        label = (_t("Practice") if crowns >= CROWN_MAX
                 else _t("Continue") if (crowns or met) else _t("Start lesson"))
        row.pack_start(self._flat_button(
            label, "checkbtn",
            lambda: (self._hide_card(), self._start_lesson(ui, si))[-1]),
            False, False, 0)
        card.pack_start(row, False, False, 0)
        self._open_card(card)

    def _show_test_card(self, ui):
        unit = self.course["units"][ui]
        passed = self._test_passed(ui)
        card = self._card_shell(
            _t("%s test") % unit.get("title", ""),
            _t("Passed. Sit it again to strengthen the unit.") if passed else
            _t("%d questions drawn from the whole unit. Pass with %d mistakes "
               "or fewer to open the next unit.") % (TEST_LEN, TEST_ALLOWED))
        icon = self._icon("trophy", 40, GOLD if passed
                          else UNIT_COLORS[ui % len(UNIT_COLORS)][0])
        icon.set_halign(Gtk.Align.CENTER)
        card.pack_start(icon, False, False, 0)
        card.reorder_child(icon, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.CENTER)
        row.set_margin_top(6)
        row.pack_start(self._flat_button(
            _t("Take the test"), "checkbtn",
            lambda: (self._hide_card(), self._start_test(ui))[-1]),
            False, False, 0)
        card.pack_start(row, False, False, 0)
        self._open_card(card)

    def _no_hearts_card(self):
        card = self._card_shell(
            _t("Out of hearts"),
            _t("One heart comes back every %d minutes. Practice refills them "
               "all and costs none.") % (HEART_REFILL_S // 60))
        icon = self._icon("heart", 40, RED)
        icon.set_halign(Gtk.Align.CENTER)
        card.pack_start(icon, False, False, 0)
        card.reorder_child(icon, 0)
        wait = Gtk.Label(label=_t("Next heart in %d min") % self._heart_wait())
        wait.get_style_context().add_class("cardmeta")
        card.pack_start(wait, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.CENTER)
        row.set_margin_top(6)
        row.pack_start(self._flat_button(
            _t("Turn Hearts Off"), "cardsecond",
            lambda: (self._hide_card(), self._toggle_hearts())[-1]),
            False, False, 0)
        row.pack_start(self._flat_button(
            _t("Practice"), "checkbtn",
            lambda: (self._hide_card(), self._start_practice())[-1]),
            False, False, 0)
        card.pack_start(row, False, False, 0)
        self._open_card(card)

    # ==================================================================
    # secondary pages: tips, vocabulary, awards, alphabet
    # ==================================================================
    def _page_shell(self, title, back_label, back_action):
        # Esc has to leave by the SAME door the Back button uses. Awards opened
        # from the picker goes back to the picker; opened from a course, back to
        # the path -- and a hardcoded Esc target got one of those wrong.
        self._page_back = back_action
        for ch in self._page_holder.get_children():
            self._page_holder.remove(ch)
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.get_style_context().add_class("coursebar")
        bar.pack_start(self._flat_button(back_label, "backbtn", back_action,
                                         "back"), False, False, 0)
        t = Gtk.Label(label=title)
        t.get_style_context().add_class("coursetitle")
        bar.pack_start(t, False, False, 0)
        bar.pack_start(Gtk.Box(), True, True, 0)
        self._page_holder.pack_start(bar, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        col.set_halign(Gtk.Align.CENTER)
        col.set_size_request(640, -1)
        col.set_margin_top(20)
        col.set_margin_bottom(36)
        scroll.add(col)
        self._page_holder.pack_start(scroll, True, True, 0)
        return col

    def _back_to_course(self):
        self._render_course(keep_scroll=True)
        self._pager.switch("course")

    # ---------------- tips ----------------
    def _tip_card(self, tip, ink):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        box.get_style_context().add_class("tipcard")
        h = Gtk.Label(label=tip.get("h", ""), xalign=0)
        h.get_style_context().add_class("tiph")
        h.set_line_wrap(True)
        box.pack_start(h, False, False, 0)
        b = Gtk.Label(label=tip.get("b", ""), xalign=0)
        b.get_style_context().add_class("tipb")
        b.set_line_wrap(True)
        b.set_max_width_chars(70)
        box.pack_start(b, False, False, 0)
        eg = tip.get("eg")
        if isinstance(eg, list) and eg:
            grid = Gtk.Grid()
            grid.get_style_context().add_class("tipgrid")
            grid.set_column_spacing(20)
            grid.set_row_spacing(5)
            grid.set_margin_top(4)
            r = 0
            for row in eg:
                if not (isinstance(row, (list, tuple)) and len(row) == 2):
                    continue
                a = Gtk.Label(label=str(row[0]), xalign=0)
                a.get_style_context().add_class("tipega")
                a.set_line_wrap(True)
                b2 = Gtk.Label(label=str(row[1]), xalign=0)
                b2.get_style_context().add_class("tipegb")
                b2.set_line_wrap(True)
                grid.attach(a, 0, r, 1, 1)
                grid.attach(b2, 1, r, 1, 1)
                r += 1
            if r:
                box.pack_start(grid, False, False, 0)
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data((".tipcard { %s: 3px solid %s; }"
                                 % (self._lead_border(), ink)).encode("ascii"))
            box.get_style_context().add_provider(
                prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        except Exception:
            pass
        return box

    def _show_skill_tips(self, ui, si):
        skill = self.course["units"][ui]["skills"][si]
        ink, _w = UNIT_COLORS[ui % len(UNIT_COLORS)]
        col = self._page_shell(skill.get("name", ""), _t("Back"),
                               self._back_to_course)
        tips = [t for t in (skill.get("tips") or []) if isinstance(t, dict)]
        if not tips:
            col.pack_start(self._empty_note(_t("This skill has no notes.")),
                           False, False, 0)
        for tip in tips:
            col.pack_start(self._tip_card(tip, ink), False, False, 0)
        # The words the notes are about, so the page is a reference and not
        # only prose.
        words, phrases = self._skill_items(skill)
        if words or phrases:
            hd = Gtk.Label(label=_t("Words in this skill"), xalign=0)
            hd.get_style_context().add_class("sectionhead")
            hd.set_margin_top(14)
            col.pack_start(hd, False, False, 0)
            for it in words + phrases:
                col.pack_start(self._vocab_row(it, self.course["code"]),
                               False, False, 0)
        self._page_holder.show_all()
        self._pager.switch("page")

    def _show_unit_tips(self, ui):
        unit = self.course["units"][ui]
        ink, _w = UNIT_COLORS[ui % len(UNIT_COLORS)]
        col = self._page_shell(unit.get("title", ""), _t("Back"),
                               self._back_to_course)
        sub = Gtk.Label(label=unit.get("subtitle", ""), xalign=0)
        sub.get_style_context().add_class("pagesub")
        sub.set_line_wrap(True)
        col.pack_start(sub, False, False, 0)
        any_tip = False
        for si, skill in enumerate(unit.get("skills", [])):
            tips = [t for t in (skill.get("tips") or []) if isinstance(t, dict)]
            if not tips:
                continue
            any_tip = True
            hd = Gtk.Label(xalign=0)
            _set_course_text(hd, skill.get("name", ""))
            hd.get_style_context().add_class("sectionhead")
            hd.set_margin_top(14)
            col.pack_start(hd, False, False, 0)
            for tip in tips:
                col.pack_start(self._tip_card(tip, ink), False, False, 0)
        if not any_tip:
            col.pack_start(self._empty_note(_t("This unit has no notes.")),
                           False, False, 0)
        self._page_holder.show_all()
        self._pager.switch("page")

    def _empty_note(self, text):
        lb = Gtk.Label(label=text)
        lb.get_style_context().add_class("emptynote")
        lb.set_line_wrap(True)
        lb.set_max_width_chars(50)
        lb.set_margin_top(30)
        return lb

    # ---------------- alphabet ----------------
    def _show_alphabet(self):
        rows = [r for r in (self.course.get("alphabet") or [])
                if isinstance(r, dict)]
        col = self._page_shell(_t("%s alphabet") % self.course.get("name", ""),
                               _t("Back"), self._back_to_course)
        sub = Gtk.Label(label=_t("The letters whose sound English spelling "
                                 "does not predict."), xalign=0)
        sub.get_style_context().add_class("pagesub")
        sub.set_line_wrap(True)
        col.pack_start(sub, False, False, 0)
        if not rows:
            col.pack_start(self._empty_note(
                _t("This course has no alphabet notes.")), False, False, 0)
        for r in rows:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
            box.get_style_context().add_class("alpharow")
            c = Gtk.Label(label=str(r.get("c", "")), xalign=0)
            c.get_style_context().add_class("alphac")
            c.set_size_request(70, -1)
            box.pack_start(c, False, False, 0)
            ipa = Gtk.Label(label=_t("/%s/") % r.get("ipa", ""), xalign=0)
            ipa.get_style_context().add_class("exipa")
            ipa.set_size_request(110, -1)
            box.pack_start(ipa, False, False, 0)
            e = Gtk.Label(label=str(r.get("e", "")), xalign=0)
            e.get_style_context().add_class("alphae")
            e.set_line_wrap(True)
            box.pack_start(e, True, True, 0)
            col.pack_start(box, False, False, 0)
        self._page_holder.show_all()
        self._pager.switch("page")

    # ---------------- vocabulary ----------------
    def _seen_key(self, term):
        return "%s:%s" % (self.course["code"], _norm(term))

    def _vocab_row(self, it, code):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        box.get_style_context().add_class("vocabrow")
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        left.set_size_request(230, -1)
        t = Gtk.Label(xalign=0)
        _set_course_text(t, it["t"])
        t.get_style_context().add_class("vocabt")
        t.set_line_wrap(True)
        left.pack_start(t, False, False, 0)
        if it.get("ipa"):
            i = Gtk.Label(label=_t("/%s/") % it["ipa"], xalign=0)
            i.get_style_context().add_class("exipa")
            i.set_line_wrap(True)
            left.pack_start(i, False, False, 0)
        box.pack_start(left, False, False, 0)

        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        e = Gtk.Label(xalign=0)
        _set_course_text(e, it["e"])
        e.get_style_context().add_class("vocabe")
        e.set_line_wrap(True)
        mid.pack_start(e, False, False, 0)
        if it.get("note"):
            n = Gtk.Label(xalign=0)
            _set_course_text(n, it["note"])
            n.get_style_context().add_class("vocabnote")
            mid.pack_start(n, False, False, 0)
        box.pack_start(mid, True, True, 0)

        strength = self.progress.get("strength", {})
        seen = self._item_progress_key(code, it, strength) in strength
        s = self._strength(it["t"], code, it)
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        bar.set_valign(Gtk.Align.CENTER)
        if seen:
            for i in range(STRENGTH_MAX):
                pip = Gtk.Box()
                pip.get_style_context().add_class(
                    "pipon" if i < s else "pipoff")
                pip.set_size_request(9, 9)
                bar.pack_start(pip, False, False, 0)
        else:
            lb = Gtk.Label(label=_t("Not met"))
            lb.get_style_context().add_class("vocabnew")
            bar.pack_start(lb, False, False, 0)
        box.pack_end(bar, False, False, 0)
        return box

    def _show_vocab(self):
        code = self.course["code"]
        col = self._page_shell(_t("%s words") % self.course.get("name", ""),
                               _t("Back"), self._back_to_course)
        seen = set(self.progress.get("seen", []))
        total = 0
        met = 0
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for ui, unit in enumerate(self.course.get("units", [])):
            for si, skill in enumerate(unit.get("skills", [])):
                words, phrases = self._skill_items(skill)
                items = [it for it in words + phrases]
                total += len(items)
                mine = [it for it in items
                        if "%s:%s" % (code, _norm(it["t"])) in seen]
                met += len(mine)
                if not mine:
                    continue
                hd = Gtk.Label(label="%s · %s" % (unit.get("title", ""),
                                                       skill.get("name", "")),
                               xalign=0)
                hd.get_style_context().add_class("sectionhead")
                hd.set_margin_top(12)
                body.pack_start(hd, False, False, 0)
                for it in mine:
                    body.pack_start(self._vocab_row(it, code), False, False, 0)
        # The bars sentence used to be unconditional, so the first thing a new
        # learner read on this page described an element that was not on it.
        sub = Gtk.Label(label=(
            _t("%d of %d met. The bars show how well each "
               "one is holding; they fade with time away.") % (met, total)
            if met else
            _t("%d of %d words and phrases met") % (met, total)), xalign=0)
        sub.get_style_context().add_class("pagesub")
        sub.set_line_wrap(True)
        col.pack_start(sub, False, False, 0)
        if not met:
            col.pack_start(self._empty_note(
                _t("Lessons add the words they teach to this list.")),
                False, False, 0)
        col.pack_start(body, False, False, 0)
        self._page_holder.show_all()
        self._pager.switch("page")

    # ---------------- awards ----------------
    def _show_awards(self):
        back = (self._back_to_course if self.course
                and self.stack.get_visible_child_name() == "course"
                else self._show_home)
        col = self._page_shell(_t("Awards"), _t("Back"), back)
        sub = Gtk.Label(label=_t("Every award is scored against a personal "
                                 "record. Nothing here is a comparison with "
                                 "anybody else."), xalign=0)
        sub.get_style_context().add_class("pagesub")
        sub.set_line_wrap(True)
        col.pack_start(sub, False, False, 0)
        for key, mark, name, what, tiers in AWARDS:
            got, n = self._award_level(key)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            row.get_style_context().add_class("awardrow")
            colour = GOLD if got else "#C9C4B6"
            img = self._icon(mark, 32, colour)
            img.set_valign(Gtk.Align.CENTER)
            row.pack_start(img, False, False, 0)
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            t = Gtk.Label(label=_t(name), xalign=0)
            t.get_style_context().add_class("awardname")
            head.pack_start(t, False, False, 0)
            lv = Gtk.Label(label=_t("Level %d") % got if got else _t("Locked"))
            lv.get_style_context().add_class(
                "awardlevel" if got else "awardlocked")
            head.pack_start(lv, False, False, 0)
            txt.pack_start(head, False, False, 0)
            nxt = tiers[got] if got < len(tiers) else tiers[-1]
            d = Gtk.Label(label=(_t("%s: %d of %d") % (_t(what), n, nxt)
                                 if got < len(tiers)
                                 else _t("%s: %d") % (_t(what), n)), xalign=0)
            d.get_style_context().add_class("awarddetail")
            txt.pack_start(d, False, False, 0)
            bar = Gtk.ProgressBar()
            bar.get_style_context().add_class("cardprog")
            bar.set_fraction(1.0 if got >= len(tiers)
                             else min(1.0, n / float(nxt or 1)))
            txt.pack_start(bar, False, False, 0)
            row.pack_start(txt, True, True, 0)
            col.pack_start(row, False, False, 0)
        self._page_holder.show_all()
        self._pager.switch("page")

    # ==================================================================
    # lesson engine
    # ==================================================================
    def _skill_items(self, skill):
        words = [dict(t=w.get("t", ""), e=w.get("e", ""), ipa=w.get("ipa", ""),
                      pos=w.get("pos", ""), note=w.get("note", ""),
                      phrase=False)
                 for w in (skill.get("words") or []) if isinstance(w, dict)]
        phr = [dict(t=p.get("t", ""), e=p.get("e", ""), ipa=p.get("ipa", ""),
                    pos="", note=p.get("note", ""), lit=p.get("lit", ""),
                    phrase=True)
               for p in (skill.get("phrases") or []) if isinstance(p, dict)]
        return ([w for w in words if w["t"] and w["e"]],
                [p for p in phr if p["t"] and p["e"]])

    def _course_words(self):
        pool = []
        for u in self.course.get("units", []):
            for s in u.get("skills", []):
                for w in (s.get("words") or []):
                    if isinstance(w, dict) and w.get("t") and w.get("e"):
                        pool.append(w)
        return pool

    def _course_tokens(self):
        """(tokens the course's SENTENCES are made of, tokens of its single
        words), cached per course.

        The two are kept apart because they are not interchangeable as wrong
        answers. A fill-in-the-blank on "Hola, me llamo Ana" whose three
        distractors are drawn from the vocabulary list offers `largo`, `libro`
        and `perro` against `me` -- the only function word on the screen is the
        answer, so the question can be passed without reading it. Sentence
        tokens carry the articles, pronouns and prepositions that make the gap
        an actual choice; the word list is the fallback for a course too thin in
        sentences."""
        code = self.course.get("code")
        if getattr(self, "_tok_code", None) == code:
            return self._tok_cache
        phrase_toks, word_toks = [], []
        for u in self.course.get("units", []):
            for s in u.get("skills", []):
                for p in (s.get("phrases") or []):
                    if isinstance(p, dict) and p.get("t"):
                        phrase_toks.extend(_toks(p["t"]))
                for w in (s.get("words") or []):
                    if isinstance(w, dict) and w.get("t"):
                        word_toks.extend(_toks(w["t"]))
        self._tok_code = code
        self._tok_cache = (phrase_toks, word_toks)
        return self._tok_cache

    @staticmethod
    def _pick_tokens(sources, n, banned):
        """`n` distinct tokens, taking each source in turn before the next."""
        out, got = [], set(banned)
        for src in sources:
            pool = list(src)
            random.shuffle(pool)
            for tok in pool:
                if len(out) >= n:
                    return out
                if _norm(tok) in got or not _norm(tok):
                    continue
                got.add(_norm(tok))
                out.append(tok)
        return out

    @staticmethod
    def _term_key(s):
        """A term's identity for the synonym maps: case and spacing are noise,
        DIACRITICS ARE NOT.

        _norm() strips accents, which is the right leniency for grading what a
        learner typed on a keyboard that may not carry them -- and exactly the
        wrong key here. Under _norm the Mandarin course's `shi` (to be) and
        `shi` (ten) are one term, so treating them as synonyms would accept
        "ten" as a reading of the verb. Matching on the spelling as written
        keeps French `fille`/`fille` together, which is the real case, and these
        two apart."""
        return " ".join((s or "").strip().lower().split())

    @staticmethod
    def _synonyms(pool):
        """(meanings of each target term, target terms for each meaning) across a
        whole course, both keyed on _term_key.

        A word does not have to mean only one thing, and the shipped courses say
        so: French `fille` is taught as "girl" in BASICS and as "daughter" in
        Family, and Mandarin `shi` is "yes" in Greetings and "to be" in Verbs.
        Both readings are correct.

        THE BUG THIS EXISTS FOR: "Translate to English" showed `fille` and
        accepted exactly ONE of its two meanings, so a learner who had done both
        skills and typed "daughter" was marked WRONG for a right answer -- which
        also cost them the crown for a perfect lesson and then asked the same
        question again, to be marked wrong a second time. Worse, `choose` drew
        its distractors from the whole course filtered only against the answer's
        OWN meaning, so it could offer "fille" with both "girl" and "daughter"
        among the options and score only one of them: a question with no right
        answer available."""
        by_target, by_english = {}, {}
        for w in pool:
            if not isinstance(w, dict):
                continue
            t, e = w.get("t"), w.get("e")
            if not isinstance(t, str) or not isinstance(e, str):
                continue
            tk, ek = Language._term_key(t), Language._term_key(e)
            if not tk or not ek:
                continue
            by_target.setdefault(tk, []).append(e)
            by_english.setdefault(ek, []).append(t)
        return by_target, by_english

    def _alts_for(self, side, prompt, answer, keep=False):
        """The OTHER accepted answers for `prompt`, looking it up as a target
        term (side "t") or as an English meaning (side "e").

        `keep=True` returns the answer itself as well, for the caller that needs
        the whole set of readings to exclude rather than the alternatives to
        accept. Never raises and returns [] when the maps are absent, so a
        lesson built before they existed still grades."""
        table = getattr(self, "_syn_t" if side == "t" else "_syn_e", None) or {}
        found = table.get(self._term_key(prompt), [])
        out, seen = [], set()
        for a in found:
            if not keep and _norm(a) == _norm(answer):
                continue
            if _norm(a) in seen:
                continue
            seen.add(_norm(a))
            out.append(a)
        return out

    # ---------------- lesson composition ----------------
    def _build_lesson(self, ui, si):
        """One skill's lesson at its current crown level."""
        skill = self.course["units"][ui]["skills"][si]
        words, phrases = self._skill_items(skill)
        items = words + phrases
        if not items:
            return None
        level = min(self._crowns(ui, si), CROWN_MAX - 1)
        plan = LEVEL_PLAN[level]
        code = self.course["code"]
        seen = set(self.progress.get("seen", []))

        def key(it):
            return self._item_skey(code, it)

        # TEACH FIRST: introduce a batch of the skill's not-yet-seen terms this
        # lesson (the rest wait for a repeat), and drill ONLY terms that are now
        # taught -- this batch plus anything already seen -- so no exercise ever
        # quizzes a word before it has been defined.
        new_items = [it for it in items if key(it) not in seen]
        seen_items = [it for it in items if key(it) in seen]
        # The plan's intro count is a PACE, not a budget. Levels three and four
        # teach nothing by design -- but a skill with more terms than the first
        # three levels introduce would then have left the last few permanently
        # untaught and permanently un-drilled, invisible on the vocabulary page
        # and unreachable by practice. Anything still new at a no-teaching level
        # gets taught anyway, four at a time.
        cap = plan["intro"] or (4 if new_items else 0)
        # Take a SENTENCE with every batch of words, not twelve words first and
        # the sentences afterwards. `items` is words-then-phrases, so slicing it
        # flat meant the first two lessons of every skill were single words
        # only: no word bank, no fill-in-the-blank, no sentence at all until the
        # third crown. A learner met "agua" and "beber" three times each before
        # ever being shown them in a sentence together.
        intro_items = self._interleave(new_items, cap)
        if not intro_items and not seen_items:
            # A level whose plan teaches nothing, met by a learner who has never
            # opened this skill. Teach anyway rather than quiz words they have
            # not been shown -- the plan is a pacing hint, not a licence.
            intro_items = new_items[:4]
        new_keys = [key(it) for it in intro_items]
        taught = intro_items + seen_items or intro_items or items
        taught_words = [it for it in taught if not it["phrase"]]
        taught_phrases = [it for it in taught if it["phrase"]]

        ex = [self._identified_exercise("intro", it, taught_words)
              for it in intro_items]
        if plan["match"] and len(taught_words) >= 4:
            ex.append(self._make_exercise("match", None, taught_words))

        drill_items = list(taught)
        random.shuffle(drill_items)
        # A lesson made only of the six words just taught is a memory test of
        # the last thirty seconds. Refill the queue from the whole taught set so
        # a long skill still drills across itself.
        while len(drill_items) < plan["drills"] and taught:
            extra = list(taught)
            random.shuffle(extra)
            drill_items.extend(extra)
        for it in drill_items[:plan["drills"]]:
            ex.append(self._identified_exercise(self._pick_kind(level, it), it,
                                                taught_words, taught_phrases))
        return self._lesson_state(ex, ui=ui, si=si, kind="lesson",
                                  new_keys=new_keys,
                                  title=skill.get("name", ""))

    @staticmethod
    def _interleave(new_items, cap):
        """`cap` terms to teach this lesson: mostly words, but always at least
        one phrase while any phrase is still untaught, and the phrase last so
        the words in it have just been defined."""
        if cap <= 0:
            return []
        words = [it for it in new_items if not it["phrase"]]
        phrases = [it for it in new_items if it["phrase"]]
        if not phrases:
            return words[:cap]
        if not words:
            return phrases[:cap]
        take_p = max(1, cap // 5)
        take_w = max(0, cap - take_p)
        out = words[:take_w] + phrases[:take_p]
        # A skill whose words ran out before the cap did takes the slack in
        # phrases rather than teaching a short lesson.
        if len(out) < cap:
            out += phrases[take_p:take_p + (cap - len(out))]
        return out[:cap]

    def _build_practice(self):
        """A practice session: the weakest words this course has taught, oldest
        first. No teaching cards -- everything in it has been met -- and no
        hearts, because a system that locks you out of the thing that fixes it
        is a system that locks you out."""
        code = self.course["code"]
        seen = set(self.progress.get("seen", []))
        pool = []
        for ui, unit in enumerate(self.course.get("units", [])):
            for si, skill in enumerate(unit.get("skills", [])):
                w, p = self._skill_items(skill)
                for it in w + p:
                    seen_key = self._item_progress_key(code, it, seen)
                    if seen_key in seen:
                        strength = self.progress.get("strength", {})
                        strength_key = self._item_progress_key(code, it, strength)
                        row = strength.get(strength_key, {})
                        pool.append((self._strength(it["t"], code, it),
                                     row.get("t", 0) if isinstance(row, dict)
                                     else 0, it))
        if not pool:
            return None
        pool.sort(key=lambda r: (r[0], r[1]))
        picks = [r[2] for r in pool[:PRACTICE_LEN]]
        words = [it for it in picks if not it["phrase"]]
        phrases = [it for it in picks if it["phrase"]]
        # The matching round needs four WORDS; a practice queue that came back
        # all phrases has none to give it.
        allw = [r[2] for r in pool if not r[2]["phrase"]] or words
        ex = []
        if len(allw) >= 4:
            ex.append(self._make_exercise("match", None, allw[:8]))
        for it in picks:
            ex.append(self._identified_exercise(self._pick_kind(2, it), it,
                                                allw, phrases))
        return self._lesson_state(ex, kind="practice", new_keys=[],
                                  title=_t("Practice"))

    def _build_test(self, ui):
        """The unit checkpoint: drawn from every skill in the unit, no teaching
        cards, no second chance on a missed question."""
        unit = self.course["units"][ui]
        words, phrases = [], []
        for skill in unit.get("skills", []):
            w, p = self._skill_items(skill)
            words.extend(w)
            phrases.extend(p)
        items = words + phrases
        if not items:
            return None
        random.shuffle(items)
        picks = items[:TEST_LEN]
        while len(picks) < TEST_LEN and items:
            picks.append(random.choice(items))
        ex = [self._identified_exercise(self._pick_kind(3, it), it, words, phrases)
              for it in picks]
        return self._lesson_state(ex, ui=ui, kind="test", new_keys=[],
                                  title=_t("%s test") % unit.get("title", ""))

    @staticmethod
    def _lesson_state(ex, ui=0, si=0, kind="lesson", new_keys=(), title=""):
        return {"ui": ui, "si": si, "ex": ex, "i": 0, "wrong": 0, "combo": 0,
                "best_combo": 0, "kind": kind, "new_keys": list(new_keys),
                "title": title, "missed": [], "start": time.time(),
                "answered": 0}

    def _pick_kind(self, level, it):
        table = PHRASE_KINDS if it["phrase"] else WORD_KINDS
        kinds = list(table[max(0, min(CROWN_MAX - 1, level))])
        if not it.get("ipa"):
            kinds = [k for k in kinds if k != "listen"] or ["translate_to_en"]
        if it["phrase"] and len(it["t"].split()) < 3:
            kinds = [k for k in kinds if k not in ("bank", "blank")] \
                or ["translate_to_en"]
        return random.choice(kinds)

    # ---------------- exercise construction ----------------
    def _identified_exercise(self, kind, item, words, phrases=None):
        exercise = self._make_exercise(kind, item, words, phrases)
        exercise["lex"] = self._item_skey(self.course["code"], item)
        return exercise

    def _distractors(self, it, n, field="e", pool=None):
        """`n` wrong options for a question about `it`, drawn from words of the
        SAME PART OF SPEECH first.

        Mixing parts of speech is what makes a multiple choice free: asked what
        a verb means, with a colour, a number and a family member on the other
        three buttons, a learner does not have to know the verb. Falling back to
        the whole course is still better than a question with two options, so
        that is what a course too thin in one class gets."""
        pool = pool if pool is not None else self._course_words()
        banned = {_norm(a) for a in
                  self._alts_for("t" if field == "e" else "e",
                                 it["t"] if field == "e" else it["e"],
                                 it["e"] if field == "e" else it["t"],
                                 keep=True)}
        banned.add(_norm(it[field]))
        same, rest = [], []
        for w in pool:
            v = w.get(field)
            if not isinstance(v, str) or _norm(v) in banned:
                continue
            (same if it.get("pos") and w.get("pos") == it["pos"]
             else rest).append(v)
        # De-duplicate on the graded form: two options the grader cannot tell
        # apart are one option that happens to be drawn twice.
        def uniq(seq):
            out, got = [], set()
            for v in seq:
                if _norm(v) in got:
                    continue
                got.add(_norm(v))
                out.append(v)
            return out
        same, rest = uniq(same), uniq(rest)
        random.shuffle(same)
        random.shuffle(rest)
        picks = same[:n]
        if len(picks) < n:
            got = {_norm(v) for v in picks}
            picks += [v for v in rest if _norm(v) not in got][:n - len(picks)]
        return picks

    def _make_exercise(self, kind, it, words, phrases=None):
        pool = self._course_words()
        if kind == "intro":
            return {"kind": "intro", "t": it["t"], "e": it["e"],
                    "ipa": it.get("ipa", ""), "note": it.get("note", ""),
                    "lit": it.get("lit", ""),
                    "phrase": it.get("phrase", False)}
        if kind == "match":
            # The buttons expose only the target/meaning text, while grading
            # uses the hidden row index.  Homographs/homophones such as
            # Mandarin shì ("yes" / "to be") therefore cannot coexist in one
            # round: two visually identical choices would have only one
            # secretly accepted pairing.  Keep both columns unambiguous and
            # refill from the remaining candidates.
            candidates = list(words)
            random.shuffle(candidates)
            picks, targets, meanings = [], set(), set()
            for word in candidates:
                target = _norm(word.get("t", ""))
                meaning = _norm(word.get("e", ""))
                if not target or not meaning:
                    continue
                if target in targets or meaning in meanings:
                    continue
                picks.append(word)
                targets.add(target)
                meanings.add(meaning)
                if len(picks) == 4:
                    break
            return {"kind": "match", "term": picks[0]["t"] if picks else "",
                    "lex": (self._item_skey(self.course["code"], picks[0])
                            if picks else ""),
                    "pairs": [(w["t"], w["e"], w.get("ipa", "")) for w in picks]}
        if kind == "translate_to_en":
            # Accept EVERY meaning this course records for the prompt, not just
            # the one this exercise was built from -- see _synonyms.
            return {"kind": "type", "term": it["t"], "prompt": it["t"],
                    "ipa": it.get("ipa", ""), "answer": it["e"],
                    "ask": _t("Translate to English"),
                    "alts": self._alts_for("t", it["t"], it["e"])}
        if kind == "translate_to_t":
            return {"kind": "type", "term": it["t"], "prompt": it["e"],
                    "ipa": "", "answer": it["t"],
                    "ask": _t("Translate to %s") % self.course.get("name", ""),
                    "alts": self._alts_for("e", it["e"], it["t"])}
        if kind == "choose":
            opts = self._distractors(it, 3, "e", pool) + [it["e"]]
            random.shuffle(opts)
            return {"kind": "choose", "term": it["t"], "prompt": it["t"],
                    "ipa": it.get("ipa", ""), "options": opts,
                    "answer": it["e"], "ask": _t("What does this mean?")}
        if kind == "select":
            opts = self._distractors(it, 3, "t", pool) + [it["t"]]
            random.shuffle(opts)
            return {"kind": "choose", "term": it["t"], "prompt": it["e"],
                    "ipa": "", "options": opts, "answer": it["t"],
                    "ask": _t("Which one is this?"),
                    "alts": self._alts_for("e", it["e"], it["t"])}
        if kind == "listen":
            # The offline stand-in for a listening exercise. There is no audio
            # on this system, so the transcription IS the sound: read it, and
            # say which word it spells.
            opts = self._distractors(it, 3, "t", pool) + [it["t"]]
            random.shuffle(opts)
            return {"kind": "listen", "term": it["t"], "prompt": it["ipa"],
                    "options": opts, "answer": it["t"],
                    "meaning": it["e"], "ask": _t("Which word is this?")}
        if kind == "blank":
            toks = _toks(it["t"])
            idx = random.randrange(len(toks))
            gap = toks[idx]
            shown = list(toks)
            shown[idx] = "____"
            phrase_toks, word_toks = self._course_tokens()
            # Sentence tokens first: they are what could plausibly stand in the
            # gap. The vocabulary list is only the fallback.
            opts = self._pick_tokens((phrase_toks, word_toks), 3,
                                     {_norm(gap)} | {_norm(t) for t in toks})
            opts.append(gap)
            random.shuffle(opts)
            return {"kind": "choose", "term": it["t"], "prompt": " ".join(shown),
                    "ipa": "", "options": opts, "answer": gap,
                    "ask": _t("Fill in the blank"), "hint": it["e"]}
        # bank: build the target phrase from word tiles. The spare tiles come
        # from other SENTENCES first for the same reason the blank's do -- a
        # bank whose only small function words are the ones the answer needs
        # gives the sentence away one tile at a time.
        toks = _toks(it["t"])
        phrase_toks, word_toks = self._course_tokens()
        spare = self._pick_tokens((phrase_toks, word_toks), max(2, len(toks)),
                                  {_norm(t) for t in toks})
        bank = toks + spare
        random.shuffle(bank)
        # `tokens` is the answer AS TILED -- the same list `bank` was built
        # from, with edge punctuation already stripped. Anything that needs to
        # know which tiles make the sentence (the selftests do) must read this
        # rather than re-split `answer`, or it looks for a tile called "Hola,"
        # that by design does not exist.
        return {"kind": "bank", "term": it["t"], "prompt": it["e"],
                "answer": it["t"], "ipa": it.get("ipa", ""), "bank": bank,
                "tokens": toks}

    # ---------------- starting one ----------------
    def _start_lesson(self, ui, si):
        if self._hearts() <= 0:
            self._no_hearts_card()
            return
        lesson = self._build_lesson(ui, si)
        if not lesson:
            self._toast(_t("This skill has no words yet"))
            return
        self._run(lesson)

    def _start_practice(self):
        lesson = self._build_practice()
        if not lesson:
            self._toast(_t("Finish a lesson first; practice reviews what it "
                           "taught"))
            return
        self._run(lesson)

    def _start_test(self, ui):
        if self._hearts() <= 0:
            self._no_hearts_card()
            return
        lesson = self._build_test(ui)
        if not lesson:
            self._toast(_t("This unit has no words yet"))
            return
        self._run(lesson)

    def _run(self, lesson):
        # A delay left over from the run just finished (Practice straight off
        # the out-of-hearts page is the easy one to hit) must not land in this
        # one and advance an exercise nobody answered.
        self._cancel_lesson_callbacks()
        self._lesson = lesson
        self._render_exercise()
        self._pager.switch("lesson")

    # ==================================================================
    # the exercise page
    # ==================================================================
    def _render_exercise(self):
        for ch in self._lesson_holder.get_children():
            self._lesson_holder.remove(ch)
        L = self._lesson
        # A fresh exercise: nothing graded yet, and no Check button until this
        # exercise builds one (a matching round has none, and the previous
        # exercise's button is a destroyed widget by now).
        self._graded = False
        self._check_btn = None
        self._choice_btns = None
        self._lesson_hearts = None
        if L["i"] >= len(L["ex"]):
            self._lesson_complete()
            return

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top.get_style_context().add_class("lessonbar")
        quit_b = Gtk.Button()
        quit_b.set_relief(Gtk.ReliefStyle.NONE)
        quit_b.get_style_context().add_class("backbtn")
        quit_b.set_tooltip_text(_t("Leave the lesson"))
        quit_b.set_image(self._icon("wclose", 14, MUTED))
        quit_b.connect("clicked", lambda *_: self._quit_lesson())
        top.pack_start(quit_b, False, False, 0)
        prog = Gtk.ProgressBar()
        prog.get_style_context().add_class("lessonprog")
        exercise_count = max(1, len(L["ex"]))
        prog.set_fraction(L["i"] / exercise_count)
        # ProgressBar already exposes its numeric value role; give that value
        # the current lesson's identity and a stable 1-based position so it is
        # meaningful without the visual bar.
        prog.get_accessible().set_name(str(L.get("title") or self.app_name))
        prog.get_accessible().set_description(
            "%d / %d" % (min(L["i"] + 1, exercise_count), exercise_count))
        prog.set_valign(Gtk.Align.CENTER)
        top.pack_start(prog, True, True, 0)
        if L["combo"] >= 3:
            cb = self._chip("bolt", _t("%d in a row") % L["combo"], GOLD,
                            "combochip")
            cb.set_valign(Gtk.Align.CENTER)
            top.pack_start(cb, False, False, 0)
        if L["kind"] != "practice":
            hr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            hr.set_valign(Gtk.Align.CENTER)
            self._lesson_hearts = hr
            self._refresh_lesson_hearts()
            top.pack_end(hr, False, False, 0)
        self._lesson_holder.pack_start(top, False, False, 0)

        ex = L["ex"][L["i"]]
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.set_margin_top(20)
        body.set_margin_start(40)
        body.set_margin_end(40)
        body.set_vexpand(True)
        # One exercise is a reading column, not a full-bleed page: unconstrained
        # it stretched every prompt, answer button and word tile across the
        # whole screen (1286px choice buttons with three centred characters in
        # them at 1366, worse at 1920). Fixing the column and centring it keeps
        # the instruction, the prompt and the answers on one axis at any size.
        body.set_size_request(EX_COLUMN, -1)
        body.set_halign(Gtk.Align.CENTER)
        self._lesson_holder.pack_start(body, True, True, 0)
        {"type": self._ex_type, "choose": self._ex_choose,
         "bank": self._ex_bank, "match": self._ex_match,
         "listen": self._ex_listen,
         "intro": self._ex_intro}[ex["kind"]](body, ex)
        # Every _ex_* ends with an expanding spacer before its footer; adding a
        # matching one at the top centres the exercise in the page instead of
        # pinning it under the progress bar above a half-screen of nothing.
        lead = Gtk.Box()
        body.pack_start(lead, True, True, 0)
        body.reorder_child(lead, 0)
        self._lesson_holder.show_all()

    def _refresh_lesson_hearts(self):
        """Repaint the hearts where they are. The row used to be built once per
        exercise, so losing a heart changed nothing on screen until the NEXT
        question -- the one moment the counter exists to be felt was the one
        moment it did not move."""
        hr = getattr(self, "_lesson_hearts", None)
        if hr is None:
            return
        for ch in hr.get_children():
            hr.remove(ch)
        hr.pack_start(self._hearts_row(15), False, False, 0)
        hr.show_all()

    def _quit_lesson(self):
        # Leaving means leaving: without this the 900ms out-of-hearts timer
        # (or a 750ms advance) fired after the quit and pushed the lesson's own
        # page back over the course path the reader had just been returned to.
        self._cancel_lesson_callbacks()
        self._lesson = None
        self._save_progress()
        self._back_to_course()

    def _ask_label(self, body, text):
        a = Gtk.Label(label=text, xalign=0)
        a.get_style_context().add_class("exask")
        body.pack_start(a, False, False, 0)

    def _prompt_block(self, body, prompt, ipa):
        p = Gtk.Label(label=prompt)
        p.get_style_context().add_class("exprompt")
        p.set_line_wrap(True)
        p.set_max_width_chars(30)
        p.set_justify(Gtk.Justification.CENTER)
        body.pack_start(p, False, False, 0)
        if ipa:
            i = Gtk.Label(label=_t("/%s/") % ipa)
            i.get_style_context().add_class("exipa")
            i.set_line_wrap(True)
            body.pack_start(i, False, False, 0)

    # ---- teaching card: a new word/phrase, defined before it is drilled ----
    def _ex_intro(self, body, ex):
        self._ask_label(body, _t("New phrase") if ex.get("phrase")
                        else _t("New word"))
        self._prompt_block(body, ex["t"], ex.get("ipa", ""))
        m = Gtk.Label(label="=  " + ex["e"])
        m.get_style_context().add_class("exmeaning")
        m.set_line_wrap(True)
        m.set_xalign(0.5)
        body.pack_start(m, False, False, 0)
        # The grammar a bare word hides: gender, the characters behind a
        # romanisation, how a compound was built. It is in the course file for
        # exactly this moment and used to be shown nowhere at all.
        for text, css in ((ex.get("note"), "exnote"), (ex.get("lit"), "exlit")):
            if not text:
                continue
            lb = Gtk.Label(label=(_t("literally: %s") % text
                                  if css == "exlit" else text))
            lb.get_style_context().add_class(css)
            lb.set_line_wrap(True)
            lb.set_xalign(0.5)
            body.pack_start(lb, False, False, 0)
        body.pack_start(Gtk.Box(), True, True, 0)
        self._continue_footer(body, _t("Continue"), self._got_intro)

    def _got_intro(self):
        """Leave a teaching card. It is not graded, so mark it answered to get
        past _advance's one-move-per-exercise guard."""
        self._graded = True
        self._advance()

    def _continue_footer(self, body, label, action):
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        foot.get_style_context().add_class("exfoot")
        foot.pack_start(Gtk.Box(), True, True, 0)
        b = Gtk.Button(label=label)
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.get_style_context().add_class("checkbtn")
        b.connect("clicked", lambda *_: action())
        foot.pack_end(b, False, False, 0)
        body.pack_start(foot, False, False, 0)
        self._lesson_later(0, b.grab_focus)

    # ---- exercise: type the translation ----
    def _ex_type(self, body, ex):
        self._ask_label(body, ex.get("ask") or _t("Translate"))
        self._prompt_block(body, ex["prompt"], ex.get("ipa", ""))
        entry = Gtk.Entry()
        entry.get_style_context().add_class("exentry")
        entry.set_placeholder_text(_t("Answer"))
        entry.connect("activate", lambda *_: self._check_type(entry, ex))
        body.pack_start(entry, False, False, 0)
        body.pack_start(Gtk.Box(), True, True, 0)
        self._check_footer(body, lambda: self._check_type(entry, ex))
        entry.connect("changed",
                      lambda e: self._arm_check(bool(e.get_text().strip())))
        self._lesson_later(0, entry.grab_focus)

    def _check_type(self, entry, ex):
        code = self.course.get("code", "")
        typed = _answer_norm(entry.get_text(), code)
        ok = typed == _answer_norm(ex["answer"], code) or \
            typed in [_answer_norm(a, code) for a in ex.get("alts", [])]
        self._grade(ok, ex)

    # ---- exercise: multiple choice (also select and fill-in-the-blank) ----
    def _ex_choose(self, body, ex):
        self._ask_label(body, ex.get("ask") or _t("Choose"))
        self._prompt_block(body, ex["prompt"], ex.get("ipa", ""))
        if ex.get("hint"):
            h = Gtk.Label(label=ex["hint"])
            h.get_style_context().add_class("exhint")
            h.set_line_wrap(True)
            body.pack_start(h, False, False, 0)
        self._choice_result = {"picked": None}
        btns = []
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for opt in ex["options"]:
            b = Gtk.Button()
            b._word = opt               # what this option MEANS, for _mark
            _set_course_text(b, opt)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("choicebtn")
            b.connect("clicked", lambda w, o=opt: self._pick_choice(w, o, btns))
            btns.append(b)
            grid.pack_start(b, False, False, 0)
        # Held so _grade can mark the answer on the buttons themselves. Being
        # told "Answer: hello" in a footer while the option you picked sits
        # there unmarked leaves you to find your own mistake in a list.
        self._choice_btns = btns
        body.pack_start(grid, False, False, 0)
        body.pack_start(Gtk.Box(), True, True, 0)
        self._check_footer(body, lambda: self._grade(
            self._choice_ok(ex), ex))

    def _choice_ok(self, ex):
        picked = _norm(self._choice_result["picked"] or "")
        return picked == _norm(ex["answer"]) or \
            picked in [_norm(a) for a in ex.get("alts", [])]

    def _pick_choice(self, w, opt, btns):
        if self._graded:            # the answer is in; don't let it be edited
            return
        self._choice_result["picked"] = opt
        for b in btns:
            b.get_style_context().remove_class("choicesel")
        w.get_style_context().add_class("choicesel")
        self._arm_check(True)

    # ---- exercise: read the transcription ----
    def _ex_listen(self, body, ex):
        self._ask_label(body, ex.get("ask") or _t("Which word is this?"))
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.get_style_context().add_class("ipacard")
        card.set_halign(Gtk.Align.CENTER)
        card.pack_start(self._icon("speech", 24, MUTED), False, False, 0)
        big = Gtk.Label(label=_t("/%s/") % ex["prompt"])
        big.get_style_context().add_class("ipabig")
        big.set_line_wrap(True)
        card.pack_start(big, False, False, 0)
        body.pack_start(card, False, False, 0)
        self._choice_result = {"picked": None}
        btns = []
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for opt in ex["options"]:
            b = Gtk.Button()
            b._word = opt               # what this option MEANS, for _mark
            _set_course_text(b, opt)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("choicebtn")
            b.connect("clicked", lambda w, o=opt: self._pick_choice(w, o, btns))
            btns.append(b)
            grid.pack_start(b, False, False, 0)
        # Held so _grade can mark the answer on the buttons themselves. Being
        # told "Answer: hello" in a footer while the option you picked sits
        # there unmarked leaves you to find your own mistake in a list.
        self._choice_btns = btns
        body.pack_start(grid, False, False, 0)
        body.pack_start(Gtk.Box(), True, True, 0)
        self._check_footer(body, lambda: self._grade(
            _norm(self._choice_result["picked"] or "") == _norm(ex["answer"]),
            ex))

    # ---- exercise: word bank ----
    def _ex_bank(self, body, ex):
        self._ask_label(body, _t("Build the sentence"))
        self._prompt_block(body, ex["prompt"], "")
        answer_box = Gtk.FlowBox()
        answer_box.set_selection_mode(Gtk.SelectionMode.NONE)
        answer_box.set_min_children_per_line(1)
        answer_box.set_max_children_per_line(12)
        answer_box.get_style_context().add_class("bankanswer")
        answer_box.set_size_request(-1, 48)
        # A homogeneous FlowBox gives every child an equal column, so a
        # two-letter word tile came out the same slab width as a six-letter one
        # (120px+ each across the page). Off, each tile is word-sized.
        answer_box.set_homogeneous(False)
        body.pack_start(answer_box, False, False, 0)
        bank_box = Gtk.FlowBox()
        bank_box.set_selection_mode(Gtk.SelectionMode.NONE)
        bank_box.set_max_children_per_line(12)
        bank_box.set_homogeneous(False)
        body.pack_start(bank_box, False, False, 0)
        # The tiles in the answer row ARE the answer. This used to keep the
        # answer twice -- the tiles, and a parallel list of words -- and the two
        # parted company the moment a repeated tile was taken back: removing a
        # word from a list drops its FIRST copy, while the screen loses the tile
        # that was tapped. A sentence with a word in it twice then read
        # correctly on screen and was graded wrong, costing a heart and the
        # skill's crown. `chosen` is kept, refreshed from the row, for the
        # Check button.
        self._bank_state = {"chosen": []}

        def built():
            """The words the answer row spells, left to right."""
            out = []
            for ch in answer_box.get_children():
                b = ch.get_child() if isinstance(ch, Gtk.FlowBoxChild) else ch
                if isinstance(b, Gtk.Button):
                    # The WORD this tile was built from, not the text the
                    # widget ended up holding: the label is translatable, and
                    # a bank word that collided with a catalog key would have
                    # been read back translated and graded wrong.
                    out.append(getattr(b, "_word", None) or b.get_label() or "")
            return out

        def add_tile(container, word, from_bank):
            b = Gtk.Button()
            b._word = word              # what this tile MEANS, for built()
            _set_course_text(b, word)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("banktile")
            b.connect("clicked", lambda w: move(w, word, from_bank))
            container.add(b)
            container.show_all()

        def move(widget, word, from_bank):
            if self._graded:        # the answer is in; don't let it be edited
                return
            parent = widget.get_parent()   # FlowBoxChild
            parent.destroy() if parent else widget.destroy()
            add_tile(answer_box if from_bank else bank_box, word, not from_bank)
            self._bank_state["chosen"] = built()
            self._arm_check(bool(self._bank_state["chosen"]))

        for word in ex["bank"]:
            add_tile(bank_box, word, True)
        body.pack_start(Gtk.Box(), True, True, 0)
        self._check_footer(body, lambda: self._grade(
            _norm(" ".join(built())) == _norm(ex["answer"]), ex))

    # ---- exercise: match pairs ----
    def _ex_match(self, body, ex):
        self._ask_label(body, _t("Match the pairs"))
        cols = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=40)
        cols.set_halign(Gtk.Align.CENTER)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        cols.pack_start(left, False, False, 0)
        cols.pack_start(right, False, False, 0)
        body.pack_start(cols, False, False, 0)
        self._match = {"sel": None, "sel_btn": None, "done": 0,
                       "total": len(ex["pairs"])}
        pairs = ex["pairs"]
        tvals = [(p[0], i, p[2]) for i, p in enumerate(pairs)]
        evals = [(p[1], i) for i, p in enumerate(pairs)]
        random.shuffle(tvals)
        random.shuffle(evals)
        for text, idx, ipa in tvals:
            b = Gtk.Button()
            b._word = text
            _set_course_text(b, text)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("matchtile")
            if ipa:
                # word in the UI font; the IPA line pinned to DejaVu Sans so the
                # whole transcription uses one phonetic typeface (the UI face
                # carries no IPA extensions, so an unpinned transcription would
                # be assembled from two type designs by the fallback).
                # The stamp has to carry the MARKUP string: _t_markup
                # translates each text run inside it, so stamping the plain
                # word left the marked-up copy of that same word unprotected.
                _markup = (GLib.markup_escape_text(text)
                           + '\n<span face="DejaVu Sans" size="small">/'
                           + GLib.markup_escape_text(ipa) + '/</span>')
                nbi18n.set_verbatim(b.get_child(), _markup)
                b.get_child().set_markup(_markup)
                b.get_child().set_justify(Gtk.Justification.CENTER)
            b.connect("clicked", lambda w, i=idx: self._match_tap(w, i, "t"))
            left.pack_start(b, False, False, 0)
        for text, idx in evals:
            b = Gtk.Button()
            b._word = text
            _set_course_text(b, text)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("matchtile")
            b.connect("clicked", lambda w, i=idx: self._match_tap(w, i, "e"))
            right.pack_start(b, False, False, 0)
        body.pack_start(Gtk.Box(), True, True, 0)
        # Matching completes itself on the last pair, so it has no Check button
        # -- but it still gets the footer rule and status line, otherwise this
        # one exercise ends in a third of a page of blank paper and its
        # "Correct" has nowhere to appear.
        self._result_footer(body)
        self._show_match_count()

    def _show_match_count(self):
        m = self._match
        self._result_lbl.set_text(_t("%d of %d matched")
                                  % (m["done"], m["total"]))

    def _match_tap(self, w, idx, side):
        m = self._match
        if self._graded or w.get_style_context().has_class("matchgone"):
            return
        if m["sel"] is None:
            m["sel"] = (idx, side)
            m["sel_btn"] = w
            w.get_style_context().add_class("matchsel")
            return
        pidx, pside = m["sel"]
        pbtn = m["sel_btn"]
        pbtn.get_style_context().remove_class("matchsel")
        m["sel"] = None
        m["sel_btn"] = None
        if idx == pidx and side != pside:
            for b in (w, pbtn):
                b.get_style_context().add_class("matchgone")
                b.set_sensitive(False)
            m["done"] += 1
            self._show_match_count()
            if m["done"] >= m["total"]:
                ex = self._lesson["ex"][self._lesson["i"]]
                self._lesson_later(250, lambda: self._grade(True, ex))
        elif w is not pbtn:
            w.get_style_context().add_class("matchbad")
            self._lesson_later(400, lambda: w.get_style_context()
                               .remove_class("matchbad"))

    # ---------------- grading + footer ----------------
    def _result_footer(self, body):
        """The rule + status line every exercise ends with. Returns the box so
        a caller can pack its own button into it."""
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        foot.get_style_context().add_class("exfoot")
        self._result_lbl = Gtk.Label(label="", xalign=0)
        self._result_lbl.get_style_context().add_class("exresult")
        self._result_lbl.set_line_wrap(True)
        foot.pack_start(self._result_lbl, True, True, 0)
        body.pack_start(foot, False, False, 0)
        return foot

    def _check_footer(self, body, checker):
        foot = self._result_footer(body)
        check = Gtk.Button(label=_t("Check"))
        check.set_relief(Gtk.ReliefStyle.NONE)
        check.get_style_context().add_class("checkbtn")
        self._check_id = check.connect("clicked", lambda *_: checker())
        # Check stays greyed until there is actually something to check. It used
        # to be live from the start, so one stray click on an untouched exercise
        # graded the blank as WRONG -- costing the crown for a perfect lesson
        # without the learner ever having answered.
        check.set_sensitive(False)
        self._check_btn = check
        foot.pack_end(check, False, False, 0)

    def _arm_check(self, on):
        if self._check_btn is not None and not self._graded:
            self._check_btn.set_sensitive(bool(on))

    def _grade(self, ok, ex):
        if getattr(self, "_closed", False):   # window is gone; nothing to score
            return
        if self._graded:            # one answer per exercise
            return
        # A matching round grades itself off a 250ms timer. If the lesson ended
        # between the last pair being joined and that timer firing -- the quit
        # button, or the last exercise completing -- there is no lesson left to
        # score, and every read below was against None.
        if self._lesson is None:
            return
        self._graded = True
        L = self._lesson
        L["answered"] += 1
        if ex.get("term"):
            self._bump_strength(ex["term"], ok, ex.get("lex"))
        self._mark_choices(ok, ex)
        if ok:
            L["combo"] += 1
            L["best_combo"] = max(L["best_combo"], L["combo"])
            self._say(True, _t("Correct"))
            self._lesson_later(750, self._advance)
            return

        L["combo"] = 0
        L["wrong"] += 1
        answer = ex.get("answer") or ""
        if ex.get("term") and ex["kind"] != "match":
            if ex["kind"] == "listen":
                # A listening exercise's prompt IS the transcription, so this
                # row read as bare IPA in the interface face -- the one place
                # in the app a transcription is shown without /slashes/, its
                # word, or what it means.
                L["missed"].append((ex["term"], ex.get("meaning") or answer,
                                    ex.get("prompt") or ""))
            else:
                L["missed"].append((ex.get("prompt") or ex["term"], answer))
        if L["kind"] != "practice":
            left = self._lose_heart()
            self._refresh_lesson_hearts()
            if left <= 0:
                self._say(False, (_t("Answer: %s") % answer) if answer
                          else _t("Incorrect"))
                self._lesson_later(900, self._out_of_hearts)
                return
        # Ask a missed item again before the lesson ends -- getting it wrong once
        # and never seeing it again is how a drill teaches nothing. One repeat
        # only (the copy is flagged), so a lesson can't grow without end. A unit
        # test does not do this: a test that hands back every question you got
        # wrong is not a test.
        if not ex.get("retry") and L["kind"] != "test":
            again = dict(ex)
            again["retry"] = True
            L["ex"].append(again)
        self._say(False, (_t("Answer: %s") % answer) if answer
                  else _t("Incorrect"))
        self._hold_for_continue()

    def _mark_choices(self, ok, ex):
        """Paint the verdict onto the option buttons: the right one green, the
        one that was picked and was not red. Never raises -- an exercise with no
        buttons (typing, word bank, matching) simply has nothing to mark."""
        btns = getattr(self, "_choice_btns", None)
        if not btns:
            return
        accepted = {_norm(ex.get("answer") or "")}
        accepted |= {_norm(a) for a in (ex.get("alts") or [])}
        picked = _norm((self._choice_result or {}).get("picked") or "")
        for b in btns:
            try:
                c = b.get_style_context()
                c.remove_class("choicesel")
                label = _norm(getattr(b, "_word", None) or b.get_label() or "")
                if label in accepted:
                    c.add_class("choiceright")
                elif not ok and label == picked:
                    c.add_class("choicewrong")
                b.set_sensitive(True)      # keep the colours legible
            except Exception:
                continue

    def _hold_for_continue(self):
        """After a wrong answer, stop and wait. The right answer used to flash
        past in three quarters of a second -- long enough to see that you were
        wrong, never long enough to read and learn what was right."""
        btn = self._check_btn
        if btn is None:
            self._lesson_later(750, self._advance)
            return
        try:
            btn.disconnect(self._check_id)
        except Exception:
            pass
        btn.set_label(_t("Continue"))
        btn.set_sensitive(True)
        btn.connect("clicked", lambda *_: self._advance())
        btn.grab_focus()

    def _say(self, ok, text):
        try:
            self._result_lbl.set_text(text)
            self._result_lbl.get_style_context().add_class(
                "resok" if ok else "resbad")
        except Exception:
            pass

    def _advance(self):
        # Reachable from a timer AND from Continue; make the second one a no-op
        # rather than a skipped exercise.
        if getattr(self, "_closed", False):   # no repaint into a dead window
            return False
        if not self._graded:
            return False
        # A correct answer schedules this 750ms out. Close the lesson inside
        # that window -- press the quit button, or answer the last question and
        # let _lesson_complete run -- and the timer still fires, into a lesson
        # that is no longer there. It crashed the app.
        if self._lesson is None:
            self._graded = False
            return False
        self._graded = False
        self._lesson["i"] += 1
        self._render_exercise()
        return False

    # ==================================================================
    # end of a lesson
    # ==================================================================
    def _end_shell(self, mark, colour, title, sub):
        for ch in self._lesson_holder.get_children():
            self._lesson_holder.remove(ch)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        img = self._icon(mark, 52, colour)
        img.set_halign(Gtk.Align.CENTER)
        box.pack_start(img, False, False, 0)
        t = Gtk.Label(label=title)
        t.get_style_context().add_class("donetitle")
        box.pack_start(t, False, False, 0)
        s = Gtk.Label(label=sub)
        s.get_style_context().add_class("donesub")
        s.set_line_wrap(True)
        s.set_max_width_chars(50)
        s.set_justify(Gtk.Justification.CENTER)
        box.pack_start(s, False, False, 0)
        self._lesson_holder.pack_start(box, True, True, 0)
        return box

    def _out_of_hearts(self):
        """The lesson stops here. What is on the screen has to be the way BACK
        to learning, not a dead end: practice costs no hearts and refills them,
        and the switch that turns the whole mechanic off is right here rather
        than buried in a menu."""
        self._cancel_lesson_callbacks()
        self._lesson = None
        self._save_progress()
        box = self._end_shell(
            "heart", RED, _t("Out of hearts"),
            _t("One heart comes back every %d minutes. Practice refills them "
               "all and costs none.") % (HEART_REFILL_S // 60))
        box.pack_start(self._chip("clock",
                                  _t("Next heart in %d min") % self._heart_wait(),
                                  MUTED), False, False, 0)
        box.get_children()[-1].set_halign(Gtk.Align.CENTER)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_halign(Gtk.Align.CENTER)
        row.set_margin_top(8)
        row.pack_start(self._flat_button(_t("Turn Hearts Off"), "cardsecond",
                                         self._toggle_hearts), False, False, 0)
        row.pack_start(self._flat_button(_t("Back to the path"), "cardsecond",
                                         self._back_to_course),
                       False, False, 0)
        row.pack_start(self._flat_button(_t("Practice"), "checkbtn",
                                         self._start_practice),
                       False, False, 0)
        box.pack_start(row, False, False, 0)
        self._lesson_holder.show_all()

    def _lesson_complete(self):
        self._cancel_lesson_callbacks()
        L = self._lesson
        progress_before = copy.deepcopy(self.progress)
        code = self.course["code"]
        # remember the words we just introduced so a later lesson doesn't
        # re-teach them
        if L.get("new_keys"):
            seen = self.progress.setdefault("seen", [])
            for k in L["new_keys"]:
                if k not in seen:
                    seen.append(k)
        # A word met in a lesson counts as met even at a level with no teaching
        # cards -- a checkpoint that quizzes you on a word is a word you have
        # been shown.
        for ex in L["ex"]:
            if ex.get("term"):
                k = "%s:%s" % (code, _norm(ex["term"]))
                seen = self.progress.setdefault("seen", [])
                if k not in seen:
                    seen.append(k)

        graded = sum(1 for e in L["ex"] if e["kind"] != "intro")
        correct = max(0, graded - L["wrong"])
        perfect = L["wrong"] == 0 and graded > 0
        secs = int(max(0, time.time() - L["start"]))

        crowned = False
        passed = True
        if L["kind"] == "lesson":
            if perfect:
                crowned = self._add_crown(L["ui"], L["si"])
            xp = 10 + (5 if perfect else 0) + min(5, L["best_combo"] // 3)
        elif L["kind"] == "practice":
            self._fill_hearts()
            xp = 8 + (4 if perfect else 0)
        else:                                   # a unit test
            passed = L["wrong"] <= TEST_ALLOWED
            xp = 20 if passed else 5
            if passed:
                self.progress.setdefault("tests", {})["%s:%d"
                                                      % (code, L["ui"])] = True
                # Testing out is the point of a checkpoint: a learner who
                # already knows the unit gets its first crown on every skill
                # rather than sitting four lessons to prove it again.
                for si in range(len(self.course["units"][L["ui"]]
                                    .get("skills", []))):
                    if self._crowns(L["ui"], si) == 0:
                        self._add_crown(L["ui"], si)

        st = self.progress.setdefault("stats", {})
        st["lessons"] = st.get("lessons", 0) + 1
        if perfect:
            st["perfect"] = st.get("perfect", 0) + 1
        before_awards = {k: self._award_level(k)[0] for k, *_r in AWARDS}
        hit_goal = self._award_xp(xp)
        new_awards = [n for k, _m, n, _w, _t2 in AWARDS
                      if self._award_level(k)[0] > before_awards[k]]
        if not self._save_progress():
            # Completion is a durable boundary.  Keep the finished lesson
            # available for retry and restore every crown/test/XP mutation;
            # never replace it with a misleading success screen.
            self.progress = progress_before
            self._toast(_t("Not saved"))
            return
        self._lesson = None

        if L["kind"] == "test" and not passed:
            mark, colour = "trophy", MUTED
            title = _t("Test not passed")
            sub = _t("%d of %d correct. %d mistakes are allowed.") \
                % (correct, graded, TEST_ALLOWED)
        elif L["kind"] == "test":
            mark, colour = "trophy", GOLD
            title = _t("Test passed")
            sub = _t("%d of %d correct") % (correct, graded)
        elif L["kind"] == "practice":
            mark, colour = "workout", GREEN
            title = _t("Practice done")
            sub = _t("%d of %d correct") % (correct, graded)
        else:
            mark, colour = ("crown", GOLD) if crowned else ("star", GOLD)
            now = self._crowns(L["ui"], L["si"])
            if crowned and now >= CROWN_MAX:
                title = _t("Skill finished")
            elif crowned:
                title = _t("Level %d") % now
            else:
                title = _t("Lesson complete")
            sub = _t("%d of %d correct") % (correct, graded)
        box = self._end_shell(mark, colour, title, sub)

        stats = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        stats.set_halign(Gtk.Align.CENTER)
        stats.set_margin_top(6)
        stats.pack_start(self._chip("star", ltr(_t("+%d XP") % xp), GOLD),
                         False, False, 0)
        stats.pack_start(self._chip("target", "%d%%" % (
            int(round(100.0 * correct / graded)) if graded else 100), GREEN),
            False, False, 0)
        stats.pack_start(self._chip("clock", _t("%d:%02d")
                                    % (secs // 60, secs % 60), MUTED),
                         False, False, 0)
        if L["best_combo"] >= 3:
            stats.pack_start(self._chip("bolt", _t("%d in a row")
                                        % L["best_combo"], GOLD),
                             False, False, 0)
        box.pack_start(stats, False, False, 0)

        # WHY there is no crown. A lesson finished at 9 of 10 showed "Lesson
        # complete" and a score, and nothing anywhere said that the next crown
        # needs a lesson with no mistakes at all — so a learner could sit the
        # same skill again and again, doing well every time, and never find out
        # why the crown would not come (ROADMAP #39). The rule is real; it was
        # simply never stated. Only shown when it can still be acted on: not on
        # a perfect run, not on a skill that already has every crown, and not
        # for practice or a unit test, which are not crowned at all.
        if (L["kind"] == "lesson" and not crowned
                and self._crowns(L["ui"], L["si"]) < CROWN_MAX):
            box.pack_start(self._note_line(
                _t("A lesson with no mistakes earns the next crown")),
                False, False, 0)
        if L["kind"] == "practice":
            box.pack_start(self._note_line(_t("Hearts refilled")), False,
                           False, 0)
        if hit_goal:
            box.pack_start(self._note_line(
                _t("Daily goal met · %d day streak") % self._streak()),
                False, False, 0)
        for name in new_awards:
            box.pack_start(self._note_line(_t("New award: %s") % _t(name)),
                           False, False, 0)

        if L["missed"]:
            hd = Gtk.Label(label=_t("Worth another look"))
            hd.get_style_context().add_class("sectionhead")
            hd.set_margin_top(12)
            box.pack_start(hd, False, False, 0)
            grid = Gtk.Grid()
            grid.set_column_spacing(18)
            grid.set_row_spacing(3)
            grid.set_halign(Gtk.Align.CENTER)
            shown, done = 0, set()
            for row in L["missed"]:
                prompt, answer = row[0], row[1]
                ipa = row[2] if len(row) > 2 else ""
                if (prompt, answer) in done or shown >= 6:
                    continue
                done.add((prompt, answer))
                a = Gtk.Label(label=prompt, xalign=1)
                a.get_style_context().add_class("misspro")
                if ipa:
                    # the transcription pinned to DejaVu Sans and shown in
                    # slashes, as it is on every other surface (_ex_match,
                    # _prompt_block): the UI face carries no IPA extensions.
                    a.set_markup(
                        GLib.markup_escape_text(prompt)
                        + '  <span face="DejaVu Sans" size="small">/'
                        + GLib.markup_escape_text(ipa) + '/</span>')
                b = Gtk.Label(label=answer, xalign=0)
                b.get_style_context().add_class("missans")
                grid.attach(a, 0, shown, 1, 1)
                grid.attach(b, 1, shown, 1, 1)
                shown += 1
            box.pack_start(grid, False, False, 0)

        cont = Gtk.Button(label=_t("Continue"))
        cont.set_relief(Gtk.ReliefStyle.NONE)
        cont.get_style_context().add_class("checkbtn")
        cont.set_halign(Gtk.Align.CENTER)
        cont.set_margin_top(14)
        cont.connect("clicked", lambda *_: self._back_to_course())
        box.pack_start(cont, False, False, 0)
        self._lesson_holder.show_all()
        cont.grab_focus()

    def _note_line(self, text):
        lb = Gtk.Label(label=text)
        lb.get_style_context().add_class("doneflag")
        lb.set_line_wrap(True)
        lb.set_justify(Gtk.Justification.CENTER)
        return lb

    # ==================================================================
    # keys
    # ==================================================================
    def _on_key(self, w, ev):
        """Esc LEAVES one level, it does not close the app from four screens
        deep.

        The OS rule is that Esc only ever leaves. In an app that is one window
        with six pages in it, the base handler's "Esc quits" reads as the app
        crashing: a learner half-way through a lesson pressed it to get out of
        the lesson and the whole thing vanished, taking the lesson with it. So
        each page leaves to the one that opened it, and only the picker -- the
        page with nothing behind it -- closes."""
        if ev.keyval != Gdk.KEY_Escape:
            return super()._on_key(w, ev)
        # An open menu or the About card wins, exactly as the base class has it.
        if self._close_about():
            return True
        if self._menu_open is not None:
            self._close_menu()
            return True
        layer = getattr(self, "_card_layer", None)
        if layer is not None and layer.get_visible():
            self._hide_card()
            return True
        try:
            where = self.stack.get_visible_child_name()
        except Exception:
            where = None
        if where == "lesson":
            # Mid-lesson this discards the run, the same as the close button in
            # the lesson bar; on an end screen there is nothing left to discard.
            self._quit_lesson()
            return True
        if where == "page":
            back = getattr(self, "_page_back", None)
            (back or self._show_home)()
            return True
        if where == "course":
            self._show_home()
            return True
        return super()._on_key(w, ev)

    # ==================================================================
    # menu
    # ==================================================================
    def menu_items(self, name):
        if name == "File":
            # Courses takes you back to the picker, so it greys out when the
            # picker is already what you are looking at rather than looking
            # live and doing nothing.
            # Ask the Stack, not self.course: _show_home leaves the last opened
            # course set, so `not self.course` would say "not home" forever
            # after the first lesson. A Stack that has not been shown yet
            # reports None, and the app always opens on the picker -- so None
            # means home too.
            try:
                where = self.stack.get_visible_child_name()
            except Exception:
                where = None
            on_home = where in (None, "home")
            # Every item that NAVIGATES is dead while a lesson is being sat.
            # They all replace the page, and the lesson would go with it: a
            # half-finished run, its crown and its XP thrown away by a menu
            # click that gave no sign it was about to. The way out of a lesson
            # is the lesson's own close button, or Esc, which both say so.
            live = self._lesson is not None
            in_course = self.course is not None and not on_home and not live
            hearts_on = self.progress.get("hearts_on", True)
            return [
                ("Courses", None if on_home or live else self._show_home),
                nbapp.SEP,
                ("Practice", self._start_practice if in_course else None),
                ("Vocabulary", self._show_vocab if in_course else None),
                ("Awards", None if live else self._show_awards),
                nbapp.SEP,
                ("Daily Goal…", self._pick_goal),
                ("Turn Hearts Off" if hearts_on else "Turn Hearts On",
                 self._toggle_hearts),
                nbapp.SEP,
                ("Reset Progress…", self._reset_progress),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        return super().menu_items(name)

    def _toggle_hearts(self):
        before = copy.deepcopy(self.progress)
        on = not self.progress.get("hearts_on", True)
        self.progress["hearts_on"] = on
        if on:
            self._fill_hearts()
        if not self._save_progress():
            # Turning hearts on refills the count and resets its timer too, so
            # restore the complete profile rather than only the switch.
            self.progress = before
        self._refresh_after_setting()

    def _refresh_after_setting(self):
        where = self.stack.get_visible_child_name()
        if where == "course" and self.course:
            self._render_course(keep_scroll=True)
        elif where in (None, "home"):
            self._show_home()
        elif where == "lesson" and self._lesson is None and self.course:
            # An END screen, not a live lesson: the out-of-hearts page carries
            # the hearts switch, and flipping it there used to leave the reader
            # sitting on a page that said they were out of hearts when they no
            # longer were, with no button that had changed. Take them back to
            # the path, which is now open to them again.
            self._back_to_course()

    # ---------------- dialogs ----------------
    def _dialog(self, title, message, ok_label, destructive=False, rows=None):
        """The one confirm/choose card this app uses. Returns True when the
        primary button was pressed."""
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.get_style_context().add_class("confirmcard")
        t = Gtk.Label(label=title, xalign=0)
        t.get_style_context().add_class("confirmtitle")
        box.pack_start(t, False, False, 0)
        if message:
            m = Gtk.Label(xalign=0, label=message)
            m.set_line_wrap(True)
            m.set_max_width_chars(44)
            m.get_style_context().add_class("confirmmsg")
            box.pack_start(m, False, False, 0)
        if rows:
            for w in rows:
                box.pack_start(w, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_halign(Gtk.Align.END)
        keep = Gtk.Button(label=_t("Cancel"))
        keep.set_relief(Gtk.ReliefStyle.NONE)
        keep.get_style_context().add_class("confirmkeep")
        keep.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.CANCEL))
        go = Gtk.Button(label=ok_label)
        go.set_relief(Gtk.ReliefStyle.NONE)
        go.get_style_context().add_class("confirmwipe" if destructive
                                         else "checkbtn")
        go.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.OK))
        row.pack_start(keep, False, False, 0)
        row.pack_start(go, False, False, 0)
        box.pack_start(row, False, False, 0)
        dlg.get_content_area().add(box)
        try:
            area = dlg.get_action_area()
            area.set_no_show_all(True)
            area.hide()
        except Exception:
            pass
        dlg.connect("key-press-event",
                    lambda _w, e: (dlg.response(Gtk.ResponseType.CANCEL) or True)
                    if e.keyval == Gdk.KEY_Escape else False)
        dlg.show_all()
        keep.grab_focus()          # the safe button takes a stray Return
        out = dlg.run() == Gtk.ResponseType.OK
        dlg.destroy()
        return out

    def _pick_goal(self):
        """How much XP a day counts as a day. The streak is scored on this, so
        it is a real commitment and not a preference -- hence the ellipsis and
        the card."""
        picked = {"v": self._goal()}
        rows = []
        group = None
        for g in GOALS:
            r = Gtk.RadioButton.new_with_label_from_widget(group, "")
            group = group or r
            lab = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            n = Gtk.Label(label=_t("%d XP") % g, xalign=0)
            n.get_style_context().add_class("goalxp")
            n.set_size_request(74, -1)
            lab.pack_start(n, False, False, 0)
            d = Gtk.Label(label=GOAL_NOTE[g](), xalign=0)
            d.get_style_context().add_class("goalnote")
            lab.pack_start(d, True, True, 0)
            child = r.get_child()
            if child is not None:
                r.remove(child)
            r.add(lab)
            r.set_active(g == self._goal())
            r.connect("toggled", lambda w, v=g: picked.__setitem__("v", v)
                      if w.get_active() else None)
            rows.append(r)
        if self._dialog(_t("Daily Goal"),
                        _t("The streak counts a day once this much XP is "
                           "banked."), _t("Set"), rows=rows):
            self._set_goal(picked["v"])

    def _set_goal(self, goal):
        """Persist the daily goal, restoring the durable goal on failure."""
        previous = self.progress.get("goal")
        self.progress["goal"] = goal
        if not self._save_progress():
            if previous is None:
                self.progress.pop("goal", None)
            else:
                self.progress["goal"] = previous
            self._refresh_after_setting()
            return False
        self._refresh_after_setting()
        return True

    def _reset_progress(self):
        """Wiping every crown, the XP and the streak has no undo, so ask first.
        This used to fire straight off the menu -- one slip and weeks of work
        were gone without a word."""
        if not self._dialog(
                _t("Reset Progress?"),
                _t("Clears all crowns, XP, streaks and word strength in every "
                   "course. This cannot be undone."),
                _t("Reset"), destructive=True):
            return
        keep_goal = self._goal()
        keep_hearts = self.progress.get("hearts_on", True)
        before = copy.deepcopy(self.progress)
        self.progress = self.norm_progress({"goal": keep_goal,
                                            "hearts_on": keep_hearts})
        if not self._save_progress():
            self.progress = before
            self._refresh_home_stats()
            self._refresh_after_setting()
            return
        self._refresh_home_stats()
        self._refresh_after_setting()

    # ==================================================================
    # css
    # ==================================================================
    def _install_css(self):
        # ASCII ONLY in here. This is a bytes literal handed straight to
        # Gtk.CssProvider.load_from_data, and a stray non-ASCII character
        # (a real ellipsis, a middle dot) raises at load time and silently
        # drops the WHOLE stylesheet -- the app then renders in bare Adwaita.
        css = b"""
        * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .homepage, stack { background: #FCFBF8; }
        .hometitle { font-size: 24px; font-weight: 700; color: #1A1916; }
        .homesub { font-size: 13px; color: #6E695E; }
        .statchip { background: #F1EEE6; border: 1px solid #D7D2C5;
                    border-radius: 100px; padding: 4px 11px; }
        .statchiptext { font-size: 12px; color: #3A362E; }
        /* The gold pair is the streak/reward family's own tint (the bolt, the
           flame and the crown are all drawn in #B8912E), and it is the only
           thing that tells a combo chip from a stat chip beside it. Kept off
           the neutral palette deliberately. */
        .combochip { background: #F7EFD8; border: 1px solid #E7D9AE;
                     border-radius: 100px; padding: 3px 10px; }
        .coursecard { background: #F1EEE6; border: 1px solid #C9C4B6;
                      border-radius: 8px; padding: 14px; }
        .coursecard:hover { background: #F8F7F2; border-color: #C8341E; }
        .pathhit { padding: 0; border: none; background: transparent;
                   background-image: none; box-shadow: none; }
        .coursehit:hover .coursecard {
            background: #F8F7F2; border-color: #C8341E; }
        .codebadge { font-size: 20px; font-weight: 700; color: #FCFBF8;
                     background: #4F7A3A; border-radius: 50%;
                     min-width: 54px; min-height: 54px; padding: 0; }
        .coursename { font-size: 17px; font-weight: 600; color: #1A1916; }
        .coursefrom { font-size: 11px; color: #6E695E; }
        .coursemeta { font-size: 11px; color: #6E695E; margin-top: 4px; }
        .cardprog trough { min-height: 6px; background: #DED4C2;
                           border-radius: 100px; border: none; }
        .cardprog progress { min-height: 6px; background: #4F7A3A;
                             border-radius: 100px; border: none; }
        .linkbtn { font-size: 13px; padding: 6px 12px; background: transparent;
                   border: none; box-shadow: none; }
        .linkbtn, .linkbtn label { color: #6E695E; }
        .linkbtn:hover { background: #F1EEE6; }
        /* Every rule below that repeats a colour on `... label` is doing real
           work: a colour set on a BUTTON node never reaches the label inside
           it, because the theme's universal `* { color: ink }` matches that
           label node directly and beats the inherited value. Without them the
           Check / Continue buttons drew ink text on the green fill -- the
           primary action of every single exercise, barely readable. */
        .backbtn { font-size: 14px; box-shadow: none; border: none;
                   background: transparent; padding: 4px 8px; }
        .backbtn, .backbtn label { color: #6E695E; }
        .backbtn:hover { background: #EAE3D2; }
        .toolbtn { font-size: 12px; box-shadow: none;
                   border: 1px solid #D7D2C5; border-radius: 4px;
                   background: #FCFBF8; padding: 4px 10px; }
        .toolbtn, .toolbtn label { color: #3A362E; }
        .toolbtn:hover { background: #F1EEE6; }
        .unitbtn { font-size: 12px; box-shadow: none; border: none;
                   border-radius: 4px; background: rgba(252,251,248,0.72);
                   padding: 5px 12px; }
        .unitbtn, .unitbtn label { color: #3A362E; }
        .unitbtn:hover { background: #FCFBF8; }
        .confirmcard { background: #FCFBF8; border: 1px solid #C9C4B6;
                       padding: 22px 26px 18px; }
        .confirmtitle { font-size: 20px; font-weight: 700; color: #1A1916; }
        .confirmmsg { font-size: 13px; color: #6E695E; }
        .confirmkeep { font-size: 13px; padding: 7px 16px; background: #FCFBF8;
                       border: 1px solid #C9C4B6; border-radius: 8px;
                       box-shadow: none; }
        .confirmkeep, .confirmkeep label { color: #1A1916; }
        .confirmkeep:hover { background: #F1EEE6; }
        .confirmwipe { font-size: 13px; font-weight: 600; padding: 7px 16px;
                       background: #C8341E; border: none; border-radius: 8px;
                       box-shadow: none; }
        .confirmwipe, .confirmwipe label { color: #FCFBF8; }
        .confirmwipe:hover { background: #B12D19; }
        .goalxp { font-size: 14px; font-weight: 600; color: #1A1916; }
        .goalnote { font-size: 12px; color: #6E695E; }
        .coursebar { background: #F1EEE6; border-bottom: 1px solid #C9C4B6;
                     padding: 9px 14px; }
        .coursetitle { font-size: 16px; font-weight: 600; color: #1A1916; }
        .coursenote { font-size: 12px; color: #6E695E; margin-left: 4px; }
        .heartwait { font-size: 11px; color: #6E695E; margin-left: 4px; }
        /* --- the path --- */
        .unitbanner { border-radius: 12px; padding: 11px 14px; }
        .unittitle { font-size: 13px; letter-spacing: 0.09em;
                     font-weight: 700; color: #1A1916; }
        .unitsub { font-size: 12px; color: #3A362E; }
        .skillnode { background: #FCFBF8; border: 3px solid #C9C4B6;
                     border-radius: 50%; }
        .skilllocked { background: #F1EEE6; border-color: #D7D2C5; }
        .testnode { background: #FCFBF8; border: 3px solid #C9C4B6;
                    border-radius: 12px; }
        .skillname { font-size: 12px; font-weight: 600; color: #3A362E; }
        .skillnamelocked { color: #6E695E; font-weight: 400; }
        .skilllevel { font-size: 10px; color: #6E695E; }
        .toastshown { font-size: 13px; color: #FCFBF8; background: #3A362E;
                      border-radius: 100px; padding: 8px 18px; }
        /* --- the card over the path --- */
        .scrim { background: rgba(26,25,22,0.34); }
        .skillcard { background: #FCFBF8; border: 1px solid #C9C4B6;
                     border-radius: 12px; padding: 22px 26px; }
        .cardtitle { font-size: 20px; font-weight: 700; color: #1A1916; }
        .cardsub { font-size: 13px; color: #6E695E; }
        .cardmeta { font-size: 12px; color: #6E695E; }
        .cardsecond { font-size: 14px; padding: 9px 18px; border-radius: 6px;
                      background: #FCFBF8; border: 1px solid #C9C4B6;
                      box-shadow: none; }
        .cardsecond, .cardsecond label { color: #3A362E; }
        .cardsecond:hover { background: #F1EEE6; }
        /* --- pages --- */
        .pagesub { font-size: 13px; color: #6E695E; }
        .sectionhead { font-size: 12px; letter-spacing: 0.07em;
                       font-weight: 700; color: #6E695E; }
        .emptynote { font-size: 13px; color: #6E695E; }
        .tipcard { background: #F4F2EC; border-radius: 6px;
                   padding: 14px 16px; }
        .tiph { font-size: 15px; font-weight: 700; color: #1A1916; }
        .tipb { font-size: 13px; color: #3A362E; }
        .tipgrid { margin-top: 2px; }
        .tipega { font-size: 13px; font-weight: 600; color: #1A1916; }
        .tipegb { font-size: 13px; color: #6E695E; }
        .alpharow { padding: 7px 4px; border-bottom: 1px solid #D7D2C5; }
        .alphac { font-size: 20px; font-weight: 600; color: #1A1916; }
        .alphae { font-size: 13px; color: #6E695E; }
        .vocabrow { padding: 6px 4px; border-bottom: 1px solid #D7D2C5; }
        .vocabt { font-size: 14px; font-weight: 600; color: #1A1916; }
        .vocabe { font-size: 13px; color: #3A362E; }
        .vocabnote { font-size: 11px; color: #6E695E; }
        .vocabnew { font-size: 11px; color: #6E695E; }
        .pipon { background: #4F7A3A; border-radius: 4px; }
        .pipoff { background: #DED4C2; border-radius: 4px; }
        .awardrow { padding: 10px 4px; border-bottom: 1px solid #D7D2C5; }
        .awardname { font-size: 15px; font-weight: 600; color: #1A1916; }
        .awardlevel { font-size: 11px; color: #4F7A3A; font-weight: 600; }
        .awardlocked { font-size: 11px; color: #6E695E; }
        .awarddetail { font-size: 12px; color: #6E695E; }
        /* --- lesson --- */
        .lessonbar { padding: 12px 16px; }
        .lessonprog trough { min-height: 12px; background: #DED4C2;
                             border-radius: 6px; border: none; }
        .lessonprog progress { min-height: 12px; background: #4F7A3A;
                               border-radius: 6px; border: none; }
        .exask { font-size: 13px; letter-spacing: 0.06em; color: #6E695E;
                 font-weight: 700; }
        .exprompt { font-size: 24px; font-weight: 600; color: #1A1916; }
        .exhint { font-size: 13px; color: #6E695E; font-style: italic; }
        /* Render IPA in DejaVu Sans for one consistent phonetic typeface. The
           shipped Nimbus Sans stops at Latin/Greek/Cyrillic and carries none of
           the IPA extensions - not even the script g, the stress mark or the
           length mark, let alone the retroflex and alveolo-palatal letters that
           Mandarin and Serbo-Croatian need - so an unpinned transcription would
           be assembled glyph-by-glyph out of two type designs. DejaVu has full
           IPA coverage; pin the whole line to it. */
        .exipa { font-family: "DejaVu Sans", sans-serif; font-size: 16px;
                 color: #6E695E; font-style: italic; }
        .ipacard { background: #F1EEE6; border: 1px solid #D7D2C5;
                   border-radius: 8px; padding: 16px 22px; }
        .ipabig { font-family: "DejaVu Sans", sans-serif; font-size: 24px;
                  color: #1A1916; }
        .exmeaning { font-size: 20px; color: #2F6B4F; font-weight: 600;
                     margin-top: 4px; }
        /* The teaching card's note carries the Mandarin course's CHARACTERS as
           well as gender marks and derivations, and Han glyphs need the extra
           couple of pixels to stay readable. */
        .exnote { font-size: 15px; color: #6E695E; }
        .exlit { font-size: 13px; color: #6E695E; font-style: italic; }
        .exentry { font-size: 17px; padding: 10px 12px; border-radius: 4px;
                   border: 1px solid #C9C4B6; background: #FCFBF8; }
        .choicebtn { font-size: 16px; padding: 12px 16px; border-radius: 6px;
                     border: 1px solid #C9C4B6; background: #FCFBF8;
                     color: #1A1916; box-shadow: none; }
        .choicebtn:hover { background: #F4F2EC; }
        /* The three answer states are the one place in the app where a wash
           has to be read as MEANING -- picked, right, wrong -- so each stays
           the pale tint of the edge above it rather than conforming to the
           neutral palette, which cannot say "correct". */
        .choicesel { border: 2px solid #3E6B8C; background: #EAF0F4; }
        .choiceright { border: 2px solid #4F7A3A; background: #E9F0E2; }
        .choicewrong { border: 2px solid #C8341E; background: #FBEFEC; }
        .banktile, .matchtile { font-size: 15px; padding: 8px 12px;
                     border-radius: 6px; border: 1px solid #C9C4B6;
                     background: #FCFBF8; color: #1A1916; box-shadow: none; }
        .banktile:hover, .matchtile:hover { background: #F4F2EC; }
        .bankanswer { border-bottom: 2px solid #D7D2C5; min-height: 44px;
                      margin-bottom: 16px; }
        .matchsel { border: 2px solid #3E6B8C; background: #EAF0F4; }
        .matchgone { opacity: 0.25; }
        .matchbad { border: 2px solid #C8341E; }
        .exfoot { border-top: 1px solid #D7D2C5; padding: 14px 0 4px; }
        .checkbtn { font-size: 15px; font-weight: 600; padding: 10px 28px;
                    border-radius: 6px; background: #4F7A3A;
                    box-shadow: none; border: none; }
        .checkbtn, .checkbtn label { color: #FCFBF8; }
        .checkbtn:hover { background: #446B32; }
        /* not-yet-answerable Check: clearly waiting, not clearly broken */
        .checkbtn:disabled { background: #DED4C2; }
        .checkbtn:disabled, .checkbtn:disabled label { color: #9A9484; }
        .exresult { font-size: 14px; color: #6E695E; }
        .resok { color: #4F7A3A; font-weight: 600; }
        .resbad { color: #C8341E; font-weight: 600; }
        .donetitle { font-size: 24px; font-weight: 700; color: #1A1916; }
        .donesub { font-size: 15px; color: #6E695E; }
        .doneflag { font-size: 13px; color: #4F7A3A; font-weight: 600; }
        .misspro { font-size: 14px; font-weight: 600; color: #1A1916; }
        .missans { font-size: 14px; color: #6E695E; }
        """
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            pass


# How a lesson is built at each crown level. The first pass teaches and asks
# you to recognise; the last asks you to produce, from English, with nothing on
# screen to pick from. This IS the difficulty curve -- there is no second set of
# lessons for level five, the same twelve words simply stop being multiple
# choice.
LEVEL_PLAN = {
    0: {"intro": 6, "match": 1, "drills": 10},
    1: {"intro": 5, "match": 1, "drills": 11},
    2: {"intro": 5, "match": 1, "drills": 12},
    3: {"intro": 0, "match": 1, "drills": 13},
    4: {"intro": 0, "match": 0, "drills": 14},
}
WORD_KINDS = {
    0: ["choose", "choose", "select", "listen"],
    1: ["choose", "select", "listen", "translate_to_en"],
    2: ["choose", "select", "listen", "translate_to_en", "translate_to_t"],
    3: ["select", "listen", "translate_to_en", "translate_to_t",
        "translate_to_t"],
    4: ["translate_to_en", "translate_to_t", "translate_to_t", "listen"],
}
PHRASE_KINDS = {
    0: ["bank"],
    1: ["bank", "bank", "blank"],
    2: ["bank", "blank", "translate_to_en"],
    3: ["bank", "blank", "translate_to_en", "translate_to_t"],
    4: ["bank", "translate_to_t", "translate_to_en"],
}
PRACTICE_LEN = 12
TEST_LEN = 16
TEST_ALLOWED = 2
# Built lazily so the strings are translated at the moment the card is opened,
# not at import, when nbi18n may not have a language loaded yet.
GOAL_NOTE = {
    10: lambda: _t("about one lesson"),
    20: lambda: _t("about two lessons"),
    30: lambda: _t("about three lessons"),
    50: lambda: _t("about five lessons"),
}


if __name__ == "__main__":
    nbapp.run(Language)
