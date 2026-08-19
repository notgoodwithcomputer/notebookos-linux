#!/usr/bin/env python3
"""
Workspace — the GBA SDK's own window manager, inside one app window.

WHY THIS EXISTS
---------------
The suite is specified to grow to seventeen subsystems (docs/GBA-SDK-SPEC.md):
five editors today, plus a score editor, a data-table editor, a map graph, a
menu layout editor, a timeline, a profiler and an emulator pane. They cannot
live behind one maximised slot — composing music while watching the room it
scores, or stepping the emulator beside the object that misbehaves, is the
whole point of the tool.

It is Phase 1 for one reason: every later subsystem is a pane inside this, and
retro-fitting a shell under sixteen finished editors is the expensive way to
get one.

IT MUST BE IN-APP, NOT REAL WINDOWS. Notebook OS runs one app at a time and its
window manager keeps the focused window topmost; child Gtk.Windows would fight
it — the panel's own dropdowns already render behind focused windows for
exactly that reason. So this is a widget tree, not a set of toplevels.

THE MODEL
---------
A layout is a tree of two node kinds:

    group : an ordered list of panes, one visible, with a tab strip
    split : two children, an orientation, and a ratio

Every pane is registered once with a stable id and never re-parented by the
caller; the workspace moves it between groups. A pane knows nothing about
where it sits — that rule is what lets subsystems be written independently.

Layouts serialise to plain dicts, so a project can carry "composing",
"level building" and "debugging" arrangements.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango  # noqa: E402

# A group narrower than this cannot show a tab strip and its editor usefully.
MIN_GROUP_PX = 180


class Pane:
    """One registered editor: an id, a title for its tab, and its widget."""

    __slots__ = ("pid", "title", "widget", "closable")

    def __init__(self, pid, title, widget, closable=True):
        self.pid = pid
        self.title = title
        self.widget = widget
        self.closable = closable


class _Group(Gtk.Box):
    """A tab strip over a stack. The leaf of the layout tree."""

    def __init__(self, ws):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.ws = ws
        self.ids = []                 # pane ids in tab order
        self.current = None
        self.get_style_context().add_class("wsgroup")

        self.tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.tabs.get_style_context().add_class("wstabs")
        # A group with many panes must not push the group wider than its share
        # of the window, so the strip scrolls rather than growing.
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        sc.add(self.tabs)
        self.pack_start(sc, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.pack_start(self.stack, True, True, 0)
        self.set_size_request(MIN_GROUP_PX, -1)

    # -- contents -------------------------------------------------------------
    def add(self, pane, show=True):
        if pane.pid in self.ids:
            if show:
                self.show_pane(pane.pid)
            return
        self.ids.append(pane.pid)
        self.stack.add_named(pane.widget, pane.pid)
        pane.widget.show_all()
        if show or self.current is None:
            self.show_pane(pane.pid)
        self._rebuild_tabs()

    def remove(self, pid):
        if pid not in self.ids:
            return
        self.ids.remove(pid)
        child = self.stack.get_child_by_name(pid)
        if child is not None:
            self.stack.remove(child)
        if self.current == pid:
            self.current = self.ids[0] if self.ids else None
            if self.current:
                self.stack.set_visible_child_name(self.current)
        self._rebuild_tabs()

    def show_pane(self, pid):
        if pid not in self.ids:
            return
        self.current = pid
        self.stack.set_visible_child_name(pid)
        self.ws._active = self
        self._rebuild_tabs()

    # -- the strip ------------------------------------------------------------
    def _rebuild_tabs(self):
        for ch in self.tabs.get_children():
            self.tabs.remove(ch)
        for pid in self.ids:
            pane = self.ws.panes.get(pid)
            if pane is None:
                continue
            # Keep the tab action and its close action as siblings.  Nesting a
            # GtkButton inside another GtkButton lets one click activate both
            # controls (and GTK does not support nested interactive buttons).
            row = Gtk.Box(spacing=0)
            btn = Gtk.Button(label=pane.title)
            btn.set_relief(Gtk.ReliefStyle.NONE)
            ctx = btn.get_style_context()
            ctx.add_class("wstab")
            if pid == self.current:
                ctx.add_class("on")
            lab = btn.get_child()
            lab.set_ellipsize(Pango.EllipsizeMode.END)
            lab.set_max_width_chars(16)
            btn.connect("clicked", lambda _b, p=pid: self.show_pane(p))
            row.pack_start(btn, False, False, 0)
            if pane.closable:
                x = Gtk.Button(label="×")
                x.set_relief(Gtk.ReliefStyle.NONE)
                x.get_style_context().add_class("wstabx")
                x.set_tooltip_text("Close this pane")
                x.connect("clicked", lambda _b, p=pid: self.ws.close(p))
                row.pack_start(x, False, False, 0)
            self.tabs.pack_start(row, False, False, 0)
        self.tabs.show_all()


class Workspace(Gtk.Box):
    """The pane tree. `on_change` fires whenever the layout changes, so the
    caller can save it with the project."""

    def __init__(self, on_change=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.panes = {}               # pid -> Pane
        self.on_change = on_change
        self._root = _Group(self)
        self._active = self._root
        self.pack_start(self._root, True, True, 0)
        self.get_style_context().add_class("workspace")

    # -- registration ---------------------------------------------------------
    def register(self, pid, title, widget, closable=True):
        """Declare a pane. Registering does not place it on screen."""
        self.panes[pid] = Pane(pid, title, widget, closable)

    def _group_holding(self, pid, node=None):
        node = node or self._root
        if isinstance(node, _Group):
            return node if pid in node.ids else None
        for child in node.get_children():
            got = self._group_holding(pid, child)
            if got is not None:
                return got
        return None

    def _groups(self, node=None):
        node = node or self._root
        if isinstance(node, _Group):
            return [node]
        out = []
        for child in node.get_children():
            out += self._groups(child)
        return out

    # -- the three verbs the menus offer --------------------------------------
    def show(self, pid):
        """Bring a pane to the front. Opens it in the active group when it is
        not on screen, which is what makes selecting a resource in the browser
        behave the way it always has."""
        pane = self.panes.get(pid)
        if pane is None:
            return
        holder = self._group_holding(pid)
        if holder is None:
            self._active.add(pane)
        else:
            holder.show_pane(pid)
        self._changed()

    def close(self, pid):
        """Take a pane off screen. It stays registered, so showing it again
        costs nothing and its editor keeps its state — closing a tab must not
        discard work."""
        holder = self._group_holding(pid)
        if holder is None:
            return
        holder.remove(pid)
        if not holder.ids and holder is not self._root:
            self._collapse(holder)
        self._changed()

    def split(self, orientation, pid=None):
        """Split the group holding `pid` (or the active one) and move that pane
        into the new half. A group with one pane does not split — there would
        be nothing to put in the other side."""
        holder = self._group_holding(pid) if pid else self._active
        if holder is None:
            holder = self._active
        pid = pid or holder.current
        if pid is None or len(holder.ids) < 2:
            return False
        parent = holder.get_parent()
        paned = Gtk.Paned(orientation=orientation)
        paned.set_wide_handle(True)
        # Swap the group for a Paned in the same slot, then put the group back
        # on one side of it.
        if isinstance(parent, Gtk.Paned):
            first = parent.get_child1() is holder
            parent.remove(holder)
            if first:
                parent.pack1(paned, True, False)
            else:
                parent.pack2(paned, True, False)
        else:
            parent.remove(holder)
            parent.pack_start(paned, True, True, 0)
            self._root = paned
        fresh = _Group(self)
        paned.pack1(holder, True, False)
        paned.pack2(fresh, True, False)
        holder.remove(pid)
        fresh.add(self.panes[pid])
        paned.show_all()
        self._active = fresh
        self._changed()
        return True

    def _collapse(self, group):
        """Remove an empty group and pull its sibling up into the split's slot,
        so a closed pane never leaves a blank half behind."""
        paned = group.get_parent()
        if not isinstance(paned, Gtk.Paned):
            return
        sibling = (paned.get_child2() if paned.get_child1() is group
                   else paned.get_child1())
        if sibling is None:
            return
        grandparent = paned.get_parent()
        paned.remove(group)
        paned.remove(sibling)
        if isinstance(grandparent, Gtk.Paned):
            first = grandparent.get_child1() is paned
            grandparent.remove(paned)
            if first:
                grandparent.pack1(sibling, True, False)
            else:
                grandparent.pack2(sibling, True, False)
        else:
            grandparent.remove(paned)
            grandparent.pack_start(sibling, True, True, 0)
            self._root = sibling
        if self._active is group:
            self._active = self._groups()[0]

    def reset(self, keep=None):
        """Back to one group. `keep` is the pane left showing."""
        for pid in list(self.panes):
            holder = self._group_holding(pid)
            if holder is not None:
                holder.remove(pid)
        for g in self._groups():
            if g is not self._root and not g.ids:
                self._collapse(g)
        # rebuild a single empty root
        parent = self._root.get_parent()
        if parent is not None:
            parent.remove(self._root)
        self._root = _Group(self)
        self.pack_start(self._root, True, True, 0)
        self._active = self._root
        self._root.show_all()
        if keep:
            self.show(keep)
        self._changed()

    # -- serialisation --------------------------------------------------------
    def layout(self):
        return self._describe(self._root)

    def _describe(self, node):
        if isinstance(node, _Group):
            return {"t": "group", "ids": list(node.ids), "cur": node.current}
        return {"t": "split",
                "o": ("h" if node.get_orientation()
                      == Gtk.Orientation.HORIZONTAL else "v"),
                "pos": node.get_position(),
                "a": self._describe(node.get_child1()),
                "b": self._describe(node.get_child2())}

    def set_layout(self, desc):
        """Rebuild from a saved description. Anything unrecognised falls back
        to a single group: a layout is a convenience, and a bad one must never
        cost the user their editors."""
        try:
            self.reset()
            node = self._build(desc)
        except Exception:                                   # noqa: BLE001
            return False
        if node is None:
            return False
        parent = self._root.get_parent()
        if parent is not None:
            parent.remove(self._root)
        self._root = node
        self.pack_start(node, True, True, 0)
        node.show_all()
        groups = self._groups()
        self._active = groups[0] if groups else self._root
        return True

    def _build(self, d):
        if not isinstance(d, dict):
            return None
        if d.get("t") == "group":
            g = _Group(self)
            for pid in d.get("ids") or []:
                if pid in self.panes:
                    g.add(self.panes[pid], show=False)
            cur = d.get("cur")
            if cur in g.ids:
                g.show_pane(cur)
            elif g.ids:
                g.show_pane(g.ids[0])
            return g
        if d.get("t") == "split":
            a, b = self._build(d.get("a")), self._build(d.get("b"))
            if a is None or b is None:
                return a or b
            p = Gtk.Paned(orientation=(Gtk.Orientation.HORIZONTAL
                                       if d.get("o") == "h"
                                       else Gtk.Orientation.VERTICAL))
            p.set_wide_handle(True)
            p.pack1(a, True, False)
            p.pack2(b, True, False)
            try:
                p.set_position(int(d.get("pos") or 0))
            except (TypeError, ValueError):
                pass
            return p
        return None

    def _changed(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception:                               # noqa: BLE001
                pass        # saving a layout must never break the editor

    # -- what the menus need to know ------------------------------------------
    def open_ids(self):
        out = []
        for g in self._groups():
            out += g.ids
        return out

    def get_visible_child_name(self):
        """The pane showing in the active group, or None.

        Named as GtkStack names it because that is what this replaced and the
        question is the same one — "which editor is in front?". Callers that
        only ever asked that keep working across the change.
        """
        return self._active.current if self._active else None

    def can_split(self):
        return len(self._active.ids) >= 2


CSS = b"""
.workspace { background: #C9C4B6; }
.wsgroup { background: #FCFBF8; border: 1px solid #B3AD9E; }
.wstabs { background: #EFEBE0; border-bottom: 1px solid #B3AD9E; }
.wstab { padding: 3px 10px; border-radius: 0; background: transparent;
         border-right: 1px solid #D7D2C5; box-shadow: none;
         font-family: "Nimbus Sans","Helvetica",sans-serif; font-size: 12px;
         color: #3A362E; }
.wstab:hover { background: #F4F2EC; }
.wstab.on { background: #FCFBF8; color: #1A1916; font-weight: 700; }
.wstabx { padding: 0 3px; min-width: 14px; min-height: 14px; font-size: 11px;
          color: #9A9484; background: transparent; box-shadow: none;
          border: none; }
.wstabx:hover { color: #C8341E; }
"""
