#!/usr/bin/env python3
"""finder_copy_collision_selftest — can two copies claim one destination?

    python3 tools/finder_copy_collision_selftest.py

WHY THIS FILE EXISTS
A copy larger than COPY_ASYNC_BYTES runs on a worker thread: `_copy` returns
the instant the thread is started, and the destination file does not exist
until the worker gets round to opening it. `_paste` and `_duplicate_selected`
picked their destination name by asking the filesystem what was already there,
so inside that window the name still looked free — and a second Ctrl+V chose
*the same path*. Two workers then wrote one file, both status lines said
"Copied ... here", and cancelling either card ran its "leave nothing
half-copied behind" cleanup over the other job's finished copy. The observed
end state was an empty destination folder, one "Copied" message, and an Undo
entry pointing at a file that was no longer there.

The destination is now claimed for as long as the job owns it
(`Finder._inflight`, consulted by `Finder._taken`), so the second paste picks
"name copy.ext" the way it already would have for a file that was on disk.

No display is needed. The name-choosing path (`_paste`, `_unique_path`,
`_copy`) and the copy worker (`_copy_job`) are the shipped ones; only the
progress *dialog* of `_copy_async` is stood in for, because it needs GTK — the
stand-in keeps that method's real contract (return at once, create the
destination later on a worker, report through on_done, clean up on failure).
The failure-and-cleanup case below runs entirely through the shipped
synchronous path, with no stand-in at all.
"""
import os
import shutil
import stat
import sys
import tempfile
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)
os.environ.setdefault("NB_LANG", "en")
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nb-copyrace-home-"))

import finder                                                   # noqa: E402

CHECKS = [0]
FAILURES = []


def check(cond, what):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        FAILURES.append(what)


class Win(finder.Finder):
    """The file-operation paths only, with no window."""

    def __init__(self, dest_dir):                              # noqa: D107
        self.rel = "dst"
        self._dest = dest_dir
        self._clipboard = None
        self._inflight = set()
        self._undo = None
        self.status = []
        self.threads = []
        self.jobs = {}                 # dst -> worker state (for Cancel)

    def abspath(self, _rel):
        return self._dest

    def get_mapped(self):              # the async path is the one under test
        return True

    def load(self, *a, **k):
        pass

    def _update_paste(self):
        pass

    def _flash_status(self, msg, restore_ms=2400):
        self.status.append(msg)

    def _flash_undoable(self, msg):
        self.status.append(msg)

    def _copy_async(self, src, dst, on_done):
        """finder._copy_async minus the GTK progress card. Same contract: the
        worker is started and this returns; dst appears only later."""
        state = {"cancel": False, "done": 0,
                 "total": max(1, self._copy_size(src)),
                 "file": "", "error": None, "cancelled": False,
                 "finished": False}
        self.jobs[dst] = state

        def work():
            GATE.wait(30)              # the scheduling window, made explicit
            try:
                self._copy_job(src, dst, state)
            except finder._CopyCancelled:
                state["cancelled"] = True
            except (OSError, shutil.Error) as exc:
                state["error"] = self._copy_error_text(exc)
            ok = not (state["cancelled"] or state["error"])
            if not ok:                 # verbatim from _copy_async.finish()
                try:
                    self._undo_remove(dst)
                except (OSError, shutil.Error):
                    pass
            on_done(ok)
            if not ok:
                self._flash_status(
                    finder._t("Copy stopped. Nothing was changed.")
                    if state["cancelled"] else state["error"])

        t = threading.Thread(target=work, daemon=True)
        self.threads.append(t)
        t.start()


GATE = threading.Event()
BIG = b"A" * (finder.COPY_ASYNC_BYTES + 4096)


def new_case(root, name):
    """A source file big enough for the async path, and an empty folder."""
    src_dir = os.path.join(root, name + "-src")
    dst_dir = os.path.join(root, name + "-dst")
    os.makedirs(src_dir)
    os.makedirs(dst_dir)
    src = os.path.join(src_dir, "report.pdf")
    with open(src, "wb") as fh:
        fh.write(BIG)
    win = Win(dst_dir)
    win._clipboard = (src, False)
    return win, src, dst_dir


def run_jobs(win):
    GATE.set()
    for t in win.threads:
        t.join(30)
    GATE.clear()


def main():
    root = tempfile.mkdtemp(prefix="nb-copyrace-")
    try:
        print("\n-- two pastes inside the async window take two names")
        win, src, dst_dir = new_case(root, "both")
        win._paste()
        win._paste()                   # before the first worker has written
        check(not os.path.exists(os.path.join(dst_dir, "report.pdf")),
              "the intended final name is absent while copying")
        staged = [n for n in os.listdir(dst_dir) if n.startswith(".nbcopy-")]
        check(len(staged) == 2,
              "each worker owns a private hidden staging file: %r" % staged)
        run_jobs(win)
        landed = sorted(os.listdir(dst_dir))
        check(landed == ["report copy.pdf", "report.pdf"],
              "both copies landed, under their own names: %r" % (landed,))
        check(all(os.path.getsize(os.path.join(dst_dir, n)) == len(BIG)
                  for n in landed),
              "and both are whole — one shared file would be two writers "
              "interleaved in one stream")
        check(os.path.getsize(src) == len(BIG), "the source is untouched")
        check(win._inflight == set(),
              "every claim is released, so the names are reusable: %r"
              % (win._inflight,))

        print("\n-- cancelling one card does not delete the other's copy")
        win, src, dst_dir = new_case(root, "cancel")
        win._paste()
        win._paste()
        first = os.path.join(dst_dir, "report.pdf")
        first_stage = next(iter(win.jobs))
        win.jobs[first_stage]["cancel"] = True  # the user presses Cancel
        run_jobs(win)
        check(not os.path.exists(first),
              "the cancelled copy is cleaned up, not left half-written")
        second = os.path.join(dst_dir, "report copy.pdf")
        check(os.path.exists(second) and os.path.getsize(second) == len(BIG),
              "the copy that finished is still there, whole")
        check(win._undo is not None and os.path.exists(win._undo["args"][0]),
              "Undo points at something that actually exists: %r"
              % (win._undo and win._undo["args"],))
        check(any("Copy stopped" in s for s in win.status),
              "and the cancel is reported: %r" % (win.status,))

        print("\n-- a copy that fails part-way leaves nothing behind")
        # Shipped synchronous path, no stand-in: a folder whose second file
        # cannot be read, so the copy dies after the first one is written.
        src_dir = os.path.join(root, "part-src")
        dst_dir = os.path.join(root, "part-dst")
        os.makedirs(os.path.join(src_dir, "papers"))
        for nm, body in (("a.txt", b"first"), ("b.txt", b"second")):
            with open(os.path.join(src_dir, "papers", nm), "wb") as fh:
                fh.write(body)
        blocked = os.path.join(src_dir, "papers", "b.txt")
        os.chmod(blocked, 0)
        os.makedirs(dst_dir)
        win = Win(dst_dir)
        win._clipboard = (os.path.join(src_dir, "papers"), False)
        results = []
        win._copy(os.path.join(src_dir, "papers"),
                  os.path.join(dst_dir, "papers"), results.append)
        os.chmod(blocked, stat.S_IRUSR | stat.S_IWUSR)
        check(results == [False], "the copy reports that it did not finish")
        check(not os.path.exists(os.path.join(dst_dir, "papers")),
              "no part-copied folder is left under the final name: %r"
              % (sorted(os.listdir(dst_dir)),))
        check(sorted(os.listdir(os.path.join(src_dir, "papers")))
              == ["a.txt", "b.txt"], "the source survives the failure whole")
        check(win._undo is None,
              "and a failed copy installs no Undo: %r" % (win._undo,))
        said = " ".join(win.status)
        check(bool(said) and "Errno" not in said and "Error(" not in said
              and "Traceback" not in said,
              "the reason is a sentence, not a traceback: %r" % (win.status,))
        check(win._inflight == set(),
              "the claim is released on the failure path too: %r"
              % (win._inflight,))

        print("\n-- an external item arriving at commit is never replaced")
        src_dir = os.path.join(root, "arrival-src")
        dst_dir = os.path.join(root, "arrival-dst")
        os.makedirs(src_dir)
        os.makedirs(dst_dir)
        src = os.path.join(src_dir, "notes.txt")
        dst = os.path.join(dst_dir, "notes.txt")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write("copy source")
        win = Win(dst_dir)
        real_publish = win._rename_noreplace
        real_replace = finder.os.replace

        def arrive_then_publish(stage, destination):
            with open(destination, "w", encoding="utf-8") as fh:
                fh.write("external arrival")
            return real_publish(stage, destination)

        win._rename_noreplace = arrive_then_publish
        finder.os.replace = lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("staged publication used replacing rename"))
        results = []
        try:
            win._copy(src, dst, results.append)
        finally:
            finder.os.replace = real_replace
        with open(dst, encoding="utf-8") as fh:
            arrival = fh.read()
        check(results == [False],
              "the copy reports that its late collision was refused")
        check(arrival == "external arrival",
              "the external arrival survives with its contents intact")
        check(os.path.exists(src), "the copy source remains intact")
        check(not any(n.startswith(".nbcopy-") for n in os.listdir(dst_dir)),
              "the refused copy removes only its private staging entry")
        check(win._inflight == set(),
              "the refused publication releases its destination claim")
        check(win._undo is None,
              "the refused publication installs no Undo")
        check(any("already exists" in s for s in win.status),
              "the collision is reported truthfully: %r" % win.status)

        print("\n-- and the check can go red: without the claim, they collide")
        win, src, dst_dir = new_case(root, "mutation")
        win._taken = os.path.exists          # the behaviour before the fix
        win._paste()
        win._paste()
        run_jobs(win)
        check(sorted(os.listdir(dst_dir)) == ["report.pdf"],
              "un-claimed, the two pastes really do choose one name — so the "
              "checks above are testing something: %r"
              % (sorted(os.listdir(dst_dir)),))
    finally:
        shutil.rmtree(root, True)

    print()
    if FAILURES:
        print("FINDER COPY COLLISION SELFTEST: %d checks, %d FAILED"
              % (CHECKS[0], len(FAILURES)))
        print("RESULT: FAIL")
        for f in FAILURES:
            print("   - %s" % f)
        return 1
    print("FINDER COPY COLLISION SELFTEST: %d checks, all pass" % CHECKS[0])
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
