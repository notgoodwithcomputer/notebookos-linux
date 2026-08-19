#!/usr/bin/env python3
"""Mutation checks for cursor alias containment."""

from pathlib import Path
import tempfile

import cursor_contract_selftest as contract


def rejected(root: Path, name: str) -> bool:
    try:
        contract.resolved(name, root)
    except (AssertionError, FileNotFoundError, RuntimeError):
        return True
    return False


def main() -> None:
    source = contract.resolved("watch")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        root = base / "cursors"
        root.mkdir()
        (root / "watch").write_bytes(source.read_bytes())
        (root / "internal").symlink_to("watch")
        assert contract.resolved("internal", root) == root / "watch"

        outside = base / "outside"
        outside.write_bytes(source.read_bytes())
        (root / "absolute").symlink_to(outside)
        (root / "traversal").symlink_to("../outside")
        (root / "broken").symlink_to("missing")
        assert rejected(root, "absolute")
        assert rejected(root, "traversal")
        assert rejected(root, "broken")

    print("PASS every cursor alias is relative and contained in its theme")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
