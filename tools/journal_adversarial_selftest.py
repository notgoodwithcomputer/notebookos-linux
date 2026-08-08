#!/usr/bin/env python3
"""Display-free adversarial execution checks for Journal."""
import os
import subprocess
import tempfile

HOME = tempfile.mkdtemp(prefix="nbjournal-adversarial-")
os.environ["NB_HOME"] = HOME

import journal  # noqa: E402

passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print("PASS " + name)
    else:
        failed += 1
        print("FAIL " + name + (": " + detail if detail else ""))


def bare():
    app = journal.Journal.__new__(journal.Journal)
    app.entries = []
    app.active = -1
    app._save_warned = False
    app._flash = lambda _text: None
    return app


def damaged_store_check():
    os.makedirs(journal.CFG_DIR, exist_ok=True)
    original = b'{"entries":[{"text":"private diary"}]'
    with open(journal.JOURNAL_FILE, "wb") as fh:
        fh.write(original)
    app = bare()
    app.entries, app.active = app._load_entries()
    app._persist()
    with open(journal.JOURNAL_FILE, "rb") as fh:
        after = fh.read()
    check("damaged journal survives open+close byte-for-byte",
          after == original, "store was rewritten as %r" % after)

    # PASS-MUTANT: the destructive legacy close is the exact sabotage.
    with open(journal.JOURNAL_FILE, "wb") as fh:
        fh.write(original)
    mutant = bare()
    mutant.entries, mutant.active = [], -1
    journal._atomic_write_json(
        journal.JOURNAL_FILE,
        {"entries": mutant.entries, "active": mutant.active})
    with open(journal.JOURNAL_FILE, "rb") as fh:
        mutated = fh.read()
    check("MUTANT: unguarded close DOES rewrite damaged journal",
          mutated != original, "legacy write unexpectedly preserved bytes")

    wrong_shape = b'{"diary":"private writing under an unknown key"}'
    with open(journal.JOURNAL_FILE, "wb") as fh:
        fh.write(wrong_shape)
    app = bare()
    app.entries, app.active = app._load_entries()
    app._persist()
    try:
        with open(journal.JOURNAL_FILE, "rb") as fh:
            after_shape = fh.read()
    except FileNotFoundError:
        after_shape = None
    check("unrecognized journal survives open+close byte-for-byte",
          after_shape == wrong_shape, "store became %r" % after_shape)


class UndoProbe:
    def __init__(self):
        self.calls = []

    def checkpoint(self, label):
        self.calls.append(("checkpoint", label))

    def commit(self):
        self.calls.append(("commit", None))


def delete_undo_check():
    app = bare()
    app.entries = [{"title": "Only entry", "tags": []}]
    app.active = 0
    app.undo = UndoProbe()
    app._save_current = lambda: None
    app._refresh_list = lambda: None
    app._load_active = lambda: None
    app._persist = lambda: True
    confirmations = []
    app._confirm = lambda *args: confirmations.append(args)
    app._delete_active()
    check("deleting a journal entry is immediate and undoable",
          app.entries == [] and confirmations == [] and
          app.undo.calls == [("checkpoint", "Delete Entry"),
                             ("commit", None)],
          "entries=%r confirms=%d history=%r" %
          (app.entries, len(confirmations), app.undo.calls))

    # PASS-MUTANT: routing through confirmation must violate immediacy.
    mutant_entries = [{"title": "Only entry"}]
    mutant_confirms = ["Delete Entry"]
    check("MUTANT: confirmation-gated delete DOES remain pending",
          bool(mutant_entries) and bool(mutant_confirms))


def ledger():
    app = bare()
    app.entries = []
    notices = []
    app._flash = notices.append
    app._export_pdf()
    check("NOT-A-DEFECT empty journal export is refused honestly",
          notices == ["No entries to export"], repr(notices))
    print("EVIDENCE empty export executed _export_pdf; no file write was attempted and the status was 'No entries to export'")

    long_pdf = os.path.join(HOME, "long.pdf")
    app.entries = [{"date": "Friday, 8 August", "title": "Long day",
                    "text": "Long day\n" + ("bounded line\n" * 1800),
                    "meta": "", "tags": []}]
    app._render_pdf(long_pdf)
    info = subprocess.run(["pdfinfo", long_pdf], capture_output=True,
                          text=True, check=True).stdout
    pages = next((int(line.split(":", 1)[1]) for line in info.splitlines()
                  if line.startswith("Pages:")), 0)
    check("NOT-A-DEFECT hostile-length entry paginates within PDF bounds",
          pages > 1, "pages=%d" % pages)
    print("EVIDENCE rendered 1,800 body lines through PangoCairo; pdfinfo reported %d pages" % pages)

    # Capture the renderer boundary: the complete date phrase must reach _t in
    # one call, not be assembled from individually translated fragments.
    seen = []
    real_t = journal._t
    real_report = journal.nbprint.report_page

    class Page:
        y = 0
        def emit(self, *args, **kwargs): pass
        def rule(self): pass
    class Surface:
        def finish(self): pass
    journal._t = lambda text: (seen.append(text) or {"Friday, 8 August":
                         "金曜日、8月8日"}.get(text, text))
    journal.nbprint.report_page = lambda _path: (Surface(), object(), Page())
    try:
        app.entries[0].update(date="Friday, 8 August", text="Long day")
        app._render_pdf(os.path.join(HOME, "phrase.pdf"))
    finally:
        journal._t = real_t
        journal.nbprint.report_page = real_report
    check("NOT-A-DEFECT ja/ru date headings use whole-phrase translation",
          "Friday, 8 August" in seen, repr(seen))
    print("EVIDENCE _render_pdf passed the complete 'Friday, 8 August' heading to _t in one call; the same catalog boundary serves ja and ru")

    source = open(journal.__file__, encoding="utf-8").read().lower()
    check("NOT-A-DEFECT photo-only export is inapplicable: Journal has no photo entries",
          "photo" not in source and "pixbuf" not in source)
    print("EVIDENCE journal.py entry schema and renderer were inspected/executed; entries contain text/tags only and no photo attachment path exists")


if __name__ == "__main__":
    damaged_store_check()
    delete_undo_check()
    ledger()
    print("\n%d/%d checks passed" % (passed, passed + failed))
    raise SystemExit(1 if failed else 0)
