# 033 — asynchronous shared printing

## Defect

The shared Print window opened printer discovery asynchronously, but its Print
button still rendered the PDF and handed it to the printer synchronously on the
GTK main thread. A slow renderer or printer handoff froze every caller. The
window also looked modal but had no key handler, so Escape did nothing.

## Design

The window now owns one `nbjobs.JobOwner` for discovery and printing. Pressing
Print captures the selected printer, copies, media options, and job name, then
starts the render-and-send pipeline under the `print` job key. The worker makes
a private complete PDF, checkpoints before rendering, at page boundaries in
`simple_pdf` and `booklet_pdf`, and before handing the complete file over. The
temporary file is always removed. Cancellation before that handoff therefore
cannot make a partial send call, and never changes the source document.

Progress reports travel through the job owner's dispatcher. A
`nbmotion.Scalar` retargets the visible fraction with the `LINEAR` easing token;
the same Scalar path provides instant equivalence when motion policy resolves
to still. Rendering occupies 5–80 percent, handoff begins at 82 percent, and a
successful completion reaches 100 percent. Progress fractions are monotone.

Cancel and Escape invoke the same window-owned cancellation callable. During a
job it requests cooperative cancellation and waits for the worker callback to
close the window; before a job it simply closes the dialog. Failures say what
happened, affirm that the document is safe, and tell the person to check the
printer and retry. No app file needed an edit, and no app-side defect was found,
so `HANDOFF.md` was not changed.

## Caller inventory

The campaign description says seven callers. The current tree has **nine app
modules** using the two public entry points; the suite imports and checks all
nine. Public signatures remain:

- `print_document(parent, make_pdf, job_name="Document", media="Letter")`
- `print_booklet(parent, make_pdf, job_name="Booklet")`

Call sites:

- `accounting.py`: `print_document(self, self._render_pdf, job_name="Report")`
- `academics.py`: `print_document(self, render, job_name=job)`
- `bills.py`: `print_document(self, self._render_pdf, job_name="Bills")`
- `contacts.py`: `print_document(self, self._make_pdf, job_name="Contacts")`
- `cookbook.py`: `print_document(self, make_pdf, job_name="Recipe")`
- `journal.py`: `print_document(self, self._make_pdf, job_name="Journal")`
- `novel.py`: `print_booklet(self, make_pdf, "Novel")`
- `screenplay.py`: `print_document(self, lambda p: simple_pdf(...), job_name="Screenplay")`; `print_booklet(self, lambda p: booklet_pdf(...), job_name="Screenplay")`
- `writer.py`: `print_document(self, self._render_pdf, job_name=self._doc_title() or "Document", media=self._page.get("size", "Letter"))`

The exact public signatures and each AST call binding are checked with
`inspect.signature`; importing every caller also confirms it resolves the same
shared `nbprint` module.

## Red proofs

### Synchronous render mutation

Temporarily inserted `make_print_file(make_pdf)` directly in the Print button
handler before `owner.start(...)`, then ran:

```text
$ python3 tools/nbprint_selftest.py
...
the Print handler never invokes render work synchronously                FAIL
...
nbprint_selftest: FAIL (1 checks)
```

The inserted call was removed. A search found no `RED-PROOF` marker, and the
green suite below confirms the intended diff was restored.

### Broken Escape mutation

Temporarily replaced the lookup of the window's shared cancel action with
`None`, then ran:

```text
$ python3 tools/nbprint_selftest.py
...
Escape invokes the same cancellation action as the Cancel button         FAIL
...
nbprint_selftest: FAIL (1 checks)
```

The original lookup was restored. A search found no `RED-PROOF` marker, and
the final green suite confirms the mutation is absent. Thus the temporary
mutation diff is empty for both red proofs; only the package's intended
uncommitted changes remain.

## Verification evidence

```text
$ python3 tools/nbprint_selftest.py
dialog action returns before rendering finishes (0.001s)                 ok
rendering starts on the background job                                   ok
the calling thread remains available while rendering waits               ok
render and send worker leaves no thread                                  ok
progress fractions advance monotonically to completion                   ok
progress and completion fire on the main-loop dispatcher                 ok
the complete print file is sent from the worker                          ok
cancel fixture reaches a page boundary                                   ok
Cancel requests the active print job to stop                             ok
cancelled print leaves no worker thread                                  ok
cancel before handoff makes no partial send call                         ok
cancel completion returns on the main-loop dispatcher                    ok
progress uses Scalar with the linear easing token                        ok
linear Scalar supplies continuous monotone progress frames               ok
the Print handler never invokes render work synchronously                ok
Escape invokes the same cancellation action as the Cancel button         ok
the dialog construction path still creates a GTK window                  ok
print_document public signature is unchanged                             ok
print_booklet public signature is unchanged                              ok
accounting imports the shared nbprint module                             ok
academics imports the shared nbprint module                              ok
bills imports the shared nbprint module                                  ok
contacts imports the shared nbprint module                               ok
cookbook imports the shared nbprint module                               ok
journal imports the shared nbprint module                                ok
novel imports the shared nbprint module                                  ok
screenplay imports the shared nbprint module                             ok
writer imports the shared nbprint module                                 ok
all nine current caller modules keep a compatible call shape             ok
nbprint_selftest: OK

$ python3 tools/jargon_sweep.py nbprint
=== nbprint.py ===
  nbprint.py:44  [graphics/X: GTK] (allow)
      'Gtk'

1 flagged strings
RESULT: CLEAN

$ python3 tools/voice_check.py --file nbprint.py
0 flagged string(s) across 1 file(s)
RESULT: CLEAN

$ python3 -m py_compile buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/nbprint.py tools/nbprint_selftest.py
(no output; exit 0)
```

The jargon gate's single `Gtk` occurrence is an existing allowed technical
identifier, not user-facing copy. No ledger change or campaign-review exception
is needed.
