#!/usr/bin/env python3
"""Standards-valid UTF-16 XHTML chapters remain readable."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import ebook  # noqa: E402


def plain(blocks):
    return " ".join(text for _kind, text in blocks)


def main() -> None:
    html = "<html><body><p>Café chapter</p></body></html>"
    for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be"):
        data = html.encode(encoding)
        if encoding == "utf-16-le":
            data = b"\xff\xfe" + data
        elif encoding == "utf-16-be":
            data = b"\xfe\xff" + data
        assert "Café chapter" in plain(ebook._epub_extract(data)), encoding
    print("PASS UTF-8 and UTF-16 EPUB chapters yield the same readable text")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
