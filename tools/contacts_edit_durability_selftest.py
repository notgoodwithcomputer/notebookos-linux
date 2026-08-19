#!/usr/bin/env python3
"""Headless regression for failed Contacts edit commits."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import contacts  # noqa: E402


class Entry:
    def __init__(self, text):
        self.text = text

    def get_text(self):
        return self.text


def bare(save_ok):
    app = contacts.Contacts.__new__(contacts.Contacts)
    app.people = [{"name": "Old Name", "role": "Friend", "phones": [],
                   "emails": [], "notes": "kept"}]
    app.active = 0
    app.editing = True
    app._entries = {"name": Entry("New Name"), "role": Entry("Colleague")}
    app._notes_view = None
    app._pending_new = False
    app._save = lambda: save_ok
    app.events = []
    app._finish_new_card = lambda: app.events.append("finish")
    app._rebuild_detail = lambda: app.events.append("detail")
    app._rebuild_list = lambda: app.events.append("list")
    return app


app = bare(False)
person = app.people[0]
app._toggle_edit()
assert app.editing is True and app.people[0] is person
assert person["name"] == "Old Name" and app.events == [], (person, app.events)
print("PASS failed contact edit restores identity and keeps editor open")

app = bare(True)
app._toggle_edit()
assert app.editing is False and app.people[0]["name"] == "New Name"
assert app.events == ["finish", "detail", "list"], app.events
print("PASS durable contact edit exits editor and rebuilds normally")
print("RESULT: PASS")
