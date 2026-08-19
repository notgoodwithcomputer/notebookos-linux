#!/usr/bin/env python3
"""Display-free truthfulness checks for the aggregate gate runner."""
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_all_gates as runner  # noqa: E402


with tempfile.TemporaryDirectory(prefix="nb-runner-") as root:
    empty = os.path.join(root, "empty.py")
    Path(empty).write_text("", encoding="utf-8")
    verdict, detail, ran = runner.run_one("empty_mutant", empty)
    assert verdict == "DID NOT RUN" and ran == 0, (verdict, detail, ran)
    print("PASS a silent zero-exit suite cannot certify the build")

    banner = os.path.join(root, "banner.py")
    Path(banner).write_text('print("Starting checks...")\n', encoding="utf-8")
    verdict, detail, ran = runner.run_one("banner_mutant", banner)
    assert verdict == "DID NOT RUN" and ran == 0, (verdict, detail, ran)
    print("PASS setup output alone cannot certify the build")

    planned = os.path.join(root, "planned.py")
    Path(planned).write_text(
        'print("100 checks selected; 0 executed")\n', encoding="utf-8")
    verdict, detail, ran = runner.run_one("planned_mutant", planned)
    assert verdict == "DID NOT RUN" and ran == 100, (verdict, detail, ran)
    print("PASS planned check counts cannot substitute for completion evidence")

    first_assertion = os.path.join(root, "first_assertion.py")
    Path(first_assertion).write_text('print("PASS setup")\n', encoding="utf-8")
    verdict, detail, ran = runner.run_one("first_assertion_mutant", first_assertion)
    assert verdict == "DID NOT RUN" and ran == 0, (verdict, detail, ran)
    print("PASS one passing assertion is not completion evidence")

    zero_failures = os.path.join(root, "zero_failures.py")
    Path(zero_failures).write_text(
        'print("ok origin")\nprint("0 failures")\n', encoding="utf-8")
    verdict, detail, ran = runner.run_one("zero_failures_fixture", zero_failures)
    assert verdict == "PASS", (verdict, detail, ran)
    print("PASS an existing zero-failures reporter remains accepted")

    # Terminal ALL-PASSED verdicts in the forms dozens of suites already use.
    ratio = os.path.join(root, "ratio.py")
    Path(ratio).write_text(
        'print("PASS a check")\nprint("58/58 checks passed")\n', encoding="utf-8")
    verdict, detail, ran = runner.run_one("ratio_fixture", ratio)
    assert verdict == "PASS", (verdict, detail, ran)
    print("PASS an N/N-passed terminal is accepted")

    all_pass_count = os.path.join(root, "all_pass_count.py")
    Path(all_pass_count).write_text(
        'print("ok a")\nprint("BOOT SURFACE SELFTEST: 54 checks, all pass")\n',
        encoding="utf-8")
    verdict, detail, ran = runner.run_one("all_pass_count_fixture", all_pass_count)
    assert verdict == "PASS", (verdict, detail, ran)
    print("PASS an N-checks-all-pass terminal is accepted")

    zero_failed = os.path.join(root, "zero_failed.py")
    Path(zero_failed).write_text(
        'print("ok a")\nprint("46 checks, 0 failed")\n', encoding="utf-8")
    verdict, detail, ran = runner.run_one("zero_failed_fixture", zero_failed)
    assert verdict == "PASS", (verdict, detail, ran)
    print("PASS an N-checks-0-failed terminal is accepted")

    partial_ratio = os.path.join(root, "partial_ratio.py")
    Path(partial_ratio).write_text(
        'print("ok a")\nprint("55/58 checks passed")\n', encoding="utf-8")
    verdict, detail, ran = runner.run_one("partial_ratio_fixture", partial_ratio)
    assert verdict == "DID NOT RUN", (verdict, detail, ran)
    print("PASS a partial N/M-passed ratio cannot certify the build")

    lying_ratio = os.path.join(root, "lying_ratio.py")
    Path(lying_ratio).write_text(
        'print("FAIL something")\nprint("57/58 checks passed")\n',
        encoding="utf-8")
    verdict, detail, ran = runner.run_one("lying_ratio_fixture", lying_ratio)
    assert verdict in ("LIES", "DID NOT RUN"), (verdict, detail, ran)
    print("PASS a FAIL line beside a passed-count is never green")

    zero_fail_tally = os.path.join(root, "zero_fail_tally.py")
    Path(zero_fail_tally).write_text(
        'print("ok a")\nprint("106 checks: 106 PASS, 0 SKIP, 0 FAIL")\n',
        encoding="utf-8")
    verdict, detail, ran = runner.run_one("zero_fail_tally_fixture", zero_fail_tally)
    assert verdict == "PASS", (verdict, detail, ran)
    print("PASS a 0-FAIL all-passed tally is accepted")

    some_fail_tally = os.path.join(root, "some_fail_tally.py")
    Path(some_fail_tally).write_text(
        'print("FAIL a")\nprint("106 checks: 100 PASS, 0 SKIP, 6 FAIL")\n',
        encoding="utf-8")
    verdict, detail, ran = runner.run_one("some_fail_tally_fixture", some_fail_tally)
    assert verdict in ("LIES", "DID NOT RUN"), (verdict, detail, ran)
    print("PASS an N-FAIL tally with N>0 is never green")

    ok_contract = os.path.join(root, "ok_contract.py")
    Path(ok_contract).write_text(
        'print("ok a")\nprint("commands_selftest: OK (29 commands, 0 gaps)")\n',
        encoding="utf-8")
    verdict, detail, ran = runner.run_one("ok_contract_fixture", ok_contract)
    assert verdict == "PASS", (verdict, detail, ran)
    print("PASS a named selftest: OK terminal is accepted")

    speaking = os.path.join(root, "speaking.py")
    Path(speaking).write_text(
        'print("1 checks, all pass")\nprint("RESULT: ALL PASS")\n',
        encoding="utf-8")
    verdict, detail, ran = runner.run_one("speaking_fixture", speaking)
    assert verdict == "PASS" and ran == 1, (verdict, detail, ran)
    print("PASS a reporting zero-exit suite remains accepted")

    counted_result = os.path.join(root, "counted_result.py")
    Path(counted_result).write_text(
        'print("RESULT: 20 checks, ALL PASS")\n', encoding="utf-8")
    verdict, detail, ran = runner.run_one("counted_result_fixture", counted_result)
    assert verdict == "PASS" and ran == 20, (verdict, detail, ran)
    print("PASS a counted ALL PASS terminal remains accepted")

    colon_pass = os.path.join(root, "colon_pass.py")
    Path(colon_pass).write_text(
        'print("PASS: identity is preserved")\n', encoding="utf-8")
    verdict, detail, ran = runner.run_one("colon_pass_fixture", colon_pass)
    assert verdict == "DID NOT RUN", (verdict, detail, ran)
    print("PASS one PASS-colon assertion is not completion evidence")

    skip_only = os.path.join(root, "skip_only.py")
    Path(skip_only).write_text(
        'print("SKIP unavailable")\n'
        'print("GUEST DIVERGENCE: 0 fail, 0 warn, 1 skipped, 1 checks")\n',
        encoding="utf-8")
    verdict, detail, ran = runner.run_one("skip_only_mutant", skip_only)
    assert verdict == "DID NOT RUN" and ran == 1, (verdict, detail, ran)
    print("PASS skip-only counted output cannot certify the build")

    mixed_skip = os.path.join(root, "mixed_skip.py")
    Path(mixed_skip).write_text(
        'print("RESULT: ALL PASS")\n'
        'print("SKIP core release assertion unavailable")\n',
        encoding="utf-8")
    verdict, detail, ran = runner.run_one("mixed_skip_mutant", mixed_skip)
    assert verdict == "DID NOT RUN", (verdict, detail, ran)
    print("PASS a positive result cannot hide a skipped release assertion")

    informational_skip = os.path.join(root, "informational_skip.py")
    Path(informational_skip).write_text(
        'print("SKIP animation.py (hidden app — withheld from every launch '
        'surface; resumes on unhide)")\n'
        'print("RESULT: FULLY COVERED")\n', encoding="utf-8")
    verdict, detail, ran = runner.run_one(
        "i18n_coverage_check", informational_skip)
    assert verdict == "PASS", (verdict, detail, ran)
    print("PASS an allowlisted informational skip preserves final success")

    menu_skip = os.path.join(root, "menu_skip.py")
    Path(menu_skip).write_text(
        'print("SKIP animation.py (hidden app — menus unreachable; resumes '
        'on unhide)")\nprint("727 checks")\nprint("RESULT: PASS")\n',
        encoding="utf-8")
    verdict, detail, ran = runner.run_one("menu_conformance_check", menu_skip)
    assert verdict == "PASS" and ran == 727, (verdict, detail, ran)
    print("PASS hidden-app menu exclusions preserve measured success")

    suffix_pass = os.path.join(root, "suffix_pass.py")
    Path(suffix_pass).write_text(
        'print("cursor contract: PASS")\n', encoding="utf-8")
    verdict, detail, ran = runner.run_one("suffix_pass_fixture", suffix_pass)
    assert verdict == "PASS", (verdict, detail, ran)
    print("PASS a descriptive line ending in PASS remains accepted")

    for phrase in ("DID NOT PASS", "mutation was NOT PASSED",
                   "No assertions ran; assuming PASS"):
        negative_prose = os.path.join(root, "negative_prose.py")
        Path(negative_prose).write_text('print(%r)\n' % phrase,
                                        encoding="utf-8")
        verdict, detail, ran = runner.run_one(
            "negative_prose_mutant", negative_prose)
        assert verdict != "PASS", (phrase, verdict, detail, ran)
    print("PASS negative prose ending in PASS cannot certify the build")

    for phrase in ("RESULT: NO CHECKS RAN", "RESULT: EVERY CHECK FAILED",
                   "RESULT: THE IMAGE CARRIES NOTHING",
                   "RESULT: ALL REMAINING TESTS FAILED"):
        broad_prefix = os.path.join(root, "broad_prefix.py")
        Path(broad_prefix).write_text('print(%r)\n' % phrase,
                                      encoding="utf-8")
        verdict, detail, ran = runner.run_one(
            "broad_prefix_mutant", broad_prefix)
        assert verdict != "PASS", (phrase, verdict, detail, ran)
    print("PASS broad descriptive RESULT prefixes cannot hide failure")

    clean = os.path.join(root, "clean.py")
    Path(clean).write_text(
        'print("CLEAN: no undefined names across 76 files")\n',
        encoding="utf-8")
    verdict, detail, ran = runner.run_one("clean_fixture", clean)
    assert verdict == "PASS", (verdict, detail, ran)
    print("PASS a CLEAN audit result remains accepted")

    descriptive_result = os.path.join(root, "descriptive_result.py")
    Path(descriptive_result).write_text(
        'print("RESULT: every anchored term has one name")\n',
        encoding="utf-8")
    verdict, detail, ran = runner.run_one(
        "descriptive_result_fixture", descriptive_result)
    assert verdict == "PASS", (verdict, detail, ran)
    print("PASS an allowlisted descriptive RESULT remains accepted")

    for result in ("NOT BOOTABLE — DO NOT SHIP", "INCONSISTENT in yi",
                   "3 UNCOVERED (1 new)", "NOT PASS"):
        negative = os.path.join(root, "negative_result.py")
        Path(negative).write_text('print("RESULT: %s")\n' % result,
                                  encoding="utf-8")
        verdict, detail, ran = runner.run_one("negative_result_mutant", negative)
        assert verdict != "PASS", (result, verdict, detail, ran)
    print("PASS domain-negative RESULT forms can never certify the build")

    inconclusive = os.path.join(root, "inconclusive.py")
    Path(inconclusive).write_text(
        'print("RESULT: INCONCLUSIVE")\n', encoding="utf-8")
    verdict, detail, ran = runner.run_one("inconclusive_fixture", inconclusive)
    assert verdict == "DID NOT RUN", (verdict, detail, ran)
    print("PASS an inconclusive result cannot certify the build")

    late_crash = os.path.join(root, "late_crash.py")
    Path(late_crash).write_text(
        'print("1 checks, all pass")\nprint("RESULT: ALL PASS")\nraise SystemExit(1)\n',
        encoding="utf-8")
    verdict, _detail, ran = runner.run_one("late_crash_fixture", late_crash)
    assert verdict == "FAIL" and ran == 1, (verdict, ran)
    print("PASS a post-result nonzero exit remains a failure")

print("RESULT: ALL PASS")
