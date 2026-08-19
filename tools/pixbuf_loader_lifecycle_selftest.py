#!/usr/bin/env python3
"""Headless cache invalidation checks for target pixbuf loaders."""
import os
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                      "etc/init.d/S34pixbufloaders")


with tempfile.TemporaryDirectory(prefix="nb-pixbuf-") as td:
    bindir = os.path.join(td, "bin")
    loaders = os.path.join(td, "gdk-pixbuf-2.0", "2.10.0", "loaders")
    os.makedirs(bindir)
    os.makedirs(loaders)
    module = os.path.join(loaders, "libpixbufloader-demo.so")
    stamp = os.path.join(td, "stamp")
    calls = os.path.join(td, "calls")
    query = os.path.join(bindir, "gdk-pixbuf-query-loaders")
    with open(query, "w") as fh:
        fh.write('#!/bin/sh\nprintf "call\\n" >> "$NB_PIXBUF_CALLS"\n'
                 'printf "loader metadata\\n"\n')
    os.chmod(query, 0o755)
    env = dict(os.environ, PATH=bindir + ":/usr/bin:/bin",
               NB_PIXBUF_LOADER_DIR=loaders, NB_PIXBUF_STAMP=stamp,
               NB_PIXBUF_CALLS=calls)

    def start():
        subprocess.run(["/bin/sh", SCRIPT, "start"], env=env, check=True,
                       stdout=subprocess.DEVNULL, timeout=10)

    with open(module, "wb") as fh:
        fh.write(b"version-one")
    start()
    start()
    with open(calls) as fh:
        assert len(fh.readlines()) == 1, "unchanged loader was requeried"

    with open(module, "wb") as fh:
        fh.write(b"version-two")
    start()
    with open(calls) as fh:
        assert len(fh.readlines()) == 2, "same-name loader upgrade stayed stale"

    with open(os.path.join(loaders, "libpixbufloader-extra.so"), "wb") as fh:
        fh.write(b"extra")
    start()
    with open(calls) as fh:
        assert len(fh.readlines()) == 3, "new loader did not refresh cache"

print("PIXBUF LOADER LIFECYCLE SELFTEST: 3 checks, all pass")
