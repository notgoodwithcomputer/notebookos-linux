#!/usr/bin/env python3
"""
Finder — the Notebook OS file manager. A native GTK window (Cinnamon/Nemo
lineage) that browses the real filesystem under Home, styled to the papertone
design language: custom Mac-OS-7 title bar, Devices/Places sidebar, list view
with Name / Size / Date Modified, and a status bar with the live free-space
figure read from statvfs. No web view — every pixel is drawn by GTK.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Gio, GObject, Pango  # noqa: E402

import ctypes
import errno
import os
import re
import stat
import time
import shutil
import subprocess
import threading
import tempfile

import nbicons
import nbapp  # for nudge_paint (swrast first-paint flush)
import nbstate  # navigation generations + stable-identity restoration
import nbmotion       # the frame-clock driver + policy for the navigation slide
import nbtransitions  # direction vocabulary shared with the rest of the OS
try:
    import nbmotion  # launch-card motion; None means instant (headless/stripped)
except Exception:  # noqa: BLE001
    nbmotion = None
from nbi18n import _t  # noqa: E402

# Whether the GPU stack is accelerated (a compositor is running). session.sh
# exports NB_ACCEL from the kernel's "[drm] features: +/-virgl" line and only
# starts xcompmgr when accelerated. Window move/resize are offered ONLY then: the
# compositor backs the window in a pixmap so dragging is smooth, whereas on the
# software stack (real hardware on simpledrm) there is no compositor and a live
# drag would repaint everything underneath on every motion event and jank the
# whole machine — so the Finder stays fixed there (double-click still zooms).
_ACCEL = os.environ.get("NB_ACCEL") == "1"

DE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- renameat2(RENAME_NOREPLACE): move onto a free name, or not at all ----
#
# Every move the Finder makes must refuse an occupied destination rather than
# consume what stands there. Looking at the name and then renaming is two
# moments, and the folder can gain that name in between — a download landing, a
# second Finder window, an editor writing its file out — so the look answers
# for a folder that no longer exists by the time the rename runs. The kernel
# has the one-step version: renameat2 with RENAME_NOREPLACE either moves the
# entry or fails with EEXIST, and nothing can slip between the two halves
# because there are no halves. It works for files, directories and symlinks
# alike (the link itself is moved, never followed).
#
# Python has no os.renameat2, so it is called through libc. glibc has exported
# the symbol since 2.28 and the shipped image is x86_64 glibc (buildroot/.config),
# so this is the normal path, not a best-effort one.
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1 << 0
_libc = None


def _quarantine_store(path):
    """Move an unreadable app store aside immediately before its replacement."""
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = "%s.damaged-%s" % (path, stamp)
        n = 2
        while os.path.lexists(dest):
            dest = "%s.damaged-%s-%d" % (path, stamp, n)
            n += 1
        os.replace(path, dest)
    except OSError:
        pass


def _libc_renameat2():
    """The libc renameat2 entry point, resolved once and kept.

    Raises OSError(ENOTSUP) if this libc does not export it. That is a refusal,
    not a signal to try something else: the only alternatives are a
    look-then-rename or shutil.move, and both answer an occupied name by
    destroying what is there — the exact failure this whole path exists to
    prevent. A move that cannot be made atomically is not made at all.
    """
    global _libc
    if _libc is None:
        try:
            _libc = ctypes.CDLL(None, use_errno=True)
        except OSError:
            _libc = False
    try:
        fn = _libc.renameat2
    except AttributeError:
        fn = None
    if fn is None:
        raise OSError(errno.ENOTSUP,
                      "renameat2 is not available in this C library")
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p,
                   ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    return fn


def _renameat2_noreplace(src, dst):
    """renameat2(AT_FDCWD, src, AT_FDCWD, dst, RENAME_NOREPLACE).

    Both names are passed as raw bytes (os.fsencode) because that is what the
    kernel takes; the errno is read back through ctypes' own copy, which is why
    the library is opened with use_errno=True — a bare ctypes call runs Python
    code before errno could be read and would report someone else's failure.

    The errno is raised as-is, so callers keep the distinctions they act on:
    EEXIST arrives as FileExistsError (the name is taken), EXDEV as a plain
    OSError the Paste path turns into a copy-then-delete across disks.
    """
    fn = _libc_renameat2()
    ctypes.set_errno(0)
    rc = fn(_AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst),
            _RENAME_NOREPLACE)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), src, None, dst)

# Apps that are NOT READY to be seen by a user, and are therefore kept out of
# the Applications folder in the shipped image.
#
# This is a SHIP-TIME decision and is deliberately separate from the user's own
# "Remove from Applications" list: this one is ours, is not restorable from the
# UI, and is checked into the tree so the reason travels with the code. An app
# listed here still has its module on disk and still has its entry in
# APP_MODULES, so a document that opens with it still opens with it — it simply
# is not offered as something to launch.
#
# The bar for being here: the app promises a capability it cannot deliver, and
# the honest fix is longer than the time available. Shipping a control that
# does nothing is worse than shipping no control, so hide it, write down why,
# and take it off this list when it is real.
#
# Format: "Display Name": "why, in one sentence, and what would remove it"
HIDDEN_APPS = {
}

# display name (without .app) -> python module in the DE directory
APP_MODULES = {
    "Writer": "writer", "Novel": "novel", "Academics": "academics",
    "Journal": "journal", "Screenplay": "screenplay", "Tasks": "tasks",
    "Calendar": "calendar", "Cookbook": "cookbook",
    "Meal Planner": "mealplanner", "E-book Reader": "ebook",
    "Calculator": "calculator", "Accounting": "accounting",
    "Bill Tracker": "bills", "Contacts": "contacts",
    "Govorimo": "govorimo",
    "Illustrator": "illustrator", "Comics": "comics", "Animation": "animation", "Sequencer": "sequencer",
    "Composer": "composer",
    "Video Editor": "video", "Media Viewer": "media", "Music": "music",
    "Packages": "packages", "2048": "g2048",
    "GBA Emulator": "gbaemu", "GBA SDK": "gbasdk", "Language": "language",
    "Maps": "maps", "Workout": "workout",
    "Terminal": "terminal", "Settings": "settings",
    "System Monitor": "sysmon", "Install Notebook OS": "installer",
    "USB Writer": "usbwriter", "Disc Burner": "burner",
}


def _is_virtual_app(rel, name):
    """Whether `name` is one of the synthetic rows in Applications.

    A suffix alone is not enough: a user can keep an ordinary file named
    ``Calculator.app`` anywhere else, and Get Info must stat that real file.
    """
    return (rel == "Applications" and name.endswith(".app")
            and name[:-4] in APP_MODULES)

# a few app modules have no same-named glyph in nbicons — alias to a fitting one
# (an unaliased name with no glyph falls back to a featureless square, which is
# what the GBA Emulator was showing in the Applications folder).
#
# An alias must never point at a glyph another app or a FILE TYPE already
# carries: sysmon aliased to the same gear as settings (two pixel-identical
# rows), terminal to "toc" — which is also icon_for()'s fallback for a file the
# OS cannot open — installer to the Devices "disk" and gbasdk to the .gba ROM
# "cartridge". Those four now have their own glyphs in nbicons under their own
# module names, so they need no alias at all; tools/icon_uniqueness_selftest.py
# fails the build if a duplicate ever comes back.
#
# The map itself now lives in nbicons (nbicons.ALIAS / nbicons.glyph_for), so
# that Packages — which also turns a module name into a glyph — cannot fall out
# of step with this list, as it had.
ICON_ALIAS = nbicons.ALIAS

# file extension -> DE module that opens it, so double-clicking a document
# launches its owning app with the file as argv[1] (media/writer/ebook accept a
# path as sys.argv[1]). Mirrors the .app launch, just with the document passed in.
FILE_APPS = {
    ".png": "media", ".jpg": "media", ".jpeg": "media", ".gif": "media",
    ".bmp": "media", ".webp": "media", ".tiff": "media", ".tif": "media",
    ".ico": "media", ".svg": "media", ".heic": "media", ".heif": "media",
    ".avif": "media",
    # video opens in the Media viewer by default (it plays via GStreamer)
    ".mp4": "media", ".m4v": "media", ".mkv": "media", ".mov": "media",
    ".webm": "media", ".avi": "media",
    ".txt": "writer", ".md": "writer", ".writer": "writer",
    ".comic": "comics", ".anim": "animation",
    ".epub": "ebook", ".pdf": "ebook",
    # audio files carry a music icon and 'Audio' Kind, so double-click must
    # honour that affordance: hand the path to the Music app (it accepts/scans
    # the file) rather than flashing "No app for this file type".
    ".mp3": "music", ".wav": "music", ".ogg": "music",
    ".flac": "music", ".m4a": "music",
    # Game Boy / GBA ROMs open in the GBA Emulator (it plays via the vbam core)
    ".gba": "gbaemu", ".gbc": "gbaemu", ".gb": "gbaemu", ".sgb": "gbaemu",
}

# Modules that actually accept a document path as sys.argv[1] and act on it.
# Only these may be honoured from the user's Settings ▸ Default Applications
# choice (settings.json 'default_apps': {ext: module}), so a stale or
# hand-edited entry can never route a file to an app that would silently
# ignore it — novel/academic, for instance, open only their own JSON formats.
FILE_OPENERS = {"writer", "ebook", "media", "music", "screenplay", "gbaemu",
                "comics", "animation"}

def _hidden_modules():
    """Modules withheld from every launch surface while their app is hidden.
    Derived, not listed twice: HIDDEN_APPS names apps, launch routes speak in
    modules, and this is the one translation between them."""
    return {APP_MODULES[n] for n in HIDDEN_APPS if n in APP_MODULES}


# human "Kind" descriptor per app (matches the design's KIND column)
APP_KIND = {
    "Writer": "Word Processor", "Novel": "Word Processor",
    "Academics": "School", "Journal": "Diary",
    "Screenplay": "Scriptwriting", "Tasks": "Productivity",
    "Calendar": "Productivity", "Cookbook": "Reference",
    # Cookbook is where you look a recipe up ("Reference"); the Meal Planner is
    # what you do with it — a week of meals — so it gets its own plain-language
    # kind rather than sharing one.
    "Meal Planner": "Cooking",
    "E-book Reader": "Reader", "Calculator": "Utility",
    # Two Finance apps, and the split is deliberate: Accounting is the cash book
    # of what HAS happened, the Bill Tracker is what has to happen next and how
    # to do it. Same Kind because that is where a person looks for either.
    "Accounting": "Finance", "Bill Tracker": "Finance",
    "Contacts": "Utility",
    # Chat over LoRa radio; "Messaging" is where a person looks for talk.
    "Govorimo": "Messaging",
    "Illustrator": "Graphics", "Comics": "Cartooning", "Animation": "Cartooning",
    "Sequencer": "Audio", "Composer": "Audio", "Video Editor": "Video",
    "Media Viewer": "Media", "Music": "Music", "Packages": "System",
    "2048": "Game", "GBA Emulator": "Game", "GBA SDK": "Development",
    "Language": "Education", "Maps": "Reference",
    "Workout": "Health",
    "Terminal": "Utility",
    "Settings": "System", "System Monitor": "System",
    "Install Notebook OS": "System", "USB Writer": "System",
    # "Media" rather than "System": USB Writer makes install media, which is a
    # system errand, but a person opens this one to make a music CD or a film
    # DVD. The Kind says what it is for, not which shelf the code sits on.
    "Disc Burner": "Media",
}

HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
# The Places sidebar is the standard set of Linux user folders (Nautilus
# lineage). Applications is not a user folder — it lives under Devices so the
# app launcher stays reachable (see _devices).
PLACES = [("Home", "home", ""), ("Desktop", "desktop", "Desktop"),
          ("Documents", "folder", "Documents"),
          ("Music", "music", "Music"),
          ("Pictures", "media", "Pictures"),
          ("Videos", "video", "Videos"),
          ("Trash", "trash", ".Trash")]
# Places whose folders we create on launch so no sidebar row is ever dead
# (Applications is provisioned by the OS image; .Trash is made on demand).
PERSONA_DIRS = ("Desktop", "Documents", "Music", "Pictures", "Videos")

# Icon pixel sizes. The list view keeps the design's compact 22px row glyph;
# the grid/icon view uses a much larger glyph so the app/file icons read as
# real icons, not dots. The mockup drew 48px cells; the reporter found those too
# small (then 64px still small — "icons should be larger in icon view"), so the
# icon view now draws big 96px glyphs. nbicons rasterizes each icon NATIVELY at
# the requested pixel size (never an upscale of a small pixbuf), so both stay
# crisp on the GPU-less framebuffer; the result is memoized per (name, size), so
# carrying two sizes costs one extra render per distinct glyph, once, at
# folder-load time — never per draw/expose.
LIST_ICON_PX = 22
GRID_ICON_PX = 96
# Store columns the list may be sorted by: Name, Size (hidden byte count),
# Date Modified (hidden mtime), Kind. Anything else is not a sortable column.
SORT_COLUMNS = (1, 6, 7, 8)
# Grid cell geometry. The label wraps to the cell's inner width, so the three
# numbers have to stay in step — a wrap width wider than the cell would let one
# long name stretch every cell and, with it, the whole window.
GRID_CELL_PX = 156
GRID_CELL_PAD = 12
GRID_LABEL_PX = GRID_CELL_PX - 2 * GRID_CELL_PAD

# Search. Typing filters the folder you are standing in instantly (from the
# cached listing), and a beat later the SAME query is run over the whole of
# Home on a worker thread, so "calc" finds Calculator and "tax" finds the
# return wherever you happen to be. Before this, search only ever looked in one
# folder, which is no help at all to the person who knows the name but not the
# place. Bounded so a deep tree can never make the desktop think.
SEARCH_DEBOUNCE_MS = 350      # wait for a typist to pause before walking disk
SEARCH_MIN_CHARS = 2          # one letter matches nearly everything
SEARCH_MAX_HITS = 200         # a wall of results is not an answer
SEARCH_MAX_SCAN = 20000       # entries examined before we stop and report

# Type-ahead: typing letters at the list jumps to the item that starts with
# them; the buffer resets after this long, so a fresh word starts a fresh jump.
TYPEAHEAD_RESET_MS = 1200

# Copying. Anything bigger than this gets a progress dialog with a working
# Cancel instead of freezing the desktop until it finishes — copying a folder of
# photos to a USB stick is minutes of silence otherwise.
COPY_ASYNC_BYTES = 8 * 1024 * 1024
COPY_CHUNK = 256 * 1024


class _CopyCancelled(Exception):
    """Raised inside the copy worker when the user presses Cancel."""


class _UndoStale(Exception):
    """Raised when the item an Undo was recorded for is no longer the item at
    that pathname. Undo names one concrete thing, not a name on disk."""


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%d %s" % (n, u)) if u == "B" else ("%.1f %s" % (n, u))
        n /= 1024.0


def size_text(name, isdir, nbytes):
    """The Size column reading for an item.

    An application is a small launcher stub, so its file size ("44 B") is not
    the size of the app and means nothing to anybody — it just put a confident
    wrong number down the whole of the most-looked-at screen in the OS. Apps
    take the same em dash folders do; the real byte count is still the hidden
    sort key, and Get Info still reports it for anyone who asks."""
    if isdir or name.endswith(".app"):
        return "—"
    return human(nbytes)


# Folders whose on-disk name is not what the user should be shown. The Trash
# lives in the dotted ".Trash" so it stays out of the Home listing, but the
# sidebar has always called it "Trash" — the title bar and breadcrumb said
# ".TRASH" / ".Trash", which reads as a stray file to anyone who has never met
# a dotfile.
DISPLAY_NAMES = {".Trash": "Trash"}


def _unframe(scroller):
    """Drop the frame GTK puts around a scroller's contents.

    Adding a non-scrollable child (a Box) to a Gtk.ScrolledWindow makes GTK slip
    a Gtk.Viewport in between, and that viewport defaults to SHADOW_IN — which
    styles as `frame` and draws a hairline in a grey (#A29E9B) that appears
    nowhere in this design. It boxed the toolbar and, worse, drew a rectangle
    around the breadcrumb that ran on past the last pill, reading as an empty
    extra crumb. The scrollers here are plumbing, not framed regions."""
    scroller.set_shadow_type(Gtk.ShadowType.NONE)
    child = scroller.get_child()
    if isinstance(child, Gtk.Viewport):
        child.set_shadow_type(Gtk.ShadowType.NONE)


def push_history(history, pos, rel):
    """Record `rel` as the newest spot in a Back/Forward history.

    Returns the new (history, position) pair. Everything after the current
    position is dropped — once you go Back and then somewhere else, the branch
    you left is not somewhere you can go Forward to any more — and navigating
    to the folder you are already in does not add a second copy of it, so Back
    never has to be pressed twice to leave one place.

    Kept out of the window class (and free of GTK) so the Back/Forward
    contract can be exercised headlessly by
    `tools/shell_finder_ux_selftest.py`."""
    hist = list(history[:pos + 1])
    if not hist or hist[-1] != rel:
        hist.append(rel)
    return hist, len(hist) - 1


def nav_direction(frm, to):
    """The direction of a move from history slot `frm` to slot `to`.

    Named in the OS-wide MEANING vocabulary (`nbtransitions.BACK` /
    `FORWARD` / `CROSSFADE`) rather than in words of the Finder's own, so a
    later view transition and this navigation agree on what "Back" is: a move
    to a LOWER history slot. Staying on the same slot is a refresh in place,
    which has no direction."""
    if to < frm:
        return nbtransitions.BACK
    if to > frm:
        return nbtransitions.FORWARD
    return nbtransitions.CROSSFADE


def restores_place(direction):
    """Whether arriving with `direction` should put the person back where they
    were in that folder — selection and scroll — rather than at the top.

    A move through history (Back/Forward) and a refresh in place are RETURNS:
    the folder was already on screen a moment ago and losing the spot reads as
    the Finder forgetting what you were doing. A fresh navigation into a folder
    (a double-click, a breadcrumb, a sidebar place) is an ARRIVAL and starts at
    the top, which is where a person expects a folder they just opened to be."""
    return direction in (nbtransitions.BACK, nbtransitions.FORWARD,
                         nbtransitions.CROSSFADE)


# The folders the OS itself provisions in Home, which the sidebar and the
# breadcrumb already show translated (both are made of Buttons, which nbi18n
# reaches). Only these, and only when they are the real one sitting directly
# in Home — a folder the user made and called "Music" inside Documents is
# their own word and stays exactly as they typed it.
HOME_FOLDERS = frozenset(("Applications",) + PERSONA_DIRS)


def display_name(name, rel=None):
    """What the user should READ for an item called `name`.

    Applications live on disk as "Calculator.app" so the launcher can recognise
    them, but ".app" is plumbing — it means nothing to the person reading the
    Applications folder, which is the most-looked-at screen in the OS. Only the
    display changes: the store still holds the real name, so search, sort,
    rename and remove all keep working against the file that exists.

    DISPLAY_NAMES handles the other case, a folder whose on-disk name is not
    the one the product uses (".Trash" is the Trash everywhere else).

    AND IT TRANSLATES THE APPLICATIONS. Every app name is already in all
    seventeen catalogs — Settings is "Ajustes", "Настройки", "設定" — and every
    one of them reached the screen in English anyway, because the Applications
    folder is a Gtk.TreeView and nbi18n's automatic layer only walks Labels and
    Buttons. So the one screen a non-English user opens first, and opens most,
    was the one screen still entirely in English, in an OS otherwise fully
    translated. Only .app entries are translated: a document called "Music.txt"
    is the user's own words and must survive untouched, which is the same rule
    nbi18n.set_verbatim exists for.

    `rel` is the item's Home-relative path, and it is what limits the second
    case: THE SIX FOLDERS THE OS PROVISIONS. The sidebar showed Documentos,
    Música, Imágenes and the list beside it showed Documents, Music, Pictures
    — the same six folders under two names, three centimetres apart on one
    screen. Only a folder whose relative path IS its name is one of them, so
    a folder the user made and called "Music" is never touched.

    ONLY THE DISPLAY CHANGES. Everything that has to find the thing on disk —
    launching (APP_MODULES is keyed on the real name), renaming, sorting,
    icons, Kind — reads the store's raw value, not this. `search_names` below
    is what keeps typing either name working."""
    if name in DISPLAY_NAMES:
        return _t(DISPLAY_NAMES[name])
    if name.endswith(".app"):
        return _t(name[:-4])
    if name in HOME_FOLDERS and rel == name:
        return _t(name)
    return name


def search_names(name, rel=None):
    """Every name an item can be FOUND by: what it is called on disk and what
    it is called on screen.

    A Spanish user typing "aju" must reach Ajustes, and one who knows the app
    as Settings must still reach it by typing "set" — the app is called both,
    and a search that knew only one of them would have made a translated name
    worse than an untranslated one."""
    disp = display_name(name, rel).lower()
    raw = name.lower()
    stem = raw[:-4] if raw.endswith(".app") else raw
    return (disp, stem) if disp != stem else (disp,)


def _unmount_esc(s):
    """Undo the octal escaping /proc/mounts applies to space, tab and backslash.

    A stick labelled "My Backup" is mounted at /media/My Backup and appears in
    /proc/mounts as "My\\040Backup"; used raw, the path does not exist."""
    if "\\" not in s:
        return s
    for esc, ch in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"),
                    ("\\134", "\\")):
        s = s.replace(esc, ch)
    return s


# Raw kernel names for a whole disk or a partition: sda1, nvme0n1p2, mmcblk0p1.
_RAW_DEV_RE = re.compile(r"^(sd[a-z]+\d*|nvme\d+n\d+(p\d+)?|mmcblk\d+(p\d+)?"
                         r"|vd[a-z]+\d*|hd[a-z]+\d*|sr\d+|loop\d+)$")


def _volume_name(dev, mnt):
    """What to call a mounted volume in the sidebar.

    automount.sh names the mount point after the volume's own label, so the
    basename is usually already the right, human answer ("PHOTOS", "My Backup").
    This only has to catch what slips through: a volume with no label, or one
    mounted by something other than automount, where the basename is a raw
    kernel device name. "sda1" means nothing to the person holding the stick."""
    name = os.path.basename(mnt) or os.path.basename(dev)
    if _RAW_DEV_RE.match(name):
        return "USB Drive" if name.startswith(("sd", "mmcblk")) else "Disk"
    return name


def icon_for(name):
    """nbicons glyph name for a file/app `name`. Module-level so nbpicker reuses
    the Finder's EXACT icon mapping (Finder._icon_for delegates here)."""
    n = name.lower()
    if n.endswith(".app"):
        mod = APP_MODULES.get(name[:-4])
        if mod:
            return ICON_ALIAS.get(mod, mod)
        return "packages"
    # The glyph says what the file IS, not which app opens it: a film clip and a
    # photo both open in the Media Viewer but must not share the photo icon, and
    # every audio format must carry the music note (.flac / .ogg / .m4a were
    # falling through to the generic document glyph beside an "Audio" Kind).
    m = {".txt": "writer", ".md": "writer", ".writer": "writer",
         ".png": "media", ".jpg": "media",
         ".jpeg": "media", ".gif": "media", ".bmp": "media", ".webp": "media",
         ".tiff": "media", ".tif": "media", ".ico": "media", ".svg": "media",
         ".heic": "media", ".heif": "media", ".avif": "media",
         ".mp4": "video", ".m4v": "video",
         ".mkv": "video", ".mov": "video", ".webm": "video", ".avi": "video",
         ".mp3": "music", ".wav": "music", ".ogg": "music", ".flac": "music",
         ".m4a": "music",
         ".gba": "cartridge", ".gbc": "cartridge", ".gb": "cartridge",
         ".sgb": "cartridge",
         ".pdf": "ebook", ".epub": "ebook"}
    for ext, ic in m.items():
        if n.endswith(ext):
            return ic
    return "toc"


def kind_for(name, isdir):
    """Human 'Kind' descriptor (module-level twin; Finder._kind_for delegates).

    Translated here, at the one point that produces it. Kind is a column of a
    Gtk.TreeView, so nbi18n's automatic layer never saw it and the whole
    column read Game / Utility / Word Processor beside app names that were
    themselves English — every word in the Applications folder, on a machine
    set to Spanish. Every value is already in all seventeen catalogs.

    Safe to translate at the source because nothing compares this: it is put
    in the store, drawn in the Kind column, and shown in the info panel. The
    "PNG File" fallback is built from the extension, which is not a word in
    any language and is left as it is."""
    if isdir:
        return _t("Folder")
    if name.endswith(".app"):
        return _t(APP_KIND.get(name[:-4], "Application"))
    n = name.lower()
    ek = {".txt": "Text", ".md": "Text", ".writer": "Document",
          ".gba": "Game", ".gbc": "Game", ".gb": "Game", ".sgb": "Game",
          ".png": "Image", ".jpg": "Image",
          ".jpeg": "Image", ".gif": "Image", ".bmp": "Image", ".webp": "Image",
          ".tiff": "Image", ".tif": "Image", ".ico": "Image", ".svg": "Image",
          ".heic": "Image", ".heif": "Image", ".avif": "Image",
          ".pdf": "Document", ".epub": "E-book",
          ".mp3": "Audio", ".wav": "Audio", ".ogg": "Audio", ".flac": "Audio",
          ".m4a": "Audio", ".mp4": "Video", ".m4v": "Video", ".mkv": "Video",
          ".mov": "Video", ".webm": "Video", ".avi": "Video"}
    for e, k in ek.items():
        if n.endswith(e):
            return _t(k)
    base, dot, ext = name.rpartition(".")
    return (_t("%s File") % ext.upper()) if dot and ext and base \
        else _t("Document")


def list_dir(abspath, show_hidden=False):
    """Shared disk walker used by BOTH the Finder and nbpicker. Returns records:
        {name, is_dir, size_bytes, mtime, size, date, kind, icon}
    Pure filesystem read (no GTK/pixbufs). Never raises. Mirrors Finder.load's
    per-entry stat loop, including the musl %-d ValueError guard."""
    out = []
    try:
        names = sorted(os.listdir(abspath))
    except OSError:
        return out
    for nm in names:
        if nm.startswith(".") and not show_hidden:
            continue
        p = os.path.join(abspath, nm)
        isdir = os.path.isdir(p)
        size_bytes, mtime = 0, 0.0
        try:
            st = os.stat(p)
            mtime = st.st_mtime
            size_bytes = 0 if isdir else st.st_size
            size = size_text(nm, isdir, st.st_size)
            date = _t(time.strftime("%-d %b %Y", time.localtime(st.st_mtime)))
        except (OSError, ValueError):
            size, date = "\u2014", "\u2014"
        out.append({"name": nm, "is_dir": isdir, "size_bytes": size_bytes,
                    "mtime": mtime, "size": size, "date": date,
                    "kind": kind_for(nm, isdir),
                    "icon": "folder" if isdir else icon_for(nm)})
    return out


class Crumbs(Gtk.Box):
    """The path breadcrumb, rendered as a row of clickable pill buttons — one
    per path component, the current folder shown active. Keeps a get_text()
    that returns the classic "Home  ›  Documents" string so callers/tests can
    still read the trail as plain text."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.get_style_context().add_class("crumbbar")
        self._text = ""
        # The trail as (label, target) pairs, so the next set_trail can tell
        # which components are NEW. None until the first paint, which is what
        # keeps the bar from staging itself when the Finder opens.
        self._pills = None

    def get_text(self):
        return self._text

    def _scroll_to_end(self):
        """Keep the LAST pill — the folder you are in — in view when the trail
        is wider than the space the toolbar can give it (see the scroller the
        Finder wraps this in)."""
        try:
            sw = self.get_parent()
            adj = sw.get_hadjustment() if sw is not None else None
            if adj is not None:
                adj.set_value(adj.get_upper() - adj.get_page_size())
        except Exception:
            pass
        return False

    def set_trail(self, root_label, trail, on_load):
        # root_label: "Home"/"Computer"; trail: list of (label, target_rel) for
        # each component after the root; on_load: callback(rel) to navigate.
        for c in self.get_children():
            self.remove(c)
        self._text = root_label + "".join("  ›  " + lbl for lbl, _ in trail)
        pills = [(root_label, "" if root_label == "Home" else "/")] + list(trail)
        last = len(pills) - 1
        # nbmotion-inventory: finder.open-folder
        # The trail is rebuilt on EVERY navigation, so a pill that was already
        # on screen must not restage: only the components DEEPER than the one
        # the person came from are new, and only those open. The shared prefix
        # is compared on (label, target) — target alone would treat a rename as
        # a move, and label alone would confuse two folders of the same name in
        # different parents.
        shared = 0
        if self._pills is not None:
            for old, new in zip(self._pills, pills):
                if old != new:
                    break
                shared += 1
        else:
            shared = len(pills)     # first paint: the bar arrives whole
        self._pills = list(pills)
        opening = []
        for i, (label, target) in enumerate(pills):
            btn = Gtk.Button(label=label)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            ctx = btn.get_style_context()
            ctx.add_class("crumb")
            if i == last:
                ctx.add_class("active")
            btn.connect("clicked", lambda _w, t=target: on_load(t))
            if i < shared:
                self.pack_start(btn, False, False, 0)
                continue
            try:
                rev = Gtk.Revealer()
                rev.set_reveal_child(False)
                rev.add(btn)
                self.pack_start(rev, False, False, 0)
                opening.append(rev)
            except Exception:                                     # noqa: BLE001
                # A crumb you cannot click is a navigation you cannot undo, so
                # the pill is packed plainly if the Revealer will not build.
                self.pack_start(btn, False, False, 0)
        self.show_all()
        # after the new pills have been sized, not before
        GLib.idle_add(self._scroll_to_end)
        for n, rev in enumerate(opening):
            try:
                # SLIDE_RIGHT: the bar GROWS rightward into the space the new
                # component needs, instead of jumping to its full width and
                # fading a pill into it. Re-scroll when the LAST one lands --
                # _scroll_to_end above runs while the pill is still opening, so
                # it measures a bar narrower than the final one and would leave
                # the folder you just opened out of view.
                nbtransitions.reveal(
                    rev, True, direction=nbtransitions.SLIDE_RIGHT,
                    duration=nbtransitions.SURFACE_IN,
                    on_done=((lambda _ok: self._scroll_to_end())
                             if n == len(opening) - 1 else None))
            except Exception:                                     # noqa: BLE001
                try:
                    rev.set_reveal_child(True)
                except Exception:                                 # noqa: BLE001
                    pass


class Finder(Gtk.Window):
    def __init__(self, start="Applications"):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_decorated(False)
        # opaque visual before realise: an RGBA visual under a compositor
        # shows black wherever the app has not painted (see nbapp).
        nbapp.force_opaque_visual(self)              # we draw our own title bar
        # Size the desktop home to fit the ACTUAL screen — real hardware panels
        # are not 1920x1080; a fixed 1180x940 overflows a smaller display (and
        # left no room for the widget column to its right). Fit within the screen,
        # leaving space for the top panel and the widget column.
        # nbapp.screen_size() returns the REAL primary-monitor pixel size (and
        # falls back sanely on its own) — never assume a literal 1920x1080 here,
        # which would overflow a smaller panel. Matches nbapp/widgets/installer.
        _sw, _sh = nbapp.screen_size()
        _wcol = min(620, max(320, _sw // 3)) + 80
        self._home_size = (min(1180, max(560, _sw - _wcol)),
                           min(940, _sh - 46 - 40))
        self.set_default_size(*self._home_size)
        # The window must never be dragged UNDER the panel: the panel is a
        # strut-docked bar across the top, and a window pushed above y=PANEL_H
        # hides its own title bar behind it — with no decorations, that leaves
        # nothing to grab and the window is stranded. The WM does the dragging
        # (begin_move_drag), so the clamp happens after the fact, on the
        # configure event that reports the new position.
        self.connect("configure-event", self._clamp_to_workarea)
        # The Finder is a floating window in the design. matchbox maximizes
        # normal toplevels (handheld lineage), so present it as a dialog in
        # free-dialog mode: it floats at the size/position it asks for.
        # matchbox pins dialogs above main clients and lets them keep
        # focus, so the Finder HIDES itself while an app it launched is
        # running (see launch_app) — the fullscreen app owns the screen,
        # exactly like the design, and the Finder returns when it exits.
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        # Accept keyboard focus so the Search field can actually receive typed
        # text (it was dead before). The old worry — the Finder starving the
        # panel menus / app windows of focus — no longer applies: the panel
        # menus are in-window pointer-driven (no Gtk.Menu grab), and a launched
        # app maps on top and takes focus as the new top window (the Finder also
        # hides while an app is active). Don't grab focus on map, though, so the
        # desktop home doesn't yank focus the instant it (re)appears.
        self.set_accept_focus(True)
        self.set_focus_on_map(False)
        panel_h = 46
        # left band of the desktop home; the widget column sits to the right
        self.move(40, panel_h + 16)
        self.get_style_context().add_class("finder")
        # Install this app's stylesheet HERE, not only from the __main__ block.
        #
        # nbapp.AppWindow.__init__ does the same for every other app, but the
        # Finder is its own Gtk.Window subclass and so was the one window whose
        # CSS depended on being launched as a script. Anything that CONSTRUCTS a
        # Finder instead -- the offscreen render harness (tools/appshot.py),
        # construct_all, a future embedder -- got a Finder with none of its own
        # styling, silently falling through to the bare theme.
        #
        # That is not a cosmetic problem for the app (the shipped desktop always
        # goes through __main__, so users saw the right thing) but it is a
        # serious one for the TOOLS: every screenshot of the Finder taken for a
        # UI audit was of an unstyled window, so the sidebar rows rendered as
        # bordered theme buttons rather than the flat rows they really are, and
        # anyone reading those renders would go and "fix" a defect that does not
        # exist. Measured: sbrow border-radius came out 8 (theme button) before
        # this call and 2 (the .sbrow rule) after it.
        # install_css() is idempotent, so constructing several Finders is free.
        install_css()
        self.rel = start                       # current path relative to HOME
        self._history = [start]                # visited rel paths (back/fwd)
        self._hpos = 0                         # index of current spot in history
        # One generation per SHOWN FOLDER. Every delayed callback that would
        # write into the view (the coalesced filesystem-monitor reload, the
        # idle scroll restore) carries the token of the folder it was posted
        # for, so a reload triggered by a folder the user has since left cannot
        # land on the one they are looking at now. Closed on destroy.
        self._dirgen = nbstate.Generation("finder-dir")
        self._dir_reload_id = 0                # pending coalesced reload source
        self._dir_reload_token = None          # ...and the folder it speaks for
        # Everything this window schedules for the rest of its life is recorded
        # here so destroy can release it. Set before any of it can start: the
        # repeating sources below are created while the UI is still being
        # built, and a callback that outlives the window would keep the whole
        # Finder alive and go on touching sidebar/visibility widgets.
        self._closed = False                   # destroy ran: callbacks must stop
        self._dev_poll_id = 0                  # repeating Devices re-read
        self._app_poll_id = 0                  # fallback app-flag poll
        self._app_flag_monitor = None          # Gio monitor, cancelled on destroy
        self._places = {}                      # rel -> {"sel": rel path, "scroll"}
        self._nav_dir = nbtransitions.NONE     # direction of the current move
        # Opening a folder is an ARRIVAL, not a history move, so it must not be
        # stamped into _nav_dir: restores_place() reads that same value and
        # would put the person back on a remembered row instead of at the top of
        # the folder they just opened. The slide direction and "is this a
        # return?" are two different questions and get two different signals.
        self._nav_enter = False                # one-shot: slide the next load in
        self._filter = ""                      # live search filter (substring)
        self._wide = []                        # search hits from the rest of Home
        self._wide_gen = 0                     # stamp: a stale scan can't land
        self._wide_id = 0                      # pending debounce timeout
        self._searching = False                # a whole-Home scan is running
        self._wide_capped = False              # ...and it stopped at the cap
        self._typeahead = ""                   # letters typed at the list
        self._typeahead_id = 0                 # timeout that clears them
        self._undo = None                      # {"label", "fn"} for Ctrl+Z
        self._raw_entries = []                 # cached disk listing (unfiltered)
        self._free = "—"                        # cached free-space status string
        self._sb_rows = []                     # (rel, button) for sidebar places
        self._clipboard = None                 # (abspath, is_cut) for copy/paste
        self._inflight = set()                 # dests claimed by a running copy
        self._show_hidden = False               # View: show dotfiles
        self._view = "list"                     # "list" | "grid" view mode
        self._sort_col = 1                      # store column the list sorts by
        self._sort_desc = False                 # ...and in which direction
        self._prefs_ready = False               # gate _save_prefs during build
        self._load_prefs()                      # restore view mode + show hidden
        self._load_removed_apps()               # apps hidden from Applications
        self._ensure_places()                   # persona folders must exist

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.get_style_context().add_class("window-frame")
        # A software compositor (xcompmgr) now backs this window in an off-screen
        # pixmap, so the home is a movable/resizable floating window again (it was
        # pinned to avoid the no-compositor expose storm). Wrap the frame in an
        # overlay so a corner resize grip can float over its bottom-right corner;
        # the title bar drives the move (see _begin_move).
        root = Gtk.Overlay()
        root.add(outer)
        if _ACCEL:                       # resize only when the compositor backs us
            root.add_overlay(self._resize_grip())
        self.add(root)
        # Name it the way nbapp names its overlay: the press-and-hold accent
        # palette (and anything else that draws inside the window instead of in
        # a second toplevel) looks for exactly this attribute. Naming files
        # "Álbumes" or "Café" is the Finder's own reason to want it.
        self._overlay = root
        self._outer = outer   # so the collapse box can roll the body up

        outer.pack_start(self._titlebar(), False, False, 0)
        # The navbar is the widest thing in the window (nine tool buttons, the
        # crumb bar, a search field and the view switcher), and as a plain Box
        # its minimum width became the WINDOW's minimum width: 1280px, so the
        # Finder could not be resized meaningfully smaller and would not fit a
        # small panel at all. Putting it in a horizontal scroller lets the
        # window shrink to whatever the content area needs, with the toolbar
        # scrolling sideways instead of blocking the resize. EXTERNAL keeps the
        # scrollbar itself out of the layout (no bar drawn across the toolbar);
        # propagate_natural_width keeps the toolbar its normal size whenever
        # there IS room, so nothing changes at a comfortable window size.
        _navscroll = Gtk.ScrolledWindow()
        _navscroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        _navscroll.set_propagate_natural_width(True)
        _navscroll.set_propagate_natural_height(True)
        _navscroll.get_style_context().add_class("navbar")
        _navscroll.add(self._navbar())
        _unframe(_navscroll)
        outer.pack_start(_navscroll, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.pack_start(self._sidebar(), False, False, 0)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # icon,name,size,date,relpath, is_dir, size_bytes, mtime, kind, gridicon
        # (size_bytes/mtime are hidden sort keys so Size/Date sort numerically
        # and folders group first; kind is the human "Kind" column, index 8;
        # gridicon at index 9 is the same glyph rendered LARGE for the icon view,
        # so list rows stay compact while the grid shows big, crisp icons — both
        # views share one model, so search/sort/load keep them in step)
        # Columns 0 and 9 hold cairo SURFACES, not pixbufs. A pixbuf has no
        # notion of display scale, so on a HiDPI panel every icon in the file
        # list and the grid was a logical-size bitmap stretched by the
        # compositor — soft icons beside sharp text, on exactly the machines
        # bought for their screen. A surface carries a device scale and
        # CellRendererPixbuf's `surface` property honours it.
        # See nbicons.SURFACE_GTYPE for why the type cannot be TYPE_OBJECT.
        self.store = Gtk.ListStore(nbicons.SURFACE_GTYPE, str, str, str, str,
                                   bool, GObject.TYPE_INT64, GObject.TYPE_DOUBLE,
                                   str, nbicons.SURFACE_GTYPE)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)
        self.tree.get_style_context().add_class("filelist")
        self._add_columns()
        self.tree.connect("row-activated", self._on_open)
        # right-click a row -> context menu (Open / Rename / Duplicate / …)
        self.tree.connect("button-press-event", self._on_tree_button)
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.tree)
        self._list_sw = sw
        views = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        views.pack_start(sw, True, True, 0)
        # Grid view: an icon grid over the SAME model, so search/sort/load all
        # keep it in step. Hidden until the toolbar's grid toggle selects it.
        self.iconview = Gtk.IconView(model=self.store)
        # bind renderers via CellLayout (not set_pixbuf_column) because the
        # store's icon columns hold cairo SURFACES, which set_pixbuf_column
        # rejects; binding the renderer's `surface` property by hand accepts
        # them, exactly like the list view's Name column below.
        gpr = Gtk.CellRendererPixbuf()
        self.iconview.pack_start(gpr, False)
        # bind to store col 9 (the LARGE glyph), not col 0 (the 22px list glyph):
        # the grid view must show big icons, and reusing col 0 is what made them
        # render tiny on real hardware.
        self.iconview.add_attribute(gpr, "surface", 9)
        gtr = Gtk.CellRendererText()
        gtr.set_property("xalign", 0.5)
        # CENTER is 1; 2 is RIGHT. The name under a grid icon was right-ragged
        # (invisible while every name fitted one line, obvious once they wrap).
        gtr.set_property("alignment", Pango.Alignment.CENTER)
        # A file name has to WRAP under its icon. GtkIconView's item-width is a
        # hint about the cell, NOT a wrap width: with no wrap-width the renderer
        # lays the name out on one line at its natural width, and because every
        # cell is as wide as the widest one, a single long name ("Holiday photos
        # from the summer of...") collapsed the grid to one column AND pushed the
        # window past the edge of a 1024px panel, where it could not be reached.
        gtr.set_property("wrap-mode", Pango.WrapMode.WORD_CHAR)
        gtr.set_property("wrap-width", GRID_LABEL_PX)
        self.iconview.pack_start(gtr, True)
        self.iconview.add_attribute(gtr, "text", 1)
        self.iconview.set_cell_data_func(gtr, self._name_cell_data)
        # keep the grid's text renderer so inline rename can drive it too; it is
        # editable only for the duration of a rename (see _begin_rename).
        gtr.connect("edited", self._on_name_edited)
        gtr.connect("editing-canceled", lambda *_: self._end_rename_mode())
        gtr.connect("editing-started", self._on_edit_started)
        self._grid_text_renderer = gtr
        # Cell geometry sized around the LARGE 96px glyph — wider than the icon
        # so a name gets a comfortable two lines under it (the renderer wraps to
        # GRID_LABEL_PX above); the spacing values give the icon-to-label gap and
        # the inter-cell gutters (a roomy grid) that scale with the bigger icon
        # without leaving cavernous whitespace around it.
        self.iconview.set_item_width(GRID_CELL_PX)
        self.iconview.set_item_padding(GRID_CELL_PAD)
        self.iconview.set_spacing(12)         # gap between the icon and its label
        self.iconview.set_row_spacing(22)
        self.iconview.set_column_spacing(12)
        self.iconview.set_margin(24)          # inset the whole grid from the edges
        self.iconview.get_style_context().add_class("filegrid")
        self.iconview.connect("item-activated", self._on_open_grid)

        # The growing launch card was RETIRED 2026-08-09 (design owner: a simple
        # fade, not a growing paper card). The transform half of system.app-launch
        # now lives in nbapp — the app fades itself in on first map. The _zoom_*
        # state and its draw-after hook below are left INERT (never started; the
        # draw handler returns at once) rather than deleted, so the launch-
        # continuity path that still calls _zoom_clear/_zoom_retract stays valid.
        self._zoom = None
        self._zoom_v = 0.0
        self._zoom_active = False
        self._zoom_from = self._zoom_to = (0.0, 0.0, 1.0, 1.0)
        self._zoom_title = ""
        self._launch_origin = None
        self._launch_pid = None
        self.connect_after("draw", self._zoom_draw)
        self._zoom_ok = True
        self.iconview.connect("button-press-event", self._on_grid_button)
        grid_sw = Gtk.ScrolledWindow()
        grid_sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        grid_sw.add(self.iconview)
        grid_sw.set_no_show_all(True)           # _apply_view controls visibility
        self._grid_sw = grid_sw
        views.pack_start(grid_sw, True, True, 0)
        # An empty folder (or a search with no matches) shows a plain, centered
        # message over the empty view instead of a disconcerting blank void.
        overlay = Gtk.Overlay(); overlay.add(views)
        self._empty_label = Gtk.Label()
        self._empty_label.get_style_context().add_class("emptystate")
        self._empty_label.set_halign(Gtk.Align.CENTER)
        self._empty_label.set_valign(Gtk.Align.CENTER)
        self._empty_label.set_justify(Gtk.Justification.CENTER)
        self._empty_label.set_line_wrap(True)
        self._empty_label.set_max_width_chars(30)
        self._empty_label.set_no_show_all(True)  # shown only when the view is empty
        overlay.add_overlay(self._empty_label)
        try:
            overlay.set_overlay_pass_through(self._empty_label, True)
        except (AttributeError, TypeError):
            pass
        # nbmotion-inventory: finder.navigate-back
        # nbmotion-inventory: finder.navigate-forward
        # A Back/Forward navigation SLIDES the outgoing listing off in the
        # direction of travel, revealing the freshly-loaded listing beneath it
        # (paper moving along the grid). It is PAINT on a pass-through
        # DrawingArea over the content, never an allocation (F2): the real views
        # are repopulated instantly underneath and the OUTGOING snapshot is drawn
        # sliding out. Under policy-still the slide is skipped and the new
        # listing simply appears -- instant-equivalent (nbmotion.Scalar's own
        # contract), and any capture/draw failure falls back to that same swap.
        self._content_overlay = overlay
        self._nav_da = Gtk.DrawingArea()
        self._nav_da.set_no_show_all(True)
        self._nav_da.connect("draw", self._nav_draw)
        overlay.add_overlay(self._nav_da)
        try:
            overlay.set_overlay_pass_through(self._nav_da, True)
        except (AttributeError, TypeError):
            pass
        self._nav_slide = None       # (surface, w, h, sign) while a slide runs
        self._nav_v = 0.0            # 0..1 progress of the outgoing slide
        self._nav_gen = 0            # drops the frames of a superseded slide
        main.pack_start(overlay, True, True, 0)
        self.status = Gtk.Label(xalign=0)
        self.status.get_style_context().add_class("statusbar")
        main.pack_start(self.status, False, False, 0)
        body.pack_start(main, True, True, 0)
        outer.pack_start(body, True, True, 0)

        self._set_view(self._view)   # reflect the restored view mode on the chrome
        self.load(self.rel)

        # Press-and-hold accent palette, connected BEFORE the Finder's own key
        # handler so Esc dismisses an open palette instead of the rename box
        # underneath it. Guarded: a load failure must never break the Finder.
        try:
            import nbdiacritics
            self._diacritics = nbdiacritics.DiacriticsPicker(self)
        except Exception:
            self._diacritics = None

        # F2 renames the selected item (window-level, so it works whichever
        # child — list, grid, or the search box — currently holds focus).
        self.connect("key-press-event", self._on_key_press)
        self.connect("destroy", self._on_destroy_navigation)

        # matchbox occasionally leaves a freshly-mapped dialog's frame
        # unmapped when it appears before any input/restack event (its
        # delayed-mapping path never completes). If we're not viewable
        # shortly after startup, remap: the new MapRequest makes matchbox
        # re-activate us, which completes the mapping. (A blank-but-viewable
        # window is the separate TCG repaint artifact — not reliably fixable
        # in-process; on real hardware the first paint lands normally.)
        GLib.timeout_add(400, self._nudge)
        GLib.timeout_add(1200, self._nudge)
        GLib.timeout_add(2000, self._ensure_mapped)
        GLib.timeout_add(6000, self._ensure_mapped)
        # Hide the Finder while a launched app owns the screen (the flag file is
        # written by launch_app AND the panel/shell), reappearing when it exits.
        # This transition is rare and event-driven, so watch the flag with an
        # inotify-backed GLib file monitor rather than stat-polling it ~2.5x a
        # second for the whole session (the old 400ms poll). monitor_file
        # reports CREATED/DELETED even for a path that doesn't exist yet.
        self._cancel_app_flag_monitor()
        self._stop_source("_app_poll_id")
        try:
            _flag = Gio.File.new_for_path(nbapp.APP_FLAG)
            self._app_flag_monitor = _flag.monitor_file(
                Gio.FileMonitorFlags.NONE, None)
            self._app_flag_monitor.connect("changed", self._on_app_flag_changed)
        except Exception:
            # No file-monitor backend available: fall back to an occasional
            # poll. The flag flips only when an app opens/closes, so a slow
            # interval is plenty (vs. the old 400ms wake).
            self._app_poll_id = GLib.timeout_add_seconds(
                3, self._poll_app_flag)
        # Reconcile once shortly after start too: covers a flag already present
        # when the Finder (re)launches, which has no future monitor event.
        GLib.timeout_add(500, self._reconcile_app_flag_once)
        self._prefs_ready = True   # user-driven changes may now persist to disk

    def _sync_app_flag(self):
        # Reconcile visibility with the app-active flag: hide while a launched
        # app owns the screen, reappear when it exits.
        try:
            active = os.path.exists(nbapp.APP_FLAG)
            if active and self.get_visible():
                self.hide()
            elif not active and not self.get_visible():
                self.show_all()
        except Exception:
            pass

    def _on_app_flag_changed(self, *_):
        # GLib file-monitor callback: the flag file was created or removed.
        # A cancelled monitor can still deliver a queued event, and a destroyed
        # window must not be shown or hidden.
        if getattr(self, "_closed", False):
            return
        self._sync_app_flag()

    def _reconcile_app_flag_once(self):
        if not getattr(self, "_closed", False):
            self._sync_app_flag()
        return False

    def _poll_app_flag(self):
        # Fallback path used only when no file monitor is available; repeats.
        if getattr(self, "_closed", False):
            return False
        self._sync_app_flag()
        return True

    def _ensure_mapped(self):
        # Don't force-remap on top of a freshly-launched app: if the app-active
        # flag exists an app owns the screen, so stay hidden (mirrors
        # _poll_app_flag) rather than flashing the Finder over the app's window.
        if getattr(self, "_closed", False):
            return False
        try:
            if os.path.exists(nbapp.APP_FLAG):
                return False
        except Exception:
            pass
        win = self.get_window()
        if win is not None and not win.is_viewable():
            self.hide()
            self.show_all()
        return False

    def _ensure_places(self):
        # create the sidebar's persona folders so none of the Places rows is a
        # dead link (navigating to a missing folder would show an empty list).
        for sub in PERSONA_DIRS:
            try:
                os.makedirs(os.path.join(HOME, sub), exist_ok=True)
            except OSError:
                pass

    # ---- preferences (view mode + show-hidden persist across launches) ----
    def _prefs_path(self):
        return os.path.join(HOME, ".config", "notebook", "finder.json")

    def _load_prefs(self):
        # Restore the view mode and Show-hidden toggle from the last session so
        # a chosen Grid view (or shown dotfiles) survives a close/reopen.
        # Guarded end-to-end: a missing or garbage file just leaves the defaults.
        self._prefs_extra = {}
        self._prefs_quarantine_pending = False
        try:
            import json
            with open(self._prefs_path()) as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                self._prefs_quarantine_pending = True
                return
            known = {"view", "show_hidden", "sort", "sort_desc", "_extra"}
            extra = data.get("_extra")
            self._prefs_extra = dict(extra) if isinstance(extra, dict) else {}
            for key, value in data.items():
                if key not in known:
                    self._prefs_extra[key] = value
            if data.get("view") in ("list", "grid"):
                self._view = data["view"]
            if isinstance(data.get("show_hidden"), bool):
                self._show_hidden = data["show_hidden"]
            # Sort order too: someone who works newest-first should not have to
            # say so again in every folder, at every launch. Only the four real
            # sort keys are accepted, so a hand-edited file can't wedge the list.
            if data.get("sort") in SORT_COLUMNS:
                self._sort_col = data["sort"]
            if isinstance(data.get("sort_desc"), bool):
                self._sort_desc = data["sort_desc"]
        except (OSError, ValueError, TypeError):
            if os.path.lexists(self._prefs_path()):
                self._prefs_quarantine_pending = True

    def _save_prefs(self):
        # Persist view mode + show-hidden. Never runs during construction
        # (gated by _prefs_ready) and never raises into the caller.
        if not getattr(self, "_prefs_ready", False):
            return
        try:
            import json
            path = self._prefs_path()
            if getattr(self, "_prefs_quarantine_pending", False):
                self._prefs_quarantine_pending = False
                _quarantine_store(path)
            nbapp.atomic_write_json(path, {"view": self._view,
                                           "show_hidden": self._show_hidden,
                                           "sort": self._sort_col,
                                           "sort_desc": self._sort_desc,
                                           "_extra": getattr(
                                               self, "_prefs_extra", {})})
        except (OSError, TypeError, ValueError) as exc:
            nbapp.save_failure_reason = str(exc)

    # ---- removed applications (hidden from the Applications listing) ----
    # The user can hide an app from the Applications view ("Remove from
    # Applications"); the choice PERSISTS under $NB_HOME/.config/notebook so the
    # app stays gone across reboots, and "Restore removed apps…" brings it back.
    # This never touches the .app file on disk — only which apps the listing
    # shows — so a restore is always possible and nothing is ever destroyed.
    def _removed_apps_path(self):
        return os.path.join(HOME, ".config", "notebook", "removed_apps.json")

    def _load_removed_apps(self):
        self._removed_apps = set()
        try:
            import json
            with open(self._removed_apps_path()) as fh:
                data = json.load(fh)
            if isinstance(data, list):
                self._removed_apps = {str(x) for x in data}
        except (OSError, ValueError, TypeError):
            pass
        self._removed_apps_mtime = self._removed_apps_stamp()

    def _removed_apps_stamp(self):
        """Cheap identity for Packages' store, including atomic replacement."""
        try:
            st = os.stat(self._removed_apps_path())
            return (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            return None

    def _save_removed_apps(self):
        try:
            import json
            path = self._removed_apps_path()
            nbapp.atomic_write_json(path, sorted(self._removed_apps))
        except (OSError, TypeError, ValueError):
            pass

    def _app_is_removed(self, name):
        return name.endswith(".app") and name[:-4] in self._removed_apps

    # ---- chrome ----
    def _titlebar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class("titlebar")
        # The three window controls were blank squares distinguished only by a
        # tooltip, so nothing on screen said which was which. Each now carries
        # its classic mark, DRAWN with cairo rather than typed: the shipped
        # Nimbus Sans has no glyph for these and a missing one renders as a tofu
        # box on real hardware.
        def _winmark(icon):
            img = Gtk.Image()
            try:
                nbicons.set_image(img, icon, 11, "#3A362E")
            except Exception:
                pass
            return img

        close = Gtk.Button(); close.get_style_context().add_class("winbox")
        close.set_valign(Gtk.Align.CENTER)     # keep it a 15px square
        close.set_tooltip_text(_t("Close"))
        close.add(_winmark("wclose"))
        close.connect("clicked", lambda *_: self.close())
        bar.pack_start(close, False, False, 0)
        self.title = Gtk.Label(label=_t("APPLICATIONS"))
        self.title.get_style_context().add_class("wintitle")
        bar.set_center_widget(self.title)
        rbox = Gtk.Box(spacing=8)
        # classic Mac window controls — were created but wired to nothing.
        zoom = Gtk.Button(); zoom.get_style_context().add_class("winbox")
        zoom.set_valign(Gtk.Align.CENTER); zoom.set_tooltip_text(_t("Zoom"))
        zoom.add(_winmark("wzoom"))
        zoom.connect("clicked", self._toggle_zoom)
        rbox.pack_start(zoom, False, False, 0)
        coll = Gtk.Button(); coll.get_style_context().add_class("winbox")
        coll.set_valign(Gtk.Align.CENTER); coll.set_tooltip_text(_t("Collapse"))
        coll.add(_winmark("wshade"))
        coll.connect("clicked", self._toggle_collapse)
        rbox.pack_start(coll, False, False, 0)
        bar.pack_end(rbox, False, False, 0)
        # drag the window by the title bar
        eb = Gtk.EventBox(); eb.add(bar)
        eb.connect("button-press-event", self._begin_move)
        return eb

    # Top panel height; a window's top edge may not go above this.
    PANEL_H = 46

    def _clamp_to_workarea(self, _w, ev):
        """Keep the window inside the work area below the panel.

        Only ever moves the window DOWN/back into view, and only when it is
        actually out of bounds, so a normal drag is untouched. Guarded against
        recursion: move() re-fires configure-event."""
        if getattr(self, "_clamping", False):
            return False
        try:
            x, y = ev.x, ev.y
            sw, sh = nbapp.screen_size()
            nx, ny = x, y
            if ny < self.PANEL_H:
                ny = self.PANEL_H
            # keep a grabbable strip on screen horizontally + at the bottom
            if sw and nx > sw - 120:
                nx = sw - 120
            if sw and nx < -(max(0, ev.width - 120)):
                nx = -(max(0, ev.width - 120))
            if sh and ny > sh - 60:
                ny = sh - 60
            if (nx, ny) != (x, y):
                self._clamping = True
                self.move(int(nx), int(ny))
                GLib.idle_add(self._end_clamp)
        except Exception:
            self._clamping = False
        return False

    def _end_clamp(self):
        self._clamping = False
        return False

    def _begin_move(self, widget, event):
        # Interactive window move. Earlier this was disabled because, with no
        # compositor, a live drag re-exposed the whole window (and everything
        # under it) on every motion event — an expose storm that pinned the CPU
        # on the GPU-less stack. xcompmgr now backs the window in an off-screen
        # pixmap, so we hand the drag to the window manager via
        # _NET_WM_MOVERESIZE (begin_move_drag): the WM/compositor reposition the
        # backing pixmap directly, with ZERO per-motion Python work, so dragging
        # stays smooth on the CPU-only stack. A double-click still zooms.
        if event.type == Gdk.EventType._2BUTTON_PRESS and event.button == 1:
            self._toggle_zoom()
            return True
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1 and _ACCEL:
            self.begin_move_drag(event.button, int(event.x_root),
                                 int(event.y_root), event.time)
            return True
        return False

    def _resize_grip(self):
        # A small opaque tab floated over the window's bottom-right corner that
        # starts a WM-driven interactive resize (begin_resize_drag), the resize
        # analogue of the title-bar move. Like the move, the compositor backs the
        # window so the resize repaints cheaply. The EventBox has its own window,
        # so it MUST paint an opaque background (no-compositor black-safety) —
        # the .resizegrip class fills it from the palette.
        grip = Gtk.EventBox()
        grip.set_halign(Gtk.Align.END)
        grip.set_valign(Gtk.Align.END)
        grip.set_size_request(16, 16)
        grip.get_style_context().add_class("resizegrip")
        grip.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        grip.connect("button-press-event", self._begin_resize)
        grip.connect("realize", self._set_resize_cursor)
        return grip

    def _begin_resize(self, _widget, event):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 1 and _ACCEL:
            self.begin_resize_drag(Gdk.WindowEdge.SOUTH_EAST, event.button,
                                   int(event.x_root), int(event.y_root),
                                   event.time)
            return True
        return False

    def _set_resize_cursor(self, widget):
        # Give the grip the diagonal-resize pointer so it reads as a handle.
        try:
            win = widget.get_window()
            if win is not None:
                win.set_cursor(Gdk.Cursor.new_for_display(
                    widget.get_display(),
                    Gdk.CursorType.BOTTOM_RIGHT_CORNER))
        except Exception:
            pass

    def _toggle_zoom(self, *_):
        # Zoom box: toggle between the default window size and maximized.
        if getattr(self, "_zoomed", False):
            self.unmaximize(); self._zoomed = False
        else:
            self.maximize(); self._zoomed = True

    def _toggle_collapse(self, *_):
        # Collapse box (WindowShade): roll the window up to just its title bar;
        # click again to roll the body back down at its previous size.
        self._collapsed = not getattr(self, "_collapsed", False)
        if self._collapsed:
            self._pre_collapse_size = self.get_size()   # remember to restore
        for child in self._outer.get_children()[1:]:    # all but the title bar
            child.set_visible(not self._collapsed)
        if self._collapsed:
            # Roll up (window-shade): keep the CURRENT width and let only the
            # height clamp to the title-bar minimum. resize(1, 1) also clamped
            # the WIDTH to the collapsed content minimum (~220px), snapping the
            # window narrow instead of just rolling its body up.
            self.resize(self._pre_collapse_size[0], 1)
        else:
            w, h = getattr(self, "_pre_collapse_size",
                           getattr(self, "_home_size", (1180, 940)))
            self.resize(w, h)

    def _icon_btn(self, name, cb, sensitive=True, tip=None):
        b = Gtk.Button(); b.get_style_context().add_class("navbtn")
        # An icon-only button with no tooltip is a control a person has to
        # guess at. (set_tooltip_text is one of the setters nbi18n patches, so
        # a plain English string here still translates.)
        if tip:
            b.set_tooltip_text(tip)
        img = nbicons.image(name, 18,
              "#3A362E" if sensitive else "#B3AD9E")
        # Show the glyph HERE, not via the window's show_all: a caller that
        # sets no_show_all on the button (the Trash button, which only appears
        # outside the Trash) then reveals it with set_visible(True), and
        # set_visible does not recurse into children — so the icon stayed
        # unshown and the toolbar carried a blank white square.
        img.show()
        b.add(img); b.set_sensitive(sensitive)
        b._img = img                           # handle so we can recolor later
        if cb:
            b.connect("clicked", cb)
        return b

    # How many distinct colours a nav-icon tween may render. nbicons caches
    # surfaces in an UNBOUNDED dict keyed partly on colour, so tweening a
    # continuous colour would add a cache entry per frame, for the life of the
    # process, on a control the person uses constantly. Quantising the tween
    # caps the damage at this many extra surfaces per icon, forever.
    _NAV_TWEEN_STEPS = 5
    _NAV_ON, _NAV_OFF = "#3A362E", "#B3AD9E"

    @staticmethod
    def _img_mapped(btn):
        """Whether this button's icon is actually on screen. Never raises: a
        partially-built or torn-down button must still get its colour."""
        try:
            return bool(btn._img.get_mapped())
        except Exception:                                         # noqa: BLE001
            return False

    @staticmethod
    def _mix_hex(a, b, t):
        """`a` toward `b` by `t`, both "#rrggbb"."""
        av = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
        bv = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
        return "#%02x%02x%02x" % tuple(
            int(round(x + (y - x) * t)) for x, y in zip(av, bv))

    def _set_nav(self, btn, name, on):
        # nbmotion-inventory: app.any-toggle
        # Back/Forward change enabled state on EVERY navigation, and the icon is
        # a pre-rendered cairo SURFACE, not a styled node -- so the theme's 90ms
        # feedback spring reaches the button's own colours and cannot reach the
        # glyph inside it. Left alone the button eases while the arrow in it
        # SNAPS. The colour is therefore tweened here, on the same token, so the
        # control changes as one thing.
        btn.set_sensitive(on)
        target = self._NAV_ON if on else self._NAV_OFF
        was = getattr(btn, "_nav_on", None)
        if (was is None or was == on or nbmotion is None
                or nbmotion.policy(nbmotion.FEEDBACK) <= 0
                or not self._img_mapped(btn)):
            # First paint, no real change, a still machine, or a button that is
            # not on screen: land at once. This is the instant-EQUIVALENT path
            # (F4), not a lesser one.
            # THE MAPPED GUARD IS A CORRECTNESS ONE, not an optimisation: frames
            # are delivered by the widget's frame clock, so an UNMAPPED button
            # can start a tween that never ticks — leaving the arrow at its OLD
            # colour, i.e. showing the wrong enabled state, indefinitely. A
            # button nobody can see has nothing to animate anyway.
            nbicons.set_image(btn._img, name, 18, target)
            btn._nav_on = on
            return
        btn._nav_on = on
        start = self._NAV_OFF if on else self._NAV_ON
        steps = self._NAV_TWEEN_STEPS

        def _frame(v):
            # Quantised, and 1.0 always lands EXACTLY on target: a control left
            # a shade off its end colour reads as a rendering fault.
            q = round(max(0.0, min(1.0, v)) * steps) / steps
            try:
                nbicons.set_image(btn._img, name, 18,
                                  self._mix_hex(start, target, q))
            except Exception:                                     # noqa: BLE001
                pass

        try:
            nbmotion.animate(btn._img, _frame, 0.0, 1.0,
                             duration=nbmotion.FEEDBACK,
                             easing=nbmotion.MOVE,
                             on_done=lambda _ok: _frame(1.0))
        except Exception:                                         # noqa: BLE001
            # A tween that will not start must not cost the button its state.
            nbicons.set_image(btn._img, name, 18, target)

    def _navbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.get_style_context().add_class("navbar")
        self.back_btn = self._icon_btn("back", lambda *_: self.go_back(), False,
                                     tip="Back")
        self.fwd_btn = self._icon_btn("fwd", lambda *_: self.go_forward(), False,
                                    tip="Forward")
        bar.pack_start(self.back_btn, False, False, 0)
        bar.pack_start(self.fwd_btn, False, False, 0)
        bar.pack_start(self._icon_btn("up", lambda *_: self.go_up(),
                                    tip="Up one folder"), False, False, 0)
        # file operations (pointer-driven; no keyboard focus). A thin divider
        # separates navigation from actions.
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.get_style_context().add_class("navsep")
        bar.pack_start(sep, False, False, 6)
        # Copy / Cut / Paste are NOT on the toolbar: three more buttons pushed the
        # bar past the window width, and all three are already available where a
        # file operation is actually chosen — the right-click context menu (see
        # _popup_context_menu) and the Edit menu. What stays here is what has no
        # other home: New Folder and Rename.
        # Kept as attributes because both are meaningless inside the Trash (a
        # new folder there is nonsense, and renaming a trashed item breaks the
        # name-keyed Put Back), so _update_nav hides them there.
        self.folder_btns = []
        for label, cb in (("New Folder", self._new_folder),
                          ("Rename", self._begin_rename)):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("toolbtn")
            b.set_no_show_all(True)
            b.set_visible(True)
            b.connect("clicked", lambda _w, fn=cb: fn())
            bar.pack_start(b, False, False, 0)
            self.folder_btns.append(b)
        # paste_btn was the toolbar's Paste; keep the attribute so the
        # clipboard-state updates that target it stay harmless no-ops.
        self.paste_btn = None
        hidden = Gtk.ToggleButton(label=_t("Hidden"))
        hidden.get_style_context().add_class("toolbtn")
        hidden.set_tooltip_text(_t("Show hidden files"))
        hidden.set_active(self._show_hidden)   # reflect the restored preference
        hidden.connect("toggled", self._on_toggle_hidden)
        bar.pack_start(hidden, False, False, 0)
        # Overflow menu. Installing, removing, and restoring applications live
        # in Packages; Finder keeps only general file inspection here.
        self.actions_btn = Gtk.Button(label=_t("Actions"))
        self.actions_btn.get_style_context().add_class("toolbtn")
        self.actions_btn.set_tooltip_text(_t("More actions"))
        self.actions_btn.connect("clicked", self._popup_actions_menu)
        bar.pack_start(self.actions_btn, False, False, 0)
        self.crumb = Crumbs()
        # The crumb bar is the flexible spacer between the left tools and the
        # right cluster (mockup: flex:1 after the breadcrumb). Packing it to
        # EXPAND — instead of a separate fixed gap widget — means on a narrow
        # panel it yields its slack FIRST, so the search field and the view
        # toggle can never be pushed off the right edge (clipped-elements fix);
        # on a wide panel the pills sit left with empty space trailing, exactly
        # as the mockup. Its children stay left-packed, so nothing stretches.
        # A row of pills has no minimum of its own, though: a deep path
        # ("Home > Documents > Projects > Archive 2025") made the trail alone
        # ~330px wide, which pushed the search field and view switcher off the
        # right edge of a 1024px panel where nothing could reach them. The same
        # horizontal scroller the whole toolbar uses solves it one level down —
        # EXTERNAL draws no scrollbar, and set_trail scrolls to the end so the
        # folder you are actually in stays the pill you can see.
        self._crumbscroll = Gtk.ScrolledWindow()
        self._crumbscroll.set_policy(Gtk.PolicyType.EXTERNAL,
                                     Gtk.PolicyType.NEVER)
        self._crumbscroll.set_propagate_natural_width(True)
        self._crumbscroll.set_propagate_natural_height(True)
        self._crumbscroll.get_style_context().add_class("crumbscroll")
        self._crumbscroll.add(self.crumb)
        _unframe(self._crumbscroll)
        bar.pack_start(self._crumbscroll, True, True, 10)
        # right cluster: search, plus context-sensitive Trash actions. The
        # window takes no keyboard focus (matchbox), so these are pointer-only.
        search = Gtk.SearchEntry(); search.set_placeholder_text(_t("Search"))
        search.set_size_request(150, -1)
        # Our own magnifier, not the icon theme's — see nbicons.style_search_entry.
        nbicons.style_search_entry(search)
        self._search_h = search.connect("search-changed", self._on_search)
        # Esc while searching clears the query and returns to the full listing.
        search.connect("stop-search", lambda *_: search.set_text(""))
        # Enter opens the top result without ever leaving the keyboard, which
        # is the whole point of knowing the name: type "calc", press Enter,
        # Calculator opens — from whatever folder you happened to be in.
        search.connect("activate", self._on_search_activate)
        self.search = search
        # .toolbtn, not .navbtn: navbtn is the padding-free 32px square used for
        # the icon-only arrows, so these two text buttons had their labels
        # pressed right up against their borders.
        self.empty_btn = Gtk.Button(label=_t("Empty Trash"))
        self.empty_btn.get_style_context().add_class("toolbtn")
        self.empty_btn.set_no_show_all(True)
        self.empty_btn.connect("clicked", lambda *_: self._confirm_empty_trash())
        self.restore_btn = Gtk.Button(label=_t("Put Back"))
        self.restore_btn.get_style_context().add_class("toolbtn")
        self.restore_btn.set_no_show_all(True)
        self.restore_btn.set_tooltip_text(_t("Restore selected item to where it came from"))
        self.restore_btn.connect("clicked", lambda *_: self._restore_selected())
        self.trash_btn = self._icon_btn("trash", lambda *_: self._trash_selected(),
                                     tip="Move to Trash")
        self.trash_btn.set_tooltip_text(_t("Move selected item to Trash"))
        self.trash_btn.set_no_show_all(True)
        bar.pack_end(self._viewswitch(), False, False, 0)  # rightmost
        bar.pack_end(search, False, False, 0)          # left of view switch
        bar.pack_end(self.trash_btn, False, False, 0)  # left of search
        bar.pack_end(self.empty_btn, False, False, 0)
        bar.pack_end(self.restore_btn, False, False, 0)
        # (no separate expanding gap: the crumb bar above is the flex spacer)
        return bar

    def _viewswitch(self):
        # list / grid segmented toggle. List is the design's primary view; the
        # active button takes a darker-beige fill (the design language reserves
        # red for row/place selection + alerts, and never uses black chrome).
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.get_style_context().add_class("viewswitch")
        self.view_list_btn = Gtk.Button()
        self.view_list_btn.get_style_context().add_class("viewbtn")
        self.view_list_btn.set_tooltip_text(_t("List view"))
        li = nbicons.image("viewlist", 16, "#1A1916")
        self.view_list_btn._img = li
        self.view_list_btn.add(li)
        self.view_list_btn.get_style_context().add_class("active")  # default
        self.view_list_btn.connect("clicked", lambda *_: self._set_view("list"))
        self.view_grid_btn = Gtk.Button()
        self.view_grid_btn.get_style_context().add_class("viewbtn")
        self.view_grid_btn.set_tooltip_text(_t("Grid view"))
        ge = nbicons.image("viewgrid", 16, "#3A362E")
        self.view_grid_btn._img = ge
        self.view_grid_btn.add(ge)
        self.view_grid_btn.connect("clicked", lambda *_: self._set_view("grid"))
        box.pack_start(self.view_list_btn, False, False, 0)
        box.pack_start(self.view_grid_btn, False, False, 0)
        return box

    def _set_view(self, mode):
        if mode not in ("list", "grid"):
            mode = "list"
        self._view = mode
        for btn, name, on in (
                (self.view_list_btn, "viewlist", mode == "list"),
                (self.view_grid_btn, "viewgrid", mode == "grid")):
            ctx = btn.get_style_context()
            (ctx.add_class if on else ctx.remove_class)("active")
            nbicons.set_image(btn._img, name, 16, "#1A1916" if on else "#3A362E")
        # A deliberate view toggle animates; the initial layout and show_all
        # remaps do not (that would flash the view on every app round-trip).
        self._apply_view(animate=True)
        self._save_prefs()

    def _apply_view(self, animate=False):
        # nbmotion-inventory: finder.list-grid
        # Show the active view's scroller, hide the other. List and grid are
        # different PRESENTATIONS of the same rows (Article C: not a
        # transform), so the incoming one settles IN place (PAGE, arrival)
        # rather than sliding — the honest "crossfade in place". Called on
        # every window show_all too (animate=False there), so a launched-app
        # round trip can't reveal both or animate on a bare remap.
        grid = getattr(self, "_view", "list") == "grid"
        incoming = self._grid_sw if grid else self._list_sw
        outgoing = self._list_sw if grid else self._grid_sw
        outgoing.hide()
        incoming.set_no_show_all(False)
        incoming.show_all()
        if animate and nbmotion is not None:
            nbmotion.fade_to(incoming, 0.0, 0)          # start hidden
            nbmotion.fade_to(incoming, 1.0, nbmotion.PAGE, nbmotion.EASE_OUT)
        elif nbmotion is not None:
            nbmotion.fade_to(incoming, 1.0, 0)          # ensure fully opaque

    def show_all(self):
        super().show_all()
        self._apply_view()

    def _sidebar(self):
        sb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sb.get_style_context().add_class("sidebar")
        # a smaller floor so the window can be resized down; the sidebar
        # still lays out at its natural width whenever there is room.
        sb.set_size_request(190, -1)
        self._sb = sb
        self._mounts_sig = None
        self._ejecting = set()                 # mounts with a flush in flight
        self._fill_sidebar()
        # re-read mounted volumes periodically so a USB stick inserted after
        # launch appears in Devices without restarting the Finder. Recorded so
        # destroy can stop it; dropping any previous one keeps a rebuilt
        # sidebar from leaving two pollers running against the same window.
        self._stop_source("_dev_poll_id")
        self._dev_poll_id = GLib.timeout_add_seconds(5, self._poll_devices)
        return sb

    def _stop_source(self, attr):
        # Remove a recorded repeating source and clear its field. Idempotent:
        # safe on a source that never started, already fired, or was already
        # removed, so destroy can run twice without raising.
        sid = getattr(self, attr, 0)
        if sid:
            try:
                GLib.source_remove(sid)
            except Exception:
                pass
        setattr(self, attr, 0)

    def _cancel_app_flag_monitor(self):
        # Gio keeps a cancelled-or-not monitor alive on its own; without this
        # the monitor (and the window it calls back into) survives destroy.
        mon = getattr(self, "_app_flag_monitor", None)
        if mon is not None:
            try:
                mon.cancel()
            except Exception:
                pass
        self._app_flag_monitor = None

    def _sb_pack(self, row, arriving_now, opening):
        """Pack one Devices row, collecting a Revealer for any row that is
        ARRIVING so the caller can open it after show_all().

        Only a genuinely new volume opens. The whole column is rebuilt on every
        change, so revealing every row would animate rows the person is already
        looking at — a plugged stick would make the entire sidebar restage
        itself, which is the flash this is here to remove, not a fix for it.
        If the Revealer cannot be built the row is packed plainly: a drive that
        is plugged in has to APPEAR, and the motion is decoration on top of
        that, never a condition of it."""
        if arriving_now:
            try:
                rev = Gtk.Revealer()
                rev.set_reveal_child(False)
                rev.add(row)
                self._sb.pack_start(rev, False, False, 0)
                opening.append(rev)
                return
            except Exception:                                     # noqa: BLE001
                pass
        self._sb.pack_start(row, False, False, 0)

    def _fill_sidebar(self, arriving=()):
        """Rebuild the Devices/Places column. `arriving` names mount points that
        were not present at the last fill — a stick just plugged in — and those
        rows OPEN down the column instead of appearing already there."""
        for c in self._sb.get_children():
            self._sb.remove(c)
        self._sb_rows = []                     # rebuilt below by _sb_row
        devs = self._devices()
        self._mounts_sig = tuple(d[2] for d in devs)
        opening = []                  # Revealers to run once the rows are shown
        self._sb.pack_start(self._sb_header("Devices"), False, False, 0)
        for label, icon, rel in devs:
            # removable volumes (mounted under /media) get an Eject button so the
            # user can flush + unmount BEFORE pulling the stick — otherwise a
            # just-copied file can be lost from the write cache.
            if isinstance(rel, str) and rel.startswith("/media/"):
                hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                hb.pack_start(self._sb_row(label, icon, rel), True, True, 0)
                ej = Gtk.Button()
                ej.set_relief(Gtk.ReliefStyle.NONE)
                ej.get_style_context().add_class("sbeject")
                # "finish saving", not "flush": the tooltip says what the
                # button does FOR you, not the name of the write-out it runs.
                ej.set_tooltip_text(
                    _t("Finish writing, then remove the drive safely"))
                ej.add(nbicons.image("eject", 13, "#6E695E"))
                ej.connect("clicked", lambda _b, m=rel: self._eject(m))
                hb.pack_start(ej, False, False, 6)
                self._sb_pack(hb, rel in arriving, opening)
            else:
                self._sb_pack(self._sb_row(label, icon, rel),
                              rel in arriving, opening)
        self._sb.pack_start(self._sb_header("Places"), False, False, 0)
        for label, icon, rel in PLACES:
            self._sb.pack_start(self._sb_row(label, icon, rel), False, False, 0)
        self._sb.show_all()
        # nbmotion-inventory: finder.sidebar-reveal
        # A volume that has just been mounted opens into the column it belongs
        # to (SLIDE_DOWN along the sidebar's own edge, SURFACE_IN). show_all()
        # has to run FIRST: a Revealer whose child was never shown has nothing
        # to slide. Instant-EQUIVALENT — under policy-still nbtransitions.reveal
        # sets the child revealed with no animation — and if a reveal raises,
        # the row is forced visible, because a drive that failed to animate must
        # still be a drive the person can click.
        for rev in opening:
            try:
                nbtransitions.reveal(rev, True,
                                     duration=nbtransitions.SURFACE_IN)
            except Exception:                                     # noqa: BLE001
                try:
                    rev.set_reveal_child(True)
                except Exception:                                 # noqa: BLE001
                    pass

    def _eject(self, mnt):
        # Leave the volume first if we're viewing it (an open cwd makes it busy),
        # then sync (flush the write cache to the device) and unmount. sync is the
        # belt to the mount's -o sync suspenders: it also flushes any driver-side
        # buffering (exfat/ntfs) the mount option doesn't cover.
        #
        # Both of those run for as long as the stick needs — flushing a file that
        # was just copied to a slow USB 2.0 drive is tens of seconds — so they go
        # on a worker thread. Run from the button's own handler they held the main
        # loop for that whole time: the window stopped redrawing and stopped
        # answering the WM, so safely removing a drive (the one operation on this
        # machine that exists to protect the user's files) looked like a crash and
        # invited exactly the yank it is there to prevent. The result comes back
        # through _eject_done on the main loop, where touching GTK is safe.
        if mnt in self._ejecting:
            return                         # already flushing this one; not twice
        if self.abspath(self.rel).startswith(mnt):
            self.load("")
        self._ejecting.add(mnt)
        threading.Thread(target=self._eject_job, args=(mnt,),
                         daemon=True).start()

    def _eject_job(self, mnt):
        # Worker thread: no GTK here, only the two blocking commands.
        try:
            subprocess.run(["sync"], timeout=30)
        except Exception:
            pass
        try:
            r = subprocess.run(["umount", mnt], capture_output=True, timeout=15)
            ok = (r.returncode == 0)
            err = (r.stderr or b"").decode(errors="replace").strip()
        except Exception:
            ok, err = False, ""
        GLib.idle_add(self._eject_done, mnt, ok, err)

    def _eject_done(self, mnt, ok, err):
        self._ejecting.discard(mnt)
        if ok:
            self._flash_status(_t("Safe to remove the drive"))
        elif "busy" in (err or "").lower():
            # The COMMON eject failure, and the only actionable one: a file or a
            # folder view still open on the volume holds it. Say what to do — a
            # generic "could not be removed" here is honest but tells the user
            # nothing they can act on, which is its own small dishonesty.
            self._flash_status(
                _t("The drive is in use — close open files, then eject."))
        else:
            # umount's stderr can contain errno jargon and absolute device or
            # mount paths.  It is diagnostic output, not safe interface text.
            self._flash_status(_t("The drive could not be removed safely."))
        self._fill_sidebar()
        return False

    def _poll_devices(self):
        if getattr(self, "_closed", False):
            return False
        try:
            sig = tuple(d[2] for d in self._devices())
            if sig != self._mounts_sig:
                # Which volumes are NEW since the last fill: only those open.
                # Worked out here, before _fill_sidebar overwrites _mounts_sig.
                arriving = set(sig) - set(self._mounts_sig or ())
                self._fill_sidebar(arriving=arriving)
        except Exception:
            pass
        if (getattr(self, "rel", None) == "Applications"
                and self._removed_apps_stamp()
                != getattr(self, "_removed_apps_mtime", None)):
            self.load(self.rel, record=False, keep_filter=True)
        return True

    def _devices(self):
        # "Local Disk" is the root filesystem; "Applications" reaches the app
        # launcher (the Finder's default view) now that it is no longer a
        # Places row. Append any real mounted volumes (USB sticks, extra disks)
        # read live from /proc/mounts.
        devs = [("Local Disk", "disk", "/"),
                ("Applications", "packages", "Applications")]
        real_fs = {"ext2", "ext3", "ext4", "vfat", "exfat", "ntfs", "ntfs3",
                   "iso9660", "btrfs", "xfs", "f2fs", "msdos", "udf"}
        try:
            with open("/proc/mounts") as fh:
                for ln in fh:
                    p = ln.split()
                    if len(p) < 3:
                        continue
                    dev, mnt, fstype = _unmount_esc(p[0]), _unmount_esc(p[1]), p[2]
                    if fstype not in real_fs or mnt == "/":
                        continue
                    if mnt.startswith(("/proc", "/sys", "/dev", "/run", "/tmp")):
                        continue
                    devs.append((_volume_name(dev, mnt), "disk", mnt))
        except OSError:
            pass
        return devs

    def _sb_header(self, text):
        l = Gtk.Label(label=text.upper(), xalign=0)
        l.get_style_context().add_class("sbheader")
        return l

    def _sb_row(self, label, icon, rel):
        row = Gtk.Button()
        row.set_relief(Gtk.ReliefStyle.NONE)
        row.get_style_context().add_class("sbrow")
        if rel == self.rel:
            row.get_style_context().add_class("selected")
        box = Gtk.Box(spacing=12)
        box.pack_start(nbicons.image(icon, 18, "#3A362E"), False, False, 0)
        box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        row.add(box)
        if rel is not None:
            self._sb_rows.append((rel, row))
            row.connect("clicked", lambda *_: self.load(rel))
        return row

    def _update_sidebar(self):
        # keep the highlighted place in step with the folder actually shown
        for rel, row in self._sb_rows:
            ctx = row.get_style_context()
            if rel == self.rel:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")

    def _add_columns(self):
        # name column: icon + text
        col = Gtk.TreeViewColumn("Name")
        icon = Gtk.CellRendererPixbuf(); icon.set_property("xpad", 6)
        icon.set_property("ypad", 5)
        txt = Gtk.CellRendererText()
        txt.set_property("ypad", 5)
        # Compress an over-long name to "…" when the window is narrower than it,
        # instead of clipping it or forcing a horizontal scroll. Needs the column
        # in FIXED sizing so it may shrink below the widest name (GROW_ONLY, the
        # default, never shrinks) while still expanding to fill spare width.
        txt.set_property("ellipsize", Pango.EllipsizeMode.END)
        col.pack_start(icon, False); col.add_attribute(icon, "surface", 0)
        col.pack_start(txt, True); col.add_attribute(txt, "text", 1)
        col.set_cell_data_func(txt, self._name_cell_data)
        col.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        col.set_fixed_width(160)
        col.set_min_width(80)
        col.set_expand(True)
        col.set_resizable(True)
        col.set_sort_column_id(1)                  # Name -> sort by name
        self.tree.append_column(col)
        # inline rename: the Name cell becomes an editable entry on demand (F2 /
        # Rename), commits on Enter. Kept non-editable otherwise so a click just
        # selects (double-click still opens); _begin_rename flips it on.
        txt.connect("edited", self._on_name_edited)
        txt.connect("editing-canceled", lambda *_: self._end_rename_mode())
        txt.connect("editing-started", self._on_edit_started)
        self._name_renderer = txt
        self._name_column = col
        # Kind column (human descriptor, store index 8), between Name and Size
        kr = Gtk.CellRendererText()
        kr.set_property("ypad", 5)
        kc = Gtk.TreeViewColumn("Kind", kr, text=8)
        kc.set_min_width(90)
        kc.set_sort_column_id(8)
        self.tree.append_column(kc)
        # Size sorts by the hidden byte-count (6); Date by hidden mtime (7).
        for i, (title, align, sortid) in enumerate(
                [("Size", 1.0, 6), ("Date Modified", 0.0, 7)], start=2):
            r = Gtk.CellRendererText(); r.set_property("xalign", align)
            r.set_property("ypad", 5)
            if title == "Size":
                # tabular figures: the mono family lines up file sizes on the
                # decimal (design language reserves mono for counters).
                r.set_property("family", "Liberation Mono")
                r.set_property("family-set", True)
            c = Gtk.TreeViewColumn(title, r, text=i)
            c.set_min_width(80)
            c.set_sort_column_id(sortid)
            self.tree.append_column(c)
        # folders group before files for every sort key. The starting order is
        # the one the user last chose (persisted): sorting a folder newest-first
        # is a standing preference, not something to re-ask on every launch.
        for sid in SORT_COLUMNS:
            self.store.set_sort_func(sid, self._sort_dirs_first, sid)
        self.store.set_sort_column_id(
            self._sort_col,
            Gtk.SortType.DESCENDING if self._sort_desc
            else Gtk.SortType.ASCENDING)
        # GTK draws a heavy dark sort wedge on the active column header. It is a
        # column gadget, not a themed CSS node, so it can't be toned down to
        # match the quiet papertone heading (the header TEXT restyles, the wedge
        # does not) and read as a clunky black arrow stranded in the wide Name
        # column. Suppress it: click-to-sort still works, the list just reorders.
        # Re-hidden after every sort change because GTK re-enables the indicator
        # on the column it sorts; the hide is deferred to idle so it lands after
        # GTK's own toggle rather than being overwritten by it.
        self.store.connect("sort-column-changed", self._on_sort_changed)
        self._hide_sort_indicators()

    def _on_sort_changed(self, *_):
        GLib.idle_add(self._hide_sort_indicators)
        col, order = self.store.get_sort_column_id()
        if col in SORT_COLUMNS:
            self._sort_col = col
            self._sort_desc = (order == Gtk.SortType.DESCENDING)
            self._save_prefs()

    def _hide_sort_indicators(self):
        for c in self.tree.get_columns():
            c.set_sort_indicator(False)
        return False

    def _sort_dirs_first(self, model, a, b, col):
        da, db = model.get_value(a, 5), model.get_value(b, 5)  # is_dir
        if da != db:
            first = -1 if da else 1
            # GTK negates the whole comparator result for a DESCENDING sort,
            # which would flip folders to the bottom. Pre-negate so folders stay
            # grouped first in both directions.
            _c, order = model.get_sort_column_id()
            if order == Gtk.SortType.DESCENDING:
                first = -first
            return first
        va, vb = model.get_value(a, col), model.get_value(b, col)
        if col == 1:
            # Name: case-insensitive, and sorted by what is ON SCREEN. The
            # store holds the on-disk name, so sorting on that put a Spanish
            # Applications folder in English alphabetical order — Ajustes
            # under S, Calculadora under C only by coincidence — which reads
            # as a list in no order at all.
            va = display_name(va, model.get_value(a, 4)).lower()
            vb = display_name(vb, model.get_value(b, 4)).lower()
        return (va > vb) - (va < vb)

    # ---- data ----
    def abspath(self, rel):
        # rel is either HOME-relative (e.g. "Documents") or, for whole-disk
        # browsing, an absolute path (starts with "/").
        if not rel:
            return HOME
        if rel.startswith("/"):
            return os.path.normpath(rel)
        return os.path.normpath(os.path.join(HOME, rel))

    # ---- Back/Forward slide (finder.navigate-back / finder.navigate-forward) --
    def _snapshot_content(self):
        """The content area as an ImageSurface for the OUTGOING half of a
        navigation slide, or None if it cannot be captured (not mapped yet, no
        cairo, zero size). Captured BEFORE the store is repopulated, so it holds
        the listing being navigated away from. Any failure returns None, which
        the caller reads as 'no slide' -- the current instant swap still runs."""
        w = getattr(self, "_content_overlay", None)
        if w is None:
            return None
        try:
            if not w.get_mapped():
                return None
            aw = int(w.get_allocated_width())
            ah = int(w.get_allocated_height())
            if aw <= 0 or ah <= 0:
                return None
            import cairo
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, aw, ah)
            w.draw(cairo.Context(surface))
            return surface, aw, ah
        except Exception:                                             # noqa: BLE001
            return None

    def _nav_draw(self, _da, cr):
        """Paint the outgoing snapshot at its current slide offset, over the new
        content the overlay has already drawn beneath it."""
        # getattr, not self._nav_slide: a draw can in principle reach the layer
        # before __init__ finished setting the slide state (finder deliberately
        # tolerates a teardown mid-construction), and a crash in a draw handler
        # is a defect, not a glitch.
        s = getattr(self, "_nav_slide", None)
        if not s:
            return False
        surface, w, _h, sign = s
        try:
            cr.set_source_surface(surface, sign * self._nav_v * w, 0)
            cr.paint()
        except Exception:                                             # noqa: BLE001
            pass
        return False

    def _start_nav_slide(self, snap, sign):
        """Slide the captured snapshot off by `sign` (+1 = right, for Back;
        -1 = left, for Forward), revealing the new listing beneath. A newer
        navigation supersedes this one through the generation counter. Instant-
        equivalent: policy-still lands nav_v at 1 and hides the layer at once."""
        surface, w, h = snap
        self._nav_gen += 1
        gen = self._nav_gen
        self._nav_slide = (surface, w, h, float(sign))
        self._nav_v = 0.0
        try:
            self._nav_da.show()
        except Exception:                                             # noqa: BLE001
            pass

        def _frame(v):
            if gen != self._nav_gen:
                return                # a newer navigation owns the slide now
            self._nav_v = v
            try:
                if self._nav_da.get_mapped():
                    self._nav_da.queue_draw()
            except Exception:                                         # noqa: BLE001
                pass

        def _done(_ok):
            if gen != self._nav_gen:
                return                # superseded; the newer slide owns cleanup
            self._nav_slide = None
            self._nav_v = 0.0
            try:
                self._nav_da.hide()
            except Exception:                                         # noqa: BLE001
                pass

        nbmotion.animate(self._nav_da, _frame, 0.0, 1.0,
                         duration=nbtransitions.PAGE, easing=nbmotion.MOVE,
                         on_done=_done)

    def load(self, rel, record=True, keep_filter=False):
        # Where the person was in the folder they are leaving, so a Back — or a
        # refresh of this same folder — can put them back on the item they had
        # selected rather than at the top of a re-read listing.
        self._remember_place()
        # A new folder is on screen from here on: every delayed callback posted
        # for the previous one is now stale (see _dirgen).
        self._dirgen.bump()
        # Back/Forward stamp the direction before calling in; anything else is
        # a refresh in place when it names the folder already on screen, and a
        # fresh arrival when it does not.
        direction = self._nav_dir
        if direction == nbtransitions.NONE and rel == getattr(self, "rel", None):
            direction = nbtransitions.CROSSFADE
        self._nav_dir = nbtransitions.NONE     # consumed; the next move sets it
        entering = self._nav_enter
        self._nav_enter = False                # consumed; the next open sets it
        # Capture the outgoing listing BEFORE anything changes it, so a
        # Back/Forward move -- or a step INTO a folder -- can slide it off
        # (finder.navigate-* / finder.open-folder). Stepping in travels the same
        # way as Forward, which makes Back its exact inverse: the listing you
        # opened leaves the way it arrived. Only real directional moves slide,
        # and only when motion is live -- otherwise the snapshot render would be
        # wasted on an instant swap.
        pending_slide = None
        sign = 0.0
        if direction == nbtransitions.BACK:
            sign = 1.0
        elif direction == nbtransitions.FORWARD or entering:
            sign = -1.0
        if sign and nbmotion.policy(nbtransitions.PAGE) > 0:
            _snap = self._snapshot_content()
            if _snap is not None:
                pending_slide = (_snap, sign)
        # a real navigation clears any active search; a search-driven reload
        # keeps it (keep_filter=True).
        if not keep_filter and self._filter:
            self._filter = ""
            self.search.handler_block(self._search_h)
            self.search.set_text("")
            self.search.handler_unblock(self._search_h)
        if not self._filter:
            # no query, no results from elsewhere — and stop any walk of Home
            # that is still running for the query we just dropped.
            self._wide = []
            self._schedule_wide_search()
        self.rel = rel
        if rel == "Applications":
            # Packages owns uninstall/restore; every Applications rebuild reads
            # the shared store before filtering the on-disk app entries.
            self._load_removed_apps()
        full = self.abspath(rel)
        if rel.startswith("/"):
            name = os.path.basename(full) or "Computer"
            parts = [p for p in full.split("/") if p]
            # each pill navigates to that component's absolute path
            trail = [(p, "/" + "/".join(parts[:i + 1]))
                     for i, p in enumerate(parts)]
            self.crumb.set_trail("Computer", trail, self.load)
        else:
            name = display_name(os.path.basename(full)) or "Home"
            # show every path component, not just the leaf (Home > Documents >
            # Projects), so a deep folder's breadcrumb is accurate.
            parts = [p for p in rel.split("/") if p]
            trail = [(display_name(p), "/".join(parts[:i + 1]))
                     for i, p in enumerate(parts)]
            self.crumb.set_trail("Home", trail, self.load)
        self.title.set_text(name.upper())
        entries = []
        try:
            for nm in sorted(os.listdir(full)):
                if nm.startswith(".") and not self._show_hidden:
                    continue
                # Apps the user hid via "Remove from Applications" are dropped
                # from the Applications listing (persisted; restorable). The
                # .app file stays on disk — this only affects what is shown.
                if rel == "Applications" and self._app_is_removed(nm):
                    continue
                # Apps WE have withheld because they are not finished (see
                # HIDDEN_APPS). Unlike the user's list this is not restorable
                # from the UI — it is a statement that the app is not ready.
                if (rel == "Applications" and nm.endswith(".app")
                        and nm[:-4] in HIDDEN_APPS):
                    continue
                # NB: do NOT apply self._filter here — cache the FULL listing so
                # live search can re-filter it in memory (see _populate_store),
                # without re-reading the directory on every keystroke.
                p = os.path.join(full, nm)
                isdir = os.path.isdir(p)
                size_bytes = 0
                mtime = 0.0
                try:
                    st = os.stat(p)
                    mtime = st.st_mtime
                    size_bytes = 0 if isdir else st.st_size
                    size = size_text(nm, isdir, st.st_size)
                    date = _t(time.strftime("%-d %b %Y",
                                            time.localtime(st.st_mtime)))
                except (OSError, ValueError):
                    # ValueError: %-d (glibc no-pad) is rejected by musl/uClibc;
                    # without this it would escape load() and break the listing.
                    size, date = "—", "—"
                ic = "folder" if isdir else self._icon_for(nm)
                # Two sizes of the SAME glyph: compact for the list rows, large
                # for the grid cells. Both are memoized by nbicons, so a folder
                # of many files sharing an icon still renders each size once.
                entries.append((nbicons.surface(ic, LIST_ICON_PX), nm, size, date,
                                os.path.join(rel, nm) if rel else nm,
                                isdir, size_bytes, mtime,
                                self._kind_for(nm, isdir),
                                nbicons.surface(ic, GRID_ICON_PX)))
        except OSError:
            pass
        # Cache the raw (unfiltered) disk listing and free-space figure so a
        # live search can repopulate the store from memory alone.
        self._raw_entries = entries
        self._free = "—"
        try:
            stv = os.statvfs(full)
            # One catalog key, not a translated word glued onto a number:
            # concatenation fixes English word order, and this line reads
            # "quedan 14,7 GB" / "14,7 GB 사용 가능" in the languages that put
            # it the other way round.
            self._free = _t("%s available") % human(
                stv.f_bavail * stv.f_frsize)
        except OSError:
            pass
        self._populate_store()
        # The new listing is in the store now; slide the captured old one off it.
        if pending_slide is not None:
            self._start_nav_slide(*pending_slide)

        if restores_place(direction):
            token = self._dirgen.token()
            GLib.idle_add(self._dirgen.guard(
                lambda: self._restore_place(rel), token))

        if record:
            del self._history[self._hpos + 1:]      # drop forward history
            if not self._history or self._history[-1] != rel:
                self._history.append(rel)
            self._hpos = len(self._history) - 1
        self._update_nav()
        self._watch_dir(full)

    # ---- live directory watch ---------------------------------------------
    # Without this the view is a one-shot snapshot: a file dropped into the
    # shown folder by another app (e.g. a .gba the GBA SDK just exported to
    # Documents) wouldn't appear until the user navigated away and back. Watch
    # the current directory with an inotify-backed monitor and auto-refresh.
    def _watch_dir(self, full):
        old = getattr(self, "_dir_monitor", None)
        if old is not None:
            try:
                old.cancel()
            except Exception:
                pass
            self._dir_monitor = None
        try:
            mon = Gio.File.new_for_path(full).monitor_directory(
                Gio.FileMonitorFlags.NONE, None)
            mon.connect("changed", self._on_dir_changed)
            self._dir_monitor = mon
        except Exception:
            self._dir_monitor = None       # no backend: view stays a snapshot

    def _on_dir_changed(self, _mon, _f, _other, _event):
        # A single copy/save fires several events; coalesce them into one reload.
        if getattr(self, "_dir_reload_id", 0):
            GLib.source_remove(self._dir_reload_id)
        self._dir_reload_token = self._dirgen.token()
        self._dir_reload_id = GLib.timeout_add(
            350, self._dir_reload_fire, self._dir_reload_token)

    def _dir_reload_fire(self, token):
        self._dir_reload_id = 0
        if not self._dirgen.valid(token):
            return False
        if self._rename_active():
            # never yank an in-progress inline rename out from under the user;
            # the rename itself will trigger another change event when it lands.
            self._dir_reload_id = GLib.timeout_add(
                700, self._dir_reload_fire, token)
            return False
        self.load(self.rel, record=False, keep_filter=True)
        return False

    def _active_scroller(self):
        return self._grid_sw if self._view == "grid" else self._list_sw

    def _remember_place(self):
        """Remember this folder by stable item path and scroll fraction."""
        rel = getattr(self, "rel", None)
        if rel is None or not hasattr(self, "store"):
            return
        model, it = self._selected_iter()
        selected = model.get_value(it, 4) if it is not None else None
        fraction = 0.0
        try:
            adj = self._active_scroller().get_vadjustment()
            span = adj.get_upper() - adj.get_page_size() - adj.get_lower()
            if span > 0:
                fraction = (adj.get_value() - adj.get_lower()) / span
        except Exception:
            pass
        self._places[rel] = {"sel": selected,
                             "scroll": nbstate.fraction(fraction)}

    def _restore_place(self, rel):
        """Restore only if `rel` is still the folder on screen."""
        if rel != self.rel:
            return False
        place = self._places.get(rel, {})
        selected = place.get("sel")
        if selected is not None:
            for row in self.store:
                if row[4] == selected:
                    path = row.path
                    if self._view == "grid":
                        self.iconview.select_path(path)
                        self.iconview.scroll_to_path(path, False, 0, 0)
                    else:
                        self.tree.get_selection().select_path(path)
                        self.tree.scroll_to_cell(path, None, False, 0, 0)
                    break
        try:
            adj = self._active_scroller().get_vadjustment()
            span = adj.get_upper() - adj.get_page_size() - adj.get_lower()
            frac = nbstate.fraction(place.get("scroll"), 0.0)
            adj.set_value(adj.get_lower() + span * frac)
        except Exception:
            pass
        return False

    def _on_destroy_navigation(self, *_):
        # The one place that owns teardown. Destroy can arrive more than once
        # (Gtk emits it, and a caller may destroy an already-closed window), so
        # this is idempotent, and _closed is set FIRST: a repeating callback
        # that fires between here and the last source_remove must drop out
        # rather than touch widgets that are being torn down.
        #
        # Gtk also fires destroy on a window whose __init__ RAISED partway
        # through, so any attribute this touches may never have been set. The
        # source-id helpers already read through getattr; _wide_gen, _dirgen and
        # _dir_reload_id are guarded the same way, so a teardown-mid-construction
        # closes cleanly instead of raising AttributeError over a half-built
        # window (finder_lifecycle / finder_poll_lifecycle).
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self._wide_gen = getattr(self, "_wide_gen", 0) + 1
        self._stop_source("_wide_id")
        self._stop_source("_typeahead_id")
        self._stop_source("_dev_poll_id")
        self._stop_source("_app_poll_id")
        self._cancel_app_flag_monitor()
        dirgen = getattr(self, "_dirgen", None)
        if dirgen is not None:
            dirgen.close()
        if getattr(self, "_dir_reload_id", 0):
            try:
                GLib.source_remove(self._dir_reload_id)
            except Exception:
                pass
            self._dir_reload_id = 0

    def _rename_active(self):
        for r in (getattr(self, "_name_renderer", None),
                  getattr(self, "_grid_text_renderer", None)):
            if r is not None and r.get_property("editable"):
                return True
        return False

    def _populate_store(self):
        # (Re)fill the visible model from the cached disk listing, applying the
        # live search filter in MEMORY. Called by load() right after a disk read
        # and by _on_search on every keystroke — the latter path touches no disk
        # (no listdir / os.stat / statvfs), so typing stays smooth on swrast.
        # self.store stays the filtered store (the finder selftests rely on it).
        self.store.clear()
        flt = self._filter
        # Matched against BOTH names an item has (see search_names): an app
        # translated on screen has to be findable by the word that is printed
        # on it, and by the one it is called on disk.
        shown = [e for e in self._raw_entries
                 if not flt or any(flt in n
                                   for n in search_names(e[1], e[4]))]
        for e in shown:
            self.store.append(list(e))
        # Matches from the REST of Home, found by the background scan (see
        # _schedule_wide_search). They sort into the same list as everything
        # else; each one's Name cell says which folder it lives in, so a result
        # from somewhere else is never mistaken for a file in this folder.
        extra = 0
        if flt:
            for p in self._wide:
                # a result can be deleted between the scan and a later refresh;
                # listing a file that is gone is worse than not listing it
                if not os.path.exists(p):
                    continue
                self.store.append(self._entry_for_path(p))
                extra += 1
        self.status.set_text(self._status_text(len(shown) + extra))
        self._update_empty_state(len(shown) + extra)

    def _entry_for_path(self, path):
        """A store row for something found OUTSIDE the current folder.

        Mirrors load()'s per-entry stat, keyed on an absolute path. Column 4
        (the relative path everything else in the Finder opens through) is kept
        Home-relative, so double-clicking a search result opens it exactly as
        if you had walked to its folder yourself."""
        nm = os.path.basename(path)
        isdir = os.path.isdir(path)
        size_bytes, mtime = 0, 0.0
        try:
            st = os.stat(path)
            mtime = st.st_mtime
            size_bytes = 0 if isdir else st.st_size
            size = size_text(nm, isdir, st.st_size)
            # _t around a strftime result is deliberate and is not a mistake:
            # this image has no C locale, so strftime always writes English
            # month names, and nbi18n has a rule for exactly that shape (see
            # its _date_lookup) which turns "15 Jul 2026" into "15 июл 2026".
            # The Date column put its value straight into the store, so every
            # row of every folder carried an English month on a machine that
            # was otherwise fully translated.
            date = _t(time.strftime("%-d %b %Y", time.localtime(st.st_mtime)))
        except (OSError, ValueError):
            size, date = "—", "—"
        ic = "folder" if isdir else self._icon_for(nm)
        try:
            rel = os.path.relpath(path, HOME)
        except ValueError:
            rel = path
        return [nbicons.surface(ic, LIST_ICON_PX), nm, size, date, rel,
                isdir, size_bytes, mtime, self._kind_for(nm, isdir),
                nbicons.surface(ic, GRID_ICON_PX)]

    # ---- whole-Home search -------------------------------------------------
    def _schedule_wide_search(self):
        """Queue the whole-Home scan that runs behind the in-folder filter.

        Debounced, so a typist starts ONE walk of the disk instead of one per
        keystroke, and generation-stamped, so a scan that finishes after the
        query has moved on is thrown away rather than repopulating the list
        with answers to a question nobody is asking any more."""
        if self._wide_id:
            GLib.source_remove(self._wide_id)
            self._wide_id = 0
        self._wide_gen += 1
        self._searching = False
        self._wide_capped = False
        if len(self._filter) < SEARCH_MIN_CHARS:
            return
        self._wide_id = GLib.timeout_add(
            SEARCH_DEBOUNCE_MS, self._fire_wide_search,
            self._filter, self._wide_gen)

    def _fire_wide_search(self, query, gen):
        self._wide_id = 0
        if getattr(self, "_closed", False) or gen != self._wide_gen:
            return False
        self._searching = True
        self.status.set_text(self._status_text(len(self.store)))
        threading.Thread(target=self._wide_scan, args=(query, gen),
                         daemon=True).start()
        return False

    def _wide_scan(self, query, gen):
        # Worker thread: walk Home for names containing the query. Touches no
        # GTK at all — the hits go back to the main loop via idle_add.
        hits = []
        scanned = 0
        capped = False
        try:
            for root, dirs, files in os.walk(HOME):
                # Dotted folders are skipped: the Trash holds what the user
                # threw away and .config is plumbing — neither is what "find my
                # file" means.
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for nm in dirs + sorted(files):
                    scanned += 1
                    if nm.startswith("."):
                        continue
                    if query in nm.lower():
                        hits.append(os.path.join(root, nm))
                        if len(hits) >= SEARCH_MAX_HITS:
                            capped = True
                            raise StopIteration
                if scanned > SEARCH_MAX_SCAN:
                    capped = True
                    break
        except StopIteration:
            pass
        except OSError:
            pass
        GLib.idle_add(self._wide_done, query, gen, hits, capped)

    def _wide_done(self, query, gen, paths, capped=False):
        if (getattr(self, "_closed", False) or gen != self._wide_gen
                or query != self._filter):
            return False                  # the query moved on: drop these
        self._searching = False
        self._wide_capped = capped
        here = self.abspath(self.rel)
        # Anything in the folder we are already showing is covered by the live
        # in-memory filter, so listing it again would double every match.
        self._wide = [p for p in paths if os.path.dirname(p) != here]
        self._populate_store()
        # nbmotion-inventory: finder.search-results
        # The whole-Home results arrive a beat AFTER the in-folder matches the
        # user is already looking at; settle them IN (SURFACE_IN arrival) rather
        # than letting the list silently grow longer, so "the rest of Home"
        # visibly lands beneath what was already found. A fade on the active
        # view's opacity — the same primitive list<->grid uses — and only when
        # the scan actually added something. Instant-equivalent under policy-
        # still (software / Reduced Motion), so the software path just shows the
        # longer list with no flicker.
        if self._wide:
            self._settle_search_results()
        return False

    def _settle_search_results(self):
        """Fade the active view in so async whole-Home results settle beneath
        the in-folder matches. Opacity only (never a layout property, F2), and
        it lands at 1.0 synchronously when motion is still, so the list is never
        left dimmed."""
        if nbmotion is None:
            return
        grid = getattr(self, "_view", "list") == "grid"
        view = self._grid_sw if grid else self._list_sw
        if view is None:
            return
        nbmotion.fade_to(view, 0.0, 0)
        nbmotion.fade_to(view, 1.0, nbmotion.SURFACE_IN, nbmotion.EASE_OUT)

    def _status_text(self, n):
        if self._filter:
            # A search reads as matches, not as "items in this folder"; and
            # while the rest of Home is still being walked it says so, so an
            # early "1 match" is never mistaken for the final answer.
            if getattr(self, "_wide_capped", False):
                # a very common word can match thousands; the scan stops at a
                # sane number, so the count must not claim to be all of them
                txt = _t("Showing the first %d matches") % n
            else:
                txt = _t("%d match%s") % (n, "" if n == 1 else "es")
            if self._searching:
                txt += " · " + _t("looking through the rest of Home…")
            return txt
        # "1 items" is the kind of detail that makes software feel unfinished.
        txt = _t("%d item%s") % (n, "" if n == 1 else "s")
        # The free-space figure is best-effort (statvfs can fail on a folder
        # that has just been removed, say). When we don't have it, say nothing
        # rather than trailing the count with a bare em dash, which reads as
        # something having gone wrong.
        return txt if self._free == "—" else "%s · %s" % (txt, self._free)

    def _update_empty_state(self, count):
        # nbmotion-inventory: finder.empty-populated
        # Show a friendly centered message when the view has nothing in it,
        # distinguishing an empty folder from a search that matched nothing —
        # a blank list otherwise reads as "the app broke" to a novice. The
        # message SETTLES in when the view empties and DEPARTS when it populates
        # -- a fade on the label's opacity (the same nbmotion.fade_to primitive
        # list<->grid and search-results use) rather than blinking. Only on the
        # empty<->populated BOUNDARY: a populate that stays empty (a search
        # narrowing to nothing) just rewrites the text, it never re-fades.
        lbl = getattr(self, "_empty_label", None)
        if lbl is None:
            return
        was_empty = lbl.get_visible()
        if count:
            if was_empty and nbmotion is not None:
                # content settles in; the message departs, then hides
                def _hide(_ok):
                    lbl.hide()
                    lbl.set_opacity(1.0)          # reset for the next appearance
                nbmotion.fade_to(lbl, 0.0, nbmotion.SURFACE_OUT,
                                 nbmotion.EASE_IN, _hide)
            else:
                lbl.hide()
            return
        # Same voice as the shared file picker's empty states (de/nbpicker.py),
        # so the two read as one product rather than two.
        query = self.search.get_text().strip() if hasattr(self, "search") else ""
        if self._filter and query:
            if self._searching:
                lbl.set_text(_t("Looking for “%s”…") % query)
            elif len(self._filter) >= SEARCH_MIN_CHARS:
                # the whole of Home was searched, not just this folder — say so,
                # or the user goes hunting through folders we already read.
                lbl.set_text(_t("Nothing in Home is called “%s”.") % query)
            else:
                lbl.set_text(_t("Nothing here matches “%s”.") % query)
        elif self.rel == ".Trash":
            # the Trash is a place, not a folder — say so in its own words
            lbl.set_text(_t("The Trash is empty."))
        else:
            lbl.set_text(_t("This folder is empty."))
        if not was_empty and nbmotion is not None:
            # the message settles IN: start hidden, then fade up to full opacity
            lbl.set_opacity(0.0)
            lbl.show()
            nbmotion.fade_to(lbl, 0.0, 0)
            nbmotion.fade_to(lbl, 1.0, nbmotion.SURFACE_IN, nbmotion.EASE_OUT)
        else:
            lbl.show()

    def _update_nav(self):
        self._set_nav(self.back_btn, "back", self._hpos > 0)
        self._set_nav(self.fwd_btn, "fwd", self._hpos < len(self._history) - 1)
        in_trash = (self.rel == ".Trash")
        in_apps = (self.rel == "Applications")
        self.trash_btn.set_visible(not in_trash and not in_apps)
        self.empty_btn.set_visible(in_trash)
        self.restore_btn.set_visible(in_trash)
        # New Folder / Rename make no sense in the Trash — and dropping them
        # there is also what keeps the Trash toolbar (which gains Put Back and
        # Empty Trash) inside a 1024px panel instead of pushing the view
        # switcher off the right edge where it cannot be clicked.
        for b in getattr(self, "folder_btns", ()):
            b.set_visible(not in_trash and not in_apps)
        self._update_sidebar()
        # request a redraw so the new folder actually shows: on real hardware
        # this repaints immediately; under the emulator it may wait for an
        # expose, but it is never wrong to ask.
        if self.get_window() is not None:
            self.queue_draw()

    def go_back(self):
        if self._hpos > 0:
            old = self._hpos
            self._hpos -= 1
            self._nav_dir = nav_direction(old, self._hpos)
            self.load(self._history[self._hpos], record=False)

    def go_forward(self):
        if self._hpos < len(self._history) - 1:
            old = self._hpos
            self._hpos += 1
            self._nav_dir = nav_direction(old, self._hpos)
            self.load(self._history[self._hpos], record=False)

    def _on_search(self, entry):
        query = entry.get_text().strip().lower()
        if query != self._filter:
            self._wide = []       # those hits answered the previous question
        self._filter = query
        # Hot path (per keystroke): re-filter the CACHED listing in memory and
        # repopulate the (still-filtered) store. No load() — no disk re-scan,
        # no os.stat, no icon re-render. A real navigation (load) refreshes the
        # cache from disk and clears the filter.
        self._populate_store()
        # ...and, a beat behind, look through the rest of Home too.
        self._schedule_wide_search()

    def _on_search_activate(self, _entry):
        # Enter in the search box opens what was found: the row the user
        # picked if they picked one, otherwise the first result.
        _model, it = self._selected_iter()
        if it is not None:
            self._open_path(self.store.get_path(it))
        elif len(self.store):
            self._open_path(Gtk.TreePath.new_first())

    def _open_selected(self):
        _model, it = self._selected_iter()
        if it is not None:
            self._open_path(self.store.get_path(it))
            return True
        return False

    def _on_toggle_hidden(self, btn):
        self._show_hidden = btn.get_active()
        self._save_prefs()
        self.load(self.rel, record=False, keep_filter=True)

    # ---- trash ----
    def _trash_dir(self):
        d = os.path.join(HOME, ".Trash")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        return d

    def _trash_selected(self):
        if self.rel == "Applications":
            self._flash_status(_t("Applications are managed in Packages."))
            return
        model, it = self._selected_iter()
        if it is None:
            self._flash_status(_t("Select an item to move to Trash"))
            return
        src = self.abspath(model.get_value(it, 4))
        # lexists on both sides: a dangling symlink is a real directory entry.
        # exists() answers about the link's TARGET, so a link whose target had
        # gone was declared "no longer exists" and could not be thrown away —
        # the row stayed on screen and Move to Trash did nothing — while a
        # dangling link already sitting in the Trash read as a free name and
        # was silently overwritten by the item being trashed onto it.
        if not os.path.lexists(src):
            self.load(self.rel, record=False, keep_filter=True)
            self._flash_status(_t("That item no longer exists"))
            return
        base = os.path.basename(src)
        dst = os.path.join(self._trash_dir(), base)
        n = 1
        while os.path.lexists(dst):
            dst = os.path.join(self._trash_dir(), "%s (%d)" % (base, n))
            n += 1
        # Record where the item belongs BEFORE moving it.  Moving first left a
        # crash window (and an ordinary ENOSPC/read-only failure) where the
        # item was safely in Trash but its only Put Back destination had never
        # been written.  In that state Finder silently restored it to Home.
        # A small orphaned record is harmless and removable; a moved item with
        # no record has lost information, so the transaction is ordered this
        # way around.
        origin_file = self._record_origin(os.path.basename(dst), src)
        if not origin_file:
            self._flash_status(_t("Could not move '%s' to Trash") % base)
            return
        # The Trash lives under $NB_HOME, so throwing away something on a USB
        # stick or a second disk is a real copy followed by a delete — the
        # kernel answers a rename across filesystems with EXDEV. Without this
        # branch that EXDEV fell into the `except OSError` below, so Move to
        # Trash was offered on every row of every stick and could NEVER
        # succeed on one: it always answered "Could not move ... to Trash".
        # Paste's cut already owns the cross-disk machinery — staged copy,
        # progress card, Cancel, identity-checked removal — so this hands the
        # work to it rather than growing a second implementation beside it.
        if not self._same_filesystem(src, os.path.dirname(dst)):
            self._trash_across(src, dst, base, origin_file)
            return
        try:
            self._rename_noreplace(src, dst)
        except OSError:
            try:
                os.remove(origin_file)
            except OSError:
                pass
            self._flash_status(_t("Could not move '%s' to Trash") % base)
            return
        self._set_undo_move(_t("Move to Trash"), dst, src, origin_file)
        self.load(self.rel, record=False, keep_filter=True)
        self._flash_undoable(_t("Moved “%s” to the Trash") % display_name(base))

    def _trash_across(self, src, dst, base, origin_file):
        """Move to Trash when the Trash is on a different filesystem.

        Same shape as Paste's cross-disk cut, and the same reasoning: the
        original is removed only AFTER the copy is safely written, and only if
        it is still the same entry. A long copy off a stick is exactly the
        window in which the original can be replaced by something else, and
        removing by name at the end would throw away an item the user never
        selected while the status line said it had been trashed.

        No one-step Undo, again matching Paste: putting it back would be
        another copy of the same length. The recourse is Put Back, which is
        why the origin record is written before any of this starts.
        """
        identity = self._path_identity(src)

        def done(ok):
            if not ok:
                # Nothing landed in the Trash, so the origin record it would
                # have belonged to is removed too; leaving it would make a
                # later item of the same name Put Back to the wrong folder.
                try:
                    os.remove(origin_file)
                except OSError:
                    pass
                self.load(self.rel, record=False, keep_filter=True)
                return
            gone = False
            if identity is not None and self._path_identity(src) == identity:
                try:
                    self._undo_remove(src, identity)
                except (_UndoStale, OSError, shutil.Error):
                    pass
                gone = not os.path.lexists(src)
            self.load(self.rel, record=False, keep_filter=True)
            if gone:
                self._flash_status(
                    _t("Moved “%s” to the Trash") % display_name(base))
            else:
                # The copy IS in the Trash and is kept — it is now the safe
                # copy. Say plainly that the original is still on the disk, so
                # nobody assumes the stick has been tidied.
                self._flash_status(
                    _t("Copied “%s” to the Trash, but the original could not "
                       "be removed.") % display_name(base))

        self._copy(src, dst, done)

    # ---- undo (one step, for the actions that move or unmake something) ----
    def _set_undo(self, label, fn, *args):
        """Remember how to put the last action back. One step is deliberate:
        it covers the stray click or the wrong row, which is what actually
        happens, without pretending to a history nothing else in the OS keeps."""
        self._undo = {"label": label, "fn": fn, "args": args}

    def _set_undo_remove(self, label, path):
        """Remember how to take back something this action just CREATED, bound
        to the exact directory entry it created rather than to its name. A
        pathname is not an identity: between the action and Ctrl+Z the new
        folder or pasted copy can be deleted and something else — a real
        document, a folder, a link — can take the name. Undoing by name then
        destroys work nobody asked to lose, and says "Undone"."""
        self._set_undo(label, self._undo_remove, path,
                       self._path_identity(path))

    def _set_undo_move(self, label, src, dst, origin_file=None):
        """Remember how to put a moved/trashed/renamed item back, bound to the
        item itself. The same replacement race applies to the source: undoing
        by name alone would drag whatever now sits in the Trash (or under the
        new name) into the original location."""
        self._set_undo(label, self._undo_move, src, dst, origin_file,
                       self._path_identity(src))

    def _undo_move(self, src, dst, origin_file=None, identity=None):
        # put a moved/trashed item back where it came from
        if identity is not None and self._path_identity(src) != identity:
            raise _UndoStale(src)
        # lexists on both sides: a link is an item. Testing exists() refused to
        # restore a symlink whose target had gone, and treated a dangling link
        # sitting in the destination as free space to move onto.
        if os.path.lexists(dst) or not os.path.lexists(src):
            raise OSError("gone")
        os.makedirs(os.path.dirname(dst) or HOME, exist_ok=True)
        self._rename_noreplace(src, dst)
        if origin_file:
            try:
                os.remove(origin_file)
            except OSError:
                pass

    def _undo_remove(self, path, identity=None):
        """Take back something the last action CREATED (a duplicate, a pasted
        copy, a new folder). Only ever removes what we just made.

        When an identity was recorded (every Undo goes through
        `_set_undo_remove`), the entry standing at this pathname must still be
        that exact inode of that exact kind; anything else is refused untouched.
        A symlink is removed as itself — `os.path.isdir` answers about the
        link's TARGET, so following it would walk out of the folder the action
        touched and empty a live directory somewhere else.
        """
        if identity is not None and self._path_identity(path) != identity:
            raise _UndoStale(path)
        if os.path.islink(path) or not os.path.isdir(path):
            if os.path.lexists(path):
                os.remove(path)
        elif os.path.exists(path):
            shutil.rmtree(path)

    def _do_undo(self):
        u = self._undo
        if not u:
            self._flash_status(_t("There is nothing to undo"))
            return
        self._undo = None
        try:
            u["fn"](*u["args"])
        except _UndoStale:
            # Refused, not failed, and nothing was touched. Say which it was:
            # "Could not undo that" would read as a glitch worth retrying.
            self._flash_status(_t("That item changed. Nothing was undone."))
            self.load(self.rel, record=False, keep_filter=True)
            return
        except (OSError, shutil.Error):
            self._flash_status(_t("Could not undo that"))
            self.load(self.rel, record=False, keep_filter=True)
            return
        self.load(self.rel, record=False, keep_filter=True)
        self._flash_status(_t("Undone: %s") % u["label"])

    def _flash_undoable(self, msg):
        # Say what happened AND how to take it back. The status bar is where
        # the user is already looking after acting on a file, which makes it
        # the one place Ctrl+Z can be taught without a manual.
        self._flash_status("%s · %s" % (msg, _t("Ctrl+Z to undo")), 4000)

    # -- permanent deletion, offered only inside the Trash --
    def _confirm_delete_forever(self):
        model, it = self._selected_iter()
        if it is None:
            self._flash_status(_t("Select an item to delete"))
            return
        name = model.get_value(it, 1)
        path = self.abspath(model.get_value(it, 4))
        if not os.path.lexists(path):
            self.load(self.rel, record=False, keep_filter=True)
            return
        identity = self._path_identity(path)
        if identity is None:
            self.load(self.rel, record=False, keep_filter=True)
            return
        # Name the thing being destroyed. "Are you sure?" over an unnamed item
        # is how people delete the wrong file.
        self._confirm(
            _t("Delete Immediately"),
            _t("Permanently erase “%s”? This cannot be undone.")
            % display_name(name),
            _t("Delete"),
            lambda: self._delete_forever(path, name, identity),
            anchor=self._selected_row_anchor())

    @staticmethod
    def _path_identity(path):
        """Stable identity of the exact directory entry being confirmed."""
        try:
            st = os.lstat(path)
            return st.st_dev, st.st_ino, stat.S_IFMT(st.st_mode)
        except OSError:
            return None

    @staticmethod
    def _purge_entry(path):
        """Erase one directory entry for good. Returns True once it is gone.

        A symlink is removed as itself: `os.path.isdir` answers about the
        link's TARGET, so testing it first walked a link in the Trash into a
        live folder somewhere else and deleted that folder's contents. Success
        is decided by looking at the disk afterwards, not by the absence of an
        exception, so a directory that only half-emptied reports honestly.
        """
        try:
            if os.path.islink(path) or not os.path.isdir(path):
                os.remove(path)
            else:
                shutil.rmtree(path)
        except (OSError, shutil.Error):
            pass
        return not os.path.lexists(path)

    def _delete_forever(self, path, name, expected_identity=None):
        # A confirmation names one concrete item, not whatever later happens
        # to occupy the same pathname. Revalidate at the last possible moment
        # so a refresh, external process, or remove/recreate race cannot turn
        # "delete A" into "delete B" while the card is open.
        if (expected_identity is not None
                and self._path_identity(path) != expected_identity):
            self._flash_status(
                _t("That item changed. Nothing was deleted."))
            self.load(self.rel, record=False, keep_filter=True)
            return
        if not self._purge_entry(path):
            self._flash_status(_t("Could not delete “%s”") % display_name(name))
            return
        try:
            os.remove(os.path.join(self._origins_dir(), name))
        except OSError:
            pass
        self._undo = None            # nothing can bring this back; don't imply it
        self.load(self.rel, record=False, keep_filter=True)

    # -- trash origin tracking, so items can be Put Back --
    def _origins_dir(self):
        d = os.path.join(self._trash_dir(), ".origins")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        return d

    def _record_origin(self, trashed_base, original):
        path = os.path.join(self._origins_dir(), trashed_base)
        try:
            # The sidecar is part of the Trash transaction, not disposable
            # cache: without it Put Back cannot return to the right folder.
            nbapp.atomic_write_text(path, original)
            return path
        except (OSError, UnicodeError):
            return None

    def _restore_selected(self):
        model, it = self._selected_iter()
        if it is None:
            self._flash_status(_t("Select an item to put back"))
            return
        name = model.get_value(it, 1)
        src = os.path.join(self._trash_dir(), name)
        # lexists: the entry to put back is the link itself, not what it points
        # at. exists() made Put Back return in silence for a dangling link —
        # the row was listed, the command was offered, and nothing happened.
        if not os.path.lexists(src):
            # The row is stale — the entry went away behind our back. Say so
            # and redraw: a Put Back that silently does nothing is a dead
            # control, and the user is left staring at a row that is gone.
            self.load(self.rel, record=False, keep_filter=True)
            self._flash_status(_t("That item no longer exists"))
            return
        origin_file = os.path.join(self._origins_dir(), name)
        dest = ""
        try:
            with open(origin_file) as fh:
                dest = fh.read().strip()
        except OSError:
            dest = ""
        if not dest:
            dest = os.path.join(HOME, name)        # fallback: restore to Home
        parent = os.path.dirname(dest)
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            dest = os.path.join(HOME, name)
            parent = HOME
        if self._taken(dest):
            dest = self._unique_path(parent, os.path.basename(dest), suffix=" copy")
        # An item trashed off a stick came here by copy, and it goes home the
        # same way. Before Move to Trash gained its cross-disk branch nothing
        # could reach the Trash from another filesystem, so this case did not
        # arise; adding that branch without this one would have moved the dead
        # end rather than removed it — the item would be throwable away and
        # then unputbackable.
        if not self._same_filesystem(src, parent):
            self._restore_across(src, dest, name, origin_file)
            return
        try:
            self._rename_noreplace(src, dest)
        except FileExistsError:
            self._flash_status(
                _t("An item named '%s' already exists")
                % os.path.basename(dest))
            self.load(self.rel, record=False, keep_filter=True)
            return
        except OSError:
            self._flash_status(_t("Could not put that back"))
            self.load(self.rel, record=False, keep_filter=True)
            return
        try:
            os.remove(origin_file)
        except OSError:
            pass
        self.load(self.rel, record=False, keep_filter=True)
        # Every other file command reports itself. Put Back moves an item to a
        # folder the user is not looking at, so silence is the one case where
        # confirmation matters most: the row leaves the Trash and nothing says
        # where it went.
        self._flash_status(_t("Put back “%s”") % display_name(name))

    def _restore_across(self, src, dest, name, origin_file):
        """Put Back when the item's home folder is on a different filesystem.

        The mirror of _trash_across, and ordered the same way round: the copy
        lands at the destination first, and only then is the Trash copy
        removed. If the removal does not happen the item exists twice, which
        is recoverable; the reverse order would risk it existing nowhere.
        """
        identity = self._path_identity(src)

        def done(ok):
            if not ok:
                self.load(self.rel, record=False, keep_filter=True)
                return
            gone = False
            if identity is not None and self._path_identity(src) == identity:
                try:
                    self._undo_remove(src, identity)
                except (_UndoStale, OSError, shutil.Error):
                    pass
                gone = not os.path.lexists(src)
            if gone:
                try:
                    os.remove(origin_file)
                except OSError:
                    pass
            self.load(self.rel, record=False, keep_filter=True)
            if gone:
                self._flash_status(_t("Put back “%s”") % display_name(name))
            else:
                self._flash_status(
                    _t("Put back “%s”, but the copy in the Trash could not be "
                       "removed.") % display_name(name))

        self._copy(src, dest, done)

    def _trash_snapshot(self):
        """The Trash exactly as it is right now: (name, identity) per entry.

        This is what a confirmation names. Identity is the lstat triple, so an
        entry replaced under the same name afterwards is a different item and
        is recognisable as one.
        """
        trash = self._trash_dir()
        try:
            names = sorted(n for n in os.listdir(trash) if n != ".origins")
        except OSError:
            return []
        snapshot = []
        for nm in names:
            identity = self._path_identity(os.path.join(trash, nm))
            if identity is not None:
                snapshot.append((nm, identity))
        return snapshot

    def _empty_trash(self, captured=None):
        # Erase the entries the confirmation actually listed, and nothing else.
        # Re-reading the directory here instead would destroy whatever arrived
        # while the card was on screen — items nobody was ever shown, let alone
        # agreed to lose.
        if captured is None:
            captured = self._trash_snapshot()
        trash = self._trash_dir()
        origins = self._origins_dir()
        done = kept = failed = 0
        for nm, identity in captured:
            path = os.path.join(trash, nm)
            current = self._path_identity(path)
            if current is None:            # already gone; nothing left to erase
                continue
            if current != identity:        # a different item now wears the name
                kept += 1
                continue
            if not self._purge_entry(path):
                failed += 1
                continue                   # its Put Back record still applies
            done += 1
            try:
                os.remove(os.path.join(origins, nm))
            except OSError:
                pass
        if kept or failed:
            self._flash_status(self._empty_report(done, kept, failed), 4000)
        self.load(self.rel, record=False, keep_filter=True)

    @staticmethod
    def _empty_report(done, kept, failed):
        # Say what is still in the Trash. Reporting a clean sweep that did not
        # happen is how someone finds their file gone a week later.
        parts = [_t("Emptied %d item%s.") % (done, "" if done == 1 else "s")]
        if kept:
            parts.append(_t("%d newer item%s stayed in the Trash.")
                         % (kept, "" if kept == 1 else "s"))
        if failed:
            parts.append(_t("%d item%s could not be deleted.")
                         % (failed, "" if failed == 1 else "s"))
        return " ".join(parts)

    def _confirm_empty_trash(self):
        # Emptying the Trash erases its contents for good, so confirm first. The
        # actual purge stays in _empty_trash (driven by both this confirmation
        # and the headless selftests).
        # Capture the entries here, while they are the ones on screen, and hand
        # that exact list to the purge: what is named is what is erased.
        captured = self._trash_snapshot()
        if not captured:
            self._flash_status(_t("Trash is already empty"))
            return
        items = [nm for nm, _identity in captured]
        n = len(items)
        # Name what is about to be destroyed. A bare count is not enough to
        # decide by: "3 items" could be junk or could be the tax return, and
        # the whole point of a confirmation is to let someone recognise the
        # mistake before it is permanent.
        items = sorted(items, key=lambda s: s.lower())
        listed = ", ".join(display_name(i) for i in items[:3])
        if n > 3:
            listed = _t("%s and %d more") % (listed, n - 3)
        self._confirm(
            _t("Empty Trash"),
            "%s\n\n%s" % (
                _t("Permanently erase %d item%s from the Trash? This cannot "
                   "be undone.") % (n, "" if n == 1 else "s"),
                listed),
            _t("Empty Trash"), lambda: self._empty_trash(captured),
            anchor=nbtransitions.widget_rect(self.empty_btn, self._overlay)
            if nbtransitions is not None else None)

    def _confirm(self, title, message, ok_label, on_yes, anchor=None):
        # nbmotion-inventory: app.confirm
        # A destructive confirmation that GROWS FROM the control that raised
        # it (Article B) via the shared _present_card_from, rather than a
        # modal dialog from nowhere. The primary button is signage-red — an
        # alert, one of the two states the design language reserves red for.
        # SAFETY is unchanged and, if anything, firmer: the safe default
        # (Cancel) takes focus only once the card is on screen (on_shown), so
        # a stray Enter cannot reach the danger button before Cancel exists;
        # Esc and a scrim click both cancel; the danger action fires the
        # SINGLE-SHOT _once guard.
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.get_style_context().add_class("finderinfobox")
        hd = Gtk.Label(label=title, xalign=0)
        hd.get_style_context().add_class("finderinfoname")
        box.pack_start(hd, False, False, 0)
        msg = Gtk.Label(label=message, xalign=0)
        msg.get_style_context().add_class("finderinfoval")
        msg.set_line_wrap(True); msg.set_max_width_chars(38)
        # START so max_width_chars can hold the card to a readable column: a
        # message quoting a long file name would otherwise stretch it (see
        # _show_info_dialog), and WORD_CHAR so that name can break.
        msg.set_halign(Gtk.Align.START)
        msg.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        msg.set_margin_top(10); msg.set_margin_bottom(18)
        box.pack_start(msg, False, False, 0)
        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btnrow.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("finderinfocancel")
        cancel.connect("clicked", lambda *_: self._info_close())
        ok = Gtk.Button(label=ok_label)
        ok.get_style_context().add_class("finderinfodanger")

        def accepted(*_args):
            cancel.set_sensitive(False)
            ok.set_sensitive(False)
            close = self._info_close      # capture before on_yes can reassign
            if close is not None:
                close()                   # retract the card
            on_yes()

        ok.connect("clicked", self._once(accepted))
        btnrow.pack_start(cancel, False, False, 0)
        btnrow.pack_start(ok, False, False, 0)
        box.pack_start(btnrow, False, False, 0)
        box.show_all()
        card = self._present_card_from(
            box, anchor,
            # A stray Enter/Space must choose safety, never irreversible
            # action — and only once the card is actually up.
            on_shown=cancel.grab_focus)
        card.connect("key-press-event", self._info_key)     # Esc cancels

    @staticmethod
    def _once(callback):
        """Return an activation callback that can commit at most once."""
        fired = [False]

        def run(*args):
            if fired[0]:
                return None
            fired[0] = True
            return callback(*args)
        return run

    # ---- file operations (copy / cut / paste / new folder) ----
    def _selected_iter(self):
        # read the selection from whichever view is active, so Copy/Cut/Trash
        # work identically in list and grid mode. (List is the default, so the
        # headless selftests — which drive w.tree — are unaffected.)
        if getattr(self, "_view", "list") == "grid":
            items = self.iconview.get_selected_items()
            return self.store, (self.store.get_iter(items[0]) if items else None)
        return self.tree.get_selection().get_selected()

    def _selected_path(self):
        model, it = self._selected_iter()
        if it is None:
            return None
        return self.abspath(model.get_value(it, 4))

    def _taken(self, path):
        """Is this name already spoken for — by any directory entry, or by a copy that is
        still running? A big copy creates its destination on a worker thread,
        so between the click and the worker's first write the name still looks
        free. A second Paste in that window chose the SAME destination: two
        jobs wrote one file, and cancelling either one deleted the other's
        finished copy while its own status line said "Copied here". lexists is
        deliberate: a dangling symlink still owns its name and must not be
        silently replaced by Rename, Paste, Duplicate, or Put Back."""
        return os.path.lexists(path) or path in self._inflight

    @staticmethod
    def _rename_noreplace(src, dst):
        """Move `src` to the name `dst` in the same folder tree, REFUSING an
        occupied destination instead of quietly consuming what stands there.
        Raises FileExistsError, having touched nothing, if the name is taken.

        Looking at the name first (`_taken`) is necessary but not sufficient:
        the look and the move are two separate moments, and the folder can gain
        that name in between — a download landing, a second Finder window, an
        editor writing its file out. Both of the obvious calls answer an
        occupied name by destroying something. `os.rename` replaces the entry
        outright, and `shutil.move` puts the item INSIDE a directory that
        already has the name, so a file that was meant to sit beside a folder
        disappears into it while the status line says "Moved here" — and the
        Undo recorded afterwards then points at that whole folder.

        So the whole move is one syscall that cannot succeed on a taken name —
        see _renameat2_noreplace. Files, directories, and symlinks all go the
        same way; an entry that is merely a dangling link still owns its name
        and is refused, with neither side touched. If the destination is on
        another filesystem the kernel says EXDEV, which Paste turns into the
        copy-then-delete a cross-disk move really is.
        """
        _renameat2_noreplace(src, dst)

    def _unique_path(self, dest_dir, base, suffix=""):
        """A non-colliding path in dest_dir for `base`, optionally forcing a
        ' copy' style suffix (used by Duplicate / paste-into-same-folder)."""
        stem, ext = os.path.splitext(base)
        cand = os.path.join(dest_dir, base)
        if not suffix and not self._taken(cand):
            return cand
        n = 1
        while True:
            tag = " copy" if n == 1 else " copy %d" % n
            cand = os.path.join(dest_dir, stem + tag + ext)
            if not self._taken(cand):
                return cand
            n += 1

    @staticmethod
    def _stage_symlink(src, stage):
        """Turn the private staging entry into a copy of the symlink `src`.

        islink is asked before isdir, so a link to a live folder copies as a
        link rather than being followed and materialised as a second, real
        folder — and a link whose target is missing copies just the same.

        `stage` was created empty by _copy (a mkstemp file, or a mkdtemp
        directory when src's target is a folder) and is ours alone, so it can
        be taken back out; the removal never follows the entry, so it cannot
        reach into anything the link points at.
        """
        if os.path.isdir(stage) and not os.path.islink(stage):
            os.rmdir(stage)                   # freshly made by mkdtemp: empty
        else:
            os.unlink(stage)
        os.symlink(os.readlink(src), stage)

    def _do_copy(self, src, dst):
        if os.path.islink(src):
            self._stage_symlink(src, dst)
        elif os.path.isdir(src):
            # `dst` is the private staging directory reserved by _copy.
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    # ---- copying, with progress and a working Cancel ----------------------
    # A copy used to run on the main loop, so copying a folder of photos to a
    # USB stick froze the whole desktop for as long as it took, with nothing on
    # screen to say why. Anything over COPY_ASYNC_BYTES now runs on a worker
    # thread behind a progress dialog: the user can watch it, stop it, and if
    # it fails (a full disk, a stick pulled out) the half-copy is cleaned up
    # and the reason is said out loud instead of leaving debris behind.
    def _copy_size(self, src):
        """Bytes the copy will move (bounded walk for a folder)."""
        try:
            # A link is copied as a link: its size is the length of the text it
            # holds, never the size of what it points at. Asked first so a link
            # to a huge folder is not walked, and so it stays under
            # COPY_ASYNC_BYTES — copying one is a single syscall, with nothing
            # for a progress bar to show.
            if os.path.islink(src):
                return os.lstat(src).st_size
            if os.path.isdir(src):
                return self._dir_size(src)[0]
            return os.path.getsize(src)
        except OSError:
            return 0

    def _copy_job(self, src, dst, state):
        # Worker thread. Copies file by file in chunks so progress advances and
        # Cancel is answered promptly even inside one huge file.
        def copy_one(s, d):
            state["file"] = os.path.basename(s)
            with open(s, "rb") as fi, open(d, "wb") as fo:
                while True:
                    if state["cancel"]:
                        raise _CopyCancelled()
                    buf = fi.read(COPY_CHUNK)
                    if not buf:
                        break
                    fo.write(buf)
                    state["done"] += len(buf)
            try:
                shutil.copystat(s, d)
            except OSError:
                pass

        if os.path.islink(src):
            # _copy_size keeps links off this path (a link is always tiny), but
            # the worker must not disagree with the synchronous copy about what
            # a link is: open() here would follow it and write out its target.
            self._stage_symlink(src, dst)
            return
        if os.path.isdir(src):
            os.makedirs(dst, exist_ok=True)
            for root, _dirs, files in os.walk(src):
                rel = os.path.relpath(root, src)
                out = dst if rel == "." else os.path.join(dst, rel)
                os.makedirs(out, exist_ok=True)
                for nm in sorted(files):
                    copy_one(os.path.join(root, nm), os.path.join(out, nm))
        else:
            copy_one(src, dst)

    def _copy_async(self, src, dst, on_done):
        """Copy src -> dst behind a progress dialog. on_done(ok) runs on the
        main loop afterwards, whether it finished, was cancelled or failed."""
        state = {"cancel": False, "done": 0, "total": max(1, self._copy_size(src)),
                 "file": os.path.basename(src), "error": None, "cancelled": False,
                 "finished": False}
        dlg, bar, namelbl = self._progress_dialog(
            _t("Copying"), _t("Copying “%s”") % display_name(
                os.path.basename(src)), state)

        def tick():
            if state["finished"]:
                return False
            frac = min(1.0, state["done"] / float(state["total"]))
            bar.set_fraction(frac)
            namelbl.set_text(state["file"])
            return True

        tick_id = GLib.timeout_add(150, tick)

        def finish():
            state["finished"] = True
            GLib.source_remove(tick_id)
            try:
                dlg.destroy()
            except Exception:
                pass
            ok = not (state["cancelled"] or state["error"])
            if not ok:
                # Leave nothing half-copied behind. dst did not exist before
                # this job, and _copy's claim on the name held for as long as
                # the job ran, so removing it can only remove ours.
                try:
                    self._undo_remove(dst)
                except (OSError, shutil.Error):
                    pass
            on_done(ok)
            # after on_done, which reloads the view and rewrites the status bar
            if not ok:
                self._flash_status(
                    _t("Copy stopped. Nothing was changed.")
                    if state["cancelled"] else state["error"])
            return False

        def work():
            try:
                self._copy_job(src, dst, state)
            except _CopyCancelled:
                state["cancelled"] = True
            except (OSError, shutil.Error) as exc:
                state["error"] = self._copy_error_text(exc)
            GLib.idle_add(finish)

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _copy_error_text(exc):
        # Say what a person can act on. "Errno 28" is not a sentence.
        import errno
        code = getattr(exc, "errno", None)
        if code == errno.ENOSPC:
            return _t("There is not enough room to copy that here")
        if code == errno.EACCES or code == errno.EPERM:
            return _t("There was no permission to copy that here")
        if code == errno.EROFS:
            return _t("That location is read-only")
        return _t("The copy could not be finished")

    def _progress_dialog(self, title, subtitle, state):
        """House-style modal progress card: what is being copied, how far it
        has got, and a Cancel that really stops it."""
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("finderinfo")
        area = dlg.get_content_area(); area.set_spacing(0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.get_style_context().add_class("finderinfobox")
        hd = Gtk.Label(label=title, xalign=0)
        hd.get_style_context().add_class("finderinfoname")
        box.pack_start(hd, False, False, 0)
        sub = Gtk.Label(label=subtitle, xalign=0)
        sub.get_style_context().add_class("finderinfokind")
        sub.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        sub.set_max_width_chars(38)
        sub.set_margin_top(4)
        box.pack_start(sub, False, False, 0)
        bar = Gtk.ProgressBar()
        bar.get_style_context().add_class("finderprogress")
        bar.set_margin_top(16)
        box.pack_start(bar, False, False, 0)
        namelbl = Gtk.Label(label="", xalign=0)
        namelbl.get_style_context().add_class("finderinfokind")
        namelbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        namelbl.set_max_width_chars(38)
        namelbl.set_margin_top(8)
        box.pack_start(namelbl, False, False, 0)
        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btnrow.set_halign(Gtk.Align.END); btnrow.set_margin_top(18)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("finderinfocancel")
        cancel.connect("clicked",
                       lambda *_: state.__setitem__("cancel", True))
        btnrow.pack_start(cancel, False, False, 0)
        box.pack_start(btnrow, False, False, 0)
        area.add(box)
        dlg.connect("key-press-event", lambda _w, ev: (
            state.__setitem__("cancel", True)
            if ev.keyval == Gdk.KEY_Escape else None))
        dlg.show_all()
        return dlg, bar, namelbl

    def _new_folder(self):
        if self.rel == "Applications":
            self._flash_status(_t("Applications are managed in Packages."))
            return
        dest = self.abspath(self.rel)
        base = "untitled folder"
        path = os.path.join(dest, base)
        n = 2
        # _taken, not exists: a name is spoken for by ANY directory entry —
        # including a dangling symlink, which exists() calls free — and by a
        # copy still running to that name. Asking exists() here made New Folder
        # pick a name it could not have, so makedirs failed EEXIST and the
        # person was told the folder could not be created instead of getting
        # "untitled folder 2"; and it could steal the destination a running
        # copy had already claimed, breaking that copy later.
        while self._taken(path):
            path = os.path.join(dest, "%s %d" % (base, n))
            n += 1
        try:
            # makedirs and not a pre-flight check: the look above and the
            # creation are two moments, so the folder can gain that name in
            # between. mkdir cannot succeed on a taken name, so a lost race
            # fails loudly here rather than consuming what stands there.
            os.makedirs(path)
        except OSError:
            self._flash_status(_t("Could not create a folder here"))
            return
        self._set_undo_remove(_t("New Folder"), path)
        # A freshly-made folder never matches an active search query, so
        # keeping the filter would hide it — and repeated clicks would silently
        # pile up invisible "untitled folder 2, 3, …". Clear the filter (the
        # default keep_filter=False) so the new folder shows immediately.
        self.load(self.rel, record=False)
        # select it, scroll it into view, and drop straight into inline rename
        # so the novice can type the folder's name over "untitled folder" — the
        # standard new-folder flow. (Only when we're actually on screen: the
        # headless selftests construct the Finder unmapped and just check the
        # folder was created, so starting an editor there would be pointless.)
        name = os.path.basename(path)
        self._select_name(name)
        if self.get_mapped():
            self._begin_rename()

    def _copy_selected(self):
        # Copy leaves the item where it is, so without a word of feedback the
        # novice can't tell it worked. Confirm it, and the Paste button lights up.
        p = self._selected_path()
        if p:
            self._clipboard = (p, False)
            self._update_paste()
            self._flash_status(_t("Copied '%s'") % os.path.basename(p))
        else:
            self._flash_status(_t("Select an item to copy"))

    def _cut_selected(self):
        if self.rel == "Applications":
            self._flash_status(_t("Applications are managed in Packages."))
            return
        p = self._selected_path()
        if p:
            self._clipboard = (p, True)
            self._update_paste()
            self._flash_status(_t("Cut '%s'. Open a folder, then Paste.")
                               % os.path.basename(p))
        else:
            self._flash_status(_t("Select an item to cut"))

    def _paste(self):
        if self.rel == "Applications":
            self._flash_status(_t("Applications are managed in Packages."))
            return
        if not self._clipboard:
            return
        src, is_cut = self._clipboard
        # lexists: the item on the clipboard is the entry the user selected. A
        # symlink whose target is gone is still that entry, and Paste copies
        # the link itself, so exists() refused a paste it could have made.
        if not os.path.lexists(src):
            self._clipboard = None
            self._flash_status(_t("That item no longer exists"))
            return
        dest_dir = self.abspath(self.rel)
        base = os.path.basename(src)
        dst = os.path.join(dest_dir, base)
        # pasting into the source's own folder (or a name clash) -> " copy"
        same_dir = os.path.dirname(src) == dest_dir
        if self._taken(dst) or (same_dir and not is_cut):
            dst = self._unique_path(dest_dir, base, suffix=" copy")
        if self._recursive_target(src, dst):
            self._flash_status(_t("A folder cannot be copied inside itself"))
            return
        if is_cut and self._same_filesystem(src, dest_dir):
            # same disk: a move is a rename, so it is instant however big it is
            try:
                self._rename_noreplace(src, dst)
            except FileExistsError:
                # The name was free when it was chosen and is not free now.
                # Nothing was moved: keep the cut on the clipboard so the user
                # can paste it somewhere else, and say what actually happened
                # rather than "Moved here" over an item that never moved.
                self._update_paste()
                self.load(self.rel, record=False, keep_filter=True)
                self._flash_status(
                    _t("An item named '%s' already exists")
                    % os.path.basename(dst))
                return
            except (OSError, shutil.Error) as exc:
                if getattr(exc, "errno", None) != errno.EXDEV:
                    self._update_paste()
                    self._flash_status(_t("Could not paste here"))
                    return
                # Not one filesystem after all (_same_filesystem answers
                # "unknown" as "same"): fall through to the copy-then-delete
                # path below, which is what a cross-disk move really is.
            else:
                self._clipboard = None      # cut is one-shot
                self._update_paste()
                self._set_undo_move(_t("Move"), dst, src)
                self.load(self.rel, record=False, keep_filter=True)
                self._flash_undoable(
                    _t("Moved “%s” here") % display_name(base))
                return
        if is_cut:
            # A move to ANOTHER disk — a USB stick, nearly always — is really a
            # full copy followed by a delete, so it costs exactly what a copy
            # costs and gets the same progress card and the same Cancel. It
            # offers no undo: putting it back would be another long copy, and
            # the original is only removed once the new one is safely written.
            self._clipboard = None
            self._update_paste()
            # Remember WHICH entry was cut, not just where it sat. This copy
            # runs for as long as a USB stick takes, and in that time the
            # original can be deleted and a different document, folder, or
            # link can take its name. Removing by name at the end would erase
            # that replacement — something the user never cut — and call it
            # "Moved". The original is removed only if it is still the same
            # entry; anything else is left exactly where it stands.
            origin = self._path_identity(src)

            def moved(ok):
                if not ok:
                    self.load(self.rel, record=False, keep_filter=True)
                    return
                gone = False
                stale = origin is None or self._path_identity(src) != origin
                if not stale:
                    try:
                        self._undo_remove(src, origin)
                    except (_UndoStale, OSError, shutil.Error):
                        pass
                    gone = not os.path.lexists(src)
                self.load(self.rel, record=False, keep_filter=True)
                if gone:
                    self._flash_status(_t("Moved “%s” here")
                                       % display_name(base))
                elif stale:
                    # The copy is finished and kept — it is the only remaining
                    # record of what was cut, so it is not thrown away.
                    self._flash_status(
                        _t("Copied “%s” here. The original changed, so it was "
                           "left alone.") % display_name(base))
                else:
                    self._flash_status(
                        _t("Copied “%s” here, but the original could not be "
                           "removed.") % display_name(base))
            self._copy(src, dst, moved)
            return
        self._update_paste()

        def done(ok):
            if ok:
                self._set_undo_remove(_t("Paste"), dst)
            self.load(self.rel, record=False, keep_filter=True)
            if ok:
                self._flash_undoable(_t("Copied “%s” here") % display_name(base))
        self._copy(src, dst, done)

    @staticmethod
    def _same_filesystem(src, dest_dir):
        """Would a move here be a rename (instant) or a real copy (slow)?
        Unknown counts as the same disk — the cheap path is also the old one."""
        try:
            return os.stat(src).st_dev == os.stat(dest_dir).st_dev
        except OSError:
            return True

    @staticmethod
    def _recursive_target(src, dst):
        """True when a directory destination is itself or below itself."""
        if not os.path.isdir(src) or os.path.islink(src):
            return False
        try:
            source = os.path.realpath(src)
            target = os.path.realpath(dst)
            return os.path.commonpath((source, target)) == source
        except (OSError, ValueError):
            return False

    def _copy(self, src, dst, on_done):
        """Copy, choosing the quiet path or the visible one. A small file is
        instant, so a progress card would only flash; anything big enough to be
        noticed gets one, with Cancel."""
        if self._recursive_target(src, dst):
            on_done(False)
            self._flash_status(_t("A folder cannot be copied inside itself"))
            return
        # Claim the destination for as long as this job owns it. The claim is
        # what makes the cleanup below safe to describe as "only removes ours":
        # while it stands, no other Paste or Duplicate can choose this name.
        self._inflight.add(dst)
        stage = None

        def settled(ok):
            self._inflight.discard(dst)
            on_done(ok)

        def clean_stage():
            if not stage or not os.path.lexists(stage):
                return
            try:
                self._undo_remove(stage)
            except (OSError, shutil.Error):
                pass

        def commit(ok):
            """Publish the staged copy, or remove every trace of it."""
            if not ok:
                clean_stage()
                settled(False)
                return
            try:
                # Finder's own concurrent operations are excluded by the
                # _inflight claim, but nothing outside Finder is: a download
                # landing, an editor writing out, another program's move can
                # create `dst` at any moment, including between a look and a
                # rename. Looking first and then calling os.replace was exactly
                # that gap — an item that arrived in the window was destroyed,
                # and the status line said the copy had been made. Publication
                # is therefore the single syscall that cannot overwrite: it
                # either puts the finished copy at the name or fails with
                # EEXIST, having touched neither side. There is deliberately no
                # userspace check-then-replace fallback; a fallback would be
                # the race, reintroduced on whichever path took it.
                self._rename_noreplace(stage, dst)
            except FileExistsError:
                # Someone else got the name. Their item stands untouched; only
                # our own private staging entry is removed. No Undo and no
                # "Copied here" — the copy is not published, and the message
                # says which name was taken.
                clean_stage()
                settled(False)
                self._flash_status(
                    _t("An item named '%s' already exists")
                    % os.path.basename(dst))
                return
            except OSError as exc:
                clean_stage()
                settled(False)
                self._flash_status(self._copy_error_text(exc))
                return
            settled(True)

        # Copy into a hidden sibling first. The final name therefore denotes
        # either no file or the complete committed result—never the first 40%
        # of a PDF while a worker is still filling it. Same-directory rename is
        # atomic on the destination filesystem, including removable media.
        dest_dir = os.path.dirname(dst) or "."
        try:
            os.makedirs(dest_dir, exist_ok=True)
            # Keep the prefix short: a legal 255-byte destination basename
            # must not become illegal merely because staging decorates it.
            prefix = ".nbcopy-"
            if os.path.isdir(src):
                stage = tempfile.mkdtemp(prefix=prefix, dir=dest_dir)
            else:
                fd, stage = tempfile.mkstemp(prefix=prefix, dir=dest_dir)
                os.close(fd)
        except OSError as exc:
            clean_stage()
            settled(False)
            self._flash_status(self._copy_error_text(exc))
            return

        big = self._copy_size(src) > COPY_ASYNC_BYTES
        if big and self.get_mapped():
            self._copy_async(src, stage, commit)
            return
        try:
            self._do_copy(src, stage)
        except (OSError, shutil.Error) as exc:
            clean_stage()
            # on_done first: it reloads the view, which rewrites the status bar
            # (an earlier flash was overwritten before anyone could read it).
            settled(False)
            self._flash_status(self._copy_error_text(exc))
            return
        commit(True)

    def _update_paste(self):
        # There is no toolbar Paste button any more (paste_btn is None); the
        # context/Edit menus build their own sensitivity from self._clipboard
        # each time they open. Guard on the value, not just the attribute.
        btn = getattr(self, "paste_btn", None)
        if btn is not None:
            btn.set_sensitive(self._clipboard is not None)

    def _select_name(self, name):
        # select the row with this name in whichever view is active and scroll
        # it into view. Used after new-folder / rename / duplicate so the item
        # the action produced is the one that stays highlighted.
        for row in self.store:
            if row[1] == name:
                if getattr(self, "_view", "list") == "grid":
                    self.iconview.select_path(row.path)
                    self.iconview.scroll_to_path(row.path, False, 0, 0)
                else:
                    self.tree.get_selection().select_iter(row.iter)
                    self.tree.scroll_to_cell(row.path, None, False, 0, 0)
                return True
        return False

    # ---- rename (inline) ----
    def _begin_rename(self):
        # Turn the selected item's Name cell into an editable entry. The
        # renderer is editable only for this edit (flipped back off when the
        # edit commits or is cancelled), so ordinary clicks keep selecting and
        # double-click keeps opening.
        if self.rel == "Applications":
            self._flash_status(_t("Applications are managed in Packages."))
            return
        if self.rel == ".Trash":
            self._flash_status(_t("Put items back before renaming them"))
            return
        model, it = self._selected_iter()
        if it is None:
            self._flash_status(_t("Select an item to rename"))
            return
        path = model.get_path(it)
        if getattr(self, "_view", "list") == "grid":
            self._grid_text_renderer.set_property("editable", True)
            self.iconview.grab_focus()
            self.iconview.select_path(path)
            self.iconview.set_cursor(path, self._grid_text_renderer, True)
        else:
            self._name_renderer.set_property("editable", True)
            self.tree.grab_focus()
            self.tree.set_cursor(path, self._name_column, True)

    def _end_rename_mode(self):
        # leave inline-edit mode: both renderers go non-editable again so a
        # later single click can't accidentally start an edit.
        for r in (getattr(self, "_name_renderer", None),
                  getattr(self, "_grid_text_renderer", None)):
            if r is not None:
                r.set_property("editable", False)
        return False

    def _on_edit_started(self, _renderer, editable, path_str):
        # Pre-select just the base name (not the extension) so retyping a rename
        # keeps ".txt"/".png" intact — the standard rename affordance. GTK
        # selects the whole cell text by default, so reselect on the next idle.
        if not isinstance(editable, Gtk.Entry):
            return
        try:
            it = self.store.get_iter_from_string(path_str)
            name = self.store.get_value(it, 1)
            is_dir = self.store.get_value(it, 5)
        except (ValueError, TypeError):
            return
        stem = name if is_dir else (os.path.splitext(name)[0] or name)
        # RENAMING EDITS THE FILE, so the field opens on the file's own name.
        # The cell shows an app translated ("Ajustes"), and the editor is
        # seeded from the cell: committing that unchanged would have renamed
        # Settings.app to Ajustes.app, which APP_MODULES does not know, and
        # the app would stop launching for the rest of the machine's life —
        # from pressing F2 and Enter without typing anything.
        if not is_dir and name.endswith(".app"):
            try:
                editable.set_text(stem)
            except Exception:                                  # noqa: BLE001
                pass

        def _select():
            try:
                editable.select_region(0, len(stem))
            except Exception:
                pass
            return False
        GLib.idle_add(_select)

    def _name_cell_data(self, _owner, cell, model, it, _data=None):
        """Draw the Name cell as the user should read it (see display_name).

        Shared by the list column and the grid's text cell so the two views can
        never disagree about what an item is called. A search result that lives
        somewhere else carries a quiet note of the folder it came from —
        without it, a whole-Home search is a list of names with no answer to
        the question the user actually asked, which is *where is it*."""
        rel = model.get_value(it, 4)
        name = display_name(model.get_value(it, 1), rel)
        where = self._result_location(rel)
        if where:
            # In the list the note trails the name on the same line; in the
            # grid the cell is only ~130px wide, so trailing it there wrapped
            # mid-phrase ("Tax notes.txt  in" / "Projects") and pulled the
            # label off centre. Under a grid icon it gets its own line.
            grid = _owner is getattr(self, "iconview", None)
            cell.set_property(
                "markup",
                "%s%s<span foreground=\"#8A857A\" size=\"small\">%s</span>"
                % (GLib.markup_escape_text(name), "\n" if grid else "   ",
                   GLib.markup_escape_text(where)))
        else:
            cell.set_property("text", name)

    def _result_location(self, rel):
        """"in Documents" for a search hit from another folder, else "" — the
        folder you are standing in needs no label."""
        if not self._filter or not isinstance(rel, str):
            return ""
        parent = os.path.dirname(rel)
        if parent == (self.rel or ""):
            return ""
        base = os.path.basename(parent)
        return _t("in %s") % (display_name(base) if base else _t("Home"))

    def _on_name_edited(self, _renderer, path_str, new_text):
        # commit an inline rename: move the item onto the new name (refusing an
        # occupied one), then reload and keep the renamed item selected. Shared
        # by the list and grid renderers (both
        # drive self.store, so the path string maps straight to a store row).
        self._end_rename_mode()
        if self.rel == "Applications":
            self._flash_status(_t("Applications are managed in Packages."))
            return
        if self.rel == ".Trash":
            self._flash_status(_t("Put items back before renaming them"))
            return
        new_name = (new_text or "").strip()
        try:
            it = self.store.get_iter_from_string(path_str)
        except (ValueError, TypeError):
            return
        if it is None:
            return
        old_name = self.store.get_value(it, 1)
        old_abs = self.abspath(self.store.get_value(it, 4))
        # the Name cell shows an app without its ".app" suffix, so the edited
        # text comes back without it too; put it back before comparing or
        # touching the filesystem, or renaming would strip it and the launcher
        # would stop recognising the app.
        if new_name and old_name.endswith(".app") \
                and not new_name.endswith(".app"):
            new_name += ".app"
        if not new_name or new_name == old_name:
            return
        if new_name in (".", "..") or "/" in new_name:
            self._flash_status(_t("A name cannot contain a slash"))
            return
        new_abs = os.path.join(os.path.dirname(old_abs), new_name)
        if self._taken(new_abs):
            self._flash_status(_t("An item named '%s' already exists") % new_name)
            return
        try:
            self._rename_noreplace(old_abs, new_abs)
        except FileExistsError:
            self._flash_status(_t("An item named '%s' already exists") % new_name)
            self.load(self.rel, record=False, keep_filter=True)
            return
        except OSError:
            self._flash_status(_t("Could not rename '%s'") % old_name)
            return
        self._set_undo_move(_t("Rename"), new_abs, old_abs)
        self.load(self.rel, record=False, keep_filter=True)
        self._select_name(new_name)

    # ---- duplicate ----
    def _duplicate_selected(self):
        # Copy the selected item alongside itself with a ' copy' suffix (files
        # and, recursively, folders), then select the duplicate.
        if self.rel == "Applications":
            self._flash_status(_t("Applications are managed in Packages."))
            return
        p = self._selected_path()
        if not p:
            self._flash_status(_t("Select an item to duplicate"))
            return
        # lexists: see _paste. Duplicating a link copies the link, so whether
        # its target is still there has no bearing on whether this can run.
        if not os.path.lexists(p):
            self.load(self.rel, record=False, keep_filter=True)
            self._flash_status(_t("That item no longer exists"))
            return
        dest_dir = os.path.dirname(p)
        dst = self._unique_path(dest_dir, os.path.basename(p), suffix=" copy")

        def done(ok):
            if ok:
                self._set_undo_remove(_t("Duplicate"), dst)
            self.load(self.rel, record=False, keep_filter=True)
            if ok:
                self._select_name(os.path.basename(dst))
                self._flash_undoable(_t("Duplicated “%s”")
                                     % display_name(os.path.basename(p)))
        self._copy(p, dst, done)

    # ---- right-click context menu ----
    def _on_tree_button(self, tree, event):
        if event.button != 3 or event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if hit is None:                       # empty space, not a row
            tree.get_selection().unselect_all()
            self._popup_background_menu(event)
            return True
        tree.grab_focus()
        tree.get_selection().select_path(hit[0])
        self._popup_context_menu(event)
        return True

    def _on_grid_button(self, iconview, event):
        if event.button != 3 or event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        path = iconview.get_path_at_pos(int(event.x), int(event.y))
        if path is None:
            iconview.unselect_all()
            self._popup_background_menu(event)
            return True
        iconview.grab_focus()
        iconview.select_path(path)
        self._popup_context_menu(event)
        return True

    def _popup_context_menu(self, event):
        menu = Gtk.Menu()
        menu.get_style_context().add_class("findermenu")
        if self.rel == ".Trash":
            # Inside the Trash the file-ops (Cut/Rename/Duplicate) are confusing
            # or would break Put-Back's name-keyed origin tracking, so offer only
            # what makes sense here: reopen, restore, destroy, or inspect.
            rows = [(_t("Open"), self._context_open),
                    (None, None),
                    (_t("Put Back"), self._restore_selected),
                    (_t("Delete Immediately…"), self._confirm_delete_forever),
                    (None, None),
                    (_t("Get Info"), self._get_info)]
        elif self.rel == "Applications":
            rows = [(_t("Open"), self._context_open),
                    (None, None),
                    (_t("Copy"), self._copy_selected),
                    (None, None),
                    (_t("Get Info"), self._get_info)]
        else:
            rows = [(_t("Open"), self._context_open),
                    (None, None),
                    (_t("Cut"), self._cut_selected),
                    (_t("Copy"), self._copy_selected),
                    (None, None),
                    (_t("Rename"), self._begin_rename),
                    (_t("Duplicate"), self._duplicate_selected),
                    (_t("Move to Trash"), self._trash_selected),
                    (None, None),
                    (_t("Get Info"), self._get_info)]
        for label, cb in rows:
            if label is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            mi = Gtk.MenuItem(label=label)
            mi.get_style_context().add_class("findermenuitem")
            mi.connect("activate", lambda _m, fn=cb: fn())
            menu.append(mi)
        menu.show_all()
        nbapp.popup_at(menu, event=event)

    def _popup_background_menu(self, event):
        # Right-click on empty space: the folder-level actions a novice reaches
        # for (New Folder, Paste) instead of nothing happening at all. Paste is
        # dimmed until something is on the clipboard.
        menu = Gtk.Menu()
        menu.get_style_context().add_class("findermenu")
        # Undo names the action it will take back, so it is never a leap of
        # faith — and it is the first thing reached for after a wrong move.
        undo_label = (_t("Undo %s") % self._undo["label"]) if self._undo \
            else _t("Undo")
        rows = [(undo_label, self._do_undo, self._undo is not None)]
        if self.rel != "Applications":
            rows += [(None, None, False),
                     (_t("New Folder"), self._new_folder, True),
                     (_t("Paste"), self._paste, self._clipboard is not None)]
        for label, cb, enabled in rows:
            if label is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            mi = Gtk.MenuItem(label=label)
            mi.get_style_context().add_class("findermenuitem")
            mi.set_sensitive(enabled)
            mi.connect("activate", lambda _m, fn=cb: fn())
            menu.append(mi)
        menu.show_all()
        nbapp.popup_at(menu, event=event)

    def _context_open(self):
        model, it = self._selected_iter()
        if it is None:
            return
        self._open_path(model.get_path(it))

    # ---- actions menu -----------------------------------------------------
    def _popup_actions_menu(self, btn):
        # App management belongs to Packages. This menu remains a compact route
        # to information about the selected item.
        menu = Gtk.Menu()
        menu.get_style_context().add_class("findermenu")
        _model, it = self._selected_iter()
        rows = [(_t("Get Info"), self._get_info, it is not None)]
        for label, cb, enabled in rows:
            if label is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            mi = Gtk.MenuItem(label=label)
            mi.get_style_context().add_class("findermenuitem")
            mi.set_sensitive(enabled)
            mi.connect("activate", lambda _m, fn=cb: fn())
            menu.append(mi)
        menu.show_all()
        nbapp.popup_at(menu, widget=btn, anchor="widget-sw")

    def _remove_selected_app(self):
        # Hide the selected app from the Applications listing (persisted). The
        # .app file is never deleted — this is a listing preference, undone by
        # Restore Removed Apps.
        if self.rel != "Applications":
            return
        model, it = self._selected_iter()
        if it is None:
            self._flash_status(_t("Select an app to remove"))
            return
        nm = model.get_value(it, 1)
        if not nm.endswith(".app"):
            self._flash_status(_t("Only apps can be removed from Applications"))
            return
        disp = nm[:-4]
        self._removed_apps.add(disp)
        self._save_removed_apps()
        self.load(self.rel, record=False, keep_filter=True)
        self._flash_status(_t("Removed '%s'. Restore it from Actions.") % disp)

    def _restore_removed_apps_dialog(self):
        # A modal list of the removed apps, each with its own Restore button,
        # plus Restore All. Sized to the live screen so it fits a small panel.
        if not self._removed_apps:
            self._flash_status(_t("No removed apps to restore"))
            return
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("finderinfo")
        sw_, sh_ = nbapp.screen_size()
        dlg.set_default_size(min(420, max(300, sw_ - 120)),
                             min(480, max(240, sh_ - 160)))
        area = dlg.get_content_area(); area.set_spacing(0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.get_style_context().add_class("finderinfobox")
        hd = Gtk.Label(label=_t("Removed Applications"), xalign=0)
        hd.get_style_context().add_class("finderinfoname")
        box.pack_start(hd, False, False, 0)
        sub = Gtk.Label(
            label=_t("Bring an app back to the Applications listing."), xalign=0)
        sub.get_style_context().add_class("finderinfokind")
        sub.set_line_wrap(True); sub.set_max_width_chars(40)
        sub.set_halign(Gtk.Align.START)   # or max_width_chars never applies
        sub.set_margin_top(4); sub.set_margin_bottom(14)
        box.pack_start(sub, False, False, 0)

        listbox = Gtk.ListBox()
        listbox.get_style_context().add_class("restorelist")
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scr = Gtk.ScrolledWindow()
        scr.get_style_context().add_class("restorescroll")
        scr.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scr.set_min_content_height(120)
        scr.add(listbox)
        box.pack_start(scr, True, True, 0)

        def add_row(disp):
            row = Gtk.ListBoxRow()
            row.get_style_context().add_class("restorerow")
            rb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            mod = APP_MODULES.get(disp)
            icname = (ICON_ALIAS.get(mod, mod) if mod else "packages")
            rb.pack_start(nbicons.image(icname, 24, "#3A362E"), False, False, 0)
            lbl = Gtk.Label(label=disp, xalign=0)
            lbl.get_style_context().add_class("finderinfoval")
            rb.pack_start(lbl, True, True, 0)
            rbtn = Gtk.Button(label=_t("Restore"))
            rbtn.get_style_context().add_class("finderinfocancel")
            rbtn.connect("clicked", self._on_restore_one, disp, row, listbox, dlg)
            rb.pack_end(rbtn, False, False, 0)
            row.add(rb)
            listbox.add(row)

        for disp in sorted(self._removed_apps):
            add_row(disp)

        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btnrow.set_halign(Gtk.Align.END); btnrow.set_margin_top(16)
        allbtn = Gtk.Button(label=_t("Restore All"))
        allbtn.get_style_context().add_class("finderinfocancel")
        allbtn.connect("clicked", self._on_restore_all, dlg)
        done = Gtk.Button(label=_t("Done"))
        done.get_style_context().add_class("finderinfobtn")
        done.connect("clicked", lambda *_: dlg.destroy())
        btnrow.pack_start(allbtn, False, False, 0)
        btnrow.pack_start(done, False, False, 0)
        box.pack_start(btnrow, False, False, 0)
        area.add(box)
        dlg.connect("key-press-event", self._info_key)
        dlg.show_all()

    def _on_restore_one(self, _btn, disp, row, listbox, dlg):
        self._removed_apps.discard(disp)
        self._save_removed_apps()
        if self.rel == "Applications":
            self.load(self.rel, record=False, keep_filter=True)
        listbox.remove(row)
        if not self._removed_apps:
            dlg.destroy()

    def _on_restore_all(self, _btn, dlg):
        self._removed_apps.clear()
        self._save_removed_apps()
        if self.rel == "Applications":
            self.load(self.rel, record=False, keep_filter=True)
        dlg.destroy()

    def _on_key_press(self, _w, event):
        # Window-level shortcuts. This handler runs before focus-child delivery,
        # so anything typed into a text field (the search box, the inline-rename
        # entry) must fall through untouched — only the file-op keys act when no
        # Gtk.Entry holds focus. Alt+arrows navigate and are safe even mid-type.
        keyval = event.keyval
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(event.state & Gdk.ModifierType.MOD1_MASK)
        if alt and keyval == Gdk.KEY_Left:
            self.go_back(); return True
        if alt and keyval == Gdk.KEY_Right:
            self.go_forward(); return True
        if alt and keyval == Gdk.KEY_Up:
            self.go_up(); return True
        if keyval == Gdk.KEY_F2:
            _model, it = self._selected_iter()
            if it is not None:
                self._begin_rename()
                return True
            return False
        if isinstance(self.get_focus(), Gtk.Entry):
            return False               # typing in a field: leave every key alone
        if ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self._copy_selected(); return True
        if ctrl and keyval in (Gdk.KEY_x, Gdk.KEY_X):
            self._cut_selected(); return True
        if ctrl and keyval in (Gdk.KEY_v, Gdk.KEY_V):
            self._paste(); return True
        if ctrl and keyval in (Gdk.KEY_z, Gdk.KEY_Z):
            self._do_undo(); return True
        if ctrl and keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.search.grab_focus(); return True
        if not ctrl and not alt and keyval == Gdk.KEY_Delete:
            if self.rel == ".Trash":
                # Already in the Trash: there is nowhere further to move it to,
                # and "trashing" it again just renamed the file to "foo (1)"
                # and broke its Put Back. Delete here means delete.
                self._confirm_delete_forever()
            else:
                self._trash_selected()
            return True
        if not ctrl and not alt and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            # close the loop on type-ahead: the row it jumped to opens on Enter
            return self._open_selected()
        if not ctrl and not alt and keyval == Gdk.KEY_Escape:
            self._clear_typeahead()
            return False
        # Type-ahead: letters typed at the list jump to the item that starts
        # with them. In a folder of a hundred files, knowing the name should be
        # enough to reach it without hunting or scrolling.
        if not ctrl and not alt:
            ch = Gdk.keyval_to_unicode(keyval)
            if ch and chr(ch).isprintable() and chr(ch) != " ":
                self._type_ahead(chr(ch))
                return True
            if keyval == Gdk.KEY_BackSpace and self._typeahead:
                self._type_ahead(None)
                return True
        return False

    # ---- type-ahead --------------------------------------------------------
    def _type_ahead(self, ch):
        """Accumulate typed letters and select the first item they name.

        Prefix first (what people expect from a file list), falling back to a
        plain substring so a memorable word mid-name still finds it."""
        if self._typeahead_id:
            GLib.source_remove(self._typeahead_id)
        self._typeahead_id = GLib.timeout_add(TYPEAHEAD_RESET_MS,
                                              self._clear_typeahead)
        if ch is None:
            self._typeahead = self._typeahead[:-1]
        else:
            self._typeahead += ch
        want = self._typeahead.lower()
        if not want:
            self._restore_status()
            return
        hit = None
        for row in self.store:
            if any(nm.startswith(want)
                   for nm in search_names(row[1], row[4])):
                hit = row
                break
        if hit is None:
            for row in self.store:
                if any(want in nm for nm in search_names(row[1], row[4])):
                    hit = row
                    break
        if hit is None:
            # say what was typed even when nothing matches, so the jump that
            # did not happen is explained rather than simply ignored.
            self._flash_status(_t("No item starts with “%s”") % self._typeahead)
            return
        self._select_name(hit[1])
        self._flash_status(_t("Jump to “%s”") % self._typeahead)

    def _clear_typeahead(self):
        if getattr(self, "_closed", False):
            self._typeahead_id = 0
            return False
        self._typeahead = ""
        self._typeahead_id = 0
        return False

    # ---- get info ----
    def _dir_size(self, path, cancel=None):
        # total bytes under a folder (files only; symlinks not followed), with a
        # soft cap so Get Info on a huge tree can't hang the UI.
        total = 0
        files = 0
        for root, _dirs, names in os.walk(path):
            if cancel is not None and cancel.is_set():
                break
            for nm in names:
                if cancel is not None and cancel.is_set():
                    return total, files
                files += 1
                try:
                    total += os.lstat(os.path.join(root, nm)).st_size
                except OSError:
                    pass
            if files > 50000:
                break
        return total, files

    def _compute_dir_size(self, dlg, path, items, size_val):
        # Walk the folder OFF the main loop so opening Get Info on a large tree
        # never stalls the UI, then update the (still-open) dialog's Size field
        # via GLib.idle_add. A liveness flag tied to the dialog's destroy keeps
        # us from touching the label after the user closed the dialog.
        if size_val is None:
            return
        alive = {"open": True}
        cancel = threading.Event()

        def closed(*_args):
            alive["open"] = False
            cancel.set()

        dlg.connect("destroy", closed)

        def work():
            total, _n = self._dir_size(path, cancel)
            if not cancel.is_set():
                GLib.idle_add(self._apply_dir_size, alive, size_val, total, items)

        threading.Thread(target=work, daemon=True).start()

    def _apply_dir_size(self, alive, size_val, total, items):
        # Runs back on the main loop (idle_add); safe to touch GTK here.
        if alive["open"]:
            size_val.set_text(_t("%s  ·  %d items") % (human(total), items))
        return False

    def _selected_row_anchor(self):
        """The selected row's rectangle in overlay coordinates — the origin a
        card raised from a row grows out of (Article B). None in grid view or
        when nothing resolves; the presenter then centre-grows."""
        try:
            model, it = self._selected_iter()
            if it is None or not self.tree.get_visible():
                return None
            path = model.get_path(it)
            col = self.tree.get_column(0)
            return self._cell_origin_tree(self.tree, path, col)
        except Exception:                                         # noqa: BLE001
            return None

    def _present_card_from(self, box, anchor, on_close=None, on_shown=None):
        """Present `box` (a .finderinfobox content column) as an in-window
        card that GROWS FROM `anchor` (Article B) and retracts to it on close.

        Reuses the About-card mechanism: a paper frame (GrowCard) grows on a
        pass-through DrawingArea over self._overlay, the real content is
        revealed on landing, and Esc or a scrim click retracts. Returns the
        card EventBox — it emits `destroy` when removed, so a caller waiting
        on an async fill (Get Info's folder walk) can watch it exactly as it
        watched the old dialog. `nbtransitions` absent → the card just
        appears, centred, without motion.

        `on_shown` fires the moment the real content is revealed — on landing
        for the animated path, immediately for the instant one. A destructive
        confirm uses it to focus its SAFE default only once the card exists,
        so a stray Enter can never reach the danger button before it does."""
        # The anchored-card presenter now lives in nbtransitions.present_card,
        # shared with every app (Article B, §B4 origin enforced there). This
        # wires it to the Finder's overlay, size and Esc handling; the signature
        # and return are unchanged, so Get Info's async fill and the confirm's
        # default-focus keep watching the same card EventBox.
        if nbtransitions is None or not hasattr(nbtransitions, "present_card"):
            # No shared presenter (a headless import without gi): show the
            # content directly so Get Info still works; close removes it.
            card_win = Gtk.EventBox()
            card_win.get_style_context().add_class("finderinfo")
            card_win.add(box)
            self._overlay.add_overlay(card_win)
            card_win.show_all()

            def _close(*_a):
                if card_win.get_parent() is not None:
                    self._overlay.remove(card_win)
                card_win.destroy()
                if on_close is not None:
                    on_close()

            self._info_close = _close
            if on_shown is not None:
                on_shown()
            return card_win
        card_win, close = nbtransitions.present_card(
            self._overlay, box, anchor, on_close=on_close, on_shown=on_shown,
            css_class="finderinfo", size_from=self)
        self._info_close = close
        return card_win

    def _get_info(self):
        model, it = self._selected_iter()
        if it is None:
            return
        name = model.get_value(it, 1)
        is_dir = model.get_value(it, 5)
        kind = model.get_value(it, 8)
        path = self.abspath(model.get_value(it, 4))
        virtual_app = _is_virtual_app(self.rel, name)
        if virtual_app:
            # Applications is an APP_MODULES-backed catalogue, not a directory
            # of launcher files. There is no byte count, mtime, or disk path to
            # stat truthfully; show the virtual location and unknown fields.
            shown_name = display_name(name, model.get_value(it, 4))
            size_txt = "—"
            modified = "—"
            path = _t("Applications")
            st = None
        else:
            shown_name = name
            try:
                st = os.stat(path)
            except OSError:
                self._flash_status(_t("Could not read '%s'") % name)
                return
        if is_dir and not virtual_app:
            try:
                items = len(os.listdir(path))
            except OSError:
                items = 0
            # The recursive folder-size walk can stall the UI on a big tree, so
            # don't run it on the main loop: open the dialog immediately with
            # the size pending and fill it in from a worker thread once the walk
            # finishes (see _compute_dir_size). The item count is a single
            # listdir, so it's known right away.
            size_txt = _t("Calculating…  ·  %d items") % items
        elif not virtual_app:
            size_txt = "%s  ·  %s bytes" % (human(st.st_size),
                                            format(st.st_size, ","))
        if not virtual_app:
            try:
                modified = _t(time.strftime("%d %b %Y, %H:%M",
                                            time.localtime(st.st_mtime)))
            except ValueError:
                modified = "—"
        icon = "folder" if is_dir else self._icon_for(name)
        # nbmotion-inventory: finder.get-info
        anchor = self._selected_row_anchor()
        dlg, size_val = self._show_info_dialog(
            shown_name, icon, kind, size_txt, modified, path, anchor)
        if is_dir and not virtual_app:
            self._compute_dir_size(dlg, path, items, size_val)

    def _show_info_dialog(self, name, icon, kind, size_txt, modified, path,
                          anchor=None):
        # The card grows from its row (Article B) via _present_card_from,
        # rather than a modal dialog appearing from nowhere. The content
        # column below is unchanged; only the presentation moved.
        if getattr(self, "_info_card", None) is not None:
            close = getattr(self, "_info_close", None)
            if close is not None:
                close()
            self._info_card = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.get_style_context().add_class("finderinfobox")
        # header: the item's own glyph, its name, and its kind
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        head.get_style_context().add_class("finderinfohead")
        head.pack_start(
            nbicons.image(icon, 44, "#3A362E"),
            False, False, 0)
        htext = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        htext.set_valign(Gtk.Align.CENTER)
        nm = Gtk.Label(label=name, xalign=0)
        nm.get_style_context().add_class("finderinfoname")
        nm.set_line_wrap(True); nm.set_max_width_chars(26)
        # max_width_chars only bites when the label is allowed to be narrower
        # than its box: at the default FILL it stretches to whatever the window
        # will give, which let one long file name blow this dialog out to 979px.
        # WORD_CHAR because a name or path can be one unbreakable run.
        nm.set_halign(Gtk.Align.START)
        nm.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        kd = Gtk.Label(label=kind, xalign=0)
        kd.get_style_context().add_class("finderinfokind")
        htext.pack_start(nm, False, False, 0)
        htext.pack_start(kd, False, False, 0)
        head.pack_start(htext, True, True, 0)
        box.pack_start(head, False, False, 0)
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.get_style_context().add_class("finderinfosep")
        box.pack_start(sep, False, False, 0)
        grid = Gtk.Grid()
        grid.get_style_context().add_class("finderinfogrid")
        grid.set_column_spacing(16)
        grid.set_row_spacing(9)
        size_val = None
        vals = []
        for r, (k, v) in enumerate((("Size", size_txt),
                                    ("Modified", modified),
                                    ("Where", path))):
            kl = Gtk.Label(label=k, xalign=1)
            kl.get_style_context().add_class("finderinfokey")
            kl.set_valign(Gtk.Align.START)
            vl = Gtk.Label(label=v, xalign=0)
            vl.get_style_context().add_class("finderinfoval")
            vl.set_line_wrap(True); vl.set_max_width_chars(34)
            # same as the name above: cap the measure, and break mid-token so a
            # long "Where" path (one unbroken run) actually wraps.
            vl.set_halign(Gtk.Align.START)
            vl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            vl.set_selectable(True)
            if k == "Size":
                size_val = vl              # updated once the folder walk lands
            vals.append(vl)
            grid.attach(kl, 0, r, 1, 1)
            grid.attach(vl, 1, r, 1, 1)
        box.pack_start(grid, False, False, 0)
        btnrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        btnrow.set_halign(Gtk.Align.END)
        done = Gtk.Button(label=_t("Done"))
        done.get_style_context().add_class("finderinfobtn")
        btnrow.pack_start(done, False, False, 0)
        box.pack_start(btnrow, False, False, 0)
        box.show_all()
        # Present as a card growing from the row; the presenter reveals it on
        # landing and returns the card handle (emits destroy on close, so
        # _compute_dir_size's liveness watch is unchanged).
        card = self._present_card_from(box, anchor)
        # Bind this button to THIS card's close function. A later Get Info
        # replaces self._info_close; an old button finishing its retract must
        # never close the newer card instead.
        card_close = self._info_close
        done.connect("clicked", lambda *_: card_close())
        self._info_card = card

        def card_gone(*_args):
            if getattr(self, "_info_card", None) is card:
                self._info_card = None
                self._info_close = None

        card.connect("destroy", card_gone)
        card.connect("key-press-event", self._info_key)
        # The values are selectable so a path can be copied — but that also makes
        # them focusable, and GTK focused the first one on open and selected all
        # of its text, so Get Info appeared with the file's size mysteriously
        # highlighted. Put the focus on Done, where it belongs, and drop the
        # selection GTK made on the way (moving the focus does not clear it).
        done.grab_focus()
        for vl in vals:
            vl.select_region(0, 0)
        return card, size_val

    def _info_key(self, _w, event):
        # Esc leaves (never destroys anything but the card) — the OS-wide
        # contract. Routes through the card's own close so the retract runs.
        if event.keyval == Gdk.KEY_Escape:
            if getattr(self, "_info_close", None) is not None:
                self._info_close()
            return True
        return False

    def _icon_for(self, name):
        return icon_for(name)             # module-level twin (nbpicker shares it)

    def _kind_for(self, name, isdir):
        return kind_for(name, isdir)

    def go_up(self):
        if self.rel.startswith("/"):
            parent = os.path.dirname(self.rel)
            if parent and parent != self.rel:   # stop at "/"
                self.load(parent)
        elif self.rel:
            self.load(os.path.dirname(self.rel))
        else:
            # at Home -> step out to the absolute parent of the home dir
            parent = os.path.dirname(HOME)
            if parent and parent != HOME:
                self.load(parent)

    def _on_open(self, tree, path, col):
        self._launch_origin = self._cell_origin_tree(tree, path, col)
        self._open_path(path)

    def _on_open_grid(self, iconview, path):
        self._launch_origin = self._cell_origin_icon(iconview, path)
        self._open_path(path)

    def _cell_origin_tree(self, tree, path, col):
        """The activated row's rectangle in TOPLEVEL coordinates, or None.
        TreeView cell areas are bin-window coordinates; convert before
        translating, or a scrolled list reports the wrong origin."""
        try:
            area = tree.get_cell_area(path, col)
            wx, wy = tree.convert_bin_window_to_widget_coords(area.x, area.y)
            at = tree.translate_coordinates(self, wx, wy)
            if at is None:
                return None
            return (at[0], at[1], max(1, area.width), max(1, area.height))
        except Exception:                                         # noqa: BLE001
            return None

    def _cell_origin_icon(self, iconview, path):
        try:
            ok, rect = iconview.get_cell_rect(path, None)
            if not ok:
                return None
            at = iconview.translate_coordinates(self, rect.x, rect.y)
            if at is None:
                return None
            return (at[0], at[1], max(1, rect.width), max(1, rect.height))
        except Exception:                                         # noqa: BLE001
            return None

    def _open_path(self, path):
        it = self.store.get_iter(path)
        rel = self.store.get_value(it, 4)
        name = self.store.get_value(it, 1)
        full = self.abspath(rel)
        if not os.path.exists(full):
            # the item was deleted elsewhere since this listing was read: don't
            # silently do nothing — refresh the view and say what happened.
            self.load(self.rel, record=False, keep_filter=True)
            self._flash_status(_t("'%s' no longer exists") % display_name(name))
            return
        if os.path.isdir(full):
            # nbmotion-inventory: finder.open-folder
            # Stepping into a folder slides the outgoing listing off to the
            # left, the way Forward travels, so Back is its exact inverse. The
            # flag is deliberately NOT stamped into _nav_dir: an open is an
            # ARRIVAL and must still land at the top of the new folder, which
            # is what restores_place() reads that value to decide.
            self._nav_enter = True
            self.load(rel)
        elif name.endswith(".app"):
            self.launch_app(name[:-4])
        else:
            # a plain document: open it in its owning app, passing the file as
            # argv[1] (Media Viewer / Writer / E-book Reader read sys.argv[1]).
            # The user's Settings ▸ Default Applications choice wins over the
            # built-in mapping (see _default_app_for).
            mod = self._default_app_for(os.path.splitext(name)[1].lower())
            if mod:
                self._launch_module(mod, file_arg=full)
            else:
                # no owning app: never fail silently. The row is already
                # selected (double-click), so flash a note in the status bar.
                self._flash_status(_t("No app for this file type"))

    def _default_app_for(self, ext):
        """Which module opens files of extension `ext`. The user's Settings ▸
        Default Applications choice (settings.json 'default_apps': {ext:
        module}) wins when it names a module that actually accepts a file path
        (FILE_OPENERS); otherwise the built-in FILE_APPS mapping. Read fresh on
        each open so a preference change takes effect without relaunching the
        Finder. Never raises — a missing/garbage settings file just falls back
        to FILE_APPS."""
        chosen = None
        try:
            import json
            cfg = os.path.join(HOME, ".config", "notebook", "settings.json")
            with open(cfg) as fh:
                data = json.load(fh)
            da = data.get("default_apps") if isinstance(data, dict) else None
            if isinstance(da, dict):
                c = da.get(ext)
                if isinstance(c, str) and c in FILE_OPENERS:
                    chosen = c
        except (OSError, ValueError, TypeError):
            pass
        mod = chosen or FILE_APPS.get(ext)
        # A hidden app is withheld from EVERY launch surface, not only the
        # Applications folder: a document routed to it reads as having no app
        # until the app is unhidden (hidden_apps_selftest catches the
        # half-hide this closes).
        if mod in _hidden_modules():
            return None
        return mod

    def launch_app(self, display_name, file_arg=None):
        mod = APP_MODULES.get(display_name)
        if not mod:
            # An item named "<something>.app" that no installed app claims.
            # The everyday way to make one is to RENAME an app: _on_name_edited
            # puts the ".app" suffix back (so the file stays an app) while the
            # stem it is keyed on changes, and "Adding Machine.app" is not in
            # APP_MODULES. Returning here made double-clicking that icon do
            # nothing, silently, for the rest of the machine's life — the dead
            # control Article II forbids. Say it instead, in the same words
            # _launch_module uses for an app module the image does not carry.
            #
            # The .app file is a stub; its CONTENTS are never read or run, so
            # an unknown one can name a module but never become one.
            self._flash_status(_t("That app is not available"))
            return
        self._launch_module(mod, file_arg=file_arg)

    def _launch_module(self, mod, file_arg=None):
        # Spawn a DE app module, optionally handing it a file path as argv[1],
        # then step out of the way while it owns the screen. Shared by the .app
        # double-click, the panel, and document double-click.
        script = os.path.join(DE_DIR, mod + ".py")
        if os.path.exists(script):
            argv = ["python3", script]
            if file_arg:
                argv.append(file_arg)
            env = dict(os.environ, PYTHONPATH=DE_DIR)
            try:
                proc = subprocess.Popen(argv, env=env)
            except OSError:
                # python3 missing / fork failed: stay visible instead of
                # hiding behind an app that never launched, and say so.
                self._flash_status(_t("Could not open that app"))
                return
            # nbmotion-inventory: system.app-launch
            # LAUNCH CONTINUITY (PAPER-PHYSICS G1): never a frame that shows
            # neither the Finder nor the app. Stay visible until the app's
            # first map — the <pid>.mapped beacon nbapp writes — and only
            # then step out of the way. Two failures stop being dark
            # screens: a process that dies before mapping leaves the Finder
            # exactly where it was, with a message; one that runs but never
            # maps falls back to the old hide after a deadline.
            self._launch_pid = proc.pid
            self._launch_beacon = os.path.join(
                nbapp.APP_DIR, "%d.mapped" % proc.pid)
            self._launch_deadline = time.monotonic() + 8.0
            # No growing launch card any more (removed 2026-08-09 on the design
            # owner's direction): the app fades itself in calmly on first map
            # (nbapp system.app-launch). Launch CONTINUITY is unchanged — the
            # Finder holds the screen until the app's .mapped beacon below.
            GLib.timeout_add(60, self._launch_watch)
            GLib.child_watch_add(proc.pid, self._app_exited)
        else:
            # the owning app module isn't present on this image (e.g. a document
            # whose app wasn't installed): tell the user instead of nothing.
            self._flash_status(_t("That app is not available"))

    def _launch_watch(self):
        """One 60ms poll of a spawned app's road to its first map.

        Returns True to keep polling. Three exits: the beacon appears (the
        app is on screen — NOW step out of the way), the process dies first
        (stay put and say so — the old code hid immediately and a crashed
        launch meant a blank desktop until the child-watch fired), or the
        deadline passes (an app that runs but never maps: fall back to the
        old hide, which is never worse)."""
        if getattr(self, "_closed", False):
            return False
        pid = self._launch_pid
        if pid is None:
            return False
        if os.path.exists(self._launch_beacon):
            self._launch_pid = None
            self._step_aside()
            return False
        if not os.path.isdir("/proc/%d" % pid):
            self._launch_pid = None
            self._zoom_retract()
            self._flash_status(_t("Could not open that app"))
            return False
        if time.monotonic() > self._launch_deadline:
            self._launch_pid = None
            self._step_aside()
            return False
        return True

    def _step_aside(self):
        # Hidden, we can't shadow the fullscreen app or steal its focus
        # (matchbox pins dialogs above main clients); the flag file tells
        # the widget column to hide too. Return when the app exits.
        self._zoom_clear()
        try:
            open(nbapp.APP_FLAG, "w").close()
        except Exception:
            pass
        self.hide()

    # -- the launch card (transform half of system.app-launch) ------------
    def _zoom_begin(self, mod):
        if not getattr(self, "_zoom_ok", False) or nbmotion is None:
            return
        alloc = self.get_allocation()
        frm = self._launch_origin or (alloc.width * 0.40, alloc.height * 0.40,
                                      alloc.width * 0.20, alloc.height * 0.20)
        self._launch_origin = None
        self._zoom_from = tuple(float(v) for v in frm)
        self._zoom_to = (0.0, 0.0, float(alloc.width), float(alloc.height))
        try:
            self._zoom_title = next(
                (n for n, m in APP_MODULES.items() if m == mod), mod)
        except Exception:                                         # noqa: BLE001
            self._zoom_title = mod
        self._zoom_v = 0.0
        self._zoom_active = True
        if self._zoom is None:
            self._zoom = nbmotion.Damaged(
                widget=self, rect_for=self._zoom_damage,
                on_frame=self._zoom_frame,
                duration=nbmotion.PAGE, easing=nbmotion.ARRIVE)
        self._zoom.jump_to(0.0)
        self._zoom.animate_to(1.0)

    def _zoom_rect(self, t):
        f, to = self._zoom_from, self._zoom_to
        return tuple(f[i] + (to[i] - f[i]) * t for i in range(4))

    def _zoom_damage(self, v):
        return self._zoom_rect(v)

    def _zoom_frame(self, v):
        self._zoom_v = v

    def _zoom_retract(self):
        """The launch died before it mapped: the card returns to the icon
        it grew from (departure easing), then clears — the Finder was never
        hidden, so nothing else moves."""
        if not self._zoom_active or self._zoom is None:
            return
        self._zoom.animate_to(0.0, duration=nbmotion.SURFACE_OUT,
                              easing=nbmotion.DEPART,
                              on_done=lambda _ok: self._zoom_clear())

    def _zoom_clear(self):
        if self._zoom is not None:
            self._zoom.cancel()
        if self._zoom_active:
            last = self._zoom_rect(self._zoom_v)
            self._zoom_active = False
            try:
                self.queue_draw_area(int(last[0]) - 2, int(last[1]) - 2,
                                     int(last[2]) + 4, int(last[3]) + 4)
            except Exception:                                     # noqa: BLE001
                pass

    def _zoom_draw(self, _w, cr):
        if not self._zoom_active:
            return False
        x, y, w, h = self._zoom_rect(self._zoom_v)
        cr.set_source_rgb(0.988, 0.984, 0.973)      # paper
        cr.rectangle(x, y, w, h)
        cr.fill()
        cr.set_source_rgb(0.788, 0.769, 0.714)      # hairline
        cr.set_line_width(1)
        cr.rectangle(x + 0.5, y + 0.5, w - 1, h - 1)
        cr.stroke()
        if w > 300:
            from gi.repository import Pango, PangoCairo
            layout = PangoCairo.create_layout(cr)
            desc = Pango.FontDescription("Nimbus Sans")
            desc.set_absolute_size(16 * Pango.SCALE)
            layout.set_font_description(desc)
            layout.set_text(self._zoom_title, -1)
            tw, th = layout.get_pixel_size()
            cr.set_source_rgb(0.102, 0.098, 0.086)  # ink
            cr.move_to(x + (w - tw) / 2.0, y + (h - th) / 2.0)
            PangoCairo.show_layout(cr, layout)
        return False

    def _flash_status(self, msg, restore_ms=2400):
        # Show a transient message in the status bar, then restore the live item
        # count. Non-silent feedback for actions with no visible result of their
        # own (e.g. double-clicking a file type no installed app can open).
        self.status.set_text(msg)
        GLib.timeout_add(restore_ms, self._restore_status)

    def _restore_status(self):
        if getattr(self, "_closed", False):
            return False
        self.status.set_text(self._status_text(len(self.store)))
        return False

    def _other_apps_running(self, exclude_pid=None):
        # Robust cross-process reconciliation: is any OTHER Notebook OS app
        # (i.e. not the desktop home — finder/widgets) still running? The
        # app-active flag is a shared boolean written by BOTH us and the shell,
        # so it can't by itself tell us that a shell-launched app (e.g. a
        # Calculator opened from the panel) is still up. Scan /proc for other
        # python3 processes running a DE app script, excluding ourselves, the
        # widget column, other Finder windows, and the app that just exited.
        me = os.getpid()
        try:
            pids = [p for p in os.listdir("/proc") if p.isdigit()]
        except OSError:
            return False
        for p in pids:
            pid = int(p)
            if pid == me or pid == exclude_pid:
                continue
            try:
                with open("/proc/%s/cmdline" % p, "rb") as fh:
                    parts = [a for a in fh.read().split(b"\0") if a]
            except OSError:
                continue
            script = next((a.decode("utf-8", "replace") for a in parts
                           if a.endswith(b".py")), None)
            if not script or os.path.dirname(script) != DE_DIR:
                continue
            # Exclude the always-running desktop infrastructure (see session.sh:
            # finder + widgets + shell + xflushd run for the whole session); any
            # OTHER de/*.py is a real user app still holding the screen.
            if os.path.basename(script)[:-3] not in (
                    "finder", "widgets", "shell", "xflushd"):
                return True
        return False

    def _app_exited(self, _pid, _status):
        # Reconcile before reappearing: the app-active flag is shared with the
        # shell, so another app (e.g. a Calculator launched from the panel) may
        # still own the screen. Only drop the flag and return if NOTHING else
        # is running; otherwise stay hidden and leave the flag in place for the
        # last app to clear (the flag monitor brings us back once it's gone).
        if self._other_apps_running(exclude_pid=_pid):
            return
        try:
            os.remove(nbapp.APP_FLAG)
        except Exception:
            pass
        self.show_all()
        self.present()
        # the re-shown Finder is blank on swrast until a scanout flush (same
        # first-paint issue as launched apps) — nudge it a few times.
        GLib.timeout_add(200, self._nudge)
        GLib.timeout_add(600, self._nudge)
        GLib.timeout_add(1500, self._ensure_mapped)

    def _nudge(self):
        if getattr(self, "_closed", False):
            return False
        nbapp.nudge_paint()
        return False


FINDER_CSS = b"""
.finder .window-frame { background: #F8F7F2; border: 1px solid #1A1916; }
.finder .titlebar { background: #FCFBF8; border-bottom: 1px solid #1A1916;
                    padding: 6px 14px; min-height: 44px; }
.finder .winbox { min-width: 15px; min-height: 15px; padding: 0;
                  background: #F8F7F2; border: 1px solid #1A1916; border-radius: 0;
                  box-shadow: none; }
.finder .wintitle { font-weight: 700; font-size: 14px; letter-spacing: 0.08em; }
/* Toolbar palette: the bar and the controls on it are ONE surface.
   These previously sat at #FCFBF8 (near-white) with a #C4BFB1 border on a
   #F1EEE6 bar, so every control read as a pale chip pasted onto a darker
   strip. The bar now uses the paper tone and the controls share it, separated
   by the standard hairline (#C9C4B6, the same rule weight as the installer and
   the new scrollbars), so the toolbar reads as one continuous surface. */
.finder .navbar { background: #FCFBF8; border-bottom: 1px solid #C9C4B6;
                  padding: 10px 16px; }
.finder .navbtn { min-width: 32px; min-height: 32px; padding: 0;
                  background: #FCFBF8; border: 1px solid #C9C4B6; border-radius: 8px;
                  box-shadow: none; }
.finder .navbtn:hover { background: #F1EEE6; }
/* a disabled nav arrow (back/fwd/up at an end) dims to the mockup's greyed
   face + border, so it reads as inactive instead of keeping the live swatch */
.finder .navbtn:disabled { background: #F4F2EC; border-color: #D7D2C5; }
.finder .navsep { color: #C9C4B6; min-width: 1px; margin: 4px 2px; }
.finder .toolbtn { padding: 5px 12px; background: #FCFBF8; color: #2A2620;
                   border: 1px solid #C9C4B6; border-radius: 8px; box-shadow: none;
                   font-size: 13px; margin: 0 1px; }
.finder .toolbtn:hover { background: #F1EEE6; }
.finder .toolbtn:disabled { color: #B3AD9E; background: #F4F2EC; }
.finder .crumb { font-size: 13px; color: #3A362E; padding: 3px 10px;
                 background: #FCFBF8; border: 1px solid #C9C4B6;
                 border-radius: 8px; box-shadow: none; margin: 0 1px; }
.finder .crumb:hover { background: #F1EEE6; }
.finder .crumb.active { background: #EAE3D2; color: #1A1916; font-weight: 600;
                        border-color: #C9C4B6; }
.finder .viewswitch { margin: 0 2px; }
.finder .viewbtn { min-width: 30px; min-height: 30px; padding: 0 5px;
                   background: #FCFBF8; color: #3A362E; border: 1px solid #C9C4B6;
                   border-radius: 0; box-shadow: none; margin: 0; }
.finder .viewbtn:first-child { border-radius: 8px 0 0 8px; }
.finder .viewbtn:last-child { border-radius: 0 8px 8px 0; border-left-width: 0; }
.finder .viewbtn:hover { background: #F1EEE6; }
.finder .viewbtn.active { background: #EAE3D2; border-color: #C9C4B6; }
.finder .filegrid { background: #F8F7F2; font-size: 13px; padding: 6px; }
.finder .filegrid:selected, .finder .filegrid .cell:selected {
                 background: #EAE3D2; color: #2A2620; }
.finder .sidebar { background: #EFEBE0; border-right: 1px solid #D7D2C5;
                   padding: 12px 10px; }
.finder .sbheader { font-size: 11px; color: #8A857A; font-weight: 600;
                    letter-spacing: 0.08em; padding: 12px 8px 6px; }
.finder .sbrow { padding: 7px 12px; background: transparent; border: none;
                 border-radius: 6px; box-shadow: none; margin: 1px 0;
                 font-size: 14px; color: #2A2620; }
.finder .sbrow:hover { background: #F0EADC; }
.finder .sbrow.selected { background: #EAE3D2; border-left: 3px solid #C8341E;
                          font-weight: 600; }
/* Eject, beside a removable volume: a quiet glyph on the sidebar surface. With
   no rule of its own it fell back to the theme's default button and sat in the
   sidebar as a bordered near-white chip among flat, borderless rows. */
.finder .sbeject { background: transparent; border: none; box-shadow: none;
                   padding: 3px 5px; min-width: 20px; min-height: 20px;
                   border-radius: 6px; }
.finder .sbeject:hover { background: #EAE3D2; }
.finder .filelist { background: #F8F7F2; font-size: 14px; }
/* Selected row: the warm selection tone, matching the grid view and the rest
   of the system. It used to carry `border-left: 3px solid #C8341E` for the
   design's single red row edge, but GTK3 paints a TreeView row one CELL at a
   time, so that rule drew a red bar down the left of the Kind, Size AND Date
   columns as well - four bars per row - and shifted every cell's text 3px
   right the moment a row was clicked. There is no CSS handle for "the first
   cell only" (a :first-child on the cell node matches nothing), so the edge is
   dropped here; signage red still marks the current place in the sidebar. */
.finder .filelist :selected { background: #EAE3D2; color: #2A2620; }
/* Column headings read as quiet labels, not buttons. The theme's base button
   rule gave every header cell a full 1px hairline, so the row came up as a
   strip of bevelled beige boxes divided by vertical rules - the clunky "file
   window heading" look. Drop every edge but a single bottom hairline so the
   heading is one clean band of small-caps labels above the list. */
.finder .filelist header button { background: #F1EEE6; color: #8A857A;
                 font-size: 11px; font-weight: 600; border-radius: 0;
                 letter-spacing: 0.08em; padding: 6px 10px;
                 border: none; border-bottom: 1px solid #D7D2C5; }
.finder .filelist header button:hover { background: #EFEBE0; }
/* The sort-direction triangle GTK draws at the end of the active column: keep
   it small and muted so it whispers which column is sorted instead of sitting
   as a big dark wedge in the middle of the wide Name column. */
.finder .filelist header button arrow,
.finder header button arrow {
                 color: #B3AD9E; opacity: 0.4;
                 min-width: 10px; min-height: 10px; }
/* .statusbar is Papertone's - see the theme. Finder used its own paler
   ink and a larger size than every other status strip. */
/* bottom-right resize grip: opaque (black-safe with no compositor), a hairline
   corner tab that reads as a drag handle */
.finder .resizegrip { background: #EFEBE0; border-top: 1px solid #C9C4B6;
                      border-left: 1px solid #C9C4B6; }
.finder .resizegrip:hover { background: #EAE3D2; }
/* empty-folder / no-search-result message, centered over the empty view */
.finder .emptystate { color: #9A9484; font-size: 15px; font-weight: 500; }
/* inline rename: the Name cell's entry, active-red to signal it is live */
.finder .filelist entry, .finder .filegrid entry {
                 background: #FCFBF8; color: #1A1916; caret-color: #C8341E;
                 border: 1px solid #C8341E; border-radius: 8px; padding: 1px 4px; }
/* right-click context menu */
.findermenu { background: #FCFBF8; border: 1px solid #C9C4B6; padding: 4px 0; }
.findermenu menuitem { padding: 5px 18px; color: #2A2620; font-size: 13px; }
.findermenu menuitem:hover { background: #EAE3D2; color: #1A1916; }
.findermenu separator { background: #D7D2C5; min-height: 1px; margin: 4px 0; }
/* Get Info dialog */
.finderinfo { background: #F8F7F2; border: 1px solid #C9C4B6; }
.finderinfobox { padding: 22px 24px 18px; }
.finderinfohead { margin-bottom: 14px; }
.finderinfoname { font-size: 17px; font-weight: 700; color: #1A1916; }
.finderinfokind { font-size: 12px; color: #8A857A; }
.finderinfosep { background: #D7D2C5; min-height: 1px; margin-bottom: 14px; }
.finderinfogrid { margin-bottom: 18px; }
.finderinfokey { font-size: 12px; color: #8A857A; font-weight: 600; }
.finderinfoval { font-size: 13px; color: #2A2620; }
/* A button's own `color` does NOT reach its label: Papertone's `* { color: ink }`
   matches the label node directly, and a direct match beats an inherited value,
   so every light-on-dark button in this file has to name its label too. Without
   this the Done button was ink on ink - a black slab with no visible text. */
.finderinfobtn { padding: 6px 20px; background: #1A1916; color: #F4F2EC;
                 border: 1px solid #1A1916; border-radius: 8px; box-shadow: none;
                 font-size: 13px; }
.finderinfobtn label, .finderinfobtn:hover label { color: #F4F2EC; }
.finderinfobtn:hover { background: #2A2620; }
/* Restore Removed Apps dialog: an opaque scroller + list (black-safe with no
   compositor) with hairline-separated rows.
   NOT scoped under .finder - this dialog is its own toplevel, so it never has
   the Finder window as an ancestor and the whole block was dead: the list came
   up as bare theme chrome with no row padding and the Restore buttons flush
   against its border. */
.restorescroll { background: #FCFBF8; border: 1px solid #D7D2C5;
                 border-radius: 12px; }
.restorelist { background: #FCFBF8; }
.restorelist row { padding: 8px 12px; border-bottom: 1px solid #EFEBE0;
                 background: #FCFBF8; }
.restorelist row:last-child { border-bottom: none; }
/* copy progress: the theme's papertone trough with the ink fill, given a
   slightly heavier bar so it reads as the subject of the card rather than a
   hairline. Never signage red: a copy is work in progress, not an alert. */
.finderprogress trough { min-height: 7px; border-radius: 4px; }
.finderprogress progress { min-height: 7px; border-radius: 4px; }
/* confirm dialog: a light Cancel and a signage-red destructive primary */
.finderinfocancel { padding: 6px 18px; background: #FCFBF8; color: #2A2620;
                 border: 1px solid #C9C4B6; border-radius: 8px; box-shadow: none;
                 font-size: 13px; }
.finderinfocancel:hover { background: #F1EEE6; }
.finderinfodanger { padding: 6px 20px; background: #C8341E; color: #F8F7F2;
                 border: 1px solid #B12D19; border-radius: 8px; box-shadow: none;
                 font-size: 13px; font-weight: 600; }
.finderinfodanger label, .finderinfodanger:hover label { color: #F8F7F2; }
.finderinfodanger:hover { background: #B12D19; }
/* The context menu is a real popup toplevel: without a rule for its decoration
   node the compositor's shadow frame renders as theme grey around the card. */
.findermenu decoration { background: #FCFBF8; box-shadow: none; }
"""


_FINDER_CSS_DONE = False


def install_css():
    # Idempotent: nbpicker calls this on every picker open, so guard against
    # leaking a fresh CssProvider each time (matches nbapp.install_css).
    global _FINDER_CSS_DONE
    if _FINDER_CSS_DONE:
        return
    prov = Gtk.CssProvider(); prov.load_from_data(FINDER_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _FINDER_CSS_DONE = True


if __name__ == "__main__":
    import sys
    install_css()
    start = sys.argv[1] if len(sys.argv) > 1 else "Applications"
    w = Finder(start)
    w.connect("destroy", Gtk.main_quit)
    w.show_all()
    Gtk.main()
