#!/usr/bin/env python3
"""Failed recovery metadata cannot turn a successful script Save into loss."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import screenplay  # noqa: E402


def main():
    app = screenplay.Screenplay.__new__(screenplay.Screenplay)
    app._path = "/tmp/saved.fountain"
    app._file_dirty = False
    app._recovery_dirty = True
    app._save_error = OSError("recovery full")
    app._save_doc = lambda: False
    app._confirm = lambda *_a: (_ for _ in ()).throw(
        AssertionError("saved writing was presented as destructive loss"))
    assert app._on_delete() is False
    app._file_dirty = True
    app._confirm = lambda *_a: False
    assert app._on_delete() is True
    print("PASS bound clean script closes despite auxiliary recovery failure")
    print("PASS genuinely dirty script retains the close-loss guard")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
