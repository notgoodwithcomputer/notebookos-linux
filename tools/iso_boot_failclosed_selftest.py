#!/usr/bin/env python3
"""The release boot checker must not pass without ISO/El Torito evidence."""

from pathlib import Path
from unittest import mock
from contextlib import redirect_stdout
from io import StringIO
import os
import struct
import tempfile

import iso_boot_check as check


def marker_fixture(path: Path) -> None:
    data = bytearray(16 * 2048 + 7)
    data[0] = 1
    data[446 + 4] = 0xEF
    struct.pack_into("<II", data, 446 + 8, 1, 1)
    data[510:512] = b"\x55\xaa"
    data[512:520] = b"EFI PART"
    path.write_bytes(data)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        image = Path(td) / "markers.iso"
        marker_fixture(image)
        check.RESULTS[:] = []
        captured = StringIO()
        with redirect_stdout(captured):
            with mock.patch.object(check.subprocess, "run",
                                   side_effect=FileNotFoundError("xorriso")):
                rc = check.main(os.fspath(image))
        assert rc != 0
        assert not all(check.RESULTS)
        assert "RESULT: NOT BOOTABLE" in captured.getvalue()
    print("PASS marker bytes and a missing inspector cannot certify an ISO")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
