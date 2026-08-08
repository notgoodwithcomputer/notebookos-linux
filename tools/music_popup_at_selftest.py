#!/usr/bin/env python3
"""Display-free pin for Music's shared per-row popup path."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ("buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/"
                 "music.py")
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
handler = next(node for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == "_on_add_clicked")

calls = [node for node in ast.walk(handler) if isinstance(node, ast.Call)]
popup_calls = [call for call in calls
               if isinstance(call.func, ast.Attribute)
               and isinstance(call.func.value, ast.Name)
               and call.func.value.id == "nbapp"
               and call.func.attr == "popup_at"]
keywords = popup_calls[0].keywords if len(popup_calls) == 1 else []
valid = (len(popup_calls) == 1
         and isinstance(popup_calls[0].args[0], ast.Name)
         and popup_calls[0].args[0].id == "menu"
         and len(keywords) == 2
         and keywords[0].arg == "widget"
         and isinstance(keywords[0].value, ast.Name)
         and keywords[0].value.id == "button"
         and keywords[1].arg == "anchor"
         and isinstance(keywords[1].value, ast.Constant)
         and keywords[1].value.value == "widget-sw")
legacy = {call.func.attr for call in calls
          if isinstance(call.func, ast.Attribute)
          and call.func.attr in {"popup", "popup_at_widget"}}

if valid and not legacy:
    print("PASS music row menu routes through nbapp.popup_at")
    print("RESULT: ALL PASS")
else:
    print("FAIL music row menu routes through nbapp.popup_at")
    raise SystemExit(1)
