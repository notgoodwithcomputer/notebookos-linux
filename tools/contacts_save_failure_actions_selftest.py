#!/usr/bin/env python3
"""Headless regression for Contacts mutation commit boundaries."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import contacts  # noqa: E402


class Probe:
    _do_delete = contacts.Contacts._do_delete
    _undo_delete = contacts.Contacts._undo_delete
    _toggle_favorite = contacts.Contacts._toggle_favorite
    # Undo now drops an untouched New Contact placeholder before it restores,
    # the way selecting another card does, so it needs these two as well.
    # (_pending_new is False in this probe, so the run stops at the first
    # test and neither of them touches the store.)
    _finish_new_card = contacts.Contacts._finish_new_card
    _is_blank = contacts.Contacts._is_blank
    _sync_favorite_button = contacts.Contacts._sync_favorite_button
    def __init__(self):
        self.people = [{"name": "Ada", "favorite": False}]
        self.active = 0; self._deleted = None; self.editing = False
        self._pending_new = False; self.flashes = []
    def _save(self): return False
    def _rebuild_list(self): pass
    def _rebuild_detail(self):
        self._favorite_button = FocusButton(True)
    def _flash(self, text): self.flashes.append(text)


class FocusButton:
    def __init__(self, focused=False):
        self.focused = focused
        self.grabbed = False
    def has_focus(self): return self.focused
    def grab_focus(self): self.grabbed = True


app = Probe(); person = app.people[0]
app._do_delete()
delete_ok = app.people == [person] and app._deleted is None and not app.flashes
app._toggle_favorite()
favorite_ok = not app.people[0]["favorite"] and not app.flashes
focused = FocusButton(True)
app._toggle_favorite(focused)
favorite_focus_ok = (not app.people[0]["favorite"]
                     and app._favorite_button.grabbed)
app._deleted = (0, dict(person)); app.people = []
app._undo_delete()
undo_ok = app.people == [] and app._deleted is not None and not app.flashes

for ok, name in ((delete_ok, "failed delete rolls back without success"),
                 (favorite_ok, "failed favorite toggle rolls back"),
                 (favorite_focus_ok,
                  "failed favorite toggle restores replacement focus"),
                 (undo_ok, "failed restore rolls back without success")):
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all((delete_ok, favorite_ok,
                                      favorite_focus_ok, undo_ok)) else "FAILED"))
raise SystemExit(not all((delete_ok, favorite_ok, favorite_focus_ok, undo_ok)))
