#!/usr/bin/env python3
"""Every newly registered visible app must enter config resilience coverage."""

import config_resilience_selftest as gate


def main():
    apps = gate.registered_apps({"Writer": "writer", "New App": "newapp",
                                 "Hidden": "hidden"}, {"Hidden": "reason"})
    ok = "newapp" in apps and "hidden" not in apps
    print(("PASS" if ok else "FAIL") + ": registry drives resilience coverage")
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return not ok


if __name__ == "__main__":
    raise SystemExit(main())
