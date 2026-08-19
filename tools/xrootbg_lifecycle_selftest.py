#!/usr/bin/env python3
"""A wallpaper handoff releases the prior retained X pixmap owner."""
import ctypes
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)
import xrootbg  # noqa: E402


class FakeX:
    def __init__(self):
        self.old = ctypes.c_ulong(41)
        self.events = []

    def __getattr__(self, name):
        def call(*args):
            self.events.append(name)
            return 0
        return call

    def XOpenDisplay(self, _name): return 1
    def XDefaultRootWindow(self, _dpy): return 2
    def XDefaultScreen(self, _dpy): return 0
    def XDefaultDepth(self, _dpy, _screen): return 24
    def XInternAtom(self, _dpy, name, _only): return 3 if name else 0
    def XCreatePixmap(self, *_args): return 99
    def XCreateGC(self, *_args): return 4

    def XGetWindowProperty(self, *_args):
        _args[-5]._obj.value = xrootbg.XA_PIXMAP
        _args[-4]._obj.value = 32
        _args[-3]._obj.value = 1
        _args[-2]._obj.value = 0
        _args[-1]._obj.value = ctypes.addressof(self.old)
        self.events.append("get-old")
        return 0

    def XKillClient(self, _dpy, resource):
        self.events.append(("kill", resource))

    def XCloseDisplay(self, _dpy): self.events.append("close")


real = xrootbg._x11
fake = FakeX()
xrootbg._x11 = fake
try:
    assert xrootbg.set_root_background("#123456")
finally:
    xrootbg._x11 = real

assert ("kill", 41) in fake.events, fake.events
assert fake.events.index(("kill", 41)) > fake.events.index("XChangeProperty")
assert fake.events[-1] == "close", fake.events


class NoGC(FakeX):
    def XCreateGC(self, *_args):
        self.events.append("no-gc")
        return 0


failed = NoGC()
xrootbg._x11 = failed
try:
    assert xrootbg.set_root_background("#123456") is False
finally:
    xrootbg._x11 = real
assert "XSetForeground" not in failed.events, failed.events
assert failed.events[-1] == "close", failed.events
print("XROOTBG LIFECYCLE SELFTEST: 6 checks, all pass")
