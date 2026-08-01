"""New Project / Open Example must not discard real work without asking."""
import os, sys, shutil
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk
# Per-PROCESS home: a shared NB_HOME also shares nbapp's single-instance marker
# dir, and the copy that loses that race is os._exit(0)ed with no output and
# exit status 0 -- a silent false pass. (It is also the home tools/gbasdk_*
# suites write projects into, so two runs would overwrite each other.)
H="/tmp/nbhome-gbasdk-%d" % os.getpid(); os.environ["NB_HOME"]=H
shutil.rmtree(H, ignore_errors=True); os.makedirs(H+"/.config/notebook", exist_ok=True)
import inspect, gbasdk
cls=[c for _n,c in inspect.getmembers(gbasdk, inspect.isclass)
     if c.__module__=="gbasdk" and issubclass(c, Gtk.Window)][0]
def pump(n=300):
    i=0
    while Gtk.events_pending() and i<n: Gtk.main_iteration_do(False); i+=1
R=[]
def check(n, ok, d=""):
    R.append(ok); print("%s %s%s" % ("PASS" if ok else "FAIL", n, "" if ok else "   <- %s"%(d,)))

w=cls(); pump()
# A fresh, untouched project must NOT nag.
asked=[]
w._confirm=lambda *a, **k: (asked.append(a[0]), False)[1]
before=dict(w.proj) if isinstance(w.proj, dict) else None
w._file_example(); pump()
check("a fresh project is replaced without nagging", not asked, asked)

# Now there IS work: it must ask, and Cancel must keep it.
w2=cls(); pump()
w2.proj.setdefault("rooms", []).append({"name":"My room","w":8,"h":8})
mine=len(w2.proj["rooms"])
asked=[]
w2._confirm=lambda *a, **k: (asked.append(a[0]), False)[1]   # user cancels
w2._file_new(); pump()
check("New Project asks before discarding work", bool(asked), asked)
check("...and Cancel keeps the project", len(w2.proj.get("rooms",[]))==mine,
      w2.proj.get("rooms"))
asked=[]
w2._confirm=lambda *a, **k: (asked.append(a[0]), False)[1]
w2._file_example(); pump()
check("Open Example asks too", bool(asked), asked)
check("...and Cancel still keeps it", len(w2.proj.get("rooms",[]))==mine)
# Confirming really does replace.
w2._confirm=lambda *a, **k: True
w2._file_new(); pump()
check("confirming replaces the project", not w2.proj.get("rooms"), w2.proj.get("rooms"))
print("\nRESULT: " + ("ALL PASS" if all(R) else "SOME FAILED"))
sys.exit(0 if all(R) else 1)
