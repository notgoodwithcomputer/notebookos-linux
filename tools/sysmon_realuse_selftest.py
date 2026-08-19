#!/usr/bin/env python3
"""System Monitor: what a person meets, driven on the real window.

Every check here failed on the shipped app before the fix beside it, and every
one was found by USING the window rather than by reading it: changing the sort
and watching a column fall off the right edge, looking at the Processor card in
the first second after launch, reading the list of "programs" and finding two
thirds of it is the kernel talking to itself, and opening a View menu that will
not say which order the table is already in.

The window is hosted offscreen by tools/appdrive (the real widget tree, the
real /proc reads, the real handlers), so what is measured is the geometry and
the text a reader would see. Two checks are display-free, because their subject
is a name that comes out of /proc rather than anything on screen.

Run:
    tools/guestrun.sh python3 tools/sysmon_realuse_selftest.py
"""
import ast
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay",
                  "opt/notebook/de")
sys.path.insert(0, HERE)
sys.path.insert(0, DE)

_ROOT = tempfile.mkdtemp(prefix="nb-sysmon-realuse-")
os.environ["NB_DRIVE_HOME_ROOT"] = _ROOT
# This file reads the words a person reads — View items, column titles, the
# footer — so it has to know which language it is reading. nbi18n binds that
# once, at import, from $NB_LANG: with a developer's shell set to German the
# run went looking for a View item called "Sort by Name", raised where no
# check could catch it, and the five checks after that point never ran at all.
# Pin the language this file is written in. The one measurement that DEPENDS
# on the language — the width of the three cards, which is where F8 was found
# — is taken deliberately, in French, in a child process at the foot of the
# file.
os.environ["NB_LANG"] = "en"

import appdrive                                                # noqa: E402
import sysmon                                                  # noqa: E402
from gi.repository import Gtk                                  # noqa: E402

failures = []


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name
          + (("  " + detail) if detail else ""))
    if not condition:
        failures.append(name)


def run(name, fn):
    """Run one check so it can only fail BY NAME: an exception inside it is
    that check failing, never the suite falling over before the rest run."""
    try:
        ok, detail = fn()
    except Exception as exc:                                   # noqa: BLE001
        ok, detail = False, "raised %s: %s" % (type(exc).__name__, exc)
    check(name, ok, detail)


# --------------------------------------------------------------- helpers
def menu_fire(drive, wanted):
    """Fire the View item whose label is `wanted`, ignoring the state mark.

    The four sort items now carry a leading "✓ " or four spaces (the OS menu
    convention for an item that shows its own state), so a caller matching on
    the bare words has to strip the mark first — which is exactly what
    docs/MENU-CONVENTIONS.md says every reader of a label must do."""
    for item in drive.menu("View"):
        if not isinstance(item, tuple):
            continue
        if item[0].lstrip("✓ ") == wanted and item[1] is not None:
            item[1]()
            drive.pump(0.2)
            return True
    raise LookupError("no View item %r" % wanted)


def header_parts(column):
    """(title label, arrow image) inside a column's header button.

    Walks the real button rather than the code, so it finds GTK's own sort
    indicator just as readily as ours — the point of the check is WHERE the
    arrow a reader sees ended up, whoever packed it."""
    button = column.get_button()
    labels, images = [], []
    stack = [button]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Container):
            stack.extend(widget.get_children())
        if isinstance(widget, Gtk.Label):
            labels.append(widget)
        elif isinstance(widget, Gtk.Image):
            images.append(widget)
    title = next((lab for lab in labels
                  if lab.get_text() == column.get_title()), None)
    # An image showing nothing is not an arrow: the reserved slot that keeps
    # the columns from moving is an EMPTY image, and GTK parks its own unused
    # indicator at width 1. Of the arrows that are actually drawn take the
    # RIGHTMOST, so a stranded one can never be excused by a well-placed one
    # somewhere else.
    drawn = [im for im in images
             if im.get_allocation().width >= 8
             and im.get_storage_type() != Gtk.ImageType.EMPTY]
    arrow = max(drawn, key=lambda im: im.get_allocation().x) if drawn else None
    return title, arrow


def gauge_cards(drive):
    """The three RESOURCES cards, left to right, with their value labels."""
    out = []
    for widget in drive.walk():
        if not isinstance(widget, Gtk.Box):
            continue
        if "smcard" not in widget.get_style_context().list_classes():
            continue
        value = None
        for child in widget.get_children():
            if isinstance(child, Gtk.Label) and "smcardval" in \
                    child.get_style_context().list_classes():
                value = child
        out.append((widget.get_allocation().x, widget, value))
    out.sort(key=lambda row: row[0])
    return [(widget, value) for _x, widget, value in out]


def kernel_thread_pids():
    """Every task the kernel owns, by the flag the kernel itself sets."""
    out = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % pid) as fh:
                data = fh.read()
            fields = data[data.rfind(")") + 2:].split()
            if int(fields[6]) & 0x00200000:
                out.add(int(pid))
        except (OSError, ValueError, IndexError):
            continue
    return out


def since_boot_busy():
    total, idle = sysmon.cpu_times()
    return 0.0 if total <= 0 else (total - idle) / float(total)


# ------------------------------------------------------- the one window
drive = appdrive.Drive("sysmon")

# F3 is about the FIRST screen, so it is measured before anything is pumped:
# appdrive's own hosting pump is far short of the app's 0.5s sampling window
# and its 2s poll, so this is the window exactly as it is first painted.
first_cpu_text = drive.app.cpu_lbl.get_text()
first_cpu_frac = drive.app.cpu_bar.get_fraction()
first_busy = since_boot_busy()
first_rows_cpu = [row[5] for row in drive.app.store]
first_mem_text = drive.app.mem_lbl.get_text()


def first_paint_gauge():
    """The Processor card said nothing at all — no bar, no figure — for the two
    seconds until the first tick, beside two cards that were already reading."""
    if not first_cpu_text:
        return False, "the Processor card is blank on the first screen"
    return (abs(first_cpu_frac - first_busy) <= 0.05,
            "%r frac %.3f, processor busy since start-up %.3f"
            % (first_cpu_text, first_cpu_frac, first_busy))


run("the processor gauge carries a figure on the first screen",
    first_paint_gauge)


def first_paint_rows():
    """And every row's PROCESSOR cell read 0%. The seeded figures are each
    program's share of the machine since start-up, so they must add up to no
    more than the gauge showing the same thing — and to more than nothing."""
    total = sum(first_rows_cpu)
    return (0.0 < total <= first_busy * 100.0 + 0.5,
            "rows total %.2f%%, gauge %.2f%%, %d rows"
            % (total, first_busy * 100.0, len(first_rows_cpu)))


run("the first screen's processor column agrees with the gauge",
    first_paint_rows)

run("the memory gauge still reads on the first screen",
    lambda: (bool(first_mem_text) and " of " in first_mem_text
             or bool(first_mem_text), repr(first_mem_text)))

drive.pump(2.4)          # let the first real 2s sample land


# ---------------------------------------------------------------- F4/F6
def only_programs():
    """kthreadd, kworker/R-*, rcu_*, ksoftirqd/* were a third of this table:
    nothing a person started, nothing End Program can end, every one of them
    0 B, and all of them counted in the footer's "N programs"."""
    kernel = kernel_thread_pids()
    rows = [(row[0], row[1]) for row in drive.app.store]
    listed = [name for name, pid in rows if pid in kernel]
    if not kernel:
        return False, "no kernel threads on this host: check is vacuous"
    if listed:
        return False, "%d kernel threads listed, e.g. %s" % (
            len(listed), listed[:4])
    return True, "%d rows, %d kernel threads on this host" % (
        len(rows), len(kernel))


run("kernel threads are not offered as programs", only_programs)


def count_matches_list():
    return (drive.app.stat.get_text().startswith("%d " % len(drive.app.store)),
            "footer %r vs %d rows"
            % (drive.app.stat.get_text(), len(drive.app.store)))


run("the footer counts exactly the programs in the table", count_matches_list)


# A program whose name is longer than the 15 characters /proc keeps. The
# symlink names the executable, so the kernel's own comm is the truncation.
_bin = tempfile.mkdtemp(prefix="nb-sysmon-name-")
_long = os.path.join(_bin, "matchbox-window-manager")
_proc = None
try:
    os.symlink("/bin/sleep", _long)
    _proc = subprocess.Popen([_long, "45"])
except OSError as _exc:                                        # noqa: BLE001
    # No fixture is not a pass: the three checks below say so by name rather
    # than the suite falling over before the rest of them run.
    print("note: could not start the long-named program (%s)" % _exc)


def comm_of(pid):
    with open("/proc/%s/stat" % pid) as fh:
        data = fh.read()
    return data[data.find("(") + 1:data.rfind(")")]


# Popen returns as soon as the fork is made, and until the exec lands the child
# is still carrying THIS process's name — wait for the program itself.
_started = None
for _ in range(500):
    if _proc is None:
        break
    try:
        if comm_of(_proc.pid).startswith("matchbox"):
            break
    except OSError:
        pass
    time.sleep(0.01)
if _proc is not None:
    _started = sysmon.proc_start_time(_proc.pid)


class BareName(object):
    """The whole surface _full_name touches: no Gtk, no window, no display."""
    _full_name = sysmon.SystemMonitor._full_name

    def __init__(self):
        self._name_cache = {}


def full_name_from_cmdline():
    """matchbox-window-manager — the guest's own window manager — arrived as
    "matchbox-window", a word broken mid-syllable."""
    if _proc is None:
        return False, "no long-named program to read"
    comm = comm_of(_proc.pid)
    if len(comm) != 15:
        return False, "kernel did not truncate: comm is %r" % comm
    name = BareName()._full_name(str(_proc.pid), _started, comm)
    return (name == "matchbox-window-manager",
            "comm %r -> %r" % (comm, name))


run("a name cut short by /proc is completed from the command line",
    full_name_from_cmdline)


def chosen_name_kept():
    """Firefox's rows really are called "Isolated Web Co" — a name the program
    set for itself, sharing one executable with a dozen others. Preferring
    argv[0] blindly would collapse them all onto "firefox-esr"."""
    if _proc is None:
        return False, "no long-named program to read"
    name = BareName()._full_name(str(_proc.pid), _started, "Isolated Web Co")
    return (name == "Isolated Web Co", "-> %r" % name)


run("a name the program chose for itself is left alone", chosen_name_kept)


def table_shows_full_name():
    if _proc is None:
        return False, "no long-named program to look for"
    for _ in range(4):
        drive.pump(2.4)
        names = [row[0] for row in drive.app.store if row[1] == _proc.pid]
        if names:
            return (names[0] == "matchbox-window-manager", "row reads %r"
                    % names[0])
    return False, "the program never appeared in the table"


run("the table shows the whole program name", table_shows_full_name)


# ------------------------------------------------------------------- F1/F5
def geometry():
    columns = drive.app.tree.get_columns()
    return (sum(col.get_width() for col in columns),
            drive.app.tree.get_allocation().width,
            drive.app.sw.get_hadjustment())


def resort_keeps_every_column():
    """Sorting by NAME and then by MEMORY used to add the new sort arrow to
    MEMORY while NAME kept the width it had grown into, so the four columns
    added up to more than the table and the PROCESSOR figures — right-aligned,
    so it is the digits that go first — were pushed off the right edge until
    the next 2s tick re-dirtied the layout."""
    menu_fire(drive, "Sort by Name")
    drive.pump(2.4)                      # let a tick settle the layout
    menu_fire(drive, "Sort by Memory")
    drive.pump(0.1)                      # look BEFORE the next tick
    total, tree_width, adj = geometry()
    widths = [(col.get_title(), col.get_width())
              for col in drive.app.tree.get_columns()]
    return (total <= tree_width and adj.get_upper() <= adj.get_page_size() + 0.5,
            "columns %s total %d, table %d, upper %.0f page %.0f"
            % (widths, total, tree_width, adj.get_upper(),
               adj.get_page_size()))


run("changing the sort keeps every column inside the table",
    resort_keeps_every_column)


def sorting_moves_no_column():
    """The same defect stated as the invariant behind it, so it cannot hide
    behind whatever happens to be in the cells: which column the table is
    sorted by is not a fact about how wide the columns are. Measured with the
    2s poll stopped, so the only thing changing is the sort."""
    from gi.repository import GLib
    source = drive.app._refresh_source
    if source:
        GLib.source_remove(source)
        drive.app._refresh_source = 0
    try:
        seen = {}
        for label in ("Sort by Name", "Sort by ID", "Sort by Memory",
                      "Sort by Processor"):
            menu_fire(drive, label)
            drive.pump(0.3)
            seen[label] = tuple(col.get_width()
                                for col in drive.app.tree.get_columns())
        shapes = set(seen.values())
        return len(shapes) == 1, "; ".join(
            "%s %s" % (k, v) for k, v in sorted(seen.items()))
    finally:
        drive.app._refresh_source = GLib.timeout_add_seconds(
            2, drive.app.refresh)


run("sorting by another column moves no column", sorting_moves_no_column)


def arrow_beside_its_title():
    """On the expand column the arrow sat 600px from the word NAME, hard up
    against ID, and read as ID's arrow."""
    active = None
    for col in drive.app.tree.get_columns():
        title, arrow = header_parts(col)
        if arrow is not None and title is not None:
            gap = arrow.get_allocation().x - (title.get_allocation().x
                                              + title.get_allocation().width)
            if active is None or gap > active[1]:
                active = (col.get_title(), gap)
    if active is None:
        return False, "no sort arrow is drawn at all"
    return active[1] <= 24, "%s: arrow %d px from its title" % active


def arrow_after_sorting_by_name():
    """Sorting first, inside the check: a menu that would not fire is this
    check failing by name, not the suite falling over before the rest run."""
    menu_fire(drive, "Sort by Name")
    drive.pump(0.3)
    return arrow_beside_its_title()


run("the sort arrow sits beside the column it marks",
    arrow_after_sorting_by_name)


def one_arrow_only():
    drawn = [col.get_title() for col in drive.app.tree.get_columns()
             if header_parts(col)[1] is not None]
    return len(drawn) == 1, "arrows on %s" % drawn


run("only the column being sorted by shows an arrow", one_arrow_only)


# ----------------------------------------------------------------- F7
def menu_says_the_sort():
    """docs/MENU-CONVENTIONS.md section 3: an item carrying its own state is
    written "✓ " + label when on and four spaces + label when off. All four
    sort items were written identically whichever sort was in force."""
    wanted = {"Sort by Memory": 4, "Sort by Processor": 5,
              "Sort by Name": 0, "Sort by ID": 1}
    for label, col in sorted(wanted.items()):
        menu_fire(drive, label)
        marked = []
        for item in drive.menu("View"):
            if not isinstance(item, tuple):
                continue
            text = item[0]
            if text.lstrip("✓ ") not in wanted:
                continue
            if text.startswith("✓ "):
                marked.append(text[2:])
            elif not text.startswith("    "):
                return False, "%r carries neither a tick nor its padding" % text
        if marked != [label]:
            return False, "sorting by %s marks %s" % (label, marked)
        if drive.app._sort_col != col:
            return False, "%s did not take (sort col %s)" % (
                label, drive.app._sort_col)
    return True, "each of the four marks itself and only itself"


run("the View menu says which sort is in force", menu_says_the_sort)


# ----------------------------------------------------------------- F8
def cards_are_equal():
    """Three cards on one row, packed expand-but-not-homogeneous, took their
    width from the length of the figure each happened to carry: in French the
    Processor card came out 34px narrower than the two beside it."""
    widths = [card.get_allocation().width for card, _v in gauge_cards(drive)]
    if len(widths) != 3:
        return False, "found %d cards" % len(widths)
    # one pixel of slack: an odd number of pixels cannot be split three ways
    return max(widths) - min(widths) <= 1, "widths %s" % widths


run("the three gauge cards are the same width", cards_are_equal)


def figures_are_whole():
    """...and no card may buy that by cutting the last digits off a figure."""
    for card, value in gauge_cards(drive):
        if value is None:
            return False, "a card has no value label"
        text = value.get_text()
        layout = value.get_layout().get_pixel_size()[0]
        if layout > value.get_allocation().width:
            return False, "%r needs %dpx in %dpx" % (
                text, layout, value.get_allocation().width)
        if not text:
            return False, "a card carries no figure"
    return True, "; ".join(v.get_text() for _c, v in gauge_cards(drive))


run("every gauge figure fits its card whole", figures_are_whole)


def long_figure_wraps():
    """Equal cards mean a card can now be narrower than the figure it carries
    — a machine with a bigger disk, a longer translation, a larger interface
    face — and the one thing that must never happen to a measurement is losing
    its last digits off the right edge. Fed a figure wider than its card, the
    label has to take a second line rather than a cut. (Measured with a figure
    the checks above cannot supply: today's disks and all 17 languages fit,
    which is exactly why this policy needs a check of its own.)"""
    card, value = gauge_cards(drive)[0]
    before = value.get_text()
    try:
        value.set_text("1234.5 GB used of 6789.0 GB used of 1234.5 GB")
        drive.pump(0.2)
        width = value.get_allocation().width
        layout = value.get_layout()
        # The claim is that nothing is CUT, not that a line was broken: a card
        # wide enough to hold the whole figure on one line has kept it too.
        return (layout.get_pixel_size()[0] <= width,
                "%d line(s), %dpx of text in a %dpx card"
                % (layout.get_line_count(), layout.get_pixel_size()[0], width))
    finally:
        value.set_text(before)
        drive.pump(0.2)


run("a figure too long for its card is wrapped, never cut", long_figure_wraps)


# The same three cards in French, the language F8 was reported in: 279 / 313 /
# 312 where the row should be three equal thirds. nbi18n binds the language at
# import, so this one is measured in a child process (the idiom the calendar
# and academics suites use for a per-language measurement).
_FR_CODE = (
    "import sys; sys.path.insert(0, %r)\n"
    "import appdrive\n"
    "from gi.repository import Gtk\n"
    "d = appdrive.Drive('sysmon'); d.pump(2.4)\n"
    "out = []\n"
    "for w in d.walk():\n"
    "    if not isinstance(w, Gtk.Box):\n"
    "        continue\n"
    "    if 'smcard' not in w.get_style_context().list_classes():\n"
    "        continue\n"
    "    vals = [c for c in w.get_children() if isinstance(c, Gtk.Label)\n"
    "            and 'smcardval' in c.get_style_context().list_classes()]\n"
    "    a = w.get_allocation(); v = vals[0] if vals else None\n"
    "    out.append((a.x, a.width, v.get_text() if v else '',\n"
    "                v.get_layout().get_pixel_size()[0] if v else 0,\n"
    "                v.get_allocation().width if v else 0))\n"
    "print('FRCARDS', sorted(out))\n"
    "d.close()\n" % HERE)


def _french_cards():
    env = dict(os.environ, NB_LANG="fr",
               NB_DRIVE_HOME_ROOT=os.path.join(_ROOT, "fr"))
    out = subprocess.run([sys.executable, "-c", _FR_CODE], env=env,
                         capture_output=True, text=True, timeout=240).stdout
    line = [ln for ln in out.splitlines() if ln.startswith("FRCARDS ")]
    if not line:
        raise RuntimeError("no measurement from the French run: %r"
                           % out[-200:])
    return ast.literal_eval(line[0][len("FRCARDS "):])


try:
    _FR = _french_cards()
except Exception as _exc:                                      # noqa: BLE001
    _FR, _FR_ERR = [], "%s: %s" % (type(_exc).__name__, _exc)
else:
    _FR_ERR = ""


def french_cards_are_equal():
    if not _FR:
        return False, _FR_ERR
    widths = [width for _x, width, _t, _lay, _alloc in _FR]
    if len(widths) != 3:
        return False, "found %d cards: %r" % (len(widths), _FR)
    return max(widths) - min(widths) <= 1, "widths %s for %s" % (
        widths, [text for _x, _w, text, _lay, _alloc in _FR])


run("the three gauge cards are the same width in French",
    french_cards_are_equal)


def french_figures_are_whole():
    if not _FR:
        return False, _FR_ERR
    for _x, _w, text, layout, alloc in _FR:
        if not text:
            return False, "a card carries no figure"
        if layout > alloc:
            return False, "%r needs %dpx in %dpx" % (text, layout, alloc)
    return True, "; ".join(text for _x, _w, text, _l, _a in _FR)


run("every French gauge figure fits its card whole", french_figures_are_whole)


# ------------------------------------------------------------------- done
try:
    if _proc is not None:
        _proc.terminate()
        _proc.wait(timeout=5)
except Exception:                                              # noqa: BLE001
    pass
drive.close()

print("\n%s" % ("ALL PASS" if not failures
                else "FAILURES: " + ", ".join(failures)))
print("RESULT: %s" % ("PASS" if not failures else "FAIL"))
sys.exit(0 if not failures else 1)
