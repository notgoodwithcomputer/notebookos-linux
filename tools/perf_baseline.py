#!/usr/bin/env python3
"""perf_baseline — where does an app's startup time and memory actually go?

Launch latency on this OS is dominated by two things a profiler can measure
cheaply and repeatably on the host: the cost of IMPORTING the module (top-level
work, JSON/asset loading, regex compilation) and the cost of CONSTRUCTING the
window (building every widget). Memory is the resident-set growth the module
causes. Measuring all three per app turns "feels slow" into a ranked list.

Each app is measured in a FRESH subprocess so one app's imports never make the
next one look fast, and the numbers include the shared nbapp/GTK cost only once
per process (reported separately as the baseline row).

    DISPLAY=:0 FONTCONFIG_FILE=tools/guest-fonts.conf python3 tools/perf_baseline.py
    ... --json out.json     also write machine-readable results
"""
import os
import sys
import json
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

APPS = ["writer", "novel", "journal", "academic", "screenplay", "ebook",
        "cookbook", "contacts", "accounting", "calendar", "music",
        "illustrator", "sequencer", "video", "media", "g2048", "packages",
        "settings", "sysmon", "calculator", "terminal", "tasks", "language",
        "maps", "finder", "gbaide", "gbaemu", "widgets"]

CHILD = r'''
import os, sys, time, resource, importlib, inspect
sys.path.insert(0, %(de)r)
os.environ.setdefault("NB_HOME", %(home)r)
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

def rss_kb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

base_rss = rss_kb()
name = %(app)r
t0 = time.perf_counter()
m = importlib.import_module(name)
t_import = time.perf_counter() - t0
imp_rss = rss_kb()

cls = None
for _n, c in inspect.getmembers(m, inspect.isclass):
    if c.__module__ == m.__name__ and issubclass(c, Gtk.Window):
        cls = c
        break
t_construct = -1.0
if cls is not None:
    t1 = time.perf_counter()
    w = cls()
    n = 0
    while Gtk.events_pending() and n < 500:
        Gtk.main_iteration_do(False)
        n += 1
    t_construct = time.perf_counter() - t1
end_rss = rss_kb()
print(json.dumps if False else "")
import json as _j
print("RESULT" + _j.dumps({
    "app": name, "import_ms": t_import * 1000.0,
    "construct_ms": t_construct * 1000.0,
    "import_rss_kb": imp_rss - base_rss,
    "total_rss_kb": end_rss - base_rss,
}))
'''


def measure(app, home):
    src = CHILD % {"de": DE, "home": home, "app": app}
    try:
        r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                           text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"app": app, "error": "timeout"}
    for ln in r.stdout.splitlines():
        if ln.startswith("RESULT"):
            return json.loads(ln[6:])
    return {"app": app, "error": (r.stderr.strip().splitlines() or ["?"])[-1][:90]}


def main():
    home = os.environ.get("NB_HOME") or "/tmp/nbperf-home"
    os.makedirs(home, exist_ok=True)
    rows = []
    for app in APPS:
        rows.append(measure(app, home))
    ok = [r for r in rows if "error" not in r]
    ok.sort(key=lambda r: -(r["import_ms"] + max(0.0, r["construct_ms"])))
    print("%-12s %10s %12s %11s %11s" %
          ("app", "import_ms", "construct_ms", "total_ms", "rss_kb"))
    print("-" * 60)
    for r in ok:
        total = r["import_ms"] + max(0.0, r["construct_ms"])
        print("%-12s %10.1f %12.1f %11.1f %11d"
              % (r["app"], r["import_ms"], r["construct_ms"], total,
                 r["total_rss_kb"]))
    for r in rows:
        if "error" in r:
            print("%-12s ERROR %s" % (r["app"], r["error"]))
    if ok:
        tot = [r["import_ms"] + max(0.0, r["construct_ms"]) for r in ok]
        print("\nslowest: %s (%.0f ms)   median: %.0f ms   sum: %.0f ms"
              % (ok[0]["app"], tot[0], sorted(tot)[len(tot) // 2], sum(tot)))
    if "--json" in sys.argv:
        p = sys.argv[sys.argv.index("--json") + 1]
        json.dump(rows, open(p, "w"), indent=1)
        print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
