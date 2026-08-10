#!/usr/bin/env python3
"""Headless checks for Novel's dedicated chapter-title field and migration."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DE = os.path.abspath(os.path.join(
    HERE, "..", "buildroot", "board", "notebookos", "rootfs-overlay",
    "opt", "notebook", "de"))
DE = os.environ.get("NOVEL_MODULE_DIR", DEFAULT_DE)
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="novel-title-home-"))

import novel  # noqa: E402

FAILURES = []


def check(condition, name):
    print(("ok   " if condition else "FAIL ") + name)
    if not condition:
        FAILURES.append(name)


def legacy(title, body, ranges=None):
    return {"title": "Book", "chapters": [{"num": "1", "title": title,
            "body": body, "ranges": ranges or {}, "part": 0}]}


def parse(doc):
    return novel.Novel._parse_state(None, doc)


def main():
    migrated = parse(legacy("Harbour", "Harbour\nFirst body line",
                            {"bold": [[8, 13]], "heading": [[0, 8]]}))
    ch = migrated["chapters"][0] if migrated else {}
    check(ch.get("body") == "First body line",
          "legacy mirrored heading migrates out of body")
    check(ch.get("ranges", {}).get("bold") == [[0, 5]],
          "migration shifts body formatting without losing it")
    check(not ch.get("ranges", {}).get("heading"),
          "legacy heading-only formatting is removed with heading")

    different = parse(legacy("Stored title", "Different first line\nMore"))
    check(different and different["chapters"][0]["body"] ==
          "Different first line\nMore",
          "differing title and first line are both preserved")
    blank = parse(legacy("Stored title", "\nBody after blank"))
    check(blank and blank["chapters"][0]["body"] == "\nBody after blank",
          "an empty first line is preserved")
    uni = parse(legacy("海辺 — café", "海辺 — café\nnaïve Ελληνικά"))
    check(uni and uni["chapters"][0]["body"] == "naïve Ελληνικά",
          "unicode mirrored headings migrate losslessly")

    current = legacy("Same", "Same\nThis is intentional")
    current["format_version"] = getattr(novel, "NOVEL_FORMAT_VERSION", 2)
    current_state = parse(current)
    check(current_state and current_state["chapters"][0]["body"] ==
          "Same\nThis is intentional",
          "an already-migrated manuscript is never migrated twice")
    check(current_state and current_state.get("format_version") == 2,
          "parsed state carries the dedicated-title format marker")

    count = getattr(novel, "_count_body_words", lambda _s: -1)
    check(count("Title words stay body words") == 5,
          "word counting treats every body line as prose")
    offsets = getattr(novel, "placeholder_offsets", lambda *a: (-1, -1))
    check(offsets(7, 11, 19, 19) == (7, 11),
          "placeholder matches equal-font first-character metrics within 1px")
    check(offsets(3, 5, 20, 18) == (3, 7),
          "placeholder baseline compensates differing font ascents")
    context = novel.PangoCairo.FontMap.get_default().create_context()
    body_metrics = context.get_metrics(
        novel.Pango.FontDescription("Liberation Serif 15"), None)
    ghost_metrics = context.get_metrics(
        novel.Pango.FontDescription("Liberation Serif Italic 15"), None)
    scale = novel.Pango.SCALE
    ba = (body_metrics.get_ascent() + scale - 1) // scale
    ga = (ghost_metrics.get_ascent() + scale - 1) // scale
    _x, ghost_top = offsets(2, 0, ba, ga)
    check(abs((ghost_top + ga) - ba) <= 1,
          "headless Pango metrics put ghost and typed baselines within 1px")

    source = open(os.path.join(DE, "novel.py"), encoding="utf-8").read()
    check("self.chapter_title" in source and "nvchaptertitle" in source,
          "chapter title is a dedicated editable field")
    check("nbi18n.set_verbatim(self.chapter_title" in source,
          "user chapter titles are protected from translation")
    check('"format_version": NOVEL_FORMAT_VERSION' in source,
          "every save writes the migration marker")
    check("ch[\"title\"] = first" not in source,
          "body edits cannot overwrite chapter title content")

    with tempfile.TemporaryDirectory(prefix="novel-damage-") as td:
        path = os.path.join(td, "novel.json")
        damaged = b'{"chapters":[{"title":"irreplaceable"}'
        open(path, "wb").write(damaged)
        old_path = novel.NOVEL_FILE
        novel.NOVEL_FILE = path
        fixture = novel.Novel.__new__(novel.Novel)
        fixture._store_read_only = False
        try:
            loaded = fixture._load_state()
        finally:
            novel.NOVEL_FILE = old_path
        recovered = [p for p in os.listdir(td) if ".damaged-" in p]
        check(loaded is None and fixture._store_read_only,
              "damaged session recovery enters read-only mode")
        check(len(recovered) == 1 and
              open(os.path.join(td, recovered[0]), "rb").read() == damaged,
              "damaged session recovery preserves the original bytes")

    # PASS-MUTANT: the suite must go red against a scratch copy with the
    # migration marker sabotaged; NOVEL_MODULE_DIR makes this tree-safe.
    if os.environ.get("NOVEL_MUTANT_CHILD") != "1" and not FAILURES:
        with tempfile.TemporaryDirectory(prefix="novel-title-mutant-") as td:
            shutil.copytree(DE, td, dirs_exist_ok=True)
            mp = os.path.join(td, "novel.py")
            text = open(mp, encoding="utf-8").read()
            text = text.replace('"format_version": NOVEL_FORMAT_VERSION',
                                '"format_version": 999', 1)
            open(mp, "w", encoding="utf-8").write(text)
            env = dict(os.environ, NOVEL_MODULE_DIR=td, NOVEL_MUTANT_CHILD="1")
            proc = subprocess.run([sys.executable, __file__], env=env,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
            check(proc.returncode != 0 and
                  "every save writes the migration marker" in proc.stdout,
                  "PASS-MUTANT rejects a sabotaged scratch module by name")

    print()
    if FAILURES:
        print("NOVEL TITLE SELFTEST: %d FAILED" % len(FAILURES))
        return 1
    print("NOVEL TITLE SELFTEST: all pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
