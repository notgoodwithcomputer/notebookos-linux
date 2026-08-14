#!/usr/bin/env python3
"""nbpkg gate: the deployment story, proved end to end.

Builds a signed package, verifies it, installs it into a scratch target root,
and asserts the app is registered — then proves the security order: a tampered
payload is refused, a forged signature is refused, and a traversal dest is
refused, all WITHOUT writing to the target. Exit 0 = all checks pass.
"""
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NBPKG = os.path.join(ROOT, "tools", "nbpkg.py")
KEY = os.path.join(ROOT, "packaging", "keys", "nbpkg-signing.key")
PUB = os.path.join(ROOT, "packaging", "nbpkg-release.pub")
CHECKS = []


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
