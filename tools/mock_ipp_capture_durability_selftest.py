#!/usr/bin/env python3
"""Failed printer capture publication preserves the prior received PDF."""
import importlib.util
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "mock_ipp_printer.py")
spec = importlib.util.spec_from_file_location("mock_ipp_printer", PATH)
printer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(printer)

with tempfile.TemporaryDirectory(prefix="ipp-capture-") as td:
    saved = os.path.join(td, "received.pdf")
    with open(saved, "wb") as fh:
        fh.write(b"OLD")
    os.chmod(saved, 0o640)

    mock = printer.Printer(saved)
    real_replace = printer.os.replace
    printer.os.replace = lambda _src, _dst: (_ for _ in ()).throw(OSError("full"))
    try:
        try:
            mock.print_job({}, b"PARTIAL")
            raise AssertionError("capture failure was hidden")
        except OSError as exc:
            assert str(exc) == "full"
    finally:
        printer.os.replace = real_replace
    assert open(saved, "rb").read() == b"OLD"
    assert mock.jobs == {} and mock.received == [] and mock.next_id == 101
    assert os.listdir(td) == ["received.pdf"]

    jid = mock.print_job({}, b"PDF")
    assert jid == 101 and open(saved, "rb").read() == b"PDF"
    assert os.stat(saved).st_mode & 0o777 == 0o640

print("MOCK IPP CAPTURE DURABILITY SELFTEST: 6 checks, all pass")
print("RESULT: ALL PASS")
