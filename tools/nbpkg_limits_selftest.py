#!/usr/bin/env python3
"""A tiny compressed package must not expand an unbounded manifest in RAM."""

import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import nbpkg_install  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="nbpkg-limit-") as tmp:
        pkg = os.path.join(tmp, "bomb.nbpkg")
        pub = os.path.join(tmp, "pub")
        Path(pub).write_text("placeholder")
        with tarfile.open(pkg, "w:gz") as tar:
            huge = b"0" * (nbpkg_install.MAX_MANIFEST + 1)
            info = tarfile.TarInfo("manifest.json")
            info.size = len(huge)
            tar.addfile(info, io.BytesIO(huge))
            sig = tarfile.TarInfo("manifest.sig")
            sig.size = 0
            tar.addfile(sig, io.BytesIO())
        try:
            nbpkg_install.inspect(pkg, pub=pub)
            ok = False
        except nbpkg_install.PkgError as exc:
            ok = "too large" in str(exc)
    print(("PASS" if ok else "FAIL") + ": oversized manifest rejected before read")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return not ok


if __name__ == "__main__":
    raise SystemExit(main())
