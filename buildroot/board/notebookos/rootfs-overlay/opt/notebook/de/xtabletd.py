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
window as NB_TABLET_MODE.  The on-screen keyboard is also best effort:
matchbox-keyboard is not yet in the image (BR2_PACKAGE_MATCHBOX_KEYBOARD is
queued), but when it ships its path will be /usr/bin/matchbox-keyboard.

The small parsing and state-machine core is deliberately independent of device,
process, and X I/O so it can be exercised without hardware or a display.
"""
import errno
import fcntl
import glob
import os
import select
import signal
import struct
import subprocess
import sys
import time

EV_SYN = 0
EV_KEY = 1
EV_SW = 5
SW_TABLET_MODE = 1
EVENT = struct.Struct("llHHi")       # Linux input_event on x86-64
SW_BYTES = 8                         # comfortably covers current SW_MAX
RESCAN_SECONDS = 5


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


class Actions:
    """Publish the state and own exactly the keyboard process we start."""

    def __init__(self, flag="/tmp/nb-tablet-mode",
                 keyboard="/usr/bin/matchbox-keyboard", stderr=None):
        self.flag = flag
        self.keyboard = keyboard
        self.stderr = stderr or sys.stderr
        self.child = None
        self._missing_reported = False

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

    def _start_keyboard(self):
        if self.child is not None and self.child.poll() is None:
            return
        self.child = None
        if not (os.path.isfile(self.keyboard) and os.access(self.keyboard, os.X_OK)):
            if not self._missing_reported:
                print("xtabletd: matchbox-keyboard is not installed",
                      file=self.stderr)
                self._missing_reported = True
            return
        try:
            self.child = subprocess.Popen([self.keyboard],
                                          start_new_session=True,
                                          stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
        except OSError as exc:
            if not self._missing_reported:
                print("xtabletd: cannot start matchbox-keyboard: %s" % exc,
                      file=self.stderr)
                self._missing_reported = True

    def _stop_keyboard(self):
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
            # SIGTERM is intentionally the strongest action: do not kill an
            # OSK that is merely taking time to save/close.
            pass

    def __call__(self, enabled):
        self._write_flag(enabled)
        if enabled:
            self._start_keyboard()
        else:
            self._stop_keyboard()
        self._set_x_property(enabled)

    def close(self):
        self._stop_keyboard()
        try:
            os.unlink(self.flag)
        except FileNotFoundError:
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
        self.devices = {}               # fd -> [path, partial bytes]
        self.running = True

    def stop(self, *_args):
        self.running = False

    def rescan(self):
        wanted = set(self.discover())
        present = {entry[0] for entry in self.devices.values()}
        for fd, (path, _partial) in list(self.devices.items()):
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
            self.devices[fd] = [path, b""]
            self.poller.register(fd, select.POLLIN | select.POLLERR |
                                 select.POLLHUP)
            self.core.set_mode(mode)

    def _drop(self, fd):
        try:
            self.poller.unregister(fd)
        except (KeyError, OSError):
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        self.devices.pop(fd, None)

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
                    self.core.feed(decode_events(data[:complete]))
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


def main():
    lock = claim_single_instance()
    if lock is None:
        return 0
    actions = Actions()
    loop = DeviceLoop(ModeCore(actions))
    signal.signal(signal.SIGTERM, loop.stop)
    signal.signal(signal.SIGINT, loop.stop)
    try:
        loop.run()
    finally:
        actions.close()
        os.close(lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
