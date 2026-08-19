#!/usr/bin/env python3
"""GTK positional label text remains in catalog coverage."""
import os
import tempfile
import i18n_coverage_check as gate


fd, path = tempfile.mkstemp(prefix="i18n-positional-", suffix=".py")
try:
    os.write(fd, b'''def f(Gtk):
 Gtk.Label("Untranslated visible sentence")
 Gtk.Button("Untranslated visible action")
 Gtk.Box(False, 4)
''')
    os.close(fd)
    shown = gate.shown_strings(path)
finally:
    try: os.close(fd)
    except OSError: pass
    os.unlink(path)

assert "Untranslated visible sentence" in shown
assert "Untranslated visible action" in shown
assert all("False" not in text for text in shown)
print("PASS positional GTK label and button text is covered")
print("RESULT: PASS")
