#!/usr/bin/env python3
"""Display-free regression for Save dialog default-extension handling."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import nbpicker  # noqa: E402


class Entry:
    def __init__(self, text): self.text = text
    def get_text(self): return self.text
    def grab_focus(self): pass


class Picker:
    _commit_save = nbpicker._Picker._commit_save
    _save_dir_safe = nbpicker._Picker._save_dir_safe
    default_ext = ".writer"
    cur = "/documents"
    def __init__(self, name, cur=None):
        self.name_entry = Entry(name)
        self.result = None
        self.cur = cur or self.cur
        self.warning = ""
        self.warn = type("Warn", (), {})()
        self.warn.set_text = lambda text: setattr(self, "warning", text)
    def _confirm_replace(self, _name): return True
    def _finish(self, path): self.result = path


cases = (("Notes", "/documents/Notes.writer"),
         ("Notes.", "/documents/Notes.writer"),
         ("Notes.txt", "/documents/Notes.txt"))
failed = 0
for name, expected in cases:
    picker = Picker(name)
    picker._commit_save()
    ok = picker.result == expected
    print(("PASS " if ok else "FAIL ") + repr(name) + " -> "
          + repr(picker.result))
    failed += not ok

with tempfile.TemporaryDirectory(prefix="picker-link-") as folder:
    os.symlink(os.path.join(folder, "missing-target"),
               os.path.join(folder, "Notes.writer"))
    picker = Picker("Notes", folder)
    picker._commit_save()
    ok = picker.result is None and bool(picker.warning)
    print(("PASS " if ok else "FAIL ")
          + "a dangling symlink name is refused without returning its target")
    failed += not ok
print("RESULT: %s" % ("PASS" if not failed else "FAILED"))
raise SystemExit(failed)
