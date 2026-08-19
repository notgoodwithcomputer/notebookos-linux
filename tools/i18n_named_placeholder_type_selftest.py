#!/usr/bin/env python3
"""Named placeholder names and conversion types are both contractual."""

import i18n_placeholder_check as gate


def main() -> None:
    key = "Found %(count)d files"
    assert gate.check(key, "Found %(count)d files") == []
    assert gate.check(key, "Found %(count)f files")
    assert gate.check(key, "Found %(count)s files")
    reordered_key = "%(first)s then %(second)d"
    assert gate.check(reordered_key, "%(second)d then %(first)s") == []
    print("PASS named placeholder conversion types cannot drift")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
