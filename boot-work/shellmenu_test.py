import sys
sys.path.insert(0, "/opt/notebook/de")
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import Gtk
import shell

def walk(w):
    yield w
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            yield from walk(c)

p = shell.Panel()
p.show_all()
while Gtk.events_pending():
    Gtk.main_iteration()

buttons = {}
for w in walk(p):
    if isinstance(w, Gtk.Button):
        ch = w.get_child()
        if isinstance(ch, Gtk.Label):
            buttons[ch.get_text()] = w

for name in ("File", "Edit", "View", "Go", "Special"):
    b = buttons.get(name)
    if not b:
        print("MENU %-8s -> BUTTON-NOT-FOUND" % name); continue
    p._menu = None
    try:
        b.emit("clicked")
        while Gtk.events_pending():
            Gtk.main_iteration()
        # count the menu items built into the open menu EventBox
        n = 0
        if p._menu is not None:
            for w in walk(p._menu):
                if isinstance(w, Gtk.Button):
                    n += 1
        print("MENU %-8s -> %s (%d items)" % (name, "OPENS" if p._menu is not None else "DEAD", n))
        p._menu_close()
    except Exception as e:
        print("MENU %-8s -> CRASH %r" % (name, e))
