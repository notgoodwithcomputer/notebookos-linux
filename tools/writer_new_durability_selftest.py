#!/usr/bin/env python3
"""Writer New must durably replace recovery or restore the old document."""

import ast
import copy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/writer.py"


tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
cls = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "Writer")
fn = copy.deepcopy(next(n for n in cls.body
                        if isinstance(n, ast.FunctionDef) and n.name == "_file_new"))
module = ast.Module(body=[fn], type_ignores=[])
ast.fix_missing_locations(module)
# _file_new now resets the page setup as well as the text, so the isolated
# exec needs _sane_page. The real one normalises an arbitrary dict; for {} it
# is exactly this default sheet.
DEFAULT_PAGE = {"size": "Letter", "orientation": "portrait",
                "margins": [1.0, 1.0, 1.0, 1.0]}
scope = {"copy": copy, "_clamp": lambda n, lo, hi: max(lo, min(hi, n)),
         "_sane_page": lambda page: dict(DEFAULT_PAGE)}
exec(compile(module, str(SOURCE), "exec"), scope)


class Buffer:
    def __init__(self, text): self.text, self.caret = text, len(text)
    def set_text(self, text): self.text = text
    def get_char_count(self): return len(self.text)
    def get_iter_at_offset(self, n): return n
    def place_cursor(self, n): self.caret = n


class Null:
    def queue_draw(self): pass


class Probe:
    _file_new = scope["_file_new"]

    def __init__(self, save_ok):
        self.buf = Buffer("irreplaceable draft")
        self._img_meta = {"image": 1}; self._tables = {"table": 1}
        self._path = "/docs/draft.writer"; self._file_dirty = True
        self._history = [{"body": "older"}, {"body": "irreplaceable draft"}]
        self._hi = 1; self._restoring = False; self.save_ok = save_ok
        self.body = Null(); self.cleared_chip = False
        # The page the PREVIOUS document was set up on. A new document must not
        # inherit it, and a New that could not be committed must get it back.
        self._page = {"size": "Legal", "orientation": "landscape",
                      "margins": [0.5, 0.5, 0.5, 0.5]}
        self._header = "HDR {title}"; self._footer = "FTR {title}"
        self._page_numbers = True
        self.geometry_applied = 0; self.scrolled_top = 0

    def _confirm_discard(self): return True
    def _snapshot(self): return {"body": self.buf.text, "runs": [], "_caret": self.buf.caret}
    def _deserialize(self, snap): self.buf.text = snap["body"]
    def _push_history(self): self._history.append(self._snapshot()); self._hi = len(self._history) - 1
    def _save_autosave(self): return self.save_ok
    def _clear_save_chip(self): self.cleared_chip = True
    def _update_status(self): pass
    def _update_wordcount(self): pass
    def _sync_toolbar(self): pass
    def _apply_page_geometry(self): self.geometry_applied += 1
    def _scroll_to_top(self): self.scrolled_top += 1


failed = Probe(False)
history = copy.deepcopy(failed._history)
assert failed._file_new() is False
assert failed.buf.text == "irreplaceable draft"
assert failed._path == "/docs/draft.writer" and failed._file_dirty is True
assert failed._history == history and failed._hi == 1
assert not failed.cleared_chip
print("PASS failed Writer New restores page, identity, dirty state, and history")

FAILED = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name
          + ((": " + detail) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


check("a Writer New that could not be committed puts the page setup back",
      failed._page == {"size": "Legal", "orientation": "landscape",
                       "margins": [0.5, 0.5, 0.5, 0.5]}
      and failed._header == "HDR {title}" and failed._footer == "FTR {title}"
      and failed._page_numbers is True and failed.geometry_applied >= 1,
      "page=%r header=%r footer=%r pn=%r geom=%d" % (
          failed._page, failed._header, failed._footer, failed._page_numbers,
          failed.geometry_applied))

saved = Probe(True)
assert saved._file_new() is True
assert saved.buf.text == "" and saved._path is None and saved._file_dirty is False
assert saved._hi == 0 and len(saved._history) == 1
assert saved.cleared_chip
print("PASS durable Writer New commits an empty recovery document")

# A new document used to inherit the last one's paper, orientation, margins,
# header/footer and page numbers, and to open at the old document's scroll
# offset -- so File > New after a Legal landscape letter gave a blank sheet
# 1344px wide, the previous title in its footer, and its top edge out of view.
fresh = Probe(True)
assert fresh._file_new() is True
check("Writer New starts from the default page setup, scrolled to the top",
      fresh._page == DEFAULT_PAGE and fresh._header == ""
      and fresh._footer == "" and fresh._page_numbers is False
      and fresh.geometry_applied >= 1 and fresh.scrolled_top >= 1,
      "page=%r header=%r footer=%r pn=%r geom=%d scroll=%d" % (
          fresh._page, fresh._header, fresh._footer, fresh._page_numbers,
          fresh.geometry_applied, fresh.scrolled_top))

if FAILED:
    print("RESULT: FAILED")
    raise SystemExit(1)
print("RESULT: PASS")
