import sys, os
sys.path.insert(0, "/opt/notebook/de")
import shell
# don't spawn real apps or register real child-watches; exercise the ref-count
class FakeProc:
    pid = 4242
shell.subprocess.Popen = lambda *a, **k: FakeProc()
shell.GLib.child_watch_add = lambda pid, cb: None
FLAG = "/tmp/nb-app-active"
try: os.remove(FLAG)
except OSError: pass
shell._app_count = 0
shell.launch("writer")
shell.launch("novel")
print("2 apps open:      count=%d flag=%s  (expect 2, True)" % (shell._app_count, os.path.exists(FLAG)))
shell._clear_app_flag()
print("closed 1 of 2:    count=%d flag=%s  (expect 1, True <- the fix)" % (shell._app_count, os.path.exists(FLAG)))
shell._clear_app_flag()
print("closed last:      count=%d flag=%s  (expect 0, False)" % (shell._app_count, os.path.exists(FLAG)))
