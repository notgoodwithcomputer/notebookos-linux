"""Shared printing for Notebook OS.

CUPS is the spooler (cupsd starts at boot). Apps never talk to a printer
directly: they render their document to a PDF with cairo and hand the file here,
and we submit it with `lp`.

A printer is reached one of two ways. Printers made since roughly 2016 advertise
IPP Everywhere and rasterise pages themselves, so the `ippusb` backend hands
them the PDF over their USB IPP interface and no driver is involved — see
package/ippusb. Older printers need a page language, which comes from Gutenprint
(~5000 models) plus brlaser, splix and captdriver for the host-based lasers.
Settings picks between the two; nothing here has to care which was used.

This module carries three things:

  * discovery + submit .......... list_printers() / submit_pdf()
  * a small themed Print dialog .. print_document() / print_booklet()
  * page/booklet rendering ....... simple_pdf() / booklet_pdf()

Discovery, document rendering, and sending all run as background jobs, so the
dialog opens immediately and remains responsive throughout printing.

booklet_pdf() is the "Zine Print" engine shared by Novel and Screenplay: it lays
N half-letter (5.5x8.5") logical pages 2-up onto letter sheets in saddle-stitch
folding order for a fold-down-the-middle booklet.

Every function is guarded so the module imports and the non-GUI helpers behave
on a host with no CUPS (they simply report "no printer") — the DE selftests run
there. cairo is imported lazily inside the render helpers so discovery works even
where cairo is absent.
"""

import errno
import os
import shutil
import subprocess
import tempfile
import threading

import nbjobs
import nbmotion

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

# ---- page sizes -------------------------------------------------------------
# CUPS media names for the page sizes the apps render at. A job is spooled with
# the size its PDF was DRAWN at; anything else and CUPS scales the page to fit,
# which quietly moves every margin the writer chose.
DEFAULT_MEDIA = "Letter"
MEDIA_NAMES = ("Letter", "Legal", "A4", "A5", "Executive", "Statement")


def _clean_media(media):
    """A CUPS media name we are sure of, or Letter. Never passes an app's raw
    string through to `lp` — an unknown -o media= is a job CUPS rejects."""
    for name in MEDIA_NAMES:
        if isinstance(media, str) and media.strip().lower() == name.lower():
            return name
    return DEFAULT_MEDIA


# ---- failure messages ------------------------------------------------------
# This is the Print dialog EVERY app in the OS opens, so a raw exception shown
# here leaks out of all of them at once. It used to read
#     Could not prepare the document: [Errno 28] No space left on device
# — an errno and a Python repr, for a person who only wants to know whether
# anything was printed and whether their document is all right. Nothing here is
# ever destructive: making the print file is a one-way copy into a temporary
# file, so the answer is always "your document is fine", and these sentences say
# so plainly.
def _prepare_problem(exc):
    """One calm sentence for a document that could not be turned into a print
    file. Never contains an errno, a path, or an exception repr."""
    err = getattr(exc, "errno", None)
    if err == errno.ENOSPC:
        return ("There was not enough room to prepare this for printing. "
                "Free up some space and try again. Nothing was printed.")
    if err in (errno.EACCES, errno.EPERM, errno.EROFS):
        return ("This document could not be prepared for printing on this "
                "machine. Nothing was printed.")
    return ("This document could not be prepared for printing. Nothing was "
            "printed.")


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


DISCOVER_KEY = "printers"


def discover_printers_async(owner, on_ready, key=DISCOVER_KEY,
                            policy=nbjobs.REPLACE):
    """Run list_printers() on a background thread; call on_ready(printers,
    default) on the owner's dispatcher, or not at all.

    list_printers() runs four `lpstat` subprocesses with a four-second timeout
    each, so on a machine whose cupsd is wedged or whose printer has been
    unplugged it can block for the better part of quarter of a minute. Called on
    the UI thread — which is how every caller used to call it — that is a Print
    dialog that does not appear, from a menu item that gave no sign of having
    been clicked, in an OS whose whole promise is that it responds.

    `owner` is an nbjobs.JobOwner belonging to whatever will show the answer.
    Close it and the answer is dropped: a discovery started by a dialog that has
    since been closed (or closed and reopened) cannot reach back into it.

    Discovery is defined never to fail — list_printers() already swallows
    everything a missing or broken CUPS can do — so an error is delivered as the
    same empty result the synchronous call would have returned.
    """
    def work(job):
        job.checkpoint()
        return list_printers()

    return owner.start(
        key, work,
        on_done=lambda res: on_ready(res[0], res[1]),
        on_error=lambda _err: on_ready([], None),
        policy=policy)


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
    return explain_reason(reason) or "The printer queue is paused."


# CUPS and IPP report why a printer stopped as a keyword. They are precise and
# unreadable, and they are the words a person sees at the exact moment their
# document did not come out, so the common ones are said in plain English.
_REASONS = (
    ("media-jam", "There is a paper jam."),
    ("media-empty", "The printer is out of paper."),
    ("media-needed", "The printer is out of paper."),
    ("cover-open", "A cover on the printer is open."),
    ("door-open", "A door on the printer is open."),
    ("input-tray-missing", "The paper tray is missing."),
    ("output-area-full", "The output tray is full."),
    ("marker-supply-empty", "The printer is out of ink or toner."),
    ("toner-empty", "The printer is out of toner."),
    ("marker-supply-low", "The printer is low on ink or toner."),
    ("toner-low", "The printer is low on toner."),
    ("offline", "The printer is switched off or unplugged."),
    ("shutdown", "The printer is switched off."),
    ("timed-out", "The printer stopped responding."),
    ("connecting-to-device", "The printer is not responding yet."),
    ("paused", "The printer queue is paused."),
)


def explain_reason(text):
    """A readable sentence for a CUPS/IPP state reason, or the text unchanged
    when it is not one we know. Never returns empty for non-empty input."""
    low = (text or "").lower()
    for key, sentence in _REASONS:
        if key in low:
            return sentence
    return (text or "").strip()


def jobs_pending(name):
    """True if the queue still has a job waiting or printing."""
    if not _have("lpstat"):
        return False
    try:
        out = subprocess.run(["lpstat", "-o", name], capture_output=True,
                             text=True, timeout=4)
    except Exception:
        return False
    return any(ln.strip() for ln in out.stdout.splitlines())


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


def make_print_file(make_pdf):
    """Render make_pdf(path) into a fresh temporary PDF and return its path.

    Whatever make_pdf raises is re-raised, but the half-written temporary file
    goes with it. A print that failed must not leave a file behind: the ENOSPC
    message asks the person to free up some space, and the leftovers of the
    attempt that just failed are part of what filled the disk. /tmp is a tmpfs
    on the live system, so each abandoned draft also sits in RAM until reboot."""
    fd, path = tempfile.mkstemp(suffix=".pdf", prefix="nbprint-")
    os.close(fd)
    try:
        make_pdf(path)
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


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
    except Exception:
        # Same rule as _prepare_problem: the spooler's own exception text is
        # never something to put in front of the person holding the paper.
        return False, ("The printer could not be reached. Check that it is "
                       "switched on and plugged in, then try again.")
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
_RENDER_CONTEXT = threading.local()


def _render_step(done, total):
    """Checkpoint and report a page boundary when printing in a worker."""
    job = getattr(_RENDER_CONTEXT, "job", None)
    if job is None:
        return
    job.checkpoint()
    job.progress(0.05 + 0.75 * (done / float(max(1, total))), "rendering")


def simple_pdf(out_path, page_count, draw_page, w_pt=HALF_W_PT, h_pt=HALF_H_PT):
    """Render page_count logical pages sequentially into a PDF (one logical page
    per sheet). draw_page(cr, page_no, w, h) draws 1-indexed page page_no with
    the origin at its top-left. Used by "Export to PDF"."""
    import cairo
    surf = cairo.PDFSurface(out_path, w_pt, h_pt)
    cr = cairo.Context(surf)
    total = max(1, page_count)
    for n in range(1, total + 1):
        _render_step(n - 1, total)
        cr.save()
        draw_page(cr, n, w_pt, h_pt)
        cr.restore()
        cr.show_page()
        _render_step(n, total)
    surf.finish()
    return out_path


def _booklet_order(total4):
    """Yield (front_left, front_right, back_left, back_right) 1-indexed page
    numbers for each sheet of a saddle-stitch booklet with total4 pages
    (a multiple of 4). Standard nested-fold imposition."""
    sheets = total4 // 4
    for s in range(sheets):
        yield (total4 - 2 * s, 1 + 2 * s, 2 + 2 * s, total4 - 1 - 2 * s)


FOLD_LINE_W = 0.4          # pt — a hairline, thin enough to fold along
FOLD_LINE_INK = (0.0, 0.0, 0.0)


def booklet_pdf(out_path, page_count, draw_page, fold_line=False):
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
    total_sides = max(1, total4 // 2)
    for fl, fr, bl, br in _booklet_order(total4):
        _render_step(sides, total_sides)
        place(cr, fl, 0)              # front side: [left | right]
        place(cr, fr, HALF_W_PT)
        # The fold, on the OUTSIDE of the finished booklet only. Sheet 1's
        # front side is [back cover | front cover], so this hairline runs
        # between them — down the spine — and is the line to fold and staple
        # along once the stack is assembled. Drawn on that sheet alone: a line
        # on the inner sheets would be buried in the fold and just costs ink.
        if fold_line and sides == 0:
            cr.save()
            cr.set_source_rgb(*FOLD_LINE_INK)
            cr.set_line_width(FOLD_LINE_W)
            cr.move_to(HALF_W_PT, 0)
            cr.line_to(HALF_W_PT, SHEET_H_PT)
            cr.stroke()
            cr.restore()
        cr.show_page()
        sides += 1
        _render_step(sides, total_sides)
        place(cr, bl, 0)             # back side: [left | right]
        place(cr, br, HALF_W_PT)
        cr.show_page()
        sides += 1
        _render_step(sides, total_sides)
    surf.finish()
    return sides


# ---- report text: PangoCairo, never the cairo toy font API -------------------
# Journal, Academics, Cookbook and Screenplay each carried their own little
# wrap()/emit() pair built on cairo's TOY font API — cr.select_font_face() plus
# cr.show_text(). That API binds ONE FreeType face and does no font fallback
# whatsoever: every character the chosen face does not have is drawn as an empty
# box. On the guest that face is DejaVu, which has no CJK and no Devanagari, so
# a journal written in Chinese, Japanese, Korean or Hindi printed as rows of
# tofu — while pdftotext still pulled the words straight back out, which is why
# text-only checks kept passing over blank paper.
#
# Pango does per-character fallback across every installed font, which is the
# only reason Writer's body and the whole of Novel print those scripts properly
# on the very same machine. PdfText is that same engine, in the shape the four
# report renderers already used, so each of them is a drop-in swap.
LETTER_W_PT = 612.0
LETTER_H_PT = 792.0

# The report body face. A generic family, resolved through fontconfig exactly
# as the old toy "Serif" was — the difference is that Pango then falls back per
# character instead of giving up on the first missing glyph.
REPORT_FAMILY = "serif"


def _rgb_of(hexc):
    """(r, g, b) floats from '#RRGGBB'. Black on anything unparseable — a
    colour is never worth failing a print for."""
    try:
        h = str(hexc).lstrip("#")
        return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
                int(h[4:6], 16) / 255.0)
    except Exception:
        return (0.0, 0.0, 0.0)


# Inline runs PdfText.emit understands, as (start_char, end_char, kind) tuples.
SPAN_KINDS = ("bold", "italic", "highlight")
_HIGHLIGHT = (0xFBFB, 0xE7E7, 0xA0A0)     # #FBE7A0, the on-screen highlighter


def _span_attrs(text, spans):
    """A Pango.AttrList for inline (start, end, kind) character spans."""
    from gi.repository import Pango
    al = Pango.AttrList()
    # char index -> byte index, since Pango attributes are indexed in bytes
    off = [0]
    for ch in text:
        off.append(off[-1] + len(ch.encode("utf-8")))
    n = len(text)
    for span in spans or ():
        try:
            s, e, kind = int(span[0]), int(span[1]), span[2]
        except (TypeError, ValueError, IndexError):
            continue
        s = max(0, min(s, n))
        e = max(0, min(e, n))
        if e <= s:
            continue
        if kind == "bold":
            attr = Pango.attr_weight_new(Pango.Weight.BOLD)
        elif kind == "italic":
            attr = Pango.attr_style_new(Pango.Style.ITALIC)
        elif kind == "highlight":
            attr = Pango.attr_background_new(*_HIGHLIGHT)
        else:
            continue
        attr.start_index = off[s]
        attr.end_index = off[e]
        al.insert(attr)
    return al


class PdfText:
    """A paginating text cursor over a cairo PDF surface, laid out with Pango.

    Replaces the per-app wrap()/emit() toy-font helpers. `y` is the live cursor
    (points from the top of the sheet); emit() wraps to `width`, breaks between
    LINES exactly where the old helpers did, and starts a new page when the next
    line would fall past `bottom`."""

    def __init__(self, surf, cr, left, top, bottom, width,
                 family=REPORT_FAMILY):
        self.surf = surf
        self.cr = cr
        self.left = left
        self.top = top
        self.bottom = bottom
        self.width = width
        self.family = family
        self.y = top

    def new_page(self):
        self.surf.show_page()
        self.y = self.top

    def _layout(self, text, size, bold, italic, spans, indent):
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Pango, PangoCairo
        lay = PangoCairo.create_layout(self.cr)
        # PDF user units ARE points, so pin Pango's resolution to 72dpi and a
        # Pango size of N points is N units tall — the same thing the old
        # cr.set_font_size(N) meant. At the default 96dpi every report would
        # have come out a third larger than it used to be.
        PangoCairo.context_set_resolution(lay.get_context(), 72.0)
        lay.set_width(int(max(1.0, self.width - indent) * Pango.SCALE))
        lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        fd = Pango.FontDescription()
        fd.set_family(self.family)
        fd.set_size(int(size * Pango.SCALE))
        if bold:
            fd.set_weight(Pango.Weight.BOLD)
        if italic:
            fd.set_style(Pango.Style.ITALIC)
        lay.set_font_description(fd)
        lay.set_text(text, -1)
        if spans:
            lay.set_attributes(_span_attrs(text, spans))
        return lay

    def emit(self, text, size, bold=False, color="#1A1916", italic=False,
             gap_before=0.0, gap_after=0.0, spans=None, indent=0.0):
        """Lay `text` out at the cursor and advance past it, paginating as
        needed. `spans` are inline (start_char, end_char, kind) runs."""
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import Pango, PangoCairo
        self.y += gap_before
        lead = size * 1.4
        if not text:
            if self.y + lead > self.bottom:
                self.new_page()
            self.y += lead + gap_after
            return
        lay = self._layout(text, size, bold, italic, spans, indent)
        r, g, b = _rgb_of(color)
        for i in range(lay.get_line_count()):
            line = lay.get_line_readonly(i)
            _ink, log = line.get_extents()
            lh = max(lead, log.height / float(Pango.SCALE))
            ascent = -log.y / float(Pango.SCALE)
            if self.y + lh > self.bottom:
                self.new_page()
            self.cr.save()
            self.cr.set_source_rgb(r, g, b)
            self.cr.move_to(self.left + indent, self.y + ascent)
            PangoCairo.show_layout_line(self.cr, line)
            self.cr.restore()
            self.y += lh
        self.y += gap_after

    def rule(self, color="#D7D2C5", right=None, gap_after=18.0):
        """A hairline across the text column, then a gap."""
        if right is None:
            right = self.left + self.width
        if self.y + 1 <= self.bottom:
            r, g, b = _rgb_of(color)
            self.cr.save()
            self.cr.set_source_rgb(r, g, b)
            self.cr.set_line_width(1.0)
            self.cr.move_to(self.left, self.y)
            self.cr.line_to(right, self.y)
            self.cr.stroke()
            self.cr.restore()
        self.y += gap_after


def report_page(path, width=LETTER_W_PT, height=LETTER_H_PT,
                margins=(72.0, 64.0, 64.0, 64.0), family=REPORT_FAMILY):
    """Open a US Letter report PDF and return (surface, context, PdfText).
    margins are (top, right, bottom, left) in points."""
    import cairo
    mt, mr, mb, ml = margins
    surf = cairo.PDFSurface(path, width, height)
    cr = cairo.Context(surf)
    text = PdfText(surf, cr, ml, mt, height - mb, width - ml - mr,
                   family=family)
    return surf, cr, text


# ---- themed Print dialog ---------------------------------------------------
_CSS = b"""
.nbprint { background: #FCFBF8; }
.nbprint * { font-family: "Nimbus Sans","Helvetica",sans-serif; color: #1A1916; }
.nbprint-h { font-family: "Newsreader","Liberation Serif","Georgia",serif;
             font-size: 20px; font-weight: 600; }
.nbprint-sub { font-size: 12px; color: #6E695E; }
.nbprint-note { font-size: 12px; color: #9A9484; }
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
.nbprint-primary:disabled { background: #C9C4B6; border-color: #C9C4B6; color: #FCFBF8; }
.nbprint-progress trough { min-height: 8px; background: #DED4C2; border: none; }
.nbprint-progress progress { min-height: 8px; background: #C8341E;
                             background-image: none; border: none; }
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


def _no_printer_body(win, box, extra_note):
    """The contents of the no-printer dialog, built into an existing box.

    Split out of _no_printer_dialog so the async dialog can put the very same
    widgets, in the same order, with the same words, into the window it already
    opened — the "no printer" answer now arrives after the window does, and it
    has to look like it always did."""
    h = Gtk.Label(label="No printer found", xalign=0)
    h.get_style_context().add_class("nbprint-h")
    box.pack_start(h, False, False, 0)
    msg = "Connect a USB printer and switch it on, then try again."
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


def _no_printer_dialog(parent, extra_note):
    win, box = _dialog(parent, "Print")
    _no_printer_body(win, box, extra_note)
    win.show_all()
    return win


NO_PRINTER_NOTE = "File ▸ Export to PDF saves this document as a file instead."
LOOKING_TEXT = "Looking for printers…"


PRINT_KEY = "print"


def _print_worker(job, make_pdf, printer, copies, options, job_name):
    """Render and hand off one complete file, entirely off the UI thread."""
    job.progress(0.0, "preparing")
    _RENDER_CONTEXT.job = job
    path = None
    try:
        job.checkpoint()
        path = make_print_file(make_pdf)
        job.checkpoint()
        job.progress(0.82, "sending")
        ok, message = submit_pdf(path, printer=printer, copies=copies,
                                 options=options, job_name=job_name)
        job.checkpoint()
        if not ok:
            raise RuntimeError(message)
        job.progress(1.0, "sent")
        return True
    finally:
        _RENDER_CONTEXT.job = None
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _print_failure(_error):
    return ("Printing stopped before the document was sent. Your document is "
            "safe. Check the printer, then try again.")


def _print_body(win, box, owner, printers, default, job_name, make_pdf, booklet,
                booklet_note, media):
    """The contents of the Print dialog once the printer list is known."""
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
    go = Gtk.Button(label="Print")
    go.get_style_context().add_class("nbprint-primary")
    row.pack_end(go, False, False, 0)
    row.pack_end(cancel, False, False, 0)
    box.pack_start(row, False, False, 0)

    status = Gtk.Label(xalign=0); status.set_line_wrap(True)
    status.set_max_width_chars(42)
    status.get_style_context().add_class("nbprint-note")
    box.pack_start(status, False, False, 0)

    progress = Gtk.ProgressBar()
    progress.set_fraction(0.0)
    progress.get_style_context().add_class("nbprint-progress")
    box.pack_start(progress, False, False, 0)
    progress.hide()

    scalar = nbmotion.Scalar(
        progress, 0.0, on_frame=progress.set_fraction,
        duration=nbmotion.SURFACE_IN, easing=nbmotion.LINEAR)

    def cancel_print(*_args):
        if owner.cancel(PRINT_KEY):
            status.set_text("Stopping printing…")
            cancel.set_sensitive(False)
        else:
            win.destroy()
        return True

    win._nbprint_cancel = cancel_print
    cancel.connect("clicked", cancel_print)

    def on_progress(fraction, phase):
        fraction = max(scalar.target, min(1.0, float(fraction or 0.0)))
        scalar.animate_to(fraction, easing=nbmotion.LINEAR)
        status.set_text("Sending to the printer…" if phase == "sending"
                        else "Preparing pages…")

    def on_done(_value):
        scalar.animate_to(1.0, easing=nbmotion.LINEAR,
                          on_done=lambda _finished: win.destroy())

    def on_error(error):
        status.set_text(_print_failure(error))
        progress.hide()
        go.set_sensitive(True)
        cancel.set_sensitive(True)

    def on_cancel():
        win.destroy()

    def on_print(_b):
        go.set_sensitive(False)
        cancel.set_sensitive(True)
        progress.show()
        status.set_text("Preparing pages…")
        pname = printers[combo.get_active() if combo.get_active() >= 0 else 0]["name"]
        copy_count = int(copies.get_value())
        # The page size the DOCUMENT was rendered at, not a hard-coded Letter.
        # Spooling an A4 PDF as Letter makes CUPS scale the whole page down to
        # fit, so every margin the writer set in Page Setup came out wrong.
        opts = {"media": _clean_media(media)}
        if booklet:
            # A booklet is always imposed 2-up on a letter sheet by
            # booklet_pdf(), whatever the document's own page size.
            opts["media"] = "Letter"
            # NO `landscape` here. booklet_pdf writes pages that are ALREADY
            # landscape — 792x612pt, the letter sheet turned on its side — so
            # the PDF states its own orientation and CUPS turns it to fit the
            # paper by itself. Asking for landscape on top of that rotated a
            # second time, which mapped the 11in imposed width onto the 8.5in
            # dimension and left CUPS to scale or crop the difference: text ran
            # off the edge of the sheet.
            if two_sided is not None and two_sided.get_active():
                # Short edge for a sheet folded down its middle: the back has to
                # be the mirror of the front about the fold, so page 2 lands
                # behind page 1. If a particular printer turns the paper the
                # other way, the give-away is the back sides reading in the
                # wrong order — everything else about the job is unaffected.
                opts["sides"] = "two-sided-short-edge"
        owner.start(
            PRINT_KEY,
            lambda job: _print_worker(job, make_pdf, pname,
                                      copy_count, opts, job_name),
            on_done=on_done, on_error=on_error, on_cancel=on_cancel,
            on_progress=on_progress, policy=nbjobs.REJECT)

    go.connect("clicked", on_print)


def _print_dialog(parent, job_name, make_pdf, booklet, booklet_note,
                  media=DEFAULT_MEDIA):
    """Core dialog. make_pdf(path) writes the document PDF to path.
    When booklet is True the copies row is joined by a two-sided toggle and the
    supplied booklet_note is shown. `media` is the CUPS page size the document
    was rendered at (see print_document).

    The window is returned before the printer list is known: discovery is four
    `lpstat` calls (see discover_printers_async) and used to happen right here,
    on the UI thread, before a single pixel of this dialog existed. Print showed
    nothing at all for as long as that took. Now the window opens saying what it
    is doing, and swaps in one of the two real bodies — the printer form, or the
    unchanged "No printer found" panel — when the answer lands.

    The job belongs to THIS window: the "destroy" handler closes the owner, so a
    discovery still running when the dialog is closed delivers nothing, and a
    dialog opened again afterwards is a different owner that the old answer has
    no way to reach.

    Rendering and sending run under the same window-owned background job.
    """
    win, box = _dialog(parent, "Print")
    owner = nbjobs.JobOwner(name="nbprint")
    _OPEN_OWNERS.add(owner)

    def _shut(*_a):
        owner.close()
        _OPEN_OWNERS.discard(owner)

    win.connect("destroy", _shut)

    def _escape(_window, event):
        if getattr(event, "keyval", None) == gi.repository.Gdk.KEY_Escape:
            action = getattr(win, "_nbprint_cancel", None)
            if action is not None:
                action()
            else:
                win.destroy()
            return True
        return False

    win.connect("key-press-event", _escape)

    # -- what the window says while it looks --------------------------------
    h = Gtk.Label(label="Print", xalign=0)
    h.get_style_context().add_class("nbprint-h")
    box.pack_start(h, False, False, 0)
    looking = Gtk.Label(label=LOOKING_TEXT, xalign=0)
    looking.get_style_context().add_class("nbprint-sub")
    box.pack_start(looking, False, False, 0)
    wait_row = Gtk.Box(spacing=10)
    wait_row.set_halign(Gtk.Align.END)
    wait_row.set_margin_top(10)
    wait_cancel = Gtk.Button(label="Cancel")
    wait_cancel.get_style_context().add_class("nbprint-btn")
    def _wait_cancel(*_args):
        win.destroy()
        return True
    win._nbprint_cancel = _wait_cancel
    wait_cancel.connect("clicked", _wait_cancel)
    wait_row.pack_end(wait_cancel, False, False, 0)
    box.pack_start(wait_row, False, False, 0)

    def _ready(printers, default):
        for child in box.get_children():
            box.remove(child)
            child.destroy()
        if printers:
            _print_body(win, box, owner, printers, default, job_name, make_pdf,
                        booklet, booklet_note, media)
        else:
            _no_printer_body(win, box, NO_PRINTER_NOTE)
        win.show_all()

    discover_printers_async(owner, _ready)
    win.show_all()
    return win


# Every dialog owner that is currently open. Only bookkeeping — the window's
# "destroy" handler is what closes an owner — but it makes a leaked dialog
# visible to the selftests instead of invisible.
_OPEN_OWNERS = set()


def print_document(parent, make_pdf, job_name="Document",
                   media=DEFAULT_MEDIA):
    """Show a Print dialog for a normal document. make_pdf(path) must write a
    PDF of the document to path. Handles the no-printer case gracefully.

    `media` is the CUPS page size the PDF is actually drawn at — "Letter",
    "Legal", "A4", "Statement" (5.5x8.5). Apps that let the user choose a page
    size MUST pass it, or CUPS scales their pages to fit letter paper."""
    return _print_dialog(parent, job_name, make_pdf, booklet=False,
                         booklet_note=None, media=media)


def print_booklet(parent, make_pdf, job_name="Booklet"):
    """Show a Print dialog for a zine/booklet. make_pdf(path) must write the
    already-imposed booklet PDF (see booklet_pdf) to path."""
    note = ("Sheets print two-up on letter paper. Stack, fold down the middle, "
            "staple the spine.")
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
