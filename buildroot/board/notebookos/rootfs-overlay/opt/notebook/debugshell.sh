#!/bin/sh
# Serial debug shell — gated on the kernel command line.
#
# The serial console used to run `getty -n -l /bin/sh`, i.e. an unauthenticated
# root shell for anyone who attached a cable. That is fine for a development
# image and unacceptable on a machine somebody owns. This runs a shell ONLY
# when the kernel was booted with `nbdebug`; otherwise it sleeps forever so
# init does not respawn-loop.
if grep -qw nbdebug /proc/cmdline 2>/dev/null; then
    exec /sbin/getty -n -l /bin/sh 115200 ttyS1
fi
# No debug requested: hold the slot open without offering anything.
while : ; do sleep 3600; done
