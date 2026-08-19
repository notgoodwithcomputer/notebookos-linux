#!/usr/bin/env python3
"""Positional translations must not add malformed percent tokens."""
import i18n_placeholder_check as gate


assert gate.check("Hello %s", "Hola %s") == []
assert gate.check("Progress: %d%%", "Progreso: %d%%") == []
assert gate.check("Hello %s", "Hola %s %")
assert gate.check("Hello %s", "Hola %s %(")
assert gate.check("Hello %s", "Hola %q")
print("PASS malformed positional percent tokens cannot pass")
print("RESULT: PASS")
