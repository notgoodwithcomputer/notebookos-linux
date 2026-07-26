import sys, inspect
sys.path.insert(0,"/opt/notebook/de")
import gi; gi.require_version("Gtk","3.0")
from gi.repository import Gtk, Gdk
def W(m): return [c for _n,c in inspect.getmembers(m,inspect.isclass) if c.__module__==m.__name__ and issubclass(c,Gtk.Window)][0]

# 1. finder folders-first in DESCENDING sort
import finder
f=W(finder)("Applications")
st=f.store
# clear + add: a folder then a file
st.clear()
from gi.repository import GObject
# columns: icon,name,size,date,rel,is_dir,size_bytes,mtime,kind
st.append([None,"zzz_file.txt","1 B","x","zzz",False,1,0.0,"Text"])
st.append([None,"aaa_folder","—","x","aaa",True,0,0.0,"Folder"])
st.append([None,"mmm_file.txt","1 B","x","mmm",False,1,0.0,"Text"])
st.set_sort_column_id(1, Gtk.SortType.DESCENDING)
order=[st[i][1] for i in range(len(st))]
folder_first = order[0]=="aaa_folder"
print("finder DESC sort order:", order, "-> folder first:", folder_first)

# 2. calculator implicit multiplication
import calculator
c=W(calculator)()
# find how to set expression: look for self.expr
def calc(expr):
    c.expr=expr
    r=c.evaluate()
    return r
for e in ["2π","2(3)","(1+1)(2)"]:
    try: print("calc %-8s -> %s"%(e, calc(e)))
    except Exception as ex: print("calc %-8s -> ERR %r"%(e,ex))

# 3. sysmon numeric sort column present (store has >=6 cols, CPU sorts numeric)
import sysmon
s=W(sysmon)()
print("sysmon store n_columns:", s.store.get_n_columns())
