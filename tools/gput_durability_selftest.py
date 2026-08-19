#!/usr/bin/env python3
"""Guest uploads stage and verify bytes before replacing their destination."""
import os
import shlex

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "gput.py")
text = open(PATH, encoding="utf-8").read()

decode = text.index('cmd("base64 -d %s > %s"')
stage_hash = text.index('md5sum %s', decode)
publish = text.index('cmd("mv -f -- %s %s"', stage_hash)
final_hash = text.index('md5sum %s', publish)

assert decode < stage_hash < publish < final_hash
assert "base64 -d /tmp/gput.b64 > '%s'" not in text
assert 'q_b64, q_out, q_dst = map(shlex.quote' in text
assert 'remote_out = dst + ".gput-%s.tmp"' in text
assert 'chmod "$(stat -c %%a %s)" %s' in text
assert 'cmd("rm -f -- %s" % q_out)' in text
assert 'cmd("rm -f -- %s" % q_b64)' in text

hostile = "/root/it's $(not-a-command).py"
quoted = shlex.quote(hostile)
assert quoted != "'%s'" % hostile
assert "$(not-a-command)" in quoted

print("GPUT DURABILITY SELFTEST: 9 checks, all pass")
print("RESULT: ALL PASS")
