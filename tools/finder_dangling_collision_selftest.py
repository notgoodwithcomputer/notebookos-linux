#!/usr/bin/env python3
"""Headless checks that dangling links remain occupied Finder names."""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, DE)

import finder  # noqa: E402

checks = 0
failures = []


def check(condition, message):
    global checks
    checks += 1
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        failures.append(message)


class Store:
    def __init__(self, name, rel):
        self.name = name
        self.rel = rel

    def get_iter_from_string(self, path):
        return object() if path == "0" else None

    def get_value(self, _it, column):
        return self.name if column == 1 else self.rel


class SelectedModel:
    def __init__(self, name, path):
        self.name = name
        self.path = path

    def get_value(self, _it, column):
        return self.path if column == 4 else self.name


with tempfile.TemporaryDirectory(prefix="nb-dangling-name-") as root:
    win = finder.Finder.__new__(finder.Finder)
    win._inflight = set()
    dangling = os.path.join(root, "occupied.txt")
    os.symlink(os.path.join(root, "missing-target"), dangling)

    check(win._taken(dangling), "a dangling link owns its pathname")
    candidate = win._unique_path(root, "occupied.txt")
    check(candidate == os.path.join(root, "occupied copy.txt"),
          "unique naming skips a dangling link")

    source = os.path.join(root, "source.txt")
    with open(source, "w", encoding="utf-8") as fh:
        fh.write("keep")
    win.store = Store("source.txt", source)
    win.abspath = lambda path: path
    win._end_rename_mode = lambda: None
    win._flash_status = lambda text, *args: setattr(win, "status", text)
    win._set_undo_move = lambda *args: setattr(win, "undo", args)
    win.load = lambda *args, **kwargs: None
    win._select_name = lambda *args: None
    win.rel = ""
    win.status = ""
    win.undo = None

    win._on_name_edited(None, "0", "occupied.txt")
    check(os.path.exists(source), "rename keeps the source when the name is occupied")
    check(os.path.islink(dangling), "rename does not replace the dangling link")
    check("already exists" in win.status,
          "rename explains the name collision")
    check(win.undo is None, "a refused rename installs no Undo")


with tempfile.TemporaryDirectory(prefix="nb-dangling-trash-") as root:
    documents = os.path.join(root, "Documents")
    trash = os.path.join(root, ".Trash")
    origins = os.path.join(trash, ".origins")
    os.makedirs(documents)
    os.makedirs(origins)
    source = os.path.join(documents, "shortcut")
    os.symlink(os.path.join(root, "missing-source-target"), source)
    occupied = os.path.join(trash, "shortcut")
    os.symlink(os.path.join(root, "missing-trash-target"), occupied)

    win = finder.Finder.__new__(finder.Finder)
    win._inflight = set()
    model = SelectedModel("shortcut", source)
    win._selected_iter = lambda: (model, object())
    # Trash and the clipboard now act on the SELECTION, not on one row,
    # so the stub answers the list helper too. Same fixture, same
    # assertions -- only the door the code comes in through moved.
    win._selected_paths = lambda: [model.path]
    win.abspath = lambda path: path
    win._trash_dir = lambda: trash
    win._origins_dir = lambda: origins
    win._flash_status = lambda text, *args: setattr(win, "status", text)
    win._flash_undoable = lambda text: None
    win._set_undo_move = lambda *args: setattr(win, "undo", args)
    win.load = lambda *args, **kwargs: None
    win.rel = "Documents"
    win.undo = None

    win._trash_selected()
    moved_name = "shortcut (1)"
    moved = os.path.join(trash, moved_name)
    sidecar = os.path.join(origins, moved_name)
    check(not os.path.lexists(source) and os.path.islink(moved),
          "Move to Trash accepts a dangling link")
    check(os.path.islink(occupied),
          "an existing dangling Trash entry is not overwritten")
    check(os.path.exists(sidecar),
          "the moved link keeps its Put Back origin metadata")

    restore_model = SelectedModel(moved_name, moved)
    win._selected_iter = lambda: (restore_model, object())
    # Trash and the clipboard now act on the SELECTION, not on one row,
    # so the stub answers the list helper too. Same fixture, same
    # assertions -- only the door the code comes in through moved.
    win._selected_paths = lambda: [restore_model.path]
    win._restore_selected()
    check(os.path.islink(source) and not os.path.lexists(moved),
          "Put Back restores a dangling link as an entry")
    check(not os.path.exists(sidecar),
          "successful Put Back removes the committed origin metadata")


def duplicate_window_with_undo(root, selected):
    """A Finder wired up just far enough to run Duplicate end to end, with the
    recorded Undo captured as `win.undo` rather than installed for real."""
    win = finder.Finder.__new__(finder.Finder)
    win._inflight = set()
    model = SelectedModel(os.path.basename(selected), selected)
    win._selected_iter = lambda: (model, object())
    # Trash and the clipboard now act on the SELECTION, not on one row,
    # so the stub answers the list helper too. Same fixture, same
    # assertions -- only the door the code comes in through moved.
    win._selected_paths = lambda: [model.path]
    win.abspath = lambda path: path
    win.get_mapped = lambda: False
    win._flash_status = lambda text, *args: setattr(win, "status", text)
    win._flash_undoable = lambda text: setattr(win, "status", text)
    win._set_undo_remove = lambda *args: setattr(win, "undo", args)
    win.load = lambda *args, **kwargs: None
    win._select_name = lambda *args: None
    win.rel = os.path.basename(root)
    win.status = ""
    win.undo = None
    return win


# Duplicate copies the selected ENTRY. A link is duplicated as a link —
# whether or not its target is still there, and without ever being followed.
with tempfile.TemporaryDirectory(prefix="nb-dangling-duplicate-") as root:
    target_text = os.path.join(root, "missing-duplicate-target")
    source = os.path.join(root, "shortcut")
    os.symlink(target_text, source)

    win = duplicate_window_with_undo(root, source)
    win._duplicate_selected()

    copy = os.path.join(root, "shortcut copy")
    check(os.path.islink(copy), "Duplicate copies a dangling link as a link")
    check(not os.path.exists(copy),
          "the duplicated link is still dangling, not a materialised file")
    check(os.path.islink(copy) and os.readlink(copy) == target_text,
          "the duplicate holds the identical link text")
    check(os.path.islink(source) and os.readlink(source) == target_text,
          "the original dangling link is left untouched")
    check(win.undo is not None and win.undo[-1] == copy,
          "duplicating a dangling link installs an Undo for the duplicate")
    check("no longer exists" not in win.status,
          "Duplicate does not refuse a dangling link")


with tempfile.TemporaryDirectory(prefix="nb-livelink-duplicate-") as root:
    real = os.path.join(root, "Real Folder")
    os.makedirs(real)
    with open(os.path.join(real, "inside.txt"), "w", encoding="utf-8") as fh:
        fh.write("target contents")
    source = os.path.join(root, "folder link")
    os.symlink(real, source)

    win = duplicate_window_with_undo(root, source)
    win._duplicate_selected()

    copy = os.path.join(root, "folder link copy")
    check(os.path.islink(copy),
          "Duplicate copies a live directory link as a link")
    check(not (os.path.isdir(copy) and not os.path.islink(copy)),
          "the duplicate is not a materialised copy of the target folder")
    check(os.path.islink(copy) and os.readlink(copy) == real,
          "the duplicated directory link holds the identical link text")
    check(sorted(os.listdir(real)) == ["inside.txt"],
          "the link target's contents are left exactly as they were")
    check(win.undo is not None and win.undo[-1] == copy,
          "duplicating a live directory link installs an Undo")
    check(not any(nm.startswith(".nbcopy-") for nm in os.listdir(root)),
          "no staging entry is left behind")


def new_folder_window(root):
    """A Finder wired up just far enough to run New Folder end to end, with the
    recorded Undo and the selected name captured rather than installed."""
    win = finder.Finder.__new__(finder.Finder)
    win._inflight = set()
    win.abspath = lambda _rel: root
    win.get_mapped = lambda: False
    win._flash_status = lambda text, *args: setattr(win, "status", text)
    win._set_undo_remove = lambda *args: setattr(win, "undo", args)
    win.load = lambda *args, **kwargs: None
    win._select_name = lambda name: setattr(win, "selected", name)
    win.rel = ""
    win.status = ""
    win.undo = None
    win.selected = None
    return win


# New Folder picks its own name, so it must ask the same question about a name
# being free that every other Finder action asks (_taken). exists() said a
# dangling link's name was free: mkdir then failed EEXIST and the person was
# told the folder could not be created, on a click that should simply have
# produced "untitled folder 2".
with tempfile.TemporaryDirectory(prefix="nb-newfolder-dangling-") as root:
    target = os.path.join(root, "missing-newfolder-target")
    dangling = os.path.join(root, "untitled folder")
    os.symlink(target, dangling)

    win = new_folder_window(root)
    win._new_folder()

    made = os.path.join(root, "untitled folder 2")
    check(os.path.isdir(made) and not os.path.islink(made),
          "New Folder steps over a dangling link to 'untitled folder 2'")
    check(os.path.islink(dangling) and os.readlink(dangling) == target,
          "the dangling link is left exactly as it was")
    check(win.status == "", "New Folder does not report a failure")
    check(win.undo is not None and win.undo[-1] == made,
          "the new folder gets an Undo bound to itself")
    check(win.selected == "untitled folder 2",
          "the new folder is the one left selected")


# A copy still running to "untitled folder" owns that name even though nothing
# is on disk under it yet. Taking it would have made New Folder and the copy
# fight over one destination.
with tempfile.TemporaryDirectory(prefix="nb-newfolder-inflight-") as root:
    claim = os.path.join(root, "untitled folder")
    win = new_folder_window(root)
    win._inflight.add(claim)
    win._new_folder()

    made = os.path.join(root, "untitled folder 2")
    check(os.path.isdir(made), "New Folder steps over an in-flight claim")
    check(not os.path.lexists(claim),
          "the claimed name is left free for the copy that claimed it")
    check(claim in win._inflight, "the in-flight claim itself is untouched")
    check(win.status == "" and win.undo is not None and win.undo[-1] == made,
          "the folder made beside the claim succeeds and is undoable")


# The ordinary case the numbering exists for, unchanged.
with tempfile.TemporaryDirectory(prefix="nb-newfolder-plain-") as root:
    win = new_folder_window(root)
    win._new_folder()
    first = os.path.join(root, "untitled folder")
    check(os.path.isdir(first), "the first New Folder is 'untitled folder'")
    check(win.selected == "untitled folder" and win.undo[-1] == first,
          "the first folder is selected and undoable")

    win = new_folder_window(root)
    win._new_folder()
    second = os.path.join(root, "untitled folder 2")
    check(os.path.isdir(second),
          "a second New Folder beside it is 'untitled folder 2'")
    check(os.path.isdir(first), "the first folder is left alone")


print("\n%d checks, %d failed" % (checks, len(failures)))
print("RESULT: %s" % ("FAILED" if failures else "PASS"))
sys.exit(1 if failures else 0)
