#!/usr/bin/env python3
"""Named adversarial checks for non-save operation failure honesty.

Scope note: this suite covers the error-path fixes owned by the bug-fix
lane (novel / screenplay / finder print + eject) plus two not-a-defect
guards it verified in passing (media decode fallback, contacts import).
The same audit also fixed bills.py (app-improve's claim) and gbasdk.py
(gba-loop's claim); those checks travel with those fixes in their own
lanes — see release/1.0/HANDOFF.md — so this committed gate only asserts
what this commit lands.
"""
import ast
import errno
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"


def method(file, cls, name, globs):
    tree = ast.parse((DE / file).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    fn = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == name)
    fn.decorator_list = []
    ns = dict(globs)
    exec(compile(ast.Module([fn], type_ignores=[]), str(DE / file), "exec"), ns)
    return ns[name]


def function(file, name, globs):
    tree = ast.parse((DE / file).read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    fn.decorator_list = []
    ns = dict(globs)
    exec(compile(ast.Module([fn], type_ignores=[]), str(DE / file), "exec"), ns)
    return ns[name]


class Sink:
    def __init__(self): self.messages = []
    def _flash(self, text, *a, **k): self.messages.append(str(text))
    def _flash_status(self, text, *a, **k): self.messages.append(str(text))
    def _set_save_error(self, text, *a, **k): self.messages.append(str(text))


def clean(messages, forbidden=("Traceback", "Errno", "/tmp/secret")):
    assert messages, "failure was silent"
    joined = "\n".join(messages)
    assert not any(x in joined for x in forbidden), joined
    assert not any(x in joined for x in ("Exported", "Printed", "Saved", "Done")), joined


def test_print_nonzero_screenplay_real_handler():
    s = Sink(); s._build_pages = lambda: (1, lambda *a: None)
    nbprint = types.SimpleNamespace(simple_pdf=lambda *a: None, print_document=lambda *a, **k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ["lp", "/tmp/secret.pdf"])))
    method("screenplay.py", "Screenplay", "_print", {"nbprint": nbprint})(s)
    clean(s.messages); assert s.messages == ["Print failed"]


def test_print_missing_lp_novel_real_handler():
    s = Sink(); s._close_style=lambda:None; s._close_prompt=lambda:None; s._prepare_render=lambda:None; s._page_count=1; s._draw_page=lambda *a:None
    nbprint = types.SimpleNamespace(booklet_pdf=lambda *a, **k: None, print_booklet=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError(errno.ENOENT, "lp", "/usr/bin/lp")))
    method("novel.py", "Novel", "_on_zine_print", {"nbprint": nbprint})(s)
    clean(s.messages); assert s.messages == ["Print failed"]


def test_device_vanish_finder_real_completion_handler():
    s = Sink(); s._ejecting={"/tmp/secret-usb"}; s._fill_sidebar=lambda:None
    fn = method("finder.py", "Finder", "_eject_done", {"_t": lambda x:x})
    assert fn(s, "/tmp/secret-usb", False, "umount: /tmp/secret-usb: No such device") is False
    clean(s.messages); assert s.messages == ["The drive could not be removed safely."]


def test_missing_media_fallback_and_nonzero_are_not_defects():
    # Real fallback function: absent tools and a non-zero decoder both return
    # None for the display handler to turn into its existing unreadable notice.
    fake_pix = types.SimpleNamespace(Pixbuf=types.SimpleNamespace(get_file_info=lambda _p:(None,1,1)))
    fn = function("media.py", "_decode_to_png", {"os":os, "GdkPixbuf":fake_pix, "MAX_PIX":8000})
    import shutil
    old_which, old_run = shutil.which, subprocess.run
    try:
        shutil.which=lambda _x: None
        assert fn("/tmp/truncated.webp") is None
        shutil.which=lambda _x: "/usr/bin/fake"
        subprocess.run=lambda *a, **k: types.SimpleNamespace(returncode=1)
        assert fn("/tmp/truncated.webp") is None
    finally:
        shutil.which, subprocess.run = old_which, old_run


def test_valid_wrong_content_vcard_is_not_defect():
    s = Sink(); s.active=0; s.people=[]; s._save=lambda:None; s._rebuild_list=lambda:None; s._rebuild_detail=lambda:None
    with tempfile.NamedTemporaryFile("w", suffix=".json") as fixture:
        fixture.write('{"course": true}'); fixture.flush()
        picker=types.SimpleNamespace(open_file=lambda *a, **k:fixture.name)
        fn=method("contacts.py", "Contacts", "_import_vcard", {"nbpicker":picker, "DOCS_DIR":"/tmp", "parse_vcards":lambda _x:[], "_t":lambda x:x})
        fn(s)
    clean(s.messages); assert s.messages == ["No contacts found"]


TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]


def main():
    failed = 0
    for test in TESTS:
        try:
            test(); print("PASS", test.__name__)
        except Exception as exc:
            failed += 1; print("FAIL", test.__name__, type(exc).__name__, str(exc))
    # In-suite mutant: removing Screenplay's print guard must fail by this
    # check's name. Screenplay has two identical print guards (normal +
    # booklet); dropping the first must leave the _print body unguarded.
    src = (DE / "screenplay.py").read_text()
    guard = 'except Exception:\n            self._flash("Print failed")'
    guarded = guard in src
    mutant = src.replace(guard, "", 1)
    body = mutant[mutant.index("def _print(self):"):mutant.index("def _print(self):") + 400]
    if guarded and 'self._flash("Print failed")' not in body:
        print("PASS-MUTANT test_print_nonzero_screenplay_real_handler")
    else:
        failed += 1; print("FAIL test_print_nonzero_screenplay_real_handler mutant survived")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
