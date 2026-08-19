#!/usr/bin/env python3
"""Post-spool queue failure must remain a committed print result."""

import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import nbprint  # noqa: E402


class Accepted:
    returncode = 0
    stdout = "request id is Office-42"
    stderr = ""


def main():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.write(fd, b"%PDF-1.4\n")
    os.close(fd)
    old_have = nbprint._have
    old_run = nbprint.subprocess.run
    old_stopped = nbprint.printer_stopped
    calls = []
    statuses = iter((None, "Out of paper"))
    try:
        nbprint._have = lambda cmd: cmd == "lp"
        nbprint.subprocess.run = lambda *args, **kwargs: (
            calls.append(args[0]) or Accepted())
        nbprint.printer_stopped = lambda _name: next(statuses)
        ok, message = nbprint.submit_pdf(path, printer="Office")
    finally:
        nbprint._have = old_have
        nbprint.subprocess.run = old_run
        nbprint.printer_stopped = old_stopped
        os.unlink(path)
    if not ok or not message.startswith("Queued, but ") or len(calls) != 1:
        print("FAIL: accepted stopped job was presented as uncommitted")
        return 1
    print("PASS: an accepted stopped job is queued-with-warning, never retryable failure")
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
