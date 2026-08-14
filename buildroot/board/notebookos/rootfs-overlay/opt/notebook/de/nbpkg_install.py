"""On-device .nbpkg verifier + installer (see docs/NBPKG.md).

This is the runtime half of tools/nbpkg.py (which also builds/signs). It ships
in the rootfs so the Packages app can verify a signed package and install it,
with the release public key pinned beside it. Verify order is the security:
the Ed25519 signature and every payload sha256 are checked before a single
byte reaches the system. openssl is on-device; no third-party crypto lib.
"""
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile

DE_DIR = os.path.dirname(os.path.abspath(__file__))
# The release public key is pinned in the image, next to the daemon dir.
PUB = os.path.join(os.path.dirname(DE_DIR), "nbpkg-release.pub")


class PkgError(Exception):
    """A package that must not be installed (bad signature, hash, or shape)."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(manifest):
    m = {k: v for k, v in manifest.items() if k != "sig"}
    return json.dumps(m, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _openssl_verify(canon, sig, pub):
    with tempfile.NamedTemporaryFile() as msg, \
            tempfile.NamedTemporaryFile() as sigf:
        msg.write(canon); msg.flush()
        sigf.write(sig); sigf.flush()
        r = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin",
                            "-inkey", pub, "-rawin", "-sigfile", sigf.name,
                            "-in", msg.name], capture_output=True)
        return r.returncode == 0


def _member_bytes(tar, name):
    m = tar.getmember(name)
    if m.name != name or ".." in name or name.startswith("/"):
        raise PkgError("unsafe member name")
    return tar.extractfile(m).read()


def inspect(pkg, pub=None):
    """Verify a package and return its manifest + payloads. Raises PkgError if
    anything fails; NOTHING is written to the system."""
    pub = pub or PUB
    if not os.path.exists(pub):
        raise PkgError("no pinned public key on this system")
    try:
        with tarfile.open(pkg, "r:gz") as tar:
            names = set(tar.getnames())
            if "manifest.json" not in names or "manifest.sig" not in names:
                raise PkgError("missing manifest or signature")
            manifest = json.loads(_member_bytes(tar, "manifest.json"))
            sig = _member_bytes(tar, "manifest.sig")
            if not _openssl_verify(_canonical(manifest), sig, pub):
                raise PkgError("signature does not verify")
            payloads = {}
            for entry in manifest["files"]:
                member = "files/" + entry["sha256"]
                if member not in names:
                    raise PkgError("a declared file is missing")
                data = _member_bytes(tar, member)
                if _sha256(data) != entry["sha256"]:
                    raise PkgError("a file does not match its hash")
                payloads[entry["sha256"]] = data
    except PkgError:
        raise
    except Exception as exc:  # a malformed archive is a refusal, not a crash
        raise PkgError("not a readable package: %s" % exc)
    return manifest, payloads


def install(pkg, target="/", pub=None):
    """Verify, then install atomically. Returns the manifest on success."""
    manifest, payloads = inspect(pkg, pub)
    for entry in manifest["files"]:
        dest = entry["dest"].lstrip("/")
        if ".." in dest.split("/"):
            raise PkgError("unsafe destination")
        full = os.path.join(target, dest)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = full + ".nbpkg-tmp"
        with open(tmp, "wb") as f:
            f.write(payloads[entry["sha256"]])
        os.chmod(tmp, int(entry.get("mode", "0644"), 8))
        os.replace(tmp, full)
    # Register LAST so a half-write is never registered.
    reg_path = os.path.join(target, "opt", "notebook", "de",
                            "installed_apps.json")
    os.makedirs(os.path.dirname(reg_path), exist_ok=True)
    try:
        with open(reg_path, encoding="utf-8") as f:
            reg = json.load(f)
    except Exception:
        reg = {}
    reg[manifest["app"]["display"]] = {
        "module": manifest["app"]["module"],
        "kind": manifest["app"].get("kind", "Utility"),
        "version": manifest["version"],
        "service": manifest.get("service"),
    }
    tmp = reg_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(reg, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, reg_path)
    return manifest


def scan(mount_points):
    """Return [(path, name)] for every readable .nbpkg on the given mounts."""
    found = []
    for mp in mount_points:
        try:
            for entry in sorted(os.listdir(mp)):
                if entry.endswith(".nbpkg"):
                    found.append((os.path.join(mp, entry), entry))
        except OSError:
            continue
    return found
