#!/usr/bin/env python3
"""Video Save As adopts a new identity only after a successful write."""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "buildroot", "board", "notebookos",
                    "rootfs-overlay", "opt", "notebook", "de", "video.py")

tree = ast.parse(open(PATH, encoding="utf-8").read(), filename=PATH)
video = next(n for n in tree.body
             if isinstance(n, ast.ClassDef) and n.name == "VideoEditor")
method = next(n for n in video.body
              if isinstance(n, ast.FunctionDef) and n.name == "_file_save_as")
probe = ast.ClassDef(name="Probe", bases=[], keywords=[],
                     body=[method], decorator_list=[])
module = ast.fix_missing_locations(ast.Module(body=[probe], type_ignores=[]))
scope = {"os": os}
exec(compile(module, PATH, "exec"), scope)


def exercise(result):
    obj = scope["Probe"]()
    obj._path = "/Documents/real-project.json"
    obj._choose_file = lambda save: "/media/USB/new-project"
    seen = []

    def save():
        seen.append(obj._path)
        return result

    obj._file_save = save
    obj._file_save_as()
    return obj._path, seen


path, attempted = exercise(False)
assert attempted == ["/media/USB/new-project.json"], attempted
assert path == "/Documents/real-project.json", path

path, attempted = exercise(True)
assert attempted == ["/media/USB/new-project.json"], attempted
assert path == "/media/USB/new-project.json", path

print("PASS: Video Save As changes identity only after a successful write")
print("RESULT: PASS")
