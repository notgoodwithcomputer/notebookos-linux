#!/usr/bin/env python3
"""Regression: Language refreshes daily XP across midnight."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import language  # noqa: E402


class Stack:
    def __init__(self, page):
        self.page = page

    def get_visible_child_name(self):
        return self.page


def main():
    app = language.Language.__new__(language.Language)
    app._closed = False
    app.progress = {"day": "2026-08-15", "day_xp": 30}
    app.stack = Stack("home")
    calls = {"save": 0, "home": 0}
    app._save_progress = lambda: calls.__setitem__("save", calls["save"] + 1)
    app._refresh_home_stats = lambda: calls.__setitem__(
        "home", calls["home"] + 1)
    old_today = language._today
    try:
        language._today = lambda: "2026-08-16"
        assert app._check_day_rollover() is True
        assert app.progress["day"] == "2026-08-16"
        assert app.progress["day_xp"] == 0
        assert calls == {"save": 1, "home": 1}
        assert app._check_day_rollover() is True
        assert calls == {"save": 1, "home": 1}
        app._closed = True
        assert app._check_day_rollover() is False
    finally:
        language._today = old_today
    print("PASS midnight rollover saves and refreshes exactly once")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
