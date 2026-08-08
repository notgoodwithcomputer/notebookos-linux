#!/usr/bin/env python3
"""nbi18n — the tiny translation layer shared by every Notebook OS app.

Design goals: no gettext/.mo build step (this is an offline image with no
locale-gen), source strings ARE the keys (so an untranslated string gracefully
shows English), and the catalogs are plain JSON in the same spirit as the
language app's course_<code>.json. Each app just does:

    from nbi18n import _t
    label = _t("Files")          # -> "Archivos" / "Fichiers" / "文件" / "Files"

The active language is read once, at import: $NB_LANG when it is set (nothing
sets it in the product — it is there so a test or a shell can pin one app to a
language), otherwise the setting Region & Language persists to
$NB_HOME/.config/notebook/locale.json. So the language a window is in is the
language it was LAUNCHED in; picking a new one changes every app opened after
that, and the desktop itself at the next restart. Region & Language says so.
Catalogs live at /opt/notebook/de/lang_<code>.json, mapping English -> target.
"""
import os
import json
import tempfile
import time

CATALOG_DIR = os.path.dirname(os.path.abspath(__file__))
SUPPORTED = ("en", "de", "el", "eo", "es", "fr", "hi", "it", "ja", "ko", "nl",
             "pl", "pt", "ru", "sr", "tr", "yi", "zh")     # zh = Simplified Chinese; sr = Latin script
# Each language names ITSELF, the way a speaker would recognise it in a list.
LANG_NAMES = {"en": "English", "de": "Deutsch", "eo": "Esperanto",
              "es": "Español", "fr": "Français", "hi": "हिन्दी",
              "it": "Italiano", "ja": "日本語", "ko": "한국어",
              "nl": "Nederlands", "pl": "Polski", "pt": "Português",
              "ru": "Русский", "sr": "Srpskohrvatski", "tr": "Türkçe",
              "el": "Ελληνικά", "yi": "ייִדיש", "zh": "中文"}

# Languages written RIGHT TO LEFT. GTK mirrors container packing, alignment and
# widget order for the whole process when the default direction is RTL, which
# is why apps ask nbi18n rather than hard-coding it (see nbapp.apply_direction).
RTL = {"yi"}


def ltr(s):
    """Keep a SIGNED or unit-prefixed figure together in an RTL interface.

    A leading "+"/"−"/"$"/"%"/"(" is a bidi-WEAK character; the digits after it
    are a run of European numerals, so the Unicode bidi algorithm resolves the
    weak char to the paragraph direction and lays it on the far side. Measured
    under yi (the one RTL language, RTL above): a label holding "+$1,105.00"
    has Pango draw "$1,105.00+", and a debit "−$950.00" draws "$950.00−". In a
    ledger the sign is the only thing on the row that says which way the money
    went, so this is correctness, not cosmetics — and it was invisible for
    months because GTK mirrors the CONTAINERS correctly (the screenshot looks
    right) and the UNSIGNED figures are unaffected.

    U+2066 LEFT-TO-RIGHT ISOLATE .. U+2069 POP DIRECTIONAL ISOLATE keeps the
    whole thing one left-to-right run with the weak char attached, and unlike
    an LRM it cannot leak its direction into the surrounding text. Gated on the
    direction ACTUALLY IN FORCE (what Pango lays out against), not the language
    name — so in the other sixteen languages it returns the string byte for
    byte, which is what makes it safe to apply everywhere. Escapes, not literal
    invisibles: a U+2066 sitting in source is a hazard to the next reader and
    to grep. (Promoted from accounting's _ltr, 2026-08-09; app-improve's find.)
    """
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        if Gtk.Widget.get_default_direction() != Gtk.TextDirection.RTL:
            return s
    except Exception:                                             # noqa: BLE001
        return s
    return "\u2066" + s + "\u2069"

# X keyboard layouts (setxkbmap). A layout string may name TWO layouts, e.g.
# "ru,us" — the first is active at login and Alt+Shift switches (see
# apply_keyboard). That matters for any script whose keyboard cannot type ASCII:
# without a Latin layout to switch to, the user could not type a file name, a
# password or a search term. Japanese (JIS) and Korean layouts already type
# ASCII directly, so they stand alone.
KEYBOARDS = (("us", "US (QWERTY)"), ("de", "Deutsch"), ("epo", "Esperanto"),
             ("es", "Español"), ("fr", "Français"), ("in,us", "हिन्दी / English"),
             ("it", "Italiano"), ("jp", "日本語 (JIS)"),
             # Offered, NOT the default. jp(kana) maps keys straight to kana
             # with no dictionary and no IME, so it is the only way to type
             # actual Japanese here — but kana-only is not adult orthography
             # (no kanji), so forcing it on everyone would be wrong. A user who
             # wants to write Japanese can choose it; one who just wants the
             # interface in Japanese is unaffected.
             # ...and it carries "us" as its second half for the same reason
             # Russian and Greek do: kana maps the LETTER keys to kana, so a
             # kana-only keyboard cannot type a password, a file name or a
             # search term. It was the one code here that broke that rule.
             ("jp(kana),us", "日本語 (かな)"), ("kr", "한국어"),
             ("nl", "Nederlands"), ("pl", "Polski"), ("pt", "Português"),
             ("ru,us", "Русский / English"), ("hr", "Srpskohrvatski"),
             ("tr", "Türkçe"), ("gr,us", "Ελληνικά / English"),
             # il(basic) really can type Yiddish: the base Hebrew letters, plus
             # the װ ױ ײ digraphs and qamats/patah/dagesh/rafe/sin-dot on AltGr.
             ("il,us", "ייִדיש / English"))
# Languages that write month and weekday names in LOWER case mid-sentence.
# German capitalises all nouns; English, Turkish and Greek capitalise these
# too; Hindi and the CJK languages have no case at all. Listing the
# lower-casers explicitly means a new language keeps its translator's own
# capitalisation until someone deliberately adds it here.
_LOWER_DATE_WORDS = {"es", "fr", "sr", "it", "pt", "nl", "pl", "ru"}

# Dutch is deliberately "us": the Netherlands types on US QWERTY in practice,
# and the xkb "nl" layout is a legacy oddity almost nobody uses. Chinese types
# Latin and composes through the Pinyin IME (Ctrl+Space), so its base is "us".
DEFAULT_KB = {"en": "us", "de": "de", "eo": "epo", "es": "es", "fr": "fr",
              "hi": "in,us", "it": "it", "ja": "jp", "ko": "kr", "nl": "us",
              "pl": "pl", "pt": "pt", "ru": "ru,us", "sr": "hr", "tr": "tr",
              "el": "gr,us", "yi": "il,us", "zh": "us"}


def _config_path():
    base = os.path.join(os.environ.get("NB_HOME", "/root"), ".config", "notebook")
    return os.path.join(base, "locale.json")


def current_lang():
    """Active UI language code. $NB_LANG wins (so the shell can pin it for a
    launched app / a test), else the persisted setting, else English."""
    env = os.environ.get("NB_LANG")
    if env in SUPPORTED:
        return env
    try:
        with open(_config_path()) as fh:
            code = json.load(fh).get("lang", "en")
        return code if code in SUPPORTED else "en"
    except (OSError, ValueError):
        return "en"


def _fsync_dir(d):
    """Durably record a rename: fsync on the file persists its contents, the
    directory entry the rename created needs its own fsync. Same rule as
    nbapp._fsync_dir, restated here because this module may not import nbapp."""
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _lock_locale(p):
    """Serialise one read-modify-write of locale.json against the others.

    There is no settings service and no session bus on this machine, so this
    file IS the synchronisation point between the processes that write it:
    Settings (language, layout), login.py (the layout a sign-in succeeded on),
    First Run and the installer. Without a lock, two overlapping updates each
    read, then each write, and the later rename silently drops the earlier
    key — Settings persisting a language could undo the layout the sign-in
    screen had just recorded.

    Best effort by design: a lock we cannot take must never stop somebody
    changing their keyboard, so every failure falls through to the unlocked
    write, which is still atomic. Returns an fd to close (closing releases the
    flock), or None."""
    try:
        import fcntl                                           # noqa: PLC0415
        fd = os.open(p + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    except (ImportError, OSError):
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return None
    return fd


def _preserve_damaged(p):
    """Move a locale.json that will not parse aside, before it is replaced.

    Those bytes are the only record of somebody's keyboard: a truncated or
    hand-edited file parses as nothing, this function's caller then starts from
    an empty dict, and the write that follows replaces "ru,us" with a one-key
    file. Nothing on this machine ever re-asks — the interface simply comes back
    in English on a layout the owner did not choose. Every other store gets this
    protection from nbapp.preserve_damaged inside atomic_write_json; locale.json
    cannot, because nbi18n has to keep working on a machine whose de/ tree is
    damaged (login.py imports it before anything else) and so must not import
    nbapp/Gtk. Same quarantine name, so one recovery convention holds OS-wide.

    A healthy file needs no .bak twin here: an update MERGES into what it read,
    so a key nothing touched is carried through rather than replaced."""
    try:
        if not os.path.isfile(p) or os.path.getsize(p) == 0:
            return None
        with open(p, "r", encoding="utf-8") as fh:
            json.loads(fh.read())
        return None                                   # parses: a normal save
    except (OSError, ValueError, UnicodeDecodeError):
        pass
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = "%s.damaged-%s" % (p, stamp)
    n = 2
    while os.path.exists(dest):
        dest = "%s.damaged-%s-%d" % (p, stamp, n)
        n += 1
    try:
        os.replace(p, dest)
        return dest
    except OSError:
        return None


def _update_locale(**kv):
    """Merge `kv` into locale.json, crash-safely. True when it reached disk.

    THE BUG THIS SHAPE EXISTS FOR: the temp file used to be the fixed name
    "locale.json.tmp", shared by every writer. Two overlapping updates opened
    the SAME temp file; the second truncated what the first had buffered, and
    once the second renamed it into place the first's still-open descriptor was
    pointing at the live locale.json and flushed its shorter payload straight
    into it. The healthy config became a half-line of JSON — after which the
    language, the keyboard and the sign-in layout all silently read as their
    defaults. A unique temp per writer (plus the lock above) is what stops that;
    fsync before the rename is what makes a power cut leave the old complete
    file or the new one, matching nbapp.atomic_write_json. The .nbw- prefix is
    deliberate: nbapp._reap_stale_tmp already tidies orphans of that name."""
    p = _config_path()
    d = os.path.dirname(p)
    lock, tmp = None, None
    try:
        os.makedirs(d, exist_ok=True)
        lock = _lock_locale(p)
        _preserve_damaged(p)
        try:
            with open(p) as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data.update(kv)
        fd, tmp = tempfile.mkstemp(prefix=".nbw-", suffix=".tmp", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
        tmp = None
        _fsync_dir(d)
        return True
    except OSError:
        return False
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)          # a write that failed leaves no litter
            except OSError:
                pass
        if lock is not None:
            try:
                os.close(lock)          # releases the flock
            except OSError:
                pass


def set_lang(code):
    """Persist the chosen UI language (applied by apps launched afterwards)."""
    if code not in SUPPORTED:
        return False
    return _update_locale(lang=code)


# Codes this file used to offer, mapped to what they are called now. A layout
# already written into somebody's locale.json goes on being read for the life
# of that machine, so a code cannot simply be changed here: "jp(kana)" alone
# leaves a machine unable to type ASCII, which is the thing the replacement
# exists to fix, and the owner would never see it because nothing re-asks.
_KB_ALIASES = {"jp(kana)": "jp(kana),us"}


def keyboard():
    """Persisted X keyboard layout code, else the current language's default."""
    try:
        with open(_config_path()) as fh:
            kb = json.load(fh).get("keyboard")
        if kb:
            return _KB_ALIASES.get(kb, kb)
    except (OSError, ValueError):
        pass
    return DEFAULT_KB.get(current_lang(), "us")


def set_keyboard(code):
    """Persist the chosen X keyboard layout."""
    return _update_locale(keyboard=code)


def login_keyboard():
    """Which HALF of a dual layout the sign-in screen should start on.

    A layout code ("us", "ru"), or "" for "whichever the saved layout puts
    first". It exists because the password and the interface do not have to be
    in the same alphabet: somebody who set this machine up in English and
    switched it to Russian afterwards has a Latin password and a Cyrillic
    group 1, and would otherwise have to press Alt+Shift before typing on
    every single boot, with nothing to remind them. login.py writes it after a
    sign-in actually succeeds, so it records what worked rather than a guess.
    Only ever the SCRIPT — no part of the password is stored anywhere."""
    try:
        with open(_config_path()) as fh:
            return json.load(fh).get("login_keyboard") or ""
    except (OSError, ValueError):
        return ""


def set_login_keyboard(code):
    """Remember the layout a sign-in succeeded on ("" to forget it)."""
    return _update_locale(login_keyboard=code or "")


def xkb_args(layout):
    """setxkbmap arguments for a layout string, as a list.

    A two-layout string ("ru,us") needs an explicit switch key or the second
    layout is unreachable — and for Russian or Hindi that second layout is the
    only way to type a file name or a password. Alt+Shift is the switch every
    other desktop uses, so it is the one a user will already try.

    Delegated to nbkeyboard, which owns what one of these codes means: this
    version handled a comma but not a parenthesised variant next to one, so
    "jp(kana),us" — a code this file now ships — produced argv the server
    could not load. The fallback below is the old body, kept because this
    module has to keep working on a machine whose de/ tree is damaged (the
    sign-in screen imports it before anything else)."""
    try:
        import nbkeyboard                                      # noqa: PLC0415
        return nbkeyboard.xkb_args(layout)
    except Exception:                                          # noqa: BLE001
        if "," in layout:
            return ["setxkbmap", "-layout", layout,
                    "-option", "grp:alt_shift_toggle"]
        return ["setxkbmap", layout]


# Monotonic Greek DROPS the tonos when a word is set in capitals — ΥΛΙΚΆ is an
# orthographic error, ΥΛΙΚΑ is correct — but Python's str.upper() preserves the
# accent. Every up-cased caption in the OS (INGREDIENTS, METHOD, SECTIONS...)
# was therefore misspelled on nearly every Greek screen. The dialytika is NOT
# dropped, so ΪΫ stay; only the tonos goes, including from the two precomposed
# vowels that carry both.
_EL_UNACCENT = str.maketrans({
    "Ά": "Α", "Έ": "Ε", "Ή": "Η", "Ί": "Ι", "Ό": "Ο", "Ύ": "Υ", "Ώ": "Ω",
    "ΐ": "Ϊ", "ΰ": "Ϋ"})


def _upper(s):
    """Upper-case the way the ACTIVE language does it."""
    out = s.upper()
    if _LANG == "el":
        out = out.translate(_EL_UNACCENT)
    return out


def _load_catalog(code):
    if code == "en":
        return {}
    try:
        with open(os.path.join(CATALOG_DIR, "lang_%s.json" % code),
                  encoding="utf-8") as fh:
            cat = json.load(fh)
        return cat if isinstance(cat, dict) else {}
    except (OSError, ValueError):
        return {}


_LANG = current_lang()
_CAT = _load_catalog(_LANG)


_SPEC = None        # compiled printf-spec matcher, built with the format table
_FMT = ()           # (anchor, regex, translated parts, spec count)


def _split_spec(s):
    """Split a printf-style string into literal / spec pieces. '%%' is a
    literal per cent sign, not a placeholder."""
    global _SPEC
    if _SPEC is None:
        import re
        _SPEC = re.compile(r"(%[-#0-9.+ ]*[a-zA-Z%])")
    out = []
    for p in _SPEC.split(s):
        if p == "%%":
            out.append(("lit", "%"))
        elif p.startswith("%") and len(p) > 1:
            out.append(("spec", p))
        elif p:
            out.append(("lit", p))
    return out


def _spec_kinds(sp):
    """Classify each spec as ordinary (""), or as one of the two slots that
    only exist to make ENGLISH grammar agree with a count:

    "n" — the plural hack, the `%s` in `"%d item%s" % (n, "" if n == 1 else
          "s")`, glued to the end of a word. No other language forms plurals
          that way (Chinese and Serbian do not add an -s at all).
    "v" — the verb that agrees with it, the middle `%s` in `"Its %d task%s %s
          kept"` standing in for "is"/"are". Every other language conjugates
          inside its own sentence.

    Both are consumed on the way in and never emitted, so the translation is
    written with its own natural grammar and simply omits them."""
    out = []
    for i, (kind, p) in enumerate(sp):
        k = ""
        if kind == "spec" and p == "%s":
            before = sp[i - 1][1] if i and sp[i - 1][0] == "lit" else ""
            after = sp[i + 1][1] if i + 1 < len(sp) and sp[i + 1][0] == "lit" else ""
            if before[-1:].isalpha() and (not after or not after[:1].isalpha()):
                k = "n"
            elif ("n" in out and before == " "
                  and after[:2] == " " + after[1:2].lower()
                  and after[1:2].isalpha()):
                # a lone slot mid-sentence, after a counted noun: the verb.
                # The single-space literals are what tells it apart from a real
                # value between separators ("%d clip%s  ·  %s  ·  %d fps").
                k = "v"
        out.append(k)
    return out


def _build_format_table():
    """A label built as `"%d items · %s" % (n, size)` never reaches the catalog
    as a key — the app already substituted the values in. So for every catalog
    entry that carries placeholders, keep a pattern that recognises the
    SUBSTITUTED string and can rebuild it in the target language.

    Only entries with enough literal text to be unmistakable qualify, so a
    filename or a song title can never be mistaken for one."""
    import re
    table = []
    for src, dst in _CAT.items():
        if "%" not in src:
            continue
        sp = _split_spec(src)
        kinds = _spec_kinds(sp)
        real = [p for (k, p), pl in zip(sp, kinds)
                if k == "spec" and not pl]
        # a translation of a counted string may give both grammatical numbers as
        # "singular|plural"; the consumed English -s picks between them
        forms = dst.split("|") if (any(kinds) and dst.count("|") == 1) else [dst]
        dsps = [_split_spec(f) for f in forms]
        if not real or any([p for k, p in f if k == "spec"] != real
                           for f in dsps):
            continue          # translation must carry the same specs, in order
        dsp = (dsps[0], dsps[-1])      # (singular, plural) forms
        lits = [p for k, p in sp if k == "lit"]
        anchor = max(lits, key=len) if lits else ""
        solid = "".join(lits).replace(" ", "")
        if len(anchor.replace(" ", "")) < 3:
            continue                      # too generic to match safely
        pat = []
        free = False
        for (kind, p), plural in zip(sp, kinds):
            if kind == "lit":
                pat.append(re.escape(p))
            elif plural == "n":           # the "…%s" English plural suffix
                pat.append("(e?s?)")      # "" / "s" / "es": number only
            elif plural == "v":           # the agreeing verb: is/are/was/were
                pat.append("([A-Za-z]{1,4})")
            elif p[-1] in "sr":
                free = True
                pat.append("(.*?)")
            else:                         # d i u f g e x X o c
                pat.append(r"([-+0-9][0-9.,+\-eExXa-fA-F]*|[-+]?[0-9.,]+)")
        # a free %s capture can swallow arbitrary user text, so it needs a
        # longer literal to be certain; a digits-only pattern is already safe
        if len(solid) < (5 if free else 4):
            continue
        # the regex captures once per spec, in source order; remember which of
        # those captures are plural markers rather than values
        marks = tuple(pl for (k, _p), pl in zip(sp, kinds) if k == "spec")
        table.append((anchor, re.compile("\\A" + "".join(pat) + "\\Z", re.S),
                      dsp, marks))
    # longest, most specific literal first: "%s used of %s  ·  %d%%" must win
    # over "%s used of %s" for a string that satisfies both
    table.sort(key=lambda t: -len("".join(p for k, p in t[2][0]
                                          if k == "lit")))
    return tuple(table)


def _format_lookup(s):
    """Translate an already-substituted format string, or None."""
    if not _FMT or len(s) > 300 or len(s) < 4:
        return None
    for anchor, rx, dsp, marks in _FMT:
        if anchor not in s:               # cheap reject before the regex
            continue
        m = rx.match(s)
        if m is None or len(m.groups()) != len(marks):
            continue
        vals, plural, seen_mark = [], False, False
        for cap, is_mark in zip(m.groups(), marks):
            if is_mark:
                # The FIRST marker is the counted noun's; a later one can be a
                # verb, where English puts the -s on the SINGULAR ("1 recipe
                # stays" / "4 recipes stay"), so only the first is trustworthy.
                if not seen_mark:
                    plural, seen_mark = bool(cap), True
            else:
                # a free %s can itself be a date ("%s · added %s" carries the
                # long date heading), and _date_lookup only fires on something
                # that is entirely a date, so this cannot touch user text
                vals.append(_date_lookup(cap) or cap)
        d = dsp[1] if plural else dsp[0]
        out = []
        for kind, p in d:
            out.append(p if kind == "lit" else (vals.pop(0) if vals else ""))
        return "".join(out)
    return None


_DATE_RE = None
_PROSE_RE = None
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December",
           "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
           "Sunday", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
           "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Oct",
           "Nov", "Dec")
# %p writes these beside the time, and they are not date words to translate
_DATE_EXTRA = ("AM", "PM", "am", "pm")


def _date_lookup(s):
    """Headings and clocks are built with time.strftime, which has no locale on
    this image and so always writes English month and weekday names ("July
    2026", "Fri 24 Jul", "Friday 24 July 2026"). The surrounding numbers make
    the whole string unmatchable, so translate the date words inside it.

    Guarded: the string has to be a date and nothing else — every word in it a
    month, a weekday or am/pm. Without that guard the rule reaches into
    ordinary prose, where "you may want to" becomes "you Mayo want to" and a
    photo called "March notes" is renamed on screen."""
    global _DATE_RE, _PROSE_RE
    if len(s) > 60:
        return None
    if _DATE_RE is None:
        import re
        longest = "|".join(sorted(_MONTHS + _DATE_EXTRA, key=len, reverse=True))
        names = [m for m in _MONTHS if m in _CAT]
        _DATE_RE = (re.compile(r"\b(%s)\b" % "|".join(sorted(names, key=len,
                                                             reverse=True)))
                    if names else False)
        # any word that is NOT one of those: one hit and this is not a date
        _PROSE_RE = re.compile(r"\b(?!(?:%s)\b)[A-Za-z]+" % longest)
    if not _DATE_RE or _PROSE_RE.search(s):
        return None
    if _LANG in _CJK_DATE:
        return _zh_date(s, _CJK_DATE[_LANG])

    def sub(m):
        # Only lower-case for languages that actually write month and weekday
        # names that way. German capitalises every noun, so blanket-lowering
        # produced "Sa 25 jul" in the panel clock and "Samstag, 25 juli" in
        # Tasks — and no catalog value could survive it. Anything not listed
        # keeps whatever capitalisation its translator wrote, which is the
        # right default: trust the catalog over a rule we guessed.
        t = _CAT.get(m.group(1), m.group(1))
        if m.start() and _LANG in _LOWER_DATE_WORDS:
            return t.lower()
        return t
    out = _DATE_RE.sub(sub, s)
    if _LANG == "es":
        out = _es_date(out)
    return out if out != s else None


_ES_RE = None


def _es_date(s):
    """Spanish binds the month to the day and the year with "de":
    "viernes, 24 de julio de 2026", not "viernes, 24 julio 2026"."""
    global _ES_RE
    if _ES_RE is None:
        import re
        full = [_CAT[m].lower() for m in _MONTHS[:12] if m in _CAT]
        if not full:
            _ES_RE = False
        else:
            names = "|".join(sorted(full, key=len, reverse=True))
            _ES_RE = (re.compile(r"(?<=\d)(\s+)(%s)\b" % names, re.I),
                      re.compile(r"\b(%s)(\s+)(?=\d{4}\b)" % names, re.I))
    if not _ES_RE:
        return s
    s = _ES_RE[0].sub(lambda m: " de " + m.group(2), s)
    return _ES_RE[1].sub(lambda m: m.group(1) + " de ", s)


_MONTH_NO = dict(
    [(m, i + 1) for i, m in enumerate(_MONTHS[:12])]
    + [(m, i + 1) for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May",
                                         "Jun", "Jul", "Aug", "Sep", "Oct",
                                         "Nov", "Dec"))])
_ZH_RE = None


# The CJK languages all write a date big-endian and numerically, differing only
# in the unit characters and whether the parts are spaced. Substituting English
# words where they stand ("周五 24 7月" / "土曜日, 25 7月 2026") is not a date in
# any of them, so take it apart and lay it out again.
#   zh  2026年7月24日 周五      ja  2026年7月24日 金曜日      ko  2026년 7월 24일 금요일
_CJK_DATE = {"zh": ("年", "月", "日", ""),
             "ja": ("年", "月", "日", ""),
             "ko": ("년", "월", "일", " ")}


def _zh_date(s, units=None):
    """Rebuild a date in CJK order. `units` is (year, month, day, joiner);
    it defaults to Chinese so existing callers are unchanged."""
    y_u, m_u, d_u, join = units or _CJK_DATE["zh"]
    global _ZH_RE
    if _ZH_RE is None:
        import re
        _ZH_RE = re.compile(r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp][Mm])?)"
                            r"|(?P<word>[A-Za-z]+)|(?P<num>\d+)")
    year = day = month = wd = ""
    tail = []
    for m in _ZH_RE.finditer(s):
        if m.group("time"):
            tail.append(m.group("time").strip())
        elif m.group("word"):
            w = m.group("word")
            if w in _MONTH_NO:
                month = "%d%s" % (_MONTH_NO[w], m_u)
            elif w in _CAT:
                wd = _CAT[w]                     # 星期五 / 周五
        else:
            n = m.group("num")
            if len(n) == 4:
                year = n + y_u
            elif not day:
                day = "%d%s" % (int(n), d_u)
    # A real date WORD has to be present — a month or a weekday. Accepting a
    # bare year here meant any four-digit number anywhere in the OS was rebuilt
    # as a date in ja/ko/zh: the 2048 game's tiles read "2048年", and the
    # Settings ▸ Displays list turned "1920x1080" into "1080年" (the second
    # 4-digit run overwrote the first) and "1366x768" into "1366年768日" — which
    # also fed xrandr --mode "1080年", so changing resolution failed outright in
    # those three languages. With no month and no weekday there is no English
    # word to translate, so there is nothing for this rule to do.
    if not (month or wd):
        return None
    stamp = join.join(p for p in (year, month, day) if p)
    out = " ".join(p for p in (stamp, wd, " ".join(tail)) if p)
    return out or None


_SUFFIX_RE = None


def _suffix_lookup(s):
    """A tooltip that advertises its shortcut — "Brush  (B)", "Save  (Ctrl+S)"
    — is the name plus a bracket the catalog has no reason to carry in every
    language. Translate the name and keep the bracket. The name must be an
    exact catalog hit, so a file called "Report (v2)" is never touched."""
    global _SUFFIX_RE
    if _SUFFIX_RE is None:
        import re
        # the bracket has to read as a shortcut or a count — a single capital,
        # a function key, a modifier combo, a number. "(v2)" and "(draft)" are
        # somebody's file name, and are left alone.
        _SUFFIX_RE = re.compile(
            r"\A(.+?)(\s{1,3}\((?:(?:Ctrl|Alt|Shift|Cmd)\+)?"
            r"(?:[A-Z]|F[0-9]{1,2}|[0-9]{1,3})\))\Z")
    m = _SUFFIX_RE.match(s)
    if m is None:
        return None
    hit = _CAT.get(m.group(1))
    return None if hit is None else hit + m.group(2)


def _lookup(s):
    """Catalog hit for `s`, or None. Falls back through the transforms an app
    applies to a label after writing it: group headings set with text.upper()
    ("COLORS", "SECTIONS", "OPACITY"), which no longer match the sentence-case
    key the catalog is written in; a shortcut appended to a tooltip; printf
    substitution, which replaces the placeholders with live values before the
    text is ever set; and strftime, which bakes an English month or weekday
    into a date heading."""
    hit = _CAT.get(s)
    if hit is not None:
        return hit
    # len > 2, not > 1. A TWO-letter capital is almost never a word we mean to
    # translate — it is a badge or an initialism — but capitalising it lands
    # straight on a weekday abbreviation: the Language app's course badge
    # `code.upper()[:2]` makes "FR", which matched the catalog's "Fr" (Friday)
    # and rendered the French course as शु in Hindi and VI in Spanish. Words
    # this branch exists for (COLORS, SECTIONS) are all longer than two.
    if s.isupper() and len(s) > 2:
        for form in (s.capitalize(), s.title()):
            hit = _CAT.get(form)
            if hit is not None:
                return _upper(hit)
    hit = _suffix_lookup(s)
    if hit is not None:
        return hit
    hit = _format_lookup(s)
    return hit if hit is not None else _date_lookup(s)


# Everything _t() has already translated. The widget walk below skips these,
# because a translation can itself be an English word: Spanish for "Network" is
# "Red", and an unguarded second look-up turned the Settings sidebar into
# "Rojo". A string this layer produced is finished with.
_EMITTED = set()


def _t(s):
    """Translate a source (English) string to the active language, or return it
    unchanged when there is no translation (graceful English fallback)."""
    if not _CAT:
        return s
    hit = _lookup(s)
    if hit is None:
        return s
    # callers do `_t("%d of %d") % (a, b)`, so a translation whose placeholders
    # do not line up with the source's would raise at the % — English instead.
    if "%" in s and _specs_of(hit) != _specs_of(s):
        return s
    if hit != s:
        _EMITTED.add(hit)
    return hit


def _specs_of(s):
    return [p for k, p in _split_spec(s) if k == "spec"]


def lang():
    return _LANG


def language_name(code=None):
    return LANG_NAMES.get(code or _LANG, code or _LANG)


# ---------------------------------------------------------------------------
# Automatic widget translation
#
# Wrapping every one of the ~2000 on-screen strings across 40+ apps in _t() by
# hand leaves gaps forever: one new Gtk.Label in one app and that screen is
# half English again. So on top of the explicit _t() calls, the catalog is also
# applied to the widget tree itself — when a window (or a dialog, a menu, a
# rebuilt row) is shown, every label/button/menu title on it is looked up in
# the catalog and replaced if there is an exact match.
#
# It is deliberately exact-match only: a string the catalog does not know is
# left completely alone, so filenames, song titles, contact names and anything
# else the user typed pass through untouched. When the language is English the
# catalog is empty and none of this is installed at all — zero cost, zero risk
# on the default path.
#
# Every widget it touches is stamped with the text it was given, and a stamped
# widget is never translated again. That is not an optimisation, it is what
# stops a SECOND pass translating a translation: Spanish "Network" is "Red",
# and "Red" on its own is the colour, so a re-run turned the Settings sidebar
# into "Rojo". Serbian "Sa"(turday) is "Su", which is English Sunday. Once
# written, the text is finished with.
# ---------------------------------------------------------------------------

_MARK = "_nbi18n_done"


def _stamped(w, cur):
    """True when this text is finished with — either we wrote it onto this
    widget ourselves, or _t() produced it at the call site."""
    return cur in _EMITTED or getattr(w, _MARK, None) == cur


def _stamp(w, new):
    try:
        setattr(w, _MARK, new)
    except (AttributeError, TypeError):
        pass                                # not stampable: worst case, a re-run

_TAG_RE = None


def set_verbatim(widget, text):
    """Put text the USER typed on a translatable widget, exactly as typed.

    The auto-translate layer below cannot tell a label the app wrote from a
    label holding the user's own words, so it looks up both. That is right for
    chrome and wrong for content, and the difference is not cosmetic when the
    widget is also where the app READS the value back from:

        Novel keeps the manuscript title in its sidebar label and _serialize()
        reads it straight out again. A writer on a Spanish install who named her
        book "Notes" got "Notas" — on screen, in the file, and in the filename
        Save As suggested, because the store was written from the translated
        label. "Contents", "Journal", "Body", "Quote", "Save" and "Chapter 1"
        all do the same thing; only a title with no catalog entry survived.

    Stamping the widget with the exact string is what _stamped() checks, so the
    setter and the show_all walk both leave it alone. Use this for any name,
    title, filename or message a person typed."""
    try:
        _stamp(widget, text)
    except Exception:
        pass
    try:
        widget.set_text(text)
    except Exception:
        widget.set_label(text)


def _t_markup(s):
    """Translate the text runs of a Pango markup string, leaving tags alone."""
    global _TAG_RE
    if _TAG_RE is None:
        import re
        _TAG_RE = re.compile(r"(<[^>]*>)")
    out = []
    for part in _TAG_RE.split(s):
        if part.startswith("<") or not part.strip():
            out.append(part)
            continue
        core = part.strip()
        hit = _lookup(core)
        if hit is None:
            out.append(part)
        else:                                   # keep the surrounding spaces
            head = part[:len(part) - len(part.lstrip())]
            tail = part[len(part.rstrip()):]
            out.append(head + hit + tail)
    return "".join(out)


def _install_auto_translate():
    """Patch the handful of Gtk setters/containers that put text on screen so
    the catalog is applied wherever it was not applied by hand. Every hook is
    wrapped in try/except: a translation must never be able to break an app."""
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
    except (ImportError, ValueError):
        return

    # -- setters: catch text assigned after the window was already shown ----
    def wrap(cls, meth, conv=None):
        orig = getattr(cls, meth, None)
        if orig is None:
            return None

        def patched(self, s, *a, **kw):
            try:
                if isinstance(s, str) and not _stamped(self, s):
                    s = conv(s) if conv else (_lookup(s) or s)
                    _stamp(self, s)
            except Exception:
                pass
            return orig(self, s, *a, **kw)
        setattr(cls, meth, patched)
        return orig

    o_label_set_label = wrap(Gtk.Label, "set_label")
    o_label_set_markup = wrap(Gtk.Label, "set_markup", _t_markup)
    wrap(Gtk.Label, "set_text")
    o_button_set_label = wrap(Gtk.Button, "set_label")
    wrap(Gtk.MenuItem, "set_label")
    wrap(Gtk.Window, "set_title")
    wrap(Gtk.Widget, "set_tooltip_text")
    wrap(Gtk.Entry, "set_placeholder_text")
    wrap(Gtk.ProgressBar, "set_text")
    wrap(Gtk.Expander, "set_label")
    wrap(Gtk.Frame, "set_label")
    wrap(Gtk.TreeViewColumn, "set_title")
    wrap(Gtk.ComboBoxText, "append_text")
    wrap(Gtk.ComboBoxText, "prepend_text")

    # -- the tree walk: catches text passed to a CONSTRUCTOR (Gtk.Label(
    #    label="…"), Gtk.Button(label="…")), which no setter ever sees -------
    def fix_label(w):
        s = w.get_label()
        if not s or _stamped(w, s):
            return
        if w.get_use_markup():
            new = _t_markup(s)
            if new != s:
                o_label_set_markup(w, new)
        else:
            new = _lookup(s)
            if new is not None:
                o_label_set_label(w, new)
        _stamp(w, new if new else s)

    def _stamp_button_child(w):
        """Carry a button's stamp onto the Label INSIDE it.

        A Gtk.Button is a Gtk.Container, and the walk below descends into the
        label it holds. So stamping the button protected nothing: the walk
        skipped the button and then translated its own child on the very next
        step. set_verbatim(button, text) — the call that exists to keep the
        catalog off text that must not change — did not work on a button at
        all. It was measured on the sign-in screen's keyboard switch, where
        the button naming the layout that types Latin letters came out reading
        "Английский (США)" while the sentence pointing at that button, which
        substitutes the name AFTER translation, went on saying "English (US)":
        a message naming a button that was not on screen, on the one screen
        that can strand somebody."""
        try:
            ch = w.get_child()
            if isinstance(ch, Gtk.Label):
                _stamp(ch, ch.get_label())
        except Exception:
            pass

    def fix_button(w):
        s = w.get_label()
        if not s or _stamped(w, s):
            _stamp_button_child(w)
            return
        new = _lookup(s)
        if new is not None:
            o_button_set_label(w, new)
        _stamp(w, new if new else s)
        _stamp_button_child(w)

    def walk(w, depth=0):
        if depth > 60:
            return
        try:
            if isinstance(w, Gtk.Label):
                fix_label(w)
            elif isinstance(w, Gtk.Button):
                fix_button(w)          # the child Label is visited below too
            elif isinstance(w, Gtk.Entry):
                p = w.get_placeholder_text()
                if p and not _stamped(w, p) and _lookup(p):
                    w.set_placeholder_text(p)     # patched setter translates
            elif isinstance(w, Gtk.TreeView):
                for col in w.get_columns():
                    t = col.get_title()
                    if t and not _stamped(col, t) and _lookup(t):
                        col.set_title(t)
            elif isinstance(w, Gtk.Window):
                t = w.get_title()
                if t and not _stamped(w, t) and _lookup(t):
                    w.set_title(t)
        except Exception:
            pass
        try:
            if isinstance(w, Gtk.MenuItem):
                sub = w.get_submenu()
                if sub is not None:
                    walk(sub, depth + 1)
            if isinstance(w, Gtk.Notebook):
                for page in w.get_children():
                    lbl = w.get_tab_label(page)
                    if lbl is not None:
                        walk(lbl, depth + 1)
            if isinstance(w, Gtk.Container):
                for c in w.get_children():
                    walk(c, depth + 1)
        except Exception:
            pass

    o_show_all = Gtk.Widget.show_all

    def patched_show_all(self):
        try:
            walk(self)
        except Exception:
            pass
        return o_show_all(self)
    Gtk.Widget.show_all = patched_show_all

    o_show = Gtk.Widget.show

    def patched_show(self):
        try:
            walk(self)
        except Exception:
            pass
        return o_show(self)
    Gtk.Widget.show = patched_show


if _CAT:
    _FMT = _build_format_table()
    _install_auto_translate()
