#!/usr/bin/env python3
"""An app that cannot be inspected cannot pass contrast coverage."""

import contextlib
import io
from unittest import mock
import button_contrast_check as gate


def main():
    output = io.StringIO()
    with mock.patch.object(gate, "check_app",
                           side_effect=RuntimeError("construction failed")), \
            mock.patch.object(gate.uishot, "load_theme"), \
            mock.patch.object(gate.sys, "argv", ["gate", "brokenapp"]), \
            contextlib.redirect_stdout(output):
        rc = gate.main()
    text = output.getvalue()
    if rc == 0 or "RESULT: FAILED" not in text or "1 probe error" not in text:
        print("FAIL: an uninspectable app passed button contrast")
        return 1
    print("PASS: app construction errors fail contrast coverage")
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
