#!/usr/bin/env python3
"""A LABEL WRITTEN IN CAPITALS MUST STILL FIND ITS CATALOG ENTRY.

nbpicker uppercases the window title it is handed, and several apps pass a raw
English string. nbi18n's upper-case fallback recovers those by trying
`capitalize()` and `title()` — but neither transform can reach a key whose own
spelling is irregular: "EXPORT TO PDF" becomes "Export to pdf" or
"Export To Pdf", while the catalog key is written "Export to PDF". The picker
title therefore stayed English in all sixteen other languages while the
translation sat in every catalog, unreachable.

The fix is a case-folded index consulted only where the fold names EXACTLY ONE
key, so it can never pick between two entries. This suite pins both halves:
the recovery, and the refusal to guess.
"""
import os
import sys

DE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_LANG", "fr")
import nbi18n  # noqa: E402

passed, failed = 0, []


def check(name, got, want):
    global passed
    if got == want:
        passed += 1
        print("PASS " + name)
    else:
        failed.append(name)
        print("FAIL %s: %r != %r" % (name, got, want))


# 1. The irregular spelling the two transforms cannot reach.
check("an all-caps picker title reaches its irregularly spelled key",
      nbi18n._t("EXPORT TO PDF"), nbi18n._t("Export to PDF").upper())
check("...and it is not left in English",
      nbi18n._t("EXPORT TO PDF") == "EXPORT TO PDF", False)

# 2. The regular spellings still work (the older transforms are not broken).
check("a title-case key still resolves in capitals",
      nbi18n._t("OPEN PROJECT"), nbi18n._t("Open Project").upper())

# 3. The two-letter guard stays: "FR" must not become Friday.
check("a two-letter badge is left alone", nbi18n._t("FR"), "FR")

# 4. AMBIGUITY IS NEVER GUESSED. Build a catalog holding two keys that differ
#    only by case and prove the fold refuses both.
idx = nbi18n._ci_index({"Bank": "Banque", "bank": "rive", "Ready": "Prêt"})
check("a fold that names two keys is dropped", "bank" in idx, False)
check("...while an unambiguous fold is kept", idx.get("ready"), "Ready")

# 5. The index is built from the catalog actually loaded, not a snapshot taken
#    before it: a key present in _CAT must be reachable through the fold.
missing = [k for k in ("Export to PDF", "Open Project")
           if nbi18n._CAT and nbi18n._CAT_CI.get(k.lower()) != k]
check("every catalog key is reachable through its own fold", missing, [])

print("\n%d/%d checks passed" % (passed, passed + len(failed)))
print("RESULT: %s" % ("ALL PASS" if not failed else "FAILED"))
raise SystemExit(1 if failed else 0)
