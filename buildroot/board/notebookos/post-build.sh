#!/bin/sh
# Notebook OS post-build: runs after the rootfs tree is assembled, before the
# image is packed. $1 = target dir ($TARGET_DIR).
set -e
TARGET="$1"

# executable bits (overlays lose them)
chmod 0755 "$TARGET/etc/init.d/S35fontcache" 2>/dev/null || true
chmod 0755 "$TARGET/etc/init.d/S99notebookos" 2>/dev/null || true
chmod 0755 "$TARGET/opt/notebook/session.sh" 2>/dev/null || true
chmod 0755 "$TARGET/opt/notebook/de/"*.py 2>/dev/null || true

# PRE-COMPILE THE DESKTOP TO BYTECODE. The DE modules arrive via the rootfs
# overlay, so Buildroot's own .pyc pass never sees them: without this every app
# launch re-parses ~100 KB of Python per imported module, on a machine that is
# already rendering every pixel on the CPU. The host python is the SAME 3.11 as
# the target (checked below), so its .pyc magic is valid there — a mismatched
# host python would silently emit bytecode the target ignores, which is why the
# version is compared rather than assumed.
# Build stamp for the About window. BUILD_ID goes into /etc/os-release so it
# travels with the image and is readable by the same os_release_field() helper
# the rest of the DE already uses. SOURCE_DATE_EPOCH is honoured when set, so a
# reproducible build stamps the source date rather than "now".
if [ -f "$TARGET/etc/os-release" ]; then
    sed -i '/^BUILD_ID=/d' "$TARGET/etc/os-release"
    if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
        _built=$(date -u -d "@$SOURCE_DATE_EPOCH" '+%Y-%m-%d' 2>/dev/null \
                 || date -u '+%Y-%m-%d')
    else
        _built=$(date -u '+%Y-%m-%d')
    fi
    echo "BUILD_ID=\"$_built\"" >> "$TARGET/etc/os-release"
    unset _built
fi

# PROPAGATE DELETIONS from the overlay.
# Buildroot COPIES the rootfs overlay over an existing target tree; it never
# removes anything. So deleting an app from the overlay leaves the old file in
# output/target and it keeps shipping — a removed app still appears in the
# Applications folder and still launches. (Messages and Tetris both survived
# their own removal this way.) Prune the two overlay-owned directories: any
# DE module or .app in the target that no longer has a counterpart in the
# overlay is stale by definition and is dropped.
_OV="$(dirname "$0")/rootfs-overlay"
if [ -d "$_OV/opt/notebook/de" ]; then
    for f in "$TARGET"/opt/notebook/de/*.py; do
        [ -e "$f" ] || continue
        [ -e "$_OV/opt/notebook/de/$(basename "$f")" ] || rm -f "$f"
    done
fi
if [ -d "$_OV/root/Applications" ]; then
    for f in "$TARGET"/root/Applications/*.app; do
        [ -e "$f" ] || continue
        [ -e "$_OV/root/Applications/$(basename "$f")" ] || rm -f "$f"
    done
fi
unset _OV

# Always start from a clean __pycache__: the host-side DE selftests
# (tools/construct_all_host.py) import these modules with the DEVELOPER's system
# python, which writes .pyc files for ITS version into the overlay — and the
# overlay is copied verbatim into the image. Bytecode for the wrong CPython is
# dead weight the target can never load (2.2 MB of it was shipping), so wipe the
# directory here rather than depending on anyone remembering to clean it.
rm -rf "$TARGET/opt/notebook/de/__pycache__"
# The same host-python litter appears beside the .app stubs and then LISTS in
# the Finder's Applications view as a folder named __pycache__ — the first row
# a person sees. Wipe every pycache under the notebook trees, not one path.
find "$TARGET/opt/notebook" "$TARGET/root" -name '__pycache__' -type d \
    -exec rm -rf {} + 2>/dev/null || true

HOSTPY="$(dirname "$0")/../../output/host/bin/python3"
if [ -x "$HOSTPY" ]; then
    HV=$("$HOSTPY" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
    TV=$(ls -d "$TARGET"/usr/lib/python3.* 2>/dev/null | head -1 | sed 's|.*/python||')
    if [ -n "$HV" ] && [ "$HV" = "$TV" ]; then
        "$HOSTPY" -m compileall -q "$TARGET/opt/notebook/de" >/dev/null 2>&1 || true
    else
        echo "post-build: host python ($HV) != target ($TV), skipping DE bytecode"
    fi
fi

# NO auto-login getty on tty1. This hook used to force one back in even if the
# overlay removed it, which is how an unauthenticated root shell survived. The
# desktop is started by S99notebookos (xinit), not by a login on tty1, so there
# is nothing to put here. Deliberately left as a comment: re-adding a getty
# with "-n -l /bin/sh" hands anyone at the keyboard -- or any USB device
# pretending to be one -- a root shell.

# SINGLE X LAUNCHER. This appliance starts X + the desktop exclusively via
# S99notebookos (xinit -> session.sh -> shell). The Buildroot xserver package
# also drops an S40xorg init that starts a SECOND, bare X server on :0 (no window
# manager, no session). Because S40 runs before S99, that bare server can grab
# display :0 first and then S99notebookos's xinit dies with "server already
# active for display 0" — a non-deterministic race that leaves no desktop.
# Remove it so S99notebookos deterministically owns the display.
rm -f "$TARGET/etc/init.d/S40xorg"

# OFFLINE BY DESIGN. The kernel is a no-internet fork (no AF_INET), so the
# Buildroot network init (S40network: ifup/udhcpc) fails noisily at every boot
# ("ip: socket: Protocol/Address family not supported"). Nothing here needs IP —
# X and D-Bus use unix sockets — so drop the failing init.
rm -f "$TARGET/etc/init.d/S40network"

# BLUETOOTH — REMOVED (2026-08, see docs/SECURITY-MODEL.md).
#
# The kernel no longer builds CONFIG_BT (tools/desktop.config, tools/phase1.config),
# bluez5_utils is deselected, and nothing on the machine speaks Bluetooth. But
# Buildroot does not delete files a now-deselected package left on an earlier
# build, so a stale bluetoothd would keep shipping and fail noisily every boot.
# Purge the whole stack — the daemon, its tools, its library, its D-Bus policy,
# its init scripts, and the BT-audio / classic-HID glue — so an incremental
# build over an old tree cannot resurrect any of it. (The controller firmware
# blobs are left in place: with no btusb driver nothing requests them, so they
# are inert; trimming them belongs to the linux-firmware selection, not here.)
for _f in \
    "$TARGET"/usr/bin/bluetoothctl "$TARGET"/usr/bin/btmon \
    "$TARGET"/usr/bin/btmgmt "$TARGET"/usr/bin/btvirt \
    "$TARGET"/usr/bin/hciconfig "$TARGET"/usr/bin/hcitool \
    "$TARGET"/usr/libexec/bluetooth/bluetoothd \
    "$TARGET"/etc/init.d/S40bluetoothd "$TARGET"/etc/init.d/S39btfirmware \
    "$TARGET"/etc/dbus-1/system.d/bluetooth.conf ; do
    rm -f "$_f"
done
rm -rf "$TARGET"/usr/libexec/bluetooth "$TARGET"/usr/lib/bluetooth 2>/dev/null || true
rm -f  "$TARGET"/usr/lib/libbluetooth.so* 2>/dev/null || true
rm -f  "$TARGET"/usr/lib/alsa-lib/libasound_module_*bluealsa* 2>/dev/null || true
rm -f  "$TARGET"/lib/udev/rules.d/97-hid2hci.rules 2>/dev/null || true

# PRINTING. Three fixes to the stock Buildroot CUPS install:
#
# 1. Buildroot's S81cupsd starts cupsd with `-s /etc/cups/cups-files` — the
#    `.conf` suffix is missing, so cupsd never reads cups-files.conf and
#    silently runs on compiled-in defaults instead. Every edit to that file
#    (FileDevice, User/Group, SystemGroup) was being ignored.
# 2. cups-browsed only discovers *network* printers over DNS-SD. This kernel has
#    no networking at all, so it is pure dead weight: a daemon started at every
#    boot that can never find anything. Drop it (boot time + idle RAM).
# 3. Upstream cupsd.conf ships `IdleExitTimeout`, which is only recognised when
#    CUPS is built with on-demand (systemd/launchd) support. Buildroot is not,
#    so cupsd logs a config error on every start. Strip the directive.
if [ -f "$TARGET/etc/init.d/S81cupsd" ]; then
    sed -i 's|-s /etc/cups/cups-files$|-s /etc/cups/cups-files.conf|' \
        "$TARGET/etc/init.d/S81cupsd"
fi
rm -f "$TARGET/etc/init.d/S82cups-browsed"
sed -i '/^IdleExitTimeout/d' "$TARGET/etc/cups/cupsd.conf" 2>/dev/null || true

# PERSISTENT /var/cache AND /var/log. Buildroot's skeleton symlinks var/cache,
# var/log and var/spool to ../tmp, which is a tmpfs — so anything "cached" is
# thrown away at every shutdown. That is meant for read-only-rootfs appliances;
# this rootfs is read-write (and the installer lays it down on a real disk), and
# the symlinks cost us badly:
#
#   * fontconfig's cache AND S35fontcache's "already seeded" stamp both live in
#     /var/cache/fontconfig, so the ~500-font scan re-runs on EVERY boot and the
#     seeder never learns it already ran — on a software-rendered machine that
#     competes with desktop startup every single time.
#   * CUPS keeps its ErrorLog in /var/log and its spool in /var/spool, so a
#     failed print job destroys its own evidence at reboot — exactly the
#     information needed to debug a printer that will not print.
#
# Replace the two that need to persist with real directories. /var/spool stays
# on tmpfs deliberately: a half-printed job should not survive a reboot.
for d in cache log; do
    if [ -L "$TARGET/var/$d" ]; then
        rm -f "$TARGET/var/$d"
    fi
    mkdir -p "$TARGET/var/$d"
done
mkdir -p "$TARGET/var/cache/fontconfig" "$TARGET/var/log/cups"

# make our fonts discoverable
mkdir -p "$TARGET/etc/fonts/conf.d"

# KEEP THE X11 CORE BITMAP FONTS OUT OF THE FONTCONFIG SCAN, for real.
#
# /usr/share/fonts/X11/misc holds 337 PCF bitmap fonts that exist only so the X
# server can open its core fonts ("fixed", "cursor"). Nothing in this desktop
# renders through them — GTK/Pango resolve everything via fontconfig from the
# DejaVu / Liberation / Notebook OS TrueType faces.
#
# etc/fonts/conf.d/09-notebookos-skip-bitmaps.conf tries to exclude them with
# <rejectfont>, but that only filters at MATCH time: fontconfig still opens and
# parses all 337 files when it builds the cache. Measured in the guest, cold
# cache, page cache dropped, same method both ways:
#
#   fc-cache -f with X11 under /usr/share/fonts    10.51s
#   fc-cache -f with X11 moved out                  6.85s
#   fc-list families, both ways                     21   (identical)
#
# On the live ISO that cache lives on the tmpfs overlay, so this scan runs on
# EVERY boot. S35fontcache does it in the background at nice 19, so it is not
# stealing much CPU from startup — the cost that matters is that the cache is
# not COMPLETE until it finishes, and a GTK app that starts first and finds a
# directory uncached scans that directory itself, at normal priority, on the
# critical path. A third less to scan is a third less exposure to that.
#
# The only way to keep fontconfig out of a directory is for the directory not to
# be under one it scans, so move the tree to /usr/share/X11/fonts and point the
# X server at it explicitly (Files section below). The files are NOT deleted:
# without them X dies with "could not open default font 'fixed'". Verified in
# the guest after the move: X starts clean, 0 "could not open default font"
# errors, FontPath resolves to /usr/share/X11/fonts/misc.
if [ -d "$TARGET/usr/share/fonts/X11" ]; then
    mkdir -p "$TARGET/usr/share/X11"
    rm -rf "$TARGET/usr/share/X11/fonts"
    mv "$TARGET/usr/share/fonts/X11" "$TARGET/usr/share/X11/fonts"
fi

# a permissive Xorg config: prefer modesetting (virtio-gpu KMS), fbdev fallback.
# Option "PageFlip" "false" on the modesetting driver is important on the
# SOFTWARE virtio-gpu path: with page-flipping the server waits on a vblank that
# never arrives under software rendering, so freshly-drawn windows never reach
# the scanout (blank desktop). Disabling it makes the driver blit + issue a
# dirty-fb on damage instead, which flushes promptly. Harmless on virgl / real
# hardware (they just don't page-flip).
mkdir -p "$TARGET/etc/X11/xorg.conf.d"
cat > "$TARGET/etc/X11/xorg.conf.d/10-notebookos.conf" <<'EOF'
# The X core font path. The bitmap fonts live outside /usr/share/fonts so that
# fontconfig never scans them (see the move in post-build.sh); the server still
# needs them for "fixed" and "cursor", so it is told where they went.
Section "Files"
    FontPath "/usr/share/X11/fonts/misc"
    FontPath "/usr/share/X11/fonts/75dpi"
    FontPath "/usr/share/X11/fonts/100dpi"
    FontPath "/usr/share/X11/fonts/cyrillic"
EndSection

Section "ServerFlags"
    Option "AutoAddGPU" "true"
    Option "DontZap" "false"
EndSection

Section "Device"
    Identifier "notebookos-gpu"
    Driver "modesetting"
    Option "PageFlip" "false"
EndSection
EOF


# ---------------------------------------------------------------------------
# gdk-pixbuf loader cache.
#
# THE BUG: the cache shipped in the image disagreed with what is on disk in
# BOTH directions. It registered libpixbufloader-svg.so, which is NOT
# installed, so every pixbuf SVG load failed with "cannot open shared object
# file"; and it omitted libpixbufloader-tiff.so, which IS installed, so TIFFs
# were pushed down a slow external-converter fallback for no reason. Nothing
# regenerated it, so it drifted further every time an image-format package was
# switched on or off.
#
# The HOST's gdk-pixbuf-query-loaders CANNOT do this job: it loads the target's
# shared objects against the host's libgdk_pixbuf and dies on a symbol mismatch
# ("undefined symbol: g_once_init_leave_pointer"), then EXITS 0 having written
# an empty cache -- a build-time seed that looks like it worked and is worse
# than doing nothing. So run the TARGET's own binary through the target's
# dynamic loader, which answers for the target's real modules, then strip the
# build-machine prefix so the paths are right on the guest.
#
# S34pixbufloaders repeats this on the machine at boot, so an image built where
# this step could not run still heals itself.
PIXL=$(echo "$TARGET"/usr/lib/gdk-pixbuf-2.0/*/loaders 2>/dev/null | head -1)
PIXLD="$TARGET/lib64/ld-linux-x86-64.so.2"
[ -x "$PIXLD" ] || PIXLD="$TARGET/lib/ld-linux-x86-64.so.2"
if [ -d "$PIXL" ] && [ -x "$PIXLD" ] && \
   [ -x "$TARGET/usr/bin/gdk-pixbuf-query-loaders" ]; then
    if GDK_PIXBUF_MODULEDIR="$PIXL" "$PIXLD" \
         --library-path "$TARGET/usr/lib:$TARGET/lib" \
         "$TARGET/usr/bin/gdk-pixbuf-query-loaders" \
         > "$PIXL/../loaders.cache.new" 2>/dev/null && \
       grep -q libpixbufloader "$PIXL/../loaders.cache.new"
    then
        sed -i "s,$TARGET,,g" "$PIXL/../loaders.cache.new"
        mv "$PIXL/../loaders.cache.new" "$PIXL/../loaders.cache"
        echo "post-build: gdk-pixbuf loaders.cache rebuilt from the target's own modules"
    else
        # Never replace a working cache with an empty one.
        rm -f "$PIXL/../loaders.cache.new"
        echo "post-build: NOTE loaders.cache left as-is; S34pixbufloaders will redo it at boot" >&2
    fi
fi

exit 0
# ---------------------------------------------------------------- lock root
# Root ships with an EMPTY password ("root::"), which means anything that can
# reach a login prompt, a getty or su becomes root by pressing Enter. Lock the
# account: "!" is not a valid hash, so no typed password can ever match it.
#
# This does NOT stop the machine working. The desktop is started by
# S99notebookos via xinit as root at boot; locking the PASSWORD prevents
# authenticating AS root, it does not stop init running things as root. And
# de/login.py treats a locked account as "nothing to ask for", so a live image
# with no user account still boots straight to the desktop instead of stranding
# somebody at a prompt no password can satisfy.
# NOTE: doing this here does NOT work. Buildroot regenerates /etc/shadow from
# its users table in "Generating root filesystems common tables", which runs
# AFTER this script -- so an edit here is silently overwritten and root ships
# with an empty password anyway. The lock is set by
#     # BR2_TARGET_ENABLE_ROOT_LOGIN is not set
# in buildroot/.config, which makes mkusers write a LOCKED root entry.
# BR2_TARGET_GENERIC_ROOT_PASSWD is NOT the way: anything that does not start
# with $1$/$5$/$6$ is taken as CLEAR TEXT and hashed, so setting it to "!" gives
# root the literal password "!" -- worse than leaving it empty. Verify in
# output/target/etc/shadow after a build, not here.
if [ -f "$TARGET/etc/shadow" ]; then
    grep -qE '^root:!' "$TARGET/etc/shadow" || \
        echo "post-build: WARNING root is not locked in /etc/shadow" >&2
fi
