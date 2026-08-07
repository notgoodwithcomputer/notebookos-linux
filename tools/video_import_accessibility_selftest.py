#!/usr/bin/env python3
"""Display-free proof that the Video import list is split by MEANING.

Every scan result used to be wrapped in the same Gtk.EventBox listening for
button-press-event, whether or not it could actually be picked. That was wrong
twice over: the pickable rows were pointer-only (not in the focus chain, not
activated by Space, nothing for a screen reader to announce), and the rows
already in the bin — which do nothing at all — looked exactly as interactive as
the rows that do.

_imp_build_rows now says which is which. A file already in the bin is an
informational plain Gtk.Box kept out of the focus chain; a file that is not is a
real Gtk.Button carrying the action it performs. This checks the split, the
artwork on both sides of it, and that the surrounding import behaviour (the
selected set, the count line, the Add button, the scrim and card holder) is
untouched by the stage.

Everything here is static — AST over the one function, plus a read of the app's
CSS blob — so it runs on a build host with no display and catches a regression
back to an EventBox at review time rather than on hardware.
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO = (ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
         / "video.py")


def check(condition, label):
    print(("ok  " if condition else "FAIL") + label)
    if not condition:
        raise AssertionError(label)


# ---------------------------------------------------------------- AST helpers

def calls(node):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def attr_calls(node, name):
    """Every `<something>.name(...)` call inside node."""
    return [c for c in calls(node)
            if isinstance(c.func, ast.Attribute) and c.func.attr == name]


def ctor_calls(node, module, name):
    """Every `module.name(...)` construction inside node."""
    return [c for c in calls(node)
            if isinstance(c.func, ast.Attribute) and c.func.attr == name
            and isinstance(c.func.value, ast.Name) and c.func.value.id == module]


def const_args(call):
    return [a.value for a in call.args if isinstance(a, ast.Constant)]


def classes(node):
    return [a for c in attr_calls(node, "add_class") for a in const_args(c)]


def strings(node):
    return [n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def text_of(node, source):
    """Source text of a node, or of a synthetic branch-body Module."""
    if isinstance(node, ast.Module):
        return "\n".join(ast.get_source_segment(source, s) or ""
                         for s in node.body)
    return ast.get_source_segment(source, node) or ""


def func(tree, name):
    found = [n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == name]
    check(len(found) == 1, "exactly one %s is defined" % name)
    return found[0]


def branches(build):
    """The `if already:` that splits the two kinds of row."""
    found = [n for n in ast.walk(build) if isinstance(n, ast.If)
             and isinstance(n.test, ast.Name) and n.test.id == "already"]
    check(len(found) == 1,
          "one `if already:` decides which kind of row is built")
    node = found[0]
    check(bool(node.orelse), "the split has both an added arm and a fresh arm")
    return (ast.Module(body=node.body, type_ignores=[]),
            ast.Module(body=node.orelse, type_ignores=[]))


# ------------------------------------------------------------------- the rows

def no_pointer_only_path(build, source):
    body = text_of(build, source)
    check("EventBox" not in body, "the import rows construct no Gtk.EventBox")
    check("button-press-event" not in body and "button_press" not in body,
          "no import row connects a raw button-press handler")
    check("add_events" not in body and "EventMask" not in body,
          "the import rows request no pointer event mask")
    names = {n.id for n in ast.walk(build) if isinstance(n, ast.Name)}
    check("Gdk" not in names,
          "_imp_build_rows touches no raw Gdk input plumbing")


def informational_rows(info, source):
    check(len(ctor_calls(info, "Gtk", "Box")) == 1,
          "an already-added row is held by a plain Gtk.Box, not a control")
    check(not ctor_calls(info, "Gtk", "Button"),
          "an already-added row builds no button")
    focus = attr_calls(info, "set_can_focus")
    check(len(focus) == 1 and const_args(focus[0]) == [False],
          "the informational holder is explicitly kept out of the focus chain")
    check(not attr_calls(info, "connect"),
          "an already-added row has nothing to activate, so connects nothing")
    check(not attr_calls(info, "set_tooltip_text"),
          "an already-added row promises no action in a tooltip")
    check("Added" in strings(info) and "impadded" in classes(info),
          "the 'Added' label and its styling survive the split")


def control_rows(control):
    check(len(ctor_calls(control, "Gtk", "Button")) == 1,
          "a fresh row is built with Gtk.Button")

    relief = attr_calls(control, "set_relief")
    check(len(relief) == 1
          and any(isinstance(a, ast.Attribute) and a.attr == "NONE"
                  for a in relief[0].args),
          "the button carries relief NONE, so the frame stays the CSS one")

    cls = classes(control)
    check("videohit" in cls,
          "the button reuses .videohit, so the theme adds no second chrome")
    check("imphit" in cls, "the button carries .imphit for its hover rule")

    check(not attr_calls(control, "set_can_focus"),
          "the button keeps GTK's own focusability instead of overriding it")
    check(not attr_calls(control, "add_accelerator"),
          "no bespoke accelerator is bolted onto the row")


def wiring(control):
    connects = attr_calls(control, "connect")
    check(len(connects) == 1, "the row has exactly one signal connection")
    check(const_args(connects[0]) == ["clicked"],
          "the row fires on \"clicked\", which the keyboard raises too")

    handler = connects[0].args[1]
    check(isinstance(handler, ast.Lambda),
          "the clicked handler is the loop's own lambda")
    # The index must be captured per-iteration as a default argument. A bare
    # closure over `i` would give EVERY row the last loop value, so whichever
    # row was clicked would toggle the last file in the scan.
    defaults = handler.args.defaults
    bound = [p.arg for p, d in
             zip(handler.args.args[-len(defaults):] if defaults else [],
                 defaults)
             if isinstance(d, ast.Name) and d.id == "i"]
    check(len(bound) == 1,
          "the row index is bound per-iteration as a default argument")
    toggles = attr_calls(handler, "_imp_toggle")
    check(len(toggles) == 1
          and [a.id for a in toggles[0].args
               if isinstance(a, ast.Name)] == bound,
          "the handler calls _imp_toggle with that per-iteration index")


def announced_actions(control, source):
    body = text_of(control, source)
    tips = attr_calls(control, "set_tooltip_text")
    check(len(tips) == 1, "the row is given exactly one tooltip")
    check('"Select %s (%s)"' in body and '"Deselect %s (%s)"' in body,
          "the action names both directions of the toggle")
    check("self._imp_selected" in body,
          "which direction is offered follows the live selected set")
    # Whole strings, not a verb glued to a name: a language that orders those
    # differently cannot repair a sentence the app assembled out of fragments.
    check(not re.search(r'_t\("Select"\)|_t\("Deselect"\)', body),
          "the action is one translatable string, not verb + name fragments")
    check("KIND_LABEL" in body,
          "the action carries the media kind alongside the name")
    check(re.search(r"%\s*\(\s*name\s*,", body) is not None,
          "the media name is what fills the action string")

    # A button holding labels is named BY those labels, so GTK reports the file
    # name and nbapp's tooltip hook — which only fills blanks — leaves it. With
    # no explicit name the ACTION, the part that changes as the row toggles, is
    # never announced.
    named = attr_calls(control, "name_control")
    check(len(named) == 1 and isinstance(named[0].func.value, ast.Name)
          and named[0].func.value.id == "nbapp",
          "the action is set as the accessible name too, not just a tooltip")
    check(any(isinstance(a, ast.Name) and a.id == "action"
              for a in named[0].args),
          "the accessible name is the same action string as the tooltip")


def artwork_preserved(build, source):
    body = text_of(build, source)
    cls = classes(build)
    for want in ("improw", "impsel", "impname", "imppath", "impadded"):
        check(want in cls, "the .%s styling survives the split" % want)
    check("i in self._imp_selected" in body,
          "the selected set still decides the .impsel state")
    check(len(ctor_calls(build, "nbicons", "image")) == 1
          and "KIND_ICON" in body,
          "the per-kind icon is still drawn on every row")

    ell = attr_calls(build, "set_ellipsize")
    check(len(ell) == 2, "both the name and the path are still ellipsized")
    modes = {a.attr for c in ell for a in c.args if isinstance(a, ast.Attribute)}
    check(modes == {"END", "MIDDLE"},
          "the name still ellipsizes at the END and the path in the MIDDLE")
    widths = sorted(a for c in attr_calls(build, "set_max_width_chars")
                    for a in const_args(c))
    check(widths == [34, 40], "both text widths are unchanged (34 / 40)")
    check("os.path.relpath" in body, "the path is still shown relative to Home")
    check(len(attr_calls(build, "show_all")) == 1,
          "the rebuilt list is still shown in one place")


def focus_survives_rebuild(build, source):
    body = text_of(build, source)
    # A toggle rebuilds the list, destroying the control the user just pressed.
    # Without this the focus would land back at the top of the window on every
    # Space, which is the keyboard equivalent of the list scrolling away.
    check("has_focus()" in body,
          "the rebuild notes which row held the keyboard focus")
    check("self._imp_rowbtns" in body,
          "the rebuild keeps a handle on its own buttons to do that")
    check(len(attr_calls(build, "grab_focus")) == 1,
          "focus is handed back to that row's replacement, in one place")


# ---------------------------------------------- untouched surrounding contract

def surrounding_import_unchanged(tree, source):
    tbody = text_of(func(tree, "_imp_toggle"), source)
    check("self._imp_selected.discard" in tbody
          and "self._imp_selected.add" in tbody,
          "toggling still moves the row in and out of the selected set")
    check("self._imp_build_rows()" in tbody, "a toggle still rebuilds the list")
    check('"%d selected"' in tbody,
          "the count line is still written on every toggle")
    check("self._imp_addbtn.set_sensitive(n > 0)" in tbody,
          "the Add button still enables only on a non-empty pick")
    check('"Add %d to Media"' in tbody and '"Add to Media"' in tbody,
          "the Add button still counts what it will add")

    confirm = text_of(func(tree, "_imp_confirm"), source)
    check("self._push_undo" in confirm and "self._save_project()" in confirm,
          "confirming an import is still one undoable, saved step")

    # Stage four is the row list only. The scrim and the card holder belong to a
    # later stage and must still be exactly what they were.
    check(source.count("scrim = Gtk.EventBox()") == 3
          and source.count("holder = Gtk.EventBox()") == 3,
          "the three overlay scrims and holders are untouched by this stage")
    check("self._imp_holder = holder" in source and "_recenter_import" in source,
          "the import card holder and its re-centring are untouched")

    # ...and the earlier Video stages must not have regressed while this landed.
    check('evt.connect("clicked", self._on_transition_click' in source,
          "stage one's transition choices are still native controls")
    check('evt.connect("clicked", lambda _w, idx=i: self._on_bin_click(idx))'
          in source,
          "stage one's media-bin rows are still native controls")


def hover_and_chrome(tree):
    blobs = [n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, bytes)
             and b".improw" in n.value]
    check(len(blobs) == 1, "the app CSS blob carries the .improw rules")
    css = blobs[0].decode("ascii")

    check(re.search(r"\.videohit\s*\{", css) is not None,
          "the neutral .videohit wrapper rule is reused, not duplicated")
    hover = re.search(r"\.imphit:hover\s+\.improw\s*\{([^}]*)\}", css)
    check(hover is not None,
          "hover feedback reaches the row THROUGH the button, as a descendant")
    check("background" in hover.group(1),
          "the hover rule actually changes the row's background")
    check(re.search(r"\.binhit:hover\s+\.binrow", css) is not None
          and re.search(r"\.transhit:hover\s+\.transcell", css) is not None,
          "the earlier stages' hover rules are still there")

    # .videohit zeroes box-shadow on the BUTTON; the selected row's marker bar
    # is a box-shadow on the ROW and must survive that.
    sel = re.search(r"\.improw\.impsel\s*\{([^}]*)\}", css)
    check(sel is not None and "inset" in sel.group(1),
          "the selected row keeps its inset marker bar")

    for block in re.findall(r"\.imphit[^{]*\{([^}]*)\}", css):
        block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
        check("outline" not in block,
              "no .imphit rule suppresses the keyboard focus ring")
    check(css.isascii(), "the CSS blob stays ASCII, as load_from_data needs")


def main():
    source = VIDEO.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(VIDEO))
    build = func(tree, "_imp_build_rows")
    info, control = branches(build)

    no_pointer_only_path(build, source)
    informational_rows(info, source)
    control_rows(control)
    wiring(control)
    announced_actions(control, source)
    artwork_preserved(build, source)
    focus_survives_rebuild(build, source)
    surrounding_import_unchanged(tree, source)
    hover_and_chrome(tree)
    print("\nvideo import accessibility selftest: all checks passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print("\nvideo import accessibility selftest FAILED: %s" % exc)
        sys.exit(1)
