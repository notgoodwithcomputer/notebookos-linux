#!/usr/bin/env python3
"""Explicit phonetic dictionaries never reuse another source's cache."""
import importlib.util
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "phonetic_en.py")
spec = importlib.util.spec_from_file_location("phonetic_en", PATH)
phonetic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(phonetic)

with tempfile.TemporaryDirectory(prefix="phonetic-cache-") as td:
    first_path = os.path.join(td, "first.dict")
    second_path = os.path.join(td, "second.dict")
    with open(first_path, "w", encoding="utf-8") as fh:
        fh.write("WORD W ER D\n")
    with open(second_path, "w", encoding="utf-8") as fh:
        fh.write("WORD W AO R D\nOTHER AH DH ER\n")

    first = phonetic.load_dict(first_path)
    assert first["word"] == ["W", "ER", "D"]
    assert "other" not in first

    second = phonetic.load_dict(second_path)
    assert second["word"] == ["W", "AO", "R", "D"]
    assert second["other"] == ["AH", "DH", "ER"]
    assert second is not first
    assert phonetic._DICT_PATH == os.path.realpath(second_path)
    assert phonetic.load_dict(second_path) is second

print("PHONETIC DICTIONARY CACHE SELFTEST: 7 checks, all pass")
print("RESULT: ALL PASS")
