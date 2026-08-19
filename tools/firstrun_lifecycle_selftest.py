"""First-run setup can always be FINISHED. Display-free.

THE FAILURE THIS GUARDS. de/firstrun.py runs once, before the sign-in screen,
on a machine somebody else installed. apply() clears the OEM marker only when
every part stuck -- correctly, because a half-configured machine that never
asks again is worse than one that asks twice. That makes every "did it stick?"
answer a gate on the machine being usable at all, so an answer that says "no"
about something that is already on disk wedges the computer on this screen
forever: pressing Finish again runs the same code and fails the same way, and
there is no desktop, no shell and no network behind it.

write_hostname returned exactly that answer. It wrote /etc/hostname and then
ran `hostname <name>` to save a reboot -- inside the SAME try. A hostname
binary that is missing, unrunnable or slow enough to hit the ten-second
timeout raised, the function returned False, apply() reported "Computer name"
could not be saved and kept the marker, while the name sat correctly in
/etc/hostname the whole time. The persisted answer and the live convenience
are two different things and only the first one is the answer.

So: a broken live apply must not fail the step, and a broken FILE still must.
Both halves are checked, because a gate that cannot go red is not a gate.

Run: python3 tools/firstrun_lifecycle_selftest.py
"""
import os
import shutil
import subprocess
import stat
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, os.path.abspath(DE))

TREE = "/tmp/nb-firstrun-lifecycle"
HOME = os.path.join(TREE, "home")
shutil.rmtree(TREE, ignore_errors=True)
os.makedirs(os.path.join(TREE, "etc"), exist_ok=True)
os.makedirs(os.path.join(HOME, ".config", "notebook"), exist_ok=True)
os.environ["NB_HOME"] = HOME
os.environ.pop("NB_LANG", None)          # else write_locale answers from $NB_LANG

import firstrun                                                  # noqa: E402

R = []


def chk(name, ok, detail=""):
    R.append(ok)
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       "" if ok else "  <- %s" % (detail,)))


# Redirect every path this writes into the throwaway tree.
MARKER = os.path.join(TREE, "var", "lib", "notebookos", "first-run")
firstrun.OEM_MARKER = MARKER
firstrun.SHADOW = os.path.join(TREE, "etc", "shadow")
firstrun.HOSTNAME_FILE = os.path.join(TREE, "etc", "hostname")
firstrun.USER_NAME_FILE = os.path.join(TREE, "etc", "notebookos-user")
firstrun.XKB_CONF = os.path.join(TREE, "etc", "X11", "xorg.conf.d",
                                 "00-keyboard.conf")
with open(firstrun.SHADOW, "w") as fh:
    fh.write("root:*:19000:0:99999:7:::\ndaemon:*:19000:0:99999:7:::\n")
# Never make a real crypt hash here: python3.13 dropped the crypt module and
# the fallback shells out. What is under test is the lifecycle, not the hash.
firstrun.hash_password = lambda pw: "$6$selftest$selftesthash"


def marker_back():
    os.makedirs(os.path.dirname(MARKER), exist_ok=True)
    with open(MARKER, "w") as fh:
        fh.write("x")


# Every subprocess this file runs is a live convenience -- `hostname`, and
# setxkbmap through nbi18n. A machine where none of them can run at all is the
# case that used to wedge, so that is the machine the whole run happens on.
def _dead_run(args, *_a, **_kw):
    if args and args[0] == "hostname":
        raise FileNotFoundError(2, "No such file or directory", "hostname")
    return subprocess.CompletedProcess(args, 0)


firstrun.subprocess = types.SimpleNamespace(
    run=_dead_run, TimeoutExpired=subprocess.TimeoutExpired,
    CalledProcessError=subprocess.CalledProcessError)

# --- 1. the live apply cannot fail the persisted answer -------------------
ok = firstrun.write_hostname("benbook")
chk("a hostname command that will not run does not fail the step", ok)
chk("...and the name is on disk anyway",
    os.path.exists(firstrun.HOSTNAME_FILE)
    and open(firstrun.HOSTNAME_FILE).read().strip() == "benbook")


def _slow_run(args, *_a, **_kw):
    if args and args[0] == "hostname":
        raise subprocess.TimeoutExpired(cmd="hostname", timeout=10)
    return subprocess.CompletedProcess(args, 0)


firstrun.subprocess.run = _slow_run
os.unlink(firstrun.HOSTNAME_FILE)
chk("a hostname command that hangs past its timeout does not fail the step",
    firstrun.write_hostname("benbook"))
chk("...and the name is still on disk",
    open(firstrun.HOSTNAME_FILE).read().strip() == "benbook")
firstrun.subprocess.run = _dead_run

# --- 2. so setup can be FINISHED on such a machine ------------------------
marker_back()
chk("setup is owed before it runs", firstrun.pending())
failed = firstrun.apply({"hostname": "benbook", "username": "Ben",
                         "lang": "fr", "kbd": "fr", "password": "nb1234"})
chk("nothing is reported unsaved when only the live commands are broken",
    not failed, failed)
chk("THE MARKER IS GONE -- the machine does not ask again on every boot",
    not firstrun.pending())
chk("the password the owner chose is the one in the shadow file",
    open(firstrun.SHADOW).read().splitlines()[0].split(":")[1]
    == "$6$selftest$selftesthash",
    open(firstrun.SHADOW).read().splitlines()[0])

# Password publication is a power-loss boundary: bytes/metadata must be
# durable before rename, and the directory entry durable afterwards.
events = []
real_fsync = firstrun.os.fsync
real_replace = firstrun.os.replace
def traced_fsync(fd):
    events.append("dir-fsync" if stat.S_ISDIR(os.fstat(fd).st_mode)
                  else "file-fsync")
    return real_fsync(fd)
def traced_replace(src, dst):
    events.append("replace")
    return real_replace(src, dst)
firstrun.os.fsync = traced_fsync
firstrun.os.replace = traced_replace
try:
    shadow_ok = firstrun.set_root_password("$6$selftest$secondhash")
finally:
    firstrun.os.fsync = real_fsync
    firstrun.os.replace = real_replace
chk("shadow bytes and directory entry are fsynced around replacement",
    shadow_ok and events.index("file-fsync") < events.index("replace")
    < events.index("dir-fsync"), events)

# Keyboard live-apply is not merely a convenience: the password is typed now
# and checked after this persisted layout starts. A normal nonzero setxkbmap
# exit must keep setup owed rather than store a password under the wrong keys.
def _bad_keyboard(args, *_a, **_kw):
    return subprocess.CompletedProcess(args, 1)

firstrun.subprocess.run = _bad_keyboard
marker_back()
failed = firstrun.apply({"hostname": "benbook", "username": "Ben",
                         "lang": "fr", "kbd": "fr", "password": "nb1234"})
chk("a failed live keyboard change is reported", "keyboard" in failed, failed)
chk("...and setup remains owed to prevent next-boot password lockout",
    firstrun.pending())
firstrun.subprocess.run = _dead_run

# --- 3. a genuinely unwritable file MUST still hold the screen ------------
# The gate has to be able to go red, or the fix above is just a check deleted.
blocked = os.path.join(TREE, "etc", "not-a-dir")
with open(blocked, "w") as fh:
    fh.write("i am a file\n")
firstrun.HOSTNAME_FILE = os.path.join(blocked, "hostname")
chk("a name that cannot be written to disk still fails the step",
    not firstrun.write_hostname("benbook"))
marker_back()
failed = firstrun.apply({"hostname": "benbook", "username": "Ben",
                         "lang": "fr", "kbd": "fr", "password": "nb1234"})
chk("...is named to the person on the screen", "hostname" in failed, failed)
chk("...and setup stays owed rather than handing over a half-set machine",
    firstrun.pending())

# A password-less setup still changes /etc/shadow (it explicitly locks root),
# and failure must be reported just like failure to install a chosen hash.
firstrun.HOSTNAME_FILE = os.path.join(TREE, "etc", "hostname")
marker_back()
real_set_password = firstrun.set_root_password
firstrun.set_root_password = lambda _hashed: False
failed = firstrun.apply({"hostname": "benbook", "username": "Ben",
                         "lang": "fr", "kbd": "fr", "password": ""})
chk("failure to apply password-less startup is reported",
    failed == ["password"], failed)
chk("...and setup remains owed", firstrun.pending())
firstrun.set_root_password = real_set_password

# Removing the marker is itself the final durable setup step. Claiming success
# when it remains would make this screen reappear on every boot.
marker_back()
real_clear_marker = firstrun.clear_marker
firstrun.clear_marker = lambda: False
failed = firstrun.apply({"hostname": "benbook", "username": "Ben",
                         "lang": "fr", "kbd": "fr", "password": "nb1234"})
chk("failure to finalize setup is reported", failed == ["completion"], failed)
chk("...and the marker still makes setup owed", firstrun.pending())
firstrun.clear_marker = real_clear_marker

print("\n%d/%d checks passed" % (sum(1 for x in R if x), len(R)))
sys.exit(0 if all(R) else 1)
