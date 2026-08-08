#!/usr/bin/env python3
"""Display-free adversarial execution checks for Contacts."""
import copy
import json
import os
import tempfile
from datetime import date

HOME = tempfile.mkdtemp(prefix="contacts-adversarial-")
os.environ["NB_HOME"] = HOME

import contacts  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("PASS " + name)
    else:
        failed += 1
        print("FAIL " + name + (": " + detail if detail else ""))


def birthday_checks():
    check("Dec 31 birthday is 364 days away on Jan 1, not in the soon window",
          contacts.days_until_birthday("31 December", date(2026, 1, 1)) == 364)
    check("Dec 31 birthday is tomorrow on Dec 30 across year end",
          contacts.days_until_birthday("31 December", date(2026, 12, 30)) == 1)
    check("Feb 29 birthday falls on Mar 1 in a non-leap year",
          contacts.days_until_birthday("29 February", date(2026, 2, 28)) == 1)
    check("Feb 29 birthday remains Feb 29 in a leap year",
          contacts.days_until_birthday("29 February", date(2028, 2, 28)) == 1)


def record_checks():
    unnamed = contacts.normalize_person({"name": "", "organization": "Anon"})
    people = [contacts.normalize_person({"name": "Zed"}), unnamed,
              contacts.normalize_person({"name": "Amy"})]
    check("unnamed contact sorts in the # group without crashing",
          [p["name"] for _, p in contacts.ordered_people(people)]
          == ["", "Amy", "Zed"])

    duplicate = contacts.normalize_person({"name": "Amy", "phones": [
        {"label": "home", "value": "111"}]})
    merged = contacts.merge_contacts([copy.deepcopy(duplicate)], [
        contacts.normalize_person({"name": "Amy", "emails": [
            {"label": "work", "value": "a@example.test"}]})])
    check("duplicate import merges complementary fields without duplicate cards",
          len(merged) == 1 and merged[0]["phones"] == duplicate["phones"]
          and merged[0]["emails"][0]["value"] == "a@example.test")

    original = contacts.normalize_person({
        "name": "Doe, Jane; Jr", "organization": "A;B, Inc",
        "address": "1 Main St\nSuite 2", "notes": "backslash \\ and comma,",
        "bday": "29 February", "phones": [
            {"label": "work", "value": "+1 212 555 0100"}],
        "emails": [{"label": "home", "value": "jane@example.test"}]})
    parsed = contacts.parse_vcards(contacts.export_vcards([original]))
    keys = ("name", "organization", "address", "notes", "bday", "phones", "emails")
    check("vCard export/import preserves all supported contact fields",
          len(parsed) == 1 and all(parsed[0][k] == original[k] for k in keys),
          repr(parsed))


def _asides():
    base = os.path.basename(contacts.CONTACTS_FILE) + ".damaged-"
    return sorted(f for f in os.listdir(contacts.CFG_DIR)
                  if f.startswith(base))


def _aside_holds(blob):
    return any(open(os.path.join(contacts.CFG_DIR, f), "rb").read() == blob
               for f in _asides())


def _clear_asides():
    for f in _asides():
        os.unlink(os.path.join(contacts.CFG_DIR, f))


def damaged_store_and_undo_checks():
    # THE OS CONTRACT (store_damage gate): bytes the app could not read are
    # MOVED ASIDE, never overwritten — and saving keeps working.
    os.makedirs(contacts.CFG_DIR, exist_ok=True)
    _clear_asides()
    original = b'{"people":[{"name":"Alice"}'
    with open(contacts.CONTACTS_FILE, "wb") as fh:
        fh.write(original)
    app = contacts.Contacts.__new__(contacts.Contacts)
    app._quarantine_pending = False
    app._extra = {}
    app._save_warned = False
    app.people = app._load_people()
    app._save()
    check("damaged contacts.json bytes survive the flush (aside or path)",
          _aside_holds(original)
          or open(contacts.CONTACTS_FILE, "rb").read() == original,
          "asides=%r" % _asides())
    try:
        works = json.load(open(contacts.CONTACTS_FILE)).get("people") == []
    except Exception:
        works = False           # unparseable = still the damaged bytes
    check("...and contacts.json is a working store again", works)

    # The case only the app can see: valid JSON that is not an address book.
    _clear_asides()
    wrong_shape = b'{"rolodex":"a text blob, not cards"}'
    with open(contacts.CONTACTS_FILE, "wb") as fh:
        fh.write(wrong_shape)
    app = contacts.Contacts.__new__(contacts.Contacts)
    app._quarantine_pending = False
    app._extra = {}
    app._save_warned = False
    app.people = app._load_people()
    app._save()
    check("unrecognized contacts.json is moved aside by the app itself",
          _aside_holds(wrong_shape), "asides=%r" % _asides())

    # A newer build's unknown top-level key rides through this build's save.
    newer = {"people": [{"name": "Alice"}], "groups": {"family": ["Alice"]}}
    with open(contacts.CONTACTS_FILE, "w") as fh:
        json.dump(newer, fh)
    app = contacts.Contacts.__new__(contacts.Contacts)
    app._quarantine_pending = False
    app._extra = {}
    app._save_warned = False
    app.people = app._load_people()
    app._save()
    saved = json.load(open(contacts.CONTACTS_FILE))
    check("a newer build's unknown top-level key survives the save",
          saved.get("groups") == newer["groups"],
          "saved keys: %r" % sorted(saved))

    # PASS-MUTANT: a shape-blind flush loses the bytes with no recovery copy.
    _clear_asides()
    with open(contacts.CONTACTS_FILE, "wb") as fh:
        fh.write(wrong_shape)
    contacts.nbapp.atomic_write_json(contacts.CONTACTS_FILE, {"people": []})
    check("PASS-MUTANT contacts quarantine: shape-blind flush DOES lose bytes",
          open(contacts.CONTACTS_FILE, "rb").read() != wrong_shape
          and not _aside_holds(wrong_shape))

    person = contacts.normalize_person({"name": "Alice"})
    app = contacts.Contacts.__new__(contacts.Contacts)
    app.people = [copy.deepcopy(person)]
    app.active = 0
    app.editing = False
    app._pending_new = False
    app._deleted = None
    app._save = lambda: None
    app._rebuild_list = lambda: None
    app._rebuild_detail = lambda: None
    app._flash = lambda _text: None
    app._delete_contact()
    deleted = app.people == [] and app._deleted is not None
    app._undo_delete()
    check("contact deletion is immediate and undo restores byte-identical data",
          deleted and app.people == [person])


birthday_checks()
record_checks()
damaged_store_and_undo_checks()
print("\n%d/%d checks passed" % (passed, passed + failed))
raise SystemExit(1 if failed else 0)
