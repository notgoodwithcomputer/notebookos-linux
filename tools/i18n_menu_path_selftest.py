#!/usr/bin/env python3
"""Menu-path checks compare against the translated canonical menu name."""
import importlib.util
import sys
from pathlib import Path
p = Path(__file__).with_name("i18n_check.py")
s = importlib.util.spec_from_file_location("i18n_check", p)
m = importlib.util.module_from_spec(s); sys.modules[s.name] = m; s.loader.exec_module(m)
key = "Use File ▸ Open to continue."
assert m.check_menu_paths({"fr": {"File": "Fichier", key:
                                  "Utilisez Fichier ▸ Ouvrir."}}) == 0
for wrong in ("File ▸ Ouvrir", "Dossier ▸ Ouvrir", "Classeur ▸ Ouvrir"):
    assert m.check_menu_paths({"fr": {"File": "Fichier", key: wrong}}) == 1
print("PASS menu paths require the catalog's canonical translated menu name")
print("RESULT: PASS")
