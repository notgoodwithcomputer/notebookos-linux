#!/bin/sh
#
# Drive every connected display at its NATIVE resolution, and work out the
# interface scale that resolution needs.
#
# THE BUG THIS EXISTS FOR (1): session.sh asked xrandr for connected outputs and
# took the FIRST one --  `awk '/ connected/{print $1; exit}'` -- then configured
# only that. A television on HDMI is a SECOND output, so it was found, listed,
# and then never switched on: the laptop panel lit up and the TV stayed dark.
# Nothing watched for a display being plugged in later either, so connecting
# the TV after the desktop was up did nothing at all.
#
# THE BUG THIS EXISTS FOR (2): this script used to force 1920x1080 "when it is
# genuinely offered". Every high-resolution panel OFFERS 1920x1080 -- a 4K
# laptop lists it as a scaled mode -- so the one class of machine whose screen
# is the reason to buy it was the one class this OS deliberately ran upscaled
# and soft. On a 3840x2160 panel that is a quarter of the real pixels, resampled.
# Nothing anywhere set a GDK scale factor either, so there was no path by which
# a dense panel could ever have been driven correctly. Native mode, always, and
# the scale is computed below rather than assumed.
#
# Plugging a screen into a computer means "show me this on that", so the
# external display MIRRORS the built-in one (--same-as) rather than extending
# the desktop onto a screen across the room where half the windows would be
# unreachable. Mirroring also keeps the layout honest: the desktop is a fixed
# board sized to one screen, and stretching it across two would put the widget
# column on the television.
#
# NOTE: mode-setting can only work where the kernel has a real KMS driver bound.
# On the plain EFI framebuffer (simpledrm) there is exactly one output whose mode
# is fixed, xrandr cannot add another, and this correctly does nothing rather
# than pretending. The SCALE calculation still runs and still matters there: the
# firmware framebuffer on a dense panel comes up at its native size, which is
# precisely the case that needs scaling.
XR=$(xrandr 2>/dev/null) || exit 0
[ -n "$XR" ] || exit 0

# Where the computed scale is published for session.sh to export. Not /run:
# on the live image /run is a tmpfs that gets shadowed during the switch_root
# dance, and this has to be readable by the session that starts right after.
# /tmp is already where the session's other boot flags live (/tmp/nb-ready).
# Overridable only so the selftest can point it somewhere private; the session
# never sets it. See tools/display_scale_selftest.py.
NB_SCALE_FILE=${NB_SCALE_FILE:-/tmp/nb-scale}

# The internal panel first if there is one: it is the screen the user is sitting
# at, so it owns the origin, everything else mirrors onto it, and it is the one
# whose pixel density decides the interface scale.
PRIMARY=$(printf '%s\n' "$XR" | awk '/^(eDP|LVDS|DSI)[^ ]* connected/{print $1; exit}')
[ -n "$PRIMARY" ] || PRIMARY=$(printf '%s\n' "$XR" | awk '/ connected/{print $1; exit}')
[ -n "$PRIMARY" ] || exit 0

# --- native mode, on every output ------------------------------------------
# --auto selects each output's PREFERRED mode, which for a laptop panel is its
# native one and for a television is the mode it reports over HDMI (the only one
# guaranteed to display without overscan surprises).
xrandr --output "$PRIMARY" --primary --auto 2>/dev/null

# Every OTHER connected output mirrors it.
printf '%s\n' "$XR" | awk '/ connected/{print $1}' | while read -r OUT; do
	[ "$OUT" = "$PRIMARY" ] && continue
	xrandr --output "$OUT" --auto --same-as "$PRIMARY" 2>/dev/null || \
		xrandr --output "$OUT" --auto 2>/dev/null
done

# ...and anything that has just been UNPLUGGED is switched off, or its dead
# framebuffer keeps the desktop sized for a screen that is no longer there.
printf '%s\n' "$XR" | awk '/ disconnected/{print $1}' | while read -r OUT; do
	printf '%s\n' "$XR" | grep -qE "^$OUT disconnected \(" && continue
	xrandr --output "$OUT" --off 2>/dev/null
done

# --- interface scale --------------------------------------------------------
# Re-ask xrandr: the geometry above has just changed, and the numbers below have
# to describe the screen as it now is.
XR2=$(xrandr 2>/dev/null) || exit 0

# The primary's line carries both its active geometry and its physical size:
#   eDP-1 connected primary 3840x2160+0+0 (normal ...) 344mm x 193mm
# Physical size comes from EDID and is the only honest source of density. It is
# also routinely WRONG or absent -- 0mm on many panels, and a handful of
# televisions report the diagonal of the cabinet -- so it is used when it is
# plausible and a pure-resolution rule stands in when it is not.
PLINE=$(printf '%s\n' "$XR2" | grep -E "^$PRIMARY connected")
GEOM=$(printf '%s\n' "$PLINE" | grep -oE '[0-9]+x[0-9]+\+[0-9]+\+[0-9]+' | head -1)
PX=$(printf '%s\n' "$GEOM" | cut -dx -f1)
PY=$(printf '%s\n' "$GEOM" | cut -dx -f2 | cut -d+ -f1)
MMW=$(printf '%s\n' "$PLINE" | grep -oE '[0-9]+mm x [0-9]+mm' | head -1 | cut -dm -f1)

# No geometry parsed -> say nothing and let the session default to 1. Writing a
# guess here would be worse than writing nothing: session.sh treats a missing
# file as "scale 1", which is the safe answer on an unknown screen.
[ -n "$PX" ] && [ -n "$PY" ] || exit 0

SCALE=1
if [ -n "$MMW" ] && [ "$MMW" -gt 100 ] 2>/dev/null; then
	# Horizontal DPI = px / (mm / 25.4). Integer arithmetic only: this runs in
	# busybox ash, which has no floating point at all.
	DPI=$(( PX * 254 / (MMW * 10) ))
	# 192 is the conventional 2x threshold (2 x 96dpi) and is what a Retina-class
	# panel clears comfortably: a 13.6" 2560x1664 laptop is ~224, a 15.6" 4K is
	# ~282. A 1920x1080 13.3" panel is ~166 and correctly stays at 1x -- it is a
	# dense screen but not a doubled one, and running it at 2x would leave a
	# 960x540 desktop.
	[ "$DPI" -ge 192 ] && SCALE=2
else
	# EDID gave us nothing usable. Fall back to resolution alone: no panel ships
	# 2560x1600-or-denser at a size where 1x is the right answer, and this is
	# deliberately conservative -- it would rather leave a dense screen at 1x
	# than shrink a normal one to an unusable desktop.
	if [ "$PX" -ge 2560 ] && [ "$PY" -ge 1600 ]; then
		SCALE=2
	fi
fi

# THE GUARD THAT MATTERS. GTK3 scales by INTEGER factors only, so scale 2 does
# not "make things bigger" -- it halves the logical desktop. Every layout in this
# OS is built to a 1024x740 minimum (see tools/minsize_sweep.py); below that,
# content stops being reachable, which is the exact real-hardware bug class that
# budget was introduced to kill. A 2560x1440 panel is dense enough to trip the
# DPI test and would land on a 1280x720 logical desktop -- 20px SHORT vertically,
# and every app that needs 740 would be clipped with no way to scroll to it.
#
# So density proposes and the budget disposes: if 2x would not leave a desktop
# the interface fits in, the panel stays at 1x.
if [ "$SCALE" = 2 ]; then
	if [ $(( PX / 2 )) -lt 1024 ] || [ $(( PY / 2 )) -lt 740 ]; then
		SCALE=1
	fi
fi

printf '%s\n' "$SCALE" > "$NB_SCALE_FILE" 2>/dev/null || true
exit 0
