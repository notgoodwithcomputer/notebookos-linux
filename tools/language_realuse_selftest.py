#!/usr/bin/env python3
"""
Five things a learner meets in the Language app, driven through the real
widgets of the real app (tools/appdrive) on a fresh store.

Each check is named for the promise it holds, and each one was RED before the
fix that put it here:

  bank                the word bank grades WHAT IS ON SCREEN. A phrase with a
                      word in it twice ("niǎo hěn xiǎo, mǎ hěn dà") used to be
                      graded off a parallel list of words, and taking a
                      repeated tile back removed the FIRST copy from that list
                      while the screen lost the tile that was tapped -- so a
                      sentence that read correctly was marked wrong and cost a
                      heart. The plain build is checked too: a grader that says
                      "wrong" to everything would otherwise pass the first.

  locked / started /  a crown needs a lesson with NO mistakes. A learner who
  continue            finished one WITH a mistake was told to "Finish a lesson
                      in Greetings first", met a course card reading like the
                      courses they had never opened, an Explorer award reading
                      "Courses started: 0", and a skill card still offering to
                      "Start lesson". The rule is unchanged -- what it SAYS is
                      what these hold.

  note                the one line explaining a course ("Pinyin only -- learning
                      to speak, not to read characters") was capped at 40
                      characters, so it was cut off mid-word at every width and
                      had no tooltip: unreadable anywhere in the app.

  missed listening    "Worth another look" showed a missed listening exercise as
                      its bare transcription in the interface face -- no word,
                      no slashes, no meaning.

  empty vocabulary    a Vocabulary page with nothing on it described the bars
                      that were not there.

Run:
    tools/guestrun.sh python3 tools/language_realuse_selftest.py
"""
import os
import sys
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_ROOT = tempfile.mkdtemp(prefix="nb-langreal-")
os.environ["NB_HOME"] = _ROOT

import appdrive                                                   # noqa: E402
from gi.repository import Gtk                                     # noqa: E402

FAILED, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail
                                                   else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def drive(tag):
    home = os.path.join(_ROOT, tag)
    shutil.rmtree(home, ignore_errors=True)
    return appdrive.Drive("language", home=home)


def course(app, code):
    return next(c for c in app.courses if c.get("code") == code)


def tiles(fb):
    out = []
    for ch in fb.get_children():
        b = ch.get_child() if isinstance(ch, Gtk.FlowBoxChild) else ch
        if isinstance(b, Gtk.Button):
            out.append(b)
    return out


def bank_boxes(d):
    fbs = [w for w in d.walk() if isinstance(w, Gtk.FlowBox)]
    ans = [f for f in fbs if f.get_style_context().has_class("bankanswer")][0]
    return ans, [f for f in fbs if f is not ans][0]


def tap(d, fb, word, which=0):
    hits = [b for b in tiles(fb) if b.get_label() == word]
    hits[which].clicked()
    d.pump(0.05)


def labelled(d, text, cls=None):
    """Visible buttons carrying `text` (on the button or a label inside it)."""
    out = []
    for b in d.find(Gtk.Button):
        if cls and not b.get_style_context().has_class(cls):
            continue
        if b.get_label() == text:
            out.append(b)
            continue
        for w in d.walk(b):
            if isinstance(w, Gtk.Label) and w.get_text() == text:
                out.append(b)
                break
    return out


# ======================================================================
# the word bank
# ======================================================================
def dup_phrase(app, c):
    """A shipped phrase with the same word in it twice, with another word
    BETWEEN the two copies, and the index of the second copy.

    The gap matters: with the copies side by side, dropping the first of them
    from a list of words leaves the same list as dropping the second, so the
    defect this holds would not show. "niǎo hěn xiǎo, mǎ hěn dà" is the shape
    that does."""
    import language
    for unit in c.get("units", []):
        for skill in unit.get("skills", []):
            words, phrases = app._skill_items(skill)
            for it in phrases + words:
                toks = language._toks(it["t"])
                for k, t in enumerate(toks):
                    j = toks.index(t)
                    if j < k - 1:
                        return skill, words, it, k
    return None, None, None, 0


def play_bank(d, ex, toks, dup_at=0):
    """Build `toks` tile by tile. With dup_at set, make the slip a person makes
    first: the repeated word tapped one word early, then tapped again in the
    answer row to send that tile back."""
    app = d.app
    app._run(app._lesson_state([ex], ui=0, si=0, kind="lesson", title="Bank"))
    d.pump(0.2)
    ans, bank = bank_boxes(d)
    hearts_before = app._hearts()
    if dup_at:
        for w in toks[:dup_at - 1]:
            tap(d, bank, w)
        tap(d, bank, toks[dup_at])            # the slip, one word too early
        tap(d, ans, toks[dup_at], which=-1)   # tap it in the row to take it back
        for w in toks[dup_at - 1:]:
            tap(d, bank, w)
    else:
        for w in toks:
            tap(d, bank, w)
    spelled = [b.get_label() for b in tiles(ans)]
    d.click("Check")
    d.pump(0.3)
    verdict = app._result_lbl.get_text()
    return spelled, verdict, hearts_before, app._hearts()


def bank_checks(shots):
    d = drive("bank")
    app = d.app
    try:
        zh = course(app, "zh")
        app._open_course(zh)
        skill, words, item, dup_at = dup_phrase(app, zh)
        if not check("a course still ships a phrase with a repeated word",
                     item is not None):
            return
        ex = app._make_exercise("bank", item, words)
        spelled, verdict, h0, h1 = play_bank(d, ex, ex["tokens"], dup_at)
        d.shot(os.path.join(shots, "bank-duplicate.png"))
        check("word bank grades the sentence its tiles spell",
              spelled == list(ex["tokens"]) and verdict == "Correct"
              and h1 == h0,
              "row %r verdict %r hearts %d->%d" % (" ".join(spelled), verdict,
                                                   h0, h1))
    finally:
        d.close()

    d = drive("bank2")
    app = d.app
    try:
        zh = course(app, "zh")
        app._open_course(zh)
        skill, words, item, _dup = dup_phrase(app, zh)
        ex = app._make_exercise("bank", item, words)
        spelled, verdict, h0, h1 = play_bank(d, ex, ex["tokens"])
        check("word bank still grades a plain build correct",
              verdict == "Correct" and h1 == h0,
              "verdict %r hearts %d->%d" % (verdict, h0, h1))
        # and a build that is genuinely wrong is still wrong
    finally:
        d.close()

    d = drive("bank3")
    app = d.app
    try:
        zh = course(app, "zh")
        app._open_course(zh)
        skill, words, item, _dup = dup_phrase(app, zh)
        ex = app._make_exercise("bank", item, words)
        spelled, verdict, h0, h1 = play_bank(
            d, ex, list(reversed(ex["tokens"])))
        check("word bank still marks a scrambled build wrong",
              verdict != "Correct" and h1 < h0,
              "verdict %r hearts %d->%d" % (verdict, h0, h1))
    finally:
        d.close()


# ======================================================================
# a lesson finished WITH a mistake
# ======================================================================
def answer_one(d, wrong):
    app = d.app
    L = app._lesson
    ex = L["ex"][L["i"]]
    kind = ex["kind"]
    if kind == "intro":
        d.button("Continue").clicked()
        d.pump(0.2)
        return
    if kind in ("choose", "listen"):
        pick = (ex["answer"] if not wrong else
                next(o for o in ex["options"] if o != ex["answer"]))
        d.button(pick).clicked()
        d.pump(0.05)
        d.button("Check").clicked()
    elif kind == "type":
        e = d.find(Gtk.Entry)[0]
        e.grab_focus()
        d.type(ex["answer"] if not wrong else "zzz")
        d.pump(0.05)
        d.button("Check").clicked()
    elif kind == "bank":
        ans, bank = bank_boxes(d)
        toks = list(ex["tokens"])
        if wrong:
            toks = list(reversed(toks))
        for w in toks:
            tap(d, bank, w)
        d.button("Check").clicked()
    elif kind == "match":
        for t, e, _ipa in ex["pairs"]:
            for lab in (t, e):
                [b for b in d.find(Gtk.Button, label=lab)
                 if b.get_style_context().has_class("matchtile")][0].clicked()
                d.pump(0.05)
        d.pump(0.8)
        settle(d)
        return
    else:
        raise RuntimeError("unknown exercise kind %r" % kind)
    d.pump(0.2)
    if d.app._lesson is not None and d.app._graded:
        cont = labelled(d, "Continue", "checkbtn")
        if cont:
            cont[0].clicked()
    d.pump(1.0)
    settle(d)


def settle(d, limit=4.0):
    import time
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if d.app._lesson is None or not d.app._graded:
            return
        d.pump(0.1)


def lesson_checks(shots):
    d = drive("lesson")
    app = d.app
    try:
        es = course(app, "es")
        app._open_course(es)
        d.pump(0.2)
        skill = es["units"][0]["skills"][0]
        app._show_skill_card(0, 0, skill)
        d.pump(0.2)
        check("a fresh skill offers to start a lesson",
              bool(labelled(d, "Start lesson", "checkbtn")))
        labelled(d, "Start lesson", "checkbtn")[0].clicked()
        d.pump(0.3)

        graded = 0
        for _ in range(60):
            if app._lesson is None:
                break
            ex = app._lesson["ex"][app._lesson["i"]]
            is_graded = ex["kind"] != "intro"
            if is_graded:
                graded += 1
            answer_one(d, wrong=(graded == 2 and is_graded))
            d.pump(0.1)
        check("the lesson was finished with one mistake and no crown",
              app._crowns(0, 0) == 0 and graded > 1,
              "%d graded, crowns %r" % (graded, app.progress.get("crowns")))
        d.shot(os.path.join(shots, "lesson-end.png"))
        labelled(d, "Continue", "checkbtn")[0].clicked()
        d.pump(0.5)

        # the locked node still says what opens it -- and now says the truth
        check("the next skill is still locked by the crown rule",
              app._skill_open(0, 1) is False)
        app._tap_skill(0, 1, es["units"][0]["skills"][1], "locked")
        d.pump(0.2)
        said = [t for t in d.texts() if "Finish a lesson" in t]
        d.shot(os.path.join(shots, "locked-toast.png"))
        check("a locked skill asks for a lesson with no mistakes",
              bool(said) and all("no mistakes" in t for t in said),
              "toast %r" % (said,))

        # the skill card knows the lesson happened
        app._show_skill_card(0, 0, skill)
        d.pump(0.2)
        d.shot(os.path.join(shots, "skill-card-after.png"))
        check("a skill with a lesson behind it offers to continue",
              bool(labelled(d, "Continue", "checkbtn"))
              and not labelled(d, "Start lesson", "checkbtn"))
        app._hide_card()
        d.pump(0.2)

        # the course card and the Explorer award know it too
        check("a course with a lesson behind it counts as started",
              app._course_progress(es)[1] >= 1,
              "progress %r" % (app._course_progress(es),))
        check("a course with a lesson behind it is not counted as untouched",
              app._course_progress(course(app, "fr"))[1] == 0,
              "an untouched course must still read as untouched")
        app._show_home()
        d.pump(0.3)
        d.shot(os.path.join(shots, "home-after.png"))
        home = [t for t in d.texts() if "skill" in t or "unit" in t]
        check("the course card says a skill has been started",
              any("started" in t for t in home), "card said %r" % (home,))
        check("Explorer counts the course as started",
              app._award_level("explorer")[1] >= 1,
              "explorer %r" % (app._award_level("explorer"),))

        # and the other side of the vocabulary copy: once there ARE bars on
        # the page, the line that explains them is back.
        app._open_course(es)
        d.pump(0.2)
        labelled(d, "Vocabulary")[0].clicked()
        d.pump(0.3)
        d.shot(os.path.join(shots, "vocab-met.png"))
        said = [t for t in d.texts() if t.strip()]
        check("a vocabulary page with words on it explains its bars",
              any("bars" in t for t in said),
              "page said %r" % ([t for t in said if " met" in t],))
    finally:
        d.close()


# ======================================================================
# the course note, the missed listening row, the empty vocabulary page
# ======================================================================
def note_checks(shots):
    d = drive("note")
    app = d.app
    try:
        zh = course(app, "zh")
        app._open_course(zh)
        d.pump(0.3)
        note = [w for w in d.walk() if isinstance(w, Gtk.Label)
                and w.get_style_context().has_class("coursenote")]
        if not check("the course bar carries the course note", bool(note)):
            return
        note = note[0]
        d.shot(os.path.join(shots, "course-note-1024.png"))
        check("the whole course note is reachable from the bar",
              note.get_tooltip_text() == zh["note"],
              "tooltip %r" % (note.get_tooltip_text(),))
        d.resize(1366, 740)
        app._render_course()
        d.pump(0.3)
        note = [w for w in d.walk() if isinstance(w, Gtk.Label)
                and w.get_style_context().has_class("coursenote")][0]
        d.shot(os.path.join(shots, "course-note-1366.png"))
        check("the course note is shown in full where the bar has room",
              not note.get_layout().is_ellipsized(),
              "%dpx shows %r" % (note.get_allocation().width,
                                 note.get_layout().get_text()))
    finally:
        d.close()


def missed_checks(shots):
    d = drive("missed")
    app = d.app
    try:
        zh = course(app, "zh")
        app._open_course(zh)
        d.pump(0.2)
        words, _phr = app._skill_items(zh["units"][0]["skills"][0])
        item = next(w for w in words if w.get("ipa"))
        ex = app._make_exercise("listen", item, words)
        ex["retry"] = True          # no repeat round; end the lesson at once
        app._run(app._lesson_state([ex], ui=0, si=0, kind="lesson",
                                   title="Listen"))
        d.pump(0.3)
        d.button(next(o for o in ex["options"]
                      if o != ex["answer"])).clicked()
        d.pump(0.05)
        d.button("Check").clicked()
        d.pump(0.4)
        labelled(d, "Continue", "checkbtn")[0].clicked()
        d.pump(1.2)
        d.shot(os.path.join(shots, "missed-listen.png"))
        rows = []
        for w in d.walk():
            if not isinstance(w, Gtk.Label):
                continue
            cls = w.get_style_context()
            if cls.has_class("misspro"):
                rows.append(("pro", w.get_text()))
            elif cls.has_class("missans"):
                rows.append(("ans", w.get_text()))
        pro = [t for k, t in rows if k == "pro"]
        ans = [t for k, t in rows if k == "ans"]
        check("a missed listening exercise is named by its word",
              bool(pro) and item["t"] in pro[0]
              and ("/%s/" % ex["prompt"]) in pro[0]
              and item["e"] in ans,
              "row %r / %r" % (pro, ans))
    finally:
        d.close()


def vocab_checks(shots):
    d = drive("vocab")
    app = d.app
    try:
        es = course(app, "es")
        app._open_course(es)
        d.pump(0.2)
        labelled(d, "Vocabulary")[0].clicked()
        d.pump(0.3)
        d.shot(os.path.join(shots, "vocab-empty.png"))
        said = [t for t in d.texts() if t.strip()]
        check("an empty vocabulary page does not describe bars",
              not any("bars" in t for t in said),
              "page said %r" % ([t for t in said if "bars" in t],))
        total = sum(len(a) + len(b) for a, b in
                    (app._skill_items(sk) for u in es.get("units", [])
                     for sk in u.get("skills", [])))
        want = "0 of %d words and phrases met" % total
        check("an empty vocabulary page still counts what is left to meet",
              want in said, "wanted %r among %r" % (want, said[:3]))
    finally:
        d.close()


def main():
    shots = os.path.join(_ROOT, "shots")
    os.makedirs(shots, exist_ok=True)
    for fn in (bank_checks, lesson_checks, note_checks, missed_checks,
               vocab_checks):
        try:
            fn(shots)
        except Exception as exc:                                  # noqa: BLE001
            # A check fails by NAME, never by traceback: a crash here is a
            # failure of the thing this group was holding.
            import traceback
            traceback.print_exc()
            check("%s ran to the end" % fn.__name__.replace("_", " "), False,
                  "%s: %s" % (type(exc).__name__, exc))
    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_ROOT, ignore_errors=True)
sys.exit(rc)
