#!/usr/bin/env python3
"""Bill Tracker PDF export must publish atomically."""

import ast
import copy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/bills.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "Bills")
fn = copy.deepcopy(next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                        and n.name == "_write_export_pdf"))
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)


class OS:
    class path:
        @staticmethod
        def join(a, b): return a + "/" + b
    @staticmethod
    def makedirs(*_a, **_kw): pass


class NB:
    fail = False
    calls = []
    @classmethod
    def atomic_write_via(cls, path, render):
        cls.calls.append(path)
        render(path + ".draft")
        if cls.fail:
            raise OSError("disk full")
    @staticmethod
    def save_failure_reason(exc, path): return "NOT SAVED " + path


scope = {"os": OS, "nbapp": NB, "DOCS_DIR": "/docs", "PDF_NAME": "Bills.pdf",
         "_t": lambda s: s}
exec(compile(module, str(SOURCE), "exec"), scope)


class Probe:
    _write_export_pdf = scope["_write_export_pdf"]
    def __init__(self): self.rendered = []; self.flashes = []
    def _render_pdf(self, path): self.rendered.append(path)
    def _flash(self, text): self.flashes.append(text)


NB.fail = False; NB.calls = []
saved = Probe(); saved._write_export_pdf()
checks = [(NB.calls == ["/docs/Bills.pdf"]
           and saved.rendered == ["/docs/Bills.pdf.draft"]
           and saved.flashes == ["Saved to Documents as Bills.pdf"],
           "Bills report renders to an atomic draft before publication")]

NB.fail = True; NB.calls = []
failed = Probe(); failed._write_export_pdf()
checks.append((failed.rendered == ["/docs/Bills.pdf.draft"]
               and failed.flashes == ["NOT SAVED /docs/Bills.pdf"],
               "failed Bills report publication reports failure without success claim"))
for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
passed = sum(bool(ok) for ok, _name in checks)
print("RESULT: 2 checks, ALL PASS (2/2)" if passed == len(checks) else
      "RESULT: FAILED (%d/%d checks passed)" % (passed, len(checks)))
raise SystemExit(passed != len(checks))
