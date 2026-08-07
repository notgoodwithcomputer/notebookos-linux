# The NotebookOS Interaction Constitution

**Status:** normative. Written in Hour 1 of the fifteen-hour UX convergence block
(`docs/UX-15-HOUR-PLAN.md`). No production code was changed to produce it.

**Scope:** every user-facing surface that ships in
`buildroot/board/notebookos/rootfs-overlay/opt/notebook/` — the 58 desktop
modules under `de/`, the shell panel, the login screen, the installer and the
first-run flow.

**Relationship to existing docs:** `docs/MENU-CONVENTIONS.md` remains the
authority on menu *wording* and *File-menu shape*, and is incorporated here by
reference as Article I §1–§3. This document is broader: it covers the seven
subsystems the fifteen-hour plan names, and each article ends with the gate that
makes it enforceable rather than aspirational.

**Rule of construction.** Where an article states a rule the repository already
follows, it is a *codification* — it names the existing implementation so a new
app cannot re-invent it. Where an article states a rule the repository does *not*
yet follow, it is marked **[GAP]** and appears in the inventory in §10 with a
priority and a migration hour. Nothing in this document authorises a visual
change.

---

## 0. What this platform actually is

Every rule below is bounded by seven facts about the shipped stack. A rule that
ignores any of them is unimplementable here, however good it looks on paper.

1. **GTK3 / PyGObject, not GTK4.** `Gtk.TextBuffer` has no undo
   (`can_undo`/`undo` arrived in GTK4), there is no `GtkEventControllerKey`, and
   there is no `Gtk.Application`/`GAction`/`GMenu` in use anywhere — menus are
   Python lists of `(label, callback)` tuples returned by
   `AppWindow.menu_items()` in `de/nbapp.py`.
2. **No session bus.** There is no D-Bus, no notification daemon, no portal, no
   settings service. Cross-process agreement happens through files under
   `$NB_HOME/.config/notebook/` and marker directories in `/tmp`
   (`nbapp.APP_FLAG`, `nbapp.APP_DIR`). A preference change reaches other
   *running* apps not at all; `nbapp.a11y_set()` can only restyle its own
   process. Any contract that requires live cross-app propagation is out of
   scope.
3. **Often no compositor.** `xcompmgr` runs only when `NB_ACCEL=1`
   (see the compositor gating in `session.sh` and `de/shell.py:151`). Popup
   windows are unreliable on the bare path, which is why every menu, confirm and
   About card in this OS is drawn as a `Gtk.Fixed` layer inside the window's own
   `Gtk.Overlay`, wrapped in a `Gtk.EventBox` so it gets its own `GdkWindow` and
   actually blits (`AppWindow._open_menu`, `AppWindow._about`,
   `media.MediaWindow._confirm`, `shell.Panel._popup`).
4. **Software rendering is a first-class target.** `NB_ACCEL` is derived from the
   kernel's `[drm] features: +virgl` line, not from the presence of
   `/dev/renderD128`. On the software path a first paint can take the better part
   of a second, so `nbapp._apply_motion_policy()` turns
   `gtk-enable-animations` **off** entirely.
5. **The layout budget is 1024x740**, not 1920x1080 — the smallest supported
   panel minus the 28px desktop panel. `nbapp.TEXT_MIN = 15` exists because a
   16px accessibility floor pushes the GBA SDK 2px past 1024.
6. **One app, one process, fullscreen.** `nbapp.claim_single_instance()` makes a
   second copy of an app `os._exit(0)` before it can load — and deliberately
   without running any save path. There is no window management to specify.
7. **The file under `$NB_HOME` is the only copy.** No network, no cloud, no
   sync. Every data-safety rule below is a rule about the last copy of
   somebody's work.

---

## Article I — Commands, menus and shortcuts

### §1 Ordering (normative)

Menu-bar titles appear left to right in exactly this order, omitting any the app
does not have:

    <App name>   File   Edit   View   <app-specific…>   Help

The app-name button is built by `AppWindow._menubar()` and always comes first;
`AppWindow.menus` supplies the rest. App-specific menus named for the app's own
subject (`Format`, `Insert`, `Table`, `Layer`, `Transport`, `Cook`, `Library`)
are correct and must not be flattened into a generic `Tools`.

Within a menu, entries are grouped and the groups are separated by
`nbapp.SEP`, in this order:

| Group | Contents |
|---|---|
| 1 | Creation (`New`, `New <Thing>`) and acquisition (`Open…`) |
| 2 | Persistence (`Save`, `Save As…`) — **document apps only** |
| 3 | Emission (`Export…`, `Print…`) |
| 4 | Destruction (`Delete <Thing>…`) |
| 5 | Exit (`Close    Esc`) — always last, always present |

`Edit` opens with the Undo/Redo pair from `nbapp.undo_menu_items(hist)` when the
app has an `UndoHistory`, then `nbapp.SEP`, then Cut / Copy / Paste /
`nbapp.SEP` / Select All as `AppWindow.menu_items("Edit")` provides them.

### §2 The two File menus

Reproduced from `docs/MENU-CONVENTIONS.md` §2 and binding: an app has *documents
the user names* or *one store the app owns*, never both. A single-store app
(Academics, Tasks, Journal, Workout, Calendar, Contacts, Cookbook, Accounting)
**must not** offer New/Open/Save/Save As. A `Save` that does nothing is worse
than no `Save`.

### §3 Labels

- Ellipsis means "this will ask you something" — a picker, a dialog, or a
  confirm. `Export to PDF` (writes straight to `$NB_HOME/Documents`) and
  `Export to PDF…` (opens `nbpicker`) are different promises and must not be
  unified.
- Accelerators are part of the label, four spaces before the key:
  `Close    Esc`, `Save    Ctrl+S`. **If the app binds it, the menu prints it;
  if the menu prints it, the app binds it.** Both directions are defects.
- Title Case for menu items; sentence case for body text, labels, tooltips and
  empty states. A menu item names the outcome, not the mechanism.
- An unavailable action stays visible and greys out (callback `None`, rendered
  insensitive by `AppWindow._open_menu`). Never remove an item to disable it.

### §4 The systemwide shortcut table

These keys mean the same thing in every app. An app may not rebind one to
something else; it may leave one unbound if the action does not exist.

| Key | Meaning | Where it lives today |
|---|---|---|
| `Esc` | **Leave** the innermost thing: overlay → menu → app. Never destroys data. | `AppWindow._on_key` |
| `Ctrl+W` / `Ctrl+Q` | Close the app | `AppWindow._on_key` (suppressed when `self.term` is set, so the terminal keeps readline word-rubout and flow control) |
| `Ctrl+Z` / `Ctrl+Shift+Z` / `Ctrl+Y` | Undo / Redo / Redo | `nbapp.undo_keys(hist, ev)` |
| `Ctrl+X` `Ctrl+C` `Ctrl+V` `Ctrl+A` | Clipboard and select-all on the focused widget | `AppWindow._edit()` |
| `Ctrl+N` `Ctrl+O` `Ctrl+S` `Ctrl+Shift+S` | New / Open / Save / Save As — **document apps only** | per-app `_on_key` |
| `Ctrl+P` | Print | per-app `_on_key` |
| `Ctrl+F` | Find within the current view | per-app |
| `Ctrl+Space` | Pinyin IME toggle (`nbpinyin.PinyinIME`) — **reserved OS-wide** | `AppWindow.__init__` |
| `Alt+Left` / `Alt+Right` / `Backspace` | Back / Forward / Up, in apps with navigation history | `finder.py` `_history` / `_hpos` |
| Press-and-hold a letter | Diacritic palette (`nbdiacritics.DiacriticsPicker`) — **reserved OS-wide** | `AppWindow.__init__` |

**Esc is never destructive.** It leaves; it does not delete, discard or revert.
An `Esc` that would lose unsaved work must first route through Article IV §3.

### §5 Context menus

A context menu is a **strict subset** of the menu bar. Every command reachable
by right-click must also be reachable from a menu-bar dropdown; the menu bar is
the complete command inventory of the app. A context-menu-only command is a
defect.

### §6 Gate

**`tools/menu_conformance_check.py`** [GAP — new, Hour 4]. Static AST pass over
every `menu_items()` in `de/*.py`, asserting: title order per §1; group order and
separator placement; the group-A/group-B File shape per §2; ellipsis agreement
with the callback's behaviour where statically decidable; four-space accelerator
formatting; and **bidirectional accelerator agreement** — every `Ctrl+…` printed
in a label has a matching `Gdk.KEY_…` branch in that module's `_on_key`, and
every such branch is printed somewhere. Existing `tools/voice_check.py` and
`tools/i18n_check.py` continue to own wording and translation coverage.

---

## Article II — Documents: dirty state, autosave, recovery, undo

### §1 Truthful dirty state

An app that can lose work shows a save indicator, in the one form the OS already
uses: a coloured dot, a word, and a time — `● Saved 14:32`
(`writer._set_save_chip`, and the same chip in Journal, Novel, Cookbook,
Screenplay). The indicator states one of exactly three things:

| State | Means |
|---|---|
| `Editing` | changes exist that are not yet on disk |
| `Saved HH:MM` | every change is durable as of that time |
| a failure sentence from `nbapp.save_failure_reason(exc, path)` | the write did not happen |

The third row is the one that matters. **A save that fails silently is the worst
outcome this OS can produce**, because the app keeps showing work that is no
longer anywhere. Every call site that ignores a false/exception return from a
save is a defect, not a style issue.

### §2 Writes

- All JSON stores go through **`nbapp.atomic_write_json`**; all plain-text
  documents through **`nbapp.atomic_write_text`**. A bare `open(path, "w")` on a
  user-data path is a defect. Both do temp-file + `fsync` + `os.replace` +
  `_fsync_dir`, so a power cut leaves the old complete file or the new complete
  file and never a truncated one.
- Autosave is debounced, not per-keystroke. The house debounce is
  **900 ms** (`writer._mark_dirty` → `_autosave_fire`).
- **Every app flushes its pending autosave on destroy.** `writer._on_destroy` is
  the reference: remove every timer source it owns (`_save_timer`, `_undo_timer`,
  `_count_timer`), then call the save once, unconditionally.
- Save As is atomic and must not leave the app pointing at a path it failed to
  create.

### §3 Recovery

Three mechanisms, already implemented in `de/nbapp.py`, and an app must use the
right one:

- **`preserve_damaged(path)`** — called from inside `atomic_write_json`, so it
  covers all ~22 persistence sites automatically. A store that fails to *parse*
  is moved to `<store>.damaged-<timestamp>` before anything overwrites it, and
  one previous-good copy is kept at `<store>.bak`, once per file per process.
- **`quarantine_unrecognized(path)`** — called by the *app*, at *load* time, when
  the store is valid JSON in a shape the app cannot read. `preserve_damaged`
  structurally cannot cover this case, and the `.bak` alone is not enough: a
  blank default often outweighs a wrong-shape store holding real prose, so the
  second open destroys the last copy. Any app that can distinguish "my shape" from
  "not my shape" **must** call this on the not-my-shape branch.
- **`_bak_would_shrink`** — never refresh a recovery copy with a poorer one. It
  asks two questions, and both are load-bearing: `_payload_weight` (how much is
  in here) *and* `_loses_records` (are there fewer records than there were).
  Weight alone measurably failed in Academics.

**Loading is never all-or-nothing.** One unparseable record must not discard the
file; read what is readable, quarantine what is not, and say so. (This was the
actual defect behind a report that read as "Esc deleted my work".)

### §4 Undo

- Any app where a single keystroke can replace existing work has an undo
  history. Not optional: a select-all-then-type in Journal once replaced years of
  entries.
- The mechanism is **`nbapp.UndoHistory`** — checkpoint snapshots over the
  serialiser the app already has for autosave, not per-operation edit records.
  Typing collapses into one step on the **600 ms** `_UNDO_DEBOUNCE`; structural
  edits bracket themselves with `checkpoint(label)` / `commit()`.
- Snapshot keys beginning with `_` are volatile (caret, scroll): they ride along
  and restore, but never consume an undo step.
- Menu entries come from `nbapp.undo_menu_items(hist)` so the label names the
  action being reversed ("Undo Delete Chapter"), and keys from
  `nbapp.undo_keys(hist, ev)` so all three conventions work.
- **Undo dirties the document.** An undo that reverts to the last-saved state
  still triggers autosave; the disk must follow the screen.

### §5 Destruction

- Prefer undo over confirmation. A destructive action that `UndoHistory` can
  reverse should just happen, and say what it did.
- Confirm only where loss is *irreversible and unexpected* — see Article IV §3.
- Bulk destruction (Empty Trash, Delete Forever, format/write a disk) always
  confirms, always names the target, and always states the count.

### §6 Gate

Existing and sufficient in kind: `tools/data_safety_selftest.py`,
`tools/store_damage_selftest.py`, `tools/reopen_damage_selftest.py`,
`tools/reopen_shapes_selftest.py`, `tools/persistence_roundtrip_selftest.py`,
`tools/save_failure_selftest.py`, `tools/undo_selftest.py`.

Extension required [GAP — Hour 6]: `data_safety_selftest` must assert the
**close-time flush** (destroy → store on disk equals the model) and the
**Save-As atomicity** path, and its write-path detector must recognise
`pathlib.Path.write_text`, `io.open`, `shutil.copy*` and `os.fdopen` — the check
previously only saw `open(..., 'w')`, which is how a green gate stayed green over
a real hole. *A green gate must be proven able to go red*: every added assertion
ships with a deliberate mutation that makes it fail.

---

## Article III — Navigation, focus and restoration

### §1 The navigation model

- `Esc` leaves one level. The stack is: modal overlay → open dropdown menu →
  the app itself (`AppWindow._on_key`, and `AppWindow._close_about` before
  `_close_menu` before `close`). An app that adds a layer (a picker, an inline
  editor, a fullscreen view) **must** insert itself into that ladder by
  overriding `_on_key` and calling `super()._on_key(w, ev)` on the fall-through
  path — not by swallowing `Esc`.
- Apps with places (Finder, Ebook, Media, Maps, Music) keep a linear history
  list with a position index, `finder.py`'s `_history` / `_hpos` being the
  reference: a new destination truncates the forward tail
  (`del self._history[self._hpos + 1:]`), and Back/Forward replay with
  `record=False`.
- Back/Forward buttons are disabled, never hidden, at the ends of the history
  (`finder._set_nav`).

### §2 Focus

- Opening a modal overlay moves focus into it; closing it **returns focus to the
  control that opened it**. [GAP] Today the overlays add a layer and never
  restore focus on removal (`AppWindow._close_menu`, `_close_about`,
  `media._close_confirm` and the ~14 per-app `_confirm` twins).
- In a confirm, focus lands on the **safe** choice, so a reflexive `Enter`
  cancels. `media.MediaWindow._confirm` already does this
  (`cancel.grab_focus()`); it is now the rule.
- `Enter` activates the default (safe) action; `Esc` activates Cancel. A dialog
  where `Enter` performs the destructive action is a defect.
- A periodic refresh, a filesystem-driven reload, or a background completion
  **must not** move focus, change the selection, or reset scroll. This is the
  single most common way a live-updating list becomes unusable.

### §3 Restoration

On reopening, an app restores: the active pane or tab, the selected item, sort
and filter, scroll position, zoom, and the open document. Restoration state
lives beside the app's data in `$NB_HOME/.config/notebook/<app>.json`.

**Every delayed restoration carries a generation token.** A scroll or selection
restore posted with `GLib.idle_add` must re-check the token before it lands, or
it will apply to whatever document is on screen when it fires. `ebook.py`'s
`_doc_gen` is the reference implementation — the token is bumped where the
document changes, captured at post time, and compared in the callback
(`if token is not None and token != getattr(self, "_doc_gen", token): return`).

[GAP] `settings.py` has no pane restoration at all: `_select(self, _btn, name)`
switches pages and nothing persists the choice, so Settings always reopens on its
first page.

### §4 Gate

`tools/finder_restore_selftest.py` and `tools/ebook_lifecycle_selftest.py` are
the pattern. Required extension [GAP — Hour 7]: a **rapid-switch fixture** that
posts a restoration callback, changes the document, runs the main loop, and
asserts the callback did nothing — per app that has delayed restoration.

---

## Article IV — Feedback, errors and confirmation

### §1 Every action produces evidence

An action either changes something visible, or it says what it did. No action
completes silently. The three house forms, in ascending weight:

| Form | Use | Implementation |
|---|---|---|
| **Status line / chip** | routine, transient, non-blocking ("Saved 14:32", "3 items copied") | `writer._flash(msg, secs=2.6)` → reverts to `_update_status()` |
| **Inline card** | a result the user must read, or must act on | the overlay-card pattern |
| **Modal confirm** | irreversible and unexpected loss only | Article IV §3 |

Transient status text auto-reverts; it never accumulates and it never becomes
the permanent contents of a status bar.

### §2 Errors

An error message states, in this order: **what did not happen**, **why**, and
**what the person can do**. `nbapp.save_failure_reason` is the model — it maps
`ENOSPC`/`EROFS`/`EACCES`/`EDQUOT` to a sentence somebody can act on rather than
printing an errno.

- Never show a traceback, an exception class, an errno, or a subprocess's stderr
  verbatim in a user-facing surface.
- Never show a bare "Error" or "Failed".
- An error that the user cannot act on and that costs nothing goes to the
  status line, not a modal.
- `except Exception: pass` is legitimate only where the failure genuinely costs
  the user nothing (a backup, a logo decode, a stylesheet rewrite) and there is
  a comment saying so. On a save, a load, or a delete it is a defect.

### §3 Confirmation

Confirm **only** when all three hold: the action is irreversible, the loss is
not what the user just asked for, and undo cannot cover it. Everything else
just happens.

A confirm card states the **consequence**, not the mechanism, and names the
**target**:

> Delete "Chapter 4" permanently?
> This chapter and its 2,100 words cannot be recovered.
> [ Cancel ] [ Delete ]

Rules: the destructive button carries the **verb** (`Delete`, `Erase`,
`Replace`), never `OK`/`Yes`; Cancel is first in reading order and holds focus;
the scrim dismisses as Cancel; `Esc` cancels; `Enter` cancels.

[GAP — P0] There are **fourteen independent implementations of this card**:
`academics._confirm`, `calendar._confirm`, `contacts._confirm`,
`cookbook._confirm`, `finder._confirm`, `g2048._confirm`, `gbasdk._confirm`,
`journal._confirm`, `mealplanner._confirm`, `media._confirm`, `music._confirm`,
`novel._confirm`, `illustrator._confirm_discard`, `writer._confirm_discard`,
plus `shell.Panel._card_dialog`, `installer._open_confirm` and
`accounting._confirm_delete`. Their signatures already disagree
(`(title, message, ok_label, on_yes)` vs `(heading, detail, ok_label)` vs
`(heading, body, ok_label, on_ok)`), only some grab focus on Cancel, and only
some handle `Esc`. This is the single largest source of interaction drift in the
OS.

**Contract:** one shared `nbapp.confirm(parent, heading, body, ok_label, on_ok,
danger=False)` returning a handle with `.close()`, built on the existing overlay
pattern (`Gtk.Fixed` layer + `Gtk.EventBox`-wrapped scrim + `Gtk.EventBox`-wrapped
card, centred on the live allocation with `nbapp.screen_size()` as the fallback —
**never** a hardcoded 1920x1080). Migration is per-app and mechanical; the
existing `_confirm` names stay as thin wrappers so no call site changes in the
same pass that introduces the primitive.

### §4 Empty states

An empty list says what it is and how to fill it, in sentence case, and offers
the create action. "No items" alone is not an empty state.

### §5 Gate

`tools/dialogshot.py` renders a real dialog offscreen under the shipped theme;
`tools/uishot.py` renders any widget the same way (via `tools/guestrun.sh`, so it
picks up the *guest* theme and fonts, not the host's). Required [GAP — Hour 4]:
a static check that every confirm construction routes through the shared
primitive, and that its `on_ok` is not wired to the first-focused button.

---

## Article V — Background jobs, cancellation and stale results

### §1 The rule

**No disk scan, subprocess, parse, encode or export runs on the GTK main
thread.** The main thread's only job is to stay responsive.

### §2 The job contract

Six requirements. Today they are re-derived, partially, in each of the
fourteen `threading.Thread` sites across `finder.py`, `settings.py`,
`installer.py`, `usbwriter.py`, `video.py` and `sequencer.py`.

1. **Worker thread, `daemon=True`.** Nothing may block process exit.
2. **Results marshal back with `GLib.idle_add` only.** No GTK call from a worker
   thread, ever — including `set_text`, `queue_draw` and store mutation.
   (`installer.py` is the house reference and says so in its own comment; so does
   `video.py:2668`.)
3. **A generation token guards every completion.** Bump on start, capture, and
   compare in the idle callback before touching a single widget. Existing
   implementations: `finder._wide_gen` (`_wide_scan` → `_wide_done`, which
   re-checks *both* the generation and the live query string),
   `installer._scan_gen` and `installer._clash_gen`, `video`'s import-scan and
   export-build generations, `ebook._doc_gen`.
4. **A liveness flag guards every completion whose target can be destroyed.**
   `finder.py:3100` is the reference: the flag is tied to the dialog's `destroy`,
   so a result cannot land in a torn-down widget.
5. **Cancellation where it is safe.** A cancel flag the worker polls at a
   natural boundary (per file, per chunk), and the UI moves to `Cancelling…`
   immediately rather than waiting for the worker to notice.
   `usbwriter._write_thread` → `_finished("stopped", "")` is the reference for a
   job that must *not* be killed mid-operation.
6. **Cleanup on destroy.** Every source the job owns is removed in the window's
   destroy handler.

### §3 The five inline states

Every job reports exactly one of these, in the app's own surface — never a
separate window and never a spinner with no words:

`Preparing…` → `Working` (with progress if the total is knowable, e.g.
`usbwriter._progress(done, total)`) → `Cancelling…` → `Completed` /
**actionable failure** per Article IV §2.

A job with an unknowable total says what it is doing
(`usbwriter._say_idle("Finishing the write…")`) rather than showing an
indeterminate bar.

### §4 Blocking calls to migrate

`subprocess.run` / `check_output` / `check_call` appear in: `nbprint.py` (8),
`firstrun.py` (4), `video.py` (3), `finder.py` (2), `gbabuild.py` (2),
`nbgame.py` (2), `sequencer.py` (2), `usbwriter.py` (2), and once each in
`installer.py`, `media.py`, `nbaudio.py`, `nbkeyboard.py`, `nbmediakeys.py`,
`settings.py`. Not all are on an interactive path — a one-shot probe at import
is different from a print job — so Hour 5 triages by *observed* duration, not by
count. `nbprint.py` is the first candidate: printing is user-initiated,
CUPS/IPP round-trips are slow, and none of its eight sites is threaded.

### §5 Gate

[GAP — Hour 5] `tools/job_contract_selftest.py`: a static pass asserting that
every `threading.Thread(target=…)` in `de/*.py` has a target that touches GTK
only through `GLib.idle_add`, and that every such `idle_add` target begins with a
generation or liveness check; plus a **slow-operation fixture** proving the GTK
caller returns within the Article VIII §1 budget and that a completion from a
superseded generation mutates nothing.

---

## Article VI — Motion

### §1 What motion is for

Motion explains **continuity** — where a thing came from, where it went, what
replaced what. It is never decoration. Explicitly forbidden by the plan and by
the Papertone design language: blur-led styling, springy/overshoot easing,
parallax, ambient animation, and anything that moves while the user is not
acting.

### §2 Durations and easing (normative)

| Class | Duration | Easing |
|---|---|---|
| Hover / press feedback | 70–100 ms | ease-out |
| Selection / focus transition | 100–140 ms | ease-out |
| Menu, inline card, disclosure — **arriving** | 140–180 ms | ease-out |
| Menu, inline card, disclosure — **departing** | 140–180 ms | ease-in |
| Page / document transition | 180–220 ms | ease-in-out |

The first three rows are the Papertone theme's existing vocabulary (90 ms state
feedback, 140 ms for a surface arriving —
`usr/share/themes/Papertone/gtk-3.0/gtk.css:469`). **The last two rows are new,
and they conflict with that theme's stated rules.** See §12 for the conflict and
its resolution; do not implement rows 4–5 without reading it.

Two theme constraints are absolute and are restated here so a motion pass cannot
lose them:

- **Only colour and border animate.** No transition on `width`, `height`,
  `margin` or `padding`: those force GTK to re-layout on every frame, which on a
  software renderer is exactly how "smooth" becomes "janky". A page transition
  must therefore be expressed as opacity/colour or as cairo-drawn offset inside a
  fixed allocation — never as an animated allocation.
- **Nothing bounces, springs, overshoots, pulses, or slides in from off-screen
  to be noticed**, and there is never any motion the user did not cause.

### §3 Invariants

- **Input is live immediately.** Animation never gates interaction. A control is
  clickable on the first frame of its arrival.
- **Transitions are interruptible and retarget from their current value**, never
  from their start value and never by queueing a second animation. Repeated
  clicks must not stack.
- **Every animation owns its source and cancels on `destroy`.** A frame callback
  outliving its widget is a crash, not a glitch.
- **No one-GLib-timer-per-widget.** Use `Gtk.Widget.add_tick_callback` (the GTK
  frame clock) with `time.monotonic()`, so motion is frame-paced rather than
  wall-clock-paced. There are currently **zero** `add_tick_callback` uses in
  `de/`; all existing periodic work is `GLib.timeout_add`, which is correct for
  clocks and polls and wrong for animation.
- **Invalidate the smallest rectangle that changed.** `queue_draw()` on a
  fullscreen window is a full-screen software repaint.
- No GLib source may be created or removed from a handler on a hot signal
  (`draw`, `motion-notify-event`, `scroll-event`, `enter/leave-notify-event`) —
  already enforced for `shell.py` and `nbapp.py` by
  `tools/redraw_timer_selftest.py`, and to be extended to every animating module.

### §4 The two fallbacks

These are different things and must be separately controllable.

- **Software rendering (`NB_ACCEL != 1`).** A 90 ms transition is six frames; on
  the CPU renderer they are not dropped evenly, so the control lurches and
  arrives late — which reads as *a computer struggling*, materially worse than an
  honest instant snap. `nbapp._apply_motion_policy()` therefore sets
  `gtk-enable-animations` false. **The shared motion engine must apply the same
  policy to its own transitions**, or it will reintroduce exactly the problem
  that switch exists to prevent.
- **Reduced Motion (user preference).** Replaces movement with an instant update,
  or with a restrained crossfade where the change would otherwise be
  incomprehensible. It applies *regardless* of `NB_ACCEL`.

[GAP — P1] There is no reduced-motion preference today. `nbapp.a11y_prefs()`
reads only `large_text` and `high_contrast` from
`$NB_HOME/.config/notebook/settings.json`. **Contract:** add a third key
`reduced_motion`, surfaced on `settings._page_accessibility`, read by
`a11y_prefs()` and applied by `a11y_set()` in-process the way the other two
already are. Per §0.2 it cannot reach *other* running apps; the Settings page
must say so, as it already does for text size.

**Policy resolution order** (one function, `nbmotion.policy()`): reduced-motion
preference → `NB_ACCEL` → duration token. Any of the first two resolving to
"still" yields duration 0, and a duration-0 transition must land on **exactly**
the same end state as an animated one. That equivalence is the gate.

### §5 Gate

[GAP — Hour 2/3] `tools/motion_selftest.py`, headless: easing endpoints
(`f(0)==from`, `f(1)==to`), retargeting mid-flight, cancellation on `destroy`
leaving no live source, policy selection under each of the four
(reduced-motion × NB_ACCEL) combinations, and **end-state equivalence** between
animated and duration-0 paths.

---

## Article VII — Accessibility, keyboard and target size

### §1 Keyboard completeness

Every action reachable by pointer is reachable by keyboard. Tab order follows
reading order (and therefore mirrors correctly under RTL, since
`nbapp.apply_direction()` sets `Gtk.TextDirection.RTL` process-wide at import).
Focus is always visible — the house rule is `has_visible_focus()`, i.e. the ring
appears for keyboard traversal and not for a click, exactly as
`nbapp.PaperSwitch.do_draw` implements it.

Custom `Gtk.DrawingArea` controls — of which this OS has many (Illustrator's
canvas, Maps, Sequencer, the widget board, the GBA SDK) — must be focusable,
must handle `space`/`Enter` as activation, and must draw their own focus ring.
A `DrawingArea` that is clickable but not focusable is a defect.

### §2 Accessible names

Every actionable control has a name a screen reader can read: a visible label,
or `set_tooltip_text()` plus `get_accessible().set_name()`. Icon-only buttons —
the Finder toolbar, the media transport, the Illustrator dock — are the specific
risk.

[GAP] There is no accessible-name coverage check today. Note the known trap from
the i18n work: `nbi18n` only walks `Label`s and `Button`s, so text living
anywhere else (a `TreeView` column title, a `DrawingArea`'s painted string)
stays English *and* stays unnamed. The same blind spot applies here.

### §3 Non-colour state

No state is conveyed by colour alone. A save chip is a dot **and** a word
(`● Saved 14:32`); a selected row is a background **and** a text weight; an error
is a colour **and** a sentence.

### §4 Target sizes

Anchored to what the 1024x740 budget actually permits, and to the sizes already
in the CSS (the two most common `min-height` values across `de/*.py` are 30px
and 34px):

| Class | Minimum |
|---|---|
| Primary and destructive buttons | 34px high, 8px clear of any neighbour |
| Ordinary buttons, toolbar items, menu items | 30px high |
| List / table rows | 30px high |
| Anything actionable at all | 24px high — **hard floor** |

A 44px iOS-style target is not achievable at 1024x740 with this information
density, and claiming it would be dishonest. 24px is the floor that is actually
enforceable.

### §5 Text and contrast

- The text floor is `nbapp.TEXT_MIN = 15` px, applied by rewriting each
  stylesheet as it loads (`_a11y_css` via the `_a11y_hook` wrapper on
  `Gtk.CssProvider.load_from_data`), because no CSS layer can express "raise this
  rule if it is below 15px and otherwise leave it". 16 is the true wall — the GBA
  SDK lands 2px past 1024 there.
- High contrast deepens the *quiet* tiers (`#9A9484` at 2.92:1 → `_HC_QUIET`
  `#55514A` at 7.05:1), because ink on paper is already 16.99:1 and darkening it
  buys nothing anyone can see.
- **Disabled controls are exempt** and must stay exempt (`_HC_SKIP`): WCAG 1.4.3
  exempts inactive components, and boosting them makes an unavailable control
  read as available. This covers both `:disabled` and the class-based dimming
  this OS uses (`.dim`, `.pipoff`, `.chip-off`, `.disabled`).
- Text scaling must not clip or hide an essential control at 1024x740.

### §6 Gate

`tools/minsize_sweep.py` (1024x740 overflow), `tools/text_stress_selftest.py`,
`tools/button_contrast_check.py`, `tools/i18n_check.py`,
`tools/tofu_sweep.py` exist. Required [GAP — Hour 12]:
`tools/a11y_inventory.py` — a static inventory asserting **no actionable custom
control without an accessible name** and **no actionable widget below the §4
floor** in the audited core surfaces.

---

## Article VIII — Performance budgets

Measured on the **software-rendered** path, because that is the weakest
supported configuration and the one the budgets exist to protect.

| Budget | Value | Rationale / how it is measured |
|---|---|---|
| **B1 — Input acknowledgement** | ≤ 100 ms from press to visible change | the top of the Article VI §2 feedback band |
| **B2 — Main-thread stall** | ≤ 50 ms in any single GTK callback on an interactive path | three dropped frames at 60 Hz; beyond this the pointer visibly stutters |
| **B3 — Handler return under load** | a user-initiated handler that starts a job returns in ≤ 16 ms | Article V §2; asserted by the slow-operation fixture |
| **B4 — Frame cadence during motion** | no frame > 2x the median frame interval | `add_tick_callback` timestamps; a lurch is worse than no motion (Article VI §4) |
| **B5 — Redraw scope** | an animation invalidates only its own allocation | no `queue_draw()` on a fullscreen toplevel from an animation |
| **B6 — Timer churn** | zero GLib source create/remove on a hot signal | already gated by `tools/redraw_timer_selftest.py` |
| **B7 — Launch: import + construct** | no regression vs. the recorded baseline | `tools/perf_baseline.py`, which measures module import and window construction separately in a fresh subprocess per app |
| **B8 — Periodic work** | a once-a-second tick touches a widget only when its value changed | `AppWindow._tick` caches `_clock_txt`/`_date_txt` precisely for this; `set_text()` re-lays-out and queues a resize even for identical text |
| **B9 — Layout** | every app fits 1024x740 | `tools/minsize_sweep.py` |

**Reporting rule.** A budget verified only headlessly is reported as *statically
checked*, never as *verified on hardware*. Display-dependent claims are not
reported as verified when the host lacks an X display — and `tools/uishot.py`
must run through `tools/guestrun.sh`, or it renders the **host** theme and the
measurement is wrong (15.6% of pixels wrong, measured, during the HiDPI run).

---

## §9 Conformance gates, consolidated

### Existing, retained as-is

`tools/construct_all_host.py` · `tools/construct_one.py` ·
`tools/css_parse_check.py` · `tools/ascii_css_check.py` ·
`tools/redraw_timer_selftest.py` · `tools/perf_baseline.py` ·
`tools/minsize_sweep.py` · `tools/data_safety_selftest.py` ·
`tools/store_damage_selftest.py` · `tools/reopen_damage_selftest.py` ·
`tools/reopen_shapes_selftest.py` · `tools/save_failure_selftest.py` ·
`tools/undo_selftest.py` · `tools/persistence_roundtrip_selftest.py` ·
`tools/i18n_check.py` · `tools/i18n_coverage_check.py` ·
`tools/voice_check.py` · `tools/button_contrast_check.py` ·
`tools/text_stress_selftest.py` · `tools/tofu_sweep.py` ·
`tools/accel_selftest.py` · `tools/display_selftest.py` ·
plus the per-app `*_selftest.py` and `*_lifecycle_selftest.py` suites.

**Three of these are load-bearing in a non-obvious way and must be run after any
CSS edit:** `py_compile`, `construct_one.py` and the design-token checker are all
**blind to CSS corruption inside a bytes literal**; only
`tools/css_parse_check.py` catches it, and only `tools/ascii_css_check.py`
catches a non-ASCII character in a `b"""…"""` block — which is a `SyntaxError`
that takes *every* app down.

### New, to be written

| Gate | Article | Hour |
|---|---|---|
| `tools/motion_selftest.py` | VI §5 | 2–3 |
| `tools/menu_conformance_check.py` | I §6 | 4 |
| `tools/job_contract_selftest.py` | V §5 | 5 |
| `tools/nav_generation_selftest.py` | III §4 | 7 |
| `tools/a11y_inventory.py` | VII §6 | 12 |

**The meta-rule, from four prior instances in this repository:** *a green gate
must be proven able to go red.* Every new check ships with a deliberate mutation
demonstrating the failure mode it claims to catch. A checker that has never
failed has not been tested; it has been observed.

---

## §10 Prioritized gap inventory

Priority is by user harm, per the plan's Hour-14 triage order: data loss → frozen
UI → stale callback → navigation inconsistency → polish.

| # | Pri | Gap | Where it is now | Article |
|---|---|---|---|---|
| 1 | **P0** | Fourteen divergent confirm-card implementations with three different signatures; inconsistent focus, `Esc` and scrim behaviour | `academics/calendar/contacts/cookbook/finder/g2048/gbasdk/journal/mealplanner/media/music/novel._confirm`, `illustrator._confirm_discard`, `writer._confirm_discard`, `shell.Panel._card_dialog`, `installer._open_confirm`, `accounting._confirm_delete` | IV §3 |
| 2 | **P0** | Close-time autosave flush is not systematically verified; only `writer._on_destroy` is known-correct | all document apps | II §2 |
| 3 | **P0** | `data_safety_selftest`'s write-path detector sees only `open(...,'w')` — a known blind spot that let a real hole stay green | `tools/data_safety_selftest.py` | II §6 |
| 4 | **P0** | 8 unthreaded `subprocess` calls on the print path; user-initiated and slow | `nbprint.py` | V §4 |
| 5 | **P1** | No shared job pattern: 14 `threading.Thread` sites each re-derive marshalling, generations, liveness and cancellation | `finder`, `settings`, `installer`, `usbwriter`, `video`, `sequencer` | V §2 |
| 6 | **P1** | Generation guards exist in only 3 modules; every other delayed callback can land on a newer state | present: `finder._wide_gen`, `installer._scan_gen`/`_clash_gen`, `ebook._doc_gen`, `video` import/export gens. Absent elsewhere | III §3, V §2 |
| 7 | **P1** | No reduced-motion preference; `a11y_prefs()` reads only `large_text` and `high_contrast` | `nbapp.a11y_prefs`, `nbapp.a11y_set`, `settings._page_accessibility` | VI §4 |
| 8 | **P1** | No shared motion facility; zero `add_tick_callback` uses; all timing is `GLib.timeout_add` | OS-wide | VI §3 |
| 9 | **P1** | Modal overlays never restore focus to the control that opened them | `AppWindow._close_menu`, `AppWindow._close_about`, every `_close_confirm` | III §2 |
| 10 | **P2** | Settings does not restore its last pane | `settings._select`, `settings._page_*` | III §3 |
| 11 | **P2** | Accelerator agreement is unverified in both directions | every `menu_items()` vs. every `_on_key` | I §3 |
| 12 | **P2** | Context-menu ⊆ menu-bar is unverified | apps with right-click menus | I §5 |
| 13 | **P2** | No accessible-name coverage for icon-only and `DrawingArea` controls | Finder toolbar, media transport, Illustrator dock, Maps, Sequencer, widget board | VII §2 |
| 14 | **P2** | Target-size floor unverified against the 24px hard floor | OS-wide CSS | VII §4 |
| 15 | **P3** | Panel dropdowns render behind a focused window under matchbox — a known WM-level limitation, six approaches already failed | `shell.Panel._popup` | — (documented limitation, not scheduled) |

Item 15 is listed for completeness and is explicitly **out of scope** for this
block: it needs a window-manager change, not a UX change.

---

## §11 Migration order, Hours 2–15

Each hour is bounded to one subsystem and a small file set, per the plan's
constraint that a pass must finish below the turn ceiling. Gap numbers refer to
§10.

| Hour | Work | Gaps closed | Gate |
|---|---|---|---|
| 2 | `nbmotion` in shared infrastructure: tokens, `policy()`, tick-callback scalar transitions, cancel-on-destroy | 8 | `motion_selftest.py` |
| 3 | Transition primitives (crossfade, directional page, reveal/collapse, selection persistence, progress interpolation) applied to **shared overlays and containers only** | 8 | reduced-motion ≡ animated end state |
| 4 | Menu/command/shortcut conformance; centralise labels, ellipsis, enablement | 11, 12 | `menu_conformance_check.py` |
| 5 | Shared job pattern; migrate `nbprint.py` and the other highest-risk blocking paths | 4, 5, 6 | `job_contract_selftest.py` + slow fixture |
| 6 | Saving/recovery/undo truthfulness; close-time flush; write-path detector | 2, 3 | extended `data_safety_selftest.py` |
| 7 | Navigation, focus restoration, generation tokens on delayed restoration | 6, 9 | `nav_generation_selftest.py` |
| 8 | Shell + Finder as one workflow; restrained motion where compositor policy permits | 1 (Finder), 9 | shell/Finder lifecycle + fileops suites |
| 9 | Settings / First Run / Login / Installer / USB Writer / printing under one language; **Settings pane restoration** | 10, 1 (installer) | setup/install/write/print fixtures |
| 10 | Productivity apps: Writer, Calendar, Tasks, Contacts, Journal, Academics, Accounting, Cookbook, Meal Planner, Novel | 1, 2, 6 | document + library conformance |
| 11 | Media and creative: Music, Media, Video, Ebook, Maps, Sequencer, Illustrator, games | 1, 6 | playback/export lifecycle |
| 12 | Accessibility and keyboard completeness; **reduced-motion preference lands here** if not earlier | 7, 13, 14 | `a11y_inventory.py` |
| 13 | Performance and frame pacing; coalesce invalidation, cache static Cairo/Pango | B4, B5, B8 | `redraw_timer_selftest.py`, `perf_baseline.py` |
| 14 | Cross-system conformance and bug burn-down, triaged by harm | residual | all suites + `construct_all_host.py` |
| 15 | Final integration, hunk review, noon report | — | full matrix |

**Sequencing constraint that must not be reordered:** the shared *primitive* is
built before any app is migrated to it (Hours 2, 4, 5 precede Hours 8–11), and
the reduced-motion preference (gap 7) must land **before or with** Hour 3, since
`nbmotion.policy()` reads it. If gap 7 slips to Hour 12, `policy()` ships reading
a key that is always absent — which is functionally "motion always on under
accel", i.e. the wrong default for the users the preference exists for.

---

## §12 Internal consistency check

Verified while writing, and recorded so the next pass need not re-derive it:

- **Article VI §2 vs. the Papertone theme's MOTION section — a genuine conflict,
  not a wording difference.** The plan's motion spec
  (`docs/UX-15-HOUR-PLAN.md`) mandates *"ease-in for departure, ease-in-out for
  movement already on screen"* and a 180–220 ms page-transition band. The shipped
  theme states the opposite on both points: *"The easing is ease-out
  everywhere… There is deliberately no ease-in-out (it feels sluggish at these
  durations)"*, and it caps its vocabulary at 140 ms because *"both are under the
  ~200ms at which a transition stops reading as 'the control responded' and
  starts reading as 'the interface is playing an animation'"*. The theme's
  numbers are a considered position with a stated rationale; the plan's are a
  general-purpose motion spec.

  **Resolution, pending the user's call:** the theme wins inside its own domain
  and the plan wins outside it. Concretely — CSS-driven state feedback (hover,
  press, focus, check, selection) keeps 90 ms ease-out and is not to be touched;
  surfaces arriving and leaving keep 140 ms ease-out rather than adopting a
  separate ease-in for departure; and rows 4–5 of the §2 table apply **only** to
  the page/document transitions the theme has no vocabulary for at all, at the
  *bottom* of the plan's band (180 ms) rather than the top, with ease-in-out
  used only where the moving thing is already on screen throughout. This keeps
  every existing pixel of motion exactly as designed and confines the new band to
  a case the theme does not currently address. **Flagged for the user:** if the
  intent was instead to supersede the theme's easing rules OS-wide, that is a
  visual-language change, it is outside "no visual redesign", and it needs an
  explicit decision before Hour 3.
- **Article VI §4 vs. `nbapp._apply_motion_policy`.** No conflict, but a real
  trap: that function disables *GTK's* animations under software rendering. A new
  `nbmotion` built on `add_tick_callback` is **not** governed by
  `gtk-enable-animations` and would keep animating on the software path unless
  `policy()` checks `NB_ACCEL` itself. Stated explicitly in §4 for that reason.
- **Article I §4 (`Esc` = leave) vs. Article II §5 (destructive actions).** No
  conflict: `Esc` never destroys, so a discard-changes flow reached by `Esc` must
  route through a confirm, and the confirm's own `Esc` cancels — one more level
  of "leave", which is consistent.
- **Article III §2 (Enter activates the default) vs. Article IV §3 (Enter
  cancels).** Consistent by construction: the default action in a *confirm* is
  Cancel. The rule is "Enter activates the safe choice", stated both places.
- **Article VII §4 (34px primary buttons) vs. Article VII §5 / B9 (fit 1024x740).**
  These pull against each other. Resolved in favour of the layout budget: 34px is
  required only for primary and destructive buttons, 24px is the universal hard
  floor, and 44px is explicitly rejected as unachievable rather than promised and
  missed.
- **Article V §2.5 (cancellation) vs. `usbwriter`.** A disk write cannot be
  cancelled at an arbitrary point without leaving an unbootable stick. "Where it
  is safe" is load-bearing, and `_write_thread` → `_finished("stopped", "")` —
  which stops at a chunk boundary and reports it — is the correct reading, not an
  exception to the rule.
- **Article IV §3's shared primitive vs. `shell.Panel._card_dialog`.** The shell
  panel is not an `AppWindow` and has no `self._overlay`, so it cannot use an
  `AppWindow`-bound helper. `nbapp.confirm()` therefore takes the parent
  explicitly and resolves the overlay from it; `_card_dialog` keeps its own
  construction until Hour 8 and is listed in gap 1 accordingly.
- **`nbapp.confirm()` vs. `nbapp.SEP`, `undo_menu_items`, `undo_keys`.** All
  free functions or `AppWindow` methods in the same module; adding one more free
  function is consistent with how the shared layer is already organised, and
  `nbapp` is already imported by all 28 apps before they build anything.

**Not verified in this pass, and not claimed:** any runtime behaviour. This
document was produced from source reading only. No app was launched, no
screenshot was rendered, and no test was run. Every duration, size and budget
above is a *contract to be met*, not a measurement — except where it cites a
number already recorded in the source (`TEXT_MIN = 15`, `_UNDO_DEBOUNCE = 600`,
writer's 900 ms autosave, the 1024x740 panel, the 46px menu bar, the 30px/34px
CSS `min-height` modes, and the WCAG ratios in `nbapp`'s accessibility block).
