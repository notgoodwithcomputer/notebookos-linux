import sys, inspect
sys.path.insert(0,"/opt/notebook/de")
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk
def W(m):
    return [c for _n,c in inspect.getmembers(m,inspect.isclass) if c.__module__==m.__name__ and issubclass(c,Gtk.Window)][0]

# calculator 200! must not crash
import calculator
c=W(calculator)()
r=None
for meth in ("evaluate","_evaluate","_eval"):
    if hasattr(c,meth):
        try: r=getattr(c,meth)("200!")
        except Exception as e: r="CRASH:%r"%e
        break
print("calc 200! ->", (str(r)[:40]+"..." if r and len(str(r))>40 else r))

# terminal Shell/View menus non-empty
import terminal
t=W(terminal)()
for nm in ("Shell","View","Edit"):
    items=t.menu_items(nm)
    print("terminal menu %-6s -> %d items: %s" % (nm, len(items), [i[0] for i in items][:5]))

# video timeline labels valid MM:SS
import video
v=W(video)()
labs=getattr(v,"_tick_labels",None)
if labs:
    txts=[l.get_text() for l in labs]
    import re
    bad=[x for x in txts if not re.match(r"^\d\d:\d\d$",x) or int(x.split(":")[1])>=60]
    print("video ruler:", txts, "-> bad:", bad if bad else "none")

# g2048 win-latch present
import g2048
g=W(g2048)()
print("g2048 has _won_shown latch:", hasattr(g,"_won_shown"))
