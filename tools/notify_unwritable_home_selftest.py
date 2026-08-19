#!/usr/bin/env python3
"""THE NOTICE ABOUT A DISK THAT WILL NOT TAKE A WRITE MUST ITSELF BE WRITTEN.

Every message that matters most — "Your recipes could not be saved", "The disc
was not written" — is posted by an app whose config directory is exactly what
just refused. The spool lived inside that directory, so `post()` returned ""
and the person was told nothing at all. Found by a skeptic driving Calculator
with a read-only config dir: the app recorded the failure on itself and posted
a notification that went nowhere.

This suite makes the config directory genuinely unwritable and checks the tray
from the panel's side: the message is there, it says what it said, and it is
one tray — the records already in the primary spool are still listed beside it.
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

passed, failed = 0, []


def check(name, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print("PASS " + name)
    else:
        failed.append(name)
        print("FAIL %s%s" % (name, ": " + str(detail) if detail else ""))


home = tempfile.mkdtemp(prefix="nb-notify-unwritable-")
os.environ["NB_HOME"] = home
sys.path.insert(0, DE)
import nbnotify  # noqa: E402

cfg = os.path.join(home, ".config", "notebook")
os.makedirs(cfg, exist_ok=True)

# One ordinary message first, so the merge has something to merge.
first = nbnotify.post("Disc written", "Sunday Mixtape", app="burner")
check("an ordinary notification is posted while the disk is fine", bool(first))
check("...and the panel lists it",
      any(r.get("title") == "Disc written" for r in nbnotify.load()))
check("...into the primary spool",
      os.path.isdir(nbnotify.SPOOL) and os.listdir(nbnotify.SPOOL))

# Now the state the message is ABOUT: the config directory refuses writes.
# Both the config directory and the spool inside it: a read-only parent still
# leaves an existing subdirectory writable, so chmod'ing only the parent would
# have made this suite vacuous — the very failure it exists to catch.
os.chmod(nbnotify.SPOOL, 0o500)
os.chmod(cfg, 0o500)
try:
    wrote = None
    try:
        with open(os.path.join(nbnotify.SPOOL, "probe"), "w") as fh:
            fh.write("x")
        wrote = True
    except OSError:
        wrote = False
    check("the spool really cannot be written (the test is not vacuous)",
          wrote is False)

    second = nbnotify.post("Recipes not saved",
                           "The disk would not take the write", app="cookbook")
    check("a notification posted against an unwritable home still lands",
          bool(second), "post() returned %r" % (second,))
    listed = nbnotify.load()
    titles = [r.get("title") for r in listed]
    check("...and the panel shows it", "Recipes not saved" in titles, titles)
    check("...beside the messages that were already there, as one tray",
          "Disc written" in titles, titles)
    check("...newest first, the order the centre presents",
          titles[0] == "Recipes not saved", titles)
    check("...with its body intact",
          any(r.get("body") == "The disk would not take the write"
              for r in listed))
    check("...and the change key the panel polls once a second moved",
          nbnotify.state_key() != (), "state_key returned nothing")
    check("...written outside the directory that refused",
          os.path.isdir(nbnotify.FALLBACK_SPOOL)
          and bool(os.listdir(nbnotify.FALLBACK_SPOOL)))
    check("...and the fallback is keyed to THIS home, not shared by every "
          "session on the machine", home.strip("/").replace("/", "-")[-20:]
          in nbnotify.FALLBACK_SPOOL or os.path.basename(
              nbnotify.FALLBACK_SPOOL) != "notifications")

    # Clear All while the disk is still read-only can only take what it can
    # take, and says so by its count — it must not raise, and it must remove
    # the record it CAN reach.
    gone = nbnotify.clear_all()
    check("Clear All takes what it can reach on a read-only disk",
          gone == 1, "cleared %r" % (gone,))
    os.chmod(cfg, 0o700)
    os.chmod(nbnotify.SPOOL, 0o700)
    check("...and once the disk takes writes again the tray empties",
          nbnotify.clear_all() >= 1 and nbnotify.load() == [])
finally:
    os.chmod(cfg, 0o700)
    os.chmod(nbnotify.SPOOL, 0o700)
    shutil.rmtree(home, ignore_errors=True)
    shutil.rmtree(nbnotify.FALLBACK_SPOOL, ignore_errors=True)

print("\n%d checks, %d passed, %d FAILED" % (passed + len(failed), passed,
                                             len(failed)))
print("RESULT: %s" % ("ALL PASS" if not failed else "FAILED"))
raise SystemExit(1 if failed else 0)
