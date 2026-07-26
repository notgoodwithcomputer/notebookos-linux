#!/usr/bin/env python3
"""
Language — an offline language course in the Duolingo lineage. Pick a course,
work down a tree of skills, and each lesson drills a handful of generated
exercises: translate (either direction), multiple choice, tap-the-pairs, and
word-bank sentence building. Since there is no audio on this offline system,
every target word carries its IPA pronunciation instead of a recording.

Courses are read from de/course_<code>.json (compact vocab + phrase lists, each
with IPA); the exercises are generated from them, so a course stays small on
disk yet plays like a full lesson. Progress — crowns per skill, XP and a daily
streak — persists to $NB_HOME/.config/notebook/language.json.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, Pango, GLib  # noqa: E402

import os
import json
import time
import random
import unicodedata

import nbapp
import nbicons
from nbi18n import _t  # noqa: E402

DE_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
CFG_FILE = os.path.join(CFG_DIR, "language.json")

INK = "#1A1916"
MUTED = "#6E695E"
FAINT = "#9A9484"
GREEN = "#4F7A3A"
RED = "#C8341E"

LESSON_LEN = 10          # graded drills per lesson
INTRO_PER_LESSON = 6     # new words taught per lesson (the rest wait for a repeat)
CROWN_MAX = 5
# Width of one exercise's reading column. Everything in a lesson — the
# instruction, the prompt, the answer buttons, the word bank and the footer —
# lines up inside it, centred on the page, so a wide panel reads the same as a
# 1024 one instead of stretching a four-letter answer across the screen.
EX_COLUMN = 560


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
                courses.append(c)
        except Exception:
            continue
    return courses


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
        self._graded = False      # this exercise has been answered
        self._check_btn = None    # the current exercise's Check / Continue
        self._check_id = 0
        self._load_progress()

        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        # Instant switch, NOT crossfade: a frame-clock-driven Stack transition
        # STALLS on this OS's no-compositor swrast fallback (the incoming page
        # never finishes fading in), so home->course->lesson could hang mid-fade
        # on software-rendered hardware. Every other app that uses a Stack
        # (settings/cookbook/installer) already sets NONE for exactly this reason
        # -- language was the lone outlier. Instant is also snappier UX.
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.content.pack_start(self.stack, True, True, 0)

        self.stack.add_named(self._home_page(), "home")
        self._course_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(self._course_holder, "course")
        self._lesson_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(self._lesson_holder, "lesson")
        self.stack.set_visible_child_name("home")

        self.connect("destroy", lambda *_: self._save_progress())

    # ================= progress =================
    def _pkey(self, ci, ui, si):
        return "%s:%d:%d" % (self.course["code"], ui, si)

    def _crowns(self, ui, si):
        return self.progress.get("crowns", {}).get(self._pkey(0, ui, si), 0)

    def _add_crown(self, ui, si):
        cr = self.progress.setdefault("crowns", {})
        k = self._pkey(0, ui, si)
        cr[k] = min(CROWN_MAX, cr.get(k, 0) + 1)

    def _bump_streak_xp(self, xp):
        self.progress["xp"] = self.progress.get("xp", 0) + xp
        today = time.strftime("%Y-%m-%d")
        last = self.progress.get("streak_day")
        if last != today:
            y = time.strftime("%Y-%m-%d",
                              time.localtime(time.time() - 86400))
            self.progress["streak"] = (self.progress.get("streak", 0) + 1
                                       if last == y else 1)
            self.progress["streak_day"] = today

    def _load_progress(self):
        try:
            with open(CFG_FILE, encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict):
                self.progress = d
        except Exception:
            self.progress = {}

    def _save_progress(self):
        try:
            nbapp.atomic_write_json(CFG_FILE, self.progress)
        except Exception:
            pass

    # ================= home (course picker) =================
    def _home_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.get_style_context().add_class("homepage")
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        head.set_margin_top(28)
        head.set_margin_bottom(10)
        t = Gtk.Label(label=_t("Learn a language"))
        t.get_style_context().add_class("hometitle")
        head.pack_start(t, False, False, 0)
        s = Gtk.Label(label=_t("Offline courses · pronunciation shown in IPA"))
        s.get_style_context().add_class("homesub")
        head.pack_start(s, False, False, 0)
        st = Gtk.Label(label="")
        st.get_style_context().add_class("homestreak")
        self._home_streak = st
        head.pack_start(st, False, False, 0)
        self._refresh_home_streak()
        outer.pack_start(head, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(3)
        flow.set_min_children_per_line(1)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_row_spacing(16)
        flow.set_column_spacing(16)
        flow.set_margin_start(28)
        flow.set_margin_end(28)
        flow.set_margin_bottom(28)
        # Cards keep their own height and a card-like width. Without these the
        # homogeneous FlowBox stretched every card to fill the scroller in both
        # directions: each course sat in the top inch of a 300px-tall panel, a
        # third of the screen wide. Three 200px columns, centred, at any size.
        flow.set_valign(Gtk.Align.START)
        flow.set_halign(Gtk.Align.CENTER)
        flow.set_size_request(3 * 200 + 2 * 16, -1)
        if not self.courses:
            empty = Gtk.Label(label=_t("No courses installed."))
            empty.get_style_context().add_class("homesub")
            outer.pack_start(empty, True, True, 0)
            return outer
        self._card_flow = flow
        self._fill_cards()
        scroll.add(flow)
        outer.pack_start(scroll, True, True, 0)
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

    def _refresh_home_streak(self):
        """Greet the returning learner with their streak/XP under the title.
        The label is built empty and stays blank until there's progress, so a
        brand-new learner sees a clean header, not a discouraging '0 XP'."""
        xp = self.progress.get("xp", 0)
        streak = self.progress.get("streak", 0)
        parts = []
        if streak:
            parts.append(_t("%d day streak") % streak)
        if xp:
            parts.append(_t("%d XP") % xp)
        self._home_streak.set_text("   ·   ".join(parts))

    def _show_home(self):
        # keep the streak/XP and each card's progress current every time the
        # picker comes back into view
        self._refresh_home_streak()
        self._fill_cards()
        self.stack.set_visible_child_name("home")

    def _course_progress(self, c):
        """(skills at full crowns, skills touched at all) for a course. Keyed
        off the course code directly, because the picker runs before any course
        is open and _pkey reads self.course."""
        crowns = self.progress.get("crowns", {})
        if not isinstance(crowns, dict):
            return 0, 0
        prefix = "%s:" % c.get("code", "")
        done = started = 0
        for k, v in crowns.items():
            if not k.startswith(prefix):
                continue
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            if v > 0:
                started += 1
            if v >= CROWN_MAX:
                done += 1
        return done, started

    def _course_card(self, c):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class("coursecard")
        card.set_size_request(200, 130)
        badge = Gtk.Label(label=(c.get("code", "?") or "?").upper()[:2])
        badge.get_style_context().add_class("codebadge")
        badge.set_halign(Gtk.Align.CENTER)
        card.pack_start(badge, False, False, 0)
        nm = Gtk.Label(label=c.get("name", "?"))
        nm.get_style_context().add_class("coursename")
        card.pack_start(nm, False, False, 0)
        sub = Gtk.Label(label=_t("from %s") % c.get("from", "English"))
        sub.get_style_context().add_class("coursefrom")
        card.pack_start(sub, False, False, 0)
        nskills = sum(len(u.get("skills", [])) for u in c.get("units", []))
        # What the card said before was the same three numbers for every course.
        # A returning learner could not tell which one they were half-way
        # through; the size of the course is what they need LAST.
        done, started = self._course_progress(c)
        if done or started:
            bar = Gtk.ProgressBar()
            bar.get_style_context().add_class("cardprog")
            bar.set_fraction(min(1.0, done / float(nskills or 1)))
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
        card.pack_start(meta, False, False, 0)
        evt = Gtk.EventBox()
        evt.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        evt.add(card)
        evt.connect("button-press-event",
                    lambda _w, _e, co=c: (self._open_course(co), True)[1])
        return evt

    # ================= course tree =================
    def _open_course(self, c):
        self.course = c
        self._render_course()
        self.stack.set_visible_child_name("course")

    def _render_course(self):
        for ch in self._course_holder.get_children():
            self._course_holder.remove(ch)
        # header
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.get_style_context().add_class("coursebar")
        back = Gtk.Button()
        back.set_relief(Gtk.ReliefStyle.NONE)
        back.get_style_context().add_class("backbtn")
        _bh = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        _bh.pack_start(Gtk.Image.new_from_pixbuf(nbicons.pixbuf("back", 13, MUTED)), False, False, 0)
        _bh.pack_start(Gtk.Label(label=_t("Courses")), False, False, 0)
        back.add(_bh)
        back.connect("clicked", lambda *_: self._show_home())
        bar.pack_start(back, False, False, 0)
        title = Gtk.Label(label=self.course.get("name", ""))
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
            note.set_max_width_chars(60)
            bar.pack_start(note, False, False, 0)
        bar.pack_start(Gtk.Box(), True, True, 0)
        xpl = Gtk.Label(label="%d XP   ·   %d day streak" %
                        (self.progress.get("xp", 0),
                         self.progress.get("streak", 0)))
        xpl.get_style_context().add_class("coursexp")
        bar.pack_end(xpl, False, False, 0)
        self._course_holder.pack_start(bar, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.set_margin_top(16)
        col.set_margin_bottom(28)
        col.set_halign(Gtk.Align.CENTER)
        for ui, unit in enumerate(self.course.get("units", [])):
            ul = Gtk.Label(label=unit.get("title", "Unit %d" % (ui + 1)))
            ul.get_style_context().add_class("unittitle")
            ul.set_margin_top(18)
            col.pack_start(ul, False, False, 0)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            row.set_halign(Gtk.Align.CENTER)
            for si, skill in enumerate(unit.get("skills", [])):
                row.pack_start(self._skill_node(ui, si, skill), False, False, 0)
                if (si + 1) % 4 == 0:
                    col.pack_start(row, False, False, 0)
                    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
                    row.set_halign(Gtk.Align.CENTER)
            col.pack_start(row, False, False, 0)
        scroll.add(col)
        self._course_holder.pack_start(scroll, True, True, 0)
        self._course_holder.show_all()

    def _skill_node(self, ui, si, skill):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.set_size_request(96, -1)
        crowns = self._crowns(ui, si)
        circle = Gtk.Box()
        circle.get_style_context().add_class("skillnode")
        if crowns >= CROWN_MAX:
            circle.get_style_context().add_class("skilldone")
        elif crowns > 0:
            circle.get_style_context().add_class("skillstarted")
        circle.set_size_request(64, 64)
        # 64x64 plus border-radius 50% is only a CIRCLE if nothing stretches
        # it. The node sits in a 96px-wide column and both it and its event box
        # default to halign FILL, so every skill drew as a flat ellipse.
        circle.set_halign(Gtk.Align.CENTER)
        icon = Gtk.Label(label=(skill.get("name", "?")[:1] or "?").upper())
        icon.get_style_context().add_class("skillicon")
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)
        icon.set_hexpand(True)
        icon.set_vexpand(True)
        circle.pack_start(icon, True, True, 0)
        evt = Gtk.EventBox()
        evt.set_halign(Gtk.Align.CENTER)
        evt.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        evt.add(circle)
        evt.connect("button-press-event",
                    lambda _w, _e: (self._start_lesson(ui, si), True)[1])
        box.pack_start(evt, False, False, 0)
        nm = Gtk.Label(label=skill.get("name", "Skill"))
        nm.get_style_context().add_class("skillname")
        nm.set_line_wrap(True)
        nm.set_justify(Gtk.Justification.CENTER)
        nm.set_max_width_chars(11)
        box.pack_start(nm, False, False, 0)
        cl = Gtk.Label(label=("%d/%d" % (crowns, CROWN_MAX)) if crowns else "")
        cl.get_style_context().add_class("crownrow")
        box.pack_start(cl, False, False, 0)
        return box

    # ================= lesson engine =================
    def _skill_items(self, skill):
        words = [dict(t=w.get("t", ""), e=w.get("e", ""), ipa=w.get("ipa", ""),
                      phrase=False) for w in skill.get("words", [])]
        phr = [dict(t=p.get("t", ""), e=p.get("e", ""), ipa=p.get("ipa", ""),
                    phrase=True) for p in skill.get("phrases", [])]
        return words, phr

    def _course_words(self):
        pool = []
        for u in self.course.get("units", []):
            for s in u.get("skills", []):
                pool.extend(s.get("words", []))
        return pool

    def _build_lesson(self, ui, si):
        skill = self.course["units"][ui]["skills"][si]
        words, phrases = self._skill_items(skill)
        pool = self._course_words()
        items = words + phrases
        if not items:
            return None
        code = self.course["code"]
        seen = set(self.progress.get("seen", []))

        def key(it):
            return "%s:%s" % (code, _norm(it["t"]))

        # TEACH FIRST: introduce a batch of the skill's not-yet-seen terms this
        # lesson (the rest wait for a repeat), and drill ONLY terms that are now
        # taught — this batch plus anything already seen — so no exercise ever
        # quizzes a word before it has been defined.
        new_items = [it for it in items if key(it) not in seen]
        seen_items = [it for it in items if key(it) in seen]
        intro_items = new_items[:INTRO_PER_LESSON]
        new_keys = [key(it) for it in intro_items]
        taught = intro_items + seen_items
        if not taught:                       # nothing to work with yet
            taught = intro_items or items
        taught_words = [it for it in taught if not it["phrase"]]

        exercises = [self._make_exercise("intro", it, words, pool)
                     for it in intro_items]
        # a tap-the-pairs warm-up once enough words are on the table
        if len(taught_words) >= 4:
            exercises.append(self._make_exercise("match", None, taught_words, pool))
        # then the graded drills, drawn only from taught terms
        drill_items = list(taught)
        random.shuffle(drill_items)
        drills = 0
        for it in drill_items:
            if drills >= LESSON_LEN:
                break
            if it["phrase"]:
                kind = random.choice(["translate_to_en", "bank", "bank"])
            else:
                kind = random.choice(["translate_to_en", "translate_to_t",
                                      "choose", "choose"])
            exercises.append(self._make_exercise(kind, it, taught_words, pool))
            drills += 1
        return {"ui": ui, "si": si, "ex": exercises, "i": 0, "wrong": 0,
                "new_keys": new_keys}

    def _make_exercise(self, kind, it, words, pool):
        if kind == "intro":
            return {"kind": "intro", "t": it["t"], "e": it["e"],
                    "ipa": it.get("ipa", ""), "phrase": it.get("phrase", False)}
        if kind == "match":
            picks = random.sample(words, min(4, len(words)))
            return {"kind": "match",
                    "pairs": [(w["t"], w["e"], w.get("ipa", "")) for w in picks]}
        if kind == "translate_to_en":
            return {"kind": "type", "prompt": it["t"], "ipa": it.get("ipa", ""),
                    "answer": it["e"], "ask": _t("Translate to English")}
        if kind == "translate_to_t":
            return {"kind": "type", "prompt": it["e"], "ipa": "",
                    "answer": it["t"], "ask": _t("Translate to %s")
                    % self.course.get("name", "")}
        if kind == "choose":
            others = [w["e"] for w in pool if _norm(w.get("e")) != _norm(it["e"])]
            opts = random.sample(others, min(3, len(others))) if others else []
            opts.append(it["e"])
            random.shuffle(opts)
            return {"kind": "choose", "prompt": it["t"],
                    "ipa": it.get("ipa", ""), "options": opts,
                    "answer": it["e"], "ask": _t("What does this mean?")}
        # bank: build the target phrase from word tiles
        toks = it["t"].split()
        distract = []
        allwords = [w["t"] for w in pool]
        for w in allwords:
            for tok in w.split():
                if tok not in toks:
                    distract.append(tok)
        random.shuffle(distract)
        bank = toks + distract[:max(2, len(toks))]
        random.shuffle(bank)
        return {"kind": "bank", "prompt": it["e"], "answer": it["t"],
                "ipa": it.get("ipa", ""), "bank": bank}

    def _start_lesson(self, ui, si):
        lesson = self._build_lesson(ui, si)
        if not lesson:
            return
        self._lesson = lesson
        self._render_exercise()
        self.stack.set_visible_child_name("lesson")

    def _render_exercise(self):
        for ch in self._lesson_holder.get_children():
            self._lesson_holder.remove(ch)
        L = self._lesson
        # A fresh exercise: nothing graded yet, and no Check button until this
        # exercise builds one (a matching round has none, and the previous
        # exercise's button is a destroyed widget by now).
        self._graded = False
        self._check_btn = None
        if L["i"] >= len(L["ex"]):
            self._lesson_complete()
            return
        # progress bar
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        top.get_style_context().add_class("lessonbar")
        quit_b = Gtk.Button()
        quit_b.set_relief(Gtk.ReliefStyle.NONE)
        quit_b.get_style_context().add_class("backbtn")
        quit_b.set_image(Gtk.Image.new_from_pixbuf(nbicons.pixbuf("wclose", 14, MUTED)))
        quit_b.connect("clicked", lambda *_: self.stack.set_visible_child_name("course"))
        top.pack_start(quit_b, False, False, 0)
        prog = Gtk.ProgressBar()
        prog.get_style_context().add_class("lessonprog")
        prog.set_fraction(L["i"] / max(1, len(L["ex"])))
        prog.set_valign(Gtk.Align.CENTER)
        top.pack_start(prog, True, True, 0)
        self._lesson_holder.pack_start(top, False, False, 0)

        ex = L["ex"][L["i"]]
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.set_margin_top(24)
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
         "intro": self._ex_intro}[ex["kind"]](body, ex)
        # Every _ex_* ends with an expanding spacer before its footer; adding a
        # matching one at the top centres the exercise in the page instead of
        # pinning it under the progress bar above a half-screen of nothing.
        lead = Gtk.Box()
        body.pack_start(lead, True, True, 0)
        body.reorder_child(lead, 0)
        self._lesson_holder.show_all()

    def _ask_label(self, body, text):
        a = Gtk.Label(label=text, xalign=0)
        a.get_style_context().add_class("exask")
        body.pack_start(a, False, False, 0)

    def _prompt_block(self, body, prompt, ipa):
        p = Gtk.Label(label=prompt)
        p.get_style_context().add_class("exprompt")
        p.set_line_wrap(True)
        body.pack_start(p, False, False, 0)
        if ipa:
            i = Gtk.Label(label=_t("/%s/") % ipa)
            i.get_style_context().add_class("exipa")
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
        body.pack_start(Gtk.Box(), True, True, 0)
        self._continue_footer(body, _t("Got it"), self._got_intro)

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

    # ---- exercise: type the translation ----
    def _ex_type(self, body, ex):
        self._ask_label(body, ex.get("ask") or _t("Translate"))
        self._prompt_block(body, ex["prompt"], ex.get("ipa", ""))
        entry = Gtk.Entry()
        entry.get_style_context().add_class("exentry")
        entry.set_placeholder_text(_t("Type your answer…"))
        entry.connect("activate", lambda *_: self._check_type(entry, ex))
        body.pack_start(entry, False, False, 0)
        body.pack_start(Gtk.Box(), True, True, 0)
        self._check_footer(body, lambda: self._check_type(entry, ex))
        entry.connect("changed",
                      lambda e: self._arm_check(bool(e.get_text().strip())))
        GLib.idle_add(entry.grab_focus)

    def _check_type(self, entry, ex):
        ok = _norm(entry.get_text()) == _norm(ex["answer"]) or \
            _norm(entry.get_text()) in [_norm(a) for a in ex.get("alts", [])]
        self._grade(ok, ex["answer"])

    # ---- exercise: multiple choice ----
    def _ex_choose(self, body, ex):
        self._ask_label(body, ex.get("ask") or _t("Choose"))
        self._prompt_block(body, ex["prompt"], ex.get("ipa", ""))
        self._choice_result = {"picked": None}
        btns = []
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for opt in ex["options"]:
            b = Gtk.Button(label=opt)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("choicebtn")
            b.connect("clicked", lambda w, o=opt: self._pick_choice(w, o, btns))
            btns.append(b)
            grid.pack_start(b, False, False, 0)
        body.pack_start(grid, False, False, 0)
        body.pack_start(Gtk.Box(), True, True, 0)
        self._check_footer(body, lambda: self._grade(
            _norm(self._choice_result["picked"] or "") == _norm(ex["answer"]),
            ex["answer"]))

    def _pick_choice(self, w, opt, btns):
        if self._graded:            # the answer is in; don't let it be edited
            return
        self._choice_result["picked"] = opt
        for b in btns:
            b.get_style_context().remove_class("choicesel")
        w.get_style_context().add_class("choicesel")
        self._arm_check(True)

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
        self._bank_state = {"chosen": []}

        def add_tile(container, word, from_bank):
            b = Gtk.Button(label=word)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("banktile")
            b.connect("clicked", lambda w: move(w, word, from_bank))
            container.add(b)
            container.show_all()

        def move(widget, word, from_bank):
            if self._graded:        # the answer is in; don't let it be edited
                return
            parent = widget.get_parent()   # FlowBoxChild
            if from_bank:
                self._bank_state["chosen"].append(word)
                widget.destroy() if parent is None else parent.destroy()
                add_tile(answer_box, word, False)
            else:
                if word in self._bank_state["chosen"]:
                    self._bank_state["chosen"].remove(word)
                parent.destroy() if parent else widget.destroy()
                add_tile(bank_box, word, True)
            self._arm_check(bool(self._bank_state["chosen"]))

        for word in ex["bank"]:
            add_tile(bank_box, word, True)
        body.pack_start(Gtk.Box(), True, True, 0)
        self._check_footer(body, lambda: self._grade(
            _norm(" ".join(self._bank_state["chosen"])) == _norm(ex["answer"]),
            ex["answer"]))

    # ---- exercise: match pairs ----
    def _ex_match(self, body, ex):
        self._ask_label(body, _t("Tap the matching pairs"))
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
            b = Gtk.Button(label=text)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("matchtile")
            if ipa:
                # word in the UI font; the IPA line pinned to DejaVu Sans so the
                # whole transcription uses one phonetic typeface (the UI face
                # carries no IPA extensions, so an unpinned transcription would
                # be assembled from two type designs by the fallback).
                b.get_child().set_markup(
                    GLib.markup_escape_text(text)
                    + '\n<span face="DejaVu Sans">/'
                    + GLib.markup_escape_text(ipa) + '/</span>')
                b.get_child().set_justify(Gtk.Justification.CENTER)
            b.connect("clicked", lambda w, i=idx: self._match_tap(w, i, "t"))
            left.pack_start(b, False, False, 0)
        for text, idx in evals:
            b = Gtk.Button(label=text)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("matchtile")
            b.connect("clicked", lambda w, i=idx: self._match_tap(w, i, "e"))
            right.pack_start(b, False, False, 0)
        body.pack_start(Gtk.Box(), True, True, 0)
        # Matching completes itself on the last pair, so it has no Check button
        # -- but it still gets the footer rule and status line, otherwise this
        # one exercise ends in a third of a page of blank paper and its
        # "Correct!" has nowhere to appear.
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
                GLib.timeout_add(250, lambda: self._grade(True, "") or False)
        elif w is not pbtn:
            w.get_style_context().add_class("matchbad")
            GLib.timeout_add(400, lambda: (w.get_style_context()
                                           .remove_class("matchbad"), False)[1])

    # ---- grading + footer ----
    def _result_footer(self, body):
        """The rule + status line every exercise ends with. Returns the box so
        a caller can pack its own button into it."""
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        foot.get_style_context().add_class("exfoot")
        self._result_lbl = Gtk.Label(label="", xalign=0)
        self._result_lbl.get_style_context().add_class("exresult")
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

    def _grade(self, ok, answer):
        if self._graded:            # one answer per exercise
            return
        self._graded = True
        L = self._lesson
        if ok:
            self._toast(True, _t("Correct!"))
            GLib.timeout_add(750, self._advance)
            return
        L["wrong"] += 1
        # Ask a missed item again before the lesson ends — getting it wrong once
        # and never seeing it again is how a drill teaches nothing. One repeat
        # only (the copy is flagged), so a lesson can't grow without end.
        ex = L["ex"][L["i"]]
        if not ex.get("retry"):
            again = dict(ex)
            again["retry"] = True
            L["ex"].append(again)
        self._toast(False, (_t("Answer: %s") % answer) if answer
                    else _t("Not quite"))
        self._hold_for_continue()

    def _hold_for_continue(self):
        """After a wrong answer, stop and wait. The right answer used to flash
        past in three quarters of a second — long enough to see that you were
        wrong, never long enough to read and learn what was right."""
        btn = self._check_btn
        if btn is None:
            GLib.timeout_add(750, self._advance)
            return
        try:
            btn.disconnect(self._check_id)
        except Exception:
            pass
        btn.set_label(_t("Continue"))
        btn.set_sensitive(True)
        btn.connect("clicked", lambda *_: self._advance())
        btn.grab_focus()

    def _toast(self, ok, text):
        try:
            self._result_lbl.set_text(text)
            self._result_lbl.get_style_context().add_class(
                "resok" if ok else "resbad")
        except Exception:
            pass

    def _advance(self):
        # Reachable from a timer AND from Continue; make the second one a no-op
        # rather than a skipped exercise.
        if not self._graded:
            return False
        self._graded = False
        self._lesson["i"] += 1
        self._render_exercise()
        return False

    def _lesson_complete(self):
        L = self._lesson
        # remember the words we just introduced so a later lesson doesn't re-teach
        if L.get("new_keys"):
            seen = self.progress.setdefault("seen", [])
            for k in L["new_keys"]:
                if k not in seen:
                    seen.append(k)
        # intro cards aren't graded, so score against the drilled exercises only
        graded = sum(1 for e in L["ex"] if e["kind"] != "intro")
        correct = graded - L["wrong"]
        if L["wrong"] == 0:
            self._add_crown(L["ui"], L["si"])
        xp = 10 + (5 if L["wrong"] == 0 else 0)
        self._bump_streak_xp(xp)
        self._save_progress()
        for ch in self._lesson_holder.get_children():
            self._lesson_holder.remove(ch)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        star = Gtk.Image.new_from_pixbuf(nbicons.pixbuf("star", 52, "#B8912E"))
        star.set_halign(Gtk.Align.CENTER)
        box.pack_start(star, False, False, 0)
        t = Gtk.Label(label=_t("Lesson complete!"))
        t.get_style_context().add_class("donetitle")
        box.pack_start(t, False, False, 0)
        s = Gtk.Label(label=_t("%d / %d correct") % (correct, graded)
                      + "   ·   " + _t("+%d XP") % xp)
        s.get_style_context().add_class("donesub")
        box.pack_start(s, False, False, 0)
        cont = Gtk.Button(label=_t("Continue"))
        cont.set_relief(Gtk.ReliefStyle.NONE)
        cont.get_style_context().add_class("checkbtn")
        cont.set_halign(Gtk.Align.CENTER)
        cont.set_margin_top(10)
        cont.connect("clicked", lambda *_: self._after_lesson())
        box.pack_start(cont, False, False, 0)
        self._lesson_holder.pack_start(box, True, True, 0)
        self._lesson_holder.show_all()

    def _after_lesson(self):
        self._render_course()
        self.stack.set_visible_child_name("course")

    # ================= menu =================
    def menu_items(self, name):
        if name == "File":
            return [
                ("Courses", self._show_home),
                nbapp.SEP,
                ("Reset Progress…", self._reset_progress),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        return super().menu_items(name)

    def _reset_progress(self):
        """Wiping every crown, the XP and the streak has no undo, so ask first.
        This used to fire straight off the menu — one slip and weeks of work
        were gone without a word."""
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.get_style_context().add_class("confirmcard")
        t = Gtk.Label(label=_t("Start over?"), xalign=0)
        t.get_style_context().add_class("confirmtitle")
        box.pack_start(t, False, False, 0)
        m = Gtk.Label(xalign=0, label=_t(
            "This clears every crown, your XP and your streak in all courses. "
            "It cannot be undone."))
        m.set_line_wrap(True)
        m.set_max_width_chars(44)
        m.get_style_context().add_class("confirmmsg")
        box.pack_start(m, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_halign(Gtk.Align.END)
        keep = Gtk.Button(label=_t("Keep My Progress"))
        keep.set_relief(Gtk.ReliefStyle.NONE)
        keep.get_style_context().add_class("confirmkeep")
        keep.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.CANCEL))
        wipe = Gtk.Button(label=_t("Reset Everything"))
        wipe.set_relief(Gtk.ReliefStyle.NONE)
        wipe.get_style_context().add_class("confirmwipe")
        wipe.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.OK))
        row.pack_start(keep, False, False, 0)
        row.pack_start(wipe, False, False, 0)
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
        go = dlg.run() == Gtk.ResponseType.OK
        dlg.destroy()
        if not go:
            return
        self.progress = {}
        self._save_progress()
        self._refresh_home_streak()
        if self.course:
            self._render_course()

    # ================= css =================
    def _install_css(self):
        css = b"""
        * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .homepage, stack { background: #FCFBF8; }
        .hometitle { font-size: 26px; font-weight: 700; color: #1A1916; }
        .homesub { font-size: 13px; color: #9A9484; }
        .homestreak { font-size: 13px; color: #4F7A3A; margin-top: 4px; }
        .coursecard { background: #F1EEE6; border: 1px solid #C9C4B6;
                      border-radius: 8px; padding: 14px; }
        .coursecard:hover { background: #FBEFEC; border-color: #C8341E; }
        .codebadge { font-size: 22px; font-weight: 700; color: #FCFBF8;
                     background: #4F7A3A; border-radius: 50%;
                     min-width: 54px; min-height: 54px; padding: 0; }
        .coursename { font-size: 17px; font-weight: 600; color: #1A1916; }
        .coursefrom { font-size: 11.5px; color: #9A9484; }
        .coursemeta { font-size: 11.5px; color: #6E695E; margin-top: 4px; }
        .cardprog trough { min-height: 6px; background: #DCD8CC;
                           border-radius: 3px; border: none; }
        .cardprog progress { min-height: 6px; background: #4F7A3A;
                             border-radius: 3px; border: none; }
        /* reset-progress confirmation (paper card; signage red only on the
           destructive button, as elsewhere in the OS) */
        .confirmcard { background: #FCFBF8; border: 1px solid #C9C4B6;
                       padding: 22px 26px 18px; }
        .confirmtitle { font-size: 19px; font-weight: 700; color: #1A1916; }
        .confirmmsg { font-size: 13px; color: #6E695E; }
        .confirmkeep { font-size: 13px; padding: 7px 16px; background: #FCFBF8;
                       border: 1px solid #C9C4B6; border-radius: 3px;
                       box-shadow: none; }
        .confirmkeep, .confirmkeep label { color: #1A1916; }
        .confirmkeep:hover { background: #F1EEE6; }
        .confirmwipe { font-size: 13px; font-weight: 600; padding: 7px 16px;
                       background: #C8341E; border: none; border-radius: 3px;
                       box-shadow: none; }
        .confirmwipe, .confirmwipe label { color: #FCFBF8; }
        .confirmwipe:hover { background: #B12D19; }
        .coursebar { background: #F1EEE6; border-bottom: 1px solid #C9C4B6;
                     padding: 12px 16px; }
        /* Every rule below that repeats a colour on `... label` is doing real
           work: a colour set on a BUTTON node never reaches the label inside
           it, because the theme's universal `* { color: ink }` matches that
           label node directly and beats the inherited value. Without them the
           Check / Continue / Got it buttons drew ink text on the green fill --
           the primary action of every single exercise, barely readable. */
        .backbtn { font-size: 14px; box-shadow: none; border: none;
                   background: transparent; padding: 4px 8px; }
        .backbtn, .backbtn label { color: #6E695E; }
        .backbtn:hover { background: #E9E5DA; }
        .coursetitle { font-size: 16px; font-weight: 600; color: #1A1916; }
        .coursenote { font-size: 12px; color: #9A9484; margin-left: 4px; }
        .coursexp { font-size: 13px; color: #6E695E; }
        .unittitle { font-size: 13px; letter-spacing: 0.1em; font-weight: 700;
                     color: #9A9484; }
        .skillnode { background: #E4DFD3; border: 2px solid #C9C4B6;
                     border-radius: 50%; }
        .skillstarted { background: #F3E7C6; border-color: #B8912E; }
        .skilldone { background: #DCE9CE; border-color: #4F7A3A; }
        .skillicon { font-size: 26px; }
        .skillname { font-size: 11.5px; color: #3A362F; }
        .crownrow { font-size: 10px; }
        .lessonbar { padding: 12px 16px; }
        .lessonprog trough { min-height: 12px; background: #E4DFD3;
                             border-radius: 6px; border: none; }
        .lessonprog progress { min-height: 12px; background: #4F7A3A;
                               border-radius: 6px; border: none; }
        .exask { font-size: 13px; letter-spacing: 0.06em; color: #9A9484;
                 font-weight: 700; }
        .exprompt { font-size: 26px; font-weight: 600; color: #1A1916; }
        /* Render IPA in DejaVu Sans for one consistent phonetic typeface. The
           shipped Nimbus Sans stops at Latin/Greek/Cyrillic and carries none of
           the IPA extensions - not even the script g, the stress mark or the
           length mark, let alone the retroflex and alveolo-palatal letters that
           Mandarin and Serbo-Croatian need - so an unpinned transcription would
           be assembled glyph-by-glyph out of two type designs. DejaVu has full
           IPA coverage; pin the whole line to it. */
        .exipa { font-family: "DejaVu Sans", sans-serif; font-size: 16px;
                 color: #6E695E; font-style: italic; }
        .exmeaning { font-size: 21px; color: #2F6B4F; font-weight: 600;
                     margin-top: 4px; }
        .exentry { font-size: 18px; padding: 10px 12px; border-radius: 4px;
                   border: 1px solid #CFC9BA; background: #FCFBF8; }
        .choicebtn { font-size: 16px; padding: 12px 16px; border-radius: 6px;
                     border: 1px solid #C9C4B6; background: #FCFBF8; color: #1A1916;
                     box-shadow: none; }
        .choicebtn:hover { background: #F4F2EC; }
        .choicesel { border: 2px solid #C8341E; background: #FBEFEC; }
        .banktile, .matchtile { font-size: 15px; padding: 8px 12px;
                     border-radius: 6px; border: 1px solid #C9C4B6;
                     background: #FCFBF8; color: #1A1916; box-shadow: none; }
        .banktile:hover, .matchtile:hover { background: #F4F2EC; }
        .bankanswer { border-bottom: 2px solid #D7D2C5; min-height: 44px; }
        .matchsel { border: 2px solid #3E6B8C; background: #EAF0F4; }
        .matchgone { opacity: 0.25; }
        .matchbad { border: 2px solid #C8341E; }
        .exfoot { border-top: 1px solid #E4DFD3; padding: 14px 0 4px; }
        .checkbtn { font-size: 15px; font-weight: 600; padding: 10px 28px;
                    border-radius: 6px; background: #4F7A3A;
                    box-shadow: none; border: none; }
        .checkbtn, .checkbtn label { color: #FCFBF8; }
        .checkbtn:hover { background: #446B32; }
        /* not-yet-answerable Check: clearly waiting, not clearly broken */
        .checkbtn:disabled { background: #DCD8CC; }
        .checkbtn:disabled, .checkbtn:disabled label { color: #9A9484; }
        .exresult { font-size: 14px; color: #6E695E; }
        .resok { color: #4F7A3A; font-weight: 600; }
        .resbad { color: #C8341E; font-weight: 600; }
        .donestar { font-size: 54px; }
        .donetitle { font-size: 24px; font-weight: 700; color: #1A1916; }
        .donesub { font-size: 15px; color: #6E695E; }
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
    nbapp.run(Language)
