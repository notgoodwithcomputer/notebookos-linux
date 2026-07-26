import os, pty, time, select
print("=== /etc/bashrc exists? ===", os.path.exists("/etc/bashrc"))
if os.path.exists("/etc/bashrc"):
    print(open("/etc/bashrc").read()[:500])
pid, fd = pty.fork()
if pid == 0:
    os.environ["TERM"] = "xterm-256color"
    os.execv("/bin/bash", ["/bin/bash", "-i"])
else:
    buf = b""
    t = time.time()
    while time.time() - t < 2.5:
        r, _, _ = select.select([fd], [], [], 0.3)
        if r:
            try:
                d = os.read(fd, 4096)
            except OSError:
                break
            if not d:
                break
            buf += d
    try:
        os.write(fd, b"exit\n")
    except OSError:
        pass
    print("=== CAPTURED STARTUP OUTPUT ===")
    print(buf.decode(errors="replace"))
    print("=== END ===")
