#!/usr/bin/env python3
"""Headless self-test for the tablet-mode daemon."""
import importlib.util
import io
import os
import signal
import struct
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON = os.path.join(ROOT, "buildroot", "board", "notebookos",
                      "rootfs-overlay", "opt", "notebook", "de",
                      "xtabletd.py")
SPEC = importlib.util.spec_from_file_location("xtabletd", DAEMON)
xt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(xt)


class CapabilityTests(unittest.TestCase):
    def test_hex_masks(self):
        expected = {"0": False, "2": True, "3": True, "20\n": False}
        for mask, answer in expected.items():
            with self.subTest(mask=mask):
                self.assertEqual(xt.switch_mask_has_tablet(mask), answer)

    def test_fake_sysfs_discovery(self):
        with tempfile.TemporaryDirectory() as td:
            sysroot, devroot = os.path.join(td, "sys"), os.path.join(td, "dev")
            os.makedirs(devroot)
            for event, mask in (("event0", "2"), ("event1", "20\n"),
                                ("event2", "3")):
                capdir = os.path.join(sysroot, event, "device", "capabilities")
                os.makedirs(capdir)
                with open(os.path.join(capdir, "sw"), "w") as fh:
                    fh.write(mask)
                open(os.path.join(devroot, event), "w").close()
            self.assertEqual(xt.discover_devices(sysroot, devroot),
                             [os.path.join(devroot, "event0"),
                              os.path.join(devroot, "event2")])


class CoreTests(unittest.TestCase):
    @staticmethod
    def event(kind, code, value):
        return xt.EVENT.pack(0, 0, kind, code, value)

    def test_noise_and_repeats_are_ignored(self):
        fired = []
        core = xt.ModeCore(fired.append)
        stream = b"".join((
            self.event(xt.EV_SYN, 0, 0), self.event(xt.EV_KEY, 1, 1),
            self.event(xt.EV_SW, xt.SW_TABLET_MODE, 1),
            self.event(xt.EV_SW, xt.SW_TABLET_MODE, 1),
            self.event(xt.EV_SW, 2, 0),
            self.event(xt.EV_SW, xt.SW_TABLET_MODE, 0),
            self.event(xt.EV_SW, xt.SW_TABLET_MODE, 0)))
        core.feed(xt.decode_events(stream))
        self.assertEqual(fired, [True, False])

    def test_startup_ioctl_folded_fires_once(self):
        calls, requests = [], []

        def fake_ioctl(fd, request, bits, mutate):
            requests.append((fd, request, mutate))
            bits[0] = 0x02

        core = xt.ModeCore(calls.append)
        core.set_mode(xt.read_startup_mode(17, fake_ioctl))
        core.set_mode(True)
        self.assertEqual(calls, [True])
        self.assertEqual(requests[0][0], 17)
        self.assertTrue(requests[0][2])


class ActionTests(unittest.TestCase):
    def test_flag_and_owned_keyboard_lifecycle(self):
        with tempfile.TemporaryDirectory() as td:
            flag = os.path.join(td, "mode")
            marker = os.path.join(td, "term")
            keyboard = os.path.join(td, "matchbox-keyboard")
            with open(keyboard, "w") as fh:
                fh.write("#!/bin/sh\ntrap 'printf term > \"%s\"; exit 0' TERM\n"
                         "printf started > \"%s.started\"\nwhile :; do sleep 1; done\n"
                         % (marker, marker))
            os.chmod(keyboard, 0o755)
            actions = xt.Actions(flag, keyboard, io.StringIO())
            actions(True)
            with open(flag) as fh:
                self.assertEqual(fh.read(), "1")
            self.assertFalse(os.path.exists(os.path.join(td, ".mode.%d.tmp" % os.getpid())))
            deadline = time.time() + 2
            while not os.path.exists(marker + ".started") and time.time() < deadline:
                time.sleep(0.02)
            self.assertTrue(os.path.exists(marker + ".started"))
            pid = actions.child.pid
            actions(False)
            with open(flag) as fh:
                self.assertEqual(fh.read(), "0")
            self.assertTrue(os.path.exists(marker))
            self.assertEqual(actions.child, None)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            actions.close()
            self.assertFalse(os.path.exists(flag))

    def test_absent_keyboard_logs_once(self):
        with tempfile.TemporaryDirectory() as td:
            err = io.StringIO()
            actions = xt.Actions(os.path.join(td, "mode"),
                                 os.path.join(td, "absent"), err)
            actions(True)
            actions(False)
            actions(True)
            self.assertEqual(len(err.getvalue().splitlines()), 1)
            self.assertIsNone(actions.child)
            actions.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
