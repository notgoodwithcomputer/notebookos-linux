#!/usr/bin/env python3
"""Contacts: what Import and Export do to the card that is open, and what a
vCard carries out of the book.

Every check here is named after the behaviour it guards and was watched go RED
against the code as it stood before the fix beside it:

  import-while-editing-keeps-typed-text
        File ▸ Import vCard rebuilt both panes with the form still open, so
        everything typed into the card and not yet committed was destroyed —
        and the form was then re-pointed at whatever card sat at index 0, in
        edit mode, over somebody else's record. (DATA LOSS)
  import-drops-an-untouched-new-contact
        importing while a blank New Contact was open left that blank behind
        for good: self.active moved off it, so nothing ever came back to drop
        it, and it survived to the store as a permanent "Unnamed" row.
  export-vcard-while-editing-writes-what-is-on-screen
        Export to PDF and Print both commit the open form first; Export vCard
        did not, so the file carried the values from BEFORE the edit.
  vcard-export-carries-the-role
        export_vcards wrote no TITLE, so Export All vCards — the only
        whole-book copy this app writes that reads back in — silently dropped
        what every person in the book DOES. (DATA LOSS)
  vcard-import-reads-title-as-the-role
        ...and a card written by any other program carrying TITLE: imported
        with an empty Role.

Run:  tools/guestrun.sh python3 tools/contacts_vcard_edits_selftest.py
"""
import os
import sys
import json
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, DE)

# A private NB_HOME before the app is imported: this suite really saves, and an
# unset NB_HOME would write over the caller's own address book AND put the
# single-instance guard on the shared registry, where nbapp os._exit(0)s with
# no output and status 0 — a pass that tested nothing.
WORK = tempfile.mkdtemp(prefix="contacts-vcard-edits-")
os.environ["NB_HOME"] = os.path.join(WORK, "home")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")

import nbapp  # noqa: E402
nbapp._APP_DIR = os.path.join(WORK, "nb-apps")
nbapp.APP_DIR = nbapp._APP_DIR
os.makedirs(nbapp._APP_DIR, exist_ok=True)

import nbpicker  # noqa: E402
import contacts as con  # noqa: E402

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def section(name):
    def wrap(fn):
        try:
            fn()
        except Exception as exc:                                  # noqa: BLE001
            import traceback
            check(name, False, "raised %s: %s\n%s"
                  % (type(exc).__name__, exc, traceback.format_exc()))
        return fn
    return wrap


def card(name, **kw):
    return con.normalize_person(dict(kw, name=name))


def seed(*people):
    os.makedirs(con.CFG_DIR, exist_ok=True)
    nbapp.atomic_write_json(con.CONTACTS_FILE, {"people": list(people)})
    return con.Contacts()


def stored():
    with open(con.CONTACTS_FILE) as fh:
        return json.load(fh)["people"]


def offer(path):
    """What the file picker hands back for the next Import / Export."""
    nbpicker.open_file = lambda *a, **k: path
    nbpicker.save_file = lambda *a, **k: path


def write_vcf(name, text):
    path = os.path.join(WORK, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


ONE_NEW = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Priya Raman\r\n"
           "TEL;TYPE=CELL:07700 900333\r\nEND:VCARD\r\n")


# ===================================================================== V1
@section("import-while-editing-keeps-typed-text")
def _v1():
    win = seed(card("Amy Pond"), card("Rory Williams", role="Nurse"))
    win.active = 1
    win._toggle_edit()
    win._entries["name"].set_text("Rory Williams MBBS")
    win._entries["organization"].set_text("Leadworth Hospital")
    offer(write_vcf("one-new.vcf", ONE_NEW))
    win._import_vcard()
    rory = next((p for p in win.people if p["name"].startswith("Rory")), {})
    check("import-while-editing-keeps-typed-text",
          rory.get("name") == "Rory Williams MBBS"
          and rory.get("organization") == "Leadworth Hospital",
          "record is %r" % ((rory.get("name"), rory.get("organization")),))
    check("...and to the store, not just to memory",
          any(p["name"] == "Rory Williams MBBS" for p in stored()),
          [p["name"] for p in stored()])
    check("import-leaves-the-read-view-not-a-form-on-another-card",
          win.editing is False
          and win.people[win.active].get("name") == "Rory Williams MBBS",
          "editing=%s active=%r" % (win.editing,
                                    win.people[win.active].get("name")))
    check("import-brought-the-new-card-in-anyway",
          any(p["name"] == "Priya Raman" for p in win.people),
          [p["name"] for p in win.people])
    win.destroy()


# ===================================================================== V2
@section("import-drops-an-untouched-new-contact")
def _v2():
    win = seed(card("Amy Pond"))
    win._new_contact()                       # the blank card the + button makes
    offer(write_vcf("one-new-2.vcf", ONE_NEW))
    win._import_vcard()
    check("import-drops-an-untouched-new-contact",
          all(p["name"] for p in win.people),
          [p["name"] for p in win.people])
    win.destroy()
    check("...so no blank row survives to the store",
          all(p["name"] for p in stored()), [p["name"] for p in stored()])


# ===================================================================== V3
@section("export-vcard-while-editing-writes-what-is-on-screen")
def _v3():
    win = seed(card("Amy Pond", role="Kissogram"))
    win._toggle_edit()
    win._entries["name"].set_text("Amelia Pond")
    win._entries["role"].set_text("Companion")
    path = os.path.join(WORK, "one-card.vcf")
    offer(path)
    win._export_vcard(False)
    text = open(path, encoding="utf-8").read()
    check("export-vcard-while-editing-writes-what-is-on-screen",
          "FN:Amelia Pond" in text, text)
    check("...including the role that was typed",
          "TITLE:Companion" in text, text)
    win.destroy()


# ===================================================================== V4
@section("vcard-export-carries-the-role")
def _v4():
    book = [card("Amy Pond", role="Kissogram"),
            card("Rory Williams", role="Nurse"),
            card("River Song")]
    text = con.export_vcards(book)
    check("vcard-export-carries-the-role",
          "TITLE:Kissogram" in text and "TITLE:Nurse" in text, text)
    check("...and writes none for a card that has no role",
          text.count("TITLE:") == 2, text)
    back = con.parse_vcards(text)
    lost = [(a["name"], a["role"], b["role"])
            for a, b in zip(book, back) if a["role"] != b["role"]]
    check("export-then-import-keeps-every-role", not lost, lost)


# ===================================================================== V5
@section("vcard-import-reads-title-as-the-role")
def _v5():
    got = con.parse_vcards(
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Priya Raman\r\n"
        "TITLE:Head Teacher\r\nORG:Fernhill Primary\r\nEND:VCARD\r\n")[0]
    check("vcard-import-reads-title-as-the-role",
          got["role"] == "Head Teacher", got)
    check("...and TITLE does not disturb the organization",
          got["organization"] == "Fernhill Primary", got)
    # vCard also has ROLE. A card carrying only ROLE still names the job.
    only_role = con.parse_vcards(
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ana Gil\r\n"
        "ROLE:Programmer\r\nEND:VCARD\r\n")[0]
    check("vcard-import-falls-back-to-role-when-there-is-no-title",
          only_role["role"] == "Programmer", only_role)
    both = con.parse_vcards(
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ana Gil\r\n"
        "ROLE:Programmer\r\nTITLE:Senior Engineer\r\nEND:VCARD\r\n")[0]
    check("...and TITLE wins when a card carries both",
          both["role"] == "Senior Engineer", both)


bad = R.count(False)
print("RESULT: %s (%d checks, %d failed)"
      % ("ALL PASS" if not bad else "SOME FAILED", len(R), bad))
sys.exit(1 if bad else 0)
