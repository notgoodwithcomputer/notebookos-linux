#!/bin/sh
#
# Drive every connected display, not just the first one.
#
# THE BUG THIS EXISTS FOR: session.sh asked xrandr for connected outputs and
# took the FIRST one --  `awk '/ connected/{print $1; exit}'` -- then configured
# only that. A television on HDMI is a SECOND output, so it was found, listed,
# and then never switched on: the laptop panel lit up and the TV stayed dark.
# Nothing watched for a display being plugged in later either, so connecting
# the TV after the desktop was up did nothing at all.
#
# Plugging a screen into a computer means "show me this on that", so the
# external display MIRRORS the built-in one (--same-as) rather than extending
# the desktop onto a screen across the room where half the windows would be
# unreachable. Mirroring also keeps the layout honest: the desktop is a fixed
# board sized to one screen, and stretching it across two would put the widget
# column on the television.
#
# NOTE: this can only work where the kernel has a real KMS driver bound (i915
# here). On the plain EFI framebuffer (simpledrm) there is exactly one output
# whose mode is fixed, xrandr cannot add another, and this correctly does
# nothing rather than pretending.
XR=$(xrandr 2>/dev/null) || exit 0
[ -n "$XR" ] || exit 0

# The internal panel first if there is one: it is the screen the user is sitting
# at, so it owns the origin and everything else mirrors onto it.
PRIMARY=$(printf '%s\n' "$XR" | awk '/^(eDP|LVDS|DSI)[^ ]* connected/{print $1; exit}')
[ -n "$PRIMARY" ] || PRIMARY=$(printf '%s\n' "$XR" | awk '/ connected/{print $1; exit}')
[ -n "$PRIMARY" ] || exit 0

# The internal panel keeps the behaviour it always had: 1920x1080 when it is
# genuinely offered, otherwise its own preferred mode.
if printf '%s\n' "$XR" | grep -qE "^[[:space:]]+1920x1080"; then
	xrandr --output "$PRIMARY" --primary --mode 1920x1080 2>/dev/null || \
		xrandr --output "$PRIMARY" --primary --auto 2>/dev/null
else
	xrandr --output "$PRIMARY" --primary --auto 2>/dev/null
fi

# Every OTHER connected output mirrors it. --auto picks the television's own
# preferred mode, which is what a TV reports over HDMI and the only mode it is
# guaranteed to display without overscan surprises.
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
exit 0
