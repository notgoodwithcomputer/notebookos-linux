#!/usr/bin/env python3
"""The constructor does not derive CPU load from a millisecond interval."""
import ast
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "buildroot/board/notebookos/"
       "rootfs-overlay/opt/notebook/de/sysmon.py")


def main():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "SystemMonitor")
    init = next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    calls = [n for n in ast.walk(init) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name)
             and n.func.value.id == "self" and n.func.attr == "refresh"]
    assert len(calls) == 1, len(calls)
    kws = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kws.get("manual"), ast.Constant)
    assert kws["manual"].value is True
    assert isinstance(kws.get("seed_cpu"), ast.Constant)
    assert kws["seed_cpu"].value is True
    print("PASS initial refresh populates rows without a short CPU sample")
    print("PASS initial refresh seeds per-program CPU for the first timer tick")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
