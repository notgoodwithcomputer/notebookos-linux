#!/usr/bin/env python3
"""Static contract for keyboard-operable GBA cartridge cards."""
import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                      "opt/notebook/de/gbaemu.py")
fails = []


def check(condition, message):
    print(("ok   " if condition else "FAIL ") + message)
    if not condition: fails.append(message)


with open(SOURCE, encoding="utf-8") as fh:
    source = fh.read()
tree = ast.parse(source, SOURCE)
method = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_rom_card")
calls = [n for n in ast.walk(method) if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Attribute)]
gtk_builds = [n.func.attr for n in calls
              if isinstance(n.func.value, ast.Name) and n.func.value.id == "Gtk"]
check("Button" in gtk_builds, "each cartridge is built as a real button")
check("EventBox" not in gtk_builds, "cartridge cards use no pointer-only EventBox")
signals = [ast.literal_eval(n.args[0]) for n in calls if n.func.attr == "connect"
           and n.args and isinstance(n.args[0], ast.Constant)]
# Every signal the card connects must be one a KEY can raise. `clicked` and
# `toggled` both fire from Space and Enter; `button-press-event` is the
# pointer only, and using it was the original defect this file was written
# for.
#
# The count used to be pinned at exactly one, as a stand-in for "the card is a
# single activatable thing". Save-state slots made that false in a legitimate
# way -- a launch button and three slot toggles are four keyboard-operable
# controls, which is correct -- so the count is no longer the question.
check("button-press-event" not in signals and set(signals) <= {"clicked",
                                                               "toggled"},
      "every cartridge control answers a key, not just a pointer")

# ...and the thing the count was really protecting, checked directly. A
# GtkButton containing GtkToggleButtons is a broken control: activating the
# inner one can fire the outer, so choosing a save slot could LAUNCH THE GAME.
# That shipped, and it presented as "the emulator breaks sometimes" because it
# only misfires when the pointer or the focus lands on the inner control.
_btn_types = {"Button", "ToggleButton", "CheckButton", "RadioButton",
              "LinkButton", "MenuButton"}
_buttons = set()
for n in ast.walk(method):
    if isinstance(n, ast.Assign) and len(n.targets) == 1 \
            and isinstance(n.targets[0], ast.Name) \
            and isinstance(n.value, ast.Call) \
            and isinstance(n.value.func, ast.Attribute) \
            and getattr(n.value.func.value, "id", None) == "Gtk" \
            and n.value.func.attr in _btn_types:
        _buttons.add(n.targets[0].id)
# Containment is TRANSITIVE, and this check was wrong until it was. The real
# structure is button -> card -> slots -> ToggleButton: three links, and a
# one-level test walked straight past it and reported green while the defect
# was present. Re-nesting the slots and watching the check NOT go red is what
# exposed that -- a gate has to be shown failing on the bug it exists for.
_contains = {}
for n in ast.walk(method):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
            and n.func.attr in ("pack_start", "pack_end", "add", "attach") \
            and n.args and isinstance(n.args[0], ast.Name) \
            and isinstance(n.func.value, ast.Name):
        _contains.setdefault(n.func.value.id, set()).add(n.args[0].id)


def _holds_a_button(name, seen=None):
    seen = seen or set()
    if name in seen:
        return False
    seen.add(name)
    for child in _contains.get(name, ()):
        if child in _buttons or _holds_a_button(child, seen):
            return True
    return False


_holds_button = {v for v in _contains if _holds_a_button(v)}
_nested = sorted(
    "%s inside %s" % (n.args[0].id, n.func.value.id)
    for n in ast.walk(method)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    and n.func.attr == "add" and isinstance(n.func.value, ast.Name)
    and n.func.value.id in _buttons and n.args
    and isinstance(n.args[0], ast.Name) and n.args[0].id in _holds_button)
check(not _nested,
      "no activatable control is nested inside another: " + (", ".join(_nested)
                                                             or "none"))
check(any(n.func.attr == "set_tooltip_text" for n in calls),
      "the launch action retains its accessible tooltip name")
check(any(n.func.attr == "set_relief" for n in calls),
      "the button keeps neutral Papertone chrome")
method_source = ast.get_source_segment(source, method) or ""
check('p=m["path"]' in method_source,
      "each clicked handler binds its own ROM path")

css = source[source.find('css = b"""'):]
check(".rombutton" in css and "padding: 0" in css
      and "background: transparent" in css and "border: none" in css,
      "rombutton CSS is layout-neutral and transparent")
check(".rombutton:hover .romart" in css,
      "pointer hover still highlights the cartridge artwork")
check(".rombutton:focus" not in css and "outline: none" not in css,
      "app CSS does not suppress the global keyboard focus indicator")

print("\n%d failed" % len(fails))
print("RESULT: %s" % ("FAILED" if fails else "PASS"))
sys.exit(1 if fails else 0)
