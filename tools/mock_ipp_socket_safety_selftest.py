#!/usr/bin/env python3
"""Mock printer startup removes only refused stale Unix sockets."""
import errno
import importlib.util
import os
import stat
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "mock_ipp_printer.py")
spec = importlib.util.spec_from_file_location("mock_ipp_printer", PATH)
printer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(printer)


class Probe:
    def __init__(self, failure=None):
        self.failure = failure
        self.closed = False

    def settimeout(self, value):
        assert value == 0.5

    def connect(self, _path):
        if self.failure:
            raise self.failure

    def close(self):
        self.closed = True


with tempfile.TemporaryDirectory(prefix="ipp-socket-safety-") as td:
    path = os.path.join(td, "printer.sock")
    open(path, "wb").write(b"KEEP")
    try:
        printer._prepare_socket(path)
        raise AssertionError("ordinary file was accepted as a socket")
    except FileExistsError:
        pass
    assert open(path, "rb").read() == b"KEEP"

    real_lstat, real_socket = printer.os.lstat, printer.socket.socket
    printer.os.lstat = lambda _path: type("S", (), {"st_mode": stat.S_IFSOCK})()
    stale = Probe(ConnectionRefusedError(errno.ECONNREFUSED, "stale"))
    printer.socket.socket = lambda *_args: stale
    try:
        printer._prepare_socket(path)
    finally:
        printer.os.lstat, printer.socket.socket = real_lstat, real_socket
    assert not os.path.exists(path)
    assert stale.closed

    open(path, "wb").close()
    printer.os.lstat = lambda _path: type("S", (), {"st_mode": stat.S_IFSOCK})()
    live = Probe()
    printer.socket.socket = lambda *_args: live
    try:
        try:
            printer._prepare_socket(path)
            raise AssertionError("live listener was accepted")
        except OSError as exc:
            assert exc.errno == errno.EADDRINUSE
    finally:
        printer.os.lstat, printer.socket.socket = real_lstat, real_socket
    assert os.path.exists(path)
    assert live.closed

print("MOCK IPP SOCKET SAFETY SELFTEST: 6 checks, all pass")
print("RESULT: ALL PASS")
