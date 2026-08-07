#!/bin/sh
#
# guestrun — run a host-side render/selftest in the GUEST's graphics environment.
#
# Every visual check in this repo needs the same five environment variables set
# the same way, and getting any one of them wrong does not fail — it renders
# something plausible and WRONG, which is the worst possible outcome for a tool
# whose entire purpose is to be believed. Two real examples from this project:
#
#   * without FONTCONFIG_FILE the render uses the developer's fonts, so type is
#     judged in a face the OS does not ship;
#   * without GTK_THEME + XDG_DATA_DIRS, Papertone is layered ON TOP of whatever
#     theme the developer runs (TraditionalOk on this machine) at a priority
#     that wins for properties Papertone SETS — and loses for every property it
#     does not. So any gap in the theme is silently filled in by the host and
#     looks fine here while looking like nothing at all on the guest. That is
#     exactly backwards from what a fidelity harness is for.
#
# Setting GTK_THEME=Papertone with XDG_DATA_DIRS pointed at the overlay makes
# GTK load Papertone as THE theme, the way the guest's session.sh does, so the
# host theme is never loaded and cannot contribute anything.
#
#   tools/guestrun.sh python3 tools/controlshot.py /tmp/controls.png
#   tools/guestrun.sh python3 tools/textshot.py /tmp/spec.png
#
# DISPLAY is passed through if already set (renders are offscreen but GTK still
# needs an X connection); it defaults to :0.
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
OVERLAY="$ROOT/buildroot/board/notebookos/rootfs-overlay"

[ -d "$OVERLAY/usr/share/themes/Papertone" ] || {
	echo "guestrun: Papertone not found under $OVERLAY" >&2
	exit 1
}

export DISPLAY="${DISPLAY:-:0}"
# GTK_THEME is what actually excludes the host theme: GTK loads exactly ONE
# theme, named by GTK_THEME when set, so TraditionalOk (or whatever the
# developer runs) is never loaded at all and cannot fill in a gap in Papertone.
export GTK_THEME=Papertone
# The overlay goes FIRST so Papertone is found there, but the host's share tree
# is KEPT on the end. Replacing it outright took gdk-pixbuf's loaders and the
# icon theme with it, and GTK aborted before drawing anything:
#   Failed to load .../image-missing.png: Unrecognized image file format
# Themes come from the overlay; icons and loaders still have somewhere to
# resolve from.
export XDG_DATA_DIRS="$OVERLAY/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export FONTCONFIG_FILE="$HERE/guest-fonts.conf"
# The same stem-darkening the guest session exports. Text rendered without it is
# ~21% lighter in ink mass than what ships, which is more than enough to draw
# the wrong conclusion about weight, contrast or hinting.
export FREETYPE_PROPERTIES="cff:no-stem-darkening=0 autofitter:no-stem-darkening=0"
# No assistive tech on the guest, and loading the bridge here just slows renders.
export NO_AT_BRIDGE=1
# Apps read their data out of $NB_HOME; renders must not touch the developer's.
export NB_HOME="${NB_HOME:-/tmp/nb-guestrun-home}"
mkdir -p "$NB_HOME" 2>/dev/null || true
export PYTHONPATH="$OVERLAY/opt/notebook/de${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p /tmp/nb-fontcache 2>/dev/null || true

exec "$@"
