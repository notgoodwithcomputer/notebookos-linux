"""nbcommands — the canonical command vocabulary of NotebookOS.

One place that owns, for every command the OS offers more than once:

  * its **id** (`file.save`), stable and never shown to anyone;
  * its **English source label** in Title Case ("Save As"), which is the key
    the translation catalogs are written against;
  * whether it takes an **ellipsis** — the promise, per
    `docs/MENU-CONVENTIONS.md` §1, that it will ASK before anything happens;
  * its **standard shortcut text** ("Ctrl+Shift+S"), rendered into the label
    with the four spaces `docs/MENU-CONVENTIONS.md` §3 requires;
  * its **menu, group and order**, so App/File/Edit/View/Help entries sort and
    separate the same way in every app (Constitution Article I §1);
  * whether it is **destructive**, so a caller can tell at a glance which
    commands must route through `nbapp.confirm()`.

Why a module and not a doc: the labels drifted precisely because each app
spelled them out by hand — "Zoom In    Ctrl++" here, "Zoom In    Ctrl+Plus"
there, "Export to PDF" with and without an ellipsis for the same code path.
A string that exists once cannot disagree with itself.

**This module imports no GTK.** It is pure data plus small helpers, so the
selftest, the static audits and any headless tool can import it on a machine
with no display and no PyGObject at all. The only import is the shared
translation layer, and even that degrades to identity when it is unavailable.

Compatibility is the whole point: `item()` returns exactly the
`(label, callback)` tuple `AppWindow.menu_items()` has always returned, and
`SEP` is the same `("-", None)` separator, so an app adopts the registry one
entry at a time and every existing menu keeps working untouched.

Translation happens EXACTLY ONCE. `AppWindow._open_menu()` translates every
label as it builds the dropdown, so the helpers here return **English source
labels** and leave it at that. The one exception is `about_label()`, whose
label is composed from two catalog keys ("About %s" and the app name) and so
must be assembled after translation, exactly as `AppWindow` has always done.
"""

try:
    from nbi18n import _t
except Exception:                                    # pragma: no cover
    def _t(s):
        return s


SEP = ("-", None)   # identical to nbapp.SEP — a separator entry in a menu list

#: four spaces before the key, per docs/MENU-CONVENTIONS.md §3
GAP = "    "

#: menu-bar titles, left to right. APP is the app-name button, which
#: AppWindow._menubar() always draws first; app-specific menus (Format, Layer,
#: Transport, Cook…) sit between VIEW and HELP and are the app's own business.
APP, FILE, EDIT, VIEW, HELP = "App", "File", "Edit", "View", "Help"
MENUS = (APP, FILE, EDIT, VIEW, HELP)

# Groups within a menu, separated by SEP. The numbers are the group order of
# Constitution Article I §1; the gaps leave room for an app's own entries.
G_CREATE = 10       # New, New <Thing>, Open…, Open Recent
G_PERSIST = 20      # Save, Save As…            (document apps only)
G_EMIT = 30         # Export…, Print…
G_DESTROY = 40      # Delete <Thing>…
G_EXIT = 50         # Close    Esc — always last, always present

G_HISTORY = 10      # Undo / Redo
G_CLIPBOARD = 20    # Cut / Copy / Paste
G_SELECT = 30       # Select All
G_SEARCH = 40       # Find

G_ZOOM = 10         # Zoom In / Zoom Out / Actual Size
G_SCREEN = 20       # Full Screen

G_INFO = 10         # About
G_PREFS = 20        # Settings…
G_HELP = 10         # Help topics


class Command:
    """One command. Immutable by convention; there is exactly one per id."""

    __slots__ = ("id", "title", "menu", "group", "order", "shortcut",
                 "ellipsis", "destructive", "dynamic", "note")

    def __init__(self, cid, title, menu, group, order, shortcut="",
                 ellipsis=False, destructive=False, dynamic=False, note=""):
        self.id = cid
        self.title = title
        self.menu = menu
        self.group = group
        self.order = order
        self.shortcut = shortcut
        self.ellipsis = ellipsis
        self.destructive = destructive
        self.dynamic = dynamic
        self.note = note

    # -- labels --
    @property
    def name(self):
        """The label without its accelerator: 'Save As…'."""
        return self.title + ("…" if self.ellipsis else "")

    @property
    def source_label(self):
        """The full English menu label: 'Save As…    Ctrl+Shift+S'."""
        return self.name + (GAP + self.shortcut if self.shortcut else "")

    def framed_label(self, frame="%s"):
        """The label with `frame` substituted for the command's own name.

        Used by the dynamic Undo/Redo pair, where the item names the action it
        would reverse: framed_label('%s') -> 'Undo %s    Ctrl+Z'."""
        return (self.title + " " + frame
                + (GAP + self.shortcut if self.shortcut else ""))

    def __repr__(self):                              # pragma: no cover
        return "<Command %s %r>" % (self.id, self.source_label)


def _c(*a, **kw):
    return Command(*a, **kw)


#: The vocabulary. Ordered as it is declared; `menu_order()` sorts by
#: (group, order) so a caller never has to remember the numbers.
_LIST = [
    # ---- the app-name menu ----
    _c("app.about", "About", APP, G_INFO, 10,
       note="label is composed per app — use about_label()"),
    _c("app.settings", "Settings", APP, G_PREFS, 10, ellipsis=True,
       note="opens the Settings app / a preferences surface"),
    _c("app.close", "Close", APP, G_EXIT, 10, shortcut="Esc",
       note="Esc leaves; it never destroys data (Article II)"),
    _c("app.quit", "Quit", APP, G_EXIT, 20, shortcut="Ctrl+Q",
       note="one app per process: quitting IS closing. Ctrl+Q is suppressed "
            "in the terminal, which needs the raw key — an app that sets "
            "self.term must not print this label."),

    # ---- File ----
    _c("file.new", "New", FILE, G_CREATE, 10, shortcut="Ctrl+N"),
    _c("file.open", "Open", FILE, G_CREATE, 20, shortcut="Ctrl+O",
       ellipsis=True, note="always a picker, so always an ellipsis"),
    _c("file.open_recent", "Open Recent", FILE, G_CREATE, 30,
       note="a submenu/section header — it asks nothing by itself, so no "
            "ellipsis; the entries under it open immediately"),
    _c("file.save", "Save", FILE, G_PERSIST, 10, shortcut="Ctrl+S",
       note="writes now — never an ellipsis"),
    _c("file.save_as", "Save As", FILE, G_PERSIST, 20,
       shortcut="Ctrl+Shift+S", ellipsis=True),
    _c("file.export", "Export", FILE, G_EMIT, 10, ellipsis=True,
       note="asks where/what — the picker form"),
    _c("file.export_pdf", "Export to PDF", FILE, G_EMIT, 20,
       note="writes straight to $NB_HOME/Documents. NOT the same command as "
            "file.export_pdf_as; MENU-CONVENTIONS §1 forbids unifying them"),
    _c("file.export_pdf_as", "Export to PDF", FILE, G_EMIT, 30, ellipsis=True,
       note="opens nbpicker first"),
    _c("file.print", "Print", FILE, G_EMIT, 40, shortcut="Ctrl+P",
       ellipsis=True, note="the print dialog always asks"),
    _c("file.close", "Close", FILE, G_EXIT, 10, shortcut="Esc",
       note="same command as app.close, offered in both menus"),

    # ---- Edit ----
    _c("edit.undo", "Undo", EDIT, G_HISTORY, 10, shortcut="Ctrl+Z",
       dynamic=True, note="names what it reverses: 'Undo Delete Chapter'"),
    _c("edit.redo", "Redo", EDIT, G_HISTORY, 20, shortcut="Ctrl+Shift+Z",
       dynamic=True, note="Ctrl+Y is also bound but Ctrl+Shift+Z is printed"),
    _c("edit.cut", "Cut", EDIT, G_CLIPBOARD, 10, shortcut="Ctrl+X"),
    _c("edit.copy", "Copy", EDIT, G_CLIPBOARD, 20, shortcut="Ctrl+C"),
    _c("edit.paste", "Paste", EDIT, G_CLIPBOARD, 30, shortcut="Ctrl+V"),
    _c("edit.select_all", "Select All", EDIT, G_SELECT, 10, shortcut="Ctrl+A"),
    _c("edit.find", "Find", EDIT, G_SEARCH, 10, shortcut="Ctrl+F",
       note="an inline bar in every app that has one, so NO ellipsis. Apps "
            "name their haystack ('Find in Script') — that is a per-app "
            "title, not a different command."),

    # ---- View ----
    _c("view.zoom_in", "Zoom In", VIEW, G_ZOOM, 10, shortcut="Ctrl+Plus"),
    _c("view.zoom_out", "Zoom Out", VIEW, G_ZOOM, 20, shortcut="Ctrl+Minus"),
    _c("view.actual_size", "Actual Size", VIEW, G_ZOOM, 30, shortcut="Ctrl+0"),
    _c("view.fit_window", "Fit in Window", VIEW, G_ZOOM, 40, shortcut="Ctrl+9"),
    _c("view.fullscreen", "Full Screen", VIEW, G_SCREEN, 10,
       note="every app is already fullscreen; this is the chrome-free view "
            "some media surfaces offer. It binds no OS-wide key, so it "
            "prints none (Article I §3 runs in both directions)."),
    _c("view.exit_fullscreen", "Exit Full Screen", VIEW, G_SCREEN, 20,
       shortcut="Esc"),

    # ---- Help ----
    _c("help.help", "Help", HELP, G_HELP, 10),
    _c("help.shortcuts", "Keyboard Shortcuts", HELP, G_HELP, 20),
]

COMMANDS = {}
for _cmd in _LIST:
    if _cmd.id in COMMANDS:                          # pragma: no cover
        raise ValueError("duplicate command id: " + _cmd.id)
    COMMANDS[_cmd.id] = _cmd
del _cmd


# ---------------------------------------------------------------- lookups --

def get(cid):
    """The Command for `cid`. Raises KeyError on a typo — loudly, at import
    or first open, rather than shipping a menu with a missing entry."""
    return COMMANDS[cid]


def source_label(cid):
    """The English label, e.g. 'Save As…    Ctrl+Shift+S'."""
    return COMMANDS[cid].source_label


def label(cid):
    """The translated label, for a caller that is NOT going through
    AppWindow._open_menu() (which translates on its own)."""
    return _t(source_label(cid))


def shortcut(cid):
    return COMMANDS[cid].shortcut


def is_destructive(cid):
    return COMMANDS[cid].destructive


def menu_order(menu):
    """Every command in `menu`, in group/order sequence."""
    return sorted((c for c in _LIST if c.menu == menu),
                  key=lambda c: (c.group, c.order))


# ------------------------------------------------------------- menu items --

def item(cid, callback):
    """The `(label, callback)` tuple menu_items() has always returned.

    `callback=None` keeps the entry VISIBLE and greys it out — Article I §3
    and MENU-CONVENTIONS §5. Never drop an item to disable it."""
    return (source_label(cid), callback)


def items(*specs):
    """Build a menu list from (command id, callback) pairs.

    Inserts `SEP` between commands from different groups, so the grouping of
    Article I §1 follows from the registry instead of from each app
    remembering where the separators go. A pair whose callback is None still
    appears (disabled); pass a literal SEP to force one."""
    out = []
    prev = None
    for spec in specs:
        if spec == SEP:
            out.append(SEP)
            prev = None
            continue
        cid, cb = spec
        grp = COMMANDS[cid].group
        if prev is not None and grp != prev:
            out.append(SEP)
        out.append(item(cid, cb))
        prev = grp
    return out


def about_label(app_name):
    """'About Writer', translated — the one label composed from two keys.

    Kept here so the App menu of every app words it identically, and composed
    AFTER translation because "About %s" and the app name are separate catalog
    entries. `_open_menu` will _t() the result again; that is a catalog miss
    and returns it unchanged, so the string is translated exactly once."""
    return _t("About %s") % _t(app_name)


def dynamic_item(cid, name, enabled, callback):
    """One half of the Undo/Redo pair.

    Named after the action it would reverse when there is one ("Undo Delete
    Chapter"), plain otherwise, and disabled-but-visible when `enabled` is
    false. Translated here rather than by the caller because the framed form
    is a printf key that must be filled AFTER lookup — and `_open_menu`'s own
    _t() then misses, leaving it translated exactly once."""
    cmd = COMMANDS[cid]
    if not enabled:
        return (cmd.source_label, None)
    if not name:
        return (_t(cmd.source_label), callback)
    return (_t(cmd.framed_label("%s")) % name, callback)


# -------------------------------------------------------- base app menus --
# The three menus AppWindow itself owns. Kept here so the base class cannot
# drift from the registry, and so a reader can see the whole default in one
# place. Each takes the callbacks it needs and returns a ready menu list.

def app_menu(app_name, about, close):
    """The app-name menu: About <App>, then Close.

    Settings is deliberately NOT here: it is an app in its own right, not a
    per-app preferences sheet, and offering it in every menu bar would promise
    a per-app surface that does not exist."""
    return [(about_label(app_name), about), SEP, item("app.close", close)]


def file_menu(close):
    """The base File menu: the exit group only.

    An app with documents or things to create prepends its own groups; this
    guarantees the last entry is always Close (Article I §1, group 5)."""
    return [item("file.close", close)]


def edit_menu(cut, copy, paste, select_all, shortcuts=False):
    """The base Edit menu: clipboard, then Select All.

    `shortcuts=False` keeps the base labels bare, which is what the untranslated
    catalogs have keys for and what every app that does not override this menu
    has always shown. An editor that binds and prints the accelerators passes
    shortcuts=True (or builds the list itself with `items()`), which is what
    Writer and the other editors already print by hand."""
    pairs = [("edit.cut", cut), ("edit.copy", copy),
             ("edit.paste", paste), ("edit.select_all", select_all)]
    if shortcuts:
        return items(*pairs)
    out = []
    prev = None
    for cid, cb in pairs:
        grp = COMMANDS[cid].group
        if prev is not None and grp != prev:
            out.append(SEP)
        out.append((COMMANDS[cid].name, cb))
        prev = grp
    return out
