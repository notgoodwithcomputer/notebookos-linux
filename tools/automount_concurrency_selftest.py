#!/usr/bin/env python3
"""Two simultaneous equal-label USB adds must reserve different mount paths."""
import os
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/automount.sh"

with tempfile.TemporaryDirectory(prefix="nb-automount-race-") as td:
    root = pathlib.Path(td)
    media = root / "media"
    bindir = root / "bin"
    log = root / "mounts"
    media.mkdir()
    bindir.mkdir()
    commands = {
        "blkid": "#!/bin/sh\nprintf SAME\n",
        "mount": ("#!/bin/sh\nfor last do :; done\n"
                  "printf '%s\\n' \"$last\" >> \"$NB_MOUNT_LOG\"\n"),
        "grep": "#!/bin/sh\nexit 1\n",
    }
    for name, body in commands.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir) + ":" + os.environ["PATH"],
               NB_MEDIA_ROOT=str(media), NB_MOUNT_LOG=str(log))
    procs = [subprocess.Popen([str(SCRIPT), "add", dev], env=env)
             for dev in ("sda1", "sdb1")]
    assert all(proc.wait(timeout=5) == 0 for proc in procs)
    targets = log.read_text().splitlines()
    assert len(targets) == 2 and len(set(targets)) == 2, targets
    assert {pathlib.Path(target).name for target in targets} == {"SAME", "SAME (2)"}
print("PASS concurrent equal-label devices reserve distinct mount points")

with tempfile.TemporaryDirectory(prefix="nb-automount-denied-") as td:
    root = pathlib.Path(td)
    bindir = root / "bin"
    bindir.mkdir()
    calls = root / "mkdir-calls"
    commands = {
        "blkid": "#!/bin/sh\nprintf SAME\n",
        "mount": "#!/bin/sh\nexit 99\n",
        "grep": "#!/bin/sh\nexit 1\n",
        # mkdir fails AND the directory is not there afterwards: the disk
        # refused, which is not the same as "that name is taken". Retrying it
        # twenty times cannot help — the stick would simply never appear —
        # so the script falls back to the device name after ONE refusal.
        "mkdir": ("#!/bin/sh\n[ \"$1\" = -p ] && exit 0\n"
                  "printf x >> \"$NB_MKDIR_CALLS\"\nexit 1\n"),
    }
    for name, body in commands.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir) + ":" + os.environ["PATH"],
               NB_MEDIA_ROOT=str(root / "media"), NB_MKDIR_CALLS=str(calls))
    result = subprocess.run([str(SCRIPT), "add", "sda1"], env=env,
                            timeout=5)
    assert result.returncode == 0
    assert len(calls.read_text()) == 1, calls.read_text()
print("PASS a refusing disk is not retried twenty times")

with tempfile.TemporaryDirectory(prefix="nb-automount-taken-") as td:
    # ...and the twenty-try bound still holds for the case it was written for:
    # every candidate name is genuinely TAKEN (mkdir fails, the directory is
    # there), which is what a stack of same-labelled sticks looks like.
    root = pathlib.Path(td)
    bindir = root / "bin"
    bindir.mkdir()
    calls = root / "mkdir-calls"
    commands = {
        "blkid": "#!/bin/sh\nprintf SAME\n",
        "mount": "#!/bin/sh\nexit 99\n",
        "grep": "#!/bin/sh\nexit 1\n",
        "mkdir": ("#!/bin/sh\n[ \"$1\" = -p ] && exit 0\n"
                  "printf x >> \"$NB_MKDIR_CALLS\"\n"
                  "/bin/mkdir -p \"$1\" 2>/dev/null\nexit 1\n"),
    }
    for name, body in commands.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir) + ":" + os.environ["PATH"],
               NB_MEDIA_ROOT=str(root / "media"), NB_MKDIR_CALLS=str(calls))
    result = subprocess.run([str(SCRIPT), "add", "sda1"], env=env, timeout=5)
    assert result.returncode == 0
    assert len(calls.read_text()) == 20, calls.read_text()
print("PASS failed mount-point reservations are bounded")

with tempfile.TemporaryDirectory(prefix="nb-automount-duplicate-") as td:
    root = pathlib.Path(td)
    bindir = root / "bin"
    bindir.mkdir()
    touched = root / "unexpected-mkdir"
    commands = {
        "grep": "#!/bin/sh\nexit 0\n",  # device is already mounted
        "mkdir": ("#!/bin/sh\nprintf x > \"$NB_TOUCHED\"\nexit 0\n"),
        "blkid": "#!/bin/sh\nexit 99\n",
        "mount": "#!/bin/sh\nexit 99\n",
    }
    for name, body in commands.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir) + ":" + os.environ["PATH"],
               NB_MEDIA_ROOT=str(root / "media"), NB_TOUCHED=str(touched))
    result = subprocess.run([str(SCRIPT), "add", "sda1"], env=env,
                            timeout=5)
    assert result.returncode == 0 and not touched.exists()
print("PASS duplicate add exits before reserving a mount point")
# The release runner will not take a stream of PASS lines as a finished run
# (a suite that dies half way also prints PASS lines). Say so at the end.
print("RESULT: ALL PASS")
