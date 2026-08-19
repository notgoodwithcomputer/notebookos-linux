"""nbtrust — which code this machine is allowed to run.

The rule, in one line: **a module may be launched only if a signature made by
the Notebook OS release key says so.** See docs/APP-TRUST.md for the layers this
sits in; this file is layer L2, the userspace half.

Two kinds of authorisation, both ending at the same pinned public key:

  * the IMAGE manifest — `de/trusted.manifest` + `.sig`, generated and signed at
    build time by tools/gen_trust_manifest.py, listing every module that ships;
  * a PACKAGE manifest — the `manifest.json` + `manifest.sig` out of a `.nbpkg`,
    kept in `de/.trust/` when the package is installed, listing the files that
    package brought.

**The device holds no private key.** It cannot mint trust, only recognise it.
That is what makes the app registry (`installed_apps.json`) safe to leave as
plain, unsigned JSON: forging an entry there gets an attacker a name in a list
and nothing else, because the launcher does not ask the registry whether
something may run — it asks for a signature over the bytes on disk.

Failure is CLOSED. A missing, unreadable or unverifiable manifest authorises
nothing; the launcher refuses and says so. There is no environment variable, no
developer flag and no "unsigned" mode: an escape hatch that ships is not a
lockout, it is a door with a sign on it. Development happens on the build host,
where nothing goes through a launcher.

What this layer does NOT do, and must not be described as doing:

  * It verifies the module being LAUNCHED, not the whole tree. `nbapp.py` is
    imported by every app rather than launched, so a change to it is not caught
    here.
  * It checks the file and then hands the path to `python3`. Between those two
    moments the file can be swapped — an ordinary time-of-check/time-of-use
    gap, and unavoidable for any userspace verifier on a writable root.
  * It runs `openssl`. If that command is absent, nothing verifies and nothing
    launches (see `tools/shipped_binaries_check.py`, which exists because that
    is precisely what shipped once).

All three close the same way: dm-verity (layer L1), in the kernel, per block, at
read time. Until L1 lands this is a policy lockout that an attacker with root
can switch off, and it should be described that way.
"""
import hashlib
import json
import os
import subprocess
import tempfile

DE_DIR = os.path.dirname(os.path.abspath(__file__))
# The release public key, pinned in the image beside the daemon directory —
# the same anchor nbpkg_install verifies packages against.
PUB = os.path.join(os.path.dirname(DE_DIR), "nbpkg-release.pub")
MANIFEST = os.path.join(DE_DIR, "trusted.manifest")
TRUST_DIR = os.path.join(DE_DIR, ".trust")
# A package manifest names its destinations relative to the root it was
# installed into — "/" on a real machine, a scratch directory under test, which
# is the same parameter nbpkg_install.install() already takes. Resolving
# against it (rather than assuming "/") is what lets the gate exercise this
# code against a real installed package instead of a hand-built fixture.
ROOT_DIR = "/"

_CACHE = None


def _sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _openssl_verify(payload, sig, pub=None):
    """True iff `sig` is a valid Ed25519 signature over `payload` by the pinned
    key. Raw Ed25519 through the openssl already on the device: no keyring, no
    trust database, no OpenPGP packet parser running as root."""
    pub = pub or PUB
    if not os.path.exists(pub):
        return False
    try:
        with tempfile.NamedTemporaryFile() as msg, \
                tempfile.NamedTemporaryFile() as sigf:
            msg.write(payload); msg.flush()
            sigf.write(sig); sigf.flush()
            r = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin",
                                "-inkey", pub, "-rawin", "-sigfile", sigf.name,
                                "-in", msg.name], capture_output=True)
            return r.returncode == 0
    except Exception:
        return False


def _read_signed(path, sig_path):
    """The bytes of `path`, but only if `sig_path` signs them. None otherwise.

    The signature covers the file EXACTLY as it sits on disk, so there is no
    canonicalisation step here to disagree with the signer about."""
    try:
        with open(path, "rb") as fh:
            payload = fh.read()
        with open(sig_path, "rb") as fh:
            sig = fh.read()
    except OSError:
        return None
    return payload if _openssl_verify(payload, sig) else None


def _load():
    """{absolute path: sha256} for every file this machine may run.

    Built once per process. Each source is verified independently, so one
    unverifiable package manifest cannot take the image's own with it."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    allowed = {}

    payload = _read_signed(MANIFEST, MANIFEST + ".sig")
    if payload is not None:
        try:
            data = json.loads(payload.decode("utf-8"))
            for name, digest in (data.get("modules") or {}).items():
                if isinstance(name, str) and isinstance(digest, str):
                    allowed[os.path.join(DE_DIR, name)] = digest
        except Exception:
            pass

    # Packages installed after the image was sealed carry their own signed
    # manifest; the sha256 in it is the authorisation for the file it names.
    try:
        entries = sorted(os.listdir(TRUST_DIR))
    except OSError:
        entries = []
    for entry in entries:
        if not entry.endswith(".manifest"):
            continue
        base = os.path.join(TRUST_DIR, entry)
        payload = _read_signed(base, base + ".sig")
        if payload is None:
            continue
        try:
            data = json.loads(payload.decode("utf-8"))
            for item in data.get("files") or []:
                dest, digest = item.get("dest"), item.get("sha256")
                if not isinstance(dest, str) or not isinstance(digest, str):
                    continue
                # A package manifest names destinations from the root; only the
                # ones that landed in the module directory can be launched.
                full = os.path.join(ROOT_DIR, dest.lstrip("/"))
                if os.path.dirname(full) == DE_DIR:
                    allowed[full] = digest
        except Exception:
            continue

    # Only a SUCCESSFUL load is cached. A verification that failed because the
    # machine was momentarily unable to run openssl must not be remembered as
    # "nothing is authorised" for the life of the process — that would turn a
    # transient failure into a desktop where nothing opens until reboot. The
    # refusal still stands for this call, which is the part that matters.
    if allowed:
        _CACHE = allowed
    return allowed


def refresh():
    """Forget the cached manifests — call after installing a package."""
    global _CACHE
    _CACHE = None


def check_path(path):
    """(ok, reason). `reason` is for a log or a status line, not for a dialog:
    a person who has been handed an unauthorised app does not need to be taught
    the vocabulary of code signing, they need to be told it will not open."""
    path = os.path.abspath(path)
    allowed = _load()
    if not allowed:
        return False, "no signed manifest on this machine"
    want = allowed.get(path)
    if want is None:
        # A long-lived Finder/Packages process may have populated the cache
        # before a signed package was installed. Reload once on a miss so the
        # new retained manifest is visible without a process restart; failure
        # still closes exactly as before.
        refresh()
        allowed = _load()
        want = allowed.get(path)
        if want is None:
            return False, "not named in any signed manifest"
    got = _sha256_file(path)
    if got is None:
        return False, "file could not be read"
    if got != want:
        # The same path can be upgraded by another signed package. A reload
        # distinguishes that from tampering; an unchanged/invalid manifest
        # still produces the ordinary hash refusal.
        refresh()
        allowed = _load()
        want = allowed.get(path)
        if want is None or got != want:
            return False, "file does not match its signed hash"
    return True, "signed"


def check_module(mod):
    """(ok, reason) for a DE module name, the way a launcher names it."""
    return check_path(os.path.join(DE_DIR, mod + ".py"))
