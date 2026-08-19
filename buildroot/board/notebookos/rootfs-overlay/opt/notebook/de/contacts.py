#!/usr/bin/env python3
"""
Contacts — the Notebook OS address book (native GTK).

A two-pane surface: a searchable, alphabetically sorted contact list on the
left and a card detail view on the right (phone, email, address, birthday,
notes). The book ships EMPTY — the first run shows a "No contacts" state and no
records are ever seeded. The address book auto-persists to
$NB_HOME/.config/notebook/contacts.json on every add / edit / delete and on
close, so cards survive quit and reboot. File ▸ Export to PDF writes a
read-only copy of the whole book into $NB_HOME/Documents; there is no
file open/save (the book is a single always-saved store, like Journal).
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, Pango, GLib  # noqa: E402

import os
import json
import quopri
import re
import time
import subprocess
import copy
import unicodedata
from datetime import date, timedelta

import cairo

import nbapp
import nbcommands
import nbicons
import nbprint
import nbpicker
import nbi18n
from nbi18n import _t  # noqa: E402

DE_DIR = os.path.dirname(os.path.abspath(__file__))

# Auto-persistence — the address book auto-saves to
# $NB_HOME/.config/notebook/contacts.json on every add / edit / delete and on
# close, matching writer.py / journal.py. The book ships EMPTY; no records are
# ever seeded. File ▸ Export to PDF writes a copy into $NB_HOME/Documents.
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
CONTACTS_FILE = os.path.join(CFG_DIR, "contacts.json")
DOCS_DIR = os.path.join(HOME, "Documents")
MAX_CONTACTS_BYTES = 8 * 1024 * 1024


class ContactsStoreTooLarge(ValueError):
    pass


def _read_contacts_json(path=None, limit=MAX_CONTACTS_BYTES):
    if path is None:
        path = CONTACTS_FILE
    with open(path, "rb") as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise ContactsStoreTooLarge("address book is too large")
    return json.loads(data)

# papertone palette
INK = "#1A1916"
PAPER = "#FCFBF8"
PANEL = "#F1EEE6"
HAIR = "#D7D2C5"
MUTED = "#6E695E"
MUTED2 = "#6E695E"
SEL = "#EAE3D2"
ACCENT = "#C8341E"

# Deliberately off-palette: these are IDENTITY tints, one per card, so two
# neighbouring rows in the book can be told apart at a glance. The papertone
# neutrals cannot supply six values a person can distinguish, and each one has
# to stay dark enough to carry the white initials drawn on it.
AVATAR_COLORS = ["#8A857A", "#6E7B57", "#9A6B4F", "#5B6B7B", "#7B5B6E", "#6B7B6E"]

# Standard address-book fields (label, storage key). Every field is free-text
# and editable; there is no radio/network metadata on a card.
FIELDS = [("Organization", "organization"), ("Address", "address"),
          ("Birthday", "bday")]

# The canonical set of string keys every card carries (color is added
# separately). Kept in one place so load / new / normalize agree.
FIELD_KEYS = ("name", "role", "organization", "address", "bday", "notes")
VALUE_LABELS = ("mobile", "home", "work")
# What a stored label is CALLED on screen. The catalog is a single
# English-keyed map with no context, and "Home" and "Work" were already in
# it for the home SCREEN and the day's work — so asking for "Home" beside a
# telephone number printed the word for a website's front page. A home
# number read "Startseite" in German, "Accueil" in French, "Главная" in
# Russian, "主页" in Chinese, "Ana Sayfa" in Turkish: thirteen of the
# seventeen languages named a home page where a person's home number is.
# "mobile" had no entry at all, so a mobile number stayed English beside
# them. These keys name the kind of thing the value IS, which is what the
# catalog can hold and what a translator can get right. The STORED label is
# untouched: the field still reads and writes "home:", and a vCard still
# carries TYPE=HOME.
PHONE_LABEL_NAMES = {"mobile": "Mobile", "home": "Home phone",
                     "work": "Work phone"}
EMAIL_LABEL_NAMES = {"mobile": "Mobile", "home": "Home email",
                     "work": "Work email"}
# What the Phones / Emails fields show while they are empty. A card holds any
# number of values, each of which can be named, and the field said only
# "Phones" — so the spelling that does either was undiscoverable. These are
# worked examples in the exact spelling the field writes and reads back
# (labeled_text / parse_labeled_text), which is why the label words are the
# stored English ones in every language: a translated label would not read
# back as a label.
VALUE_EXAMPLES = {"phones": "mobile: 555-0100; work: 555-0101",
                  "emails": "me@example.com; me@job.com"}
MAX_VCARD_BYTES = 32 * 1024 * 1024

# The contact card is a fixed reading measure centred in the detail pane —
# the width the design draws it at, and the width it keeps until the pane is
# too narrow to hold it (see _fit_card).
CARD_W = 760
CARD_MARGIN = 64

# Top-to-bottom order of the edit fields, so Enter advances to the next field
# (Tab-like) instead of dropping out of edit mid-form. DERIVED from the order
# _rebuild_detail packs them in — name, role, then Phones and Emails, then
# FIELDS — because a second, hand-written copy of that order drifted from it:
# Enter in Role landed in Organization (the third row on screen), then jumped
# back up to Phones, so anyone filling a card in from the keyboard typed their
# phone number into the organization field. Notes is the multi-line area the
# run ends in.
EDIT_ORDER = ("name", "role", "phones", "emails") + tuple(k for _l, k in FIELDS)

# Letter dividers only appear once a book is long enough that scanning it needs
# them; a handful of contacts reads better as the flat list the design draws.
GROUP_FROM = 40
# How far ahead a birthday counts as "soon" — near enough to still get a card
# in the post, not so far that the banner is permanently on screen.
BIRTHDAY_SOON = 30

MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july",
               "august", "september", "october", "november", "december")
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")
_BDAY_NUM_RE = re.compile(r"^(\d{1,4})\D+(\d{1,2})(?:\D+(\d{1,4}))?\D*$")
_WORD_RE = re.compile(r"[A-Za-z]+")
_DIGITS_RE = re.compile(r"\d+")


def value_label_name(field, label):
    """The on-screen name of one value's label, in the running language."""
    names = EMAIL_LABEL_NAMES if field == "emails" else PHONE_LABEL_NAMES
    return _t(names.get(label, label.capitalize()))


def _set_person_text(label, text, fallback=""):
    """Put a person's own words on a label without the interface catalog
    touching them.

    nbi18n auto-translates the text of every Gtk.Label it walks, which is
    right for chrome and wrong for content. A contact filed under "Home" —
    the name people genuinely give their own landline — came up as "Accueil"
    on a French install, in the list AND on the card; an organization called
    "Library" as "Bibliothèque"; a role of "Work" as "Travail". The stored
    record was never altered, so the name on screen was not the name the
    search box could find, and the edit form showed a different word again.
    Only the empty state is ours to translate."""
    value = str(text or "")
    if value:
        nbi18n.set_verbatim(label, value)
    else:
        label.set_text(_t(fallback))


def _labeled_values(value, fallback_label):
    """Normalize old strings and new labeled objects without dropping values."""
    if isinstance(value, str):
        return [{"label": fallback_label, "value": value}] if value else []
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item:
            out.append({"label": fallback_label, "value": item})
        elif isinstance(item, dict) and item.get("value") not in (None, ""):
            row = dict(item)
            label = str(row.get("label") or fallback_label).lower()
            row["label"] = ("mobile" if label in ("cell", "mobile") else
                            label if label in VALUE_LABELS else fallback_label)
            row["value"] = str(row["value"])
            out.append(row)
    return out


def normalize_person(p, i=0):
    """Return the lossless current record shape for old or current data."""
    person = dict(p)
    for key in FIELD_KEYS:
        person[key] = str(person.get(key, "") or "")
    person["phones"] = _labeled_values(
        person.get("phones", person.get("phone", "")), "mobile")
    person["emails"] = _labeled_values(
        person.get("emails", person.get("email", "")), "home")
    person.pop("phone", None)
    person.pop("email", None)
    person["favorite"] = bool(person.get("favorite", False))
    color = person.get("color")
    if not isinstance(color, str) or not _is_css_color(color):
        color = AVATAR_COLORS[i % len(AVATAR_COLORS)]
    person["color"] = color
    return person


def labeled_text(values, field=None):
    """Editable one-item-per-line spelling: ``mobile: 555-0100``.

    EMAILS CARRY NO CATEGORY. A phone genuinely has kinds -- which number to
    ring at nine in the evening is a real question -- but an email address is
    an email address, and asking a person to file each one as Home or Work
    added a decision with no consequence to every card in the book. Addresses
    are written and read back plain. Nothing is lost: parse_labeled_text still
    understands a "work: a@b" that was typed or imported, so an existing card
    and a vCard from elsewhere both still come in whole."""
    if field == "emails":
        return "; ".join(v["value"] for v in values)
    return "; ".join("%s: %s" % (v["label"], v["value"]) for v in values)


def parse_labeled_text(text, fallback, split_values=False):
    """Read the editable spelling back.

    A semicolon or a newline separates values, and so does a comma FOLLOWED BY
    A SPACE — "555-0001, 555-0002" is the two numbers anybody writing that
    meant. A comma with no space after it stays inside the value, because in a
    dialling string that is what it is ("555-1234,,123" is one number with two
    pauses in it), and splitting one of those would turn a number somebody
    imported into two wrong ones.

    `split_values` additionally splits one value on any space, for a field
    whose values can hold none: an email address never does, so
    "a@x.com b@x.com" is two addresses and not one impossible one. A value
    with a colon in it is left whole — a colon is how a value is NAMED, and
    guessing where the names go in "a@x.com work: b@x.com" would invent an
    address called "work:" out of somebody's typing.
    """
    out = []
    for line in re.split(r"[;\n]+|,\s", text or ""):
        line = line.strip()
        if not line:
            continue
        label, sep, value = line.partition(":")
        label = label.strip().lower()
        label = "mobile" if label == "cell" else label
        # A colon only means "label: value" when what precedes it IS a label.
        # This used to keep the truncated value even when the label was
        # unrecognised, so anything with a colon in it lost everything before
        # the first one:
        #
        #     http://example.com   ->  //example.com     the scheme, gone
        #     3:30 meeting         ->  30 meeting        the hour, gone
        #
        # Typing a plain URL into a field and having it silently shortened is
        # the kind of loss nobody looks for afterwards. When the label is not
        # one of ours the whole line is the value, which is exactly what the
        # no-colon case already did.
        # ...and the field's own fallback counts as a label, because
        # labeled_text WRITES it: a phones field with no explicit label spells
        # itself "phone: 555-0100", and that has to read back as it was written.
        # Leaving it out broke the round trip for every unlabelled value — my
        # own new suite caught it before this shipped.
        if sep and value.strip() and (label in VALUE_LABELS
                                      or label == fallback):
            values = [value.strip()]
        else:
            label, values = fallback, [line]
        if split_values and ":" not in values[0]:
            values = [v for v in re.split(r"\s+", values[0]) if v]
        out.extend({"label": label, "value": v} for v in values)
    return out


def merge_labeled_values(original, edited):
    """Keep newer per-address fields while the visible label/value are edited.

    The form has one row per stored item and keeps their order, so an edited
    row remains the same address record even when its visible value changes.
    Newly added rows have no source metadata; deleted rows simply disappear.
    """
    source = original if isinstance(original, list) else []
    same_shape = len(source) == len(edited)
    unused = list(range(len(source)))
    out = []
    for i, value in enumerate(edited):
        match = i if same_shape else next((j for j in unused
            if isinstance(source[j], dict)
            and source[j].get("label") == value.get("label")
            and source[j].get("value") == value.get("value")), None)
        row = (dict(source[match]) if match is not None
               and isinstance(source[match], dict) else {})
        if match in unused:
            unused.remove(match)
        row.update(value)
        out.append(row)
    return out


def contact_matches(person, query):
    q = (query or "").strip().lower()
    if not q:
        return True
    text = [str(person.get(k, "")) for k in FIELD_KEYS]
    text += [v["value"] for k in ("phones", "emails")
             for v in person.get(k, [])]
    if q in " ".join(text).lower():
        return True
    digits = "".join(c for c in q if c.isdigit())
    return bool(digits and any(digits in "".join(c for c in v["value"]
                                                  if c.isdigit())
                               for v in person.get("phones", [])))


# Latin letters that carry their mark inside the glyph, which is why NFKD
# cannot lift it off. An address book files each one under the letter it is
# read as, so a name is where the reader's finger goes looking for it.
_FOLD_LETTERS = {"Ø": "O", "ø": "o", "Æ": "AE", "æ": "ae",
                 "Œ": "OE", "œ": "oe", "Đ": "D", "đ": "d",
                 "Ð": "D", "ð": "d", "Ł": "L", "ł": "l",
                 "Þ": "TH", "þ": "th", "ß": "ss", "İ": "I",
                 "ı": "i"}


def fold_name(text):
    """`text` with its accents taken off, for FILING only — what is displayed
    is always the name as it was typed. Sorting and grouping on the raw string
    put every accented initial after Z under a divider of its own, so
    "Émile Éluard" filed itself past "Zed Zane" instead of beside "Eve
    Evans", and "Øyvind" was nowhere near the O's. A name in a script with no
    Latin base (日本 太郎) comes back unchanged and keeps its own divider."""
    out = []
    for ch in unicodedata.normalize("NFKD", text or ""):
        if unicodedata.combining(ch):
            continue
        out.append(_FOLD_LETTERS.get(ch, ch))
    return "".join(out)


def sort_letter(name):
    """The divider a card files under: the base letter of its initial, or '#'
    for a name that starts with a digit, a symbol or nothing at all."""
    folded = fold_name((name or "").strip())
    return folded[0].upper() if folded[:1].isalpha() else "#"


def ordered_people(people, query=""):
    pairs = [(i, p) for i, p in enumerate(people) if contact_matches(p, query)]
    def filed(p):
        return fold_name(p.get("name") or "").lower()
    pairs.sort(key=lambda ip: (filed(ip[1]), not ip[1].get("favorite")))
    # Favorites come first inside each initial group, while letters remain A-Z.
    pairs.sort(key=lambda ip: (sort_letter(ip[1].get("name")),
                               not ip[1].get("favorite"),
                               filed(ip[1])))
    return pairs


def next_after_delete(people, index, query=""):
    """The record that takes `index`'s PLACE IN THE LIST when it is deleted:
    the row after it in the visible order, or the row before it when the last
    row goes. None when the deleted card is not on screen (a filter is hiding
    it) or nothing is left to select."""
    order = [i for i, _p in ordered_people(people, query)]
    if index not in order:
        return None
    pos = order.index(index)
    after = order[pos + 1:] + order[:pos][::-1]
    return people[after[0]] if after else None


def _vc_escape(value):
    return (str(value).replace("\\", "\\\\").replace("\n", "\\n")
            .replace(";", "\\;").replace(",", "\\,"))


def _vc_unescape(value):
    out, escaped = [], False
    for ch in value:
        if escaped:
            out.append("\n" if ch in "nN" else ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            out.append(ch)
    if escaped:
        out.append("\\")
    return "".join(out)


def _vc_split(value, delimiter=";"):
    parts, buf, escaped = [], [], False
    for ch in value:
        if escaped:
            buf.extend(("\\", ch)); escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == delimiter:
            parts.append(_vc_unescape("".join(buf))); buf = []
        else:
            buf.append(ch)
    if escaped:
        buf.append("\\")
    parts.append(_vc_unescape("".join(buf)))
    return parts


def export_vcards(people):
    cards = []
    for p in people:
        name = p.get("name", "")
        words = name.split()
        family, given = (words[-1], " ".join(words[:-1])) if len(words) > 1 \
            else (name, "")
        lines = ["BEGIN:VCARD", "VERSION:3.0", "FN:" + _vc_escape(name),
                 "N:%s;%s;;;" % (_vc_escape(family), _vc_escape(given))]
        for v in p.get("phones", []):
            lines.append("TEL;TYPE=%s:%s" % (v["label"].upper(),
                                              _vc_escape(v["value"])))
        for v in p.get("emails", []):
            lines.append("EMAIL;TYPE=%s:%s" % (v["label"].upper(),
                                                _vc_escape(v["value"])))
        # TITLE carries the card's Role. Leaving it out meant Export All
        # vCards — the only whole-book copy this app can write that reads
        # back in — dropped what every person in the book DOES, silently:
        # exporting and re-importing gave an address book with every role
        # blank, and nothing said so.
        for prop, key in (("TITLE", "role"), ("ORG", "organization"),
                          ("ADR", "address"), ("NOTE", "notes"),
                          ("BDAY", "bday")):
            if p.get(key):
                value = p[key]
                if prop == "ADR":
                    lines.append("ADR:;;%s;;;;" % _vc_escape(value))
                    continue
                lines.append(prop + ":" + _vc_escape(value))
        lines.append("END:VCARD")
        cards.append("\r\n".join(lines))
    return "\r\n".join(cards) + ("\r\n" if cards else "")


def parse_vcards(text):
    raw = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = []
    for line in raw:
        if (lines and lines[-1].endswith("=")
                and "ENCODING=QUOTED-PRINTABLE" in
                lines[-1].split(":", 1)[0].upper()):
            lines[-1] = lines[-1][:-1] + line.lstrip(" \t")
        elif line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    cards, current = [], None
    for line in lines:
        upper = line.upper()
        if upper == "BEGIN:VCARD":
            current = normalize_person({}, len(cards)); continue
        if upper == "END:VCARD":
            if current is not None:
                cards.append(current)
            current = None; continue
        if current is None or ":" not in line:
            continue
        left, value = line.split(":", 1)
        bits = left.split(";"); prop = bits[0].upper()
        params = ";".join(bits[1:])
        # What kind of number or address this is. vCard 3.0 spells it as a
        # TYPE= parameter ("TEL;TYPE=CELL:"); vCard 2.1 — what older phones,
        # Outlook and most phone-to-file exports write — spells it as a BARE
        # parameter ("TEL;CELL:", "EMAIL;INTERNET;WORK:"). Only TYPE= was read,
        # so every number in such a file imported as Home and an address book
        # came across with no mobile numbers in it at all.
        label = "home"
        for part in re.split(r"[;,]", params):
            got = part.strip().lower()
            if got.startswith("type="):
                got = got[5:].strip()
            got = "mobile" if got == "cell" else got
            if got in VALUE_LABELS:
                label = got
                break
        if re.search(r"(?:^|;)ENCODING=QUOTED-PRINTABLE(?:;|$)", params,
                     re.IGNORECASE):
            charset_match = re.search(r"(?:^|;)CHARSET=([^;:]+)", params,
                                      re.IGNORECASE)
            charset = charset_match.group(1) if charset_match else "utf-8"
            decoded = quopri.decodestring(value.encode("utf-8"))
            try:
                value = decoded.decode(charset, "replace")
            except (LookupError, UnicodeError):
                value = decoded.decode("utf-8", "replace")
        value = _vc_unescape(value)
        if prop == "TITLE": current["role"] = value
        # vCard also has ROLE (the function, where TITLE is the job title).
        # Cards written elsewhere carry one or the other; take ROLE only
        # when no TITLE has already named the same thing.
        elif prop == "ROLE" and not current["role"]:
            current["role"] = value
        elif prop == "FN": current["name"] = value
        elif prop == "N" and not current["name"]:
            n = _vc_split(value)
            current["name"] = " ".join(x for x in (n[1], n[0]) if x)
        elif prop == "TEL": current["phones"].append({"label": label,
                                                        "value": value})
        elif prop == "EMAIL": current["emails"].append({"label": label,
                                                          "value": value})
        elif prop == "ORG": current["organization"] = value
        elif prop == "ADR":
            current["address"] = "\n".join(x for x in _vc_split(value) if x)
        elif prop == "NOTE": current["notes"] = value
        elif prop == "BDAY": current["bday"] = value
    return cards


def read_vcard_text(path, limit=MAX_VCARD_BYTES):
    """Read one selected vCard without letting it exhaust the GTK process."""
    with open(path, "rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("vCard is too large")
    return raw.decode("utf-8-sig")


def merge_contacts(existing, incoming, stats=None):
    """Merge exact-name imports, filling blanks and retaining list conflicts.

    `stats`, when a dict is passed, is filled with how many cards were "added"
    and how many existing cards were "updated" by the merge. The import status
    line used to count the cards in the FILE, so re-importing an export said
    "Imported 11 contacts" while nothing at all had changed.
    """
    # Import runs on the GTK thread. Rescanning the whole address book for each
    # vCard made a large but ordinary import quadratic (10,000 into 10,000 is
    # 100 million name comparisons) and left the window apparently hung.
    # setdefault preserves the old rule when the book already contains the
    # same name twice: the first matching record receives the merge.
    by_name = {}
    added = updated = 0
    for person in existing:
        by_name.setdefault(person.get("name"), person)
    for got in incoming:
        target = by_name.get(got.get("name"))
        if target is None:
            target = copy.deepcopy(got)
            existing.append(target)
            by_name.setdefault(target.get("name"), target)
            added += 1
            continue
        changed = False
        for key in FIELD_KEYS:
            if not target.get(key) and got.get(key):
                target[key] = got[key]
                changed = True
        for key in ("phones", "emails"):
            seen = {(v["label"], v["value"]) for v in target.get(key, [])}
            values = target.setdefault(key, [])
            for value in got.get(key, []):
                identity = (value["label"], value["value"])
                if identity in seen:
                    continue
                values.append(copy.deepcopy(value))
                seen.add(identity)
                changed = True
        updated += 1 if changed else 0
    if stats is not None:
        stats["added"] = added
        stats["updated"] = updated
    return existing


def _vc_menu(text):
    """Translate a vCard menu label while preserving the standard brand case."""
    return _t(text)


def _is_css_color(text):
    """True when GTK can read `text` as a colour (any spelling it accepts:
    '#8A857A', 'red', 'rgb(1,2,3)'). Used to keep an unreadable colour out of
    the avatar stylesheet, where it would raise rather than just look wrong."""
    if not text:
        return False
    try:
        return bool(Gdk.RGBA().parse(text))
    except Exception:
        return False


def _month_index(text):
    """A month named in words -> 1-12, in English or the running language."""
    low = text.lower()
    for i, name in enumerate(MONTH_NAMES):
        if low == name or low == name[:3]:
            return i + 1
    for i, name in enumerate(MONTH_NAMES):
        trans = _t(name.capitalize()).lower()
        if low == trans or low == trans[:3]:
            return i + 1
    return None


def parse_birthday(text):
    """(month, day) from whatever somebody typed in the Birthday field, or None.

    The field is free text and always has been, so this reads the shapes people
    actually write — "14/03/1948", "14 March", "March 14, 1948", "1948-03-14" —
    and gives up quietly on anything else rather than guessing. Day-first is
    assumed when both numbers could be either, matching the date format the rest
    of the OS writes ("25 July 2026")."""
    text = (text or "").strip()
    if not text:
        return None
    word = _WORD_RE.search(text)
    if word:
        m = _month_index(word.group(0))
        if m is None:
            return None
        nums = [int(n) for n in _DIGITS_RE.findall(text)]
        days = [n for n in nums if 1 <= n <= 31]
        if not days:
            return None
        return _valid(m, days[0])
    m = _BDAY_NUM_RE.match(text)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    c = m.group(3)
    if a > 31:                                   # a leading year: 1948-03-14
        return _valid(b, int(c)) if c else None
    if a > 12:                                   # day first, unambiguously
        return _valid(b, a)
    if b > 12:                                   # "12/25/1990" — month first
        # The second number is past twelve, so it cannot be a month and the
        # order can only be M/D. This used to be tested only when no year
        # followed, which meant the full American form "12/25/1990" — Christmas,
        # a real date typed by half the world — parsed as month 25 and came back
        # None. The card still showed the text, so nothing looked wrong; that
        # person was just silently missing from the birthdays-soon banner.
        return _valid(a, b)
    return _valid(b, a)                          # ambiguous -> day first


def _valid(month, day):
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    try:
        date(2024, month, day)                   # a leap year, so 29 Feb passes
    except ValueError:
        return None
    return (month, day)


def days_until_birthday(text, today=None):
    """How many days until the next anniversary of `text` (0 = today), or None
    when it carries no readable date. A 29 February birthday falls back to the
    1st of March in a year that has no 29th."""
    got = parse_birthday(text)
    if got is None:
        return None
    month, day = got
    today = today or date.today()
    for year in (today.year, today.year + 1):
        try:
            nxt = date(year, month, day)
        except ValueError:
            nxt = date(year, 3, 1)               # 29 Feb in a common year
        if nxt >= today:
            return (nxt - today).days
    return None


def birthday_phrase(days):
    """When a birthday falls, as a whole sentence — 'Birthday today', 'Birthday
    on Tuesday', 'Birthday in 12 days'. Written out in full rather than glued
    together from fragments so each one can be translated as a phrase."""
    if days == 0:
        return _t("Birthday today")
    if days == 1:
        return _t("Birthday tomorrow")
    if days < 7:
        return _t("Birthday on %s") % _t(
            DAY_NAMES[(date.today() + timedelta(days=days)).weekday()])
    if days == 7:
        return _t("Birthday in a week")
    return _t("Birthday in %d days") % days

class Contacts(nbapp.AppWindow):
    app_name = "Contacts"
    menus = ("File", "Edit", "Card", "View")

    def __init__(self):
        super().__init__()
        self._install_css()

        # Set before anything can arm a timer or touch a widget: every deferred
        # callback (search debounce, status clear) reads this to decide whether
        # the window it belongs to is still there.
        self._closed = False
        self._quarantine_pending = False
        self._extra = {}
        self._deleted = None

        # Load the saved address book (empty on a fresh device — nothing is
        # seeded). Normalization guarantees every render / edit / delete /
        # search path sees the same full-key shape _new_contact() produces.
        self.people = self._load_people()
        self.active = 0
        self.editing = False
        self.search_text = ""
        self._entries = {}    # key -> Gtk.Entry, live only while editing
        self._notes_view = None       # multi-line Notes TextView, live while editing
        self._addr_view = None        # multi-line Address TextView, ditto
        self._pending_new = False     # active card is a just-created, untouched one
        self._search_timer = 0        # pending search-debounce source (0 = none)
        self._status_timer = 0        # pending status-line clear source (0 = none)
        self._avatar_css_cache = {}   # (color,size,fontsize) -> Gtk.CssProvider
        self._card_col = None         # the card column, refitted on resize
        self._card_w = 0              # its current width (0 = not fitted yet)
        self._row_widgets = {}        # index -> row button, for in-place select

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_hexpand(True)
        body.set_vexpand(True)
        self.content.pack_start(body, True, True, 0)

        body.pack_start(self._build_list_pane(), False, False, 0)
        body.pack_start(self._build_detail_pane(), True, True, 0)

        self._rebuild_list()
        self._rebuild_detail()

        # Final flush on close so the last add/edit/delete is never lost — File
        # ▸ Close / the logo / Esc all route through "destroy".
        self._delete_pending = False
        self.connect("delete-event", self._on_delete)
        self.connect("destroy", self._on_destroy)

    # -------------------------------------------------------- persistence
    def _load_people(self):
        """Return the saved address book, normalized to the canonical card
        shape _new_contact() produces (every field key present, plus a palette
        colour). Ships EMPTY: a missing file (fresh device), an empty file, or
        a malformed file all load as the "No contacts" state — no records are
        ever seeded."""
        try:
            data = _read_contacts_json()
        except FileNotFoundError:
            return []
        except ContactsStoreTooLarge:
            # A valid oversized object evades the shared parse-damage guard;
            # route it through this app's preservation gate before any empty
            # fallback address book is allowed to replace it.
            self._quarantine_pending = True
            return []
        except Exception:
            # Unreadable bytes. nbapp.atomic_write_json moves the original
            # aside (preserve_damaged) immediately before its next replacing
            # write, so the address book's bytes land in
            # contacts.json.damaged-* and persistence KEEPS WORKING. Gating
            # every later save kept the file but silently killed saving for
            # the session — journal shipped that cure and the save-failure
            # gate caught it.
            return []
        # Top-level keys a NEWER build may have added ride through the save
        # untouched (accounting's _extra idiom): rebuilding the file from only
        # the keys this build knows silently deletes the rest.
        self._extra = ({k: v for k, v in data.items() if k != "people"}
                       if isinstance(data, dict) else {})
        raw = data.get("people") if isinstance(data, dict) else data
        if raw is None and isinstance(data, dict):
            # The wrapper key is gone or was written under another name. The
            # cards are still in the file; take the first list of records
            # instead of opening on "No contacts" and saving that over them.
            for v in data.values():
                if isinstance(v, list) and any(isinstance(x, dict) for x in v):
                    raw = v
                    break
        # The address book stored as an object keyed by name or id: its values
        # are still the user's cards. Rejecting the wrapper used to open the app
        # on "No contacts", and _on_destroy then wrote that empty book over the
        # only copy of every number in it — so read what is there and let a bad
        # record cost itself alone.
        if isinstance(raw, dict):
            raw = list(raw.values())
        if not isinstance(raw, list):
            if data and data != {"people": []}:
                # Parsed fine, but nothing in it reads as an address book — a
                # shape only this app can judge. _save moves the file aside
                # immediately before its first replacing write, so there is
                # never a window with no store and the bytes end up where the
                # OS contract says. An empty dict or empty people list is OUR
                # OWN empty book, not somebody's data in a foreign shape.
                self._quarantine_pending = True
            return []
        return [self._normalize_person(p, i)
                for i, p in enumerate(raw) if isinstance(p, dict)]

    @staticmethod
    def _normalize_person(p, i):
        """Coerce one loaded record into the canonical card shape so every field
        key is present (as a string) and a palette colour is guaranteed."""
        return normalize_person(p, i)

    def _quarantine(self):
        """Move a contacts file this app could not read AS AN ADDRESS BOOK
        aside, under the same <name>.damaged-<stamp> name
        nbapp.preserve_damaged uses. nbapp quarantines a store that fails to
        PARSE on every write; it deliberately cannot cover this case — valid
        JSON of the wrong shape parses perfectly, and only this app knows the
        shape is not an address book."""
        if not os.path.exists(CONTACTS_FILE):
            return True
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dest = "%s.damaged-%s" % (CONTACTS_FILE, stamp)
            n = 2
            while os.path.exists(dest):
                dest = "%s.damaged-%s-%d" % (CONTACTS_FILE, stamp, n)
                n += 1
            os.replace(CONTACTS_FILE, dest)
        except OSError:
            return False
        return True

    def _save(self):
        """Persist the full address book. Never raises, so a bad write cannot
        crash the app — but it does SAY when the write failed."""
        try:
            if getattr(self, "_quarantine_pending", False):
                if not self._quarantine():
                    raise OSError("could not preserve the unrecognized address book")
                self._quarantine_pending = False
            payload = dict(getattr(self, "_extra", None) or {})
            payload["people"] = self.people
            nbapp.atomic_write_json(CONTACTS_FILE, payload)
            self._save_warned = False
            self._last_store_error = None
            return True
        except Exception as exc:
            self._last_store_error = exc
            # See academics._save_to_disk. This used to be a bare `pass`, and a
            # full disk or a read-only filesystem then looked exactly like
            # "Contacts lost my address book": the file keeps whatever the last
            # write that worked put there, so the people added since simply are
            # not there the next time it opens. Warn once per run of failures so
            # a jammed disk does not strobe the status line.
            if not getattr(self, "_save_warned", False):
                self._save_warned = True
                try:
                    self._flash(nbapp.save_failure_reason(exc, CONTACTS_FILE))
                except Exception:
                    pass
            return False

    def _on_destroy(self, *_):
        # Idempotent, and it runs first: "destroy" can reach this handler more
        # than once (File ▸ Close on an already-closing window, a second
        # teardown pass at Shut Down), and the final commit/save below must
        # happen exactly once — twice would re-run _commit_edits against dead
        # entries and write again for nothing.
        if self._closed:
            return
        self._closed = True

        # Cancel both deferred sources BEFORE anything else. The 130ms search
        # debounce and the 3s status clear both outlive the window otherwise:
        # GLib keeps the callback (and through it this Contacts and its whole
        # widget tree) alive to the deadline, then _search_timeout tears down
        # and re-realizes a list inside a destroyed window. Clear the id first
        # so a failed removal still leaves nothing to fire against.
        for attr in ("_search_timer", "_status_timer"):
            sid = getattr(self, attr, 0)
            setattr(self, attr, 0)
            if sid:
                try:
                    GLib.source_remove(sid)
                except Exception:
                    pass

        # Capture any in-progress edit before the final write. The inline field
        # entries only push into self.people via _commit_edits (Done / switch /
        # New / Enter); a value typed but not yet committed lives solely in the
        # Gtk.Entry. Those entries are still alive while this destroy handler
        # runs, so commit them here — otherwise closing the window (or Shut
        # Down) mid-edit would silently discard the last keystrokes even though
        # Contacts always-saves.
        if self.editing:
            try:
                self._commit_edits()   # also writes to disk
            except Exception:
                pass
        try:
            self._finish_new_card()    # don't persist an untouched New Contact
        except Exception:
            pass
        self._save()

    def _on_delete(self, *_):
        """Keep widget-only field edits alive when their final write fails --
        and say why, with a way out (nbapp.close_unsaved_card). A bare veto
        was a window that could not be closed on a full disk."""
        if self.editing and not self._commit_edits():
            return not nbapp.close_unsaved_card(
                self, getattr(self, "_last_store_error", None), CONTACTS_FILE)
        return False

    # ---------------------------------------------------------------- list
    def _build_list_pane(self):
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pane.set_size_request(360, -1)
        pane.get_style_context().add_class("listpane")

        # search header — a boxed field with a quiet "add" affordance beside it
        sbwrap = Gtk.Box(spacing=8)
        sbwrap.get_style_context().add_class("searchheader")
        searchbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        searchbox.get_style_context().add_class("searchbox")
        icon = nbicons.image("search", 16, MUTED2)
        icon.set_valign(Gtk.Align.CENTER)
        icon.set_margin_start(10)
        searchbox.pack_start(icon, False, False, 0)
        self.search = Gtk.Entry()
        self.search.set_has_frame(False)
        self.search.set_placeholder_text(_t("Search contacts"))
        self.search.get_style_context().add_class("searchentry")
        self.search.connect("changed", self._on_search)
        self.search.connect("activate", self._on_search_activate)
        searchbox.pack_start(self.search, True, True, 0)
        sbwrap.pack_start(searchbox, True, True, 0)

        # new-contact affordance — quiet borderless glyph
        newbtn = Gtk.Button()
        newbtn.set_relief(Gtk.ReliefStyle.NONE)
        newbtn.get_style_context().add_class("newbtn")
        newbtn.set_tooltip_text(_t("New contact"))
        newbtn.set_valign(Gtk.Align.CENTER)
        newbtn.add(nbicons.image("plus", 18, MUTED2))
        newbtn.connect("clicked", self._new_contact)
        sbwrap.pack_start(newbtn, False, False, 0)
        pane.pack_start(sbwrap, False, False, 0)

        # Whose birthday is coming. An address book that quietly knows this and
        # never says so is a filing cabinet; this is the one thing it can tell
        # you without being asked. Empty (and takes no space) when nobody's is
        # near, so it is never permanent furniture.
        self.bday_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.bday_bar.set_no_show_all(True)
        pane.pack_start(self.bday_bar, False, False, 0)

        # scrollable list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.list_box.set_margin_start(10); self.list_box.set_margin_end(10)
        self.list_box.set_margin_top(8); self.list_box.set_margin_bottom(16)
        scroll.add(self.list_box)
        pane.pack_start(scroll, True, True, 0)

        # transient status line (empty at rest). File ▸ Export to PDF surfaces
        # its result here — "Exported to Documents" on success, a neutral
        # error otherwise — then clears after a moment.
        self.status_lbl = Gtk.Label(label="", xalign=0)
        self.status_lbl.get_style_context().add_class("statusline")
        self.status_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        # Hidden until something is actually reported: an empty status line
        # still drew its rule and padding, so the list sat above a blank strip.
        self.status_lbl.set_no_show_all(True)
        pane.pack_start(self.status_lbl, False, False, 0)
        return pane

    def _rebuild_list(self):
        # A direct (structural) rebuild supersedes any pending debounced search
        # rebuild, so the two never race into a redundant double teardown.
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = 0
        for c in self.list_box.get_children():
            self.list_box.remove(c)
        self._row_widgets = {}
        self._rebuild_bday_bar()

        entries = self._visible_order_pairs()

        if not entries:
            # Two different empty columns, and only one of them is "add your
            # first contact". An empty BOOK is answered by the card pane beside
            # this one, which names the space and points at the + button in this
            # column's own header; here it only needs the heading. A search that
            # matched nobody is this column's own state, and used to end at
            # "Nobody matches that" — naming the absence and leaving the reader
            # stuck with a filtered list and no stated way back.
            empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            empty.set_margin_top(40); empty.set_margin_bottom(40)
            if not self.people:
                head = Gtk.Label(label=_t("No contacts"))
            else:
                head = Gtk.Label(label=_t("No matching contacts"))
            head.get_style_context().add_class("listempty")
            empty.pack_start(head, False, False, 0)
            if self.people:
                hint = Gtk.Label(label=_t("Clear the search to see all contacts."))
                hint.get_style_context().add_class("listempty")
                hint.set_line_wrap(True)
                hint.set_max_width_chars(22)
                hint.set_justify(Gtk.Justification.CENTER)
                empty.pack_start(hint, False, False, 0)
            self.list_box.pack_start(empty, False, False, 0)
            self.list_box.show_all()
            return

        # Alphabetical, flat as the design draws it — with letter dividers once
        # the book is long enough that a flat run of hundreds of names cannot be
        # scanned (and never while a search is narrowing it anyway).
        grouped = (len(entries) >= GROUP_FROM and not self.search_text.strip())
        letter = None
        for i, p in entries:
            if grouped:
                first = self._sort_letter(p)
                if first != letter:
                    letter = first
                    self.list_box.pack_start(self._letter_head(first),
                                             False, False, 0)
            row = self._contact_row(i, p)
            self._row_widgets[i] = row
            self.list_box.pack_start(row, False, False, 0)
        self.list_box.show_all()

    @staticmethod
    def _sort_letter(p):
        """The divider a card files under. The one the ORDER is built from
        (sort_letter), so a name cannot be sorted under E and then given a
        divider of its own two rows later."""
        return sort_letter(p.get("name"))

    def _letter_head(self, letter):
        # The divider is one letter of a name the user typed, and a single
        # letter is a catalog key in every language: "B" is the Bold button,
        # so an address book of 40+ contacts drew its B section as "G" in
        # French, "Ж" in Russian and "דיק" in Yiddish. It is the user's
        # alphabet, not ours.
        lbl = Gtk.Label(xalign=0)
        nbi18n.set_verbatim(lbl, letter)
        lbl.get_style_context().add_class("letterhead")
        return lbl

    # ------------------------------------------------------------- birthdays
    def _birthdays_soon(self):
        """(days away, index) for everyone whose birthday falls within the next
        BIRTHDAY_SOON days, soonest first."""
        out = []
        for i, p in enumerate(self.people):
            days = days_until_birthday(p.get("bday", ""))
            if days is not None and days <= BIRTHDAY_SOON:
                out.append((days, i))
        out.sort()
        return out

    def _rebuild_bday_bar(self):
        for c in self.bday_bar.get_children():
            self.bday_bar.remove(c)
        soon = self._birthdays_soon()
        if not soon:
            self.bday_bar.hide()
            return
        days, idx = soon[0]
        person = self.people[idx]
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("bdayrow")
        btn.connect("clicked", lambda *_: self._select(idx))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(xalign=0)
        _set_person_text(name, person.get("name"), "Unnamed")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.get_style_context().add_class("bdayname")
        col.pack_start(name, False, False, 0)
        note = birthday_phrase(days)
        if len(soon) > 1:
            note += "  ·  " + _t("%d more soon") % (len(soon) - 1)
        sub = Gtk.Label(label=note, xalign=0)
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.get_style_context().add_class("bdaynote")
        col.pack_start(sub, False, False, 0)
        btn.add(col)
        self.bday_bar.pack_start(btn, False, False, 0)
        self.bday_bar.show()
        btn.show_all()

    def _matches(self, p, q):
        """True when card `p` matches the lowercase query `q` across any of its
        text fields (name, role, phone, email, address, birthday, notes) — a
        novice searching a phone number or email expects a hit, not just names.
        An empty query matches everything."""
        return contact_matches(p, q)

    def _visible_order_pairs(self):
        """(index, card) pairs for the current filter, sorted alphabetically by
        name — the single source of truth for the list and for step/jump."""
        q = self.search_text.strip().lower()
        return ordered_people(self.people, q)

    def _contact_row(self, i, p):
        row = Gtk.Button()
        row.set_relief(Gtk.ReliefStyle.NONE)
        row.get_style_context().add_class("contactrow")
        if i == self.active:
            row.get_style_context().add_class("selected")
        row.connect("clicked", lambda *_: self._select(i))

        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        hb.pack_start(self._avatar(p, 34, 13), False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(xalign=0)
        _set_person_text(name, p.get("name"), "Unnamed")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.get_style_context().add_class("rowname")
        col.pack_start(name, False, False, 0)
        if p.get("role"):
            role = Gtk.Label(xalign=0)
            _set_person_text(role, p["role"])
            role.set_ellipsize(Pango.EllipsizeMode.END)
            role.get_style_context().add_class("rowrole")
            col.pack_start(role, False, False, 0)
        hb.pack_start(col, True, True, 0)
        if p.get("favorite"):
            star = Gtk.Label(label="★")
            star.set_tooltip_text(_t("Favorite contact"))
            hb.pack_end(star, False, False, 0)
        row.add(hb)
        return row

    def _avatar(self, p, size, fontsize):
        lbl = Gtk.Label()
        nbi18n.set_verbatim(lbl, self._initials(p["name"]))
        lbl.get_style_context().add_class("avatar")
        lbl.set_size_request(size, size)
        lbl.set_halign(Gtk.Align.CENTER); lbl.set_valign(Gtk.Align.CENTER)
        lbl.get_style_context().add_provider(
            self._avatar_css(p.get("color", "#8A857A"), size, fontsize),
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)
        return lbl

    def _avatar_css(self, color, size, fontsize):
        """Cached CssProvider for one avatar style. A provider is safe to share
        across widgets, so load_from_data (a fresh CSS parse) runs ~once per
        palette colour instead of once per row on every list rebuild."""
        key = (color, size, fontsize)
        prov = self._avatar_css_cache.get(key)
        if prov is None:
            prov = Gtk.CssProvider()
            prov.load_from_data(
                ("label { background:%s; color:#fff; border-radius:%dpx; "
                 "font-size:%dpx; font-weight:600; }"
                 % (color, size, fontsize)).encode())
            self._avatar_css_cache[key] = prov
        return prov

    @staticmethod
    def _initials(name):
        parts = [w for w in name.split() if w and w[0].isalpha()]
        return "".join(w[0] for w in parts[:2]).upper() or "?"

    # -------------------------------------------------------------- detail
    def _build_detail_pane(self):
        scroll = Gtk.ScrolledWindow()
        # EXTERNAL, not NEVER, horizontally: NEVER hands the card's full width
        # up as the window's minimum width, which on a 1024-wide screen pushed
        # the Edit button off the right edge with no way to reach it. The card
        # is fitted to the pane instead (_fit_card), so nothing is ever
        # scrolled out of sight sideways.
        scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("detailwrap")
        self.detail_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.detail_holder.set_hexpand(True); self.detail_holder.set_vexpand(True)
        scroll.add(self.detail_holder)
        scroll.connect("size-allocate", self._fit_card)
        return scroll

    def _fit_card(self, _w, alloc):
        """Keep the contact card at its 760px reading measure while the pane is
        wide enough, and narrow it to fit on a small screen. Writing the same
        width twice is a no-op, so this settles instead of looping."""
        w = max(300, min(CARD_W, alloc.width - 2 * CARD_MARGIN))
        if w != self._card_w:
            self._card_w = w
            col = self._card_col
            if col is not None:
                col.set_size_request(w, -1)

    def _rebuild_detail(self):
        for c in self.detail_holder.get_children():
            self.detail_holder.remove(c)
        self._entries = {}    # rebuilt fresh; populated below when editing
        self._notes_view = None
        self._addr_view = None
        self._card_col = None  # re-set below when a card (not the empty state)

        if not self.people:
            empty = Gtk.Label(label=_t("No contacts. Add one with +."))
            empty.get_style_context().add_class("detailempty")
            empty.set_hexpand(True); empty.set_vexpand(True)
            empty.set_halign(Gtk.Align.CENTER); empty.set_valign(Gtk.Align.CENTER)
            self.detail_holder.pack_start(empty, True, True, 0)
            self.detail_holder.show_all()
            return

        a = self.people[self.active]
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.set_halign(Gtk.Align.CENTER)
        self._card_col = col
        col.set_size_request(self._card_w or CARD_W, -1)
        col.set_margin_top(56); col.set_margin_bottom(80)
        col.set_margin_start(CARD_MARGIN); col.set_margin_end(CARD_MARGIN)

        # header
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=32)
        head.set_margin_bottom(44)
        head.pack_start(self._avatar(a, 96, 34), False, False, 0)

        idcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        idcol.set_valign(Gtk.Align.START)
        idcol.set_margin_top(6)
        if self.editing:
            nm = Gtk.Entry()
            nm.set_text(a.get("name", "") or "")
            nm.set_placeholder_text(_t("Name"))
            nm.get_style_context().add_class("nameentry")
            self._entries["name"] = nm
            nm.connect("activate", self._entry_activated)
            idcol.pack_start(nm, False, False, 0)
            rl = Gtk.Entry()
            rl.set_text(a.get("role", "") or "")
            rl.set_placeholder_text(_t("Role (optional)"))
            rl.get_style_context().add_class("roleentry")
            rl.set_margin_top(8)
            self._entries["role"] = rl
            rl.connect("activate", self._entry_activated)
            idcol.pack_start(rl, False, False, 0)
        else:
            nm = Gtk.Label(xalign=0)
            _set_person_text(nm, a.get("name"), "Unnamed")
            nm.get_style_context().add_class("bigname")
            nm.set_line_wrap(True)                 # a long name wraps, never
            nm.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)  # overflows the pane
            idcol.pack_start(nm, False, False, 0)
            if a.get("role"):
                rl = Gtk.Label(xalign=0)
                _set_person_text(rl, a["role"])
                rl.get_style_context().add_class("bigrole")
                rl.set_line_wrap(True)
                rl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                rl.set_margin_top(8)
                idcol.pack_start(rl, False, False, 0)
        head.pack_start(idcol, True, True, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btns.set_valign(Gtk.Align.START)
        edit = Gtk.Button(label=_t("Done") if self.editing else _t("Edit"))
        edit.set_relief(Gtk.ReliefStyle.NONE)
        edit.get_style_context().add_class("editbtn")
        if self.editing:
            edit.get_style_context().add_class("editon")
        edit.connect("clicked", self._toggle_edit)
        btns.pack_start(edit, False, False, 0)
        fav = Gtk.ToggleButton(label="★" if a.get("favorite") else "☆")
        fav.set_relief(Gtk.ReliefStyle.NONE)
        fav.set_active(bool(a.get("favorite")))
        fav_action = (_t("Remove from favorites") if a.get("favorite")
                      else _t("Add to favorites"))
        fav.set_tooltip_text(fav_action)
        fav.get_accessible().set_name(fav_action)
        # Kept so the handler can be blocked around a programmatic set_active
        # (see _sync_favorite_button).
        self._favorite_handler = fav.connect("clicked", self._toggle_favorite)
        self._favorite_button = fav
        btns.pack_start(fav, False, False, 0)
        head.pack_start(btns, False, False, 0)
        col.pack_start(head, False, False, 0)

        # fields
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        grid.get_style_context().add_class("fieldgrid")
        grid.pack_start(self._field_row(a, _t("Phones"), "phones"),
                        False, False, 0)
        grid.pack_start(self._field_row(a, _t("Emails"), "emails"),
                        False, False, 0)
        for label, key in FIELDS:
            grid.pack_start(self._field_row(a, _t(label), key), False, False, 0)
        col.pack_start(grid, False, False, 0)

        # notes
        nb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        nb.set_margin_top(34)
        nlbl = Gtk.Label(label=_t("NOTES"), xalign=0)
        nlbl.get_style_context().add_class("fieldlabel")
        nlbl.set_margin_bottom(12)
        nb.pack_start(nlbl, False, False, 0)
        if self.editing:
            # A real multi-line area: the read view and the PDF already lay
            # notes out over several lines, so the editor has to let a novice
            # actually type them (a single-line Entry silently flattened newlines).
            nframe, self._notes_view = self._text_area(
                a.get("notes", "") or "", 84, "notesedit")
            nb.pack_start(nframe, False, False, 0)
        else:
            has_notes = bool((a.get("notes", "") or "").strip())
            notes = Gtk.Label(xalign=0)
            _set_person_text(notes, a.get("notes", ""), "—")
            notes.set_line_wrap(True)
            notes.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            notes.set_max_width_chars(48)
            # max_width_chars caps only what the label ASKS for; filling the
            # card it wrapped at the card's width instead of the 48-character
            # measure, so a long note ran the full width of the page.
            notes.set_halign(Gtk.Align.START)
            notes.get_style_context().add_class("notestext")
            if not has_notes:
                # keep the empty state honest instead of a blank gap under NOTES,
                # matching the "—" the other fields show when empty
                notes.get_style_context().add_class("fieldempty")
            nb.pack_start(notes, False, False, 0)
        col.pack_start(nb, False, False, 0)

        self.detail_holder.pack_start(col, False, False, 0)
        self.detail_holder.show_all()

    def _text_area(self, text, height, css_class):
        """A framed multi-line editor — the shape Notes has always had, now
        shared with Address. Returns (frame, view).

        A bare TextView asks for its whole text as its MINIMUM width and never
        shrinks, so a wordy note made the card wider than the pane and carried
        the Done button off the right edge, with no scrollbar to reach it.
        Inside a ScrolledWindow the field shrinks to the card measure and
        wraps, exactly like the read view — NEVER would re-propagate that whole
        minimum, so the policy is AUTOMATIC. And GTK draws no border for a
        TextView's own CSS, so an unframed area is an invisible field: a
        heading, then blank paper. The frame carries the outline the entries
        have."""
        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD)
        view.set_accepts_tab(False)    # Tab leaves the field, never indents
        view.get_style_context().add_class(css_class)
        view.get_buffer().set_text(text)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(-1, height)
        scroll.add(view)
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.get_style_context().add_class("notesframe")
        frame.pack_start(scroll, True, True, 0)
        return frame, view

    def _field_row(self, a, label, key):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.get_style_context().add_class("fieldrow")
        lbl = Gtk.Label(label=label.upper(), xalign=0)
        lbl.get_style_context().add_class("fieldlabel")
        lbl.set_size_request(200, -1)
        lbl.set_valign(Gtk.Align.START)
        row.pack_start(lbl, False, False, 0)
        if self.editing and key == "address":
            # An address is several lines, and this app already knows it: the
            # read view wraps one, the PDF prints one, and an imported ADR is
            # joined with newlines. In a single-line Entry those breaks showed
            # as "↵" glyphs, anything typed at the end glued itself onto the
            # last line, and there was no way to start a new one at all. The
            # same framed area Notes uses, where Enter breaks the line and Tab
            # leaves the field.
            frame, self._addr_view = self._text_area(a.get(key, "") or "",
                                                     84, "fieldarea")
            row.pack_start(frame, True, True, 0)
        elif self.editing:
            ent = Gtk.Entry()
            ent.set_text(labeled_text(a.get(key, []), key) if key in
                         ("phones", "emails") else (a.get(key, "") or ""))
            ent.set_placeholder_text(VALUE_EXAMPLES.get(key, label))
            ent.get_style_context().add_class("fieldentry")
            self._entries[key] = ent
            ent.connect("activate", self._entry_activated)
            row.pack_start(ent, True, True, 0)
        else:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            if key in ("phones", "emails"):
                values = a.get(key, [])
                if not values:
                    val = Gtk.Label(label="—", xalign=0)
                    val.get_style_context().add_class("fieldempty")
                    col.pack_start(val, False, False, 0)
                for item in values:
                    line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                   spacing=8)
                    val = Gtk.Label(xalign=0)
                    nbi18n.set_verbatim(
                        val, item["value"] if key == "emails" else
                        "%s  %s" % (value_label_name(key, item["label"]),
                                    item["value"]))
                    val.set_selectable(True)
                    val.set_line_wrap(True)
                    # ...and break INSIDE the word when the word is the whole
                    # value. One long address has nowhere to break, so its
                    # minimum width became the card's: the card grew past the
                    # pane and carried Edit, the star and Copy off the right of
                    # a 1024-wide screen, where nothing could scroll to them
                    # (the pane deliberately has no horizontal scrollbar). The
                    # other fields have broken this way all along.
                    val.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    val.get_style_context().add_class("fieldval")
                    line.pack_start(val, True, True, 0)
                    copy_btn = Gtk.Button(label=_t("Copy"))
                    copy_btn.set_relief(Gtk.ReliefStyle.NONE)
                    # Beside the first line of the value, not stretched down
                    # the side of it: a long address now wraps to three lines,
                    # and a button that fills them reads as a panel.
                    copy_btn.set_valign(Gtk.Align.START)
                    copy_btn.set_tooltip_text(_t("Copy phone") if key ==
                                              "phones" else _t("Copy email"))
                    copy_btn.connect("clicked", self._copy_value, key,
                                     item["value"])
                    line.pack_end(copy_btn, False, False, 0)
                    col.pack_start(line, False, False, 0)
                row.pack_start(col, True, True, 0)
                return row
            val = Gtk.Label(xalign=0)
            _set_person_text(val, a.get(key, ""), "—")
            val.get_style_context().add_class("fieldval")
            val.set_line_wrap(True)                 # long email / address wraps
            val.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)  # instead of clipping
            if not (a.get(key, "") or ""):
                val.get_style_context().add_class("fieldempty")
            col.pack_start(val, False, False, 0)
            # A date on its own does not answer the question anyone actually
            # has about a birthday, which is how long they have got.
            if key == "bday":
                days = days_until_birthday(a.get("bday", ""))
                if days is not None and days <= BIRTHDAY_SOON:
                    note = Gtk.Label(label=birthday_phrase(days), xalign=0)
                    note.get_style_context().add_class("fieldnote")
                    col.pack_start(note, False, False, 0)
            row.pack_start(col, True, True, 0)
        return row

    # -------------------------------------------------------------- events
    def _on_search(self, entry):
        # Coalesce keystroke bursts: on swrast, tearing the whole list down and
        # re-realizing it on every character causes visible typing lag. Track
        # the query eagerly (so activate/step read the latest filter), but defer
        # the actual list rebuild until typing settles, cancelling any pending
        # rebuild first so only the final query is rendered.
        if self._closed:
            return   # the window is gone; nothing to filter and nothing to arm
        self.search_text = entry.get_text()
        if self._search_timer:
            GLib.source_remove(self._search_timer)
        self._search_timer = GLib.timeout_add(130, self._search_timeout)

    def _search_timeout(self):
        self._search_timer = 0
        if self._closed:
            return False   # window torn down inside the debounce — rebuild nothing
        self._rebuild_list()
        return False   # one-shot

    def _flush_pending_search(self):
        """Make the rendered rows answer the current entry before an action
        derives a target from the filter.  During the typing debounce,
        search_text is already current but the list still answers the previous
        query; navigation must never select a row the user cannot yet see."""
        if not self._search_timer:
            return False
        GLib.source_remove(self._search_timer)
        self._search_timer = 0
        if self._closed:
            return False
        self._rebuild_list()
        return True

    def _focus_search(self):
        """Ctrl+F / View ▸ Find — put the caret in the search field, wherever
        focus happens to be. The same command Journal and Academics bind."""
        try:
            self.search.grab_focus()
        except Exception:
            pass

    def _clear_search(self):
        """Drop the filter and show the whole book again; True when there was
        one to drop.

        Applied IMMEDIATELY rather than through the 130ms typing debounce:
        clearing is a single deliberate act, not a burst, and a filtered list
        that stays filtered for another eighth of a second after the key reads
        as a dropped keypress."""
        if not self.search_text and not self.search.get_text():
            return False
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = 0
        self.search_text = ""
        # set_text re-enters _on_search, which re-arms the debounce; clear it
        # again afterwards and rebuild once, here and now.
        self.search.set_text("")
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = 0
        self.search_text = ""
        self._rebuild_list()
        return True

    def _on_search_activate(self, *_):
        """Enter in the search box jumps to the first matching card, using the
        same filter/sort order as the visible list. No-op when nothing matches."""
        self._flush_pending_search()
        order = self._visible_order_pairs()
        if order:
            self._select(order[0][0])

    def _select(self, i):
        # If an edit is in progress, persist it into the CURRENT card before
        # switching away — the same write "Done" performs — so moving to another
        # contact never silently discards the uncommitted edit. Commit while
        # self.active/self._entries still point at the card being left, then drop
        # back to the read view: another contact should read as a card, not a form.
        structural = self.editing        # a committed edit can re-sort the list
        if self.editing:
            self._commit_edits()
            self.editing = False
        # An untouched New Contact left behind here would linger as a blank
        # "Unnamed" row; drop it. The pending card is always the last-appended
        # one, so its removal only ever shifts indexes at/after it.
        dropped = self._finish_new_card()
        if dropped is not None:
            structural = True
            if dropped < i:
                i -= 1
        was = self.active
        if self.people:
            self.active = max(0, min(i, len(self.people) - 1))
        else:
            self.active = 0
        # Picking a name out of the list changes which row is highlighted and
        # nothing else, so move the highlight instead of rebuilding every row.
        # With 500 contacts the rebuild cost 114 ms on every single click; this
        # is two style-class changes.
        if structural or not self._move_highlight(was):
            self._rebuild_list()
        self._rebuild_detail()

    def _move_highlight(self, was):
        """Shift the .selected class from row `was` to the active one. False
        when the rows on screen are not the ones we hold handles for (a fresh
        pane, a filtered list that no longer holds the target), in which case
        the caller falls back to a full rebuild."""
        rows = self._row_widgets
        if not rows or self.active not in rows:
            return False
        old = rows.get(was)
        if old is not None:
            old.get_style_context().remove_class("selected")
        rows[self.active].get_style_context().add_class("selected")
        return True

    def _toggle_edit(self, *_):
        if self.editing:            # leaving edit mode: persist the entries
            if not self._commit_edits():
                return
            self.editing = False
            self._finish_new_card()  # a New Contact tapped Done blank is dropped
        else:
            self.editing = True
        self._rebuild_detail()
        # Only a FINISHED edit can have changed a name or role, and so the
        # rows; opening the form changes nothing in the list, and rebuilding it
        # there cost ~95 ms of nothing on a five-hundred-contact book.
        if not self.editing:
            self._rebuild_list()
        if self.editing:
            # Land the cursor in the name field so editing starts by typing,
            # not by hunting for a field to click.
            nm = self._entries.get("name")
            if nm is not None:
                try:
                    nm.grab_focus()
                    nm.set_position(-1)
                except Exception:
                    pass

    def _commit_edits(self):
        if not (0 <= self.active < len(self.people)):
            return False
        a = self.people[self.active]
        before = copy.deepcopy(a)
        for key, ent in self._entries.items():
            if key in ("phones", "emails"):
                values = parse_labeled_text(
                    ent.get_text(), "mobile" if key == "phones" else "home",
                    split_values=(key == "emails"))
                a[key] = merge_labeled_values(a.get(key), values)
            else:
                a[key] = ent.get_text()
        # The two multi-line fields hold their text in a TextBuffer, not in
        # self._entries: read them the same way.
        for key, view in (("address", getattr(self, "_addr_view", None)),
                          ("notes", self._notes_view)):
            if view is None:
                continue
            buf = view.get_buffer()
            a[key] = buf.get_text(buf.get_start_iter(), buf.get_end_iter(),
                                  False)
        if not self._save():
            # Preserve object identity: selection and any open action retain a
            # reference to this dictionary. Done stays in edit mode so the
            # visible field values remain available for a retry.
            a.clear()
            a.update(before)
            return False
        return True

    def _is_blank(self, p):
        """True when a card carries no user content at all (every text field
        empty). Used to recognise an untouched New Contact."""
        return (not any((p.get(k) or "").strip() for k in FIELD_KEYS)
                and not p.get("phones") and not p.get("emails"))

    def _finish_new_card(self):
        """Drop a just-created card the user never filled in, so navigating away
        from an untouched New Contact leaves no blank 'Unnamed' record behind.
        Only ever removes a card born via New Contact (self._pending_new) — never
        a pre-existing card the user cleared, which would be silent data loss.
        Returns the removed index, or None."""
        dropped = None
        if (self._pending_new and 0 <= self.active < len(self.people)
                and self._is_blank(self.people[self.active])):
            dropped = self.active
            person = self.people.pop(self.active)
            if self.active >= len(self.people):
                self.active = max(0, len(self.people) - 1)
            if not self._save():
                self.people.insert(dropped, person)
                self.active = dropped
                # Keep ownership of this placeholder so a later Done/New can
                # retry its removal. Clearing the flag here let a failed blank
                # become an ordinary immortal "Unnamed" contact.
                return None
        self._pending_new = False
        return dropped

    def _edit_widget(self, key):
        """The widget one edit field is typed into: an Entry for most of them,
        the framed multi-line area for Address."""
        if key == "address":
            return getattr(self, "_addr_view", None)
        return self._entries.get(key)

    def _entry_activated(self, entry, *_):
        """Enter advances to the next field (Tab-like) so a card fills in
        top-to-bottom without dropping out of edit mid-form; from the last
        single-line field it steps into the multi-line Notes area. Only the true
        end-of-form (no next field, no notes) commits — deferred to idle so the
        entry finishes emitting 'activate' before the pane is rebuilt under it."""
        if not self.editing:
            return
        order = [k for k in EDIT_ORDER if self._edit_widget(k) is not None]
        cur = next((k for k in order if self._entries.get(k) is entry), None)
        if cur is not None:
            i = order.index(cur)
            if i + 1 < len(order):
                nxt = self._edit_widget(order[i + 1])
                if nxt is not None:
                    nxt.grab_focus()
                    try:
                        nxt.set_position(-1)
                    except Exception:
                        pass
                    return
            if self._notes_view is not None:
                self._notes_view.grab_focus()
                return
        GLib.idle_add(self._commit_and_finish)

    def _commit_and_finish(self):
        if self.editing:
            self._toggle_edit()
        return False   # one-shot idle

    def _new_contact(self, *_):
        # If an edit is already in progress, persist it into the current card
        # before creating a new one — the same write "Done" performs — so making
        # a new contact never silently discards the uncommitted edit. Commit
        # while self.active/self._entries still point at the card being left.
        if self.editing:
            if not self._commit_edits():
                # The field widgets still contain the attempted edit. Keep
                # this pane intact so the person can retry after fixing the
                # storage problem instead of replacing their only copy with a
                # blank New Contact form.
                return
        # Replace an untouched New Contact rather than stacking a second blank
        # one, so a novice tapping "+" twice never ends up with duplicate empty
        # cards to clean up afterwards.
        self._finish_new_card()
        if self._pending_new:
            # The untouched placeholder could not be removed durably. Do not
            # stack another blank card on top of it.
            return
        # Start blank with a "Name" placeholder — no fabricated "New Contact"
        # string is ever written to disk (per the no-seed / no-placeholder rule);
        # an unnamed card reads as "Unnamed" until the user types.
        color = AVATAR_COLORS[len(self.people) % len(AVATAR_COLORS)]
        person = {k: "" for k in FIELD_KEYS}
        person["phones"] = []
        person["emails"] = []
        person["favorite"] = False
        person["color"] = color
        self.people.append(person)
        self.active = len(self.people) - 1
        self.editing = True
        self._pending_new = True
        self._save()   # the new card exists on disk immediately
        # clear any active search so the new card is visible in the list
        self.search_text = ""
        if self.search.get_text():
            self.search.set_text("")   # triggers _on_search -> _rebuild_list
        self._rebuild_list()
        self._rebuild_detail()
        # Put the cursor in the (empty) name field — the user is here to type a name.
        nm = self._entries.get("name")
        if nm is not None:
            try:
                nm.grab_focus()
            except Exception:
                pass

    def _delete_contact(self, *_):
        """Delete immediately. The one-step undo is saved when restored."""
        if (getattr(self, "_delete_pending", False)
                or not (0 <= self.active < len(self.people))):
            return
        self._delete_pending = True
        self._do_delete()
        GLib.idle_add(self._release_delete_guard)

    def _release_delete_guard(self):
        self._delete_pending = False
        return False

    def _do_delete(self):
        if not (0 <= self.active < len(self.people)):
            return
        # The form writes through only when editing is committed.  Capture its
        # visible values before taking the undo snapshot, otherwise deleting
        # mid-edit followed by Ctrl+Z silently restores the stale pre-edit card.
        if self.editing and not self._commit_edits():
            # The form deliberately retains its typed values after a failed
            # save so Done can be retried.  Deleting now would rebuild the
            # detail pane and destroy that only copy of the edit.
            return
        index = self.active
        # Where the highlight goes next. self.active is an index into the
        # STORE and the list beside it is sorted by name, so keeping it landed
        # the highlight wherever the store happened to shift: deleting the
        # fifth row of six selected the second — a card the reader was not
        # looking at, with its whole record on the pane beside it. Which row
        # takes this one's place is a question about what is on screen, so it
        # is asked of the visible order, and of nothing else about the window.
        successor = next_after_delete(self.people, index,
                                      getattr(self, "search_text", ""))
        old_deleted = self._deleted
        person = self.people.pop(index)
        self._deleted = (index, copy.deepcopy(person))
        moved = next((i for i, p in enumerate(self.people) if p is successor),
                     None) if successor is not None else None
        if moved is not None:
            self.active = moved
        elif self.active >= len(self.people):
            self.active = max(0, len(self.people) - 1)
        self.editing = False
        self._pending_new = False
        if not self._save():
            self.people.insert(index, person)
            self.active = index
            self._deleted = old_deleted
            self._rebuild_list()
            self._rebuild_detail()
            return
        self._rebuild_list()
        self._rebuild_detail()
        self._flash(_t("Contact deleted") + "  ·  " + _t("Ctrl+Z to undo"))

    def _undo_delete(self):
        deleted = self._deleted
        if deleted is None:
            self._flash(_t("There is nothing to undo"))
            return
        # Undo rebuilds both panes around the restored card. An edit open on
        # ANOTHER card is the newest thing the person typed, so commit it here
        # first — the same write Done performs — and drop back to the read
        # view, exactly as selecting another contact does. Without this, Ctrl+Z
        # threw the typed values away AND opened the restored card in a form
        # nobody asked for.
        if self.editing:
            if not self._commit_edits():
                # The form still holds the only copy of those values.
                return
            self.editing = False
        index, person = deleted
        dropped = self._finish_new_card()
        if dropped is not None and dropped < index:
            index -= 1
        self._deleted = None
        self.people.insert(min(index, len(self.people)), copy.deepcopy(person))
        self.active = min(index, len(self.people) - 1)
        if not self._save():
            self.people.pop(self.active)
            self._deleted = deleted
            self.active = min(index, len(self.people) - 1) if self.people else 0
            self._rebuild_list()
            self._rebuild_detail()
            return
        self._rebuild_list()
        self._rebuild_detail()
        self._flash(_t("Contact restored"))

    def _toggle_favorite(self, button=None, *_):
        if not (0 <= self.active < len(self.people)):
            return
        restore_focus = bool(button is not None and button.has_focus())
        person = self.people[self.active]
        # The star rebuilds the card, and a rebuilt form is re-filled from the
        # record — so anything typed into the OPEN form and not yet committed
        # was destroyed by a single click on a button that has nothing to do
        # with it. Commit first, exactly as switching card / New / Delete do.
        if self.editing and not self._commit_edits():
            # The form deliberately keeps its typed values for a retry after a
            # failed save; rebuilding now would throw away that only copy. Put
            # the star back where the record says it is instead — GTK has
            # already flipped the button, and nothing else here will.
            self._sync_favorite_button(button, person)
            return
        person["favorite"] = not person.get("favorite", False)
        if not self._save():
            person["favorite"] = not person["favorite"]
            self._rebuild_list()
            self._rebuild_detail()
            replacement = getattr(self, "_favorite_button", None)
            if restore_focus and replacement is not None:
                replacement.grab_focus()
            return
        self._rebuild_list()
        self._rebuild_detail()
        replacement = getattr(self, "_favorite_button", None)
        if restore_focus and replacement is not None:
            replacement.grab_focus()
        self._flash(_t("Added to favorites") if person["favorite"]
                    else _t("Removed from favorites"))

    def _sync_favorite_button(self, button, person):
        """Put the star back in step with the record without rebuilding the
        pane. The handler is blocked around set_active: a ToggleButton emits
        "clicked" for a programmatic change too, so re-entering this handler
        would flip the record straight back (the set_active re-entrancy that
        has bitten four apps in this OS)."""
        handler = getattr(self, "_favorite_handler", 0)
        if button is None or not handler:
            return
        try:
            button.handler_block(handler)
            button.set_active(bool(person.get("favorite")))
            button.handler_unblock(handler)
        except Exception:                                      # noqa: BLE001
            pass

    def _copy_value(self, _button, kind, value):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(value, -1)
        self._flash(_t("Phone copied") if kind == "phones"
                    else _t("Email copied"))

    def _import_vcard(self, *_):
        path = nbpicker.open_file(self, title=_t("Import vCard"),
                                  start_dir=DOCS_DIR,
                                  patterns=("*.vcf", "*.vcard"))
        if not path:
            return
        # An import rebuilds both panes, and a rebuilt form is re-filled from
        # the record — so anything typed into an OPEN form and not yet
        # committed was destroyed, and the form was then re-pointed at
        # whatever card sat at index 0, still in edit mode, over somebody
        # else's card. Commit first and drop back to the read view, exactly as
        # switching card / New / Delete / the star already do. The picker is
        # opened before this, so cancelling it leaves the form untouched.
        if self.editing:
            if not self._commit_edits():
                return    # the form holds the only copy of those values
            self.editing = False
        # ...and an untouched New Contact open here became a permanent blank
        # "Unnamed" row: self.active moved off it, so nothing ever came back
        # to drop it.
        self._finish_new_card()
        try:
            incoming = parse_vcards(read_vcard_text(path))
            if not incoming:
                self._flash(_t("No contacts found")); return
            keep = (self.people[self.active]
                    if 0 <= self.active < len(self.people) else None)
            before = copy.deepcopy(self.people)
            before_active = self.active
            stats = {}
            merge_contacts(self.people, incoming, stats)
            # Stay on the card the reader was looking at. merge_contacts fills
            # existing records in place, so that record is still the same
            # object; index 0 was only whichever card happened to be first.
            self.active = next((i for i, p in enumerate(self.people)
                                if p is keep), 0)
            if not self._save():
                self.people = before
                self.active = min(before_active, len(self.people) - 1) \
                    if self.people else 0
                self._rebuild_list(); self._rebuild_detail()
                return
            self._rebuild_list(); self._rebuild_detail()
            # What happened, not how many cards the file held: importing an
            # export of this same book said "Imported 11 contacts" while it
            # had added nothing, which is the one thing the reader wanted to
            # know.
            added, updated = stats["added"], stats["updated"]
            said = []
            if added:
                said.append(_t("Added 1 contact") if added == 1
                            else _t("Added %d contacts") % added)
            if updated:
                said.append(_t("Updated 1 contact") if updated == 1
                            else _t("Updated %d contacts") % updated)
            self._flash("  ·  ".join(said) if said
                        else _t("Every contact in that file is already here"))
        except Exception:
            self._flash(_t("Import failed"))

    def _export_vcard(self, whole_book=True):
        # The file has to hold the card that is on screen. Export to PDF and
        # Print both commit an open form first; this one did not, so a vCard
        # written mid-edit carried the values from BEFORE the edit — a person
        # exporting a card they had just corrected got the copy they were
        # correcting, and nothing on screen said so.
        if self.editing and not self._commit_edits():
            # The commit failed, so the card on screen is not on disk and an
            # export now would write the stale copy. _commit_edits already
            # says why the SAVE failed; this says what it cost — the person
            # asked to export, and silence would read as an export that
            # happened. The form keeps its typed values for the retry.
            self._flash(_t("Export failed") + "  ·  "
                        + _t("The card could not be saved first"))
            return
        if not self.people:
            return
        default = "contacts.vcf" if whole_book else \
            re.sub(r"[^A-Za-z0-9._-]+", "-", self.people[self.active]["name"]
                   or "contact").strip("-") + ".vcf"
        path = nbpicker.save_file(self, title=_t("Export vCard"),
                                  start_dir=DOCS_DIR, suggested_name=default,
                                  patterns=("*.vcf",), default_ext=".vcf")
        if not path:
            return
        try:
            chosen = self.people if whole_book else [self.people[self.active]]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            nbapp.atomic_write_text(path, export_vcards(chosen))
            self._flash(_t("vCard exported"))
        except Exception:
            self._flash(_t("Export failed"))

    # -------------- File menu: Export to PDF under $NB_HOME/Documents ------
    # No file open/save. contacts.json is the always-saved store (the source of
    # truth); Export renders the whole book as a read-only PDF into Documents.
    def _export_pdf(self, *_a):
        """Render the whole address book to a paginated PDF under Documents.
        Commits any in-progress edit first so the export reflects on-screen
        text. Reports a neutral status line; never crashes on a bad write."""
        if self.editing:
            self._commit_edits()
        if not self.people:
            self._flash("No contacts to export")
            return
        name = "contacts-" + time.strftime("%Y-%m-%d") + ".pdf"
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            # Beside the destination, then into place. Exporting twice in a day
            # lands on the same dated name, so a render that threw part-way
            # destroyed the copy already sitting there. (Print is unaffected:
            # nbprint hands _make_pdf a temp path of its own.)
            nbapp.atomic_write_via(os.path.join(DOCS_DIR, name),
                                   self._make_pdf)
        except Exception:
            self._flash("Export failed")
            return
        self._flash("Exported to Documents")

    def _print(self, *_a):
        """File ▸ Print — hand the SAME PDF Export produces to the shared print
        dialog. Commits any in-progress edit first so the printout matches what
        is on screen. The no-printer case is handled inside nbprint."""
        if self.editing:
            self._commit_edits()
        if not self.people:
            self._flash("No contacts to print")
            return
        try:
            nbprint.print_document(self, self._make_pdf, job_name="Contacts")
        except Exception:
            self._flash("Printing is unavailable")

    def _make_pdf(self, path):
        """Write the address-book PDF to `path` — the exact document File ▸
        Export to PDF produces. Shared by Export (into Documents) and Print
        (into a temp file for the spooler)."""
        self._render_pdf(path)

    def _render_pdf(self, path):
        """Draw every contact (name, role, fields, notes) onto a cairo PDF at
        `path`, paginating when the cursor overflows the page."""
        PW, PH = 612.0, 792.0            # US Letter, points
        ML, MR, MT, MB = 64.0, 64.0, 72.0, 64.0
        text_w = PW - ML - MR
        surf = cairo.PDFSurface(path, PW, PH)
        cr = cairo.Context(surf)

        # Laid out with nbprint.PdfText (PangoCairo). The private wrap()/emit()
        # this replaces used cairo's TOY font API, which binds one FreeType face
        # and does no per-character fallback: an address book with a name,
        # address or note in Japanese, Chinese, Korean, Hindi or Yiddish printed
        # as a page of empty .notdef boxes — and the wrapping was measured
        # against those boxes, so even the line breaks were wrong. Pango picks a
        # face per glyph, so a name prints in the script it was typed in.
        pt = nbprint.PdfText(surf, cr, ML, MT, PH - MB, text_w)

        def ink(hexc):
            r, g, b = nbicons._hex(hexc)
            cr.set_source_rgb(r, g, b)

        def emit(text, size, bold, color, serif=False,
                 gap_before=0.0, gap_after=0.0):
            """Same signature the toy-font helper had, so the page below reads
            unchanged. `serif` picks the family per line, which is why this is a
            wrapper rather than a bare alias for pt.emit."""
            pt.family = "serif" if serif else "sans-serif"
            pt.emit(text, size, bold=bold, color=color,
                    gap_before=gap_before, gap_after=gap_after)

        def rule():
            if pt.y + 1 <= PH - MB:
                ink("#EFEBE0")
                cr.set_line_width(1.0)
                cr.move_to(ML, pt.y)
                cr.line_to(PW - MR, pt.y)
                cr.stroke()
            pt.y += 18

        # Filed the way the list files it (fold_name), so the printed book
        # is in the order the screen is: sorting the raw string put "Émile"
        # past "Zed" on the page and beside "Eve" on screen.
        people = sorted(self.people,
                        key=lambda p: fold_name(p.get("name", "")).lower())
        # Cover header, then each contact alphabetically. Every word on this
        # page is the app's, not the reader's, and every one of them was
        # printed in English whatever language the app was running in: a
        # Japanese address book exported a document headed CONTACTS with
        # ORGANIZATION and BIRTHDAY down the side of it.
        emit(_t("Contacts").upper(), 9.5, False, "#6E695E", gap_after=6)
        emit(_t("1 contact") if len(people) == 1
             else _t("%d contacts") % len(people),
             26, True, "#1A1916", serif=True, gap_after=3)
        rule()
        for idx, p in enumerate(people):
            if idx:
                pt.y += 8
                rule()
            emit(p.get("name", "") or _t("Unnamed"), 20, True, "#1A1916",
                 serif=True, gap_before=6, gap_after=2)
            role = p.get("role", "")
            if role:
                emit(role, 11, False, "#6E695E", gap_after=6)
            else:
                pt.y += 4
            for label, key in FIELDS:
                val = p.get(key, "")
                if val:
                    emit("%s   %s" % (_t(label).upper(), val), 10.5, False,
                         "#2A2620", gap_after=2)
            for key in ("phones", "emails"):
                for item in p.get(key, []):
                    emit(item["value"] if key == "emails" else
                         "%s   %s" % (value_label_name(
                             key, item["label"]).upper(), item["value"]),
                         10.5, False, "#2A2620", gap_after=2)
            notes = p.get("notes", "")
            if notes:
                emit(_t("Notes").upper(), 9, False, "#9A9484",
                     gap_before=6, gap_after=2)
                for raw in notes.split("\n"):
                    emit(raw, 11, False, "#2A2620", serif=True)

        surf.finish()

    def _flash(self, text):
        """Surface a transient status/result in the list pane's status line,
        then clear it after a moment. Crash-safe: a UI failure never propagates
        out of the action that called it."""
        if self._closed:
            # Nothing to show it on and no one to read it: a status line on a
            # closed window would only re-arm a timer against dead widgets. The
            # one caller that can land here is a save that failed during
            # teardown, which has no visible window left to warn in anyway.
            return
        try:
            self.status_lbl.set_text(text)
            self.status_lbl.show()
        except Exception:
            pass
        if self._status_timer:
            GLib.source_remove(self._status_timer)
        self._status_timer = GLib.timeout_add_seconds(3, self._clear_status)

    def _clear_status(self):
        self._status_timer = 0
        if self._closed:
            return False   # the status label went with the window
        try:
            self.status_lbl.set_text("")
            self.status_lbl.hide()
        except Exception:
            pass
        return False   # one-shot

    def _step(self, delta):
        """Select the next/previous card in the visible (filtered, sorted)
        order. Safe when the list is empty."""
        self._flush_pending_search()
        order = [i for i, _ in self._visible_order_pairs()]
        if not order:
            return
        pos = order.index(self.active) if self.active in order else 0
        self._select(order[(pos + delta) % len(order)])

    def _on_key(self, w, ev):
        # Esc while editing finishes the edit (committing, exactly like Done)
        # and returns to the read view, rather than quitting Contacts out from
        # under someone mid-card. Only when nothing is being edited does Esc fall
        # through to the base handler and close the app. An open menu / About
        # card is dismissed by the base first.
        if (ev.keyval == Gdk.KEY_Escape and self.editing
                and self._menu_open is None
                and getattr(self, "_about_layer", None) is None):
            self._toggle_edit()
            return True
        # ...and then the search, so a filtered book is one key from whole
        # again instead of looking permanently half-empty. Esc LEAVES the
        # transient layer it is in — the filter — before it leaves the window
        # (Constitution Article II; Accounting, Journal and Academics all read
        # Esc the same way).
        if (ev.keyval == Gdk.KEY_Escape
                and self._menu_open is None
                and getattr(self, "_about_layer", None) is None
                and self._clear_search()):
            return True
        # Ctrl+F puts the caret in the search field wherever focus happens to
        # be — the one Find shortcut the whole OS binds (nbcommands edit.find).
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and ev.keyval in (Gdk.KEY_f, Gdk.KEY_F)):
            self._focus_search()
            return True
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and ev.keyval in (Gdk.KEY_z, Gdk.KEY_Z)):
            self._undo_delete()
            return True
        # The File menu prints "Print…    Ctrl+P" from the shared command
        # registry, and nothing here answered it — an accelerator advertised
        # on every card and bound by no one. writer.py binds it the same way.
        if (ev.state & Gdk.ModifierType.CONTROL_MASK
                and ev.keyval in (Gdk.KEY_p, Gdk.KEY_P)):
            if self.people:          # the same condition the menu greys on
                self._print()
            return True
        return super()._on_key(w, ev)

    # ------------------------------------------------------------- menus
    def menu_items(self, name):
        if name == "File":
            # contacts.json is the sole source of truth and is rewritten on
            # every edit, so there is no Save here — only the create action and
            # a one-way render of the book. Export writes the PDF straight into
            # $NB_HOME/Documents and asks nothing, so it takes NO ellipsis;
            # Print opens the printer dialog, so it does. Both grey out on an
            # empty book, where they used to look live and only flash "No
            # contacts to export".
            has = bool(self.people)
            return [
                # NO ELLIPSIS. Rule 1 reads "a dialog, a picker or a confirm
                # BEFORE ANYTHING HAPPENS", and nothing is asked here at any
                # point: _new_contact appends the person, sets self.active,
                # enters edit mode and calls self._save() -- the record is on
                # disk before a single field has been typed. What follows is
                # the ordinary detail pane with the caret in the name box, the
                # same pane that is there for every other contact. This is
                # also what MENU-CONVENTIONS §2B prints for a single-store
                # app: "New <Thing>" plain, "Delete <Thing>…" with the mark.
                # Journal, Cookbook, Academics and Tasks all word it that way;
                # only this app promised a form that never comes.
                ("New Contact", lambda: self._new_contact()),
                nbapp.SEP,
                (_vc_menu("Import vCard…"), self._import_vcard),
                (_vc_menu("Export Contact vCard…"),
                 (lambda: self._export_vcard(False)) if has else None),
                (_vc_menu("Export All vCards…"),
                 (lambda: self._export_vcard(True)) if has else None),
                nbapp.SEP,
                ("Export to PDF", self._export_pdf if has else None),
                nbcommands.item("file.print", self._print) if has else
                nbcommands.item("file.print", None),
                nbapp.SEP,
                ("Close    Esc", self.close),
            ]
        if name == "Edit":
            return [((_t("Undo Delete Contact") + "    Ctrl+Z")
                     if self._deleted else (_t("Undo") + "    Ctrl+Z"),
                     self._undo_delete if self._deleted else None)]
        if name == "Card":
            has = bool(self.people)
            return [
                # Same item, same promise: see the File menu's note above.
                ("New Contact", lambda: self._new_contact()),
                ("Done Editing" if self.editing else "Edit Card",
                 (lambda: self._toggle_edit()) if has else None),
                nbapp.SEP,
                (_t("Delete Card"),
                 (lambda: self._delete_contact()) if has else None),
            ]
        if name == "View":
            has_text = bool(self.search.get_text())
            return [
                # The label AND the accelerator come from the registry, so
                # Find is spelled and bound identically in every app that has
                # one (nbcommands edit.find — "Find    Ctrl+F").
                nbcommands.item("edit.find", self._focus_search),
                ("Clear Search",
                 (lambda: self._clear_search()) if has_text else None),
                nbapp.SEP,
                ("Next Contact", lambda: self._step(1)),
                ("Previous Contact", lambda: self._step(-1)),
            ]
        return super().menu_items(name)

    # ------------------------------------------------------------------ css
    def _install_css(self):
        css = ("""
        .listpane, .listpane *, .detailwrap, .detailwrap * {
                     font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .listpane { background: %(panel)s; border-right: 1px solid %(hair)s; }
        .searchheader { padding: 18px 18px 14px; }
        .searchbox { background: %(paper)s; border: 1px solid #C9C4B6;
                     border-radius: 8px; min-height: 38px; }
        .searchentry { background: transparent; border: none; box-shadow: none;
                       font-size: 14px; color: #2A2620; }
        .searchentry image { color: %(muted2)s; }
        .listempty, .detailempty { color: %(muted)s; font-size: 13px; }
        .detailempty { font-size: 14px; }
        .contactrow { padding: 10px 12px; border-radius: 6px; margin-bottom: 2px;
                      background: transparent; border: none; box-shadow: none; }
        .contactrow:hover { background: %(sel)s; }
        .contactrow.selected { background: %(sel)s;
                               box-shadow: inset 3px 0 0 %(accent)s; }
        .rowname { font-size: 15px; color: %(ink)s; font-weight: 500; }
        .rowrole { font-size: 12px; color: %(muted2)s; margin-top: 2px; }
        /* Letter dividers, only drawn on a long book (see GROUP_FROM). */
        .letterhead { font-size: 11px; letter-spacing: 0.14em; font-weight: 700;
                      color: %(muted)s; padding: 14px 12px 5px; }
        /* The one thing the book volunteers. Signage-red hairline on the
           leading edge, the same marker the selected row uses, because this
           IS the alert case the accent is reserved for. */
        .bdayrow { padding: 12px 18px; background: #F4F2EC; border: none;
                   border-top: 1px solid #C9C4B6;
                   border-bottom: 1px solid #C9C4B6; border-radius: 0;
                   box-shadow: inset 3px 0 0 %(accent)s; }
        .bdayrow:hover { background: #EFEBE0; }
        .bdayname { font-size: 14px; color: %(ink)s; font-weight: 600; }
        .bdaynote { font-size: 12px; color: %(muted2)s; }
        .fieldnote { font-size: 12px; color: %(muted2)s; }
        .statusline { padding: 12px 16px; font-size: 12px; color: %(muted2)s;
                      border-top: 1px solid #C9C4B6; }
        .detailwrap { background: %(paper)s; }
        /* The card pane's paper is painted on the ScrolledWindow, NOT on the
           GtkViewport inside it. With a background of its own the viewport
           left a band the height of the menu bar unpainted along the bottom
           of the pane: the last row of a long card was cut through the middle
           of its glyphs and anything below it was invisible until you
           scrolled, while the list pane beside it drew to the very edge. */
        .bigname { font-family: "Newsreader","Liberation Serif",serif;
                   font-size: 40px; font-weight: 500; color: %(ink)s;
                   letter-spacing: -0.01em; }
        .bigrole { font-size: 17px; color: #6E695E; }
        .editbtn { min-height: 40px; padding: 0 16px; border-radius: 8px;
                   background: %(paper)s; border: 1px solid #C9C4B6;
                   box-shadow: none; color: #3A362E; font-size: 14px; }
        .editbtn:hover { background: #F1EEE6; }
        .editbtn.editon { background: %(ink)s; color: %(paper)s;
                          border: 1px solid %(ink)s; font-weight: 600; }
        /* The theme's `* { color: ink }` matches a button's label node itself,
           so a colour set on the button never reaches its text: Done rendered
           as an ink slab with an ink label on it. Name the label too. */
        .editbtn label { color: #3A362E; }
        .editbtn.editon label { color: %(paper)s; font-weight: 600; }
        .fieldgrid { border-top: 1px solid #C9C4B6; }
        .fieldrow { padding: 18px 0; border-bottom: 1px solid #EFEBE0; }
        .fieldlabel { font-size: 12px; letter-spacing: 0.1em; color: %(muted)s;
                      font-weight: 600; }
        .fieldval { font-size: 17px; color: %(ink)s; }
        .fieldempty { color: %(muted)s; }
        .notestext { font-family: "Newsreader","Liberation Serif",serif;
                     font-size: 20px; color: #2A2620; }
        /* .notestext is declared after .fieldempty and would otherwise win,
           printing the "no notes" dash in ink while every other empty field
           shows a muted one. */
        .notestext.fieldempty { color: %(muted)s; }
        .newbtn { min-width: 30px; min-height: 30px; padding: 0 6px;
                  border-radius: 8px; background: transparent;
                  border: none; box-shadow: none; }
        .newbtn:hover { background: %(sel)s; }
        .fieldentry, .nameentry, .roleentry, .notesframe {
                  background: %(paper)s; border: 1px solid #C9C4B6;
                  border-radius: 8px; box-shadow: none; padding: 4px 8px;
                  color: %(ink)s; }
        .fieldentry { font-size: 17px; }
        .nameentry { font-family: "Newsreader","Liberation Serif",serif;
                     font-size: 30px; font-weight: 500; color: %(ink)s; }
        .roleentry { font-size: 16px; color: #6E695E; }
        .notesedit { font-family: "Newsreader","Liberation Serif",serif;
                     font-size: 17px; color: #2A2620; background: %(paper)s; }
        .notesedit text { background: %(paper)s; color: #2A2620; }
        /* Address reads back as one of the fields above it, so its editor is
           set in the same face and size the field entries are. */
        .fieldarea { font-size: 17px; color: %(ink)s; background: %(paper)s; }
        .fieldarea text { background: %(paper)s; color: %(ink)s; }
        /* confirm dialog for destructive actions — papertone card, darker-beige
           border; signage-red ONLY on the destructive primary button */
        .cdlg { background: %(paper)s; border: 1px solid #C9C4B6; }
        .cdlgbox { padding: 24px 28px 20px; }
        .cdlgbox * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .cdlgtitle { font-family: "Newsreader","Liberation Serif",serif;
                     font-size: 20px; color: %(ink)s; }
        .cdlgmsg { font-size: 13px; color: #6E695E; }
        .cdlgcancel { font-size: 13px; color: #2A2620; padding: 6px 16px;
                      background: %(paper)s; border: 1px solid #C9C4B6;
                      border-radius: 8px; box-shadow: none; }
        .cdlgcancel:hover { background: #F1EEE6; }
        .cdlgok { font-size: 13px; color: %(paper)s; padding: 6px 16px;
                  background: %(accent)s; border: 1px solid %(accent)s;
                  border-radius: 8px; box-shadow: none; }
        .cdlgok:hover { background: #B12D19; border-color: #B12D19; }
        .cdlgok label { color: %(paper)s; }
        .cdlgcancel label { color: #2A2620; }
        """ % dict(panel=PANEL, hair=HAIR, paper=PAPER, muted=MUTED,
                   muted2=MUTED2, ink=INK, sel=SEL, accent=ACCENT)).encode()
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            # styling is cosmetic; a bad screen/provider must not stop launch
            pass


if __name__ == "__main__":
    nbapp.run(Contacts)
