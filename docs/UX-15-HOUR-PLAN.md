# NotebookOS Fifteen-Hour UX Convergence Plan

**Window:** 2026-08-04 21:20 EDT to 2026-08-05 12:00 EDT (a fifteen-hour block, rounded)

**Objective:** Make NotebookOS feel coherent, safe, responsive, and immediately understandable at an iPad-class level while preserving the existing Papertone 2D visual language and fully offline product model.

## Product contract

By noon, the system should demonstrate these properties:

1. One interaction language across apps: predictable menus, shortcuts, selection, navigation, confirmation, cancellation, and feedback.
2. No known blocking disk, subprocess, parsing, or export work on the GTK main thread in the audited critical paths.
3. Calm, purposeful motion that explains continuity without changing the flat Papertone design.
4. User data is never silently discarded; autosave, recovery, undo, and destructive actions tell the truth.
5. Long operations visibly progress, can be cancelled where safe, and cannot deliver stale results into a newer state.
6. Keyboard focus, common shortcuts, target sizes, contrast, and reduced-motion behavior are systemwide concerns.
7. The weakest supported software-rendered path remains usable; animation degrades gracefully rather than reducing responsiveness.

## Non-negotiable constraints

- Preserve all existing dirty-worktree changes.
- No commits, resets, reverts, stashes, or visual redesign.
- No Liquid Glass, blur-led styling, springy motion, parallax, or decorative ambient animation.
- No new heavy dependencies or assumptions beyond the shipped Buildroot GTK3/PyGObject/picom stack.
- Every implementation pass is bounded to one subsystem, a small file set, and explicit tests so Claude Code finishes below its turn ceiling.
- A change is accepted only after independent review and a relevant passing regression test.
- Display-dependent claims are not reported as verified when the host lacks an X display.

## Motion specification

- Hover/press feedback: 70–100 ms.
- Selection/focus transitions: 100–140 ms.
- Menus, inline cards, and disclosure: 140–180 ms.
- Page/document transitions: 180–220 ms.
- Ease-out for arrival, ease-in for departure, ease-in-out for movement already on screen.
- Input becomes active immediately; animation never gates interaction.
- Transitions are interruptible and retarget from their current state.
- Reduced Motion replaces movement with an instant update or restrained crossfade.
- Software-rendering mode may shorten or disable expensive transitions automatically.

## Execution schedule

### Hour 1 — 21:20–22:20: Baseline and UX constitution

- Inventory shared UI infrastructure, animation/timer patterns, menu implementations, document models, and background-job patterns.
- Write a concise NotebookOS interaction constitution covering commands, saving, undo, navigation, feedback, cancellation, focus, and motion.
- Establish measurable budgets for input response, main-thread stalls, animation cadence, and redraw scope.
- Capture authoritative baseline test results and known display-dependent gaps.
- Gate: constitution and conformance checklist exist; no production change yet.

### Hour 2 — 22:20–23:20: Shared motion engine

- Implement a lightweight `nbmotion` facility in shared infrastructure using GTK frame-clock tick callbacks and monotonic time.
- Provide duration/easing tokens, reduced-motion and software-rendering policy, cancellation on destroy, and retargetable scalar transitions.
- Avoid one-GLib-timer-per-widget designs and full-window invalidation.
- Add headless/static tests for lifecycle, easing endpoints, retargeting, cancellation, and policy selection.
- Gate: shared engine passes tests and imports on the shipped PyGObject version.

### Hour 3 — 23:20–00:20: Core transition primitives

- Add reusable crossfade, directional page transition, inline reveal/collapse, selection-highlight persistence, and progress interpolation primitives.
- Apply them first to shared overlays/cards and navigation containers, not individual app artwork.
- Ensure controls are interactive throughout transitions and repeated actions do not stack animations.
- Gate: reduced-motion and software fallback paths are behaviorally equivalent and lifecycle-safe.

### Hour 4 — 00:20–01:20: Commands, menus, and shortcuts

- Audit canonical File/Edit/View/App/Help ordering and command labels across core apps.
- Centralize standard labels, ellipsis rules, enablement, and shortcuts where safe.
- Ensure context menus are subsets while the menu bar remains the complete command inventory.
- Fix duplicate submission/default-button paths and inconsistent Escape/Enter behavior.
- Gate: static command-conformance test covers representative document, library, media, and utility apps.

### Hour 5 — 01:20–02:20: Background jobs and feedback

- Create or consolidate a lightweight shared job pattern for worker execution, generation guards, progress, cancellation, and completion cleanup.
- Standardize inline states: Preparing, Working, Cancelling, Completed, and actionable failure.
- Migrate the highest-risk remaining blocking paths discovered in the inventory.
- Ensure stale worker completions cannot mutate a new document, target, or window.
- Gate: slow-operation fixtures prove the GTK caller returns promptly and stale results are discarded.

### Hour 6 — 02:20–03:20: Saving, recovery, and undo truthfulness

- Audit document and library apps for dirty-state truth, pending autosave on switch/close, undo/redo dirtying, atomic Save As, and damaged-store quarantine.
- Fix the highest-confidence remaining loss paths.
- Normalize destructive actions: undo where feasible, confirmation only for unexpected irreversible loss.
- Gate: recovery tests prove the prior durable copy survives malformed input, failed writes, and close-time flushes.

### Hour 7 — 03:20–04:20: Navigation and state restoration

- Standardize Back/Forward, Escape, selection, focus restoration, and page transition direction.
- Restore reasonable window state: active pane, selected item, sort/filter, scroll, zoom, and open content.
- Add generation tokens to delayed restoration callbacks so state never lands on the wrong document.
- Gate: rapid-switch fixtures demonstrate stale navigation and scroll callbacks are ignored.

### Hour 8 — 04:20–05:20: Desktop shell and Finder coherence

- Audit panel menus, window activation, Finder navigation, file operations, removable media, and default-app routing as one workflow.
- Add restrained motion to panel menus, inline confirmations, selection changes, and Finder view transitions where compositor policy permits.
- Preserve keyboard operation, focus, selection, and scroll during periodic or filesystem-driven refreshes.
- Gate: shell/Finder lifecycle, timer, file-operation, and command tests pass.

### Hour 9 — 05:20–06:20: Settings, first run, and system workflows

- Make Settings panes, First Run steps, Login feedback, Installer progress, USB Writer, and printing follow the same navigation/feedback language.
- Restore the last Settings pane and prevent configuration options from exposing choices the system can determine safely.
- Animate step transitions and inline feedback without delaying validation or authentication.
- Gate: setup/install/write/print fixtures preserve safety and remain nonblocking.

### Hour 10 — 06:20–07:20: Core productivity apps

- Apply the shared contracts to Writer, Calendar, Tasks, Contacts, Journal, Academics, Accounting, Cookbook, Meal Planner, and Novel.
- Prioritize state continuity, consistent commands, undo, selection preservation, and subtle content transitions.
- Avoid broad per-app cosmetic rewrites; repair shared or repeated interaction defects first.
- Gate: representative document and library conformance suites pass.

### Hour 11 — 07:20–08:20: Media and creative apps

- Apply motion/lifecycle rules to Music, Media, Video, E-book Reader, Maps, Sequencer, Illustrator, and games.
- Smooth playheads/progress and selection changes without moving UI work to audio rate or repainting unchanged canvases.
- Ensure fullscreen, playback, export, and document switches cleanly restore chrome and cancel stale callbacks.
- Gate: playback/export/animation lifecycle tests pass; no decorative motion added.

### Hour 12 — 08:20–09:20: Accessibility and keyboard completeness

- Audit keyboard-only traversal, visible focus, accessible names/roles, reading order, non-color state cues, and target sizes.
- Verify global text scaling and reduced-motion behavior do not clip or hide essential controls.
- Normalize tab order and default/cancel button behavior in dialogs and inline cards.
- Gate: static accessibility inventory has no unlabeled actionable custom control in the audited core surfaces.

### Hour 13 — 09:20–10:20: Performance and frame pacing

- Audit high-frequency signals, GLib sources, tick callbacks, filesystem monitors, polling, and full-model/full-window rebuilds.
- Coalesce invalidation, cache static Cairo/Pango work, and update only changed rows/cells.
- Validate compositor health/fallback behavior and make motion quality respond to acceleration policy.
- Gate: redraw/timer tests pass and every new animation owns and cleans up its source.

### Hour 14 — 10:20–11:20: Cross-system conformance and bug burn-down

- Run all headless/static suites and parse every desktop module.
- Triage failures by user harm: data loss, disk safety, authentication, frozen UI, stale callback, navigation inconsistency, then polish.
- Use tightly scoped Claude Code repair passes for every high-confidence regression found.
- Gate: targeted suite is green; known failures are proven display/environment limitations or documented real-device work.

### Hour 15 — 11:20–12:00: Final integration and noon handoff

- Run shell syntax, Python AST parsing, diff hygiene, acceleration/display fixtures, and the complete new conformance matrix.
- Review every touched production hunk for compatibility, cancellation, cleanup, dirty-tree preservation, and unintended visual drift.
- Produce a noon report: implemented UX contracts, files and tests, remaining display/device verification, and prioritized next steps.
- Do not claim iPad-class completion from headless tests alone; state exactly what runtime evidence remains.

## Acceptance matrix

| Area | Evidence required by noon |
|---|---|
| Motion | Shared tokens/engine, reduced-motion policy, cancellation and retarget tests |
| Responsiveness | No audited critical path blocks GTK; slow fixtures return promptly |
| Frame pacing | No redundant high-frequency full rebuild in audited always-visible/animated surfaces |
| Commands | Core apps follow standard menu ordering, labels, enablement, and shortcuts |
| Data safety | Atomic writes, truthful dirty state, recovery, and damaged-store preservation |
| Long operations | Progress, cancellation where safe, generation guards, lifecycle cleanup |
| Navigation | Stable Back/Escape/selection/focus and state restoration |
| Accessibility | Keyboard access, visible focus, labels/roles, contrast and reduced motion |
| Compatibility | 58+ desktop modules parse; shell syntax and targeted tests pass |
| Fidelity | Papertone layout, colors, artwork, and flat 2D identity remain intact |

## Noon definition of done

The block is complete when the shared UX rules are implemented in the highest-leverage infrastructure and representative workflows, all safely runnable automated gates pass, the dirty worktree is preserved, and remaining real-display or hardware evidence is listed without overclaiming. “As user-friendly as an iPad” is treated as a behavior target—predictability, immediacy, continuity, safety, and accessibility—not as visual imitation.
