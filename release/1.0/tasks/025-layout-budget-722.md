# 025 — The 18px phantom in the height budget

**Lane:** C (harness) · **Streams:** S1/S2 · **Status:** CLOSED 2026-08-07

shell.py strut-reserves PANEL_H=46; nbapp.py's note claimed a 28px panel and
minsize_sweep checked 740. Real budget: **722** (docs/PAPER-PHYSICS.md §E3.6).

**Fixed:** minsize_sweep defaults → (1024,722)/(1366,722) with the derivation;
nbapp.py note corrected; PANEL_H/canvas_h() made law in design_tokens + nbapp
(task 027), with grid_check holding every copy in lockstep.

**The predicted victim had already escaped.** The handoff said video (725,
"tallest app") would flag at 722 — but that figure was nbapp's pre-task-013
measurement note; the transport rebuild shrank video to **628**. The bug-fix
session ran the full sweep at 722 with the documented method: **ALL FIT**
(tallest now video 628, then calendar/packages 573). So the original closure
line ("baseline shows video flagged") could never fire, and closing on it
would have been waiting on a ghost. Amended closure, satisfied:

1. The sweep runs at 722 by default — committed, and proven mutation-sensitive
   by grid_check red-proof #3 (budget→740 fails lockstep with recorded text).
2. A full sweep at 722 is on record (bug-fix session, 2026-08-07, ALL FIT,
   with the TIGHT set: academics[ru] 0px → handed to app-improve,
   cookbook[pl] 5px → bug-fix, journal 30px, packages[el] 36px).

**Lesson, again:** a measurement in a comment is data with a date. The 725 was
true when written and stale when read; the gate that measures beats the note
that remembers (see memory: roadmap-is-a-stale-queue).
