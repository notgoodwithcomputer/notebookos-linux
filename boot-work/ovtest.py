import sys, inspect
sys.path.insert(0, "/tmp/de_override"); sys.path.insert(0, "/opt/notebook/de")
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk
import g2048
G = [c for _n,c in inspect.getmembers(g2048, inspect.isclass) if c.__module__=="g2048" and issubclass(c,Gtk.Window)][0]
w = G()
while Gtk.events_pending(): Gtk.main_iteration()
# force a win and refresh
w.status = "win"; w.ov_text.set_text("You reached 2048!"); w._refresh()
while Gtk.events_pending(): Gtk.main_iteration()
# is the overlay content now visible?
print("ov_box visible:", w.ov_box.get_visible())
print("ov_text visible:", w.ov_text.get_visible(), "text:", repr(w.ov_text.get_text()))
