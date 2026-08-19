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

def retired(source, phrase):
    """THE guard: True when `phrase` is gone from `source`.

    Both sections below call THIS function. That is the whole point of the
    repair: the PASS-MUTANT block used to re-implement the test inline as
    `phrase in (source + phrase)`, which is true whatever the guard does --
    a tautology, not a proof. MEASURED before the repair: with `ok` forced to
    True (a dead guard) AND `_t("Remove this track\'s clips?")` physically put
    back into sequencer.py, every line of this suite still printed PASS and the
    verdict was still `RESULT: PASS`. The block whose only job was to show the
    guard is alive could not see the guard die. Routing both sections through
    one named predicate is what makes the mutant able to go red."""
    return phrase not in source


failed = 0
for app, forbidden in CASES.items():
    source = (ROOT / (app + ".py")).read_text(encoding="utf-8")
    for phrase in forbidden:
        ok = retired(source, phrase)
        print(("PASS" if ok else "FAIL") + ": %s retires %r" % (app, phrase))
        failed += not ok

# PASS-MUTANT: put each retired phrase back into an in-memory mutant and
# require the SAME predicate -- the function above, not a re-statement of it --
# to answer "not retired". A guard that has stopped looking now fails here.
for app, forbidden in CASES.items():
    source = (ROOT / (app + ".py")).read_text(encoding="utf-8")
    caught = all(not retired(source + "\n" + phrase, phrase)
                 for phrase in forbidden)
    print(("PASS" if caught else "FAIL") + ": PASS-MUTANT %s guard catches reintroduction" % app)
    failed += not caught

print("RESULT: %s" % ("FAILED" if failed else "PASS"))
raise SystemExit(bool(failed))
