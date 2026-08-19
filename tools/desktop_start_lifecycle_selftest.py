#!/usr/bin/env python3
"""Headless ownership/readiness checks for the desktop xinit service."""
import os
import signal
import subprocess
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                      "etc/init.d/S99notebookos")


with tempfile.TemporaryDirectory(prefix="nb-desktop-start-") as td:
    fake = os.path.join(td, "xinit")
    pidfile = os.path.join(td, "xinit.pid")
    # /dev/console is root-only on a developer host; the script's output
    # goes to a file here so the lifecycle — not the console's permissions —
    # is what this exercises.
    env = dict(os.environ, NB_XINIT=fake, NB_XINIT_PIDFILE=pidfile,
               NB_XINIT_GRACE="0.05", NB_LD_PRELOAD="",
               NB_XINIT_CONSOLE=os.path.join(td, "console.log"))

    with open(fake, "w") as fh:
        fh.write("#!/bin/sh\nexit 1\n")
    os.chmod(fake, 0o755)
    failed = subprocess.run(["/bin/sh", SCRIPT, "start"], env=env,
                            capture_output=True, text=True, timeout=3)
    assert failed.returncode != 0, "dead xinit was reported successful"
    assert "FAILED" in failed.stdout, "early failure was not reported"
    assert not os.path.exists(pidfile), "dead xinit left an owned PID"

    with open(fake, "w") as fh:
        fh.write("#!/bin/sh\nexec sleep 30\n")
    os.chmod(fake, 0o755)
    started = subprocess.run(["/bin/sh", SCRIPT, "start"], env=env,
                             capture_output=True, text=True, timeout=3)
    assert started.returncode == 0 and "OK" in started.stdout
    with open(pidfile) as fh:
        pid_text, birth = fh.read().split()
        pid = int(pid_text)
    assert birth.isdigit(), "PID ownership omitted process start-time"
    os.kill(pid, 0)

    # Repeated service starts must retain ownership of the first live desktop
    # rather than launch a doomed second xinit and overwrite its PID record.
    repeated = subprocess.run(["/bin/sh", SCRIPT, "start"], env=env,
                              capture_output=True, text=True, timeout=3)
    assert repeated.returncode == 0 and "already running" in repeated.stdout
    with open(pidfile) as fh:
        repeated_pid, repeated_birth = fh.read().split()
    assert (repeated_pid, repeated_birth) == (pid_text, birth), \
        "repeated start replaced live desktop ownership"
    os.kill(pid, 0)

    subprocess.run(["/bin/sh", SCRIPT, "stop"], env=env, check=True,
                   timeout=3)
    time.sleep(0.05)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        stopped = True
    else:
        stopped = False
        os.kill(pid, signal.SIGKILL)
    assert stopped, "stop did not signal the owned xinit PID"
    assert not os.path.exists(pidfile), "stop retained stale PID ownership"

    # A stale PID can be reused. Its old start-time must not authorize killing
    # the unrelated process now carrying that numeric PID.
    sentinel = subprocess.Popen(["sleep", "30"])
    try:
        with open(pidfile, "w") as fh:
            fh.write("%d 1\n" % sentinel.pid)
        subprocess.run(["/bin/sh", SCRIPT, "stop"], env=env, check=True,
                       timeout=3)
        assert sentinel.poll() is None, "stale PID killed an unrelated process"
    finally:
        sentinel.terminate()
        sentinel.wait(timeout=3)

print("DESKTOP START LIFECYCLE SELFTEST: 11 checks, all pass")
