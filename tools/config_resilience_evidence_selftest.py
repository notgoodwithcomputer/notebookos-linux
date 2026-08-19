#!/usr/bin/env python3
"""Exit zero without post-construction evidence is not a resilience pass."""

import json
import config_resilience_selftest as gate


def main():
    evidence = gate.WORKER_SENTINEL + json.dumps(
        {"app": "writer", "class": "Writer", "stage": "destroy-ready"})
    checks = [not gate.worker_succeeded(0, "", "writer"),
              not gate.worker_succeeded(0, "setup only\n", "writer"),
              gate.worker_succeeded(0, evidence + "\n", "writer"),
              not gate.worker_succeeded(1, evidence + "\n", "writer")]
    for ok, label in zip(checks, ("empty rc0 rejected", "banner rc0 rejected",
                                  "valid evidence accepted", "nonzero rejected")):
        print(("PASS" if ok else "FAIL") + ": " + label)
    all_ok = all(checks)
    print("RESULT: %s" % ("ALL PASS" if all_ok else "FAILED"))
    return not all_ok


if __name__ == "__main__":
    raise SystemExit(main())
