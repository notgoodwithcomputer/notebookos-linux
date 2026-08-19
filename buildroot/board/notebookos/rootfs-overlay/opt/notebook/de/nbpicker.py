#!/usr/bin/env python3
"""nbpicker — a Finder-style modal file picker, drawn OVER the calling app.

Apps call nbpicker.open_file() / nbpicker.save_file() instead of building a
Gtk.FileChooserDialog, so every open/save uses the SAME papertone engine as the
Finder: a Places sidebar rooted at $NB_HOME, the Name/Kind/Size/Date list (or a
big-icon grid), a breadcrumb trail, and type-to-filter search.

It is an UNDECORATED MODAL Gtk.Dialog (exactly how calendar.py's dialogs are
built — proven to render reliably under matchbox with no compositor, and the
one form that is genuinely safe here). The dialog supplies the modal grab, the
nested run loop and Escape=cancel for free, so an app's _choose_file() stays a
plain synchronous call: `path = nbpicker.open_file(self, ...)`.

Reuses finder.py wholesale: finder.list_dir (the shared disk walker),
finder.icon_for/finder.kind_for, finder.PLACES, finder.HOME, finder.Crumbs and
finder.install_css (the .finder CSS). The picker IS the Finder, not a lookalike.
"""
import os
import fnmatch

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GObject  # noqa: E402

import nbicons
import nbapp
import nbmotion
import finder
from nbi18n import _t


def _visible_leaf(name):
    """A single non-hidden name the picker can show again after creating it."""
    return bool(name) and not name.startswith(".") \
        and os.sep not in name and not (os.altsep and os.altsep in name)

LIST_ICON_PX = 20
GRID_ICON_PX = 84

_CSS = b"""
.nbpicker .pickerfooter { background: #F1EEE6; border-top: 1px solid #C9C4B6;
                          padding: 10px 16px; }
.nbpicker .pickername { background: #FCFBF8; color: #1A1916;
                        border: 1px solid #C9C4B6; border-radius: 8px;
                        padding: 5px 8px; }
.nbpicker .pickerok { padding: 6px 20px; background: #C8341E; color: #FCFBF8;
                      border: 1px solid #C8341E; border-radius: 8px;
                      box-shadow: none; font-size: 13px; font-weight: 600; }
.nbpicker .pickerok:hover { background: #B12D19; border-color: #B12D19; }
/* #E0B8B0 is deliberately off-palette: it is the OS-wide "primary action, not
   available yet" tint (settings, nbprint, usbwriter and the installer all use
   the same value), and the palette has no desaturated accent to express it.
   It also has to stay dark enough to carry the #FCFBF8 label below. */
.nbpicker .pickerok:disabled { background: #E0B8B0; border-color: #E0B8B0;
                               color: #FCFBF8; }
.nbpicker .pickercancel { padding: 6px 18px; background: #FCFBF8; color: #2A2620;
                          border: 1px solid #C9C4B6; border-radius: 8px;
                          box-shadow: none; font-size: 13px; }
.nbpicker .pickercancel:hover { background: #F1EEE6; }
.nbpicker .pickerfooter .pickerwarn,
.nbpicker .pickerwarn { color: #C8341E; font-size: 12px; }
.nbpicker .pickerempty { color: #8A857A; font-size: 14px; background: #FCFBF8; }
/* The footer caption ("Save As:") is a bare label sitting DIRECTLY in the
   footer box. It has to be selected as such: as a plain descendant rule this
   also matched the label node inside every BUTTON in the footer, and since a
   colour declared on a label node beats one inherited from the button, the
   OS-wide Save / Open button painted its word in muted grey on signage red --
   barely readable, in every app that opens this dialog. The two button rules
   below carry the footer class as well so they outrank it wherever GTK still
   prefers the later rule. */
.nbpicker .pickerfooter > label { color: #6E695E; font-size: 13px; }
.nbpicker .pickerfooter .pickerok label,
.nbpicker .pickerok label { color: #FCFBF8; }
.nbpicker .pickerfooter .pickerok:disabled label { color: #FCFBF8; }
.nbpicker .pickerfooter .pickercancel label,
.nbpicker .pickercancel label { color: #2A2620; }
.nbpicker .pickernewfolder { padding: 4px 10px; border-radius: 8px; }
.nbpicker .pickernewfolder:hover { background: #EAE3D2; }
.nbpicker .pickernewfolder label { color: #2A2620; font-size: 12px; }
.nbpicker .pickerfooter .pickerdlgtitle,
.nbpicker .pickerdlgtitle { color: #1A1916; font-size: 16px; font-weight: 700; }
.nbpicker .pickerfooter .pickerdlgmsg,
.nbpicker .pickerdlgmsg { color: #2A2620; font-size: 13px; }
"""
_CSS_DONE = False


def _install_css():
    global _CSS_DONE
    finder.install_css()                 # register the .finder chrome (guarded)
    if _CSS_DONE:
        return
    prov = Gtk.CssProvider()
    prov.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _CSS_DONE = True


def open_file(parent, title="Open", start_dir=None, patterns=None):
    """Finder-style Open picker over `parent` (an nbapp.AppWindow). Returns the
    chosen absolute path, or None if cancelled. `patterns` is a list of shell
    globs ('*.txt', ...) — only matching files show (folders always show)."""
    return _Picker(parent, "open", title, start_dir, "", patterns, None).run()


def save_file(parent, title="Save As", start_dir=None, suggested_name="untitled",
              patterns=None, default_ext=None):
    """Finder-style Save picker over `parent`. Returns the chosen absolute path
    (folder + typed name) or None. When `default_ext` (e.g. '.json') is given and
    the typed name has no extension it is appended before the overwrite check;
    a caller's own suffix logic then becomes an idempotent no-op."""
    return _Picker(parent, "save", title, start_dir, suggested_name, patterns,
                   default_ext).run()


class _Picker:
    def __init__(self, parent, mode, title, start_dir, suggested, patterns,
                 default_ext):
        _install_css()
        self.parent = parent
        self.mode = mode                 # "open" | "save"
        self.title = title
        self.suggested = suggested or ""
        self.patterns = list(patterns) if patterns else None
        self.default_ext = default_ext
        self._view = "list"
        self._filter = ""
        self._raw = []
        self._result = None
        # The live replace-confirmation dialog, while one is up. Kept on the
        # instance so a test can drive the real card instead of a stand-in.
        self._replace_dlg = None
        self.cur = (start_dir if start_dir and os.path.isdir(start_dir)
                    else finder.HOME)

    # ---- lifecycle: an undecorated modal Gtk.Dialog (calendar.py pattern) ----
    def run(self):
        dlg = Gtk.Dialog(transient_for=self.parent, modal=True)
        dlg.set_decorated(False)
        # Pin the opaque system visual BEFORE realise, like every app window and
        # the Finder do: under a compositor GTK can hand a toplevel the RGBA
        # visual, and every pixel this dialog does not paint then shows BLACK
        # through it (the same bug that made Writer/Novel come up black on real
        # hardware). This modal was the one toplevel still missing the pin.
        nbapp.force_opaque_visual(dlg)
        dlg.set_default_size(*self._card_size())
        dlg.get_style_context().add_class("finder")     # reuse Finder chrome
        dlg.get_style_context().add_class("nbpicker")
        dlg.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        self.dlg = dlg

        # The whole card sits inside a Gtk.Overlay purely so the press-and-hold
        # accent palette can draw INSIDE this dialog. A second toplevel stacked
        # over a modal dialog is exactly the case that does not paint reliably
        # on this no-compositor stack, and an invisible palette that still ate
        # keystrokes would be worse than no palette at all. Typing a file name
        # like "Résumé" is the whole reason the picker belongs here.
        area = dlg.get_content_area()
        overlay = Gtk.Overlay()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay.add(inner)
        area.pack_start(overlay, True, True, 0)
        dlg._overlay = overlay

        inner.pack_start(self._titlebar(), False, False, 0)
        inner.pack_start(self._toolbar(), False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.pack_start(self._sidebar(), False, False, 0)
        body.pack_start(self._filearea(), True, True, 0)
        inner.pack_start(body, True, True, 0)
        inner.pack_start(self._footer(), False, False, 0)

        # Accent palette BEFORE the dialog's own key handler, so Esc closes an
        # open palette rather than cancelling the whole save.
        try:
            import nbdiacritics
            self._diacritics = nbdiacritics.DiacriticsPicker(dlg)
        except Exception:
            self._diacritics = None

        # Escape / window-close map to cancel (return None). Gtk.Dialog already
        # routes Escape to a "close" -> delete-event; make it explicit and safe.
        dlg.connect("delete-event", lambda *_: (self._cancel(), True)[1])
        dlg.connect("key-press-event", self._on_key)

        dlg.show_all()
        self._arrive(inner)
        self._apply_view()
        self._load(self.cur)
        (self.name_entry if self.mode == "save" else self.search).grab_focus()
        dlg.run()                        # modal nested loop; grab is automatic
        dlg.destroy()
        return self._result

    def _arrive(self, body):
        """Settle the toplevel dialog's body into place without gating it.

        The synchronous picker API receives the parent window, but not the menu
        item that raised it, so this Gtk.Dialog cannot honestly use the shared
        anchored-card presenter.  Paint translation gives its own content a
        geometric arrival without animating allocation; the final frame removes
        the draw hook, making the animated and instant end states identical.
        """
        state = {"offset": -12.0, "handler": 0}

        def draw(_widget, cr):
            cr.translate(0.0, state["offset"])
            return False

        def frame(value):
            state["offset"] = float(value)
            body.queue_draw()

        def land(_completed=True):
            state["offset"] = 0.0
            handler = state["handler"]
            if handler:
                state["handler"] = 0
                body.disconnect(handler)
            body.queue_draw()

        try:
            state["handler"] = body.connect("draw", draw)
            # nbmotion-inventory: app.picker
            self._arrival_motion = nbmotion.animate(
                body, frame, -12.0, 0.0, duration=nbmotion.SURFACE_IN,
                easing=nbmotion.ARRIVE, on_done=land)
        except Exception:                                        # motion never gates I/O
            try:
                land(False)
            except Exception:
                pass

    def _card_size(self):
        alloc = self.parent.get_allocation()
        sw, sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else sw
        H = alloc.height if alloc.height > 1 else sh
        return (min(860, max(560, W - 160)), min(600, max(360, H - 130)))

    def _finish(self, path):
        self._result = path
        self.dlg.response(Gtk.ResponseType.OK)

    def _cancel(self):
        self._result = None
        self.dlg.response(Gtk.ResponseType.CANCEL)

    # ---- build ----
    def _titlebar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class("titlebar")
        close = Gtk.Button(); close.get_style_context().add_class("winbox")
        close.set_valign(Gtk.Align.CENTER)
        try:
            img = Gtk.Image()
            nbicons.set_image(img, "wclose", 11, "#3A362E")
            close.add(img)
        except Exception:
            pass
        close.connect("clicked", lambda *_: self._cancel())
        bar.pack_start(close, False, False, 0)
        t = Gtk.Label(label=self.title.upper())
        t.get_style_context().add_class("wintitle")
        bar.set_center_widget(t)
        return bar

    def _toolbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.get_style_context().add_class("navbar")
        self.crumb = finder.Crumbs()
        bar.pack_start(self.crumb, True, True, 6)
        self.search = Gtk.SearchEntry()
        nbicons.style_search_entry(self.search)
        self.search.set_placeholder_text("Search")
        self.search.set_size_request(140, -1)
        self.search.connect("search-changed", self._on_search)
        newf = Gtk.Button()
        newf.set_relief(Gtk.ReliefStyle.NONE)
        newf.get_style_context().add_class("pickernewfolder")
        newf.set_tooltip_text("Create a new folder here")
        nfb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        nfb.pack_start(nbicons.image("plus", 13, "#3A362E"), False, False, 0)
        nfb.pack_start(Gtk.Label(label="New Folder"), False, False, 0)
        newf.add(nfb)
        newf.connect("clicked", lambda *_: self._new_folder())
        bar.pack_end(self._viewswitch(), False, False, 0)
        bar.pack_end(self.search, False, False, 0)
        bar.pack_end(newf, False, False, 0)
        return bar

    def _viewswitch(self):
        box = Gtk.Box(); box.get_style_context().add_class("viewswitch")
        self.btn_list = Gtk.Button()
        self.btn_list.get_style_context().add_class("viewbtn")
        self.btn_list.get_style_context().add_class("active")
        self.btn_list.add(nbicons.image("viewlist", 16, "#1A1916"))
        self.btn_list.connect("clicked", lambda *_: self._set_view("list"))
        self.btn_grid = Gtk.Button()
        self.btn_grid.get_style_context().add_class("viewbtn")
        self.btn_grid.add(nbicons.image("viewgrid", 16, "#3A362E"))
        self.btn_grid.connect("clicked", lambda *_: self._set_view("grid"))
        box.pack_start(self.btn_list, False, False, 0)
        box.pack_start(self.btn_grid, False, False, 0)
        return box

    def _sidebar(self):
        sb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sb.get_style_context().add_class("sidebar")
        sb.set_size_request(168, -1)
        hdr = Gtk.Label(label="PLACES", xalign=0)
        hdr.get_style_context().add_class("sbheader")
        sb.pack_start(hdr, False, False, 0)
        self._sb_rows = []
        for label, icon, rel in finder.PLACES:
            if rel == ".Trash":
                continue                     # never open-from / save-into Trash
            ap = os.path.normpath(
                os.path.join(finder.HOME, rel) if rel else finder.HOME)
            row = Gtk.Button(); row.set_relief(Gtk.ReliefStyle.NONE)
            row.get_style_context().add_class("sbrow")
            hb = Gtk.Box(spacing=12)
            hb.pack_start(nbicons.image(icon, 18, "#3A362E"), False, False, 0)
            hb.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
            row.add(hb)
            row.connect("clicked", lambda _b, p=ap: self._load(p))
            self._sb_rows.append((ap, row))
            sb.pack_start(row, False, False, 0)
        return sb

    def _filearea(self):
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # icon, name, kind, size, date, is_dir, size_bytes, mtime, gridicon
        # Columns 0 and 8 hold cairo SURFACES so the icons stay sharp on a HiDPI
        # panel -- a pixbuf carries no device scale and gets stretched. Same
        # change, same reason, as the Finder's model; see nbicons.SURFACE_GTYPE.
        self.store = Gtk.ListStore(nbicons.SURFACE_GTYPE, str, str, str, str,
                                   bool, GObject.TYPE_INT64,
                                   GObject.TYPE_DOUBLE, nbicons.SURFACE_GTYPE)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.get_style_context().add_class("filelist")
        col = Gtk.TreeViewColumn("Name")
        ic = Gtk.CellRendererPixbuf(); ic.set_property("xpad", 6)
        tx = Gtk.CellRendererText()
        col.pack_start(ic, False); col.add_attribute(ic, "surface", 0)
        col.pack_start(tx, True); col.add_attribute(tx, "text", 1)
        col.set_expand(True)
        self.tree.append_column(col)
        for tt, idx, align in (("Kind", 2, 0.0), ("Size", 3, 1.0),
                               ("Date Modified", 4, 0.0)):
            r = Gtk.CellRendererText(); r.set_property("xalign", align)
            c = Gtk.TreeViewColumn(tt, r, text=idx); c.set_min_width(80)
            self.tree.append_column(c)
        self.tree.connect("row-activated", self._on_activate)
        self.tree.get_selection().connect("changed", lambda *_: self._sync_ok())
        self._list_sw = Gtk.ScrolledWindow()
        self._list_sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._list_sw.add(self.tree)
        main.pack_start(self._list_sw, True, True, 0)
        self.iconview = Gtk.IconView(model=self.store)
        gpr = Gtk.CellRendererPixbuf(); self.iconview.pack_start(gpr, False)
        self.iconview.add_attribute(gpr, "surface", 8)
        gtr = Gtk.CellRendererText(); gtr.set_property("xalign", 0.5)
        self.iconview.pack_start(gtr, True); self.iconview.add_attribute(gtr, "text", 1)
        self.iconview.set_item_width(128)
        self.iconview.get_style_context().add_class("filegrid")
        self.iconview.connect("item-activated", self._on_grid_activate)
        self.iconview.connect("selection-changed", lambda *_: self._sync_ok())
        self._grid_sw = Gtk.ScrolledWindow()
        self._grid_sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._grid_sw.add(self.iconview)
        self._grid_sw.set_no_show_all(True)
        main.pack_start(self._grid_sw, True, True, 0)

        # An empty folder has to SAY it is empty. A column header sitting over a
        # blank white area reads as a dialog that failed to load — and this
        # picker is the file UI every app in the OS puts in front of the user.
        self._empty = Gtk.Label(label="")
        self._empty.get_style_context().add_class("pickerempty")
        self._empty.set_no_show_all(True)
        self._empty.set_line_wrap(True)
        self._empty.set_max_width_chars(38)
        # halign CENTER is what makes max_width_chars actually bind: a box child
        # defaults to FILL and would be stretched to the full file area, so the
        # measure would only ever wrap at the window width.
        self._empty.set_halign(Gtk.Align.CENTER)
        self._empty.set_justify(Gtk.Justification.CENTER)
        main.pack_start(self._empty, True, True, 0)
        return main

    def _footer(self):
        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        foot.get_style_context().add_class("pickerfooter")
        self.warn = Gtk.Label(xalign=0)
        self.warn.get_style_context().add_class("pickerwarn")
        if self.mode == "save":
            foot.pack_start(Gtk.Label(label="Save As:"), False, False, 0)
            self.name_entry = Gtk.Entry()
            self.name_entry.get_style_context().add_class("pickername")
            self.name_entry.set_text(self.suggested)
            self.name_entry.connect("activate", lambda *_: self._commit_save())
            self.name_entry.connect("changed",
                                    lambda *_: self.warn.set_text(""))
            foot.pack_start(self.name_entry, True, True, 0)
            foot.pack_start(self.warn, False, False, 0)
        else:
            foot.pack_start(self.warn, True, True, 0)
        cancel = Gtk.Button(label="Cancel")
        cancel.get_style_context().add_class("pickercancel")
        cancel.connect("clicked", lambda *_: self._cancel())
        self.ok = Gtk.Button(label="Save" if self.mode == "save" else "Open")
        self.ok.get_style_context().add_class("pickerok")
        self.ok.connect("clicked", lambda *_: (
            self._commit_save() if self.mode == "save" else self._commit_open()))
        foot.pack_end(self.ok, False, False, 0)
        foot.pack_end(cancel, False, False, 0)
        if self.mode == "open":
            self.ok.set_sensitive(False)
        return foot

    # ---- view switch ----
    def _set_view(self, mode):
        self._view = mode
        self._apply_view()

    def _apply_view(self):
        grid = self._view == "grid"
        for btn, on in ((self.btn_list, not grid), (self.btn_grid, grid)):
            ctx = btn.get_style_context()
            (ctx.add_class if on else ctx.remove_class)("active")
        self._sync_empty()
        self._sync_ok()

    def _sync_empty(self):
        """Show whichever of list / grid / "nothing here" belongs on screen.

        Both view switching and (re)loading a folder go through here, so the
        empty message and the two views can never both be visible."""
        if not self.store.get_iter_first():
            if self._filter:
                msg = "Nothing here matches “%s”." % self._filter
            elif self.patterns:
                msg = "No files here that this app can open."
            else:
                msg = "This folder is empty."
            self._empty.set_text(msg)
            self._list_sw.hide()
            self._grid_sw.hide()
            self._empty.set_no_show_all(False)
            self._empty.show()
            return
        self._empty.hide()
        if self._view == "grid":
            self._list_sw.hide()
            self._grid_sw.set_no_show_all(False)
            self._grid_sw.show_all()
        else:
            self._grid_sw.hide()
            self._list_sw.show_all()

    # ---- data ----
    def _match(self, name):
        if not self.patterns:
            return True
        return any(fnmatch.fnmatch(name.lower(), p.lower())
                   for p in self.patterns)

    def _load(self, abspath):
        self.cur = os.path.normpath(abspath)
        self._raw = finder.list_dir(self.cur, show_hidden=False)
        self._set_crumbs()
        self._update_sidebar()
        self._populate()

    def _new_folder(self):
        if not self._save_dir_safe():
            self.warn.set_text(_t("That name cannot be used"))
            return
        name = self._ask_folder_name()
        if not name:
            return
        # keep the typed name to a single path component; reject separators so a
        # slip like "a/b" can't quietly create a nested tree or escape self.cur.
        name = name.strip()
        if os.sep in name or (os.altsep and os.altsep in name):
            self.warn.set_text(_t("A name cannot contain a slash"))
            return
        if not name:
            return
        if not _visible_leaf(name):
            self.warn.set_text(_t("That name cannot be used"))
            return
        path = os.path.join(self.cur, name)
        if os.path.exists(path):
            self.warn.set_text('“%s” already exists here' % name)
            return
        try:
            os.makedirs(path)
        except OSError:
            self.warn.set_text("Could not create a folder here")
            return
        self.warn.set_text("")
        self._load(path)                 # step into the new folder, ready to save
        (self.name_entry if self.mode == "save" else self.search).grab_focus()

    def _ask_folder_name(self):
        """Small undecorated modal prompt (same chrome as the picker). Returns the
        typed name or None if cancelled/empty."""
        dlg = Gtk.Dialog(transient_for=self.dlg, modal=True)
        nbapp.force_opaque_visual(dlg)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("finder")
        dlg.get_style_context().add_class("nbpicker")
        dlg.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        area = dlg.get_content_area()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.get_style_context().add_class("pickerfooter")
        box.set_size_request(320, -1)
        box.pack_start(Gtk.Label(label="Name of new folder", xalign=0),
                       False, False, 0)
        entry = Gtk.Entry()
        entry.get_style_context().add_class("pickername")
        entry.set_text("untitled folder")
        entry.select_region(0, -1)
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cancel = Gtk.Button(label="Cancel")
        cancel.get_style_context().add_class("pickercancel")
        cancel.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.CANCEL))
        create = Gtk.Button(label="Create")
        create.get_style_context().add_class("pickerok")
        create.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.OK))
        btns.pack_end(create, False, False, 0)
        btns.pack_end(cancel, False, False, 0)
        box.pack_start(btns, False, False, 0)
        area.pack_start(box, True, True, 0)
        dlg.set_default(create)
        dlg.connect("key-press-event", lambda _w, ev:
                    dlg.response(Gtk.ResponseType.CANCEL)
                    if ev.keyval == Gdk.KEY_Escape else None)
        dlg.show_all()
        entry.grab_focus()
        resp = dlg.run()
        name = entry.get_text()
        dlg.destroy()
        return name if resp == Gtk.ResponseType.OK else None

    def _set_crumbs(self):
        home = os.path.normpath(finder.HOME)
        cur = self.cur
        if cur == home or cur.startswith(home + os.sep):
            rel = os.path.relpath(cur, home)
            parts = [] if rel == "." else rel.split(os.sep)
            trail = [(p, os.path.join(home, *parts[:i + 1]))
                     for i, p in enumerate(parts)]
            self.crumb.set_trail("Home", trail,
                                 lambda t: self._load(home if t == "" else t))
        else:
            parts = [p for p in cur.split("/") if p]
            trail = [(p, "/" + "/".join(parts[:i + 1]))
                     for i, p in enumerate(parts)]
            self.crumb.set_trail("Computer", trail,
                                 lambda t: self._load("/" if t == "/" else t))

    def _update_sidebar(self):
        for ap, row in self._sb_rows:
            ctx = row.get_style_context()
            (ctx.add_class if ap == self.cur else ctx.remove_class)("selected")

    def _populate(self):
        self.store.clear()
        rows = [r for r in self._raw if r["is_dir"] or self._match(r["name"])]
        if self._filter:
            rows = [r for r in rows if self._filter in r["name"].lower()]
        rows.sort(key=lambda r: (not r["is_dir"], r["name"].lower()))
        for r in rows:
            self.store.append([
                nbicons.surface(r["icon"], LIST_ICON_PX), r["name"], r["kind"],
                r["size"], r["date"], r["is_dir"], r["size_bytes"],
                r["mtime"], nbicons.surface(r["icon"], GRID_ICON_PX)])
        self._sync_empty()
        self._sync_ok()

    def _on_search(self, entry):
        self._filter = entry.get_text().strip().lower()
        self._populate()

    def _selected_iter(self):
        if self._view == "grid":
            items = self.iconview.get_selected_items()
            return self.store.get_iter(items[0]) if items else None
        _m, it = self.tree.get_selection().get_selected()
        return it

    def _sync_ok(self):
        if self.mode != "open":
            return
        it = self._selected_iter()
        self.ok.set_sensitive(it is not None and not self.store.get_value(it, 5))

    def _on_activate(self, _tree, path, _col):
        self._activate_iter(self.store.get_iter(path))

    def _on_grid_activate(self, _iv, path):
        self._activate_iter(self.store.get_iter(path))

    def _activate_iter(self, it):
        name = self.store.get_value(it, 1)
        target = os.path.join(self.cur, name)
        if self.store.get_value(it, 5):        # dir -> navigate
            self._load(target)
        elif self.mode == "open":
            self._finish(target)
        else:
            self.name_entry.set_text(name)     # save -> adopt the name

    def _commit_open(self):
        it = self._selected_iter()
        if it is None:
            return
        name = self.store.get_value(it, 1)
        if self.store.get_value(it, 5):
            self._load(os.path.join(self.cur, name))
            return
        self._finish(os.path.join(self.cur, name))

    def _commit_save(self):
        name = self.name_entry.get_text().strip()
        if not name:
            self.name_entry.grab_focus()
            return
        if os.sep in name or (os.altsep and os.altsep in name):
            self.warn.set_text(_t("A name cannot contain a slash"))
            self.name_entry.grab_focus()
            return
        if self.default_ext:
            _root, ext = os.path.splitext(name)
            if ext in ("", "."):
                name = name.rstrip(".") + self.default_ext
        if not _visible_leaf(name):
            # Open/listing deliberately hides dotfiles and this picker offers
            # no Show Hidden mode. Never report a successful save to a name the
            # same app cannot subsequently display or reopen.
            self.warn.set_text(_t("That name cannot be used"))
            self.name_entry.grab_focus()
            return
        if not self._save_dir_safe():
            # Breadcrumbs describe the lexical Home path. A symlinked parent
            # must not make that promise while redirecting the caller's write
            # somewhere outside Home (the process runs as root on-device).
            self.warn.set_text(_t("That name cannot be used"))
            self.name_entry.grab_focus()
            return
        path = os.path.join(self.cur, name)
        if os.path.islink(path):
            # Never hand a Save caller a symlink pathname. Ordinary writers
            # follow it, which can create/replace a target outside the folder
            # this picker shows—especially when the link is dangling and
            # exists() would otherwise say the name is free.
            self.warn.set_text(_t("That name cannot be used"))
            self.name_entry.grab_focus()
            return
        if os.path.isdir(path):
            # Not a replaceable file at all — saving onto a folder cannot work,
            # and offering to "replace" one would be a promise we can't keep.
            self.warn.set_text('A folder here is already called “%s”' % name)
            self.name_entry.grab_focus()
            return
        if os.path.lexists(path) and not self._confirm_replace(name):
            # Declined: change nothing, leave the name in the box so the user
            # can edit it into a new one.
            self.warn.set_text("")
            self.name_entry.grab_focus()
            return
        self._finish(path)

    def _save_dir_safe(self):
        """Whether a displayed Home directory still resolves inside Home.

        Explicit external start directories remain usable (USB export is a
        supported workflow); this closes only the deceptive Home breadcrumb
        case where a parent symlink escapes the place being shown.
        """
        home = os.path.normpath(finder.HOME)
        cur = os.path.normpath(self.cur)
        if cur != home and not cur.startswith(home + os.sep):
            return True
        try:
            return os.path.commonpath((os.path.realpath(home),
                                       os.path.realpath(cur))) == \
                   os.path.realpath(home)
        except (OSError, ValueError):
            return False

    def _confirm_replace(self, name):
        """Ask, in so many words, before an existing file is overwritten.

        This is the OS's shared Save dialog — Writer, Illustrator, the GBA SDK
        and the video editor all end up here — so it is the single widest path
        by which a user could lose a file they already have. It used to ARM the
        Save button instead: a one-line hint in the footer and a second press
        destroyed the file, with no statement of what was about to happen and
        no way to say no except noticing the hint in time. A destructive action
        gets its own card, with the consequence spelled out and Cancel resting
        under the keyboard focus (novel.py's Replace-file card, same shape).

        Returns True only if the user explicitly chose Replace."""
        dlg = Gtk.Dialog(transient_for=self.dlg, modal=True)
        nbapp.force_opaque_visual(dlg)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("finder")
        dlg.get_style_context().add_class("nbpicker")
        dlg.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        area = dlg.get_content_area()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.get_style_context().add_class("pickerfooter")
        box.set_size_request(380, -1)

        head = Gtk.Label(label="Replace file?", xalign=0)
        head.get_style_context().add_class("pickerdlgtitle")
        box.pack_start(head, False, False, 0)

        msg = Gtk.Label(
            label=("A file called “%s” is already in this folder. Saving "
                   "replaces it. This cannot be undone." % name), xalign=0)
        msg.get_style_context().add_class("pickerdlgmsg")
        msg.set_line_wrap(True)
        # width AND max width: a label in a box only as wide as its widest
        # child otherwise wraps at the button row and turns into a column.
        msg.set_width_chars(38)
        msg.set_max_width_chars(38)
        box.pack_start(msg, False, False, 0)

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cancel = Gtk.Button(label="Cancel")
        cancel.get_style_context().add_class("pickercancel")
        cancel.connect("clicked",
                       lambda *_: dlg.response(Gtk.ResponseType.CANCEL))
        replace = Gtk.Button(label="Replace")
        replace.get_style_context().add_class("pickerok")
        replace.connect("clicked",
                        lambda *_: dlg.response(Gtk.ResponseType.OK))
        btns.pack_end(replace, False, False, 0)
        btns.pack_end(cancel, False, False, 0)
        box.pack_start(btns, False, False, 0)
        area.pack_start(box, True, True, 0)

        dlg.connect("delete-event",
                    lambda *_: (dlg.response(Gtk.ResponseType.CANCEL), True)[1])
        dlg.connect("key-press-event", lambda _w, ev:
                    dlg.response(Gtk.ResponseType.CANCEL)
                    if ev.keyval == Gdk.KEY_Escape else None)
        dlg.show_all()
        # Focus rests on Cancel, never on Replace: a stray Return or Space in
        # front of a destructive card must not be the thing that deletes a file.
        cancel.grab_focus()
        self._replace_dlg = dlg
        try:
            resp = dlg.run()
        finally:
            self._replace_dlg = None
            dlg.destroy()
        return resp == Gtk.ResponseType.OK

    def _on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self._cancel()
            return True
        return False
