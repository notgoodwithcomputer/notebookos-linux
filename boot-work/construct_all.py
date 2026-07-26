import sys, importlib, inspect, traceback
sys.path.insert(0, "/opt/notebook/de")
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk

APPS = ["writer","novel","journal","academic","screenplay","ebook","cookbook",
        "contacts","messages","accounting","calendar","music","illustrator",
        "sequencer","video","media","g2048","tetris","packages","settings",
        "sysmon","calculator","terminal","tasks","installer"]
ok=0; fail=0
for name in APPS:
    try:
        if name in sys.modules: del sys.modules[name]
        m = importlib.import_module(name)
        cls = None
        for _n,c in inspect.getmembers(m, inspect.isclass):
            if c.__module__==m.__name__ and issubclass(c, Gtk.Window):
                cls=c; break
        if cls is None:
            print("NOCLASS %s"%name); continue
        w = cls()
        while Gtk.events_pending(): Gtk.main_iteration()
        try: w.destroy()
        except Exception: pass
        ok+=1
    except Exception as e:
        fail+=1
        print("CRASH   %-12s %s: %s" % (name, type(e).__name__, str(e)[:80]))
print("CONSTRUCT: %d ok, %d crashed" % (ok, fail))
