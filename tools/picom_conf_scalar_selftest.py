#!/usr/bin/env python3
"""Display-free scalar grammar mutations for picom.conf."""
from pathlib import Path
import sys
import tempfile


sys.path.insert(0, str(Path(__file__).resolve().parent))
import picom_conf_check as gate  # noqa: E402


def errors(text):
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as fh:
        fh.write(text)
        path = fh.name
    try:
        return gate.check(path)
    finally:
        Path(path).unlink()


for bad in ("backend = @@@;", "shadow = maybe;",
            "corner-radius = 12 34;"):
    assert errors(bad), bad
print("PASS junk, bare identifiers, and trailing scalar tokens are rejected")

for good in ('backend = "xrender";', "shadow = true;",
             "radius = -20;", "opacity = 0.16;",
             'rules = [ "one", "two" ];',
             "wintypes: { normal = { shadow = false; }; };"):
    assert not errors(good), (good, errors(good))
print("PASS shipped string/boolean/number/list/group forms remain valid")

print("RESULT: ALL PASS")
