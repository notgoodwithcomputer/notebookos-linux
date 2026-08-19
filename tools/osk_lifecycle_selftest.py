#!/usr/bin/env python3
"""OSK singleton ownership rejects PID reuse, not the keyboard itself."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
    "opt", "notebook", "de"))
import osk  # noqa: E402

with tempfile.TemporaryDirectory(prefix="osk-owner-") as td:
    path = os.path.join(td, "osk.pid")
    with open(path, "w", encoding="ascii") as handle:
        handle.write("%d 1\n" % os.getpid())
    assert osk._claim_instance(path) == path
    fields = open(path, encoding="ascii").read().split()
    assert fields == [str(os.getpid()), osk._process_started(os.getpid())]
    assert osk._claim_instance(path) is None
    for malformed in ("", "   \n", "not-a-pid"):
        os.unlink(path)
        with open(path, "w", encoding="ascii") as handle:
            handle.write(malformed)
        assert osk._claim_instance(path) == path
        assert open(path, encoding="ascii").read().split()[0] == str(os.getpid())
    with open(path, "w", encoding="ascii") as handle:
        handle.write("999999 123\n")
    assert osk._release_instance(path) is False and os.path.exists(path)
    with open(path, "w", encoding="ascii") as handle:
        handle.write("%d %s\n" % (os.getpid(), osk._process_started(os.getpid())))
    assert osk._release_instance(path) is True and not os.path.exists(path)

# Terminal verdict the release runner recognises (SUCCESSWORD).
print("OSK LIFECYCLE SELFTEST: malformed ownership is reclaimed; all pass")
print("RESULT: ALL PASS")
