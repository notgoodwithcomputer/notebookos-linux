#!/usr/bin/env python3
"""Concurrent mutation sweeps never share their mutable module workspace."""
import importlib.util
import os
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "mutation_sweep.py")

with tempfile.TemporaryDirectory(prefix="mutation-workspace-") as td:
    caller = os.path.join(td, "caller-owned")
    saved = os.environ.get("SWEEP_WORK")
    os.environ["SWEEP_WORK"] = caller
    try:
        spec = importlib.util.spec_from_file_location("mutation_sweep", PATH)
        sweep = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sweep)
    finally:
        if saved is None:
            os.environ.pop("SWEEP_WORK", None)
        else:
            os.environ["SWEEP_WORK"] = saved

    assert sweep.WORK == caller and not sweep._OWN_WORK
    assert os.path.isdir(caller)

    first, own_first = sweep._make_work({})
    second, own_second = sweep._make_work({})
    try:
        assert own_first and own_second
        assert first != second
        assert os.path.isdir(first) and os.path.isdir(second)
    finally:
        shutil.rmtree(first, ignore_errors=True)
        shutil.rmtree(second, ignore_errors=True)

    chosen, owned = sweep._make_work({"SWEEP_WORK": caller})
    assert chosen == caller and not owned

print("MUTATION SWEEP WORKSPACE SELFTEST: 6 checks, all pass")
print("RESULT: ALL PASS")
