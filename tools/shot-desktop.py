#!/usr/bin/env python3
"""
Boot Notebook OS headless in QEMU and screenshot the running desktop via QMP
`screendump`. Proves the native GTK desktop actually renders on the real
no-internet kernel — no host display needed.

  shot-desktop.py <out.png> [boot_wait_secs]

Starts run-desktop.sh --headless, waits for the desktop to come up, asks QEMU
to dump the virtio-gpu framebuffer to a PNG, then powers the guest off.
"""
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "boot-work")
SOCK = os.path.join(WORK, "qmp.sock")


def qmp(sock, cmd, **args):
    msg = {"execute": cmd}
    if args:
        msg["arguments"] = args
    sock.sendall((json.dumps(msg) + "\r\n").encode())
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf.decode(errors="replace")


def main():
    out = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else \
        os.path.join(WORK, "desktop.png")
    wait = int(sys.argv[2]) if len(sys.argv) > 2 else 45

    if os.path.exists(SOCK):
        os.remove(SOCK)
    proc = subprocess.Popen(
        ["bash", os.path.join(ROOT, "tools", "run-desktop.sh"), "--headless"])
    try:
        # wait for the QMP socket to appear
        for _ in range(60):
            if os.path.exists(SOCK):
                break
            time.sleep(0.5)
        else:
            print("QMP socket never appeared"); return 1

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        for _ in range(20):
            try:
                s.connect(SOCK); break
            except OSError:
                time.sleep(0.5)
        s.recv(4096)                      # greeting
        qmp(s, "qmp_capabilities")

        print("booting; waiting %ds, then sampling the display..." % wait,
              flush=True)
        time.sleep(wait)

        # TCG boot timing is variable and the framebuffer may briefly show the
        # boot console; sample several frames and keep the one with the most
        # content (largest PNG), which is the settled desktop.
        best, best_size = None, -1
        for i in range(8):
            tmp = out + (".%d" % i)
            qmp(s, "screendump", filename=tmp, format="png")
            time.sleep(1.0)
            try:
                sz = os.path.getsize(tmp)
            except OSError:
                sz = 0
            print("  sample %d: %d bytes" % (i, sz), flush=True)
            if sz > best_size:
                best_size, best = sz, tmp
            if i < 7:
                time.sleep(11)
        if best:
            os.replace(best, out)
        for i in range(8):
            try:
                os.remove(out + (".%d" % i))
            except OSError:
                pass
        qmp(s, "quit")
    finally:
        time.sleep(1)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    ok = os.path.exists(out) and os.path.getsize(out) > 0
    print("wrote", out, os.path.getsize(out) if ok else "(MISSING)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
