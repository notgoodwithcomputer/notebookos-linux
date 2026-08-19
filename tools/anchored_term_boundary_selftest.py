#!/usr/bin/env python3
"""Aggressive anchor stems match word starts, not unrelated substrings."""
import anchored_term_check as gate


stem = gate.stems("Carpeta", "es")
assert stem == ["carpe"]
assert gate.has_stems("La carpeta está vacía", stem, "es")
assert gate.has_stems("Las carpetas están vacías", stem, "es")
assert not gate.has_stems("La carpintería está vacía", stem, "es")
assert gate.has_stems("文件夹为空", gate.stems("文件夹", "zh"), "zh")
print("PASS anchored stems require a translated word boundary")
print("RESULT: PASS")
