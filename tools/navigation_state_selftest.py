#!/usr/bin/env python3
"""Headless acceptance checks for navigation/restoration state."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import nbstate  # noqa: E402


def check(condition, label):
    print(("ok  " if condition else "FAIL") + label)
    if not condition:
        raise AssertionError(label)


def generation_contract():
    generation = nbstate.Generation("switch")
    delivered = []
    a = generation.guard(lambda: delivered.append("A"))
    a_token = generation.token()
    b_token = generation.bump()
    b = generation.guard(lambda: delivered.append("B"))
    c_token = generation.bump()
    c = generation.guard(lambda: delivered.append("C"))
    check((a_token, b_token, c_token) == (0, 1, 2),
          "generation tokens increase monotonically")
    check(a() is False and b() is False and delivered == [],
          "rapid A to B to C switching rejects A and B callbacks")
    check(c() is False and delivered == ["C"],
          "only the current restoration callback is delivered")
    queued = generation.guard(lambda: delivered.append("after close"))
    generation.close()
    check(queued() is False and delivered == ["C"],
          "closing an owner invalidates queued restoration")
    check(not generation.valid(generation.token()),
          "future tokens remain invalid after close")


def restoration_scope_contract():
    scope = nbstate.RestoreScope()
    saves = []

    def maybe_save():
        if not scope.active:
            saves.append("saved")

    with scope:
        maybe_save()
        with scope:
            maybe_save()
        check(scope.active and scope.depth == 1,
              "nested restoration remains active until its outer exit")
    maybe_save()
    check(saves == ["saved"],
          "restoration suppresses save effects but later edits still save")
    try:
        with scope:
            raise RuntimeError("fixture")
    except RuntimeError:
        pass
    check(not scope.active, "an exception cannot leave restoration stuck on")


def safe_state_contract():
    check(nbstate.choice("missing", ["songs", "albums"], "songs") == "songs",
          "unknown persisted pane falls back safely")
    check(nbstate.choice(None, [], None) is None,
          "missing choices do not raise")
    rows = [{"id": "beta"}, {"id": "alpha"}, {"id": "gamma"}]
    check(nbstate.identity_index(rows, "alpha", key=lambda row: row["id"]) == 1,
          "selection restoration follows stable identity after reordering")
    check(nbstate.identity_index(rows, "gone", key=lambda row: row["id"]) == -1,
          "missing selection identity has an explicit fallback")
    check(nbstate.clamp_index("99", 3) == 2 and
          nbstate.clamp_index("bad", 3) == 0,
          "damaged and stale indices are safely bounded")
    check(nbstate.fraction(float("nan"), .25) == .25 and
          nbstate.fraction(4) == 1.0,
          "damaged scroll fractions fall back or clamp")


def wiring_contract():
    ebook_path = DE / "ebook.py"
    music_path = DE / "music.py"
    ebook = ebook_path.read_text(encoding="utf-8")
    music = music_path.read_text(encoding="utf-8")
    ast.parse(ebook, filename=str(ebook_path))
    ast.parse(music, filename=str(music_path))
    check("self._nav.close()" in ebook and
          "self._nav.valid(token)" in ebook and
          "self._nav.guard(self._scroll_top)" in ebook,
          "Ebook guards delayed scrolls and invalidates them on destroy")
    check("nbstate.identity_index(self._playlists, name)" in music and
          "with self._restoring:" in music and
          "if self._restoring.active:" in music,
          "Music restores by identity without saving the restoration")


if __name__ == "__main__":
    generation_contract()
    restoration_scope_contract()
    safe_state_contract()
    wiring_contract()
    print("navigation state selftest: OK")
