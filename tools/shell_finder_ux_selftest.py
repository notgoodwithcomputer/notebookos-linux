#!/usr/bin/env python3
"""Display-free acceptance checks for Finder navigation coherence."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import finder  # noqa: E402
import nbstate  # noqa: E402
import nbtransitions  # noqa: E402


def check(condition, label):
    print(("ok  " if condition else "FAIL") + label)
    if not condition:
        raise AssertionError(label)


def history_contract():
    history, pos = finder.push_history(["Home", "Documents", "Pictures"],
                                       1, "Music")
    check(history == ["Home", "Documents", "Music"] and pos == 2,
          "new navigation after Back drops the Forward branch")
    same, same_pos = finder.push_history(history, pos, "Music")
    check(same == history and same_pos == pos,
          "reopening the current folder does not duplicate history")
    check(finder.nav_direction(2, 1) == nbtransitions.BACK and
          finder.nav_direction(1, 2) == nbtransitions.FORWARD and
          finder.nav_direction(1, 1) == nbtransitions.CROSSFADE,
          "Back, Forward and refresh use the shared direction vocabulary")
    check(finder.restores_place(nbtransitions.BACK) and
          finder.restores_place(nbtransitions.FORWARD) and
          finder.restores_place(nbtransitions.CROSSFADE) and
          not finder.restores_place(nbtransitions.NONE),
          "returns restore place while fresh arrivals start at the top")


def stale_refresh_contract():
    gen = nbstate.Generation("finder")
    delivered = []
    old = gen.guard(lambda: delivered.append("old folder"))
    gen.bump()
    current = gen.guard(lambda: delivered.append("current folder"))
    check(old() is False and delivered == [],
          "a monitor callback from the previous folder is rejected")
    current()
    gen.close()
    after_close = gen.guard(lambda: delivered.append("closed"))
    check(after_close() is False and delivered == ["current folder"],
          "destroy invalidates Finder callbacks still in flight")


def wiring_contract():
    path = DE / "finder.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path))
    required = (
        "model.get_value(it, 4)",       # stable relative path, not row index/name
        "row[4] == selected",
        "self._dirgen.valid(token)",
        "self._nav_dir = nav_direction(old, self._hpos)",
        "self._clear_typeahead()",
    )
    missing = [fragment for fragment in required if fragment not in source]
    # Destroy must CLOSE the dirgen. The teardown was refactored to a
    # getattr-guarded spelling (safe against destroy-mid-construction), so
    # accept either form instead of pinning one exact source string — the
    # navigation_state gate already learned this brittleness lesson.
    closes = ("self._dirgen.close()" in source
              or ('getattr(self, "_dirgen", None)' in source
                  and "dirgen.close()" in source))
    if not closes:
        missing.append("dirgen.close() on destroy")
    check(not missing,
          "Finder wires stable selection, stale guards, direction and local Escape"
          + ("" if not missing else " [missing: %s]" % ", ".join(missing)))


if __name__ == "__main__":
    history_contract()
    stale_refresh_contract()
    wiring_contract()
    print("shell/Finder UX selftest: OK")
    print("RESULT: PASS")
