#!/usr/bin/env python3
"""A failed live keyboard switch must block password mutation."""
import importlib.util
import sys
from pathlib import Path
from unittest import mock

p = (Path(__file__).resolve().parents[1] / "buildroot/board/notebookos/"
     "rootfs-overlay/opt/notebook/de/firstrun.py")
s = importlib.util.spec_from_file_location("firstrun_test", p)
m = importlib.util.module_from_spec(s); sys.modules[s.name] = m; s.loader.exec_module(m)
answers = {"username": "", "hostname": "notebook", "kbd": "fr",
           "lang": "fr", "password": "secret"}
with mock.patch.object(m, "write_hostname", return_value=True), \
     mock.patch.object(m, "write_keyboard", return_value=False), \
     mock.patch.object(m, "write_locale", return_value=True), \
     mock.patch.object(m, "set_root_password") as set_password, \
     mock.patch.object(m, "clear_marker") as clear_marker:
    failed = m.apply(answers)
assert "keyboard" in failed
set_password.assert_not_called()
clear_marker.assert_not_called()
print("PASS failed live keyboard setup cannot commit a password or finish")
print("RESULT: PASS")
