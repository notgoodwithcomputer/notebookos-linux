#!/usr/bin/env python3
"""Selftest for the bundled map pack — the ISO carries it, both sides read it.

A continent-sized .nbm2 rides INSIDE the ISO as a plain file at /maps rather
than in the rootfs overlay, because the ISO stores that root twice (the live
squashfs and the install tarball) and 2.7 GB doubled is 5.4 GB. That buys the
size back but splits the reading into two places, and NEITHER is cheap to test
by hand: one needs a booted live session, the other needs a real installation
onto a real disk. So they are driven here instead.

  de/maps.py            must scan the mounted medium, and must prefer a copy
                        already on the machine's own disk over the same pack on
                        what might be a DVD.
  de/installer.py       must copy the packs onto the installed system, and must
                        never turn a problem doing so into a failed install —
                        it runs after the system is already complete.

Run as:
  DISPLAY=:0 PYTHONPATH=<overlay>/opt/notebook/de python3 maps_bundle_selftest.py
"""
import collections
import os
import shutil
import sys
import tempfile

# The shape shutil.disk_usage returns, so the tight-disk case can stand in for
# a full one without needing a full disk.
_Usage = collections.namedtuple("_Usage", "total used free")

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                             # noqa: E402,F401

import maps                                               # noqa: E402
import installer                                          # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "   <- " + str(detail)))


def write(path, data=b"nbm2 payload"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


class FakeInstall:
    """Just enough Installer to drive the copy: the three reporting calls it
    makes, plus the real _copy_chunked bound to this object, so what runs is
    the shipped code and not a re-implementation of it."""

    def __init__(self):
        self.log = []
        self.progress = []
        self._copy_chunked = installer.Installer._copy_chunked.__get__(self)

    def _phase(self, frac, status):
        self.progress.append(frac)
        self.log.append("== %s ==" % status)

    def _post_log(self, text):
        self.log.append(text)

    def _post_progress(self, frac, status=None):
        self.progress.append(frac)

    def text(self):
        return "\n".join(self.log)


# ---------------------------------------------------------------- maps.py ---
tmp = tempfile.mkdtemp(prefix="nbmapbundle.")
try:
    bundled, home, data, medium = (os.path.join(tmp, d) for d in
                                   ("opt", "home", "data", "medium"))
    for d in (bundled, home, data, medium):
        os.makedirs(d, exist_ok=True)
    write(os.path.join(bundled, "monaco.nbm2"))
    write(os.path.join(medium, "north-america.nbm2"), b"from the medium")

    maps.MAPS_DIR = bundled
    maps.LIVE_MAPS_DIR = medium
    os.environ["NB_HOME"] = home

    # _scan_maps reads module constants and the environment, no instance state.
    found = dict(maps.Maps._scan_maps(None))
    check("a pack on the live medium is offered by Maps",
          "north-america" in found, sorted(found))
    check("the bundled default is still offered", "monaco" in found,
          sorted(found))
    check("the medium pack resolves to the medium",
          found.get("north-america", "").startswith(medium), found)

    # The same pack copied onto the machine must win: the medium may be a DVD.
    write(os.path.join(data, "north-america.nbm2"), b"from the disk")
    saved_scan_dirs = maps.LIVE_MAPS_DIR
    real_data = "/data/maps"
    # /data/maps is hard-coded in _scan_maps; stand in for it by pointing the
    # home directory at the local copy, which is scanned ahead of the medium
    # for the same reason and exercises the same first-wins rule.
    write(os.path.join(home, "maps", "north-america.nbm2"), b"from the disk")
    found2 = dict(maps.Maps._scan_maps(None))
    check("a local copy is preferred over the same pack on the medium",
          found2.get("north-america", "").startswith(home), found2)
    maps.LIVE_MAPS_DIR = saved_scan_dirs
    del real_data

    # ----------------------------------------------------------- installer ---
    saved_src, saved_du = installer.MAPS_SRC, shutil.disk_usage

    # A. the ordinary case: packs on the medium land on the installed system.
    src = os.path.join(tmp, "isomaps")
    write(os.path.join(src, "north-america.nbm2"), b"NBM2" + b"x" * 100000)
    write(os.path.join(src, "monaco.nbm2"), b"NBM2" + b"y" * 500)
    write(os.path.join(src, "notes.txt"), b"not a pack")
    installer.MAPS_SRC = src
    target = os.path.join(tmp, "target")
    os.makedirs(target, exist_ok=True)
    inst = FakeInstall()
    installer.Installer._copy_map_packs(inst, target)
    landed = os.path.join(target, "data", "maps")
    got = sorted(os.listdir(landed)) if os.path.isdir(landed) else []
    check("both packs are copied onto the installed system",
          got == ["monaco.nbm2", "north-america.nbm2"], got)
    check("a non-pack file on the medium is not copied", "notes.txt" not in got,
          got)
    check("the copied pack is byte-identical",
          open(os.path.join(landed, "north-america.nbm2"), "rb").read()
          == open(os.path.join(src, "north-america.nbm2"), "rb").read())
    check("the bar moves during the copy rather than sitting still",
          len(inst.progress) >= 3 and max(inst.progress) <= 0.97,
          inst.progress)

    # B. no packs on this medium: silent, and above all not an error.
    installer.MAPS_SRC = os.path.join(tmp, "nothing-here")
    quiet = FakeInstall()
    installer.Installer._copy_map_packs(quiet, os.path.join(tmp, "target2"))
    check("a medium with no packs is a no-op, not a failure", not quiet.log,
          quiet.log)

    # C. a disk too small for an OPTIONAL map must keep its install, say so,
    #    and write nothing at all.
    installer.MAPS_SRC = src
    target3 = os.path.join(tmp, "target3")
    os.makedirs(target3, exist_ok=True)
    shutil.disk_usage = lambda p: _Usage(0, 0, 1024)   # 1 KiB free
    tight = FakeInstall()
    try:
        installer.Installer._copy_map_packs(tight, target3)
    finally:
        shutil.disk_usage = saved_du
    left = os.path.join(target3, "data", "maps")
    check("a full disk skips the maps instead of failing the install",
          "skipped" in tight.text(), tight.text())
    check("the skip says the system is still installed",
          "installed and complete" in tight.text(), tight.text())
    check("a skipped copy writes no pack at all",
          not (os.path.isdir(left) and os.listdir(left)),
          os.listdir(left) if os.path.isdir(left) else "(no dir)")

    # D. an unreadable pack: report it, preserve any installed copy, keep going.
    bad = os.path.join(tmp, "badmaps")
    os.makedirs(os.path.join(bad, "broken.nbm2"), exist_ok=True)  # a DIRECTORY
    write(os.path.join(bad, "good.nbm2"), b"NBM2" + b"z" * 2000)
    installer.MAPS_SRC = bad
    target4 = os.path.join(tmp, "target4")
    os.makedirs(os.path.join(target4, "data", "maps"), exist_ok=True)
    # An existing installed pack must survive a failed attempted replacement.
    old_pack = os.path.join(target4, "data", "maps", "broken.nbm2")
    write(old_pack, b"previous valid pack")
    broke = FakeInstall()
    installer.Installer._copy_map_packs(broke, target4)
    out4 = sorted(os.listdir(os.path.join(target4, "data", "maps")))
    check("an unreadable pack is reported, not raised",
          "could not be copied" in broke.text(), broke.text())
    check("a failed replacement preserves the existing installed pack",
          "broken.nbm2" in out4 and open(old_pack, "rb").read()
          == b"previous valid pack", out4)
    check("a failed replacement leaves no visible or hidden partial file",
          not [n for n in out4 if n.startswith(".broken.nbm2.")], out4)
    check("one bad pack does not stop the others", "good.nbm2" in out4, out4)

    installer.MAPS_SRC = saved_src
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d checks, %d passed, %d failed"
      % (len(RESULTS), sum(RESULTS), len(RESULTS) - sum(RESULTS)))
print("RESULT: " + ("ALL PASS" if all(RESULTS) else "SOME FAILED"))
sys.exit(0 if all(RESULTS) else 1)
