#!/usr/bin/env python3
"""dict.update writes must participate in dead-setting round-trip analysis."""

import ast
import dead_setting_check as gate


SRC = '''
class App:
    def build(self):
        self.fullscreen.set_active(self.settings.get("fullscreen", False))
        self.fullscreen.connect("toggled", self._on_toggle)
    def _on_toggle(self, widget):
        self.settings.update({"fullscreen": widget.get_active()})
'''


def main():
    scan = gate.Scan("mutant", ast.parse(SRC)).run()
    findings = gate.verdicts(scan)
    ok = any(k == "fullscreen" and kind == "ROUND TRIP"
             for k, kind, _detail in findings)
    print(("PASS" if ok else "FAIL") + ": dict.update dead round trip detected")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return not ok


if __name__ == "__main__":
    raise SystemExit(main())
