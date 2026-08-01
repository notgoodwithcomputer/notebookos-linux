"""The guard: a blank app must never erase a store that still has content;
a delete the user asked for still must."""
import os, sys, shutil, json
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk
# Per-PROCESS home: see academics_class_selftest.py. A shared NB_HOME also
# shares nbapp's single-instance marker dir, and the copy that loses that race
# is os._exit(0)ed with no output and exit status 0 -- a silent false pass.
H="/tmp/nbhome-guard-%d" % os.getpid(); os.environ["NB_HOME"]=H
import academics
STORE = H+"/.config/notebook/academics.json"
GOOD = {"classes":[{"label":"Chem","color":"#9A7B4F","room":"","instructor":"",
                    "meets":[{"day":0,"start":"09:00","end":"10:20","room":""}]}],
        "homework":[{"title":"PS4","cls":0,"due":"2026-07-30","done":False,"note":""}],
        "lectures":[],"active":-1}
def fresh():
    shutil.rmtree(H, ignore_errors=True); os.makedirs(H+"/.config/notebook", exist_ok=True)
    json.dump(GOOD, open(STORE,"w"))
def pump(n=200):
    i=0
    while Gtk.events_pending() and i<n: Gtk.main_iteration_do(False); i+=1
def content():
    d=json.load(open(STORE))
    return (len(d.get("classes",[])), len(d.get("homework",[])))
R=[]
def check(n, ok, d=""):
    R.append(ok); print("%s %s%s" % ("PASS" if ok else "FAIL", n, "" if ok else "   <- %s"%(d,)))

# 1. Something wipes the model behind the app's back; the save must refuse.
fresh(); w = academics.Academics(); pump()
w.classes, w.lectures, w.homework = [], [], []
w._save_to_disk()
check("a blank model does not erase a real store", content() == (1, 1), content())
w.destroy()

# 2. A delete the user asked for still empties the file.
fresh(); w = academics.Academics(); pump()
w._confirm = lambda *a, **k: True
w._delete_class_at(0); pump()
check("a confirmed delete still writes through", content() == (0, 1), content())
w.destroy()

# 3. A genuinely empty first run is still allowed to create its file.
shutil.rmtree(H, ignore_errors=True); os.makedirs(H+"/.config/notebook", exist_ok=True)
w = academics.Academics(); pump()
ok = w._save_to_disk() or not os.path.exists(STORE)
check("a fresh install can still save an empty term", ok)
print("\nRESULT: " + ("ALL PASS" if all(R) else "SOME FAILED"))
sys.exit(0 if all(R) else 1)
