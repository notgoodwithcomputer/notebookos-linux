#!/usr/bin/env python3
"""A vanished voice allow entry must make the full gate stale/red."""

from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import os
import sys
import tempfile

import voice_check as gate


def main() -> None:
    old_de, old_load, old_argv = gate.DE, gate.load_ledger, list(sys.argv)
    with tempfile.TemporaryDirectory() as td:
        Path(td, "clean.py").write_text("x = 1\n", encoding="utf-8")
        gate.DE = td
        gate.load_ledger = lambda: ({"A stale sentence that no longer exists":
                                     "reviewed"}, {})
        sys.argv = ["voice_check.py"]
        try:
            output = StringIO()
            with redirect_stdout(output):
                rc = gate.main()
        finally:
            gate.DE, gate.load_ledger, sys.argv = old_de, old_load, old_argv
        assert rc != 0 and "STALE allow entry" in output.getvalue()
    print("PASS voice allow entries ratchet in both directions")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
