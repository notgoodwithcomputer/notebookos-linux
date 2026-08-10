# 049 — calendar, day 5 of the per-app loop

Claimed 2026-08-09 00:05, `de/calendar.py`. Baseline (taken read-only the night
before): **9 suites, 157 checks, 0 failing**.

Calendar starts from a much stronger position than calculator did — nine suites,
already in both damage gates with a fixture, so day 4's "build the first guard"
job does not apply. What the baseline hides is the *shape* of that coverage:

```
  calculator  2060 lines, 258 checks at start  ->  1 per  8.0 lines
  calendar    3644 lines, 157 checks at start  ->  1 per 23.2 lines
```

Broad and thin. A method-coverage map made that concrete: **calendar.py defines
135 functions and 94 are never NAMED by any of the nine suites** — 46 widget
plumbing (fine) and **48 domain logic**. Named-ness is a proxy, not proof; a
suite can drive a method through the UI without naming it. But it ranked the
day, and the ranking held.

---

## The hypothesis that was wrong, and why that was worth an hour

The shortlist put **recurrence** first: `_next_repeat`, `_whole_periods`,
`_series_members`, unnamed, in an app where month-end and DST bugs are close to
a tradition. Measured before claiming:

```
  anchor-based    Jan 31 -> Feb 28, Mar 31, Apr 30, May 31   (recovers)
  previous-based  Jan 31 -> Feb 28, Mar 28, Apr 28, May 28   (collapses)
  leap yearly     2024-02-29 -> Feb 28 x3, then 2028-02-29   (recovers)
  backwards       Mar 31 -1mo = Feb 28;  Jan 31 -1mo = Dec 31
  _whole_periods  round-trips exactly on a clamped series, n=1..6
```

The primitives are correct. So the real question was the **caller** — the
"who writes it last" shape, where an isolated function is right and the code
around it breaks the contract. `_extend_series` turned out to be correct *and*
to document the exact trap at lines 2780–2787: month/year count from the anchor
so a standing order on the 31st does not decay to the 28th, while day / week /
fortnight step from the last occurrence because they carry no clamp.

Nothing to fix. The hour was not wasted: it is now written down that the top
item on the shortlist is sound, so nobody re-derives it.

---

## Defects fixed

All three were found by **rendering the app in German at 1024×722 and reading
the quick-add hint** — not by reading the parser, which I had already measured
as correct in English.

### 1. The French word for "today" did not work

`_word_tokens()` builds its vocabulary through `_t()`, so day and month names
are localised. The catalog's French for Today is `Aujourd’hui` — with a
**typographic apostrophe, U+2019**. Every keyboard produces the ASCII `'`.

```
  'Gym aujourd’hui'   typographic  -> RECOGNISED
  "Gym aujourd'hui"   what you type -> NOT recognised, stayed in the name
```

So a French user typing their own language correctly got an event called
"Gym aujourd'hui" on today's date instead of an event called "Gym".

### 2. Turkish lost the word if you typed in capitals

The catalog's Turkish for Tomorrow is `Yarın`, with a **dotless ı**. Python's
`str.lower()` is not locale-aware: `"YARIN".lower()` is `"yarin"` with a dotted
i, which never matches.

```
  'Gym yarın'   -> RECOGNISED
  'Gym YARIN'   -> NOT recognised
  'Gym yarin'   -> NOT recognised
```

### The fix for both

A `_fold()` helper applied on **both** sides — where the token table is built
and where a typed word is compared — mapping typographic apostrophes to ASCII
and the dotted/dotless I pair to a plain `i`.

**Accents are deliberately NOT stripped.** `manana` for `mañana` is a spelling
mistake, not something the keyboard did to the person, and folding it away would
start matching words nobody typed. That asymmetry is the whole judgement in the
helper, so it is argued for in its docstring rather than left to be rediscovered.

### 3. `noon` / `midday` / `midnight` were English-only

The **only** day-time vocabulary still hard-coded, so `Mittag` stayed in the
event's name while `Donnerstag` was understood — an inconsistency inside one
sentence. Now looked up through `_t()` like everything else. `_t()` returns the
English source until the catalogs carry the keys, so this is correct today and
starts working in the other sixteen languages the moment they land.

### 4. An unreadable record was dropped, against a promise in the line below it

`calendar.json` is not this app's private store — `tasks.py` writes schedule
entries into the same file — so every save re-reads the file and merges. That
machinery promises, in `_merge_disk_events`, that a record it cannot salvage is
*"carried through the write untouched rather than dropped"*.

`_read_events_file` ended with:

```python
return [it for it in items if isinstance(it, dict)]
```

which meant a row that is not a dict never reached the orphan path at all.
Measured — four rows planted on disk, three came back:

```
  {"title": "Good", "date": ...}   a real event       kept
  "this is not an event"           a bare string      *** DROPPED ***
  {"no": "date"}                   malformed dict     kept
  {"title": "No date either"}      malformed dict     kept
```

The filter was also **redundant**: `_norm_event` opens with its own
`isinstance(item, dict)` guard and returns None for anything else, which is
exactly the signal the orphan path waits for. Removed; the only caller is
`_merge_disk_events`, and the rest of the contract is unchanged.

### 5. Any JSON with an `events` list replaced the whole calendar

`_apply_document` promises to return False **"touching no state"** on an
unusable structure — explicitly so a foreign JSON dict from the shared folder
cannot wipe the calendar. File ▸ Open reads whatever the person picks out of
Documents, beside every other app's JSON.

The guard asked only that `events` be a *list*. The load below keeps whatever
`_norm_event` can salvage and assigns the result, so **the contents were never
checked**. Measured, each opened over a calendar holding three events:

```
  {"events": ["track1", "track2"]}   a playlist   3 -> 0, returned True
  {"events": [1, 2, 3]}              a log        3 -> 0, returned True
  {"events": [null, null]}                        3 -> 0, returned True
  {"events": [[[]]]}                              3 -> 0, returned True
```

Every event gone, and the Open reported as success. A non-empty `events` list
must now contain at least one record `_norm_event` can actually read. An
explicitly **empty** list still loads and still clears — that one is the
document saying so, and starting clean is a real thing to want.

### 6. Silence about events was read as "delete them"

A dict with `calendars` and no `events` key at all was accepted, and because the
load assigns `self.events` unconditionally, opening `{"calendars": [...]}` over
three events took it to zero and swapped the calendar list. `_serialize_document`
always writes both keys, so a dict without `events` was never one of ours —
rejected now, which also keeps `_apply_document` all-or-nothing.

---

## The third suite

`tools/calendar_document_selftest.py` — **19 checks, 3 red proofs**. Nine
foreign files (a ledger, a task list, a cookbook, a bare string, a number,
null…) must leave the model untouched; four `events`-shaped impostors must not
replace it; and the things a real document must still do are pinned beside them
— an empty list clears, one readable event among rubbish loads that event, and
the app's own serialised document round-trips.

Red proof 3 came back **4**, not the 3 I predicted: the empty dict takes the
same branch, and "touching no state" has to hold for it too.

---

## The second suite

`tools/calendar_merge_selftest.py` — **10 checks, 4 red proofs**. It drives the
concurrent-write contract with an actual second writer: a foreign append
survives, a deletion is not resurrected, an edit beats its stale disk copy, an
unreadable row of any shape is kept, and no shape of stored file makes a save
raise (11 tried).

**One red proof changed this suite too.** Mutating the memory-wins guard came
back clean against the "an edit beats the stale copy" check — because after an
edit the ORIGINAL tokens are still in `_seen`, so the stale copy is skipped as a
deliberate *deletion* and `mem_tokens` is never consulted. That check tests
`_seen`, not the guard it is named after. Reaching `mem_tokens` needs an event
added *this session* — never read from disk, so not in `_seen` — that another
writer adds independently. With the guard disabled it lands on disk twice. Both
checks are kept; they cover different guards.

---

## The suite

`tools/calendar_quickadd_selftest.py` — **61 checks, 9 red proofs**. The parser
is ~120 lines with a docstring full of promises and **no suite named it**.

Measured first: in English it is *correct*. Every documented promise holds, and
a hostile battery of 52 inputs plus 3000 random strings raised nothing — NUL
bytes, a 10 000-character name, an RTL override, fullwidth and Arabic-Indic
digits. So most of this suite is not a bug fix; it pins behaviour that is
conservative **by construction** in ways that are easy to undo. What stops
`at 25` becoming 01:00 is that the hour test refuses it and the words fall
through into the title — one `% 24` away from being silently wrong.

The promise worth the most:

```
  "table for 4"        -> title "table for 4", 09:00   a table for four
  "table for 4 at 7"   -> title "table for 4", 19:00   ...at seven
  "Standup at 7"       -> title "Standup",     19:00   a time
```

**One red proof changed the suite.** Mutating the empty-title guard came back
clean, because for `"3pm"` the words are all consumed, `keep` is empty, and
`all([])` is **True** — so the day-words guard returns None first and the
empty-title guard is never reached. Only input surviving as *punctuation* gets
there. Five checks (`","`, `"-"`, `"--"`…) exist because a proof failed to land,
which is what proofs are for.

Also pinned: a weekday that **opens** the line stays in the name. The source
records the scar — dropping it turned "Sunday lunch at noon" into an event
called "lunch".

---

## New English source strings

For the campaign's i18n merge. Catalogs untouched.

| string | note |
|---|---|
| `Noon` | new — quick-add vocabulary, wired through `_t()` and falling back to English until merged |
| `Midday` | new — same |
| `Midnight` | new — same |

Until these land, the three words work in English only, exactly as before. The
code is already correct; only the translations are missing.

---

## State

```
  12 calendar suites            247 checks, 0 red      (from 9 / 157)
  construct_one calendar        OK
  construct_all_host            38 ok, 0 crashed
  i18n_check                    1 problem, pre-existing (academics
                                'Move to Class…', present at HEAD)
```

Rendered at 1024×722 in English and German: fits, nothing clipped.

Only `de/calendar.py` and three new `tools/calendar_*_selftest.py` touched. Left
uncommitted for the campaign's integration pass.
