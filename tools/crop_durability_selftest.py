#!/usr/bin/env python3
"""A failed crop encoder preserves the previous inspection image."""
import importlib.util
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "crop.py")
spec = importlib.util.spec_from_file_location("crop", PATH)
crop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crop)


class Encoder:
    def __init__(self, payload, failure=None):
        self.payload = payload
        self.failure = failure

    def savev(self, path, _kind, _keys, _values):
        with open(path, "wb") as fh:
            fh.write(self.payload)
        if self.failure:
            raise self.failure


with tempfile.TemporaryDirectory(prefix="crop-durability-") as td:
    out = os.path.join(td, "detail.png")
    with open(out, "wb") as fh:
        fh.write(b"OLD")
    os.chmod(out, 0o640)

    try:
        crop._save_png(Encoder(b"PART", OSError("full")), out)
        raise AssertionError("encoder failure was hidden")
    except OSError as exc:
        assert str(exc) == "full"
    assert open(out, "rb").read() == b"OLD"
    assert os.listdir(td) == ["detail.png"]

    crop._save_png(Encoder(b"PNG"), out)
    assert open(out, "rb").read() == b"PNG"
    assert os.stat(out).st_mode & 0o777 == 0o640

print("CROP DURABILITY SELFTEST: 5 checks, all pass")
print("RESULT: ALL PASS")
