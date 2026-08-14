#!/usr/bin/env python3
"""Packages install-path gate: the on-device installer the UI drives.

Builds a signed .nbpkg onto a fake USB mount, then exercises the exact
nbpkg_install module the Packages app uses: scan finds it, inspect verifies it,
install lays it into a scratch target and registers the app so Finder sees it.
Then proves refusal: a tampered package is not installable and writes nothing.
Exit 0 = all checks pass.
"""
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos", "rootfs-overlay",
                  "opt", "notebook", "de")
NBPKG = os.path.join(ROOT, "tools", "nbpkg.py")
KEY = os.path.join(ROOT, "packaging", "keys", "nbpkg-signing.key")
CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"  ({detail})" if detail and not cond else ""))


def main():
    if not os.path.exists(KEY):
        print("no signing key")
        return 2
    sys.path.insert(0, DE)
    import nbpkg_install

    td = tempfile.mkdtemp(prefix="nbpkg-ui-")
    try:
        # Build a signed package into a fake USB mount.
        src = os.path.join(td, "src")
        os.makedirs(os.path.join(src, "app"))
        open(os.path.join(src, "app", "govorimo.py"), "w").write("# app\n")
        manifest = {
            "name": "Govorimo", "version": "2.0.0",
            "app": {"display": "Govorimo", "module": "govorimo",
                    "kind": "Messaging", "icon": "radio"},
            "files": [{"src": "app/govorimo.py",
                       "dest": "opt/notebook/de/govorimo.py", "mode": "0755"}],
        }
        mpath = os.path.join(td, "m.json")
        json.dump(manifest, open(mpath, "w"))
        mount = os.path.join(td, "media", "STICK")
        os.makedirs(mount)
        pkg = os.path.join(mount, "govorimo.nbpkg")
        subprocess.run([sys.executable, NBPKG, "build", "--manifest", mpath,
                        "--root", src, "--out", pkg, "--key", KEY],
                       check=True, capture_output=True)

        # scan(): the UI finds the package on the stick.
        found = nbpkg_install.scan([mount])
        check("the app store finds a package on the stick",
              any(p.endswith("govorimo.nbpkg") for p, _ in found), str(found))

        # inspect(): verify before offering — identity is the manifest's, and
        # it verifies against the pinned public key that ships in the overlay.
        m, payloads = nbpkg_install.inspect(pkg)
        check("the package verifies against the pinned key",
              m["name"] == "Govorimo" and m["version"] == "2.0.0")

        # install(): into a scratch target root.
        target = os.path.join(td, "target")
        os.makedirs(target)
        nbpkg_install.install(pkg, target=target)
        check("the app installs to the target",
              os.path.exists(os.path.join(target, "opt", "notebook", "de",
                                          "govorimo.py")))
        reg = os.path.join(target, "opt", "notebook", "de",
                           "installed_apps.json")
        reg_data = json.load(open(reg)) if os.path.exists(reg) else {}
        check("the app is registered so Finder sees it",
              reg_data.get("Govorimo", {}).get("module") == "govorimo")

        # Refusal: a tampered package must not be installable, and install must
        # write nothing to a fresh target.
        tampered = os.path.join(td, "tampered.nbpkg")
        import io
        with tarfile.open(pkg, "r:gz") as tin, \
                tarfile.open(tampered, "w:gz") as tout:
            for mem in tin.getmembers():
                data = tin.extractfile(mem).read()
                if mem.name.startswith("files/"):
                    data += b"evil"
                info = tarfile.TarInfo(mem.name); info.size = len(data)
                tout.addfile(info, io.BytesIO(data))
        refused = False
        try:
            nbpkg_install.inspect(tampered)
        except nbpkg_install.PkgError:
            refused = True
        check("a tampered package is refused by inspect", refused)
        t2 = os.path.join(td, "target2"); os.makedirs(t2)
        wrote = False
        try:
            nbpkg_install.install(tampered, target=t2)
            wrote = True
        except nbpkg_install.PkgError:
            wrote = False
        check("a tampered package writes nothing on install",
              not wrote and not os.path.exists(os.path.join(t2, "opt")))

        failed = [n for n, ok in CHECKS if not ok]
        print(f"\nRESULT: {'ALL PASS' if not failed else str(len(failed))+' FAILED'}"
              f"  ({len(CHECKS)} checks)")
        return 0 if not failed else 1
    finally:
        shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
