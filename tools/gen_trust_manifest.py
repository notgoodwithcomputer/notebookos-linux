#!/usr/bin/env python3
"""Generate and sign the image's trust manifest — the list of modules a
Notebook OS machine is allowed to launch (docs/APP-TRUST.md, layer L2).

    python3 tools/gen_trust_manifest.py [--key PRIV] [--check] [--target DIR]

Writes `de/trusted.manifest` and `de/trusted.manifest.sig` into the rootfs
overlay, or with --target into a built target tree (DIR/opt/notebook/de).

**--target is the one that matters for a release.** What ships is
`output/target`, not the overlay, and buildroot copies one to the other at a
moment other sessions are still editing the overlay. Sign the overlay and a
module edited in between leaves the image carrying a file the manifest does not
match — measured, on the 2.4 build: `tasks.py` differed, so Tasks alone would
have refused to open on that ISO. mkrelease therefore signs from the post-build
hook, over the bytes that are about to become the image.

Both files are build outputs, regenerated from the modules on disk — never
hand-edited, and gitignored so a module edit does not churn a signature.

`--check` verifies the manifest on disk covers every module and matches their
current hashes, without signing. That is the build gate: an image whose manifest
is stale or missing would boot a desktop where NOTHING launches, because the
launcher fails closed. Better to fail the build.

The private key is the release key (`packaging/keys/nbpkg-signing.key`), the
same authority that signs `.nbpkg` packages, and it is gitignored. On a machine
without it, `--check` still works; generating does not.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
DE_REL = "opt/notebook/de"
OVERLAY_DE = DE          # the source tree, kept even when --target moves DE
KEY = os.path.join(ROOT, "packaging/keys/nbpkg-signing.key")
PUB = os.path.join(ROOT, "packaging/nbpkg-release.pub")
MANIFEST = os.path.join(DE, "trusted.manifest")

# Build outputs that live in the module directory but are not modules.
SKIP = ("trusted.manifest", "trusted.manifest.sig")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def modules():
    """{basename: sha256} for every module in the image, in a stable order.

    Every `.py`, not only the launchable ones: the manifest is the record of
    what the image contains, and a check that covered only apps would say
    nothing about the modules they import."""
    out = {}
    for name in sorted(os.listdir(DE)):
        if not name.endswith(".py") or name in SKIP:
            continue
        out[name] = sha256_file(os.path.join(DE, name))
    return out


def sign(payload, key):
    with tempfile.NamedTemporaryFile() as msg, \
            tempfile.NamedTemporaryFile() as sig:
        msg.write(payload); msg.flush()
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", key,
                        "-rawin", "-in", msg.name, "-out", sig.name],
                       check=True, capture_output=True)
        return open(sig.name, "rb").read()


def verify(payload, sig, pub):
    with tempfile.NamedTemporaryFile() as msg, \
            tempfile.NamedTemporaryFile() as sigf:
        msg.write(payload); msg.flush()
        sigf.write(sig); sigf.flush()
        r = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin",
                            "-inkey", pub, "-rawin", "-sigfile", sigf.name,
                            "-in", msg.name], capture_output=True)
        return r.returncode == 0


def do_check():
    if not os.path.exists(MANIFEST) or not os.path.exists(MANIFEST + ".sig"):
        print("FAIL: no trust manifest in the overlay — every launch would be "
              "refused on the built image (run this tool without --check)")
        return 1
    payload = open(MANIFEST, "rb").read()
    sig = open(MANIFEST + ".sig", "rb").read()
    if not verify(payload, sig, PUB):
        print("FAIL: the trust manifest is not signed by the release key")
        return 1
    listed = json.loads(payload.decode("utf-8")).get("modules") or {}
    have = modules()
    missing = sorted(set(have) - set(listed))
    extra = sorted(set(listed) - set(have))
    changed = sorted(n for n in set(have) & set(listed) if have[n] != listed[n])
    for name in missing:
        print("FAIL: %s is in the image but not in the manifest — it could "
              "not be launched" % name)
    for name in extra:
        print("FAIL: %s is in the manifest but not in the image" % name)
    for name in changed:
        print("FAIL: %s has changed since the manifest was signed" % name)
    bad = len(missing) + len(extra) + len(changed)
    print("%s: %d modules covered, %d problem(s)"
          % ("FAIL" if bad else "OK", len(have), bad))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=KEY)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--target", default="",
                    help="a built target tree; signs DIR/opt/notebook/de")
    args = ap.parse_args()

    if args.target:
        global DE, MANIFEST
        DE = os.path.join(args.target, DE_REL)
        MANIFEST = os.path.join(DE, "trusted.manifest")

    # No "is this module in the overlay?" guard here, deliberately. It was
    # written after a stray govorimo.py appeared in a target tree, and it was
    # wrong twice over: board/notebookos/post-build.sh already PRUNES any
    # de/*.py with no overlay counterpart (that is how a removed app stops
    # shipping), and it then bundles Govorimo in ON PURPOSE, after the prune,
    # so every ISO carries the current build of it. The overlay is not the only
    # legitimate source, and the prune already closes the window a hand-dropped
    # file could arrive through. Buildroot runs post-build.sh BEFORE
    # mkrelease.sh (see BR2_ROOTFS_POST_BUILD_SCRIPT), so by the time this runs
    # the target is final and signing it is signing what ships.
    if args.check:
        return do_check()

    if not os.path.exists(args.key):
        print("no signing key at %s — this machine cannot mint trust" % args.key,
              file=sys.stderr)
        return 2
    mods = modules()
    # Compact and sorted, so the same tree signs to the same bytes.
    payload = (json.dumps({"version": 1, "modules": mods},
                          sort_keys=True, separators=(",", ":"))
               + "\n").encode("utf-8")
    with open(MANIFEST, "wb") as fh:
        fh.write(payload)
    with open(MANIFEST + ".sig", "wb") as fh:
        fh.write(sign(payload, args.key))
    print("signed %d modules -> %s" % (len(mods), os.path.relpath(MANIFEST, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
