#!/usr/bin/env python3
"""Locale updates never replace bytes whose quarantine failed."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import nbi18n  # noqa: E402


with tempfile.TemporaryDirectory(prefix="nb-i18n-damage-") as home:
    old_home = os.environ.get("NB_HOME")
    os.environ["NB_HOME"] = home
    path = Path(home) / ".config/notebook/locale.json"
    path.parent.mkdir(parents=True)
    for original in (b'{"keyboard":', b""):
        path.write_bytes(original)
        real_replace = nbi18n.os.replace
        nbi18n.os.replace = lambda *_args: (_ for _ in ()).throw(OSError("read only"))
        try:
            assert nbi18n._update_locale(keyboard="ru,us") is False
        finally:
            nbi18n.os.replace = real_replace
        assert path.read_bytes() == original
    if old_home is None:
        os.environ.pop("NB_HOME", None)
    else:
        os.environ["NB_HOME"] = old_home
    print("PASS malformed and zero-byte locale stores fail closed")

print("RESULT: ALL PASS")
