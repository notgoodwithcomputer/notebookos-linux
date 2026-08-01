#!/usr/bin/env python3
"""Every app gets its OWN icon, and no app wears a file's icon.

This exists because four apps once shared somebody else's glyph and nobody
noticed until the whole set was rendered side by side:

  * Settings and System Monitor were the SAME gear — two pixel-identical rows
    in the Applications folder;
  * Terminal aliased to "toc", which is ALSO finder.icon_for()'s fallback for
    any file the OS cannot open, so Terminal.app looked like junk on the disk;
  * the installer wore the Devices rail's "disk";
  * the GBA SDK wore the .gba ROM's "cartridge".

Each is a one-word edit to finder.ICON_ALIAS away from coming back, so it is
checked here instead of by eye:

  1. no two entries in finder.APP_MODULES resolve to the same glyph;
  2. no app resolves to the unrecognised-file fallback glyph;
  3. no app resolves to a glyph a FILE TYPE uses (a .gba ROM, a photo, a PDF);
  4. every app's glyph actually exists in nbicons.ICONS -- a name that does not
     draws a featureless square, which is how the GBA Emulator used to look;
  5. every glyph renders to a pixbuf that is not blank.

Run:  DISPLAY=:0 python3 tools/icon_uniqueness_selftest.py
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"))

# finder reads NB_HOME at import time; keep it off the developer's real home.
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="nbicons-"))

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")

import nbicons  # noqa: E402
import finder  # noqa: E402

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (("  " + detail) if detail and not cond else ""))
    if not cond:
        ok = False


# Resolve exactly the way the Applications folder does: through icon_for() on
# the on-disk "<Name>.app", so alias table and fallbacks are both exercised.
app_glyph = {}
for disp in finder.APP_MODULES:
    app_glyph[disp] = finder.icon_for(disp + ".app")

# --- 1. one glyph per app -------------------------------------------------
by_glyph = {}
for disp, g in app_glyph.items():
    by_glyph.setdefault(g, []).append(disp)
shared = {g: a for g, a in by_glyph.items() if len(a) > 1}
check("no two apps share a glyph", not shared,
      "; ".join("%s <- %s" % (g, ", ".join(a)) for g, a in sorted(shared.items())))

# --- 2. no app wears the unrecognised-file fallback ------------------------
# icon_for() returns this for a file whose type the OS does not know.
fallback = finder.icon_for("some-file-with-no-known-extension.qqq")
wearing = sorted(d for d, g in app_glyph.items() if g == fallback)
check("no app uses the unrecognised-file glyph (%s)" % fallback, not wearing,
      ", ".join(wearing))

# --- 3. no app wears a file type's glyph -----------------------------------
FILE_SAMPLES = ("game.gba", "rom.gb", "photo.png", "clip.mp4", "song.mp3",
                "book.epub", "paper.pdf", "notes.txt", "readme.qqq")
file_glyphs = {finder.icon_for(f) for f in FILE_SAMPLES}
# Documents and their editor may legitimately meet in the middle: the Writer
# owns the text-document glyph and the Media Viewer the photo one, on purpose.
ALLOWED = {"Writer": "writer", "Media Viewer": "media", "Music": "music",
           "E-book Reader": "ebook", "Video Editor": "video"}
clash = sorted(d for d, g in app_glyph.items()
               if g in file_glyphs and ALLOWED.get(d) != g)
check("no app wears a file type's glyph", not clash, ", ".join(clash))

# --- 3b. the file-type fallbacks still work --------------------------------
# Changing the alias table must never disturb what a FILE gets.
for fname, want in (("Zelda.gba", "cartridge"), ("pokemon.GB", "cartridge"),
                    ("holiday.jpg", "media"), ("clip.mkv", "video"),
                    ("song.flac", "music"), ("thesis.pdf", "ebook"),
                    ("notes.md", "writer"), ("core.dump", "toc"),
                    ("no-extension-at-all", "toc")):
    check("file glyph %s -> %s" % (fname, want),
          finder.icon_for(fname) == want, finder.icon_for(fname))

# --- 4. every app glyph is a real glyph ------------------------------------
missing = sorted(d for d, g in app_glyph.items() if g not in nbicons.ICONS)
check("every app glyph exists in nbicons.ICONS", not missing, ", ".join(missing))

# The sidebar/devices marks and the file-type marks must exist too.
for extra in ("disk", "folder", "home", "trash", "cartridge", "toc"):
    check("glyph present: " + extra, extra in nbicons.ICONS)

# --- 5. every app glyph actually draws something ---------------------------
def ink(name, size):
    pb = nbicons.pixbuf(name, size)
    data = pb.get_pixels()
    stride, nch = pb.get_rowstride(), pb.get_n_channels()
    n = 0
    for y in range(pb.get_height()):
        row = y * stride
        for x in range(pb.get_width()):
            if data[row + x * nch + 3]:
                n += 1
    return n


blank = []
for disp, g in sorted(app_glyph.items()):
    for size in (16, 22, 48):
        if ink(g, size) < size // 2:
            blank.append("%s@%d" % (g, size))
check("every app glyph renders ink at 16/22/48", not blank, ", ".join(blank))

# --- 6. the app set is fully described -------------------------------------
no_kind = sorted(d for d in finder.APP_MODULES if d not in finder.APP_KIND)
check("every app has a Kind", not no_kind, ", ".join(no_kind))
no_module = sorted(
    d for d, m in finder.APP_MODULES.items()
    if not os.path.exists(os.path.join(finder.DE_DIR, m + ".py")))
check("every app's module exists", not no_module, ", ".join(no_module))

print("%d apps, %d distinct glyphs" % (len(app_glyph), len(set(app_glyph.values()))))
print("OK" if ok else "FAILURES")
sys.exit(0 if ok else 1)
