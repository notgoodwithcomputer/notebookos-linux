#!/usr/bin/env python3
"""
System Monitor — the Notebook OS activity monitor (native GTK).

Live processor and memory gauges plus a sortable table of the programs that
are running, all read straight from /proc (no external tools). Select one and
End Program to send it a signal. The window says "program" throughout, never
"process": the row IS a running program, and the P-word is one a person cannot
look up on a machine with no internet. Cinnamon/gnome-system-monitor lineage,
papertone-styled.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GObject  # noqa: E402

import errno
import os
import signal
import time

import nbapp
import nbicons
from nbi18n import _t  # noqa: E402


def meminfo():
    d = {}
    try:
        with open("/proc/meminfo") as fh:
            for ln in fh:
                k, _, v = ln.partition(":")
                try:
                    d[k] = int(v.split()[0])  # kB
                except (ValueError, IndexError):
                    continue  # malformed line -> skip it, keep the rest
    except OSError:
        pass
    return d


def cpu_times():
    try:
        with open("/proc/stat") as fh:
            parts = fh.readline().split()[1:]
            vals = [int(x) for x in parts]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            return sum(vals), idle
    except (OSError, ValueError, IndexError):
        return 0, 0


# Command names that are an interpreter rather than a program: /proc reports
# the executable, and every Notebook OS app is run as
# `python3 /opt/notebook/de/<app>.py`, so without this the process table is a
# dozen identical "python3" rows with no way to tell Writer from the desktop.
INTERPRETERS = ("python3", "python", "sh", "bash", "ash", "busybox")

_APP_DISPLAY = None


def _app_display(mod):
    """The human name for a de/<mod>.py script — the same names the Packages
    window and the Finder use, so one app is called one thing everywhere.

    Read from packages.py rather than copied, so a new app named in one place
    is named in both. Best-effort: an unknown module keeps its own file name,
    which is honest, and a failed import just leaves every name as it was."""
    global _APP_DISPLAY
    if _APP_DISPLAY is None:
        _APP_DISPLAY = {}
        try:
            import packages
            _APP_DISPLAY.update(packages._APP_NAMES)
            _APP_DISPLAY.update(packages._SYS_NAMES)
        except Exception:
            pass
    return _APP_DISPLAY.get(mod, mod)


def proc_start_time(pid):
    """The moment a process started, from /proc/<pid>/stat (field 22).

    An ID on its own only names a program while that program is alive: once it
    finishes the kernel is free to hand the same number to something new. Pair
    the ID with its start time and the pair names one program for good, which
    is what the name cache keys on and what End Program re-checks before it
    signals anything. None means the ID is not readable (usually: gone)."""
    try:
        with open("/proc/%s/stat" % pid) as fh:
            data = fh.read()
        return data[data.rfind(")") + 2:].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def human_kb(kb):
    n = kb * 1024.0
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return "%.0f %s" % (n, u) if u in ("B", "KB") else "%.1f %s" % (n, u)
        n /= 1024.0


class SystemMonitor(nbapp.AppWindow):
    app_name = "System Monitor"
    menus = ("View",)
    # End Program icon colours: ink when a row is selected (actionable), muted
    # when the button is disabled. A pixbuf can't be recoloured by CSS, so the
    # icon is swapped to match the button's sensitivity (see _on_selection_changed).
    _END_ICON_ON = "#1A1916"
    _END_ICON_OFF = "#9A9484"

    def __init__(self):
        super().__init__()
        self._install_css()
        # Stop the 2s /proc poll the moment the window goes away, so a closed
        # monitor never keeps reading /proc or poking now-destroyed widgets.
        self._alive = True
        self.connect("destroy", self._on_destroy)
        self._last_cpu = cpu_times()
        self._last_sample = time.monotonic()   # wall time of that CPU sample
        self._proc_prev = {}    # pid -> (utime+stime) last sample
        self._proc_cpu = {}     # pid -> last computed CPU% (reused when a
        #                         manual refresh lands inside the sample window)
        # (pid, start-time) -> display name, so a process's command line is
        # read once rather than on every 2s tick. Keying on the start time as
        # well as the pid means a recycled pid can never inherit the dead
        # process's name.
        self._name_cache = {}
        self._status_text = None   # transient footer message (kill result, ...)
        self._status_until = 0.0   # monotonic time that message expires

        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        stage.get_style_context().add_class("smstage")
        stage.set_margin_top(28); stage.set_margin_bottom(28)
        stage.set_margin_start(40); stage.set_margin_end(40)
        self.content.pack_start(stage, True, True, 0)

        # gauges
        res_lbl = Gtk.Label(label=_t("RESOURCES"), xalign=0)
        res_lbl.get_style_context().add_class("smsection")
        res_lbl.set_margin_bottom(12)
        stage.pack_start(res_lbl, False, False, 0)

        gauges = Gtk.Box(spacing=20)
        stage.pack_start(gauges, False, False, 0)
        self.cpu_bar, cpu_card = self._gauge("Processor")
        self.mem_bar, mem_card = self._gauge("Memory")
        # "Am I running out of space?" is the question a person actually
        # brings to an activity monitor, and it was the one thing this window
        # could not answer: a full disk stops documents saving and stops apps
        # starting, and neither of the other two gauges would move.
        self.disk_bar, disk_card = self._gauge("Storage")
        gauges.pack_start(cpu_card, True, True, 0)
        gauges.pack_start(mem_card, True, True, 0)
        gauges.pack_start(disk_card, True, True, 0)
        self.cpu_lbl = self._gauge_value(cpu_card)
        self.mem_lbl = self._gauge_value(mem_card)
        self.disk_lbl = self._gauge_value(disk_card)

        # the table of running programs
        # cols 4/5 are hidden NUMERIC sort keys (rss_kb, cpu_pct) so MEMORY and
        # PROCESSOR sort by value, not by their formatted text ("100%"
        # mis-sorts vs "9%").
        self.store = Gtk.ListStore(str, int, str, str,
                                   GObject.TYPE_INT64, GObject.TYPE_DOUBLE)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.get_style_context().add_class("smtree")
        # Click-sortable headers. Each visible column maps to the MODEL column
        # it sorts on: NAME->0, ID->1, MEMORY->4 (rss_kb), PROCESSOR->5
        # (cpu_pct) -- the last two so value, not formatted text, orders rows.
        # We drive sorting ourselves (set_clickable + "clicked") rather than via
        # column.set_sort_column_id so the FIRST click on a resource column lands
        # on the useful order -- busiest first, like any activity monitor -- rather
        # than GTK's fixed ascending, which buries the busy programs under idle 0%
        # ones. Clicking the already-active column flips the direction.
        self._sort_widgets = {}         # model col -> TreeViewColumn (for indicator)
        self._desc_first = {4, 5}       # MEMORY, PROCESSOR: 1st click -> DESC
        self._sort_col = 4              # current model sort column
        self._sort_order = Gtk.SortType.DESCENDING
        self._load_sort_prefs()
        sort_cols = {2: 4, 3: 5}  # MEMORY->rss_kb(4), PROCESSOR->cpu_pct(5) keys
        # "ID", not "PID": the number is only ever used to tell two identically
        # named rows apart, and the P is a word from another world.
        # "NAME", not "PROCESS": the section heading directly above this table
        # already says what the rows are, so the column repeated the word rather
        # than saying what is in it — and the View menu's own entry for this
        # column has always been "Sort by Name".
        # "PROCESSOR", not "CPU": the gauge measuring the very same thing, a few
        # centimetres above, has said Processor since it was built; two names
        # for one number on one page is one name too many, and the acronym is
        # the one a person cannot look up on a machine with no internet.
        for i, title in enumerate(["NAME", "ID", "MEMORY", "PROCESSOR"]):
            r = Gtk.CellRendererText()
            r.set_property("ypad", 4)
            if i in (1, 2, 3):
                r.set_property("xalign", 1.0)
                # tabular figures: mono aligns the ID / MEMORY / % digits
                r.set_property("family", "Liberation Mono")
                r.set_property("family-set", True)
            c = Gtk.TreeViewColumn(title, r, text=i)
            c.set_expand(i == 0)
            model_col = sort_cols.get(i, i)
            c.set_clickable(True)
            c.connect("clicked", self._on_header_clicked, model_col)
            self._sort_widgets[model_col] = c
            self.tree.append_column(c)
        # NAME sorts case-insensitively so names group naturally instead of
        # segregating every capitalised command ahead of the lowercase ones.
        self.store.set_sort_func(0, self._cmp_name)
        self._apply_sort(self._sort_col, self._sort_order)

        proc_lbl = Gtk.Label(label=_t("RUNNING PROGRAMS"), xalign=0)
        proc_lbl.get_style_context().add_class("smsection")
        proc_lbl.set_margin_top(28); proc_lbl.set_margin_bottom(12)
        stage.pack_start(proc_lbl, False, False, 0)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.tree)
        sw.get_style_context().add_class("smtreewrap")
        self.sw = sw  # keep a handle to the scroller
        stage.pack_start(sw, True, True, 0)

        # footer
        foot = Gtk.Box(spacing=12)
        foot.get_style_context().add_class("smfoot")
        foot.set_margin_top(16)
        self.stat = Gtk.Label(xalign=0)
        self.stat.get_style_context().add_class("smstat")
        foot.pack_start(self.stat, True, True, 0)
        self.endbtn = Gtk.Button()
        self.endbtn.get_style_context().add_class("smend")
        ebox = Gtk.Box(spacing=8)
        # Keep a handle to the icon so it can dim in step with the button — it
        # starts disabled, so it starts muted.
        self._end_icon = nbicons.image("stopsq", 16, self._END_ICON_OFF)
        ebox.pack_start(self._end_icon, False, False, 0)
        ebox.pack_start(Gtk.Label(label=_t("End Program")), False, False, 0)
        self.endbtn.add(ebox)
        self.endbtn.connect("clicked", self._end_process)
        self.endbtn.set_sensitive(False)   # enabled only when a row is selected
        foot.pack_end(self.endbtn, False, False, 0)
        stage.pack_start(foot, False, False, 0)

        # End Program is destructive — gate it on an actual selection so a stray
        # click can't no-op silently (the View-menu entry greys out to match).
        self.tree.get_selection().connect("changed", self._on_selection_changed)
        # Right-click a row -> context menu (End Program / Copy ID); Delete key
        # ends the selected program. Both route through the same confirmed path.
        self.tree.connect("button-press-event", self._on_tree_button)
        self.connect("key-press-event", self._on_key_del)

        self.refresh()
        GLib.timeout_add_seconds(2, self.refresh)

    def _gauge(self, title):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("smcard")
        t = Gtk.Label(label=title.upper(), xalign=0)
        t.get_style_context().add_class("smcardtitle")
        card.pack_start(t, False, False, 0)
        bar = Gtk.ProgressBar()
        bar.get_style_context().add_class("smbar")
        card.pack_start(bar, False, False, 0)
        return bar, card

    def _gauge_value(self, card):
        v = Gtk.Label(xalign=0)
        v.get_style_context().add_class("smcardval")
        card.pack_start(v, False, False, 0)
        return v

    def _on_destroy(self, *_):
        self._alive = False

    def _prefs_path(self):
        home = os.environ.get("NB_HOME", os.path.expanduser("~"))
        return os.path.join(home, ".config", "notebook", "sysmon.json")

    def _load_sort_prefs(self):
        try:
            import json
            with open(self._prefs_path(), encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return
            if data.get("sort_col") in (0, 1, 4, 5):
                self._sort_col = data["sort_col"]
            if data.get("sort_desc") is True:
                self._sort_order = Gtk.SortType.DESCENDING
            elif data.get("sort_desc") is False:
                self._sort_order = Gtk.SortType.ASCENDING
        except (OSError, ValueError, TypeError):
            pass

    def _save_sort_prefs(self):
        try:
            nbapp.atomic_write_json(self._prefs_path(), {
                "sort_col": self._sort_col,
                "sort_desc": self._sort_order == Gtk.SortType.DESCENDING,
            })
        except (OSError, TypeError, ValueError) as exc:
            nbapp.save_failure_reason = str(exc)

    # ---- sampling ----
    def refresh(self, manual=False):
        # Once the window is gone the poll must stop: return False so GLib drops
        # the timeout, and never touch the (destroyed) gauge/tree widgets.
        if not self._alive:
            return False
        now = time.monotonic()
        tot, idle = cpu_times()
        # A CPU reading is a DELTA over an interval. The 2s auto-tick always
        # spans a real interval, but a manual "Refresh Now" can land a fraction
        # of a second after a sample: dividing by that near-zero window snaps the
        # Processor gauge and every process's CPU% toward 0 and, worse, advances
        # the baseline so the NEXT tick is short too. So recompute the delta (and
        # move the baseline) only once >=0.5s of wall time has accumulated;
        # otherwise keep the last CPU figures and reuse the cached per-process %.
        recompute = (not manual) or (now - self._last_sample) >= 0.5
        if recompute:
            dtot = tot - self._last_cpu[0]
            didle = idle - self._last_cpu[1]
            cpu = 0.0 if dtot <= 0 else max(0.0, min(1.0, (dtot - didle) / dtot))
            self._last_cpu = (tot, idle)
            self._last_sample = now
            self.cpu_bar.set_fraction(cpu)
            self.cpu_lbl.set_text(_t("%d%% in use") % round(cpu * 100))
        else:
            dtot = 0   # no meaningful interval this call; reuse cached CPU%
        # mem — an instantaneous reading (no delta), so always refresh it
        mi = meminfo()
        total = mi.get("MemTotal", 0)
        avail = mi.get("MemAvailable", mi.get("MemFree", 0))
        used = max(0, total - avail)  # never negative if avail momentarily > total
        # set_fraction() warns (and mis-draws) outside [0,1]; clamp defensively.
        frac = max(0.0, min(1.0, used / total)) if total else 0.0
        self.mem_bar.set_fraction(frac)
        # "used of" — the bare "20.2 GB of 31.2 GB" never said which number was
        # which, and the Settings System page has always spelt it out.
        self.mem_lbl.set_text("%s used of %s"
                              % (human_kb(used), human_kb(total)))
        self._refresh_disk()
        # programs — list/memory always rebuild; processor % honours `recompute`
        self._refresh_procs(dtot, recompute)
        return self._alive

    def _script_name(self, pid, started, fallback):
        # An interpreter's row is useless as "python3": show the app it is
        # running. This matters twice over — the table becomes readable, and
        # End Program stops being a coin toss between the user's document and
        # the desktop itself. Read once per process (see _name_cache).
        key = (pid, started)
        hit = self._name_cache.get(key)
        if hit is not None:
            return hit
        name = fallback
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                args = [a for a in fh.read().split(b"\0") if a]
            script = next((a for a in args if a.endswith(b".py")), None)
            if script:
                mod = os.path.basename(script.decode("utf-8", "replace"))[:-3]
                name = _app_display(mod)
        except (OSError, ValueError, IndexError):
            pass    # unreadable command line: keep the interpreter's own name
        self._name_cache[key] = name
        return name

    def _refresh_disk(self):
        # Space left on the machine's own disk, read fresh each tick (statvfs
        # is a single cheap syscall). Leads with FREE, because "how much room
        # is left" is the question, and turns the bar signage-red past 90% —
        # the one moment on this page that is a genuine alert rather than data.
        try:
            st = os.statvfs("/")
            total = st.f_blocks * st.f_frsize
            free = st.f_bavail * st.f_frsize
        except OSError:
            self.disk_bar.set_fraction(0.0)
            # drop the alert too: a bar that cannot be read is not an alert,
            # and a stale red one over an empty trough reads as broken
            self.disk_bar.get_style_context().remove_class("nearfull")
            self.disk_lbl.set_text(_t("Cannot be read"))
            return
        used = max(0, total - free)
        frac = max(0.0, min(1.0, used / total)) if total else 0.0
        self.disk_bar.set_fraction(frac)
        ctx = self.disk_bar.get_style_context()
        if frac >= 0.9:
            ctx.add_class("nearfull")
        else:
            ctx.remove_class("nearfull")
        self.disk_lbl.set_text("%s free of %s"
                               % (human_kb(free // 1024),
                                  human_kb(total // 1024)))

    def _refresh_procs(self, dtot, recompute):
        rows = []
        seen = {}
        cpu_now = {}   # pid -> CPU% shown this cycle (becomes next call's cache)
        names_seen = set()   # (pid, start-time) keys alive this cycle
        try:
            pids = os.listdir("/proc")
        except OSError:
            pids = []  # /proc unreadable -> empty table this cycle
        for pid in pids:
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/stat" % pid) as fh:
                    data = fh.read()
                rp = data.rfind(")")
                name = data[data.find("(") + 1:rp]
                fields = data[rp + 2:].split()
                utime = int(fields[11]); stime = int(fields[12])
                ptime = utime + stime
                started = fields[19]        # start time, for the name cache key
            except (OSError, ValueError, IndexError):
                continue
            if name in INTERPRETERS:
                name = self._script_name(pid, started, name)
            names_seen.add((pid, started))
            rss_kb = 0
            try:
                with open("/proc/%s/status" % pid) as fh:
                    for ln in fh:
                        if ln.startswith("VmRSS:"):
                            rss_kb = int(ln.split()[1]); break
            except (OSError, ValueError, IndexError):
                pass  # missing/malformed VmRSS -> leave rss_kb at 0
            if recompute:
                prev = self._proc_prev.get(pid)
                seen[pid] = ptime
                cpu_pct = 0.0
                if prev is not None and dtot > 0:
                    cpu_pct = max(0.0, min(100.0, 100.0 * (ptime - prev) / dtot))
            else:
                # manual refresh inside the sample window: leave the baseline
                # alone and re-show the last CPU% we computed for this pid
                cpu_pct = self._proc_cpu.get(pid, 0.0)
            cpu_now[pid] = cpu_pct
            # append the raw cpu_pct too (col 5) as the numeric CPU sort key
            rows.append((name, int(pid), human_kb(rss_kb),
                         "%.0f%%" % cpu_pct, rss_kb, cpu_pct))
        if recompute:
            self._proc_prev = seen
        self._proc_cpu = cpu_now
        # forget the names of processes that have gone, so a long session
        # doesn't accumulate an entry per process ever seen
        if len(self._name_cache) > len(names_seen):
            self._name_cache = {k: v for k, v in self._name_cache.items()
                                if k in names_seen}
        self._sync_store(rows)
        # a transient footer message (e.g. an End Program result) holds for a few
        # seconds; otherwise show the live count of running programs.
        if self._status_text is not None and time.monotonic() < self._status_until:
            self.stat.set_text(self._status_text)
        else:
            self._status_text = None
            n = len(rows)
            self.stat.set_text("%d program%s" % (n, "" if n == 1 else "s"))

    def _sync_store(self, rows):
        """Fold this tick's rows into the model in place, writing only what has
        actually changed.

        The table used to be rebuilt wholesale every 2s — clear() then append —
        which rewrote all four cells of every row whether or not a single figure
        had moved, so GTK redrew the entire list on each tick. It also threw
        away everything the model was anchoring: the selection and the scroll
        offset had to be captured and put back by hand, and the keyboard cursor
        (which nothing captured) was lost outright, so arrow-keying down the
        list dropped you back at the top twice a second.

        Rows are keyed on the program's ID (model col 1): one that is still
        there is updated cell by cell, one that has gone is removed, one that is
        new is appended. Nothing else is touched, so an unchanged program emits
        no change at all, and selection, cursor and scroll simply stay put.

        Gtk.ListStore iters are persistent, so the iters collected up front stay
        valid across the removals below and across the re-sorting that writing a
        sort-key cell triggers."""
        fresh = {r[1]: r for r in rows}     # pid -> row tuple
        gone = []
        for it in [row.iter for row in self.store]:
            row = fresh.pop(self.store.get_value(it, 1), None)
            if row is None:
                gone.append(it)
                continue
            for col, val in enumerate(row):
                if self.store.get_value(it, col) != val:
                    self.store.set_value(it, col, val)
        for it in gone:
            self.store.remove(it)
        for pid in sorted(fresh):
            self.store.append(list(fresh[pid]))

    def _end_process(self, _b=None):
        # End Program signals the program to stop — destructive, so confirm
        # first (with the exact target named) before anything is sent.
        model, it = self.tree.get_selection().get_selected()
        if it is None:
            self._flash(_t("Select a program first"))
            return
        pid = model.get_value(it, 1)
        name = model.get_value(it, 0)
        # One verb throughout: the button says End Program, so the card asks to
        # END it and the result says it is ending. The card used to ask "Ask
        # 'Writer' to close?" — a different verb from the button that had just
        # been pressed, which reads as a different action.
        # Pin WHICH program this is, not just its number — see _do_end.
        started = proc_start_time(pid)
        self._confirm(
            _t("End Program"),
            _t("End “%s”? Anything it has not saved will be lost.") % name,
            _t("End Program"), lambda: self._do_end(pid, name, started))

    def _do_end(self, pid, name, started=None):
        # The confirmation card is modal and can sit open for as long as the
        # user takes to read it. If the chosen program finishes in that window
        # its ID goes back to the kernel, which may already have given it to
        # something else — and the signal would then end a program the user
        # never picked, quietly and with its unsaved work. So re-read the start
        # time and only signal if this is still the same program; if it is not,
        # the one the user chose is already gone, which is what we say.
        if proc_start_time(pid) != started:
            self._flash(_t("%s had already finished") % name)
            return
        try:
            os.kill(pid, signal.SIGTERM)
            self._flash(_t("Ending %s") % name)
        except OSError as e:
            self._flash(self._end_problem(name, e))

    @staticmethod
    def _end_problem(name, exc):
        """A plain sentence for a program that would not end.

        The footer used to print the kernel's own wording straight through
        (`Could not close Writer: Operation not permitted`), which reads as an
        accusation and tells the user nothing about what to do. Nothing is lost
        in any of these cases — the program is simply still there."""
        err = getattr(exc, "errno", None)
        if err == errno.ESRCH:
            return _t("%s had already finished") % name
        if err in (errno.EPERM, errno.EACCES):
            return _t("%s belongs to the system and cannot be ended here") % name
        return _t("%s could not be ended, and is still running") % name

    def _confirm(self, title, message, ok_label, on_yes):
        # House-style modal confirmation for a destructive action: a papertone
        # card with a darker-beige border, a neutral Cancel, and a signage-red
        # primary — red is reserved for alerts and the active selection.
        dlg = Gtk.Dialog(transient_for=self, modal=True)
        dlg.set_decorated(False)
        dlg.get_style_context().add_class("smdlg")
        area = dlg.get_content_area()
        area.set_spacing(0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.get_style_context().add_class("smdlgbox")
        hd = Gtk.Label(label=title, xalign=0)
        hd.get_style_context().add_class("smdlgtitle")
        box.pack_start(hd, False, False, 0)
        msg = Gtk.Label(label=message, xalign=0)
        msg.get_style_context().add_class("smdlgmsg")
        msg.set_line_wrap(True); msg.set_max_width_chars(40)
        msg.set_margin_top(10); msg.set_margin_bottom(18)
        box.pack_start(msg, False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.get_style_context().add_class("smdlgcancel")
        cancel.connect("clicked", lambda *_: dlg.destroy())
        ok = Gtk.Button(label=ok_label)
        ok.get_style_context().add_class("smdlgdanger")
        ok.connect("clicked", lambda *_: (dlg.destroy(), on_yes()))
        row.pack_start(cancel, False, False, 0)
        row.pack_start(ok, False, False, 0)
        box.pack_start(row, False, False, 0)
        area.add(box)
        dlg.connect("key-press-event", self._dlg_key)   # Esc cancels
        dlg.show_all()

    def _dlg_key(self, dlg, ev):
        if ev.keyval == Gdk.KEY_Escape:
            dlg.destroy()
            return True
        return False

    def _on_selection_changed(self, sel):
        # End Program is enabled only while a row is actually selected.
        has = sel.get_selected()[1] is not None
        self.endbtn.set_sensitive(has)
        # Dim the icon in step with the button (CSS can't recolour a pixbuf).
        nbicons.set_image(self._end_icon,
            "stopsq", 16, self._END_ICON_ON if has else self._END_ICON_OFF)

    def _on_tree_button(self, tree, event):
        # Right-click selects the row under the pointer and opens a context menu
        # with the actions a program row implies (End Program / Copy ID).
        if event.button != 3 or event.type != Gdk.EventType.BUTTON_PRESS:
            return False
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if hit is None:
            return False          # right-click on empty space: no menu
        tree.grab_focus()
        tree.get_selection().select_path(hit[0])
        menu = Gtk.Menu()
        menu.get_style_context().add_class("smmenu")
        for label, cb in (("End Program", self._end_process),
                          (None, None),
                          ("Copy ID", self._copy_pid)):
            if label is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda _m, fn=cb: fn())
            menu.append(mi)
        menu.show_all()
        nbapp.popup_at(menu, event=event)
        return True

    def _copy_pid(self):
        # Copy the selected ID to the clipboard — handy for pasting into the
        # Terminal. Best-effort: a missing clipboard must never crash the app.
        model, it = self.tree.get_selection().get_selected()
        if it is None:
            return
        pid = model.get_value(it, 1)
        name = model.get_value(it, 0)
        try:
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(str(pid), -1)
            # Name what was copied, not just the number: with a dozen rows on
            # screen, "Copied ID 1274" does not say which row it came from.
            self._flash(_t("Copied the ID for %s") % name, secs=3)
        except Exception:
            pass

    def _on_key_del(self, _w, ev):
        # Delete ends the selected program (same confirmed path as the button).
        # Ignored while a dropdown or the About card owns the screen.
        if ev.keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            if self._menu_open is not None or getattr(self, "_about_layer", None):
                return False
            self._end_process()
            return True
        return False

    def _flash(self, msg, secs=6):
        # Show a status message in the footer that survives the next few refresh
        # ticks before reverting to the count of running programs.
        self._status_text = msg
        self._status_until = time.monotonic() + secs
        self.stat.set_text(msg)

    # ---- sorting ----
    def _on_header_clicked(self, _col, model_col):
        # A header click sorts by that column. Clicking the already-active column
        # flips direction; a fresh column opens on its natural order (busiest first
        # for the resource columns, A->Z / low->high otherwise).
        if model_col == self._sort_col:
            order = (Gtk.SortType.ASCENDING
                     if self._sort_order == Gtk.SortType.DESCENDING
                     else Gtk.SortType.DESCENDING)
        else:
            order = (Gtk.SortType.DESCENDING if model_col in self._desc_first
                     else Gtk.SortType.ASCENDING)
        self._apply_sort(model_col, order)

    def _apply_sort(self, model_col, order):
        # Single sort path shared by header clicks and the View menu: set the
        # model's sort key and move the header arrow onto the active column.
        self._sort_col = model_col
        self._sort_order = order
        self.store.set_sort_column_id(model_col, order)
        self._save_sort_prefs()
        for mc, c in self._sort_widgets.items():
            active = (mc == model_col)
            c.set_sort_indicator(active)
            if active:
                c.set_sort_order(order)

    def _cmp_name(self, model, a, b, *_data):
        na = (model.get_value(a, 0) or "").lower()
        nb = (model.get_value(b, 0) or "").lower()
        return (na > nb) - (na < nb)

    # ---- menus ----
    def _sort(self, col, order):
        try:
            self._apply_sort(col, order)
        except Exception:
            pass  # sorting is best-effort; never crash the menu

    def _manual_refresh(self):
        # Re-sample immediately and acknowledge it, so the action never feels
        # like it did nothing (the flash reverts to the count shortly).
        self.refresh(manual=True)
        # No full stop: the footer's resting text is "12 programs", so a
        # sentence-punctuated flash sat oddly beside it.
        self._flash(_t("Refreshed"), secs=2)

    def menu_items(self, name):
        if name == "View":
            # match the button: greyed-out (callback=None) unless a row is picked
            has_sel = self.tree.get_selection().get_selected()[1] is not None
            end_cb = (lambda: self._end_process(None)) if has_sel else None
            # advertise the Delete shortcut only while it's actionable. Both
            # labels are written out as LITERALS rather than picked by an
            # inline conditional: tools/i18n_check's chrome scan only sees a
            # constant in the label slot, so a computed label drops out of the
            # translation audit entirely.
            items = [
                ("Refresh Now", self._manual_refresh),
                nbapp.SEP,
                ("Sort by Memory",
                 lambda: self._sort(4, Gtk.SortType.DESCENDING)),
                ("Sort by Processor",  # numeric cpu_pct (5), not the formatted text (3)
                 lambda: self._sort(5, Gtk.SortType.DESCENDING)),
                ("Sort by Name",
                 lambda: self._sort(0, Gtk.SortType.ASCENDING)),
                ("Sort by ID",
                 lambda: self._sort(1, Gtk.SortType.ASCENDING)),
                nbapp.SEP,
            ]
            if has_sel:
                items.append(("End Program    Del", end_cb))
            else:
                items.append(("End Program", end_cb))
            return items
        return super().menu_items(name)

    def _install_css(self):
        css = b"""
        .smstage { background: #FCFBF8; }
        .smstage * { font-family: "Nimbus Sans","Helvetica",sans-serif; }

        /* small uppercase, letter-tracked section labels */
        .smsection { font-size: 11px; font-weight: 700; letter-spacing: 0.14em;
                     color: #6E695E; }

        /* gauge cards: flat papertone panels, hairline border, soft lift */
        .smcard { background: #F4F2EC; border: 1px solid #C9C4B6;
                  border-radius: 12px; padding: 18px 20px;
                  box-shadow: 0 1px 3px rgba(26,25,22,0.05); }
        .smcardtitle { font-size: 11px; font-weight: 700; letter-spacing: 0.14em;
                       color: #6E695E; }
        .smcardval { font-size: 15px; font-weight: 500; color: #1A1916; }

        /* gauges read as calm, neutral data, not alerts. The fill is a warm
           muted taupe (the muted-text tone) rather than ink: a full memory bar
           in #1A1916 rendered as a heavy BLACK slab, the one thing the papertone
           language never does. #6E695E stays legible on the #DED4C2 trough while
           reading as quiet data. */
        .smbar { min-height: 8px; }
        .smbar trough { min-height: 8px; background: #DED4C2;
                        border: none; border-radius: 4px; }
        .smbar progress { min-height: 8px; background: #6E695E;
                          border-radius: 4px; }
        /* a disk with under a tenth of its room left is a real alert, and the
           only thing on this page signage red is allowed to mean. (The
           Settings Storage page uses the same threshold and the same red.) */
        .smbar.nearfull progress { background: #C8341E; }

        /* process table: airy list inside a hairline frame */
        .smtreewrap { border: 1px solid #C9C4B6; border-radius: 12px;
                      background: #FCFBF8; }
        .smtree { background: #FCFBF8; color: #1A1916; font-size: 13px; }
        .smtree :selected { background: #EAE3D2; color: #1A1916; }
        .smtree header button { background: #F1EEE6; color: #6E695E;
                 font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
                 border: none; border-bottom: 1px solid #D7D2C5;
                 border-radius: 0; box-shadow: none; padding: 8px 12px; }
        .smtree header button:hover { background: #EFEBE0; }

        /* footer: hairline-separated status + destructive action */
        .smfoot { border-top: 1px solid #D7D2C5; padding-top: 14px; }
        .smstat { font-size: 13px; color: #6E695E; }
        /* neutral destructive trigger: ink text + hairline beige at rest, a
           red-bordered alert hint on hover; the real red lives on the confirm
           dialog's primary, per the design language (red = alert/selection). */
        .smend { padding: 8px 18px; background: #F1EEE6; color: #1A1916;
                 border: 1px solid #C9C4B6; border-radius: 8px;
                 box-shadow: none; font-size: 14px; font-weight: 600; }
        .smend:hover { background: #EFEBE0; border-color: #C8341E; }
        .smend:disabled { background: #F4F2EC; color: #9A9484;
                          border-color: #D7D2C5; }

        /* destructive-action confirmation -- house modal style */
        .smdlg { background: #F8F7F2; border: 1px solid #C9C4B6; }
        .smdlg * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .smdlgbox { padding: 22px 24px 18px; }
        .smdlgtitle { font-size: 17px; font-weight: 700; color: #1A1916; }
        .smdlgmsg { font-size: 13px; color: #2A2620; }
        .smdlgcancel { padding: 6px 18px; background: #FCFBF8; color: #2A2620;
                 border: 1px solid #C9C4B6; border-radius: 8px;
                 box-shadow: none; font-size: 13px; }
        .smdlgcancel:hover { background: #EFEBE0; }
        .smdlgdanger { padding: 6px 20px; background: #C8341E; color: #F8F7F2;
                 border: 1px solid #B12D19; border-radius: 8px;
                 box-shadow: none; font-size: 13px; font-weight: 600; }
        .smdlgdanger:hover { background: #B12D19; }

        /* right-click context menu -- house dropdown style (beige, never black) */
        .smmenu, .smmenu menuitem {
                 font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .smmenu { background: #F8F7F2; border: 1px solid #C9C4B6; padding: 4px 0; }
        .smmenu menuitem { padding: 6px 22px 6px 16px; color: #1A1916;
                           font-size: 13px; }
        .smmenu menuitem:hover { background: #EAE3D2; color: #1A1916; }
        .smmenu separator { background: #D7D2C5; min-height: 1px; margin: 4px 0; }
        """
        prov = Gtk.CssProvider(); prov.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


if __name__ == "__main__":
    nbapp.run(SystemMonitor)
