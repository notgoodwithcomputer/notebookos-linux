import os, sys, shutil, inspect
# Per-PROCESS home: a shared NB_HOME also shares nbapp's single-instance marker
# dir, and the copy that loses that race is os._exit(0)ed with no output and
# exit status 0 -- a silent false pass.
H="/tmp/nbhome-mediafs-%d" % os.getpid(); os.environ["NB_HOME"]=H
shutil.rmtree(H, ignore_errors=True); os.makedirs(H+"/.config/notebook", exist_ok=True)
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk
import media
cls=[c for _n,c in inspect.getmembers(media, inspect.isclass)
     if c.__module__=="media" and issubclass(c, Gtk.Window)][0]
w=cls()
def pump(n=300):
    i=0
    while Gtk.events_pending() and i<n: Gtk.main_iteration_do(False); i+=1
pump()
R=[]
def chk(n, ok, d=""):
    R.append(ok); print("%s %s%s" % ("PASS" if ok else "FAIL", n, "" if ok else "  <- %s"%(d,)))
chk("transport handle exists", hasattr(w,"_vctl"))
w._video.show(); w._stage_full=False
w._enter_stage_fullscreen(); pump()
chk("entering fullscreen hides the transport", not w._vctl.get_visible(), "visible")
w._on_fs_motion(w, None); pump()
chk("mouse movement brings it back", w._vctl.get_visible())
w._fs_conceal(); pump()
chk("it hides again on its own", not w._vctl.get_visible())
w._exit_stage_fullscreen(); pump()
chk("leaving fullscreen restores it", w._vctl.get_visible())
chk("no stray timer left running", getattr(w,"_vctl_hide_timer",None) is None)
print("\nRESULT: " + ("ALL PASS" if all(R) else "SOME FAILED"))
sys.exit(0 if all(R) else 1)
