#!/usr/bin/env python3
"""Static, bidirectional audit of Notebook OS app registries.

The authority is Finder's APP_MODULES literal.  This deliberately parses source
instead of importing Finder: the registration gate must work without GTK or a
DISPLAY.  Hand-maintained app collections in tools/ are catalogued below only
after inspecting what each consumer promises to cover.  Capability-specific
collections are reported as subset registries and are not compared as though
they promised every launchable app.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DE_REL = Path("buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")


@dataclass(frozen=True)
class Registry:
    name: str
    path: str
    symbol: str
    subset_reason: str = ""
    mode: str = "modules"


# These are the hand-maintained app-name collections discovered in tools/.  A
# list belongs here only when its values identify apps, rather than test cases,
# filenames, controls, or generated assets.
REGISTRIES = (
    Registry("button-contrast", "tools/button_contrast_check.py", "APPS"),
    Registry("performance-baseline", "tools/perf_baseline.py", "APPS"),
    Registry("app-icons", "tools/nbicons_selftest.py", "APP_ICONS", mode="icons"),
    Registry("writing-apps", "tools/writing_apps_selftest.py", "APPS",
             "the suite explicitly exercises the four writing apps only"),
    Registry("reopen-damage", "tools/reopen_damage_selftest.py", "APPS",
             "only apps with a user-authored recovery store belong here"),
    Registry("export-overwrite", "tools/export_overwrite_selftest.py", "APPS",
             "only apps with deterministic PDF exports are exercised"),
    Registry("config-resilience", "tools/config_resilience_selftest.py", "APPS",
             "only apps that read an on-disk config/store are exercised"),
    Registry("semantic-colours", "tools/design_tokens.py", "SEMANTIC_FILES",
             "only files whose non-token colours carry domain meaning belong here",
             mode="pyfiles"),
    Registry("empty-state", "tools/empty_state_selftest.py", "APPS",
             "the suite is a scoped first-run-honesty inventory, not all apps"),
    Registry("messaging-honesty", "tools/messaging_honesty_selftest.py", "APPS",
             "the inventory covers apps with relevant recovery/failure/progress/reset copy"),
    Registry("view-persistence", "tools/view_persistence_selftest.py", "APPS",
             "the file says it covers only the two preference fixes landed with it"),
    Registry("store-damage", "tools/store_damage_selftest.py", "GOOD",
             "only apps with structured user-data stores and bespoke damage fixtures belong here",
             mode="dictkeys"),
)


# Exact two-way allowances.  Every key has its own reason; shared blanket
# explanations are intentionally forbidden.
DEBT = {
    ("button-contrast", "finder"):
        "Finder is shell-started rather than Finder-table launchable, but this visual sweep includes its buttons.",
    ("performance-baseline", "finder"):
        "Finder is shell-started rather than Finder-table launchable, but its window startup is intentionally benchmarked.",
    ("performance-baseline", "widgets"):
        "Widgets is a session-start component rather than a launchable app, but its window startup is intentionally benchmarked.",
}


def _assignment(path: Path, symbol: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == symbol for t in node.targets):
            return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == symbol:
            return node.value
    raise ValueError(f"{path}: no literal {symbol} assignment")


def _literal(path: Path, symbol: str):
    return ast.literal_eval(_assignment(path, symbol))


def launch_table(root: Path) -> dict[str, str]:
    value = _literal(root / DE_REL / "finder.py", "APP_MODULES")
    if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError("finder.APP_MODULES is not a literal string mapping")
    return value


def _sequence_names(value) -> set[str]:
    names = set()
    for item in value:
        if isinstance(item, str):
            names.add(item.removesuffix(".py"))
        elif isinstance(item, (tuple, list)) and item and isinstance(item[0], str):
            names.add(item[0].removesuffix(".py"))
    return names


def registry_names(root: Path, registry: Registry) -> set[str]:
    node = _assignment(root / registry.path, registry.symbol)
    if registry.mode == "dictkeys" and isinstance(node, ast.Dict):
        return {k.value for k in node.keys if isinstance(k, ast.Constant)
                and isinstance(k.value, str)}
    value = ast.literal_eval(node)
    if registry.mode == "pyfiles":
        return {x.removesuffix(".py") for x in value}
    return _sequence_names(value)


def icon_names(root: Path, modules: set[str]) -> tuple[set[str], set[str]]:
    icons = registry_names(root, next(r for r in REGISTRIES if r.name == "app-icons"))
    aliases = _literal(root / DE_REL / "nbicons.py", "ALIAS")
    expected = {aliases.get(module, module) for module in modules}
    # APP_ICONS also guards file-type and potential-app glyphs.  Those are not
    # stale app registrations, so only launchable apps' resolved glyphs are the
    # comparable portion of this registry.
    return expected - icons, set()


def app_stub_names(root: Path) -> set[str]:
    directory = root / "buildroot/board/notebookos/rootfs-overlay/root/Applications"
    return {p.stem for p in directory.glob("*.app")}


def audit(root: Path, quiet: bool = False):
    table = launch_table(root)
    modules = set(table.values())
    findings_missing: list[tuple[str, str]] = []
    findings_stale: list[tuple[str, str]] = []

    for module in sorted(modules):
        path = root / DE_REL / (module + ".py")
        valid = path.is_file() and not path.is_symlink()
        if valid:
            try:
                ast.parse(path.read_text(encoding="utf-8"), str(path))
            except (OSError, SyntaxError, UnicodeError):
                valid = False
        if not valid:
            findings_missing.append(("app-modules", module))

    stubs = app_stub_names(root)
    findings_missing.extend(("app-stubs", table[name]) for name in sorted(set(table) - stubs))
    findings_stale.extend(("app-stubs", name) for name in sorted(stubs - set(table)))

    for registry in REGISTRIES:
        if registry.subset_reason:
            continue
        if registry.mode == "icons":
            missing, stale = icon_names(root, modules)
        else:
            names = registry_names(root, registry)
            missing, stale = modules - names, names - modules
        findings_missing.extend((registry.name, app) for app in sorted(missing))
        findings_stale.extend((registry.name, app) for app in sorted(stale))

    actual = {(*x, "missing") for x in findings_missing} | {
        (*x, "stale") for x in findings_stale}
    allowed = {(registry, app, "stale") for registry, app in DEBT}
    unallowed = actual - allowed
    stale_debt = allowed - actual

    if not quiet:
        print(f"launchable apps: {len(modules)}")
        print(f"registries found: {len(REGISTRIES) + 1}")
        for registry in REGISTRIES:
            if registry.subset_reason:
                print(f"SUBSET {registry.name}: {registry.subset_reason}")
        for registry, app in sorted(findings_missing):
            print(f"MISSING {registry}: {app}")
        for registry, app in sorted(findings_stale):
            if (registry, app) in DEBT:
                print(f"DEBT {registry}: {app} -- {DEBT[(registry, app)]}")
            else:
                print(f"STALE {registry}: {app}")
        for registry, app, _direction in sorted(stale_debt):
            print(f"LEDGER STALE {registry}: {app} -- remove this debt entry")
        problems = len(unallowed) + len(stale_debt)
        print("RESULT: " + ("PASS" if not problems else f"FAILED: {problems} problem(s)"))
    return findings_missing, findings_stale, 1 if unallowed or stale_debt else 0


def _replace_once(path: Path, old: str, new: str):
    text = path.read_text(encoding="utf-8")
    if text.count(old) < 1:
        raise AssertionError(f"selfcheck mutation target absent: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def selfcheck(root: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="app-registration-") as tmp:
        scratch = Path(tmp)
        needed = {DE_REL / "finder.py", DE_REL / "nbicons.py"}
        needed.update(Path(r.path) for r in REGISTRIES)
        apps = root / "buildroot/board/notebookos/rootfs-overlay/root/Applications"
        shutil.copytree(apps, scratch / apps.relative_to(root))
        for rel in needed:
            dest = scratch / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, dest)
        for module in set(launch_table(root).values()):
            rel = DE_REL / (module + ".py")
            dest = scratch / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, dest)

        perf = scratch / "tools/perf_baseline.py"
        _replace_once(perf, '"writer", ', "")
        missing, _stale, _ = audit(scratch, quiet=True)
        assert ("performance-baseline", "writer") in missing
        print("SELFCHECK missing-direction: PASS (removed writer from performance-baseline; MISSING reported)")

        _replace_once(perf, 'APPS = [', 'APPS = ["nonexistent_app", ')
        _missing, stale, _ = audit(scratch, quiet=True)
        assert ("performance-baseline", "nonexistent_app") in stale
        print("SELFCHECK stale-direction: PASS (added nonexistent_app to performance-baseline; STALE reported)")

        writer = scratch / DE_REL / "writer.py"
        writer.unlink()
        missing, _stale, _ = audit(scratch, quiet=True)
        assert ("app-modules", "writer") in missing
        print("SELFCHECK app-module existence: PASS (missing writer.py reported)")
        shutil.copy2(root / DE_REL / "writer.py", writer)
        writer.write_text("def broken(:\n", encoding="utf-8")
        missing, _stale, _ = audit(scratch, quiet=True)
        assert ("app-modules", "writer") in missing
        print("SELFCHECK app-module syntax: PASS (invalid writer.py reported)")
    print("SELFCHECK RESULT: PASS (both directions went red)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--selfcheck", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    return selfcheck(root) if args.selfcheck else audit(root)[2]


if __name__ == "__main__":
    raise SystemExit(main())
