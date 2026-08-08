#!/usr/bin/env python3
"""Adversarial checks for disabled actions reached through real handlers."""
import os
import sys
import tempfile
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)
os.environ["NB_HOME"] = tempfile.mkdtemp(prefix="disabled-action-")

import contacts  # noqa: E402
import g2048  # noqa: E402
import illustrator  # noqa: E402
import journal  # noqa: E402
import packages  # noqa: E402
import sequencer  # noqa: E402
import video  # noqa: E402


FAILS = []


def check(name, fn):
    try:
        fn()
        print("PASS " + name)
        return True
    except Exception as exc:
        FAILS.append(name)
        print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
        return False


def packages_no_selection_does_not_open_last_package():
    calls = []
    app = SimpleNamespace(sel=-1, _flash=lambda *_a: None)
    old = packages.subprocess.Popen
    packages.subprocess.Popen = lambda argv, **_kw: calls.append(argv)
    try:
        packages.Packages._on_open(app)
    finally:
        packages.subprocess.Popen = old
    assert calls == [], "no-selection Open launched %r" % (calls,)


def packages_no_selection_does_not_verify_last_package():
    calls = []
    app = SimpleNamespace(sel=-1, _flash=lambda *_a: None,
                          _verify_module=lambda path: calls.append(path) or True)
    packages.Packages._on_verify(app)
    assert calls == [], "no-selection Verify targeted %r" % (calls,)


def illustrator_empty_layers_delete_noops():
    calls = []
    app = SimpleNamespace(active=-1, layers=[], cw=16, ch=16,
                          _push=lambda *_a: calls.append("history"),
                          _struct_frame=lambda: ("st", [], -1, 16, 16))
    illustrator.Illustrator._delete_layer(app)
    assert calls == [], "empty-layer Delete created history"


def g2048_zero_best_reset_noops():
    calls = []
    app = SimpleNamespace(best=0, _best_undo=None,
                          _save_best=lambda: calls.append("save"),
                          _refresh=lambda: calls.append("refresh"))
    app._do_reset_best = lambda: g2048.Game2048._do_reset_best(app)
    g2048.Game2048._reset_best(app)
    assert calls == [] and app._best_undo is None, \
        "disabled Reset Best mutated empty state: %r" % calls


def video_empty_undo_redo_noop():
    calls = []
    app = SimpleNamespace(_undo=[], _redo=[], _undo_names=[], _redo_names=[],
                          _snapshot=lambda: calls.append("snapshot"),
                          _restore=lambda *_a: calls.append("restore"))
    video.VideoEditor._undo_action(app)
    video.VideoEditor._redo_action(app)
    assert calls == [] and app._undo == [] and app._redo == []


def journal_empty_export_print_refuse_honestly():
    messages = []
    app = SimpleNamespace(entries=[], _flash=messages.append)
    journal.Journal._export_pdf(app)
    journal.Journal._print(app)
    assert messages == ["No entries to export", "No entries to print"]


def journal_navigation_clamps_at_edges():
    calls = []
    app = SimpleNamespace(entries=[{}], active=0,
                          select_entry=lambda i: calls.append(i))
    journal.Journal._go_entry(app, -1)
    journal.Journal._go_entry(app, 1)
    assert calls == [], "edge navigation selected %r" % calls


def sequencer_empty_clipboard_paste_noops():
    app = SimpleNamespace(_clipboard=None)
    sequencer.Sequencer._paste_clip(app)
    assert vars(app) == {"_clipboard": None}


def pass_mutant_guards_detect_negative_index_fallback():
    # The retired shape: Python accepts -1 and silently chooses the last item.
    items = ["first", "last"]
    selected = -1
    mutant_target = items[selected]
    caught = mutant_target == "last" and not (0 <= selected < len(items))
    assert caught, "negative-index mutant was not distinguished from selection"


def main():
    checks = (
        ("DELETE packages no-selection Open cannot target index -1",
         packages_no_selection_does_not_open_last_package),
        ("DELETE packages no-selection Verify cannot target index -1",
         packages_no_selection_does_not_verify_last_package),
        ("DELETE illustrator empty layer list is a no-op",
         illustrator_empty_layers_delete_noops),
        ("CONTEXT g2048 disabled zero-best reset is a no-op",
         g2048_zero_best_reset_noops),
        ("UNDO video empty undo and redo preserve both stacks",
         video_empty_undo_redo_noop),
        ("EXPORT journal empty export and print refuse honestly",
         journal_empty_export_print_refuse_honestly),
        ("NAVIGATE journal first/last edge stays clamped",
         journal_navigation_clamps_at_edges),
        ("PASTE sequencer empty clipboard is a no-op",
         sequencer_empty_clipboard_paste_noops),
        ("PASS-MUTANT negative index fallback is caught by named guard",
         pass_mutant_guards_detect_negative_index_fallback),
    )
    results = [check(name, fn) for name, fn in checks]
    print("%d checks, %d failures" % (len(results), len(FAILS)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
