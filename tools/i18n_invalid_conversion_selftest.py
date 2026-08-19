#!/usr/bin/env python3
"""Matching unsupported percent conversions cannot certify each other."""
import importlib.util
import sys
from pathlib import Path
p = Path(__file__).with_name("i18n_placeholder_check.py")
s = importlib.util.spec_from_file_location("placeholder", p)
m = importlib.util.module_from_spec(s); sys.modules[s.name] = m; s.loader.exec_module(m)
for key, value in (("Value %q", "Valor %q"),
                   ("Value %D", "Valor %D"),
                   ("Value %(n)q", "Valor %(n)q")):
    assert any("unsupported" in reason for reason in m.check(key, value))
assert not m.check("Value %r %a %c", "Valor %r %a %c")
print("PASS unsupported percent conversions fail even when catalogs match")
print("RESULT: PASS")
