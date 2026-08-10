# 026 — Three text gates stop being blind

**Lane:** C · **Streams:** S2 · **Status:** CLOSED 2026-08-07
(Dispatched to Codex; its inner session stalled mid-edit and was cancelled by
the user; the campaign session took the package over and executed it. The
stalled partial had the right shape — same direction, finished properly.)

## jargon_sweep (commit 4293497d)
Before: 68 findings, exit 0 ALWAYS (a report wearing a gate's name), 698 of
2,941 single-word strings discarded, 4 modules exempt, audience docstring
said "mainstream, non-technical user". After: lowercase-identifier shapes
only are skipped (a Title-case single word is what a label looks like);
len<4 → len<2; exemptions replaced by per-string reasons; audience corrected
to the campaign thesis (domain vocabulary stays, machinery never leaks);
ratchet ledger tools/jargon_ledger.json (28 allow + 2 pending); exit 1 on
unaccounted. 114 findings triaged. Notable: the four exempt modules produced
ZERO real findings — the exemption protected nothing; the one substantive
catch is "Widget Settings…" (guide says CARDS; toolkit word leaked as a
product name — campaign-owned rename, 17-catalog footprint, queued).
Red-proofs: redproofs/jargon_sweep-2026-08-07.txt (3 directions).

## voice_check (commit 4293497d)
Before: 3 findings; every %s string skipped (305 — precisely the confirms
and destructive warnings). After: placeholders neutralised to an inert
token; ratchet ledger tools/voice_ledger.json; exit 1 on unaccounted. 9
findings: 5 allowed as function (installer procedure statements, build
diagnostics), 1 rule-shape false match allowed ("Add %d to Media"), 3 REAL
second-person strings pending for app lanes (gbabuild.py:459, music.py:2566,
novel.py:1342). Red-proofs: redproofs/voice_check-2026-08-07.txt — including
the corrected first attempt (a bare-assignment bait is invisible to the
scanner BY DESIGN; the bait must be a TEXT_CALLS call site).

## term_consistency_check (this commit)
The "3 of 63 modules" framing was imprecise: the tool is CONCEPT-driven and
had only GBA-scoped concepts. Going OS-wide is knowledge curation, so the
repair is a MECHANISM plus its first use: `--survey PATTERN [apps...]` shows
every language's actual word choices for a concept (measured, never
invented), and the first OS-wide concept — folder, 20 owned keys, 7 apps —
is curated from that survey and LOCKS the state it found: every language on
one root, sr on fascikla with the documented mapa regression as the named
stray that must not come back. maps.py is deliberately out of scope (the
Maps app's Serbian name legitimately contains the stray root — the ficha
lesson). Red-proof: redproofs/term-folder-2026-08-07.txt — resurrected
"Nova mapa" fails naming sr and the exact key. Further concepts (delete vs
remove, save, track/note sense collisions) are ongoing curation work using
the survey; each lands as measured data, never guessed roots.

## Survey by-catch, filed to HANDOFF
English near-duplicates the folder survey exposed: "could not be created" /
"could not be made" (backup, two adjacent keys); "Move to Folder…" vs
"Move to folder" (gbasdk, capitalization split); "This folder is empty" with
and without a period (two keys). App-lane string fixes + campaign catalog
migration.
