#!/usr/bin/env python3
"""Headless test for the Finder's whole-filesystem (absolute-path) navigation
and the real-devices sidebar."""
import os
import tempfile

# finder.HOME is read at IMPORT time and defaults to the CALLER'S REAL HOME.
# Pin a throwaway one first: without it this walks (and the sibling suites
# write into) the developer's own Documents/.Trash, and the single-instance
# guard shares the unscoped /tmp/nb-apps with any real app, which would make
# claim_single_instance() os._exit(0) mid-suite -- a silent, green "pass".
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nbfinder-fs-"))
for _d in ("Documents", "Applications", "Pictures", "Music", "Videos"):
    os.makedirs(os.path.join(os.environ["NB_HOME"], _d), exist_ok=True)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk  # noqa: E402

import finder  # noqa: E402

w = finder.Finder()
HOME = finder.HOME
ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        ok = False


def names():
    return [w.store[i][1] for i in range(len(w.store))]


# --- abspath handles both relative and absolute ---
check("abspath('/') == /", w.abspath("/") == "/")
check("abspath('/usr') == /usr", w.abspath("/usr") == "/usr")
check("abspath('Documents') under HOME",
      w.abspath("Documents") == os.path.normpath(os.path.join(HOME, "Documents")))

# --- navigate to root ---
w.load("/")
check("rel is /", w.rel == "/")
check("root lists real entries",
      "usr" in names() and "bin" in names() and "etc" in names())
check("title = COMPUTER", w.title.get_text() == "COMPUTER")
check("crumb = Computer", w.crumb.get_text() == "Computer")
check("back enabled at root (came from Applications)",
      w.back_btn.get_sensitive())

# --- descend into /usr, then up ---
w.load("/usr")
check("in /usr", w.rel == "/usr" and "bin" in names())
check("crumb shows Computer > usr", "Computer" in w.crumb.get_text() and
      "usr" in w.crumb.get_text())
w.go_up()
check("go_up /usr -> /", w.rel == "/")
w.go_up()
check("go_up at / stays /", w.rel == "/")

# --- Home go_up steps out to the absolute parent of HOME ---
w.load("")                     # Home
check("Home rel empty", w.rel == "")
w.go_up()
check("go_up from Home -> absolute parent",
      w.rel == os.path.dirname(HOME))

# --- devices list includes Local Disk -> / ---
devs = w._devices()
check("Local Disk present -> /", devs and devs[0] == ("Local Disk", "disk", "/"))
# a sidebar row for '/' exists and is registered
check("sidebar has '/' row", any(rel == "/" for rel, _row in w._sb_rows))

# --- history still works with absolute paths mixed in ---
w.load("Applications")
w.load("/")
w.load("/etc")
w.go_back()
check("back from /etc -> /", w.rel == "/")
w.go_back()
check("back -> Applications", w.rel == "Applications")
w.go_forward()
check("forward -> /", w.rel == "/")

print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
