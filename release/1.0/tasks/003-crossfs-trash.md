# 003 — Move to Trash could never work on removable media

**Lane:** A (finder) + C (catalogs, i18n_merge) · **Streams:** S1, S5, S10
**Status:** CLOSED 2026-08-06

## Claim  (ROADMAP #16)

The Trash is one folder under `$NB_HOME`. Everything on a USB stick, an SD card
or a second disk is therefore on the far side of a filesystem boundary from it,
and the kernel answers a rename across that boundary with `EXDEV` instead of
moving anything. `_trash_selected` was a single `os.rename`, so on removable
media **Move to Trash was offered on every row and could never once succeed** —
it always answered "Could not move ... to Trash". A dead end, in the exact place
C5 says there may not be one.

**The ROADMAP entry is wrong about where the fix already lived.** It records
that "the restore path already has the fallback". It does not: the only
cross-device branch in the file is in Paste's cut. `_restore_selected` was one
`os.rename` too, so **Put Back was missing it as well** — and adding the trash
branch alone would have moved the dead end rather than removed it, leaving items
that could be thrown away and then never put back.

## Fix
Both paths now detect the boundary with the existing `_same_filesystem` and hand
the work to `_copy` — Paste's own cross-disk machinery, with its staged write,
progress card, Cancel, and destination claim. No second implementation.

Ordering is the same as Paste's and for the same reason: the copy is written
first and the original removed only afterwards, and only if it is still the same
entry. A copy off a stick runs long enough for the original to be replaced, and
removing by name at the end would destroy an item the user never selected while
the status line said it had been trashed. Put Back is ordered the same way
round: if the Trash copy cannot be removed the item exists twice, which is
recoverable; the reverse order risks it existing nowhere.

Cross-disk trash offers no one-step Undo, matching Paste — undoing it would be a
second copy of the same length. The recourse is Put Back, which is why the
origin record is written before any of it begins.

## Gate: `tools/finder_crossfs_trash_selftest.py`
Uses a **real** second filesystem: `/dev/shm`, `/run/user/$UID` and `/tmp` are
separate mounts on any Linux and all are writable without root, so the stick
lives on one and `$NB_HOME` on another and `os.rename` between them raises a
genuine `EXDEV` — asserted as the suite's first check. A test that monkeypatched
`_same_filesystem` to return False would prove only that the new branch runs
when told to; it could not prove the branch is reached by the condition that
actually occurs on a stick, which is the half that was broken. If two
filesystems cannot be obtained the suite **fails** rather than skips.

19 checks: file and folder both cross, bytes verified on both legs, origin
record written and cleaned up, no `.nbcopy-` staging entry left on either side,
and the same-disk path still fast and still undoable.

## Red-proof (2026-08-06)
Both branches deleted, on the real tree, restored immediately after:

    PASS a rename from the stick to home raises EXDEV
    FAIL the item reaches the Trash
    FAIL the bytes survive the crossing
    FAIL it is gone from the stick
    FAIL its origin is recorded so Put Back knows where it came from
    FAIL the Trash lists it  [not reached: nothing was trashed]
    ... 10 FAILED, same-disk checks still PASS

The first draft of the suite scored only 7 failures here, because three Put Back
assertions **passed vacuously**: with the trash step broken the file was still
on the stick, so "Put Back returns it to the stick" was true without Put Back
having done anything. Those assertions now refuse to evaluate and report
`[not reached]`. A vacuous green on the path under test is precisely the failure
this discipline exists to prevent, and it appeared in a gate written to enforce
it.

## i18n: Finder is now fully covered
The two new strings joined nine that were **already uncovered** — Finder had
eleven strings showing English in all sixteen non-English languages, including
`Put back “%s”` and `Could not put that back`. All eleven are now in all
seventeen catalogs (3084 → 3095 keys), with each language's own Trash noun and
quote style: Papierkorb, Corbeille, Cestino, ゴミ箱, 휴지통, 废纸篓, מיסטקאַסטן.

**`i18n_merge.py` could not have added three of them.** Its placeholder check
compared the key's specs to the translation's, and a counted string like
`"%d item%s could not be deleted."` carries a second spec that is the English
plural hack — consumed by nbi18n and never emitted, so a correct German
translation is `"%d Element|%d Elemente"`: one spec, and the pipe giving both
grammatical numbers. That read as a lost placeholder and was rejected, which
would have pushed every future plural string around the one tool that writes the
seventeen catalogs atomically. The check now classifies specs with nbi18n's own
`_spec_kinds`, **imported rather than reimplemented** — if the two disagreed
about which `%s` is a plural marker the merge would either refuse correct work
or admit a string the app cannot format, and the disagreement would surface as a
crash in a language nobody here reads.

**Red-proof of the loosened check** — four faults planted in `de.json`:

    x de: '%d item%s could not be deleted.' placeholders ['%d'] -> [[], []]
    x de: '%d newer item%s stayed in the Trash.' placeholders ['%d'] -> [['%s']]
    x de: 'Emptied %d item%s.' placeholders ['%d'] -> [['%d'], []]
    x de: 'Put back “%s”' placeholders ['%s'] -> [[]]
    x catalogs would end at different sizes: [3091, 3095]
    5 problem(s); NOTHING written

The third is the one that matters: only the PLURAL half lost its `%d`, and the
singular still looked right.

## A note on the coverage baseline
`i18n_coverage_check --update-baseline` rewrites the baseline to whatever is
uncovered now, which after this work would have absorbed **23 genuinely new**
accessibility strings into accepted debt and stopped reporting them. The
baseline was reconstructed to the 117 that predate them, so the count reads
`140 UNCOVERED (23 new)` and the 23 stay visible. Worth knowing before anyone
runs that flag again: it does not distinguish "fixed" from "given up on".
