import sys
sys.path.insert(0, "/opt/notebook/de")
import gi; gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

def check(mod, win_status, want_text):
    if mod in sys.modules:
        del sys.modules[mod]
    m = __import__(mod)
    # find the Gtk.Window subclass
    import inspect
    cls = None
    for _n, c in inspect.getmembers(m, inspect.isclass):
        if c.__module__ == m.__name__ and issubclass(c, Gtk.Window):
            cls = c; break
    w = cls()
    while Gtk.events_pending(): Gtk.main_iteration()
    w.status = win_status
    w._refresh()
    while Gtk.events_pending(): Gtk.main_iteration()
    box_vis = w.ov_box.get_visible()
    txt_vis = w.ov_text.get_visible()
    txt = w.ov_text.get_text()
    ok = box_vis and txt_vis and (want_text in txt)
    print("%-8s status=%-5s ov_box.visible=%s ov_text.visible=%s text=%r -> %s"
          % (mod, win_status, box_vis, txt_vis, txt, "PASS" if ok else "FAIL"))
    try: w.destroy()
    except Exception: pass
    return ok

a = check("g2048", "win", "2048")
b = check("tetris", "over", "Game")   # "Game Over" text + visibility flags
print("OVERLAY: %s" % ("ALL PASS" if (a and b) else "FAIL"))
