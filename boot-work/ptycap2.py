import os, pty, time, select
# load the EXACT environment of the live DE session (PID 138)
denv = {}
try:
    raw = open("/proc/138/environ","rb").read()
    for kv in raw.split(b"\0"):
        if b"=" in kv:
            k,v = kv.split(b"=",1)
            denv[k.decode()] = v.decode(errors="replace")
except Exception as e:
    print("envload err", e)
print("=== DE env keys ===")
print(" ".join(sorted(denv.keys())))
denv["TERM"]="xterm-256color"; denv["HOME"]=denv.get("NB_HOME","/root")
pid, fd = pty.fork()
if pid == 0:
    os.execve("/bin/bash", ["/bin/bash"], denv)   # NON-login, exactly like terminal.py
else:
    buf=b""; t=time.time()
    while time.time()-t < 2.5:
        r,_,_ = select.select([fd],[],[],0.3)
        if r:
            try: d=os.read(fd,4096)
            except OSError: break
            if not d: break
            buf+=d
    try: os.write(fd,b"exit\n")
    except OSError: pass
    print("=== CAPTURED (DE env, non-login bash) ===")
    print(buf.decode(errors="replace"))
    print("=== END ===")
