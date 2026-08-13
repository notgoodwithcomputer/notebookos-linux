#!/usr/bin/env python3
"""
Client library for the Govorimo daemon (govorimod), plus the E22 dongle
provisioner. No GTK — the app imports this, and so do headless tools.

Two independent halves:

DAEMON LINK. govorimod owns the radio, the framing, the crypto, the mesh and
the message store; a client is UI only and never sees key material. The wire
is newline-delimited JSON over a Unix socket (spec/08-local-api.md in the
Govorimo source tree): requests carry an id that the response echoes, events
arrive unsolicited with no id, and the first call on every connection must be
`hello`. DaemonLink integrates that socket with the GLib main loop so an app
never blocks on it: call() takes a callback, events fan out to subscribers,
and a lost daemon (a dongle unplugged mid-session restarts it) is survived by
retrying once a second until the socket answers again. BlockingClient is the
same wire for scripts and suites that have no main loop.

E22 PROVISIONER. The Ebyte E22-900T22U reaches the daemon as /dev/lora
(docs/LORA-DONGLE.md), but a factory-fresh module is on the EU band at the
wrong UART rate with software mode switching disabled — it must be provisioned
once, in configuration mode (LED red, entered by holding the module's button
>1.5 s), with the standard profile: address 0xFFFF (a dumb broadcast pipe),
UART 115200, air rate 9.6k, channel 68 = 918.125 MHz (US/Canada plan),
subpacket 240, ambient RSSI + per-packet RSSI byte, LBT, software mode
switching. After that one step the host has full software control and the
daemon manages modes itself. The byte protocol here mirrors the daemon's own
driver (core/src/radio/regs.rs) exactly; configuration mode may answer at
9600 regardless of the profile, so every probe tries both rates.
"""

import errno
import json
import os
import select
import socket
import sys
import tempfile
import termios
import time

from gi.repository import GLib

API_VERSION = "0.1"

# ----------------------------------------------------------------- socket path


def socket_path():
    """Where govorimod listens. Mirrors the daemon's own resolution order."""
    p = os.environ.get("GOVORIMO_SOCKET")
    if p:
        return p
    d = os.environ.get("XDG_RUNTIME_DIR")
    if d:
        return os.path.join(d, "govorimo.sock")
    return os.path.join(tempfile.gettempdir(), "govorimo-%d.sock" % os.getuid())


# ------------------------------------------------------------------ DaemonLink

CONNECTING = "connecting"   # no daemon yet; retrying once a second
READY = "ready"             # hello answered; calls and events flow
MISMATCH = "mismatch"       # daemon speaks another API major — fatal, no retry

_RETRY_MS = 1000
_CALL_TIMEOUT_S = 15.0


class DaemonLink:
    """The daemon socket on the GLib main loop. Everything is a callback on
    the main loop; nothing here blocks and nothing runs on another thread."""

    def __init__(self, client_name="notebook", path=None):
        self.client_name = client_name
        self.path = path or socket_path()
        self.state = CONNECTING
        self.state_detail = ""
        self.hello = {}          # last hello result while READY
        self._sock = None
        self._buf = b""
        self._out = b""
        self._in_watch = None
        self._out_watch = None
        self._retry_src = None
        self._next_id = 0
        self._pending = {}       # id -> (on_done, timeout_source)
        self._subs = {}          # sub id -> (event name or "*", fn)
        self._next_sub = 0
        self._state_subs = {}
        self._started = False

    # -- subscriptions

    def on_event(self, name, fn):
        """fn(event_name, data_dict). name "*" hears everything."""
        self._next_sub += 1
        self._subs[self._next_sub] = (name, fn)
        return self._next_sub

    def off_event(self, sub_id):
        self._subs.pop(sub_id, None)

    def on_state(self, fn):
        """fn(state, detail). Called immediately with the current state."""
        self._next_sub += 1
        self._state_subs[self._next_sub] = fn
        self._guard(fn, self.state, self.state_detail)
        return self._next_sub

    def off_state(self, sub_id):
        self._state_subs.pop(sub_id, None)

    # -- lifecycle

    def start(self):
        if not self._started:
            self._started = True
            self._connect()

    def stop(self):
        self._started = False
        self._teardown(fail_pending=True)
        if self._retry_src is not None:
            GLib.source_remove(self._retry_src)
            self._retry_src = None

    # -- calls

    def call(self, method, params=None, on_done=None, timeout=_CALL_TIMEOUT_S):
        """Send one request. on_done(result, error): exactly one is not None.
        error is {"code", "message", ...}; a dead link fails fast with code
        "gone" rather than waiting on a socket that is not there."""
        if self.state != READY:
            if on_done is not None:
                GLib.idle_add(self._guard, on_done, None,
                              {"code": "gone", "message": "govorimod is not answering"})
            return
        self._next_id += 1
        rid = self._next_id
        line = json.dumps({"id": rid, "method": method, "params": params or {}},
                          ensure_ascii=False)
        src = None
        if on_done is not None:
            src = GLib.timeout_add(int(timeout * 1000), self._call_timed_out, rid)
        self._pending[rid] = (on_done, src)
        self._send(line.encode("utf-8") + b"\n")

    def _call_timed_out(self, rid):
        on_done, _ = self._pending.pop(rid, (None, None))
        if on_done is not None:
            self._guard(on_done, None,
                        {"code": "gone", "message": "govorimod did not answer in time"})
        return False

    # -- connection machinery

    def _connect(self):
        self._retry_src = None
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.setblocking(False)
        rc = s.connect_ex(self.path)
        if rc in (0, errno.EINPROGRESS, errno.EAGAIN):
            self._sock = s
            self._out_watch = GLib.io_add_watch(
                s.fileno(), GLib.PRIORITY_DEFAULT,
                GLib.IO_OUT | GLib.IO_ERR | GLib.IO_HUP, self._connect_done)
        else:
            s.close()
            self._set_state(CONNECTING, os.strerror(rc))
            self._schedule_retry()
        return False

    def _connect_done(self, _fd, cond):
        self._out_watch = None
        err = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if cond & (GLib.IO_ERR | GLib.IO_HUP) or err != 0:
            self._teardown(fail_pending=False)
            self._set_state(CONNECTING, os.strerror(err) if err else "the radio service is not running")
            self._schedule_retry()
            return False
        self._in_watch = GLib.io_add_watch(
            self._sock.fileno(), GLib.PRIORITY_DEFAULT,
            GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP, self._readable)
        # The mandatory first call. Its response flips the link to READY.
        self._next_id += 1
        rid = self._next_id
        self._pending[rid] = (self._hello_done, None)
        line = json.dumps({"id": rid, "method": "hello",
                           "params": {"client": self.client_name,
                                      "version": API_VERSION}})
        self._send(line.encode("utf-8") + b"\n")
        return False

    def _hello_done(self, result, error):
        if error is not None:
            self._teardown(fail_pending=True)
            self._set_state(CONNECTING, error.get("message", ""))
            self._schedule_retry()
            return
        api = str(result.get("api_version", ""))
        if api.split(".")[0] != API_VERSION.split(".")[0]:
            # A major mismatch is fatal by contract; retrying cannot fix it.
            self._teardown(fail_pending=True)
            self._set_state(MISMATCH,
                            "the radio service and this app do not match (service %s, app %s)"
                            % (api, API_VERSION))
            return
        self.hello = result
        self._set_state(READY, "")

    def _schedule_retry(self):
        if self._started and self._retry_src is None and self.state != MISMATCH:
            self._retry_src = GLib.timeout_add(_RETRY_MS, self._connect)

    def _teardown(self, fail_pending):
        for watch in (self._in_watch, self._out_watch):
            if watch is not None:
                GLib.source_remove(watch)
        self._in_watch = self._out_watch = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buf = b""
        self._out = b""
        self.hello = {}
        if fail_pending:
            pending, self._pending = self._pending, {}
            for on_done, src in pending.values():
                if src is not None:
                    GLib.source_remove(src)
                if on_done is not None:
                    self._guard(on_done, None,
                                {"code": "gone", "message": "the radio service stopped"})

    def _lost(self, detail):
        self._teardown(fail_pending=True)
        self._set_state(CONNECTING, detail)
        self._schedule_retry()

    # -- I/O

    def _send(self, data):
        self._out += data
        if self._sock is None:
            return
        try:
            n = self._sock.send(self._out)
            self._out = self._out[n:]
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                self._lost(str(e))
                return
        if self._out and self._out_watch is None:
            self._out_watch = GLib.io_add_watch(
                self._sock.fileno(), GLib.PRIORITY_DEFAULT,
                GLib.IO_OUT, self._writable)

    def _writable(self, _fd, _cond):
        try:
            if self._out:
                n = self._sock.send(self._out)
                self._out = self._out[n:]
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                self._out_watch = None
                self._lost(str(e))
                return False
        if not self._out:
            self._out_watch = None
            return False
        return True

    def _readable(self, _fd, cond):
        if cond & (GLib.IO_ERR | GLib.IO_HUP):
            self._in_watch = None
            self._lost("the radio service stopped")
            return False
        try:
            while True:
                chunk = self._sock.recv(65536)
                if not chunk:
                    self._in_watch = None
                    self._lost("the radio service closed the connection")
                    return False
                self._buf += chunk
                if len(chunk) < 65536:
                    break
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                self._in_watch = None
                self._lost(str(e))
                return False
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            if line.strip():
                self._dispatch(line)
        return True

    def _dispatch(self, line):
        try:
            v = json.loads(line)
        except ValueError:
            return
        if not isinstance(v, dict):
            return
        if "id" in v and v["id"] in self._pending:
            on_done, src = self._pending.pop(v["id"])
            if src is not None:
                GLib.source_remove(src)
            if on_done is None:
                return
            if v.get("ok"):
                self._guard(on_done, v.get("result"), None)
            else:
                err = v.get("error")
                if not isinstance(err, dict):
                    err = {"code": "internal", "message": "malformed error"}
                self._guard(on_done, None, err)
        elif "event" in v:
            name = v.get("event", "")
            data = v.get("data")
            if not isinstance(data, dict):
                data = {}
            for want, fn in list(self._subs.values()):
                if want == "*" or want == name:
                    self._guard(fn, name, data)

    def _set_state(self, state, detail):
        changed = (state != self.state) or (detail != self.state_detail)
        self.state = state
        self.state_detail = detail
        if changed:
            for fn in list(self._state_subs.values()):
                self._guard(fn, state, detail)

    @staticmethod
    def _guard(fn, *args):
        # A listener that raises must not take the io watch down with it.
        try:
            fn(*args)
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)
        return False


# -------------------------------------------------------------- BlockingClient


class BlockingClient:
    """The same wire, synchronous, for suites and command-line tools. Connects,
    performs the mandatory hello, and then call() blocks for its response
    while collecting any events that arrive in between."""

    def __init__(self, path=None, client_name="notebook-tool", timeout=15.0):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path or socket_path())
        self.sock.settimeout(timeout)
        self._buf = b""
        self._next_id = 0
        self.events = []
        self.hello = self.call("hello",
                               client=client_name, version=API_VERSION)["result"]

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _read_line(self):
        while b"\n" not in self._buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("govorimod closed the socket")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

    def call(self, method, **params):
        """Returns the raw response object {"id", "ok", "result"|"error"}."""
        self._next_id += 1
        line = json.dumps({"id": self._next_id, "method": method, "params": params})
        self.sock.sendall(line.encode("utf-8") + b"\n")
        while True:
            v = self._read_line()
            if v.get("id") == self._next_id:
                return v
            if "event" in v:
                self.events.append(v)

    def ok(self, method, **params):
        """Returns the result, raising on an API error."""
        v = self.call(method, **params)
        if not v.get("ok"):
            raise RuntimeError("%s: %s" % (method, v.get("error")))
        return v["result"]

    def wait_event(self, name, secs=15.0, where=None):
        deadline = time.monotonic() + secs
        while True:
            for e in list(self.events):
                data = e.get("data", {})
                if e.get("event") == name and (where is None or where(data)):
                    self.events.remove(e)
                    return data
            if time.monotonic() >= deadline:
                return None
            try:
                v = self._read_line()
            except socket.timeout:
                continue
            if "event" in v:
                self.events.append(v)


# ---------------------------------------------------------- E22 provisioning
#
# Byte protocol, mirrored from the daemon's driver so the two can never
# disagree: read = C1 start len -> C1 start len data..., persistent write =
# C0 start len data... (echoed as C1...), a malformed command answers
# FF FF FF, and mode switching (once enabled) is C0 C1 C2 C3 02 <mode>.

PROFILE_US = bytes((0xFF, 0xFF, 0x00, 0xE4, 0x24, 0x44, 0x93, 0x00, 0x00))
_CONFIG_BAUDS = (termios.B9600, termios.B115200)
_BAUD_NAMES = {termios.B9600: 9600, termios.B115200: 115200}
_CMD_TIMEOUT = 0.4


class ProvisionError(Exception):
    """What did not happen, why, and what the person can do — the message is
    shown verbatim in the app's provisioning card."""


def _open_raw(dev, baud):
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    # cfmakeraw, spelled out: 8N1, no flow control, no translation, no echo.
    attrs[0] = 0                                   # iflag
    attrs[1] = 0                                   # oflag
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0                                   # lflag
    attrs[4] = baud                                # ispeed
    attrs[5] = baud                                # ospeed
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def _xfer(fd, cmd, want, timeout=_CMD_TIMEOUT):
    """Write cmd, read until `want` bytes or an FF FF FF error or silence."""
    os.write(fd, bytes(cmd))
    reply = b""
    deadline = time.monotonic() + timeout
    while len(reply) < want:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        r, _, _ = select.select([fd], [], [], left)
        if not r:
            break
        chunk = os.read(fd, 64)
        if chunk:
            reply += chunk
            if reply[:3] == b"\xff\xff\xff":
                return reply[:3]
    return reply


def _read_regs(fd, start, count):
    """One C1 read. Returns the data bytes or None."""
    reply = _xfer(fd, bytes((0xC1, start, count)), 3 + count)
    if len(reply) == 3 + count and reply[0] == 0xC1 \
            and reply[1] == start and reply[2] == count:
        return reply[3:]
    return None


def probe(dev="/dev/lora"):
    """What state is the dongle in? Non-destructive. Returns a dict:
    mode      "config" | "transfer" | "silent"
    baud      the UART rate that answered, or None
    registers first 7 registers (ADDH..REG3) when readable, else None
    provisioned  True/False when determinable, else None
    """
    if not os.path.exists(dev):
        # Fixed sentences only: the app shows these through its catalog, so a
        # device path interpolated here would break the translation key.
        raise ProvisionError(
            "No radio is attached. The port appears by itself when the "
            "dongle is plugged in.")
    for baud in _CONFIG_BAUDS:
        fd = _open_raw(dev, baud)
        try:
            regs = _read_regs(fd, 0x00, 7)
            if regs is not None:
                return {"mode": "config", "baud": _BAUD_NAMES[baud],
                        "registers": bytes(regs),
                        "provisioned": bytes(regs) == PROFILE_US[:7]}
        finally:
            os.close(fd)
    # Not in configuration mode. A provisioned module in transfer mode
    # answers the ambient-noise query (REG1 bit 5); a factory-fresh one
    # stays silent because that query needs the provisioned profile.
    for baud in reversed(_CONFIG_BAUDS):
        fd = _open_raw(dev, baud)
        try:
            reply = _xfer(fd, bytes((0xC0, 0xC1, 0xC2, 0xC3, 0x00, 0x01)), 4)
            if len(reply) >= 4 and reply[0] == 0xC1 and reply[1] == 0x00:
                return {"mode": "transfer", "baud": _BAUD_NAMES[baud],
                        "registers": None, "provisioned": True}
        finally:
            os.close(fd)
    return {"mode": "silent", "baud": None, "registers": None,
            "provisioned": None}


def provision(dev="/dev/lora"):
    """Write the standard profile persistently and verify the readback.
    The module must already be in configuration mode (LED red — hold the
    button >1.5 s); raises ProvisionError with the exact instruction if not.
    Returns {"before": bytes, "after": bytes, "baud": int}."""
    state = probe(dev)
    if state["mode"] != "config":
        raise ProvisionError(
            "The radio did not answer in configuration mode. Hold the button "
            "on the dongle for two seconds, until the LED turns red, then "
            "provision again.")
    baud = termios.B9600 if state["baud"] == 9600 else termios.B115200
    fd = _open_raw(dev, baud)
    try:
        before = state["registers"]
        echo = _xfer(fd, bytes((0xC0, 0x00, len(PROFILE_US))) + PROFILE_US,
                     3 + len(PROFILE_US))
        if echo[:3] == b"\xff\xff\xff":
            raise ProvisionError(
                "The radio rejected the profile write. Unplug the dongle, "
                "plug it back in, hold the button until the LED turns red, "
                "and provision again.")
        after = _read_regs(fd, 0x00, 7)
        if after is None or bytes(after) != PROFILE_US[:7]:
            raise ProvisionError(
                "The profile did not read back after writing. Unplug the "
                "dongle, plug it back in, and provision again.")
        # Leave the module in transfer mode so the daemon can open it
        # without a second button press. Software switching is enabled by
        # the profile just written, so this succeeds silently.
        os.write(fd, bytes((0xC0, 0xC1, 0xC2, 0xC3, 0x02, 0x00)))
        time.sleep(0.06)
        # Stamp completion for the session supervisor (govorimod-run.sh):
        # its probes are event-gated, and this is the event that tells it
        # the dongle is now worth probing again.
        stamp = os.environ.get("GOVORIMO_STAMP",
                               "/run/govorimo-provisioned.stamp")
        try:
            with open(stamp, "w") as f:
                f.write("%d\n" % int(time.time()))
        except OSError:
            pass  # no /run outside the guest session; the probe button covers it
        return {"before": bytes(before) if before else b"",
                "after": bytes(after), "baud": state["baud"]}
    finally:
        os.close(fd)


# ------------------------------------------------------------------ airtime
#
# The composer prices every message before it is sent. One byte is ~1.04 ms
# at the standard 9.6k rate (preamble and header amortised); the true figure
# comes from the daemon's budget enforcement — this is honest arithmetic for
# display, not a promise.

FRAME_OVERHEAD = 31          # header + tag around the L5 payload
CHAT_HEADER = 6              # msg_type, flags, Lamport clock
MAX_TEXT_BYTES = 209         # one frame's worth of chat body


def airtime_ms(payload_bytes, air_rate_bps=9600):
    """Milliseconds of shared channel one frame of this payload costs."""
    total = payload_bytes + CHAT_HEADER + FRAME_OVERHEAD
    return total * 8 * 1000.0 / air_rate_bps


if __name__ == "__main__":
    # Command-line probe, for bring-up on real hardware:
    #   python3 govorimolib.py probe [/dev/lora]
    #   python3 govorimolib.py provision [/dev/lora]
    verb = sys.argv[1] if len(sys.argv) > 1 else "probe"
    port = sys.argv[2] if len(sys.argv) > 2 else "/dev/lora"
    try:
        if verb == "provision":
            r = provision(port)
            print("provisioned; registers now %s (was %s), config at %d baud"
                  % (r["after"].hex(), r["before"].hex() or "unreadable",
                     r["baud"]))
        else:
            r = probe(port)
            print("mode=%s baud=%s provisioned=%s regs=%s"
                  % (r["mode"], r["baud"], r["provisioned"],
                     r["registers"].hex() if r["registers"] else "-"))
    except ProvisionError as e:
        print("cannot: %s" % e)
        sys.exit(1)
