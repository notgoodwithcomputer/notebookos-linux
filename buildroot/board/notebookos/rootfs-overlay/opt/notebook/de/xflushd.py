#!/usr/bin/env python3
"""xflushd.py — best-effort scanout flush for the SOFTWARE virtio-gpu path.

Under software rendering the virtio-gpu pushes framebuffer regions to the host
scanout lazily. The primary mitigation is the Xorg modesetting `PageFlip`
`false` option (see board/notebookos/post-build.sh), which makes the server
blit + dirty-fb on damage instead of waiting on a (never-arriving software)
page-flip. This daemon is a lightweight backstop: it flushes the Gdk display
each tick so pending requests are pushed promptly. It does NOT warp the pointer
(a same-spot warp is a coalesced no-op, and sweeping the cursor to force flushes
is too janky to ship).

Only runs on software rendering; session.sh gates it on NB_ACCEL (from the
kernel's "[drm] features: +/-virgl" line) and we double-check. A DRM render node
exists even under software rendering, so it is only a last-resort fallback
signal when NB_ACCEL is unset.
"""
import os
import time
import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402

def flush_loop(display, pause=time.sleep):
    """Flush until the X connection is lost; False means it went away."""
    while True:
        try:
            display.flush()
            display.sync()
        except Exception:
            # The daemon belongs to this X session. Retrying a dead connection
            # forever leaves a wakeup loop behind each desktop restart.
            return False
        pause(0.5)


def main():
    accel = os.environ.get("NB_ACCEL")
    # Normally an accelerated session must not pay for a blocking sync twice
    # per second. The exception is session.sh's compositor-failure fallback.
    if accel == "1" and os.environ.get("NB_XFLUSHD_FORCE") != "1":
        return 0
    display = Gdk.Display.get_default()
    if display is None:
        return 0
    flush_loop(display)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
