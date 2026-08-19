#!/usr/bin/env python3
"""Notification deletion failures remain visible and do not claim success."""
import ast
from pathlib import Path

p = (Path(__file__).resolve().parents[1] / "buildroot/board/notebookos/"
     "rootfs-overlay/opt/notebook/de/shell.py")
tree = ast.parse(p.read_text(encoding="utf-8"))
names = {"_notify_open", "_notify_dismiss", "_notify_clear"}
methods = [n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name in names]

class Notify:
    dismiss_result = False; gone = 0
    @classmethod
    def dismiss(cls, _nid): return cls.dismiss_result
    @classmethod
    def load(cls): return [{"id": "a"}, {"id": "b"}]
    @classmethod
    def clear_all(cls): return cls.gone

launches = []
ns = {"nbnotify": Notify, "_t": lambda text: text,
      "launch": launches.append}
exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
             str(p), "exec"), ns)

class App:
    _notify_open = ns["_notify_open"]
    _notify_dismiss = ns["_notify_dismiss"]
    _notify_clear = ns["_notify_clear"]
    def __init__(self): self._notify_error = ""; self.rebuilt = 0; self.closed = 0
    def _notify_rebuild(self): self.rebuilt += 1
    def _menu_close(self): self.closed += 1
    def _notify_opens(self, _rec): return "Writer"

app = App(); rec = {"id": "a", "title": "Done", "app": "writer"}
app._notify_dismiss(None, rec)
assert app.rebuilt == 1 and app._notify_error
app._notify_open(None, rec)
assert app.closed == 0 and launches == [] and app.rebuilt == 2
Notify.gone = 1; app._notify_clear(None)
assert app.rebuilt == 3 and app._notify_error
Notify.gone = 2; app._notify_clear(None)
assert app.rebuilt == 4 and app._notify_error == ""
print("PASS failed and partial notification removals stay visible")
print("RESULT: PASS")
