#!/usr/bin/env python3
"""
Packages — the Notebook OS package manager (native GTK).

A sidebar of views (Installed / Updates / Sources) beside a sortable table of
the packages that make up the read-only system image, with a detail inspector
on the right. The Installed list is enumerated live from the desktop image on
disk (de/*.py): real names, sizes, and modification dates — nothing seeded. It
lists the applications and the system components a person can actually name;
internal plumbing modules are skipped (see _APP_NAMES / _SYS_NAMES).

This is an offline system with no network install path. New packages install
from a USB stick; a package format and SDK for that are planned, so for now the
installed set is fixed and read-only. A selected package can be verified (its
module file is re-read and parsed to confirm it is intact) but not removed.
Updates is an honest "up to date" surface — this system never polls. Sources
reports the local disk and any mounted USB media, read live from /proc/mounts.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa: E402

import os
import ast
import re
import subprocess
import time

import nbapp
import nbicons
from nbi18n import _t  # noqa: E402

INK = "#1A1916"
MUTED = "#6E695E"
FAINT = "#9A9484"

# Where the desktop image lives on disk. The Installed list is enumerated from
# here at launch, so it reflects the real package set, not a seeded catalog.
DE_DIR = os.path.dirname(os.path.abspath(__file__))

# columns: field indices into a package tuple
NAME, KEY, KIND, SIZE, SIZE_B, MODIFIED, MTIME, DESC, PATH = range(9)

# Layout widths. The smallest panel this OS supports is 1024 wide, and matchbox
# maximises every app to the screen, so the two fixed side panels plus the fixed
# table columns must leave a genuinely readable NAME column at 1024 — otherwise
# the package name is the one thing that ellipsizes away ("…Application") and
# the list becomes unusable on the machine it most needs to work on. Budget at
# 1024: 212 sidebar + 300 inspector + 56 gutters + 246 of fixed columns leaves
# ~135px for the name, and every extra pixel of a wider screen goes to it.
SIDEBAR_W = 212
INSPECTOR_W = 300
COL_KIND, COL_MODIFIED, COL_SIZE = 88, 92, 66
# The NAME header cell alone carries a floor, so the "NAME"/"KIND" labels can
# never run together into "NAMEKIND" when the table is at its narrowest.
COL_NAME_MIN = 120

# Display names for the modules that make up the image. These are labels for
# real files on disk (enumerated below), not a fabricated inventory. A module
# with no entry in EITHER map is internal plumbing (the X11 repaint helpers, the
# desktop backdrop painters, the GBA build step) — nothing a person can
# recognise, decide about or act on — so it is not listed at all rather than
# shown under its raw filename. Applications are the surfaces launched from the
# Finder (names match the Finder exactly); the system entries are the parts of
# the system a person can actually name.
_APP_NAMES = {
    "writer": "Writer", "novel": "Novel", "academics": "Academics",
    "journal": "Journal", "screenplay": "Screenplay", "tasks": "Tasks",
    "calendar": "Calendar", "cookbook": "Cookbook", "ebook": "E-book Reader",
    "calculator": "Calculator", "accounting": "Accounting",
    "contacts": "Contacts", "illustrator": "Illustrator",
    "sequencer": "Sequencer", "video": "Video Editor", "media": "Media Viewer",
    "music": "Music", "packages": "Packages", "g2048": "2048",
    "gbaemu": "GBA Emulator", "gbasdk": "GBA SDK", "language": "Language",
    "maps": "Maps", "workout": "Workout",
    "terminal": "Terminal", "settings": "Settings",
    "sysmon": "System Monitor",
}
_SYS_NAMES = {
    "finder": "Finder", "shell": "Desktop Panel",
    "nbapp": "Application Framework", "nbicons": "Icon Set",
    "widgets": "Desktop Widgets", "installer": "Installer",
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
    "ebook": "The Notebook OS e-book reader.",
    "gbaemu": "Plays Game Boy Advance games.",
    "gbasdk": "Builds Game Boy Advance games.",
    "illustrator": "The Notebook OS paint and drawing app.",
    "language": "A course for learning a new language.",
    "maps": "Maps of streets and places.",
    "packages": "Lists everything installed on this computer.",
    "sequencer": "The Notebook OS multi-track music maker.",
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
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            doc = ast.get_docstring(ast.parse(fh.read()))
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
    # appearing; add one and it shows up.
    out = []
    try:
        names = os.listdir(DE_DIR)
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".py"):
            continue
        mod = fn[:-3]
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
        self.sort_field = None
        self.sort_desc = False
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
        self.stack.add_named(self._installed_page(), "installed")
        self.stack.add_named(self._updates_page(), "updates")
        self.stack.add_named(self._sources_page(), "sources")
        body.pack_start(self.stack, True, True, 0)

        self._rebuild_list()
        self._rebuild_detail()

    # -------------------------------------------------------------------- menus
    def menu_items(self, name):
        if name == "Package":
            # Every entry is a real action. There is no "Remove" — the image is
            # read-only, so a permanently-disabled item would be a dead stub.
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
            return [op, verify, nbapp.SEP, ("Find…", self._focus_search), clear]
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
            row = Gtk.Button()
            row.set_relief(Gtk.ReliefStyle.NONE)
            row.get_style_context().add_class("navrow")
            hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            img = Gtk.Image.new_from_pixbuf(nbicons.pixbuf(glyph, 18, MUTED))
            hb.pack_start(img, False, False, 0)
            lab = Gtk.Label(label=label, xalign=0)
            lab.get_style_context().add_class("navlabel")
            hb.pack_start(lab, True, True, 0)
            cnt = Gtk.Label(label=count)
            cnt.get_style_context().add_class("navcount")
            hb.pack_end(cnt, False, False, 0)
            row.add(hb)
            row.connect("clicked", self._on_nav, vid)
            box.pack_start(row, False, False, 0)
            self._nav[vid] = row
            self._nav_count[vid] = cnt

        note = Gtk.Label(
            label=_t("New packages install from a USB stick."))
        note.get_style_context().add_class("sidenote")
        _wrap_to_panel(note)
        note.set_xalign(0)
        note.set_valign(Gtk.Align.END)
        box.pack_end(note, False, False, 0)

        self._nav["installed"].get_style_context().add_class("active")
        return box

    def _on_nav(self, _b, vid):
        self.view = vid
        for k, row in self._nav.items():
            ctx = row.get_style_context()
            if k == vid:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")
        # Re-scan removable media each time Sources is opened, so a USB stick
        # inserted after launch shows up (the page is otherwise a launch-time
        # snapshot). The scan is a tiny /proc/mounts read — never blocking.
        if vid == "sources":
            self._refresh_sources()
        self.stack.set_visible_child_name(vid)

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
            Gtk.Image.new_from_pixbuf(nbicons.pixbuf("search", 15, FAINT)),
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
            w = (vsb.get_allocated_width()
                 if (vsb is not None and vsb.get_visible()) else 0)
            if w != self._hdr_gutter:
                self._hdr_gutter = w
                self._colhdr.set_margin_end(w)
        except Exception:
            pass

    def _table_row(self, name, kind, modified, size, header=False,
                   glyph=None, index=None):
        if header:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        else:
            row = Gtk.Button()
            row.set_relief(Gtk.ReliefStyle.NONE)
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        # name cell (expands)
        namecell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        namecell.set_hexpand(True)
        if glyph is not None:
            namecell.pack_start(
                Gtk.Image.new_from_pixbuf(nbicons.pixbuf(glyph, 20, INK)),
                False, False, 0)
        nl = Gtk.Label(label=name, xalign=0)
        nl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        # A gutter the name can never spill into: the name cell takes all the
        # slack, so without it a long name butts straight against the KIND
        # column and the two read as one word ("CookbookApplication").
        nl.set_margin_end(16)
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
        return row

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
            if self.sort_field == field:
                self.sort_desc = not self.sort_desc
            else:
                self.sort_field = field
                self.sort_desc = False
            self._update_sort_labels()
            self._rebuild_list()
        except Exception:
            # Sorting is a convenience; never let a bad state crash the app.
            pass

    def _update_sort_labels(self):
        up = nbicons.pixbuf("up", 11, MUTED)
        # Vertical flip of the "up" glyph gives a down arrow without needing a
        # separate icon; flip() returns a fresh pixbuf so the cache is untouched.
        try:
            down = up.flip(False)
        except Exception:
            down = up
        for f, arrow in getattr(self, "_sort_labels", {}).items():
            try:
                if f == self.sort_field:
                    arrow.set_from_pixbuf(down if self.sort_desc else up)
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

    def _rebuild_list(self):
        for c in self.listbox.get_children():
            self.listbox.remove(c)
        self._rows = {}
        self._visible_order = []
        q = self.query.strip().lower()
        matched = [(i, p) for i, p in enumerate(PACKAGES)
                   if self._matches(p, q)]
        matched = self._sorted(matched)
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
                    glyph=p[KEY], index=i)
                row.get_style_context().add_class("datarow")
                if i == self.sel:
                    row.get_style_context().add_class("selected")
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
        q = self.query.strip().lower()
        visible = [i for i, p in enumerate(PACKAGES) if self._matches(p, q)]
        if self.sel not in visible:
            self.sel = visible[0] if visible else None
            self._flash_src = None
            self._rebuild_list()
            self._rebuild_detail()
        else:
            self._rebuild_list()

    def _on_entry_key(self, _w, ev):
        # Esc clears a non-empty search instead of quitting the app; an empty
        # box falls through so Esc still returns to the Finder.
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
        prev = self.sel
        if prev in self._rows:
            try:
                self._rows[prev].get_style_context().remove_class("selected")
            except Exception:
                pass
        self.sel = index
        row = self._rows.get(index)
        if row is not None:
            row.get_style_context().add_class("selected")
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
            Gtk.Image.new_from_pixbuf(nbicons.pixbuf(p[KEY], 36, INK)),
            False, False, 0)
        nm = Gtk.Label(label=p[NAME], xalign=0)
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
        rows = [
            ("Kind", p[KIND]),
            # "File", not "Module": the row shows a file name, and a reader of
            # this window has no reason to know what a module is.
            ("File", os.path.basename(p[PATH])),
            ("Size", p[SIZE]),
            ("Modified", p[MODIFIED]),
            # Same name the Sources page gives it: one place cannot call the
            # machine "Local Disk" while the other calls it "This computer".
            ("Source", _t("This computer")),
        ]
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
        # Verify re-reads the file and parses it to confirm the package is
        # intact. There is no Remove: the image is read-only, so a
        # permanently-disabled control would be a dead stub.
        ver = Gtk.Button(label=_t("Verify"))
        ver.set_relief(Gtk.ReliefStyle.NONE)
        ver.get_style_context().add_class("btn-primary")
        ver.set_tooltip_text(
            _t("Check that this package's file is present and undamaged."))
        ver.connect("clicked", self._on_verify)
        btns.pack_start(ver, False, False, 0)
        self.detail.pack_start(btns, False, False, 0)

        note = Gtk.Label(
            label=_t("Packages in this image can be verified. They cannot be "
                     "removed."))
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

    def _on_open(self, _b=None):
        # Start the selected application, the same way the Finder does: a fresh
        # python3 running its module, with the desktop's own directory on
        # PYTHONPATH so its sibling modules import. Best-effort — a failure
        # says so in the inspector rather than doing nothing.
        try:
            p = PACKAGES[self.sel]
        except (IndexError, TypeError):
            return
        try:
            subprocess.Popen(["python3", p[PATH]],
                             env=dict(os.environ, PYTHONPATH=DE_DIR))
            ok = True
        except OSError:
            ok = False
        self._flash_src = self.sel
        self._flash_err = not ok
        self._flash_text = (_t("Opening %s") % p[NAME] if ok
                            else _t("Could not open %s") % p[NAME])
        self._rebuild_detail()
        GLib.timeout_add_seconds(4, self._clear_flash, self.sel)

    def _on_verify(self, _b=None):
        try:
            p = PACKAGES[self.sel]
        except (IndexError, TypeError):
            return
        ok = self._verify_module(p[PATH])
        self._flash_src = self.sel
        self._flash_err = not ok
        # Say what was found, not what the machine did. "Verify failed" reads as
        # though the person's own action went wrong, when what actually
        # happened is that the package could not be read.
        self._flash_text = (_t("Checked: this package is complete") if ok
                            else _t("This package could not be read, so it "
                                    "could not be checked"))
        self._rebuild_detail()
        GLib.timeout_add_seconds(4, self._clear_flash, self.sel)

    def _verify_module(self, path):
        # A real integrity check: the module file must be present, readable, and
        # parse as valid Python. We parse the source text with ast — never
        # import it, because de/calendar.py shadows the stdlib and importing
        # would be an instant crash — so this is safe for every package.
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                ast.parse(fh.read())
            return True
        except (OSError, SyntaxError, ValueError):
            return False

    def _clear_flash(self, which):
        if self._flash_src == which:
            self._flash_src = None
            self._rebuild_detail()
        return False

    _flash_text = ""
    _flash_err = False

    # ------------------------------------------------------------------ updates
    def _updates_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_valign(Gtk.Align.CENTER)
        page.set_halign(Gtk.Align.CENTER)

        page.pack_start(
            Gtk.Image.new_from_pixbuf(nbicons.pixbuf("update", 44, FAINT)),
            False, False, 0)
        h = Gtk.Label(label=_t("No updates"))
        h.get_style_context().add_class("empty-h")
        page.pack_start(h, False, False, 0)
        s = Gtk.Label(label=_t("Package updates install from a USB stick."))
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
            label=_t("New packages install from a USB stick."))
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

        # USB sticks are the install path; show whatever is actually plugged in
        # now. The stick's OWN name is what a person recognises — automount.sh
        # names the mount point after the volume label precisely so that name
        # can be shown here instead of "/media/sda1", which tells a reader
        # nothing and looks like a fault.
        media = self._removable_media()
        if media:
            usb_detail = _t("Plugged in: %s") % ", ".join(m[0] for m in media)
            usb_status, usb_active = _t("IN USE"), True
        else:
            # The empty state carries the next step rather than only the news.
            usb_detail = _t("Plug one in to install new packages")
            usb_status, usb_active = _t("NOT PRESENT"), False

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
        n = len(PACKAGES)
        for glyph, label, detail, status, active in (
            ("disk", _t("This computer"),
             _t("%d package%s installed") % (n, "" if n == 1 else "s"),
             _t("IN USE"), True),
            ("sources", _t("USB stick"), usb_detail, usb_status, usb_active),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
            row.get_style_context().add_class("source-row")
            row.pack_start(
                Gtk.Image.new_from_pixbuf(nbicons.pixbuf(glyph, 24, INK)),
                False, False, 0)
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            txt.set_hexpand(True)
            lab = Gtk.Label(label=label, xalign=0)
            lab.get_style_context().add_class("source-label")
            txt.pack_start(lab, False, False, 0)
            det = Gtk.Label(label=detail, xalign=0)
            # A stick's own label can be 40 characters, and two sticks put two
            # of them on this line — cap the natural width so the page never
            # grows a horizontal scroll on a 1024 panel.
            det.set_ellipsize(Pango.EllipsizeMode.END)
            det.set_max_width_chars(46)
            det.get_style_context().add_class("source-detail")
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
        box.show_all()

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
        .navrow { padding: 10px 12px; border-radius: 2px; margin-bottom: 2px;
                  background: transparent; border: none; box-shadow: none; }
        .navrow:hover { background: #EAE5D9; }
        .navrow.active { background: #E6E0D2;
                         box-shadow: inset 3px 0 0 #C8341E; }
        .navlabel { font-size: 15px; color: #1A1916; font-weight: 500; }
        .navcount { font-size: 12px; color: #9A9484; }
        .sidenote { font-size: 12px; color: #9A9484; padding: 0 12px; }

        /* --- list header + search --- */
        .pk-head { padding: 32px 28px 20px 28px; }
        .pk-title { font-size: 26px; font-weight: 700; color: #1A1916; }
        .searchbox { background: #F4F2EC; border: 1px solid #D7D2C5;
                     border-radius: 2px; padding: 0 11px; }
        .searchentry { background: transparent; border: none; box-shadow: none;
                       font-size: 13px; color: #1A1916; padding: 0; margin: 0;
                       min-height: 0; }

        /* --- table: uppercase tracked column labels over a strong hairline,
               soft hairline separators between rows --- */
        .colhdr { padding: 0 28px; min-height: 38px;
                  border-bottom: 1px solid #C9C4B6; }
        .cell-hdr { font-size: 11px; letter-spacing: 0.11em; color: #9A9484;
                    font-weight: 600; }
        .sorthdr { background: transparent; border: none; box-shadow: none;
                   padding: 0; margin: 0; min-height: 0; }
        .sorthdr:hover { background: #EAE5D9; }
        .pk-list { background: #FCFBF8; }
        .datarow { padding: 0 28px; min-height: 46px;
                   border-bottom: 1px solid #D7D2C5;
                   background: transparent; border-radius: 0; }
        .datarow:hover { background: #F4F2EC; }
        .datarow.selected { background: #EDE8DC;
                            box-shadow: inset 3px 0 0 #C8341E; }
        .cell-name { font-size: 14px; color: #1A1916; font-weight: 500; }
        .cell-kind { font-size: 13px; color: #6E695E; }
        .cell-mono { font-size: 13px; color: #6E695E;
                     font-family: "Liberation Mono","DejaVu Sans Mono",monospace; }
        .list-empty { padding: 48px 0; font-size: 13px; color: #9A9484; }

        /* --- inspector: lighter panel, strong hairline divider --- */
        .inspector { background: #F4F2EC; border-left: 1px solid #C9C4B6;
                     padding: 32px 28px; }
        .insp-none { font-size: 13px; color: #9A9484; }
        .insp-head { margin-bottom: 10px; }
        .insp-name { font-size: 21px; font-weight: 700; color: #1A1916; }
        .insp-desc { font-size: 14px; color: #6E695E; margin-bottom: 24px; }
        .insp-table { border-top: 1px solid #C9C4B6; }
        .insp-row { padding: 12px 0; border-bottom: 1px solid #D7D2C5; }
        .insp-row.last { border-bottom: none; }
        .insp-k { font-size: 11px; letter-spacing: 0.08em; color: #9A9484;
                  font-weight: 600; }
        .insp-v { font-size: 13px; color: #1A1916; }
        .insp-btns { margin-top: 24px; }
        /* Verify is a benign action, so the button is a warm-paper card with a
           darker-beige border. Signage red stays reserved for alerts and the
           active/selected chrome, never a decorative fill. */
        .btn-primary { background: #FCFBF8; color: #1A1916; border-radius: 2px;
                       font-size: 14px; font-weight: 600; min-height: 42px;
                       border: 1px solid #C4BFB1; box-shadow: none; }
        .btn-primary:hover { background: #ECE8DD; }
        /* Open is the one thing anyone actually wants from this window, so it
           reads as the primary: ink on a warmer card, above the paper-toned
           Verify. Still not signage red, which stays for alerts. */
        .btn-open { background: #EAE3D2; color: #1A1916; border-radius: 2px;
                    font-size: 14px; font-weight: 600; min-height: 42px;
                    border: 1px solid #C4BFB1; box-shadow: none; }
        .btn-open:hover { background: #E2D9C3; }
        .insp-note { font-size: 12px; color: #9A9484; margin-top: 14px; }
        .flashdot { min-width: 8px; min-height: 8px; background: #6E695E;
                    border-radius: 50%; }
        /* a failed verify is an alert: signage red */
        .flashdot.err { background: #C8341E; }
        .flashtext { font-size: 13px; color: #6E695E; }

        /* --- empty / centred states --- */
        .empty-h { font-size: 18px; font-weight: 600; color: #1A1916; }
        .empty-s { font-size: 13px; color: #9A9484; }
        .empty-meta { font-size: 12px; color: #9A9484; }

        /* --- sources --- */
        .sources-page { padding: 32px 28px; }
        .source-row { padding: 20px 4px; border-bottom: 1px solid #D7D2C5; }
        .source-label { font-size: 15px; font-weight: 600; color: #1A1916; }
        .source-detail { font-size: 13px; color: #6E695E; }
        .source-note { font-size: 12px; color: #9A9484; }
        .chip-on { font-size: 10px; letter-spacing: 0.1em; padding: 4px 10px;
                   border-radius: 2px; background: #1A1916; color: #FCFBF8; }
        .chip-off { font-size: 10px; letter-spacing: 0.1em; padding: 4px 10px;
                    border-radius: 2px; background: transparent;
                    border: 1px solid #D7D2C5; color: #9A9484; }
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
