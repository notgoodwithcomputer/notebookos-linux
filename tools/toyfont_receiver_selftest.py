#!/usr/bin/env python3
"""Cairo toy-font calls cannot hide behind a renamed context variable."""
from pathlib import Path
import tempfile

import toyfont_check as gate


def hits(source):
    with tempfile.TemporaryDirectory(prefix="nb-toyfont-") as td:
        path = Path(td, "view.py")
        path.write_text(source, encoding="utf-8")
        return gate.offenders(str(path))


def main():
    cr = hits("cr.show_text(user_text)\n")
    canvas = hits("canvas.show_text(user_text)\n")
    context = hits("context.select_font_face('Sans')\n"
                   "context.text_path(user_text)\n")
    prose = hits("note = 'canvas.show_text(user_text)'\n"
                 "# context.show_text(user_text)\n")
    ok = cr == [1] and canvas == [1] and context == [1, 2] and not prose
    print(("PASS" if cr == [1] and canvas == [1] else "FAIL")
          + ": toy text calls are receiver-name independent")
    print(("PASS" if context == [1, 2] else "FAIL")
          + ": every toy-font method is inventoried")
    print(("PASS" if not prose else "FAIL")
          + ": comments and string prose remain excluded")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
