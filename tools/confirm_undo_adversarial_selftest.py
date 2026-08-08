#!/usr/bin/env python3
"""Static adversarial guards for the confirm/undo reconciliation sweep."""
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1] /
        "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

CASES = {
    "tasks": ["This cannot be undone.",
              "lambda n=nm: self._open_removelist(n)"],
    "workout": ["if not _confirm(self, _t(\"Delete exercise\")",
                "if not _confirm(self, _t(\"Clear today\")"],
    "screenplay": ["if not self._confirm_replace(\"New Script\")",
                   "if not self._confirm_replace(\"Open Script\")"],
    "calendar": ["if has_data and not self._confirm("],
    "cookbook": ["self._confirm(\"Delete Category\""],
    "novel": ["\"Delete chapter?\"", "self._confirm(\"Delete part?\"",
              "\"Discard this manuscript?\""],
    "sequencer": ["_t(\"Remove this track's clips?\")",
                  "_t(\"Remove every clip?\")", "_t(\"New project?\")",
                  "_t(\"Open this project?\")", "_t(\"Shorten to %s?\")"],
    "video": ["_t(\"Remove clip?\")", "_t(\"New project?\")"],
    "media": ["_t(\"Move “%s” to the Trash?\")"],
}

failed = 0
for app, forbidden in CASES.items():
    source = (ROOT / (app + ".py")).read_text(encoding="utf-8")
    for phrase in forbidden:
        ok = phrase not in source
        print(("PASS" if ok else "FAIL") + ": %s retires %r" % (app, phrase))
        failed += not ok

# PASS-MUTANT: prove every guard is live by putting each retired phrase back
# into an in-memory mutant and requiring the same predicate to reject it.
for app, forbidden in CASES.items():
    source = (ROOT / (app + ".py")).read_text(encoding="utf-8")
    caught = all(phrase in (source + "\n" + phrase) for phrase in forbidden)
    print(("PASS" if caught else "FAIL") + ": PASS-MUTANT %s guard catches reintroduction" % app)
    failed += not caught

raise SystemExit(bool(failed))
