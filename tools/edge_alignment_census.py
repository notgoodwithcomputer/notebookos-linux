#!/usr/bin/env python3
"""Rendered structural-edge census for Notebook OS (report only, never a gate).

Each application is constructed in a fresh process because application CSS is
screen-global in GTK3.  The child moves the app's real root into an
OffscreenWindow, forces the requested allocation, pumps the GTK queue, and
returns allocations of widgets whose style classes identify structural roles.

Run through tools/guestrun.sh so the guest theme and fonts are in force.
"""
import collections
import importlib
import inspect
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path[:0] = [os.path.join(REPO, "tools"), DE]
SIZES = ((1024, 722), (1366, 722))
RAIL = 240

# These are product-shape declarations, not measurement exceptions.  They keep
# a canvas without a navigation rail out of the rail outlier list, and make the
# exclusion visible in the report.
NO_SIDEBAR = {
    "animation": "timeline/canvas workspace; no navigation sidebar",
    "calculator": "single calculation workspace; no sidebar",
    "composer": "score workspace; no navigation sidebar",
    "contacts": "list/detail split, but no styled structural sidebar band",
    "ebook": "reader surface; no persistent sidebar",
    "g2048": "game board; no sidebar",
    "gbaemu": "emulator surface; no sidebar",
    "gbasdk": "IDE workspace; no single comparable navigation sidebar",
    "illustrator": "specialist tool dock, not a navigation sidebar",
    "language": "page flow; no persistent sidebar",
    "maps": "full-bleed map canvas; no sidebar",
    "media": "full-bleed media surface; no sidebar",
    "screenplay": "document surface; no sidebar",
    "sequencer": "timeline workspace; no navigation sidebar",
    "sysmon": "dashboard; no sidebar",
    "usbwriter": "single task flow; no sidebar",
    "video": "multi-pane editing workspace; no comparable navigation sidebar",
    "writer": "document surface; no sidebar",
}
FULL_BLEED = {
    "animation": "canvas/timeline is intentionally full-bleed",
    "composer": "score workspace is intentionally full-bleed",
    "g2048": "game surface, not a content column",
    "gbaemu": "emulator display is intentionally full-bleed",
    "gbasdk": "IDE panes do not form one content column",
    "illustrator": "canvas and tool docks do not form one content column",
    "maps": "map canvas is intentionally full-bleed",
    "media": "media surface is intentionally full-bleed",
    "sequencer": "timeline workspace is intentionally full-bleed",
    "video": "editor panes do not form one content column",
}

SIDEBAR_CLASSES = {
    "sidebar", "side", "nvside", "wo-side", "bl-side", "setsidebar",
    "inst-rail", "calsidebar", "comics-side", "rail",
}
HEADER_CLASSES = {
    "toolbar", "mainhead", "pk-head", "termhead", "emuhead", "wo-head",
    "bl-head", "calhead", "sethead", "inst-head", "ac-head", "nvtoolbar",
    "cookhead", "mp-head", "journalhead", "contacthead",
}
HEADER_WORDS = ("toolbar", "mainhead", "-toolbar", "apphead", "pagehead")


def _classes(widget):
    try:
        return set(widget.get_style_context().list_classes())
    except Exception:
        return set()


def _walk(widget, path="root"):
    yield widget, path
    if hasattr(widget, "get_children"):
        for i, child in enumerate(widget.get_children()):
            yield from _walk(child, "%s/%s[%d]" %
                             (path, child.__class__.__name__, i))


def _rect(widget, root):
    a = widget.get_allocation()
    try:
        pt = widget.translate_coordinates(root, 0, 0)
        if pt is None:
            return None
        x, y = pt
    except Exception:
        return None
    return {"x": int(x), "y": int(y), "w": int(a.width), "h": int(a.height)}


def _identity(path, classes):
    return "%s class=%s" % (path, ",".join(sorted(classes)) or "(none)")


def _pick_sidebar(rows, width, height):
    found = []
    for widget, path, cls, r in rows:
        if (cls & SIDEBAR_CLASSES and r["x"] <= 4 and
                120 <= r["w"] <= 380 and r["h"] >= height * .45):
            found.append((r["h"] * r["w"], widget, path, cls, r))
    return max(found, default=None, key=lambda item: item[0])


def _pick_header(rows, width, sidebar_edge):
    found = []
    for widget, path, cls, r in rows:
        named = bool(cls & HEADER_CLASSES) or any(
            word in c for c in cls for word in HEADER_WORDS)
        # A structural band is wide, near the top, and shallow.  Exclude heads
        # wholly inside a sidebar and tiny dialog/list section headings.
        if (named and r["y"] < 190 and 20 <= r["h"] <= 130 and
                r["w"] >= min(280, width * .28) and
                (sidebar_edge is None or r["x"] >= sidebar_edge - 2)):
            found.append((r["y"], -r["w"], path.count("/"), widget, path, cls, r))
    return min(found, default=None, key=lambda item: item[:3])


def _content_candidate(rows, width, height, sidebar, header):
    boundary = sidebar[-1]["x"] + sidebar[-1]["w"] if sidebar else 0
    hy = header[-1]["y"] + header[-1]["h"] if header else 0
    found = []
    for widget, path, cls, r in rows:
        if (r["x"] + 2 < boundary or r["w"] < 260 or r["h"] < 180 or
                r["x"] > width * .55 or r["y"] + r["h"] < hy + 100):
            continue
        # Prefer a page/main/canvas identity, then the shallowest broad region.
        semantic = any(any(k in c for k in ("main", "page", "canvas", "content", "stack"))
                       for c in cls)
        found.append((0 if semantic else 1, abs(r["x"] - boundary),
                      path.count("/"), -r["w"], widget, path, cls, r))
    return min(found, default=None, key=lambda item: item[:4])


def _measure_child(name, width, height):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk
    init = Gtk.init_check(None)
    gtk_ok = bool(init[0] if isinstance(init, tuple) else init)
    if not gtk_ok:
        return {"gtk_init_check": False, "error": "Gtk.init_check() failed"}
    import dialogshot
    import nbapp
    import uishot

    os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-edge-home-"))
    os.makedirs(os.environ["NB_HOME"], exist_ok=True)
    uishot.load_theme()
    nbapp.screen_size = lambda: (width, height)
    mod = importlib.import_module(name)
    dialogshot.install_app_css(mod)
    cls = next((c for _n, c in inspect.getmembers(mod, inspect.isclass)
                if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window)), None)
    if cls is None:
        return {"gtk_init_check": gtk_ok, "error": "no Gtk.Window subclass"}
    app = cls()
    child = app.get_child()
    if child is None:
        return {"gtk_init_check": gtk_ok, "error": "window has no child"}
    app.remove(child)
    off = Gtk.OffscreenWindow()
    off.set_default_size(width, height)
    off.set_size_request(width, height)
    off.add(child)
    off.show_all()
    for _ in range(80):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        off.resize(width, height)
        # Gdk.Rectangle, not Gtk.Allocation: PyGObject exposes no
        # Gtk.Allocation, so the original raised for EVERY app and the
        # census reported "COMPLETE" having measured nothing.
        alloc = Gdk.Rectangle()
        alloc.x, alloc.y, alloc.width, alloc.height = 0, 0, width, height
        child.size_allocate(alloc)

    rows = []
    for widget, path in _walk(child):
        r = _rect(widget, child)
        if r and r["w"] > 0 and r["h"] > 0 and widget.get_visible():
            rows.append((widget, path, _classes(widget), r))
    side = None if name in NO_SIDEBAR else _pick_sidebar(rows, width, height)
    side_edge = side[-1]["x"] + side[-1]["w"] if side else None
    head = _pick_header(rows, width, side_edge)
    content = None if name in FULL_BLEED else _content_candidate(rows, width, height, side, head)

    def measured(value, candidate):
        return {"state": "measured", "value": int(value),
                "source": _identity(candidate[-3], candidate[-2]),
                "rect": candidate[-1]}

    if name in NO_SIDEBAR:
        sidebar = {"state": "not applicable", "reason": NO_SIDEBAR[name]}
    elif side:
        sidebar = measured(side_edge, side)
    else:
        sidebar = {"state": "could not measure", "reason": "no rendered structural sidebar candidate"}
    if head:
        hr = head[-1]
        header_bottom = measured(hr["y"] + hr["h"], head)
        header_height = measured(hr["h"], head)
    else:
        header_bottom = header_height = {
            "state": "could not measure", "reason": "no rendered structural header-band candidate"}
    if name in FULL_BLEED:
        content_edge = {"state": "not applicable", "reason": FULL_BLEED[name]}
    elif content:
        content_edge = measured(content[-1]["x"], content)
    else:
        content_edge = {"state": "could not measure", "reason": "no rendered content-column candidate"}
    out = {"gtk_init_check": gtk_ok, "app": name, "size": [width, height],
           "edges": {"sidebar.trailing_x": sidebar,
                     "header.bottom_y": header_bottom,
                     "content.leading_x": content_edge,
                     "header.height": header_height}}
    off.destroy()
    try:
        app.destroy()
    except Exception:
        pass
    return out


def _run_one(name, width, height):
    env = dict(os.environ)
    env["NB_HOME"] = tempfile.mkdtemp(prefix="nb-edge-%s-" % name)
    try:
        proc = subprocess.run([sys.executable, os.path.abspath(__file__), "--one",
                               name, str(width), str(height)], text=True,
                              capture_output=True, timeout=180, env=env)
    except subprocess.TimeoutExpired:
        return {"app": name, "size": [width, height], "gtk_init_check": None,
                "error": "timed out"}
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith("{"):
            try:
                answer = json.loads(line)
                answer.setdefault("app", name)
                answer.setdefault("size", [width, height])
                return answer
            except ValueError:
                pass
    detail = ((proc.stderr or proc.stdout or "no output").strip().splitlines() or ["no output"])[-1]
    return {"app": name, "size": [width, height], "gtk_init_check": None,
            "error": detail[:160]}


def measure_app(name, width, height):
    """Measure one app in its isolated GTK process.

    This is the supported entry point for gates which need the census's exact
    rendered-allocation semantics rather than a second, drifting detector.
    The returned record always includes ``gtk_init_check``; callers must treat
    anything other than True as a failed observation, never as geometry.
    """
    return _run_one(name, width, height)


def _clusters(results, edge):
    vals = collections.defaultdict(list)
    na = collections.Counter()
    cm = collections.Counter()
    for row in results:
        if "error" in row:
            cm[row["error"]] += 1
            continue
        item = row["edges"][edge]
        if item["state"] == "measured":
            vals[item["value"]].append(row["app"])
        elif item["state"] == "not applicable":
            na[item["reason"]] += 1
        else:
            cm[item["reason"]] += 1
    ordered = sorted(vals.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return ordered, na, cm


def _rank_score(clusters):
    n = sum(len(apps) for _v, apps in clusters)
    top = len(clusters[0][1]) if clusters else 0
    dissent = n - top
    # Agreement first; for a tie prefer more evidence, then fewer dissenters.
    return ((top / n) if n else 0, n, -dissent)


def main():
    import finder
    apps = sorted(set(finder.APP_MODULES.values()) | {"finder"})
    all_results = []
    print("EDGE ALIGNMENT CENSUS — rendered allocations; report only; exit is always 0")
    print("sizes: " + ", ".join("%dx%d" % s for s in SIZES))
    for width, height in SIZES:
        print("\n=== %dx%d ===" % (width, height))
        size_rows = []
        for name in apps:
            row = _run_one(name, width, height)
            size_rows.append(row)
            all_results.append(row)
            if "error" in row:
                print("APP %-12s FAILED: %s" % (name, row["error"]))
                continue
            print("APP %-12s measured (Gtk.init_check()=%s)" %
                  (name, "succeeded" if row["gtk_init_check"] else "failed"))
            for edge, item in row["edges"].items():
                if item["state"] == "measured":
                    print("  %-20s MEASURED %4d  source=%s rect=%s" %
                          (edge, item["value"], item["source"], item["rect"]))
                else:
                    print("  %-20s %-17s reason=%s" %
                          (edge, item["state"].upper(), item["reason"]))
        ranked = []
        for edge in ("sidebar.trailing_x", "header.bottom_y", "content.leading_x", "header.height"):
            clusters, na, cm = _clusters(size_rows, edge)
            ranked.append((_rank_score(clusters), edge, clusters, na, cm))
        ranked.sort(reverse=True)
        print("\nRANKED DISAGREEMENTS %dx%d" % (width, height))
        for rank, (_score, edge, clusters, na, cm) in enumerate(ranked, 1):
            print("%d. %s%s" % (rank, edge,
                  " (design token RAIL=%d)" % RAIL if edge == "sidebar.trailing_x" else ""))
            for value, names in clusters:
                print("     %4d: %2d apps — %s" % (value, len(names), ", ".join(names)))
            print("     excluded N/A=%d; could-not-measure/failed=%d" %
                  (sum(na.values()), sum(cm.values())))
            for reason, count in sorted(na.items()):
                print("       N/A x%d: %s" % (count, reason))
            for reason, count in sorted(cm.items()):
                print("       UNMEASURED x%d: %s" % (count, reason))

    constructed = sum(1 for r in all_results if "error" not in r)
    failed = len(all_results) - constructed
    na_count = sum(1 for r in all_results if "error" not in r
                   for e in r["edges"].values() if e["state"] == "not applicable")
    measured = sum(1 for r in all_results if "error" not in r
                   for e in r["edges"].values() if e["state"] == "measured")
    gtk = [r.get("gtk_init_check") for r in all_results]
    failed_apps = len({r["app"] for r in all_results if "error" in r})
    print("\nSUMMARY app-size runs: constructed=%d failed=%d; edge observations: measured=%d N/A=%d" %
          (constructed, failed, measured, na_count))
    print("SUMMARY unique apps: measured=%d excluded=%d failed=%d (of %d)" %
          (len(apps) - failed_apps, 0, failed_apps, len(apps)))
    print("DISPLAY/Gtk.init_check(): %s (%d/%d attempted runs succeeded)" %
          ("present" if gtk and all(gtk) else "NOT reliably present", sum(bool(x) for x in gtk), len(gtk)))
    print("RESULT: CENSUS COMPLETE (report only; exit 0)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--one":
        try:
            answer = _measure_child(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
        except Exception as exc:  # census failures are data, not process failure
            answer = {"app": sys.argv[2], "size": [int(sys.argv[3]), int(sys.argv[4])],
                      "error": "%s: %s" % (type(exc).__name__, str(exc)[:140])}
        print(json.dumps(answer, sort_keys=True))
        raise SystemExit(0)
    try:
        main()
    except Exception as exc:
        print("CENSUS DRIVER FAILED (reported, exit remains 0): %s: %s" %
              (type(exc).__name__, exc))
    raise SystemExit(0)
