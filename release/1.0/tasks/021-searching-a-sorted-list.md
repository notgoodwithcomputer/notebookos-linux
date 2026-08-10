# 021 — Searching a sorted list

**Lane:** F (packages) · **Streams:** S1 truth defects
**Status:** CLOSED

Chosen by measurement, not by guessing: `tools/func_coverage.py` (task 020)
reported **13 of packages.py's 39 functions never entered** by any suite, and
all thirteen were the interaction handlers. `packages_selftest` covers the
enumeration of the package list; nothing covered what a person does to it.

## The defect

`_on_search` reconciles the selection after the query narrows the list, so the
inspector never shows a package that is not on screen. Its comment says it
falls back to *"the first visible row"*. It computed that list itself:

```python
visible = [i for i, p in enumerate(PACKAGES) if self._matches(p, q)]
```

— straight off the underlying enumeration, with **no sort applied**, while
`_rebuild_list` sorts before it draws. Two callers, two different ideas of what
"first" means.

Measured with the table sorted by name descending: a search that hid the
selection selected **Academics**, index 1, while the top row on screen was
**Translations**, index 39. The highlighted row is somewhere down the middle of
the list — possibly scrolled out of sight — and the inspector shows a package
the reader did not choose. Under the default (unsorted) order the two agree,
which is why it survived.

Fixed by giving the order one owner: `_visible()` returns the packages the list
shows, in the order it shows them, and both `_rebuild_list` and the search
fallback call it.

## Gate

`tools/packages_interaction_selftest.py`, 37 checks, all through the real
handlers on a real window, against the packages actually enumerated from the
image rather than seeded rows.

Covers: search reaching all three fields its comment claims (display name,
kind, module filename); a search matching nothing clearing the selection, the
inspector saying so rather than going blank, and the empty list saying **which
kind** of empty it is; Esc clearing a non-empty search and *falling through* on
an empty one so Esc still leaves the app; each of the four sort fields
ascending, reversing on a second click, and ordering on the real numeric field
rather than the formatted string; Up/Down/Home/End walking the list **as
displayed**; Up at the top staying put rather than wrapping; the sidebar views;
and the menu greying out Clear Search with no query and Open on a system
component rather than shipping a dead item.

**Open's parity with the Finder is now tested, not just claimed.** The comment
says it starts an app "the same way the Finder does". Reading confirmed all
three launchers (`packages._on_open`, `finder._launch_module`, `shell.launch`)
use `python3 <script>` with `env=dict(os.environ, PYTHONPATH=DE_DIR)`. The
suite now asserts the argv and the environment against a stand-in `Popen`, so
the claim cannot quietly stop being true — this image has already lost
GStreamer's plugin registry once to a missing variable.

That check needed a second pass. The first version went red against the
mutation only because the scratch tree's `DE_DIR` differed from the
`PYTHONPATH` the harness had exported — a path mismatch, not the missing
assignment. In production `session.sh` exports the same value, so the child
would have **inherited a correct PYTHONPATH** and the mutation would have gone
undetected. The suite now clears `PYTHONPATH` from the parent before the call,
so the property under test is the one that matters: the app sets it *itself*
and launches correctly from anywhere. Against the mutation the check now reads
`None` rather than a plausible-looking path.

**Red-proof, six mutations:**

| mutation | result |
|---|---|
| the fallback back on enumeration order (the shipped code) | 1 fail |
| size sorting on the formatted string instead of the number | 2 fail |
| Esc no longer clearing the search | 1 fail |
| Up at the top wrapping to the bottom | 1 fail |
| Open offered for a system component | 1 fail |
| PYTHONPATH dropped from the spawn | 1 fail |

packages is now **37 of 40** functions entered, from 26 of 39.
`_sync_head_gutter` is left — a scrollbar-gutter alignment helper driven by a
size-allocate signal, cosmetic and awkward to drive headlessly.


## Two changes to the runner, forced by my own mistake

The full run after this work reported **2 NEW FAILURE(S)** and nothing else —
no names, no output. Both suites (`calendar_mirror`, `config_resilience`)
passed in isolation and under the runner on their own. The cause was mine: I
had run `packages_interaction_selftest` twice in the FOREGROUND while the gate
run was going in the background, so two Xvfb sessions were competing, and the
two failures sit at positions 27 and 33 — exactly that window. **Do not run a
guestrun suite while a gate run is going.**

But the report should have made that diagnosable in one read, and it did not:

* **A failing suite now always prints its tail**, not only under `-v`. An
  hour-long run ending in a bare count forces the whole thing to be run again
  just to learn what broke, which is what happened and cost another forty
  minutes. `-v` now means "print every suite's tail, passing ones included".
* **The summary names the failing suites** and how many checks each ran, rather
  than counting them. A bare count makes the reader scan 193 lines for the two
  that say FAIL — and says nothing about what went wrong, which is how a real
  failure gets filed as "probably flaky".

Proved by planting a suite that fails with a distinctive line: before, the
sentinel appeared once and only in passing; after, the tail and the named
summary both carry it.
