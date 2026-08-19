#!/usr/bin/env python3
"""A failed map publication preserves the last valid region binary."""
import importlib.util
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "osm2nbmap.py")
spec = importlib.util.spec_from_file_location("osm2nbmap", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

features = [(module.CAT["road_minor"], False, "Main",
             [(1.0, 2.0), (1.5, 2.5)])]
bounds = (1.0, 2.0, 1.5, 2.5)

with tempfile.TemporaryDirectory(prefix="nbmap-durability-") as td:
    output = os.path.join(td, "region.nbmap")
    with open(output, "wb") as fh:
        fh.write(b"OLD")
    os.chmod(output, 0o640)
    module._write_map(output, "Test", features, bounds)
    good = open(output, "rb").read()
    assert good.startswith(b"NBMAP1\n")
    assert os.stat(output).st_mode & 0o777 == 0o640

    real_replace = module.os.replace
    module.os.replace = lambda _src, _dst: (_ for _ in ()).throw(OSError("full"))
    try:
        try:
            module._write_map(output, "Changed", features, bounds)
            raise AssertionError("replace failure was hidden")
        except OSError as exc:
            assert str(exc) == "full"
    finally:
        module.os.replace = real_replace

    assert open(output, "rb").read() == good
    assert os.stat(output).st_mode & 0o777 == 0o640
    assert os.listdir(td) == ["region.nbmap"]

print("OSM2NBMAP DURABILITY SELFTEST: 7 checks, all pass")
print("RESULT: ALL PASS")
