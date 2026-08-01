#!/bin/sh
#
# udev fires this when a display is plugged in or unplugged. udev runs with no
# DISPLAY, no XAUTHORITY and a minimal PATH, so hand xrandr what it needs
# before calling the real configurator.
#
# Backgrounded and detached: udev kills a RUN+= helper that takes too long, and
# xrandr against a busy server can take a moment.
[ -x /opt/notebook/display.sh ] || exit 0
export DISPLAY=:0
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
# The session runs as the administrator on :0 with no cookie file of its own;
# if one ever appears, use it.
[ -f /root/.Xauthority ] && export XAUTHORITY=/root/.Xauthority
# nbaudio keeps the user's chosen output under $NB_HOME. udev inherits HOME=/
# from init, so without this the hotplug path would not see the choice the
# Settings page saved and would silently revert to following the television.
export NB_HOME=/root
# Settle: the connector state the kernel just announced is not always readable
# by the X server the same instant.
#
# THE SOUND MOVES WITH THE PICTURE. A television plugged in after boot is a
# second sound device as well as a second screen, and the route was previously
# decided once at start-up and never revisited -- so a TV connected later showed
# a picture and stayed silent, and one unplugged left every app playing into a
# dead HDMI port with no way back short of a reboot. Re-deciding it here is what
# makes "follow the screen" true. An explicit choice in Settings is preserved:
# nbaudio re-applies the SAVED output and only falls back to following the
# television when there is none.
( sleep 1
  /opt/notebook/display.sh
  [ -f /opt/notebook/de/nbaudio.py ] && python3 /opt/notebook/de/nbaudio.py apply
) >/dev/null 2>&1 &
exit 0
