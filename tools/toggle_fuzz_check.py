#!/usr/bin/env python3
"""Flip every persistent toggle in every shipped app and demand silence.

The defect class this exists for has now bitten SIX times (sequencer, packages
sidebar, installer rail, finder list/grid, accounting Debit/Credit, screenplay
element row, packages list rows): a pick-one row of plain Gtk.Buttons is made a
row of Gtk.ToggleButtons/RadioButtons for accessibility, and the row's setter —
called from the row's own "clicked" handler — restates the row with set_active,
which emits "clicked" again. The result is a RecursionError that GTK swallows
at the handler boundary: the process prints a traceback (or hundreds), exits 0,
and construct_all_host says "0 crashed". On the appliance the window dies.

Reading code cannot find these reliably (the setter is often one or two calls
away from the handler). This gate constructs every shipped app on the host
display, walks its widget tree and FLIPS every ToggleButton, RadioButton,
CheckButton, Switch, ComboBox, Notebook page, SpinButton and Scale it finds
(then flips it back), pumping the main loop between, in a subprocess whose
stderr is captured. ANY traceback on stderr, any RecursionError, a window that
is gone afterwards, or a subprocess that dies or hangs is a failure — the flip
itself is never wrong; the app's reaction to it can be.

Apps whose toggles act on the HOST rather than the process are excluded:
settings (xrandr/setxkbmap/amixer on :0), usbwriter, burner, installer,
gbaemu, login, firstrun. Their rows are covered by their own suites.

    tools/guestrun.sh python3 tools/toggle_fuzz_check.py            # all
    tools/guestrun.sh python3 tools/toggle_fuzz_check.py accounting # one
"""
import os
import re
import subprocess
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
sys.path.insert(0, DE)

# Apps whose toggles reach the HOST are still covered here: a stub PATH (see
# run_one) neutralises the commands they shell out to (xrandr, setxkbmap,
# amixer, cdrecord, ...), so flipping a display/sound/burn toggle changes
# nothing on the developer's machine while the re-entrancy that the segmented
# rows can hide is still exercised. Only the few that manipulate real block
# devices or authentication stay out — a stub cannot make those safe AND
# meaningful, and they carry their own suites.
HOST_ACTING = {"usbwriter", "login", "firstrun"}

# Commands a shipped app may shell out to from a toggle handler. Each is
# shadowed by a no-op on the child's PATH so the fuzz cannot change the host.
_STUB_CMDS = ("xrandr", "setxkbmap", "xset", "xsetroot", "amixer", "alsactl",
              "pactl", "cdrecord", "wodim", "growisofs", "xorriso", "genisoimage",
              "mkisofs", "dd", "mkfs.fat", "mkfs.vfat", "eject", "udisksctl",
              "mount", "umount", "sync", "hdparm", "smartctl", "blockdev",
              "systemctl", "rfkill", "brightnessctl", "gsettings")

CHILD = r'''
import os, sys, tempfile, faulthandler, importlib, inspect, traceback
sys.setrecursionlimit(300)
faulthandler.dump_traceback_later(150, exit=True)
mod = sys.argv[1]
sys.path.insert(0, sys.argv[2])
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
def pump(n=3):
    for _ in range(n):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
m = importlib.import_module(mod)
cls = None
for name, obj in inspect.getmembers(m, inspect.isclass):
    if obj.__module__ == m.__name__ and issubclass(obj, Gtk.Window):
        cls = obj; break
if cls is None:
    print("NOCLASS"); sys.exit(0)
try:
    win = cls()
except Exception:
    print("CONSTRUCT-FAIL"); traceback.print_exc(); sys.exit(0)
try:
    win.show_all()
except Exception:
    traceback.print_exc()
pump(10)
def walk(w, out):
    out.append(w)
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            walk(c, out)
ws = []; walk(win, ws)
def alive():
    return win in Gtk.Window.list_toplevels() and win.get_visible()
def name(w):
    lab = ""
    try:
        if isinstance(w, Gtk.Button) and w.get_label():
            lab = w.get_label()
        elif w.get_tooltip_text():
            lab = w.get_tooltip_text()
    except Exception:
        pass
    return "%s(%s)" % (type(w).__name__, lab[:30])
n = 0; issues = []
for w in ws:
    if not alive():
        issues.append("WINDOW GONE before %s" % name(w)); break
    try:
        if isinstance(w, Gtk.ToggleButton) and w.get_sensitive():
            a = w.get_active(); w.set_active(not a); pump(); w.set_active(a); pump(); n += 1
        elif isinstance(w, Gtk.Switch) and w.get_sensitive():
            a = w.get_active(); w.set_active(not a); pump(); w.set_active(a); pump(); n += 1
        elif isinstance(w, Gtk.ComboBox) and w.get_sensitive():
            a = w.get_active(); model = w.get_model(); cnt = len(model) if model else 0
            for i in range(min(cnt, 6)):
                w.set_active(i); pump()
            w.set_active(a); pump(); n += 1
        elif isinstance(w, Gtk.Notebook):
            a = w.get_current_page()
            for i in range(w.get_n_pages()):
                w.set_current_page(i); pump()
            w.set_current_page(a); pump(); n += 1
        elif isinstance(w, (Gtk.SpinButton, Gtk.Scale)) and w.get_sensitive():
            adj = w.get_adjustment(); v = w.get_value()
            w.set_value(adj.get_upper()); pump(); w.set_value(adj.get_lower()); pump()
            w.set_value(v); pump(); n += 1
    except RecursionError:
        issues.append("RECURSION on %s" % name(w)); break
    except Exception as e:
        issues.append("EXC on %s: %r" % (name(w), e))
if not alive():
    issues.append("WINDOW GONE at end")
print("FUZZ %s: %d widgets driven; %s" % (mod, n, "; ".join(issues) if issues else "clean"))
try:
    win.destroy(); pump()
except Exception:
    traceback.print_exc()
os._exit(0)
'''


def shipped_apps():
    import finder
    return sorted({m for n, m in finder.APP_MODULES.items()
                   if n not in finder.HIDDEN_APPS} | {"finder"})


def _stub_bin():
    """A PATH dir where every host-mutating command is a silent no-op, so a
    flipped display/sound/burn toggle cannot touch the developer's machine."""
    d = tempfile.mkdtemp(prefix="nbtogglefuzz-stub-")
    for name in _STUB_CMDS:
        pth = os.path.join(d, name)
        with open(pth, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(pth, 0o755)
    return d


def run_one(mod):
    home = tempfile.mkdtemp(prefix="nbh-togglefuzz-")
    for d in ("Documents", "Music", "Pictures", "Movies", "Desktop"):
        os.makedirs(os.path.join(home, d), exist_ok=True)
    stub = _stub_bin()
    env = dict(os.environ, NB_HOME=home,
               PATH=stub + os.pathsep + os.environ.get("PATH", ""))
    env.pop("PYTHONPATH", None)
    try:
        p = subprocess.run([sys.executable, "-u", "-c", CHILD, mod, DE],
                           env=env, capture_output=True, text=True,
                           timeout=200)
        out = p.stdout + p.stderr
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "") + "\nTIMEOUT"
        rc = -1
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(stub, ignore_errors=True)
    return rc, out


def main(argv):
    apps = [a for a in argv if a] or [a for a in shipped_apps()
                                       if a not in HOST_ACTING]
    fails = []
    for mod in apps:
        rc, out = run_one(mod)
        summary = ""
        m = re.search(r"^FUZZ .*$", out, re.M)
        if m:
            summary = m.group(0)
        tracebacks = len(re.findall(r"^Traceback \(most recent call last\)",
                                    out, re.M))
        recursions = out.count("RecursionError")
        bad = (rc != 0 or tracebacks or recursions or "TIMEOUT" in out
               or "CONSTRUCT-FAIL" in out or "NOCLASS" in out
               or not summary or "clean" not in summary)
        line = "%s %-14s %s%s" % ("FAIL" if bad else "ok  ", mod,
                                  summary or ("(no summary; rc=%d)" % rc),
                                  ("  [%d traceback(s), %d RecursionError]"
                                   % (tracebacks, recursions))
                                  if (tracebacks or recursions) else "")
        print(line, flush=True)
        if bad:
            fails.append(mod)
            # show the first traceback so the failure is actionable
            idx = out.find("Traceback (most recent call last)")
            if idx >= 0:
                print("      " + out[idx:idx + 1200].replace("\n", "\n      "))
    print("\nTOGGLE-FUZZ: %d apps, %d failing%s"
          % (len(apps), len(fails), (": " + ", ".join(fails)) if fails else ""))
    # Terminal verdict for the release runner (run_all_gates SUCCESSWORD):
    # a count is a work report, not an outcome.
    print("RESULT: %s" % ("ALL PASS" if not fails else "FAILED"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
