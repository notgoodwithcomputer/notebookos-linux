#!/usr/bin/env python3
"""
voice_check — find the assistant's writing voice in user-visible text.

THE MANDATE this enforces, from the owner: "every single piece of text across
the entire OS should purely describe function and literally nothing else."
A heading is a noun. A button is a verb. A message states a fact. Anything that
reassures, encourages, editorialises, sets a scene, explains the design, or
mentions the internet is not function and does not belong in the UI.

This is a LINTER, not a judge: it flags candidates by pattern, and a human
decides. It exists because the voice is pervasive rather than localised -- it
turns up as a rhetorical em dash, an appositive gloss, a possessive that
editorialises -- and eyeballing 40 files misses most of it.

Strings are extracted with ast, never grep: this codebase uses implicit
multi-line concatenation everywhere, so a grep for a phrase silently misses any
sentence that happens to wrap.

  python3 tools/voice_check.py [--file X.py ...] [--fail] [-v]

A bare run is a GATE (task 026): it exits non-zero on any flagged string that
tools/voice_ledger.json does not account for, and on any ledger entry gone
stale. The ledger has two shelves — "allow" (judged acceptable, with the
reason) and "pending" (real voice, awaiting rewrite by the owning app lane) —
and both ratchet: fixing a pending string without deleting its entry fails,
deleting an entry without fixing the string fails. --fail is retained for
compatibility and is now implied.

Format strings are CHECKED, not skipped (this was the gate's largest blind
spot: 305 strings containing %s — precisely the confirms and destructive
warnings — were invisible). Placeholders are neutralised to an inert token
before the rules run, so "%s deleted" is judged as a sentence without the
substitution inventing words a rule could false-match.

ENGLISH SOURCE ONLY. Do not point this, or the mandate it enforces, at the
translation catalogs. Measured 2026-08-04: 110 Japanese strings end in
`ください`, 80 Chinese ones contain `请`, 25 Korean ones `주세요` — all
"please", none of which appears in the English. That is not the assistant's
voice creeping in; it is the required register for a consumer interface in
those languages, and an English rule against politeness markers would make
every one of them read curtly wrong. The mandate is about not editorialising,
and what counts as plain description is a fact about each language.

What IS worth flagging in a catalog is a translation that addresses the reader
where the English does not — Japanese `ましょう` ("let's") against a plain
instruction, or "Ya puedes retirar la unidad" for "Safe to remove the drive".
Six such strings were found and fixed by hand; there is no automatic check for
it, because telling those apart from required politeness needs the language.
"""
import argparse
import ast
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

# Calls whose first argument is shown to a person.
TEXT_CALLS = {"set_text", "set_label", "set_markup", "set_tooltip_text",
              "set_placeholder_text", "set_title", "_t", "_flash",
              "append_text", "prepend_text"}

RULES = [
    # (name, regex, why)
    ("network", r"\b(offline|online|internet|network|wi-?fi|cloud|upload|download"
                r"|connected to|never leaves|leaves this (computer|machine))\b",
     "mentions connectivity; this computer never needs to"),
    ("reassurance", r"\b(don'?t worry|no need to worry|nothing is lost|you can "
                    r"always|there is no rush|no rush|perfectly fine|it'?s fine"
                    r"|safely tucked|rest assured|as long as you like)\b",
     "reassures instead of stating a fact"),
    ("encouragement", r"\b(enjoy|have fun|happy \w+ing|good luck|nicely done"
                      r"|well done|you'?re all set|ready when you are)\b",
     "encourages the user"),
    ("editorialising", r"\b(yours to \w+|is yours|beautiful|lovely|delightful"
                       r"|magic|elegant|charming|the way you\b|as you will\b)\b",
     "editorialises rather than describing"),
    ("appositive-gloss", r"^[A-Z][A-Za-z ]{2,24} (—|--) (the|a|an|what|where|how) ",
     "heading followed by an explanatory gloss"),
    ("rhetorical-dash", r"\S+ (—|--) (so|which|and that|meaning|because|the one"
                        r"|not )",
     "em dash used to add an aside"),
    ("second-person-flourish", r"\b(you'?ll see|you will see|what you'?ve|things "
                               r"you\b|somewhere to\b|a place to\b)\b",
     "addresses the reader instead of naming the thing"),
    ("design-explanation", r"\b(that is why|the reason|deliberately|on purpose"
                           r"|by design|we chose|this exists)\b",
     "explains the design to the user"),
    # The dominant form of the voice in THIS tree. An earlier version of this
    # checker missed 7 of 9 strings I knew to be the voice, because it looked
    # for soft adjectives while the actual habit is second-person possessives
    # and a coaxing "Open X to ..." prompt under every empty state.
    ("second-person", r"\b(your|yours|you'?ll|you'?ve|you can|you have)\b",
     "addresses the user; a label names a thing instead"),
    ("coaxing-prompt", r"^(Open|Start|Try|Pick|Choose|Make|Add) [A-Z]?\w+ to \w+",
     "instructs the user in prose rather than labelling a control"),
    ("this-is-where", r"\b(this is where|appears? here|shows? up here|goes here"
                      r"|will appear|somewhere for)\b",
     "narrates the layout"),
    ("soft-absence", r"^(Nothing|No \w+)\b.*\b(yet|so far|just now)\b",
     "softens an empty state; state the absence plainly"),
    ("prose-in-ui", r"^[A-Z][^.!?]{64,}[.!?]$",
     "a sentence of prose in a UI; cut to the fact or delete"),
]

SKIP_SUBSTR = ("http://", "https://", "/dev/", "/etc/", "/opt/")

# %s / %d / %(name)s / {} / {0} → an inert token no rule can word-match.
_PLACEHOLDER = re.compile(r"%\([^)]+\)[sdifr]|%[sdifxr%]|%\.\d+[fd]|\{[^{}]*\}")


def catalog_keys():
    """Every string the translation catalogs know about.

    Catalog membership is the strongest available signal that a string is
    USER-VISIBLE: nothing gets translated unless it is shown. This catches the
    ones the call-site scan cannot -- menu items passed as tuples, dict values
    like TILE_EMPTY, anything handed through a variable -- which is most of
    them, and is why an earlier version of this check under-reported so badly.
    """
    import json
    try:
        with io.open(os.path.join(DE, "lang_es.json"), encoding="utf-8") as fh:
            return set(json.load(fh))
    except (OSError, ValueError):
        return set()


CATALOG = None


def strings_in(path):
    """(lineno, text) for every string that reaches a person."""
    try:
        tree = ast.parse(io.open(path, encoding="utf-8").read())
    except (OSError, SyntaxError):
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        args = []
        if name in TEXT_CALLS and node.args:
            args = [node.args[0]]
        elif name in ("Label", "Button", "CheckButton", "MenuItem", "Entry"):
            args = [kw.value for kw in node.keywords
                    if kw.arg in ("label", "text", "title")]
        for a in args:
            # unwrap _t("...") so the literal inside is judged
            if isinstance(a, ast.Call) and (
                    getattr(a.func, "id", None) == "_t") and a.args:
                a = a.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                yield node.lineno, a.value
    # ...plus every literal anywhere in the file that the catalogs translate.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value in (CATALOG or ()):
            yield getattr(node, "lineno", 0), node.value


def judge(text):
    out = []
    if len(text.strip()) < 3:
        return out
    text = _PLACEHOLDER.sub("X", text)
    for name, pat, why in RULES:
        if re.search(pat, text, re.I):
            out.append((name, why))
    return out


def load_ledger():
    """{'allow': {string: reason}, 'pending': {file: [string, ...]}} — the
    reviewed state of the tree. Missing file = empty ledger, which simply
    means every finding fails; the gate cannot go vacuously green."""
    import json
    try:
        with io.open(os.path.join(REPO, "tools", "voice_ledger.json"),
                     encoding="utf-8") as fh:
            data = json.load(fh)
        return (dict(data.get("allow", {})),
                {f: set(v) for f, v in data.get("pending", {}).items()})
    except (OSError, ValueError):
        return {}, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", nargs="+", default=None,
                    help="one or more files, relative to the de/ dir")
    ap.add_argument("--fail", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    global CATALOG
    CATALOG = catalog_keys()
    files = ([os.path.join(DE, f) if not os.path.isabs(f) else f
              for f in a.file] if a.file else
             sorted(os.path.join(DE, f) for f in os.listdir(DE)
                    if f.endswith(".py")))
    allow, pending = load_ledger()
    total = 0
    fails = 0
    per_rule = {}
    seen_pending = {}
    for path in files:
        base = os.path.basename(path)
        hits = []
        seen = set()
        for lineno, text in strings_in(path):
            if text in seen:
                continue
            seen.add(text)
            if any(s in text for s in SKIP_SUBSTR):
                continue
            found = judge(text)
            if found:
                hits.append((lineno, text, found))
        if hits:
            print("\n%s" % base)
            for lineno, text, found in sorted(hits):
                tags = ",".join(n for n, _w in found)
                if text in allow:
                    status = "allow"
                elif text in pending.get(base, ()):
                    status = "pending"
                    seen_pending.setdefault(base, set()).add(text)
                else:
                    status = "NEW"
                    fails += 1
                print("  :%-5d [%s] (%s) %r" % (lineno, tags, status,
                                                text[:80]))
                if a.verbose:
                    for _n, why in found:
                        print("           %s" % why)
                for n, _w in found:
                    per_rule[n] = per_rule.get(n, 0) + 1
            total += len(hits)
    # the ratchet's other direction: a pending entry whose string is gone was
    # fixed — its entry must go too, or the ledger drifts into fiction
    if not a.file:
        for base, texts in sorted(pending.items()):
            for text in sorted(texts - seen_pending.get(base, set())):
                print("STALE pending entry (%s): %r — fixed in source, delete "
                      "it from voice_ledger.json" % (base, text[:60]))
                fails += 1

    print("\n%d flagged string(s) across %d file(s)" % (total, len(files)))
    if per_rule:
        for n in sorted(per_rule, key=lambda k: -per_rule[k]):
            print("   %-24s %d" % (n, per_rule[n]))
    print("RESULT: " + ("CLEAN" if not fails
                        else "FAILED: %d unaccounted (new or stale)" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
