#!/usr/bin/env python3
"""A corrupted catalog cannot vote itself into a different alphabet."""

from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import json
import shutil
import tempfile

import catalog_script_check as gate


def main() -> None:
    old = gate.DE
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for lang in gate.LANGS:
            shutil.copy2(Path(old) / ("lang_%s.json" % lang), root)
        greek_path = root / "lang_el.json"
        cat = json.loads(greek_path.read_text(encoding="utf-8"))
        greek_path.write_text(json.dumps({k: "English text" for k in cat},
                                         ensure_ascii=False), encoding="utf-8")
        gate.DE = str(root)
        try:
            with redirect_stdout(StringIO()):
                rc = gate.main([])
        finally:
            gate.DE = old
        assert rc != 0
    print("PASS each catalog is held to its declared script family")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
