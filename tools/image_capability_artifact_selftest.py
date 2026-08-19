#!/usr/bin/env python3
"""Mutation: capability-shaped empty files are not shipped capabilities."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import image_capability_check as gate  # noqa: E402


with tempfile.TemporaryDirectory(prefix="image-cap-empty-") as tmp:
    target = Path(tmp, "target")
    gst = target / "usr/lib/gstreamer-1.0"
    fonts = target / "usr/share/fonts/demo"
    sbin = target / "sbin"
    for directory in (gst, fonts, sbin):
        directory.mkdir(parents=True, exist_ok=True)
    for name in ("libgstplayback.so", "libgstgtk.so", "libgstlibav.so",
                 "libgstisomp4.so", "libgstalsa.so",
                 "libgstvideoconvertscale.so"):
        (gst / name).write_bytes(b"")
    (fonts / "fake.ttf").write_bytes(b"")
    (sbin / "hwclock").write_bytes(b"")
    (sbin / "hwclock").chmod(0o755)
    config = Path(tmp, "config")
    config.write_text("\n".join((
        "CONFIG_VFAT_FS=y", "CONFIG_EXFAT_FS=y", "CONFIG_NTFS3_FS=y",
        "CONFIG_RTC_DRV_CMOS=y")) + "\n", encoding="utf-8")

    old_target, old_config = gate.TARGET, gate.KCONFIG
    old_links = gate.links_against
    gate.TARGET, gate.KCONFIG = str(target), str(config)
    gate.links_against = lambda *_args: True
    gate.FAILED.clear()
    gate.N[0] = 0
    try:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = gate.main()
    finally:
        gate.TARGET, gate.KCONFIG = old_target, old_config
        gate.links_against = old_links

assert rc != 0, output.getvalue()
text = output.getvalue()
assert "FAIL hwclock" in text and "FAIL gstreamer" in text
assert "FAIL fonts" in text
print("PASS zero-byte executables, plugins, and fonts fail capability checks")
print("RESULT: PASS")
