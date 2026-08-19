#!/usr/bin/env python3
"""Quick-add project prefixes must be deterministic and unambiguous."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))
import tasks  # noqa: E402


def parsed(token, projects):
    old = tasks.PROJECTS
    tasks.PROJECTS = projects
    try:
        return tasks.Tasks._parse_quickadd(None, "Read chapter " + token)
    finally:
        tasks.PROJECTS = old


def main():
    projects = [("Home", "h"), ("Homework", "w")]
    ambiguous = parsed("#Ho", projects)
    exact = parsed("#Home", list(reversed(projects)))
    unique = parsed("#Homew", projects)
    ok = (ambiguous[0] == "Read chapter #Ho" and ambiguous[1] is None
          and exact[0] == "Read chapter" and exact[1] == "Home"
          and unique[0] == "Read chapter" and unique[1] == "Homework")
    print(("PASS" if ok else "FAIL") +
          ": project prefixes prefer exact and reject ambiguity")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return not ok


if __name__ == "__main__":
    raise SystemExit(main())
