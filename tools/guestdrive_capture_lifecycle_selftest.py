#!/usr/bin/env python3
"""Failed guest screenshots release their owned temporary framebuffer."""
import importlib.util
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "guestdrive.py")
spec = importlib.util.spec_from_file_location("guestdrive", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BrokenQmp:
    def cmd(self, *_args, **_kwargs):
        raise RuntimeError("guest disconnected")


with tempfile.TemporaryDirectory(prefix="guestdrive-lifecycle-") as td:
    ppm = os.path.join(td, "capture.ppm")
    real_mkstemp = module.tempfile.mkstemp

    def owned_temp(**_kwargs):
        return os.open(ppm, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600), ppm

    module.tempfile.mkstemp = owned_temp
    try:
        try:
            module.shot(BrokenQmp(), os.path.join(td, "out.png"))
            raise AssertionError("QMP failure was hidden")
        except RuntimeError as exc:
            assert str(exc) == "guest disconnected"
    finally:
        module.tempfile.mkstemp = real_mkstemp
    assert not os.path.exists(ppm)
    assert not os.path.exists(os.path.join(td, "out.png"))

source = open(PATH, encoding="utf-8").read()
assert "finally:" in source[source.index("def shot"):source.index("def main")]

print("GUESTDRIVE CAPTURE LIFECYCLE SELFTEST: 4 checks, all pass")
