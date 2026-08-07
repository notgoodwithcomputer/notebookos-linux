#!/usr/bin/env python3
"""Display-free proof that the Video timeline zoom controls are real buttons.

The two zoom controls used to be icons inside a Gtk.EventBox listening for
button-press-event. That is a pointer-only control: it is not in the focus
chain, so a keyboard user could not reach the timeline zoom at all, and no
assistive technology saw a control there. They are now Gtk.Buttons.

Everything here is static — AST over the loop that builds the pair, plus a
read of the app's CSS block — so it runs on a build host with no display and
catches a regression back to an EventBox at review time rather than on
hardware.
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIDEO = (ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
         / "video.py")

ICONS = ("zoomout", "zoomin")


def check(condition, label):
    print(("ok  " if condition else "FAIL") + label)
    if not condition:
        raise AssertionError(label)


def zoom_loop(tree):
    """The single `for ic in ("zoomout", "zoomin"):` loop that builds the pair."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        it = node.iter
        if not isinstance(it, ast.Tuple):
            continue
        names = [e.value for e in it.elts
                 if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if tuple(names) == ICONS:
            found.append(node)
    check(len(found) == 1,
          "exactly one loop builds the zoom-out/zoom-in pair")
    return found[0]


def calls(node):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def attr_calls(node, name):
    """Every `<something>.name(...)` call inside node."""
    out = []
    for call in calls(node):
        f = call.func
        if isinstance(f, ast.Attribute) and f.attr == name:
            out.append(call)
    return out


def const_args(call):
    return [a.value for a in call.args if isinstance(a, ast.Constant)]


def no_pointer_only_path(loop, source):
    body = ast.get_source_segment(source, loop) or ""
    check("EventBox" not in body,
          "the zoom loop constructs no Gtk.EventBox")
    check("button-press-event" not in body and "button_press" not in body,
          "the zoom loop connects no raw button-press handler")
    check("add_events" not in body and "EventMask" not in body,
          "the zoom loop requests no pointer event mask")
    # Both controls must come from this one loop; a stray hand-built zoom
    # widget elsewhere would slip past every check above.
    others = re.findall(r"\"(?:Zoom in|Zoom out)\"", source)
    check(len(others) == 2,
          "the two zoom tooltips exist only once each, inside the loop")


def real_buttons(loop):
    made = [c for c in calls(loop)
            if isinstance(c.func, ast.Attribute) and c.func.attr == "Button"
            and isinstance(c.func.value, ast.Name) and c.func.value.id == "Gtk"]
    check(len(made) == 1,
          "the loop body builds its control with Gtk.Button")

    relief = attr_calls(loop, "set_relief")
    check(len(relief) == 1
          and any(isinstance(a, ast.Attribute) and a.attr == "NONE"
                  for a in relief[0].args),
          "the button carries relief NONE, so the frame is the CSS one")

    classes = [const_args(c) for c in attr_calls(loop, "add_class")]
    check(["squarebtn"] in classes,
          "the button carries the squarebtn class")

    sizes = [const_args(c) for c in attr_calls(loop, "set_size_request")]
    check(sizes == [[28, 28]],
          "the control keeps its exact 28x28 request")

    icon = [c for c in calls(loop)
            if isinstance(c.func, ast.Attribute) and c.func.attr == "image"
            and isinstance(c.func.value, ast.Name)
            and c.func.value.id == "nbicons"]
    check(len(icon) == 1, "the same nbicons glyph is still the button's child")
    aligns = {a.attr for c in attr_calls(loop, "set_halign")
              + attr_calls(loop, "set_valign") for a in c.args
              if isinstance(a, ast.Attribute)}
    check(aligns == {"CENTER"}, "the glyph stays centred in the control")

    # No custom key handling: keyboard activation is GTK's own.
    check(not attr_calls(loop, "add_accelerator"),
          "no bespoke accelerator is bolted onto the control")
    body_names = {n.id for n in ast.walk(loop) if isinstance(n, ast.Name)}
    check("Gdk" not in body_names,
          "the loop touches no raw Gdk input plumbing")


def wiring(loop, source):
    connects = attr_calls(loop, "connect")
    check(len(connects) == 1, "the control has exactly one signal connection")
    signals = const_args(connects[0])
    check(signals == ["clicked"],
          "zoom fires on \"clicked\", which the keyboard raises too")

    handler = connects[0].args[1]
    check(isinstance(handler, ast.Lambda),
          "the clicked handler is the loop's own lambda")
    # The delta must be captured per-iteration as a default argument. A bare
    # closure over `delta` would give BOTH buttons the last loop value, so
    # zoom out would zoom in. Find the parameter defaulted to `delta`, then
    # require that same parameter to be what reaches _zoom_by.
    bound = [p.arg for p, d in
             zip(handler.args.args[-len(handler.args.defaults):]
                 if handler.args.defaults else [], handler.args.defaults)
             if isinstance(d, ast.Name) and d.id == "delta"]
    check(len(bound) == 1,
          "the delta is bound per-iteration as a default argument")

    zooms = attr_calls(handler, "_zoom_by")
    check(len(zooms) == 1 and [a.id for a in zooms[0].args
                               if isinstance(a, ast.Name)] == bound,
          "the handler calls _zoom_by with that per-iteration delta")

    # The +/-0.2 pairing, and which icon gets which sign.
    deltas = [n for n in ast.walk(loop) if isinstance(n, ast.IfExp)
              and "delta" not in ast.dump(n)]
    assign = [n for n in ast.walk(loop) if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "delta"
                      for t in n.targets)]
    check(len(assign) == 1, "delta is decided once per iteration")
    expr = ast.get_source_segment(source, assign[0].value) or ""
    check(re.fullmatch(r'0\.2 if ic == "zoomin" else -0\.2', expr.strip()),
          "zoom in steps +0.2 and zoom out steps -0.2, unchanged")
    del deltas

    tips = attr_calls(loop, "set_tooltip_text")
    check(len(tips) == 1, "each control is given exactly one tooltip")
    tip = ast.get_source_segment(source, tips[0].args[0]) or ""
    check("Zoom in" in tip and "Zoom out" in tip,
          "the tooltip strings are unchanged, and name the function")
    # nbapp's naming hook turns a tooltip into the accessible name, so the
    # tooltip is also what a screen reader announces for these glyphs.
    check("Zoom" in tip and "px" not in tip and "%" not in tip,
          "the tooltip describes the function, not the mechanism")


def size_neutral_chrome():
    source = VIDEO.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(VIDEO))
    blobs = [n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, bytes)
             and b".squarebtn" in n.value]
    check(len(blobs) == 1, "the app CSS block carries the squarebtn rule")
    css = blobs[0].decode("ascii")

    base = re.search(r"\.squarebtn\s*\{([^}]*)\}", css)
    check(base is not None, "squarebtn has a base rule")
    decls = re.sub(r"/\*.*?\*/", "", base.group(1), flags=re.S)

    def has(prop, value):
        return re.search(re.escape(prop) + r"\s*:\s*" + value + r"\s*;", decls)

    # A Gtk.Button, unlike the old Box, arrives with theme padding, a minimum
    # size and a pressed shadow. Zeroing those is what keeps 28x28 honest.
    check(bool(has("padding", r"0")), "button padding is zeroed")
    check(bool(has("min-width", r"0")) and bool(has("min-height", r"0")),
          "the theme's minimum button size is released")
    check(bool(has("box-shadow", r"none")), "no button shadow inflates the box")

    # ...while the appearance the design specified is untouched.
    check(bool(has("border", r"1px solid #C9C4B6")),
          "the Papertone hairline border is retained")
    check(bool(has("background", r"#F4F2EC")),
          "the Papertone fill is retained")
    check(bool(has("border-radius", r"8px")), "the 8px radius is retained")

    # Stripping the focus ring would undo the very thing this change is for.
    for block in re.findall(r"\.squarebtn[^{]*\{([^}]*)\}", css):
        block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
        check("outline" not in block,
              "no squarebtn rule suppresses the keyboard focus ring")

    check(css.isascii(), "the CSS blob stays ASCII, as load_from_data needs")


def main():
    source = VIDEO.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(VIDEO))
    loop = zoom_loop(tree)
    no_pointer_only_path(loop, source)
    real_buttons(loop)
    wiring(loop, source)
    size_neutral_chrome()
    print("\nvideo zoom accessibility selftest: all checks passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print("\nvideo zoom accessibility selftest FAILED: %s" % exc)
        sys.exit(1)
