#!/usr/bin/env python3
"""
One command, one report, non-zero on any failure.

There are 179 `*_selftest.py` files in this directory and nothing ran them
together, so an individually-red suite could sit red indefinitely and a
regression could survive several sessions. Both happened:

* `music_transport_accessibility_selftest` was failing on a FALSE POSITIVE — a
  comment explaining that Gtk.EventBox is no longer used tripped a
  `"Gtk.EventBox" not in factory` check — and had presumably been red since the
  transport was fixed.
* `music_playlist_selftest` was failing on state its OWN earlier runs left
  behind: it pins a throwaway NB_HOME with `os.environ.setdefault`, but
  `guestrun.sh` exports NB_HOME already, so the setdefault never fired and
  sixteen playlists had accumulated in the shared home.
* A change of mine broke `music_lifecycle_selftest` and I did not notice for two
  sessions, because I ran the suite I had just written and not the ones next
  to it.

Isolation
---------
Every test gets its OWN NB_HOME, unset from the environment first so a suite
using `setdefault` still lands in a private directory. That is a guard, not a
licence: 36 selftests use `setdefault` and each of them is one careless caller
away from sharing state again.

Reading the result
------------------
Exit status decides. A suite that exits 0 while printing FAIL is itself broken,
so the output is scanned too and a disagreement is reported as `LIES` rather
than quietly believed — a runner that trusts a broken reporter is no better
than not running it.

    python3 run_all_gates.py                 # everything, summary at the end
    python3 run_all_gates.py -v              # print EVERY suite's tail
                                             # (a failing one always prints)
    python3 run_all_gates.py --only music    # substring filter
    python3 run_all_gates.py --update-baseline
Exit status is nonzero if any suite fails that the baseline does not excuse.
"""
import os
import re
import sys
import glob
import shutil
import tempfile
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GUESTRUN = os.path.join(HERE, "guestrun.sh")
BASELINE = os.path.join(HERE, "gates_baseline.txt")

# Suites that are not self-checking: renderers, shot tools and interactive
# harnesses that exit 0 having produced a file for a human to look at.
NOT_A_GATE = re.compile(r"(shots?|shot)\.py$|^(appshot|controlshot|dialogshot|"
                        r"uishot|crop|i18n_shot|gbasdk_shots|language_shots)\.py$")
# Anything needing hardware, a network, or many minutes.
SLOW_OR_HARDWARE = {
    "boot_test", "drive_proof",
}
# Gates that are not *_selftest.py. EXPLICITLY NAMED, not globbed: several
# *_check / *_sweep tools in this directory want arguments or produce reports
# for a human rather than verdicts, so a checker joins the run by being named
# here — and a new gate should be named here the day it is born, because a
# gate outside the one command protects nothing (the release condition is
# "one command, exit 0, covering every gate in tools/"). A named gate whose
# file has gone missing is deliberately left in the run so it shows up as a
# crash instead of silently narrowing coverage.
CHECK_GATES = [
    "anchored_term_check", "ascii_css_check", "button_contrast_check",
    "catalog_dialect_check", "catalog_script_check", "css_parse_check",
    "data_stress_sweep", "dead_setting_check", "ellipsis_sweep",
    "disabled_reason_check",
    # Static: a menu item that is enabled, does nothing, and says
    # nothing. Its ledger carries 96 UNVERIFIED guards — read the note
    # there before trusting a green result to mean the class is closed.
    "silent_refusal_check",
    # Static: a slider that offers values its own apply will refuse.
    # Connects only 2 of 46 ranged controls — read its coverage note.
    "offered_range_check",
    # Static: every launchable app registered everywhere its neighbours
    # are, in BOTH directions — missing, and stale after a removal.
    "app_registration_check",
    # Static, both directions: a shortcut printed that nothing answers,
    # and a key bound that no menu shows. Analyses 35 of 36 handlers.
    "accelerator_promise_check",
    # Static: a preference applied to the app's own process while the
    # page reports it as done. 32 settings apply AND persist; 3 reach
    # the mixer through amixer and are ledgered with what they do.
    "setting_scope_check",
    "frame_pacing_check", "grid_check",
    # §E4 check 5 (no diagonal travel). Separate from grid_check because that
    # one owns the STATIC constants and is edited whenever a sidebar
    # converges, while this reads the MOTION inventory — two ratchets with two
    # burn-downs in one file is how a ledger ends up with an edit conflict.
    "grid_e4_travel_check", "grid_e4_hairline_check",
    # The MEASURED rail gate. grid_check's rail scan greps for constants named
    # SIDEBAR_W/PANEL_W/DOCK_W/RAIL_W, so four apps whose width is inline or
    # computed were never checked at all. This one constructs each app and
    # measures where content actually starts.
    "rail_measured_check", "grid_e4_rest_check", "image_capability_check",
    # The code-vs-catalog half of i18n. i18n_check grades the catalog it is
    # given and reported 17 x 100% while three strings added that morning
    # were missing from every language — a key nobody wrote down is not a
    # key it iterates over. This one starts from what the source can SAY.
    "i18n_source_coverage",
    "jargon_sweep",
    "language_content_check", "menu_conformance_check", "minsize_sweep",
    "motion_inventory_check", "page_switch_consistency_check",
    "picom_conf_check", "rtl_check",
    "self_attr_audit", "term_consistency_check", "theme_transition_check",
    # Drives each Article G transition and reads its real frame trace. It is
    # the gate that caught GrowCard arriving on the wrong token, which no
    # selftest had pinned — motion_inventory_check only reads the RECORDED
    # pacing, so without this in the run nothing re-measures it. Exits non-zero
    # only when a MEASURED verdict leaves its band; incomplete coverage is
    # reported, not failed.
    "transition_pacing_probe",
    "voice_check",
]
PER_TEST_TIMEOUT = 300
# Suites that legitimately need longer. A timeout reported as a failure is a
# lie about the code, so a slow suite gets the time it needs or it does not
# belong in the run at all. language_course plays EVERY skill of every shipped
# course through the real widgets — 8800 generated exercises before it answers
# the first question — which is the only way to catch a lesson that cannot be
# finished.
#
# 600, not 2400. It took 29m44s until its lesson timers were compressed
# (the suite clamps _lesson_later's duration; see its header) and now runs
# in 2m15 with byte-identical output. The ceiling is kept close to the real
# cost on purpose: a generous timeout does not make a suite pass, it only
# delays the moment a genuine hang is noticed.
TIMEOUTS = {"language_course_selftest": 600,
            # spawns every app at two sizes, re-measuring near-budget apps in
            # the width-heavy languages; the honest cost of the sweep
            "minsize_sweep": 900,
            # ellipsis_sweep spawns one subprocess PER APP to inspect mapped
            # labels — same shape as minsize_sweep, and it timed out at the
            # 300s default during a full run under load 6 (four sessions +
            # Codex). A timeout under contention is not a defect in the suite
            # (blind-spot #8); give the per-app-subprocess sweeps room.
            "ellipsis_sweep": 900,
            "data_stress_sweep": 600}

# A suite's own failure vocabulary — NOT "Traceback" or "ERROR". Several suites
# here provoke exceptions on purpose (jobs_selftest raises inside a callback to
# prove the exception does not escape, and GLib prints the traceback to stderr
# as designed), so matching those made a passing suite look like it was lying
# about its exit code. A suite that genuinely crashes exits non-zero and is
# caught by the FAIL branch below; this pattern only has to catch the narrower
# case of a suite that reports failure and then exits 0 anyway.
FAILWORD = re.compile(r"^(FAIL[ :]|FAILED:|RESULT: (FAILED|SOME FAILED))", re.M)

# How many assertions a suite actually got through. A suite that CRASHES on its
# first test and one that runs everything and fails a single assertion look
# identical in a pass/fail ledger, and they are not remotely the same debt:
# boot_surface_selftest died on line 76 of the first of its three tests, so two
# whole sections — including the guard against a USB stick labelled "." mounting
# ON /media and hiding every other volume — never ran at all, behind a baseline
# entry that read as one excused failure.
COUNTED = re.compile(r"(\d+)\s+checks?\b|checks?[,:]\s*(\d+)|"
                     r"(\d+)\s*/\s*\d+\s+checks", re.I)


def check_count(out):
    """The largest check-count the suite reported, or 0 if it said none."""
    best = 0
    for m in COUNTED.finditer(out):
        for g in m.groups():
            if g and int(g) > best:
                best = int(g)
    return best


def discover(only=None):
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "*_selftest.py"))):
        name = os.path.basename(path)[:-3]
        if NOT_A_GATE.search(os.path.basename(path)):
            continue
        if name in SLOW_OR_HARDWARE:
            continue
        if only and only not in name:
            continue
        out.append((name, path))
    for name in CHECK_GATES:
        if only and only not in name:
            continue
        # no existence check on purpose — a missing named gate must crash
        # the run, not shrink it
        out.append((name, os.path.join(HERE, name + ".py")))
    return out


def load_baseline():
    known = {}
    if not os.path.exists(BASELINE):
        return known
    with open(BASELINE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, _, why = line.partition("\t")
            known[name.strip()] = why.strip()
    return known


def run_one(name, path):
    limit = TIMEOUTS.get(name, PER_TEST_TIMEOUT)
    home = tempfile.mkdtemp(prefix="nbgate-%s-" % name[:20])
    env = dict(os.environ)
    env["NB_HOME"] = home            # set, so even a setdefault suite is private
    try:
        p = subprocess.run([GUESTRUN, "python3", path],
                           capture_output=True, text=True,
                           timeout=limit, env=env, cwd=ROOT)
        out = (p.stdout or "") + (p.stderr or "")
        rc = p.returncode
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "no result in %ds" % limit, 0
    finally:
        shutil.rmtree(home, ignore_errors=True)

    said_fail = bool(FAILWORD.search(out)) or "SOME FAILED" in out
    ran = check_count(out)
    if rc == 0 and said_fail:
        return "LIES", _tail(out), ran
    if rc != 0:
        return "FAIL", _tail(out), ran
    return "PASS", "", ran


def _tail(out, n=6):
    lines = [l for l in out.splitlines() if l.strip()]
    return "\n".join("      " + l[:110] for l in lines[-n:])


def main(argv):
    verbose = "-v" in argv
    update = "--update-baseline" in argv
    only = None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]

    tests = discover(only)
    if not tests:
        print("no suites matched")
        return 2
    known = load_baseline()

    results = {}
    width = max(len(n) for n, _ in tests)
    for i, (name, path) in enumerate(tests, 1):
        verdict, detail, ran = run_one(name, path)
        results[name] = (verdict, detail, ran)
        mark = verdict
        if verdict != "PASS" and name in known:
            mark = verdict + " (known)"
        # flush: this run takes the better part of an hour and Python buffers
        # stdout when it is piped, so without it the operator sees nothing at
        # all until the very end and cannot tell a slow suite from a hung one.
        print("[%3d/%d] %-*s %s" % (i, len(tests), width, name, mark),
              flush=True)
        # A failure ALWAYS prints its tail, not only under -v. An hour-long run
        # that ends "2 NEW FAILURE(S)" and nothing else forces the whole thing
        # to be run again just to find out what broke — which is what happened,
        # and the re-run cost another forty minutes. -v adds the tail for
        # PASSING suites; a failure without its evidence is not a report.
        if detail and verdict != "PASS":
            print(detail)
        elif verbose and detail:
            print(detail)

    bad = {n: v for n, (v, _d, _r) in results.items() if v != "PASS"}
    new = {n: v for n, v in bad.items() if n not in known}
    # Only suites that ACTUALLY RAN can be said to pass. `results.get(n, "PASS")`
    # treated a suite excluded by --only as passing, so `--only foo` cheerfully
    # advised pruning every other baseline entry — a checker that cannot tell
    # "did not fail" from "was never asked" is the same fault this runner exists
    # to catch elsewhere.
    fixed = [n for n in known if n in results and results[n][0] == "PASS"]
    # A baselined suite that now runs FEWER assertions has regressed even though
    # it is still "known failing" — that is the crash-hides-the-rest shape.
    shrunk = []
    for n, why in known.items():
        m = re.search(r"ran=(\d+)", why or "")
        if m and n in results and results[n][2] < int(m.group(1)):
            shrunk.append((n, int(m.group(1)), results[n][2]))

    print("\n%d suites: %d pass, %d fail (%d already in the baseline)"
          % (len(tests), len(tests) - len(bad), len(bad), len(bad) - len(new)))
    if fixed:
        print("\n%d baseline entr(y/ies) now PASS — prune them:" % len(fixed))
        for n in sorted(fixed):
            print("   " + n)
    if shrunk:
        print("\n%d baselined suite(s) now run FEWER checks — something is "
              "crashing earlier than it was:" % len(shrunk))
        for n, was, now in shrunk:
            print("   %-*s %d -> %d checks" % (width, n, was, now))
    if new:
        print("\n%d NEW failure(s):" % len(new))
        for n in sorted(new):
            print("   %-*s %s" % (width, n, results[n][0]))
            if not verbose:
                print(results[n][1])

    if update:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            fh.write("# Suites failing as of the last --update-baseline.\n"
                     "# This file is a LEDGER OF DEBT, not a permission slip:\n"
                     "# every line is a gate that is not protecting anything.\n"
                     "# Shrink it.\n")
            for n in sorted(bad):
                # ran= is the point: it makes a crash distinguishable from a
                # failing assertion the next time this file is read.
                fh.write("%s\t%s ran=%d\n" % (n, bad[n], results[n][2]))
        print("\nbaseline rewritten: %d entr(y/ies)" % len(bad))
        return 0

    if new or shrunk:
        print("\nRESULT: %d NEW FAILURE(S)%s"
              % (len(new), ", %d suite(s) shrunk" % len(shrunk) if shrunk else ""))
        # NAME them. An hour-long run that ends in a bare count makes the
        # reader scan 193 lines for the two that say FAIL, and says nothing at
        # all about what went wrong — which is how a real failure gets filed
        # as "probably flaky".
        for n in sorted(new):
            print("  %-44s %s  (ran %d check(s))"
                  % (n, new[n], results[n][2]))
        for n in sorted(shrunk):
            print("  %-44s SHRUNK" % n)
        return 1
    if bad:
        print("\nRESULT: all remaining failures are in the baseline")
        return 0
    print("\nRESULT: ALL GATES PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
