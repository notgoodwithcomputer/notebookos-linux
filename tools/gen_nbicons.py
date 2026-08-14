#!/usr/bin/env python3
"""Compile the pinned Lucide SVG set into Notebook OS cairo path data."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor/lucide"
DEFAULT_OUTPUT = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/nbicons_data.py"

# Every Notebook OS glyph is selected from the professionally designed Lucide
# set.  This table is intentionally the only hand-authored part of the family.
MAPPING = {
    # A filmstrip of frames is the subject; shot footage owns clapperboard, stills own image.
    "writer": "file-pen-line", "novel": "book-open", "comics": "panels-top-left", "animation": "film",
    "academic": "graduation-cap", "journal": "notebook-pen", "screenplay": "scroll-text",
    "tasks": "list-todo", "calendar": "calendar", "workout": "dumbbell",
    "cookbook": "cooking-pot", "mealplanner": "utensils", "ebook": "book",
    # accounting: banknote, not scale — the scales read as JUSTICE, not money
    # (design owner, 2026-08-10, on the 1.8 boot shot). banknote is money
    # itself and currency-neutral; wallet/landmark/piggy-bank are the easy
    # swaps if the note ever reads as a card.
    "calculator": "calculator", "accounting": "banknote", "bills": "mail",
    "contacts": "contact-round", "messages": "message-circle-more",
    "g2048": "layout-grid", "tetris": "blocks", "gamepad": "gamepad-2",
    "mappin": "map-pin", "globe": "languages", "cartridge": "cassette-tape",
    "illustrator": "pen-tool", "sequencer": "audio-lines", "composer": "music",
    "video": "clapperboard", "media": "image", "music": "disc-3",
    # music owns disc-3, the grooved record — something already recorded. The
    # burner gets the plain blank disc, which is the thing you put in the drive
    # and the distinction a person actually makes between the two.
    "burner": "disc",
    "packages": "package", "signal": "radio-tower", "play": "play",
    "stopsq": "square", "pause": "pause", "wclose": "x", "wzoom": "maximize-2",
    "wshade": "chevron-up", "rew": "rewind", "ff": "fast-forward",
    "folder": "folder", "home": "house", "desktop": "monitor", "disk": "hard-drive",
    "trash": "trash-2", "search": "search", "back": "chevron-left",
    "backspace": "delete", "fwd": "chevron-right", "up": "arrow-up",
    "down": "arrow-down", "viewlist": "list", "viewgrid": "grid-2x2",
    "check": "check", "link": "link", "quote": "quote", "plus": "plus",
    "star": "star", "inbox": "inbox", "bullet": "list", "number": "list-ordered",
    "highlight": "highlighter", "toc": "rows-3", "alignleft": "text-align-start",
    "aligncenter": "text-align-center", "alignright": "text-align-end",
    "alignjustify": "text-align-justify", "indent": "list-indent-increase",
    "outdent": "list-indent-decrease", "table": "table-2", "eject": "eject",
    "library": "library-big", "bookmark": "bookmark", "pencil": "pencil",
    "brush": "paintbrush", "eraser": "eraser", "fill": "paint-bucket",
    "picker": "pipette", "line": "slash", "rect": "rectangle-horizontal",
    "duplicate": "copy", "ellipse": "circle", "eye": "eye", "eyeoff": "eye-off",
    "prev": "skip-back", "next": "skip-forward", "zoomin": "zoom-in",
    "zoomout": "zoom-out", "rotate": "rotate-cw", "trfade": "blend",
    "trdissolve": "layers-2", "trwipe": "square-split-horizontal",
    "trslide": "arrow-right-to-line", "triris": "scan", "trblack": "square",
    "album": "disc-album", "artist": "user-round", "vol": "volume-2",
    "shuffle": "shuffle", "repeat": "repeat-2", "box": "box", "update": "refresh-cw",
    "sources": "server", "sys": "settings", "terminal": "square-terminal",
    "sysmon": "activity", "installer": "hard-drive-download", "gbasdk": "square-code",
    "usbwriter": "usb", "cup": "coffee", "palette": "palette", "family": "users-round",
    "bolt": "zap", "question": "circle-question-mark", "nosign": "ban",
    "shirt": "shirt", "paw": "paw-print", "leaf": "leaf", "clock": "clock-3",
    "cloud": "cloud", "compass": "compass", "bus": "bus-front", "plane": "plane",
    "heart": "heart", "body": "person-standing", "cross": "cross",
    "briefcase": "briefcase-business", "coins": "coins", "cart": "shopping-cart",
    "ball": "circle-dot", "tree": "tree-pine", "city": "building-2", "flame": "flame",
    "crown": "crown", "lock": "lock-keyhole", "trophy": "trophy",
    "target": "target", "speech": "message-circle",
    # The menu bar's notification centre. A bell, not "inbox" (already the
    # Finder's tray) and not "speech" (a message from a PERSON, which nothing
    # in this offline OS can be): the sender is always the machine reporting on
    # work the user started.
    #
    # Two glyphs, because the unread mark is REGISTERED rather than painted on
    # top: bell-dot is the same bell with its shoulder cut back to leave a notch
    # for the spot, so shell.bell_surface can fill that notch in signage red and
    # the ink outline lands exactly on the fill's edge. Overlaying a dot on the
    # plain bell would have crossed its silhouette.
    "bell": "bell", "belldot": "bell-dot",
}

_TOKEN = re.compile(r"[AaCcHhLlMmQqSsTtVvZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4,
          "Q": 4, "T": 2, "A": 7, "Z": 0}


def _num(value: float) -> float:
    value = round(float(value), 4)
    return 0.0 if value == 0 else value


def _arc_cubics(x1, y1, rx, ry, angle, large, sweep, x2, y2):
    """SVG endpoint-parameterized elliptical arc -> cubic Beziers."""
    rx, ry = abs(rx), abs(ry)
    if not rx or not ry or (x1 == x2 and y1 == y2):
        return []
    phi = math.radians(angle % 360.0)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    xp, yp = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = xp * xp / (rx * rx) + yp * yp / (ry * ry)
    if lam > 1:
        scale = math.sqrt(lam); rx *= scale; ry *= scale
    den = rx * rx * yp * yp + ry * ry * xp * xp
    coef = 0.0 if den == 0 else math.sqrt(max(0.0, (rx * rx * ry * ry - den) / den))
    if bool(large) == bool(sweep):
        coef = -coef
    cxp, cyp = coef * rx * yp / ry, coef * -ry * xp / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def vang(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    ux, uy = (xp - cxp) / rx, (yp - cyp) / ry
    vx, vy = (-xp - cxp) / rx, (-yp - cyp) / ry
    start = math.atan2(uy, ux)
    delta = vang(ux, uy, vx, vy)
    if not sweep and delta > 0: delta -= 2 * math.pi
    if sweep and delta < 0: delta += 2 * math.pi
    count = max(1, int(math.ceil(abs(delta) / (math.pi / 2))))
    step = delta / count
    result = []
    def point(t):
        return (cx + rx * cp * math.cos(t) - ry * sp * math.sin(t),
                cy + rx * sp * math.cos(t) + ry * cp * math.sin(t))
    def deriv(t):
        return (-rx * cp * math.sin(t) - ry * sp * math.cos(t),
                -rx * sp * math.sin(t) + ry * cp * math.cos(t))
    for i in range(count):
        a, b = start + i * step, start + (i + 1) * step
        alpha = 4 / 3 * math.tan((b - a) / 4)
        p0, p3, d0, d3 = point(a), point(b), deriv(a), deriv(b)
        result.append((p0[0] + alpha*d0[0], p0[1] + alpha*d0[1],
                       p3[0] - alpha*d3[0], p3[1] - alpha*d3[1], p3[0], p3[1]))
    return result


def parse_path(data: str):
    tokens = _TOKEN.findall(data.replace(",", " "))
    out, i, command = [], 0, None
    x = y = sx = sy = 0.0
    last_cubic = last_quad = None
    while i < len(tokens):
        if tokens[i].isalpha(): command = tokens[i]; i += 1
        if command is None: raise ValueError("path data starts without a command")
        upper, relative = command.upper(), command.islower()
        if upper == "Z":
            out.append(("z",)); x, y = sx, sy; last_cubic = last_quad = None; command = None; continue
        n = _ARITY[upper]
        # SVG arc flags are single characters and may be packed with each
        # other and the following coordinate (for example ``0 0022``).
        if upper == "A" and i + 3 < len(tokens):
            packed = tokens[i + 3]
            if not packed.isalpha() and len(packed) >= 2 and packed[0] in "01" and packed[1] in "01":
                replacement = [packed[0], packed[1]]
                if packed[2:]: replacement.append(packed[2:])
                tokens[i + 3:i + 4] = replacement
            elif packed in ("0", "1") and i + 4 < len(tokens):
                packed2 = tokens[i + 4]
                if len(packed2) >= 2 and packed2[0] in "01":
                    replacement = [packed2[0]]
                    if packed2[1:]: replacement.append(packed2[1:])
                    tokens[i + 4:i + 5] = replacement
        if i + n > len(tokens) or any(v.isalpha() for v in tokens[i:i+n]):
            raise ValueError(f"missing operands for {command}: {data!r}")
        values = [float(v) for v in tokens[i:i+n]]; i += n
        oldx, oldy = x, y
        if upper == "M":
            nx, ny = values; nx += x if relative else 0; ny += y if relative else 0
            out.append(("m", _num(nx), _num(ny))); x, y = sx, sy = nx, ny
            command = "l" if relative else "L"
        elif upper == "L":
            nx, ny = values; nx += x if relative else 0; ny += y if relative else 0
            out.append(("l", _num(nx), _num(ny))); x, y = nx, ny
        elif upper == "H":
            nx = values[0] + (x if relative else 0); out.append(("l", _num(nx), _num(y))); x = nx
        elif upper == "V":
            ny = values[0] + (y if relative else 0); out.append(("l", _num(x), _num(ny))); y = ny
        elif upper == "C":
            a,b,c,d,nx,ny=values
            if relative: a+=x;b+=y;c+=x;d+=y;nx+=x;ny+=y
            out.append(("c",*map(_num,(a,b,c,d,nx,ny)))); x,y=nx,ny; last_cubic=(c,d)
        elif upper == "S":
            c,d,nx,ny=values
            if relative: c+=x;d+=y;nx+=x;ny+=y
            a,b=(2*x-last_cubic[0],2*y-last_cubic[1]) if last_cubic else (x,y)
            out.append(("c",*map(_num,(a,b,c,d,nx,ny)))); x,y=nx,ny; last_cubic=(c,d)
        elif upper in ("Q", "T"):
            if upper == "Q":
                qx,qy,nx,ny=values
                if relative: qx+=x;qy+=y;nx+=x;ny+=y
            else:
                nx,ny=values
                if relative: nx+=x;ny+=y
                qx,qy=(2*x-last_quad[0],2*y-last_quad[1]) if last_quad else (x,y)
            c1=(x+2*(qx-x)/3, y+2*(qy-y)/3); c2=(nx+2*(qx-nx)/3, ny+2*(qy-ny)/3)
            out.append(("c",*map(_num,(*c1,*c2,nx,ny)))); x,y=nx,ny; last_quad=(qx,qy)
        elif upper == "A":
            rx,ry,rot,large,sweep,nx,ny=values
            if relative: nx+=x;ny+=y
            curves=_arc_cubics(x,y,rx,ry,rot,int(large),int(sweep),nx,ny)
            if curves:
                out.extend(("c",*map(_num,c)) for c in curves)
            elif x != nx or y != ny: out.append(("l",_num(nx),_num(ny)))
            x,y=nx,ny
        if upper not in ("C", "S"): last_cubic = None
        if upper not in ("Q", "T"): last_quad = None
    return tuple(out)


def _circle(cx, cy, rx, ry=None):
    ry = rx if ry is None else ry; k = 0.5522847498
    return (("m",_num(cx+rx),_num(cy)),
            ("c",_num(cx+rx),_num(cy+k*ry),_num(cx+k*rx),_num(cy+ry),_num(cx),_num(cy+ry)),
            ("c",_num(cx-k*rx),_num(cy+ry),_num(cx-rx),_num(cy+k*ry),_num(cx-rx),_num(cy)),
            ("c",_num(cx-rx),_num(cy-k*ry),_num(cx-k*rx),_num(cy-ry),_num(cx),_num(cy-ry)),
            ("c",_num(cx+k*rx),_num(cy-ry),_num(cx+rx),_num(cy-k*ry),_num(cx+rx),_num(cy)),("z",))


def _shape_commands(el):
    tag = el.tag.rsplit("}", 1)[-1]; a = el.attrib
    if tag == "path": return parse_path(a.get("d", ""))
    if tag == "circle": return _circle(float(a["cx"]),float(a["cy"]),float(a["r"]))
    if tag == "ellipse": return _circle(float(a["cx"]),float(a["cy"]),float(a["rx"]),float(a["ry"]))
    if tag == "line": return (("m",_num(a["x1"]),_num(a["y1"])),("l",_num(a["x2"]),_num(a["y2"])))
    if tag in ("polyline", "polygon"):
        nums=[float(x) for x in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?",a["points"])]
        commands=[("m",_num(nums[0]),_num(nums[1]))]+[("l",_num(nums[j]),_num(nums[j+1])) for j in range(2,len(nums),2)]
        if tag == "polygon": commands.append(("z",))
        return tuple(commands)
    if tag == "rect":
        x,y,w,h=map(float,(a.get("x",0),a.get("y",0),a["width"],a["height"])); rx=float(a.get("rx",0))
        if not rx: return (("m",_num(x),_num(y)),("l",_num(x+w),_num(y)),("l",_num(x+w),_num(y+h)),("l",_num(x),_num(y+h)),("z",))
        rx=min(rx,w/2,h/2); k=.5522847498
        return (("m",_num(x+rx),_num(y)),("l",_num(x+w-rx),_num(y)),
                ("c",_num(x+w-rx+k*rx),_num(y),_num(x+w),_num(y+rx-k*rx),_num(x+w),_num(y+rx)),
                ("l",_num(x+w),_num(y+h-rx)),("c",_num(x+w),_num(y+h-rx+k*rx),_num(x+w-rx+k*rx),_num(y+h),_num(x+w-rx),_num(y+h)),
                ("l",_num(x+rx),_num(y+h)),("c",_num(x+rx-k*rx),_num(y+h),_num(x),_num(y+h-rx+k*rx),_num(x),_num(y+h-rx)),
                ("l",_num(x),_num(y+rx)),("c",_num(x),_num(y+rx-k*rx),_num(x+rx-k*rx),_num(y),_num(x+rx),_num(y)),("z",))
    raise ValueError(f"unsupported SVG shape: {tag}")


def compile_svg(path):
    root=ET.parse(path).getroot(); strokes=[]; fills=[]
    supported={"path","circle","ellipse","rect","line","polyline","polygon"}
    for el in root.iter():
        if el.tag.rsplit("}",1)[-1] not in supported: continue
        commands=_shape_commands(el)
        fill=el.get("fill", root.get("fill", "none"))
        stroke=el.get("stroke", root.get("stroke", "none"))
        if fill not in ("none", "transparent"): fills.append(commands)
        if stroke not in ("none", "transparent"): strokes.extend(commands)
    return tuple(strokes), tuple(fills)


def _format_command(command):
    return "(" + ", ".join(repr(v) for v in command) + ("," if len(command)==1 else "") + ")"


def generate(vendor=VENDOR):
    paths={}; fills={}
    for key, stem in MAPPING.items():
        source=vendor/"icons"/(stem+".svg")
        if not source.is_file(): raise FileNotFoundError(f"{key}: {source}")
        paths[key], fill=compile_svg(source)
        if fill: fills[key]=fill
    lines=["# Generated by tools/gen_nbicons.py; DO NOT EDIT.",
           "# Lucide 1.31.0, ISC license; source: vendor/lucide/icons/*.svg",
           "# Path coordinates remain in Lucide's native 24 x 24 grid.","", "PATHS = {"]
    for key, commands in paths.items():
        lines.append(f"    {key!r}: (")
        lines.extend(f"        {_format_command(c)}," for c in commands)
        lines.append("    ),")
    lines += ["}", "", "FILLS = {"]
    for key, groups in fills.items():
        lines.append(f"    {key!r}: (")
        for commands in groups:
            lines.append("        ("); lines.extend(f"            {_format_command(c)}," for c in commands); lines.append("        ),")
        lines.append("    ),")
    lines += ["}", "", "MAPPING = {"].copy()
    lines.extend(f"    {key!r}: {stem!r}," for key,stem in MAPPING.items())
    lines += ["}", ""]
    return "\n".join(lines)


def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path,default=DEFAULT_OUTPUT); parser.add_argument("--vendor",type=Path,default=VENDOR)
    args=parser.parse_args(argv); args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(generate(args.vendor),encoding="utf-8")
    print(f"generated {args.output} ({len(MAPPING)} icons)")


if __name__ == "__main__": main()
