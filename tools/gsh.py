#!/usr/bin/env python3
"""
gsh.py — run a shell command INSIDE the running Notebook OS guest, over the
debug root shell on ttyS1 (qemu -serial unix:boot-work/ttyS1.sock).

  gsh.py 'xwininfo -root -tree'
  gsh.py 'DISPLAY=:0 xdotool getactivewindow getwindowname'

Sends the command, waits for a sentinel, prints the output. X clients need
DISPLAY=:0 — gsh exports it for you.

Hot-reload note: finder.py and app modules can be killed + relaunched freely
(gput.py the new file, then setsid ... python3 <app> &). But DON'T kill
shell.py — session.sh does `exec python3 shell.py`, so shell.py OWNS the X
session; killing it exits xinit and tears down X. Changes to shell.py need a
rebuild + fresh boot to see.
"""
import os
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOCK = os.path.join(ROOT, "boot-work", "ttyS1.sock")
SENTINEL = "__GSH_DONE__"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = " ".join(sys.argv[1:])

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.settimeout(0.5)

    # drain any pending prompt/noise
    try:
        while s.recv(65536):
            pass
    except socket.timeout:
        pass

    # serial tty wants CR line endings; wake the prompt first. The sentinel
    # is split ('' in the middle) so the tty ECHO of this command line never
    # contains the assembled marker — only the real output line does.
    split = SENTINEL[:6] + "''" + SENTINEL[6:]
    full = ("\r\nexport DISPLAY=:0; { %s; }; echo %s$?\r\n" % (cmd, split))
    s.sendall(full.encode())

    buf, deadline = b"", time.time() + 30
    while time.time() < deadline:
        try:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            if SENTINEL.encode() in buf:
                break
        except socket.timeout:
            continue
    out = buf.decode(errors="replace")

    # strip the echoed command line and the sentinel trailer
    lines = out.splitlines()
    body, rc = [], None
    for ln in lines:
        if SENTINEL in ln:
            tail = ln.split(SENTINEL, 1)[1].strip()
            rc = tail if tail else "0"
            break
        body.append(ln)
    # drop the echo of what we typed (first line usually)
    if body and cmd[:20] in body[0]:
        body = body[1:]
    print("\n".join(body))
    if rc is None:
        print("[gsh: timed out waiting for sentinel]", file=sys.stderr)
        return 124
    return int(rc) if rc.isdigit() else 0


if __name__ == "__main__":
    sys.exit(main())
