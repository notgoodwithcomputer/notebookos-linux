#!/bin/sh
#
# udev fires this when a display is plugged in or unplugged. udev runs with no
# DISPLAY, no XAUTHORITY and a minimal PATH, so hand xrandr what it needs
# before calling the real configurator.
#
# Keep the bounded refresh in this RUN process. eudev kills every descendant
# when a RUN helper exits, detached or not, so backgrounding the settle delay
# causes the actual display/audio work to be killed before it starts.
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
# DRM can emit several connector events for one cable and udev may run their
# helpers concurrently. Keep topology, saved mode/scale and audio routing one
# transaction; otherwise a later worker can reset xrandr between the first
# worker's display and preference steps, leaving picture and Settings out of
# sync. An fd flock is released by the kernel even if udev kills this process.
exec 9>/tmp/nb-display-hotplug.lock || exit 0
flock 9 || exit 0
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
sleep 1
/opt/notebook/display.sh >/dev/null 2>&1
# display.sh rebuilds topology from hardware defaults. Reapply the saved panel
# mode/scale afterwards; nbprefs then remirrors every connected output onto
# that chosen logical canvas. Without this, plugging in a TV resets the panel
# to --auto while Settings continues to display the old saved choice.
if [ -f /opt/notebook/de/nbprefs.py ]; then
    python3 /opt/notebook/de/nbprefs.py >/dev/null 2>&1
fi
if [ -f /opt/notebook/de/nbaudio.py ]; then
    python3 /opt/notebook/de/nbaudio.py apply >/dev/null 2>&1
fi
exit 0
