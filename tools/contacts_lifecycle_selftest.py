#!/usr/bin/env python3
"""Headless ownership checks for Contacts debounce/status callbacks."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
import contacts  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class Label:
    def __init__(self):
        self.text = "notice"
        self.visible = True

    def set_text(self, text):
        self.text = text

    def hide(self):
        self.visible = False


win = contacts.Contacts.__new__(contacts.Contacts)
win._closed = False
win._search_timer = 41
win.rebuilds = 0
win._rebuild_list = lambda: setattr(win, "rebuilds", win.rebuilds + 1)
check(win._search_timeout() is False and win._search_timer == 0
      and win.rebuilds == 1,
      "a live debounce rebuilds once and unregisters itself")

win._status_timer = 42
win.status_lbl = Label()
check(win._clear_status() is False and win._status_timer == 0
      and win.status_lbl.text == "" and not win.status_lbl.visible,
      "a live status timeout clears and hides its line")

win._closed = True
win._search_timer = 43
win._status_timer = 44
win.status_lbl = None  # any widget access would raise
check(win._search_timeout() is False and win.rebuilds == 1,
      "a closed debounce touches no list widgets")
check(win._clear_status() is False,
      "a closed status callback touches no label")


win = contacts.Contacts.__new__(contacts.Contacts)
win._closed = False
win._search_timer = 51
win._status_timer = 52
win.editing = True
events = []
win._commit_edits = lambda: events.append(("commit", win._closed))
win._finish_new_card = lambda: events.append(("finish", win._closed))
win._save = lambda: events.append(("save", win._closed))
removed = []
real_remove = contacts.GLib.source_remove
contacts.GLib.source_remove = lambda source_id: removed.append(
    (source_id, win._closed))
try:
    win._on_destroy()
    win._on_destroy()
finally:
    contacts.GLib.source_remove = real_remove

check(win._closed is True, "destroy marks Contacts closed first")
check(removed == [(51, True), (52, True)],
      "destroy removes each owned source exactly once after closing the gate")
check(win._search_timer == win._status_timer == 0,
      "destroy clears both source IDs")
check(events == [("commit", True), ("finish", True), ("save", True)],
      "close commits the edit, drops blank new card, and saves exactly once")

print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
