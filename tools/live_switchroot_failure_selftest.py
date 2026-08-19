#!/usr/bin/env python3
"""switch_root must be exec'd, and a failed one must restore the rescue
shell's device tree.

BusyBox switch_root refuses to run unless it is PID 1: it prints its usage
text and returns. `exec` is what makes this shell BECOME switch_root and so
inherit PID 1. An earlier revision ran it as `if ! /sbin/switch_root ...`
to get a failure branch -- a forked child, never PID 1 -- and the ISO it
built could not boot AT ALL: it reached "starting session...", printed the
usage text, and dropped to the emergency shell. Every structural ISO gate
(El Torito, GPT, ISO9660) still passed, because none of them run the init.
So this file pins the exec, and the checks below keep the recovery that the
`if !` form was reaching for.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "live", "init.sh")
text = open(PATH, encoding="utf-8").read()

SWITCH = 'exec /sbin/switch_root "$NEWROOT" /sbin/init'

# The forked-child checks read CODE only. The comment above the exec quotes the
# broken `if !` form on purpose -- saying why it is wrong is what stops it being
# reintroduced -- and a naive substring search over the whole file would trip on
# that prose and go red against a correct script.
code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))

# 1. It is exec'd, so it runs as PID 1.
assert SWITCH in code, "switch_root must be exec'd or it is not PID 1"
# 2. And never as a forked child, however the branch is spelled.
assert 'if ! /sbin/switch_root' not in code
assert '$(/sbin/switch_root' not in code

start = text.index(SWITCH)
failure = text[start + len(SWITCH):]

# 3. devtmpfs is carried INTO the new root before the switch...
assert text.index('/bin/mount --move /dev "$NEWROOT/dev"') < start
# 4. ...and moved back out if the exec itself never took, so the emergency
#    shell has a console and a tty.
assert '/bin/mount --move "$NEWROOT/dev" /dev' in failure
assert 'mount -t devtmpfs dev /dev' in failure
# 5. Restore the devices BEFORE announcing the rescue, not after.
assert 'rescue "cannot switch to live root"' in failure
assert failure.index('mount --move') < failure.index('rescue ')

print("LIVE SWITCH_ROOT FAILURE SELFTEST: 8 checks, all pass")
print("RESULT: ALL PASS")
