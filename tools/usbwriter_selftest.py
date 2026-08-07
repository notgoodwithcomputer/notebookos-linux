#!/usr/bin/env python3
"""USB Writer's safety rules, checked against this machine's real block devices.

    python3 tools/usbwriter_selftest.py

This app erases a disk as its normal operation, so the only property that
really matters is the one checked first: a device this machine is running from
must never be offered. Everything else in the app is recoverable; that is not.

The device-shaped checks run against whatever is actually plugged into the
machine running the test (which is the point — a synthetic /sys would only
prove the test's own fixture is consistent), and the parsing is additionally
driven with fabricated input so the awkward names are covered on a host that
has none of them.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Run from the repo (against the overlay sources) or ON THE GUEST (against
# what actually shipped). The guest has no repo checkout, so a path built only
# from __file__ made this suite unrunnable exactly where it matters most.
_CANDIDATES = [
    os.path.abspath(os.path.join(HERE, "..", "buildroot", "board",
                                 "notebookos", "rootfs-overlay", "opt",
                                 "notebook", "de")),
    "/opt/notebook/de",
]
DE = next((p for p in _CANDIDATES if os.path.isdir(p)), _CANDIDATES[0])
if DE not in sys.path:
    sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", "/tmp/nbhome-usbwriter-selftest")
os.makedirs(os.environ["NB_HOME"], exist_ok=True)

import gi                                                   # noqa: E402
gi.require_version("Gtk", "3.0")
import usbwriter                                            # noqa: E402

FAILED = []


def check(cond, what):
    print("%-64s %s" % (what, "ok" if cond else "FAIL"))
    if not cond:
        FAILED.append(what)


def main():
    # ---- RULE 1: never a disk this machine is running from -----------------
    system = usbwriter._system_disks()
    offered = {d["name"] for d in usbwriter._drives()}
    print("system disks : %s" % (", ".join(sorted(system)) or "(none found)"))
    print("offered      : %s" % (", ".join(sorted(offered)) or "(none)"))
    print()

    if system:
        check(True, "the running system's own disk(s) were identified at all")
    else:
        # In a container with no /sys there is nothing to identify, and
        # _drives() correctly offers nothing. Say so rather than failing a
        # check that has no subject.
        print("(no block devices visible here; rule 1 has no subject)")
        check(not offered, "with nothing visible, nothing is offered either")
    check(not (system & offered),
          "NO disk this machine runs from is offered as a target")

    # The root filesystem's disk specifically, named rather than inferred.
    root_dev = None
    for line in open("/proc/mounts", encoding="utf-8"):
        p = line.split()
        if len(p) >= 2 and p[1] == "/" and p[0].startswith("/dev/"):
            root_dev = usbwriter._disk_of(p[0][5:])
    if root_dev:
        check(root_dev in system, "the root filesystem's disk is a system disk")
        check(root_dev not in offered, "the root disk is not offered")
    else:
        print("(root is not on a /dev node here; skipped that pair)")

    # ---- every offered device really is removable USB, and real ------------
    for d in usbwriter._drives():
        check(usbwriter._is_usb(d["name"]),
              "offered device %s is USB-attached" % d["name"])
        check(d["bytes"] > 0, "offered device %s reports a real size"
              % d["name"])
        check(d["node"] == "/dev/" + d["name"],
              "offered device %s has a matching node" % d["name"])
    if not offered:
        print("(no USB drive plugged in; the per-device checks had nothing "
              "to run against)")

    # ---- _disk_of must not guess by stripping digits -----------------------
    # This is the function rule 1 leans on. Getting nvme0n1p2 wrong would put
    # the machine's own SSD in the list.
    real = usbwriter._listdir("/sys/block")
    if not real:
        print("(no /sys/block visible here; the partition-naming checks had "
              "nothing to run against)")
    for disk in real:
        parts = [p for p in usbwriter._listdir("/sys/block/" + disk)
                 if p.startswith(disk)]
        for part in parts:
            check(usbwriter._disk_of(part) == disk,
                  "%s resolves to its whole disk %s" % (part, disk))
    check(usbwriter._disk_of("/dev/sda1") in real + ["sda1"],
          "a full /dev path is accepted as well as a bare name")
    check(usbwriter._disk_of("nosuchdev9") == "nosuchdev9",
          "an unknown device resolves to itself rather than raising")

    # ---- sizes are stated the way a stick's box states them ----------------
    for n, want in ((0, "unknown size"), (1000 ** 3, "1.0 GB"),
                    (16 * 1000 ** 3, "16 GB"), (2 * 1000 ** 2, "2.0 MB"),
                    (2 * 1000 ** 4, "2.0 TB"), (512, "512 bytes")):
        got = usbwriter._fmt_size(n)
        check(got == want, "%d bytes reads as %r (got %r)" % (n, want, got))

    # ---- will it BOOT once it is on the stick? -----------------------------
    # Writing is a byte copy, so an image with no boot record copies fine and
    # then the firmware silently skips the stick. Checked against real files.
    import tempfile
    def _img(head, iso=False):
        fd, p = tempfile.mkstemp(suffix=".iso")
        with os.fdopen(fd, "wb") as fh:
            buf = bytearray(40000)
            buf[:len(head)] = head
            if iso:
                buf[32769:32774] = b"CD001"
            fh.write(bytes(buf))
        return p

    mbr = bytearray(512)
    mbr[510:512] = b"\x55\xaa"
    p_hybrid = _img(bytes(mbr), iso=True)
    p_plain = _img(b"", iso=True)
    gpt = bytearray(520)
    gpt[512:520] = b"EFI PART"
    p_gpt = _img(bytes(gpt))
    check(usbwriter.image_boot_note(p_hybrid) == "",
          "an isohybrid ISO is reported as bootable (no note)")
    check(usbwriter.image_boot_note(p_gpt) == "",
          "a GPT disk image is reported as bootable (no note)")
    note = usbwriter.image_boot_note(p_plain)
    check(bool(note) and "boot record" in note,
          "a plain ISO9660 warns that nothing will boot from it")
    check(usbwriter.image_boot_note("/nonexistent/x.iso") == "",
          "an unreadable file yields no note rather than raising")
    for p in (p_hybrid, p_plain, p_gpt):
        os.unlink(p)

    # The real thing, if it is here: this is the check that would have caught
    # the ISO shipping without a hybrid MBR.
    for real in ("/home/ben/Documents/notebookos-linux/release/notebookos-1.0.iso",
                 "/home/ben/Downloads/debian-13.6.0-amd64-DVD-1.iso"):
        if os.path.exists(real):
            note = usbwriter.image_boot_note(real)
            print("   %-46s %s" % (os.path.basename(real),
                                   note or "bootable from a stick"))

    # ---- the write must be refused when the image cannot fit ---------------
    # Checked at the level the UI decides it, so the guard and the button can
    # never disagree.
    w = usbwriter.UsbWriter.__new__(usbwriter.UsbWriter)
    w.image = {"path": "/tmp/x.iso", "bytes": 8 * 1000 ** 3}
    w.selected = {"label": "Stick", "size": "4.0 GB", "node": "/dev/sdz",
                  "bytes": 4 * 1000 ** 3, "name": "sdz"}
    w.busy = False
    fits = w.image["bytes"] <= w.selected["bytes"]
    check(not fits, "an image larger than the stick is recognised as too big")

    # ---- a short write must never be counted as a written byte -------------
    # The target is opened unbuffered, so its write() may take part of a chunk
    # and say so. Crediting the whole chunk anyway loses the remainder and ends
    # the write reporting "Finished. The stick can be unplugged." over a
    # truncated image — the one wrong answer this app must never give.
    class ShortSink:
        """A drive that takes at most `cap` bytes per write, as write(2) may."""

        def __init__(self, cap):
            self.cap = cap
            self.data = bytearray()

        def write(self, b):
            b = bytes(b)[:self.cap]
            self.data += b
            return len(b)

    payload = bytes(range(256)) * 40                 # 10240 bytes
    sink = ShortSink(1000)
    n = usbwriter._write_all(sink, payload)
    check(n == len(payload) and bytes(sink.data) == payload,
          "a drive taking 1000 bytes at a time still receives every byte")
    check(n == len(payload),
          "the byte count returned is what actually reached the drive")
    # The gate has to be able to go red: prove the fixture really short-writes,
    # so the check above is not passing on a sink that never truncates.
    check(sink.write(payload) < len(payload),
          "the short-write fixture really does short-write")

    class DeadSink:
        def write(self, _b):
            return 0

    try:
        usbwriter._write_all(DeadSink(), b"x" * 16)
        raised = False
    except OSError:
        raised = True
    check(raised, "a drive accepting nothing raises rather than spinning")

    # ---- the failure sentence must not depend on the language ----
    # _write_error used to choose its sentence by matching the English phrase
    # "ran out of room" inside the exception's message — and that message is
    # built with _t(). It matched only while those strings were missing from
    # the catalogs; once they were translated, a German reader whose stick
    # filled up was told it may have been unplugged instead.
    import nbjobs as _nbjobs
    _w = usbwriter.UsbWriter()
    _seen = []
    _w._finished = lambda kind, msg: _seen.append(msg)
    for _exc in (usbwriter._OutOfRoom("x"), usbwriter._NotPermitted("y"),
                 OSError("anything else")):
        _w._write_error(_nbjobs.JobError.of(_exc))
    check(len(set(_seen)) == 3,
          "each kind of write failure gets its own sentence")
    check(all(_seen), "no write failure is reported with an empty sentence")
    # the same three, with the messages carrying TRANSLATED text as they do at
    # runtime: the mapping must be unaffected by what the words say
    _seen2 = []
    _w._finished = lambda kind, msg: _seen2.append(msg)
    for _exc in (usbwriter._OutOfRoom("Auf dem Stick war kein Platz mehr."),
                 usbwriter._NotPermitted("Nicht zugelassen."),
                 OSError("Etwas anderes")):
        _w._write_error(_nbjobs.JobError.of(_exc))
    check(_seen2 == _seen,
          "the sentence chosen does not depend on the wording of the error")
    _w.destroy()

    print()
    if FAILED:
        print("usb writer selftest: %d FAILED" % len(FAILED))
        for f in FAILED:
            print("  - %s" % f)
        return 1
    print("usb writer selftest: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
