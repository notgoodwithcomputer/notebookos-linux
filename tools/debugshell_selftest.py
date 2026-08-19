#!/usr/bin/env python3
"""Exact-token gate for the optional root serial debug shell."""
import os
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                      "opt/notebook/debugshell.sh")


with tempfile.TemporaryDirectory(prefix="nb-debuggate-") as td:
    cmdline = os.path.join(td, "cmdline")
    getty = os.path.join(td, "getty")
    trace = os.path.join(td, "trace")
    with open(getty, "w") as fh:
        fh.write('#!/bin/sh\nprintf "%s\\n" "$*" > "$NB_DEBUG_TRACE"\n')
    os.chmod(getty, 0o755)

    def enabled(value):
        try:
            os.unlink(trace)
        except FileNotFoundError:
            pass
        with open(cmdline, "w") as fh:
            fh.write(value + "\n")
        env = dict(os.environ, NB_CMDLINE=cmdline, NB_GETTY=getty,
                   NB_DEBUG_TRACE=trace, NB_DEBUG_TEST_ONLY="1")
        subprocess.run(["/bin/sh", SCRIPT], env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=2)
        return os.path.exists(trace)

    assert enabled("quiet nbdebug splash"), "bare nbdebug did not enable shell"
    for token in ("quiet", "nbdebug=0", "nbdebug=1", "xnbdebug",
                  "nbdebugging"):
        assert not enabled("quiet " + token), "%r enabled debug shell" % token

print("DEBUG SHELL SELFTEST: 6 checks, all pass")
