#!/usr/bin/env python3
"""The GBA checksum pass replaces a ROM atomically and preserves failures."""
import importlib.util
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot", "board", "notebookos",
                    "rootfs-overlay", "opt", "notebook", "gbaruntime",
                    "gbafix.py")
spec = importlib.util.spec_from_file_location("gbafix", PATH)
gbafix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gbafix)

with tempfile.TemporaryDirectory(prefix="gbafix-durability-") as td:
    rom = os.path.join(td, "game.gba")
    original = bytes(range(64))
    with open(rom, "wb") as fh:
        fh.write(original)
    os.chmod(rom, 0o640)

    complement, size = gbafix.fix(rom)
    fixed = open(rom, "rb").read()
    assert size == 0xC0 and len(fixed) == 0xC0
    assert complement == fixed[0xBD]
    assert ((sum(fixed[0xA0:0xBD]) + fixed[0xBD] + 0x19) & 0xFF) == 0
    assert os.stat(rom).st_mode & 0o777 == 0o640

    before = fixed
    real_replace = gbafix.os.replace
    gbafix.os.replace = lambda _src, _dst: (_ for _ in ()).throw(OSError("full"))
    try:
        try:
            gbafix.fix(rom)
            raise AssertionError("replace failure was hidden")
        except OSError as exc:
            assert str(exc) == "full"
    finally:
        gbafix.os.replace = real_replace
    assert open(rom, "rb").read() == before
    assert not [name for name in os.listdir(td) if name.startswith(".gbafix-")]

print("GBAFIX DURABILITY SELFTEST: 7 checks, all pass")
print("RESULT: ALL PASS")
