#!/usr/bin/env python3
"""OS-wide menu conformance release gate (Interaction Constitution Article I).

MENU-CONVENTIONS rules checked statically:
  §1 Ellipsis: registry-backed labels exactly match the registry's ellipsis;
     literal labels using dialog/picker/confirm callbacks carry an ellipsis.
  §2 File models: document-style New/Open/Save groups and single-store create/
     delete groups are checked for canonical registry ordering when present;
     Save/Save As is checked as a pair.  Which data model an app conceptually
     owns is not inferred: that is product meaning, not a source-text fact.
  §3 Accelerators: exactly four spaces, unique within an app, and two-way
     agreement with nbcommands.py for every statically resolved command.
     Runtime key-event bindings cannot be proved without executing the app.
  §4 Titles/order: literal ``menus`` tuples obey File, Edit, View, then custom,
     then Help; registry commands obey registry group/order; Undo/Redo precede
     clipboard commands when present.  Dynamically replaced menus are ignored.
  §5 Disabled-not-absent: ``(label, None)`` remains an item.  Conditional
     runtime omission cannot be decided statically because branch state is
     unknown, so visibility over time is not checked.
  §6 Wording: statically resolved action labels are Title Case and registry
     wording is exact. Outcome-versus-mechanism is semantic and not checkable.
  Separators: resolved lists have no leading, trailing, or adjacent separators,
     and registry-backed commands change groups only across a separator.
  Context menus: labels appended to a hand-built Gtk.Menu are a subset of that
     module's statically resolved menu-bar labels (translation wrappers unwrap).

The debt ledger is an exact, two-direction ratchet.  A finding absent from the
ledger is NEW; a ledger row absent from findings is STALE.  Either fails.
App modules are parsed with ast only; this program never imports app code.
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
NB_COMMANDS = os.path.join(DE, "nbcommands.py")
GAP = "    "
STD = ("File", "Edit", "View")

# Exact rows: (file, line, rule, detail).  Existing deviations, not exceptions.
# Debt is keyed on (file, rule, detail) → COUNT, deliberately WITHOUT a line
# number. An earlier version keyed on the line, which any peer edit ABOVE a
# debt line silently broke: the line shifted, the old entry went stale, and
# the SAME violation reappeared as "new" one line lower — a stale+new pair
# for a defect that never moved in substance (four apps hit this in one run
# as the app lanes edited above their Print items). The violation's identity
# is "this file shows this menu item with the wrong accelerator", not "at
# line N"; the count carries the two-of-a-kind cases (an app's two Esc
# Close items) that a plain set would have collapsed. Both-direction ratchet:
# more findings than the count is a regression, fewer is a stale entry to
# prune.
DEBT = {
    ("accounting.py", "registry-accelerator", "Print: shown '', registry 'Ctrl+P'"): 1,
    ("bills.py", "registry-accelerator", "Print: shown '', registry 'Ctrl+P'"): 1,
    ("cookbook.py", "registry-accelerator", "Print: shown '', registry 'Ctrl+P'"): 1,
    ("ebook.py", "registry-accelerator", "Open: shown '', registry 'Ctrl+O'"): 1,
    ("journal.py", "registry-accelerator", "Print: shown '', registry 'Ctrl+P'"): 1,
    ("maps.py", "registry-accelerator", "Zoom In: shown '', registry 'Ctrl+Plus'"): 1,
    ("maps.py", "registry-accelerator", "Zoom Out: shown '', registry 'Ctrl+Minus'"): 1,
    ("packages.py", "registry-accelerator", "Find: shown '', registry 'Ctrl+F'"): 1,
    # ("packages.py", "registry-ellipsis", "Find: …") was here. FIXED, not
    # pruned to be rid of it: menu_promise_check drove the item and found it
    # raises nothing at all -- _focus_search shows the Installed view and puts
    # the caret in the search box already on screen -- so the ellipsis was
    # promising a card twice over, once against rule 1 and once against the
    # registry's own wording. The label is now "Find", which every catalog
    # already carries.
    # Animation's New… genuinely asks (the canvas-preset + fps card; size and
    # speed are fixed at creation), so the ellipsis is the honest label per
    # MENU-CONVENTIONS rule 1 — a deliberate deviation from the registry's
    # immediate document-app New.
    ("animation.py", "registry-ellipsis", "New: shown ellipsis True, registry False"): 1,
    ("screenplay.py", "registry-accelerator", "Print: shown '', registry 'Ctrl+P'"): 1,
    ("sequencer.py", "registry-accelerator", "Zoom In: shown '+', registry 'Ctrl+Plus'"): 1,
    ("sequencer.py", "registry-accelerator", "Zoom Out: shown '−', registry 'Ctrl+Minus'"): 1,
    ("music.py", "context-subset", "'Add to playlist'"): 1,
}

checks = 0


def literal(node):
    """Resolve source literals and _t('x'); return None when runtime-derived."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        vals = [literal(x) for x in node.elts]
        return vals if all(x is not None for x in vals) else None
    if isinstance(node, ast.Call) and node.args:
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name == "_t":
            return literal(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = literal(node.left), literal(node.right)
        if isinstance(a, str) and isinstance(b, str):
            return a + b
    return None


def registry():
    tree = ast.parse(open(NB_COMMANDS, encoding="utf-8").read(), NB_COMMANDS)
    groups = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and
                any(isinstance(t, ast.Name) and t.id == "_LIST" for t in node.targets)
                and isinstance(node.value, ast.List)):
            continue
        env = {}
        # Numeric group constants are module-level assignments.
        for top in tree.body:
            if isinstance(top, ast.Assign) and len(top.targets) == 1 and isinstance(top.targets[0], ast.Name):
                v = literal(top.value)
                if isinstance(v, (str, int)): env[top.targets[0].id] = v
        for call in node.value.elts:
            if not isinstance(call, ast.Call) or len(call.args) < 5: continue
            def val(x): return env.get(x.id) if isinstance(x, ast.Name) else literal(x)
            cid, title, menu, group, order = [val(x) for x in call.args[:5]]
            kw = {x.arg: val(x.value) for x in call.keywords}
            shortcut, ell = kw.get("shortcut", ""), bool(kw.get("ellipsis", False))
            label = title + ("…" if ell else "") + (GAP + shortcut if shortcut else "")
            groups[cid] = (label, shortcut, menu, group, order)
    return groups


REG = registry()


def violation(path, line, rule, detail):
    return (os.path.basename(path), int(line), rule, detail)


def command_call(node):
    """Return command id for nbcommands item/source_label/dynamic_item calls."""
    if not isinstance(node, ast.Call) or not node.args: return None
    name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
    if name not in ("item", "source_label", "dynamic_item"): return None
    cid = literal(node.args[0])
    return cid if cid in REG else None


def labels_in_menu_method(fn):
    """Resolved (line,label,cid,separator) entries from returns/list literals."""
    out, seen = [], set()
    local_lists = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.List):
            for target in n.targets:
                if isinstance(target, ast.Name):
                    local_lists.add(target.id)
    # Backward slice the local containers whose contents can reach Return.
    # Scratch/preview lists are implementation detail, not visible menu rows.
    returned_lists = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Return) and n.value is not None:
            returned_lists.update(x.id for x in ast.walk(n.value)
                                  if isinstance(x, ast.Name)
                                  and x.id in local_lists)
    changed = True
    while changed:
        changed = False
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Name):
                targets = [t.id for t in n.targets if isinstance(t, ast.Name)]
                if any(t in returned_lists for t in targets) \
                        and n.value.id in local_lists \
                        and n.value.id not in returned_lists:
                    returned_lists.add(n.value.id); changed = True
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "extend" \
                    and isinstance(n.func.value, ast.Name) \
                    and n.func.value.id in returned_lists and n.args \
                    and isinstance(n.args[0], ast.Name) \
                    and n.args[0].id in local_lists \
                    and n.args[0].id not in returned_lists:
                returned_lists.add(n.args[0].id); changed = True
    for n in ast.walk(fn):
        candidates = []
        if isinstance(n, ast.Return): candidates = [n.value]
        for root in candidates:
            if not isinstance(root, (ast.List, ast.Tuple)): continue
            for item in root.elts:
                cid = command_call(item)
                if cid:
                    row = (item.lineno, REG[cid][0], cid, False)
                elif isinstance(item, ast.Call) and ((getattr(item.func, "attr", None) or getattr(item.func, "id", None)) == "items"):
                    for spec in item.args:
                        if isinstance(spec, ast.Tuple) and spec.elts:
                            scid = literal(spec.elts[0])
                            if scid in REG:
                                row = (spec.lineno, REG[scid][0], scid, False)
                                if row not in seen: seen.add(row); out.append(row)
                    continue
                elif isinstance(item, (ast.List, ast.Tuple)) and item.elts:
                    label = literal(item.elts[0])
                    if label == "-": row = (item.lineno, "-", None, True)
                    elif isinstance(label, str): row = (item.lineno, label, None, False)
                    else: continue
                else: continue
                if row not in seen: seen.add(row); out.append(row)
        # A very common readable construction is ``rows=[]`` followed by
        # conditional ``rows.append((label, action))``. Treat those literal or
        # registry-backed rows exactly like rows written in the return list;
        # source-substring/list-literal-only scanning otherwise silently drops
        # visible actions from every wording check.
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id in returned_lists and n.args):
            args = []
            if n.func.attr in ("append", "prepend"):
                args = [n.args[0]]
            elif n.func.attr == "extend" and isinstance(n.args[0], (ast.List, ast.Tuple)):
                args = n.args[0].elts
            for item in args:
                cid = command_call(item)
                if cid:
                    row = (item.lineno, REG[cid][0], cid, False)
                elif isinstance(item, (ast.List, ast.Tuple)) and item.elts:
                    label = literal(item.elts[0])
                    if label == "-":
                        row = (item.lineno, "-", None, True)
                    elif isinstance(label, str):
                        row = (item.lineno, label, None, False)
                    else:
                        continue
                else:
                    continue
                if row not in seen:
                    seen.add(row)
                    out.append(row)
    return sorted(out)


def context_labels(tree):
    """Labels of MenuItems appended to variables constructed as Gtk.Menu()."""
    menus, item_labels, out = set(), {}, []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            attr = getattr(n.value.func, "attr", None)
            for t in n.targets:
                if isinstance(t, ast.Name) and attr == "Menu": menus.add(t.id)
                if isinstance(t, ast.Name) and attr in ("MenuItem", "CheckMenuItem", "RadioMenuItem"):
                    args = list(n.value.args) + [k.value for k in n.value.keywords if k.arg == "label"]
                    if args:
                        lab = literal(args[0])
                        if isinstance(lab, str): item_labels[t.id] = (n.lineno, lab)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) in ("append", "prepend"):
            owner = getattr(getattr(n.func, "value", None), "id", None)
            if owner in menus and n.args:
                arg = n.args[0]
                if isinstance(arg, ast.Name) and arg.id in item_labels: out.append(item_labels[arg.id])
                elif isinstance(arg, ast.Call):
                    args = list(arg.args) + [k.value for k in arg.keywords if k.arg == "label"]
                    if args and isinstance(literal(args[0]), str): out.append((n.lineno, literal(args[0])))
    return out


def app_findings(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, path)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and
               any((isinstance(b, ast.Name) and b.id == "AppWindow") or
                   (isinstance(b, ast.Attribute) and b.attr == "AppWindow") for b in n.bases)]
    if not classes: return [], 0
    found, local_checks = [], 1
    menus, entries = None, []
    for cls in classes:
        for n in cls.body:
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "menus" for t in n.targets):
                v = literal(n.value)
                if isinstance(v, list): menus = (n.lineno, v)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "menu_items":
                entries.extend(labels_in_menu_method(n))
    if menus:
        line, names = menus; local_checks += 1
        pos = [names.index(x) for x in STD if x in names]
        ok = pos == sorted(pos)
        if "Help" in names: ok = ok and names[-1] == "Help"
        if not ok: found.append(violation(path, line, "menu-order", repr(tuple(names))))
    bar = {x[1].split(GAP)[0].rstrip("…") for x in entries if not x[3]}
    shortcuts = {}
    for line, label, cid, sep in entries:
        local_checks += 1
        if sep: continue
        if "\t" in label or re.search(r"(?<! ) {1,3}(Ctrl\+|Alt\+|Shift\+|Esc$)", label):
            found.append(violation(path, line, "accelerator-spacing", repr(label)))
        parts = label.rsplit(GAP, 1); accel = parts[1] if len(parts) == 2 else ""
        if accel:
            if accel in shortcuts:
                found.append(violation(path, line, "duplicate-accelerator", "%s also at line %d" % (accel, shortcuts[accel])))
            else: shortcuts[accel] = line
        candidates = []
        known_case_bad = False
        if not cid:
            shown_name = parts[0]
            candidates = [(key, row) for key, row in REG.items()
                          if row[0].split(GAP)[0].rstrip("…").casefold()
                          == shown_name.rstrip("…").casefold()]
            known_case_bad = bool(candidates) and all(
                row[0].split(GAP)[0].rstrip("…") != shown_name.rstrip("…")
                for _key, row in candidates)
            expected_shortcuts = {row[1] for _key, row in candidates}
            expected_ellipsis = {row[0].split(GAP)[0].endswith("…") for _key, row in candidates}
            if candidates and len(expected_shortcuts) == 1:
                want = next(iter(expected_shortcuts))
                if accel != want:
                    found.append(violation(path, line, "registry-accelerator",
                                           "%s: shown %r, registry %r" %
                                           (shown_name.rstrip("…"), accel, want)))
            if candidates and len(expected_ellipsis) == 1:
                want_ell = next(iter(expected_ellipsis))
                if shown_name.endswith("…") != want_ell:
                    found.append(violation(path, line, "registry-ellipsis",
                                           "%s: shown ellipsis %s, registry %s" %
                                           (shown_name.rstrip("…"), shown_name.endswith("…"), want_ell)))
        if cid:
            expected, want, _m, _g, _o = REG[cid]
            if label != expected:
                found.append(violation(path, line, "registry-label", "%s: %r != %r" % (cid, label, expected)))
            if accel != want:
                found.append(violation(path, line, "registry-accelerator", "%s: shown %r, registry %r" % (cid, accel, want)))
        title = parts[0].rstrip("…")
        words = re.findall(r"[A-Za-z]+", title)
        minor = {"a", "an", "and", "as", "at", "by", "for", "from", "in",
                 "of", "on", "or", "the", "to"}
        # Ignore scalar choice values that happen to live in helper lists in
        # menu_items; a menu action is conventionally multi-word/capitalised.
        if known_case_bad or (words and words[0][0].isupper() and any(
                w[0].islower() for w in words[1:] if w not in minor)):
            found.append(violation(path, line, "title-case", repr(label)))
    # Separator shape for each literal list, conservatively across source order.
    for a, b in zip(entries, entries[1:]):
        local_checks += 1
        if a[3] and b[3]: found.append(violation(path, b[0], "separator", "adjacent separators"))
    for line, label in context_labels(tree):
        local_checks += 1
        base = label.split(GAP)[0].rstrip("…")
        if base not in bar:
            found.append(violation(path, line, "context-subset", repr(label)))
    return found, local_checks


def main():
    global checks
    real = set()
    files = sorted(os.path.join(DE, f) for f in os.listdir(DE) if f.endswith(".py"))
    # Apps withheld by finder.HIDDEN_APPS have no reachable menus; skip them
    # VISIBLY and resume the moment they unhide (same doctrine as
    # i18n_check/i18n_coverage_check). Parsed from source, never imported.
    hidden_files = set()
    try:
        _tree = ast.parse(open(os.path.join(DE, "finder.py"),
                               encoding="utf-8").read())
        _names, _mods = set(), {}
        for _node in ast.walk(_tree):
            if isinstance(_node, ast.Assign) and _node.targets and \
                    isinstance(_node.targets[0], ast.Name):
                if _node.targets[0].id == "HIDDEN_APPS":
                    _names = {k.value for k in _node.value.keys}
                elif _node.targets[0].id == "APP_MODULES":
                    _mods = {k.value: v.value for k, v in
                             zip(_node.value.keys, _node.value.values)}
        for _hf in sorted(_mods[n] + ".py" for n in _names if n in _mods):
            _p = os.path.join(DE, _hf)
            if _p in files:
                files.remove(_p)
                hidden_files.add(_hf)
                print("SKIP %s (hidden app — menus unreachable; resumes on "
                      "unhide)" % _hf)
    except (OSError, SyntaxError, AttributeError, TypeError):
        pass
    parse_fail = []
    for path in files:
        try:
            rows, n = app_findings(path); checks += n; real.update(rows)
        except SyntaxError as exc:
            checks += 1; parse_fail.append(violation(path, exc.lineno or 0, "parse", exc.msg))
    real.update(parse_fail)
    # Count findings by their line-agnostic identity, keeping one line per
    # identity for a readable report.
    counts, a_line = {}, {}
    for (fn, line, rule, detail) in real:
        key = (fn, rule, detail)
        counts[key] = counts.get(key, 0) + 1
        a_line.setdefault(key, line)
    new = []      # (file, line, rule, detail, over) — findings past the debt
    for key, c in sorted(counts.items()):
        allowed = DEBT.get(key, 0)
        if c > allowed:
            fn, rule, detail = key
            new.append((fn, a_line[key], rule, detail, c - allowed))
    # A hidden app's debt is SUSPENDED, not stale. Its file was skipped above,
    # so of course it produced no findings — calling that stale would demand
    # the ledger be pruned on every hide and written back on every unhide, and
    # a pruned row is a known violation forgotten at exactly the moment nobody
    # is watching the app. Held instead, and reported so it stays legible.
    suspended = sorted(key for key in DEBT if key[0] in hidden_files)
    stale = sorted(key for key, allowed in DEBT.items()
                   if key[0] not in hidden_files and counts.get(key, 0) < allowed)
    for (fn, line, rule, detail, over) in new:
        print("FAIL NEW   %s:%d [%s] %s%s" % (fn, line, rule, detail,
              "" if over == 1 else "  (x%d over debt)" % over))
    for (fn, rule, detail) in stale:
        print("FAIL STALE %s [%s] %s — fewer than the %d in debt; prune it"
              % (fn, rule, detail, DEBT[(fn, rule, detail)]))
    for (fn, rule, detail) in suspended:
        print("HELD  %s [%s] %s — debt kept while the app is hidden"
              % (fn, rule, detail))
    print("%d checks" % checks)
    print("RESULT: %s" % ("PASS" if not new and not stale else
                          "FAILED: %d new, %d stale" % (len(new), len(stale))))
    return 0 if not new and not stale else 1


if __name__ == "__main__":
    sys.exit(main())
