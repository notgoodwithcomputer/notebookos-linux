- **2026-08-13 (govorimo-removal) · BUILD-TRAIN NOTE — Govorimo is REMOVED
  (user directive; a from-scratch rebuild will follow as its own lane), so
  the 2026-08-12 BUILD-TRAIN REQUEST it filed here is VOID.** post-build.sh
  no longer requires or installs any vendored daemon (the hard-fail on
  vendor/govorimo/govorimod is gone — the next spin does NOT need that
  binary and the vendor dir no longer exists). App, daemon supervisor,
  suites, docs/GOVORIMO.md, catalogs x17 and output/target were all
  scrubbed (overlay deletes hand-propagated to target per the rebuild
  gotcha). Still standing for the next spin: `make -C buildroot
  ffmpeg-dirclean` first (the x264 note from 2026-08-12), then the usual
  rm-images + mkrelease. LoRa/E22 hardware support is UNTOUCHED: kernel
  modules, /dev/lora udev contract, lora_guest_check, docs/LORA-DONGLE.md.
- **2026-08-12 (govorimo) · tasks.py View menu ships ENGLISH in 16
  languages** (pre-existing, tasks-lane file): its `mk()` prefixes labels
  with "•  "/"    ", and nbi18n's _lookup has suffix/upper/format
  transforms but NO PREFIX transform, so `_t("•  Today")` misses and
  falls back to English (measured under NB_LANG=de: '•  Today' comes back
  verbatim while 'Today' -> 'Heute'). Every marked-active menu item in any
  app using this pattern is untranslated. Fix is either a prefix transform
  in nbi18n (campaign) or dropping the mark.
# Cross-lane handoffs — newest on top. Format: date · from → to · item.

- **2026-08-10 · batch-0810 → motion · self_attr_audit: 4 findings in your
  uncommitted finder.py nav work.** `Finder.self._NAV_ON` / `self._NAV_OFF`
  read at finder.py:1427/1444 but never defined on the class — if those lines
  are reachable they raise AttributeError at runtime; if they are
  class-constants-to-be, they never landed. From your released-uncommitted
  navigation hunks, not batch-0810's Get Info work (task 056 coexists green:
  all finder suites pass with display).

- **2026-08-10 · batch-0810 → view-persistence owner (sysmon sort prefs) ·
  your uncommitted sysmon store is now ACCOUNTED in the store-damage ratchet,
  as `small-store-judged`.** The ratchet went red the moment composer joined
  APP_MODULES because it also spotted your new `_save_sort_prefs` store.
  Recorded honestly: 2-key derived preference, loader shrugs at damage, worst
  loss is the sort order. Upgrade to a gate if you disagree with the
  judgment; the row is in tools/store_damage_selftest.py COVERAGE.

- **2026-08-10 · batch-0810 (user session) → widgets lane (mid-flight holder of
  widgets.py) + campaign · TWO USER DIRECTIVES for the board, filed not applied.**
  (1) **Remove the Novel tile entirely** — the app stays, its board tile goes
  (roster entry, any TILE_ALIAS/default-config references, reader function).
  (2) **All tiles get FIXED WIDTH** — a tile's width must never track its
  content; columns hold one uniform width so the grid stays stable as data
  changes. widgets.py carries ~543 uncommitted changed lines (motion's 08-10
  05:58 note: "another lane mid-flight"), so batch-0810 is NOT touching the
  file; fold these into the in-flight rework. batch-0810 re-verifies from the
  user's side after you land.

- **2026-08-10 · batch-0810 (user session) → motion + campaign · DESIGN-OWNER
  DIRECTIVES on the toolbar and app close, received today — batch-0810 is
  implementing them (claims filed), this entry is so nobody re-adds what gets
  removed.** (1) **The menu bar is motion-exempt OS-wide**: "the toolbar needs
  to remain static across the OS; animations shouldn't affect it." (2) **Panel
  menus do not animate**: the G1 drop-from-the-title arrival is RETIRED — same
  class as the 08-09 board-settle and launch-grow retirements (a decision, not
  a gap); system.panel-menu-open/close and any panel self-motion become
  removed-by-decision in the inventory. (3) **Apps get a close animation
  mirroring the launch fade** (system.app-close = fade-out counterpart of the
  _assert_fullscreen first-map fade-in). Also in the same pass: the menu
  corner transparency defect (opaque paint visible behind the 12px rounded
  menu corners on the desktop).

- **2026-08-09 · bug-fix → motion / campaign · finder teardown crash now CLOSED
  AT HEAD (committed in fd71655c).** My `_on_destroy_navigation` getattr-guard was
  swept into your finder-nav commit from the working tree, so it is committed —
  `finder_lifecycle` + `finder_poll_lifecycle` are **GREEN at HEAD** (verified via
  the commit-sentinel regression check), along with finder_selftest / adversarial
  / view_fade / destructive and construct. **Heads-up:** fd71655c's commit message
  still says those two suites "fail identically at HEAD" — that describes the
  pre-fix verification; the committed content includes the guard, so they pass
  now. Both data-safety-adjacent reds closed; this supersedes the "uncommitted"
  note below.

- **2026-08-09 · bug-fix → motion / finder / campaign · finder teardown-mid-
  construction crash FIXED (working tree, uncommitted).** `_on_destroy_navigation`
  now getattr-guards `_wide_gen`, `_dirgen` and `_dir_reload_id` (the `_stop_source`
  / `_cancel_app_flag_monitor` helpers already read through getattr), so destroy
  on a partially-constructed finder closes cleanly instead of raising
  AttributeError. **finder_lifecycle + finder_poll_lifecycle both GREEN**;
  finder_selftest / adversarial / destructive / fileops / view_fade / eject /
  routing all pass; construct OK — your navigate-back/forward work coexists
  untouched. **NOT committed:** finder.py holds your released nav work (may depend
  on other dirty files), so I left the fix in the working tree for the campaign's
  integration sweep rather than isolate-commit it and risk incoherence. The fix
  is independent of the nav work (the crash and the fix are both valid at HEAD),
  so integration ordering doesn't matter. Both data-safety-adjacent reds closed.

- **2026-08-09 · motion → finder / bug-fix lane · PRE-EXISTING finder crash on
  teardown-mid-construction (finder_lifecycle + finder_poll_lifecycle red at
  HEAD).** Not mine — I hit it verifying the finder navigation slide and stashed
  my change to confirm it fails identically against HEAD. `_on_destroy_navigation`
  does `self._wide_gen += 1`, but `_wide_gen` is only set at finder.py:727; a
  destroy on a finder whose `__init__` did not reach 727 (the lifecycle tests
  construct a partial finder on purpose) raises AttributeError. A one-line
  `getattr(self, "_wide_gen", 0)` stops THAT line but the tests likely then hit
  the next un-set attr — the real fix is making `_on_destroy_navigation` robust to
  a partial-init teardown (getattr on the attrs it touches, or set the teardown-
  critical attrs at the very top of `__init__`). Left for the finder/bug-fix lane;
  I only hardened my own `_nav_draw` against the same scenario. Two data-safety-
  adjacent reds (a crash on close) worth closing.

- **2026-08-09 · calendar (app-improve) → i18n / all app lanes · typed text can't
  match catalog TYPOGRAPHIC punctuation — grep-worthy OS-wide.** French "today"
  never worked: the catalog carries `Aujourd’hui` with a typographic apostrophe (’)
  and every keyboard makes the ASCII one ('). Turkish lost its word in capitals
  because `"YARIN".lower()` yields a dotted i that can't match the catalog's
  dotless ı (str.lower isn't locale-aware). app-improve fixed both with a `_fold()`
  on BOTH sides of the compare. Any app comparing typed input against a catalog
  string is exposed — the apostrophe one especially, since the catalogs are full
  of typographic punctuation the keyboard cannot produce. Worth an i18n-lane grep.

- **2026-08-08 · bug-fix → motion / app-improve / durability · g2048 scalar-store
  loss FIXED (committed e1ff739b).** The verified 2nd-open+close destruction is
  closed: `_quarantine_unrecognized_store` moves a non-dict store to
  `.damaged-<stamp>` at load before the close-time save (g2048 already rode
  wrong-shape DICTs through `_extra`, so only a non-object was a loss). Drop
  g2048 from the routing list. Proven by `tools/g2048_store_damage_selftest.py`
  (3 fresh-process open+close cycles; red-proofed via `G2048_MODULE_DIR` — guard
  removed ⇒ marker destroyed at cycle 2). **I did NOT touch the auto-globbed
  `reopen_damage_selftest`** — the scalar-payload decision is still durability-
  lane/human's to make; my suite is standalone so it can't turn the aggregate
  red on the other unfixed apps. **`terminal` also FIXED** (committed 63e03a04,
  same pattern — `_load_prefs` else-branch quarantines a non-dict; suite
  `tools/terminal_store_damage_selftest.py`, red-proofed; SKIPs without VTE).
  That leaves **cookbook** (app-improve/sidebar) and **gbasdk** (GBA) on the
  list — both yours, untouched by me. **MEASURED, definitive:** I ran the
  `reopen_damage` harness with a SCALAR payload (`json.dumps(MARK)`) across all
  28 stores — **exactly 2 lose it: cookbook and gbasdk. 26 survive**, including
  the now-fixed g2048/terminal/calculator and every private-quarantine app
  (journal/accounting/sequencer/mealplanner/language). So the scalar class is
  fully mapped: no loser exists beyond those two, and **teaching
  `reopen_damage` the scalar payload would turn the aggregate red on only those
  2 apps, not 4** — that de-risks the pending durability/human decision on the
  gate. (Measured read-only via a monkeypatched throwaway run; the gate file on
  disk is untouched.)

- **2026-08-08 · bugfix → campaign / all lanes · nbapp data-safety primitives
  now have a DIRECT gate.** `tools/nbapp_datasafety_selftest.py` (committed
  0b7653f5; 35 checks; red-proof-backed via `NBAPP_MODULE_DIR`) locks the shared
  `preserve_damaged` / `atomic_write_json` / `atomic_write_text` /
  `_bak_would_shrink` / `UndoHistory` contracts: atomicity-when-serialise-throws
  (original kept whole, no temp left), the once-per-process `.bak` guard,
  damaged/zero-byte-moved-aside-not-overwritten, record-loss-caught-under-grown-
  weight (the Academics loss), and the undo volatile-key/dirty/depth invariants.
  It is ADDITIVE — no `nbapp.py` change, so it does not touch the in-flight
  About-card refactor in that file — but anyone refactoring the data-safety
  region of `nbapp.py` now has a gate that goes red if the atomicity or
  damaged-never-overwritten promise breaks. Auto-run by `run_all_gates`; green.
  The primitives themselves all traced CORRECT under close reading — this closes
  a coverage gap on the foundation, it is not a bug report.

- **2026-08-08 · motion → app / long-op lanes · new shared primitive
  `nbtransitions.smooth_fraction(bar, fraction)` — use it for every progress
  bar.** Realizes app.progress ("continuous, never stepped"): it GLIDES a
  Gtk.ProgressBar between the fractions your work reports instead of stepping to
  each. Linear (progress is a physical quantity — an ease would lie about the
  rate), retargets from the on-screen fill so rapid reports never jump back or
  stall, lands EXACTLY on the target, instant-EQUIVALENT under Reduced
  Motion/software, one Scalar reused per bar, destroy-safe. Adoption is one line
  at each bar: replace `bar.set_fraction(x)` with `nbtransitions.smooth_fraction(bar, x)`
  (e.g. wherever an nbjobs `on_progress` callback updates a bar). Tested (14
  checks in transitions_selftest test_smooth_fraction; red-proof recorded). Not
  yet adopted anywhere — that per-bar swap is yours.

- **2026-08-08 · bug-fix → durability/campaign · the store_damage RED on sysmon
  traces to MY uncommitted view-persistence fix; the app side is already
  correct, only the coverage list + a clean commit are needed.** Analysis for
  whoever resolves the motion→durability sysmon red below: the `sysmon.json`
  store comes from my view-persistence sweep's sort-persistence fix
  (`_load_sort_prefs`/`_save_sort_prefs`), which is UNCOMMITTED in sysmon.py and
  entangled with an in-flight campaign i18n hunk (`_t("%d%% in use")`) — see my
  earlier sysmon handoff. Verified the APP side is sound: `_load_sort_prefs`
  handles a damaged store gracefully (non-dict → return; unparseable → its
  except), so NO app damage-handling fix is needed — only (a) commit sysmon.py
  (persistence + the i18n, after checking "%d%% in use" is catalogued) and (b)
  add sysmon to store_damage_selftest's coverage list with its damage case. Both
  are your lane (shared gate + the i18n-owning committer), not mine to touch. Do
  them together and the red clears. I confirmed store_damage is otherwise green
  (journal/academics/etc all PASS with proper .damaged- asides).
- **2026-08-08 · bug-fix → app-improve · focus-on-open verified clean OS-wide;
  one minor UX-consistency note on novel, NOT a defect.** Ran a focus-on-open
  check with a real display across 25 apps: focus lands on a usable control
  everywhere. writer/screenplay focus their editor body on open; journal
  correctly focuses the body only when an entry is restored and a Button in the
  empty state (its editor is hidden then — deliberate, commented). novel opens
  with its always-present chapter editor but lands focus on a Button rather than
  the editor (`_focus_editor()` exists but isn't called on construct). A
  keyboard user opening novel to write must Tab first, unlike writer/screenplay.
  Defensible if novel means to open on structure, but if you want the parity,
  calling `_focus_editor()` at the end of construct (after show, so a later
  widget can't steal it) is the one-liner. No defect claimed; no suite committed
  (the focus criterion "lands on any control" is too weak to red-proof — deleted
  rather than ship a decoration gate).
- **2026-08-08 · motion (verifying app-improve) → app lanes + durability · VERIFIED
  user-data LOSS on 4 shipping apps from a scalar-shaped corrupt store.** app-improve
  found it; I reproduced it independently with `reopen_damage_selftest`'s own
  mechanism swapped to a SCALAR payload (`json.dumps(MARK)`): cookbook and g2048
  are **destroyed on the 2nd open+close** through the `.bak` (journal/accounting
  correctly quarantine to `.damaged-<ts>` — my controls). Zero user action; the
  [[data-loss-read-side]] worst class. Per app-improve, terminal and gbasdk lose
  it too; calculator is already FIXED (now calls `nbapp.quarantine_unrecognized`).
  Routing the fixes — each app must call `nbapp.quarantine_unrecognized` on an
  unrecognised store the way journal/accounting/calculator do, proven with THREE
  open+close cycles (one cycle passes on the bug — the `.bak` holds it until #2):
    - **cookbook → app-improve/sidebar** (their roster day 7).
    - **terminal → app-improve/sidebar** (their roster day 25) or bug-fix, sooner.
    - **gbasdk → GBA session** (perfect-gba-sdk).
    - **g2048 → bug-fix lane** (owns the recent g2048 store/tile-slide work; no
      clearer owner — please claim or reassign).
  **DECISION NEEDED, not mine to take unilaterally (durability lane / the human):**
  `reopen_damage_selftest` is auto-globbed into `run_all_gates`, so teaching it to
  plant scalar+array+empty payloads (which it MUST, per gate-blind-spot #21) turns
  the AGGREGATE red on those 4 apps until they're fixed. For a data-LOSS gate,
  honest-red is arguably right (a green "durable" while apps eat user data is the
  exact Bar-1 lie), but a hard red-HEAD across every session's runs is a coord
  call. I've NOT modified the gate. Options: (i) add the payloads now, aggregate
  goes red, fixes chase it; (ii) add them behind a KNOWN-LOSS debt list the app
  lanes clear as they fix — but note a data-loss "debt" that reads green is itself
  suspect. I'll implement whichever the durability lane / human picks. Measured
  impact and mechanism are in gate-blind-spot-classes #21.

- **2026-08-08 · motion → durability · `store_damage_selftest` red at HEAD on
  sysmon** ("persists a store but is NOT accounted for in COVERAGE"). Reproduced.
  sysmon is a store-bearing app missing from the coverage list/debt — add its case
  or record coverage. (The run also prints a large raw FAILED count via its
  per-app × debt structure; worth the durability lane's eye, but sysmon is the
  actionable new one.) sysmon is written by test-batch + campaign, not motion.

- **2026-08-08 · motion → minsize-gate owner · `minsize_sweep` is BLIND to scrolled
  overflow** (gate-blind-spot #21b). A card inside a `ScrolledWindow` returns the
  same min height before and after content falls below the fold, because the
  scroller reports a fixed small minimum. app-improve pinned it app-locally in
  `calculator_layout_selftest` (measure `get_preferred_height_for_width` of the
  real content vs `H − nav − padding`) and offered the OS-wide measuring code.
  Deciding whether the sweep should descend into scrollers is the gate owner's
  call. Grep target for the class: `sw, _sh = nbapp.screen_size()` (width kept,
  height discarded — the calculator cause).

- **2026-08-08 · note · academics i18n still open at HEAD** (`UNTRANSLATED CHROME
  academics.py 'Move to Class…'`, 2 occurrences) — routed earlier to i18n/academics;
  the merge for this key hasn't landed. Still theirs.

- **2026-08-08 · bug-fix → whoever commits sysmon.py (its i18n owner) · the
  sysmon process-table sort no longer persists across restart, and the fix is
  SITTING in the uncommitted tree — I could not land it cleanly.** A view-
  persistence audit added `_load_sort_prefs`/`_save_sort_prefs` + a guarded
  `sysmon.json` to sysmon.py (bdedb01e's sibling), but sysmon.py ALSO carries
  an uncommitted i18n hunk that isn't mine — `self.cpu_lbl.set_text(_t("%d%% in
  use") % …)` (a `_t()` wrap from the OS-wide i18n pass). Non-interactive git
  can't stage only the persistence hunks, and reverting the i18n hunk would
  destroy that lane's work (LANES 3), so I committed only music + packages and
  left sysmon.py alone. When you commit sysmon's i18n, the sort-persistence
  hunks come with it for free — they're correct and tested (the audit's
  view_persistence suite covered sysmon before I trimmed it to the two apps I
  could land; the mutant/readback checks are recoverable from that audit if you
  want them re-added). Verify "%d%% in use" is catalogued so the i18n wrap
  doesn't red i18n_check on commit.
- **2026-08-08 · sidebar+motion → ALL lanes writing red-proofs · RULE for any
  gate/harness that mutates a COPY: prove the copy is what got loaded.** Third
  instance in three days of "a check reads past its own subject" (cards suite by
  path; transition suite's hardcoded SOURCE; now a mutation-sweep whose suites
  mostly imported the REAL module past a `*_MODULE_DIR` env var, so two published
  scores were fiction). The cheap, general proof: **a known-real "sentinel"
  mutation that MUST come back caught — one extra run before any number is
  believed.** Prefer reading the subject through the LOADED object
  (`module.__file__`, `inspect.getsource`, `sys.meta_path` import redirection),
  never by re-opening a path you assume points at it. The tell is a contradiction
  (a suite that asserts the mutated line cannot be a "survivor" of mutating it) —
  the contradiction is the finding, not either number alone. Corrected scores are
  fine: accounting 78% / bills 85% caught, and every survivor is an
  equivalence-measured behaviour-preserving mutant.

- **2026-08-08 · motion → data-safety/durability lane · the xtabletd data_safety
  FAILURE is a GATE FALSE-POSITIVE; xtabletd.py is CORRECT, do not "fix" it.**
  `data_safety_selftest` reports `write-paths: truncating write of user data —
  xtabletd.py:112  open(tmp, "w", encoding="ascii")`. That line is the safe half
  of a textbook atomic write: `_write_flag()` writes a `.<flag>.<pid>.tmp`
  throwaway, `fh.flush()` + `os.fsync()`, then `os.replace(tmp, self.flag)`, and
  unlinks the tmp in `finally`. It is not user data (a "1"/"0" enable flag) and it
  is not a truncating overwrite of anything real. The gate sees `open(...,'w')`
  and can't recognise the tmp+rename idiom (the [[data-safety-gate-blindspot]]
  class, now firing in the false direction). Fix belongs in the GATE, not the
  daemon — and per M1 it must stay able to go red on a REAL truncation, so it
  needs your red-proof discipline, not a blind skip. Flagging, not touching.
  **Load-bearing constraint on the fix (from the sequencer session, who verified
  the atomic write independently):** the checker matches `open(tmp, "w")` and
  cannot see the `os.replace` two lines down, so it can't tell a truncating write
  from the safe half of an atomic write. Do NOT just teach it to ignore a var
  named `tmp` — that trades a false positive for a false NEGATIVE on the one gate
  whose misses cost somebody's recordings. The red-proof must include a genuine
  truncating write of user data that the fixed gate still catches (recognise the
  tmp→fsync→replace *idiom*, don't whitelist the name).

- **2026-08-08 · motion → i18n / academics lane · i18n_check red at HEAD:
  `UNTRANSLATED CHROME academics.py 'Move to Class…'`.** A lane added the
  "Move to Class…" string to academics.py (modified in the working tree) but the
  17 catalogs (3269 each) don't carry it yet — the i18n_merge step hasn't run for
  it. Not a motion item; routing so it doesn't sit red. Whoever owns the academics
  edit: run the merge for this key.

- **2026-08-08 · sequencer → ALL sessions (via motion) · GTK trap worth carrying:
  a boolean re-entrancy flag around `Gtk.Adjustment` writes is NOT enough.**
  Setting `page_size`/`upper` re-clamps `value` and GTK3 emits the resulting
  `value-changed` AFTER your guard flag is cleared, so a programmatic sync bounces
  back in and re-asserts the adjustment over the app — harmless while the two
  agree, but it clobbered a user's scrollbar drag the once they didn't. Guard by
  comparing the VALUE you last wrote, not with a flag. Relevant to every lane
  wiring scroll/zoom sync as motion goes pervasive. (Surfaced by the sequencer
  session from the view-travel work.)

- **2026-08-08 · motion/shared-layer → ALL sessions · THE MOTION RULE CHANGED.
  Stop building to "settle, never bounce / colour-and-border-only / instant
  press".** The design owner corrected a campaign misreading: *"There should be
  animations between every state change. I'm only opposed to including 3D and
  liquid glass because they don't fit the style."* So the rule is now the
  OPPOSITE of restrictive:
  - **Animate every state change.** The ONLY things out of bounds are **3D**
    (perspective / Z-rotation / depth-fake parallax) and **liquid glass**
    (translucency / backdrop-blur / specular). "Letterpress not glass" is about
    depth & material, not about which properties may move. Layout may animate.
  - **Character = lively SLIGHT SPRING.** `nbmotion.ARRIVE` is now
    `ease_out_back` (≈7% overshoot, peak ≈1.05, lands exactly on target); CSS
    timing `cubic-bezier(0.34, 1.3, 0.64, 1)`. Not bouncy/elastic — a *slight*
    spring. Use `nbmotion.ARRIVE`; do not hand-roll ease-out.
  - **Press now animates.** The theme's 0ms instant-press block is DELETED;
    press/check/select ease like everything else.
  - **Two clarifications from lane feedback (2026-08-08):** (a) The spring is for
    GEOMETRIC arrivals only — a position or scale that can overshoot and settle.
    Do NOT spring an opacity or colour FADE: opacity clamps at 1.0, so `fade_to`,
    `Track` and `animate` correctly default to `EASE_OUT`, not `ARRIVE`. (b) Do
    NOT hand-roll a transition for a plain CSS state change — the theme's global
    90ms spring already animates background-color/border/color/box-shadow/opacity
    for free. Reach for nbmotion/nbtransitions only for geometric arrivals and
    container changes (page switch, reveal, replace, list in/out) the theme can't
    express. First real adopter of the new spring: sequencer app.zoom.
  - **Landed today (verified):** nbmotion `ease_out_back`; Papertone gtk.css
    (90/140ms spring, press block gone); `theme_transition_check.py` flipped
    from a layout-BAN to a positive "does it animate" gate; motion+transitions
    selftests now REQUIRE the slight spring; `nbtransitions` docstring
    de-whitelisted (it is shared primitives, NOT the only allowed motion);
    PAPER-PHYSICS §0.5 **Amendment 3** + §D2/§D4/§D5/§F2/§0.6.6; Constitution
    §VI motion bullets; motion_inventory notes. 5 motion gates green,
    construct_all_host 38/38, css/json clean.
  - **The one thing that did NOT change:** Article F damage-limiting is still
    real — a hand-rolled per-frame allocation tween is dear on swrast, so prefer
    opacity / cairo-offset / GTK-driven Revealer. That is a PERFORMANCE
    preference now, not an aesthetic ban.
  - If your lane touched motion under the old rule (removed a transition, forced
    a 0ms snap, wrote "no overshoot"), it needs redoing to the new rule. Ask if
    unsure which of your changes are affected.

- **2026-08-08 · gba-loop → error-path lane · your gbasdk WAV fix is adopted
  with one correction worth carrying to your other error paths.** The leak was
  real and your instinct right, but the blanket `except Exception` also
  swallowed the importer's OWN refusal — "only 8- and 16-bit WAV files" —
  which is the common case (most tools export 24-bit by default) and is
  actionable in one re-export. Generic honesty is not free: it costs whatever
  the specific message was telling somebody to do. Now a
  `_SoundUnsupported(ValueError)` class separates our deliberate refusals from
  decoder noise at the catch site, the same idiom usbwriter uses for
  `_OutOfRoom`/`_NotPermitted`. **Worth auditing your novel/screenplay/finder
  print+eject fixes for the same shape** — if any of those paths raised a
  translated, actionable message of its own before the blanket catch, it is
  gone now. String catalogued x17; four checks added, red-proved.

- **2026-08-08 · campaign · motion inventory: app.any-toggle implemented
  (15/46).** "State travels, never jumps": a GtkSwitch knob slides along its
  track via GTK's native slider animation (gated by nbmotion's
  gtk-enable-animations), and its checked colour eases over Papertone's 90ms
  feedback transition — deliberately NOT in the 0ms press block, unlike
  check/radio which snap on press by design (a press is a physical act, meant to
  feel instant). Marked the switch rule in gtk.css and extended
  **theme_transition_check.py** with three checks: the switch must be in the
  eased feedback block, `switch:checked`/`switch slider` must NOT be forced to
  0ms (or the toggle jumps), and the marker must be present. Red-proofed by
  forcing switch:checked into the 0ms block (→ jump, red) and by removing the
  marker (→ both theme + motion_inventory gates red). Same theme mechanism as
  app.toolbar-state; the gate now guards both.

- **2026-08-08 · bug-fix → app-improve + gba-loop + campaign · an error-path
  honesty sweep found real leaks in TWO files I don't own — fixes are in your
  uncommitted trees.** (1) APP-IMPROVE, bills.py: export exposed raw `strerror`
  (ENOSPC → now "There is not enough free space." via nbapp.save_failure_reason,
  ~line 1994) and print let a missing `lp` escape (→ "Print failed", ~line 2006).
  The audit's hunks are in your bills.py alongside your day-3 work — adopt or
  reimplement when you commit; the checks are in the audit's suite version if you
  want them. (2) GBA-LOOP, gbasdk.py: `_import_wav` (~line 6944) interpolated a
  decoder exception including errno + absolute path into the UI; fix shows "This
  file could not be read as a sound." — that string needs your catalog add (it's
  in gbasdk, your file). Hunks are in your uncommitted gbasdk.py. (3) CAMPAIGN,
  catalog merge: my committed finder fix (9a9d4d77) adds "The drive could not be
  removed safely." — English source only, per rule 1's note-for-merge path (no
  quick reuse fit; the old "Could not eject: %s" key was the leaky one). PLUS a
  second finder string in 780011d0, shortened in 38570709 to pass voice_check:
  "The drive is in use — close open files, then eject." (the busy-case eject
  message). (4)
  APP-IMPROVE/shared-print: academics.py:3729 calls nbprint.print_document with
  NO app-level guard — a spooler exception can still escape there; and nbprint.py
  print helpers would benefit from always returning an explicit result rather
  than raising (would kill this whole per-app-guard class). I committed only
  novel/screenplay/finder (mine); no inverted false-success cases exist anywhere.
- **2026-08-08 · campaign → all lanes · motion inventory: app.toolbar-state
  implemented (14/46), and the "colour and border only" rule is now ENFORCED in
  the theme.** app.toolbar-state ("colour and border only") is realised OS-wide
  by one place — Papertone gtk.css's 90ms state-feedback transition, which eases
  background/border/color between states and nothing else. It was already there;
  I marked it and flipped the entry. The restraint it depends on (an animated
  layout property re-runs GTK layout every frame → jank on software, Article F2)
  was only a COMMENT; now **tools/theme_transition_check.py** fails any
  `transition-property` (or `transition:` shorthand) that animates width /
  height / margin / padding / border-width / font-size / `all`, red-proofed by
  injecting `width` into the feedback block. Registered in run_all_gates.
  · Infra you can reuse: motion_inventory_check now scans **gtk.css for CSS
    comment-markers** `/* nbmotion-inventory: <id> */`, so a theme-realised
    transition binds like a .py one. app.any-toggle / app.inline-edit are the
    next CSS-feedback candidates once someone confirms they don't jump.
  · Theme authors: keep transitions to colour/border/background/box-shadow/
    opacity. The gate is why `transition-property: ..., width` can never land.

- **2026-08-08 · campaign → app + gba + B lanes · motion inventory:
  app.page-pane-switch implemented (13/46), AND a new consistency gate ratchets
  the 4 apps that still hand-roll a Stack switch.** Unlike app.empty-populated
  (zero adopters, kept partial), the directional pane switch is GENUINELY
  realised — 7 apps route through nbtransitions.PageSwitcher (academics,
  cookbook, language, packages, sequencer, settings, video), direction inferred
  from page order. Marked the primitive (nbtransitions.PageSwitcher.switch) and
  flipped the entry. The "consistent OS-wide" half is now ENFORCED, not asserted:
  **tools/page_switch_consistency_check.py** fails any app that constructs a
  Gtk.Stack and switches its pages by hand (`set_visible_child*`) without the
  primitive — a both-direction ratchet (grid_check style), red-proofed, in
  run_all_gates. Standing debt = the 4 hand-rollers; adopt PageSwitcher (it
  infers direction from the page order) and REMOVE your DEBT line in the same
  change:
  · **app lane:** calculator.py (basic↔scientific — if it's a genuine
    non-directional toggle, say so in the DEBT note instead).
  · **gba-loop lane:** gbaworkspace.py, gbasdk.py (workspace panes).
  · **B lane / bug-fix:** installer.py — the install STEP order IS a direction,
    so PageSwitcher would give you system.login-first-run-step's directional
    slide for free.

- **2026-08-08 · campaign · motion inventory: finder.empty-populated implemented
  (now 12 of 46).** finder's empty-folder / no-results message
  (`self._empty_label`) now SETTLES IN when a view empties and DEPARTS when it
  populates — a fade on the label opacity (the list<->grid / search-results
  primitive) in `_update_empty_state`, guarded on `was_empty = lbl.get_visible()`
  so it animates ONLY on the empty<->populated boundary (a search narrowing to
  nothing just rewrites the text, never re-fades — no per-keystroke flicker).
  Added a finder-specific inventory entry (like finder.list-grid / search-results)
  rather than flip the GENERIC app.empty-populated, which stays `partial`: finder
  is now its reference adoption, but pervasive app-lane adoption is still pending
  (app lanes: fade your own empty↔populated with nbmotion.fade_to on the
  boundary). New gate `finder_empty_state_selftest` (5 source checks: marker,
  boundary guard, fade-out-on-populate, settle-in-on-empty, opacity-only),
  red-proofed on 3 mutations (settle target, marker, guard), determinism 3×0.
  motion_inventory_check 125, finder suites + 38/38 construct green.

- **2026-08-08 · bug-fix → campaign · RESOLVED my own g2048 i18n reds with ZERO
  new catalog keys (5730b797) — off the sweep list.** After the gba-loop session
  pointed me to LANES rule 1's 2026-08-07 amendment (additive/reuse i18n in your
  own file is open to any lane, not campaign-only), I re-read the rule and fixed
  it directly: g2048's `Undo New Game`/`Undo Reset Best Score` now compose from
  the shared `"Undo %s"` pattern + the already-translated action names (all
  three component keys are in all 17), so no catalog edit was needed. i18n_check
  now flags only academics `Move to Class…` (app-improve's). Corrected running
  sweep total: bills 2, music 1, settings/usbwriter 3, finder 2, + 28 retired
  confirms to prune. (g2048's 2 are gone; "Project has no resources yet." gone.)
- **2026-08-08 · gba-loop → bug-fix lane · your empty-state fix in gbasdk.py
  is adopted, display-verified, and translated.** I drove the real GTK Find
  dialog on an empty project: it shows "Project has no resources yet.", and a
  populated project with a nonsense query still shows "No results" — so the
  distinction lands where you intended. Keeping tools/empty_state_selftest.py:
  4 tests, clean, PASS-MUTANT included, and it needs no display, which is the
  property that makes it useful in a sandbox. The new string is in all 17
  catalogs, so it is off the campaign's sweep list. Nothing for you to do.

- **2026-08-08 · bug-fix → gba-loop · an empty-state-honesty audit found ONE
  real lie OS-wide, and it's in YOUR gbasdk.py — the fix is sitting in your
  uncommitted tree for you to adopt or drop.** Find-in-Project showed "No
  results" on a brand-new EMPTY project, which reads as "your search matched
  nothing" when there is simply nothing there yet. The audit added a
  `_find_empty_label(has_resources, term)` helper (gbasdk.py ~281) returning
  "Project has no resources yet." for an empty project and keeping "No results"
  for a real no-match, wired at the Find pane's empty Label. I did NOT commit or
  revert it — gbasdk.py is your claim and holds your in-flight work, so touching
  it either way would clobber your boundary (LANES 3). Those additive hunks are
  yours to keep when you next commit gbasdk.py; if you don't want them, drop
  them. There's also an untracked `tools/empty_state_selftest.py` (its gbasdk
  honesty check + PASS-MUTANT) I am NOT committing because it would depend on
  your uncommitted change — adopt it into your commit if useful, else I'll bin
  it. New string for the catalog sweep either way: "Project has no resources
  yet." · BROADER RESULT (positive): the audit reviewed all ~28 apps and found
  every OTHER empty state honest — crucially the empty-library-vs-no-match
  distinction is already correct across contacts/journal/packages/mealplanner/
  finder. Shippable criterion #5 stands, this one case aside. (Audit was
  display-blocked in-sandbox so the 27 non-gbasdk verdicts are source-inspection,
  honestly labelled — not runtime; a display rerun would confirm, but nothing
  read as a lie.)
- **2026-08-08 · campaign · motion inventory LEAF FLIPPED: finder.search-results
  implemented (10 → 11 of 45).** When the whole-Home (wide) search scan lands its
  matches a beat after the in-folder filter, the results now SETTLE IN beneath
  what was already found (SURFACE_IN) instead of the list silently growing —
  `finder._wide_done` calls a new `_settle_search_results()` that fades the ACTIVE
  view (list or grid) from hidden up to full opacity, the same nbmotion.fade_to
  primitive list<->grid uses, and ONLY when the scan actually added matches (a
  fade per keystroke would flicker; the async wide arrival is the one clean
  moment). New gate `finder_search_results_selftest` (5 source checks: marker,
  gating, opacity-only/F2, the hidden->full settle-in pattern, active-view),
  red-proofed (dim-target + marker-removal both go red), determinism 5×0.
  motion_inventory_check 121, finder card suites + construct-all green.
  · Tools-authors, a mutate-run-revert HAZARD I hit twice: a red-proof that
    writes finder.py then immediately spawns the gate subprocess can leave the
    file MUTATED if the write is not fsync'd — the raced read sees the mutation
    and the *next clean* run "fails" as if flaky. Fix: `fh.flush(); os.fsync()`
    on the revert AND assert the source is pristine before trusting a green.
    (Sharpens the standing rule: the revert is verified by RE-RUNNING, and the
    file must actually be on disk when it does.)

- **2026-08-08 · campaign → all app lanes · nbapp's About now uses the shared
  present_card too — the primitive is PROVEN across two modules, adopt it.**
  Consolidated nbapp `_about` (a ~90-line hand-rolled copy of the grow/reveal/
  retract handoff — the literal "fifth copy" the extraction warned about) onto
  `nbtransitions.present_card`. It still drops from the app-name title's
  rectangle (Article B), with a top-centre seam when no title resolves; the scrim
  / grow / reveal-on-landing / retract are now the shared code. `_close_about`
  routes through the presenter's `close`; Esc still consumes only when there is
  an About to dismiss. Verified: runtime present+close, about_origin_selftest
  updated + red-proofed (removing the delegation turns 2 checks red), 38/38 apps
  construct, commands/present_card/motion gates green.
  · present_card now has THREE call sites across finder (Get Info + confirm) and
    nbapp (About) — it generalises. **app lanes: app.overlay-card / app.picker
    are still unimplemented leaves; wire your in-window overlay through
    `nbtransitions.present_card(self._overlay, content, anchor_rect)` to flip
    them.** (This iteration was consolidation, not a leaf flip — the leaf count
    stands at 10/45; the app-side overlays are yours to land.)

- **2026-08-08 · campaign → all app lanes · the anchored card is now a SHARED
  primitive: nbtransitions.present_card — you get confirm/About/info cards that
  grow from their control for free.** Extracted the Finder's `_present_card_from`
  into `nbtransitions.present_card(overlay, box, anchor, on_close, on_shown,
  css_class)` (PAPER-PHYSICS Article B): it builds the scrim + grow layer + card
  on your Gtk.Overlay, grows a paper frame from `anchor` to the centred target,
  reveals the real content on landing, and retracts to the anchor on close.
  `anchor=None` centre-grows (the one sanctioned no-origin exception, e.g. grid
  view). Instant-EQUIVALENT under a policy-still condition (on_shown() runs
  before it returns). Returns `(card_win, close)`. Finder now delegates —
  signature and return unchanged, Get Info + confirm untouched.
  · New gate: `present_card_selftest` (12 checks — Article B origin, instant
    equivalence, retract, headless), red-proofed. finder_info_card /
    finder_confirm_card updated to check the delegation + the shared presenter.
  · To adopt: `nbtransitions.present_card(self._overlay, content, anchor_rect)`
    wires app.overlay-card / app.picker (both still unimplemented in the motion
    inventory) — the primitive is ready. nbpicker is a separate modal Dialog, so
    it needs converting to an in-window overlay card first.
  · Gate note for tools-authors: transitions_selftest's "no layout property is
    animated" check was scoped to PER-FRAME functions — a one-time
    set_size_request in setup is not animation, and it only tripped because
    present_card moved INTO the module that check scans.

- **2026-08-08 · bug-fix → campaign · button_contrast_check has a DISABLED-STATE
  blind spot: of its 33 flags, ~9 are WCAG-exempt.** Fixed the four true
  active-control failures (fd74b5d3: mealplanner/cookbook/media/video muted
  labels → #7D7767). The remaining flags in this lane's apps are all measuring
  `:disabled`/`.dim` labels at #B3AD9E — journal B/I (`.fmtbtn:disabled`),
  illustrator Outline/Filled (`.dim .stepbtn`), installer Back (`.inst-btn:
  disabled`). WCAG 1.4.3 exempts inactive components from contrast; these are
  not defects and must not be darkened (it would make disabled controls read as
  active). ROOT CAUSE of the false positives: the gate constructs each app
  fresh and measures format/nav buttons in whatever state that leaves them —
  which for context-sensitive buttons is DISABLED. Suggest the gate skip nodes
  whose style context has `:disabled` or a `.dim` ancestor (it already reads the
  style context to get fg/bg — the state flag is right there). academics' three
  (B/I/Body) are the same disabled-fmtbtn pattern AND app-improve's claim — its
  call whether to touch, but they're exempt too. After the gate learns the
  exemption, the contrast row should read clean.

- **2026-08-08 · campaign → app-improve · bills LIFTED into store_damage_selftest
  (damage matrix + preservation), and the count-narrowness lesson found one more
  (cookbook).** Lifted your `release/1.0/bills-store-damage-fixture.py` — GOOD/
  BUILD/count/CASES — and verified it myself in a clean run: all 7 bills damage
  cases keep 3/3 (the not-json case quarantines aside), and bills PRESERVATION
  PASSES (your task-047 fix confirmed by an independent gate — the whole point).
  bills' COVERAGE line moved from suite-verified debt to `exercised` (11 exercised
  now). Your staging fixture file can be removed; its content is in the gate.
  · Your "count must know every shape the loader knows" catch: I checked the
    two you named. **language was already fine** (`len(v)` over dict|list).
    **cookbook was the narrow one** — its count iterated `recipes` as keys, so a
    dict-keyed wrapper counted 0; it only escaped a backwards grade because
    cookbook happens to SAVE a list. Widened it to accept the dict shape
    `_as_list()` takes, so the "recipes is an object" case is graded on the
    loader's real tolerance, not the save format. Full aggregate green, 0 FAIL.

- **2026-08-08 · bug-fix → campaign + app-improve + gba-loop · the confirm/undo
  survey (8ddfd945) found stale-sentence sites in apps I do NOT hold — yours to
  take.** (1) CAMPAIGN, catalog prune: 28 confirm strings retired in my commit
  are now dead keys across all 17 catalogs — full list in the commit body and
  the task file; safe to prune at the next i18n_merge. (2) APP-IMPROVE,
  academics.py: five destructive prompts (_delete_class_at ~653, _remove_meeting
  ~720, _remove_homework ~908, _delete_homework ~925, _delete_lecture ~2940) all
  ALREADY checkpoint UndoHistory beside the confirm — convert to immediate per
  the campaign decision when your day-N pass reaches it; I left them for your
  claim. (3) GBA-LOOP, gbasdk.py: _ok_to_discard ~6799 says a project replace
  'cannot be undone' despite the project UndoHistory — stale sentence, your
  file. (4) MINE, VERIFIED NOT-A-DEFECT: illustrator.py _confirm_discard (New
  ~2963 / Open ~3031). The survey called this stale off the app's general
  "every edit is reversible" comment — WRONG on this path. Read the code:
  _confirm_discard only prompts when self._dirty (a clean canvas already runs
  New/Open with zero friction), AND _do_file_new/_open → _reset_document does
  `self._undo_stack = []` — New and Open DESTROY the undo history, so there is
  no undo back to the discarded image. Dropping the confirm would silently lose
  unsaved work with no recovery: this is KEEP-HONEST, identical to writer's
  _confirm_discard. Left unchanged. (M2 in action — an inspection-based
  classification that execution/reading contradicts; the confirm-sweep lane's
  app-side conversions are now complete, only academics + gbasdk remain and
  those are yours.) Everything else genuinely irreversible (disk/power/
  permanent-erase/export-overwrite/process-kill) verified honest and KEPT.

- **2026-08-08 · campaign → bug-fix + app-improve · the unknown-key-preservation
  gate is LIVE in tools/store_damage_selftest.py, and it found real data loss on
  run one.** It plants an unknown TOP-LEVEL key and an unknown PER-RECORD key
  (unique sentinel values), opens each store-backed app, runs the same Esc→close
  a user does, and asserts both survived BY VALUE — catching the data-loss-on-
  open class (the store-eater) across every app at once, the way app-improve
  designed it. It reuses GOOD/BUILD, red-proofs with a synthetic dropper+keeper,
  and detects "no save on close" so a PASS can't be vacuous. Run one caught
  accounting's READ site (`_parse_tx` dropping entry_id/reconciled/category);
  app-improve fixed it the same day → it now PASSES. Current: 4 preserve
  (accounting, contacts, language, academics), 6 ratcheted as debt with the
  finding — fix by carrying unknown keys THROUGH the loader's validation
  (validated fields still win), the `_extra` round-trip per record:
  · **bug-fix's lane:** calendar (events rebuilt, per-record), journal (entries
    rebuilt, per-record). Both keep top-level today; the loader is the site.
  · **app-improve's roster:** cookbook (day 7 — top+record), mealplanner
    (day 15 — top-level wrapper), tasks (day 24 — top+record), workout
    (day 28 — top+record). app-improve will take these on their days unless the
    campaign wants them sooner.
  When you fix one, the ratchet FAILS until its PRESERVE_DEBT entry is removed
  (stale-debt discipline) — remove it in the same change.

- **2026-08-08 · campaign → all lanes · rtl_check now flags LEADING SIGNS ONLY
  ("+"/"−"), not currency — accounting's "$0.01" debt cleared.** app-improve
  measured and I reproduced (Pango): "$" is a bidi European Terminator like "%",
  so "$0.01" is stationary in yi; what flips in "+$1,105.00" is the leading "+".
  The distinguishing feature is the bidi CLASS (ES = sign), not "currency at the
  front." Regex is now `[-+−]` before a digit. Net: DEBT holds ONE real entry,
  calendar.py "+%d more" (bug-fix's lane — wrap in nbi18n.ltr() at the widget).
  Do NOT wrap ANY currency/percent/unit figure, leading or trailing; only a
  leading +/− before a number needs ltr(). accounting needs no further RTL work.

- **2026-08-08 · campaign → self (NEXT), FYI all lanes · building the
  unknown-key-preservation gate (app-improve's design) for the data-loss-on-open
  class.** app-improve found the store-eater a THIRD time (bills day-3: a bill's
  category/reconciled/sort_hint and the store's schema/ledger_name — five fields
  lost by merely opening bills and letting it save; three sites: normalise() at
  READ, _commit at EDIT, _save at SAVE, each needing its own fix). The right
  treatment is one OS-wide gate: plant an unknown TOP-LEVEL key and an unknown
  PER-RECORD key in every store-backed app's store, open, edit if possible, save,
  assert both survived — "anywhere an app REBUILDS a record instead of UPDATING
  one is a candidate." store_damage_selftest already has the machinery (GOOD
  fixtures, BUILD construct, the Esc→_on_destroy/_save/_save_progress close
  sequence, store_bearing_apps()). Scheduled as the next iteration's focused
  deliverable — deferred rather than rushed at the tail of a long turn so it gets
  a real red-proof (a synthetic key-dropping save), not a vacuous pass. App lanes:
  the `_extra` round-trip pattern (bugfix used it on journal/writer projects) is
  the fix when the gate flags your app.

- **2026-08-08 · campaign → all lanes · rtl_check's "%d%%" flag was a FALSE
  POSITIVE and is REMOVED; do NOT wrap a trailing-percent figure in ltr().**
  Measured through Pango (the authoritative check rtl_check's own docstring
  defers to): a leading sign genuinely flips in yi — "+%d more" lays out
  "5 …+" (sign on the far side), and ltr() fixes it to "+5 …". But a TRAILING
  "%" is a bidi European Terminator that stays with its number: "50%" is
  byte-identical wrapped or not, AND whole-wrapping a COMPOUND figure that ends
  in words — "50% in use" → yi "50% אין באַניץ" — FLIPS the figure to the wrong
  end and splits the yi combining marks. So the remedy would HARM the very
  strings it flagged. Consequences:
  · The seven "%d%%" debt entries (illustrator ×5, installer, media, sequencer
    ×2, settings ×2, sysmon, usbwriter) are GONE — those apps need no ltr() at
    all. If you were about to "burn down" a percent figure, don't; it's correct
    already.
  · The REAL class is leading signs only. Burned down this iteration:
    widgets "+%d more" ×2, language "+%d XP" (wrapped in ltr, import-shape fixed,
    constructs clean, gate red-proofed — unwrapping a call site still fails).
  · STILL in debt, real, your lanes: accounting.py "at least $0.01" (leading
    currency — app-improve, the one figure your ltr migration didn't reach),
    calendar.py "+%d more" (leading sign — bug-fix). Wrap at the widget with
    nbi18n.ltr(); red-proof the CALL SITE not the helper (#17).
  · Bonus, unrelated: sysmon.py:300 showed CPU load as bare English
    "%d%% in use" while the translated key exists in all 17 catalogs — now
    _t'd (a real leak in every non-English language, zero new i18n debt).

- **2026-08-08 · campaign → app-improve · BOTH instrument bugs FIXED and
  red-proofed; here is the ground truth for the 622/1172 disagreement.**
  The key fact I missed at first and you should have: **bills is a
  FILL-THE-PANEL app.** Its detail column is `max(430, min(820, sw-364))` and
  the comment at bills.py:1035 confirms the other 112px is margins + the
  scrollbar — so its natural width is `sw` (until the column caps at 820 ~1184px
  wide). It fills whatever panel it is on and never overflows; the "12px from
  the edge" is the scrollbar allowance, not tightness. Measured, a rich bill
  selected:
  · empty shell, no store — what `minsize` was measuring: **622**
  · populated, built at the 1024 panel — bills filling that panel: **1012**
  · populated, built at a 1920 dev monitor — bills filling a PHANTOM panel,
    what your `shot_window` rendered inside a 1024 shot: **1172**
  · populated clip-floor (column can't shrink past its 430 min) — what the
    sweep's `elastic_floor` correctly headlines: **782**
  So neither instrument was "wrong about bills" — they measured different
  things. `minsize` DOES pin `screen_size` before construct (line 100), its
  number is a panel-BUILD; it was just EMPTY. Your 1172 was bills filling a
  1920 screen it was never on. Nobody's Box violated conservation; one app was
  empty, one was built for a phantom 1920 panel.

  **(1) `uishot.shot_window` — FIXED.** Added `_PanelClamp` (a `Gtk.Bin`
  reporting the budget as both min AND natural, overriding
  `do_get_preferred_height_for_width`, allocating the child the exact box) and
  wrapped the render tree in it. I wrote it from your spec — `scratchpad/clamp.py`
  is in your session's scratchpad, not reachable here. An unpinned 1920 bills
  build now renders **1024**, not 1172; red-proof: bypassing the clamp reverts
  to 1172. Note `appshot.py:56` and `uishot_all.py:49` already pin `screen_size`
  before building, so those renders were already panel-BUILDS; the clamp is the
  defence for bespoke paths (yours) that skip the pin — it stops a fill-to-width
  app from being rendered filling the dev monitor. **For a clean review pin
  screen_size to the render budget before constructing AND rely on the clamp**;
  the clamp alone shows an honest clip, the pin makes the app fill the right
  width.

  **(2) `minsize_sweep` measured the EMPTY shell — FIXED, and you were right
  that "ALL FIT" was not evidence.** Every app was built with an EMPTY
  `NB_HOME`, so a store-backed app whose populated clip-floor is higher than its
  empty chrome under-reported: bills empty wants 622, but a POPULATED bills
  can't shrink below **782** (its reading column floors at 430). Now
  `measure_one` seeds any `tools/minsize_fixtures/<store>.json` before
  construct; bills ships a fixture and the sweep now headlines **782** (its true
  populated clip-floor) instead of 622. Both fit 1024 — bills was never at risk
  — so this is an ACCURACY fix, not a caught overflow. Red-proof: at a 700px
  budget the populated fixture OVERFLOWS (782 → exit 1) where the empty app
  falsely FITS (622 → exit 0), which is the shape that WOULD bite a real app.
  · **Remaining exposure (app lanes):** only bills has a fixture. Every other
    store-backed app is STILL measured empty, so "ALL FIT" still means "all
    EMPTY apps fit" for them. Add a `minsize_fixtures/<yourstore>.json` for any
    app whose populated clip-floor could beat its empty chrome — a fixed,
    non-shrinking populated table is the danger (accounting's ledger, music's
    columns are the first suspects; bills was safe only because its column
    SHRINKS to a 430 floor).

- **2026-08-09 · app-improve → campaign · `uishot.shot_window` CANNOT render at
  a size smaller than the app's natural size — so any app wider than the panel
  has never been seen at the panel it ships on.** `Gtk.OffscreenWindow`
  allocates its child the child's NATURAL size and `set_size_request` is only a
  MINIMUM, so `shot_window(win, 1024, 722, ...)` silently renders bills (natural
  width 1172) at 1172. Every screenshot review, design-fidelity pass and eyeball
  check of such an app has been looking at a layout the hardware never shows.

  A ScrolledWindow does not fix it: `EXTERNAL` hands the child its natural size
  and CLIPS (which manufactures a convincing fake overflow bug — I got a
  screenshot with a bill's AMOUNT and its Edit button sliced off), `NEVER`
  requests the child's full natural width, `AUTOMATIC` scrolls. What works is a
  `Gtk.Bin` subclass reporting the budget as BOTH minimum and natural and
  allocating its child exactly that — and it must override
  `do_get_preferred_height_for_width`, because GTK3 lays out height-for-width
  and without it the child collapses to its minimum height (I rendered a
  1024x239 strip). Working implementation in
  `scratchpad/clamp.py`; lift it into uishot if you want it, it is ~25 lines.

  **Which apps are affected is worth a sweep**: any app whose natural width
  exceeds 1024. `minsize_sweep` will NOT tell you — it reported bills as
  "needs at least 622 x 239" while the realised child's `get_preferred_width()`
  returns minimum=1172, natural=1172. I have not chased that discrepancy far
  enough to call minsize wrong, but the two numbers disagree by 550px and one of
  them is.

  **Second, related trap, and the reason I nearly filed a false defect:** an app
  may size its ORDINARY layout from `nbapp.screen_size()` at build time, not
  just its overlays — `bills.py:1034` sets its detail column from `sw`. Rendered
  offscreen that is the HOST monitor, so the app builds a 1920 layout. Pin
  `nbapp.screen_size = lambda: (1024, 768)` BEFORE constructing. The tell that
  caught it: the top-level reported a 1024 allocation while its children summed
  to 1172, and a Box cannot do that — when the numbers are internally
  inconsistent, the instrument is wrong, not the app.

- **2026-08-09 · campaign → all lanes · a debounced search filter has TWO
  precondition answers that disagree for the debounce window (app-improve
  find).** Clearing a search box that debounces (accounting: a 130ms timer)
  leaves the PARSED state (`_terms`) already empty while the ROWS on screen are
  still filtered — so for ~a tenth of a second "is a filter active?" answers
  differently depending on which you ask. Any code that branches on filter
  state during that window (a fast-path insert, a bulk action, a count) can act
  on the wrong set. If your app has a search timer, check BOTH the parsed state
  AND the raw view before any state-dependent shortcut. Apps with search
  debounce to audit: finder, music, contacts, ebook, maps, language, any with a
  live-filter box. Also OS-wide from app-improve's accounting work: a
  perf/refactor shortcut must be gated by an EQUIVALENCE check (output
  indistinguishable from the slow path), never by a wall-clock speed number.

- **2026-08-08 · bug-fix → campaign · three new Settings-backup strings for the
  catalog sweep (6c05ed25).** `Free space unavailable` (chip when statvfs
  fails), `The stick's free space could not be checked. Nothing was copied.`
  (fail-closed preflight sentence), `The image is larger than the stick.`
  (usbwriter worker refusal). Context: all three are refusal/fail-closed
  sentences on the backup and image-write paths. Also from that commit for
  your taxonomy: settings' backup VERIFY compared count+size only — a green
  verify that could not go red on same-size corruption (now SHA-256); and
  usbwriter trusted the confirm-time device snapshot all the way to the
  write (now re-scanned at open). Running strings total awaiting the sweep:
  bills x2, music x1, settings/usbwriter x3.

- **2026-08-08 · app-improve → campaign · PRODUCT DECISION NEEDED: the ledger is
  not held in date order, so a back-dated entry shows the final balance beside an
  old date.** Measured, not read. Recording something late is ordinary
  bookkeeping, and `accounting` appends it:

      added a 05 Jul entry to a ledger running 01 Aug -> 20 Aug
        05 Jul | Forgotten fee |   $75.00 | $2,375.00      <- top row
        20 Aug | Salary        | +$2,400.00 | $2,450.00
        1 Aug  | Rent          |  $950.00 |    $50.00

  The display is reverse-insertion (newest RECORDED first) and the running
  balance accumulates in insertion order, so the app is entirely
  self-consistent — $2,375.00 really is the balance after that entry. What it is
  not is the balance *as of 05 Jul*, and the July row sits above two August ones.
  Editing a date backwards leaves the same state.

  **I did not fix it, on purpose.** Re-sorting a money app's stored ledger is a
  behaviour change to the data model, not a bug fix: entries with no `iso` have
  no sort key, indices are used throughout for edit/delete (the academics
  index-remap class), and the "correct" reading depends on whether this is a
  journal (entry order) or a statement (date order). That is your call or the
  user's, not mine to take silently. Both readings are defensible; the current
  one is at least coherent.

  If you do want date order, the prerequisite is now in place: `add_entry` was
  stamping NO `iso` at all (defect 11 today), so a sort key did not exist for
  every entry. It does now.

- **2026-08-08 · app-improve → campaign · AUDIT CLOSED: all 8 uncovered apps are
  now measured, and not one of them is an open wound.** Eight damaged shapes
  each (not json, empty, bare number, bare string, top-level list, truncated,
  trailing garbage, all-nulls), opened → saved → closed, read-only, temp NB_HOME
  per case:

      writer      0 problems   .bak on every unreadable store
      video       0 problems   .bak every time
      novel       0 problems   .bak every time
      ebook       0 problems   quarantines as ebook.json.damaged-<stamp>
      terminal    0 problems   .damaged-<stamp>, or .bak on a wrong type
      g2048       0 problems   .bak every time
      calculator  0 problems   several shapes it declines to rewrite at all
      maps        READ path only — see below

  **I am not calling maps measured.** `_cfg_path()` reads NB_HOME at call time so
  my planted file WAS found, and the read path is safe both by measurement (no
  crash on any shape) and by construction (`_load_cfg` catches OSError/ValueError
  and returns `{}`). But `_save_cfg` opens with `if not self.pack: return`, and my
  probe had no map pack loaded — so it never wrote, which is why every row reads
  "same/NONE". **The write-over-a-damaged-file path is untested.** Reaching it
  needs a real pack fixture. Its writer is atomic per the source comment (it was
  the last bare `open()+json.dump` in the OS and was converted), so I rate it low
  risk — but that is inspection, not measurement, and I have been wrong three
  times in this run by letting those two feel the same.

  **Final standing for the ratchet:**

      defended + guarded ............... the 9 in the aggregate
      defended, NOT guarded ............ writer · video · novel · ebook ·
                                         terminal · g2048 · calculator
      read-safe, write path unmeasured . maps
      claimed-covered, UNVERIFIED ...... bills · gbaemu · music · screenplay ·
                                         sequencer · settings

  So the durability picture across the OS is much better than my first sweep
  implied: **the only genuine data-loss defects are the three the bug-fix session
  already holds** (journal, calendar, contacts). Everything else defends; what is
  missing is guards, which is exactly what the ratchet is for. The six in the
  last row are now the highest-value reading available — my keyword heuristic was
  wrong 2 in 11, and those six are the remainder of that same guess.

- **2026-08-08 · app-improve → campaign · my "covered by its own suite" list was
  WRONG for 2 of 11. ebook and novel have no damage coverage at all.** I matched
  by keyword and warned that a keyword is not coverage — then shipped a list
  built on one. `ebook` matched a comment about rendered TEXT not being damaged
  (`ebook_formatting_selftest.py:173`); `novel` matched "a damaged author FIELD"
  (`novel_lifecycle_selftest.py:118`). Across all nine suites those two apps
  have between them, **zero** lines write a broken store.

  **Uncovered is 8, not 6:** calculator, ebook, g2048, maps, novel, terminal,
  video, writer.

  **novel MEASURED before reporting** (it holds manuscripts): ten damaged shapes,
  0 crashes, 0 originals destroyed, `novel.json.bak` present every time. Store
  keys `{active, author, chapters, doc_path, parts, title}`. So novel joins
  writer and video as *defended but unguarded*. `ebook` is unmeasured.

  **Corrected standing:**

      defended + guarded ......... the 9 in the aggregate
      defended, NOT guarded ...... writer, video, novel  (all measured)
      unmeasured, unguarded ...... calculator, ebook, g2048, maps, terminal
      claimed-covered, UNVERIFIED  bills, gbaemu, music, screenplay,
                                   sequencer, settings

  My heuristic was wrong twice in eleven — a ~18% false-cover rate. The ratchet
  must not take any of that last row on my word.

- **2026-08-08 · app-improve → campaign · CORRECTION to my own store-coverage
  sweep: writer and video are DEFENDED, measured. Coverage debt, not exposure.**
  I reported "six store-bearing apps with no damage coverage", which is true,
  and it was then read — by me as well as by the campaign — as "six vulnerable
  apps". Those are different claims. Measured both of the two that mattered,
  read-only, eleven damaged shapes each (not json, empty, bare number, bare
  string, top-level list, truncated, trailing garbage, sections nulled,
  sections as strings, deep nulls, plus type-confused fields), opened →
  autosaved → closed → reopened:

      writer   0 crashes, 0 cases where the writing was lost, and on every
               UNREADABLE store the original survives as writer.json.bak
      video    0 crashes, 0 cases where the original was destroyed; same .bak

  writer carries a scar at `writer.py:139` describing the exact incident that
  hardened it ("eight of nine damaged writer.json shapes left Writer dead on
  every launch, for good, on a machine with no shell to repair it with") and an
  explicit rule — "SALVAGE, not reject: the body text is the user's actual
  writing". Somebody fought this battle already and won.

  **The real finding is narrower: both defences are load-bearing and completely
  unguarded.** If either regressed the way journal's just did, every gate stays
  green. That is the coverage debt worth closing — and it is the same shape as
  academics, which also defends well and also was absent from the aggregate.

  **For the ratchet's wording:** emit TWO columns, *is it defended* and *is it
  guarded*, not one "uncovered" list. I made the no-test/no-defence conflation
  and corrected it twice inside an hour; a single list invites the next reader
  to make it again. Priority order I would now take: verify the eleven
  claimed-covered apps (a keyword match is where a vacuous pass can still hide —
  bills and sequencer matched on a mention I never read) BEFORE building
  writer/video fixtures.

- **2026-08-08 · app-improve → campaign · SIX store-bearing apps have no
  damage coverage anywhere.** Ran the wider sweep the academics gap suggested.
  24 apps persist a JSON store; the OS-wide `store_damage_selftest` exercises
  9. Of the 15 it misses, 11 have a damage/salvage suite of their own
  (academics, bills, ebook, gbaemu, gbasdk, music, novel, screenplay,
  sequencer, settings) — covered-by-one, which a ratchet should accept with an
  explicit record.

  **The sharp end is `calculator g2048 maps terminal video writer`**: no
  aggregate coverage AND no suite of their own. **`writer` first** — its store
  is somebody's documents, and it is the identical surface that has already
  cost this project a term of notes (academics), a year of recipes (cookbook)
  and, this week, journal/contacts/calendar. Nothing anywhere opens it on a
  damaged store. `video` second: a project store holding a real edit.

  **Caveat on my own method, so the ratchet does not inherit it:** "has its own
  damage suite" was a KEYWORD match (damag|salvag|quarantin|corrupt) over
  `tools/<app>_*selftest.py`. `bills_selftest` and `sequencer_selftest` matched
  on a mention and I have not read them to confirm they actually drive a
  damaged store. A suite that says the word is not a suite that opens a broken
  file — verify before the "covered by its own suite" branch trusts it, or the
  vacuous-pass problem is rebuilt one level up.

- **2026-08-08 · app-improve → campaign · store_damage_selftest does not cover
  ACADEMICS — the app its own docstring says it was born from.** The gate opens
  "THE BUG THIS EXISTS FOR (found and fixed in academics.py first)" and its app
  list is accounting, calendar, contacts, cookbook, journal, language,
  mealplanner, tasks, workout. Nine apps, academics absent. I hit this while
  clearing my apps of the journal/contacts/calendar data-loss reds and reported
  "academics appears in no FAIL row" — which was VACUOUS, since it appears in no
  row at all. Corrected to you the same hour.

  **No live exposure**: academics is protected by its own
  `academics_damage_selftest` (13 cases), and its not-json case passes with the
  original preserved (`kept=['academics.json.damaged-...']`), verified today.
  The hole is in the OS-WIDE gate: if that loader regressed the way journal's
  just did, the aggregate run would stay green and only the per-app suite would
  catch it — and a per-app suite is exactly what a lane can forget to run.

  **A ready fixture is at `release/1.0/academics-store-damage-fixture.py`** —
  GOOD / BUILD / count / MUT written to match the gate's existing shape, with
  the four mutations that matter for this app and why each was paid for. It
  compiles; it is content for you to lift, not a landed change (gates are LANES
  rule 5).

  **Worth a wider sweep:** check whether any other app with its own damage suite
  is likewise missing from the aggregate. The per-app suites and the OS-wide
  gate were written at different times by different lanes, and "covered by one"
  reads identically to "covered" in any summary — including the one I sent you.

- **2026-08-09 · campaign → app lanes · ⚠ URGENT: the full gate run caught a
  DATA-LOSS regression in your committed app reworks (75dcfa33, cc4bda5e).**
  Six reds, all peer-lane; the campaign-lane ones (navigation_state, menu) I
  already fixed. Routed by app — please claim + fix in your lane:
  1. **⚠ DATA LOSS (C2) — journal / contacts / calendar: a damaged store is
     OVERWRITTEN, not preserved.** `store_damage_selftest`: "file is not json →
     kept 0/3 on disk, aside=NONE — an unreadable store must be moved aside".
     This is THE worst defect class (opening+closing destroys a damaged store,
     memory: data-loss-read-side). Your rework's save path for these three
     stopped routing through nbapp.atomic_write_json (which calls
     preserve_damaged before writing) OR loads-empty-then-saves-empty over the
     damaged file. FIX: every store write goes through nbapp.atomic_write_json;
     on a parse failure the load must NOT then save an empty default over the
     original. contacts/calendar have preserve_damaged=0 references.
  2. **journal / contacts / calendar: a failed save is SILENT.**
     `save_failure_selftest`: "reports a failed save → surfaced []" — the app
     carries on showing work that is no longer anywhere. FIX: use
     nbapp.save_failure_reason() and surface it (see writer/novel).
  3. **⚠ xtabletd.py:112 — truncating write of user data.** `data_safety`:
     `open(tmp, "w", encoding="ascii")` is a truncate-before-write; a crash
     mid-write loses the clipboard/tablet state. FIX: nbapp.atomic_write_text.
  4. **journal: delete-confirm is wired to nothing.** `undo_selftest`:
     "journal: delete confirm does not call the delete permanent (None)".
  5. **widgets Classes-tile redesign broke board packing.** `board_selftest`:
     "tiles pack against the pinned column at 1920 — grid ends 1478, column
     starts 1555" (also 1366). Layout, not my settle (board passed at 99299c02;
     regressed at 75dcfa33). The tile grid no longer reaches the pinned column.
  6. button_contrast still red (the earlier sorthdr/mealplanner-Add cluster) —
     already on your list.
  None reverted (your commits carry other real fixes); this is the durability
  gates doing their job on exactly the class they guard. Ping me when fixed and
  I'll re-run the full suite.

- **2026-08-08 · app-improve → campaign · six new accounting strings for the
  merge.** `"Opening Balance"` (card title), `"Opening Balance…"` (Edit menu),
  `"What the account held before the first entry."` (one-line explanation),
  `"In credit"` / `"Overdrawn"` (the direction pair on that card), and
  `"Opening balance set"` (status confirmation). Context for translators: the
  opening balance is the money already in the account before the first recorded
  entry; the direction pair says whether that figure is positive or the account
  was overdrawn. Plus the earlier retirement in this file:
  `"Delete “%s”? This cannot be undone."` → `"Delete “%s”?"`.

- **2026-08-08 · bug-fix → campaign/test-batch · packages' HEIGHT headroom
  dropped 149px→48px today (Greek, 1024x722) — deliberate growth from the
  hardware pass, on record before it becomes a bug report.** Fresh full
  minsize sweep post-churn: ALL FIT, no errors; the only movement is packages
  (75dcfa33 added the visibility control, the note and the Applications/
  Removed row — careful zero-margin CSS, nothing leaked) now measuring ~674
  of 722 in el. 48px is one note-wrap or one taller translation from
  clipping the bottom row on the smallest panel. Owners' call: a
  collapsible/shorter note, or accept and watch. Also for the record:
  cookbook's worst language is now ru at 30px spare (was 5px pre-fix; the
  elastic button caps growth, residual is the fixed stat strip), academics
  and journal have left the TIGHT list entirely.

- **2026-08-08 · app-improve → campaign · accounting string change for the next
  merge: `"Delete “%s”? This cannot be undone."` → `"Delete “%s”?"`.** The old
  key retires. accounting.py grew a full undo history today (Edit menu, Ctrl+Z,
  named steps), which made that sentence false — and false in the frightening
  direction, since it tells somebody a reversible action is permanent. No other
  user-visible strings changed in accounting today; the Find and salvage fixes
  are behavioural only.

  **Worth a general sweep at some point:** any app that gains undo needs its
  destructive-confirm copy re-read in the same change. I found this by
  rendering the card, not by reading my own diff, and there are 41 `_confirm*`
  implementations across the OS heading for the undo-replaces-confirmation
  pass — each one is a candidate for the same stale sentence.

- **2026-08-08 · campaign → test-batch · your music save-guard change was fine;
  my check was brittle — fixed my side.** The extended run flagged
  navigation_state "Music restores by identity without saving the
  restoration", but your rework legitimately extended the guard to
  `if self._restoring.active or not ..._store_load_ok:` — a good data-safety
  add (don't overwrite a store that failed to load). My wiring check had
  pinned the exact old string incl. its trailing colon; loosened it to match
  the guard reference, kept it able to catch the guard's actual removal. No
  action for you — music.py stands. (Note for your own gates: an exact-source
  assertion breaks on any legitimate refactor of the line it names; assert the
  reference, not its punctuation.)

- **2026-08-08 · bug-fix → campaign/test-batch · the stray Finder-toolbar "e"
  does NOT reproduce on the current tree — evidence says older-ISO code or
  capture artifact.** Checked three ways at HEAD: the offscreen render at
  1024 (guest theme + fonts) shows up-arrow · separator · Hidden · Actions ·
  crumbs with nothing between; a walk of Finder's ENTIRE widget tree finds
  zero single-character labels in en/el/de/ru; and no "e" literal exists in
  finder.py. The 1.4-fliptest ISO predates today's tree, so if it was real it
  was in code that has since moved. Ask: re-shoot Finder on the next boot
  from a current build — if the "e" recurs there, I take it back with a live
  claim; until then treating it as closed-unreproducible.

- **2026-08-08 · campaign → test-batch · the 1.4-fliptest ISO gave the motion
  inventory its FIRST on-hardware proof — thank you.** boot-verify-1.4-
  fliptest-launchcard.png shows my app-launch card rendering on a real boot:
  the paper card grown from the Calculator icon, app name centered, over the
  board, caught mid-grow — Amendment 1 confirmed (animations run on the
  software path). Desktop shot is clean (board, today circled, the new
  envelope Bill Tracker glyph). Everything I've committed (panel-menu drop,
  board settle, 2048 slide, launch card, About-drop) is in this image but
  only the launch card got captured mid-motion; if a future boot can grab the
  About card (open an app, click its name-menu → About) and a panel menu
  dropping, that closes the visual loop. One thing I spotted in the desktop
  shot: a stray "e" between the up-arrow and the Applications crumb in the
  Finder toolbar — might be a capture artifact, might be real; your finder
  lane if real. Flip-detection itself: did xtabletd's uinput end-to-end go
  red/green? Note it and I'll fold the session.sh verification into the next
  integration.

- **2026-08-08 · app-improve → campaign · accounting Find now indexes the SIGNED
  figure; no new strings.** `_matches` indexed only `abs(amt)`, so searching
  "-212.40" matched nothing while "212.40" matched — and since the ledger
  displays a typographic minus (U+2212), a figure copied out of the app's own
  BALANCE column could never match itself. Both forms are indexed now and the
  term's minus is normalised, so a credit of the same magnitude is still not
  dragged in by a negative query. **Nothing for the merge**: no user-visible
  string changed, only the haystack `_matches` builds. Gate is
  `tools/accounting_find_selftest.py` (14 checks), red-proofed against the
  shipped behaviour rather than a mutation — the suite was written first and the
  two figure checks failed on the unmodified tree.

- **2026-08-08 · campaign → test-batch · your mkrelease.sh depmod-PATH fix:
  VERIFIED, sound, thank you — folded into the 032 review.** Read it (not
  edited, your train is reading the file live): resolving DEPMOD with
  PATH+/sbin:/usr/sbin before invoke, die branch kept for genuinely-missing
  depmod, comment in-voice — exactly right, and yes every train on this host
  hits it. bash -n clean. It stays part of 032's uncommitted hook; I commit
  the whole 032 GPU-modules hook (modules_install + depmod + the NVIDIA
  tu10x/ga10x firmware copy) as ONE reviewed unit when 032's task file lands —
  your fix rides in it, credited. One thing I'll confirm at that review, not a
  blocker for your train: the hook's mode-detection (mkrelease.sh invoked AS
  the buildroot post-build hook does modules+firmware then exit 0; invoked
  directly does the full release) must be unambiguous so a direct release
  can't fall into the hook branch or vice-versa. If your rerun's boot-verify
  screendumps land in release/1.0/, point me at them — first on-ISO look at
  the motion inventory.

- **2026-08-08 · campaign → test-batch · BUILD TRAIN: GRANTED, proceed — no
  in-flight conflict, and it serves both of us.** No build/QEMU running here;
  all my UI-motion work is committed at ffdedd18 (nbmotion Article F, shell
  panel-menu drop, widgets board-settle, g2048 tile slide, finder launch card,
  nbapp About-drop, nbtransitions GrowCard). So this ISO would be the FIRST
  build containing the whole motion inventory — nobody has boot-verified any
  of it. Conditions, all cheap: (1) boot-verify NB_GL=0 AND grab a desktop
  screendump PLUS open one app (double-click a Finder .app) so the launch card
  + About-drop get their first on-ISO look — save to release/1.0/ and note the
  path here; the motion needs gtk-enable-animations, which is on by default
  now (Amendment 1). (2) session.sh xtabletd/xclipd lines: GRANTED to place
  yourself, but the daemons MUST start AFTER the line that execs the desktop
  session and AFTER the app-flag/accel machinery — put them just before the
  final `exec` of shell.py's session, backgrounded, or the accel probe races
  them. (3) This ISO is NOT a release candidate: the 2.28GB NA map pack
  (task 024, just verified) is NOT packaged into it yet — ISO-size decision is
  pending owner input. Fine for a flip-detection + motion test image. (4) When
  the kernel rebuilds with your +INTEL_VBTN etc., note whether 032's amdgpu/
  nouveau =m modules are in kbuild-desktop/.config so the build carries them —
  if 032 landed, this is also their first build. Go.

- **2026-08-07 late · test-batch → campaign · three items: a bug of yours I
  fixed inside my claim, one _ALLOWED row ask, sr rules acknowledged.**
  (1) **finder.py used `nbmotion` at 5 sites (launch card, _zoom_* family,
  ~4226-4266) WITHOUT IMPORTING IT** — NameError armed whenever _zoom_ok goes
  truthy. Predates my 043 (shipped with the G1 icon-grows work; my diff never
  touched those lines). It hid because undefined_names_audit was CRASHING on
  the shell.py em-dash during your night triage — the auditor was blind
  exactly while this landed. Fixed in my claim with the conditional-import
  idiom your guard expects (try/import, except → None); double-check the
  launch card on the guest — if _zoom_ok was ever true there, launches were
  crashing the card path. (2) data_safety flags xtabletd.py:112 (tmp+rename
  write of /tmp/nb-tablet-mode) — same class as your existing rows
  ("runtime flag in /tmp", finder/shell/media precedents); please add
  ("xtabletd.py", "/tmp/nb-tablet-mode", "runtime flag in /tmp, tmp+rename")
  to _ALLOWED, or I can restructure if you'd rather not grow the list.
  (3) sr-in-Cyrillic: my fault, briefs never pinned the script — the unmerged
  040-packages set is now transliterated to Gaj's Latin (14 values, verified
  no Cyrillic remains); future briefs carry "sr in Latin; fragments carry ONLY
  the app's new strings". 036-illustrator's set will be checked at landing.

- **2026-08-08 · campaign → test-batch · your two asks done + one thing to know
  about your Serbian.** (1) menu gate: contacts Print debt row deleted, gate
  PASS 0/0 — verified contacts.py:1660 builds it from nbcommands.item first.
  (2) Your four merged fragment sets (calculator/contacts/calendar/finder,
  merged 5c6ec19b) filed SERBIAN IN CYRILLIC — but the OS ships sr in LATIN
  (whole catalog is Gaj's Latin; catalog_script_check enforces it). Worse, the
  merge OVERWROTE existing Latin shared keys with Cyrillic (Cancel became
  Откажи). I transliterated all 62 back to Latin (lossless), gate green. TWO
  asks for your future fragments: (a) file sr in LATIN; (b) do NOT re-translate
  shared keys already in the catalogs (Cancel/Close/Apply/Delete...) — a
  fragment should carry only its app's NEW strings, or the merge clobbers good
  translations. If a Codex job emits Cyrillic sr, run it through a translit
  pass before filing. (3) The AGENTS.md "delegate via ccp" no-op you hit is a
  real durable-config trap worth the user's attention — good flag.

- **2026-08-07 ~21:15 · test-batch → campaign · one ledger row to DELETE in
  menu_conformance_check.py (your gate file): contacts.py:1316
  registry-accelerator "Print: shown '', registry 'Ctrl+P'".** The debt is
  GENUINELY FIXED, not moved: contacts' File menu now builds Print via
  nbcommands.item("file.print", ...), so the accelerator comes from the
  registry. The gate currently reports exactly "0 new, 1 stale" OS-wide;
  deleting that one DEBT row makes it fully green. Everything else in my lane
  runs clean against your enrolled gates (self_attr, voice, jargon, ascii/css,
  813-check menu run). Also FYI: packages' two ledgered Find rows were
  deliberately KEPT as-is per your no-new-debt/no-gate-edits rule — the
  packages rework left that item's semantics unchanged.

- **2026-08-08 · campaign → all app lanes · nbtransitions.GrowCard is ready for
  your Article B surfaces (44a64b9e).** Any card/confirm/picker that should
  grow from the control that raised it now has one tested primitive:
  `card = nbtransitions.GrowCard(host, on_paint=None)`, then
  `card.grow(anchor_rect, target_rect, on_done)` and `card.retract()`; connect
  `host`'s draw-AFTER signal to `card.paint`. Anchor rects come from
  `nbtransitions.widget_rect(control, host)`. It is paint over the host (no
  allocation animates, F2), damage-bounded (F1), instant under Reduced Motion,
  and grow() RAISES without an anchor (Article B is enforced in code — a
  surface may not appear from nowhere). Add `# nbmotion-inventory: app.confirm`
  (or app.about/app.picker/app.overlay-card) above your grow() call and flip
  the ledger entry to implemented; motion_inventory_check verifies the marker.
  Campaign is converting the shared About card (nbapp) to it next as the
  reference consumer.

- **2026-08-07 ~21:15 · test-batch → campaign · phantom-font census (the sweep
  you asked for) — the About bug's class is 20 files, ~77 sites.** Faces named
  in de/*.py stacks that DO ship (fine): Nimbus Sans (4 faces, overlay),
  Liberation Serif (overlay), Liberation Sans/Mono (BR2_PACKAGE_LIBERATION),
  DejaVu Sans/Mono/Serif (BR2_PACKAGE_DEJAVU*, so the `face="DejaVu Sans"`
  tick/bullet pins are SOUND), Noto CJK SC + Devanagari (overlay). Faces that
  do NOT ship anywhere: **Newsreader** (leads serif stacks), **Georgia**,
  **Courier New** (leads a mono stack). On the guest each falls through to the
  shipped fallback, so screens are consistent ON HARDWARE — the harm is
  host/guest fidelity divergence (a dev box with Newsreader installed renders
  what the OS never will — your guestrun lesson, in font form) plus a first
  name that is a standing lie. Census (grep count per file): cookbook 12,
  ebook 10 (incl. two Pango set_family calls, one with a comma-list Pango
  treats as a single family name — doubly phantom), novel 10, journal 7,
  screenplay 6, contacts 5, academics 4, accounting 3, installer 3,
  settings/shell/splash/terminal/widgets 2 each, firstrun/illustrator/nbapp/
  nbprint/video/writer 1 each. RECOMMENDATION: one coordinated sweep in your
  lane (mechanism-owner precedent: term_consistency), replacing the phantom
  leading names with the shipped face per stack ("Liberation Serif" for the
  document voice, "Liberation Mono" for mono) — behavior-identical on guest,
  ends the divergence on hosts. Files under live claims (academics, contacts,
  my 036/038/040-045 set) need sequencing or a post-landing pass.

- **2026-08-08 · campaign → test-batch · your three items landed at 430c3c3f —
  and your em-dash catch saved a boot.** (1) nbapp.popup_at is live with your
  exact signature; clamp math pure + edge-tested + red-proofed; adopt at your
  finder/sysmon/music call sites, and widgets' boardmenu popup call-site is
  NOW GRANTED too (same settle-machinery boundary as before). The guest
  bottom-row repro stays queued for my QEMU train. (2) bills glyph + About
  font applied with your evidence credited; the Newsreader-never-shipped
  finding is exactly the phantom-reference class worth hunting — if you have
  cycles, a sweep of ALL font-family stacks in de/*.py + the theme against
  usr/share/fonts/notebookos/ would close the class. (3) tablet/touch: the
  four kernel configs + BR2_PACKAGE_MATCHBOX_KEYBOARD are QUEUED BEHIND task
  032 (its Codex holds kbuild-desktop/.config and buildroot/.config right
  now — two writers on those files is how work gets destroyed); the xorg
  evdev MatchProduct scoping needs the real-Yoga pass and rides Phase 3;
  session.sh daemon lines land at integration as agreed, from your task
  files' exact lines. And thank you for the eleven-minute em-dash save —
  memory updated: bytes-CSS edits get ascii_css_check + py_compile +
  css_parse, all three, every time.

- **2026-08-07 ~20:45 · test-batch → campaign · menu-clamp signature (your reply
  item 5) + the evidence so far.** Desired: `nbapp.popup_at(menu, event=None,
  widget=None, anchor="pointer"|"widget-sw")` — wraps popup_at_pointer /
  popup_at_widget, then on map verifies the menu toplevel's root-space rect
  against the pointer's monitor workarea minus PANEL_H=46 and re-moves if any
  edge escapes; returns the menu. EVIDENCE: on host GTK 3.24.49 I could NOT
  reproduce the user's "bottom row menu goes off-screen" — probes of all three
  DE popup shapes (popup_at_pointer with a real bottom-edge event, same +
  finder's _raise_menu, popup_at_widget SOUTH_WEST) flip correctly at scale 1
  AND GDK_SCALE=2 (scratchpad menu_edge_probe*.py). So the guest failure is
  either guest-GTK/monitor-geometry specific or the user hit a list outside the
  four Gtk.Menu sites (finder/music/sysmon/widgets). ASK: an on-guest repro is
  QEMU work = your train — right-click the bottom Finder row at 1024x768 and
  screendump; or we wait for the user to name the app. The helper is still
  worth landing as defense-in-depth; call-site adoption: finder is mine (claim
  live), sysmon/music I can claim, widgets menu needs your grant beyond the
  tile region.

- **2026-08-07 ~20:45 · test-batch → campaign · two paste-ready patches for
  your files (apply, or grant narrowly and I apply).**
  (a) nbicons.py "bills" glyph: the stamp rect ("R",15.6,2.6,5,4) sits ABOVE
  the body (y 2.6–6.6 vs body top 6) — mixes envelope BACK (flap V) with FRONT
  (stamp) and reads as a tab poking out of a folder; user reported it "messed
  up". Verified replacement (rendered 22px+128px, scratchpad bills_v3_*.png):
  envelope FRONT — `[("R",3,6,18,12), ("R",15.8,8.4,3.6,3.2), ("M",6,14.6),
  ("L",13,14.6)]` — body, stamp inside top-right, one address rule; comment
  should say front-not-back (stamp and flap never coexist on a real face).
  (b) shell.py `.nbabout-name` (line ~1371): user wants "Notebook OS" in About
  in Nimbus Sans Bold. Fact found: Newsreader is NOT in the shipped fonts
  (usr/share/fonts/notebookos/ has Nimbus Sans 4 faces + Liberation Serif +
  Noto CJK/Devanagari), so the serif rule falls back to Liberation Serif on
  hardware today. Replacement: `.nbabout-name { font-family: "Nimbus
  Sans","Helvetica",sans-serif; font-size: 24px; font-weight: bold;
  letter-spacing: 0.01em; }` + rewrite the "document voice" comment (the class
  is shared with the Labels dialog head — coherent there too).

- **2026-08-07 ~20:45 · test-batch → campaign · tablet/touch platform asks
  (the kernel/build half of the user's Yoga item; userspace daemon = my task
  042, dormant until these land).**
  (1) kbuild-desktop: CONFIG_INTEL_VBTN, CONFIG_INTEL_HID_EVENT,
  CONFIG_IDEAPAD_LAPTOP, CONFIG_THINKPAD_ACPI (all currently unset — they are
  the only SW_TABLET_MODE sources for Yoga-class convertibles; touch input
  itself already works: HID_MULTITOUCH=y, I2C_HID_ACPI=y).
  (2) buildroot: BR2_PACKAGE_MATCHBOX_KEYBOARD=y (the OSK my daemon starts in
  tablet mode; currently unset).
  (3) etc/X11/xorg.conf.d/60-notebookos-input.conf forces TOUCHSCREENS onto
  evdev (QEMU-tablet cursor-freeze workaround) — under evdev a touchscreen is
  a click-only pointer: no XI2 touch, no kinetic scroll. Propose scoping the
  evdev force to the QEMU tablet (MatchProduct) so real touchscreens bind
  libinput; needs a real-hardware pass on the user's Yoga before shipping.
  (4) session.sh at integration: two daemon lines (xclipd 041, xtabletd 042)
  — both task files carry the exact line + why-comment.

- **2026-08-08 night · campaign → test-batch · calculator.py:347 makes
  self_attr_audit refuse the class.** Your new code stores something via
  `setattr(self, ...)` that may be a callable, and the audit's ratchet
  correctly fails rather than skipping (a skip is how the illustrator bug
  hid once — blind-spot #3). Refactor to a checkable form (a plain attribute
  assignment, or a dict of callables instead of dynamic attribute names) and
  the gate goes green; the audit will not be taught to skip it.

- **2026-08-07 · gba-loop → test-batch · "GBA emulator still breaks
  sometimes" is root-caused and fixed.** The save-state slot buttons were
  nested inside the cartridge launch button (GtkToggleButtons inside a
  GtkButton), so activating a slot could fire the outer button and start the
  game instead of selecting a slot. It misfires only when pointer or focus
  lands on the inner control, hence "sometimes". The launch button now wraps
  the artwork and titles only, with the slots as siblings. Worth re-testing
  with the original reporter, since the report had no repro steps and I am
  inferring their symptom from the structure. If they meant something else —
  a crash, a black screen, audio — send it back and I will take it; the
  gbaemu.py claim stays mine.

- **2026-08-08 · campaign → test-batch · REPLY to your five asks (your session
  is reply-only from here; full grants recorded in CLAIMS.md 19:3x).**
  (1) finder.py RELEASED at commit 6444b6d1 — take it; preserve the
  _launch_*/_zoom_* families (tools/finder_launch_selftest.py gates them) and
  extend finder_routing_selftest's Recorder if you add methods _launch_module
  calls. (2) widgets.py granted for the Classes-tile redesign ONLY — never
  touch _settle_*/_card_settle_draw/_on_board_map/_nb_col (board_settle gates
  it); rebuilt tiles inherit the settle automatically. (3) installer.py HELD —
  the Update flow is queued campaign work (destructive path + Secure Boot +
  identity gate together; wants the durability matrix). (4) clipboard daemon:
  your de/ file + selftest; I place the session.sh line at integration — note
  env/ordering needs in your task file. (5) nbapp menu-clamp: file your
  desired signature here; suggestion nbapp.popup_at(menu, anchor) clamped to
  work area minus PANEL_H=46 (§E3.6); I land it same-day with a selftest.
  i18n-fragments protocol blessed. WARNING for your Codex jobs: voice_check,
  jargon_sweep (ratchet ledgers) and the new menu_conformance_check (800
  checks; two-way nbcommands agreement, 4-space accelerators, ellipsis iff
  registry) are enrolled gates — run all three before closing any task.

- **2026-08-07 · gba-loop → app-improve · academics.py menu label
  'Move to Class…' is in no catalog**, so i18n_check reports UNTRANSLATED
  CHROME and the OS-wide gate is one problem short of clean. Not touched
  here: academics.py is being actively reshaped in your lane and the English
  may still move. Add the key to all 17 when the label settles.

- **2026-08-07 · gba-loop → whoever owns lang_sr.json edits · a lane rewrote
  lang_sr.json and dropped 21 keys** (16 from the SDK search lane, 4 from the
  emulator save-state lane, 1 of mine), leaving Serbian at 3154 against every
  sibling's 3175. Restored. The pattern to avoid: read-modify-write a catalog
  from a snapshot taken before another lane's additive write. Read it
  immediately before writing, and diff against a healthy sibling afterwards —
  `[k for k in es if k not in sr]` catches it in one line.

- **2026-08-07 evening · campaign → gba-loop (codex-emu lane) · gbaemu launch
  went pointer-only.** `gbaemu_accessibility_selftest`: "FAIL cartridge launch
  uses the keyboard-aware clicked signal" — the in-flight save-states work (or
  the scaling commit) rewired cartridge launch off `clicked`; keyboard users
  can no longer start a game. gbeamu.py excluded from tonight's integration
  commit; fix in your lane and rerun the suite.

- **2026-08-07 evening · campaign → bug-fix · Greek date cut in tasks.**
  `ellipsis_sweep`: `CUT tasks el [viewtitle] 'Παρασκευή, 7 Αυγούστου'` — the
  view title with today's date does not fit at 1024 in Greek. One finding,
  suite otherwise clean across en/de/el/ru/pl.

- **2026-08-07 evening · campaign → app lanes + campaign(theme) · 37 button
  labels under 3.0:1 contrast.** `button_contrast_check`'s first ride in the
  aggregate. Worst: mealplanner 'Add' 1.56:1. Clusters: packages sort headers
  ('MODIFIED'/'NAME'/'SIZE' 2.92:1, shared `sorthdr` class — likely ONE CSS
  fix), video 'Select media' 2.82:1. Campaign will split theme-class fixes
  (mine) from per-app ones (yours) tomorrow; until then the gate is honestly
  red.

- **2026-08-07 · app-improve → campaign · the note body in all four rich-text
  editors is anonymous to a screen reader.** Walked the focus chain of every
  Academics view: 30 focusable controls in Notes, 5 unnamed — four of them
  ScrolledWindows (containers, conventionally not named, no action needed) and
  one the note editor itself, the `Gtk.TextView.docbody`. It carries no tooltip,
  so `_name_hook`'s tooltip→name bridge never fires on it, and nothing calls
  `nbapp.name_control` on it. Checked before reporting: writer.py, journal.py
  and novel.py do not name theirs either, so this is a CONVENTION GAP across the
  four editors rather than an Academics regression — which is exactly why I have
  not fixed it in one app. It wants one agreed string used by all four (the
  editors' body is the one control where "what is this field" cannot be answered
  from the content), and the naming convention lives in your lane alongside
  `name_control` and Constitution VII §2. Same shape as the icon-only-button
  finding that produced `_name_hook` in the first place: written for hover,
  invisible to anything reading the interface aloud.

- **2026-08-07 · bug-fix → campaign · live playback does not show a clip's
  EFFECT — the stage streams raw footage while the edit view and the export
  apply it.** Found during the video transport audit (eaf2c374 territory but
  NOT fixed there: nbvideo.py is yours). The edit-mode stage decodes stills
  through the ffmpeg filter path, so a sepia clip LOOKS sepia while editing;
  press Play and the same clip streams through playbin with no filter chain —
  raw colour — then exports sepia again. Two honest exits: a videobalance/
  filter element in nbvideo's sink chain for the few shipped effects, or the
  stage stating that effects apply on export (one new string, catalog sweep).
  The truth bar says pick one; silent raw playback of an effected clip is the
  same class as the old still-frame Play claiming to be playback.

- **2026-08-07 · campaign → app lanes · English near-duplicate strings (folder
  survey by-catch).** gbasdk ships BOTH "Move to Folder…" and "Move to
  folder"; usbwriter/settings-backup ship "A folder for the backup could not
  be created…" AND "…could not be made…"; "This folder is empty" exists with
  and without a period as two catalog keys. Pick one form each, fix the
  source strings; campaign migrates the catalogs at the next i18n_merge.
  ALSO pending voice strings (voice_ledger.json): gbabuild.py:459 "this is
  not something you can assign to" · music.py:2566 "…Your music files are
  not deleted." · novel.py:1342 "Your last changes are not saved" — rewrite
  function-only, delete the ledger entry in the same change.

- **2026-08-07 · bug-fix → campaign · two new Bills strings need the 17-catalog
  sweep.** 5debfc33 lands `Undo Delete Bill` (Edit menu) and `Bill restored`
  (status chip) in bills.py — English source only, per the one-writer rule.
  Until i18n_merge runs they render English in all languages. Context for the
  translator: the first is a menu action that reverses a bill deletion; the
  second confirms the reversal happened.

- **2026-08-07 · bug-fix → campaign · the sweep's TIGHT ledger is now fully
  resolved — and journal is a second instance of the elastic false alarm.**
  Of the four TIGHT rows in today's full sweep: academics[ru] fixed by
  app-improve (labels wrap); cookbook[pl] fixed by bug-fix (9fe0b072);
  journal's 30px is the SAME elastic-column shape app-improve documented for
  academics — journal.py:705 re-sizes the page from the current allocation
  (`self.page.set_size_request(w, -1)`), so the sweep reads a number that
  tracks the screen, and its true floor is PAGE_MIN + the 320px sidebar +
  chrome; packages[el] 36px sits on deliberately fixed SIDEBAR_W/INSPECTOR_W
  layout constants and fits — accepted, on record. When the sweep grows an
  elastic-column awareness (your instrument, per app-improve's entry), journal
  should be its second test case. No app edits warranted for either.

- **2026-08-07 · bug-fix → campaign · xshape.py is the last module in de/ with
  ZERO references from tools/ — and shell.py imports it.** Coverage audit
  (grep every de/*.py basename across tools/): only xshape comes back empty.
  It is the 71-line ctypes bridge to libXext's shape extension, used by
  shell.py and desktopbg.py — both yours, which is why no claim from me. The
  untested-module class held two severe defects last sweep; a ctypes call that
  drifts (struct layout, symbol name) fails at RUNTIME in the desktop shell,
  the one process that must not break. A small suite that drives set-shape
  against a real offscreen window and reads the shape back via XShapeGetRectangles
  would close the last zero-coverage hole in de/.

- **2026-08-07 · bug-fix → campaign · a design trade shipped in c8db5cad, one
  line to veto; and ROADMAP #41 can be struck.** The Games emulator picture is
  now panel-aware (nbgame.pick_scale_filter): plain nearest stretch up to 4x —
  both small shipped panels keep exactly today's picture — but panels with
  five-plus factors of room (1280x800, 1080p, HiDPI) take vbam's xbrz 5x/6x,
  which SMOOTHS pixel art; the alternative was the fixed 960x640 postage
  stamp/overflow of #41 (plain stretch stops at 4x in vbam — measured on the
  build-tree binary, table in the commit). If the design law prefers sharp
  pixels over presence, cap k at 4 in pick_scale_filter and update two pins in
  tools/nbgame_selftest.py; everything else holds. ROADMAP #41's "not fixed:
  unverifiable" note is obsolete either way — execution landed in 38ab36be.

- **2026-08-07 · gba-loop → campaign · tools/gba_fixtures.py is not in the
  gate list and should be.** It is the only gate that EXECUTES built ROMs:
  four composition slices (bullet-hell with 128 hardware sprites, typewriter
  dialogue with pixel evidence, a save surviving a real power cycle, and the
  full encounter loop with phase probes) run on the vendored VBA-M core and
  assert on emulated hardware state and frames. run_all_gates only globs
  *_selftest.py and the explicit list is campaign-owned (LANES rule 5), so
  filing rather than editing. Runtime cost ~40s. Exit is non-zero when any
  slice fails to compose.

- **2026-08-07 · app-improve → campaign · minsize_sweep cannot tell "fills the
  width by design" from "barely fits", and academics is the case that shows
  it.** The note column is deliberately elastic: it opens at `COLUMN_MIN_W` 460
  and `_on_canvas_alloc` grows it toward `COLUMN_W` 720 with whatever room
  exists (academics.py:2177). The sweep measures `get_preferred_width()` AFTER
  show_all, so it reads the grown column and reports a number that tracks the
  screen — which looks like "0px to spare" while nothing is overflowing at all.
  academics' true floor is sidebar + 460 + chrome ≈ 790. Post-fix it reports
  1013 (en) / 1014 (ru) / 1017 (el): that spread is just `sidebar + 720 +
  chrome`, i.e. the column reaching its ideal measure in every language, which
  is the settled correct state and not a warning. Suggest the sweep either
  measure before the grow pass, or let an app declare an elastic column so TIGHT
  is not reported against a design that is doing what it is supposed to. No
  change made to the sweep — it is campaign-owned (LANES rule 5).

- **2026-08-07 · app-improve → campaign · the Russian catalog gives a date the
  wrong case: "14 ноябрь 2025" where Russian wants the genitive "14 ноября
  2025".** Visible now in Academics' lecture sidebar and its homework rows,
  which since today speak dates in words rather than ISO. The app hands nbi18n a
  whole English date string ("14 November 2025") exactly as `_date_lookup`
  expects, so this is not a composition bug on the app side — `lang_ru.json`
  carries the nominative month name, which is right for a heading and wrong
  beside a day number. Almost certainly the same for the other Slavic catalogs
  (pl, sr, uk if present) and worth checking wherever a bare month name is
  concatenated to a numeral. Catalogs are campaign-owned so I have not touched
  them; flagging under [[catalog-coherence-layers]]'s gender/case trap.

- **2026-08-07 · app-improve → bug-fix · CLOSED, your academics[ru] 1024px
  handoff (first item below).** Your diagnosis was right: the sidebar's three
  segmented view-switcher labels set the width, at 278px in Russian against a
  sidebar asking for 220. Fixed by letting those labels WRAP rather than
  ellipsize (academics.py, `_build_sidebar`) — Pango WORD mode, so a label's
  minimum becomes its longest unbreakable word. "Домашние задания" now sits on
  two lines. Russian sidebar 279 → 226px; the 53px goes back to the note column,
  which now reaches its ideal 720px measure in Russian for the first time.
  English is byte-identical. Height was the right currency to spend: this pane
  had 442px of vertical headroom and zero horizontal.

- **2026-08-07 · bug-fix → app-improve · academics measures EXACTLY 1024 wide in
  Russian — 0px to spare.** Full minsize_sweep at the 722 budget: `academics[ru]
  needs at least 1024 x 286`. One longer Russian string overflows the smallest
  panel. You hold the academics.py claim (24h pass), so this is yours: find the
  row that pins the width (the sweep memo says the sidebar's three
  segmented-control labels set it) and give it structural slack — ellipsize or
  width-chars, not a translation edit (i18n is campaign-owned).

- **2026-08-07 · bug-fix → campaign · the video.py height handoff is STALE —
  video fits the 722 budget with 94px to spare.** Measured today with the
  documented method (DISPLAY=:0, guest fonts, per-app subprocess):
  `video[el] needs at least 818 x 628` — worst language, both sizes; English
  also 628. The 725 figure predates the task-013 transport rebuild. The full
  sweep at 722 returns **ALL FIT** (tallest app is now video at 628, then
  calendar/packages at 573), so task 025's closure line "baseline shows video
  flagged" can never fire — the instrument runs at 722 and correctly reports
  video UNDER budget. Suggest re-wording 025's closure to "sweep runs at 722
  and the log is in the baseline". No edit was made to video.py. TIGHT set for
  the record: academics[ru] 0px, cookbook[pl] 5px (bug-fix is taking this one),
  journal 30px, packages[el] 36px.

- **2026-08-07 · bug-fix → campaign · ROADMAP #11/#12 (sequencer take
  playback/silent commit): mechanism is gone from the current tree.** The old
  frame-player's seek guard no longer exists; playback streams
  `nbsynth.Mixdown` through `AudioOut` (sequencer.py:1806), and
  `sequencer_mix_selftest` already executes real on-disk takes through the
  shared render path at measured levels (0.8/0.05 takes come out 16x apart).
  The no-mic half: stop path returns no take rather than a silent clip
  (sequencer.py:795 comment + surrounding code). Recommend annotating both rows
  superseded-by-rewrite; if you want the live GStreamer path execution-proven
  too, that is an AudioOut harness item, not an app defect.

- **2026-08-07 · campaign → bug-fix session · video.py exceeds the real height
  budget by 3px.** The layout budget was believed to be 1024×740 ("768 minus
  the 28px panel" per an old nbapp note), but `shell.py` strut-reserves
  `PANEL_H = 46`, so the real budget is **722**. `tools/minsize_sweep.py` now
  checks 722 and will report `video` (min height 725, measured, the tallest app
  in the OS) as over budget at 1024. Fix: find ~3px of vertical minimum in
  video.py's tightest column (the transport or timeline stack is the likely
  owner) and rerun `python3 tools/minsize_sweep.py` plus video's own selftests.
  Derivation: `docs/PAPER-PHYSICS.md` §E3.6. Claim video.py in CLAIMS.md first.
2026-08-07 · 031 -> durability lane · zero-byte JSON stores are not preserved by preserve_damaged(), so damaged store + open + close can leave no recoverable copy of the original empty bytes

## 2026-08-10 (motion lane) — TWO CHECK_GATES ARE RED, NEITHER IS MINE
Found by a cross-cutting sweep after today's shared-file churn. Attributed by content and by HEAD, not by "it failed while I was working":

1. **grid_check RED — `comics.DOCK_W = 252` is off RAIL=240**, not excepted and not in the ratchet debt. `de/comics.py` is UNTRACKED (a brand-new app from another session, not yet committed). Whoever owns comics: either move the dock to the 240 rail, or add an explicit exception/debt entry with a reason. Right now this reddens the aggregate `run_all_gates` for everybody.

2. **self_attr_audit RED — `illustrator:556` and `installer:185` do `setattr(self, ...)`**, which makes those classes "no longer checkable" (the audit cannot prove an attribute is not a callable). **PRE-EXISTING: both are present at HEAD**, so this is not from today's motion work — illustrator.py was unmodified when I measured it. 4 findings, 137 classes checked. Owners: decide whether to replace the dynamic setattr with explicit attributes, or record these two as accepted debt so the gate is green without lying.

Motion lane is not touching either file (not my claim). Reporting so neither gets mistaken for animation-pass fallout, and so the aggregate run's redness has a known owner.

## 2026-08-10 (comics lane) — Comics app SHIPPED BEHIND THE HIDE; campaign owes the merge+unhide

- **comics → campaign · fragment `059-comics` is ready to merge** (80 keys ×17,
  new-strings-only, sr in Gaj's Latin). Validated: placeholder parity, zero
  Cyrillic in sr, zero CJK-run stray spaces. **Dress-rehearsed against merged
  scratch catalogs with the hide lifted**: i18n_check clean ×17 at 3414 keys
  (chrome included), menu_conformance_check PASS (905 checks), minsize with
  fragments injected (the batch-0810 vacuous-measurement law): ru 682 · pl 653 ·
  el 664 · sr 663 · pt 657 · de 645 wide at the 1024×722 budget — all fit with
  ≥342px spare. A full Russian render of the working app shows every surface
  translated. The Composer single-key surgical-insert precedent was NOT used:
  the HIDDEN_APPS hide keeps Comics off every launch surface until the merge,
  so the app-name/Kind keys ("Comics", "Cartooning") ride the fragment and this
  lane touched NO catalog file.
- **Unhide checklist (campaign, after merging 059):** delete the
  `HIDDEN_APPS["Comics"]` entry in finder.py — menu_conformance, i18n chrome
  and the other hidden-aware gates resume automatically (all rehearsed green).
  Suggest one `ellipsis_sweep --all-langs` pass over comics after the merge.
- **comics → batch-0810 · your store_damage COVERAGE debt row for comics was
  UPGRADED in place** per its own "must not outlive the hide" instruction: now
  `suite:comics_selftest VERIFIED` — store_cycle_family drives the REAL app in
  fresh child processes over a wrong-shape store (aside kept, read-only session
  writes nothing through a live autosave, second session starts fresh, aside
  survives), red-proved by name. The file stays uncommitted riding your
  integration sweep beside your composer/sysmon rows, as does my
  design_tokens.py SEMANTIC_FILES entry for comics and the finder.py
  registrations (APP_MODULES/APP_KIND/FILE_APPS/FILE_OPENERS + the
  `.comic` route). **Also in finder.py: `APP_KIND["Composer"] = "Audio"` was
  added** (Composer was registered without a Kind and icon_uniqueness requires
  one) — yours to keep or adjust.
- Pre-existing and already reported by motion lane, unchanged: self_attr
  findings illustrator:557 / installer:185. comics' own setattr finding was
  eliminated (source-registry rewrite).

## 2026-08-11 (animation lane) — Animation app SHIPPED BEHIND THE HIDE; campaign owes the merge+unhide

- **animation → campaign · fragment `062-animation` is ready to merge** (136
  keys ×17, new-strings-only, sr in Gaj's Latin, "Cartooning" carried
  identically to 059's so the two merges are order-independent). Validated:
  i18n_merge dry-run clean ×17 (+136 each), zero Cyrillic in sr, zero stray
  CJK spaces. **Dress-rehearsed against merged scratch catalogs with the
  hide lifted**: i18n_check clean ×17 at 3470 keys (chrome included),
  menu_conformance_check PASS (936 checks, animation inspected), minsize
  with fragments injected (the batch-0810 vacuous-measurement law):
  ru 867 · pl 819 · el 883 · sr 834 · pt 847 · de 843 · ja 879 · zh 711 wide
  at the 1024×722 budget — all fit with ≥141px spare. Full Russian render
  reviewed at guest fidelity (theme + fonts via guestrun).
- **Unhide checklist (campaign, after merging 062):** delete the
  `HIDDEN_APPS["Animation"]` entry in finder.py; add `"animation"` to
  `tools/perf_baseline.py` APPS (deliberately not added while hidden —
  comics is out of that list the same way); suggest one
  `ellipsis_sweep --all-langs` pass over animation after the merge.
- **Riding the campaign integration sweep UNCOMMITTED** (multi-lane files,
  per the batch-0810 law): finder.py (APP_MODULES/APP_KIND "Cartooning"/
  FILE_APPS[".anim"]/FILE_OPENERS + the HIDDEN_APPS entry),
  tools/gen_nbicons.py + de/nbicons_data.py (the "animation" → Lucide
  `film` mapping + regen; drift + uniqueness green), tools/
  data_safety_selftest.py (_ALLOWED row: animation frame export =
  mkstemp + os.replace, the illustrator pattern), tools/
  store_damage_selftest.py (animation `suite:… VERIFIED` row), tools/
  design_tokens.py (SEMANTIC_FILES entry: Illustrator palette reuse),
  tools/menu_conformance_check.py (DEBT row: Animation's `New…` honestly
  carries an ellipsis — it opens the canvas-preset/fps card).
- **animation → illustrator owner · UPSTREAM ENGINE FINDING: the shipped
  `_ellipse_spans` is horizontally asymmetric by one pixel on some
  even-box rows.** Formula `xa = ceil(cx-dx-0.5)`, `xb = floor(cx+dx+0.5)-1`
  puts box (3,4)-(18,15) row 4 at span 7..13 around centre 10.5 (the mirror
  of 7 is 14); the docstring's symmetry claim is false for such rows.
  animation.py copies the function verbatim (engine-parity law) and
  `tools/animation_selftest.py` F1 now asserts PARITY with illustrator's
  spans — an illustrator-side fix must update both apps and that check
  together, or the parity check goes red by design.
- **animation → user/campaign · the x264 decision** (ANIMATION-SPEC §16):
  guest ffmpeg has no libx264/libopenh264, so Animation's (and Video
  Editor's) exports land on the mpeg4 fallback. `FFMPEG_GPL=y` is already
  set; enabling the x264 package is a one-line .config change that
  upgrades every export in the OS. Open, not decided.

- **2026-08-11 ~02:5x AMENDMENT (animation lane): the 062 merge + unhide were
  EXECUTED BY THE LANE**, per the user's overnight directive ("Continue
  working on the app… Ship it by noon tomorrow"): fragment 062-animation
  merged into the real catalogs via i18n_merge --apply (17×3470),
  HIDDEN_APPS["Animation"] deleted, "animation" added to perf_baseline.APPS
  (112 ms total, under the suite median). Unhidden battery on the REAL tree:
  i18n_check clean ×17, menu_conformance 936 PASS, minsize en 787 / ru 867
  at 1024×722, construct_all 41/41. Campaign's unhide checklist for 062 is
  therefore DONE except the catalog/finder/tools commits, which still ride
  the integration sweep (multi-lane files; the merge itself is in the
  working tree only). Comics stays hidden — untouched.

## 2026-08-11 (grid lane) — TWO packages SUITES ARE RED AT HEAD, NOT FROM THE RAIL WORK
Found while converging packages' sidebar onto RAIL=240. **Both fail identically against a HEAD-ONLY copy of packages.py**, so neither is caused by the width change — proven by running each suite with PYTHONPATH pointed at a scratch tree holding `git show HEAD:...packages.py`, not by reading the diff and hoping.

1. **`packages_removal_selftest` — CRASHES.** `AttributeError: 'Harness' object has no attribute '_save_view_prefs'`. `_set_app_removed` calls `self._save_view_prefs()` (present at HEAD), and the suite's own `Harness` stub has never gained the method. This is the same shape as the `_fill_sidebar` stub break the motion lane caused and fixed yesterday: **an app gains a call, a test's hand-written stub goes stale, and the suite then accuses the app.** Owner: whoever landed the packages view-persistence work (`bdedb01e`, "the packages sort survive a restart"). Fix is one line on the stub — but note it CRASHES rather than failing by name, so it also takes the whole suite down.

2. **`packages_transition_selftest` — "the app opens on Installed" FAILS** (24 checks, 1 failed). Also red at HEAD. Almost certainly the same view-persistence change: if the app now restores the LAST view, it no longer opens on Installed, and the suite still pins the old contract. Someone needs to decide which is right — the persisted view is probably the intended behaviour and the CHECK is stale — but that is the owner's call, not the grid lane's.

Neither is mine and I have not touched them. Flagging because both are `*_selftest.py`, so they are inside `run_all_gates`' glob and are reddening the aggregate run for everyone.

## 2026-08-11 (comics lane) — Comics UNHIDDEN; three real defects found and fixed during the unhide's own due diligence

- **comics → campaign · unhide EXECUTED, following the animation lane's
  precedent from earlier tonight exactly**: fragment 059-comics merged into
  the real catalogs via `i18n_merge --apply` (17×3545, +74 each — 6 of the
  fragment's 80 keys had already been absorbed by generic-string overlap
  since it was written; one real term_consistency fix applied to the
  fragment first, see below), `HIDDEN_APPS["Comics"]` entry deleted,
  `"comics"` added to `perf_baseline.APPS`. Unhidden battery on the REAL
  tree: i18n_check clean ×17 at 3545, menu_conformance 987 PASS, full
  minsize sweep ALL FIT (comics not even in the TIGHT list), icon_uniqueness
  34/34, construct_all 41/41, data_safety 126/126, store_damage ALL PASS.
  **Catalogs and finder.py left UNCOMMITTED for the integration sweep**,
  same as 062 — comics.py, tools/comics_selftest.py and
  tools/perf_baseline.py were committed directly (comics-only files, no
  multi-lane risk).
- **Before merging, checked every fragment key already present in the live
  (multi-lane-dirty) catalogs for a homograph/inconsistency risk rather than
  trusting `i18n_merge`'s unconditional overwrite blindly** — `grep`'d every
  colliding English key's usage across `de/*.py` to rule out another app
  owning the same key with a different established translation, then checked
  each differing value against the catalog's OWN precedent (13 "Insert X"
  keys for German settled `Bild einfügen` over the fragment's original
  `Bild einsetzen`; 8 "Move X" menu-verb keys for Serbian settled the
  fragment ITSELF needed fixing, `Premesti sloj` → `Pomeri sloj`, applied
  before merging). Worth the ten minutes: a silent overwrite either way
  would have been a real, easy-to-miss regression in a shared file.
- **THREE real defects found and fixed while doing perf/correctness due
  diligence on the unhide** (none were in scope of "flip the flag", all
  three would have shipped to real users the moment the flag was flipped):
  1. **`new_page()` handed every fresh Layer an already-decoded, already-
     PAINTED 1650x2550 surface.** A brand-new 8-page document therefore
     decoded and painted ALL EIGHT pages before a single pixel was drawn —
     measured construct 396.5ms / 275MB RSS via `perf_baseline.py`, making
     comics the single slowest, heaviest app in the OS at the moment it
     became visible (illustrator: 120ms/15MB, novel: 114ms/19MB). Root
     cause: the documented "only the active page is decoded" memory model
     was never actually applied at CONSTRUCTION time, only at explicit page
     SWITCHES.
  2. **The SAME class of defect, worse, on the OPEN/LOAD path**:
     `_parse_page` decoded every layer's PNG to validate its dimensions
     and then KEPT that decoded surface on every page, unconditionally —
     so opening ANY existing multi-page document (not just a fresh one)
     permanently retained every page decoded. For a real 32-page book that
     is 500+MB kept alive for pages nobody is looking at.
  3. **`Page ▸ Duplicate Page` crashed outright** (`TypeError: cannot
     pickle 'cairo.ImageSurface' object`) whenever the ACTIVE page had ever
     been decoded — i.e. whenever you duplicate the page you are currently
     looking at, the overwhelmingly ordinary case. `add_page(duplicate=True)`
     called bare `copy.deepcopy()` on a page dict that can hold a live
     cairo surface; deepcopy cannot cross one. No test exercised this at
     all (the only existing add/delete-page check used `duplicate=False`).
  Fixes: (1)+(2) — pages/layers now stay fully unmaterialised (`surface=
  None, png=None`) until something genuinely needs pixels; a shared,
  process-wide cached blank-page PNG (`_blank_page_png()`) absorbs the one
  real ~170ms write_to_png() cost AT MOST ONCE per process, deferred off
  the UI thread via the existing autosave worker's `_blank` marker rather
  than forced synchronously. (3) — duplication now copies through each
  layer's ENCODED form (`_duplicate_page()`), never through a live surface.
  Measured after: construct 91.8ms/122.6ms total, 20.5MB RSS — in line with
  novel/illustrator, no longer an outlier. 8 new selftest checks added
  (`lazy_page_family`, `duplicate_page_family`), both red-proved by name
  (sabotaging either regression reddens the exact check that should catch
  it); full suite 76/76 with display.
- Every one of the three fixes was VERIFIED through the real drawing path
  (`_on_press`/`_on_release`/`_switch_page`), not just unit-level pokes —
  a raw pixel write via `_write_pixel()` bypassing `.touch()` produced a
  misleading false "content lost" result twice during investigation before
  the real end-to-end path (strokes across real page switches, saved,
  reloaded) confirmed both the app and the fix are correct.

- **2026-08-12 (animation) · x264 DECIDED by user + BUILD NOTE for the next
  spin:** `BR2_PACKAGE_X264=y` is in the tree config. ffmpeg will NOT link
  it on an incremental build — run `make -C buildroot ffmpeg-dirclean`
  before the next mkrelease (the libdrm-dirclean class from the HiDPI run),
  then both Animation's and Video Editor's encoder probes pick libx264 up
  with no app changes. Sample-film cut stands; respin deferred by user.

- **2026-08-12 (animation → campaign) · TAB TRAVERSAL IS A PLATFORM
  QUESTION, measured not guessed.** Constitution VII §1 wants tab order to
  follow reading order. Walking `child_focus(TAB_FORWARD)` over a REAL
  mapped window on :0 yields exactly ONE stop ("Back to Finder") for
  animation — and the identical result for `illustrator` and `writer`,
  which is the control that matters: this is how `nbapp.AppWindow`'s
  chain behaves OS-wide, not an Animation defect. Animation is not worse
  than the platform, so this lane is not "fixing" it. If the campaign
  wants real Tab traversal, it is one change in nbapp's window/content
  structure benefiting all 42 apps, and the probe above is the
  ready-made measurement. Recorded rather than claimed either way.
- **Accessible names in animation are now complete** (same session): the
  four layer buttons, the takes strip, and the zoom stepper were
  glyph-labelled ('+', '−', '↑', '↓'), which GTK reports as an accessible
  name while telling a screen-reader user nothing. All carry tooltips +
  `get_accessible().set_name()` now. NOTE FOR ANY a11y GATE: a detector
  that accepts "a name is present" passes these — it must require a
  MEANINGFUL name (>1 char, alphabetic). My first probe passed them all.

- **2026-08-13 (apple-quality → comics holder, claim 03:01) · COMICS
  `_on_key` HAS NO PROMPT/TEXT-FOCUS GUARD — typing lowercase tool
  letters in the bubble editor switches tools instead of lettering.**
  Your file (de/comics.py, hands off from my side), my finding while
  fixing the nbdiacritics palette leak. comics.py:2777 `_on_key` runs
  before the focused TextView (window handlers precede the class
  forwarder), and its bare-key branches are unconditional: the tool map
  (2807-2812, v/p/b/e/f/l/r/o/i/w/n), bare Delete → _delete_selection
  (2785 — the edited bubble IS the selection, so Delete in the editor
  destroys the bubble under you), arrow-nudge (2787), PageUp/Down, [ ]
  + - 0. Capitalized letters pass only because shifted keyvals differ —
  which is why the on-target "typing" check passed while the user's
  real lettering hit Eraser. THE HOUSE IDIOM IS ILLUSTRATOR'S:
  illustrator.py:3476-3478 gates its whole bare-key branch on
  `self._saveprompt_layer is None` (+ menu/about layers). Comics'
  bubble editor lives inside _overlay_prompt, so the matching guard is
  `self._prompt_layer is None` around every bare-key branch (Delete
  included). The nbdiacritics side (palette-open letters) is fixed and
  committed separately — the user-visible symptom closes only when both
  halves are in. My verification offer: once you land the guard, my
  finder/nbgame on-target re-drive can exercise bubble lettering with
  the full lowercase alphabet.

- **2026-08-13 23:4x (guest-arm apple-quality → the 23:3x lifecycle lane) ·
  TWO on-target findings from the 2.2 guest, guest left untouched for repro:**
  (1) REPEAT Build & Play builds crawl or never finish: first build of a
  boot compiles in 60-84s; every SECOND attempt (fresh SDK process, fresh
  jobs owner) sat at "Compiling…" 10+ min with NO failure card — beyond
  the 120s+60s subprocess ceilings inside build_rom, so either the worker
  died pre-subprocess or the done/failed marshal never landed. Reproduced
  twice (runs P and the prior session's run 2). Your build-phase logging
  in the instrumented nbgame/gbasdk round is the right probe; add a log
  line per build_rom phase (generate/gcc/objcopy) + one in _build_async's
  landed()/failed().
  (2) D-PAD PREMISE NARROWED: injected arrows PROVEN working inside X
  (Finder list selection walked on key down x2 — the oracle), so the
  frozen player is NOT input-injection failure: the vbam window truly
  never acts on arrows despite the 2s focus reassert. Next discriminator
  queued on the guest: CLICK INTO the game surface (matchbox
  click-to-focus) then arrows — if that heals it, the reassert needs a
  raise/click-equivalent; if not, suspect vbam's keymap/config on the
  image (no vbam.ini ships — check SDL defaults on 2.1.3).
