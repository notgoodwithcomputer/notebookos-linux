#!/usr/bin/env python3
"""Display-free Contacts schema, vCard, search, ordering, and undo checks."""
import copy
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="contacts-interop-"))
import contacts as c  # noqa: E402

failed = []


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failed.append(name)


old = {"name": "Zoë 李", "role": "Friend", "phone": "+1 (212) 555-0199",
       "email": "zoe@example.test", "address": "1 Main St", "bday": "1990-03-14",
       "notes": "Line one\nLine two", "color": "#8A857A",
       "future_field": {"kept": True}}
p = c.normalize_person(old, 0)
check("old phone migrates", p["phones"] == [{"label": "mobile", "value": old["phone"]}])
check("old email migrates", p["emails"] == [{"label": "home", "value": old["email"]}])
check("unknown data survives migration", p["future_field"] == {"kept": True})
encoded = json.loads(json.dumps({"people": [p]}, ensure_ascii=False))["people"][0]
check("new shape round trips losslessly", encoded == p and "phone" not in encoded)

damaged_dir = tempfile.mkdtemp(prefix="contacts-damaged-")
damaged_path = os.path.join(damaged_dir, "contacts.json")
damaged_bytes = b'{"people": [broken\n'
with open(damaged_path, "wb") as fh:
    fh.write(damaged_bytes)
old_path = c.CONTACTS_FILE
c.CONTACTS_FILE = damaged_path
holder = types.SimpleNamespace(_quarantine_pending=False, _extra={},
                               _save_warned=False)
loaded = c.Contacts._load_people(holder)
holder.people = []
c.Contacts._save(holder)
c.CONTACTS_FILE = old_path
check("damaged store loads empty", loaded == [])
# THE OS CONTRACT (store_damage gate): unreadable bytes are MOVED ASIDE by
# the shared writer at the next flush — never overwritten, never the only
# thing standing between the user and a store that silently stopped saving.
_recovered = any(
    open(os.path.join(damaged_dir, f), "rb").read() == damaged_bytes
    for f in os.listdir(damaged_dir) if ".damaged-" in f)
check("damaged bytes survive the flush (moved aside, store works again)",
      _recovered and json.load(open(damaged_path)).get("people") == [])

p.update({"organization": "Café, Inc.; R&D",
          "phones": [{"label": "mobile", "value": "+1 (212) 555-0199"},
                     {"label": "work", "value": "020 7946 0958"}],
          "emails": [{"label": "home", "value": "zoe@example.test"},
                     {"label": "work", "value": "z.li@work.test"}]})
card = c.export_vcards([p])
back = c.parse_vcards(card)[0]
for key in ("name", "organization", "phones", "emails", "notes", "bday"):
    check("vCard round trip " + key, back[key] == p[key])

folded = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Renée D\\,Angelo\r\n"
          "NOTE:first\\nsecond and a very\r\n long folded line\r\n"
          "TEL;TYPE=WORK:555\\,0100\r\nX-UNKNOWN:anything\r\nEND:VCARD\r\n")
fp = c.parse_vcards(folded)[0]
check("folded line unfolds", fp["notes"] == "first\nsecond and a verylong folded line")
check("escaped comma unescapes", fp["name"] == "Renée D,Angelo")
check("unknown property skipped", fp["phones"][0]["value"] == "555,0100")

target = c.normalize_person({"name": "Zoë 李", "organization": "Existing",
                             "address": "", "phones": [{"label": "home", "value": "111"}]})
incoming = c.normalize_person({"name": "Zoë 李", "organization": "Conflict",
                               "address": "Filled", "phones": [
                                   {"label": "home", "value": "111"},
                                   {"label": "work", "value": "222"}]})
book = c.merge_contacts([target], [incoming])
check("dedupe keeps one exact name", len(book) == 1)
check("dedupe fills blanks", book[0]["address"] == "Filled")
check("dedupe keeps scalar conflict", book[0]["organization"] == "Existing")
check("dedupe retains list conflict", book[0]["phones"] == [
      {"label": "home", "value": "111"}, {"label": "work", "value": "222"}])

check("digit search ignores formatting", c.contact_matches(p, "2125550199"))
people = [c.normalize_person({"name": "Amy"}),
          c.normalize_person({"name": "Ava", "favorite": True}),
          c.normalize_person({"name": "Bob", "favorite": True})]
check("favorites sort first inside letter", [x[1]["name"] for x in
      c.ordered_people(people)] == ["Ava", "Amy", "Bob"])

# Exercise the real delete/undo methods with widget and disk effects replaced
# by recorders. The restored dict must be byte-identical to the deleted dict.
original = copy.deepcopy(p)
win = c.Contacts.__new__(c.Contacts)
win.people = [copy.deepcopy(original)]
win.active = 0; win.editing = False; win._pending_new = False; win._deleted = None
win._save = lambda: None; win._rebuild_list = lambda: None
win._rebuild_detail = lambda: None; win._flash = lambda _s: None
win._do_delete(); win._undo_delete()
check("undo restores byte-identical record", win.people == [original])

print("RESULT: " + ("ALL PASS" if not failed else "SOME FAILED"))
sys.exit(1 if failed else 0)
