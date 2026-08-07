#!/usr/bin/env python3
"""Printer setup logic, driven against the REAL settings.py methods.

Run on the guest (or in a chroot of the shipped rootfs):

    NB_HOME=/root python3 /opt/notebook/tools/printer_selftest.py

The interesting behaviour here is all in how a device list turns into a queue,
and getting it wrong is invisible: a queue builds fine, every filter succeeds,
and the printer silently discards the job. So this drives the actual Settings
methods with canned `lpinfo` output rather than reimplementing them — a copy of
the logic would pass while the shipped code was broken.
"""

import os
import sys
import threading
import time
import types

DE = "/opt/notebook/de"
if not os.path.isdir(DE):
    # Run straight from a checkout as well: same modules, no guest needed.
    DE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", "/root")

import gi                                                    # noqa: E402
gi.require_version("Gtk", "3.0")
import settings                                              # noqa: E402

FAILED = []


def check(cond, what):
    print("%-64s %s" % (what, "ok" if cond else "FAIL"))
    if not cond:
        FAILED.append(what)


def fresh():
    """A Settings object with no __init__ run — only the printer methods are
    under test and they touch nothing else."""
    return settings.Settings.__new__(settings.Settings)


# `lpinfo -l -v` as CUPS prints it when a driverless printer is plugged in: the
# stock usb backend sees its printer port and ippusb sees its IPP interface.
LPINFO_LV = """Device: uri = usb://Brother/MFC-J1355DW?serial=U64123
        class = direct
        info = Brother MFC-J1355DW
        make-and-model = Brother MFC-J1355DW
        device-id = MFG:Brother;MDL:MFC-J1355DW;CMD:PJL;
        location =
Device: uri = ippusb://Brother/MFC-J1355DW?serial=U64123
        class = direct
        info = Brother MFC-J1355DW
        make-and-model = Brother MFC-J1355DW
        device-id = MFG:Brother;MDL:MFC-J1355DW;CMD:PJL;CMD:IPP;
        location =
Device: uri = usb://Kyocera/FS-1020D?serial=ABC
        class = direct
        info = Kyocera FS-1020D
        make-and-model = Kyocera FS-1020D
        device-id = MFG:Kyocera;MDL:FS-1020D;CMD:PCL;
        location =
Device: uri = file:///dev/null
        class = file
        info = Local printer
"""

LPINFO_M = """drv:///sample.drv/generic.ppd Generic PostScript Printer
drv:///sample.drv/generpcl.ppd Generic PCL Laser Printer
drv:///sample.drv/laserjet.ppd HP LaserJet Series PCL 4/5
drv:///cupsfilters.drv/pxlmono.ppd Generic PDF Printer
gutenprint.5.2://brother-mfc-6550mc/expert Brother MFC-6550MC - CUPS+Gutenprint
gutenprint.5.2://brother-hl-1250/expert Brother HL-1250 - CUPS+Gutenprint
gutenprint.5.2://kyocera-fs-1020d/expert Kyocera FS-1020D - CUPS+Gutenprint
"""


def install_fakes(lv=LPINFO_LV, lm=LPINFO_M):
    calls = []

    def fake_run(cmd, timeout=4):
        calls.append(list(cmd))
        if cmd[:3] == ["lpinfo", "-l", "-v"]:
            return 0, lv
        if cmd[:2] == ["lpinfo", "-m"]:
            return 0, lm
        return 0, ""

    settings.run = fake_run
    settings.have = lambda _b: True
    return calls


def check_print_file_cleanup():
    """nbprint.make_print_file must not leave a temporary PDF behind when the
    document could not be rendered.

    The Print dialog used to mkstemp() and then call make_pdf() inside the same
    try: when rendering raised, the message said "Nothing was printed" and left
    the half-written /tmp/nbprint-*.pdf sitting there — on the disk-full path,
    while telling the person to free up some space. /tmp is a tmpfs on the live
    system, so every failed attempt also held on to RAM until reboot."""
    import shutil as _shutil
    import tempfile as _tempfile

    import nbprint

    real_tmpdir = _tempfile.tempdir
    sandbox = _tempfile.mkdtemp(prefix="nbprint-selftest-")
    try:
        _tempfile.tempdir = sandbox

        def boom(path):
            with open(path, "wb") as fh:      # a partly-written document
                fh.write(b"%PDF-1.4\n")
            raise OSError(28, "No space left on device")

        raised = None
        try:
            nbprint.make_print_file(boom)
        except OSError as e:
            raised = e
        check(raised is not None and getattr(raised, "errno", None) == 28,
              "a failed render still reports its error to the dialog")
        check(os.listdir(sandbox) == [],
              "a failed render leaves no temporary print file behind")
        # and the dialog still gets a sentence with no errno or path in it
        said = nbprint._prepare_problem(raised)
        check("28" not in said and sandbox not in said and "Errno" not in said,
              "the disk-full message stays plain English")

        ok_path = nbprint.make_print_file(
            lambda p: open(p, "wb").write(b"%PDF-1.4\n"))
        check(os.path.exists(ok_path) and os.path.getsize(ok_path) > 0,
              "a successful render returns the file it wrote")
        os.unlink(ok_path)
        check(os.listdir(sandbox) == [],
              "the dialog is what removes the file it printed")
    finally:
        _tempfile.tempdir = real_tmpdir
        _shutil.rmtree(sandbox, ignore_errors=True)


def check_async_discovery():
    """Printer discovery must happen off the UI thread, and must not be able to
    reach a dialog that has been closed.

    list_printers() is four `lpstat` calls at a four-second timeout each. It ran
    on the UI thread, before the Print window was created, so on a machine with
    a wedged cupsd or an unplugged printer, Print did nothing visible for up to
    a quarter of a minute — from a menu item that gave no sign of having been
    clicked. The answer now arrives into a window that is already open, which
    creates the second problem this checks: the window may be gone, or closed
    and opened again, by the time the answer lands.
    """
    import inspect

    import nbjobs
    import nbprint

    real_list = nbprint.list_printers
    try:
        # A discovery that will not answer until this test says so.
        entered = threading.Event()
        release = threading.Event()
        answer = [([{"name": "P", "info": "A printer", "ready": True}], "P")]

        def slow_list():
            entered.set()
            release.wait(10.0)
            return answer[0]

        nbprint.list_printers = slow_list

        disp = nbjobs.ManualDispatcher()
        owner = nbjobs.JobOwner(dispatch=disp, name="test")
        got = []
        t0 = time.monotonic()
        job = nbprint.discover_printers_async(
            owner, lambda pr, d: got.append((pr, d)))
        elapsed = time.monotonic() - t0
        check(job is not None and elapsed < 0.5,
              "discovery returns to the caller immediately (%.3fs)" % elapsed)
        check(entered.wait(10.0), "discovery is really running in the meantime")
        check(got == [], "nothing has been reported while lpstat is blocked")
        release.set()
        owner.join(10.0)
        check(got == [], "the answer waits for the dispatcher, not the thread")
        disp.drain()
        check(len(got) == 1 and got[0][0][0]["name"] == "P" and got[0][1] == "P",
              "the printer list and default arrive through the dispatcher")

        # ---- a closed dialog cannot be updated ----------------------------
        entered.clear(); release.clear()
        disp2 = nbjobs.ManualDispatcher()
        closed_owner = nbjobs.JobOwner(dispatch=disp2, name="closed")
        stale = []
        nbprint.discover_printers_async(
            closed_owner, lambda pr, d: stale.append(pr))
        check(entered.wait(10.0), "the doomed discovery started")
        closed_owner.close()                      # the dialog is closed
        release.set()
        closed_owner.join(10.0)
        disp2.drain()
        check(stale == [],
              "a discovery that finishes after its dialog closed reports nothing")

        # ---- closed, then opened again ------------------------------------
        entered.clear(); release.clear()
        disp3 = nbjobs.ManualDispatcher()
        old = nbjobs.JobOwner(dispatch=disp3, name="old-dialog")
        old_seen = []
        nbprint.discover_printers_async(old, lambda pr, d: old_seen.append(pr))
        entered.wait(10.0)
        old.close()
        nbprint.list_printers = lambda: ([{"name": "Q", "info": "Q",
                                           "ready": True}], "Q")
        disp4 = nbjobs.ManualDispatcher()
        new = nbjobs.JobOwner(dispatch=disp4, name="new-dialog")
        new_seen = []
        nbprint.discover_printers_async(new, lambda pr, d: new_seen.append(pr))
        release.set()                             # the OLD discovery returns
        old.join(10.0); new.join(10.0)
        disp3.drain(); disp4.drain()
        check(old_seen == [],
              "the reopened Print dialog is not written to by the old one")
        check(len(new_seen) == 1 and new_seen[0][0]["name"] == "Q",
              "the reopened dialog shows its own, fresh list")
        new.close()

        # ---- superseded within one dialog ---------------------------------
        entered.clear(); release.clear()
        answer[0] = ([{"name": "OLD", "info": "OLD", "ready": True}], "OLD")
        nbprint.list_printers = slow_list
        disp5 = nbjobs.ManualDispatcher()
        one = nbjobs.JobOwner(dispatch=disp5, name="one-dialog")
        seen = []
        nbprint.discover_printers_async(one, lambda pr, d: seen.append(pr))
        entered.wait(10.0)
        nbprint.list_printers = lambda: ([{"name": "NEW", "info": "NEW",
                                           "ready": True}], "NEW")
        nbprint.discover_printers_async(one, lambda pr, d: seen.append(pr))
        one.join(10.0)
        disp5.drain()
        release.set()
        one.join(10.0)
        disp5.drain()
        check([p[0]["name"] for p in seen] == ["NEW"],
              "a second discovery for one dialog supersedes the first")
        check(one.generation(nbprint.DISCOVER_KEY) == 2,
              "the generation for the discovery key went up, once")
        one.close()

        # ---- discovery is defined never to fail ---------------------------
        def boom():
            raise OSError("cupsd went away")

        nbprint.list_printers = boom
        disp6 = nbjobs.ManualDispatcher()
        o6 = nbjobs.JobOwner(dispatch=disp6)
        out = []
        nbprint.discover_printers_async(o6, lambda pr, d: out.append((pr, d)))
        o6.join(10.0)
        disp6.drain()
        check(out == [([], None)],
              "a discovery that raises reports an empty list, not a crash")
        o6.close()
    finally:
        nbprint.list_printers = real_list

    # ---- what the dialog itself does, read from the source ----------------
    # The dialog needs a display to build, so its lifecycle is checked the way
    # commands_selftest checks nbapp: by reading the code that runs.
    src = inspect.getsource(nbprint._print_dialog)
    check("discover_printers_async" in src,
          "the Print dialog discovers printers asynchronously")
    check("list_printers()" not in src,
          "the Print dialog no longer blocks on lpstat before it opens")
    check(src.index("win.show_all()") < src.index("return win"),
          "the window is shown before the printer list is known")
    check('win.connect("destroy"' in src and "owner.close()" in src,
          "closing the Print window closes its job owner")

    # Task 033 inverted the old pins ON PURPOSE: rendering and spooling left
    # the UI thread for an nbjobs worker, and the double-click guard's job is
    # done by the running job itself. These pin the NEW contract; the old
    # three sat green for a day while asserting the exact freeze 033 removed.
    body = inspect.getsource(nbprint._print_body)
    check("owner.start" in body,
          "printing runs as an nbjobs job, never on the UI thread")
    check("Gtk.main_iteration()" not in body,
          "no main-loop pumping inside the Print handler")
    check("cancel_print" in body,
          "the Print window owns one cancellation callable")
    worker = inspect.getsource(nbprint._print_worker) \
        if hasattr(nbprint, "_print_worker") else body
    check("make_print_file" in worker and "submit_pdf(" in worker
          and "job.checkpoint()" in worker,
          "render and spool live in the worker, checkpointed between stages")

    # Wording and visuals: the two final states are the same widgets they were,
    # and the only new sentence is the one for the state that did not exist.
    nop = inspect.getsource(nbprint._no_printer_body)
    check("No printer found" in nop and
          "Connect a USB printer and switch it on, then try again." in nop,
          "the no-printer wording is unchanged")
    check(nbprint.NO_PRINTER_NOTE ==
          "File ▸ Export to PDF saves this document as a file instead.",
          "the export note is unchanged")
    check(nbprint.LOOKING_TEXT.endswith("…"),
          "the waiting state says what it is doing, with an ellipsis")
    for word in ("Printer", "Copies", "Print", "Cancel"):
        check(('"%s"' % word) in body or ("label=\"%s\"" % word) in body,
              "the Print dialog still says %s" % word)


def main():
    check_print_file_cleanup()
    check_async_discovery()
    real_run, real_have = settings.run, settings.have
    try:
        # ---- one physical printer must appear once ----
        install_fakes()
        s = fresh()
        devices = s._printers_scan_usb()
        uris = [u for u, _l in devices]
        check(len(devices) == 2,
              "two printers on the bus are listed as two devices")
        check("ippusb://Brother/MFC-J1355DW?serial=U64123" in uris,
              "the driverless view of a printer wins the merge")
        check("usb://Brother/MFC-J1355DW?serial=U64123" not in uris,
              "its duplicate printer-port entry is not offered as well")
        check("usb://Kyocera/FS-1020D?serial=ABC" in uris,
              "a printer with no IPP interface is still listed")
        check("file:///dev/null" not in uris,
              "non-USB devices are ignored")
        check(s._pr_classic_uri.get(
                  "ippusb://Brother/MFC-J1355DW?serial=U64123") ==
              "usb://Brother/MFC-J1355DW?serial=U64123",
              "the printer port is remembered behind the driverless entry")
        check(s._pr_devinfo.get(
                  "ippusb://Brother/MFC-J1355DW?serial=U64123", ("", ""))[0]
              != "",
              "the merged entry keeps the printer's own device-id")

        # ---- driverless is offered first, and confidently ----
        matches, confident = s._printers_match_drivers(
            "Brother MFC-J1355DW", "ippusb://Brother/MFC-J1355DW?serial=U64123")
        check(matches and matches[0][0] == settings.Settings._DRIVERLESS,
              "driverless is the first choice for an IPP printer")
        check(confident,
              "a driverless printer is reported as identified")
        check(any(p != settings.Settings._DRIVERLESS for p, _d in matches),
              "classic drivers remain available underneath it")

        # ---- a printer with no IPP interface is NOT claimed to be identified --
        matches2, confident2 = s._printers_match_drivers(
            "Kyocera FS-1020D", "usb://Kyocera/FS-1020D?serial=ABC")
        check(settings.Settings._DRIVERLESS not in [p for p, _d in matches2],
              "driverless is not offered for a printer without IPP")
        check(confident2,
              "an exact model match is still reported as identified")

        # An unknown model must not be passed off as identified — preselecting a
        # same-brand guess as if it were right is what made a wrong page
        # language reach the printer in the first place.
        matches3, confident3 = s._printers_match_drivers(
            "Brother MFC-J9999XX", "usb://Brother/MFC-J9999XX?serial=Z")
        check(not confident3,
              "a brand-only guess is not reported as identified")

        # ---- adding: driverless must not spool down the printer port --------
        s2 = fresh()
        install_fakes()
        s2._printers_scan_usb()
        s2._IPPUSB_BACKEND = "/nonexistent/ippusb"
        cmd, ppd, err = s2._driverless_lpadmin(
            "P", "ippusb://Brother/MFC-J1355DW?serial=U64123")
        check(cmd is None and err,
              "a missing driverless backend reports a reason, not a crash")

        # ---- URI identity ---------------------------------------------------
        import urllib.parse as up
        s3 = fresh()
        a = s3._printer_ident("usb://Brother/MFC-J1355DW?serial=U64123", up)
        b = s3._printer_ident(
            "ippusb://Brother/MFC-J1355DW?serial=U64123&interface=2", up)
        check(a == b, "the two views of one printer share an identity")
        c = s3._printer_ident("usb://Brother/MFC-J1355DW?serial=OTHER", up)
        check(a != c, "two of the same model are told apart by serial")
        check(s3._printer_ident("usb://Brother/MFC%2DJ1355DW", up) ==
              s3._printer_ident("usb://Brother/MFC-J1355DW", up),
              "percent-encoding does not split one printer into two")
        check(settings.Settings._is_usb_uri("ippusb://x/y") and
              settings.Settings._is_usb_uri("usb://x/y") and
              not settings.Settings._is_usb_uri("ipp://x/y") and
              not settings.Settings._is_usb_uri("socket://x"),
              "only the two USB schemes are accepted")

        # ---- scanning must survive a backend that says nothing ---------------
        install_fakes(lv="")
        s4 = fresh()
        check(s4._printers_scan_usb() == [],
              "an empty device list is empty, not an error")
    finally:
        settings.run, settings.have = real_run, real_have

    print()
    if FAILED:
        print("printer selftest: %d FAILED" % len(FAILED))
        for f in FAILED:
            print("  - %s" % f)
        return 1
    print("printer selftest: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
