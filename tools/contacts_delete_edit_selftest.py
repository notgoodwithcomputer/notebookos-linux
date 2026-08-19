#!/usr/bin/env python3
"""Headless regression for deleting a contact while its form is being edited."""
import copy
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="contacts-delete-"))
import contacts as c  # noqa: E402


class Entry:
    def __init__(self, text):
        self.text = text

    def get_text(self):
        return self.text


original = c.normalize_person({"name": "Old name", "role": "Friend"})


def make_window(save):
    win = c.Contacts.__new__(c.Contacts)
    win.people = [copy.deepcopy(original)]
    win.active = 0
    win.editing = True
    win._pending_new = False
    win._deleted = None
    win._entries = {"name": Entry("New name"), "role": Entry("Colleague")}
    win._notes_view = None
    win._save = save
    win._rebuild_list = lambda: None
    win._rebuild_detail = lambda: None
    win._flash = lambda _message: None
    return win


save_calls = []
failed = make_window(lambda: save_calls.append(1) and False)
failed._do_delete()
assert len(save_calls) == 1, "a failed edit commit must not attempt deletion save"
assert failed.people[0]["name"] == "Old name"
assert failed._entries["name"].get_text() == "New name"
assert failed.editing is True and failed._deleted is None

win = c.Contacts.__new__(c.Contacts)
win = make_window(lambda: True)

win._do_delete()
assert win.people == [], "delete should remove the active contact"
assert win._deleted[1]["name"] == "New name", \
    "undo snapshot must contain the visible edit"
assert win._deleted[1]["role"] == "Colleague", \
    "all visible fields must be committed before snapshotting"

win._undo_delete()
assert win.people[0]["name"] == "New name"
assert win.people[0]["role"] == "Colleague"
print("PASS contacts delete preserves in-progress edits for undo")
print("PASS contacts delete stops when the in-progress edit cannot be saved")
print("RESULT: PASS")
