#!/usr/bin/env python3
"""Headless self-test for the tablet-mode daemon."""
import ctypes
import importlib.util
import fcntl
import io
import os
import signal
import struct
import subprocess
import tempfile
import time
import unittest
import uuid
from unittest import mock

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


class DeviceLoopTests(unittest.TestCase):
    def test_multiple_switches_are_aggregated(self):
        calls = []
        loop = xt.DeviceLoop(xt.ModeCore(calls.append), discover=lambda: [])
        loop.devices = {10: ["a", b"", True], 11: ["b", b"", False]}
        loop._publish_mode()
        loop._consume_records(11, [(xt.EV_SW, xt.SW_TABLET_MODE, 0)])
        self.assertEqual(calls, [True])
        loop._consume_records(10, [(xt.EV_SW, xt.SW_TABLET_MODE, 0)])
        self.assertEqual(calls, [True, False])

    def test_inactive_device_cannot_cancel_active_peer(self):
        calls = []
        loop = xt.DeviceLoop(xt.ModeCore(calls.append), discover=lambda: [])
        loop.devices = {10: ["a", b"", True], 11: ["b", b"", True]}
        loop._publish_mode()
        loop._consume_records(10, [(xt.EV_SW, xt.SW_TABLET_MODE, 0)])
        self.assertEqual(calls, [True])

    def test_losing_last_switch_preserves_folded_safety(self):
        calls = []
        loop = xt.DeviceLoop(xt.ModeCore(calls.append), discover=lambda: [])
        loop.devices = {10: ["hinge", b"", True]}
        loop._publish_mode()
        loop._drop(10)
        self.assertEqual(calls, [True])

    def test_losing_one_switch_publishes_remaining_authority(self):
        calls = []
        loop = xt.DeviceLoop(xt.ModeCore(calls.append), discover=lambda: [])
        loop.devices = {10: ["folded", b"", True],
                        11: ["unfolded", b"", False]}
        loop._publish_mode()
        loop._drop(10)
        self.assertEqual(calls, [True, False])


class XInputResourceTests(unittest.TestCase):
    def test_missing_advertised_class_array_is_conservatively_touch(self):
        self.assertTrue(xt.has_touch_class(None, 1))
        self.assertFalse(xt.has_touch_class(None, 0))

    def test_malformed_device_node_property_is_freed(self):
        backing = (ctypes.c_ubyte * 2)(1, 2)
        freed = []

        class X11:
            @staticmethod
            def XInternAtom(_dpy, _name, _only):
                return 1

            @staticmethod
            def XFree(data):
                freed.append(bool(data))

        class XI:
            @staticmethod
            def XIGetProperty(*args):
                args[-4]._obj.value = 16       # allocated, wrong format
                args[-3]._obj.value = 2
                ptr = ctypes.cast(backing, ctypes.POINTER(ctypes.c_ubyte))
                ctypes.cast(
                    args[-1],
                    ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)))[0] = ptr
                return 0

        adapter = object.__new__(xt.XInput2)
        adapter.x11, adapter.xi = X11(), XI()
        self.assertIsNone(adapter._node(1, 2))
        self.assertEqual(freed, [True])


class ActionTests(unittest.TestCase):
    def test_hung_keyboard_is_killed_and_reaped(self):
        child = mock.Mock(pid=43210)
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("osk", 2), 0]
        controller = xt.OskController()
        controller.child = child
        with mock.patch.object(xt.os, "killpg") as killpg:
            controller.stop()
        self.assertEqual(
            killpg.call_args_list,
            [mock.call(43210, signal.SIGTERM),
             mock.call(43210, signal.SIGKILL)])
        self.assertEqual(child.wait.call_count, 2)
        self.assertIsNone(controller.child)

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


class FakeXInput:
    def __init__(self, devices=(), events=None):
        self.inventory = list(devices)
        self.events = events
        self.changed = []

    def devices(self):
        return list(self.inventory)

    def set_enabled(self, device_id, enabled):
        self.changed.append((device_id, enabled))
        if self.events is not None:
            self.events.append("enable" if enabled else "inhibit")
        return True


class FakeOsk:
    child = None

    def __init__(self, confirmed=True, events=None):
        self.confirmed = confirmed
        self.events = events

    def start_confirmed(self):
        if self.events is not None:
            self.events.extend(("start-osk", "confirm"))
        return self.confirmed

    def stop(self):
        if self.events is not None:
            self.events.append("stop-osk")


def fake_device(device_id, name, use, node="/dev/input/event0", phys="",
                bus_type=None, touch=False):
    return {"id": device_id, "name": name, "use": use,
            "node": node, "phys": phys, "bus_type": bus_type,
            "touch": touch}


class MatcherTests(unittest.TestCase):
    def test_internal_at_keyboard_matches(self):
        device = fake_device(1, "AT Translated Set 2 keyboard",
                             xt.XI_SLAVE_KEYBOARD, phys="isa0060/serio0/input0")
        self.assertTrue(xt.device_is_internal(device))

    def test_internal_i2c_touchpad_matches(self):
        device = fake_device(2, "ELAN Touchpad", xt.XI_SLAVE_POINTER,
                             phys="i2c-ELAN0000:00/input0")
        self.assertTrue(xt.device_is_internal(device))

    def test_usb_keyboard_never_matches(self):
        device = fake_device(3, "Generic Keyboard", xt.XI_SLAVE_KEYBOARD,
                             phys="usb-0000:00:14.0-1/input0",
                             bus_type="0003")
        self.assertFalse(xt.device_is_internal(device))

    def test_bluetooth_keyboard_never_matches(self):
        device = fake_device(4, "Generic Keyboard", xt.XI_SLAVE_KEYBOARD,
                             phys="11:22:33:44:55:66", bus_type="0005")
        self.assertFalse(xt.device_is_internal(device))

    def test_usb_and_bluetooth_mice_never_match(self):
        usb = fake_device(7, "Generic Mouse", xt.XI_SLAVE_POINTER,
                          phys="usb-1/input0", bus_type="0003")
        bluetooth = fake_device(8, "Generic Mouse", xt.XI_SLAVE_POINTER,
                                phys="11:22:33:44:55:66", bus_type="0005")
        self.assertFalse(xt.device_is_internal(usb))
        self.assertFalse(xt.device_is_internal(bluetooth))

    def test_touchscreen_never_matches(self):
        device = fake_device(5, "ELAN Finger", xt.XI_SLAVE_POINTER,
                             phys="i2c-ELAN9008:00/input0", touch=True)
        self.assertFalse(xt.device_is_internal(device))

    def test_missing_device_node_never_matches(self):
        device = fake_device(6, "AT keyboard", xt.XI_SLAVE_KEYBOARD,
                             node=None, phys="isa0060/serio0/input0")
        self.assertFalse(xt.device_is_internal(device))


class SafetyTests(unittest.TestCase):
    def _actions(self, directory, devices=(), events=None, confirmed=True):
        return xt.Actions(
            flag=os.path.join(directory, "mode"),
            xinput=FakeXInput(devices, events),
            inhibited_file=os.path.join(directory, "inhibited"),
            osk=FakeOsk(confirmed, events))

    def test_enter_order(self):
        with tempfile.TemporaryDirectory() as td:
            events = []
            device = fake_device(1, "AT keyboard", xt.XI_SLAVE_KEYBOARD,
                                 phys="isa0060/serio0/input0")
            actions = self._actions(td, [device], events)
            actions(True)
            self.assertEqual(events, ["start-osk", "confirm", "inhibit"])
            with open(actions.inhibited_file) as fh:
                self.assertEqual(fh.read(), "AT keyboard\n")
            actions.close()

    def test_reconcile_catches_late_internal_device(self):
        with tempfile.TemporaryDirectory() as td:
            first = fake_device(10, "AT keyboard", xt.XI_SLAVE_KEYBOARD,
                                phys="isa0060/serio0/input0")
            actions = self._actions(td, [first])
            actions.inhibit()
            late = fake_device(11, "AT keyboard", xt.XI_SLAVE_KEYBOARD,
                               phys="isa0060/serio0/input0")
            external = fake_device(12, "USB keyboard", xt.XI_SLAVE_KEYBOARD,
                                   phys="usb-0000/input0")
            actions.xinput.inventory = [late, external]
            actions.reconcile_inhibition()
            self.assertIn((11, False), actions.xinput.changed)
            self.assertNotIn((12, False), actions.xinput.changed)
            self.assertEqual(set(actions.disabled), {11})

    def test_failed_confirmation_does_not_inhibit(self):
        with tempfile.TemporaryDirectory() as td:
            events = []
            device = fake_device(1, "AT keyboard", xt.XI_SLAVE_KEYBOARD,
                                 phys="isa0060/serio0/input0")
            actions = self._actions(td, [device], events, confirmed=False)
            actions(True)
            self.assertEqual(events, ["start-osk", "confirm"])
            self.assertEqual(actions.xinput.changed, [])
            actions.close()

    def test_bad_diagnostic_marker_cannot_interrupt_close(self):
        with tempfile.TemporaryDirectory() as td:
            events = []
            actions = xt.Actions(
                flag=os.path.join(td, "mode"),
                xinput=FakeXInput(events=events),
                inhibited_file=td,       # unlink/open is an IsADirectoryError
                osk=FakeOsk(events=events))
            actions.disabled = {7: "internal keyboard"}
            actions.close()
            self.assertEqual(events, ["enable", "stop-osk"])
            self.assertEqual(actions.disabled, {})

    def test_bad_diagnostic_marker_cannot_abort_startup_heal(self):
        with tempfile.TemporaryDirectory() as td:
            device = fake_device(1, "AT keyboard", xt.XI_SLAVE_KEYBOARD,
                                 phys="isa0060/serio0/input0")
            actions = xt.Actions(
                flag=os.path.join(td, "mode"),
                xinput=FakeXInput([device]),
                inhibited_file=td,       # unlink is an IsADirectoryError
                osk=FakeOsk())
            actions.startup_heal()
            self.assertEqual(actions.xinput.changed, [(1, True)])

    def test_bad_mode_flag_cannot_abort_close(self):
        with tempfile.TemporaryDirectory() as td:
            events = []
            actions = xt.Actions(
                flag=td,                # unlink is an IsADirectoryError
                xinput=FakeXInput(events=events),
                inhibited_file=os.path.join(td, "inhibited"),
                osk=FakeOsk(events=events))
            actions.close()
            self.assertEqual(events, ["stop-osk"])

    def test_leave_order(self):
        with tempfile.TemporaryDirectory() as td:
            events = []
            actions = self._actions(td, events=events)
            actions.disabled = {7: "internal keyboard"}
            actions(False)
            self.assertEqual(events, ["enable", "stop-osk"])
            actions.close()

    def test_sigterm_reenables_entire_disabled_set(self):
        with tempfile.TemporaryDirectory() as td:
            actions = self._actions(td)
            actions.disabled = {7: "keyboard", 9: "touchpad"}

            class Loop:
                stopped = False

                def stop(self):
                    self.stopped = True

            loop = Loop()
            xt.fail_open_stop(actions, loop, signal.SIGTERM, None)
            self.assertEqual(actions.xinput.changed, [(7, True), (9, True)])
            self.assertTrue(loop.stopped)

    def test_startup_heals_matches_and_removes_stale_file(self):
        with tempfile.TemporaryDirectory() as td:
            internal = fake_device(1, "AT keyboard", xt.XI_SLAVE_KEYBOARD,
                                   phys="isa0060/serio0/input0")
            external = fake_device(2, "USB Keyboard", xt.XI_SLAVE_KEYBOARD,
                                   phys="usb-1/input0")
            actions = self._actions(td, [internal, external])
            with open(actions.inhibited_file, "w") as fh:
                fh.write("stale keyboard\n")
            actions.startup_heal()
            self.assertEqual(actions.xinput.changed, [(1, True)])
            self.assertFalse(os.path.exists(actions.inhibited_file))

    def test_watch_exception_reenables_disabled_set(self):
        with tempfile.TemporaryDirectory() as td:
            actions = self._actions(td)
            actions.disabled = {3: "keyboard", 4: "touchpad"}

            class BrokenLoop:
                def run(self):
                    raise RuntimeError("injected watch failure")

            with self.assertRaisesRegex(RuntimeError, "injected watch failure"):
                xt.run_watch_loop(actions, BrokenLoop())
            self.assertEqual(actions.xinput.changed, [(3, True), (4, True)])


# ---------------------------------------------------------------------------
# UINPUT CHAIN -- real kernel routing, discovery, decoding, and daemon actions
# ---------------------------------------------------------------------------

UINPUT_SKIP = ("SKIP: /dev/uinput not accessible -- chain section needs "
               "root (runs on the guest)")


def uinput_chain_available(uinput="/dev/uinput", sys_input="/sys/class/input"):
    """Keep the live-test gate small enough to red-proof without uinput."""
    return os.path.exists(uinput) and os.access(uinput, os.W_OK) and \
        os.path.isdir(sys_input)


def _ioc(direction, kind, number, size=0):
    """Encode Linux's generic ioctl number (dir:2,size:14,type:8,nr:8)."""
    return ((direction << 30) | (size << 16) |
            (ord(kind) << 8) | number)


# uinput.h uses _IOW('U', nr, int) for capability bits, _IO for lifecycle,
# and _IOW('U', 3, struct uinput_setup) for the modern device setup call.
_IOC_WRITE = 1
UI_SET_EVBIT = _ioc(_IOC_WRITE, "U", 100, struct.calcsize("i"))
UI_SET_SWBIT = _ioc(_IOC_WRITE, "U", 109, struct.calcsize("i"))
UI_DEV_CREATE = _ioc(0, "U", 1)
UI_DEV_DESTROY = _ioc(0, "U", 2)
UINPUT_SETUP = struct.Struct("HHHH80sI")
UI_DEV_SETUP = _ioc(_IOC_WRITE, "U", 3, UINPUT_SETUP.size)


class UinputChainTests(unittest.TestCase):
    def test_gate_decision_headlessly(self):
        """Red-proof both decisions without pretending a live device exists."""
        with tempfile.TemporaryDirectory() as td:
            fake_uinput = os.path.join(td, "uinput")
            fake_sys = os.path.join(td, "sys", "class", "input")
            os.makedirs(fake_sys)
            with open(fake_uinput, "wb"):
                pass
            self.assertTrue(uinput_chain_available(fake_uinput, fake_sys))
            self.assertFalse(uinput_chain_available(
                os.path.join(td, "missing-uinput"), fake_sys))
            self.assertFalse(uinput_chain_available(
                fake_uinput, os.path.join(td, "missing-sysfs")))

    @staticmethod
    def _wait_for_device(name, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event_dir in sorted(os.path.join("/sys/class/input", entry)
                                    for entry in os.listdir("/sys/class/input")
                                    if entry.startswith("event")):
                try:
                    with open(os.path.join(event_dir, "device", "name"),
                              encoding="utf-8") as fh:
                        matches = fh.read().strip() == name
                    cap = os.path.join(event_dir, "device", "capabilities", "sw")
                    with open(cap, encoding="ascii") as fh:
                        tablet = xt.switch_mask_has_tablet(fh.read())
                    node = os.path.join("/dev/input", os.path.basename(event_dir))
                    if matches and tablet and os.path.exists(node):
                        return node
                except OSError:
                    continue
            time.sleep(0.02)
        return None

    def _wait_for_flag(self, expected, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with open("/tmp/nb-tablet-mode", encoding="ascii") as fh:
                    if fh.read() == expected:
                        return True
            except OSError:
                pass
            time.sleep(0.02)
        return False

    @staticmethod
    def _emit(fd, value):
        event = struct.Struct("llHHi")
        os.write(fd, event.pack(0, 0, xt.EV_SW, xt.SW_TABLET_MODE, value))
        os.write(fd, event.pack(0, 0, xt.EV_SYN, 0, 0))

    def test_real_kernel_event_chain(self):
        if not uinput_chain_available():
            self.skipTest(UINPUT_SKIP)

        print("UINPUT CHAIN: OSK path is hardcoded; asserting flag-file actions only")
        flag = "/tmp/nb-tablet-mode"
        daemon = None
        uinput_fd = None
        created = False
        # xtabletd has no flag override.  This guest-only lock serializes use of
        # the real /tmp path; cleanup below is intentionally unconditional.
        chain_lock = os.open("/tmp/xtabletd-uinput-selftest.lock",
                             os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(chain_lock, fcntl.LOCK_EX)
        try:
            try:
                os.unlink(flag)
            except FileNotFoundError:
                pass

            uinput_fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
            fcntl.ioctl(uinput_fd, UI_SET_EVBIT, xt.EV_SW)
            fcntl.ioctl(uinput_fd, UI_SET_SWBIT, xt.SW_TABLET_MODE)
            name = "nb-xtabletd-selftest-" + uuid.uuid4().hex
            setup = UINPUT_SETUP.pack(0x03, 0x1, 0x1, 1,
                                      name.encode("ascii"), 0)
            fcntl.ioctl(uinput_fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(uinput_fd, UI_DEV_CREATE)
            created = True
            self.assertIsNotNone(self._wait_for_device(name),
                                 "uinput event node/capabilities did not appear")

            with tempfile.TemporaryDirectory() as scratch:
                env = os.environ.copy()
                env.update(HOME=scratch, TMPDIR=scratch)
                env.pop("DISPLAY", None)
                daemon = subprocess.Popen(
                    [os.environ.get("PYTHON", "python3"), DAEMON], env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
                self._emit(uinput_fd, 1)
                self.assertTrue(self._wait_for_flag("1"),
                                "daemon did not publish tablet mode")
                self._emit(uinput_fd, 0)
                self.assertTrue(self._wait_for_flag("0"),
                                "daemon did not publish laptop mode")

                stable_mtime = os.stat(flag).st_mtime_ns
                self._emit(uinput_fd, 0)
                self._emit(uinput_fd, 0)
                time.sleep(0.25)
                self.assertEqual(os.stat(flag).st_mtime_ns, stable_mtime,
                                 "repeat values rewrote/flapped the flag")

                daemon.send_signal(signal.SIGTERM)
                daemon.wait(timeout=7)
                daemon = None
                self.assertFalse(os.path.exists(flag),
                                 "SIGTERM did not remove the mode flag")
        finally:
            if daemon is not None:
                daemon.terminate()
                try:
                    daemon.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    daemon.wait(timeout=2)
            try:
                os.unlink(flag)
            except FileNotFoundError:
                pass
            if uinput_fd is not None:
                if created:
                    try:
                        fcntl.ioctl(uinput_fd, UI_DEV_DESTROY)
                    except OSError:
                        pass
                os.close(uinput_fd)
            os.close(chain_lock)


if __name__ == "__main__":
    # unittest's own "OK (skipped=1)" is not a verdict the release runner
    # recognises (run_all_gates SUCCESSWORD), so this gate was recorded as DID
    # NOT RUN while passing. Print the outcome in the house grammar; the one
    # skip (/dev/uinput needs root) is declared in run_all_gates.ALLOWED_SKIPS.
    _result = unittest.main(verbosity=2, exit=False).result
    _ok = _result.wasSuccessful()
    print("RESULT: %s" % ("ALL PASS" if _ok else "FAILED"))
    raise SystemExit(0 if _ok else 1)
