"""A second copy of an app must stand down instead of overwriting the first."""
import os, sys, shutil, json, subprocess, time
H="/tmp/nbhome-single"; os.environ["NB_HOME"]=H
D=os.environ.get("DE") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
STORE=H+"/.config/notebook/academics.json"
R=[]
def chk(n, ok, d=""):
    R.append(ok); print("%s %s%s" % ("PASS" if ok else "FAIL", n, "" if ok else "  <- %s"%(d,)))

shutil.rmtree(H, ignore_errors=True); os.makedirs(H+"/.config/notebook", exist_ok=True)
# Clear the marker directory THIS RUN will actually use. nbapp scopes it by
# NB_HOME (_app_scope hashes anything that is not the guest's /root), so the
# literal "/tmp/nb-apps" cleaned here before is the GUEST's directory and never
# this test's -- the run started against whatever a previous run left behind. A
# stale marker naming a pid the OS has since reused would make instance A stand
# down and fail this test for a reason that has nothing to do with the code.
sys.path.insert(0, D)
import gi; gi.require_version("Gtk", "3.0")
import nbapp
shutil.rmtree(nbapp.APP_DIR, ignore_errors=True)

# instance A: holds the app open and adds three classes
a_src = '''
import os, sys, time
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk, GLib
import academics
w=academics.Academics()
w._name_dialog=lambda *a,**k:"Chem"
w._meeting_dialog=lambda *a,**k:(0,0,"09:00","10:00","")
w._set_view("schedule"); w._add_meeting()
for i in (2,3):
    w._class_dialog=lambda *a,_i=i,**k:{"label":"Class%d"%_i,"color":"#4A5E73","room":"","instructor":""}
    w._new_class_only()
sys.stderr.write("A_READY\\n"); sys.stderr.flush()
GLib.timeout_add(12000, Gtk.main_quit)
Gtk.main()
'''
open("/tmp/inst_a.py","w").write(a_src)
env=dict(os.environ, PYTHONPATH=D)
A=subprocess.Popen([sys.executable,"/tmp/inst_a.py"], env=env, stderr=subprocess.PIPE)
# wait for A to be up and to have written its three classes
for _ in range(200):
    line=A.stderr.readline()
    if b"A_READY" in line: break
time.sleep(1.0)
on_disk=[c["label"] for c in json.load(open(STORE))["classes"]]
chk("the open app saved all three classes", len(on_disk)==3, on_disk)

# instance B: exactly what clicking the desktop tile does, while A is open
b_src = '''
import os, sys
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk
import academics
w=academics.Academics()          # should never be reached
w._on_destroy()
sys.stderr.write("B_SAVED\\n")
'''
open("/tmp/inst_b.py","w").write(b_src)
B=subprocess.run([sys.executable,"/tmp/inst_b.py"], env=env,
                 capture_output=True, timeout=90)
chk("the second copy stood down", b"B_SAVED" not in B.stderr, B.stderr[-120:])
after=[c["label"] for c in json.load(open(STORE))["classes"]]
chk("the first app's work is intact", after==on_disk, after)
A.terminate(); A.wait(timeout=20)
final=[c["label"] for c in json.load(open(STORE))["classes"]]
chk("...and still intact after the real app closes", len(final)==3, final)
print("\nRESULT: " + ("ALL PASS" if all(R) else "SOME FAILED"))
sys.exit(0 if all(R) else 1)
