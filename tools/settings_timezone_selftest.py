#!/usr/bin/env python3
"""
The time zone has to leave the Settings window.

ROADMAP #23. Picking a zone called `_apply_tz`, which sets `os.environ["TZ"]`
and calls `time.tzset()` — so the clock ON THE DATE & TIME PAGE moved, and
nothing else did. `os.environ` is one process's memory. The panel clock,
Calendar, Journal and every other app are separate processes that were started
by session.sh long before, and they kept the zone the machine booted in. The
setting looked like it worked, from the one window that could not tell.

The fix has two halves and this suite checks both:

  * settings.py persists `tz_posix` beside `tz`. The POSIX form, because no
    zoneinfo ships on this image — "Europe/Paris" names a file that is not
    there, while "CET-1CEST,M3.5.0,M10.5.0/3" carries the offset and the
    daylight-saving rule on its own.
  * session.sh reads that key and exports TZ before anything starts, so every
    app inherits it as a child of that shell.

That leaves apps ALREADY open on the old zone — a process cannot be handed a
new environment — so the page now says so, in the wording the Language setting
already uses. The honest sentence is part of the fix and is checked here too.

WHY THE SESSION HALF RUNS THE REAL SHELL TEXT. Re-typing the block into the
test would prove the test's copy works. This extracts the lines from the
shipped session.sh and runs those, with TZ unset first — otherwise an inherited
TZ would satisfy the assertion whether or not the block did anything.

Run:
    tools/guestrun.sh python3 tools/settings_timezone_selftest.py
    tools/guestrun.sh python3 tools/settings_timezone_selftest.py --de DIR
"""
import os
import re
import sys
import json
import time
import shutil
import subprocess
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-tz-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)
SESSION = os.path.join(os.path.dirname(DE), "session.sh")

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import settings as S  # noqa: E402

FAILED, N = [], [0]
CFG = os.path.join(_HOME, ".config", "notebook", "settings.json")

# Two zones that are neither the default nor each other, and both a long way
# from UTC so a fallback to UTC cannot be mistaken for success.
KOLKATA = [t for t in S.TIMEZONES if t[1] == "Asia/Kolkata"][0]
DENVER = [t for t in S.TIMEZONES if t[1] == "America/Denver"][0]

NOTE = ("Apps opened from now on use the time zone chosen here. Apps that are "
        "already open keep the one they started in; restart the computer to "
        "change all of them.")


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def pump():
    for _ in range(300):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration()


def saved():
    try:
        with open(CFG) as fh:
            return json.load(fh)
    except Exception:
        return {}


def session_block():
    """The real export block, lifted out of the shipped session.sh."""
    with open(SESSION) as fh:
        lines = fh.read().splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        if start is None and ln.startswith("NB_TZ="):
            start = i
        elif start is not None and ln.strip() == "unset NB_TZ":
            end = i
            break
    if start is None or end is None:
        return None
    return "\n".join(lines[start:end + 1])


def run_session_block(block, home):
    """Run it from a shell with no TZ at all and report what it exported."""
    script = ("%s\nprintf '%%s' \"${TZ-<<unset>>}\"\n" % block)
    env = dict(os.environ)
    env.pop("TZ", None)
    env["NB_HOME"] = home
    p = subprocess.run(["sh", "-c", script], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.stdout.decode("utf-8", "replace")


def labels(root, out=None):
    """Every label text under a widget, whitespace flattened."""
    out = [] if out is None else out
    if isinstance(root, Gtk.Label):
        out.append(" ".join(root.get_text().split()))
    if isinstance(root, Gtk.Container):
        for kid in root.get_children():
            labels(kid, out)
    return out


CHILD = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import settings as S
app = S.Settings()
for _ in range(300):
    if not Gtk.events_pending():
        break
    Gtk.main_iteration()
app._ensure_built("Region & Language")
rc = getattr(app, "_region_tz", None)
res = {"built": rc is not None,
       "tz_combo": getattr(app, "_tz_combo", None) is not None}
if rc is not None:
    rc.set_active(int(sys.argv[2]))
    for _ in range(300):
        if not Gtk.events_pending():
            break
        Gtk.main_iteration()
try:
    with open(os.path.join(os.environ["NB_HOME"], ".config", "notebook",
                           "settings.json")) as fh:
        res["saved"] = json.load(fh)
except Exception as exc:
    res["saved"] = None
    res["error"] = str(exc)
print("<<<" + json.dumps(res) + ">>>")
"""


def region_child(home, idx):
    """A second launch, for real: NB_HOME is read at import, so a fresh
    profile means a fresh PROCESS. Nothing here has ever opened Date & Time."""
    env = dict(os.environ)
    env["NB_HOME"] = home
    p = subprocess.run([sys.executable, "-c", CHILD, DE, str(idx)], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = p.stdout.decode("utf-8", "replace")
    m = re.search(r"<<<(.*)>>>", out, re.S)
    if not m:
        return {"error": (out + p.stderr.decode("utf-8", "replace"))[-300:]}
    return json.loads(m.group(1))


def offset_minutes(posix):
    """What glibc makes of the string — None if it rejects it."""
    keep = os.environ.get("TZ")
    try:
        os.environ["TZ"] = posix
        time.tzset()
        t = time.time()
        local = time.localtime(t)
        utc = time.gmtime(t)
        return int(round((time.mktime(local) - time.mktime(utc)) / 60.0))
    except Exception:
        return None
    finally:
        if keep is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = keep
        try:
            time.tzset()
        except Exception:
            pass


def main():
    app = S.Settings()
    pump()
    # Pages are built on first view, so ask for them the way a click does.
    app._ensure_built("Date & Time")
    pump()

    # ---- the Date & Time page persists BOTH forms ---------------------
    combo = getattr(app, "_tz_combo", None)
    built = check("the Date & Time page has a zone chooser", combo is not None)
    if not built:
        not_reached("no combo", "choosing a zone persists tz_posix",
                    "...and the IANA name too")
    else:
        idx = S.TIMEZONES.index(KOLKATA)
        combo.set_active(idx)          # fires _on_tz
        pump()
        d = saved()
        check("choosing a zone persists tz_posix", d.get("tz_posix") == KOLKATA[2],
              repr(d.get("tz_posix")))
        check("...and the IANA name too", d.get("tz") == KOLKATA[1],
              repr(d.get("tz")))

    # ---- the Region page is the same setting, so it must persist too --
    # Two pages write one zone; a fix applied to one of them is half a fix.
    #
    # ON A SECOND WINDOW THAT HAS NEVER OPENED DATE & TIME, and this matters:
    # _on_region_tz ends by syncing the Date & Time combo, which fires _on_tz,
    # which saves. Reuse the instance above and that sync does the Region
    # handler's work for it — deleting the Region page's own save then changes
    # nothing and the check passes on a broken handler (measured: it did).
    # Somebody who opens Settings and goes straight to Region & Language has
    # no _tz_combo to sync, so this is also the ordinary path, not a corner.
    home2 = tempfile.mkdtemp(prefix="nb-tz-region-")
    out = region_child(home2, S.TIMEZONES.index(DENVER))
    fresh = check("a second window builds the Region page alone",
                  out.get("built") is True, repr(out)[:160])
    unbuilt = check("...and has no Date & Time combo to lean on",
                    out.get("tz_combo") is False)
    if not (fresh and unbuilt):
        not_reached("no isolated Region page",
                    "choosing a zone THERE persists it too")
    else:
        d = out.get("saved") or {}
        check("choosing a zone THERE persists it too",
              d.get("tz_posix") == DENVER[2] and d.get("tz") == DENVER[1],
              repr((d.get("tz"), d.get("tz_posix"))))
    shutil.rmtree(home2, ignore_errors=True)

    # ---- the persisted string is one the C library actually honours ---
    # A POSIX string glibc rejects falls back to UTC in silence, which is
    # exactly the failure this whole item is about.
    for lbl, iana, posix in (KOLKATA, DENVER):
        off = offset_minutes(posix)
        ok = check("%s: glibc reads the stored string" % iana, off is not None)
        if ok:
            check("...and it is not UTC (%+d min)" % off, off != 0)
        else:
            not_reached("glibc rejected it", "...and it is not UTC")

    # ---- session.sh reads that key and exports it ---------------------
    block = session_block()
    got = check("session.sh has a TZ export block", block is not None)
    if not got:
        not_reached("no block in session.sh",
                    "it exports the saved zone",
                    "it leaves TZ alone when nothing is saved",
                    "it survives a corrupt settings file")
    else:
        out = run_session_block(block, _HOME)
        check("it exports the saved zone", out == KOLKATA[2],
              "%r (wanted %r)" % (out, KOLKATA[2]))

        # Nothing saved: an empty export would tell glibc "UTC", which is a
        # decision the user never made. The block must not fire.
        blank = tempfile.mkdtemp(prefix="nb-tz-blank-")
        try:
            check("it leaves TZ alone when nothing is saved",
                  run_session_block(block, blank) == "<<unset>>",
                  repr(run_session_block(block, blank)))
        finally:
            shutil.rmtree(blank, ignore_errors=True)

        # A truncated settings.json must not stop the session starting.
        broke = tempfile.mkdtemp(prefix="nb-tz-broke-")
        try:
            os.makedirs(os.path.join(broke, ".config", "notebook"))
            with open(os.path.join(broke, ".config", "notebook",
                                   "settings.json"), "w") as fh:
                fh.write('{"tz_posix": "IST-5:3')
            check("it survives a corrupt settings file",
                  run_session_block(block, broke) == "<<unset>>",
                  repr(run_session_block(block, broke)))
        finally:
            shutil.rmtree(broke, ignore_errors=True)

    # ---- and the page says what it can and cannot do ------------------
    # The export reaches apps STARTED after the change. Saying more than that
    # would be the same lie the setting used to tell by staying silent. Read
    # the LABEL, not the source: a sentence that exists in the file but is
    # never packed says nothing to anybody.
    shown = [t for t in labels(app._page_holders["Date & Time"])
             if "already open" in t and "time zone" in t]
    said = check("the page states the scope of the change", bool(shown),
                 repr(shown[:1]))
    if said:
        cat = json.load(open(os.path.join(DE, "lang_fr.json")))
        check("...and it is translated", isinstance(cat.get(NOTE), str)
              and bool(cat.get(NOTE)))
    else:
        not_reached("no scope note", "...and it is translated")

    try:
        app.destroy()
    except Exception:
        pass

    print("\n%d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
    for d in os.listdir(tempfile.gettempdir()):
        if d.startswith("nb-tz-region-"):
            shutil.rmtree(os.path.join(tempfile.gettempdir(), d),
                          ignore_errors=True)
sys.exit(rc)
