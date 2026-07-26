# Notebook OS — improvement run brief (agent shared context)

Read `tools/AUDIT_BRIEF.md` FIRST. It has the harness, the design language, the
verification gates and the known gotchas, and all of it still applies. This file
says how THIS run is different.

## The bar is different

The last run hunted defects. This one hunts **improvements**. The question is
not "is this broken?" but:

> A thoughtful person uses this app for a real task, start to finish. Where does
> it make them work harder than they should? What would make them glad?

An app can be completely bug-free and still be mediocre. Mediocre is what we are
attacking. Deliver things a user would *notice and appreciate*, not a longer
list of trivia.

## What counts as a real improvement

* **Removes work.** A default that is right so nobody changes it. A step that
  disappears. Something remembered instead of re-asked. A sensible starting
  document instead of a blank one.
* **Reveals capability.** The app can already do something useful that nobody
  will ever find. Surface it where the user is, at the moment it helps.
* **Teaches in place.** Empty states, first-run and error messages are the best
  teaching surfaces in any app. An empty state that says only "nothing here" is
  a wasted screen; it should show the one action that matters.
* **Handles the real world.** Long names, huge numbers, a hundred items, a
  file that vanished, a disk that filled, a wrong format, a half-finished edit.
  Degrade gracefully and say something useful.
* **Respects work in progress.** Nothing a user typed should ever be lost by a
  crash, a stray click, a close, or an app switch. Undo where it is cheap.
  Confirm before destroying. Autosave.
* **Feels quick.** Do less work before first paint. Load big things lazily and
  on demand. See the performance section.
* **Is keyboard-reachable.** Every frequent action should have a shortcut, and
  the app should be usable without a pointer.

## What does NOT count — do not do these

* Restyling something that is already coherent, or inventing new visual
  language. The papertone system is settled.
* Renaming things that are already clear.
* Adding options. A setting is usually a failure to pick a good default. Prefer
  the right behaviour over a switch that asks the user to think.
* Features nobody asked for that add a surface to maintain.
* Churn to look busy. **"I examined X thoroughly and it is genuinely good" is a
  valuable, welcome result** — then spend your time going deeper somewhere that
  is not.

## Performance — what is actually true (measured, do not re-derive)

Host baseline, warm caches, `tools/perf_baseline.py`:

* Import is **9-30 ms** per app; construct is **40-110 ms**; memory **11-21 MB**.
* There is **no outlier** left. (gbaide once measured 191 ms — that was bytecode
  compilation right after an edit, not real cost. Warm it is 14 ms.)
* The shipped image **already carries valid Python 3.11 bytecode** for all 47
  modules, so there is no first-launch compile cost to win back. Verified.

So: **do not micro-optimise Python.** Shaving 5 ms off a 60 ms construct is
invisible and risks breaking working code. The wins that are real:

1. **Work avoided.** Build a pane when it is first shown, not at construct. Load
   a font, dictionary, map pack or sample only on demand. An app with six tabs
   should not build six tabs to show one.
2. **Work not repeated.** Cache a decoded asset instead of decoding per draw.
   Do not re-scan a directory on every keystroke.
3. **Big data.** Anything that grows with the user's content — a long document,
   a full ledger, a large image, hundreds of files — should stay responsive.
   Test with a LOT of data, not three rows.

If you make a performance claim, **measure it**: `tools/perf_baseline.py` for
launch/memory, or time the specific operation before and after. State both
numbers. An unmeasured optimisation is not a result.

## Rules that still bind

* Fix at source in the overlay; never edit `buildroot/output/target`.
* **Only your assigned files.** Shared files (`nbapp.py`, `nbicons.py`,
  `nbpicker.py`, `nbi18n.py`, the Papertone theme) are the lead's — report what
  you need instead of editing.
* Any new user-facing string must be wrapped in `_t()` and added to all four
  catalogs (`lang_es/fr/zh/sr.json`) with a real translation, or it will be the
  only English left on a translated screen. Run `tools/i18n_check.py`.
* After EVERY edit, and before you report:
  ```
  python3 tools/ascii_css_check.py            # must say "clean"
  DISPLAY=:0 python3 tools/construct_all_host.py   # must say "28 ok, 0 crashed"
  DISPLAY=:0 python3 tools/i18n_check.py      # must say "clean", all four equal
  ```
  and confirm your apps still fit **1024x740** via `tools/appshot.py`.
* **Render it and look at it.** A change you have not seen is a change you have
  not made.

## Reporting

For each improvement: *what a user could not do before, what they can do now,
the concrete scenario it helps, how you verified it (which render, which
measurement).* Separate SHIPPED from CONSIDERED-AND-REJECTED, and say why you
rejected. Rejections are useful — they stop the next agent redoing the analysis.

Be honest about scale. Three improvements someone would actually notice beat
twenty nobody would.
