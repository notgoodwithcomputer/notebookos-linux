#!/usr/bin/env python3
"""nbkeyboard — what an nbi18n layout code MEANS, in one place.

An nbi18n keyboard code is an X keyboard string: "us", "fr", "ru,us",
"jp(kana)". Four different files used to take that string apart with four
private helpers, and every one of them got a different subset of the rules
right. The rules are:

  * a COMMA separates layout GROUPS. Group 1 is the one that is live when the
    keymap is loaded; the rest are only reachable through a switch key, so a
    multi-group string without `grp:alt_shift_toggle` has an unreachable half.
  * PARENTHESES name a VARIANT, and a variant is not a layout: XkbLayout
    "jp(kana)" in xorg.conf yields no keymap at all. `setxkbmap` happens to
    accept the parenthesised form on its command line, the X server does not,
    so the two need different argv/config shapes from the same code.
  * some groups cannot type ASCII. That is not a detail — the password, the
    file name and the search box are all ASCII on this machine, so a code
    whose groups are ALL non-Latin is a code somebody can be locked out by.

WHY "CAN THIS GROUP TYPE LATIN" IS A FUNCTION AND NOT A COMMENT
de/login.py is the one screen that can strand the owner of an offline computer.
A Russian, Greek, Hindi or Yiddish install comes up with Cyrillic/Greek/
Devanagari/Hebrew as group 1, so a password made of Latin letters — which is
what anybody who set the machine up in English and switched language later, or
who deliberately pressed Alt+Shift while choosing it, actually has — cannot be
typed at the prompt at all. Nothing on the screen said which script the keys
were producing, and the field is masked, so the only visible fact was "my
password is wrong". `latin_index()` is what lets the sign-in screen say which
script it is typing and offer the other one.

The Latin question is answered from an explicit list, and anything unknown is
assumed NOT to be Latin. That direction is deliberate: guessing "Latin" wrongly
locks somebody out, guessing "not Latin" wrongly costs one redundant switcher.
"""
import re
import subprocess

# Layout codes whose letter keys produce ASCII. Everything the OS itself
# offers (nbi18n.KEYBOARDS) is listed, plus the common European codes a
# hand-edited locale.json might hold. "jp" (JIS) and "kr" belong here: both
# type ASCII directly and reach their own scripts through an input method,
# which is why nbi18n ships them without a second group.
LATIN_LAYOUTS = {
    "us", "gb", "de", "at", "ch", "fr", "be", "es", "it", "nl", "pt", "br",
    "dk", "se", "no", "fi", "is", "ie", "ee", "lv", "lt", "pl", "cz", "sk",
    "hu", "ro", "si", "hr", "ba", "al", "mt", "tr", "epo", "latam", "ca",
    "jp", "kr", "vn", "ph", "id", "my", "af", "ng", "tz", "ke", "za",
}

# A variant can move a layout across that line in either direction, so the
# variant is checked before the layout. jp(kana) maps the letter keys straight
# to kana: the base layout types ASCII, this variant does not, and it is the
# one shipped code with no Latin group of its own.
NON_LATIN_VARIANTS = {("jp", "kana")}
# ...and the Latin-alphabet variants of Cyrillic-alphabet layouts.
LATIN_VARIANTS = {("rs", "latin"), ("by", "latin"), ("kz", "latin"),
                  ("ua", "latin"), ("ru", "latin")}

# The Latin group appended when a code has none. Plain "us" and not the
# language's own layout: this exists so a password, a file name and a URL can
# be typed at all, and QWERTY is the one arrangement every one of those was
# probably written on.
LATIN_FALLBACK = "us"

# What each group is CALLED, in its own script — the way nbi18n.LANG_NAMES
# names languages. Deliberately not translated: somebody looking for the half
# of their keyboard that types their own alphabet is looking for their own
# alphabet, not for the English word for it.
GROUP_NAMES = {
    "us": "English (US)", "gb": "English (UK)", "de": "Deutsch",
    "epo": "Esperanto", "es": "Español", "fr": "Français", "in": "हिन्दी",
    "it": "Italiano", "jp": "日本語 (JIS)", "jp(kana)": "日本語 (かな)",
    "kr": "한국어", "nl": "Nederlands", "pl": "Polski", "pt": "Português",
    "ru": "Русский", "hr": "Srpskohrvatski", "tr": "Türkçe",
    "gr": "Ελληνικά", "il": "ייִדיש",
}

SWITCH_OPTION = "grp:alt_shift_toggle"

_PAREN = re.compile(r"^([^(]+)\((.+)\)$")


def parse(code):
    """An nbi18n code -> [(layout, variant), ...], one pair per group.

    "ru,us" -> [("ru", ""), ("us", "")]; "jp(kana),us" -> [("jp", "kana"),
    ("us", "")]. Never empty and never raises: a code this cannot make sense
    of becomes plain US, because every caller of this is on a path where
    returning nothing means a machine with no keyboard. Exact duplicates are
    collapsed in first-use order: two identical XKB groups only create a dead
    Alt+Shift binding and duplicate keyboard buttons that switch nowhere.
    """
    # Locale state is persisted JSON and can be damaged or hand-edited.  This
    # helper is used while constructing Login, so an integer/list here must
    # degrade to the safe layout rather than strand the machine at sign-in.
    if not isinstance(code, str):
        return [(LATIN_FALLBACK, "")]
    out = []
    seen = set()
    for part in (code or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = _PAREN.match(part)
        if m:
            group = (m.group(1).strip(), m.group(2).strip())
        else:
            group = (part, "")
        if group not in seen:
            out.append(group)
            seen.add(group)
    return out or [(LATIN_FALLBACK, "")]


def join(groups):
    """[(layout, variant), ...] -> the nbi18n code for it."""
    return ",".join("%s(%s)" % (l, v) if v else l for l, v in groups)


def is_latin(layout, variant=""):
    """Can this group type the ASCII alphabet?

    Unknown answers False. See the module docstring: the cost of being wrong
    is not symmetric."""
    if (layout, variant) in LATIN_VARIANTS:
        return True
    if (layout, variant) in NON_LATIN_VARIANTS:
        return False
    return layout in LATIN_LAYOUTS


def latin_index(code):
    """Index of the first group that can type ASCII, or -1 if there is none."""
    for i, (layout, variant) in enumerate(parse(code)):
        if is_latin(layout, variant):
            return i
    return -1


def ensure_latin(code):
    """`code`, guaranteed to contain a group that can type ASCII.

    Appends US rather than replacing anything, so the user's own script stays
    group 1 and stays the default. Used by the sign-in screen, which cannot
    ask somebody to type a password in an alphabet their keyboard does not
    have, and by anything else that has to be typable in ASCII."""
    if latin_index(code) >= 0:
        return join(parse(code))
    return join(parse(code) + [(LATIN_FALLBACK, "")])


def ensure_qwerty(code):
    """`code`, guaranteed to contain a plain US QWERTY group.

    STRONGER THAN ensure_latin, AND FOR A DIFFERENT QUESTION. ensure_latin
    asks "can some group here produce ASCII letters?" — and a machine locked
    somebody out while the answer was yes. Japanese ships "jp", which is on
    the Latin list because JIS types ASCII directly; but JIS puts ASCII in
    DIFFERENT PLACES than US QWERTY, and three of those places do not exist on
    an ANSI keyboard at all:

        _ \\ |   live on <AB11>, the RO key, which a 104-key board has no
                 key for. A password containing an underscore was therefore
                 not mistyped on that hardware — it was untypeable.

    and 20 more ASCII characters merely move (@ is Shift+2 on US and its own
    key on JIS, so Shift+2 yields "). The field is masked, so the only fact on
    screen is "that password did not work", forever, on an offline machine
    with no getty. The same measurement over the rest of the shipped layouts:
    de loses < > ^ ` |, es loses ^ `, pt loses ^ ` ~, and fr (AZERTY) moves 38
    characters without losing any — typing "abc" gives "qbc".

    So the sign-in screen guarantees the arrangement passwords are actually
    written on, not merely an alphabet. US is APPENDED, never substituted: the
    machine's own layout stays group 1 and stays live, and this only adds a
    way back. XKB supports at most four groups, so an already-full custom list
    retains its first three and deterministically gives the final slot to US.
    """
    groups = parse(code)
    if any(lay == LATIN_FALLBACK and not var for lay, var in groups):
        return join(groups)
    if len(groups) >= 4:
        groups = groups[:3]
    return join(groups + [(LATIN_FALLBACK, "")])


def reorder(code, index):
    """`code` with group `index` moved to the front, i.e. made the live one.

    Every other group is kept, in order, so the switch key still reaches them.
    An index outside the code is ignored rather than raising."""
    groups = parse(code)
    if not (0 <= index < len(groups)):
        return join(groups)
    return join([groups[index]] + groups[:index] + groups[index + 1:])


def group_name(layout, variant=""):
    """What to call one group on screen."""
    key = "%s(%s)" % (layout, variant) if variant else layout
    if key in GROUP_NAMES:
        return GROUP_NAMES[key]
    if layout in GROUP_NAMES:
        # A variant we have no name for still belongs to a layout we do.
        return "%s (%s)" % (GROUP_NAMES[layout], variant) if variant \
            else GROUP_NAMES[layout]
    return key.upper() if len(key) <= 3 else key


def group_names(code):
    """One display name per group, in the code's own order."""
    return [group_name(l, v) for l, v in parse(code)]


def xorg_parts(code):
    """(XkbLayout, XkbVariant, XkbOptions) — the xorg.conf / -variant shape.

    The X server takes layouts and variants as two PARALLEL comma lists and
    does not parse "jp(kana)" at all, so the variant list is padded to the
    same length as the layout list. Returns "" for the variant list when no
    group has one, so the common case writes no XkbVariant line."""
    groups = parse(code)
    layouts = ",".join(l for l, _v in groups)
    variants = ",".join(v for _l, v in groups) if any(v for _l, v in groups) \
        else ""
    options = SWITCH_OPTION if len(groups) > 1 else ""
    return layouts, variants, options


def xkb_args(code):
    """The setxkbmap argv for `code`.

    `-option ""` before the real option clears whatever the server is already
    carrying: without it setxkbmap ADDS to the current option list, so a
    machine that once loaded a dual layout keeps Alt+Shift bound after moving
    to a single one, and Alt+Shift then does nothing visible in a session
    where it used to switch alphabets."""
    layouts, variants, options = xorg_parts(code)
    args = ["setxkbmap", "-layout", layouts]
    if variants:
        args += ["-variant", variants]
    args += ["-option", ""]
    if options:
        args += ["-option", options]
    return args


def apply(code, timeout=10):
    """Load `code` on the running X server. True if setxkbmap said it worked.

    Best effort by design: every caller has something better to do than fail
    because a keymap did not load, and the sign-in screen in particular must
    still appear on a machine with no setxkbmap at all."""
    try:
        r = subprocess.run(xkb_args(code), capture_output=True,
                           timeout=timeout)
        return r.returncode == 0
    except Exception:                                          # noqa: BLE001
        return False


def live_code(timeout=3):
    """The layouts the X server is actually using, or "" if unknown."""
    try:
        result = subprocess.run(["setxkbmap", "-query"], capture_output=True,
                                text=True, timeout=timeout)
        if result.returncode != 0:
            return ""
        fields = {}
        for line in result.stdout.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.strip()] = value.strip()
        layouts = [part.strip() for part in fields.get("layout", "").split(",")]
        variants = [part.strip() for part in fields.get("variant", "").split(",")]
        groups = []
        for index, layout in enumerate(layouts):
            if not layout:
                continue
            variant = variants[index] if index < len(variants) else ""
            groups.append((layout, variant))
        return join(groups) if groups else ""
    except Exception:                                          # noqa: BLE001
        return ""
