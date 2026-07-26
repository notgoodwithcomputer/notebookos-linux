import os, sys, json, importlib, inspect, tempfile, shutil
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

DE = "/tmp/de_override"
sys.path.insert(0, DE)
sys.path.insert(0, "/opt/notebook/de")

def win_cls(mod):
    for _n, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            return c
    return None

results = []
def check(name, ok):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name)

for name in ["writer","novel","journal","academic","tasks"]:
    home = tempfile.mkdtemp(prefix="ptest_"+name+"_")
    os.environ["NB_HOME"] = home
    cfg = os.path.join(home, ".config", "notebook")
    datafile = os.path.join(cfg, name+".json" if name!="tasks" else "tasks.json")
    try:
        # fresh import each app with the temp HOME
        for m in [name]:
            if m in sys.modules: del sys.modules[m]
        mod = importlib.import_module(name)
        cls = win_cls(mod)
        check(name+"-has-window-class", cls is not None)
        if cls is None: continue
        w = cls()   # construct (catches crash-on-load)
        check(name+"-constructs", True)
        # trigger a final flush (destroy handler) to force a save
        try:
            w.emit("destroy")
        except Exception as e:
            print("   (destroy emit note:", e, ")")
        # a save should have produced the data file (seed or content)
        check(name+"-writes-datafile-on-destroy", os.path.exists(datafile))
        if os.path.exists(datafile):
            try:
                data = json.load(open(datafile))
                check(name+"-datafile-valid-json", data is not None)
            except Exception as e:
                check(name+"-datafile-valid-json", False)
        # construct a SECOND instance with same HOME -> must load without crashing
        for m in [name]:
            if m in sys.modules: del sys.modules[m]
        mod2 = importlib.import_module(name)
        w2 = win_cls(mod2)()
        check(name+"-reloads-without-crash", True)
    except Exception as e:
        import traceback; traceback.print_exc()
        check(name+"-no-exception", False)
    finally:
        shutil.rmtree(home, ignore_errors=True)

print("RESULT: " + ("ALL PASS" if all(results) else "SOME FAILED") + " (%d/%d)" % (sum(results), len(results)))
