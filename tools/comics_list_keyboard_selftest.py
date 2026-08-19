#!/usr/bin/env python3
"""Keyboard activation switches Comics pages/layers like a pointer click."""
import os
import sys
from types import SimpleNamespace

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import comics  # noqa: E402


def main():
    app = comics.Comics.__new__(comics.Comics)
    calls = []
    app._switch_page = lambda i: calls.append(("page", i))
    app._select_layer = lambda i: calls.append(("layer", i))
    app._on_page_activated(None, SimpleNamespace(_page_index=1))
    app._on_layer_activated(None, SimpleNamespace(_layer_index=2))
    app._on_layer_activated(None, SimpleNamespace())  # disabled hint row
    assert calls == [("page", 1), ("layer", 2)], calls
    print("PASS row activation switches the indexed page and reversed layer")
    print("PASS activating the non-layer hint is a no-op")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
