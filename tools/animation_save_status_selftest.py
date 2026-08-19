#!/usr/bin/env python3
"""Successful Animation saves replace stale editing/recovery status."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import animation  # noqa: E402


class Chip:
    def __init__(self, text): self.text = text
    def set_text(self, text): self.text = text


def app(path, stale):
    obj = animation.Animation.__new__(animation.Animation)
    obj.doc = object(); obj.doc_path = path
    obj._dirty = obj._doc_dirty = True
    obj._save_error = None; obj.save_chip = Chip(stale)
    obj.set_title = lambda _title: None
    def autosave():
        obj._dirty = False
        obj.save_chip.set_text('Saved 12:34')
        return False
    obj._autosave = autosave
    return obj


def main():
    old_save, old_picker = animation.save_document, animation.nbpicker.save_file
    try:
        animation.save_document = lambda _doc, _path: None
        first = app('/tmp/film.anim', 'Editing')
        assert first._save() and first.save_chip.text.startswith('Saved ')
        second = app(None, 'Not saved to file')
        animation.nbpicker.save_file = lambda *_a, **_k: '/tmp/new.anim'
        assert second._save_as() and second.save_chip.text.startswith('Saved ')
        assert not first._dirty and not first._doc_dirty
        assert not second._dirty and not second._doc_dirty
        print("PASS Save and Save As replace stale status with saved time")
        print("RESULT: PASS")
        return 0
    finally:
        animation.save_document, animation.nbpicker.save_file = old_save, old_picker


if __name__ == '__main__':
    raise SystemExit(main())
