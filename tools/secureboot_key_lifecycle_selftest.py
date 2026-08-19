#!/usr/bin/env python3
"""Missing derived MOK files must not rotate the enrolled Secure Boot key."""
import hashlib
import os
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tools", "gen-sb-keys.sh")


def digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


with tempfile.TemporaryDirectory(prefix="mok-lifecycle-") as td:
    env = dict(os.environ, NB_SB_KEYDIR=td, NB_SB_CN="Lifecycle Test")
    first = subprocess.run([SCRIPT], env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    key = os.path.join(td, "MOK.key")
    crt = os.path.join(td, "MOK.crt")
    cer = os.path.join(td, "MOK.cer")
    old_key, old_crt = digest(key), digest(crt)

    os.unlink(cer)
    recovered = subprocess.run([SCRIPT], env=env, capture_output=True, text=True)
    assert recovered.returncode == 0, recovered.stderr
    assert digest(key) == old_key
    assert digest(crt) == old_crt
    assert os.path.exists(cer)
    converted = subprocess.check_output(
        ["openssl", "x509", "-in", crt, "-outform", "DER"])
    assert open(cer, "rb").read() == converted

    original_crt = open(crt, "rb").read()
    with tempfile.TemporaryDirectory(prefix="mok-other-") as other:
        other_env = dict(env, NB_SB_KEYDIR=other, NB_SB_CN="Other Identity")
        assert subprocess.run([SCRIPT], env=other_env,
                              capture_output=True).returncode == 0
        with open(crt, "wb") as fh:
            fh.write(open(os.path.join(other, "MOK.crt"), "rb").read())
    mismatched = subprocess.run([SCRIPT], env=env, capture_output=True, text=True)
    assert mismatched.returncode == 2
    assert digest(key) == old_key
    assert "do not match" in mismatched.stderr
    with open(crt, "wb") as fh:
        fh.write(original_crt)

    os.unlink(crt)
    before = digest(key)
    refused = subprocess.run([SCRIPT], env=env, capture_output=True, text=True)
    assert refused.returncode == 2
    assert digest(key) == before
    assert "refusing" in refused.stderr

print("SECUREBOOT KEY LIFECYCLE SELFTEST: 13 checks, all pass")
