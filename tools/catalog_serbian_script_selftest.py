#!/usr/bin/env python3
"""A one-letter Serbian word cannot switch alphabets unnoticed."""
import importlib.util
import sys
from pathlib import Path

p = Path(__file__).with_name("catalog_script_check.py")
s = importlib.util.spec_from_file_location("catalog_script", p)
m = importlib.util.module_from_spec(s); sys.modules[s.name] = m; s.loader.exec_module(m)
assert "CYRILLIC" in m.scripts_in("У uređaju je disk.", "sr", minrun=1)
checked = "".join(" " if ch in m.LETTER_SYMBOLS else ch for ch in "π = 3.14")
assert not m.scripts_in(checked, "sr", minrun=1)
print("PASS Serbian one-letter words are checked while explicit symbols remain allowed")
print("RESULT: PASS")
