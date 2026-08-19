#!/usr/bin/env python3
"""Display-free truthfulness contract for the print submission boundary."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import nbjobs  # noqa: E402
import nbprint  # noqa: E402


class Job:
    def checkpoint(self):
        return None

    def progress(self, *_args):
        return None


with tempfile.TemporaryDirectory(prefix="nb-print-commit-") as root:
    pdf = os.path.join(root, "job.pdf")
    Path(pdf).write_bytes(b"%PDF")
    real_make, real_submit = nbprint.make_print_file, nbprint.submit_pdf
    calls = []
    nbprint.make_print_file = lambda _make: pdf
    nbprint.submit_pdf = lambda *_args, **_kw: (calls.append("submit") or
                                                (True, "queued"))
    try:
        def cancelled(_job):
            calls.append("commit")
            raise nbjobs.Cancelled()

        try:
            nbprint._print_worker(Job(), None, "printer", 1, {}, "job",
                                  cancelled)
        except nbjobs.Cancelled:
            pass
        assert calls == ["commit"]
        print("PASS cancellation winning the boundary prevents submission")

        Path(pdf).write_bytes(b"%PDF")
        calls.clear()
        nbprint._print_worker(
            Job(), None, "printer", 1, {}, "job",
            lambda _job: calls.append("commit"))
        assert calls == ["commit", "submit"]
        print("PASS Cancel is retired before the irreversible submit call")
    finally:
        nbprint.make_print_file, nbprint.submit_pdf = real_make, real_submit

print("RESULT: ALL PASS")
