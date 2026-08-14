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
# NB_TTYS1 points gsh at a guest booted with a private NB_WORK dir.
SOCK = os.environ.get("NB_TTYS1") or os.path.join(ROOT, "boot-work", "ttyS1.sock")
SENTINEL = "__GSH_DONE__"
BEGIN = "__GSH_BEGIN__"


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

    # serial tty wants CR line endings; wake the prompt first. Both markers
    # are split ('' in the middle) so the tty ECHO of this command line never
    # contains an assembled marker — only real output lines do. The BEGIN
    # marker exists because the tty wraps the echoed command at its width, so
    # a long command's echo arrives as SEVERAL lines that no first-line
    # heuristic can strip; everything before the assembled BEGIN is echo.
    split = SENTINEL[:6] + "''" + SENTINEL[6:]
    begin = BEGIN[:6] + "''" + BEGIN[6:]
    full = ("\r\nexport DISPLAY=:0; echo %s; { %s; }; echo %s$?\r\n"
            % (begin, cmd, split))
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

    # keep only lines between the assembled markers: before BEGIN is the
    # tty's (possibly wrapped) echo of the command, after DONE is the prompt
    lines = out.splitlines()
    body, rc, seen_begin = [], None, False
    for ln in lines:
        if not seen_begin:
            if BEGIN in ln and begin not in ln:
                seen_begin = True
            continue
        if SENTINEL in ln:
            tail = ln.split(SENTINEL, 1)[1].strip()
            rc = tail if tail else "0"
            break
        body.append(ln)
    print("\n".join(body))
    if rc is None:
        print("[gsh: timed out waiting for sentinel]", file=sys.stderr)
        print("[gsh: the usual cause is that the guest was booted WITHOUT "
              "`nbdebug` on the kernel command line. /opt/notebook/"
              "debugshell.sh only runs a shell when /proc/cmdline contains "
              "it, and otherwise sleeps — deliberately, so an unauthenticated "
              "root shell is not offered to anyone who attaches a serial "
              "cable. A shell-less tty still ECHOES, which is why this looks "
              "like a hang rather than a refusal. Boot a debug image, or "
              "drive the UI over QMP instead (tools/guestdrive.py).]",
              file=sys.stderr)
        return 124
    return int(rc) if rc.isdigit() else 0


if __name__ == "__main__":
    sys.exit(main())
