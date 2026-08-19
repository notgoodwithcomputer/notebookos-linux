#!/usr/bin/env python3
"""app_trust_selftest — the walled garden's userspace half (docs/APP-TRUST.md L2).

The contract, stated as the questions an attacker asks:

  * a module that ships in the image and is unchanged  -> runs
  * one byte changed in it                             -> refused
  * a .py dropped into the module directory            -> refused
  * ...even with an entry forged into installed_apps.json, because the registry
    is a cache and never an authority
  * a manifest re-signed with a DIFFERENT key          -> authorises nothing
  * no manifest at all                                 -> authorises nothing
    (fail closed: the lockout must not be one deleted file away from off)
  * an app installed from a real signed .nbpkg         -> runs, on the strength
    of the signature that came WITH it, because the device holds no private key

Everything here runs against a scratch tree built by the real tools: the
manifest is produced by tools/gen_trust_manifest.py logic and the package by
tools/nbpkg.py, so a change to either format breaks this suite rather than
quietly passing. Display-free.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
KEY = os.path.join(ROOT, "packaging/keys/nbpkg-signing.key")
PUB = os.path.join(ROOT, "packaging/nbpkg-release.pub")
NBPKG = os.path.join(ROOT, "tools", "nbpkg.py")
sys.path.insert(0, DE)

import nbtrust                                                   # noqa: E402
import nbpkg_install                                             # noqa: E402

FAILS = []
RAN = []


def check(name, ok, detail=""):
    RAN.append(name)
    print(("ok   " if ok else "FAIL ") + name + (("  (%s)" % detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def sign(payload, key):
    with tempfile.NamedTemporaryFile() as msg, tempfile.NamedTemporaryFile() as sig:
        msg.write(payload); msg.flush()
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", key, "-rawin",
                        "-in", msg.name, "-out", sig.name],
                       check=True, capture_output=True)
        return open(sig.name, "rb").read()


def write_manifest(de_dir, mods, key):
    payload = (json.dumps({"version": 1, "modules": mods},
                          sort_keys=True, separators=(",", ":")) + "\n").encode()
    open(os.path.join(de_dir, "trusted.manifest"), "wb").write(payload)
    open(os.path.join(de_dir, "trusted.manifest.sig"), "wb").write(sign(payload, key))


def point_at(de_dir, root_dir):
    nbtrust.DE_DIR = de_dir
    nbtrust.MANIFEST = os.path.join(de_dir, "trusted.manifest")
    nbtrust.TRUST_DIR = os.path.join(de_dir, ".trust")
    nbtrust.ROOT_DIR = root_dir
    nbtrust.PUB = PUB
    nbtrust.refresh()


def main():
    # The launcher deliberately fails closed, so the signing hook is part of
    # the canonical clean-build configuration, not an optional release nicety.
    # Generated buildroot/.config files cannot be the sole source of this.
    defconfig = os.path.join(ROOT, "buildroot", "configs",
                             "notebookos_defconfig")
    configured = open(defconfig, encoding="utf-8").read()
    check("a clean Buildroot build invokes the trust-manifest signer",
          'BR2_ROOTFS_POST_BUILD_SCRIPT="board/notebookos/post-build.sh '
          '../tools/mkrelease.sh"' in configured)

    if not os.path.exists(KEY):
        print("no signing key at %s" % KEY)
        return 2

    td = tempfile.mkdtemp(prefix="app-trust-")
    try:
        target = os.path.join(td, "target")
        de = os.path.join(target, "opt", "notebook", "de")
        os.makedirs(de)

        # An image with two modules, signed.
        for mod, body in (("writer", '"""Writer."""\n'), ("music", '"""Music."""\n')):
            open(os.path.join(de, mod + ".py"), "w").write(body)
        mods = {n: nbtrust._sha256_file(os.path.join(de, n))
                for n in ("writer.py", "music.py")}
        write_manifest(de, mods, KEY)
        point_at(de, target)

        ok, why = nbtrust.check_module("writer")
        check("a module the release key signed runs", ok, why)

        # One byte changed.
        with open(os.path.join(de, "music.py"), "a") as fh:
            fh.write("# tampered\n")
        nbtrust.refresh()
        ok, why = nbtrust.check_module("music")
        check("one byte changed in a signed module refuses it",
              not ok and "hash" in why, why)

        # A module nobody signed.
        open(os.path.join(de, "evil.py"), "w").write('"""Not ours."""\n')
        nbtrust.refresh()
        ok, why = nbtrust.check_module("evil")
        check("a module dropped into the image is refused", not ok, why)

        # ...and forging the registry does not help, because the registry is
        # not what the launcher asks.
        json.dump({"Evil": {"module": "evil", "kind": "Utility",
                            "version": "1.0"}},
                  open(os.path.join(de, "installed_apps.json"), "w"))
        nbtrust.refresh()
        ok, why = nbtrust.check_module("evil")
        check("a forged installed_apps.json entry does not authorise it",
              not ok, why)

        # A manifest signed by somebody else's key.
        other = os.path.join(td, "other.key")
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519",
                        "-out", other], check=True, capture_output=True)
        mods2 = dict(mods)
        mods2["evil.py"] = nbtrust._sha256_file(os.path.join(de, "evil.py"))
        write_manifest(de, mods2, other)
        nbtrust.refresh()
        ok, _ = nbtrust.check_module("writer")
        ok2, _ = nbtrust.check_module("evil")
        check("a manifest signed by another key authorises NOTHING",
              not ok and not ok2)

        # No manifest at all: fail closed.
        os.unlink(os.path.join(de, "trusted.manifest"))
        os.unlink(os.path.join(de, "trusted.manifest.sig"))
        nbtrust.refresh()
        ok, why = nbtrust.check_module("writer")
        check("with no manifest nothing runs at all (fail closed)", not ok, why)

        # --- a real package, installed by the real installer -----------------
        write_manifest(de, mods, KEY)          # put the image manifest back
        src = os.path.join(td, "src")
        os.makedirs(os.path.join(src, "app"))
        open(os.path.join(src, "app", "govorimo.py"), "w").write('"""Govorimo."""\n')
        manifest = {
            "name": "Govorimo", "version": "2.0.0",
            "app": {"display": "Govorimo", "module": "govorimo",
                    "kind": "Messaging", "icon": "radio"},
            "files": [{"src": "app/govorimo.py",
                       "dest": "opt/notebook/de/govorimo.py", "mode": "0755"}],
        }
        mpath = os.path.join(td, "m.json")
        json.dump(manifest, open(mpath, "w"))
        pkg = os.path.join(td, "govorimo.nbpkg")
        subprocess.run([sys.executable, NBPKG, "build", "--manifest", mpath,
                        "--root", src, "--out", pkg, "--key", KEY],
                       check=True, capture_output=True)

        nbtrust.refresh()
        ok, _ = nbtrust.check_module("govorimo")
        check("before installing, the package's app is not authorised", not ok)

        nbpkg_install.install(pkg, target=target, pub=PUB)
        ok, why = nbtrust.check_module("govorimo")
        check("a newly installed signed app runs without restarting the launcher",
              ok, why)
        check("...on its own retained signature, not a locally minted one",
              os.path.exists(os.path.join(de, ".trust", "govorimo.manifest.sig")))

        # And the same app, edited after install, stops running.
        with open(os.path.join(de, "govorimo.py"), "a") as fh:
            fh.write("# tampered after install\n")
        nbtrust.refresh()
        ok, why = nbtrust.check_module("govorimo")
        check("an installed app edited afterwards is refused",
              not ok and "hash" in why, why)
    finally:
        shutil.rmtree(td, ignore_errors=True)

    print("\nRESULT: %s  (%d checks)"
          % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS), len(RAN)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
