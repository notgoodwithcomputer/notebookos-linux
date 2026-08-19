#!/usr/bin/env python3
"""Display-free regression for Writer's non-UTF-8 plain-text recovery."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import writer  # noqa: E402


class Probe:
    _open_file = writer.Writer._open_file
    _is_writer_store = lambda *_: False
    def __init__(self):
        self._page = {}; self._history = []; self._hi = -1
        self.flashes = []; self.body = ""
    def _deserialize(self, doc): self.body = doc["body"]
    def _apply_page_geometry(self): pass
    # Opening a document now scrolls the desk back to the top of the
    # new page (it used to keep the old document's scroll offset).
    def _scroll_to_top(self): self.scrolled_top = True
    def _push_history(self): pass
    def _clear_save_chip(self): pass
    def _update_status(self): pass
    def _update_wordcount(self): pass
    def _sync_toolbar(self): pass
    def _flash(self, text, **_kw): self.flashes.append(text)


with tempfile.TemporaryDirectory(prefix="writer-encoding-") as root:
    path = os.path.join(root, "notes.txt")
    original = b"price:\x96 10\n"
    with open(path, "wb") as fh: fh.write(original)
    app = Probe(); app._open_file(path)
    ok = (app._path is None and app._file_dirty and "\ufffd" in app.body
          and app.flashes and open(path, "rb").read() == original)
    print(("PASS" if ok else "FAIL")
          + " invalid UTF-8 opens Save-As-only and preserves source bytes")

    good = os.path.join(root, "good.txt")
    with open(good, "wb") as fh: fh.write("café\n".encode("utf-8"))
    app2 = Probe(); app2._open_file(good)
    ok2 = (app2._path == good and not app2._file_dirty
           and app2.body == "café\n")
    print(("PASS" if ok2 else "FAIL")
          + " valid UTF-8 remains normally bound to its source")

    sample = "Résumé\r\nSecond line"
    encoded = (
        ("UTF-8 BOM", b"\xef\xbb\xbf" + sample.encode("utf-8")),
        ("UTF-16 LE BOM", sample.encode("utf-16-le")),
        ("UTF-16 BE BOM", sample.encode("utf-16-be")),
    )
    ok3 = True
    for label, payload in encoded:
        if "UTF-16" in label:
            payload = ((b"\xff\xfe" if "LE" in label else b"\xfe\xff")
                       + payload)
        bom_path = os.path.join(root, label.replace(" ", "-") + ".txt")
        with open(bom_path, "wb") as fh: fh.write(payload)
        probe = Probe(); probe._open_file(bom_path)
        case_ok = (probe._path == bom_path and not probe._file_dirty
                   and probe.body == sample and not probe.flashes)
        ok3 = ok3 and case_ok
        print(("PASS" if case_ok else "FAIL")
              + " %s opens as declared Unicode text" % label)
print("RESULT: %s" % ("PASS" if ok and ok2 and ok3 else "FAILED"))
raise SystemExit(not (ok and ok2 and ok3))
