#!/usr/bin/env python3
"""A partial target fails before allocating ffmpeg wrapper storage."""
import os
import stat
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tools", "video_target_ffmpeg.sh")

with tempfile.TemporaryDirectory(prefix="video-wrapper-") as td:
    target = os.path.join(td, "target")
    tmp = os.path.join(td, "tmp")
    os.makedirs(os.path.join(target, "lib64"))
    os.makedirs(os.path.join(target, "usr", "bin"))
    os.makedirs(tmp)
    loader = os.path.join(target, "lib64", "ld-linux-x86-64.so.2")
    ffmpeg = os.path.join(target, "usr", "bin", "ffmpeg")
    for path in (loader, ffmpeg):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    env = dict(os.environ, NB_FFMPEG_TARGET=target, TMPDIR=tmp)
    failed = subprocess.run([SCRIPT], env=env, capture_output=True, text=True)
    assert failed.returncode == 1
    assert "no ffprobe" in failed.stdout
    assert os.listdir(tmp) == []

source = open(SCRIPT, encoding="utf-8").read()
assert source.index('for t in ffmpeg ffprobe; do') < source.index('D=$(mktemp -d)')
assert 'NB_FFMPEG_TARGET' in source

print("VIDEO TARGET WRAPPER LIFECYCLE SELFTEST: 5 checks, all pass")
print("RESULT: ALL PASS")
