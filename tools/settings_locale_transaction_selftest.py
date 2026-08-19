#!/usr/bin/env python3
"""Region choices save atomically and never claim failed persistence."""

from pathlib import Path
import json
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import nbi18n  # noqa: E402
import settings  # noqa: E402


class Combo:
    def __init__(self, active):
        self.active = active
    def get_active(self):
        return self.active
    def set_active(self, value):
        self.active = value


class Note:
    def __init__(self):
        self.text = ""
    def set_text(self, text):
        self.text = text
    def set_visible(self, _visible):
        pass


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        os.environ["NB_HOME"] = td
        assert nbi18n.set_locale("fr", "fr")
        data = json.loads((Path(td) / ".config/notebook/locale.json").read_text())
        assert data["lang"] == "fr" and data["keyboard"] == "fr"

    app = settings.Settings.__new__(settings.Settings)
    app._region_lang_codes = ["en", "fr"]
    app._region_kb_codes = ["us", "fr"]
    app._region_note = Note()
    combo = Combo(1)
    applied = []
    app._apply_keyboard = lambda code: (applied.append(code) or True)
    old_set, old_current, old_keyboard = (nbi18n.set_locale,
                                          nbi18n.current_lang,
                                          nbi18n.keyboard)
    try:
        nbi18n.set_locale = lambda *_a, **_k: False
        nbi18n.current_lang = lambda: "en"
        nbi18n.keyboard = lambda: "us"
        app._on_region_lang(combo)
    finally:
        nbi18n.set_locale, nbi18n.current_lang, nbi18n.keyboard = (old_set,
            old_current, old_keyboard)
    assert combo.active == 0 and applied == ["fr", "us"]
    assert "could not" in app._region_note.text.lower()
    print("PASS locale pairs save atomically and failed choices are rolled back")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
