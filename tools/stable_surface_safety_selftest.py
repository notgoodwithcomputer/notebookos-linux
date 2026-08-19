#!/usr/bin/env python3
"""Headless regressions for stable-interface data-safety fixes."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

DE = (Path(__file__).resolve().parents[1]
      / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, str(DE))

import media
import osk
import settings
import shell
import usbwriter


class Job:
    def checkpoint(self):
        pass

    def progress(self, *_args):
        pass


def check(label, condition):
    print(("PASS " if condition else "FAIL ") + label)
    if not condition:
        raise AssertionError(label)


def usb_unmount_failure_stops_before_open():
    writer = SimpleNamespace()
    drive = {"name": "sdz", "node": "/dev/sdz", "bytes": 4096}
    image = {"path": "/tmp/image.iso", "bytes": 1024}
    mounted = [[("/dev/sdz1", "/media/test")]]
    result = SimpleNamespace(returncode=32)
    with mock.patch.object(usbwriter, "_target_still_safe", return_value=True), \
            mock.patch.object(usbwriter.os.path, "getsize", return_value=1024), \
            mock.patch.object(usbwriter, "_mounted_parts",
                              side_effect=lambda _name: mounted[0]), \
            mock.patch.object(usbwriter.subprocess, "run", return_value=result), \
            mock.patch("builtins.open") as opened:
        try:
            usbwriter.UsbWriter._write_job(writer, Job(), drive, image)
        except OSError:
            pass
        else:
            raise AssertionError("failed unmount did not abort")
    check("USB Writer never opens a target after umount fails",
          not opened.called)


def usb_sync_failure_is_not_reported_as_finished():
    writer = SimpleNamespace()
    with tempfile.TemporaryDirectory() as td:
        source = os.path.join(td, "image.iso")
        target = os.path.join(td, "stick")
        with open(source, "wb") as fh:
            fh.write(b"complete image")
        drive = {"name": "sdz", "node": target, "bytes": 4096}
        image = {"path": source, "bytes": os.path.getsize(source)}
        failed_sync = SimpleNamespace(returncode=1)
        with mock.patch.object(usbwriter, "_target_still_safe",
                               return_value=True), \
                mock.patch.object(usbwriter, "_mounted_parts", return_value=[]), \
                mock.patch.object(usbwriter.subprocess, "run",
                                  return_value=failed_sync):
            try:
                usbwriter.UsbWriter._write_job(writer, Job(), drive, image)
            except OSError:
                refused = True
            else:
                refused = False
    check("USB Writer treats a failed final sync as a failed write", refused)


def vanished_backup_never_creates_local_directory():
    pane = SimpleNamespace()
    pane._usb_media = lambda: []
    pane._backup_dest_dir = mock.Mock(side_effect=AssertionError(
        "destination resolution must not run"))
    result = settings.Settings._backup_worker(
        pane, Job(), "/media/removed-stick", 10)
    check("Backup refuses a USB mount that vanished before the worker",
          result["outcome"] == "failed" and result["dest"] is None)
    check("Backup creates no directory beneath a vanished mount",
          not pane._backup_dest_dir.called)


def fullscreen_flag_is_owner_scoped():
    viewer = SimpleNamespace()
    with tempfile.TemporaryDirectory() as td:
        flag = os.path.join(td, "video-fullscreen")
        with mock.patch.object(media, "VIDEO_FULL_FLAG", flag):
            with open(flag, "w") as fh:
                fh.write("%d stale" % (os.getpid() + 1))
            media.MediaViewer._hide_panel(viewer, False)
            check("one viewer cannot clear another viewer's fullscreen flag",
                  os.path.exists(flag))
            with open(flag, "w") as fh:
                fh.write(media._process_token())
            media.MediaViewer._hide_panel(viewer, False)
            check("a viewer can clear its own fullscreen flag",
          not os.path.exists(flag))


def recycled_fullscreen_pid_does_not_hide_panel():
    with tempfile.TemporaryDirectory() as td:
        flag = os.path.join(td, "video-fullscreen")
        with open(flag, "w") as fh:
            fh.write("123 100")
        panel = SimpleNamespace(get_visible=lambda: False, show=mock.Mock(),
                                hide=mock.Mock(), _reserve_strut=mock.Mock(),
                                _apply_shape=mock.Mock())
        with mock.patch.object(shell, "VIDEO_FULL_FLAG", flag), \
                mock.patch.object(shell, "_process_token",
                                  return_value="123 200"):
            shell.Panel._poll_video_full(panel)
        check("a recycled fullscreen PID cannot keep the panel hidden",
              panel.show.called and not os.path.exists(flag))


def osk_release_preserves_open_accent_palette():
    selected = []

    class Hold:
        open = True

        def cancel(self):
            self.open = False

        def select(self, index, inject):
            if self.open:
                inject(("á", "é")[index])
            self.cancel()

    # _pick now routes the chosen accent through the window's own _character,
    # which injects it AND consumes any one-shot shift — so the fake needs
    # both the injector and a state whose consume_printable says "nothing to
    # rebuild". (Codex moved the injection off self.injector.character so a
    # held-key accent obeys shift the same way a tap does.)
    keyboard = SimpleNamespace(
        _hold_source=0, hold=Hold(), _tap=lambda _code: None,
        injector=SimpleNamespace(character=selected.append), _palette=None,
        state=SimpleNamespace(consume_printable=lambda: False),
        _rebuild=lambda: None)
    keyboard._character = lambda ch: osk.OSKWindow._character(keyboard, ch)
    osk.OSKWindow._release_letter(keyboard, None, 38)
    osk.OSKWindow._pick(keyboard, 1)
    check("releasing a held OSK key preserves its open accent palette",
          selected == ["é"])


def main():
    usb_unmount_failure_stops_before_open()
    usb_sync_failure_is_not_reported_as_finished()
    vanished_backup_never_creates_local_directory()
    fullscreen_flag_is_owner_scoped()
    recycled_fullscreen_pid_does_not_hide_panel()
    osk_release_preserves_open_accent_palette()
    # Terminal verdict the release runner recognises (run_all_gates SUCCESSWORD):
    # a descriptive line is not a report it will trust.
    print("\nall stable-surface safety checks passed")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
