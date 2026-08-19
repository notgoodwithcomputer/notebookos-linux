#!/usr/bin/env python3
"""nbi18n's printf splitter — the piece every translated format string goes
through.

WHY THIS EXISTS. _split_spec decided which pieces of a string were
placeholders by asking whether a piece STARTED WITH "%". That is the right
answer for all but one shape, and the exception shipped: `%(name)s` is
deliberately not matched by the spec regex, so a string beginning with one
produced a single unmatched piece that was entirely literal text and began
with "%" — and the whole sentence was classified as one giant placeholder.

The same text mid-string was classified correctly, so the defect depended on
WORD ORDER. English rarely opens with the name; German, Esperanto, Hindi,
Italian, Polish and Chinese translations of "Installed %(name)s. Open it from
Applications." all naturally do. The result was a red placeholder gate on
correct translations, and — quieter — those catalog entries dropped silently
out of the substituted-string format table, because the table requires the
translation to carry the same specs as the source and this one appeared to
carry a spec the source did not have.

re.split with a capturing group puts the matched specs at the ODD indices.
Position is the fact; "starts with %" was a proxy for it.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

from nbi18n import _split_spec                                  # noqa: E402

passed = failed = 0


def check(name, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print("PASS " + name)
    else:
        failed += 1
        print("FAIL %s\n       got  %r\n       want %r" % (name, got, want))


LIT = "lit"
SPEC = "spec"

# A named placeholder is invisible to the spec regex by design, so a string
# carrying one is literal all the way through — wherever it sits.
check("a named placeholder mid-string is literal",
      _split_spec("head %(name)s tail"),
      [(LIT, "head %(name)s tail")])
check("a named placeholder AT THE START is literal too",
      _split_spec("%(name)s tail"),
      [(LIT, "%(name)s tail")])
check("a real translation that fronts the name stays literal",
      _split_spec("%(name)s installiert. Weiter."),
      [(LIT, "%(name)s installiert. Weiter.")])

# Ordinary specs still split, at the start and in the middle.
check("a leading ordinary spec is a spec",
      _split_spec("%s tail"), [(SPEC, "%s"), (LIT, " tail")])
check("specs and literals alternate",
      _split_spec("a %s and a %d"),
      [(LIT, "a "), (SPEC, "%s"), (LIT, " and a "), (SPEC, "%d")])
check("a width-and-precision spec is one spec",
      _split_spec("%-6.2f kg"), [(SPEC, "%-6.2f"), (LIT, " kg")])

# %% is a literal per cent sign, never a placeholder — including first.
check("an escaped per cent is a literal per cent",
      [p for _k, p in _split_spec("100%% done")], ["100", "%", " done"])
check("a leading escaped per cent is a literal per cent",
      _split_spec("%% off")[0], (LIT, "%"))

# The empty pieces re.split leaves around an anchored match are dropped.
check("no empty literal is emitted", _split_spec("%s"), [(SPEC, "%s")])
check("an empty string yields nothing", _split_spec(""), [])

print("\n%d/%d checks passed" % (passed, passed + failed))
print("RESULT: %s" % ("PASS" if not failed else "FAILED"))
raise SystemExit(1 if failed else 0)
