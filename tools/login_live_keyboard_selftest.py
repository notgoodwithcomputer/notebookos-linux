#!/usr/bin/env python3
"""Login's keyboard chip must describe the X server's actual layout."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import login  # noqa: E402


def setup(live):
    obj = login.Login.__new__(login.Login)
    kb, i18n = login.nbkeyboard, login.nbi18n
    old_live, old_keyboard, old_login = kb.live_code, i18n.keyboard, i18n.login_keyboard
    try:
        kb.live_code = lambda: live
        i18n.keyboard = lambda: "ru,us"
        i18n.login_keyboard = lambda: ""
        obj._setup_keyboard()
    finally:
        kb.live_code, i18n.keyboard, i18n.login_keyboard = old_live, old_keyboard, old_login
    return obj


def main() -> None:
    fallback = setup("us")
    assert fallback._kb_code == "us" and fallback._kb_groups == [("us", "")]
    loaded = setup("ru,us")
    assert loaded._kb_code == "ru,us" and len(loaded._kb_groups) == 2
    print("PASS Login reports the live layout after session fallback")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
