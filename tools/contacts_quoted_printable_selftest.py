#!/usr/bin/env python3
"""Quoted-printable vCards import human text, not transfer encoding."""

from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, os.fspath(DE))

import contacts  # noqa: E402


def main() -> None:
    card = ("BEGIN:VCARD\r\nVERSION:3.0\r\n"
            "FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:Jos=C3=A9 Garc=C3=ADa\r\n"
            "NOTE;ENCODING=QUOTED-PRINTABLE:Caf=C3=\r\n=A9 au lait\r\n"
            "END:VCARD\r\n")
    person = contacts.parse_vcards(card)[0]
    assert person["name"] == "José García", person["name"]
    assert person["notes"] == "Café au lait", person["notes"]
    ordinary = contacts.parse_vcards(
        "BEGIN:VCARD\nFN:Doe\\, Jane\nNOTE:Line one\\nLine two\nEND:VCARD\n")[0]
    assert ordinary["name"] == "Doe, Jane" and ordinary["notes"] == "Line one\nLine two"
    print("PASS quoted-printable and ordinary vCards import readable text")
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
