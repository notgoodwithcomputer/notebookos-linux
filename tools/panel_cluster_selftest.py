#!/usr/bin/env python3
"""
The menu bar's right-hand cluster does not shift, in any language.

`shell.py` is the panel: the one surface on screen at all times, in all
seventeen languages, and until now **no suite imported it**.

`Panel._pin_widths` reserves, for each read-out, the width of the widest text
it can ever show, so the cluster does not jitter as the clock ticks and the
date rolls over. It measured those samples with `Gtk.Label.create_pango_layout`
— a raw Pango call, which does NOT pass through nbi18n the way `set_markup`
does. So it measured English and displayed the translation.
`set_size_request` is a MINIMUM, so the label simply grew past its reservation
and the cluster moved. Measured before the fix, eight of the seventeen
languages were over: es +25px ("Dom 28 de mayo"), it +20, zh +12, el +10,
tr +10, pt +6, fr +5, yi +4, eo +2. English was right by luck.

WHAT THIS MEASURES. Every date the bar can actually show — all 366 days of a
leap year through the bar's own `%a %-d %b`, translated the way the label
translates it — and every minute of the day for the clock, in both 12- and
24-hour form. Not a sample: the maximum over the real domain, which is the only
thing a "widest possible" claim can mean.

Each language runs in its OWN PROCESS. nbi18n fixes the language at import and
caches its date regexp on first use, so switching in-process measures the first
language seventeen times (measured: all seventeen reported English's 67px).

Run:
    tools/guestrun.sh python3 tools/panel_cluster_selftest.py
    tools/guestrun.sh python3 tools/panel_cluster_selftest.py --de DIR
    tools/guestrun.sh python3 tools/panel_cluster_selftest.py --child LANG
"""
import os
import re
import sys
import json
import glob
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])

FAILED, N = [], [0]

# The bar is 1024px wide at the smallest screen this OS targets, and the left
# half carries the logo and the app menus.
SCREEN_W = 1024
# A third of that bar, for clock + date + battery together. English measures
# 160px, the widest language (es) 187px, so this is headroom, not a ratchet
# fitted to today's numbers.
CLUSTER_BUDGET = SCREEN_W // 3


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


# --------------------------------------------------------------------------
# the child: one language, one real Panel
# --------------------------------------------------------------------------
def child(lang):
    os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="nb-panel-")
    sys.path.insert(0, DE)
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    import time
    from nbi18n import _t
    import shell

    p = shell.Panel()
    for _ in range(200):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration()

    def wide(lbl, strings):
        return max(lbl.create_pango_layout(s).get_pixel_size()[0]
                   for s in strings)

    # Every date the bar can show: the bar's own format, every day of a leap
    # year, translated exactly as the label translates it.
    dates = []
    for yday in range(366):
        st = time.localtime(time.mktime((2024, 1, 1, 12, 0, 0, 0, 1, 0))
                            + yday * 86400)
        dates.append(_t(time.strftime("%a %-d %b", st)))

    out = {"lang": lang, "shown_date": dates[218]}

    # 24-hour and 12-hour, seconds off and on — four pinnings, each measured
    # against every minute of the day in that form.
    for h24 in (True, False):
        for secs in (False, True):
            p._clock_24h, p._clock_seconds = h24, secs
            p._pin_widths()
            fmt = ("%H:%M" if h24 else "%-I:%M %p")
            if secs:
                fmt = ("%H:%M:%S" if h24 else "%-I:%M:%S %p")
            times = []
            for m in range(0, 1440):
                st = time.localtime(time.mktime((2024, 1, 1, 0, 0, 0, 0, 1, 0))
                                    + m * 60 + 59)
                times.append(_t(time.strftime(fmt, st)))
            key = "clock_%s_%s" % ("24" if h24 else "12",
                                   "sec" if secs else "min")
            out[key] = {"pin": p.clocklbl.get_size_request()[0],
                        "need": wide(p.clocklbl, times),
                        "widest": max(times, key=lambda s: wide(p.clocklbl, [s]))}

    p._clock_24h, p._clock_seconds = True, False
    p._pin_widths()
    out["date"] = {"pin": p.datelbl.get_size_request()[0],
                   "need": wide(p.datelbl, dates),
                   "widest": max(dates, key=lambda s: wide(p.datelbl, [s]))}
    bats = [_t("%d%%" % n) for n in range(0, 101)] + \
           [_t("%d%%+" % n) for n in range(0, 101)]
    out["battery"] = {"pin": p.batlbl.get_size_request()[0],
                      "need": wide(p.batlbl, bats),
                      "widest": max(bats, key=lambda s: wide(p.batlbl, [s]))}

    # And what the whole bar asks for, against the narrowest screen shipped.
    # The panel lays out in a Gtk.Fixed, which does not propagate a child's
    # width, so the window's preferred width is just the screen it spans and
    # says nothing about the content. What the cluster actually RESERVES is
    # the number that grew, and it is the one worth watching.
    out["cluster"] = (p.clocklbl.get_size_request()[0]
                      + p.datelbl.get_size_request()[0]
                      + p.batlbl.get_size_request()[0])
    print("<<<" + json.dumps(out, ensure_ascii=False) + ">>>")
    return 0


# --------------------------------------------------------------------------
def run_lang(lang):
    env = dict(os.environ)
    env["NB_LANG"] = lang
    env.pop("NB_HOME", None)
    p = subprocess.run([sys.executable, os.path.abspath(__file__),
                        "--child", lang, "--de", DE], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    m = re.search(r"<<<(.*)>>>", p.stdout.decode("utf-8", "replace"), re.S)
    if not m:
        return {"error": (p.stdout + p.stderr).decode("utf-8", "replace")[-300:]}
    return json.loads(m.group(1))


def main():
    langs = ["en"] + sorted(os.path.basename(f)[5:-5]
                            for f in glob.glob(os.path.join(DE, "lang_*.json")))
    results = {}
    for lg in langs:
        results[lg] = run_lang(lg)

    broke = [lg for lg, r in results.items() if "error" in r]
    ok = check("a real Panel builds in every language", not broke,
               "%s %s" % (broke[:3], results[broke[0]]["error"][-120:]) if broke else "")
    if not ok:
        print("\nRESULT: FAILED\n  the panel did not build; nothing below ran")
        return 1

    # ---- the reservation covers what is displayed ------------------------
    for field, label in (("date", "the date"),
                         ("clock_24_min", "the 24-hour clock"),
                         ("clock_12_min", "the 12-hour clock"),
                         ("clock_24_sec", "the 24-hour clock with seconds"),
                         ("clock_12_sec", "the 12-hour clock with seconds"),
                         ("battery", "the battery read-out")):
        over = [(lg, r[field]["need"] - r[field]["pin"], r[field]["widest"])
                for lg, r in results.items() if r[field]["need"] > r[field]["pin"]]
        check("%s reserves its widest reading in all %d languages"
              % (label, len(langs)), not over,
              "; ".join("%s +%dpx %r" % (l, d, w) for l, d, w in over[:4]))

    # ---- and it is not reserving absurdly more than it needs -------------
    # A pin far wider than anything shown is dead space in a 1024px bar.
    slack = [(lg, r["date"]["pin"] - r["date"]["need"])
             for lg, r in results.items()
             if r["date"]["pin"] - r["date"]["need"] > 12]
    check("...without reserving space nothing can fill", not slack,
          "; ".join("%s +%dpx" % (l, d) for l, d in slack[:4]))

    # ---- the cluster stays a cluster ------------------------------------
    # It sits at the right end of a bar that is 1024px on the smallest screen
    # this OS targets, with the logo and the app menus to its left. A third of
    # the bar is the budget; English uses BUDGET_REF of it.
    fat = [(lg, r["cluster"]) for lg, r in results.items()
           if r["cluster"] > CLUSTER_BUDGET]
    check("the read-out cluster stays inside its %dpx budget (en uses %d)"
          % (CLUSTER_BUDGET, results["en"]["cluster"]), not fat,
          "; ".join("%s %dpx" % (l, w) for l, w in fat[:4]))

    # ---- the date really is translated -----------------------------------
    # If it were not, every width above would agree with English for the wrong
    # reason and this suite would be measuring nothing.
    same = [lg for lg, r in results.items()
            if lg not in ("en", "eo") and r["shown_date"] == results["en"]["shown_date"]]
    check("the bar date is translated, not left in English", not same,
          "%s all show %r" % (same[:5], results["en"]["shown_date"]))

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


if "--child" in sys.argv:
    sys.exit(child(sys.argv[sys.argv.index("--child") + 1]))
sys.exit(main())
