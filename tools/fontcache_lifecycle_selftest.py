#!/usr/bin/env python3
"""Headless first-boot font-cache retry contract."""
import os
import subprocess
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(
    ROOT, "buildroot/board/notebookos/rootfs-overlay/etc/init.d/S35fontcache")


def run(script, env):
    subprocess.run(["/bin/sh", script, "start"], env=env, check=True,
                   stdout=subprocess.DEVNULL, timeout=10)


with tempfile.TemporaryDirectory(prefix="nb-fontcache-") as td:
    bindir = os.path.join(td, "bin")
    os.mkdir(bindir)
    stamp = os.path.join(td, "cache", ".notebookos-seeded")
    calls = os.path.join(td, "calls")
    nice = os.path.join(bindir, "nice")
    fc = os.path.join(bindir, "fc-cache")
    with open(nice, "w") as fh:
        fh.write("#!/bin/sh\nshift 2\nexec \"$@\"\n")
    with open(fc, "w") as fh:
        fh.write('#!/bin/sh\nprintf "call\\n" >> "$NB_FC_CALLS"\n'
                 'exit "${NB_FC_RESULT:-0}"\n')
    os.chmod(nice, 0o755)
    os.chmod(fc, 0o755)
    env = dict(os.environ, PATH=bindir + ":/usr/bin:/bin",
               NB_FONT_CACHE_STAMP=stamp, NB_FC_CALLS=calls)

    env["NB_FC_RESULT"] = "1"
    run(SCRIPT, env)
    time.sleep(0.1)
    assert not os.path.exists(stamp), "failed cache run was stamped successful"

    env["NB_FC_RESULT"] = "0"
    run(SCRIPT, env)
    for _ in range(50):
        if os.path.exists(stamp):
            break
        time.sleep(0.02)
    assert os.path.exists(stamp), "successful cache run was not stamped"

    run(SCRIPT, env)
    time.sleep(0.05)
    with open(calls) as fh:
        assert len(fh.readlines()) == 2, "seeded cache was unnecessarily rerun"

print("FONT CACHE LIFECYCLE SELFTEST: 3 checks, all pass")
print("RESULT: PASS")
