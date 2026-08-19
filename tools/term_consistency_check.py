#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One concept, one word — per language, inside one app.

A translated interface can be 100% covered, pass every placeholder check and
still read badly, because nothing in the pipeline notices that the same thing
is called three different names. The GBA SDK called a tile `baldosa`,
`mosaico` and `pieza` in Spanish, `tessera` and `casella` in Italian, `peça`
and `mosaico` in Portuguese, `плитка` and `тайл` in Russian, `karo`, `taş` and
`döşeme` in Turkish, and `פּליטקע` and `קאַפֿל` in Yiddish — all in the same
app, several of them in adjacent labels.

For each concept this checks every catalog key that names it, and fails if a
language uses more than one root across them.

Two traps this tool exists to avoid, both of which produced wrong answers on
the first attempt:

* **Match roots on word boundaries.** Bare substring matching reported Italian
  as inconsistent because the verb `mettile` ("put them") contains `tile`.
* **Establish which app owns a key before unifying it.** Spanish uses `ficha`
  for the desktop board tiles, the 2048 tiles and the sliding-tile puzzle.
  That is a genuinely different concept and correctly a different word;
  "fixing" it would have been the defect. Ownership is decided by looking for
  the key as a QUOTED literal — plain substring matching claimed `TILES` was
  shared with widgets.py, where the real text is the identifier `FILL_TILES`,
  and that `1 tile` was shared with xrootbg.py, where it is inside a comment.
"""
import io
import json
import os
import re
import sys
import ast
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")

# concept -> (key pattern, owning apps, {lang: [root, ...]})
# The FIRST root listed is the agreed term; the rest are the words that have
# turned up as alternatives and must not come back.
CONCEPTS = {
    # The first OS-WIDE concept (task 026): curated from a measured survey
    # (--survey '\bfolders?\b', 2026-08-07) that showed every language
    # already on ONE root across seven clean keys — this entry LOCKS that
    # state. sr regressed here once (a folder was 'mapa' while the OS ships
    # a Maps app); maps.py is deliberately OUT of scope because the Maps
    # app's own Serbian name legitimately contains the stray root — the
    # `ficha` lesson: scope decides what a word may mean.
    "folder": {
        "pattern": r"\bfolders?\b",
        "apps": {"finder.py", "nbpicker.py", "gbasdk.py", "music.py",
                 "media.py", "video.py", "sequencer.py"},
        "also": ("A folder for the backup could not be",
                 "Scanning your Home folder",
                 "This folder has nothing this app can open"),
        "roots": {
            "de": [r"ordner", r"verzeichnis"],
            "el": [r"φάκελ"],
            "eo": [r"dosieruj"],
            "es": [r"carpet", r"directori"],
            "fr": [r"dossier", r"répertoire"],
            "hi": [r"फ़ोल्डर|फोल्डर"],
            "it": [r"cartell", r"\bdirectory"],
            "ja": [r"フォルダ"],
            "ko": [r"폴더"],
            "nl": [r"\bmap\b|mappen"],  # 'map' IS Dutch for folder — correct
            "pl": [r"folder"],
            "pt": [r"\bpasta", r"diretóri"],
            "ru": [r"папк|папок"],
            "sr": [r"fascikl", r"\bmap[aeiu]\b"],  # mapa must not come back
            "tr": [r"klasör"],
            "yi": [r"פּאַפּקע"],
            "zh": [r"文件夹"],
        },
    },
    "tile": {
        "pattern": r"\btiles?\b",
        "apps": {"gbasdk.py", "gbahelp.py", "gbabuild.py"},
        # These five are written across two source lines, so they are not
        # findable as a single quoted literal. Naming them by prefix keeps
        # them in the check instead of silently dropping five of 25 keys.
        "also": ("Click to paint the chosen tile",
                 "Every tile in this set is cropped",
                 "Paint 8×8 tiles",
                 "Pin this sprite to one colour set",
                 "This tile and the 15 after it"),
        "roots": {
            "de": [r"kachel"],
            "el": [r"πλακ"],
            "eo": [r"kahel"],
            "es": [r"baldos", r"mosaic", r"\bpieza", r"\blosa"],
            "fr": [r"tuile"],
            "it": [r"tesser", r"\btile\b", r"casell"],
            "nl": [r"tegel"],
            "pl": [r"kafel"],
            "pt": [r"mosaic", r"peça"],
            "ru": [r"плит", r"тайл"],
            "tr": [r"karo", r"taş", r"döşeme"],
            "yi": [r"פּליטקע", r"קאַפֿל"],
        },
    },
    "sprite": {
        "pattern": r"\bsprites?\b",
        "apps": {"gbasdk.py", "gbahelp.py", "gbabuild.py"},
        "also": ("Pin this sprite to one colour set",),
        "roots": {
            "eo": [r"sprajt", r"rolfigur", r"\bfigur"],
            "pl": [r"dusz", r"postać"],
            "sr": [r"sprajt", r"\bfigur"],
        },
    },
    "frame": {
        "pattern": r"\bframes?\b",
        "apps": {"gbasdk.py", "gbahelp.py", "gbabuild.py"},
        "roots": {
            "sr": [r"kadar|kadr", r"sličic"],
        },
    },
    # The action that turns a project into a .gba. English said BOTH "Build"
    # (menu) and "Compile" (buttons, messages) for it, and every translation
    # inherited the split. English is now Build throughout; each language picks
    # ONE word, which need NOT be a literal translation of "build" -- Spanish
    # "Compilar" used consistently is right, and "Construir" would be worse.
    # The tool itself stays "the compiler" everywhere: that is a different
    # noun, not the action.
    "build": {
        "pattern": r"^(Build|Cannot build|Build .*|.*did not build.*|"
                   r"The build log.*|The working files for the build.*|"
                   r"Loaded the example game.*)$",
        "apps": {"gbasdk.py", "gbahelp.py", "gbabuild.py"},
        "roots": {
            "de": [r"kompilier", r"erstell"],
            "el": [r"μεταγλωττ", r"δημιουργ"],
            "eo": [r"kompil", r"konstru", r"traduk"],
            "es": [r"compil", r"constru"],
            "fr": [r"compil", r"constru"],
            "it": [r"compil", r"costru"],
            "ja": [r"ビルド", r"コンパイル"],
            "ko": [r"빌드", r"컴파일"],
            "nl": [r"compiler", r"bouw"],
            "pl": [r"kompil", r"budow|zbuduj|buduj"],
            "pt": [r"compil", r"constru"],
            "ru": [r"собра|сборк", r"компил"],
            "sr": [r"kompajl", r"izgrad", r"\bpreve"],
            "tr": [r"derle", r"inşa"],
            "yi": [r"קאָמפּיל", r"בוי"],
            "zh": [r"编译", r"构建", r"生成"],
        },
    },
    # "event" is the standing homograph: a diary appointment (calendar, tasks)
    # and a thing a game object reacts to are the same English word and would
    # share one catalog key. gbasdk already renamed its own labels -- OBJECT
    # EVENTS, Add Object Event, No object events -- so only SDK-owned keys are
    # checked here, and Turkish must use `olay` for them, never the calendar's
    # `etkinlik`.
    "event": {
        "pattern": r"\bevents?\b",
        "apps": {"gbasdk.py", "gbahelp.py", "gbabuild.py"},
        "also": ("Start a countdown; fires the Alarm event",
                 "Stop running the rest of this event",
                 "Select an event, then add actions"),
        "roots": {
            "tr": [r"olay", r"etkinlik"],
        },
    },
}


def norm(s):
    return "".join(ch for ch in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(ch) != "Mn")


def owned_by(key, src, apps):
    """True when this key appears as a quoted literal ONLY in `apps`."""
    found = {a for a, literals in src.items() if key in literals}
    return bool(found) and found <= apps


def source_literals(text):
    """Runtime string literals, excluding comments and declaration docstrings."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docs.add(id(first.value))
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docs}


def survey(english, src, pattern, apps):
    """Show every language's ACTUAL word choices for a concept, so a new
    CONCEPTS entry is curated from measured data instead of invented roots.

    This is how the check goes OS-wide without repeating the `ficha` mistake
    (unifying words that correctly differ because the concepts differ): the
    survey scopes keys by OWNING APP, a human reads the real values, and only
    a demonstrated multi-root split becomes a CONCEPTS entry.

        python3 term_consistency_check.py --survey '\\bfolders?\\b' finder.py
    """
    pat = re.compile(pattern, re.I)
    keys = [k for k in english if pat.search(k)]
    if apps:
        keys = [k for k in keys if owned_by(k, src, apps)]
    if not keys:
        print("no keys match%s" % (" in those apps" if apps else ""))
        return 1
    owners = {}
    for k in keys:
        held = {a for a, literals in src.items() if k in literals}
        owners[k] = sorted(held) or ["(not found as a literal)"]
    print("%d keys match %r%s" % (len(keys), pattern,
                                  " owned by %s" % ", ".join(sorted(apps))
                                  if apps else ""))
    for k in sorted(keys):
        print("  %-52s %s" % (k[:52], ",".join(owners[k])[:40]))
    langs = sorted(n[5:-5] for n in os.listdir(DE)
                   if n.startswith("lang_") and n.endswith(".json"))
    for lang in langs:
        try:
            with io.open(os.path.join(DE, "lang_%s.json" % lang),
                         encoding="utf-8") as fh:
                cat = json.load(fh)
        except (OSError, ValueError):
            continue
        vals = sorted({cat.get(k, "«missing»")[:56] for k in keys})
        print("%s:" % lang)
        for v in vals:
            print("    %s" % v)
    return 0


def main():
    try:
        with io.open(os.path.join(DE, "lang_es.json"), encoding="utf-8") as fh:
            english = list(json.load(fh))
    except (OSError, ValueError):
        print("could not read a catalog to take the key set from")
        return 2
    src = {}
    for name in sorted(os.listdir(DE)):
        if name.endswith(".py"):
            with io.open(os.path.join(DE, name), encoding="utf-8",
                         errors="replace") as fh:
                src[name] = source_literals(fh.read())

    if "--survey" in sys.argv:
        i = sys.argv.index("--survey")
        pattern = sys.argv[i + 1]
        apps = set(sys.argv[i + 2:])
        return survey(english, src, pattern, apps)

    problems = 0
    for concept, spec in sorted(CONCEPTS.items()):
        pat = re.compile(spec["pattern"], re.I)
        keys = [k for k in english if pat.search(k)]
        owned = sorted(k for k in keys if owned_by(k, src, spec["apps"]))
        # Keys the source wraps across lines are not findable as one literal;
        # take them by prefix rather than dropping them from the check.
        extra = [k for k in keys if not owned_by(k, src, spec["apps"])
                 and any(k.startswith(p) for p in spec.get("also", ()))]
        keys = sorted(set(owned) | set(extra))
        if not keys:
            print("%s: no owned keys found — the pattern or the app set is "
                  "wrong" % concept)
            problems += 1
            continue
        print("%s: %d keys owned by %s"
              % (concept, len(keys), ", ".join(sorted(spec["apps"]))))
        for lang, roots in sorted(spec["roots"].items()):
            p = os.path.join(DE, "lang_%s.json" % lang)
            try:
                with io.open(p, encoding="utf-8") as fh:
                    cat = json.load(fh)
            except (OSError, ValueError):
                continue
            agreed = roots[0]
            used = {}
            for root in roots:
                count = sum(1 for key in keys
                            if re.search(norm(root), norm(cat.get(key, ""))))
                if count:
                    used[root] = count
            strays = {root: count for root, count in used.items()
                      if root != agreed}
            # Uniformity alone is insufficient: a wholesale new synonym used
            # to match no declared root and therefore passed vacuously.
            if agreed not in used or strays:
                print("  %s: agreed root %r appears %d time(s); alternatives %s"
                      % (lang, agreed, used.get(agreed, 0), strays or "none"))
                problems += 1
    print("\nRESULT: " + ("CONSISTENT" if not problems
                          else "%d language(s) outside agreed terminology"
                               % problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
