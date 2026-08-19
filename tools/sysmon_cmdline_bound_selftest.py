#!/usr/bin/env python3
"""System Monitor polling must not ingest unbounded process arguments."""

import ast
import copy
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/sysmon.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
fn = copy.deepcopy(next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_read_cmdline"))
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
scope = {"MAX_CMDLINE_BYTES": 64 * 1024}
exec(compile(module, str(SOURCE), "exec"), scope)
read_cmdline = scope["_read_cmdline"]

with tempfile.NamedTemporaryFile() as fh:
    fh.write(b"python3\0/opt/notebook/de/writer.py\0" + b"x" * 1000)
    fh.flush()
    data = read_cmdline(fh.name, 48)
    assert len(data) == 48
    assert b"writer.py" in data
    assert data != Path(fh.name).read_bytes()

source = SOURCE.read_text(encoding="utf-8")
assert '_read_cmdline("/proc/%s/cmdline" % pid)' in source
assert ".read().split" not in ast.get_source_segment(
    source, next(node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "script_module"))
print("PASS process command-line naming reads only a bounded prefix")
print("RESULT: ALL PASS")
