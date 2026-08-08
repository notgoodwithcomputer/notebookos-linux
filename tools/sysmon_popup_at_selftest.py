#!/usr/bin/env python3
"""Display-free pin for System Monitor's shared context-menu popup path."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ("buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/"
                 "sysmon.py")
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
handler = next(node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == "_on_tree_button")

calls = [node for node in ast.walk(handler) if isinstance(node, ast.Call)]
popup_calls = [call for call in calls
               if isinstance(call.func, ast.Attribute)
               and isinstance(call.func.value, ast.Name)
               and call.func.value.id == "nbapp"
               and call.func.attr == "popup_at"]
valid = (len(popup_calls) == 1
         and isinstance(popup_calls[0].args[0], ast.Name)
         and popup_calls[0].args[0].id == "menu"
         and [(kw.arg, getattr(kw.value, "id", None))
              for kw in popup_calls[0].keywords] == [("event", "event")])
legacy = {call.func.attr for call in calls
          if isinstance(call.func, ast.Attribute)
          and call.func.attr in {"popup", "popup_at_pointer"}}

if valid and not legacy:
    print("PASS sysmon context menu routes through nbapp.popup_at")
    print("RESULT: ALL PASS")
else:
    print("FAIL sysmon context menu routes through nbapp.popup_at")
    raise SystemExit(1)
