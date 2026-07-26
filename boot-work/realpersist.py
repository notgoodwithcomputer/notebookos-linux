import os, sys, json, inspect
os.environ["NB_HOME"] = "/root"
sys.path.insert(0, "/opt/notebook/de")
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk
import writer
cls=[c for _n,c in inspect.getmembers(writer,inspect.isclass) if c.__module__=="writer" and issubclass(c,Gtk.Window)][0]
w=cls()
MARK="REBOOT-PROOF-MARKER-42 the quick brown fox"
# set the body buffer text to the marker (the app's real editable body)
w.body.get_buffer().set_text(MARK)
w._recount(w.body.get_buffer())     # trigger the change path
# force the debounced/destroy save
w.emit("destroy")
f="/root/.config/notebook/writer.json"
ok = os.path.exists(f)
print("writer.json exists:", ok)
if ok:
    d=json.load(open(f))
    print("body contains marker:", MARK in json.dumps(d))
    print("keys:", list(d.keys()))
