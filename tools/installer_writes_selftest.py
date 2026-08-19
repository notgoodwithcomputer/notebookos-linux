#!/usr/bin/env python3
"""
What the installer WRITES into the installed system.

The campaign lists the installer's destructive half among the untouched
surfaces, and it stays untouched here: nothing in this suite partitions,
formats or mounts anything. What it does test is the other half — the config
writers — by pointing them at an ordinary directory standing in for the new
root and reading back the files they leave behind.

That covers three ROADMAP entries, all of which describe a choice the user made
being silently dropped:

  #26  swap partitioned and formatted, then never switched on, because nothing
       added it to /etc/fstab and inittab's `swapon -a` reads fstab.
  #27  keyboard and language discarded on first boot, because the desktop reads
       locale.json out of $NB_HOME and the installer never wrote one.
  #25  a "Login account" written to /etc/passwd and never used, since the
       session pins NB_HOME=/root and runs as the administrator.

All three are fixed. This is what keeps them fixed — and each is checked by
reading the ARTEFACT, because "the function exists" is not the same claim as
"the file says the right thing".

Run:
    tools/guestrun.sh python3 tools/installer_writes_selftest.py
    tools/guestrun.sh python3 tools/installer_writes_selftest.py --de DIR
"""
import os
import ast
import re
import sys
import json
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-inst-")
os.environ["NB_HOME"] = _HOME

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(os.path.dirname(HERE), "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import installer  # noqa: E402

FAILED, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def fake_root():
    """A directory shaped like a freshly extracted rootfs. Only the files the
    writers touch need to exist; the shipped fstab has no swap line, which is
    the starting state the entries describe."""
    root = tempfile.mkdtemp(prefix="nb-root-", dir=_HOME)
    os.makedirs(os.path.join(root, "etc"), exist_ok=True)
    os.makedirs(os.path.join(root, "root"), exist_ok=True)
    with open(os.path.join(root, "etc", "fstab"), "w") as fh:
        fh.write("# shipped fstab\n"
                 "proc\t/proc\tproc\tdefaults\t0\t0\n")
    with open(os.path.join(root, "etc", "profile"), "w") as fh:
        fh.write("# shipped profile\n")
    return root


def blank_installer():
    """An Installer with its config set, without building the window: the
    writers are plain methods and constructing the whole wizard would drag in
    the disk scan."""
    app = installer.Installer.__new__(installer.Installer)
    app.cfg = {"swap": True, "swap_mib": 512, "locale": 0, "kbd": 0,
               "password": "hunter2", "root_passwordless": False,
               "oem": False, "username": "", "hostname": "notebook"}
    app._post_log = lambda *a, **k: None
    return app


def main():
    # ---- #26: swap reaches fstab -------------------------------------
    app = blank_installer()
    root = fake_root()
    source_release = os.path.join(_HOME, "live-os-release")
    with open(source_release, "w") as fh:
        fh.write('NAME="Notebook OS"\nBUILD_ID="2026-08-15"\n')
    old_release = installer.OS_RELEASE_SOURCE
    installer.OS_RELEASE_SOURCE = source_release
    try:
        app._write_os_release(root)
    finally:
        installer.OS_RELEASE_SOURCE = old_release
    installed_release = open(os.path.join(root, "etc", "os-release")).read()
    check("installed system retains the image build stamp",
          'BUILD_ID="2026-08-15"' in installed_release,
          installed_release.replace("\n", " | "))

    app._configure_fstab(root)
    fstab = open(os.path.join(root, "etc", "fstab")).read()
    got = check("a swap line is added to /etc/fstab",
                re.search(r"^\s*(LABEL=\S+|UUID=\S+|/dev/\S+)\s+(swap|none)\s+swap",
                          fstab, re.M | re.I) is not None,
                repr(fstab.splitlines()[-1] if fstab else ""))
    if got:
        # By LABEL, not a device path: /dev/sda2 becomes /dev/sdb2 the day a
        # second disk is plugged in, and swapon then finds nothing.
        check("...by LABEL, so a second disk cannot break it",
              "LABEL=" in fstab, repr([l for l in fstab.splitlines()
                                       if "swap" in l.lower()]))
        check("...and the shipped entries are left alone", "proc" in fstab)
    else:
        not_reached("no swap line", "...by LABEL, so a second disk cannot break it",
                    "...and the shipped entries are left alone")

    # and with swap declined, nothing is written
    app2 = blank_installer()
    app2.cfg["swap"] = False
    root2 = fake_root()
    app2._configure_fstab(root2)
    check("declining swap writes no swap line",
          "swap" not in open(os.path.join(root2, "etc", "fstab")).read().lower())

    # ---- #27: the keyboard choice reaches the desktop ----------------
    root3 = fake_root()
    app3 = blank_installer()
    app3._write_locale_json(root3, "fr")
    lj = os.path.join(root3, "root", ".config", "notebook", "locale.json")
    wrote = check("locale.json is written where the desktop reads it",
                  os.path.exists(lj), lj.replace(root3, "<root>"))
    if wrote:
        data = json.load(open(lj))
        check("...and carries the chosen keyboard",
              data.get("keyboard") == "fr", repr(data))
        check("...and a language", bool(data.get("lang")), repr(data))
        # $NB_HOME is /root on this appliance; a locale.json under /home/<user>
        # would be read by nobody.
        check("...under /root, which is where NB_HOME points",
              "/root/" in lj.replace(root3, ""))
    else:
        not_reached("no locale.json", "...and carries the chosen keyboard",
                    "...and a language",
                    "...under /root, which is where NB_HOME points")

    # ---- #25: no unused account is created ---------------------------
    check("the installer has no _create_user at all",
          not hasattr(installer.Installer, "_create_user"))
    src = open(os.path.join(DE, "installer.py"), encoding="utf-8").read()
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    check("...and nothing writes /etc/passwd or /etc/shadow for a second user",
          "useradd" not in code and "/etc/passwd" not in code)
    check("the password still guards the machine (_configure_login exists)",
          hasattr(installer.Installer, "_configure_login"))

    # Final writeback is an installation commit boundary.  Optional map packs
    # may fail best-effort, but sync and target unmounts must not be hidden by
    # allow_fail before the worker publishes Complete.
    tree = ast.parse(src)
    do_install = next(n for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and n.name == "_do_install")
    # Anchored on the "Finishing up" phase itself, not on a line number. This
    # check used to name line 2385, so every line added ABOVE it in the file
    # silently slid the window it was measuring off the calls it was about --
    # a green check measuring nothing at all, which is the shape a gate goes
    # blind in most often.
    finish = [n for n in ast.walk(do_install)
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute)
              and n.func.attr == "_phase"
              and n.args and isinstance(n.args[0], ast.Constant)
              and n.args[0].value == 0.97]
    anchored = check("the install engine still has a 'Finishing up' phase to "
                     "measure from", bool(finish))
    after = finish[0].lineno if finish else 0
    final_sh = [n for n in ast.walk(do_install)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "_sh"
                and n.lineno >= after]
    if anchored:
        check("final sync and target unmounts are not best-effort",
              len(final_sh) >= 3 and all(
                  not any(k.arg == "allow_fail"
                          and isinstance(k.value, ast.Constant)
                          and k.value.value is True for k in call.keywords)
                  for call in final_sh[:3]))
    else:
        not_reached("no anchor phase",
                    "final sync and target unmounts are not best-effort")

    # ---- #3: the disk is refused BEFORE anything is erased -----------
    # "Erases the disk before checking it fits": wipefs, sgdisk -Z and mkfs all
    # run before the tar is extracted, so a disk too small was discovered with
    # the user's files already gone. The payload is measured up front now and
    # the disk is refused on the screen where it is chosen.
    app4 = blank_installer()
    app4.payload_bytes = 2 * 1024 ** 3          # a 2 GB system
    need = app4._min_disk_bytes(512)
    sized = check("the installer knows how big a disk it needs (%s)"
                  % (need and "%.1f GB" % (need / 1024.0 ** 3)), need > 0)
    if sized:
        check("a disk smaller than the payload is refused",
              app4._disk_too_small(1 * 1024 ** 3, 512))
        check("a disk with room is accepted",
              not app4._disk_too_small(64 * 1024 ** 3, 512))
        # Swap is chosen AFTER the disk, so raising it can push a disk that
        # fitted a moment ago past what it can hold.
        just = need + 64 * 1024 ** 2
        check("more swap can turn an acceptable disk into a refused one",
              (not app4._disk_too_small(just, 512))
              and app4._disk_too_small(just, 512 + 8192),
              "%.2f GB disk" % (just / 1024.0 ** 3))
    else:
        not_reached("no payload size", "a disk smaller than the payload is refused",
                    "a disk with room is accepted",
                    "more swap can turn an acceptable disk into a refused one")

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
sys.exit(rc)
