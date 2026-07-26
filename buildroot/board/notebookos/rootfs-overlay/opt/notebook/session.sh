#!/bin/sh
#
# The X session for Notebook OS. Runs as the client of `xinit`; when this
# script exits, X exits. Sets the papertone desktop backdrop, starts a
# lightweight window manager, and launches the native GTK desktop shell.
#
set -x
echo "== notebook session starting =="
export DISPLAY=:0
export GTK_THEME=Papertone
export XDG_DATA_DIRS=/usr/share
export XDG_CONFIG_HOME=/root/.config
export XCURSOR_THEME=notebook
export XCURSOR_SIZE=32
export NB_HOME=/root
export PYTHONPATH=/opt/notebook/de
export GDK_BACKEND=x11
# No assistive technology is shipped (no screen reader, no magnifier), but GTK
# still loads the atk-bridge module in EVERY app and D-Bus-activates the whole
# at-spi2 stack for it — measured on a cold boot: three extra processes
# (at-spi-bus-launcher, its private dbus-daemon, at-spi2-registryd) and an
# accessible-tree registration in each of the seven desktop processes, all
# talking to nothing. Turning the bridge off is the one env var that removes it.
export NO_AT_BRIDGE=1

# Use the display's NATIVE/preferred resolution. Real hardware boots on the EFI
# framebuffer (simpledrm), whose mode is FIXED at the panel's native size and
# cannot be changed by xrandr — and forcing a fixed 1920x1080 there (or a custom
# modeline) is a no-op at best and, on a smaller panel, leaves the desktop laid
# out for 1920 wide so the right-hand widgets fall off-screen. --auto selects the
# preferred mode; the UI itself is responsive to whatever size it gets.
# One xrandr invocation, not three: this runs before anything is on screen, and
# on a CPU-rendered machine booting off a compressed read-only root every extra
# process is a cold binary read. Ask once, parse the answer twice.
XR=$(xrandr 2>/dev/null || true)
OUT=$(printf '%s\n' "$XR" | awk '/ connected/{print $1; exit}')
if [ -n "$OUT" ]; then
	# On a mode-settable GPU (QEMU virtio-gpu), prefer 1920x1080 if the panel
	# actually offers it; otherwise fall back to the native preferred mode.
	if printf '%s\n' "$XR" | grep -qE "^\s+1920x1080"; then
		xrandr --output "$OUT" --mode 1920x1080 2>/dev/null || \
			xrandr --output "$OUT" --auto 2>/dev/null
	else
		xrandr --output "$OUT" --auto 2>/dev/null
	fi
fi
unset XR

# never blank the screen — this is a single-seat appliance, and blanking
# gets in the way of a walk-up demo / hardware bring-up. One xset, not three.
xset s off s noblank -dpms 2>/dev/null

# ---------------------------------------------------------------------------
# FIRST PIXELS.
#
# Everything above is invisible; everything below this block is invisible until
# it finishes. So the field, the window manager and the loading screen go up
# HERE, before any of the slow setup runs, and the rest of the session starts
# underneath them.
#
# This used to sit two thirds of the way down the file. Measured on a cold live
# boot (TCG, software rendering): the splash was spawned 13.8s after the session
# began, and the screen was blank for that entire time. The work that filled
# those 13.8s — the keyboard layout, the mixer, the root pixmap, the accel probe
# — is all work the user cannot see and does not need before the machine looks
# alive, so none of it belongs in front of the splash.
#
# The user's saved backdrop is resolved BEFORE the field is painted rather than
# after. The old order painted the default #DED4C2, started desktopbg.py on it,
# then read the setting, killed that process and started a second one on the
# right colour — a visible flash of the wrong colour plus a wasted CPython
# start, a ps|grep|awk pipeline and a kill, every boot.
#
# Fully guarded: a missing/garbage settings.json or absent python just leaves
# the default field, and the session proceeds. Pulled out with sed rather than a
# whole python3 interpreter — starting CPython to read one hex string cost more
# than everything else here combined. The `case` is what makes this safe:
# anything that is not a literal #RRGGBB is ignored and the default stands.
NB_BG=$(sed -n 's/.*"background"[[:space:]]*:[[:space:]]*"\(#[0-9A-Fa-f]\{3,8\}\)".*/\1/p' \
	"${NB_HOME:-/root}/.config/notebook/settings.json" 2>/dev/null | head -1)
case "$NB_BG" in
	'#'*) ;;
	*) NB_BG="#DED4C2" ;;      # papertone desktop field
esac

# The field itself: one X round-trip, on screen immediately.
xsetroot -solid "$NB_BG" 2>/dev/null

# A minimal window manager (matchbox) for move/stack; the shell + apps carry
# their own client-side title bars to match the design language. It starts
# before the first window maps so nothing has to be re-parented after the fact.
# dialog_mode free: dialog-type windows (the Finder) float at the size and
# position they ask for instead of being maximized like main windows.
matchbox-window-manager -use_titlebar no -use_cursor yes \
	-use_dialog_mode free >/dev/null 2>&1 &

# A session dbus, so the GTK processes below all share one bus instead of each
# autolaunching its own.
if command -v dbus-launch >/dev/null 2>&1; then
	eval "$(dbus-launch --sh-syntax --exit-with-session)"
fi

# Boot loading screen: the snail logo + "Notebook OS" + a filling progress bar,
# kept above everything while the desktop parts start beneath it. It dismisses
# itself when the shell writes /tmp/nb-ready (panel mapped), so clear any stale
# flag before it goes up. See splash.py.
rm -f /tmp/nb-ready
python3 /opt/notebook/de/splash.py &
# ---------------------------------------------------------------------------

# Keyboard layout from Region & Language (locale.json). Defaults to the UI
# language's layout; Chinese uses a US base + the Pinyin input method.
# Backgrounded: this is a CPython start plus setxkbmap (which forks xkbcomp to
# compile the keymap), and the layout only has to be right before someone
# types — which is long after the desktop is on screen. On the critical path it
# held the loading screen back by its whole runtime on every boot.
(
	# nbi18n owns the layout AND how to apply it: a two-layout string like
	# "ru,us" needs grp:alt_shift_toggle or the Latin half is unreachable, and
	# the user could not type a file name. Ask it for the argv rather than
	# reimplementing that rule here.
	NB_KBCMD="$(python3 -c 'import nbi18n; print(" ".join(nbi18n.xkb_args(nbi18n.keyboard())))' 2>/dev/null)"
	${NB_KBCMD:-setxkbmap us} 2>/dev/null || setxkbmap us 2>/dev/null
) &

# Audio comes up MUTED on a fresh ALSA state, which reads as "sound doesn't
# work" even with the right codec driver loaded. Un-mute and raise the common
# output controls to a sensible default (best-effort across card naming). The
# Settings ▸ Sound page drives volume from here on via amixer.
# Ten process spawns (amixer x9 + alsactl) that nothing on screen depends on, so
# they run in the BACKGROUND: audio just needs to be unmuted before the user
# reaches for the volume, not before the first pixel is drawn. Keeping them on
# the critical path delayed the splash by their whole runtime on every boot.
if command -v amixer >/dev/null 2>&1; then
	(
		for ctl in Master Speaker Headphone PCM Front "Front Speaker" Line-Out; do
			amixer -q -M sset "$ctl" unmute 2>/dev/null
		done
		amixer -q -M sset Master 85% unmute 2>/dev/null
		amixer -q -M sset Speaker 90% unmute 2>/dev/null
		amixer -q -M sset PCM 90% unmute 2>/dev/null
		# some codecs default the speaker path to the "auto-mute" behaviour that
		# silences the internal speakers unless headphones are unplugged — leave
		# that alone, but make sure a bare headphone/speaker isn't force-muted.
		alsactl store 2>/dev/null || true
	) &
fi

# The backdrop, in the two forms a compositor needs.
# xsetroot (above) sets only the root background PIXEL, which a compositor never
# sees: xcompmgr composites a "root tile" built from _XROOTPMAP_ID and falls
# back to flat GREY when that property is missing — the grey desktop that
# appears the moment the compositor starts. xrootbg.py sets a real root pixmap
# AND publishes the property, so the backdrop survives compositing. It is
# backgrounded because the field is already painted and nothing waits on it.
python3 /opt/notebook/de/xrootbg.py "$NB_BG" 2>/dev/null &
# ...and the backdrop that actually survives compositing: a desktop-type
# window. Measured, with the root-only approaches above: xcompmgr shows a flat
# grey (#808080) and picom shows black (#000000) regardless of the root colour,
# because under a compositor the root is not what is on screen. desktopbg.py
# paints a real window at the bottom of the stack, which composites correctly
# with any compositor and is harmless without one.
python3 /opt/notebook/de/desktopbg.py "$NB_BG" >/dev/null 2>&1 &
# Desktop (root-window) pointer: the Notebook black-dot cursor — a medium black
# circle with a white ring. xsetroot's -cursor_name uses the core X font (a plain
# arrow) and ignores the Xcursor theme, so the root gets an XBM copy of the same
# dot (xsetroot sets RetainPermanent, so it survives this client exiting). GTK
# apps and the Finder pick up the antialiased themed version via XCURSOR_THEME +
# gtk-cursor-theme-name. Fall back to the font arrow if the bitmaps are missing.
if [ -f /opt/notebook/cursor/cursor.xbm ]; then
	xsetroot -cursor /opt/notebook/cursor/cursor.xbm /opt/notebook/cursor/mask.xbm 2>/dev/null \
		|| xsetroot -cursor_name left_ptr 2>/dev/null
else
	xsetroot -cursor_name left_ptr 2>/dev/null
fi

# (The saved backdrop from Settings ▸ Backdrop is resolved into $NB_BG up in the
# first-pixels block, so it is painted correctly the first time.)

# Accelerated-rendering detection — needed BEFORE the compositor decision below.
# NB_ACCEL gates the compositor AND interactive window move/resize (finder.py),
# so getting it wrong on real hardware silently costs the user draggable windows.
#
# A DRM render node (/dev/dri/renderD128) exists even under PURE SOFTWARE
# rendering, so its presence is NOT a usable signal. What actually decides it is
# WHICH KMS DRIVER IS BOUND, so ask the kernel directly:
#
#   i915 / xe / amdgpu / radeon / nouveau -> a real GPU driver: accelerated.
#   virtio_gpu                            -> only with host GL; the driver
#                                            reports "[drm] features: +virgl".
#   simple-framebuffer (simpledrm) / none -> the firmware framebuffer: software.
#
# This previously tested ONLY for the virgl dmesg line, which is virtio-specific:
# on real Intel hardware i915 binds and renders in hardware but prints no such
# line, so NB_ACCEL came out 0 and the desktop ran in its software-fallback mode
# — no compositor, and windows that could not be moved or resized — on exactly
# the machines that needed it least.
NB_ACCEL=0
for _card in /sys/class/drm/card[0-9]*; do
	[ -e "$_card/device/driver" ] || continue
	_drv=$(basename "$(readlink -f "$_card/device/driver" 2>/dev/null)" 2>/dev/null)
	case "$_drv" in
		i915|xe|amdgpu|radeon|nouveau)
			NB_ACCEL=1 ;;
		virtio_gpu|virtio-pci)
			dmesg 2>/dev/null | grep -qE "\[drm\] features:.*\+virgl" && NB_ACCEL=1 ;;
	esac
done
unset _card _drv
# Explicit override for testing either path: nb.accel=1 / nb.accel=0.
grep -qw nb.accel=1 /proc/cmdline 2>/dev/null && NB_ACCEL=1
grep -qw nb.accel=0 /proc/cmdline 2>/dev/null && NB_ACCEL=0
export NB_ACCEL
echo "== notebook: NB_ACCEL=$NB_ACCEL =="

# COMPOSITOR — picom, configured in /etc/picom.conf.
#
# It backs every window in an off-screen pixmap, which is what makes windows
# move and resize smoothly and makes a dropdown opening over another window
# paint correctly.
#
# This used to run xcompmgr, and xcompmgr is why the desktop went grey and app
# canvases (Writer, Novel) went black the moment the compositor came up:
#   * it paints a flat GREY root tile when _XROOTPMAP_ID is absent, ignoring
#     the xsetroot backdrop colour entirely (xrootbg.py above now publishes
#     that property, but picom is the one that reads it correctly), and
#   * it mishandles windows carrying an ARGB visual, compositing anything the
#     app has not painted as BLACK.
# picom handles both correctly, repaints only damaged regions, and is the
# compositor this desktop is now built around.
#
# The backend is xrender either way (see /etc/picom.conf and the note below).
# "nb.compositor=1" forces it on for testing, "nb.nocompositor=1" off.
if grep -qw nb.nocompositor=1 /proc/cmdline 2>/dev/null; then
	: # explicitly disabled
elif [ "$NB_ACCEL" = 1 ] || grep -qw nb.compositor=1 /proc/cmdline 2>/dev/null; then
	if command -v picom >/dev/null 2>&1; then
		# Backend comes from /etc/picom.conf (xrender) and is NOT overridden
		# here. picom 10 moved glx behind --legacy-backends, so passing
		# "--backend glx" makes it exit immediately with
		#   Backend "glx" is only available as part of the legacy backends
		# — which is how the compositor silently failed to start at all.
		# xrender is the right backend regardless: this desktop composites
		# plain opaque rectangles and uses no shadow, fade or blur, so there
		# is nothing for glx to accelerate.
		picom --config /etc/picom.conf >/dev/null 2>&1 &
	elif command -v xcompmgr >/dev/null 2>&1; then
		xcompmgr -n >/dev/null 2>&1 &   # last resort; see the note above
	fi
fi

# (The window manager, the session dbus and the loading screen were all started
# up in the first-pixels block.)

# scanout-flush daemon: makes freshly-mapped windows paint promptly on the
# software (swrast) virtio-gpu stack, which has no real vblank. On virgl / a real
# GPU, first paints land on their own and the daemon's perpetual pointer-warp +
# blocking flush/sync would only add latency AND make the cursor vanish when
# focus leaves and re-enters a window. So run it only when NOT accelerated.
if [ "$NB_ACCEL" = 0 ]; then
	python3 /opt/notebook/de/xflushd.py >/dev/null 2>&1 &
fi

# open a Finder window on the applications folder (the default desktop state)
# global media keys: volume + brightness with an on-screen % popup. Its own
# X connection grabs the XF86 keysyms on the root window (see de/nbmediakeys.py),
# so it works regardless of which window has focus. Harmless where there is no
# backlight (brightness keys just no-op).
python3 /opt/notebook/de/nbmediakeys.py >/dev/null 2>&1 &

python3 /opt/notebook/de/finder.py &

# the desktop-home widget column (Tasks + Calendar) to the right of the Finder
python3 /opt/notebook/de/widgets.py &

# NOTE: the live session deliberately does NOT auto-launch the installer.
# It used to open a few seconds in, which meant a walk-up user met a
# disk-erasing wizard before they had seen the desktop — the live session is for
# trying the OS, and installing is one choice among many, not the default. The
# installer is launched like any other app: Finder ▸ Applications ▸
# "Install Notebook OS" (finder.py maps that .app to de/installer.py).

# the desktop shell (panel + session). It runs in the foreground; when it
# exits, the session ends.
exec python3 /opt/notebook/de/shell.py
