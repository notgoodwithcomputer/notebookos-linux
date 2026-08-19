#!/usr/bin/env python3
"""Shell-only boot/session commands belong to the shipped binary contract."""

from pathlib import Path
import tempfile

import shipped_binaries_check as gate


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "session.sh"
        path.write_text("#!/bin/sh\ndefinitely-not-shipped --start\n", encoding="utf-8")
        found = gate.shell_commands_in(path)
        assert found == {"definitely-not-shipped": False}
        path.write_text("command -v optional >/dev/null && optional --start\n",
                        encoding="utf-8")
        assert gate.shell_commands_in(path).get("optional") is True
    print("PASS shipped shell entrypoints contribute external commands")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
