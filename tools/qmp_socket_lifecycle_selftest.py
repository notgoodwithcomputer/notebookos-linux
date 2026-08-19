#!/usr/bin/env python3
"""QMP boot rejects live listeners but heals sockets left by crashed guests."""
import importlib.util
import os
import signal
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "qmp.py")
spec = importlib.util.spec_from_file_location("qmp", PATH)
qmp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qmp)


class Probe:
    def __init__(self, failure=None):
        self.failure = failure
        self.closed = False
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def connect(self, _path):
        if self.failure:
            raise self.failure

    def close(self):
        self.closed = True


with tempfile.TemporaryDirectory(prefix="qmp-socket-") as td:
    path = os.path.join(td, "qmp.sock")
    assert qmp._prepare_boot_socket(path)

    open(path, "wb").close()  # stand-in inode; socket syscalls are sandboxed
    stale = Probe(ConnectionRefusedError())
    real_socket = qmp.socket.socket
    qmp.socket.socket = lambda *_args: stale
    try:
        assert qmp._prepare_boot_socket(path)
    finally:
        qmp.socket.socket = real_socket
    assert not os.path.exists(path)
    assert stale.closed and stale.timeout == 0.5

    open(path, "wb").close()
    live = Probe()
    qmp.socket.socket = lambda *_args: live
    try:
        assert not qmp._prepare_boot_socket(path)
    finally:
        qmp.socket.socket = real_socket
    assert os.path.exists(path)
    assert live.closed


class Process:
    pid = 4321

    def __init__(self, running=True):
        self.running = running
        self.waits = []

    def poll(self):
        return None if self.running else 0

    def wait(self, timeout=None):
        self.waits.append(timeout)
        self.running = False
        return 0


kills = []
real_killpg = qmp.os.killpg
qmp.os.killpg = lambda pid, sig: kills.append((pid, sig))
try:
    running = Process()
    qmp._stop_boot_process(running)
    exited = Process(running=False)
    qmp._stop_boot_process(exited)
finally:
    qmp.os.killpg = real_killpg
assert kills == [(4321, signal.SIGTERM)]
assert running.waits == [5]
assert exited.waits == [5]


class Capture:
    def __init__(self, result, payload):
        self.result = result
        self.payload = payload

    def cmd(self, _name, **args):
        with open(args["filename"], "wb") as fh:
            fh.write(self.payload)
        return self.result


with tempfile.TemporaryDirectory(prefix="qmp-capture-") as td:
    out = os.path.join(td, "shot.png")
    with open(out, "wb") as fh:
        fh.write(b"OLD")
    os.chmod(out, 0o640)
    error = qmp._capture(Capture({"error": {"desc": "failed"}}, b"PART"), out)
    assert "error" in error and open(out, "rb").read() == b"OLD"
    assert os.listdir(td) == ["shot.png"]
    ok = qmp._capture(Capture({"return": {}}, b"PNG"), out)
    assert "return" in ok and open(out, "rb").read() == b"PNG"
    assert os.stat(out).st_mode & 0o777 == 0o640


class Fragments:
    def __init__(self):
        self.chunks = [b'{"event":"READY"}\r\n{"ret',
                       b'urn":{"ok":true}}\r\n']
        self.sent = []

    def recv(self, _size):
        return self.chunks.pop(0) if self.chunks else b""

    def sendall(self, data):
        self.sent.append(data)


framed = qmp.Qmp.__new__(qmp.Qmp)
framed.s = Fragments()
framed._buf = b""
assert framed._read() == '{"event":"READY"}\r'
assert framed._buf == b'{"ret'
reply = framed.cmd("query-status")
assert reply == {"return": {"ok": True}}
assert framed._buf == b""

closed = qmp.Qmp.__new__(qmp.Qmp)
closed.s = Fragments()
closed.s.chunks = []
closed._buf = b""
try:
    closed._read()
    raise AssertionError("closed QMP connection was hidden")
except ConnectionError:
    pass

print("QMP SOCKET LIFECYCLE SELFTEST: 19 checks, all pass")
print("RESULT: ALL PASS")
