#!/usr/bin/env python3
"""Stage image decoding must never run synchronously in Media._display."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/media.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "MediaViewer")
fn = next(n for n in cls.body
          if isinstance(n, ast.FunctionDef) and n.name == "_display")

calls = [n.func for n in ast.walk(fn) if isinstance(n, ast.Call)]
sync = [f for f in calls if isinstance(f, ast.Name)
        and f.id in ("_bounded_pixbuf", "_pixbuf_any")]
checks = [(not sync,
           "visible image display performs no synchronous stage decode")]

background = [f for f in calls if isinstance(f, ast.Attribute)
              and isinstance(f.value, ast.Name) and f.value.id == "self"
              and f.attr == "_decode_in_background"]
checks.append((len(background) == 1,
               "every stage image routes through the replaceable decode worker"))

worker = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
              and n.name == "_decode_in_background")
policies = [n for n in ast.walk(worker) if isinstance(n, ast.keyword)
            and n.arg == "policy"]
checks.append((bool(policies) and isinstance(policies[0].value, ast.Attribute)
               and policies[0].value.attr == "REPLACE",
               "rapid browsing replaces stale decode work instead of queueing it"))

for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
passed = sum(bool(ok) for ok, _name in checks)
print("RESULT: 3 checks, ALL PASS (3/3)" if passed == len(checks) else
      "RESULT: FAILED (%d/%d checks passed)" % (passed, len(checks)))
raise SystemExit(passed != len(checks))
