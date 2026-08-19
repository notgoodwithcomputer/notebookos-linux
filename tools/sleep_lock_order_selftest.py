#!/usr/bin/env python3
"""Sleep never blanks before a required lock surface has mapped."""

import importlib.util
from pathlib import Path
import sys
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
SHELL = DE / "shell.py"
LOGIN = DE / "login.py"

spec = importlib.util.spec_from_file_location("nb_login_order", LOGIN)
login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(login)


def main():
    shell_src = SHELL.read_text(encoding="utf-8")
    login_src = LOGIN.read_text(encoding="utf-8")
    assert "xset dpms force off" not in shell_src
    assert '"--lock", "--sleep"' in shell_src
    assert 'connect_after("map-event", blank_after_map)' in login_src
    print("PASS shell delegates blanking to the lock process")
    print("PASS protected sleep waits for the lock window map")

    calls = []
    result = mock.Mock(returncode=0)
    with mock.patch.object(login.subprocess, "run",
                           side_effect=lambda argv, **_kw:
                           (calls.append(argv), result)[1]):
        assert login._blank_for_sleep()
    assert calls == [["xset", "+dpms"],
                     ["xset", "dpms", "force", "off"]], calls
    print("PASS DPMS enable precedes the force-off request")
    # Terminal verdict the release runner recognises (run_all_gates SUCCESSWORD):
    # a descriptive line is not a report it will trust.
    print("sleep / lock ordering: PASS")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
