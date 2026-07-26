"""Shared printing for Notebook OS.

CUPS is the spooler (cupsd starts at boot); USB printers are auto-discovered by
the kernel usblp driver + CUPS usb backend, with Gutenprint (~5000 models) and
driverless IPP covering the drivers. Apps never talk to a printer directly: they
render their document to a PDF with cairo and hand the file here, and we submit
it with `lp`.

This module carries three things:

  * discovery + submit .......... list_printers() / submit_pdf()
  * a small themed Print dialog .. print_document() / print_booklet()
  * page/booklet rendering ....... simple_pdf() / booklet_pdf()

booklet_pdf() is the "Zine Print" engine shared by Novel and Screenplay: it lays
N half-letter (5.5x8.5") logical pages 2-up onto letter sheets in saddle-stitch
folding order for a fold-down-the-middle booklet.

Every function is guarded so the module imports and the non-GUI helpers behave
on a host with no CUPS (they simply report "no printer") — the DE selftests run
there. cairo is imported lazily inside the render helpers so discovery works even
where cairo is absent.
"""

import os
import shutil
import subprocess
import tempfile

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

# ---- units -----------------------------------------------------------------
PT_PER_IN = 72.0
HALF_W_PT = int(round(5.5 * PT_PER_IN))   # 396  — half-letter page width
HALF_H_PT = int(round(8.5 * PT_PER_IN))   # 612  — half-letter page height
# A letter sheet turned landscape holds two half-letter pages side by side
# (11" x 8.5"); folding it down the middle yields the 5.5x8.5 booklet.
SHEET_W_PT = HALF_W_PT * 2                 # 792
SHEET_H_PT = HALF_H_PT                      # 612


# ---- discovery + submit ----------------------------------------------------
def _have(cmd):
    return shutil.which(cmd) is not None


def list_printers():
    """Return (printers, default_name).

    printers is a list of {"name", "info", "ready"} dicts; default_name is the
    system default destination or None. Returns ([], None) if CUPS is not
    reachable (no lpstat, cupsd down, no printers configured)."""
    if not _have("lpstat"):
        return [], None
    names = []
    try:
        out = subprocess.run(["lpstat", "-e"], capture_output=True,
                             text=True, timeout=4)
        names = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    except Exception:
        names = []
    # status/info per printer (best effort)
    ready = {}
    info = {}
    try:
        out = subprocess.run(["lpstat", "-l", "-p"], capture_output=True,
                             text=True, timeout=4)
        cur = None
        for ln in out.stdout.splitlines():
            if ln.startswith("printer "):
                parts = ln.split()
                cur = parts[1] if len(parts) > 1 else None
                if cur is not None:
                    ready[cur] = ("disabled" not in ln and "unable" not in ln)
            elif cur and ln.strip().startswith("Description:"):
                info[cur] = ln.split(":", 1)[1].strip()
    except Exception:
        pass
    default = None
    try:
        out = subprocess.run(["lpstat", "-d"], capture_output=True,
                             text=True, timeout=4)
        line = out.stdout.strip()
        if ":" in line and "no " not in line.lower():
            default = line.split(":", 1)[1].strip() or None
    except Exception:
        default = None
    printers = [{"name": n, "info": info.get(n, n),
                 "ready": ready.get(n, True)} for n in names]
    if default is None and printers:
        default = printers[0]["name"]
    return printers, default


def printer_stopped(name):
    """Return a human-readable reason if the queue is stopped/disabled, else None.

    CUPS reacts to a backend failure by STOPPING the queue (the default
    ErrorPolicy is stop-printer). Everything afterwards still spools happily and
    `lp` still exits 0 — the jobs simply pile up behind a dead queue. Without
    this check the UI cheerfully reports "Sent to printer" forever while nothing
    ever prints, which is indistinguishable to the user from the OS ignoring
    them."""
    if not _have("lpstat"):
        return None
    try:
        out = subprocess.run(["lpstat", "-p", name], capture_output=True,
                             text=True, timeout=4)
    except Exception:
        return None
    text = out.stdout or ""
    low = text.lower()
    if "disabled" not in low and "stopped" not in low:
        return None
    # "printer X disabled since ... -\n\treason" — keep the reason if there is one.
    reason = ""
    for ln in text.splitlines()[1:]:
        if ln.strip():
            reason = ln.strip()
            break
    return reason or "The printer queue is paused."


def resume_printer(name):
    """Re-enable a stopped queue. Returns True if it looks enabled afterwards."""
    if not _have("cupsenable"):
        return False
    try:
        subprocess.run(["cupsenable", name], capture_output=True,
                       text=True, timeout=6)
        subprocess.run(["cupsaccept", name], capture_output=True,
                       text=True, timeout=6)
    except Exception:
        return False
    return printer_stopped(name) is None


def submit_pdf(pdf_path, printer=None, copies=1, options=None,
               job_name="Notebook OS"):
    """Send a PDF to the spooler with `lp`. options is a dict of -o key=value
    (value None -> a bare -o flag). Returns (ok, message).

    Note `lp` exiting 0 only means the job was QUEUED, never that it printed, so
    a stopped queue is checked both before and after handing the job over."""
    if not _have("lp"):
        return False, "Printing is unavailable (CUPS not installed)."
    if not (pdf_path and os.path.exists(pdf_path)):
        return False, "Nothing to print."
    # A queue that is already stopped will swallow the job silently — clear the
    # jam first rather than adding to the pile.
    if printer:
        why = printer_stopped(printer)
        if why and not resume_printer(printer):
            return False, ("The printer is paused and could not be restarted. "
                           "%s" % why)
    cmd = ["lp"]
    if printer:
        cmd += ["-d", printer]
    cmd += ["-n", str(max(1, int(copies)))]
    cmd += ["-t", job_name]
    for k, v in (options or {}).items():
        cmd += ["-o", k if v is None else "%s=%s" % (k, v)]
    cmd.append(pdf_path)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception as e:
        return False, "Could not reach the printer: %s" % e
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "print command failed").strip()
        return False, msg
    # The job is queued. If processing it knocked the queue over, say so now
    # instead of claiming success.
    if printer:
        why = printer_stopped(printer)
        if why:
            return False, ("The printer stopped while starting the job. %s" % why)
    return True, (r.stdout.strip() or "Sent to printer.")


# ---- page rendering --------------------------------------------------------
def simple_pdf(out_path, page_count, draw_page, w_pt=HALF_W_PT, h_pt=HALF_H_PT):
    """Render page_count logical pages sequentially into a PDF (one logical page
    per sheet). draw_page(cr, page_no, w, h) draws 1-indexed page page_no with
    the origin at its top-left. Used by "Export to PDF"."""
    import cairo
    surf = cairo.PDFSurface(out_path, w_pt, h_pt)
    cr = cairo.Context(surf)
    for n in range(1, max(1, page_count) + 1):
        cr.save()
        draw_page(cr, n, w_pt, h_pt)
        cr.restore()
        cr.show_page()
    surf.finish()
    return out_path


def _booklet_order(total4):
    """Yield (front_left, front_right, back_left, back_right) 1-indexed page
    numbers for each sheet of a saddle-stitch booklet with total4 pages
    (a multiple of 4). Standard nested-fold imposition."""
    sheets = total4 // 4
    for s in range(sheets):
        yield (total4 - 2 * s, 1 + 2 * s, 2 + 2 * s, total4 - 1 - 2 * s)


def booklet_pdf(out_path, page_count, draw_page):
    """Impose page_count half-letter (5.5x8.5") logical pages 2-up onto letter
    sheets in booklet folding order, front then back per sheet, so the printed
    stack folds down the middle into a reading-order booklet.

    draw_page(cr, page_no, w, h) draws 1-indexed logical page page_no (w=396,
    h=612) with the origin at its top-left; it is never called for the blank
    padding pages that round the count up to a multiple of four. Returns the
    number of physical sheet-sides written (for duplex hinting)."""
    import cairo
    total4 = ((max(1, page_count) + 3) // 4) * 4

    def place(cr, page_no, x_off):
        if 1 <= page_no <= page_count:
            cr.save()
            cr.translate(x_off, 0)
            cr.rectangle(0, 0, HALF_W_PT, HALF_H_PT)
            cr.clip()
            draw_page(cr, page_no, HALF_W_PT, HALF_H_PT)
            cr.restore()

    surf = cairo.PDFSurface(out_path, SHEET_W_PT, SHEET_H_PT)
    cr = cairo.Context(surf)
    sides = 0
    for fl, fr, bl, br in _booklet_order(total4):
        place(cr, fl, 0)              # front side: [left | right]
        place(cr, fr, HALF_W_PT)
        cr.show_page()
        sides += 1
        place(cr, bl, 0)             # back side: [left | right]
        place(cr, br, HALF_W_PT)
        cr.show_page()
        sides += 1
    surf.finish()
    return sides


# ---- themed Print dialog ---------------------------------------------------
_CSS = b"""
.nbprint { background: #FCFBF8; }
.nbprint * { font-family: "Nimbus Sans","Helvetica",sans-serif; color: #1A1916; }
.nbprint-h { font-family: "Newsreader","Liberation Serif","Georgia",serif;
             font-size: 20px; font-weight: 600; }
.nbprint-sub { font-size: 12.5px; color: #6E695E; }
.nbprint-note { font-size: 12.5px; color: #9A9484; }
.nbprint entry, .nbprint combobox button.combo, .nbprint spinbutton {
             background: #FCFBF8; border: 1px solid #C9C4B6; border-radius: 4px;
             box-shadow: none; padding: 4px 8px; }
.nbprint-btn { padding: 8px 20px; background: #FCFBF8; border: 1px solid #C9C4B6;
             border-radius: 4px; box-shadow: none; font-size: 14px; }
.nbprint-btn:hover { background: #F1EEE6; }
.nbprint-primary { padding: 8px 22px; background: #C8341E; background-image: none;
             color: #FCFBF8; border: 1px solid #C8341E; border-radius: 4px;
             box-shadow: none; font-size: 14px; font-weight: 600; }
.nbprint-primary:hover { background: #B12D19; border-color: #B12D19; }
.nbprint-primary:disabled { background: #E0B8B0; border-color: #E0B8B0; color: #FCFBF8; }
"""
_css_done = False


def _ensure_css():
    global _css_done
    if _css_done:
        return
    try:
        from gi.repository import Gdk
        prov = Gtk.CssProvider()
        prov.load_from_data(_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        _css_done = True
    except Exception:
        pass


def _dialog(parent, title):
    _ensure_css()
    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title(title)
    win.set_modal(True)
    win.set_type_hint(gi.repository.Gdk.WindowTypeHint.DIALOG)
    if parent is not None:
        win.set_transient_for(parent)
        win.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
    else:
        win.set_position(Gtk.WindowPosition.CENTER)
    win.set_border_width(0)
    win.get_style_context().add_class("nbprint")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_margin_top(24); box.set_margin_bottom(20)
    box.set_margin_start(28); box.set_margin_end(28)
    win.add(box)
    return win, box


def _no_printer_dialog(parent, extra_note):
    win, box = _dialog(parent, "Print")
    h = Gtk.Label(label="No printer found", xalign=0)
    h.get_style_context().add_class("nbprint-h")
    box.pack_start(h, False, False, 0)
    msg = ("Connect a USB printer and switch it on, then try again — Notebook OS "
           "detects most printers automatically.")
    s = Gtk.Label(label=msg, xalign=0); s.set_line_wrap(True)
    s.set_max_width_chars(42)
    s.get_style_context().add_class("nbprint-sub")
    box.pack_start(s, False, False, 0)
    if extra_note:
        n = Gtk.Label(label=extra_note, xalign=0); n.set_line_wrap(True)
        n.set_max_width_chars(42)
        n.get_style_context().add_class("nbprint-note")
        box.pack_start(n, False, False, 0)
    row = Gtk.Box(spacing=10); row.set_halign(Gtk.Align.END)
    row.set_margin_top(8)
    close = Gtk.Button(label="Close")
    close.get_style_context().add_class("nbprint-primary")
    close.connect("clicked", lambda *_: win.destroy())
    row.pack_end(close, False, False, 0)
    box.pack_start(row, False, False, 0)
    win.show_all()
    return win


def _print_dialog(parent, job_name, make_pdf, booklet, booklet_note):
    """Core dialog. make_pdf(path) writes the document PDF to path.
    When booklet is True the copies row is joined by a two-sided toggle and the
    supplied booklet_note is shown."""
    printers, default = list_printers()
    if not printers:
        note = ("Your work is safe — use File ▸ Export to PDF to save it as "
                "a file you can print anywhere.")
        return _no_printer_dialog(parent, note)

    win, box = _dialog(parent, "Print")
    h = Gtk.Label(label="Print", xalign=0)
    h.get_style_context().add_class("nbprint-h")
    box.pack_start(h, False, False, 0)

    grid = Gtk.Grid(row_spacing=12, column_spacing=14)
    box.pack_start(grid, False, False, 4)

    def _lbl(text):
        la = Gtk.Label(label=text, xalign=0)
        la.get_style_context().add_class("nbprint-sub")
        return la

    grid.attach(_lbl("Printer"), 0, 0, 1, 1)
    combo = Gtk.ComboBoxText()
    sel = 0
    for idx, p in enumerate(printers):
        combo.append_text(p["info"] or p["name"])
        if p["name"] == default:
            sel = idx
    combo.set_active(sel)
    combo.set_hexpand(True)
    grid.attach(combo, 1, 0, 1, 1)

    grid.attach(_lbl("Copies"), 0, 1, 1, 1)
    copies = Gtk.SpinButton.new_with_range(1, 99, 1)
    copies.set_value(1)
    copies.set_halign(Gtk.Align.START)
    grid.attach(copies, 1, 1, 1, 1)

    two_sided = None
    if booklet:
        two_sided = Gtk.CheckButton(label="Print both sides (fold into a booklet)")
        two_sided.set_active(True)
        grid.attach(two_sided, 1, 2, 1, 1)
        if booklet_note:
            bn = Gtk.Label(label=booklet_note, xalign=0)
            bn.set_line_wrap(True); bn.set_max_width_chars(40)
            bn.get_style_context().add_class("nbprint-note")
            grid.attach(bn, 1, 3, 1, 1)

    row = Gtk.Box(spacing=10); row.set_halign(Gtk.Align.END)
    row.set_margin_top(10)
    cancel = Gtk.Button(label="Cancel")
    cancel.get_style_context().add_class("nbprint-btn")
    cancel.connect("clicked", lambda *_: win.destroy())
    go = Gtk.Button(label="Print")
    go.get_style_context().add_class("nbprint-primary")
    row.pack_end(go, False, False, 0)
    row.pack_end(cancel, False, False, 0)
    box.pack_start(row, False, False, 0)

    status = Gtk.Label(xalign=0); status.set_line_wrap(True)
    status.set_max_width_chars(42)
    status.get_style_context().add_class("nbprint-note")
    box.pack_start(status, False, False, 0)

    def on_print(_b):
        # Rendering + spooling blocks this handler for a beat; without this the
        # button stays live and an impatient second click prints twice.
        go.set_sensitive(False)
        status.set_text("Preparing…")
        while Gtk.events_pending():
            Gtk.main_iteration()
        pname = printers[combo.get_active() if combo.get_active() >= 0 else 0]["name"]
        try:
            fd, path = tempfile.mkstemp(suffix=".pdf", prefix="nbprint-")
            os.close(fd)
            make_pdf(path)
        except Exception as e:
            status.set_text("Could not prepare the document: %s" % e)
            go.set_sensitive(True)
            return
        opts = {"media": "Letter"}
        if booklet:
            opts["media"] = "Letter"
            opts["landscape"] = None
            if two_sided is not None and two_sided.get_active():
                opts["sides"] = "two-sided-short-edge"
        ok, msg = submit_pdf(path, printer=pname,
                             copies=int(copies.get_value()),
                             options=opts, job_name=job_name)
        try:
            os.unlink(path)
        except Exception:
            pass
        if ok:
            win.destroy()
        else:
            status.set_text(msg)
            go.set_sensitive(True)

    go.connect("clicked", on_print)
    win.show_all()
    return win


def print_document(parent, make_pdf, job_name="Document"):
    """Show a Print dialog for a normal document. make_pdf(path) must write a
    PDF of the document to path. Handles the no-printer case gracefully."""
    return _print_dialog(parent, job_name, make_pdf, booklet=False,
                         booklet_note=None)


def print_booklet(parent, make_pdf, job_name="Booklet"):
    """Show a Print dialog for a zine/booklet. make_pdf(path) must write the
    already-imposed booklet PDF (see booklet_pdf) to path."""
    note = ("Sheets print two-up on letter paper; stack them, fold down the "
            "middle and staple the spine.")
    return _print_dialog(parent, job_name, make_pdf, booklet=True,
                         booklet_note=note)


# ---- selftest --------------------------------------------------------------
def _selftest():
    # imposition order is the crux — verify the classic N=8 layout.
    got = list(_booklet_order(8))
    assert got == [(8, 1, 2, 7), (6, 3, 4, 5)], got
    # padding rounds up to a multiple of four
    assert ((6 + 3) // 4) * 4 == 8
    # render a booklet without a printer present
    import cairo  # noqa: F401
    fd, p = tempfile.mkstemp(suffix=".pdf"); os.close(fd)
    sides = booklet_pdf(p, 6, lambda cr, n, w, h: (
        cr.move_to(20, 40), cr.show_text("page %d" % n)))
    assert sides == 4, sides          # 6 pages -> 8 slots -> 2 sheets -> 4 sides
    assert os.path.getsize(p) > 0
    os.unlink(p)
    # discovery must never raise, even with no CUPS
    printers, _ = list_printers()
    assert isinstance(printers, list)
    print("nbprint selftest: OK")


if __name__ == "__main__":
    _selftest()
