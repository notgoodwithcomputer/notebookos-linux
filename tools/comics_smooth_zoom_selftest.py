#!/usr/bin/env python3
"""Comics smooth zoom follows vertical gesture direction only."""
import ast
from pathlib import Path
p = (Path(__file__).resolve().parents[1] / "buildroot/board/notebookos/"
     "rootfs-overlay/opt/notebook/de/comics.py")
method = next(n for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
              if isinstance(n, ast.FunctionDef) and n.name == "_on_scroll")
text = ast.unparse(method)
assert "ScrollDirection.SMOOTH" in text and "dy < 0" in text
assert "if not ok or not dy" in text and "self._step_zoom(step)" in text
print("PASS Comics smooth vertical direction controls Ctrl+zoom")
print("RESULT: PASS")
