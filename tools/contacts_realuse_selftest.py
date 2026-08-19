#!/usr/bin/env python3
"""Real-use regressions for Contacts — the defects a person meets by using the
address book, not by calling one function at a time.

Every check here is named after the behaviour it guards and was watched go RED
against the code as it stood before the fix beside it:

  star-while-editing-keeps-typed-text     the star rebuilt the open form from
                                          the record, so everything typed into
                                          it vanished (DATA LOSS)
  undo-delete-keeps-the-open-edit         Ctrl+Z threw away the edit open on
                                          ANOTHER card and left the restored
                                          card in a form (DATA LOSS)
  address-is-a-multi-line-field           an address is several lines
                                          everywhere except in its own editor
  enter-follows-the-form-order            Enter jumped Role -> Organization ->
                                          Phones, so a card filled in by
                                          keyboard got its values shuffled
  long-value-keeps-the-card-buttons-...   one long email widened the card until
                                          Edit / the star / Copy were off a
                                          1024-wide screen with no way back
  card-pane-paints-to-its-bottom-edge     the bottom ~40px of the card pane
                                          never painted
  accented-names-file-under-their-...     Émile filed after Z, under its own É
  import-says-what-it-actually-did        re-importing an export said
                                          "Imported 11 contacts" having added
                                          none of them
  bare-vcard-2.1-types-keep-their-kind    "TEL;CELL:" (what older phones write)
                                          imported as Home
  empty-value-fields-show-how-to-add-...  nothing said a card can hold several
                                          numbers, or that each can be named

Run:  tools/guestrun.sh python3 tools/contacts_realuse_selftest.py
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

# A private NB_HOME before the app is imported: this suite really saves, and
# an unset NB_HOME would write over the caller's own address book AND put the
# single-instance guard on the shared registry, where nbapp os._exit(0)s with
# no output and status 0 — a pass that tested nothing.
WORK = tempfile.mkdtemp(prefix="contacts-realuse-")
os.environ["NB_HOME"] = os.path.join(WORK, "home")
os.environ["NB_DRIVE_HOME_ROOT"] = os.path.join(WORK, "drive")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango  # noqa: E402

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
    """Run one group; a crash inside it fails that group BY NAME instead of
    taking the whole suite down with a traceback."""
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
    person = con.normalize_person(dict(kw, name=name))
    return person


def seed(*people):
    """Write an address book straight into the store and open a window on it."""
    os.makedirs(con.CFG_DIR, exist_ok=True)
    nbapp.atomic_write_json(con.CONTACTS_FILE, {"people": list(people)})
    return con.Contacts()


def stored():
    with open(con.CONTACTS_FILE) as fh:
        return json.load(fh)["people"]


def index_of(win, name):
    return next(i for i, p in enumerate(win.people) if p["name"] == name)


# ===================================================================== C1
@section("star-while-editing-keeps-typed-text")
def _c1():
    win = seed(card("Long Email"), card("Amy Pond"))
    win.active = index_of(win, "Long Email")
    win._toggle_edit()                       # Edit
    win._entries["name"].set_text("Long Email Person")
    win._entries["organization"].set_text("Acme Corp")
    win._favorite_button.clicked()           # the real star, mid-edit
    person = win.people[win.active]
    check("star-while-editing-keeps-typed-text",
          person["name"] == "Long Email Person"
          and person["organization"] == "Acme Corp",
          "record is %r" % ((person["name"], person["organization"]),))
    check("star-while-editing-leaves-the-form-open-and-filled",
          win.editing
          and win._entries["organization"].get_text() == "Acme Corp"
          and win._entries["name"].get_text() == "Long Email Person",
          "editing=%s entries=%r" % (win.editing,
                                     {k: e.get_text() for k, e
                                      in win._entries.items()}))
    check("star-while-editing-still-favorites-the-card",
          person["favorite"] is True, person.get("favorite"))
    win._toggle_edit()                       # Done
    saved = {p["name"]: p["organization"] for p in stored()}
    check("star-while-editing-survives-to-the-store",
          saved.get("Long Email Person") == "Acme Corp", saved)
    win.destroy()


# ===================================================================== C2
@section("undo-delete-keeps-the-open-edit")
def _c2():
    win = seed(card("Search Kid"), card("Temp Person"))
    win.active = index_of(win, "Temp Person")
    win._do_delete()
    win._select(index_of(win, "Search Kid"))
    win._toggle_edit()
    win._entries["role"].set_text("Student")
    win._undo_delete()                       # Ctrl+Z, mid-edit
    kid = win.people[index_of(win, "Search Kid")]
    check("undo-delete-keeps-the-open-edit", kid["role"] == "Student",
          "role is %r" % (kid["role"],))
    check("undo-delete-leaves-the-restored-card-in-the-read-view",
          not win.editing
          and win.people[win.active]["name"] == "Temp Person",
          "editing=%s active=%r" % (win.editing,
                                    win.people[win.active]["name"]))
    saved = {p["name"]: p["role"] for p in stored()}
    check("undo-delete-survives-to-the-store",
          saved.get("Search Kid") == "Student", saved)
    # ...and undo through an untouched New Contact drops that placeholder
    # instead of leaving a blank "Unnamed" row behind it (or, worse, carrying
    # the "this card is a placeholder" flag onto the card it just restored).
    win.active = index_of(win, "Temp Person")
    win._do_delete()
    win._new_contact()
    win._undo_delete()
    check("undo-delete-after-a-blank-new-contact-leaves-no-ghost",
          sorted(p["name"] for p in win.people)
          == ["Search Kid", "Temp Person"]
          and not win._pending_new and not win.editing,
          [p["name"] for p in win.people])
    win.destroy()


# ===================================================================== C6
@section("address-is-a-multi-line-field")
def _c6():
    win = seed(card("Rory Williams",
                    address="1 Nurse Ln\nLeadworth\nLW1 1AA\nUK"))
    win._toggle_edit()
    view = getattr(win, "_addr_view", None)
    check("address-is-a-multi-line-field",
          isinstance(view, Gtk.TextView) and "address" not in win._entries,
          "addr view %r, entries %r" % (view, sorted(win._entries)))
    if isinstance(view, Gtk.TextView):
        buf = view.get_buffer()
        shown = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        check("address-editor-shows-the-lines-as-lines",
              shown == "1 Nurse Ln\nLeadworth\nLW1 1AA\nUK", repr(shown))
        buf.set_text(shown + "\nFlat 2")
    win._toggle_edit()                       # Done
    saved = stored()[0]["address"]
    check("address-editor-writes-its-lines-back",
          saved == "1 Nurse Ln\nLeadworth\nLW1 1AA\nUK\nFlat 2", repr(saved))
    win.destroy()


# ===================================================================== C8
@section("import-says-what-it-actually-did")
def _c8():
    win = seed(card("Amy Pond", phones=[{"label": "mobile",
                                         "value": "555-0001"}]),
               card("Rory Williams"))
    path = os.path.join(WORK, "roundtrip.vcf")
    nbpicker.save_file = lambda *a, **k: path
    nbpicker.open_file = lambda *a, **k: path
    win._export_vcard(True)
    before = len(win.people)
    win._import_vcard()
    said = win.status_lbl.get_text()
    check("import-says-what-it-actually-did",
          len(win.people) == before and "2" not in said, repr(said))
    check("import-of-an-export-says-nothing-was-added",
          said == "Every contact in that file is already here", repr(said))
    fresh = os.path.join(WORK, "one.vcf")
    with open(fresh, "w") as fh:
        fh.write("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Rose Tyler\r\n"
                 "END:VCARD\r\n")
    nbpicker.open_file = lambda *a, **k: fresh
    win._import_vcard()
    said = win.status_lbl.get_text()
    check("import-of-one-new-card-counts-it-in-the-singular",
          said == "Added 1 contact" and len(win.people) == before + 1,
          repr(said))
    win.destroy()


# ==================================================================== C11
@section("empty-value-fields-show-how-to-add-another")
def _c11():
    win = seed(card("Amy Pond"))
    win._toggle_edit()
    hints = {k: win._entries[k].get_placeholder_text()
             for k in ("phones", "emails")}
    # Both fields still show that they hold MORE THAN ONE value (the semicolon);
    # only PHONES still shows how to name one, because only phones are named.
    check("empty-value-fields-show-how-to-add-another",
          all(";" in (h or "") for h in hints.values()), hints)
    check("...and the phone field names one, in the spelling it reads back",
          ":" in (hints["phones"] or "")
          and con.parse_labeled_text(hints["phones"], "mobile")
          == con.parse_labeled_text(
              con.labeled_text(
                  con.parse_labeled_text(hints["phones"], "mobile")),
              "mobile"), hints)
    check("...and the email field offers no category to file one under",
          ":" not in (hints["emails"] or ""), hints)
    win.destroy()


# ============================================ pure model: C7, C8, C9, C11
@section("accented-names-file-under-their-base-letter")
def _model():
    people = [card(n) for n in ("Zed Zane", "Émile Éluard", "Eve Evans",
                               "Ólafur Ó", "Oscar Otto", "Øyvind Ås",
                               "Adam Ant", "日本 太郎")]
    order = [p["name"] for _i, p in con.ordered_people(people)]
    check("accented-names-file-under-their-base-letter",
          order.index("Émile Éluard") < order.index("Zed Zane")
          and abs(order.index("Émile Éluard") - order.index("Eve Evans")) == 1,
          order)
    check("...and so does a letter whose mark is inside the glyph",
          order.index("Ólafur Ó") < order.index("Zed Zane")
          and order.index("Øyvind Ås") < order.index("Zed Zane"), order)
    letters = [con.Contacts._sort_letter(p) for _i, p in
               con.ordered_people(people)]
    check("...and the divider they sit under is that base letter",
          letters == sorted(letters) and "É" not in letters
          and letters.count("E") == 2 and letters.count("O") == 3,
          letters)
    check("a name in another script keeps its own divider",
          con.Contacts._sort_letter({"name": "日本 太郎"}) == "日",
          con.Contacts._sort_letter({"name": "日本 太郎"}))
    check("a name that starts with a digit still files under #",
          con.Contacts._sort_letter({"name": "3 Amigos"}) == "#",
          con.Contacts._sort_letter({"name": "3 Amigos"}))


@section("bare-vcard-2.1-types-keep-their-kind")
def _vcard():
    cards = con.parse_vcards(
        "BEGIN:VCARD\r\nVERSION:2.1\r\nFN:Jurgen Muller\r\n"
        "TEL;CELL:+49 170 0000\r\nTEL;WORK:+49 30 1111\r\n"
        "TEL;HOME:+49 30 2222\r\nEMAIL;INTERNET;WORK:j@work.de\r\n"
        "END:VCARD\r\n")
    labels = [v["label"] for v in cards[0]["phones"]]
    check("bare-vcard-2.1-types-keep-their-kind",
          labels == ["mobile", "work", "home"], labels)
    check("...for an email written the same way",
          [v["label"] for v in cards[0]["emails"]] == ["work"],
          cards[0]["emails"])
    v3 = con.parse_vcards("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:A B\r\n"
                          "TEL;TYPE=CELL,VOICE:1\r\nTEL;TYPE=WORK:2\r\n"
                          "TEL:3\r\nEND:VCARD\r\n")
    check("a vCard 3.0 TYPE= still reads as it always did",
          [v["label"] for v in v3[0]["phones"]] == ["mobile", "work", "home"],
          v3[0]["phones"])


@section("import-counts-what-the-merge-did")
def _merge():
    book = [card("Amy Pond")]
    stats = {}
    con.merge_contacts(book, [card("Amy Pond")], stats)
    check("import-counts-what-the-merge-did",
          stats == {"added": 0, "updated": 0}, stats)
    stats = {}
    con.merge_contacts(book, [card("Amy Pond", organization="Torchwood"),
                              card("Rose Tyler")], stats)
    check("...added for a new card, updated for one that gained a field",
          stats == {"added": 1, "updated": 1}, stats)


@section("two-values-typed-as-people-type-them")
def _labels():
    got = con.parse_labeled_text("555-0001, 555-0002", "mobile")
    check("two-values-typed-as-people-type-them",
          [v["value"] for v in got] == ["555-0001", "555-0002"], got)
    got = con.parse_labeled_text("a@x.com b@x.com", "home", split_values=True)
    check("...and two emails with only a space between them",
          [v["value"] for v in got] == ["a@x.com", "b@x.com"], got)
    got = con.parse_labeled_text("a@x.com work: b@x.com", "home",
                                 split_values=True)
    check("...but a half-named line is kept exactly as it was typed",
          [v["value"] for v in got] == ["a@x.com work: b@x.com"], got)
    got = con.parse_labeled_text("work: a@x.com b@x.com", "home",
                                 split_values=True)
    check("...and a named pair keeps the name on both",
          got == [{"label": "work", "value": "a@x.com"},
                  {"label": "work", "value": "b@x.com"}], got)
    got = con.parse_labeled_text("555-1234,,123", "mobile")
    check("a comma INSIDE a number is part of the number",
          [v["value"] for v in got] == ["555-1234,,123"], got)
    text = con.labeled_text([{"label": "mobile", "value": "555-0100"},
                             {"label": "work", "value": "555-0101"}])
    check("the written spelling still reads back unchanged",
          con.parse_labeled_text(text, "mobile")
          == [{"label": "mobile", "value": "555-0100"},
              {"label": "work", "value": "555-0101"}],
          con.parse_labeled_text(text, "mobile"))


# ========================================== on-screen: C3, C4, C5 (appdrive)
@section("card-pane-paints-to-its-bottom-edge")
def _screen():
    import appdrive
    import cairo
    home = os.path.join(os.environ["NB_DRIVE_HOME_ROOT"], "contacts")
    store = os.path.join(home, ".config", "notebook")
    os.makedirs(store, exist_ok=True)
    long_email = ("firstname.middlename.lastname@engineering."
                  "some-long-company-name.example.com")
    book = [card("Amy Thirty", phones=[{"label": "mobile",
                                        "value": "555-%04d" % i}
                                       for i in range(30)]),
            card("Long Email", emails=[{"label": "home",
                                        "value": long_email}])]
    with open(os.path.join(store, "contacts.json"), "w") as fh:
        json.dump({"people": book}, fh)
    d = appdrive.Drive("contacts")
    try:
        png = os.path.join(WORK, "bottom.png")
        d.app._select(0)
        d.shot(png)
        surf = cairo.ImageSurface.create_from_png(png)
        data, stride = surf.get_data(), surf.get_stride()

        def has_ink(y):
            base = y * stride
            for x in range(380, 1000):
                o = base + x * 4
                if (abs(data[o + 2] - 0xFC) > 6 or abs(data[o + 1] - 0xFB) > 6
                        or abs(data[o] - 0xF8) > 6):
                    return True
            return False

        last = max((y for y in range(d.h - 80, d.h) if has_ink(y)),
                   default=0)
        check("card-pane-paints-to-its-bottom-edge", last >= d.h - 4,
              "last painted row %d of %d" % (last, d.h))

        # C4 — the header buttons stay on a 1024-wide screen
        d.app._select(1)
        d.pump(0.1)
        edges = {}
        for w in d.walk():
            if not isinstance(w, Gtk.Button) or not w.get_visible():
                continue
            cls = w.get_style_context().list_classes()
            tag = ("Edit" if "editbtn" in cls else
                   "star" if isinstance(w, Gtk.ToggleButton) else
                   "Copy" if w.get_label() == "Copy" else None)
            if tag is None:
                continue
            xy = w.translate_coordinates(d.child, 0, 0)
            if xy is None:
                continue
            edges[tag] = xy[-2] + w.get_allocation().width
        check("long-value-keeps-the-card-buttons-on-screen",
              edges and all(e <= d.w for e in edges.values()),
              "right edges %r on a %d-wide screen" % (edges, d.w))

        # C5 — Enter walks the form the way the form is laid out
        d.app._select(0)
        [b for b in d.find(Gtk.Button)
         if "editbtn" in b.get_style_context().list_classes()][0].clicked()
        d.pump(0.1)
        walked = []
        d.app._entries["role"].grab_focus()
        for _ in range(3):
            d.key("Return")
            d.pump(0.02)
            got = d.focus()
            walked.append(next((k for k, e in d.app._entries.items()
                                if e is got), repr(got)))
        check("enter-follows-the-form-order",
              walked == ["phones", "emails", "organization"], walked)
        packed = [k for k in con.EDIT_ORDER
                  if d.app._edit_widget(k) is not None]
        on_screen = sorted(
            (d.app._edit_widget(k).translate_coordinates(d.child, 0, 0)[-1], k)
            for k in packed)
        check("...which is the order the fields are packed in",
              [k for _y, k in on_screen] == packed,
              (packed, [k for _y, k in on_screen]))
    finally:
        d.close()


bad = R.count(False)
print("RESULT: %s (%d checks, %d failed)"
      % ("ALL PASS" if not bad else "SOME FAILED", len(R), bad))
sys.exit(1 if bad else 0)
