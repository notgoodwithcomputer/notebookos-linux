import sys, inspect, time
sys.path.insert(0, "/opt/notebook/de")
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk
import calculator
C = [cl for _n,cl in inspect.getmembers(calculator, inspect.isclass)
     if cl.__module__=="calculator" and issubclass(cl, Gtk.Window)][0]
c = C()
for e in ["9^9^9", "9999999!", "2^10", "200!", "2π"]:
    c.expr = e
    t = time.time(); r = c.evaluate(); dt = time.time()-t
    print("calc %-10s -> %-16s (%.3fs)" % (e, str(r)[:16], dt))
