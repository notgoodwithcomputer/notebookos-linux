#!/usr/bin/env python3
"""Parallel construction probes own isolated temporary app homes."""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "construct_one.py")
spec = importlib.util.spec_from_file_location("construct_one", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

saved = os.environ.pop("NB_HOME", None)
try:
    first = module._prepare_home()
    assert first and os.path.isdir(first)
    os.environ.pop("NB_HOME")
    second = module._prepare_home()
    assert second and os.path.isdir(second) and second != first
    module._cleanup_home(first)
    assert not os.path.exists(first)
    module._cleanup_home(second)
    assert not os.path.exists(second)

    caller = os.path.join(ROOT, ".caller-owned-home")
    os.environ["NB_HOME"] = caller
    assert module._prepare_home() is None
    assert os.environ["NB_HOME"] == caller
finally:
    os.environ.pop("NB_HOME", None)
    if saved is not None:
        os.environ["NB_HOME"] = saved

assert module.main([]) == 2
print("CONSTRUCT ONE CONCURRENCY SELFTEST: 7 checks, all pass")
print("RESULT: ALL PASS")
