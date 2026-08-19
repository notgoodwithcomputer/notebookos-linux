#!/usr/bin/env python3
"""Only the configuration embedded in the shipped kernel may certify it."""

from pathlib import Path
import os
import tempfile
from types import SimpleNamespace

import kernel_hardening_check as gate


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        image = root / "bzImage"
        extractor = root / "extract-ikconfig"
        image.write_bytes(b"old kernel")
        extractor.write_text("#!/bin/sh\n", encoding="utf-8")
        absent = lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="")
        embedded = lambda *_a, **_k: SimpleNamespace(
            returncode=0, stdout="CONFIG_SECURITY_LOCKDOWN_LSM=y\n")
        assert gate.artifact_config(os.fspath(image), os.fspath(extractor),
                                    absent) is None
        assert "CONFIG_SECURITY" in gate.artifact_config(
            os.fspath(image), os.fspath(extractor), embedded)
        image.unlink()
        assert gate.artifact_config(os.fspath(image), os.fspath(extractor),
                                    embedded) is None
    print("PASS only an embedded artifact config can receive kernel approval")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
