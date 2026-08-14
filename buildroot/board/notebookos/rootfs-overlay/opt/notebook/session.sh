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

# STEM DARKENING — the one text-rendering lever that is off by default and that
# visibly separates this from a stock Linux desktop.
#
# Antialiasing computes a glyph's coverage geometrically, and for a thin stem
# that lands between pixel centres the result is a row of mid-greys that the eye
# reads as LIGHTER than the weight the type designer drew. The thinner the stem
# and the smaller the size, the bigger the error. Nimbus Sans is a Helvetica
# design — light, even stems — set as ink on warm paper rather than black on
# white, which is the case where the washing-out is most visible. FreeType can
# compensate by thickening stems in proportion to how few pixels they get, and
# it is what gives macOS/iOS text its characteristic solidity at small sizes.
#
# It is OFF by default (FreeType turns darkening off for the CFF driver because
# it interacts badly with subpixel AA — we render GRAYSCALE, see
# root/.config/gtk-3.0/settings.ini, so that objection does not apply here).
# Nimbus Sans ships as OTF/CFF, so the `cff` driver is the one that matters;
# `autofitter` covers the TrueType faces (Liberation, Noto) on the same terms.
#
# MEASURED, on the 13px ink-on-paper specimen that is this OS's workhorse size:
# +21.3% ink mass, 15.9% of pixels changed (tools/textshot.py renders the
# before/after). The effect is ppem-scaled, so it tapers off on large text and
# on a HiDPI panel, which is exactly where it is no longer needed.
export FREETYPE_PROPERTIES="cff:no-stem-darkening=0 autofitter:no-stem-darkening=0"
# HOME was never exported here, so the session inherited whatever init left --
# "/" in practice. Everything that caches under $HOME then wrote to the
# filesystem root or failed: GStreamer keeps its PLUGIN REGISTRY in
# $XDG_CACHE_HOME (else $HOME/.cache), and without a registry it cannot create
# `playbin`, which is exactly the state where Music lists a track, highlights
# it, and never plays a sound.
export HOME=/root
export XDG_CONFIG_HOME=/root/.config
export XDG_CACHE_HOME=/root/.cache
mkdir -p /root/.cache 2>/dev/null || true
export XCURSOR_THEME=notebook
export XCURSOR_SIZE=32
export NB_HOME=/root

# TIME ZONE, for the whole session.
#
# Settings ▸ Date & Time writes the choice to settings.json and calls tzset() —
# but os.environ["TZ"] reaches only the Settings process, so the panel clock,
# Calendar, Journal and every other app carried on in the zone the machine
# booted in. Exporting it HERE, before anything starts, is what makes the
# choice mean something: every app is a child of this shell.
#
# The POSIX form, not the IANA name: no zoneinfo ships on this image, so
# "Europe/Paris" would name a file that is not there while
# "CET-1CEST,M3.5.0,M10.5.0/3" carries the offset AND the daylight-saving rule
# on its own. settings.py stores both for exactly this reason.
#
# Inline, not backgrounded: a variable exported by a subshell is lost, and the
# whole point is that the apps below inherit it.
NB_TZ=$(python3 -c 'import json, os
try:
    with open(os.path.join(os.environ.get("NB_HOME", "/root"),
                           ".config", "notebook", "settings.json")) as fh:
        v = json.load(fh).get("tz_posix")
    print(v if isinstance(v, str) and v else "")
except Exception:
    print("")' 2>/dev/null)
if [ -n "$NB_TZ" ]; then
	export TZ="$NB_TZ"
fi
unset NB_TZ
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
# Every connected display, not just the first: see opt/notebook/display.sh.
# A television on HDMI is a SECOND output, and taking only the first is why it
# stayed dark. The same script runs again from a udev rule when a cable is
# plugged in after boot.
/opt/notebook/display.sh

# INTERFACE SCALE, from the panel display.sh just measured.
#
# This has to be exported HERE: GDK reads it once when a process starts, so it
# must be set before the first GTK process (the loading screen, a few lines
# down) and before every one after it. Nothing can change it later without
# restarting the session, which is why it is computed from the panel rather than
# from a setting somebody has to find.
#
# A missing file means "1" -- display.sh deliberately writes nothing when it
# could not measure the screen, because on an unknown panel 1x is the answer
# that is never unusable, while a wrong 2x halves the desktop.
NB_SCALE=1
if [ -r /tmp/nb-scale ]; then
	read -r _s < /tmp/nb-scale 2>/dev/null
	case "$_s" in
		1|2|3) NB_SCALE=$_s ;;   # anything else is not a scale; ignore it
	esac
	unset _s
fi
# Explicit override for testing a scale the attached panel would not choose:
# nb.scale=1 / nb.scale=2. Same shape as nb.accel below.
for _a in $(cat /proc/cmdline 2>/dev/null); do
	case "$_a" in
		nb.scale=1) NB_SCALE=1 ;;
		nb.scale=2) NB_SCALE=2 ;;
		nb.scale=3) NB_SCALE=3 ;;
	esac
done
unset _a
export NB_SCALE
# GDK_SCALE is the integer factor GTK draws at: at 2 it renders every widget,
# border and glyph at twice the pixels and reports HALF the logical size to the
# app, which is exactly what makes text and vector chrome resolve at full panel
# density instead of being upscaled.
export GDK_SCALE="$NB_SCALE"
# NOT set: GDK_DPI_SCALE. It divides text size back down to compensate when
# GDK_SCALE has been raised for a screen that did not really need it. Ours is
# only ever raised on a panel dense enough to earn it, so dividing the text
# again would just make it small.
echo "== notebook: NB_SCALE=$NB_SCALE =="

# never blank the screen — this is a single-seat appliance, and blanking
# gets in the way of a walk-up demo / hardware bring-up. One xset, not three.
# This is the DEFAULT, for a machine whose owner has never said otherwise.
xset s off s noblank -dpms 2>/dev/null

# Then whatever they DID say. Settings used to apply screen blanking, key
# repeat and render scale in its own __init__, so a saved preference took
# effect only while Settings was open — nothing starts it at boot, and the line
# above then actively undid the blanking choice on every restart while the page
# went on displaying it. nbprefs is the same code the page calls, so the two
# cannot drift; it imports no Gtk, so it costs the boot path nothing; and it
# touches only keys that are actually present, which is what leaves the default
# above in charge of a fresh machine.
python3 /opt/notebook/de/nbprefs.py 2>/dev/null

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
# One fixed colour. The alternate desktop colours were removed before release
# (they mis-rendered behind the widget board), so this no longer reads
# settings.json — a "background" left there by an older build must NOT come
# back, because there is no longer a screen that could change it again.
NB_BG="#DED4C2"                    # papertone desktop field, the only one

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
#
# BOUNDED, and it is the only step before the first pixel that is not. This runs
# BEFORE the loading screen goes up, synchronously, and dbus-launch waits for the
# daemon it forks to report its address back down a pipe. If that ever fails to
# arrive -- a full /tmp, an exhausted fd table, a machine whose hostname will not
# resolve -- the read blocks with nothing whatever on screen, and the user is
# holding a laptop showing black. Ten seconds of a shared bus is worth waiting
# for; forever is not. On timeout the session simply continues: every GTK app
# below then autolaunches its own bus, which is what happened before there was a
# session bus here at all, so the desktop still comes up.
# The bound is ARMED. board/notebookos/busybox-timeout.fragment sets
# CONFIG_TIMEOUT=y and BR2_PACKAGE_BUSYBOX_CONFIG_FRAGMENT_FILES points at it,
# so /usr/bin/timeout ships (a busybox symlink, verified in rootfs.tar) and the
# `command -v timeout` branch below is the one that runs. This note used to say
# the applet was missing and the bound inert; somebody added the fragment and
# the note was not updated, so it advertised a protection the session did have.
# The `command -v` test stays: writing `timeout 10 dbus-launch` unconditionally
# would make the command not-found on any image built without the fragment,
# leaving _dbus_env empty and quietly taking the session bus away from every
# app -- a worse failure than the one being guarded against.
if command -v dbus-launch >/dev/null 2>&1; then
	if command -v timeout >/dev/null 2>&1; then
		_dbus_env=$(timeout 10 dbus-launch --sh-syntax --exit-with-session 2>/dev/null)
	else
		_dbus_env=$(dbus-launch --sh-syntax --exit-with-session 2>/dev/null)
	fi
	if [ -n "$_dbus_env" ]; then
		eval "$_dbus_env"
	else
		# Not fatal: each GTK app then autolaunches its own bus, which is what
		# happened before there was a session bus here at all.
		echo "session: no session dbus; apps will use their own bus" >&2
	fi
	unset _dbus_env
fi

# Boot loading screen: the snail logo + "Notebook OS" + a filling progress bar,
# kept above everything while the desktop parts start beneath it. It dismisses
# itself when the shell writes /tmp/nb-ready (panel mapped), so clear any stale
# flag before it goes up. See splash.py.
rm -f /tmp/nb-ready
python3 /opt/notebook/de/splash.py &
NB_SPLASH_PID=$!
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
	# reimplementing that rule here. ensure_latin is the same rule enforced
	# from the other end: a saved layout with NO group that can type ASCII
	# (kana was one) gets US added, because a password, a file name and a
	# search term are ASCII on this machine whatever the interface is in.
	#
	# It RUNS setxkbmap rather than printing the argv for the shell to split.
	# One of those arguments is an empty string (`-option ""`, which clears the
	# options the server is already carrying), and an empty string does not
	# survive word splitting: the command came back out of "$(...)" as
	# `-option -option grp:alt_shift_toggle`, i.e. an option literally named
	# "-option". Handing the list straight to exec is the only shape that
	# cannot lose an argument.
	python3 -c 'import nbi18n, nbkeyboard
raise SystemExit(0 if nbkeyboard.apply(
    nbkeyboard.ensure_latin(nbi18n.keyboard())) else 1)' 2>/dev/null \
		|| setxkbmap us 2>/dev/null
) &
# Remembered because the FIRST thing typed on this machine is the password, and
# it has to be typed on the user's own layout. See the sign-in block below.
NB_KB_PID=$!

# Audio comes up MUTED on a fresh ALSA state, which reads as "sound doesn't
# work" even with the right codec driver loaded. Un-mute the output controls,
# raise them to a sensible default, and point ALSA's "default" device at the
# speakers that are actually there — a television on HDMI if one is plugged in,
# otherwise the built-in ones. See de/nbaudio.py for why all three are needed;
# in short, HDMI is a different PCM device, its mute is IEC958 rather than
# Master, and "default" otherwise follows card 0, which is a USB microphone the
# moment one is plugged in.
#
# Backgrounded: nothing on screen depends on it, and audio only has to be ready
# before the user reaches for the volume, not before the first pixel. Keeping
# these process spawns on the critical path delayed the splash by their whole
# runtime on every boot.
if command -v amixer >/dev/null 2>&1; then
	(
		# The route first, because it pins ctl.!default to the card that is
		# playing — without that, every amixer call below can land on the wrong
		# card and silently do nothing.
		python3 /opt/notebook/de/nbaudio.py apply >/dev/null 2>&1
		for ctl in Master Speaker Headphone PCM Front "Front Speaker" Line-Out; do
			amixer -q -M sset "$ctl" unmute 2>/dev/null
		done
		amixer -q -M sset Master 85% unmute 2>/dev/null
		amixer -q -M sset Speaker 90% unmute 2>/dev/null
		amixer -q -M sset PCM 90% unmute 2>/dev/null
		# some codecs default the speaker path to the "auto-mute" behaviour that
		# silences the internal speakers unless headphones are unplugged — leave
		# that alone, but make sure a bare headphone/speaker isn't force-muted.

		# The CAPTURE side, which the block above never touched. It comes up
		# muted and at zero on a fresh ALSA state exactly as playback does, and
		# the result is worse to diagnose: arecord opens the device, writes a
		# valid WAV and fills it with silence, so the Sequencer records a take
		# that plays back inaudibly with no error anywhere. Done for EVERY card
		# that has a capture device, because a microphone is usually not the
		# card the speakers are on. (The Sequencer repeats this for the chosen
		# device before each take, which is what covers a mic plugged in after
		# boot — see de/sequencer.py open_capture_path.)
		if command -v arecord >/dev/null 2>&1; then
			for c in $(arecord -l 2>/dev/null \
					| awk -F'[ :]' '/^card /{print $2}' | sort -u); do
				for ctl in Capture Mic "Internal Mic" "Front Mic" "Rear Mic" \
						"Line" "Line In" Digital; do
					# `cap` is the capture SWITCH and is a different control
					# from `unmute`; both are needed.
					amixer -q -c "$c" -M sset "$ctl" cap 2>/dev/null
					amixer -q -c "$c" -M sset "$ctl" unmute 2>/dev/null
				done
				amixer -q -c "$c" -M sset Capture 80% cap 2>/dev/null
				amixer -q -c "$c" -M sset Mic 80% unmute 2>/dev/null
				# HDA leaves the boost at 0 on many codecs, which records a
				# technically valid and completely inaudible signal.
				for ctl in "Mic Boost" "Internal Mic Boost" "Front Mic Boost"; do
					amixer -q -c "$c" -M sset "$ctl" 50% 2>/dev/null
				done
			done
		fi
		# Whatever the person last chose in Settings, applied last so it
		# wins over the defaults above. Nothing on this machine restores a
		# mixer at boot — there is no alsactl restore in any init script —
		# so without this the volume, microphone level and mute set in
		# Settings came back to these defaults after every reboot while the
		# page had reported them as set.
		_snd="${NB_HOME:-/root}/.config/notebook/settings.json"
		if [ -r "$_snd" ]; then
			_v=$(sed -n 's/.*"sound\.volume"[[:space:]]*:[[:space:]]*\([0-9]\{1,3\}\).*/\1/p' "$_snd" | head -1)
			_c=$(sed -n 's/.*"sound\.capture"[[:space:]]*:[[:space:]]*\([0-9]\{1,3\}\).*/\1/p' "$_snd" | head -1)
			_m=$(sed -n 's/.*"sound\.muted"[[:space:]]*:[[:space:]]*\(true\|false\).*/\1/p' "$_snd" | head -1)
			[ -n "$_v" ] && amixer -q -M sset Master "$_v%" 2>/dev/null
			[ -n "$_c" ] && amixer -q -M sset Capture "$_c%" 2>/dev/null
			[ "$_m" = "true" ] && amixer -q sset Master mute 2>/dev/null
			[ "$_m" = "false" ] && amixer -q sset Master unmute 2>/dev/null
		fi
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
# The decision moved into opt/notebook/accel.sh, because it stopped being a
# two-line test: a KMS driver being bound says the SCREEN works, not that MESA
# can render on it, and this image does not ship a Mesa driver for every GPU the
# kernel can bind. Asking only the kernel (which is what this block used to do)
# marked every AMD, NVIDIA and pre-2014 Intel laptop as accelerated while Mesa
# fell back to software — turning OFF the software path's paint helper and
# turning ON a vsynced compositor on precisely the machines least able to afford
# it. See that file for the full reasoning and its honest limitation.
# Run through `sh` rather than executing the file directly. The probe was first
# written as `$(/opt/notebook/accel.sh)`, and the file reached the image with
# mode 644 because that is what it was created with in the overlay -- so it died
# with "Permission denied", the substitution came back EMPTY, the guard below
# read that as "unreadable" and every machine in the world fell to NB_ACCEL=0.
# The whole desktop then ran in software mode: no compositor, no window shadows,
# no animations, and the slow-path repaint daemon started on hardware that did
# not need it. Nothing said a word, because 0 is a completely legitimate value.
# `sh <file>` does not care about the execute bit, so the mode can never take
# the desktop's rendering mode away again.
NB_ACCEL=$(sh /opt/notebook/accel.sh 2>/dev/null)
case "$NB_ACCEL" in
	0|1) ;;
	*)
		# Say so. A silent fall back to 0 is indistinguishable from a machine
		# that genuinely has no acceleration, which is exactly what made the
		# bug above invisible.
		echo "session: accel probe failed, assuming software rendering" >&2
		NB_ACCEL=0 ;;
esac
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
	NB_COMP_PID=      # the compositor that was started, if one was
	NB_COMP_ALT=      # what is left to try if it does not survive
	if command -v picom >/dev/null 2>&1; then
		# Backend comes from /etc/picom.conf (xrender) and is NOT overridden
		# here. picom 10 moved glx behind --legacy-backends, so passing
		# "--backend glx" makes it exit immediately with
		#   Backend "glx" is only available as part of the legacy backends
		# — which is how the compositor silently failed to start at all.
		# xrender is the right backend regardless: this desktop composites
		# plain opaque rectangles and uses no shadow, fade or blur, so there
		# is nothing for glx to accelerate.
		# --vsync ONLY on genuinely accelerated hardware. Presenting on the
		# vertical blank is what makes window movement and scrolling look
		# solid instead of torn; waiting for a blank that does not exist (a
		# compositor forced on with nb.compositor=1 over software rendering)
		# would stall the desktop instead, so the flag follows NB_ACCEL and
		# not the config file.
		if [ "$NB_ACCEL" = 1 ]; then
			picom --config /etc/picom.conf --vsync >/dev/null 2>&1 &
		else
			picom --config /etc/picom.conf >/dev/null 2>&1 &
		fi
		NB_COMP_PID=$!
		command -v xcompmgr >/dev/null 2>&1 && NB_COMP_ALT=xcompmgr
	elif command -v xcompmgr >/dev/null 2>&1; then
		xcompmgr -n >/dev/null 2>&1 &   # last resort; see the note above
		NB_COMP_PID=$!
	fi

	# ...and then SOMEBODY HAS TO LOOK AT IT AGAIN.
	#
	# The launch above is `&` and nothing ever waited on it, so a compositor
	# that exits one second in is indistinguishable from one that is running:
	# the session carries on and the desktop comes up with no compositing at
	# all. That is not hypothetical — it is exactly what "--backend glx" did
	# (see the note above), and a bad /etc/picom.conf, a missing xrender
	# extension or an X server that refuses the composite redirect all end the
	# same way. The half that makes it hurt is the NB_ACCEL=1 half: the
	# scanout-flush daemon further down is deliberately started only when
	# NB_ACCEL=0, so on a machine the probe called accelerated a dead
	# compositor leaves NEITHER a compositor NOR the software repaint helper,
	# and the accel probe's own honest limitation (a bound KMS driver is not a
	# working Mesa driver) means the machines most likely to lose picom are
	# precisely the ones that needed that helper.
	#
	# So: a grace period, one `kill -0`, and a ladder down. Backgrounded,
	# because the boot must not spend two seconds here — everything below this
	# point is start-up the user is waiting on, and the answer is not needed
	# before any of it.
	if [ -n "$NB_COMP_PID" ]; then
		(
			# Long enough for the failures above to have happened (they are
			# immediate: a config or extension error kills picom before it
			# maps anything), short enough that the desktop is not composited
			# by two things at once for any length of time.
			sleep 2
			if kill -0 "$NB_COMP_PID" 2>/dev/null; then
				exit 0
			fi
			echo "session: the compositor exited at start-up; falling back" >&2
			if [ "$NB_COMP_ALT" = xcompmgr ]; then
				xcompmgr -n >/dev/null 2>&1 &
				NB_ALT_PID=$!
				sleep 2
				if kill -0 "$NB_ALT_PID" 2>/dev/null; then
					exit 0
				fi
				echo "session: xcompmgr exited too" >&2
			fi
			# Nothing composites on this machine. If the probe said software,
			# the daemon below is already coming, so this is only for the
			# NB_ACCEL=1 case -- where a compositor that will not start is the
			# best evidence there is that the probe was wrong. Guarded so the
			# two launch sites cannot both fire and leave two daemons flushing
			# the same display. (This used to say they would "fight over the
			# cursor": that is xFLUSH, the one-shot pointer warp. xflushd
			# states outright that it does NOT warp the pointer -- it only
			# flushes -- so the comment was describing the wrong file.)
			if [ "$NB_ACCEL" = 1 ]; then
				echo "session: starting the software repaint daemon" >&2
				python3 /opt/notebook/de/xflushd.py >/dev/null 2>&1 &
			fi
		) &
	fi
	unset NB_COMP_PID NB_COMP_ALT
fi

# (The window manager, the session dbus and the loading screen were all started
# up in the first-pixels block.)

# scanout-flush daemon: makes freshly-mapped windows paint promptly on the
# software (swrast) virtio-gpu stack, which has no real vblank. On virgl / a
# real GPU, first paints land on their own and its blocking flush/sync twice a
# second would only add latency, so run it only when NOT accelerated. It does
# not touch the pointer -- the warp is xflush.py, run once per window map.
if [ "$NB_ACCEL" = 0 ]; then
	python3 /opt/notebook/de/xflushd.py >/dev/null 2>&1 &
fi

# open a Finder window on the applications folder (the default desktop state)
# global media keys: volume + brightness with an on-screen % popup. Its own
# X connection grabs the XF86 keysyms on the root window (see de/nbmediakeys.py),
# so it works regardless of which window has focus. Harmless where there is no
# backlight (brightness keys just no-op).
python3 /opt/notebook/de/nbmediakeys.py >/dev/null 2>&1 &

# ---- sign in -------------------------------------------------------------
# BEFORE anything of the desktop is drawn, and NOT backgrounded: the desktop
# must not exist behind it.
#
# `login.py --needed` answers "would a sign-in screen appear?" from two small
# file reads and one crypt call, without importing Gtk. Asking first buys three
# things that cannot be had by simply running login.py:
#
#  1. THE LOADING SCREEN GETS OUT OF THE WAY. The splash and the sign-in screen
#     are both full-screen, keep-above windows. Two of those on a WM that
#     stacks by focus is a race, and the losing outcome is a person staring at
#     "STARTING UP" while the prompt they need sits underneath it. So the
#     splash is retired first (it dismisses on /tmp/nb-ready), and a fresh one
#     goes up afterwards for the rest of the start-up — which the old order
#     could not do either, because the splash's own 30s failsafe fires while
#     somebody is still typing.
#  2. THE KEYBOARD IS THE USER'S OWN. The layout job above is backgrounded, and
#     the password is the first thing typed on this machine. On a French,
#     Russian or Greek install, typing it on the US layout it starts out with
#     produces a password that is simply wrong, with nothing on screen to say
#     why. Bounded, so a wedged setxkbmap costs five seconds and not the boot.
#  3. On a machine with no password — every live boot — nothing here loads Gtk
#     at all, where before it loaded Gtk, nbapp and nbicons just to return 0.
# ---- first-run setup, then sign in ---------------------------------------
# BEFORE anything of the desktop is drawn, and NOT backgrounded: the desktop
# must not exist behind either screen.
#
# Both gates ask first ("--needed") from a couple of small file reads, without
# importing Gtk. That buys three things:
#
#  1. THE LOADING SCREEN GETS OUT OF THE WAY, ONCE. The splash and these
#     screens are all full-screen keep-above windows, and on a WM that stacks
#     by focus two of them is a race whose losing outcome is somebody staring
#     at "STARTING UP" while the prompt they need sits underneath. So the
#     splash is retired ONCE here and put back ONCE afterwards -- not per
#     screen, which is how a machine with both ended up launching three.
#  2. THE KEYBOARD IS THE USER'S OWN. The layout job above is backgrounded and
#     the password is the first thing typed on this machine; on a French or
#     Russian install, typing it on the US layout it starts out with produces a
#     password that is simply wrong with nothing on screen to say why. Bounded,
#     so a wedged setxkbmap costs five seconds and not the boot.
#  3. On a machine with neither -- every live boot -- nothing here loads Gtk.
NB_WANT_FIRSTRUN=0
NB_WANT_LOGIN=0
python3 /opt/notebook/de/firstrun.py --needed && NB_WANT_FIRSTRUN=1
python3 /opt/notebook/de/login.py --needed && NB_WANT_LOGIN=1

if [ "$NB_WANT_FIRSTRUN" = 1 ] || [ "$NB_WANT_LOGIN" = 1 ]; then
	touch /tmp/nb-ready
	_i=0
	while [ "$_i" -lt 5 ] && kill -0 "$NB_KB_PID" 2>/dev/null; do
		sleep 1
		_i=$((_i + 1))
	done
	unset _i
	# First-run comes first: on a machine set up for somebody else there is no
	# password yet, and this screen is where one gets chosen.
	if [ "$NB_WANT_FIRSTRUN" = 1 ]; then
		python3 /opt/notebook/de/firstrun.py
		# It may have just set a password, so ask again rather than trusting
		# the answer from before it ran.
		NB_WANT_LOGIN=0
		python3 /opt/notebook/de/login.py --needed && NB_WANT_LOGIN=1
	fi
	if [ "$NB_WANT_LOGIN" = 1 ]; then
		python3 /opt/notebook/de/login.py
	fi
	# Put the configured layout back, unconditionally. Both screens above can
	# load a different one -- the sign-in screen switches alphabets so a Latin
	# password can be typed on a Cyrillic, Greek, Devanagari or Hebrew machine
	# -- and each restores it on the way out, but neither can if it is killed.
	# The desktop must come up in the alphabet its owner configured, so the
	# last word on the subject belongs here rather than in a screen that might
	# not reach its own last line.
	python3 -c 'import nbi18n, nbkeyboard
nbkeyboard.apply(nbkeyboard.ensure_latin(nbi18n.keyboard()))' 2>/dev/null &
	# Put a loading screen back for the rest of the start-up: the first one has
	# retired by now (the touch above), and its 30s failsafe would otherwise
	# expire while somebody is still typing.
	rm -f /tmp/nb-ready
	if ! kill -0 "$NB_SPLASH_PID" 2>/dev/null; then
		python3 /opt/notebook/de/splash.py &
		NB_SPLASH_PID=$!
	fi
fi

python3 /opt/notebook/de/finder.py &

# the desktop-home widget column (Tasks + Calendar) to the right of the Finder
python3 /opt/notebook/de/widgets.py &

# NOTE: the live session deliberately does NOT auto-launch the installer.
# It used to open a few seconds in, which meant a walk-up user met a
# disk-erasing wizard before they had seen the desktop — the live session is for
# trying the OS, and installing is one choice among many, not the default. The
# installer is launched like any other app: Finder ▸ Applications ▸
# "Install Notebook OS" (finder.py maps that .app to de/installer.py).

# clipboard keeper: X11 selections belong to the copying window, so remember
# CLIPBOARD and serve it only after that window exits. PRIMARY stays untouched.
python3 /opt/notebook/de/xclipd.py >/dev/null 2>&1 &

# tablet-mode watch: a convertible's flip arrives as SW_TABLET_MODE on a
# platform input device (intel-vbtn / ideapad-laptop / thinkpad-acpi). The
# daemon tracks it into /tmp/nb-tablet-mode, raises the on-screen keyboard
# while folded, and stays quietly dormant on hardware that never produces the
# switch — so this line is safe on every machine, convertible or not.
python3 /opt/notebook/de/xtabletd.py >/dev/null 2>&1 &

# the desktop shell (panel + session). It runs in the foreground; when it
# exits, the session ends.
exec python3 /opt/notebook/de/shell.py
