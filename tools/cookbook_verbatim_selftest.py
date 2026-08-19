#!/usr/bin/env python3
"""Headless regression for user-authored Cookbook sidebar text."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import cookbook  # noqa: E402


class Label:
    def __init__(self): self.text = ""
    def set_text(self, text): self.text = text


calls = []
real_v = cookbook.nbi18n.set_verbatim
real_t = cookbook._t
cookbook.nbi18n.set_verbatim = lambda label, text: (
    calls.append(text), label.set_text(text))[-1]
cookbook._t = lambda text: {"Untitled recipe": "無題のレシピ"}.get(text, text)
try:
    authored = Label(); cookbook._set_recipe_text(
        authored, "Save", "Untitled recipe")
    empty = Label(); cookbook._set_recipe_text(empty, "", "Untitled recipe")
finally:
    cookbook.nbi18n.set_verbatim = real_v
    cookbook._t = real_t

checks = [
    (authored.text == "Save" and calls == ["Save"],
     "authored title is stamped verbatim"),
    (empty.text == "無題のレシピ", "empty-title fallback is translated"),
]
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
# Terminal verdict for the release runner (run_all_gates SUCCESSWORD): a stream
# of PASS lines with a zero exit is not a report it will trust.
_ok = all(ok for ok, _name in checks)
print("RESULT: %s" % ("ALL PASS" if _ok else "FAILED"))
raise SystemExit(not _ok)
