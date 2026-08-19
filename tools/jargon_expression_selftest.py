#!/usr/bin/env python3
"""Visible constant expressions cannot split machinery terms past the gate."""
import os
import tempfile
import jargon_sweep as gate


fd, path = tempfile.mkstemp(prefix="jargon-expression-", suffix=".py")
try:
    os.write(fd, b'def f(label):\n label.set_text("frame" + "buffer unavailable")\n')
    os.close(fd)
    strings = [text for _line, text in gate.ui_strings(path)]
finally:
    try: os.close(fd)
    except OSError: pass
    os.unlink(path)

assert "framebuffer unavailable" in strings, strings
assert any("framebuffer" in words for words in gate.JARGON.values())
print("PASS visible concatenation is folded before jargon scanning")
print("RESULT: PASS")
