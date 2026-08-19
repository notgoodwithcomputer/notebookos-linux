#!/usr/bin/env python3
"""
Packages — the Notebook OS package manager (native GTK).

A sidebar of views (Installed / Updates / Sources) beside a sortable table of
the packages that make up the read-only system image, with a detail inspector
on the right. The Installed list is enumerated live from the desktop image on
disk (de/*.py): real names, sizes, and modification dates — nothing seeded. It
lists the applications and the system components a person can actually name;
internal plumbing modules are skipped (see _APP_NAMES / _SYS_NAMES).

This is an offline system with no package-install path. The installed system
image is fixed and read-only. Applications can be removed from Finder's
Applications view and restored later; their files stay on disk. A selected
package can also be verified by re-reading and parsing its module file. Updates
states the image-level update boundary without claiming to perform one. Sources
reports the local disk and mounted USB media, read live from /proc/mounts.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

import os
import ast
import json
import re
import subprocess
import time

import nbapp
import nbicons
import nbjobs
import nbtransitions
# Fail CLOSED: nothing launches without a release-key signature over the
# bytes on disk (docs/APP-TRUST.md). A missing nbtrust refuses.
try:
    import nbtrust
except Exception:
    nbtrust = None
import nbi18n
from nbi18n import _t  # noqa: E402

try:
    import nbpkg_install  # signed .nbpkg verify + install (docs/NBPKG.md)
except Exception:  # a device without the installer still runs Packages
    nbpkg_install = None

INK = "#1A1916"
MUTED = "#6E695E"
FAINT = "#9A9484"
# The theme's UNAVAILABLE ink (@inkoff): "this is here, and you cannot use it
# right now". Used for an app that has been removed from Applications, so the
# row keeps its shape and its place and changes only its ink weight.
OFF = "#A9A395"

# Where the desktop image lives on disk. The Installed list is enumerated from
# here at launch, so it reflects the real package set, not a seeded catalog.
DE_DIR = os.path.dirname(os.path.abspath(__file__))

# columns: field indices into a package tuple
NAME, KEY, KIND, SIZE, SIZE_B, MODIFIED, MTIME, DESC, PATH = range(9)

# Layout widths. The smallest panel this OS supports is 1024 wide, and matchbox
# maximises every app to the screen, so the two fixed side panels plus the fixed
# table columns must leave a genuinely readable NAME column at 1024 — otherwise
# the package name is the one thing that ellipsizes away ("…Application") and
# the list becomes unusable on the machine it most needs to work on.
#
# It did: "Application Framework" and "Install Notebook OS" — two of the names
# this image actually ships — came out "Application Fra…" and "Install Notaboo…"
# in a 112px name cell. The columns were budgeted for the text they hold and
# not measured against it. Measured (Nimbus Sans, the sizes below): the widest
# KIND is "Application" at 64px, the widest date "30 Sep 2026" at 88px, a size
# 48px, and the longest shipped name 144px. So each column now carries its own
# content plus a gutter, and the ~38px that buys goes to the name.
SIDEBAR_W = 240
INSPECTOR_W = 300
COL_KIND, COL_MODIFIED, COL_SIZE = 74, 92, 56
# The NAME header cell alone carries a floor, so the "NAME"/"KIND" labels can
# never run together into "NAMEKIND" when the table is at its narrowest.
COL_NAME_MIN = 120

# Display names for the modules that make up the image. These are labels for
# real files on disk (enumerated below), not a fabricated inventory. A module
# with no entry in EITHER map is internal plumbing (the X11 repaint helpers, the
# desktop backdrop painters, the GBA build step) — nothing a person can
# recognise, decide about or act on — so it is not listed at all rather than
# shown under its raw filename.
#
# The applications are DERIVED from the one registry the system actually
# launches from, rather than named again here. This was a second, hand-written
# copy of that list until 2026-08-14, and it had fallen seven apps behind it:
# Animation, Bill Tracker, Comics, Composer, Disc Burner, Meal Planner and USB
# Writer were missing from this window altogether — a person could not see, let
# alone remove, seven of the applications on their own computer — and the
# installer was filed under System as "Installer" while the Finder calls it
# "Install Notebook OS". The reach was wider than this window: sysmon and
# settings both import _APP_NAMES from here to put a human name to a running
# process, so all three surfaces went wrong from the one stale copy. Deriving
# it also means an app installed from a signed package (which the Finder merges
# in from installed_apps.json) is listed here without a second registration.
def _app_names():
    """module -> display name, for every app the Applications folder launches.

    Imported inside the function, not at module scope: a later finder ->
    packages import cannot then become a cycle, and a registry that will not
    load leaves the System list intact instead of taking the window down."""
    try:
        import finder
        # Re-run the merge rather than trusting the one finder did when it was
        # imported: an app installed while this window is open is written to
        # installed_apps.json AFTER that. The merge is additive and idempotent
        # (setdefault), so asking again can only add what is newly there.
        finder._merge_installed_apps()
        return {mod: display for display, mod in finder.APP_MODULES.items()}
    except Exception:
        return {}


_APP_NAMES = _app_names()


def _hidden_modules():
    """Modules this window must not list, because the image withholds the app.

    The Packages window is the machine's answer to "what is installed on this
    computer". An app the image deliberately does not offer (finder.HIDDEN_APPS)
    appearing here — as an Application, with a size and a date — is exactly the
    half-hide that list exists to prevent, so it is filtered out on the way in.

    Derived from finder, never restated here: this file has already paid once
    for keeping its own copy of what the apps are (see _app_names above).

    Note what is NOT filtered: _APP_NAMES itself stays COMPLETE. sysmon and
    settings read it to put a human name to a RUNNING process, and a withheld
    app that is somehow running is exactly when a person needs it named."""
    try:
        import finder
        return finder._hidden_modules()
    except Exception:
        return set()


def _installed_registry():
    """{display name: {module, kind, version, service}} for the apps that were
    added to this computer from a package, as nbpkg_install recorded them.

    The base image is deliberately NOT in here: this file is the machine's
    record of what came AFTER it shipped, which is the difference the Packages
    window has to be able to state — "this one came with Notebook OS" against
    "you installed this one, and this is the version you have"."""
    try:
        with open(os.path.join(DE_DIR, "installed_apps.json"),
                  encoding="utf-8") as fh:
            reg = json.load(fh)
        if not isinstance(reg, dict):
            return {}
        # This registry is unsigned discovery metadata, not an authority, and
        # may be old or hand-damaged. Normalize at the boundary so detail/list
        # callers can safely use mapping methods on every retained entry.
        return {name: value for name, value in reg.items()
                if isinstance(name, str) and isinstance(value, dict)}
    except Exception:
        return {}

def _manifest_display(manifest):
    """The name a .nbpkg manifest goes into the installed registry under.

    nbpkg_install keys its registry on app.display, so that is what a "do I
    already have this?" question has to ask about; the package's own top-level
    name is the fallback for a manifest that predates the app block."""
    if not isinstance(manifest, dict):
        return None
    app = manifest.get("app")
    if isinstance(app, dict) and isinstance(app.get("display"), str):
        return app["display"]
    name = manifest.get("name")
    return name if isinstance(name, str) else None


# The parts of the system a person can actually name. Hand-written on purpose:
# there is no registry of these — they are not launched from anywhere — and
# every entry is a judgement about what a reader would recognise. The installer
# is NOT here: it is in the Applications folder, so it is listed as the Finder
# names it.
_SYS_NAMES = {
    "finder": "Finder", "shell": "Desktop Panel",
    "nbapp": "Application Framework", "nbicons": "Icon Set",
    "widgets": "Desktop Widgets",
    "splash": "Startup Screen", "nbprint": "Printing",
    "nbpicker": "File Picker", "nbi18n": "Translations",
    "nbmediakeys": "Media Keys", "nbdiacritics": "Accent Picker",
    "nbpinyin": "Pinyin Input",
}

# Plain-English descriptions, for the entries whose module docstring opens in
# the language of whoever wrote the module rather than the language of whoever
# reads this window. The docstring is still the source of truth everywhere else
# (see _module_doc); these say the same thing in words a person choosing what
# to keep on their computer can act on — no "raster", "front-end", "core",
# "OSD", "reading surface", no hex colours, no other company's product name.
_DESCRIPTIONS = {
    "g2048": "The sliding-tile number puzzle.",
    # These three open their docstring with "Name - ..." rather than the
    # "Name — ..." the label-stripper knows, and then describe themselves to a
    # reader of the source ("exposure-sheet", "pixel comic zine", "staff-
    # notation MIDI"). Said plainly, for a person deciding what to keep.
    "animation": "Draws a cartoon film one frame at a time, and plays it back.",
    "comics": "Draws comic books, and prints them as a folded booklet.",
    "composer": "Writes music on a staff, and plays it back.",
    "ebook": "The Notebook OS e-book reader.",
    "gbaemu": "Plays Game Boy Advance games.",
    "gbasdk": "Builds Game Boy Advance games.",
    "illustrator": "The Notebook OS paint and drawing app.",
    "language": "A course for learning a new language.",
    "maps": "Maps of streets and places.",
    "packages": "Lists everything installed on this computer.",
    "sequencer": "Records and arranges music on eight tracks.",
    "video": "Makes movies from clips, photos and music.",
    "nbdiacritics": "Hold a letter key to type an accented version of it.",
    "nbi18n": "The interface translations.",
    "nbicons": "The line drawings used for the icons throughout the system.",
    "nbmediakeys": "The volume and brightness keys, with the level they show.",
    "nbpicker": "The Open and Save window shared by every app.",
    "nbpinyin": "Types Chinese characters from Pinyin spelling.",
    "shell": "The menu bar across the top of the screen.",
    "splash": "The loading screen shown while the desktop starts up.",
    "widgets": "The column of small tools beside the desktop.",
}


def _wrap_to_panel(label):
    """Let a wrapping label take its width FROM its fixed-width panel, instead
    of giving the panel its width.

    set_line_wrap alone does not cap anything: a wrapped label still asks for
    the whole unwrapped string as its NATURAL width, and a GtkBox hands every
    non-expanding child that natural width — so one long package name in the
    inspector swelled the panel past INSPECTOR_W and squeezed the table until
    every name in the list ellipsized ("Acad…", "Calcu…"). max_width_chars is
    the documented cap but sizes off the font's APPROXIMATE character width,
    which overshoots badly for this face; asking for one character instead
    pins the natural width to the minimum (the longest word), and WORD_CHAR
    lets even a single over-long word break rather than push the panel wider.
    The label then wraps to whatever width its panel allocates it."""
    label.set_line_wrap(True)
    label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_max_width_chars(1)


def _fmt_size(n):
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.0f KB" % (n / 1024.0)
    if n < 1024 * 1024 * 1024:
        return "%.1f MB" % (n / (1024.0 * 1024.0))
    return "%.1f GB" % (n / (1024.0 * 1024.0 * 1024.0))


def _fmt_date(mtime):
    try:
        return time.strftime("%-d %b %Y", time.localtime(mtime))
    except Exception:
        return ""


_DOC_CACHE = {}
MAX_PACKAGE_SOURCE_BYTES = 2 * 1024 * 1024


def _read_source_bounded(path, limit=MAX_PACKAGE_SOURCE_BYTES):
    """Read a package module for inspection without trusting its file size."""
    with open(path, "rb") as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:
        raise ValueError("package source is too large to inspect")
    return data.decode("utf-8", "replace")


def _module_doc(path):
    # The module docstring's opening sentence, read straight from the file — a
    # real, source-of-truth description (unless the module has a plain-English
    # entry in _DESCRIPTIONS, which wins). We parse the text (never import it)
    # so the calendar.py stdlib-shadow trap can't bite. Failures degrade to "".
    #
    # Parsing is deferred until a package is actually selected (the inspector is
    # the only place a description is shown) and memoized, so launch never pays
    # to ast.parse every module on disk just to read one docstring line.
    #
    # The raw text is written for whoever reads the source, so it is groomed
    # into something a reader of the Packages window can use: the whole opening
    # PARAGRAPH is joined first (taking only the first physical line cut long
    # descriptions off mid-phrase — "…exports real Game Boy Advance"), then the
    # first complete sentence is kept, the "(native GTK)" build note every
    # module carries is dropped, and the sentence is capitalised.
    mod = os.path.basename(path)[:-3]
    if mod in _DESCRIPTIONS:       # written for this window, not for the source
        return _DESCRIPTIONS[mod]
    if path in _DOC_CACHE:
        return _DOC_CACHE[path]
    result = ""
    try:
        doc = ast.get_docstring(ast.parse(_read_source_bounded(path)))
        if doc:
            para = doc.strip().split("\n\n", 1)[0]
            text = " ".join(ln.strip() for ln in para.splitlines()).strip()
            if " — " in text:              # drop a leading "Name — " label
                text = text.split(" — ", 1)[1].strip()
            text = re.sub(r"\s*\([^)]*GTK[^)]*\)", "", text)   # build note
            text = re.split(r"(?<=[.!?])\s", text, 1)[0].strip()
            if text:
                result = text[0].upper() + text[1:]
    except Exception:
        result = ""
    _DOC_CACHE[path] = result
    return result


def _scan_installed():
    # Enumerate the real desktop image: every de/*.py file, with its true size
    # and modification date. Nothing here is seeded — remove a file and it stops
    # appearing; add one and it shows up, unless the image withholds it.
    out = []
    try:
        names = os.listdir(DE_DIR)
    except OSError:
        return out
    hidden = _hidden_modules()
    for fn in names:
        if not fn.endswith(".py"):
            continue
        mod = fn[:-3]
        # An app the image does not offer is not an app this computer has.
        # Read once, before the loop: this is a decision about the build, and
        # it cannot change while a single listing is being assembled.
        if mod in hidden:
            continue
        path = os.path.join(DE_DIR, fn)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if mod in _APP_NAMES:
            name, kind = _APP_NAMES[mod], "Application"
        elif mod in _SYS_NAMES:
            name, kind = _SYS_NAMES[mod], "System"
        else:
            # Internal plumbing with no human name (see _APP_NAMES): listing it
            # under its raw filename ("xnudge", "nbgame") only puts something in
            # front of a person that they cannot understand or act on.
            continue
        # Via nbicons.ALIAS: five apps' glyphs are not named after their module,
        # and looking only for a same-named glyph gave four of them ("academics",
        # "gbaemu", "language", "maps") the generic starburst Settings wears.
        glyph = nbicons.glyph_for(mod)
        # DESC is filled lazily from _module_doc(path) when the package is
        # selected, so the launch-time scan stays a cheap listdir + stat.
        out.append((name, glyph, kind, _fmt_size(st.st_size), st.st_size,
                    _fmt_date(st.st_mtime), st.st_mtime,
                    "", path))
    # applications first, then system components; alphabetical within each group
    out.sort(key=lambda p: (0 if p[KIND] == "Application" else 1,
                            p[NAME].lower()))
    return out


# The installed package set: the OS's own applications and system components,
# read live from the read-only image at launch. The user-facing empty states
# live in Updates (nothing to update) and Sources (no USB media detected); the
# Installed list itself is real, so it is never seeded to avoid emptiness.
PACKAGES = _scan_installed()


def refresh_installed():
    """Re-read the image and the installed-app registry into PACKAGES.

    In place (PACKAGES[:]), not by rebinding: the window holds this list by
    name in a dozen places, and a rebind would leave every one of them looking
    at the old set."""
    global _APP_NAMES
    _APP_NAMES = _app_names()
    PACKAGES[:] = _scan_installed()
    return PACKAGES


class Packages(nbapp.AppWindow):
    app_name = "Packages"
    # NB: the custom menu is "Package" (singular), NOT "Packages" — the app-name
    # button is already keyed on app_name ("Packages") in nbapp's menu bar, so a
    # second "Packages" menu would collide with it and bury "About Packages".
    menus = ("File", "Package", "View")

    def __init__(self):
        super().__init__()
        self._install_css()

        self.view = "installed"
        self.sel = 0
        self.query = ""
        self._flash_src = None
        self._flash_timer = None
        self._flash_serial = 0
        self._jobs = nbjobs.JobOwner(name="packages")
        # The inspector's result line is cleared by a timer, and a timer left
        # running past the window is a callback into a dead inspector.
        try:
            self.connect("destroy", lambda *_: self._cancel_flash_timer())
            self.connect("destroy", lambda *_: self._save_view_prefs())
            self.connect("destroy", lambda *_: self._jobs.close())
        except Exception:
            pass
        self.sort_field = None
        self.sort_desc, self._removed_apps = False, self._load_removed_apps()
        self._load_view_prefs()
        # index -> row widget, and the visible packages in display order, kept
        # in sync by _rebuild_list. Selection re-styles rows in place (see
        # _select_row) instead of rebuilding, so keyboard focus is preserved
        # and arrow-key navigation stays anchored to the highlighted row.
        self._rows = {}
        self._visible_order = []

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_hexpand(True)
        body.set_vexpand(True)
        self.content.pack_start(body, True, True, 0)

        body.pack_start(self._sidebar(), False, False, 0)

        self.stack = Gtk.Stack()
        # Opaque page-tone fallback behind every view: with no compositor an
        # unpainted stack/viewport shows solid black, never the theme default.
        self.stack.get_style_context().add_class("pk-stack")
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        # Instant until the pager says otherwise: the pages below are added
        # while the window is still being built, and a stack that already had a
        # transition type would try to animate that construction.
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.add_named(self._installed_page(), "installed")
        self.stack.add_named(self._updates_page(), "updates")
        self.stack.add_named(self._sources_page(), "sources")
        body.pack_start(self.stack, True, True, 0)
        # The shared page-switch primitive owns the transition from here on. It
        # sets the type and duration on EVERY switch, so the direction follows
        # the sidebar order below: going down the list (Installed -> Updates ->
        # Sources) slides forward and coming back up slides back, instead of
        # every switch looking identical. Under Reduced Motion, and on the
        # no-compositor swrast fallback, nbmotion's policy resolves to instant —
        # exactly the NONE set above — so those machines keep the switch they
        # had and only accelerated sessions animate. See de/nbtransitions.py.
        self._pager = nbtransitions.PageSwitcher(
            self.stack, order=["installed", "updates", "sources"],
            duration=nbtransitions.PAGE)
        # The opening view is shown, not navigated to: there is nothing to have
        # come from, so it is stated as NONE rather than left to the default
        # crossfade. Routed through the pager anyway so it records where the app
        # starts — otherwise the first real click has no origin to measure a
        # direction against and Installed -> Updates would fade, not slide.
        self._pager.switch(self.view, direction=nbtransitions.NONE)

        self._rebuild_list()
        self._rebuild_detail()

    # -------------------------------------------------------------------- menus
    def menu_items(self, name):
        if name == "Package":
            # Every entry is a real action. Application visibility belongs in
            # the inspector, where its scope and always-available undo fit.
            # menu_items() is re-read each time the menu opens, so items that
            # depend on state (a selection to verify, a query to clear) are
            # greyed out when they would be no-ops rather than looking live.
            has_sel = self.sel is not None and 0 <= self.sel < len(PACKAGES)
            # Open only for applications — a system component has no window to
            # open, so the item greys out rather than pretending.
            openable = has_sel and PACKAGES[self.sel][KIND] == "Application"
            op = ("Open", (lambda: self._on_open()) if openable else None)
            verify = ("Verify Package",
                      (lambda: self._on_verify()) if has_sel else None)
            clear = ("Clear Search",
                     self._clear_search if self.query.strip() else None)
            # "Find", not "Find…": nbcommands registers edit.find WITHOUT an
            # ellipsis, and _focus_search raises nothing to answer -- it shows
            # the Installed view and puts the caret in the search box that is
            # already on screen. The ellipsis promised a card that never came,
            # against the OS registry's own wording.
            return [op, verify, nbapp.SEP, ("Find", self._focus_search), clear]
        if name == "View":
            return [("Installed", lambda: self._on_nav(None, "installed")),
                    ("Updates", lambda: self._on_nav(None, "updates")),
                    ("Sources", lambda: self._on_nav(None, "sources"))]
        return super().menu_items(name)

    def _focus_search(self):
        # jump to the Installed view and put the cursor in the search box
        try:
            self._on_nav(None, "installed")
            self.entry.grab_focus()
        except Exception:
            pass

    def _clear_search(self):
        # reset the query (set_text fires "changed" -> _on_search -> rebuild)
        try:
            self.entry.set_text("")
        except Exception:
            pass

    # ------------------------------------------------------------------ sidebar
    def _sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_size_request(SIDEBAR_W, -1)
        box.get_style_context().add_class("sidebar")

        self._nav = {}
        self._nav_count = {}
        # Sources count is the number of connected sources (Local Disk is always
        # one; each mounted USB stick adds another), read live — not a static
        # "2" that would claim a stick is present when none is. It refreshes
        # whenever Sources is re-scanned (see _refresh_sources).
        src_count = 1 + len(self._removable_media())
        for vid, label, glyph, count in (
            ("installed", "Installed", "box", str(len(PACKAGES))),
            ("updates", "Updates", "update", "0"),
            ("sources", "Sources", "sources", str(src_count)),
        ):
            row = Gtk.ToggleButton()
            row.set_relief(Gtk.ReliefStyle.NONE)
            row.get_style_context().add_class("navrow")
            row.get_accessible().set_name(_t(label))
            hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            img = nbicons.image(glyph, 18, MUTED)
            hb.pack_start(img, False, False, 0)
            lab = Gtk.Label(label=label, xalign=0)
            lab.get_style_context().add_class("navlabel")
            hb.pack_start(lab, True, True, 0)
            cnt = Gtk.Label(label=count)
            cnt.get_style_context().add_class("navcount")
            hb.pack_end(cnt, False, False, 0)
            row.add(hb)
            row._nav_hid = row.connect("clicked", self._on_nav, vid)
            box.pack_start(row, False, False, 0)
            self._nav[vid] = row
            self._nav_count[vid] = cnt

        note = Gtk.Label(
            label=_t("Packages lists the system image and app visibility."))
        note.get_style_context().add_class("sidenote")
        _wrap_to_panel(note)
        note.set_xalign(0)
        note.set_valign(Gtk.Align.END)
        box.pack_end(note, False, False, 0)

        # Lit, not navigated to. Gtk.ToggleButton.set_active emits "clicked",
        # so setting the opening row here ran _on_nav BEFORE the rest of the
        # window existed — the sidebar is packed first and the pager it
        # switches is not built until further down, so every launch threw
        # AttributeError out of the handler. Block the row's own handler: the
        # opening view is stated below by the pager itself.
        self._set_nav_active(self.view)
        return box

    def _set_nav_active(self, vid):
        """Light exactly one sidebar row, without navigating.

        Blocks each row's own "clicked" handler while the rail is updated:
        set_active emits it, so a rail refreshed from inside _on_nav would
        call _on_nav again for every row it touched."""
        for k, row in self._nav.items():
            hid = getattr(row, "_nav_hid", None)
            if hid is not None:
                row.handler_block(hid)
        try:
            for k, row in self._nav.items():
                on = (k == vid)
                if row.get_active() != on:
                    row.set_active(on)
                ctx = row.get_style_context()
                if on:
                    ctx.add_class("active")
                else:
                    ctx.remove_class("active")
        finally:
            for k, row in self._nav.items():
                hid = getattr(row, "_nav_hid", None)
                if hid is not None:
                    row.handler_unblock(hid)

    def _on_nav(self, _b, vid):
        self.view = vid
        self._set_nav_active(vid)
        # Re-scan removable media each time Sources is opened, so a USB stick
        # inserted after launch shows up (the page is otherwise a launch-time
        # snapshot). The scan is a tiny /proc/mounts read — never blocking.
        if vid == "sources":
            self._refresh_sources()
        # Through the pager, so the slide direction agrees with the sidebar the
        # click came from; the page is refreshed BEFORE the switch either way,
        # so what slides in is already current.
        self._pager.switch(vid)
        self._save_view_prefs()

    # ---------------------------------------------------------------- installed
    def _installed_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.get_style_context().add_class("pk-page")
        left.set_hexpand(True)

        # header: title + search
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        head.get_style_context().add_class("pk-head")
        title = Gtk.Label(label=_t("Installed"), xalign=0)
        title.get_style_context().add_class("pk-title")
        title.set_valign(Gtk.Align.END)
        head.pack_start(title, False, False, 0)

        search = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search.get_style_context().add_class("searchbox")
        search.set_valign(Gtk.Align.CENTER)
        search.set_size_request(240, 34)
        search.pack_start(
            nbicons.image("search", 15, FAINT),
            False, False, 0)
        self.entry = Gtk.Entry()
        self.entry.set_has_frame(False)
        self.entry.set_placeholder_text(_t("Search packages"))
        self.entry.get_style_context().add_class("searchentry")
        self.entry.connect("changed", self._on_search)
        # Esc while typing clears the search first, and only falls through to
        # the app-wide "Esc returns to Finder" once the box is already empty —
        # so a novice cancelling a search never accidentally quits the app.
        self.entry.connect("key-press-event", self._on_entry_key)
        search.pack_start(self.entry, True, True, 0)
        head.pack_end(search, False, False, 0)
        left.pack_start(head, False, False, 0)

        # column header (clickable: click a column to sort by it)
        colhdr = self._sort_header()
        colhdr.get_style_context().add_class("colhdr")
        self._colhdr = colhdr
        left.pack_start(colhdr, False, False, 0)

        # scrollable list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("pk-scroll")
        scroll.set_vexpand(True)
        self.listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.listbox.get_style_context().add_class("pk-list")
        scroll.add(self.listbox)
        left.pack_start(scroll, True, True, 0)

        # The list scrolls and the column header does not, so with enough
        # packages to need a scrollbar the rows lose its width and every column
        # sits ~13px left of its header. Reserve the same width in the header
        # while the scrollbar is showing, so the two line up either way.
        self._listscroll = scroll
        vsb = scroll.get_vscrollbar()
        if vsb is not None:
            vsb.connect("notify::visible", lambda *_: self._sync_head_gutter())
        scroll.connect("size-allocate", lambda *_: self._sync_head_gutter())
        # The scrolled window keeps its own allocation when the list inside it
        # shrinks past the scrollbar (a search that leaves four rows), so its
        # size-allocate does not fire and the gutter would stay reserved. The
        # list's does fire, because the list is what changed size.
        self.listbox.connect("size-allocate", lambda *_: self._sync_head_gutter())

        page.pack_start(left, True, True, 0)

        # detail inspector
        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.detail.set_size_request(INSPECTOR_W, -1)
        # GTK3 propagates hexpand UP from descendants, and the inspector holds
        # several (the key labels, the Verify button). Left computed, the
        # inspector counts as an expanding child, so the page splits its free
        # width evenly with the table and — packed fill=False — the inspector
        # then sits CENTRED in its half, leaving two paper gaps across the
        # window on a wide screen. Pin it explicitly so the table gets the width.
        self.detail.set_hexpand(False)
        self.detail.get_style_context().add_class("inspector")
        page.pack_start(self.detail, False, False, 0)

        return page

    _hdr_gutter = 0

    def _sync_head_gutter(self):
        """Match the column header's right gutter to the list's scrollbar. A
        no-op unless the width really changed, so calling it from size-allocate
        can never loop; crash-safe, since a gutter must not take down the app."""
        try:
            vsb = self._listscroll.get_vscrollbar()
            # get_visible() is NOT the question. Under PolicyType.AUTOMATIC the
            # scrollbar stays "visible" and GTK takes it off screen with
            # gtk_widget_set_child_visible instead, so this read stayed True
            # for a list too short to scroll: the header kept a 17px gutter the
            # rows no longer had, and every column header sat 16px left of its
            # column the moment a search shortened the list.
            w = (vsb.get_allocated_width()
                 if (vsb is not None and vsb.get_visible()
                     and vsb.get_child_visible()) else 0)
            if w != self._hdr_gutter:
                self._hdr_gutter = w
                self._colhdr.set_margin_end(w)
        except Exception:
            pass

    def _table_row(self, name, kind, modified, size, header=False,
                   glyph=None, index=None, removed=False):
        if header:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        else:
            row = Gtk.ToggleButton()
            row.set_relief(Gtk.ReliefStyle.NONE)
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        # name cell (expands)
        namecell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        namecell.set_hexpand(True)
        if glyph is not None:
            # An app removed from Applications is printed faintly, icon and
            # all — the paper equivalent of greyed-out, and the same UNAVAILABLE
            # family the theme names for a control you cannot use.
            namecell.pack_start(
                nbicons.image(glyph, 20, OFF if removed else INK),
                False, False, 0)
        nl = Gtk.Label(label=name, xalign=0)
        nl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        # A gutter the name can never spill into: the name cell takes all the
        # slack, so without it a long name butts straight against the KIND
        # column and the two read as one word ("CookbookApplication").
        nl.set_margin_end(10)
        nl.get_style_context().add_class("cell-hdr" if header else "cell-name")
        namecell.pack_start(nl, True, True, 0)
        inner.pack_start(namecell, True, True, 0)

        for text, width, xalign, cls in (
            (kind, COL_KIND, 0.0, "cell-kind"),
            (modified, COL_MODIFIED, 0.0, "cell-mono"),
            (size, COL_SIZE, 1.0, "cell-mono"),
        ):
            lab = Gtk.Label(label=text, xalign=xalign)
            lab.set_size_request(width, -1)
            lab.get_style_context().add_class("cell-hdr" if header else cls)
            inner.pack_start(lab, False, False, 0)

        if header:
            row.add(inner)
        else:
            row.add(inner)
            row.connect("clicked", self._on_select, index)
            row.connect("key-press-event", self._on_row_key, index)
            self._mark_removed(row, removed)
        return row

    def _mark_removed(self, row, removed):
        """Say in the LIST which apps are removed from Applications.

        The state was written to the store, shown in the inspector for the one
        selected package, and nowhere else: a person who had removed five apps
        could only find out which by clicking every row in turn. The row keeps
        its shape and its place and changes only its ink weight, and the
        tooltip says what the faintness means (and that it is reversible)."""
        try:
            ctx = row.get_style_context()
            if removed:
                ctx.add_class("removed")
                row.set_tooltip_text(
                    _t("This app is removed from Applications. It stays on "
                       "disk and can always be restored."))
            else:
                ctx.remove_class("removed")
                row.set_tooltip_text(None)
        except Exception:
            pass

    # ------------------------------------------------------------ sortable head
    def _sort_header(self):
        # The docstring promises "a sortable table": build the column header out
        # of clickable cells so clicking NAME/KIND/MODIFIED/SIZE actually sorts
        # the list (and re-clicking a column flips the direction), instead of
        # being an inert label band. Column widths/classes match _table_row so
        # the header still lines up with the rows below.
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self._sort_labels = {}

        name = self._hdr_cell("NAME", "name", 0.0)
        name.set_hexpand(True)
        name.set_size_request(COL_NAME_MIN, -1)
        inner.pack_start(name, True, True, 0)

        for text, field, width, xalign in (
            ("KIND", "kind", COL_KIND, 0.0),
            ("MODIFIED", "modified", COL_MODIFIED, 0.0),
            ("SIZE", "size", COL_SIZE, 1.0),
        ):
            cell = self._hdr_cell(text, field, xalign)
            cell.set_size_request(width, -1)
            inner.pack_start(cell, False, False, 0)

        row.add(inner)
        return row

    def _hdr_cell(self, text, field, xalign):
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("sorthdr")
        # A tooltip signals that the column header is a sort control (the hover
        # highlight alone is easy to miss) and names what it sorts by.
        btn.set_tooltip_text({
            "name": "Sort by name",
            "kind": "Sort by kind",
            "modified": "Sort by date modified",
            "size": "Sort by size",
        }.get(field, "Sort"))
        cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        # right-aligned columns (SIZE) hug the right edge; the rest pack left,
        # so the sort arrow always sits just past the label text.
        cell.set_halign(Gtk.Align.END if xalign >= 1.0 else Gtk.Align.START)
        lab = Gtk.Label(label=text, xalign=xalign)
        lab.get_style_context().add_class("cell-hdr")
        cell.pack_start(lab, False, False, 0)
        # The sort-direction indicator is a cairo-drawn nbicons arrow, never a
        # font glyph: the sans body font lacks ↑/↓ and would render tofu boxes
        # on real hardware. Hidden until this column is the active sort key.
        arrow = Gtk.Image()
        arrow.set_no_show_all(True)
        cell.pack_start(arrow, False, False, 0)
        btn.add(cell)
        self._sort_labels[field] = arrow
        btn.connect("clicked", lambda _b, f=field: self._on_sort(f))
        return btn

    def _on_sort(self, field):
        try:
            previous = (self.sort_field, self.sort_desc)
            if self.sort_field == field:
                self.sort_desc = not self.sort_desc
            else:
                self.sort_field = field
                self.sort_desc = False
            self._update_sort_labels()
            self._rebuild_list()
            if not self._save_view_prefs():
                # Keep the visible package order and its arrow aligned with
                # the preference that the next launch will actually load.
                self.sort_field, self.sort_desc = previous
                self._update_sort_labels()
                self._rebuild_list()
        except Exception:
            # Sorting is a convenience; never let a bad state crash the app.
            pass

    def _update_sort_labels(self):
        # Vertical flip of the "up" glyph gives a down arrow without needing a
        # separate icon. Done by nbicons on the SURFACE (flip_v) rather than by
        # GdkPixbuf.flip() on a pixbuf: the pixbuf route carries no device scale,
        # so this one arrow would have stayed soft on a HiDPI panel while every
        # other icon around it went sharp.
        up = nbicons.surface("up", 11, MUTED)
        down = nbicons.surface("up", 11, MUTED, flip_v=True)
        for f, arrow in getattr(self, "_sort_labels", {}).items():
            try:
                if f == self.sort_field:
                    arrow.set_from_surface(down if self.sort_desc else up)
                    arrow.show()
                else:
                    arrow.hide()
            except Exception:
                pass

    def _sorted(self, matched):
        f = getattr(self, "sort_field", None)
        if not f:
            return matched
        try:
            # size and date sort on their real numeric fields, not the
            # human-formatted strings, so ordering stays correct.
            if f == "size":
                keyfn = lambda ip: ip[1][SIZE_B]
            elif f == "modified":
                keyfn = lambda ip: ip[1][MTIME]
            else:
                col = {"name": NAME, "kind": KIND}[f]
                keyfn = lambda ip: str(ip[1][col]).lower()
            return sorted(matched, key=keyfn,
                          reverse=getattr(self, "sort_desc", False))
        except Exception:
            return matched

    def _matches(self, p, q):
        # Search matches the display name, the kind ("application"/"system"),
        # and the module filename — so "system", "sysmon", or "writer" all find
        # the package a novice means, not only its exact display name. A single
        # predicate keeps the list and the selection-reconcile logic in step.
        if not q:
            return True
        return (q in p[NAME].lower()
                or q in p[KIND].lower()
                or q in os.path.basename(p[PATH]).lower())

    def _visible(self):
        """The packages the list shows, in the order it shows them.

        One place, because two callers have to agree on it. The search's
        selection fallback used to build its own list straight off
        enumerate(PACKAGES) — unsorted — so with the table ordered by name
        descending, a search that hid the selection put it on "Academics"
        while the top row on screen was "Translations": a highlighted row in
        the middle of the list, and an inspector showing a package the reader
        did not choose."""
        q = self.query.strip().lower()
        return self._sorted([(i, p) for i, p in enumerate(PACKAGES)
                             if self._matches(p, q)])

    def _rebuild_list(self):
        for c in self.listbox.get_children():
            self.listbox.remove(c)
        self._rows = {}
        self._visible_order = []
        q = self.query.strip().lower()
        matched = self._visible()
        if not matched:
            # Honest empty state: tell the truth about which kind of empty it is.
            msg = (_t("No packages match the search.") if q
                   else _t("No packages installed."))
            empty = Gtk.Label(label=msg)
            empty.get_style_context().add_class("list-empty")
            self.listbox.pack_start(empty, False, False, 0)
        else:
            for i, p in matched:
                row = self._table_row(
                    p[NAME], p[KIND], p[MODIFIED], p[SIZE],
                    glyph=p[KEY], index=i,
                    removed=(p[KIND] == "Application"
                             and p[NAME] in self._removed_apps))
                row.get_style_context().add_class("datarow")
                if i == self.sel:
                    row.get_style_context().add_class("selected")
                    # lit, not clicked: a rebuild restates the selection, it
                    # does not choose one (set_active would fire _on_select)
                    nbapp.set_active_quietly(row, True)
                self.listbox.pack_start(row, False, False, 0)
                self._rows[i] = row
                self._visible_order.append(i)
        self.listbox.show_all()

    def _on_search(self, entry):
        self.query = entry.get_text()
        # Reconcile the selection with the filtered results: if the selected
        # package is no longer visible, fall back to the first visible row (or
        # clear the selection when nothing matches) so the detail inspector
        # never shows a package that isn't in the list.
        visible = [i for i, _p in self._visible()]
        if self.sel not in visible:
            self.sel = visible[0] if visible else None
            self._flash_src = None
            self._rebuild_list()
            self._rebuild_detail()
        else:
            self._rebuild_list()

    def _on_key(self, w, ev):
        # Esc LEAVES the transient layer it is in — the search filter — before
        # it leaves the window (Constitution Article II; Contacts, Accounting
        # and Academics all read Esc the same way).
        #
        # It has to be answered HERE, not on the entry. nbapp connects
        # AppWindow._on_key on the TOPLEVEL, and GTK runs a window's own
        # connected handlers before it propagates the key down to the focus
        # widget, so the entry's key handler below is never reached for Escape:
        # the base handler ran first and closed the app. Typing "cal" into the
        # search box and pressing Esc quit Packages with the query still in the
        # box — the exact accident the entry handler was written to prevent.
        #
        # Only while the search box is the layer being cancelled: an open menu
        # or the About card is the base's to dismiss first, and on Updates or
        # Sources the box is not on screen, so Esc leaves the window as it
        # always did.
        if (ev.keyval == Gdk.KEY_Escape
                and getattr(self, "_menu_open", None) is None
                and getattr(self, "_about_close", None) is None
                and getattr(self, "_about_layer", None) is None
                and getattr(self, "view", "") == "installed"
                and getattr(self, "entry", None) is not None
                and self.entry.get_text()):
            self._clear_search()
            return True
        return super()._on_key(w, ev)

    def _on_entry_key(self, _w, ev):
        # The same rule, kept on the entry as a fallback for anywhere the key
        # reaches the box without passing the window first: Esc clears a
        # non-empty search instead of quitting the app, and an empty box falls
        # through so Esc still returns to the Finder.
        if ev.keyval == Gdk.KEY_Escape and self.entry.get_text():
            self.entry.set_text("")   # fires "changed" -> _on_search
            return True
        return False

    def _on_select(self, _b, index):
        self._select_row(index)

    def _select_row(self, index):
        # Move the selection by re-styling rows in place — never a full rebuild —
        # so the activated/focused row keeps keyboard focus and the list does
        # not flicker on every click or arrow keypress.
        # Rows are toggle buttons (the selected package is readable to
        # assistive technology), and Gtk.ToggleButton.set_active emits
        # "clicked": unlighting the previous row from inside _on_select fired
        # _on_select for THAT row, which relit it and unlit this one, and so
        # on until the stack blew — every click in the list printed hundreds
        # of RecursionErrors. set_active_quietly restates a row with its
        # handlers blocked.
        prev = self.sel
        if prev in self._rows:
            try:
                self._rows[prev].get_style_context().remove_class("selected")
                nbapp.set_active_quietly(self._rows[prev], False)
            except Exception:
                pass
        self.sel = index
        row = self._rows.get(index)
        if row is not None:
            row.get_style_context().add_class("selected")
            nbapp.set_active_quietly(row, True)
        self._flash_src = None
        self._rebuild_detail()

    def _on_row_key(self, _w, ev, index):
        # Up/Down/Home/End walk the visible list and carry the selection (and
        # keyboard focus) with them, so the table is fully navigable without a
        # mouse. Enter/Space still activate the row via the button default.
        order = self._visible_order
        if not order:
            return False
        kv = ev.keyval
        if kv not in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Home, Gdk.KEY_End):
            return False
        try:
            pos = order.index(index)
        except ValueError:
            pos = 0
        if kv == Gdk.KEY_Up:
            pos = max(0, pos - 1)
        elif kv == Gdk.KEY_Down:
            pos = min(len(order) - 1, pos + 1)
        elif kv == Gdk.KEY_Home:
            pos = 0
        else:
            pos = len(order) - 1
        new_index = order[pos]
        self._select_row(new_index)
        row = self._rows.get(new_index)
        if row is not None:
            row.grab_focus()
        return True

    def _rebuild_detail(self):
        for c in self.detail.get_children():
            self.detail.remove(c)
        try:
            p = PACKAGES[self.sel]
        except (IndexError, TypeError):
            # Nothing selected (a search that matched nothing clears it). Say so
            # rather than leaving a blank panel that reads as a broken pane —
            # and follow it with the one thing to do next, which depends on
            # WHICH kind of empty this is: a search with no matches is fixed by
            # clearing the search, an empty list by nothing at all.
            none = Gtk.Label(label=_t("No package selected"), xalign=0)
            none.get_style_context().add_class("insp-none")
            none.set_valign(Gtk.Align.START)
            self.detail.pack_start(none, False, False, 0)
            hint = Gtk.Label(
                label=(_t("Clear the search box to list every package.")
                       if self.query.strip()
                       else _t("Choose a package from the list.")),
                xalign=0)
            _wrap_to_panel(hint)
            hint.set_valign(Gtk.Align.START)
            hint.get_style_context().add_class("insp-note")
            self.detail.pack_start(hint, False, False, 0)
            self.detail.show_all()
            return

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        head.get_style_context().add_class("insp-head")
        head.pack_start(
            nbicons.image(p[KEY], 36, INK),
            False, False, 0)
        # An app added from a package is named with the version that was
        # installed — the same "Govorimo 2.0.0" the Sources page shows before
        # installing it, so the identity a person read there is the identity
        # they find here. It goes on the name rather than into a row of its
        # own: this panel does not scroll, and at 1024x740 a sixth row left
        # ONE pixel of height in Greek and Russian (measured), which is not a
        # margin — it is the next translation away from an app that cannot be
        # used on the smallest screen this OS supports.
        added = (_installed_registry().get(p[NAME])
                 if p[KIND] == "Application" else None)
        nm = Gtk.Label(
            label=("%s %s" % (p[NAME], added["version"])
                   if added and added.get("version") else p[NAME]),
            xalign=0)
        # A long package name folds onto a second line inside the panel; it
        # must never widen the panel (see _wrap_to_panel — that is what made
        # every name in the table ellipsize on a 1024-wide screen).
        _wrap_to_panel(nm)
        nm.set_valign(Gtk.Align.CENTER)
        nm.get_style_context().add_class("insp-name")
        head.pack_start(nm, True, True, 0)
        self.detail.pack_start(head, False, False, 0)

        # Description is parsed on demand for the selected package only (cached).
        desc_text = p[DESC] or _module_doc(p[PATH])
        if desc_text:
            desc = Gtk.Label(label=desc_text, xalign=0)
            _wrap_to_panel(desc)
            desc.get_style_context().add_class("insp-desc")
            self.detail.pack_start(desc, False, False, 0)

        table = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        table.get_style_context().add_class("insp-table")
        removed = (p[KIND] == "Application"
                   and p[NAME] in self._removed_apps)
        rows = [
            ("Kind", p[KIND]),
            # "File", not "Module": the row shows a file name, and a reader of
            # this window has no reason to know what a module is.
            ("File", os.path.basename(p[PATH])),
            ("Size", p[SIZE]),
            ("Modified", p[MODIFIED]),
            # Where this app came from — which is the difference between an app
            # that came with Notebook OS and one this machine was given later,
            # and the whole of what "keeping track of what is installed" means
            # on a fixed-image OS. For a shipped app the name is the one the
            # Sources page gives the machine: one place cannot call it "Local
            # Disk" while the other calls it "This computer".
            ("Source", _t("Installed from a package") if added
             else _t("This computer")),
        ]
        if p[KIND] == "Application":
            rows.append(("Applications", _t("Removed") if removed
                         else _t("Shown")))
        for i, (k, v) in enumerate(rows):
            r = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            r.get_style_context().add_class("insp-row")
            if i == len(rows) - 1:
                r.get_style_context().add_class("last")
            # _t() on the up-cased form: nbi18n looks the sentence-case key up
            # and up-cases the TRANSLATION the way that language does (Greek
            # drops the tonos, Turkish keeps its dotted I).
            kl = Gtk.Label(label=_t(k.upper()), xalign=0)
            kl.get_style_context().add_class("insp-k")
            kl.set_hexpand(True)
            r.pack_start(kl, True, True, 0)
            vl = Gtk.Label(label=v, xalign=1)
            vl.get_style_context().add_class("insp-v")
            r.pack_start(vl, False, False, 0)
            table.pack_start(r, False, False, 0)
        self.detail.pack_start(table, False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        btns.get_style_context().add_class("insp-btns")
        # This window is the only place in the system that says what each app
        # IS, in a sentence. Reading "Language - an offline course for learning
        # a new language" and then having to go and hunt for it in the Finder
        # is the gap between finding out something exists and using it, so the
        # description comes with a way in. System components are not launchable
        # surfaces, so they get no Open button rather than a dead one.
        if p[KIND] == "Application":
            op = Gtk.Button(label=_t("Open"))
            op.set_relief(Gtk.ReliefStyle.NONE)
            op.get_style_context().add_class("btn-open")
            op.set_tooltip_text(_t("Start this app."))
            op.connect("clicked", self._on_open)
            btns.pack_start(op, False, False, 0)
            visibility = Gtk.Button(
                label=_t("Restore") if removed else _t("Uninstall"))
            visibility.set_relief(Gtk.ReliefStyle.NONE)
            visibility.get_style_context().add_class("btn-primary")
            visibility.set_tooltip_text(
                _t("Show this app in Applications again.") if removed else
                _t("Remove this app from Applications."))
            visibility.connect("clicked", self._on_restore if removed
                               else self._on_uninstall)
            btns.pack_start(visibility, False, False, 0)
        # Verify re-reads the file and parses it to confirm the package is
        # intact. System components have no visibility control: the image is
        # read-only, so such a control would be a dead stub.
        ver = Gtk.Button(label=_t("Verify"))
        ver.set_relief(Gtk.ReliefStyle.NONE)
        ver.get_style_context().add_class("btn-primary")
        ver.set_tooltip_text(
            _t("Check that this package's file is present and undamaged."))
        ver.connect("clicked", self._on_verify)
        btns.pack_start(ver, False, False, 0)
        self.detail.pack_start(btns, False, False, 0)

        note = Gtk.Label(label=(
            _t("This app is removed from Applications. It stays on disk and "
               "can always be restored.") if removed else
            _t("Uninstall removes this app from Applications. It stays on "
               "disk and can always be restored.")
            if p[KIND] == "Application" else
            _t("System files stay in the read-only system image.")))
        _wrap_to_panel(note)
        note.set_xalign(0)
        note.get_style_context().add_class("insp-note")
        self.detail.pack_start(note, False, False, 0)

        if self._flash_src == self.sel and self._flash_text:
            flash = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            flash.set_valign(Gtk.Align.END)
            flash.set_vexpand(True)
            dot = Gtk.Label()
            dot.get_style_context().add_class("flashdot")
            # A dot, not a stripe. Packed with the default FILL alignment the
            # label was stretched to the height of the sentence beside it, and
            # a 50% radius on an 8x28 box draws a tall capsule — signage red,
            # the loudest thing in the panel, in the shape of a warning bar.
            # Pinned to the top and nudged down onto the first line's centre.
            dot.set_valign(Gtk.Align.START)
            dot.set_size_request(8, 8)
            dot.set_margin_top(5)
            # Signage-red is reserved for alerts: a failed verify is one.
            if self._flash_err:
                dot.get_style_context().add_class("err")
            flash.pack_start(dot, False, False, 0)
            ft = Gtk.Label(label=self._flash_text, xalign=0)
            # Same cap as every other label in this fixed-width panel: without
            # it a one-line result ("Opening Illustrator", a checked/not-checked
            # sentence) asks for its whole length as a natural width and widens
            # the inspector, squeezing the table beside it.
            _wrap_to_panel(ft)
            ft.get_style_context().add_class("flashtext")
            # expand=True: a wrap-to-panel label asks for ONE character of
            # natural width, so a non-expanding child is handed exactly that
            # and the sentence comes out one letter per line.
            flash.pack_start(ft, True, True, 0)
            self.detail.pack_start(flash, True, True, 0)

        self.detail.show_all()

    # ----------------------------------------------------- app visibility store
    def _removed_apps_path(self):
        home = os.environ.get("NB_HOME", os.path.expanduser("~"))
        return os.path.join(home, ".config", "notebook", "removed_apps.json")

    def _load_removed_apps(self):
        """Read Finder's exact list contract without repairing bad data."""
        self._removed_extra = {}
        self._removed_quarantine_pending = False
        path = self._removed_apps_path()
        try:
            import json
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return {str(item) for item in data}
            if isinstance(data, dict) and isinstance(data.get("removed"), list):
                known = {"removed", "view", "sort_field", "sort_desc"}
                self._removed_extra = {k: v for k, v in data.items()
                                       if k not in known}
                return {str(item) for item in data["removed"]}
        except (OSError, ValueError, TypeError):
            if not os.path.exists(path):
                return set()
        # A present store that did not match either recognized shape must not
        # be replaced by the close-time preference flush. Move its exact bytes
        # aside first, and refuse the write if that move cannot be completed.
        #
        # A store of ZERO bytes is not that store. It holds nothing to preserve
        # — it is an interrupted write or a full disk, not a shape this app
        # failed to understand — and nbapp.quarantine_unrecognized refuses to
        # move an empty file aside, so asking it to made the refusal permanent:
        # every Uninstall and Restore answered "This could not be saved.", on
        # that launch and on every launch afterwards, with nothing on disk to
        # lose. Write the fresh store over it instead.
        try:
            unreadable = os.path.getsize(path) > 0
        except OSError:
            unreadable = False
        self._removed_quarantine_pending = os.path.exists(path) and unreadable
        return set()

    def _load_view_prefs(self):
        """Restore the last section and installed-list ordering."""
        try:
            import json
            with open(self._removed_apps_path(), encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return
            if data.get("view") in ("installed", "updates", "sources"):
                self.view = data["view"]
            if data.get("sort_field") in (None, "name", "kind", "modified", "size"):
                self.sort_field = data.get("sort_field")
            if isinstance(data.get("sort_desc"), bool):
                self.sort_desc = data["sort_desc"]
        except (OSError, ValueError, TypeError):
            pass

    def _save_view_prefs(self):
        """Returns whether the store reached the disk. The caller acting on a
        real user action has to know: this handler swallows the same exception
        types the callers used to guard with, so a caller that only wrapped the
        call in its own try never heard about a failed write at all."""
        try:
            path = self._removed_apps_path()
            if getattr(self, "_removed_quarantine_pending", False):
                moved = nbapp.quarantine_unrecognized(path)
                if moved is None and os.path.exists(path):
                    raise OSError("could not preserve unrecognized app preferences")
                self._removed_quarantine_pending = False
            # getattr, not bare reads: this writer also runs from the destroy
            # handler, and a window torn down mid-construction (or a harness
            # driving the store methods alone) has real state for none of the
            # view fields yet. Losing a prefs write must never lose the
            # removed-apps store riding in the same file.
            payload = dict(getattr(self, "_removed_extra", {}) or {})
            payload.update({
                "removed": sorted(self._removed_apps),
                "view": getattr(self, "view", "installed"),
                "sort_field": getattr(self, "sort_field", None),
                "sort_desc": bool(getattr(self, "sort_desc", False)),
            })
            nbapp.atomic_write_json(path, payload)
            return True
        except (OSError, TypeError, ValueError) as exc:
            nbapp.note_save_failure(self, exc, self._removed_apps_path())
            return False

    def _set_app_removed(self, remove):
        """Read-modify-write the current store after a real user action."""
        try:
            p = PACKAGES[self.sel]
        except (IndexError, TypeError):
            return
        if p[KIND] != "Application":
            return
        current = self._load_removed_apps()
        if remove:
            current.add(p[NAME])
        else:
            current.discard(p[NAME])
        # Removing an application is a real user action with the person right
        # here looking at it, so a write that did not land is said in the
        # inspector rather than left for them to discover at the next launch —
        # the listing would show the app back with nothing to explain it.
        self._removed_apps = current
        if not self._save_view_prefs():
            # Back to what is actually recorded, read from the file rather than
            # remembered: the write failed, so the disk is what the next launch
            # will show, and the listing must not disagree with it in the
            # meantime.
            self._removed_apps = self._load_removed_apps()
            self._flash(getattr(self, "_save_error", None)
                        or nbapp.save_failure_reason(None), True)
            return
        # The list carries the marker too, so it has to be restated here — the
        # inspector alone would say "Removed" about a row that still reads as
        # shown. getattr, because the store methods are also driven headless.
        if getattr(self, "listbox", None) is not None:
            self._rebuild_list()
        self._rebuild_detail()

    def _on_uninstall(self, _button=None):
        self._set_app_removed(True)

    def _on_restore(self, _button=None):
        self._set_app_removed(False)

    def _on_open(self, _b=None):
        # Start the selected application, the same way the Finder does: a fresh
        # python3 running its module, with the desktop's own directory on
        # PYTHONPATH so its sibling modules import. Best-effort — a failure
        # says so in the inspector rather than doing nothing.
        if not isinstance(self.sel, int) or not (0 <= self.sel < len(PACKAGES)):
            return
        try:
            p = PACKAGES[self.sel]
        except (IndexError, TypeError):
            return
        signed, why = (nbtrust.check_path(p[PATH]) if nbtrust
                       else (False, "the trust module is missing"))
        if not signed:
            print("nbtrust: refused %s (%s)" % (p[PATH], why))
            self._flash(_t("This app can't be opened on this computer."), True)
            return
        try:
            subprocess.Popen(["python3", p[PATH]],
                             env=dict(os.environ, PYTHONPATH=DE_DIR))
            ok = True
        except OSError:
            ok = False
        self._flash(_t("Opening %s") % p[NAME] if ok
                    else _t("Could not open %s") % p[NAME], not ok)

    def _on_verify(self, _b=None):
        if not isinstance(self.sel, int) or not (0 <= self.sel < len(PACKAGES)):
            return
        try:
            p = PACKAGES[self.sel]
        except (IndexError, TypeError):
            return
        ok = self._verify_module(p[PATH])
        # Say what was found, not what the machine did. "Verify failed" reads as
        # though the person's own action went wrong, when what actually
        # happened is that the package could not be read.
        #
        # The two failures are NOT the same thing and must not share a line: a
        # file that cannot be read is a damaged disk, while a file that no
        # longer matches its signature has been CHANGED — the one case this
        # button exists for. Reporting a tampered package as unreadable would
        # be a falsehood exactly where the truth matters most.
        if ok:
            msg, bad = _t("Checked: this package is complete"), False
        else:
            _fine, why = (nbtrust.check_path(p[PATH]) if nbtrust
                          else (False, "file could not be read"))
            if "could not be read" in why:
                msg = _t("This package could not be read, so it "
                         "could not be checked")
            else:
                msg = _t("This package has been changed, so it can no "
                         "longer be opened.")
            bad = True
        self._flash(msg, bad)

    def _verify_module(self, path):
        """Is this package still exactly what was signed?

        It used to parse the file as Python and return True — which said
        nothing about integrity: any tampered file that still compiles passed,
        and the button reported "complete" over it. The answer now comes from
        the same signature the launcher enforces (nbtrust), so Verify and Open
        cannot disagree about whether a package is intact. The syntax parse is
        kept as a second, weaker question — a signed file that will not compile
        is also not usable — and never imports the module, because
        de/calendar.py shadows the stdlib."""
        if nbtrust is None:
            return False
        ok, _why = nbtrust.check_path(path)
        if not ok:
            return False
        try:
            ast.parse(_read_source_bounded(path))
            return True
        except (OSError, SyntaxError, ValueError):
            return False

    def _flash(self, text, err):
        """Show a one-line result in the inspector, and start the timer that
        takes it away again — replacing any timer still pending from an
        earlier result.

        The source id has to be kept. Without it, pressing Verify twice (or
        Open and then Verify) left the FIRST result's timer running, and it
        fired on its own schedule: _clear_flash only checks WHICH PACKAGE a
        result belongs to, not which result, so the second message was wiped
        the moment the first one's four seconds were up — a fraction of a
        second after it appeared, if the two presses were close together. The
        same untracked timer also outlived the window, waking up afterwards to
        rebuild an inspector that no longer exists."""
        self._cancel_flash_timer()
        self._flash_src = self.sel
        self._flash_err = err
        self._flash_text = text
        self._rebuild_detail()
        self._flash_timer = GLib.timeout_add_seconds(
            4, self._clear_flash, self.sel, self._flash_serial)

    def _cancel_flash_timer(self):
        # Crash-safe: a source that has already fired is gone, and dropping a
        # result line must never be able to take the app down.
        tid = self._flash_timer
        self._flash_timer = None
        self._flash_serial = getattr(self, "_flash_serial", 0) + 1
        if tid is not None:
            try:
                GLib.source_remove(tid)
            except Exception:
                pass

    def _clear_flash(self, which, serial=None):
        if serial is not None and serial != self._flash_serial:
            return False
        self._flash_timer = None      # this source is finishing; nothing to cancel
        if self._flash_src == which:
            self._flash_src = None
            self._rebuild_detail()
        return False

    _flash_text = ""
    _flash_err = False
    _flash_timer = None

    # ------------------------------------------------------------------ updates
    def _updates_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_valign(Gtk.Align.CENTER)
        page.set_halign(Gtk.Align.CENTER)

        page.pack_start(
            nbicons.image("update", 44, FAINT),
            False, False, 0)
        h = Gtk.Label(label=_t("No updates"))
        h.get_style_context().add_class("empty-h")
        page.pack_start(h, False, False, 0)
        s = Gtk.Label(label=_t(
            "Packages does not install updates. Notebook OS is delivered as "
            "a complete system image."))
        s.set_line_wrap(True)
        s.set_justify(Gtk.Justification.CENTER)
        s.set_max_width_chars(46)
        s.get_style_context().add_class("empty-s")
        page.pack_start(s, False, False, 0)
        return page

    # ------------------------------------------------------------------ sources
    @staticmethod
    def _unmount_esc(s):
        """Undo the octal escaping /proc/mounts applies to space, tab and
        backslash — the same unescape finder.py and settings.py do.

        automount.sh names a mount point after the volume's OWN label, and a
        label with a space in it is the ordinary case ("My Backup", "Family
        Photos"). /proc/mounts writes that as /media/My\\040Backup, so the
        Sources page read "Plugged in: My\\040Backup" — machine escaping shown
        to a person, in the one place this app has to name the thing they are
        holding — and the path could not be used to reach the stick either."""
        if "\\" not in s:
            return s
        for esc, ch in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"),
                        ("\\134", "\\")):
            s = s.replace(esc, ch)
        return s

    def _removable_media(self):
        # Real USB media, read live from /proc/mounts. automount.sh mounts
        # removable partitions under /media/<dev>, so restrict to that prefix to
        # report actual sticks — not the root or system pseudo-filesystems.
        out = []
        real_fs = {"vfat", "exfat", "ntfs", "ntfs3", "msdos", "udf",
                   "ext2", "ext3", "ext4", "iso9660", "f2fs"}
        try:
            with open("/proc/mounts") as fh:
                for ln in fh:
                    p = ln.split()
                    if len(p) < 3:
                        continue
                    dev, mnt, fstype = p[0], p[1], p[2]
                    mnt = self._unmount_esc(mnt)
                    if mnt == "/" or fstype not in real_fs:
                        continue
                    if not mnt.startswith("/media/"):
                        continue
                    out.append((os.path.basename(mnt) or os.path.basename(dev),
                                mnt))
        except OSError:
            pass
        return out

    def _sources_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.get_style_context().add_class("sources-page")

        title = Gtk.Label(label=_t("Sources"), xalign=0)
        title.get_style_context().add_class("pk-title")
        title.set_margin_bottom(20)
        page.pack_start(title, False, False, 0)

        # The source rows live in their own box so they can be re-scanned live
        # (see _refresh_sources / _on_nav) whenever the page is opened.
        self._sources_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.pack_start(self._sources_list, False, False, 0)

        note = Gtk.Label(
            label=_t("Sources lists storage attached to this computer."))
        note.set_line_wrap(True)
        # A readable measure, not one long line across a 1920px page. halign
        # must be pinned too: max_width_chars only caps the NATURAL width, and a
        # box child defaults to FILL, so the label would be stretched the full
        # width of the page and wrap there instead of at 72 characters.
        note.set_max_width_chars(72)
        note.set_halign(Gtk.Align.START)
        note.set_xalign(0)
        note.get_style_context().add_class("source-note")
        note.set_margin_top(20)
        page.pack_start(note, False, False, 0)

        self._refresh_sources()
        return page

    def _refresh_sources(self):
        box = getattr(self, "_sources_list", None)
        if box is None:
            return
        for c in box.get_children():
            box.remove(c)

        # Show whatever is actually plugged in now. The stick's OWN name is
        # what a person recognises — automount.sh
        # names the mount point after the volume label precisely so that name
        # can be shown here instead of "/media/sda1", which tells a reader
        # nothing and looks like a fault.
        media = self._removable_media()

        # Keep the sidebar badge honest with what is actually connected now
        # (Local Disk + any mounted sticks), so plugging a stick in and
        # reopening Sources ticks the count up.
        cnt = getattr(self, "_nav_count", {}).get("sources")
        if cnt is not None:
            try:
                cnt.set_text(str(1 + len(media)))
            except Exception:
                pass

        # Row names a person recognises: not "Local Disk" / "USB Media", which
        # are the names the machine keeps for its own parts.
        #
        # ONE ROW PER STICK, named the way the volume names itself. Two sticks
        # (a stick and a card reader is an ordinary pair) used to be joined
        # into a single row still titled "USB stick", singular, while the
        # sidebar counted them and said 3 — a page and a badge disagreeing
        # about what is plugged into the machine. Counting the rows is now the
        # same arithmetic as the badge: this computer, plus one per stick.
        n = len(PACKAGES)
        source_rows = [
            ("disk", _t("This computer"),
             _t("%d package%s installed") % (n, "" if n == 1 else "s"),
             _t("IN USE"), True),
        ]
        if media:
            source_rows.extend(("sources", label, _t("Plugged in"),
                                _t("IN USE"), True) for label, _mnt in media)
        else:
            source_rows.append(
                ("sources", _t("USB stick"), _t("No USB storage is connected"),
                 _t("NOT PRESENT"), False))
        for glyph, label, detail, status, active in source_rows:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
            row.get_style_context().add_class("source-row")
            row.pack_start(
                nbicons.image(glyph, 24, INK),
                False, False, 0)
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            txt.set_hexpand(True)
            lab = Gtk.Label(xalign=0)
            # The volume label is what the person wrote on the stick.
            nbi18n.set_verbatim(lab, label)
            # A row title is now a name the stick chose for itself, and a
            # volume label can be 40 characters — cap it the way the detail
            # line is capped, so no stick can widen this page.
            lab.set_ellipsize(Pango.EllipsizeMode.END)
            lab.set_max_width_chars(40)
            lab.get_style_context().add_class("source-label")
            txt.pack_start(lab, False, False, 0)
            det = Gtk.Label(label=detail, xalign=0)
            # A stick's own label can be 40 characters, and two sticks put two
            # of them on this line — cap the natural width so the page never
            # grows a horizontal scroll on a 1024 panel.
            det.set_ellipsize(Pango.EllipsizeMode.END)
            det.set_max_width_chars(46)
            det.get_style_context().add_class("source-detail")
            if glyph == "disk":
                # Held so an install can correct the count without rebuilding
                # the row the reader is looking at (see _refresh_after_install).
                self._installed_count_label = det
            txt.pack_start(det, False, False, 0)
            row.pack_start(txt, True, True, 0)
            # The chip text is already up-cased IN THE CATALOG, so each language
            # capitalises the way it actually does. str.upper() here would get
            # Turkish and Greek wrong (i -> I, and a retained tonos).
            chip = Gtk.Label(label=status)
            chip.set_valign(Gtk.Align.CENTER)
            chip.get_style_context().add_class(
                "chip-on" if active else "chip-off")
            row.pack_end(chip, False, False, 0)
            box.pack_start(row, False, False, 0)

        # Any signed .nbpkg on a plugged-in stick can be installed from here
        # (docs/NBPKG.md). Each is verified before it is offered — a package
        # that does not verify is shown as refused, never installed.
        self._show_installable(box, media)
        box.show_all()

    def _show_installable(self, box, media):
        if nbpkg_install is None:
            return
        packages = nbpkg_install.scan(m[1] for m in media)
        if not packages:
            return
        head = Gtk.Label(label=_t("Apps to install"), xalign=0)
        head.get_style_context().add_class("source-label")
        head.set_margin_top(18)
        box.pack_start(head, False, False, 0)
        rows = {}
        for path, name in packages:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
            row.get_style_context().add_class("source-row")
            row.pack_start(nbicons.image("box", 24, INK), False, False, 0)
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            txt.set_hexpand(True)
            # A package's name comes off the stick, not out of our catalog.
            lab = Gtk.Label(xalign=0)
            nbi18n.set_verbatim(lab, name)
            lab.get_style_context().add_class("source-label")
            txt.pack_start(lab, False, False, 0)
            det = Gtk.Label(label=_t("Preparing…"), xalign=0)
            det.set_line_wrap(True)
            det.set_max_width_chars(46)
            det.set_xalign(0)
            det.get_style_context().add_class("source-detail")
            txt.pack_start(det, False, False, 0)
            row.pack_start(txt, True, True, 0)
            btn = Gtk.Button(label=_t("Install"))
            btn.set_valign(Gtk.Align.CENTER)
            btn.set_sensitive(False)
            # The shipped theme has no rule for a button that cannot be used,
            # so an insensitive Install rendered pixel-identical to the live
            # one beside it: same ink, same paper, same border. This class
            # carries the theme's own UNAVAILABLE tones (see the css below).
            btn.get_style_context().add_class("pkg-install")
            btn.connect("clicked", self._on_install, path, det)
            row.pack_end(btn, False, False, 0)
            box.pack_start(row, False, False, 0)
            rows[path] = (name, lab, det, btn)

        def verify(job):
            out = []
            for path, name in packages:
                job.checkpoint()
                try:
                    manifest, _payloads = nbpkg_install.inspect(path)
                    out.append((path, name, manifest, True))
                except Exception:
                    out.append((path, name, None, False))
            return out

        def show(results):
            # What this machine already has, read at the moment the answer is
            # shown rather than trusted from launch: an install that happened
            # while this window was open wrote to it.
            registry = _installed_registry()
            for path, name, manifest, ok in results:
                widgets = rows.get(path)
                if not widgets:
                    continue
                _old_name, lab, det, btn = widgets
                if not ok:
                    nbi18n.set_verbatim(lab, name)
                    det.set_text(_t("This package can't be trusted and won't install."))
                    # There is no install to offer, so the control is withdrawn
                    # rather than left standing greyed. A refused package sat
                    # under a button that looked exactly like the working one
                    # next to it, which is an invitation to press it.
                    btn.set_sensitive(False)
                    btn.set_no_show_all(True)
                    btn.hide()
                    continue
                nbi18n.set_verbatim(
                    lab, "%s %s" % (manifest["name"], manifest["version"]))
                # Already on this computer? Rebuilding this page (leaving
                # Sources and coming back is enough) used to offer the very
                # package that had just been installed as though nothing had
                # happened — the one thing _refresh_after_install takes care
                # not to do. The registry nbpkg_install writes is the record;
                # a DIFFERENT version on the stick is a real install, so it is
                # still offered.
                have = registry.get(_manifest_display(manifest))
                if have and have.get("version") == manifest["version"]:
                    det.set_text(
                        _t("Installed %(name)s. Open it from Applications.")
                        % {"name": _manifest_display(manifest)})
                    btn.set_label(_t("Installed"))
                    btn.set_sensitive(False)
                else:
                    det.set_text(_t("Verified — ready to install"))
                    btn.set_sensitive(True)

        self._jobs.start("verify-media-packages", verify, on_done=show)

    def _on_install(self, btn, path, status_label):
        # Re-verify at the moment of install (the stick could have changed),
        # then install to the live system. Both can read/copy enough data to
        # take seconds, so keep them off GTK's event thread.
        target = os.environ.get("NB_PKG_TARGET", "/")
        btn.set_sensitive(False)
        status_label.set_text(_t("Preparing…"))

        def work(_job):
            return nbpkg_install.install(path, target=target)

        def failed(error):
            btn.set_sensitive(True)
            status_label.set_text(
                _t("Not installed — %s") % error.message)

        def finished(manifest):
            btn.set_label(_t("Installed"))
            status_label.set_text(
                _t("Installed %(name)s. Open it from Applications.")
                % {"name": manifest["app"]["display"]})
            # This window's answer to "what is on this machine" has to change
            # now, without requiring Packages to be reopened.
            self._refresh_after_install()

        started = self._jobs.start("install-package", work, on_done=finished,
                                   on_error=failed, policy=nbjobs.REJECT)
        if started is None:
            btn.set_sensitive(True)
            # REJECT means this package never entered a job; no callback will
            # arrive to replace the optimistic Preparing state.
            status_label.set_text(_t("Verified — ready to install"))

    def _refresh_after_install(self):
        """Re-scan the machine and put the result on screen."""
        # Read the selected package's NAME before the scan: self.sel is an
        # index into the old list, and every name sorted after the new app
        # shifts by one, so keeping the index would move the inspector onto a
        # package the reader never chose.
        chosen = None
        if isinstance(self.sel, int) and 0 <= self.sel < len(PACKAGES):
            chosen = PACKAGES[self.sel][NAME]
        refresh_installed()
        self.sel = next((i for i, p in enumerate(PACKAGES)
                         if p[NAME] == chosen), None)
        cnt = getattr(self, "_nav_count", {}).get("installed")
        if cnt is not None:
            try:
                cnt.set_text(str(len(PACKAGES)))
            except Exception:
                pass
        # Update the count on the page in place. Re-running _refresh_sources
        # here would rebuild the very row the reader is looking at, throwing
        # away the "Installed X" message and offering the Install button again
        # as though nothing had happened.
        det = getattr(self, "_installed_count_label", None)
        if det is not None:
            try:
                n = len(PACKAGES)
                det.set_text(_t("%d package%s installed")
                             % (n, "" if n == 1 else "s"))
            except Exception:
                pass
        if getattr(self, "listbox", None) is not None:
            self._rebuild_list()
            self._rebuild_detail()

    # ---------------------------------------------------------------------- css
    def _install_css(self):
        css = b"""
        * { font-family: "Nimbus Sans","Helvetica",sans-serif; }

        /* --- no-compositor black-safety: every surface paints an opaque
               page-tone so an unpainted stack/scroller/viewport never shows
               through as solid black on the software-rendered stack --- */
        .pk-stack { background: #FCFBF8; }
        .pk-page { background: #FCFBF8; }
        .pk-scroll { background: #FCFBF8; }
        .pk-scroll viewport { background: #FCFBF8; }

        /* --- sidebar: deeper papertone panel, strong hairline divider --- */
        .sidebar { background: #F1EEE6; border-right: 1px solid #C9C4B6;
                   padding: 24px 16px; }
        .navrow { padding: 10px 12px; border-radius: 6px; margin-bottom: 2px;
                  background: transparent; border: none; box-shadow: none; }
        .navrow:hover { background: #EFEBE0; }
        .navrow.active { background: #EAE3D2;
                         box-shadow: inset 3px 0 0 #C8341E; }
        .navlabel { font-size: 15px; color: #1A1916; font-weight: 500; }
        .navcount { font-size: 12px; color: #6E695E; }
        /* The ACTIVE row's ground is @select, where @muted is 4.27:1 --
           under AA for 12px. The count steps to @ink-3 on the one row you
           chose, so it is not the faintest thing in the sidebar exactly
           when you are reading it. */
        .navrow.active .navcount { color: #3A362E; }
        .sidenote { font-size: 12px; color: #6E695E; padding: 0 12px; }

        /* --- list header + search --- */
        /* 24px, not 28: the four pixels each side are the last of what the
           NAME column needed to hold "Application Framework" whole at 1024.
           The head, the column header and the rows share the one gutter, so
           the title, the labels and the names all still start on one line. */
        .pk-head { padding: 32px 24px 20px 24px; }
        .pk-title { font-size: 24px; font-weight: 700; color: #1A1916; }
        .searchbox { background: #F4F2EC; border: 1px solid #D7D2C5;
                     border-radius: 8px; padding: 0 11px; }
        .searchentry { background: transparent; border: none; box-shadow: none;
                       font-size: 13px; color: #1A1916; padding: 0; margin: 0;
                       min-height: 0; }

        /* --- table: uppercase tracked column labels over a strong hairline,
               soft hairline separators between rows --- */
        .colhdr { padding: 0 24px; min-height: 38px;
                  border-bottom: 1px solid #C9C4B6; }
        .cell-hdr { font-size: 11px; letter-spacing: 0.11em; color: #9A9484;
                    font-weight: 600; }
        .sorthdr { background: transparent; border: none; box-shadow: none;
                   color: #6E695E; padding: 0; margin: 0; min-height: 0; }
        .sorthdr label { color: inherit; }
        .sorthdr:hover { background: #EFEBE0; }
        .pk-list { background: #FCFBF8; }
        .datarow { padding: 0 24px; min-height: 46px;
                   border-bottom: 1px solid #D7D2C5;
                   background: transparent; border-radius: 0; }
        .datarow:hover { background: #F4F2EC; }
        .datarow.selected { background: #EAE3D2;
                            box-shadow: inset 3px 0 0 #C8341E; }
        .cell-name { font-size: 14px; color: #1A1916; font-weight: 500; }
        .cell-kind { font-size: 13px; color: #6E695E; }
        .cell-mono { font-size: 13px; color: #6E695E;
                     font-family: "Liberation Mono","DejaVu Sans Mono",monospace; }
        /* An app removed from Applications is printed faintly and keeps its
           place, so which apps are put away is readable from the list itself
           instead of one click at a time. Same UNAVAILABLE ink the theme
           names for a control that is there but cannot be used. */
        .datarow.removed .cell-name,
        .datarow.removed .cell-kind,
        .datarow.removed .cell-mono { color: #6E695E; }
        /* A SELECTED row is read against @select, and @muted measures 4.27:1
           there -- the quiet columns were at their faintest on the one row
           the pointer had chosen. @ink-3 keeps a visible step below the
           name's @ink and clears AA with room. */
        .datarow.selected .cell-kind,
        .datarow.selected .cell-mono { color: #3A362E; }
        .list-empty { padding: 48px 0; font-size: 13px; color: #6E695E; }

        /* --- inspector: lighter panel, strong hairline divider --- */
        .inspector { background: #F4F2EC; border-left: 1px solid #C9C4B6;
                     padding: 32px 28px; }
        .insp-none { font-size: 13px; color: #6E695E; }
        .insp-head { margin-bottom: 10px; }
        .insp-name { font-size: 20px; font-weight: 700; color: #1A1916; }
        .insp-desc { font-size: 14px; color: #6E695E; margin-bottom: 24px; }
        .insp-table { border-top: 1px solid #C9C4B6; }
        .insp-row { padding: 12px 0; border-bottom: 1px solid #D7D2C5; }
        .insp-row.last { border-bottom: none; }
        .insp-k { font-size: 11px; letter-spacing: 0.08em; color: #6E695E;
                  font-weight: 600; }
        .insp-v { font-size: 13px; color: #1A1916; }
        .insp-btns { margin-top: 24px; }
        /* Verify is a benign action, so the button is a warm-paper card with a
           darker-beige border. Signage red stays reserved for alerts and the
           active/selected chrome, never a decorative fill. */
        .btn-primary { background: #FCFBF8; color: #1A1916; border-radius: 8px;
                       font-size: 14px; font-weight: 600; min-height: 42px;
                       border: 1px solid #C9C4B6; box-shadow: none; }
        .btn-primary:hover { background: #EFEBE0; }
        /* Open is the one thing anyone actually wants from this window, so it
           reads as the primary: ink on a warmer card, above the paper-toned
           Verify. Still not signage red, which stays for alerts. */
        .btn-open { background: #EAE3D2; color: #1A1916; border-radius: 8px;
                    font-size: 14px; font-weight: 600; min-height: 42px;
                    border: 1px solid #C9C4B6; box-shadow: none; }
        .btn-open:hover { background: #DED4C2; }
        .insp-note { font-size: 12px; color: #6E695E; margin-top: 14px; }
        /* font-size is load-bearing, not decoration: this label has no text,
           but a label still asks for a whole LINE of height, so an 8px-wide
           box came out 13px tall and the 50% radius drew an oval. At 1px the
           line is smaller than the 8px minimum and the dot is round. */
        .flashdot { min-width: 8px; min-height: 8px; background: #6E695E;
                    border-radius: 50%; font-size: 1px; }
        /* a failed verify is an alert: signage red */
        .flashdot.err { background: #C8341E; }
        .flashtext { font-size: 13px; color: #6E695E; }

        /* --- empty / centred states --- */
        .empty-h { font-size: 17px; font-weight: 600; color: #1A1916; }
        .empty-s { font-size: 13px; color: #6E695E; }
        .empty-meta { font-size: 12px; color: #6E695E; }

        /* --- sources --- */
        .sources-page { padding: 32px 28px; }
        .source-row { padding: 20px 4px; border-bottom: 1px solid #D7D2C5; }
        .source-label { font-size: 15px; font-weight: 600; color: #1A1916; }
        .source-detail { font-size: 13px; color: #6E695E; }
        .source-note { font-size: 12px; color: #6E695E; }
        .chip-on { font-size: 10px; letter-spacing: 0.1em; padding: 4px 10px;
                   border-radius: 4px; background: #1A1916; color: #FCFBF8; }
        .chip-off { font-size: 10px; letter-spacing: 0.1em; padding: 4px 10px;
                    border-radius: 4px; background: transparent;
                    border: 1px solid #D7D2C5; color: #6E695E; }
        /* A control that cannot be used has to READ that way. The shipped
           theme carries :disabled rules for checks, radios, switches and menu
           items but none for a button, so an Install still being verified,
           or one already installed, was drawn in full ink on full paper,
           indistinguishable from the live button beside it. These are the
           theme's own UNAVAILABLE tones (@inkoff/@paperoff/@hairoff), written
           as hex because a colour named in another provider is not ours to
           rely on. The label needs saying separately: a colour set on a
           button never reaches the label inside it. */
        .pkg-install:disabled { background: #F1EEE6; border-color: #DDD8CB;
                                color: #A9A395; }
        .pkg-install:disabled label { color: #A9A395; }
        """
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            # Styling is cosmetic: a CSS parse error or a missing default
            # screen must not stop the app window from constructing.
            pass


if __name__ == "__main__":
    nbapp.run(Packages)
