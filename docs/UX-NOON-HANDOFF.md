# NotebookOS UX convergence — noon handoff

Date: 2026-08-05  
Source plan: `docs/UX-15-HOUR-PLAN.md`

## Outcome

The fifteen-hour convergence block implemented shared interaction contracts and
migrated representative high-use workflows without replacing the Papertone 2D
design. The automated, display-free acceptance matrix is green. This is strong
evidence for predictability, lifecycle safety, responsiveness and consistency;
it is not evidence by itself that physical hardware now matches an iPad's frame
pacing or touch ergonomics.

## Implemented contracts

| Area | Result |
|---|---|
| Motion | `nbmotion.py`: frame-clock animation, canonical timing/easing, retargeting, ownership cleanup, Reduced Motion and software-rendering instant equivalence |
| Transitions | `nbtransitions.py`: directional page switches, reveal/crossfade/highlight primitives, replacement/cancellation safety |
| Commands | `nbcommands.py`: canonical 29-command registry and shared menu adapters; conformance test reports zero gaps |
| Background work | `nbjobs.py`: cancellation checkpoints, duplicate policy, generations/newest-wins, ordered progress, exactly-once delivery, close suppression |
| Navigation/state | `nbstate.py`: generations, restoration scopes, stable-identity lookup and safe persisted-state coercion |
| Data safety | Shared atomic text/JSON writes and saved-checkpoint dirty semantics; Screenplay Save As rollback and atomic plain-text save |
| Finder | Back/Forward direction, stable path selection/scroll restoration, coalesced and generation-guarded monitor refresh, destroy invalidation |
| System workflows | Settings restores its last valid pane without restore-time writes; USB Writer uses owned cancellable jobs and rejects duplicate writes; printer discovery is asynchronous |
| Productivity | Contacts follows canonical Find/Ctrl+F and local-first Escape; Writer command labels now conform fully |
| Media | Media transport ignores late EOS/error/poll callbacks after stop/replace/close and exits fullscreen when leaving the video surface |
| Accessibility | Visible shared focus treatment; missing accessible names derive from existing tooltips without replacing explicit labels; unavailable states remain distinct under High Contrast |
| Performance | Frame-clock motion; coalesced Finder/Widgets monitor sources; incremental System Monitor rows; gated/partial Sequencer redraw; bounded undo history and job references |

## Automated evidence

- Twelve focused UX suites exit 0: motion, transitions, commands, jobs,
  document safety, navigation state, shell/Finder, system workflows,
  productivity, media/creative, accessibility and performance.
- Motion: 959 checks; transitions: 101 checks; commands: 29 commands and zero
  reported gaps.
- Desktop compatibility: 63 Python modules parse with zero failures.
- Shell compatibility: every shipped NotebookOS and tools shell script passes
  `bash -n`.
- Acceleration policy: 12/12; display-scale policy: 10/10; session boot: 62/62.
- Relevant legacy suites passed for printer discovery, USB Writer, Finder eject,
  Ebook navigation, Music lifecycle, Media fullscreen, Video export lifecycle,
  Sequencer redraw, System Monitor, Widgets, First Run, Login, Installer,
  Contacts records, Tasks, Calendar, Academics and Cookbook.
- CSS parser and `git diff --check` are clean.

## Required real-runtime verification

These items cannot be honestly closed on the current host because it has no X
display and no target devices:

1. Run a 60 Hz/HiDPI compositor trace on accelerated target hardware and a
   software-rendered fallback machine; measure missed frames, input-to-paint
   latency and transition completion.
2. Perform keyboard-only traversal and screen-reader inspection of Shell,
   Finder, Settings, Writer, Contacts, Media and every destructive confirmation.
3. Run the contrast and HiDPI icon renderers with a real `Gdk.Screen`; both abort
   before rendering on this displayless host.
4. Exercise real USB write/cancel/unplug, printer discovery/print failure,
   installer target selection/cancel, login authentication, audio/video EOS and
   fullscreen transitions on representative hardware.
5. Validate touchpad/touch target comfort and small-screen clipping at 1024x600,
   1366x768, Retina-class 2x and external-display scale changes.

## Next priority

The next block should be a hardware UX validation pass, not another broad source
refactor. Record frame-time and interaction traces, log defects against the
shared contracts above, and repair only reproducible failures. That is the
remaining evidence needed before describing NotebookOS as iPad-class in actual
use rather than in its automated behavioral architecture.
