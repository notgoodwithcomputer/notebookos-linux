#!/usr/bin/env python3
"""Gate durable preferences which take effect now but are not written down.

This is intentionally a small, conservative inventory rather than a grep for
every ``changed`` signal.  Search, document editing, transport controls and
one-shot system actions are not preferences.  Each entry below is a durable
choice exposed by an application surface; AST analysis follows one direct
helper call and reports uncertainty instead of treating it as success.

Run:
  python3 tools/setting_scope_check.py
  python3 tools/setting_scope_check.py --de /path/to/de
  python3 tools/setting_scope_check.py --selfcheck
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"

# Hand-audited preference surfaces.  The value is the concrete preference,
# both to document the scope and to keep additions reviewable.
HANDLERS: dict[str, dict[str, str]] = {
    "settings.py": {
        "_on_pref_switch": "accessibility/motion preference switch",
        "_on_hostname": "device name",
        "_on_res": "display resolution",
        "_on_scale": "display render scale",
        "_on_vol": "speaker volume",
        "_on_capvol": "microphone level",
        "_on_mute": "speaker mute",
        "_on_audio_out": "preferred sound output",
        "_on_blank": "screen blank timeout",
        "_on_kbd": "keyboard layout",
        "_on_repeat": "keyboard repeat",
        "_on_tz": "time zone",
        "_on_region_lang": "interface language",
        "_on_region_kb": "region keyboard layout",
        "_on_region_tz": "region time zone",
        "_on_defaultapp": "default application",
    },
    "widgetsettings.py": {
        "_move": "desktop tile order",
        "_on_toggle": "desktop tile visibility",
        "_fill_board": "filled desktop tiles",
        "_set_all": "desktop tile visibility set",
        "_reset_order": "default desktop tile order",
    },
    "finder.py": {
        "_set_view": "file view mode",
        "_on_sort_changed": "file sort order",
        "_on_toggle_hidden": "hidden-file visibility",
    },
    "sysmon.py": {"_apply_sort": "process-list sort order"},
    # Excluded from edits by the task, but still analysed read-only.
    "packages.py": {
        "_on_nav": "last packages section",
        "_on_sort": "package-list sort order",
    },
    "calculator.py": {
        "_set_deg": "angle mode",
        "_display_mode_dialog": "number display mode",
    },
    "accounting.py": {"_toggle_chart": "balance-chart visibility"},
    "bills.py": {"_set_sort": "bill sort order"},
    "shell.py": {
        "_toggle_view": "menu-bar clock/date view",
        "_set_label": "Finder label selection",
    },
}

# Ambiguity is debt, never a quiet pass.  Every entry has its own reason.
DEBT: dict[tuple[str, str], str] = {}

WRITE_NAMES = {
    "atomic_write_json", "atomic_write_text", "write_text", "write_bytes",
    "dump", "set_lang", "set_locale", "set_keyboard", "save_choice", "choose",
}
SAVE_WORDS = ("save", "persist", "write")
LIVE_METHODS = {
    "set_property", "set_visible", "set_active", "show", "hide",
    "set_font_scale", "set_cursor_blink_mode", "queue_draw", "set_sort_column_id",
}
LIVE_HELPERS = {
    "run", "Popen", "apply_blank", "apply_repeat", "apply_scale",
    "_apply_tz", "_apply_keyboard", "_apply_view", "_refresh", "_tick",
    "_after_change", "_apply_sort", "choose",
}


@dataclasses.dataclass(frozen=True)
class Result:
    module: str
    handler: str
    preference: str
    classification: str
    line: int
    live: tuple[str, ...]
    writes: tuple[str, ...]


def dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def functions(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def call_name(call: ast.Call) -> str:
    return dotted(call.func) or ""


def direct_helpers(fn: ast.AST, fmap: dict[str, ast.AST]) -> list[ast.AST]:
    """The handler plus one level of same-module method/function calls."""
    out = [fn]
    seen = {id(fn)}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node).rsplit(".", 1)[-1]
        helper = fmap.get(name)
        if helper is not None and id(helper) not in seen:
            out.append(helper)
            seen.add(id(helper))
        # GLib.idle_add(self._helper, ...) and similar callback schedulers name
        # the next hop as an argument rather than as the callee.
        for arg in node.args:
            arg_name = dotted(arg)
            helper = fmap.get((arg_name or "").rsplit(".", 1)[-1])
            if helper is not None and id(helper) not in seen:
                out.append(helper)
                seen.add(id(helper))
    return out


def evidence(scopes: list[ast.AST]) -> tuple[set[str], set[str]]:
    live: set[str] = set()
    writes: set[str] = set()
    for scope in scopes:
        for node in ast.walk(scope):
            if isinstance(node, ast.Call):
                full = call_name(node)
                short = full.rsplit(".", 1)[-1]
                if short in WRITE_NAMES or any(w in short.lower() for w in SAVE_WORDS):
                    writes.add(full or short)
                if short in LIVE_METHODS or short in LIVE_HELPERS:
                    live.add(full or short)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                for target in targets:
                    name = dotted(target) or ""
                    if name.startswith("self.") or name.startswith("os.environ"):
                        live.add(name)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    call = item.context_expr
                    if isinstance(call, ast.Call) and call_name(call) == "open":
                        mode = ""
                        if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
                            mode = str(call.args[1].value)
                        for kw in call.keywords:
                            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                                mode = str(kw.value.value)
                        if any(ch in mode for ch in "wax+"):
                            writes.add("open(" + mode + ")")
    return live, writes


def split_branch_evidence(fn: ast.AST) -> bool:
    """True when apply and save exist only on mutually exclusive branches."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.If) or not node.orelse:
            continue
        left_live, left_write = evidence(list(node.body))
        right_live, right_write = evidence(list(node.orelse))
        if ((left_live and not left_write and right_write and not right_live)
                or (right_live and not right_write and left_write
                    and not left_live)):
            return True
    return False


def analyse(de: Path) -> list[Result]:
    results: list[Result] = []
    for module, wanted in HANDLERS.items():
        path = de / module
        if not path.is_file():
            for name, pref in wanted.items():
                results.append(Result(module, name, pref, "UNRESOLVED", 0, (), ()))
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            for name, pref in wanted.items():
                results.append(Result(module, name, pref, "UNRESOLVED", 0, (), ()))
            continue
        fmap = functions(tree)
        for name, pref in wanted.items():
            fn = fmap.get(name)
            if fn is None:
                results.append(Result(module, name, pref, "UNRESOLVED", 0, (), ()))
                continue
            live, writes = evidence(direct_helpers(fn, fmap))
            if split_branch_evidence(fn):
                kind = "UNRESOLVED"
            elif live and writes:
                kind = "BOTH"
            elif writes:
                kind = "PERSISTED"
            elif live:
                kind = "IN-PROCESS ONLY"
            else:
                kind = "UNRESOLVED"
            results.append(Result(module, name, pref, kind, fn.lineno,
                                  tuple(sorted(live)), tuple(sorted(writes))))
    return results


def print_report(results: list[Result]) -> int:
    counts = Counter(r.classification for r in results)
    apps = len({r.module for r in results})
    print("SETTING SCOPE CHECK")
    print("HANDLERS: %d across %d apps" % (len(results), apps))
    for kind in ("IN-PROCESS ONLY", "PERSISTED", "BOTH", "UNRESOLVED"):
        print("%s: %d" % (kind, counts[kind]))
    for result in results:
        if result.classification in {"IN-PROCESS ONLY", "UNRESOLVED"}:
            detail = ", ".join(result.live) or "no mutation/write connection"
            print("%s: %s:%d %s — %s (%s)" % (
                result.classification, result.module, result.line,
                result.handler, result.preference, detail))
    print("DEBT: %d" % len(DEBT))
    for (module, handler), reason in sorted(DEBT.items()):
        print("DEBT: %s %s — %s" % (module, handler, reason))
    unresolved = {(r.module, r.handler) for r in results
                  if r.classification == "UNRESOLVED"}
    debt = set(DEBT)
    unexpected = unresolved - debt
    stale = debt - unresolved
    for module, handler in sorted(unexpected):
        print("UNLEDGERED UNRESOLVED: %s %s" % (module, handler))
    for module, handler in sorted(stale):
        print("STALE DEBT: %s %s" % (module, handler))
    ok = not counts["IN-PROCESS ONLY"] and not unexpected and not stale
    print("RESULT: PASS" if ok else "RESULT: FAILED — setting scope incomplete")
    return 0 if ok else 1


def selfcheck(de: Path) -> int:
    source = de / "settings.py"
    if not source.is_file():
        print("SELFCHECK FAIL: real settings.py is missing")
        return 2
    with tempfile.TemporaryDirectory(prefix="setting-scope-") as td:
        copy = Path(td) / "de"
        copy.mkdir()
        for module in HANDLERS:
            path = de / module
            if path.is_file():
                shutil.copy2(path, copy / module)
        target = copy / "settings.py"
        text = target.read_text(encoding="utf-8")
        tree = ast.parse(text)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_on_scale"), None)
        if fn is None:
            print("SELFCHECK FAIL: could not find Settings._on_scale")
            return 2
        lines = text.splitlines(keepends=True)
        removed = False
        for lineno in range(fn.lineno, fn.end_lineno + 1):
            if "self._save_settings()" in lines[lineno - 1]:
                lines[lineno - 1] = lines[lineno - 1].replace(
                    "self._save_settings()",
                    "json.dumps(self._settings)  # serialization is not persistence")
                removed = True
                break
        if not removed:
            print("SELFCHECK FAIL: persisted Settings._on_scale could not be mutated")
            return 2
        target.write_text("".join(lines), encoding="utf-8")
        hit = next((r for r in analyse(copy)
                    if r.module == "settings.py" and r.handler == "_on_scale"), None)
        if hit is None or hit.classification != "IN-PROCESS ONLY":
            got = hit.classification if hit else "missing"
            print("SELFCHECK FAIL: mutated settings.py:_on_scale was %s, not reported" % got)
            return 2
        print("SELFCHECK PASS: discarded json.dumps is REPORTED as IN-PROCESS ONLY")
        split = ast.parse(
            "def handler(self, enabled):\n"
            "    if enabled:\n"
            "        self.widget.set_visible(True)\n"
            "    else:\n"
            "        self._save_settings()\n").body[0]
        if not split_branch_evidence(split):
            print("SELFCHECK FAIL: mutually exclusive apply/save looked complete")
            return 2
        print("SELFCHECK PASS: mutually exclusive apply/save is unresolved")
        return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--de", type=Path, default=DEFAULT_DE)
    parser.add_argument("--selfcheck", action="store_true")
    args = parser.parse_args(argv)
    if args.selfcheck:
        return selfcheck(args.de)
    return print_report(analyse(args.de))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
