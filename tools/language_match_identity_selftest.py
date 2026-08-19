#!/usr/bin/env python3
"""A match round must never contain visually indistinguishable choices."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import language  # noqa: E402


def main():
    app = language.Language.__new__(language.Language)
    app.course = {"code": "zh"}
    words = [
        {"t": "shì", "e": "yes"},
        {"t": "shì", "e": "to be"},
        {"t": "nǐ", "e": "you"},
        {"t": "hǎo", "e": "good"},
        {"t": "zàijiàn", "e": "goodbye"},
    ]
    ex = app._make_exercise("match", None, words)
    left = [language._norm(p[0]) for p in ex["pairs"]]
    right = [language._norm(p[1]) for p in ex["pairs"]]
    ok = len(left) == 4 and len(set(left)) == 4 and len(set(right)) == 4
    print(("PASS" if ok else "FAIL") +
          ": matching choices are visibly unique on both sides")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return not ok


if __name__ == "__main__":
    raise SystemExit(main())
