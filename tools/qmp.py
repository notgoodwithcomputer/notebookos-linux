#!/usr/bin/env python3
"""
qmp.py — drive the running Notebook OS guest over QMP, one command per call.

Boot the guest once (`qmp.py boot`), then poke it incrementally: move the
tablet pointer, click, double-click, type keys, take screendumps. Each
invocation opens a fresh QMP connection to boot-work/qmp.sock, so the guest
keeps running between calls — the dev loop is interactive without re-booting
the (slow, TCG) machine for every step.

  qmp.py boot [wait_secs]        start run-desktop.sh --headless, wait for X
  qmp.py shot <out.png>          screendump the virtio-gpu framebuffer
  qmp.py move <x> <y>            absolute pointer move (pixel coords)
  qmp.py click <x> <y>           move + left click
  qmp.py dblclick <x> <y>        move + double left click
  qmp.py key <qcode> [...]       send-key, e.g. `key kp_7 kp_multiply kp_6`
  qmp.py quit                    power the guest off (qmp quit)

Pointer coords are pixels on the current mode (1920x1080); the usb-tablet
axis range is 0..32767 and QEMU maps it to the framebuffer.
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

SCREEN_W, SCREEN_H = 1920, 1080
TABLET_MAX = 32767


class Qmp:
    def __init__(self, path=SOCK):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self._read()                       # greeting
        self.cmd("qmp_capabilities")

    def _read(self):
        buf = b""
        while b"\n" not in buf:
            chunk = self.s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf.decode(errors="replace")

    def cmd(self, name, **args):
        msg = {"execute": name}
        if args:
            msg["arguments"] = args
        self.s.sendall((json.dumps(msg) + "\r\n").encode())
        # skip async events until we get a return/error
        while True:
            for line in self._read().splitlines():
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if "return" in obj or "error" in obj:
                    return obj


def scale(x, y):
    return (int(x * TABLET_MAX / (SCREEN_W - 1)),
            int(y * TABLET_MAX / (SCREEN_H - 1)))


def ev_abs(x, y):
    sx, sy = scale(x, y)
    return [{"type": "abs", "data": {"axis": "x", "value": sx}},
            {"type": "abs", "data": {"axis": "y", "value": sy}}]


def ev_btn(down):
    return [{"type": "btn", "data": {"button": "left", "down": down}}]


def send(q, events):
    r = q.cmd("input-send-event", events=events)
    if "error" in r:
        print("QMP error:", r, file=sys.stderr)
        sys.exit(1)


def click_at(q, x, y, n=1):
    send(q, ev_abs(x, y))
    time.sleep(0.15)
    for i in range(n):
        send(q, ev_btn(True))
        time.sleep(0.06)
        send(q, ev_btn(False))
        if i + 1 < n:
            time.sleep(0.12)               # well inside GTK's dbl-click window


def do_boot(wait):
    if os.path.exists(SOCK):
        print("qmp.sock exists — guest already running? (rm it if stale)",
              file=sys.stderr)
        sys.exit(1)
    logf = open(os.path.join(WORK, "qemu.log"), "wb")
    subprocess.Popen(
        ["bash", os.path.join(ROOT, "tools", "run-desktop.sh"), "--headless"],
        stdout=logf, stderr=logf, start_new_session=True)
    for _ in range(120):
        if os.path.exists(SOCK):
            break
        time.sleep(0.5)
    else:
        print("QMP socket never appeared", file=sys.stderr)
        sys.exit(1)
    print("guest up; waiting %ds for the desktop..." % wait, flush=True)
    # the session logs to serial; wait for the shell's proof-of-life line
    serial = os.path.join(WORK, "serial.log")
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            with open(serial, "rb") as fh:
                if b"Notebook OS shell up" in fh.read():
                    print("desktop is up")
                    time.sleep(3)          # let the first paint settle
                    return
        except OSError:
            pass
        time.sleep(2)
    print("timed out waiting for the shell (check %s)" % serial,
          file=sys.stderr)
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    op = sys.argv[1]

    if op == "boot":
        do_boot(int(sys.argv[2]) if len(sys.argv) > 2 else 180)
        return 0

    q = Qmp()
    if op == "shot":
        out = os.path.abspath(sys.argv[2])
        r = q.cmd("screendump", filename=out, format="png")
        if "error" in r:
            print("QMP error:", r, file=sys.stderr)
            return 1
        time.sleep(0.5)
        print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
    elif op == "move":
        send(q, ev_abs(int(sys.argv[2]), int(sys.argv[3])))
    elif op == "click":
        click_at(q, int(sys.argv[2]), int(sys.argv[3]), n=1)
    elif op == "dblclick":
        click_at(q, int(sys.argv[2]), int(sys.argv[3]), n=2)
    elif op == "key":
        for qcode in sys.argv[2:]:
            r = q.cmd("send-key",
                      keys=[{"type": "qcode", "data": qcode}])
            if "error" in r:
                print("QMP error on %s: %s" % (qcode, r), file=sys.stderr)
                return 1
            time.sleep(0.15)
    elif op == "quit":
        q.cmd("quit")
    else:
        print("unknown op:", op, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
