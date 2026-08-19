#!/usr/bin/env python3
"""Headless regression for closing GBA Emulator during a game."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import gbaemu  # noqa: E402


def bare(active, confirmed):
    app = gbaemu.GbaEmu.__new__(gbaemu.GbaEmu)
    app._session = object() if active else None
    app._confirm_stop_game = lambda: confirmed
    app.destroyed = False
    app.destroy = lambda: setattr(app, "destroyed", True)
    return app


app = bare(True, False)
app.close()
assert app.destroyed is False
print("PASS cancelling close leaves the active game running")

app = bare(True, True)
app.close()
assert app.destroyed is True
print("PASS confirmed close proceeds to normal session teardown")

app = bare(False, False)
app.close()
assert app.destroyed is True
print("PASS closing an idle emulator remains immediate")

app = bare(True, False)
assert app._on_delete() is True and app.destroyed is False
print("PASS window-manager close is vetoed while active warning is declined")

app = bare(True, True)
assert app._on_delete() is False
print("PASS confirmed window-manager close may proceed to teardown")
print("RESULT: PASS")
