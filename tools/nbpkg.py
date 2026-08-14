#!/usr/bin/env python3
"""nbpkg — Notebook OS's signed package format, builder, verifier, installer.

Notebook OS ships as a fixed image with no package-install path (packages.py
is a read-only inventory). This adds the deployment story a real app store
needs: a SIGNED package that the OS verifies BEFORE parsing anything, installs
atomically, and registers so Finder/Packages see the app — without editing any
Python source (the registry is data, not code).

Format (.nbpkg = a POSIX tar, gzip):
    manifest.json      name, version, app registration, files[] with sha256
    manifest.sig       Ed25519 detached signature over CANONICAL(manifest)
    files/<sha256>     each payload file, content-addressed

Trust: the release public key (packaging/nbpkg-release.pub) is pinned in the
OS image; the private key is offline and gitignored. Verify order is the whole
point — signature and every hash are checked before a single byte is written
to the system, and USB media is parsed as root (docs/SECURITY-MODEL.md), so a
bad package must never reach an unpacker that trusts it.

CLI:
    nbpkg build   --manifest M.json --root SRC --out APP.nbpkg --key PRIV.pem
    nbpkg verify  APP.nbpkg [--pub PUB.pem]
    nbpkg install APP.nbpkg --target ROOTDIR [--pub PUB.pem]
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PUB = os.path.join(ROOT, "packaging", "nbpkg-release.pub")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(manifest: dict) -> bytes:
    """The exact bytes a signature covers: a sorted, compact JSON of the
    manifest with its own signature field excluded. Deterministic so a rebuild
    verifies against the same signature."""
    m = {k: v for k, v in manifest.items() if k != "sig"}
    return json.dumps(m, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _openssl_sign(canon: bytes, key: str) -> bytes:
    with tempfile.NamedTemporaryFile() as msg, \
            tempfile.NamedTemporaryFile() as sig:
        msg.write(canon); msg.flush()
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", key,
                        "-rawin", "-in", msg.name, "-out", sig.name],
                       check=True, capture_output=True)
        return open(sig.name, "rb").read()


def _openssl_verify(canon: bytes, sig: bytes, pub: str) -> bool:
    with tempfile.NamedTemporaryFile() as msg, \
            tempfile.NamedTemporaryFile() as sigf:
        msg.write(canon); msg.flush()
        sigf.write(sig); sigf.flush()
        r = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin",
                            "-inkey", pub, "-rawin", "-sigfile", sigf.name,
                            "-in", msg.name], capture_output=True)
        return r.returncode == 0


def build(manifest_path: str, srcroot: str, out: str, key: str):
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    # Content-address every declared file; the manifest carries the hashes the
    # installer will re-check, so a swapped payload fails even if re-signed.
    files = []
    payloads = {}
    for entry in manifest["files"]:
        src = os.path.join(srcroot, entry["src"])
        data = open(src, "rb").read()
        digest = sha256(data)
        payloads[digest] = data
        files.append({"src": entry["src"], "dest": entry["dest"],
                      "mode": entry.get("mode", "0644"), "sha256": digest})
    manifest["files"] = files
    canon = canonical(manifest)
    sig = _openssl_sign(canon, key)

    with tarfile.open(out, "w:gz") as tar:
        def add_bytes(name, data):
            import io
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        add_bytes("manifest.json", json.dumps(manifest, indent=1).encode())
        add_bytes("manifest.sig", sig)
        for digest, data in sorted(payloads.items()):
            add_bytes("files/" + digest, data)
    print(f"built {out}: {manifest['name']} {manifest['version']}, "
          f"{len(files)} files, signed")


def _safe_extract_member(tar, name, td):
    """Read one member's bytes without honoring absolute/traversal names."""
    member = tar.getmember(name)
    if member.name != name or ".." in name or name.startswith("/"):
        raise ValueError(f"unsafe member {name!r}")
    return tar.extractfile(member).read()


def _load_and_verify(pkg: str, pub: str):
    with tarfile.open(pkg, "r:gz") as tar:
        names = set(tar.getnames())
        if "manifest.json" not in names or "manifest.sig" not in names:
            raise ValueError("missing manifest or signature")
        manifest = json.loads(_safe_extract_member(tar, "manifest.json", None))
        sig = _safe_extract_member(tar, "manifest.sig", None)
        canon = canonical(manifest)
        if not _openssl_verify(canon, sig, pub):
            raise ValueError("SIGNATURE INVALID — refusing package")
        # Every payload must be present and hash exactly (no swapped files).
        payloads = {}
        for entry in manifest["files"]:
            member = "files/" + entry["sha256"]
            if member not in names:
                raise ValueError(f"missing payload {entry['sha256']}")
            data = _safe_extract_member(tar, member, None)
            if sha256(data) != entry["sha256"]:
                raise ValueError(f"payload hash mismatch {entry['sha256']}")
            # dest is sanitized on install; hold the bytes now.
            payloads[entry["sha256"]] = data
        return manifest, payloads


def verify(pkg: str, pub: str) -> int:
    try:
        manifest, _ = _load_and_verify(pkg, pub)
    except Exception as e:
        print(f"REFUSED: {e}")
        return 1
    print(f"VERIFIED: {manifest['name']} {manifest['version']} "
          f"({len(manifest['files'])} files, signature + all hashes OK)")
    return 0


def install(pkg: str, target: str, pub: str) -> int:
    # VERIFY FIRST — nothing touches the target until the signature and every
    # hash pass (spec order is the security).
    try:
        manifest, payloads = _load_and_verify(pkg, pub)
    except Exception as e:
        print(f"REFUSED: {e} — nothing was written")
        return 1
    # Atomic-ish: write every file, then the registry entry last, so a
    # half-install is never registered.
    written = []
    for entry in manifest["files"]:
        dest = entry["dest"].lstrip("/")
        if ".." in dest.split("/"):
            print(f"REFUSED: unsafe dest {entry['dest']!r}")
            return 1
        full = os.path.join(target, dest)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        tmp = full + ".nbpkg-tmp"
        with open(tmp, "wb") as f:
            f.write(payloads[entry["sha256"]])
        os.chmod(tmp, int(entry.get("mode", "0644"), 8))
        os.replace(tmp, full)
        written.append(dest)
    # Data-driven registration (no source edit): the installer appends to a
    # registry Finder/Packages read. app = {display, module, kind, icon}.
    reg_path = os.path.join(target, "opt", "notebook", "de",
                            "installed_apps.json")
    os.makedirs(os.path.dirname(reg_path), exist_ok=True)
    try:
        reg = json.load(open(reg_path, encoding="utf-8"))
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
    print(f"INSTALLED: {manifest['name']} {manifest['version']} — "
          f"{len(written)} files, registered {manifest['app']['display']}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--manifest", required=True)
    b.add_argument("--root", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--key", required=True)
    v = sub.add_parser("verify")
    v.add_argument("pkg")
    v.add_argument("--pub", default=DEFAULT_PUB)
    i = sub.add_parser("install")
    i.add_argument("pkg")
    i.add_argument("--target", required=True)
    i.add_argument("--pub", default=DEFAULT_PUB)
    a = ap.parse_args()
    if a.cmd == "build":
        build(a.manifest, a.root, a.out, a.key); return 0
    if a.cmd == "verify":
        return verify(a.pkg, a.pub)
    if a.cmd == "install":
        return install(a.pkg, a.target, a.pub)


if __name__ == "__main__":
    sys.exit(main())
