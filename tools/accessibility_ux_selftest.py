#!/usr/bin/env python3
"""Display-free conformance checks for shared accessibility contracts."""

import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
THEME = (ROOT / "buildroot/board/notebookos/rootfs-overlay/usr/share/themes/"
         "Papertone/gtk-3.0/gtk.css")
sys.path.insert(0, str(DE))

import nbmotion  # noqa: E402


def check(condition, label):
    print(("ok  " if condition else "FAIL") + label)
    if not condition:
        raise AssertionError(label)


def focus_and_state_contract():
    css = THEME.read_text(encoding="utf-8")
    check(all(rule in css for rule in (
        "outline-color: @accent", "outline-style: solid",
        "outline-width: 2px", "outline-offset: -3px")),
        "Papertone defines a visible keyboard focus ring, not color alone")
    check("entry:focus" in css and "border-color: @accent" in css,
          "text entry focus has a non-layout-shifting visible edge")
    default_block = re.search(r"button\.default\s*\{([^}]*)\}", css, re.S)
    declarations = (re.sub(r"/\*.*?\*/", "", default_block.group(1), flags=re.S)
                    if default_block is not None else "")
    check(default_block is not None and "outline: none" not in declarations,
          "default/safe dialog actions do not suppress keyboard focus")
    check("menuitem:disabled" in css and "@inkoff" in css,
          "disabled controls retain a distinct unavailable state")


def accessible_name_contract():
    path = DE / "nbapp.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    functions = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef)}
    check("name_control" in functions and "_name_hook" in functions,
          "shared explicit and tooltip-derived accessible naming APIs exist")
    hook = ast.get_source_segment(source, functions["_name_hook"]) or ""
    check("Gtk.Widget.set_tooltip_text" in hook and "acc.set_name(text)" in hook,
          "icon tooltips also become assistive-technology names")
    check("not (acc.get_name() or \"\").strip()" in hook,
          "the naming hook never overwrites a widget's explicit label")
    check("_name_hook()" in source,
          "accessible naming is installed for every app importing nbapp")
    check(all(token in source for token in
              (b.decode() for b in (b".dim", b".disabled", b".pipoff", b".chip-off"))),
          "high contrast excludes class-based unavailable states")

    contacts = (DE / "contacts.py").read_text(encoding="utf-8")
    check("fav = Gtk.ToggleButton" in contacts and
          "fav.set_active(bool(a.get(\"favorite\")))" in contacts,
          "Contacts favorite exposes a checkable state")
    check("fav.get_accessible().set_name(fav_action)" in contacts,
          "Contacts favorite has a meaningful localized accessible name")

    cookbook = (DE / "cookbook.py").read_text(encoding="utf-8")
    check('less.get_accessible().set_name(_t("Cook less"))' in cookbook and
          'more.get_accessible().set_name(_t("Cook more"))' in cookbook,
          "Cookbook servings controls expose actions instead of glyph names")

    writer = (DE / "writer.py").read_text(encoding="utf-8")
    check('prev.get_accessible().set_name(_t("Previous match"))' in writer and
          'nxt.get_accessible().set_name(_t("Next match"))' in writer,
          "Writer Find navigation exposes actions instead of glyph names")

    calculator = (DE / "calculator.py").read_text(encoding="utf-8")
    check('shut.get_accessible().set_name(_t("Close"))' in calculator,
          "Calculator damaged-state dismissal exposes a semantic name")
    check("self._histbox.set_can_focus(available)" in calculator and
          "self._histbox.set_sensitive(available)" in calculator and
          "self._histbox.get_accessible().set_name(action or \"\")" in calculator,
          "Calculator history recall disappears semantically when empty")

    finder = (DE / "finder.py").read_text(encoding="utf-8")
    check('more.get_accessible().set_name(_t("Open")' in finder,
          "Finder collapsed breadcrumbs expose a navigation action")

    packages = (DE / "packages.py").read_text(encoding="utf-8")
    # The rail is toggle rows, and it is LIT through _set_nav_active — which
    # blocks each row's own handler first, because set_active emits "clicked"
    # and the bare `row.set_active(k == vid)` this check used to pin re-entered
    # _on_nav for every row (the packages sidebar ping-pong). A check must not
    # pin the defective form of the code it is meant to protect.
    check("row = Gtk.ToggleButton()" in packages and
          "def _set_nav_active(self, vid):" in packages and
          "self._set_nav_active(self.view)" in packages and
          "row.handler_block(hid)" in packages,
          "Packages navigation exposes and transfers current-page state")

    sysmon = (DE / "sysmon.py").read_text(encoding="utf-8")
    check("dlg.set_title(title)" in sysmon and
          "dlg.get_accessible().set_name(title)" in sysmon and
          "cancel.grab_focus()" in sysmon,
          "System Monitor destructive dialogs are named and focus Cancel")
    check("Gtk.Label(label=_t(title).upper()" in sysmon,
          "System Monitor resource headings use their catalog translations")

    installer = (DE / "installer.py").read_text(encoding="utf-8")
    check("step_btn = Gtk.ToggleButton()" in installer and
          "step_btn.set_active(j == i)" in installer and
          "step_btn.set_sensitive(" in installer,
          "Installer completed-step navigation is keyboard and state aware")

    settings = (DE / "settings.py").read_text(encoding="utf-8")
    # The CONTRACT is that the sidebar rows are toggles, so assistive tech can
    # read which section is current, and that exactly one of them carries the
    # state. HOW the app restates the row is not this check's business, and
    # pinning the literal `row.set_active(True/False)` made it fail the day
    # that implementation was corrected: set_active on a ToggleButton emits
    # "clicked", so a clicked-handler restating its own row recursed ~1000
    # deep on every sidebar click. nbapp.choose_segment() lights a whole
    # pick-one row with the handlers blocked, which is the same state
    # transfer without the recursion. Either spelling satisfies a11y; the
    # BEHAVIOUR (one row active, clicks do not recurse) is driven for real in
    # tools/segment_row_selftest.py.
    check("row = Gtk.ToggleButton()" in settings and
          ("choose_segment(" in settings
           or ("row.set_active(True)" in settings
               and "row.set_active(False)" in settings)),
          "Settings sidebar exposes and transfers current-section state")

    shell = (DE / "shell.py").read_text(encoding="utf-8")
    check("wrap.pack_start(row, True, True, 0)" in shell and
          "wrap.pack_end(x, False, False, 0)" in shell and
          "line.pack_end(x" not in shell,
          "Notification Open and Dismiss are sibling actions")


def reduced_motion_contract():
    old = nbmotion.reduced_motion()
    old_accel = os.environ.get("NB_ACCEL")
    try:
        os.environ["NB_ACCEL"] = "1"
        nbmotion.set_reduced_motion(True)
        check(nbmotion.policy(200) == 0,
              "Reduced Motion makes shared animations instant")
        nbmotion.set_reduced_motion(False)
        os.environ["NB_ACCEL"] = "0"
        check(nbmotion.policy(200) == 200,
              "software rendering keeps the motion language — the render "
              "path is not a motion input (PAPER-PHYSICS §0.5 Amendment 1)")
    finally:
        nbmotion.set_reduced_motion(old)
        if old_accel is None:
            os.environ.pop("NB_ACCEL", None)
        else:
            os.environ["NB_ACCEL"] = old_accel


if __name__ == "__main__":
    focus_and_state_contract()
    accessible_name_contract()
    reduced_motion_contract()
    print("accessibility UX selftest: OK")
    print("RESULT: PASS")
