#!/usr/bin/env python3
"""Comics recovery remembers binding without embedding it in .comic files."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import comics  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="comics-identity-") as td:
        old_home = comics.NB_HOME
        comics.NB_HOME = td
        try:
            bound = os.path.join(td, "Documents", "story.comic")
            recovery = os.path.join(td, "comics.json")
            os.makedirs(os.path.dirname(bound))
            doc = comics.ComicDocument()
            Path(bound).write_text(json.dumps(doc.serial()), encoding="utf-8")
            payload = doc.serial()
            payload["_session"] = {"doc_path": comics._portable_path(bound)}
            Path(recovery).write_text(json.dumps(payload), encoding="utf-8")
            restored, read_only, reports = comics.load_store(recovery)
            assert not read_only and not reports
            assert restored.doc_path == bound
            exported = restored.serial()
            assert "_session" not in exported and "doc_path" not in exported
            assert td not in json.dumps(exported)
            print("PASS recovery restores compatible saved-file identity")
            print("PASS portable .comic serialization contains no machine path")
            print("RESULT: PASS")
            return 0
        finally:
            comics.NB_HOME = old_home


if __name__ == "__main__":
    raise SystemExit(main())
