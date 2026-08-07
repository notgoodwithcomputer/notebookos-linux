#!/bin/sh
#
# accel.sh — decide whether this machine can actually render in HARDWARE.
# Prints "1" (accelerated) or "0" (software). Nothing else goes to stdout.
#
# WHAT NB_ACCEL CONTROLS, and why getting it wrong is expensive:
#   * the compositor runs only when it is 1, and runs with --vsync;
#   * the scanout-flush daemon (de/xflushd.py), which is what makes freshly
#     mapped windows paint at all on a software stack, runs only when it is 0;
#   * finder.py enables interactive window move/resize on it.
# So a machine that claims acceleration it does not have gets the SLOW path's
# helper switched off, a compositor added on top, and that compositor told to
# wait for a vertical blank — the exact combination that produces a desktop that
# stutters and windows that paint late. It is worse than either honest answer.
#
# THE BUG THIS EXISTS FOR. session.sh used to decide from the KERNEL DRIVER NAME
# alone: i915/xe/amdgpu/radeon/nouveau => accelerated. But the kernel binding a
# KMS driver says only that the SCREEN works; whether GL is accelerated depends
# on whether MESA was built with a driver for that GPU. This image ships
# iris (Intel Broadwell+), virtio_gpu and swrast — so on an AMD, NVIDIA or
# pre-2014 Intel laptop the old test returned "accelerated" while Mesa quietly
# fell back to the software rasterizer. Every one of those machines ran the
# desktop in its worst configuration, and they are ordinary second-hand laptops,
# which is exactly the hardware this OS is for.
#
# HOW IT DECIDES NOW: the kernel says which GPU is present, and the presence of
# the matching Mesa DRI driver says whether Mesa can drive it. Both must agree.
#
# HONEST LIMITATION: a driver being installed proves Mesa supports that GPU
# FAMILY, not this exact chip (iris covers Broadwell and later; a Sandy Bridge
# machine also binds i915 and needs crocus). Checking every acceptable driver
# for a family, as below, keeps that error small — and it is a far smaller error
# than the one it replaces, which was to not look at Mesa at all. A truly
# authoritative answer needs a GL context (glxinfo), which this image does not
# ship and which would cost a process spawn plus a context creation on the boot
# path.
#
# Testable: NB_SYS_DRM and NB_DRI_DIR can be pointed at fixtures.
# See tools/accel_selftest.py.

NB_SYS_DRM=${NB_SYS_DRM:-/sys/class/drm}
NB_DRI_DIR=${NB_DRI_DIR:-/usr/lib/dri}
NB_CMDLINE=${NB_CMDLINE:-/proc/cmdline}

# Is at least one of the named Mesa DRI drivers installed?
_have_dri() {
	for _d in "$@"; do
		[ -e "$NB_DRI_DIR/${_d}_dri.so" ] && return 0
	done
	return 1
}

NB_ACCEL=0
for _card in "$NB_SYS_DRM"/card[0-9]*; do
	[ -e "$_card/device/driver" ] || continue
	_drv=$(basename "$(readlink -f "$_card/device/driver" 2>/dev/null)" 2>/dev/null)
	case "$_drv" in
		i915)
			# Two generations, two Mesa drivers: crocus for gen4-7, iris for
			# gen8+. i915_dri is the ancient gen2-3 classic driver.
			_have_dri crocus iris i915 && NB_ACCEL=1 ;;
		xe)
			_have_dri iris && NB_ACCEL=1 ;;
		amdgpu)
			_have_dri radeonsi && NB_ACCEL=1 ;;
		radeon)
			_have_dri r600 r300 radeonsi && NB_ACCEL=1 ;;
		nouveau)
			_have_dri nouveau && NB_ACCEL=1 ;;
		virtio_gpu|virtio-pci)
			# virtio-gpu is only accelerated when the HOST offers GL; the guest
			# driver announces that and nothing else does, so the dmesg line
			# stays part of the test rather than being replaced by it.
			if _have_dri virtio_gpu; then
				dmesg 2>/dev/null | grep -qE "\[drm\] features:.*\+virgl" && NB_ACCEL=1
			fi ;;
		# simple-framebuffer (simpledrm) and anything unrecognised stay at 0.
	esac
done
unset _card _drv _d

# Explicit override for testing either path: nb.accel=1 / nb.accel=0.
if [ -r "$NB_CMDLINE" ]; then
	grep -qw nb.accel=1 "$NB_CMDLINE" 2>/dev/null && NB_ACCEL=1
	grep -qw nb.accel=0 "$NB_CMDLINE" 2>/dev/null && NB_ACCEL=0
fi

printf '%s\n' "$NB_ACCEL"
