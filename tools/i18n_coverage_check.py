#!/usr/bin/env python3
"""
i18n_coverage_check — user-visible strings that are in NO catalog.

WHY THIS EXISTS, AND WHY i18n_check CANNOT SEE IT: i18n_check compares the
seventeen catalogs to EACH OTHER. A string that is missing from all seventeen is
therefore invisible to it, and it will happily report 100% while whole screens
are English-only in every other language. This bit the project once before, when
a newly added app was absent from every catalog and coverage still read 100%;
check_chrome() was added then, but it only inspects MENU labels.

This asks the other question: for every string the code actually shows a person,
is there a catalog entry at all? A miss means that string is English in the
sixteen non-English languages, however green i18n_check looks.

  python3 tools/i18n_coverage_check.py [--file X.py ...] [--fail] [-v]

Exit 0 always unless --fail (then non-zero when anything is uncovered).
"""
import argparse
import ast
import io
import re
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

# Calls whose first argument is shown to a person. nbi18n patches these, so a
# bare literal here IS displayed and IS translated when the catalogs know it.
CALLS = {"set_text", "set_label", "set_markup", "set_tooltip_text",
         "set_placeholder_text", "set_title", "_t", "_flash", "append_text",
         "prepend_text"}
CTORS = {"Label", "Button", "CheckButton", "MenuItem", "RadioButton"}

# Not prose: paths, format scaffolding, single glyphs, pure punctuation.
# A user-visible string may LEAD with a placeholder and still be prose somebody
# reads -- "%d to do" and "%d of %d sets" are on the desktop board. Excluding
# everything starting with "%" hid 39 such strings and made this tool report
# "FULLY COVERED" while they displayed in English in all sixteen languages.
# Judge a string on the words left once the placeholders are removed.
_FMT = re.compile(r"%[-+ #0]*[\d*]*(?:\.\d+)?[hlL]?[a-zA-Z%]")


BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "i18n_coverage_baseline.txt")


def is_prose(v):
    v = v.strip()
    if len(v) < 4 or not any(c.isalpha() for c in v):
        return False
    if v.startswith(("/", "http", "#", "_")):
        return False
    if v.startswith(".") and " " not in v:      # ".json", a dotted path
        return False
    # Pango markup carries no words of its own: the sentence is whatever fills
    # the %s. Strip the tags before deciding, or a swatch template like
    # `<span foreground="#C8341E">●</span>  <span>%s</span>` is offered for
    # translation and a translator is asked to render a colour.
    bare = _FMT.sub("", re.sub(r"<[^>]+>", "", v))
    if sum(c.isalpha() for c in bare) < 3:
        return False                             # "%s", "%d%%" -- no words
    if v in ("None", "True", "False"):
        return False
    return True


def shown_strings(path):
    try:
        tree = ast.parse(io.open(path, encoding="utf-8").read())
    except (OSError, SyntaxError):
        return set()
    out = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
        args = []
        if name in CALLS and n.args:
            args = [n.args[0]]
        elif name in CTORS:
            args = [kw.value for kw in n.keywords
                    if kw.arg in ("label", "text", "title")]
        for a in args:
            if isinstance(a, ast.Call) and \
                    getattr(a.func, "id", None) == "_t" and a.args:
                a = a.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                    and is_prose(a.value):
                out.add(a.value.strip())
    out |= _indirect_strings(tree)
    out |= _via_local_strings(tree)
    return out


def _via_local_strings(tree):
    """Text a function builds in a LOCAL, then shows.

    nbpicker chooses its empty-state message before displaying it:

        if self._filter:
            msg = "Nothing here matches “%s”." % self._filter
        elif self.patterns:
            msg = "No files here that this app can open."
        else:
            msg = "This folder is empty."
        self._empty.set_text(msg)

    Nothing here is a literal at a display call, so the scanner saw none of
    them — and two of those three happened to be catalog keys already, so the
    third sat untranslated between two translated siblings with nothing to say
    so.

    Every literal assigned to the name is collected, not just the last: each
    branch really is displayed on its own path.
    """
    out = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        lits = {}
        for n in ast.walk(fn):
            if not isinstance(n, ast.Assign) or len(n.targets) != 1:
                continue
            tgt = n.targets[0]
            if not isinstance(tgt, ast.Name):
                continue
            v = n.value
            # `x = "..."` and `x = "..." % thing` are both a literal message
            if isinstance(v, ast.BinOp) and isinstance(v.op, ast.Mod):
                v = v.left
            if isinstance(v, ast.Constant) and isinstance(v.value, str) \
                    and is_prose(v.value):
                lits.setdefault(tgt.id, set()).add(v.value.strip())
        if not lits:
            continue
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call) or not n.args:
                continue
            fname = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if fname not in (set(CALLS) | set(CTORS)):
                continue
            for a in list(n.args) + [k.value for k in n.keywords
                                     if k.arg in ("label", "text", "title")]:
                if isinstance(a, ast.Name) and a.id in lits:
                    out |= lits[a.id]
    return out


def _indirect_strings(tree):
    """Text this module translates through a VARIABLE.

    A helper like usbwriter's

        def _step(self, box, n, text):
            lbl = Gtk.Label(label=_t("STEP %d   %s") % (n, _t(text)))

    is doing the right thing — but the literal it renders lives at the call
    site (`self._step(inner, 1, "The image to write")`), and a scanner looking
    for `_t("…")` sees nothing there. Those three headings sat in English under
    a translated "STEP 1" in every language, and this tool called the file
    clean. 158 call sites across 27 apps translate a variable this way.

    So: find every function that passes one of its OWN PARAMETERS to _t(), then
    collect the literals handed to that parameter at each call. Matching is by
    parameter NAME rather than position, so a keyword call is caught too.
    """
    targets = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = ([a.arg for a in fn.args.args]
                 + [a.arg for a in fn.args.kwonlyargs])
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call) or not n.args:
                continue
            # A parameter handed to the translator OR straight to a widget's
            # text is display text either way. nbgame's `_set_banner(text)`
            # does `self._banner.set_text(text)` and never touches _t(), so
            # tracing only into _t() missed every message it shows — four of
            # them, all English in seventeen languages.
            fname = (getattr(n.func, "attr", None)
                     or getattr(n.func, "id", None))
            if fname not in (set(CALLS) | {"_t", "_"}):
                continue
            if isinstance(n.args[0], ast.Name) and n.args[0].id in names:
                targets.setdefault(fn.name, set()).add(n.args[0].id)
    if not targets:
        return set()
    sig = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and fn.name in targets:
            params = [a.arg for a in fn.args.args]
            if params and params[0] in ("self", "cls"):
                params = params[1:]          # bound call drops the receiver
            sig[fn.name] = params

    out = set()
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        fname = (getattr(call.func, "attr", None)
                 or getattr(call.func, "id", None))
        if fname not in targets:
            continue
        for want in targets[fname]:
            params = sig.get(fname, [])
            if want in params:
                i = params.index(want)
                if i < len(call.args):
                    a = call.args[i]
                    if isinstance(a, ast.Constant) \
                            and isinstance(a.value, str) and is_prose(a.value):
                        out.add(a.value.strip())
            for kw in call.keywords:
                if kw.arg == want and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str) \
                        and is_prose(kw.value.value):
                    out.add(kw.value.value.strip())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", nargs="+", default=None)
    ap.add_argument("--fail", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--update-baseline", action="store_true",
                    help="rewrite the baseline to the current gaps")
    a = ap.parse_args()
    try:
        with io.open(os.path.join(DE, "lang_es.json"), encoding="utf-8") as fh:
            cat = set(json.load(fh))
    except (OSError, ValueError):
        print("could not read a catalog to compare against")
        return 2

    files = ([os.path.join(DE, f) for f in a.file] if a.file else
             sorted(os.path.join(DE, f) for f in os.listdir(DE)
                    if f.endswith(".py")))
    # Apps withheld by finder.HIDDEN_APPS have no reachable strings; skip
    # them VISIBLY (never silently), and resume the moment they unhide.
    # Parsed from source — this tool must not import GTK-bearing modules.
    try:
        import ast as _ast
        _tree = _ast.parse(io.open(os.path.join(DE, "finder.py"),
                                   encoding="utf-8").read())
        _names, _mods = set(), {}
        for _node in _ast.walk(_tree):
            if isinstance(_node, _ast.Assign) and _node.targets and \
                    isinstance(_node.targets[0], _ast.Name):
                if _node.targets[0].id == "HIDDEN_APPS":
                    _names = {k.value for k in _node.value.keys}
                elif _node.targets[0].id == "APP_MODULES":
                    _mods = {k.value: v.value for k, v in
                             zip(_node.value.keys, _node.value.values)}
        _hidden = {_mods[n] + ".py" for n in _names if n in _mods}
        for _hf in sorted(_hidden):
            _p = os.path.join(DE, _hf)
            if _p in files:
                files.remove(_p)
                print("SKIP %s (hidden app — withheld from every launch "
                      "surface; resumes on unhide)" % _hf)
    except (OSError, SyntaxError, AttributeError, TypeError):
        pass
    # Known gaps live in a baseline so --fail can catch a NEW omission without
    # being drowned by the standing debt. The file only ever shrinks: adding to
    # it to silence a failure is the one thing it must not be used for.
    known = set()
    try:
        with io.open(BASELINE, encoding="utf-8") as fh:
            known = {ln.rstrip("\n") for ln in fh
                     if ln.strip() and not ln.startswith("#")}
    except OSError:
        pass

    total = 0
    fresh = 0
    seen = set()
    lines = []
    for path in files:
        gap = sorted(s for s in shown_strings(path) if s not in cat)
        app = os.path.basename(path)
        for g in gap:
            rec = "%s\t%s" % (app, g.replace("\n", "\\n"))
            lines.append(rec)
            if rec in known:
                seen.add(rec)
            else:
                fresh += 1
                print("NEW UNTRANSLATED  %s  %r" % (app, g[:80]))
        if not gap:
            continue
        print("\n%s   %d uncovered" % (os.path.basename(path), len(gap)))
        for s in (gap if a.verbose else gap[:6]):
            print("    %r" % s[:96])
        if not a.verbose and len(gap) > 6:
            print("    ... and %d more (-v for all)" % (len(gap) - 6))
        total += len(gap)

    print("\n%d user-visible string(s) with no catalog entry, across %d file(s)"
          % (total, len(files)))
    print("Each one displays in ENGLISH in the sixteen non-English languages.")
    if a.update_baseline:
        with io.open(BASELINE, "w", encoding="utf-8") as fh:
            fh.write("# User-visible strings with no catalog entry: they render in\n"
                     "# English in all sixteen non-English languages. --fail passes\n"
                     "# on these and fails on anything NEW. Shrink this file.\n")
            fh.write("\n".join(sorted(lines)) + "\n")
        print("baseline rewritten: %d entr(y/ies)" % len(lines))
        return 0
    stale = known - seen
    if stale:
        print("%d baseline entr(y/ies) no longer present -- "
              "rerun with --update-baseline to prune" % len(stale))
    if known:
        print("standing debt: %d of these are recorded in %s"
              % (len(seen), os.path.basename(BASELINE)))
    print("RESULT: " + ("FULLY COVERED" if not total else
                        "%d UNCOVERED (%d new)" % (total, fresh)))
    return 1 if (fresh and a.fail) else 0


if __name__ == "__main__":
    sys.exit(main())
