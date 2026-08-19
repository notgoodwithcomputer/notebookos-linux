#!/usr/bin/env python3
"""Sentinel-checked mutation sweep — measures the SUITES, not the app.

    SWEEP_MOD=accounting python3 tools/mutation_sweep.py

ADOPTED 2026-08-08 into tools/. Authored by the app-improve / sidebar lane
(days 2-3), staged for the shared layer as release/1.0/mutation-sweep-harness.py
and lifted here; that staging copy has since been removed, so this file is the
tracked home. The mutation-enumeration, string-masking and sentinel logic were
re-verified in a clean process before adoption (bills 46 / accounting 32
candidates reproduced exactly; both sentinels match one line).

PLACEMENT DECISION (motion/shared-layer lane, whose call this is):
  * It is a DIAGNOSTIC, not a ratchet, and NOT in run_all_gates.py. Three
    reasons: (1) it is slow — ~5 min for accounting (32 mutations x 20 suites
    under guestrun); (2) it needs manual per-app setup (a REGIONS range AND a
    watched SENTINEL) so it cannot blanket the roster the way the fast gates do;
    (3) a mutation-score FLOOR is premature on two apps of data, and scores move
    legitimately as equivalent mutants come and go — a hard ratchet would fail in
    the false direction. Revisit ratcheting once the sweep has run clean across a
    dozen apps with stable, triaged survivors.
  * It belongs in the per-app APP-LOOP routine / a nightly, not the aggregate.
    APP-LOOP.md step 5b references it; point that reference here.

WHAT IT ANSWERS. Once an app stops yielding defects, "is the app right" stops
being the useful question and "would these checks notice if it stopped being
right" starts. This flips ONE decision point at a time in a COPY of the module,
runs every suite for that app against the copy, and reports the SURVIVORS — a
survivor being a change no check notices, stated in the app's own terms rather
than as a coverage percentage.

MEASURED by the author (after the harness itself was fixed — see TRAP 2):

    accounting   32 candidates,  7 survived   78% caught
    bills        46 candidates,  7 survived   85% caught

with all 14 survivors independently equivalence-measured. The % is a per-run
DIAGNOSTIC, not a fixed score — and the reason is worth stating because a
plausible WRONG reason was ruled out first. A motion-lane re-run on 2026-08-08
got bills 9 survived / 80%, two extra survivors on the deadline-STATE boundaries
(`days == 1`, `post_days <= 0`). The tempting explanation was the real clock:
`due_info` falls back to `today_key()` when a suite omits `today=` (34 of 48
calls do). MEASURED FALSE by the author — patching `today_key()` seven months
forward leaves all eight bills suites stable, because those calls sit on
RELATIVE-date bills that move with the clock. The actual cause is a MOVING TREE:
the suites grew 61 -> 209 checks that morning and bug-fix hunks shifted line
numbers (moving what a fixed REGIONS range selects), so an earlier run
legitimately saw more survivors; both boundary mutations are caught in the
settled tree. So the score is only meaningful against a STATED TREE STATE —
record the suite check-count (or a commit) beside any figure and re-run after any
suite or module change. And always read the survivor LIST (triaged by
measurement), never the bare percentage, as the result.

--------------------------------------------------------------------------
THE THREE TRAPS. Each produced a confidently wrong answer before it was found.

TRAP 1 — MASK STRINGS AND COMMENTS FIRST.
    Skipping lines that START with `#` or `"` is not the same as skipping
    strings. The first run rewrote `and` -> `or` inside docstring PROSE and
    reported 15 phantom survivors, in exactly the shape of real findings.
    `tokenize` the source and blank every STRING and COMMENT span before
    looking for an operator.

TRAP 2 — REDIRECT THE IMPORT, NEVER AN ENV VAR.
    Passing `<APP>_MODULE_DIR` and running each suite as a subprocess only
    reaches suites that read it: 6 of 20 for accounting, 6 of 8 for bills. The
    rest imported the REAL module, so most "survivors" were never tested
    against the mutant and both published scores were wrong. A `sys.meta_path`
    hook plus `runpy.run_path` works whatever the suite does.

TRAP 3 — SENTINEL-CHECK BEFORE BELIEVING ANY NUMBER.
    Pick a mutation you KNOW breaks a named check; require it to come back
    CAUGHT before the sweep is allowed to report. One extra run, and it would
    have caught traps 1 and 2 before a figure was published. This is the
    campaign taxonomy's class #15 (a check reads past its own subject) — the
    sweep was its third instance in three days.

AND TRIAGE SURVIVORS BY MEASUREMENT, NEVER BY READING THEM.
    "Equivalent mutant" is a claim like any other. Apply each survivor to a copy
    and compare a battery of real calls against the true module. On bills, 16
    survivors were dismissed by reading and **7 of them changed behaviour** —
    `add_days` among them, the function computing the POST-BY deadline, wrong by
    a day across month edges. A battery only certifies what it EXERCISES: it
    called one mutant equivalent purely because it never called `fmt_due`.
    Anything the battery does not call is "unexercised", not "equivalent".
--------------------------------------------------------------------------
"""
import atexit
import glob
import io
import os
import shutil
import subprocess
import sys
import tempfile
import tokenize

# REPO derived from this file's location (tools/ sits directly under the repo
# root) rather than hardcoded, so the tool runs wherever the checkout lives.
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DE = REPO + "/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
MOD = os.environ.get("SWEEP_MOD", "bills")
SRC = os.path.join(DE, MOD + ".py")


def _make_work(environ=None):
    """(path, owned): unique by default; explicit SWEEP_WORK is caller-owned."""
    environ = os.environ if environ is None else environ
    override = environ.get("SWEEP_WORK")
    if override:
        os.makedirs(override, exist_ok=True)
        return override, False
    return tempfile.mkdtemp(prefix="mutsweep-%s-" % MOD), True


WORK, _OWN_WORK = _make_work()
if _OWN_WORK:
    atexit.register(shutil.rmtree, WORK, ignore_errors=True)

# Line ranges worth mutating: domain logic and store paths. Widget construction
# is excluded — a mutation there mostly means "the sheet looks different", which
# the suites are not trying to be pixel gates for.
REGIONS = {
    "bills": [(120, 560), (575, 660)],
    "accounting": [(100, 300), (400, 470), (1420, 1460), (1510, 1660),
                   (1720, 1760), (2120, 2170)],
}
SWAPS = [(" <= ", " < "), (" >= ", " > "), (" < ", " <= "), (" > ", " >= "),
         (" == ", " != "), (" != ", " == "), (" and ", " or "),
         (" is None", " is not None"), (" is not None", " is None")]

# A mutation per module whose catch has been WATCHED — the harness refuses to
# report a score unless this comes back caught. bills: zero money would render
# with a minus sign. accounting: the opening row would never reveal.
SENTINEL = {
    "bills": ('sign = "−" if n < 0 else ""',
              'sign = "−" if n <= 0 else ""',
              "zero money gains a minus sign"),
    "accounting": ("        if rev is not None:",
                   "        if rev is None:",
                   "the opening row never reveals"),
}

RUNNER = os.path.join(WORK, "_runsuite.py")
RUNNER_SRC = '''import importlib.util, os, runpy, sys
mod, copy_dir, suite = sys.argv[1], sys.argv[2], sys.argv[3]
copy = os.path.join(copy_dir, mod + ".py")
os.environ[mod.upper() + "_MODULE_DIR"] = copy_dir
sys.path.insert(0, %r + "/tools"); sys.path.insert(0, %r)
class R:
    def find_spec(self, name, path=None, target=None):
        if name == mod:
            return importlib.util.spec_from_file_location(name, copy)
sys.meta_path.insert(0, R())
try:
    runpy.run_path(suite, run_name="__main__")
except SystemExit as e:
    sys.exit(1 if e.code else 0)
except BaseException:
    sys.exit(1)
sys.exit(0)
''' % (REPO, DE)


def candidates(src):
    """(line_index, mutated_line, label, code) for every real-code operator."""
    lines = src.split("\n")
    masked = list(lines)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                (r1, c1), (r2, c2) = tok.start, tok.end
                for r in range(r1, r2 + 1):
                    ln = masked[r - 1]
                    a = c1 if r == r1 else 0
                    b = c2 if r == r2 else len(ln)
                    masked[r - 1] = ln[:a] + (" " * (b - a)) + ln[b:]
    except tokenize.TokenError:
        pass
    out = []
    for i, line in enumerate(lines):
        if not any(a <= i + 1 <= b for a, b in REGIONS[MOD]):
            continue
        code = masked[i]
        if not code.strip():
            continue
        for a, b in SWAPS:
            if a in code:
                col = code.index(a)
                out.append((i, line[:col] + b + line[col + len(a):],
                            "%s->%s" % (a.strip(), b.strip()),
                            code.strip()[:58]))
                break
    return lines, out


def caught(lines, index, newline, suites):
    """How the suites respond to a copy carrying this one mutation:

      ('caught', suite)    a suite RAN and FAILED a named check — a real detection
      ('crash', suite)     a suite exited non-zero with NO `FAIL` line: an import
                           error, a missing fixture, a moved module. NOT a
                           detection. This is the case that silently turns a
                           misconfigured harness into 100% caught (the sentinel
                           included), so it must never masquerade as a catch.
      ('survived', None)   every suite ran and passed — no check noticed

    A real FAIL anywhere wins over an earlier crash, so a crash in one suite
    cannot hide a genuine catch in another."""
    out = list(lines)
    out[index] = newline
    with open(os.path.join(WORK, MOD + ".py"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    crashed = None
    for s in suites:
        r = subprocess.run([REPO + "/tools/guestrun.sh", "python3", RUNNER,
                            MOD, WORK, s], capture_output=True, text=True,
                           timeout=300)
        if r.returncode != 0:
            text = (r.stdout or "") + (r.stderr or "")
            if "FAIL" in text:
                return "caught", os.path.basename(s)
            if crashed is None:
                crashed = os.path.basename(s)
    if crashed is not None:
        return "crash", crashed
    return "survived", None


def main():
    # An unknown module needs a REGIONS range and a watched SENTINEL before it
    # can be swept; say so plainly rather than KeyError-ing on REGIONS[MOD].
    if MOD not in REGIONS or MOD not in SENTINEL:
        sys.exit("no sweep defined for %r — add a REGIONS range AND a SENTINEL "
                 "whose catch you have WATCHED (see TRAP 3), or the score cannot "
                 "be trusted. Defined: %s"
                 % (MOD, ", ".join(sorted(set(REGIONS) & set(SENTINEL)))))

    with open(RUNNER, "w") as fh:
        fh.write(RUNNER_SRC)
    src = open(SRC, encoding="utf-8").read()
    lines, muts = candidates(src)
    suites = sorted(glob.glob(REPO + "/tools/%s_*selftest.py" % MOD))
    if not muts or not suites:
        sys.exit("nothing to sweep: %d mutations, %d suites"
                 % (len(muts), len(suites)))

    # TRAP 3 — the sentinel. A mutation KNOWN to break a NAMED check, run
    # before any number is reported. If the harness is grading against the
    # pristine module this comes back a survivor and the sweep refuses to speak.
    #
    # It must be a mutation whose catch has been WATCHED, not just the first
    # candidate: the first version used `muts[0]`, which for bills happens to be
    # one of the genuinely EQUIVALENT mutations, so the sentinel failed for the
    # right reason and the wrong cause. A sentinel that can legitimately survive
    # is not a sentinel.
    pat, rep, expect = SENTINEL[MOD]
    hits = [k for k, ln in enumerate(lines) if pat in ln]
    if len(hits) != 1:
        sys.exit("sentinel pattern matches %d lines, need exactly 1 — the source "
                 "moved under the sentinel; re-anchor it before trusting a score"
                 % len(hits))
    i = hits[0]
    status, by = caught(lines, i, lines[i].replace(pat, rep, 1), suites)
    print("sentinel: line %d %s -> %s"
          % (i + 1, expect,
             {"caught": "caught by %s" % by,
              "survived": "*** SURVIVED ***",
              "crash": "*** CRASHED in %s ***" % by}.get(status, status)))
    # The sentinel must be CAUGHT by a real failed check — not merely produce a
    # non-zero exit, which a crashed suite does too. Requiring "caught" is what
    # makes it a proof the harness actually tests the mutant, rather than a
    # formality a broken harness passes by dying on every import.
    if status != "caught":
        sys.exit("SENTINEL NOT CAUGHT (%s) — the harness is not testing the "
                 "mutant (grading against the pristine module, or every suite "
                 "is crashing on import). Refusing to report a score. (TRAP 2/3.)"
                 % status)

    print("\n%d candidate mutations, %d suites\n" % (len(muts), len(suites)))
    survivors = []
    unscored = []            # crashes + timeouts: the harness could not test these
    for n, (i, newline, lbl, code) in enumerate(muts):
        try:
            status, by = caught(lines, i, newline, suites)
        except subprocess.TimeoutExpired:
            status, by = "timeout", "?"
        tag = {"caught": "caught by %s" % by,
               "survived": "*** SURVIVED ***",
               "crash": "[CRASH in %s — harness did not test this mutant]" % by,
               "timeout": "[TIMEOUT — a suite hung; not a detection]"}[status]
        print("  %2d/%d line %-5d %-22s %-58s %s"
              % (n + 1, len(muts), i + 1, lbl, code, tag))
        if status == "survived":
            survivors.append((i + 1, lbl, code))
        elif status in ("crash", "timeout"):
            unscored.append((i + 1, lbl, status))
    # The score is over the mutations the harness could ACTUALLY test. Folding
    # crashes/timeouts into "caught" (the old behaviour) flattered the number and
    # hid a broken harness — a hang is evidence of something, but it is not a
    # check detecting wrong behaviour.
    n_measured = len(muts) - len(unscored)
    n_caught = n_measured - len(survivors)
    pct = round(100.0 * n_caught / n_measured) if n_measured else 0
    print("\n%d of %d MEASURED mutations survived — %d%% caught"
          % (len(survivors), n_measured, pct))
    if unscored:
        print("%d NOT SCORED (crashed or timed out — the harness could not test "
              "them; investigate, do not read as caught):" % len(unscored))
        for ln, lbl, why in unscored:
            print("   line %-5d %-22s [%s]" % (ln, lbl, why))
    for ln, lbl, code in survivors:
        print("   line %-5d %-22s %s" % (ln, lbl, code))
    print("\nNow triage every survivor BY MEASUREMENT (see the docstring): "
          "apply it to a copy and diff a battery of real calls against the "
          "true module. Do not read them and call them equivalent.")


if __name__ == "__main__":
    main()
