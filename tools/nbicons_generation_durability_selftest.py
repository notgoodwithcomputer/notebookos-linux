#!/usr/bin/env python3
"""A failed icon-table publication preserves the existing runtime asset."""
import importlib.util
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "gen_nbicons.py")
spec = importlib.util.spec_from_file_location("gen_nbicons", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory(prefix="nbicons-durability-") as td:
    output = os.path.join(td, "nbicons_data.py")
    with open(output, "w", encoding="utf-8") as fh:
        fh.write("OLD\n")
    os.chmod(output, 0o640)

    module._write_atomic(output, "NEW\n")
    assert open(output, encoding="utf-8").read() == "NEW\n"
    assert os.stat(output).st_mode & 0o777 == 0o640

    real_replace = module.os.replace
    module.os.replace = lambda _src, _dst: (_ for _ in ()).throw(OSError("full"))
    try:
        try:
            module._write_atomic(output, "PARTIAL\n")
            raise AssertionError("replace failure was hidden")
        except OSError as exc:
            assert str(exc) == "full"
    finally:
        module.os.replace = real_replace

    assert open(output, encoding="utf-8").read() == "NEW\n"
    assert os.stat(output).st_mode & 0o777 == 0o640
    assert os.listdir(td) == ["nbicons_data.py"]

print("NBICONS GENERATION DURABILITY SELFTEST: 7 checks, all pass")
print("RESULT: ALL PASS")
