#!/usr/bin/env python3
"""A helper window must not replace the real app in resilience probes."""

from types import SimpleNamespace
import config_resilience_selftest as gate


class AHelper(gate.Gtk.Window):
    pass


class ZRealApp(gate.nbapp.AppWindow):
    pass


def main():
    AHelper.__module__ = ZRealApp.__module__ = "mutant_app"
    mod = SimpleNamespace(__name__="mutant_app", AHelper=AHelper,
                          ZRealApp=ZRealApp)
    selected = gate.find_window_cls(mod)
    ok = selected is ZRealApp
    print(("PASS" if ok else "FAIL") + ": real AppWindow beats helper window")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return not ok


if __name__ == "__main__":
    raise SystemExit(main())
