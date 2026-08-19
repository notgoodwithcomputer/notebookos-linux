#!/usr/bin/env python3
"""Adversarial, device-safe checks for USB Writer's erase gates."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="usbwriter-audit-")

import usbwriter  # noqa: E402

failed = []
checks = []


def check(name, condition):
    checks.append(name)
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failed.append(name)


def fixture_drives(system=frozenset(), usb=frozenset(("sdb",)),
                   sizes=None, removable=frozenset()):
    sizes = sizes or {"nvme0n1": "2000000", "sdb": "16"}
    old = usbwriter._listdir, usbwriter._read, usbwriter._is_usb
    usbwriter._listdir = lambda p: sorted(sizes) if p == "/sys/block" else []
    usbwriter._read = lambda p, default="": (
        "1" if p.endswith("/removable") and p.split("/")[-2] in removable
        else "SERIAL-" + p.split("/")[3] if p.endswith("/device/serial")
        else sizes.get(p.split("/")[-2], default) if p.endswith("/size")
        else default)
    usbwriter._is_usb = lambda name: name in usb
    old_system = usbwriter._system_disks
    usbwriter._system_disks = lambda: set(system)
    try:
        return usbwriter._drives()
    finally:
        usbwriter._listdir, usbwriter._read, usbwriter._is_usb = old
        usbwriter._system_disks = old_system


# NVMe root and a boot device exposed through USB are absent, irrespective of
# the kernel's removable bit.
offered = fixture_drives(system={"nvme0n1"}, usb={"sdb"},
                         removable={"nvme0n1"})
check("SYSTEM-NVME-ROOT-NEVER-OFFERED",
      {d["name"] for d in offered} == {"sdb"})
offered = fixture_drives(system={"sdb"}, usb={"sdb"})
check("SYSTEM-USB-BOOT-NEVER-OFFERED", not offered)

# PASS-MUTANT: removable is not USB provenance. This deliberately broken
# selector offers an internal/system-looking removable disk, and the check
# must catch it.
mutant = fixture_drives(system=set(), usb={"nvme0n1"},
                        removable={"nvme0n1"})
check("PASS-MUTANT-REMOVABLE-GUARD-CAN-GO-RED",
      any(d["name"] == "nvme0n1" for d in mutant))

# PASS-MUTANT: prove omitting _system_disks changes the safety result.
mutant = fixture_drives(system=set(), usb={"sdb"})
check("PASS-MUTANT-SYSTEM-GUARD-CAN-GO-RED",
      any(d["name"] == "sdb" for d in mutant))

# Missing/empty size nodes are fail-closed.
check("EMPTY-SYSFS-SIZE-NEVER-OFFERED",
      not fixture_drives(usb={"sdb"}, sizes={"sdb": ""}))

class PulledStick:
    def write(self, _data):
        raise OSError(6, "device gone")
try:
    usbwriter._write_all(PulledStick(), b"payload")
    pulled_honest = False
except OSError:
    pulled_honest = True
check("PULLED-STICK-MID-WRITE-SURFACES-FAILURE", pulled_honest)

# Exact capacity is valid; one sector beyond is not. The remainder is counted,
# not rounded down to sectors.
image_fits = getattr(usbwriter, "_image_fits", lambda _image, _drive: False)
check("EXACT-SIZE-IMAGE-FITS", image_fits(4096, 4096))
check("512-BYTE-REMAINDER-REFUSED", not image_fits(4608, 4096))

# The confirmed snapshot must still name an offered USB target immediately
# before open. No block node is opened by this suite.
d = {"name": "sdb", "node": "/dev/sdb", "bytes": 8192,
     "identity": "device/serial:SERIAL-sdb"}
old_drives = usbwriter._drives
try:
    usbwriter._drives = lambda: []
    check("DISAPPEARED-AFTER-CONFIRM-REFUSED-BEFORE-OPEN",
          not getattr(usbwriter, "_target_still_safe", lambda _d: True)(d))
    usbwriter._drives = lambda: [dict(d)]
    check("UNCHANGED-TARGET-STILL-SAFE",
          getattr(usbwriter, "_target_still_safe", lambda _d: False)(d))
    replacement = dict(d, identity="device/serial:DIFFERENT-STICK")
    usbwriter._drives = lambda: [replacement]
    check("SAME-NODE-SAME-SIZE-REPLACEMENT-REFUSED",
          not usbwriter._target_still_safe(d))
    no_identity = {k: v for k, v in d.items() if k != "identity"}
    usbwriter._drives = lambda: [dict(no_identity)]
    check("TARGET-WITHOUT-STABLE-IDENTITY-REFUSED",
          not usbwriter._target_still_safe(no_identity))
finally:
    usbwriter._drives = old_drives

print("%d checks, %d failed" % (len(checks), len(failed)))
print("RESULT: %s" % ("FAILED" if failed else "PASS"))
raise SystemExit(bool(failed))
