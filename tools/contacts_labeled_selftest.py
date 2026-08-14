#!/usr/bin/env python3
"""A colon in what somebody typed is not always a label separator.

    tools/guestrun.sh python3 tools/contacts_labeled_selftest.py

The multi-value fields (phones, emails, addresses) are edited as free text in
the spelling `mobile: 555-0100`, several to a field separated by `;` or a
newline. `parse_labeled_text` reads that back, and `labeled_text` writes it.
Neither was named by any of the five suites — they came off the day-6
method-coverage map, where 45 of contacts.py's 77 functions are never named.

THE DEFECT. The parser split each line on its FIRST colon, and when the text
before that colon was not one of the three real labels it fell the LABEL back to
the field default — but kept the truncated value. So anything containing a colon
lost everything up to it:

    http://example.com          ->  //example.com          the scheme, gone
    https://example.com/a:b     ->  //example.com/a:b
    mailto:someone@example.com  ->  someone@example.com
    3:30 meeting                ->  30 meeting             the hour, gone

Typing a plain URL into a field and having it silently shortened is the kind of
loss nobody looks for afterwards — it is still a plausible-looking string, just
not the one they typed. A colon now only separates when what precedes it IS a
label; otherwise the whole line is the value, exactly as the no-colon case
already did.

WHAT IS NOT A DEFECT AND IS PINNED AS INTENDED: a `;` inside a value still
splits it, because `;` is the format's separator — `labeled_text` joins with
`"; "`. "Flat 3; 12 High St" becomes two addresses. That is the format working
as documented, not a bug, and it is checked here so nobody "fixes" it by
accident. Escaping the separator would be a format change, not a repair.

A SECOND DEFECT, introduced by the first fix and caught by this suite before it
shipped: `labeled_text` also writes the FIELD'S OWN FALLBACK as a label — a
phones field with no explicit label spells itself `phone: 555-0100` — and my
first rule only recognised the three real labels, so every unlabelled value
stopped round-tripping. The fallback counts as a label too. Worth saying plainly
because it is the argument for writing the round-trip checks at all: the fix and
its regression were four minutes apart.

RED PROOFS (M1), each mutation applied ALONE to a scratch COPY, suite pointed at
it with CONTACTS_MODULE_DIR. MEASURED:

  1. the truncation is restored — the defect, put back                5 FAILED
       FAIL a URL keeps its scheme            <- '//example.com'
       FAIL a URL with a colon in its path keeps all of it
       FAIL a mailto: keeps its scheme
       FAIL a time of day keeps its hour      <- '30 meeting'
       FAIL a word that is not a label is part of the value

  2. any colon separates, label or not
     (the `label in VALUE_LABELS or label == fallback` test dropped)  5 FAILED
       the same five. Both mutations produce the same damage by different
       routes, which is the point: what protects the value is the test on WHAT
       precedes the colon, not the presence of a colon.

  3. the parser's cell -> mobile alias is dropped
     (anchored on the parse_labeled_text copy — vcard import has its own)
                                                                     1 FAILED
       FAIL 'cell' is read as 'mobile'
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
MODULE_DIR = os.environ.get("CONTACTS_MODULE_DIR", DE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, MODULE_DIR)

import tempfile                                               # noqa: E402
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="contacts-lab-"))
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)

import contacts as con                                        # noqa: E402

R = []


def check(name, ok, detail=""):
    R.append(bool(ok))
    print("%s %s%s" % ("ok  " if ok else "FAIL", name,
                       "" if ok else "\n     <- %s" % (detail,)))


def one(text, fallback="phone"):
    got = con.parse_labeled_text(text, fallback)
    return (got[0]["label"], got[0]["value"]) if got else None


# ------------------------------------------- a colon that is not a separator
check("a URL keeps its scheme",
      one("http://example.com", "url") == ("url", "http://example.com"),
      one("http://example.com", "url"))
check("a URL with a colon in its path keeps all of it",
      one("https://example.com/a:b", "url")
      == ("url", "https://example.com/a:b"),
      one("https://example.com/a:b", "url"))
check("a mailto: keeps its scheme",
      one("mailto:someone@example.com", "email")
      == ("email", "mailto:someone@example.com"),
      one("mailto:someone@example.com", "email"))
check("a time of day keeps its hour",
      one("3:30 meeting", "note") == ("note", "3:30 meeting"),
      one("3:30 meeting", "note"))
# NB the example must not be the field's own fallback — that IS a label.
check("a word that is not a label is part of the value",
      one("urgent: see me later", "note") == ("note", "urgent: see me later"),
      one("urgent: see me later", "note"))

# ------------------------------------------------- a colon that IS a separator
for label in sorted(con.VALUE_LABELS):
    check("%r is read as a label" % label,
          one("%s: 555-0100" % label) == (label, "555-0100"),
          one("%s: 555-0100" % label))
check("'cell' is read as 'mobile'",
      one("cell: 555-0100") == ("mobile", "555-0100"), one("cell: 555-0100"))
check("...and the case does not matter",
      one("MOBILE: 555-0100") == ("mobile", "555-0100"),
      one("MOBILE: 555-0100"))
check("a label with an empty value is not a label",
      one("mobile:") == ("phone", "mobile:"), one("mobile:"))
check("a valid label still splits a URL off correctly",
      one("work: http://example.com", "url") == ("work", "http://example.com"),
      one("work: http://example.com", "url"))

# ---------------------------------------------------------- nothing to read
for text in ("", "   ", ";", ";;", "\n", " ; \n ; "):
    check("%r yields no values" % text,
          con.parse_labeled_text(text, "phone") == [],
          con.parse_labeled_text(text, "phone"))

# ------------------------------------------------- the round trip, where it holds
# Values free of the separator must survive being written and read back.
for values in ([{"label": "phone", "value": "555-0100"}],
               [{"label": "work", "value": "http://example.com"}],
               [{"label": "mobile", "value": "+44 20 7946 0000"}],
               [{"label": "home", "value": "1"},
                {"label": "work", "value": "2"}]):
    text = con.labeled_text(values)
    back = con.parse_labeled_text(text, values[0]["label"])
    check("round trip: %s" % text, back == values, (text, back))

# --------------------------------- ...and where it deliberately does NOT hold
# `;` is the separator. A value containing one becomes two values, and that is
# the format working as documented. Pinned so nobody "repairs" it by accident:
# escaping the separator would be a format change, not a fix.
check("a semicolon inside a value splits it, as the format says",
      len(con.parse_labeled_text("Flat 3; 12 High St", "address")) == 2,
      con.parse_labeled_text("Flat 3; 12 High St", "address"))
check("...and so does a newline",
      len(con.parse_labeled_text("one\ntwo", "phone")) == 2,
      con.parse_labeled_text("one\ntwo", "phone"))

# ------------------------------------------------------------- never raises
HOSTILE = [None, "", ":", "::", ":::", "a:", ":b", "\n:\n", ";" * 50,
           "x" * 5000, "\x00", "\x00:\x00", ":" + "a" * 100,
           "mobile:" * 30, "\U0001F600:\U0001F600"]
raised = []
for text in HOSTILE:
    try:
        con.parse_labeled_text(text, "phone")
    except Exception as exc:                                  # noqa: BLE001
        raised.append((repr(text)[:24], type(exc).__name__, str(exc)[:40]))
check("nothing typed into the field makes it raise (%d inputs)" % len(HOSTILE),
      not raised, raised[:3])

bad = R.count(False)
print("\n%d checks, %d failed" % (len(R), bad))
print("all checks passed" if not bad else "RESULT: %d FAILED" % bad)
sys.exit(1 if bad else 0)
