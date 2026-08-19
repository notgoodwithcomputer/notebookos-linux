#!/usr/bin/env python3
"""Headless proof that simultaneous app launches have one atomic winner."""
import os
import subprocess
import sys
import tempfile

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))

WORKER = r'''
import sys, time
import nbapp
class Race: pass
Race.__module__ = "raceapp"
nbapp.claim_single_instance(Race())
print("CLAIMED", flush=True)
time.sleep(1.0)
nbapp._unregister_app()
'''


def main():
    with tempfile.TemporaryDirectory(prefix="nb-single-claim-") as home:
        env = dict(os.environ, NB_HOME=home, PYTHONPATH=DE)
        workers = [subprocess.Popen([sys.executable, "-c", WORKER], env=env,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
                   for _ in range(2)]
        outputs = [proc.communicate(timeout=10)[0] for proc in workers]
    winners = sum("CLAIMED" in output for output in outputs)
    if winners != 1:
        print("FAIL atomic single-instance claim: %d winners" % winners)
        print("RESULT: FAILED")
        return 1
    print("PASS atomic single-instance claim admits exactly one process")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
