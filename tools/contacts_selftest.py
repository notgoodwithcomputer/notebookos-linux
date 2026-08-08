#!/usr/bin/env python3
"""
Headless selftest for the Contacts app (contacts.py).

Contacts is a plain address book: each card carries name, role, and the
free-text fields phone / email / address / birthday (bday) / notes, plus a
palette colour. There is NO radio / mesh / LoRa metadata on a card (the old
'node' / 'heard' fields were removed) — this test asserts on the CURRENT
model only.

It drives the real app methods headlessly and checks state directly:
  1. Add — "New Contact" grows self.people, selects the new card, enters edit
     mode, and the row shows up in the list pane.
  2. Edit — edit mode shows Gtk.Entry widgets for the tracked fields; typing a
     value and leaving edit mode writes it back into the person dict and
     re-renders the fields as labels. A tracked field (phone) round-trips; the
     removed LoRa fields are absent from the editable set.
  3. Delete — _do_delete removes the active card.
  4. Persist — the book auto-saves to $NB_HOME/.config/notebook/contacts.json;
     a freshly constructed second instance reloads the saved card, and a third
     instance (built after the delete) confirms the deletion stuck.

Run as:
  DISPLAY=:0 \
  PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de \
  NB_HOME=/tmp/ct python3 contacts_selftest.py
"""
import inspect
import os
import sys
import tempfile

# PIN NB_HOME BEFORE IMPORTING THE APP. Two things went wrong without it, both
# silent. (1) This suite really saves and reloads contacts.json, so run without
# NB_HOME it overwrote the CALLER'S OWN ~/.config/notebook/contacts.json --
# on the machine (NB_HOME=/root) that is the user's real address book.
# (2) An unset NB_HOME also puts the single-instance guard on the unscoped
# /tmp/nb-apps shared with any running app: nbapp.claim_single_instance() then
# os._exit(0)s this process with NO output and EXIT STATUS 0, which reads as a
# pass while nothing was tested.
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="contacts-selftest-"))

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import contacts as mod

results = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    results.append(bool(ok))


def walk(w):
    yield w
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            yield from walk(c)


def find_by_class(root, cls_name):
    return [w for w in walk(root)
            if w.get_style_context().has_class(cls_name)]


def entries_in(root):
    return [w for w in walk(root) if isinstance(w, Gtk.Entry)]


def main():
    # Start from a known-empty store so the run is deterministic and idempotent
    # regardless of any leftover book from an earlier run.
    try:
        os.remove(mod.CONTACTS_FILE)
    except OSError:
        pass

    # Locate the AppWindow subclass defined in contacts.py.
    win_cls = None
    for _n, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            win_cls = c
            break
    if win_cls is None:
        print("FAIL locate-window-class")
        print("RESULT: SOME FAILED")
        sys.exit(1)
    check("locate-window-class", win_cls is not None)

    win = win_cls()

    # --- 1. Add a contact ----------------------------------------------
    # The book ships EMPTY (no seeded contacts) — the user builds it.
    check("starts-empty", len(win.people) == 0)
    n_before = len(win.people)
    win._new_contact()
    check("new-contact-grows-people", len(win.people) == n_before + 1)
    check("new-contact-selected", win.active == len(win.people) - 1)
    check("new-contact-enters-edit-mode", win.editing is True)
    rows = find_by_class(win.list_box, "contactrow")
    check("new-contact-row-in-list", len(rows) == n_before + 1)

    # --- 2. Edit mode is really editable -------------------------------
    ents = entries_in(win.detail_holder)
    check("edit-mode-shows-entries", len(ents) > 0)
    # A real tracked field is present in the live editable set...
    check("editable-field-tracked", "phones" in win._entries)
    check("name-editable", "name" in win._entries)
    # ...notes is a multi-line area (Gtk.TextView), not one of the single-line
    # Entry fields, so it lives in win._notes_view — NOT in win._entries.
    check("notes-editable", win._notes_view is not None)
    # ...and the removed LoRa-era fields are NOT part of the model anymore.
    check("no-lora-node-field", "node" not in win._entries)
    check("no-lora-heard-field", "heard" not in win._entries)

    # Type into the entries, then leave edit mode -> must persist into the dict.
    win._entries["name"].set_text("Alice Test")
    win._entries["phones"].set_text("mobile: 555-0100")
    win._notes_view.get_buffer().set_text("coffee friend")
    win._toggle_edit()  # Done
    p = win.people[win.active]
    check("leave-edit-writes-name", p.get("name") == "Alice Test")
    check("leave-edit-writes-phone", p.get("phones") == [
          {"label": "mobile", "value": "555-0100"}])
    check("leave-edit-writes-notes", p.get("notes") == "coffee friend")
    check("left-edit-mode", win.editing is False)
    check("labels-after-done", len(entries_in(win.detail_holder)) == 0)

    # Re-enter edit, change a tracked field again, and confirm it round-trips.
    win._toggle_edit()  # Edit
    check("re-enter-edit-shows-entries",
          win.editing is True and len(entries_in(win.detail_holder)) > 0)
    win._entries["phones"].set_text("work: 555-0200")
    win._toggle_edit()  # Done
    check("edit-tracked-field-persists",
          win.people[win.active].get("phones") == [
              {"label": "work", "value": "555-0200"}])

    # --- 3. No Message button: the Messages app was removed ------------
    # Contacts used to carry a "Message" action that launched messages.py over
    # the LoRa radio. Both the app and the radio were cut from the product, so
    # the card must NOT offer an action that cannot work. Asserted the other way
    # round now, so the button can never quietly come back.
    check("no-message-button", not find_by_class(win.detail_holder, "msgbtn"))

    # --- 4a. Persistence round-trip: the saved card reloads ------------
    win2 = win_cls()
    check("persistence-loads-contact", len(win2.people) == 1)
    if win2.people:
        loaded = win2.people[0]
        check("persistence-preserves-name", loaded.get("name") == "Alice Test")
        check("persistence-preserves-phone", loaded.get("phones") == [
              {"label": "work", "value": "555-0200"}])
        check("persistence-no-node-key", "node" not in loaded)
    else:
        check("persistence-preserves-name", False)
        check("persistence-preserves-phone", False)
        check("persistence-no-node-key", False)

    # --- 5. Delete the active card, and confirm the delete persists ----
    win._do_delete()
    check("delete-removes-contact", len(win.people) == 0)
    win3 = win_cls()
    check("delete-persists", len(win3.people) == 0)

    ok = all(results)
    print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
