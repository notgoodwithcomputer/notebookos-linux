#!/usr/bin/env python3
"""Animation state forwarded through one helper must remain visible."""

import ast
import grid_e4_travel_check as gate


def main():
    tree = ast.parse('''
class View:
    def _blit(self, cr, x, y):
        cr.set_source_surface(self.surface, x, y)
    def _frame(self, cr):
        self._blit(cr, self.dx, self.dy)
''')
    helpers = gate.forwarded_travel_helpers(tree)
    call = next(n for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == '_blit')
    xi, yi, _ = helpers['_blit'][0]
    x, y = call.args[xi], call.args[yi]
    ok = gate.reads_state(x) and gate.reads_state(y)
    print(("PASS" if ok else "FAIL") + ": helper-forwarded animation state")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return not ok


if __name__ == '__main__':
    raise SystemExit(main())
