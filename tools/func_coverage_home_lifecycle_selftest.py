#!/usr/bin/env python3
"""Profiled suites release their isolated temporary app home."""
import importlib.util
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "func_coverage.py")
spec = importlib.util.spec_from_file_location("func_coverage", PATH)
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)

with tempfile.TemporaryDirectory(prefix="func-cov-lifecycle-") as td:
    suite = os.path.join(td, "suite.py")
    marker = os.path.join(td, "home.txt")
    with open(suite, "w", encoding="utf-8") as fh:
        fh.write("import os\n"
                 "open(os.environ['COVERAGE_MARKER'], 'w').write("
                 "os.environ['NB_HOME'])\n")
    saved = os.environ.get("NB_HOME")
    caller_home = os.path.join(td, "caller-owned")
    os.environ["NB_HOME"] = caller_home
    os.environ["COVERAGE_MARKER"] = marker
    try:
        seen, ok = coverage.run_under_profile(suite, os.path.join(td, "target.py"))
        scratch = open(marker, encoding="utf-8").read()
        assert seen == set() and ok
        assert scratch != caller_home and os.path.basename(scratch).startswith("nb-cov-")
        assert not os.path.exists(scratch)
        assert os.environ["NB_HOME"] == caller_home

        target = os.path.join(td, "target.py")
        failed_suite = os.path.join(td, "failed_suite.py")
        with open(failed_suite, "w", encoding="utf-8") as fh:
            fh.write("import os\n"
                     "exec(compile('def touched(): pass\\ntouched()\\n', "
                     "os.environ['COVERAGE_TARGET'], 'exec'))\n"
                     "raise SystemExit(3)\n")
        os.environ["COVERAGE_TARGET"] = target
        failed_seen, failed_ok = coverage.run_under_profile(failed_suite, target)
        assert failed_seen
        assert not failed_ok
    finally:
        os.environ.pop("COVERAGE_MARKER", None)
        os.environ.pop("COVERAGE_TARGET", None)
        if saved is None:
            os.environ.pop("NB_HOME", None)
        else:
            os.environ["NB_HOME"] = saved

print("FUNC COVERAGE HOME LIFECYCLE SELFTEST: 6 checks, all pass")
print("RESULT: ALL PASS")
