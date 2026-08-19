#!/usr/bin/env python3
"""Mandarin ü and keyboard-v are one vowel; plain u is not."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
from language import _answer_norm  # noqa: E402


def main():
    green = _answer_norm("lǜ", "zh")
    checks = [
        green == _answer_norm("lü", "zh") == _answer_norm("lv", "zh"),
        green != _answer_norm("lu", "zh"),
        _answer_norm("nǚrén", "zh") == _answer_norm("nvren", "zh"),
        _answer_norm("lǚxíng", "zh") == _answer_norm("lvxing", "zh"),
    ]
    labels = ["tone/no-tone/v agree", "plain u remains different",
              "compound woman spelling", "compound travel spelling"]
    for ok, label in zip(checks, labels):
        print(("PASS" if ok else "FAIL") + ": " + label)
    all_ok = all(checks)
    print("RESULT: %s" % ("ALL PASS" if all_ok else "FAILED"))
    return not all_ok


if __name__ == "__main__":
    raise SystemExit(main())
