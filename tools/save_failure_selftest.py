"""A save that cannot happen must SAY SO, and must not look like deletion.

THE BUG CLASS THIS EXISTS FOR. There is no network and no cloud on this
machine, so the file under the user's home is the only copy of their work. When
a write fails -- a full disk, a home remounted read-only after a filesystem
error, a permission the installer got wrong -- the app carries on showing work
that is no longer anywhere. The store keeps whatever the last write that DID
succeed put there, so the first thing entered survives and everything after it
appears to vanish the moment the app closes. From the user's chair that is
indistinguishable from "this app deleted my data".

Part 1 is the depth test, on Academics: warn once, name the actual cause, and
go quiet again on recovery.

Part 2 sweeps every other app that holds work which exists nowhere else. Nine
of them used to swallow the failure with a bare `except: pass`. One app per
process -- these modules hold state (loaded stores, the single-instance
registry) that would otherwise let one case lie about the next.
"""
import os, sys, shutil, json, errno, subprocess
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk, Gdk
H="/tmp/nbhome-nospace"; os.environ["NB_HOME"]=H
shutil.rmtree(H, ignore_errors=True); os.makedirs(H+"/.config/notebook", exist_ok=True)
import academics, nbapp
def pump(n=300):
    i=0
    while Gtk.events_pending() and i<n: Gtk.main_iteration_do(False); i+=1
R=[]
def chk(n, ok, d=""):
    R.append(ok); print("%s %s%s" % ("PASS" if ok else "FAIL", n, "" if ok else "  <- %s"%(d,)))

print("--- 1. the reason, the once, and the recovery (Academics) --------")
w=academics.Academics(); pump()
said=[]
w._flash=lambda msg, *a, **k: said.append(msg)
w._name_dialog=lambda *a,**k:"Chem"
w._meeting_dialog=lambda *a,**k:(0,0,"09:00","10:00","")
w._set_view("schedule"); w._add_meeting(); pump()
chk("a normal save says nothing", not said, said)

# now the disk is full
real=nbapp.atomic_write_json
def full(*a, **k):
    raise OSError(errno.ENOSPC, "No space left on device")
nbapp.atomic_write_json=full
said[:] = []
w._meeting_dialog=lambda *a,**k:(0,0,"11:00","12:00","")
w._add_meeting(); pump()
chk("a failed save warns the user", bool(said), said)
chk("...and names the disk being full",
    any("disk is full" in m.lower() for m in said), said)
n_after_first=len(said)
w._meeting_dialog=lambda *a,**k:(0,0,"14:00","15:00","")
w._add_meeting(); pump()
chk("it does not strobe on every later failure", len(said)==n_after_first, said)

# read-only filesystem reads differently
nbapp.atomic_write_json=lambda *a,**k: (_ for _ in ()).throw(OSError(errno.EROFS,"ro"))
w._save_warned=False; said[:]=[]
w._save_to_disk(); pump()
chk("a read-only disk gets its own wording",
    bool(said) and any("read-only" in m.lower() for m in said)
    and not any("disk is full" in m.lower() for m in said), said)

nbapp.atomic_write_json=real
w._save_warned=False; said[:]=[]
w._save_to_disk(); pump()
chk("recovery is silent again", not said, said)


# =====================================================================
#  2. every other app that holds irreplaceable work
# =====================================================================
DE = os.environ.get("DE") or os.path.dirname(os.path.abspath(academics.__file__))
SWEEP_HOME = "/tmp/nbhome-savefail"

# module, class, the save method to drive, and how the app tells the user.
#   flash:<name>  -- the app calls that method with a sentence
#   label:<attr>  -- the app holds the reason and its refresh puts it on a strip
CASES = [
    ("journal",     "Journal",     "_persist",     "flash:_flash",
     "journal.json",     "years of diary entries"),
    ("contacts",    "Contacts",    "_save",        "flash:_flash",
     "contacts.json",    "every person they know"),
    ("accounting",  "Accounting",  "_autosave",    "flash:_flash",
     "accounting.json",  "the ledger"),
    ("cookbook",    "Cookbook",    "_save_state",  "flash:_flash_status",
     "cookbook.json",    "their recipes"),
    ("calendar",    "Calendar",    "_save_events", "flash:_flash_status",
     "calendar.json",    "every appointment"),
    ("tasks",       "Tasks",       "_save_tasks",  "flash:_flash",
     "tasks-app.json",   "the task list"),
    ("workout",     "Workout",     "_save",        "label:status",
     "workout.json",     "the training log and streaks"),
    ("mealplanner", "MealPlanner", "_save",        "label:status",
     "mealplanner.json", "the week's meal plan"),
]

CHILD = r'''
import os, sys, json, errno
os.environ["NB_HOME"] = sys.argv[1]
sys.path.insert(0, sys.argv[2])
mod_name, cls_name, save_name, surface = sys.argv[3:7]
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import nbapp
win = getattr(__import__(mod_name), cls_name)()

seen = []
kind, attr = surface.split(":", 1)
if kind == "flash":
    setattr(win, attr, lambda text, *a, **k: seen.append(str(text)))

# The disk fills up between one edit and the next.
def full(*a, **k):
    raise OSError(errno.ENOSPC, "No space left on device")
nbapp.atomic_write_json = full

getattr(win, save_name)()
if kind == "label":
    # the app holds the reason; its own refresh is what puts it on screen
    try: win._refresh_status()
    except Exception: pass
    lbl = getattr(win, attr, None)
    if lbl is not None:
        try: seen.append(lbl.get_text())
        except Exception: pass
print("SURFACED:" + json.dumps(seen))
'''

print("\n--- 2. every other app that holds irreplaceable work -------------")
child = "/tmp/nb_savefail_child.py"
open(child, "w", encoding="utf-8").write(CHILD)
# The sentences nbapp.save_failure_reason produces. An app that says something
# else is not reporting the failure, it is reporting something else.
EXPECT = ("disk is full", "read-only", "no room left", "could not be saved")

for mod, cls, save, surface, store, what in CASES:
    shutil.rmtree(SWEEP_HOME, ignore_errors=True)
    cfg = os.path.join(SWEEP_HOME, ".config", "notebook")
    os.makedirs(cfg, exist_ok=True)
    json.dump({}, open(os.path.join(cfg, store), "w", encoding="utf-8"))
    env = dict(os.environ, PYTHONPATH=DE); env.pop("NB_HOME", None)
    name = "%s reports a failed save (%s)" % (mod, what)
    try:
        p = subprocess.run([sys.executable, child, SWEEP_HOME, DE,
                            mod, cls, save, surface],
                           capture_output=True, timeout=180, env=env)
    except subprocess.TimeoutExpired:
        chk(name, False, "timed out"); continue
    out = p.stdout.decode("utf-8", "replace")
    hit = [l for l in out.splitlines() if l.startswith("SURFACED:")]
    if not hit:
        chk(name, False, (p.stderr.decode("utf-8", "replace")[-200:] or out[-200:]))
        continue
    seen = json.loads(hit[0][len("SURFACED:"):])
    chk(name, any(e in s.lower() for s in seen for e in EXPECT),
        "surfaced %r" % (seen,))

shutil.rmtree(SWEEP_HOME, ignore_errors=True)
print("\n%d checks, %d passed, %d FAILED"
      % (len(R), sum(1 for x in R if x), sum(1 for x in R if not x)))
print("RESULT: " + ("ALL PASS" if all(R) else "SOME FAILED"))
sys.exit(0 if all(R) else 1)
