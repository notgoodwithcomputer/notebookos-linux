#!/usr/bin/env python3
"""xtabletd — turn a convertible into a tablet when its hinge says it is one.

The flip arrives from a platform input device (intel-vbtn, ideapad-laptop, or
thinkpad-acpi) as EV_SW (type 5), SW_TABLET_MODE (code 1), value 1 on entering
tablet mode and 0 on leaving it.  The shipped kernel does not yet enable the
sources: CONFIG_INTEL_VBTN, CONFIG_INTEL_HID_EVENT, CONFIG_IDEAPAD_LAPTOP, and
CONFIG_THINKPAD_ACPI are unset in kbuild-desktop/.config and are queued in
another session.  With no switch source this daemon therefore does nothing but
sleep and keep looking for a device to appear; it never invents a hinge state.

Tablet mode is published in /tmp and, when an X display is usable, on the root
window as NB_TABLET_MODE.

WHY: a folded convertible rests on its physical keyboard, so keys, touchpads,
and pointing sticks on its back can be pressed accidentally.  Once the native
OSK is visibly alive, this daemon disables only input devices proven internal
by both their Linux physical path and a narrow keyboard/touchpad/trackpoint
class allowlist; touchscreens and external input must remain usable.  The order
is a lockout rule: start and confirm the OSK before inhibition, and re-enable
physical input before stopping the OSK.  Every shutdown and failure path is
fail-open and restores all devices this daemon disabled.  If no OSK can be
confirmed, physical input is never inhibited.

The small parsing and state-machine core is deliberately independent of device,
process, and X I/O so it can be exercised without hardware or a display.
"""
import errno
import atexit
import ctypes
import fcntl
import glob
import os
import select
import signal
import struct
import subprocess
import sys
import threading
import time
from ctypes import (POINTER, Structure, byref, c_char_p, c_int, c_long,
                    c_ubyte, c_ulong, c_void_p)

EV_SYN = 0
EV_KEY = 1
EV_SW = 5

_X_ERROR_LOCK = threading.Lock()
SW_TABLET_MODE = 1
EVENT = struct.Struct("llHHi")       # Linux input_event on x86-64
SW_BYTES = 8                         # comfortably covers current SW_MAX
RESCAN_SECONDS = 5
XI_ALL_DEVICES = 0
XI_SLAVE_POINTER = 3
XI_SLAVE_KEYBOARD = 4
XI_TOUCH_CLASS = 8
PROP_MODE_REPLACE = 0
XA_INTEGER = 19


class _XIDeviceInfo(Structure):
    _fields_ = [("deviceid", c_int), ("name", c_char_p), ("use", c_int),
                ("attachment", c_int), ("enabled", c_int),
                ("num_classes", c_int), ("classes", c_void_p)]


class _XIAnyClassInfo(Structure):
    _fields_ = [("type", c_int), ("sourceid", c_int)]


def has_touch_class(classes, count):
    """Conservatively inspect XI classes; malformed metadata means touch."""
    if count <= 0:
        return False
    if not classes:
        # A device advertising classes without a class array is racing removal
        # or malformed. Never risk disabling a touchscreen on incomplete data.
        return True
    try:
        items = ctypes.cast(classes, POINTER(POINTER(_XIAnyClassInfo)))
        return any(not items[index]
                   or items[index].contents.type == XI_TOUCH_CLASS
                   for index in range(count))
    except (TypeError, ValueError, IndexError):
        return True


def device_is_internal(device):
    """Conservatively select an internal keyboard/touchpad/trackpoint."""
    name = (device.get("name") or "").lower()
    node = device.get("node")
    phys = (device.get("phys") or "").lower()
    bus_type = (device.get("bus_type") or "").lower()
    use = device.get("use")
    if not node or not phys:
        return False
    if device.get("touch") or any(
            word in name for word in ("touchscreen", "touch screen")):
        return False
    if ("usb" in phys or "bluetooth" in phys or "bluetooth" in name or
            bus_type in ("0003", "0005", "usb", "bluetooth")):
        return False
    keyboard = use == XI_SLAVE_KEYBOARD and "keyboard" in name
    pointer = use == XI_SLAVE_POINTER and any(
        word in name for word in ("touchpad", "trackpad", "trackpoint",
                                  "pointing stick"))
    if not (keyboard or pointer):
        return False
    return ("isa0060" in phys or "serio" in phys or "i2c" in phys)


class XInput2:
    """Small ctypes XInput2 adapter; callers may inject a headless fake."""

    def __init__(self, sys_class="/sys/class/input"):
        self.sys_class = sys_class
        self.x11 = ctypes.CDLL("libX11.so.6")
        self.xi = ctypes.CDLL("libXi.so.6")
        self.x11.XOpenDisplay.restype = c_void_p
        self.x11.XOpenDisplay.argtypes = [c_char_p]
        self.x11.XInternAtom.restype = c_ulong
        self.x11.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
        self.x11.XFree.argtypes = [c_void_p]
        self.x11.XFlush.argtypes = [c_void_p]
        self.x11.XCloseDisplay.argtypes = [c_void_p]
        self.xi.XIQueryDevice.restype = POINTER(_XIDeviceInfo)
        self.xi.XIQueryDevice.argtypes = [c_void_p, c_int, POINTER(c_int)]
        self.xi.XIFreeDeviceInfo.argtypes = [POINTER(_XIDeviceInfo)]
        self.xi.XIGetProperty.argtypes = [
            c_void_p, c_int, c_ulong, c_long, c_long, c_int, c_ulong,
            POINTER(c_ulong), POINTER(c_int), POINTER(c_ulong),
            POINTER(c_ulong), POINTER(POINTER(c_ubyte))]
        self.xi.XIChangeProperty.argtypes = [
            c_void_p, c_int, c_ulong, c_ulong, c_int, c_int,
            POINTER(c_ubyte), c_int]
        self._x_error_handler_type = ctypes.CFUNCTYPE(c_int, c_void_p,
                                                       c_void_p)
        self.x11.XSetErrorHandler.argtypes = [self._x_error_handler_type]
        self.x11.XSetErrorHandler.restype = self._x_error_handler_type
        self.x11.XSync.argtypes = [c_void_p, c_int]
        self.x11.XSync.restype = c_int

    def _open(self):
        if not os.environ.get("DISPLAY"):
            return None
        return self.x11.XOpenDisplay(None)

    def _node(self, dpy, device_id):
        prop = self.x11.XInternAtom(dpy, b"Device Node", False)
        actual, count, remain = c_ulong(), c_ulong(), c_ulong()
        fmt = c_int()
        data = POINTER(c_ubyte)()
        status = self.xi.XIGetProperty(
            dpy, device_id, prop, 0, 1024, False, 0, byref(actual),
            byref(fmt), byref(count), byref(remain), byref(data))
        try:
            if status != 0 or not data or fmt.value != 8:
                return None
            return ctypes.string_at(data, count.value).decode("utf-8", "replace")
        finally:
            if data:
                self.x11.XFree(data)

    def devices(self):
        dpy = self._open()
        if not dpy:
            return []
        count = c_int()
        infos = self.xi.XIQueryDevice(dpy, XI_ALL_DEVICES, byref(count))
        result = []
        try:
            for index in range(count.value):
                info = infos[index]
                if info.use not in (XI_SLAVE_POINTER, XI_SLAVE_KEYBOARD):
                    continue
                node = self._node(dpy, info.deviceid)
                touch = has_touch_class(info.classes, info.num_classes)
                phys = None
                bus_type = None
                if node:
                    device_dir = os.path.join(self.sys_class,
                                              os.path.basename(node), "device")
                    try:
                        with open(os.path.join(device_dir, "phys"),
                                  encoding="utf-8") as fh:
                            phys = fh.read().strip()
                    except OSError:
                        pass
                    try:
                        with open(os.path.join(device_dir, "id", "bustype"),
                                  encoding="ascii") as fh:
                            bus_type = fh.read().strip()
                    except OSError:
                        pass
                result.append({"id": info.deviceid,
                               "name": (info.name or b"").decode(
                                   "utf-8", "replace"),
                               "use": info.use, "node": node, "phys": phys,
                               "bus_type": bus_type, "touch": touch})
        finally:
            if infos:
                self.xi.XIFreeDeviceInfo(infos)
            self.x11.XCloseDisplay(dpy)
        return result

    def set_enabled(self, device_id, enabled):
        dpy = self._open()
        if not dpy:
            return False
        try:
            failed = [False]

            @self._x_error_handler_type
            def trap(_display, _event):
                failed[0] = True
                return 0

            # X errors are asynchronous and the handler is process-global.
            # Serialize the brief trap+sync window and do not claim success
            # until the server has acknowledged this exact request.
            with _X_ERROR_LOCK:
                previous = self.x11.XSetErrorHandler(trap)
                try:
                    prop = self.x11.XInternAtom(dpy, b"Device Enabled", False)
                    value = (c_ubyte * 1)(1 if enabled else 0)
                    self.xi.XIChangeProperty(
                        dpy, device_id, prop, XA_INTEGER, 8,
                        PROP_MODE_REPLACE, value, 1)
                    self.x11.XSync(dpy, False)
                finally:
                    self.x11.XSetErrorHandler(previous)
            return not failed[0]
        finally:
            # X connection loss needs no special recovery: the X session has
            # ended and its server-side device state is discarded on restart.
            self.x11.XCloseDisplay(dpy)


def switch_mask_has_tablet(text):
    """Return whether a sysfs hexadecimal switch mask contains bit 1."""
    try:
        return bool(int(text.strip() or "0", 16) & (1 << SW_TABLET_MODE))
    except ValueError:
        return False


def discover_devices(sys_class="/sys/class/input", dev_input="/dev/input"):
    """Return event nodes whose sysfs switch capability advertises bit 1."""
    found = []
    pattern = os.path.join(sys_class, "event*", "device", "capabilities", "sw")
    for cap in sorted(glob.glob(pattern)):
        try:
            with open(cap, encoding="ascii") as fh:
                capable = switch_mask_has_tablet(fh.read())
        except OSError:
            continue
        if capable:
            event = os.path.basename(os.path.dirname(os.path.dirname(
                os.path.dirname(cap))))
            node = os.path.join(dev_input, event)
            if os.path.exists(node):
                found.append(node)
    return found


def decode_events(data):
    """Yield complete (type, code, value) records from packed input_events."""
    complete = len(data) - (len(data) % EVENT.size)
    for offset in range(0, complete, EVENT.size):
        _sec, _usec, kind, code, value = EVENT.unpack_from(data, offset)
        yield kind, code, value


class ModeCore:
    """Deduplicate tablet transitions and call the injected action."""

    def __init__(self, action):
        self.action = action
        self.mode = None

    def set_mode(self, enabled):
        enabled = bool(enabled)
        if enabled == self.mode:
            return False
        self.mode = enabled
        self.action(enabled)
        return True

    def feed(self, records):
        for kind, code, value in records:
            if kind == EV_SW and code == SW_TABLET_MODE:
                self.set_mode(value != 0)


class OskController:
    """Own exactly one OSK process group and confirm it survives startup."""

    def __init__(self, native="/opt/notebook/de/osk.py",
                 fallback="/usr/bin/matchbox-keyboard", stderr=None,
                 confirm_seconds=2.0):
        self.native = native
        self.fallback = fallback
        self.stderr = stderr or sys.stderr
        self.confirm_seconds = confirm_seconds
        self.child = None
        self._fallback_reported = False
        self._missing_reported = False

    @staticmethod
    def _usable(path, executable=False):
        return os.path.isfile(path) and (not executable or os.access(path, os.X_OK))

    def _launch(self, command):
        try:
            self.child = subprocess.Popen(
                command, start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            self.child = None
            return False

    def _survives(self):
        deadline = time.monotonic() + self.confirm_seconds
        while time.monotonic() < deadline:
            if self.child.poll() is not None:
                self.child = None
                return False
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        return self.child.poll() is None

    def start_confirmed(self):
        if self.child is not None and self.child.poll() is None:
            return True
        self.child = None
        native_ok = self._usable(self.native) and \
            self._launch(["python3", self.native]) and self._survives()
        if native_ok:
            return True
        if self._usable(self.fallback, executable=True):
            if not self._fallback_reported:
                print("xtabletd: native OSK unavailable; falling back to "
                      "matchbox-keyboard", file=self.stderr)
                self._fallback_reported = True
            return self._launch([self.fallback]) and self._survives()
        if not self._missing_reported:
            print("xtabletd: no usable on-screen keyboard", file=self.stderr)
            self._missing_reported = True
        return False

    def stop(self):
        child, self.child = self.child, None
        if child is None or child.poll() is not None:
            return
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            # A wedged keyboard must not become an unowned always-on-top
            # process.  Escalate, then reap it before another fold is allowed
            # to launch a replacement.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # Retain ownership so start_confirmed cannot create a second
                # OSK while this process is still alive.
                self.child = child


class Actions:
    """Publish state, enforce safe transition order, and own inhibition."""

    def __init__(self, flag="/tmp/nb-tablet-mode",
                 keyboard="/usr/bin/matchbox-keyboard", stderr=None,
                 xinput=None, inhibited_file="/tmp/nb-tablet-inhibited",
                 osk=None):
        self.flag = flag
        self.stderr = stderr or sys.stderr
        self.xinput = xinput if xinput is not None else XInput2()
        self.inhibited_file = inhibited_file
        self.osk = osk or OskController(fallback=keyboard, stderr=self.stderr)
        self.disabled = {}              # device id -> human-readable name

    @property
    def child(self):
        return self.osk.child

    def _write_flag(self, enabled):
        directory = os.path.dirname(self.flag) or "."
        tmp = os.path.join(directory, ".%s.%d.tmp" %
                           (os.path.basename(self.flag), os.getpid()))
        try:
            with open(tmp, "w", encoding="ascii") as fh:
                fh.write("1" if enabled else "0")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.flag)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    def _set_x_property(self, enabled):
        if not os.environ.get("DISPLAY"):
            return
        try:
            import gi
            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk
            display = Gdk.Display.get_default()
            if display is None:
                return
            screen = display.get_default_screen()
            root = screen.get_root_window() if screen is not None else None
            if root is None:
                return
            prop = Gdk.atom_intern("NB_TABLET_MODE", False)
            cardinal = Gdk.atom_intern("CARDINAL", False)
            root.property_change(prop, cardinal, 32,
                                 Gdk.PropMode.REPLACE, [int(enabled)])
            display.flush()
        except Exception:
            pass

    def _record_disabled(self):
        try:
            if not self.disabled:
                os.unlink(self.inhibited_file)
                return
            with open(self.inhibited_file, "w", encoding="utf-8") as fh:
                for name in self.disabled.values():
                    fh.write(name + "\n")
        except OSError:
            # This is a recovery breadcrumb, not the inhibition mechanism.
            # Failure to maintain it must never interrupt re-enabling devices
            # or stopping the OSK on the fail-open teardown path.
            pass

    def inhibit(self):
        try:
            for device in self.xinput.devices():
                if device_is_internal(device):
                    self.disabled[device["id"]] = device["name"]
                    if not self.xinput.set_enabled(device["id"], False):
                        self.disabled.pop(device["id"], None)
                        continue
                    self._record_disabled()
        except Exception:
            self.enable_all()
            raise

    def reconcile_inhibition(self):
        """Disable internal devices that appeared while already folded."""
        try:
            devices = self.xinput.devices()
            by_id = {device["id"]: device for device in devices}
            # Forget vanished IDs and IDs now belonging to an external/touch
            # device. Never issue an enable/disable command to such a device.
            for device_id in list(self.disabled):
                device = by_id.get(device_id)
                if device is None or not device_is_internal(device):
                    self.disabled.pop(device_id, None)
            for device in devices:
                device_id = device["id"]
                if device_is_internal(device) and device_id not in self.disabled:
                    if self.xinput.set_enabled(device_id, False):
                        self.disabled[device_id] = device["name"]
            self._record_disabled()
        except Exception:
            # A partial/inconsistent XInput view must fail open.
            self.enable_all()

    def enable_all(self):
        pending = dict(self.disabled)
        for device_id in pending:
            try:
                if self.xinput.set_enabled(device_id, True):
                    self.disabled.pop(device_id, None)
            except Exception:
                # Continue through the entire set: one bad ID must not keep a
                # second keyboard disabled.
                pass
        self._record_disabled()

    def startup_heal(self):
        try:
            for device in self.xinput.devices():
                if device_is_internal(device):
                    try:
                        self.xinput.set_enabled(device["id"], True)
                    except Exception:
                        pass
        finally:
            try:
                os.unlink(self.inhibited_file)
            except OSError:
                # A stale/bad diagnostic marker must not abort the daemon
                # after physical input has been healed. Monitoring the next
                # fold is more important than tidying this breadcrumb.
                pass

    def __call__(self, enabled):
        if enabled:
            self._write_flag(enabled)
            if self.osk.start_confirmed():
                self.inhibit()
        else:
            self.enable_all()
            self.osk.stop()
            self._write_flag(enabled)
        self._set_x_property(enabled)

    def close(self):
        self.enable_all()
        self.osk.stop()
        try:
            os.unlink(self.flag)
        except OSError:
            # State publication is already obsolete once the daemon is
            # closing. A stale directory or permission failure here must not
            # skip main()'s remaining atexit/instance-lock teardown.
            pass


def _eviocgsw(length):
    # _IOR('E', 0x1b, len): Linux _IOC_READ is 2 in the top two bits.
    return (2 << 30) | (length << 16) | (ord("E") << 8) | 0x1b


def read_startup_mode(fd, ioctl=fcntl.ioctl):
    bits = bytearray(SW_BYTES)
    ioctl(fd, _eviocgsw(len(bits)), bits, True)
    return bool(bits[SW_TABLET_MODE // 8] & (1 << (SW_TABLET_MODE % 8)))


class DeviceLoop:
    def __init__(self, core, discover=discover_devices):
        self.core = core
        self.discover = discover
        self.poller = select.poll()
        self.devices = {}               # fd -> [path, partial bytes, mode]
        self.running = True

    def stop(self, *_args):
        self.running = False

    def rescan(self):
        wanted = set(self.discover())
        present = {entry[0] for entry in self.devices.values()}
        for fd, (path, _partial, _mode) in list(self.devices.items()):
            if path not in wanted:
                self._drop(fd)
        for path in sorted(wanted - present):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                # Read the kernel's switch bitmap before polling: a machine can
                # boot already folded and no transition event will follow.
                mode = read_startup_mode(fd)
            except OSError:
                try:
                    os.close(fd)
                except (UnboundLocalError, OSError):
                    pass
                continue
            self.devices[fd] = [path, b"", mode]
            self.poller.register(fd, select.POLLIN | select.POLLERR |
                                 select.POLLHUP)
            self._publish_mode()
        if self.core.mode is True:
            reconcile = getattr(self.core.action, "reconcile_inhibition", None)
            if reconcile is not None:
                reconcile()

    def _publish_mode(self):
        # Firmware may expose the same physical hinge through more than one
        # input device. Laptop mode is safe only when every live source says
        # unfolded; one active source must keep tablet protections enabled.
        self.core.set_mode(any(entry[2] for entry in self.devices.values()))

    def _consume_records(self, fd, records):
        entry = self.devices.get(fd)
        if entry is None:
            return
        changed = False
        for kind, code, value in records:
            if kind == EV_SW and code == SW_TABLET_MODE:
                entry[2] = value != 0
                changed = True
        if changed:
            self._publish_mode()

    def _drop(self, fd):
        try:
            self.poller.unregister(fd)
        except (KeyError, OSError):
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        removed = self.devices.pop(fd, None)
        # Losing the last authority is not an "unfolded" event. Preserve the
        # last known safety state until a switch device returns and reports a
        # real mode; otherwise a transient driver reset can enable the folded
        # keyboard and remove the only usable on-screen keyboard.
        if removed is not None and self.devices:
            self._publish_mode()

    def run(self):
        while self.running:
            self.rescan()
            # A periodic rescan avoids a Linux-specific inotify binding and is
            # slow by design. poll() sleeps the entire no-device interval, so a
            # missing kernel driver is honest and costs no busy loop.
            deadline = time.monotonic() + RESCAN_SECONDS
            while self.running:
                timeout = max(0, int((deadline - time.monotonic()) * 1000))
                if timeout == 0:
                    break
                events = self.poller.poll(timeout)
                for fd, mask in events:
                    if mask & (select.POLLERR | select.POLLHUP):
                        self._drop(fd)
                        continue
                    try:
                        chunk = os.read(fd, EVENT.size * 32)
                    except OSError as exc:
                        if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                            self._drop(fd)
                        continue
                    if not chunk:
                        self._drop(fd)
                        continue
                    entry = self.devices.get(fd)
                    if entry is None:
                        continue
                    data = entry[1] + chunk
                    complete = len(data) - len(data) % EVENT.size
                    self._consume_records(fd, decode_events(data[:complete]))
                    entry[1] = data[complete:]
        for fd in list(self.devices):
            self._drop(fd)


def claim_single_instance(path="/tmp/xtabletd.lock"):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd                       # keeping it open keeps the flock owned


def fail_open_stop(actions, loop, *_args):
    """Signal callback: restore input synchronously before stopping the loop."""
    actions.enable_all()
    loop.stop()


def run_watch_loop(actions, loop):
    """Run a watcher with fail-open cleanup, including exceptional exits."""
    try:
        loop.run()
    finally:
        actions.enable_all()


def main():
    lock = claim_single_instance()
    if lock is None:
        return 0
    actions = Actions()
    actions.startup_heal()
    atexit.register(actions.enable_all)
    loop = DeviceLoop(ModeCore(actions))

    def stop_fail_open(*args):
        fail_open_stop(actions, loop, *args)

    signal.signal(signal.SIGTERM, stop_fail_open)
    signal.signal(signal.SIGINT, stop_fail_open)
    try:
        run_watch_loop(actions, loop)
    finally:
        actions.close()
        atexit.unregister(actions.enable_all)
        os.close(lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
