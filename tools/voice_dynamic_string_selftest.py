#!/usr/bin/env python3
"""Visible dynamic prose remains inside the voice gate."""
import os
import tempfile
import voice_check as gate


def extracted(source):
    fd, path = tempfile.mkstemp(prefix="voice-dynamic-", suffix=".py")
    try:
        os.write(fd, source.encode("utf-8"))
        os.close(fd)
        return [text for _line, text in gate.strings_in(path)]
    finally:
        try: os.close(fd)
        except OSError: pass
        os.unlink(path)


for expression in ('f"Your file {name} is ready"',
                   '"Your file {} is ready".format(name)'):
    texts = extracted("def f(label, name):\n label.set_text(%s)\n" % expression)
    assert texts and gate.judge(texts[0]), (expression, texts)

neutral = extracted('def f(label, n):\n label.set_text(f"{n} files")\n')
assert neutral == ["X files"] and gate.judge(neutral[0]) == []
print("PASS f-strings and .format() prose remain visible to voice rules")
print("RESULT: PASS")
