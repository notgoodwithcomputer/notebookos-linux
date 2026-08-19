#!/usr/bin/env python3
"""Display-free rollback checks for both package installer entry points."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import nbpkg_install  # noqa: E402

spec = importlib.util.spec_from_file_location("host_nbpkg", ROOT / "tools/nbpkg.py")
host_nbpkg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host_nbpkg)


def exercise(module, label):
    with tempfile.TemporaryDirectory(prefix="nbpkg-rollback-") as root:
        first = os.path.join(root, "first.py")
        second = os.path.join(root, "second.py")
        Path(first).write_bytes(b"original")
        real_replace = module.os.replace

        def fail_second(src, dst):
            if dst == second and src.endswith(".nbpkg-tmp"):
                raise OSError("injected second replace failure")
            return real_replace(src, dst)

        module.os.replace = fail_second
        try:
            try:
                module._publish_transaction([
                    (first, b"replacement", 0o644),
                    (second, b"new", 0o644),
                ])
            except OSError:
                pass
            else:
                raise AssertionError("failure injection did not fire")
        finally:
            module.os.replace = real_replace
        leftovers = [name for name in os.listdir(root)
                     if "nbpkg-tmp" in name or "nbpkg-backup" in name]
        assert Path(first).read_bytes() == b"original", (label, "old lost")
        assert not os.path.exists(second), (label, "partial new file")
        assert not leftovers, (label, leftovers)
        print("PASS %s rolls back a later publish failure" % label)


exercise(host_nbpkg, "host installer")
exercise(nbpkg_install, "on-device installer")
print("RESULT: ALL PASS")
