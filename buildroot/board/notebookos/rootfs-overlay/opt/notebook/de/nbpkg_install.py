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
import time

DE_DIR = os.path.dirname(os.path.abspath(__file__))
# The release public key is pinned in the image, next to the daemon dir.
PUB = os.path.join(os.path.dirname(DE_DIR), "nbpkg-release.pub")
MAX_MEMBERS = 256
MAX_MANIFEST = 256 * 1024
MAX_SIGNATURE = 4096
MAX_PAYLOAD = 32 * 1024 * 1024
MAX_TOTAL_PAYLOAD = 64 * 1024 * 1024


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


def _member_bytes(tar, name, limit=MAX_PAYLOAD):
    m = tar.getmember(name)
    if m.name != name or ".." in name or name.startswith("/"):
        raise PkgError("unsafe member name")
    if not m.isfile() or m.size < 0 or m.size > limit:
        raise PkgError("package member is too large")
    stream = tar.extractfile(m)
    if stream is None:
        raise PkgError("package member cannot be read")
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise PkgError("package member is too large")
    return data


def _stream_member_bytes(tar, member, limit):
    """Read the current streaming member without allowing a decompression bomb."""
    if not member.isfile() or member.size < 0 or member.size > limit:
        raise PkgError("package member is too large")
    stream = tar.extractfile(member)
    if stream is None:
        raise PkgError("package member cannot be read")
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise PkgError("package member is too large")
    return data


def inspect(pkg, pub=None):
    """Verify a package and return its manifest + payloads. Raises PkgError if
    anything fails; NOTHING is written to the system."""
    pub = pub or PUB
    if not os.path.exists(pub):
        raise PkgError("no pinned public key on this system")
    try:
        # Streaming mode lets us reject an oversized member from its header;
        # getmembers() would first seek/decompress across its entire body.
        with tarfile.open(pkg, "r|gz") as tar:
            members = iter(tar)
            first = next(members, None)
            if first is None or first.name != "manifest.json":
                raise PkgError("manifest must be the first package member")
            manifest_raw = _stream_member_bytes(tar, first, MAX_MANIFEST)
            second = next(members, None)
            if second is None or second.name != "manifest.sig":
                raise PkgError("signature must follow the manifest")
            manifest = json.loads(manifest_raw)
            sig = _stream_member_bytes(tar, second, MAX_SIGNATURE)
            if not _openssl_verify(_canonical(manifest), sig, pub):
                raise PkgError("signature does not verify")
            files = manifest.get("files") if isinstance(manifest, dict) else None
            if not isinstance(files, list) or len(files) > MAX_MEMBERS - 2:
                raise PkgError("invalid file list")
            if any(not isinstance(entry, dict) or
                   not isinstance(entry.get("sha256"), str) or
                   not entry["sha256"] for entry in files):
                raise PkgError("invalid file list")
            # Several destinations may intentionally share identical content;
            # the archive carries that digest once even though files[] refers
            # to it more than once.
            expected = {"files/" + entry["sha256"] for entry in files}
            payloads = {}
            total = 0
            seen = set()
            for count, info in enumerate(members, 3):
                if count > MAX_MEMBERS:
                    raise PkgError("package contains too many files")
                if info.name not in expected or info.name in seen:
                    raise PkgError("package contains undeclared or duplicate members")
                if not info.isfile() or info.size < 0 or info.size > MAX_PAYLOAD:
                    raise PkgError("package member is too large")
                total += info.size
                if total > MAX_TOTAL_PAYLOAD:
                    raise PkgError("package payload is too large")
                data = _stream_member_bytes(tar, info, MAX_PAYLOAD)
                digest = info.name[len("files/"):]
                if _sha256(data) != digest:
                    raise PkgError("a file failed its integrity check")
                payloads[digest] = data
                seen.add(info.name)
            if seen != expected:
                raise PkgError("a declared file is missing")
    except PkgError:
        raise
    except Exception as exc:  # a malformed archive is a refusal, not a crash
        raise PkgError("not a readable package: %s" % exc)
    return manifest, payloads


def _sig_bytes(pkg):
    """The detached signature out of the package, for retaining beside it."""
    with tarfile.open(pkg, "r:gz") as tar:
        return _member_bytes(tar, "manifest.sig", MAX_SIGNATURE)


def _atomic_write(path, data):
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path),
                               suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _install_payload(path, data, mode):
    """Publish one verified payload with a draft owned by this installer."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path),
                               suffix=".nbpkg-tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _quarantine_registry(path):
    """Move an unreadable/wrong-shaped registry aside before rebuilding it."""
    if not os.path.exists(path):
        return
    damaged = "%s.damaged-%d" % (path, time.time_ns())
    try:
        os.replace(path, damaged)
    except OSError:
        pass


def _validated_files(manifest, payloads, target):
    """Resolve every payload destination and mode before touching the target."""
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        raise PkgError("invalid file list")
    resolved = []
    seen = set()
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("dest"), str):
            raise PkgError("invalid file entry")
        dest = entry["dest"].lstrip("/")
        if not dest or ".." in dest.split("/") or dest in seen:
            raise PkgError("unsafe or duplicate destination")
        try:
            mode = int(entry.get("mode", "0644"), 8)
        except (TypeError, ValueError, OverflowError):
            raise PkgError("invalid file mode")
        if mode < 0 or mode > 0o7777:
            raise PkgError("invalid file mode")
        digest = entry.get("sha256")
        if digest not in payloads:
            raise PkgError("a declared file is missing")
        seen.add(dest)
        full = os.path.join(target, dest)
        target_real = os.path.realpath(target)
        parent_real = os.path.realpath(os.path.dirname(full) or target)
        try:
            contained = os.path.commonpath((target_real, parent_real)) == target_real
        except ValueError:
            contained = False
        if not contained:
            raise PkgError("destination escapes target")
        resolved.append((full, payloads[digest], mode))
    return resolved


def _fsync_directories(directories):
    """Make rename/unlink results durable before a transaction reports."""
    for directory in sorted(set(directories)):
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(directory, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _publish_transaction(outputs):
    """Stage all `(path, bytes, mode)` outputs, then publish with rollback."""
    staged = []
    committed = []
    touched_dirs = set()
    try:
        for path, data, mode in outputs:
            directory = os.path.dirname(path) or "."
            os.makedirs(directory, exist_ok=True)
            touched_dirs.add(directory)
            fd, draft = tempfile.mkstemp(prefix=".%s." % os.path.basename(path),
                                         suffix=".nbpkg-tmp", dir=directory)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.chmod(draft, mode)
                    os.fsync(f.fileno())
            except Exception:
                try:
                    os.unlink(draft)
                except OSError:
                    pass
                raise
            staged.append((path, draft))
        for path, draft in staged:
            backup = None
            if os.path.exists(path):
                fd, backup = tempfile.mkstemp(
                    prefix=".%s." % os.path.basename(path),
                    suffix=".nbpkg-backup", dir=os.path.dirname(path) or ".")
                os.close(fd)
                os.unlink(backup)
                os.replace(path, backup)
            try:
                os.replace(draft, path)
            except Exception:
                if backup is not None:
                    os.replace(backup, path)
                raise
            committed.append((path, backup))
        _fsync_directories(touched_dirs)
    except Exception:
        for path, backup in reversed(committed):
            try:
                os.unlink(path)
            except OSError:
                pass
            if backup is not None:
                try:
                    os.replace(backup, path)
                except OSError:
                    pass
        try:
            _fsync_directories(touched_dirs)
        except OSError:
            pass
        raise
    else:
        for _path, backup in committed:
            if backup is not None:
                try:
                    os.unlink(backup)
                except OSError:
                    pass
        _fsync_directories(touched_dirs)
    finally:
        for _path, draft in staged:
            try:
                os.unlink(draft)
            except OSError:
                pass


def install(pkg, target="/", pub=None):
    """Verify, then install atomically. Returns the manifest on success."""
    manifest, payloads = inspect(pkg, pub)
    outputs = _validated_files(manifest, payloads, target)
    app = manifest.get("app")
    if not isinstance(app, dict) or not all(isinstance(app.get(k), str)
                                            and app.get(k)
                                            for k in ("module", "display")):
        raise PkgError("invalid app registration")
    # Keep the package's OWN signature on the machine. This is what lets the
    # launcher allow an app that was not in the image: the device holds no
    # private key and cannot mint trust, so the authorisation for an installed
    # app has to be the signature that came with it (docs/APP-TRUST.md).
    # The CANONICAL manifest is what the signature covers, so that is what is
    # stored — byte for byte, or the check on the other side cannot agree.
    trust_dir = os.path.join(target, "opt", "notebook", "de", ".trust")
    stem = os.path.join(trust_dir, str(manifest["app"]["module"]))
    reserved = {stem + ".manifest", stem + ".manifest.sig",
                os.path.join(target, "opt", "notebook", "de",
                             "installed_apps.json")}
    if any(path in reserved for path, _data, _mode in outputs):
        raise PkgError("package payload collides with installer metadata")
    outputs.extend([(stem + ".manifest", _canonical(manifest), 0o644),
                    (stem + ".manifest.sig", _sig_bytes(pkg), 0o644)])

    # Register LAST so a half-write is never registered.
    reg_path = os.path.join(target, "opt", "notebook", "de",
                            "installed_apps.json")
    damaged_registry = None
    old_registry = None
    try:
        with open(reg_path, "rb") as f:
            old_registry = f.read()
        reg = json.loads(old_registry.decode("utf-8"))
    except FileNotFoundError:
        reg = {}
    except Exception:
        if old_registry is None:
            raise PkgError("existing app registry is unreadable")
        damaged_registry = old_registry
        reg = {}
    if not isinstance(reg, dict):
        damaged_registry = old_registry
        reg = {}
    reg[manifest["app"]["display"]] = {
        "module": manifest["app"]["module"],
        "kind": manifest["app"].get("kind", "Utility"),
        "version": manifest["version"],
        "service": manifest.get("service"),
    }
    reg_bytes = (json.dumps(reg, indent=1, sort_keys=True) + "\n").encode()
    outputs.append((reg_path, reg_bytes, 0o644))
    if damaged_registry is not None:
        outputs.append(("%s.damaged-%d" % (reg_path, time.time_ns()),
                        damaged_registry, 0o600))
    _publish_transaction(outputs)
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
