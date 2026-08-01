"""OEM install -> first-run setup, end to end, against a fake target root."""
import os, sys, shutil, json, inspect
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk
ROOT="/tmp/oemroot"; H="/tmp/nbhome-oem"
for d in (ROOT,H): shutil.rmtree(d, ignore_errors=True)
os.makedirs(ROOT+"/etc", exist_ok=True); os.makedirs(H+"/.config/notebook", exist_ok=True)
open(ROOT+"/etc/shadow","w").write("root:*:::::::\ndaemon:*:::::::\n")
open(ROOT+"/etc/inittab","w").write("::sysinit:/etc/init.d/rcS\n")
os.environ["NB_HOME"]=H
import installer, firstrun
R=[]
def chk(n, ok, d=""):
    R.append(ok); print("%s %s%s" % ("PASS" if ok else "FAIL", n, "" if ok else "  <- %s"%(d,)))

cls=[c for _n,c in inspect.getmembers(installer, inspect.isclass)
     if c.__module__=="installer" and issubclass(c, Gtk.Window)][0]
w=cls()
n=0
while Gtk.events_pending() and n<400: Gtk.main_iteration_do(False); n+=1

# --- OEM install ---
w.cfg["oem"]=True; w.cfg["hostname"]="notebook"; w.cfg["password"]="unused"
w._post_log=lambda *a, **k: None
w._configure_target(ROOT)
marker=os.path.join(ROOT, w.OEM_MARKER)
chk("the installer leaves a first-run marker", os.path.isfile(marker))
sh=open(ROOT+"/etc/shadow").read()
chk("root is left LOCKED, no password invented",
    sh.splitlines()[0].split(":")[1]=="*", sh.splitlines()[0])
chk("no keyboard file forced on the new owner",
    not os.path.exists(ROOT+"/etc/X11/xorg.conf.d/00-keyboard.conf"))

# --- non-OEM must still behave ---
shutil.rmtree(ROOT+"/var", ignore_errors=True)
w.cfg["oem"]=False; w.cfg["root_passwordless"]=True
w._configure_target(ROOT)
chk("a normal install leaves NO marker",
    not os.path.isfile(marker))

# --- first-run setup applies the answers ---
firstrun.OEM_MARKER=marker; firstrun.SHADOW=ROOT+"/etc/shadow"
firstrun.HOSTNAME_FILE=ROOT+"/etc/hostname"
firstrun.XKB_CONF=ROOT+"/etc/X11/xorg.conf.d/00-keyboard.conf"
os.makedirs(os.path.dirname(marker), exist_ok=True); open(marker,"w").write("x")
chk("setup is pending before it runs", firstrun.pending())
failed=firstrun.apply({"hostname":"benbook","lang":"fr","kbd":"fr","password":"nb1234"})
chk("every answer applied", not failed, failed)
chk("the name was written", open(ROOT+"/etc/hostname").read().strip()=="benbook")
chk("the keyboard was written",
    'XkbLayout" "fr"' in open(firstrun.XKB_CONF).read())
# ASKED OF nbi18n, NOT OF THE FILE. This check used to read the key firstrun
# had just written and confirm it was there -- a writer graded against itself.
# firstrun wrote "language" while nbi18n.current_lang() reads "lang", so the
# check was green while every machine set up for somebody else came up in
# English whatever its new owner chose. The only question worth asking is
# whether the code that reads this file on every later boot agrees.
import nbi18n
loc=json.load(open(H+"/.config/notebook/locale.json"))
chk("the language chosen is the one the desktop will actually start in",
    nbi18n.current_lang()=="fr", (nbi18n.current_lang(), loc))
chk("the keyboard chosen is the one the session will actually apply",
    nbi18n.keyboard()=="fr", (nbi18n.keyboard(), loc))
root_hash=open(ROOT+"/etc/shadow").read().splitlines()[0].split(":")[1]
chk("a real password hash was set", root_hash.startswith("$6$"), root_hash[:12])
try:
    import crypt
    chk("...and the chosen password verifies",
        crypt.crypt("nb1234", root_hash)==root_hash)
except ImportError:
    print("SKIP crypt not on this host (guest ships it)")
chk("the marker is gone, so it never asks again", not firstrun.pending())
print("\nRESULT: " + ("ALL PASS" if all(R) else "SOME FAILED"))
sys.exit(0 if all(R) else 1)
