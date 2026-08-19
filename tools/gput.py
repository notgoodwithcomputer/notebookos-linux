#!/usr/bin/env python3
"""
gput.py — copy a host file into the running Notebook OS guest over the ttyS1
debug shell (base64 over serial; no network exists by design).

  gput.py <host-file> <guest-path>
"""
import base64
import os
import shlex
import socket
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOCK = os.path.join(ROOT, "boot-work", "ttyS1.sock")


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    src, dst = sys.argv[1], sys.argv[2]
    data = open(src, "rb").read()
    b64 = base64.b64encode(data).decode()
    token = str(os.getpid())
    remote_b64 = "/tmp/.gput-%s.b64" % token
    # Same directory as the destination so the final mv is a filesystem-local
    # atomic rename, not a cross-device copy that can truncate the destination.
    remote_out = dst + ".gput-%s.tmp" % token
    q_b64, q_out, q_dst = map(shlex.quote, (remote_b64, remote_out, dst))

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.settimeout(0.5)

    def drain():
        try:
            while s.recv(65536):
                pass
        except socket.timeout:
            pass

    def cmd(c, wait=0.3):
        drain()
        s.sendall(("\r\n" + c + "\r\n").encode())
        time.sleep(wait)

    cmd(": > %s" % q_b64)
    for i in range(0, len(b64), 400):
        cmd("echo '%s' >> %s" % (b64[i:i + 400], q_b64))
    # Decode beside the destination logically, not over it. The last known-good
    # guest file remains intact until the staged bytes pass their checksum.
    cmd("base64 -d %s > %s" % (q_b64, q_out), wait=1.5)
    cmd('if [ -e %s ]; then chmod "$(stat -c %%a %s)" %s; fi'
        % (q_dst, q_dst, q_out))

    # verify by md5
    import hashlib
    want = hashlib.md5(data).hexdigest()
    drain()
    s.sendall(("\r\nmd5sum %s\r\n" % q_out).encode())
    time.sleep(2.0)
    buf = b""
    try:
        while True:
            c = s.recv(65536)
            if not c:
                break
            buf += c
    except socket.timeout:
        pass
    ok = want.encode() in buf
    if ok:
        cmd("mv -f -- %s %s" % (q_out, q_dst), wait=0.5)
        drain()
        s.sendall(("\r\nmd5sum %s\r\n" % q_dst).encode())
        time.sleep(1.0)
        final = b""
        try:
            while True:
                c = s.recv(65536)
                if not c:
                    break
                final += c
        except socket.timeout:
            pass
        ok = want.encode() in final
    else:
        cmd("rm -f -- %s" % q_out)
    cmd("rm -f -- %s" % q_b64)
    print("uploaded %s -> %s  md5 %s  %s"
          % (src, dst, want, "VERIFIED" if ok else "MISMATCH"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
