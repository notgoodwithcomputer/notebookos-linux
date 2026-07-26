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

_accel = os.environ.get("NB_ACCEL")
if _accel == "1" or (_accel is None and os.path.exists("/dev/dri/renderD128")):
    raise SystemExit(0)

d = Gdk.Display.get_default()
if d is None:
    raise SystemExit(0)

while True:
    try:
        d.flush()
        d.sync()
    except Exception:
        pass
    time.sleep(0.5)
