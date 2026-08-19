#!/usr/bin/env python3
"""Comments/docstrings cannot launder terminology ownership."""
import term_consistency_check as gate


def main():
    src = {
        "finder.py": gate.source_literals('label = "Folder"\n'),
        "accounting.py": gate.source_literals(
            '"""documentation mentions Folder"""\n'
            '# another quoted "Folder" comment\nvalue = 1\n'),
    }
    assert gate.owned_by("Folder", src, {"finder.py"})
    src["accounting.py"] = gate.source_literals('label = "Folder"\n')
    assert not gate.owned_by("Folder", src, {"finder.py"})
    print("PASS comments/docstrings do not change terminology ownership")
    print("PASS a real runtime literal still makes ownership shared")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
