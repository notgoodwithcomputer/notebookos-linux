#!/usr/bin/env python3
"""Gate the rendered leading edge of every structural navigation sidebar.

Unlike grid_check's legacy source-name scan, this constructs each in-scope app
and measures the content column using edge_alignment_census.  Run through
tools/guestrun.sh so GTK uses the guest graphics environment.

The debt ledger is an exact, two-way ratchet: an undeclared measured deviation
is a regression, and a declaration which no longer exactly matches the render
is stale debt.  Reasons are data, not comments, so unexplained entries fail.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import design_tokens as dt  # noqa: E402
import edge_alignment_census as census  # noqa: E402

SIZE = (1024, 722)

# This is an explicit product-shape inventory.  Scope cannot disappear merely
# because an app chose a different constant name (or no constant at all).
STRUCTURAL_SIDEBARS = {
    "academics": "classes/lectures navigation beside the active term view",
    "bills": "bill navigation list beside the selected bill detail",
    "finder": "Devices/Places navigation beside the file view",
    "music": "library views and playlists navigation beside the track view",
    "novel": "manuscript/chapter navigation beside the editor",
    "packages": "package-view navigation beside the package table",
    "tasks": "Lists navigation beside the task list (not the schedule rail)",
    "workout": "week/navigation sidebar beside the workout content",
}

# Exact measured exceptions: app -> (content-leading x, written reason).
# Keep this separate from design_tokens.RAIL_EXCEPTIONS: that static mapping
# describes named source constants and includes specialist docks, while this
# gate governs only structural navigation sidebars.
RAIL_EXCEPTIONS = {
}

# Grandfathered rendered debt: app -> (content-leading x, written reason).
RAIL_DEBT = {
    "academics": (220, "responsive classes sidebar predates the 240px rail"),
    "bills": (252, "legacy wide bills list; 252 is not yet a proven second rail"),
    "finder": (190, "legacy compact Devices/Places sidebar has not converged"),
    # NOT un-converged legacy — RESPONSIVE BY DESIGN: music sizes its
    # sidebar as max(200, min(264, screen_w * 0.17)), so it has no single
    # width to put on the rail. Converging it would DELETE that behaviour,
    # which is a design decision and not this gate's to make. Left as debt
    # so it stays visible until somebody decides whether the rail permits a
    # responsive sidebar at all.
    "music": (206, "RESPONSIVE BY DESIGN (screen_w*0.17 clamped 200-264); converging would remove that behaviour — needs a design call, not a width edit"),
}

_FAILS = []
_CHECKS = [0]


def _check(ok, msg):
    _CHECKS[0] += 1
    if not ok:
        _FAILS.append(msg)
        print("FAIL  %s" % msg)


def _validate_declarations():
    for app, reason in sorted(STRUCTURAL_SIDEBARS.items()):
        _check(isinstance(reason, str) and bool(reason.strip()),
               "STRUCTURAL_SIDEBARS[%r] must carry a written reason" % app)
    for label, ledger in (("RAIL_EXCEPTIONS", RAIL_EXCEPTIONS),
                          ("RAIL_DEBT", RAIL_DEBT)):
        for app, entry in sorted(ledger.items()):
            valid = (isinstance(entry, tuple) and len(entry) == 2 and
                     isinstance(entry[0], int) and
                     isinstance(entry[1], str) and bool(entry[1].strip()))
            _check(valid, "%s[%r] must be (measured_x, written reason)" %
                   (label, app))
            _check(app in STRUCTURAL_SIDEBARS,
                   "%s[%r] is not in the structural-sidebar inventory" %
                   (label, app))
    overlap = set(RAIL_EXCEPTIONS) & set(RAIL_DEBT)
    for app in sorted(overlap):
        _check(False, "%r cannot be both a rail exception and rail debt" % app)


def check_rails():
    _validate_declarations()
    seen_debt = set()
    seen_exceptions = set()
    observed = set()
    gtk_succeeded = 0
    for app in sorted(STRUCTURAL_SIDEBARS):
        row = census.measure_app(app, *SIZE)
        gtk_ok = row.get("gtk_init_check") is True
        gtk_succeeded += int(gtk_ok)
        if not gtk_ok:
            _check(False, "%s: Gtk.init_check() did not succeed; no rendered "
                   "rail measurement is valid" % app)
            continue
        if "error" in row:
            _check(False, "%s: rendered rail measurement failed: %s" %
                   (app, row["error"]))
            continue
        item = row.get("edges", {}).get("content.leading_x", {})
        if item.get("state") != "measured":
            _check(False, "%s content.leading_x could not be measured: %s" %
                   (app, item.get("reason", "missing observation")))
            continue
        value = item["value"]
        observed.add(app)
        if value == dt.RAIL:
            _CHECKS[0] += 1
            continue
        exception = RAIL_EXCEPTIONS.get(app)
        if exception and exception[0] == value:
            seen_exceptions.add(app)
            _CHECKS[0] += 1
            continue
        debt = RAIL_DEBT.get(app)
        if debt and debt[0] == value:
            seen_debt.add(app)
            _CHECKS[0] += 1
            continue
        _check(False, "%s content.leading_x = %d is off RAIL=%d and not "
               "excepted or in debt" % (app, value, dt.RAIL))

    for app in sorted((set(RAIL_DEBT) & observed) - seen_debt):
        _check(False, "STALE DEBT: RAIL_DEBT[%r] no longer matches the "
               "source — it was fixed; delete the entry" % app)
    for app in sorted((set(RAIL_EXCEPTIONS) & observed) - seen_exceptions):
        _check(False, "STALE EXCEPTION: RAIL_EXCEPTIONS[%r] no longer matches "
               "the rendered content edge — delete or update the entry" % app)
    print("DISPLAY/Gtk.init_check(): %s (%d/%d measured apps succeeded)" %
          ("succeeded" if gtk_succeeded == len(STRUCTURAL_SIDEBARS) else "FAILED",
           gtk_succeeded, len(STRUCTURAL_SIDEBARS)))


def main():
    check_rails()
    if _FAILS:
        print("RESULT: FAILED — %d of %d checks (measured content-leading rail)" %
              (len(_FAILS), _CHECKS[0]))
        return 1
    print("PASS  measured rail conformance: %d checks" % _CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
