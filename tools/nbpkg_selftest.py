#!/usr/bin/env python3
"""nbpkg gate: the deployment story, proved end to end.

Builds a signed package, verifies it, installs it into a scratch target root,
and asserts the app is registered — then proves the security order: a tampered
payload is refused, a forged signature is refused, and a traversal dest is
refused, all WITHOUT writing to the target. Exit 0 = all checks pass.
"""
import json
import glob
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NBPKG = os.path.join(ROOT, "tools", "nbpkg.py")
KEY = os.path.join(ROOT, "packaging", "keys", "nbpkg-signing.key")
PUB = os.path.join(ROOT, "packaging", "nbpkg-release.pub")
CHECKS = []
sys.path.insert(0, os.path.join(ROOT, "buildroot", "board", "notebookos",
                                "rootfs-overlay", "opt", "notebook", "de"))
import nbpkg_install  # noqa: E402


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  ({detail})" if detail and not cond else ""))


def run(*args):
    return subprocess.run([sys.executable, NBPKG, *args],
                          capture_output=True, text=True)


def main():
    if not os.path.exists(KEY):
        print("no signing key — run: openssl genpkey -algorithm ed25519 "
              "-out packaging/keys/nbpkg-signing.key")
        return 2
    td = tempfile.mkdtemp(prefix="nbpkg-test-")
    try:
        # Trust metadata for the same app can be published by overlapping
        # installer processes. Each writer must own its staging file; a shared
        # `path.tmp` lets one replace consume the other's file.
        trust_race = os.path.join(td, "trust.manifest")
        replace_barrier = threading.Barrier(2)
        real_replace = nbpkg_install.os.replace
        write_results = []

        def synchronized_replace(src, dst):
            replace_barrier.wait(timeout=2)
            return real_replace(src, dst)

        def trust_write(value):
            try:
                nbpkg_install._atomic_write(trust_race, value)
                write_results.append(True)
            except OSError:
                write_results.append(False)

        nbpkg_install.os.replace = synchronized_replace
        try:
            writers = [threading.Thread(target=trust_write, args=(value,))
                       for value in (b"first manifest", b"second manifest")]
            for writer in writers:
                writer.start()
            for writer in writers:
                writer.join(timeout=3)
        finally:
            nbpkg_install.os.replace = real_replace
        complete = open(trust_race, "rb").read() if os.path.exists(trust_race) else b""
        check("overlapping trust writes retain independent ownership",
              write_results == [True, True] and
              complete in (b"first manifest", b"second manifest"),
              "results=%r content=%r" % (write_results, complete))

        # Verified payloads need the same ownership guarantee as trust files.
        payload_race = os.path.join(td, "installed-module.py")
        replace_barrier = threading.Barrier(2)
        write_results = []

        def payload_write(value):
            try:
                nbpkg_install._install_payload(payload_race, value, 0o755)
                write_results.append(True)
            except OSError:
                write_results.append(False)

        nbpkg_install.os.replace = synchronized_replace
        try:
            writers = [threading.Thread(target=payload_write, args=(value,))
                       for value in (b"first payload", b"second payload")]
            for writer in writers:
                writer.start()
            for writer in writers:
                writer.join(timeout=3)
        finally:
            nbpkg_install.os.replace = real_replace
        installed = (open(payload_race, "rb").read()
                     if os.path.exists(payload_race) else b"")
        check("overlapping payload writes retain independent ownership",
              write_results == [True, True] and
              installed in (b"first payload", b"second payload") and
              os.stat(payload_race).st_mode & 0o111,
              "results=%r content=%r" % (write_results, installed))

        # A tiny source tree standing in for a Govorimo package.
        src = os.path.join(td, "src")
        os.makedirs(os.path.join(src, "app"))
        os.makedirs(os.path.join(src, "root", "Applications"))
        open(os.path.join(src, "app", "govorimo.py"), "w").write(
            "# the app module\n")
        open(os.path.join(src, "root", "Applications", "Govorimo.app"),
             "w").write("#!/bin/sh\n# Notebook OS application package\n")
        manifest = {
            "name": "Govorimo", "version": "2.0.0",
            "app": {"display": "Govorimo", "module": "govorimo",
                    "kind": "Messaging", "icon": "radio"},
            "service": "govorimod-run.sh",
            "files": [
                {"src": "app/govorimo.py",
                 "dest": "opt/notebook/de/govorimo.py", "mode": "0755"},
                {"src": "root/Applications/Govorimo.app",
                 "dest": "root/Applications/Govorimo.app", "mode": "0755"},
            ],
        }
        mpath = os.path.join(td, "manifest.json")
        json.dump(manifest, open(mpath, "w"))
        pkg = os.path.join(td, "govorimo.nbpkg")

        r = run("build", "--manifest", mpath, "--root", src, "--out", pkg,
                "--key", KEY)
        check("a signed package builds", r.returncode == 0, r.stderr[-200:])

        r = run("verify", pkg, "--pub", PUB)
        check("the signed package verifies", r.returncode == 0
              and "VERIFIED" in r.stdout, r.stdout + r.stderr)

        # Install into a scratch target root.
        target = os.path.join(td, "target")
        os.makedirs(target)
        r = run("install", pkg, "--target", target, "--pub", PUB)
        check("it installs into the target", r.returncode == 0
              and "INSTALLED" in r.stdout, r.stdout + r.stderr)
        check("the app module landed at its dest",
              os.path.exists(os.path.join(target, "opt", "notebook", "de",
                                          "govorimo.py")))
        check("the .app launcher landed",
              os.path.exists(os.path.join(target, "root", "Applications",
                                          "Govorimo.app")))
        # Data-driven registration — Finder/Packages can see it without a
        # source edit.
        reg_path = os.path.join(target, "opt", "notebook", "de",
                                "installed_apps.json")
        reg = json.load(open(reg_path)) if os.path.exists(reg_path) else {}
        check("the app registered (data-driven, no source edit)",
              reg.get("Govorimo", {}).get("module") == "govorimo", str(reg))
        check("the mode was honored (executable module)",
              os.stat(os.path.join(target, "opt", "notebook", "de",
                                   "govorimo.py")).st_mode & 0o111)

        # Valid JSON can still have the wrong registry shape. Installation must
        # rebuild discovery metadata instead of failing after payload/trust
        # publication, and it must preserve the old bytes for diagnosis.
        for bad_registry in ([], None, "legacy registry"):
            with open(reg_path, "w") as fh:
                json.dump(bad_registry, fh)
            before_damage = set(glob.glob(reg_path + ".damaged-*"))
            r = run("install", pkg, "--target", target, "--pub", PUB)
            repaired = json.load(open(reg_path)) if r.returncode == 0 else {}
            after_damage = set(glob.glob(reg_path + ".damaged-*"))
            check("wrong-shaped registry %r is quarantined and rebuilt"
                  % (bad_registry,),
                  r.returncode == 0
                  and repaired.get("Govorimo", {}).get("module") == "govorimo"
                  and len(after_damage - before_damage) == 1,
                  r.stdout + r.stderr)

        # SECURITY: a tampered payload must be refused, and nothing new written.
        tampered = os.path.join(td, "tampered.nbpkg")
        with tarfile.open(pkg, "r:gz") as tin, \
                tarfile.open(tampered, "w:gz") as tout:
            import io
            for m in tin.getmembers():
                data = tin.extractfile(m).read()
                if m.name.startswith("files/"):
                    data = data + b"malware"  # swap the payload bytes
                info = tarfile.TarInfo(m.name); info.size = len(data)
                tout.addfile(info, io.BytesIO(data))
        r = run("verify", tampered, "--pub", PUB)
        check("a tampered payload is REFUSED", r.returncode != 0
              and "REFUSED" in r.stdout, r.stdout)
        t2 = os.path.join(td, "target2"); os.makedirs(t2)
        r = run("install", tampered, "--target", t2, "--pub", PUB)
        check("a tampered package writes NOTHING on install",
              r.returncode != 0 and not os.path.exists(
                  os.path.join(t2, "opt")), r.stdout)

        # SECURITY: a forged signature (different key) must be refused.
        forged_key = os.path.join(td, "forged.key")
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519",
                        "-out", forged_key], capture_output=True)
        forged = os.path.join(td, "forged.nbpkg")
        run("build", "--manifest", mpath, "--root", src, "--out", forged,
            "--key", forged_key)
        r = run("verify", forged, "--pub", PUB)
        check("a package signed by the wrong key is REFUSED",
              r.returncode != 0 and "REFUSED" in r.stdout, r.stdout)

        failed = [n for n, ok in CHECKS if not ok]
        print(f"\nRESULT: {'ALL PASS' if not failed else str(len(failed))+' FAILED'}"
              f"  ({len(CHECKS)} checks)")
        return 0 if not failed else 1
    finally:
        shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
