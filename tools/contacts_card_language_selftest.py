#!/usr/bin/env python3
"""Contacts: whose words are on the card, and in which language.

Run under a non-English catalog (Russian, where every string this suite
looks at differs from its English source), because the defects below are
invisible in English and only in English. Every expectation is READ OUT OF THE
CATALOG rather than written down here, and each one is guarded against the
vacuous case where a translation happens to equal its English key.

Every check here is named after the behaviour it guards and was watched go RED
against the code as it stood before the fix beside it:

  a-contact-named-home-is-still-called-home
        nbi18n auto-translates the text of every Gtk.Label it walks. The card
        and the list row put the person's own words straight onto a label, so
        a contact filed under "Home" — the name people genuinely give their own
        landline — read "Accueil" on a French install, an organization called
        "Library" read "Bibliothèque", and a role of "Work" read "Travail". The
        record was never altered, so the name on screen was not the name the
        search box could find, and opening Edit showed a third spelling again.
  a-home-number-is-not-labelled-with-the-home-page-word
        the value labels asked the catalog for the bare words "Home" and
        "Work", which are already in it for the home SCREEN and the day's
        work. A home telephone number came out labelled "Startseite" (de),
        "Accueil" (fr), "Главная" (ru), "主页" (zh) — a website's front page,
        in thirteen of the seventeen languages. "Mobile" had no entry at all.
  the-organization-field-label-is-translated
        "Organization" was in none of the seventeen catalogs, so ORGANIZATION
        stood in English in a column of translated field labels.
  the-exported-book-is-in-the-app-s-language
        every word of the exported / printed PDF was English whatever language
        the app was running in.

Run:  tools/guestrun.sh python3 tools/contacts_card_language_selftest.py
"""
import os
import sys
import glob
import json
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, DE)

WORK = tempfile.mkdtemp(prefix="contacts-language-")
os.environ["NB_HOME"] = os.path.join(WORK, "home")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)
# The language is read ONCE, at nbi18n import. Set it before anything imports
# the catalog or this suite silently runs in English and proves nothing.
os.environ["NB_LANG"] = "ru"

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import nbapp  # noqa: E402
nbapp._APP_DIR = os.path.join(WORK, "nb-apps")
nbapp.APP_DIR = nbapp._APP_DIR
os.makedirs(nbapp._APP_DIR, exist_ok=True)

import nbi18n  # noqa: E402
import nbprint  # noqa: E402
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


def shown(win):
    """Every label text the window is currently showing. show_all has run, so
    the auto-translate walk has already had its pass over the tree."""
    out = []
    stack = [win.get_child()]
    while stack:
        w = stack.pop()
        if isinstance(w, Gtk.Label):
            out.append(w.get_text())
        if isinstance(w, Gtk.Container):
            stack.extend(w.get_children())
    return out


# ===================================================================== L0
@section("the-catalog-loaded-and-every-word-here-differs")
def _l0():
    # A guard on the suite itself: if the catalog did not load, every check
    # below would pass vacuously because English equals English.
    # A guard on the suite itself. Every check below compares what is on
    # screen with what the catalog says; if the catalog had not loaded, all of
    # them would pass vacuously because English equals English.
    words = ["Home", "Library", "Work", "Notes", "Contacts", "1 contact",
             "Organization", "Home phone", "Work email", "Mobile"]
    same = [w for w in words if nbi18n._t(w) == w]
    check("the-catalog-loaded-and-every-word-here-differs", not same,
          "these have no translation to tell apart: %r" % (same,))


# ===================================================================== L1
@section("a-contact-named-home-is-still-called-home")
def _l1():
    win = seed(card("Home", organization="Library", role="Work",
                    notes="Notes",
                    phones=[{"label": "home", "value": "020 7946 0000"}]))
    win.active = 0
    texts = shown(win)
    check("a-contact-named-home-is-still-called-home",
          texts.count("Home") >= 2 and nbi18n._t("Home") not in texts,
          texts)
    check("an-organization-in-the-catalog-is-not-translated",
          "Library" in texts and nbi18n._t("Library") not in texts, texts)
    check("a-role-in-the-catalog-is-not-translated",
          "Work" in texts and nbi18n._t("Work") not in texts, texts)
    check("notes-in-the-catalog-are-not-translated",
          "Notes" in texts and nbi18n._t("Notes") not in texts, texts)
    # ...and the card the SEARCH BOX finds is the card on screen.
    check("...so the name on screen is the name the search finds",
          con.contact_matches(win.people[0], "Home"), win.people[0]["name"])
    win.destroy()


# ===================================================================== L2
@section("a-home-number-is-not-labelled-with-the-home-page-word")
def _l2():
    win = seed(card("Amy Pond",
                    phones=[{"label": "home", "value": "020 7946 0000"},
                            {"label": "mobile", "value": "07700 900461"}],
                    emails=[{"label": "work", "value": "amy@example.com"}]))
    win.active = 0
    texts = " | ".join(shown(win))
    homepage = nbi18n._t("Home")            # "Accueil" — a website's front page
    check("a-home-number-is-not-labelled-with-the-home-page-word",
          homepage not in texts, texts)
    check("...it is labelled with the translated kind of number",
          nbi18n._t("Home phone") in texts
          and nbi18n._t("Home phone") != "Home phone", texts)
    check("a-mobile-number-is-not-left-in-english",
          nbi18n._t("Mobile") in texts, texts)
    # AN EMAIL CARRIES NO CATEGORY ANY MORE. A phone has kinds worth naming --
    # which number to ring at nine in the evening is a real question -- but an
    # address is an address, and filing each one as Home or Work was a decision
    # with no consequence. The address stands alone; a stored "work" label
    # survives in the record and in vCard export, it simply is not shown.
    check("an-email-address-stands-alone-with-no-category",
          "amy@example.com" in texts
          and nbi18n._t("Work email") not in texts, texts)
    win.destroy()


# ===================================================================== L3
@section("every-field-label-on-a-card-is-translated")
def _l3():
    win = seed(card("Amy Pond"))
    win.active = 0
    texts = shown(win)
    english = [lab for lab, _k in con.FIELDS] + ["Phones", "Emails"]
    untranslated = [lab for lab in english
                    if lab.upper() in texts and nbi18n._t(lab) == lab]
    check("every-field-label-on-a-card-is-translated", not untranslated,
          "still English: %r  (on screen: %r)" % (untranslated, texts))
    win.destroy()


# ===================================================================== L4
@section("every-card-label-has-an-entry-in-all-seventeen-catalogs")
def _l4():
    # getattr, not con.PHONE_LABEL_NAMES: a build without the per-field label
    # names must still be MEASURED on the field labels it does have, not blow
    # up and report an exception where a missing catalog key is the finding.
    keys = ([lab for lab, _k in con.FIELDS]
            + sorted(set(list(getattr(con, "PHONE_LABEL_NAMES", {}).values())
                         + list(getattr(con, "EMAIL_LABEL_NAMES", {}).values()))))
    missing = {}
    for path in sorted(glob.glob(os.path.join(DE, "lang_*.json"))):
        lang = os.path.basename(path)[5:-5]
        with open(path, encoding="utf-8") as fh:
            cat = json.load(fh)
        gap = [k for k in keys if k not in cat]
        if gap:
            missing[lang] = gap
    check("every-card-label-has-an-entry-in-all-seventeen-catalogs",
          not missing, missing)


# ===================================================================== L5
@section("the-exported-book-is-in-the-app-s-language")
def _l5():
    win = seed(card("Amy Pond", organization="Leadworth",
                    phones=[{"label": "home", "value": "020 7946 0000"}],
                    notes="Fish fingers and custard."))
    win.active = 0
    said = []

    class Recorder(object):
        """Stands in for nbprint.PdfText and keeps every line the page emits,
        so the document can be read as text instead of as a PDF."""
        def __init__(self, *_a, **_kw):
            self.y = 0.0
            self.family = "sans-serif"

        def emit(self, text, *_a, **_kw):
            said.append(text)

    real = nbprint.PdfText
    nbprint.PdfText = Recorder
    try:
        win._render_pdf(os.path.join(WORK, "book.pdf"))
    finally:
        nbprint.PdfText = real
    page = " | ".join(said)
    check("the-exported-book-is-in-the-app-s-language",
          "CONTACTS" not in said and nbi18n._t("Contacts").upper() in said,
          page)
    check("...including the count of what is in it",
          nbi18n._t("1 contact") in page and "1 contact" not in page, page)
    check("...including the field labels down the side of it",
          nbi18n._t("Organization").upper() in page
          and "ORGANIZATION" not in page, page)
    check("...including what kind of number each one is",
          nbi18n._t("Home phone").upper() in page and "PHONE HOME" not in page,
          page)
    check("...and the person's own words are still their own",
          "Amy Pond" in page and "Fish fingers and custard." in page, page)
    win.destroy()


# ===================================================================== L6
@section("the-printed-book-is-filed-the-way-the-list-files-it")
def _l6():
    win = seed(card("Zed Zane"), card("Émile Éluard"), card("Eve Evans"))
    said = []

    class Recorder(object):
        def __init__(self, *_a, **_kw):
            self.y = 0.0
            self.family = "sans-serif"

        def emit(self, text, *_a, **_kw):
            said.append(text)

    real = nbprint.PdfText
    nbprint.PdfText = Recorder
    try:
        win._render_pdf(os.path.join(WORK, "book2.pdf"))
    finally:
        nbprint.PdfText = real
    names = {"Zed Zane", "Émile Éluard", "Eve Evans"}
    order = [t for t in said if t in names]
    # The invariant, not a written-down answer: the page is filed the way the
    # column beside it is. Sorting the raw string put "Émile" past "Zed" on the
    # page while the list filed it under E.
    listed = [p["name"] for _i, p in win._visible_order_pairs()]
    check("the-printed-book-is-filed-the-way-the-list-files-it",
          order == listed and order[0] != "Zed Zane", (order, listed))
    win.destroy()


bad = R.count(False)
print("RESULT: %s (%d checks, %d failed)"
      % ("ALL PASS" if not bad else "SOME FAILED", len(R), bad))
sys.exit(1 if bad else 0)
