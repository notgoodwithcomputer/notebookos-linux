#!/usr/bin/env python3
"""
Accounting — a cash ledger for Notebook OS (native GTK).

A single-account cash book: each entry is a debit (money out) or a credit
(money in), and the ledger keeps a cent-accurate running balance. The left
panel shows the current balance and period totals; the right panel shows a
cairo-drawn balance-over-time chart above the transaction table and an inline
entry form.

The ledger ships EMPTY (balance 0) — no seeded transactions. Entries typed into
the form are appended live with a running balance and autosaved to a config
file so the working ledger survives close/reboot. Click any posted row to edit
it in place, or delete it after a confirmation (the Delete key on a focused row
opens the same confirm — deletion is destructive and has no undo); balances, the
chart and the autosave all recompute afterwards. That config file is the sole
source of truth; the File menu adds an entry, exports the ledger to a PDF under
$NB_HOME/Documents, and closes — it does no file open/save management.
"""
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo  # noqa: E402

import os
import csv
import json
import time
import math

import cairo

import nbapp
import nbicons
import nbprint
from nbi18n import _t  # noqa: E402

# Auto-persist — the working ledger is flushed to
# $NB_HOME/.config/notebook/accounting.json after every committed entry (and on
# window close) so nothing is lost across close/reboot. This file is the sole
# source of truth for the ledger. Export to PDF writes a separate, one-way
# rendering under $NB_HOME/Documents; it never becomes a reopened data file.
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
CFG_DIR = os.path.join(HOME, ".config", "notebook")
TX_FILE = os.path.join(CFG_DIR, "accounting.json")
DOCS_DIR = os.path.join(HOME, "Documents")

# Sign convention (bank-statement view): credit = money in (amt > 0),
# debit = money out (amt < 0). The running balance is opening + Σ amt.
MINUS = "−"

INK = "#1A1916"
MUTED = "#8A857A"
GRID = "#D7D2C5"
BG = "#FCFBF8"
CREDIT_C = "#4F6B45"
DEBIT_C = "#A23B2B"


# ---------------------------------------------------------------- drawn text
# Every string this file paints goes through Pango, never cairo's "toy" font
# API (cr.select_font_face + cr.show_text).
#
# THE BUG THIS EXISTS FOR: the toy API binds ONE FreeType face and does no
# per-character fallback, so it draws .notdef boxes for anything that face does
# not carry. Nimbus Sans carries no CJK, no Devanagari and no Hebrew — so an
# exported or printed ledger whose descriptions were typed in Japanese,
# Chinese, Korean, Hindi or Yiddish came out as a page of empty boxes, and the
# column-fitting in fit() MEASURED those boxes, so the truncation was wrong as
# well. tofu_sweep.py cannot see this class: it asks whether some shipped face
# has the glyph, which was true all along and is not the question show_text
# answers. Sizes are set with set_absolute_size, which is device units, so the
# same helper is correct on screen (px) and on a PDF surface (points).

def _layout(cr, text, size, bold=False, family="Nimbus Sans"):
    lay = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription(family)
    fd.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    fd.set_absolute_size(size * Pango.SCALE)
    lay.set_font_description(fd)
    lay.set_text(text, -1)
    return lay


def _text_w(cr, text, size, bold=False):
    """The drawn width of `text`, for right-alignment and truncation."""
    return _layout(cr, text, size, bold).get_pixel_size()[0]


def _show_text(cr, x, y, text, size, bold=False):
    """Draw `text` with its BASELINE at y — the anchor cr.show_text used, so
    every call site keeps the geometry it was tuned with."""
    lay = _layout(cr, text, size, bold)
    cr.move_to(x, y - lay.get_baseline() / Pango.SCALE)
    PangoCairo.show_layout(cr, lay)


def _cents(v):
    """Snap a money value to whole cents — the ONE rule that makes this ledger
    add up.

    Every figure on screen is rendered to two places, but the running balance
    was accumulated a rounded step at a time (round(bal + amt, 2) per row) while
    the headline total rounded the raw sum once at the end. Feed those two
    sub-cent amounts and they disagree: seven entries of 0.005 showed a final
    running balance of -$0.01 against a BALANCE of -$0.04 — the same ledger,
    two different answers, on a screen whose whole job is to be right about
    money. Quantising at the door (typed, imported and loaded amounts alike)
    makes every stored amount an exact number of cents, and the two totals then
    agree by construction at any ledger size."""
    try:
        return round(float(v), 2)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _recovered_note(n):
    """"We got your history back" — two COMPLETE sentences chosen by the count,
    not "%d entr%s". No English suffix turns "entry" into "entries", and even
    where one would, a fragment glued to a noun is not how Russian, Polish or
    Serbian form a plural. At one salvaged entry the single-sentence version
    used to read "Recovered 1 entries from a damaged ledger file"."""
    return (_t("Recovered 1 entry from a damaged ledger file") if n == 1
            else _t("Recovered %d entries from a damaged ledger file") % n)


def _salvage_tx(text):
    """Recover every complete transaction object from a DAMAGED ledger file.

    The ledger is one json.dump line, so the realistic damage modes — a write
    cut short by failing media, a file half-copied off a USB stick — leave a
    document json.load rejects outright. Giving up there loses the whole
    history to one bad byte, when almost all of it is still sitting in the file
    perfectly intact. So walk the raw text instead and cut out every balanced
    {...} run, string-aware so a description containing a brace or a quote
    cannot confuse the scan, and json.loads each one on its own. Entries that
    parse are returned in file order; only the damaged part is lost."""
    out = []
    stack = []
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            start = stack.pop()
            try:
                obj = json.loads(text[start:i + 1])
            except ValueError:
                continue
            # the whole-file wrapper {"tx": [...], "opening": 0} has no "amt",
            # so only real transactions are collected
            if isinstance(obj, dict) and "amt" in obj:
                out.append(obj)
    return out


class Accounting(nbapp.AppWindow):
    app_name = "Accounting"
    menus = ("File", "Edit", "View", "Reports")

    def __init__(self):
        super().__init__()
        self._install_css()

        # Restore the working ledger from the auto-persist config file. On first
        # run (no file) the ledger opens EMPTY — no seeded transactions.
        state = self._load_state()
        self.tx = state["tx"]
        self.opening = state["opening"]
        self._damaged = state["damaged"]
        self._quarantine_pending = state["quarantine"]
        self.fdir = "debit"           # entry form direction: "debit" / "credit"
        self.filter = ""              # raw FIND query ("" = show all)
        self._terms = ()              # its lower-cased words, ANDed
        self._search_timer = 0        # pending search debounce (0 = none)
        self._shown = self._PAGE      # rows currently built (see _refresh)
        # Cached (W, H, ImageSurface) for the balance chart. The GPU-less
        # hardware framebuffer repaints every expose in software, so the vector
        # + text render is done once per size/data change and just blitted on
        # incidental repaints. Invalidated in _refresh whenever the data moves.
        self._chart_cache = None

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        body.set_hexpand(True)
        body.set_vexpand(True)
        self.content.pack_start(body, True, True, 0)

        body.pack_start(self._sidebar(), False, False, 0)
        body.pack_start(self._ledger(), True, True, 0)

        self._refresh()
        # A ledger that came back damaged has to SAY so. Everything else on
        # screen (a $0.00 balance, "No entries") would otherwise read as the
        # honest truth about the user's money.
        if state["note"]:
            self._flash(state["note"], kind="error")

        # Final flush: File->Close / Esc / logo all route through
        # Gtk.Window.close -> "destroy", which re-saves the working ledger.
        self.connect("destroy", self._on_destroy)

    # ------------------------------------------------------------- persistence
    @staticmethod
    def _num(v, default):
        """Coerce a JSON scalar to a FINITE float; bool, junk and non-finite
        values (NaN / Infinity, which json.load will happily produce) all fall
        back to `default` so garbage on disk can never poison the ledger."""
        if isinstance(v, bool):
            return default
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        return f if math.isfinite(f) else default

    def _parse_tx(self, raw):
        """Validate a raw transaction list to the {date, desc, amt} shape."""
        out = []
        if not isinstance(raw, list):
            return out
        for t in raw:
            if not isinstance(t, dict) or "amt" not in t:
                # No amount AT ALL is not a zero-amount entry: defaulting it to
                # 0 turned any stray object in the file into a phantom "$0.00"
                # row with a blank description sitting in the user's ledger.
                continue
            amt = self._num(t["amt"], None)
            if amt is None:
                continue
            out.append({"date": str(t.get("date", "")),
                        "desc": str(t.get("desc", "")),
                        "amt": _cents(amt)})
        return out

    @staticmethod
    def _quarantine():
        """Move a ledger file this app could not make sense of aside, under the
        same <name>.damaged-<timestamp> name nbapp.preserve_damaged uses.

        nbapp quarantines any store that fails to PARSE, which covers a
        truncated or corrupted file and is done for us on every write. It
        deliberately cannot cover the case below: valid JSON of the wrong shape
        parses perfectly, and only this app knows that the shape is not a
        ledger. Without this the next autosave would write an empty book
        straight over whatever the file really held."""
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dest = "%s.damaged-%s" % (TX_FILE, stamp)
            n = 2
            while os.path.exists(dest):
                dest = "%s.damaged-%s-%d" % (TX_FILE, stamp, n)
                n += 1
            os.replace(TX_FILE, dest)
        except OSError:
            pass

    def _load_state(self):
        """Return the recovered working ledger, or an empty one on first run.

        A file that will not parse is NOT written off: every intact entry still
        in it is salvaged (see _salvage_tx), the original bytes are kept, and
        `note` carries a plain-language account of what happened for the status
        line — because a ledger that quietly reopens empty, and then autosaves
        that emptiness back over the only copy, is the one failure this app can
        never have. Never seeds sample data."""
        st = {"tx": [], "opening": 0.0, "note": "", "damaged": False,
              "quarantine": False}
        if not os.path.exists(TX_FILE):
            return st
        try:
            with open(TX_FILE, encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            text = ""
        try:
            data = json.loads(text)
        except Exception:
            # Unreadable. nbapp.atomic_write_json quarantines it before the next
            # save, so those bytes survive; salvage every entry still intact in
            # them now, so the user gets their history back and not a blank book.
            st["damaged"] = True
            st["tx"] = self._parse_tx(_salvage_tx(text))
            n = len(st["tx"])
            st["note"] = _recovered_note(n) if n else \
                _t("The ledger file could not be read. A new ledger was started.")
            return st
        raw = data.get("tx") if isinstance(data, dict) else data
        # Transactions stored as an object keyed by id rather than a list. The
        # values are still the user's ledger lines, so read them in file order
        # instead of declaring the whole book unreadable — a wrapper of the
        # wrong type must not cost somebody their year of figures.
        if isinstance(raw, dict):
            raw = list(raw.values())
        st["tx"] = self._parse_tx(raw)
        if isinstance(data, dict):
            st["opening"] = self._num(data.get("opening", 0.0), 0.0)

        # PARSED, BUT NOT A LEDGER. Valid JSON of the wrong shape ({"tx": "..."},
        # a bare number, some other app's file) reads as an empty ledger, and
        # the generic quarantine cannot see it because the file parses
        # perfectly — so left alone the next autosave writes an empty book over
        # whatever this really was. A file that IS a ledger and simply has no
        # entries yet ("tx": []) is a normal, legitimate state a user reaches by
        # deleting their last entry, so the test is the SHAPE, never emptiness.
        if not isinstance(raw, list) or (raw and not st["tx"]):
            st["damaged"] = True
            st["quarantine"] = True
            st["tx"] = []
            st["opening"] = 0.0
            st["note"] = _t(
                "The ledger file could not be read. A new ledger was started.")
        elif len(st["tx"]) < len(raw):
            # A ledger, but some of its entries were not: keep the good ones,
            # keep the file that still holds the bad ones, and say how many
            # survived rather than quietly showing a short ledger as complete.
            st["quarantine"] = True
            st["note"] = _recovered_note(len(st["tx"]))
        return st

    def _autosave(self):
        """Flush the working ledger through the shared crash-safe writer (temp
        file + fsync + atomic rename + directory fsync, and quarantine of any
        store it could not read). Swallows I/O errors — a bad write must never
        crash a ledger."""
        try:
            # A file that PARSED but was not a ledger gets moved aside here,
            # immediately before the write that would otherwise replace it —
            # the same moment nbapp picks for the files it can detect, so there
            # is never a window in which the ledger has no file at all.
            if self._quarantine_pending:
                self._quarantine()
                self._quarantine_pending = False
            nbapp.atomic_write_json(TX_FILE, {"tx": self.tx,
                                              "opening": self.opening})
            self._save_warned = False
        except Exception as exc:
            # See academics._save_to_disk. A silently failed write is the worst
            # thing a ledger can do: the app carries on showing a balance that
            # is not on disk anywhere, and every entry made after the disk
            # filled up is gone the moment it closes. Warn once per run.
            if not getattr(self, "_save_warned", False):
                self._save_warned = True
                try:
                    self._flash(nbapp.save_failure_reason(exc, TX_FILE))
                except Exception:
                    pass

    def _on_destroy(self, *_):
        if self._search_timer:
            GLib.source_remove(self._search_timer)
            self._search_timer = 0
        self._autosave()
        return False

    # ------------------------------------------------------------- status line
    def _flash(self, text, kind="info"):
        """Surface a status line (export result / confirmation / error) in the
        sidebar status label. `kind="error"` paints it in the signage-red alert
        colour (reserved for problems); anything else uses the muted ink. The
        next committed action overwrites it. Crash-safe: a bad label update must
        never crash a ledger, and the text is escaped so a stray & / < can't
        break the markup."""
        color = "#C8341E" if kind == "error" else MUTED
        try:
            safe = GLib.markup_escape_text(text)
            self.status_lbl.set_markup(
                '<span foreground="%s">%s</span>' % (color, safe))
            self.status_lbl.set_visible(bool(text))
        except Exception:
            pass

    @staticmethod
    def _missing_msg(desc, amt_n, raw=""):
        """Precise, novice-friendly prompt naming exactly what an entry is
        missing — so a blocked commit says why instead of silently doing
        nothing."""
        if not desc and amt_n is None:
            return _t("Enter a description and an amount")
        if not desc:
            return _t("Enter a description")
        if Accounting._tiny_amount(raw):
            return _t("Enter an amount of at least $0.01")
        return _t("Enter an amount")

    # ------------------------------------------------------------------ export
    def _pdf_name(self):
        """A sensible, date-stamped file name for the exported ledger PDF."""
        return "ledger-%s.pdf" % time.strftime("%Y-%m-%d")

    def _export_pdf(self, *_a):
        """Render the current ledger to a PDF under $NB_HOME/Documents.

        A one-way export: the period summary, the balance-over-time chart, and
        the full transaction table (date / description / debit / credit /
        running balance) are drawn onto a US-Letter cairo surface with
        pagination. Reports a neutral status line; never crashes on a bad path
        or write."""
        name = self._pdf_name()
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            self._render_pdf(os.path.join(DOCS_DIR, name))
        except Exception:
            self._flash(_t("Export failed"), kind="error")
            return
        self._flash(_t("Exported %s to Documents") % name)

    def _export_csv(self, *_a):
        """Write the ledger to a spreadsheet file under $NB_HOME/Documents.

        The PDF export is a picture of the ledger: fine to read or print, no use
        at all if you want to add up a column, hand your figures to somebody, or
        keep a copy somewhere other than this machine. There is no network here,
        so a plain CSV on a USB stick IS the way the numbers leave the device —
        and CSV is the one format every spreadsheet on earth opens. Debit and
        credit stay in separate columns exactly as they read on screen, with the
        running balance beside them, so the export reconciles against the app."""
        name = "ledger-%s.csv" % time.strftime("%Y-%m-%d")
        try:
            os.makedirs(DOCS_DIR, exist_ok=True)
            # newline="" is the csv module's documented requirement; without it
            # every row is written with a stray blank line between it and the
            # next on some platforms.
            with open(os.path.join(DOCS_DIR, name), "w", newline="",
                      encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["Date", "Description", "Debit", "Credit",
                            "Balance"])
                bal = self.opening
                for t in self.tx:
                    bal = round(bal + t["amt"], 2)
                    amt = t["amt"]
                    # Bare numbers, no "$" and no thousands separators: a
                    # spreadsheet reads these as numbers it can add up, where
                    # "$1,234.56" arrives as text and every sum comes to zero.
                    w.writerow([t["date"], t["desc"],
                                "%.2f" % -amt if amt < 0 else "",
                                "%.2f" % amt if amt > 0 else "",
                                "%.2f" % bal])
        except Exception:
            self._flash(_t("Export failed"), kind="error")
            return
        self._flash(_t("Exported %s to Documents") % name)

    def _print(self, *_a):
        """Print the ledger through the shared themed Print dialog.

        make_pdf writes the SAME PDF as Export to PDF — the existing
        _render_pdf renderer — to a temp file nbprint hands to the spooler, so
        the printed and exported documents are byte-for-byte the same layout.
        nbprint owns the printer picker, the copies count and the no-printer
        case; a failure to even open the dialog is surfaced, never crashes."""
        try:
            nbprint.print_document(self, self._render_pdf, job_name="Report")
        except Exception:
            self._flash(_t("Print failed"), kind="error")

    def _render_pdf(self, path):
        """Draw the ledger onto a cairo PDF at `path`, paginating the table when
        rows overflow the page and re-drawing the column header on each page."""
        PW, PH = 612.0, 792.0            # US Letter, points
        ML, MR, MT, MB = 54.0, 54.0, 64.0, 56.0
        surf = cairo.PDFSurface(path, PW, PH)
        cr = cairo.Context(surf)

        def ink(hexc):
            r, g, b = nbicons._hex(hexc)
            cr.set_source_rgb(r, g, b)

        def text_at(x, y, s, size, bold=False, color=INK):
            ink(color)
            _show_text(cr, x, y, s, size, bold)

        def right_at(r_edge, y, s, size, bold=False, color=INK):
            ink(color)
            _show_text(cr, r_edge - _text_w(cr, s, size, bold), y, s, size, bold)

        def fit(s, size, maxw, bold=False):
            if _text_w(cr, s, size, bold) <= maxw:
                return s
            # ASCII "..." rather than U+2026 stays, but for a different reason
            # now: it is the ellipsis every other export in this OS uses, and
            # Pango would render either. It is no longer a fallback guard --
            # _show_text picks a face per glyph.
            while s and _text_w(cr, s + "...", size, bold) > maxw:
                s = s[:-1]
            return s + "..."

        # Column geometry: date + flexible description on the left, three
        # right-aligned money columns on the right. COL_W is the money column
        # PITCH, so a column's figures start at (its right edge - COL_W): the
        # description has to stop clear of that left edge, not of the right one,
        # or a long description runs straight into the figure
        # ("…winter quarte$216.40"). Each row keeps its own limit, so a row with
        # no debit gets the debit column's width back for its description.
        COL_W = 86
        date_x = ML
        desc_x = ML + 58
        bal_r = PW - MR
        cred_r = bal_r - COL_W
        deb_r = cred_r - COL_W

        def desc_room(amt):
            first = deb_r if amt < 0 else (cred_r if amt > 0 else bal_r)
            return first - COL_W - 12 - desc_x

        total = round(self.opening + sum(t["amt"] for t in self.tx), 2)
        credit = round(sum(t["amt"] for t in self.tx if t["amt"] > 0), 2)
        debit = round(-sum(t["amt"] for t in self.tx if t["amt"] < 0), 2)

        # Header: eyebrow, closing balance, one-line summary, hairline rule.
        y = MT
        text_at(ML, y, "CASH LEDGER", 9.5, False, "#6E695E")
        y += 22
        text_at(ML, y, "Balance " + self._cmoney(total), 22, True, INK)
        y += 18
        text_at(ML, y, "Credit +%s     Debit -%s     Entries %d" % (
            self._cmoney(credit), self._cmoney(debit), len(self.tx)),
            10, False, MUTED)
        y += 14
        ink("#ECE7DA")
        cr.set_line_width(1.0)
        cr.move_to(ML, y)
        cr.line_to(PW - MR, y)
        cr.stroke()
        y += 22

        # Balance-over-time chart (only meaningful with two or more points).
        vals = self._balance_series()
        if len(vals) >= 2:
            self._render_chart(cr, ML, y, PW - MR - ML, 120.0, vals)
            y += 120.0 + 26

        # Table header, repeated at the top of each page.
        def table_header(yy):
            text_at(date_x, yy, "DATE", 9, True, MUTED)
            text_at(desc_x, yy, "DESCRIPTION", 9, True, MUTED)
            right_at(deb_r, yy, "DEBIT", 9, True, MUTED)
            right_at(cred_r, yy, "CREDIT", 9, True, MUTED)
            right_at(bal_r, yy, "BALANCE", 9, True, MUTED)
            rule = yy + 6
            ink(INK)
            cr.set_line_width(0.8)
            cr.move_to(ML, rule)
            cr.line_to(PW - MR, rule)
            cr.stroke()
            return rule + 16

        y = table_header(y)

        # Rows, oldest-first so the running balance builds down the page.
        row_h = 17.0
        bal = self.opening
        for t in self.tx:
            bal = round(bal + t["amt"], 2)
            if y + row_h > PH - MB:
                surf.show_page()
                y = table_header(MT)
            amt = t["amt"]
            text_at(date_x, y, str(t["date"]), 9.5, False, MUTED)
            text_at(desc_x, y, fit(str(t["desc"]), 10, desc_room(amt)), 10,
                    False, INK)
            if amt < 0:
                right_at(deb_r, y, self._cmoney(-amt), 10, False, DEBIT_C)
            elif amt > 0:
                right_at(cred_r, y, "+" + self._cmoney(amt), 10, False, CREDIT_C)
            right_at(bal_r, y, self._cmoney(bal), 10, False, "#3A362E")
            ink("#F0ECE0")
            cr.set_line_width(0.6)
            cr.move_to(ML, y + 5)
            cr.line_to(PW - MR, y + 5)
            cr.stroke()
            y += row_h

        if not self.tx:
            text_at(date_x, y, "No entries.", 10, False, MUTED)
            y += row_h

        # Closing-balance footer.
        if y + 30 > PH - MB:
            surf.show_page()
            y = MT
        y += 6
        ink(INK)
        cr.set_line_width(0.8)
        cr.move_to(ML, y)
        cr.line_to(PW - MR, y)
        cr.stroke()
        y += 16
        text_at(desc_x, y, "Closing balance", 10.5, True, INK)
        right_at(bal_r, y, self._cmoney(total), 10.5, True, INK)

        surf.finish()

    def _render_chart(self, cr, ox, oy, w, h, vals):
        """Draw the running-balance line chart into the rect (ox, oy, w, h) on
        the PDF context — a print rendering of the on-screen balance chart."""
        cr.set_source_rgb(*nbicons._hex(BG))
        cr.rectangle(ox, oy, w, h)
        cr.fill()
        cr.set_source_rgb(*nbicons._hex(GRID))
        cr.set_line_width(0.8)
        cr.rectangle(ox, oy, w, h)
        cr.stroke()

        pad_l, pad_r, pad_t, pad_b = 12, 70, 12, 16
        x0, x1 = ox + pad_l, ox + w - pad_r
        y0, y1 = oy + pad_t, oy + h - pad_b
        vmin = min(vals + [0.0])
        vmax = max(vals + [0.0])
        if vmax == vmin:
            vmax = vmin + 1.0
        n = len(vals)

        def sx(i):
            return (x0 + x1) / 2 if n == 1 else x0 + (x1 - x0) * i / (n - 1)

        def sy(v):
            return y1 - (y1 - y0) * (v - vmin) / (vmax - vmin)

        # zero baseline, when 0 is within range
        if vmin <= 0 <= vmax:
            yz = sy(0)
            cr.set_line_width(0.8)
            cr.set_dash([3, 3])
            cr.set_source_rgb(*nbicons._hex(GRID))
            cr.move_to(x0, yz)
            cr.line_to(x1, yz)
            cr.stroke()
            cr.set_dash([])

        # subtle area fill under the line
        cr.move_to(sx(0), y1)
        for i, v in enumerate(vals):
            cr.line_to(sx(i), sy(v))
        cr.line_to(sx(n - 1), y1)
        cr.close_path()
        r, g, b = nbicons._hex(INK)
        cr.set_source_rgba(r, g, b, 0.06)
        cr.fill()

        # the balance line
        cr.set_line_width(1.6)
        cr.set_source_rgb(*nbicons._hex(INK))
        for i, v in enumerate(vals):
            (cr.move_to if i == 0 else cr.line_to)(sx(i), sy(v))
        cr.stroke()

        # end marker
        cr.arc(sx(n - 1), sy(vals[-1]), 2.5, 0, 6.2832)
        cr.fill()

        # min / max value labels on the right gutter
        cr.set_source_rgb(*nbicons._hex(MUTED))
        _show_text(cr, x1 + 6, sy(vmax) + 3, self._cmoney(vmax), 9)
        _show_text(cr, x1 + 6, sy(vmin) + 3, self._cmoney(vmin), 9)

    # ---------------------------------------------------------------- sidebar
    def _sidebar(self):
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side.get_style_context().add_class("sidebar")
        side.set_size_request(312, -1)

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head.get_style_context().add_class("sidehead")
        cap = Gtk.Label(label=_t("BALANCE"), xalign=0)
        cap.get_style_context().add_class("caption")
        head.pack_start(cap, False, False, 0)
        self.balance = Gtk.Label(label="$0.00", xalign=0)
        self.balance.get_style_context().add_class("balance")
        head.pack_start(self.balance, False, False, 0)
        side.pack_start(head, False, False, 0)

        stats = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        stats.get_style_context().add_class("statlist")
        self.credit_lbl = self._stat(stats, "CREDIT", "credit")
        self.debit_lbl = self._stat(stats, "DEBIT", "debit")
        self.count_lbl = self._stat(stats, "ENTRIES", None)
        side.pack_start(stats, False, False, 0)

        # FIND — "what did I spend on food?" had no answer in a ledger with no
        # way to look through it, and the lower half of this panel was dead
        # space. Typing here filters the table to matching entries and totals
        # them, beside the figures that answer the same kind of question for the
        # whole ledger. Dates are stored as "12 Mar", so a month name works too.
        find = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        find.get_style_context().add_class("findbox")
        fcap = Gtk.Label(label=_t("FIND"), xalign=0)
        fcap.get_style_context().add_class("caption")
        find.pack_start(fcap, False, False, 0)

        searchbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        searchbox.get_style_context().add_class("searchbox")
        icon = Gtk.Image.new_from_pixbuf(nbicons.pixbuf("search", 15, MUTED))
        icon.set_valign(Gtk.Align.CENTER)
        icon.set_margin_start(10)
        searchbox.pack_start(icon, False, False, 0)
        self.search = Gtk.Entry()
        self.search.set_has_frame(False)
        # Sized in CHARACTERS: a Gtk.Entry's own ~172px minimum would push the
        # whole sidebar wider than its 312px and steal that width from the
        # ledger's columns (the same trap the amount field documents).
        self.search.set_width_chars(8)
        self.search.set_placeholder_text(_t("Description or month"))
        self.search.get_style_context().add_class("searchentry")
        self.search.connect("changed", self._on_search)
        searchbox.pack_start(self.search, True, True, 0)
        find.pack_start(searchbox, False, False, 0)

        # the answer: how many entries matched, and what they come to. Hidden
        # entirely until something is being searched for.
        self.findsum = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.findsum.get_style_context().add_class("findsum")
        self.find_n = Gtk.Label(label="", xalign=0)
        self.find_n.get_style_context().add_class("findn")
        self.findsum.pack_start(self.find_n, True, True, 0)
        self.find_net = Gtk.Label(label="", xalign=1)
        self.find_net.get_style_context().add_class("findnet")
        self.findsum.pack_end(self.find_net, False, False, 0)
        # Show the two labels now and gate visibility on the BOX: show_all() is
        # a no-op on a widget carrying no_show_all, so a row hidden that way can
        # never be revealed by showing it (the status line above uses
        # set_visible for the same reason).
        self.find_n.show()
        self.find_net.show()
        self.findsum.set_no_show_all(True)
        find.pack_start(self.findsum, False, False, 0)
        side.pack_start(find, False, False, 0)

        # push the status line to the bottom of the panel. It carries a rule
        # above it, so it stays HIDDEN until there is something to say —
        # otherwise the panel ends in an empty ruled-off strip that reads as a
        # bar someone forgot to fill in.
        side.pack_start(Gtk.Box(), True, True, 0)
        self.status_lbl = Gtk.Label(label="", xalign=0)
        self.status_lbl.get_style_context().add_class("statusline")
        # WRAP, not ellipsize, and capped in characters. Ellipsizing still asks
        # for the whole string as the label's NATURAL width, so the longest
        # thing this line ever says ("The ledger file could not be read...")
        # widened the whole sidebar by 48px and stole that from the ledger's
        # columns — and then cut the sentence off before the part that matters.
        # Wrapped, the message reads in full and the panel never moves.
        self.status_lbl.set_line_wrap(True)
        self.status_lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.status_lbl.set_max_width_chars(30)
        self.status_lbl.set_no_show_all(True)
        side.pack_start(self.status_lbl, False, False, 0)
        return side

    def _stat(self, parent, caption, cls):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.get_style_context().add_class("statrow")
        c = Gtk.Label(label=caption, xalign=0)
        c.get_style_context().add_class("statcap")
        row.pack_start(c, True, True, 0)
        v = Gtk.Label(label="$0.00", xalign=1)
        v.get_style_context().add_class("statval")
        if cls:
            v.get_style_context().add_class(cls)
        row.pack_end(v, False, False, 0)
        parent.pack_start(row, False, False, 0)
        return v

    # ----------------------------------------------------------------- ledger
    def _ledger(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.get_style_context().add_class("ledger")

        # balance-over-time chart (cairo). Kept as an attribute so the View
        # menu can show / hide it.
        self.chartwrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.chartwrap.get_style_context().add_class("chartwrap")
        ccap = Gtk.Label(label=_t("BALANCE OVER TIME"), xalign=0)
        ccap.get_style_context().add_class("caption")
        self.chartwrap.pack_start(ccap, False, False, 0)
        self.chart = Gtk.DrawingArea()
        self.chart.set_size_request(-1, 156)
        self.chart.connect("draw", self._draw_chart)
        self.chartwrap.pack_start(self.chart, False, False, 0)
        col.pack_start(self.chartwrap, False, False, 0)

        # entry: a dashed "add" affordance that reveals the inline form
        addbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.addrow = Gtk.Button()
        self.addrow.set_relief(Gtk.ReliefStyle.NONE)
        self.addrow.get_style_context().add_class("addrow")
        self.addrow.connect("clicked", self._toggle_form)
        ar = Gtk.Box(spacing=10)
        ar.pack_start(Gtk.Image.new_from_pixbuf(
            nbicons.pixbuf("plus", 15, MUTED)), False, False, 0)
        ar.pack_start(Gtk.Label(label=_t("Add entry"), xalign=0), False, False, 0)
        self.addrow.add(ar)
        addbox.pack_start(self.addrow, False, False, 0)

        # Revealer with NONE transition — SLIDE stalls the swrast path.
        self.form_reveal = Gtk.Revealer()
        self.form_reveal.set_transition_type(Gtk.RevealerTransitionType.NONE)
        self.form_reveal.add(self._form())
        self.form_reveal.set_reveal_child(False)
        addbox.pack_start(self.form_reveal, False, False, 0)
        col.pack_start(addbox, False, False, 0)

        # header
        header = self._grid_row(
            ("DATE", "DESCRIPTION", "DEBIT", "CREDIT", "BALANCE"),
            "colhead", (0, 0, 1, 1, 1))
        header.get_style_context().add_class("ledgerhead")
        self._ledgerhead = header
        col.pack_start(header, False, False, 0)

        # rows
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("rowscroll")
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.rows.get_style_context().add_class("rows")
        self.empty = Gtk.Label(label=_t("No entries. Add one above."))
        self.empty.get_style_context().add_class("emptystate")
        self.rows.pack_start(self.empty, False, False, 0)
        scroll.add(self.rows)
        col.pack_start(scroll, True, True, 0)

        # The rows scroll and the column header does not, so the moment the
        # ledger is long enough to need a scrollbar the rows lose its width and
        # every money column slides ~13px left of the header it is supposed to
        # sit under. Reserve the same width in the header while the scrollbar is
        # showing, so header and figures line up whether or not it is.
        self._rowscroll = scroll
        vsb = scroll.get_vscrollbar()
        if vsb is not None:
            vsb.connect("notify::visible", lambda *_: self._sync_head_gutter())
        scroll.connect("size-allocate", lambda *_: self._sync_head_gutter())
        return col

    _hdr_gutter = 0

    def _sync_head_gutter(self):
        """Match the header's right gutter to the rows' scrollbar. A no-op
        unless the width really changed, so calling it from size-allocate can
        never loop; crash-safe, because a gutter is not worth a ledger."""
        try:
            vsb = self._rowscroll.get_vscrollbar()
            w = (vsb.get_allocated_width()
                 if (vsb is not None and vsb.get_visible()) else 0)
            if w != self._hdr_gutter:
                self._hdr_gutter = w
                self._ledgerhead.set_margin_end(w)
        except Exception:
            pass

    # Ledger rows built per page. Every row is a live widget, so the cost of a
    # rebuild is linear in this number (~1 ms/row on the host); 150 is far more
    # than anyone scrolls, and keeps adding an entry to a five-year ledger as
    # quick as adding one to an empty ledger. See _refresh.
    _PAGE = 150

    # Column pitch: date, elastic description, then the three money columns.
    # BALANCE is the widest because it carries the running total, which is by
    # definition the largest figure on the row; the 12px it gains over the old
    # geometry comes out of DATE (which only ever holds "12 Jul"), so the
    # description keeps every pixel it had.
    _GRID = (80, -1, 118, 118, 140)
    # The gutter kept clear at the LEFT of every money cell. set_size_request is
    # a minimum, not a cap, so a figure wider than its column (a seven-figure
    # credit, "+$12,345,678.90") grows its cell rather than being truncated —
    # and with no reserved gutter it grew straight into the figure beside it, so
    # two amounts read as one number ("…678.90$12,351,285.36"). The gutter is a
    # margin kept INSIDE the pitch above, so every ordinary amount lands on
    # exactly the right edge the header column sets, and only a figure too wide
    # for its own column shifts — by the little it overflows, still gutter-clear
    # of its neighbour, and identically on every row so the columns still read
    # as columns.
    _MONEY_GUTTER = 14

    def _money_cell(self, w, i):
        """Size a right-aligned money cell in column `i`: the column pitch, less
        a gutter its figures can never spill into."""
        w.set_margin_start(self._MONEY_GUTTER)
        w.set_size_request(self._GRID[i] - self._MONEY_GUTTER, -1)

    def _grid_row(self, cells, cls, aligns):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.get_style_context().add_class(cls)
        for i, text in enumerate(cells):
            w = Gtk.Label(label=text)
            w.set_xalign(1.0 if aligns[i] else 0.0)
            if aligns[i]:                 # the money columns — see _money_cell
                self._money_cell(w, i)
            else:
                w.set_size_request(self._GRID[i], -1)
            if self._GRID[i] == -1:
                box.pack_start(w, True, True, 0)
            else:
                box.pack_start(w, False, False, 0)
        return box

    def _form(self):
        # The row of controls, plus an inline validation line under it.
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        form = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        form.get_style_context().add_class("entryform")

        # Kept as an attribute and refreshed each time the form opens, so a
        # window left open past midnight still stamps the new entry with today.
        self.fdate = Gtk.Label(label=time.strftime("%-d %b"))
        self.fdate.get_style_context().add_class("fdate")
        self.fdate.set_tooltip_text(_t("Date of a new entry"))
        form.pack_start(self.fdate, False, False, 0)

        self.f_desc = Gtk.Entry()
        self.f_desc.set_placeholder_text(_t("Description"))
        self.f_desc.get_style_context().add_class("finput")
        self.f_desc.connect("activate", self._on_add)
        form.pack_start(self.f_desc, True, True, 0)

        self.f_amt = Gtk.Entry()
        self.f_amt.set_placeholder_text("0.00")
        self.f_amt.set_alignment(1.0)
        # Sized in CHARACTERS, not pixels: a Gtk.Entry's own minimum (~172px)
        # ignores a smaller set_size_request, and with the date, description,
        # Debit/Credit and Add controls beside it that pushed the whole window
        # past a 1024-wide panel — the Add button would sit off the screen
        # exactly when someone is adding an entry. Ten characters holds
        # "1,234,567.89" comfortably.
        self.f_amt.set_width_chars(10)
        self.f_amt.get_style_context().add_class("finput")
        self.f_amt.connect("activate", self._on_add)
        form.pack_start(self.f_amt, False, False, 0)

        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        seg.get_style_context().add_class("segbox")
        self.btn_debit = Gtk.Button(label=_t("Debit"))
        self.btn_debit.set_relief(Gtk.ReliefStyle.NONE)
        self.btn_debit.get_style_context().add_class("seg")
        self.btn_debit.get_style_context().add_class("segon")
        self.btn_debit.set_tooltip_text(_t("Money out of the account"))
        self.btn_debit.connect("clicked", lambda *_: self._set_dir("debit"))
        self.btn_credit = Gtk.Button(label=_t("Credit"))
        self.btn_credit.set_relief(Gtk.ReliefStyle.NONE)
        self.btn_credit.get_style_context().add_class("seg")
        self.btn_credit.set_tooltip_text(_t("Money into the account"))
        self.btn_credit.connect("clicked", lambda *_: self._set_dir("credit"))
        seg.pack_start(self.btn_debit, False, False, 0)
        seg.pack_start(self.btn_credit, False, False, 0)
        form.pack_start(seg, False, False, 0)

        add = Gtk.Button(label=_t("Add"))
        add.set_relief(Gtk.ReliefStyle.NONE)
        add.get_style_context().add_class("addbtn")
        add.connect("clicked", self._on_add)
        form.pack_start(add, False, False, 0)
        wrap.pack_start(form, False, False, 0)

        # Inline validation line, directly under the fields it is about (the
        # same reasoning as the row editor's _e_err): the sidebar status line is
        # at the far bottom-left of the window, so a blocked Add explained only
        # there reads as a button that did nothing. Hidden until needed.
        self._f_err = Gtk.Label(label="", xalign=0)
        self._f_err.get_style_context().add_class("formerr")
        self._f_err.set_no_show_all(True)
        wrap.pack_start(self._f_err, False, False, 0)
        return wrap

    # ------------------------------------------------------------- behaviour
    def _toggle_form(self, *_):
        want = not self.form_reveal.get_reveal_child()
        self.form_reveal.set_reveal_child(want)
        self._form_error("")        # a fresh form starts without a complaint
        if want:
            self._stamp_today()
            self.f_desc.grab_focus()

    def _reveal_form(self):
        """Reveal the inline entry form and focus the description field."""
        try:
            self._stamp_today()
            self._form_error("")
            self.form_reveal.set_reveal_child(True)
            self.f_desc.grab_focus()
        except Exception:
            pass

    def _form_error(self, text):
        """Show (or clear) the entry form's inline validation line. Crash-safe:
        a label update must never take down a ledger."""
        try:
            if text:
                self._f_err.set_text(text)
                self._f_err.show()
            else:
                self._f_err.hide()
        except Exception:
            pass

    def _stamp_today(self):
        """Refresh the form's date label to today so a long-open window never
        stamps a new entry with a stale day."""
        try:
            self.fdate.set_text(time.strftime("%-d %b"))
        except Exception:
            pass

    def _set_dir(self, d):
        self.fdir = d
        for b, name in ((self.btn_debit, "debit"), (self.btn_credit, "credit")):
            ctx = b.get_style_context()
            if name == d:
                ctx.add_class("segon")
            else:
                ctx.remove_class("segon")

    @staticmethod
    def _parse_amount(raw):
        """Parse a typed amount into a positive, finite, whole-cent float, or
        None.

        Accepts the way people actually type money — a leading '$', thousands
        commas and a stray minus are tolerated. Empty, non-numeric, non-finite
        (inf / nan) and anything that does not reach a cent all return None, so
        a broken or unrecordable amount is never committed."""
        s = (raw or "").strip().replace(",", "").replace("$", "") \
            .replace(MINUS, "-")
        try:
            v = abs(float(s))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(v):
            return None
        v = _cents(v)
        return v if v else None

    @staticmethod
    def _tiny_amount(raw):
        """True when `raw` IS a number, but one too small to record as money
        ("0", "0.00", "0.004"). Answering "Enter an amount" to someone who
        plainly did would read as a bug, so the two cases get different words."""
        s = (raw or "").strip().replace(",", "").replace("$", "") \
            .replace(MINUS, "-")
        try:
            v = abs(float(s))
        except (TypeError, ValueError):
            return False
        return math.isfinite(v) and _cents(v) == 0

    def _on_add(self, *_):
        raw = self.f_amt.get_text()
        desc = self.f_desc.get_text().strip()
        amt_n = self._parse_amount(raw)
        if not desc or amt_n is None:
            # a click that can't commit should say why, not silently no-op —
            # right under the form, where the person is looking
            self._form_error(self._missing_msg(desc, amt_n, raw))
            (self.f_desc if not desc else self.f_amt).grab_focus()
            return
        self._form_error("")
        amt = amt_n if self.fdir == "credit" else -amt_n
        entry = {"date": time.strftime("%-d %b"), "desc": desc, "amt": amt}
        self.tx.append(entry)
        self._autosave()   # persist immediately — a committed entry must survive
        self.f_desc.set_text("")
        self.f_amt.set_text("")
        # A committed entry must be VISIBLE. If a search is on that the new
        # entry does not match, it would land somewhere the user cannot see and
        # read as an Add button that did nothing — so drop the filter, and go
        # back to the first page, where the newest entry always is.
        if self._terms and not self._matches(entry, self._terms):
            self.search.set_text("")
        self._shown = self._PAGE
        self._refresh()
        # positive confirmation that also clears any lingering validation error
        self._flash(_t("Entry added"))
        self.f_desc.grab_focus()

    def add_entry(self, desc, amt, date=None):
        """Append a ledger entry (amt > 0 credit, amt < 0 debit) and refresh.
        Public entry point for programmatic use and selftests."""
        self.tx.append({"date": date or time.strftime("%-d %b"),
                        "desc": str(desc), "amt": _cents(amt)})
        self._autosave()
        self._refresh()

    @staticmethod
    def _money(n):
        """Cent-accurate money string; negatives get a Unicode minus. Any
        non-finite value renders as $0.00 rather than the literal 'nan'."""
        try:
            if not math.isfinite(n):
                n = 0.0
            cents = round(abs(n), 2)
        except (TypeError, ValueError, OverflowError):
            n, cents = 0.0, 0.0
        # sign comes from the ROUNDED magnitude: a sub-cent negative (e.g. a
        # -0.001 float-accumulation remainder) rounds to zero and must read
        # "$0.00", never "−$0.00".
        sign = MINUS if (n < 0 and cents != 0) else ""
        return "%s$%s" % (sign, format(cents, ",.2f"))

    def _cmoney(self, n):
        """`_money` for text drawn with cairo. GTK labels render the
        typographic minus (U+2212) fine — Pango falls back per glyph — but
        cairo's toy-font API does NO per-glyph fallback: whatever single face
        fontconfig resolves is the only one it will draw from, so a face lacking
        U+2212 puts a tofu box in the middle of a number. The shipped interface
        face (Nimbus Sans) does carry it, but the image ships only Nimbus Sans +
        Liberation + DejaVu and the resolved face depends on config, so cairo
        money is drawn with the always-present ASCII hyphen instead — that holds
        whatever gets resolved (the chart and the exported PDF)."""
        return self._money(n).replace(MINUS, "-")

    def _balance_series(self):
        """Running balance at each point: opening, then after each entry."""
        vals = [self.opening]
        b = self.opening
        for t in self.tx:
            b = round(b + t["amt"], 2)
            vals.append(b)
        return vals

    def _refresh(self):
        total = round(self.opening + sum(t["amt"] for t in self.tx), 2)
        credit = round(sum(t["amt"] for t in self.tx if t["amt"] > 0), 2)
        debit = round(-sum(t["amt"] for t in self.tx if t["amt"] < 0), 2)
        self.balance.set_text(self._money(total))
        self.credit_lbl.set_text(
            ("+" + self._money(credit)) if credit else self._money(0))
        self.debit_lbl.set_text(
            (MINUS + self._money(debit)) if debit else self._money(0))
        self.count_lbl.set_text(str(len(self.tx)))

        # running balance — accumulate chronologically over the WHOLE ledger
        # (so a filtered row still shows the true balance it stood at), display
        # newest-first so the BALANCE column agrees with the AMOUNT columns. The
        # chronological index rides along so a clicked row can be edited/deleted
        # in place.
        bal = self.opening
        withbal = []
        for i, t in enumerate(self.tx):
            bal = round(bal + t["amt"], 2)
            withbal.append((i, t, bal))
        display = list(reversed(withbal))
        if self._terms:
            display = [r for r in display if self._matches(r[1], self._terms)]
        self._sync_find(display)

        for child in self.rows.get_children():
            self.rows.remove(child)
        if not display:
            self.empty.set_text(self._empty_text())
            self.rows.pack_start(self.empty, False, False, 0)
        else:
            # Build at most a page of rows. This ledger is meant to hold years,
            # and rebuilding every row on every change cost 1.3 SECONDS at 1200
            # entries (measured on the host; the guest's software renderer is
            # slower still) — a full second of lag on each entry someone types.
            # Nobody scrolls a thousand rows: the rest is one click away below,
            # and FIND reaches any of them directly.
            for i, t, running in display[:self._shown]:
                self.rows.pack_start(
                    self._tx_row(t, running, i), False, False, 0)
            if len(display) > self._shown:
                self.rows.pack_start(self._more_row(len(display)),
                                     False, False, 0)
        self.rows.show_all()
        self._chart_cache = None      # data moved — force a chart re-render
        self.chart.queue_draw()

    # ------------------------------------------------------------------- find
    @staticmethod
    def _matches(t, terms):
        """True when entry `t` matches EVERY lower-cased term in `terms`.

        Description, date and the entry's own figure are all searched, so
        "food" finds the groceries, "mar" finds March (dates are stored "12
        Mar") and "212.40" finds one figure off a paper statement. Terms are
        ANDed across those fields, which is what makes the real question —
        "what did I spend on food in March?" — a single query: `food mar`."""
        try:
            hay = "%s %s %.2f" % (t.get("desc", ""), t.get("date", ""),
                                  abs(t.get("amt", 0.0)))
        except (TypeError, ValueError):
            hay = "%s %s" % (t.get("desc", ""), t.get("date", ""))
        hay = hay.lower()
        return all(term in hay for term in terms)

    def _on_search(self, entry):
        """Track the query eagerly, but coalesce keystroke bursts: rebuilding
        the table on every character is visible typing lag on the software
        renderer (the same debounce contacts.py uses)."""
        self.filter = entry.get_text().strip()
        self._terms = tuple(self.filter.lower().split())
        if self._search_timer:
            GLib.source_remove(self._search_timer)
        self._search_timer = GLib.timeout_add(130, self._search_timeout)

    def _search_timeout(self):
        self._search_timer = 0
        self._shown = self._PAGE      # a new query starts at the top of its list
        self._refresh()
        return False                  # one-shot

    def _sync_find(self, display):
        """Answer the search beside the ledger's own totals: how many entries
        matched and what they come to. Crash-safe, and hidden entirely when
        nothing is being searched for."""
        try:
            if not self._terms:
                self.findsum.set_visible(False)
                return
            n = len(display)
            net = round(sum(t["amt"] for _i, t, _b in display), 2)
            self.find_n.set_text(_t("%d match%s") % (n, "" if n == 1 else "es"))
            self.find_net.set_text(("+" + self._money(net)) if net > 0
                                   else self._money(net))
            ctx = self.find_net.get_style_context()
            ctx.remove_class("credit")
            ctx.remove_class("debit")
            if net > 0:
                ctx.add_class("credit")
            elif net < 0:
                ctx.add_class("debit")
            self.findsum.set_visible(True)
        except Exception:
            pass

    def _empty_text(self):
        """What the ledger says when it has no rows to show — the three cases
        are genuinely different, and only one of them is "add your first
        entry"."""
        if self._terms:
            return _t("Nothing here matches “%s”.") % self.filter
        if self._damaged:
            return _t("The ledger file could not be read. A new ledger was started.")
        return _t("No entries. Add one above.")

    def _more_row(self, n):
        """Footer under a paged ledger: says how much of it is showing, and
        reveals the next page. Without it a long ledger would simply stop, with
        nothing to say that anything older exists."""
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("morerow")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        lab = Gtk.Label(label=_t("Show older entries"))
        lab.get_style_context().add_class("morelab")
        box.pack_start(lab, False, False, 0)
        cnt = Gtk.Label(label=_t("Showing %d of %d entries") % (self._shown, n))
        cnt.get_style_context().add_class("morecount")
        box.pack_start(cnt, False, False, 0)
        btn.add(box)
        btn.connect("clicked", self._show_more)
        return btn

    def _show_more(self, *_):
        # No scroll-position juggling needed: _refresh removes and re-adds every
        # row inside one call, so the main loop never sees the empty box and the
        # scrollbar's upper bound never collapses. Measured both ways at 400
        # entries — the reader stays exactly where they were.
        self._shown += self._PAGE
        self._refresh()

    def _tx_row(self, t, running, idx):
        # Each posted row is a relief-less button: clicking (or Enter/Space on a
        # focused row) opens the in-place editor, and Delete removes the entry.
        row = Gtk.Button()
        row.set_relief(Gtk.ReliefStyle.NONE)
        row.get_style_context().add_class("txrow")
        # Lead with the entry's own description. The column is elastic and on a
        # 1024-wide panel it is the narrowest thing on the row, so a real
        # description ellipsizes to "Groceries - Sunnysi..." and the only way to
        # read the rest was to open the editor. The description is the user's
        # own words, so it is never translated; the hint under it is.
        hint = _t("Click to edit  ·  Delete key removes")
        desc_txt = str(t["desc"]).strip()
        row.set_tooltip_text((desc_txt + "\n" + hint) if desc_txt else hint)
        row.connect("clicked", lambda *_: self._edit_tx(idx))
        row.connect("key-press-event", self._row_key, idx)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.get_style_context().add_class("txrowinner")

        date = Gtk.Label(label=t["date"], xalign=0)
        date.get_style_context().add_class("txdate")
        date.set_size_request(self._GRID[0], -1)
        box.pack_start(date, False, False, 0)

        desc = Gtk.Label(label=t["desc"], xalign=0)
        desc.get_style_context().add_class("txdesc")
        desc.set_ellipsize(3)
        box.pack_start(desc, True, True, 0)

        amt = t["amt"]
        deb = Gtk.Label(label=self._money(-amt) if amt < 0 else "", xalign=1)
        deb.get_style_context().add_class("txdebit")
        self._money_cell(deb, 2)
        box.pack_start(deb, False, False, 0)

        cred = Gtk.Label(label=("+" + self._money(amt)) if amt > 0 else "",
                         xalign=1)
        cred.get_style_context().add_class("txcredit")
        self._money_cell(cred, 3)
        box.pack_start(cred, False, False, 0)

        bal = Gtk.Label(label=self._money(running), xalign=1)
        bal.get_style_context().add_class("txbal")
        self._money_cell(bal, 4)
        box.pack_start(bal, False, False, 0)

        row.add(box)
        return row

    def _row_key(self, _w, ev, idx):
        """Delete key on a focused ledger row asks before removing that entry
        (deletion is destructive and has no undo)."""
        if ev.keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self._confirm_delete(idx)
            return True
        return False

    # -------------------------------------------------------------- edit / del
    def _edit_field(self, caption, widget):
        """A captioned column: small eyebrow caption above an input widget."""
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        cap = Gtk.Label(label=caption, xalign=0)
        cap.get_style_context().add_class("caption")
        col.pack_start(cap, False, False, 0)
        col.pack_start(widget, False, False, 0)
        return col

    # ------------------------------------------------------- overlay geometry
    def _overlay_size(self):
        """Live pixel size for a full-window modal scrim. Real hardware panels
        are NOT 1920x1080 (they may be 1366x768, 1280x800, 1600x900, …), so a
        hardcoded scrim overflows a smaller panel and drags the centred card
        off-screen. Size to the live window allocation, falling back to
        nbapp.screen_size() (the real primary monitor) — never to a literal
        1920x1080."""
        alloc = self.get_allocation()
        sw, sh = nbapp.screen_size()
        W = alloc.width if alloc.width > 1 else sw
        H = alloc.height if alloc.height > 1 else sh
        return W, H

    def _center_card(self, layer, card_win, W, H):
        """Centre an overlay card on the ACTUAL window size using the card's
        measured natural size, so it stays fully on-screen at any resolution
        (matches nbapp's About-card pattern). Call after layer.show_all() so
        the natural size is realised."""
        try:
            _min, nat = card_win.get_preferred_size()
            cw = nat.width if nat.width > 1 else 300
            ch = nat.height if nat.height > 1 else 200
            layer.move(card_win, max((W - cw) // 2, 0), max((H - ch) // 2, 0))
        except Exception:
            pass

    def _edit_tx(self, idx):
        """Open a modal editor over one ledger row (drawn as an overlay, same
        pattern as the report / about cards). Amount, description, date and
        direction are prefilled; Save writes the edit back in place, Delete
        removes the row. Both recompute balances + chart and re-persist."""
        if not (0 <= idx < len(self.tx)):
            return
        self._close_menu()
        self._close_edit()          # never stack two editors
        t = self.tx[idx]
        self._edit_idx = idx
        self._edir = "credit" if t["amt"] > 0 else "debit"

        layer = Gtk.Fixed()
        W, H = self._overlay_size()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_edit(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("editcard")
        title = Gtk.Label(label=_t("Edit Entry"), xalign=0)
        title.get_style_context().add_class("edittitle")
        card.pack_start(title, False, False, 0)

        # date + description
        self._e_date = Gtk.Entry()
        self._e_date.set_text(str(t["date"]))
        self._e_date.set_size_request(120, -1)
        self._e_date.get_style_context().add_class("finput")
        self._e_date.connect("activate", lambda *_: self._save_edit())
        self._e_desc = Gtk.Entry()
        self._e_desc.set_text(str(t["desc"]))
        self._e_desc.get_style_context().add_class("finput")
        self._e_desc.connect("activate", lambda *_: self._save_edit())
        row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row1.pack_start(self._edit_field("DATE", self._e_date), False, False, 0)
        row1.pack_start(self._edit_field("DESCRIPTION", self._e_desc),
                        True, True, 0)
        card.pack_start(row1, False, False, 0)

        # amount + direction
        self._e_amt = Gtk.Entry()
        self._e_amt.set_text("%.2f" % abs(t["amt"]))
        self._e_amt.set_alignment(1.0)
        self._e_amt.set_size_request(120, -1)
        self._e_amt.get_style_context().add_class("finput")
        self._e_amt.connect("activate", lambda *_: self._save_edit())
        seg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        seg.get_style_context().add_class("segbox")
        self._e_btn_debit = Gtk.Button(label=_t("Debit"))
        self._e_btn_debit.set_relief(Gtk.ReliefStyle.NONE)
        self._e_btn_debit.get_style_context().add_class("seg")
        self._e_btn_debit.set_tooltip_text(_t("Money out of the account"))
        self._e_btn_debit.connect("clicked",
                                  lambda *_: self._e_set_dir("debit"))
        self._e_btn_credit = Gtk.Button(label=_t("Credit"))
        self._e_btn_credit.set_relief(Gtk.ReliefStyle.NONE)
        self._e_btn_credit.get_style_context().add_class("seg")
        self._e_btn_credit.set_tooltip_text(_t("Money into the account"))
        self._e_btn_credit.connect("clicked",
                                   lambda *_: self._e_set_dir("credit"))
        seg.pack_start(self._e_btn_debit, False, False, 0)
        seg.pack_start(self._e_btn_credit, False, False, 0)
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row2.pack_start(self._edit_field("AMOUNT", self._e_amt),
                        False, False, 0)
        row2.pack_start(self._edit_field("DIRECTION", seg), False, False, 0)
        card.pack_start(row2, False, False, 0)
        self._e_set_dir(self._edir)

        # Inline validation line. The editor is an overlay, so the sidebar
        # status is hidden behind it — a blocked Save must explain itself right
        # here rather than appear to do nothing. Hidden until needed.
        self._e_err = Gtk.Label(label="", xalign=0)
        self._e_err.get_style_context().add_class("editerr")
        self._e_err.set_no_show_all(True)
        card.pack_start(self._e_err, False, False, 0)

        # actions — Delete (alert) on the left, Cancel / Save on the right
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        delete = Gtk.Button(label=_t("Delete"))
        delete.set_relief(Gtk.ReliefStyle.NONE)
        delete.get_style_context().add_class("delbtn")
        delete.connect("clicked", lambda *_: self._confirm_delete(self._edit_idx))
        actions.pack_start(delete, False, False, 0)
        actions.pack_start(Gtk.Box(), True, True, 0)     # spacer
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("cancelbtn")
        cancel.connect("clicked", lambda *_: self._close_edit())
        save = Gtk.Button(label=_t("Save"))
        save.set_relief(Gtk.ReliefStyle.NONE)
        save.get_style_context().add_class("savebtn")
        save.connect("clicked", lambda *_: self._save_edit())
        actions.pack_start(cancel, False, False, 0)
        actions.pack_start(save, False, False, 0)
        card.pack_start(actions, False, False, 0)

        card_win = Gtk.EventBox()   # own GdkWindow so it blits (see nbapp)
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        # Centre on the ACTUAL window (never a fixed 1920x1080, which lands the
        # card off-centre / off-screen on a smaller panel).
        self._center_card(layer, card_win, W, H)
        # keep the editor (and its inputs) in front of the app content on the
        # no-compositor stack, exactly as nbapp does for dropdown menus.
        try:
            lw = layer.get_window()
            if lw is not None:
                lw.raise_()
            cw = card_win.get_window()
            if cw is not None:
                cw.raise_()
        except Exception:
            pass
        self._edit_layer = layer
        self._e_desc.grab_focus()
        # Focusing an entry parks the cursor at the END of its text, which
        # scrolls a long description so the editor opens showing its tail
        # ("…ewerage service charge") — the entry looks like it holds something
        # else entirely. Park the cursor at the start so the field reads from
        # the beginning of what was written.
        self._e_desc.set_position(0)

    def _e_set_dir(self, d):
        """Toggle the editor's Debit/Credit segmented control."""
        self._edir = d
        for b, name in ((self._e_btn_debit, "debit"),
                        (self._e_btn_credit, "credit")):
            ctx = b.get_style_context()
            if name == d:
                ctx.add_class("segon")
            else:
                ctx.remove_class("segon")

    def _save_edit(self):
        """Write the editor fields back onto self.tx[idx], cent-accurate, then
        persist + refresh. An empty description or non-numeric / zero amount
        leaves the editor open rather than committing a broken entry."""
        idx = getattr(self, "_edit_idx", -1)
        if not (0 <= idx < len(self.tx)):
            self._close_edit()
            return
        raw = self._e_amt.get_text()
        desc = self._e_desc.get_text().strip()
        amt_n = self._parse_amount(raw)
        if not desc or amt_n is None:
            # incomplete edit — keep the editor open, but say why + focus the
            # offending field rather than letting Save appear to do nothing
            try:
                self._e_err.set_text(self._missing_msg(desc, amt_n, raw))
                self._e_err.show()
            except Exception:
                pass
            (self._e_desc if not desc else self._e_amt).grab_focus()
            return
        amt = amt_n if self._edir == "credit" else -amt_n
        date = self._e_date.get_text().strip() or str(self.tx[idx]["date"])
        self.tx[idx] = {"date": date, "desc": desc, "amt": amt}
        self._autosave()         # a committed edit must survive
        self._close_edit()
        self._refresh()
        self._flash(_t("Entry updated"))

    def _confirm_delete(self, idx):
        """Ask before removing entry `idx` — deletion is destructive and has no
        undo. Drawn as an in-window overlay card (same no-compositor-safe
        pattern as the editor / report cards), so it always paints on top."""
        if not (0 <= idx < len(self.tx)):
            return
        self._close_menu()
        self._close_confirm()        # never stack two confirms
        self._confirm_idx = idx
        desc = str(self.tx[idx].get("desc", "")).strip() or "this entry"
        if len(desc) > 40:
            desc = desc[:40] + "…"

        layer = Gtk.Fixed()
        W, H = self._overlay_size()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_confirm(), True)[1])
        layer.put(scrim, 0, 0)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("confirmcard")
        # Give the card a width, as the summary card does. max_width_chars alone
        # sizes off the font's APPROXIMATE character width, which for this face
        # is far narrower than the text really sets — the card came out ~224px
        # and broke a one-line question over three cramped lines.
        card.set_size_request(320, -1)
        title = Gtk.Label(label=_t("Delete Entry"), xalign=0)
        title.get_style_context().add_class("confirmtitle")
        card.pack_start(title, False, False, 0)
        msg = Gtk.Label(label=_t("Delete “%s”? This cannot be undone.") % desc,
                        xalign=0)
        msg.set_line_wrap(True)
        msg.set_max_width_chars(38)
        msg.get_style_context().add_class("confirmmsg")
        card.pack_start(msg, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        actions.pack_start(Gtk.Box(), True, True, 0)     # right-align
        cancel = Gtk.Button(label=_t("Cancel"))
        cancel.set_relief(Gtk.ReliefStyle.NONE)
        cancel.get_style_context().add_class("cancelbtn")
        cancel.connect("clicked", lambda *_: self._close_confirm())
        confirm = Gtk.Button(label=_t("Delete"))
        confirm.set_relief(Gtk.ReliefStyle.NONE)
        confirm.get_style_context().add_class("confirmdel")
        confirm.connect("clicked", lambda *_: self._do_confirmed_delete())
        actions.pack_start(cancel, False, False, 0)
        actions.pack_start(confirm, False, False, 0)
        card.pack_start(actions, False, False, 0)

        card_win = Gtk.EventBox()   # own GdkWindow so it blits (see nbapp)
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        # Centre on the ACTUAL window (never a fixed 1920x1080, which lands the
        # card off-centre / off-screen on a smaller panel).
        self._center_card(layer, card_win, W, H)
        try:
            lw = layer.get_window()
            if lw is not None:
                lw.raise_()
            cw = card_win.get_window()
            if cw is not None:
                cw.raise_()
        except Exception:
            pass
        self._confirm_layer = layer
        confirm.grab_focus()

    def _do_confirmed_delete(self):
        """Commit the confirmed deletion, then dismiss the confirm card."""
        idx = getattr(self, "_confirm_idx", -1)
        self._close_confirm()
        self._delete_tx(idx)

    def _close_confirm(self):
        """Dismiss the open delete-confirm overlay; True if one was showing."""
        layer = getattr(self, "_confirm_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._confirm_layer = None
            return True
        return False

    def _delete_tx(self, idx):
        """Remove one entry, persist, refresh + redraw. Reached only after the
        confirm card is accepted (editor Delete button and the Delete key on a
        focused row both route through _confirm_delete first)."""
        if not (0 <= idx < len(self.tx)):
            return
        del self.tx[idx]
        self._autosave()         # a deletion must survive too
        self._close_confirm()
        self._close_edit()
        self._refresh()
        self._flash(_t("Entry deleted"))

    def _close_edit(self):
        """Dismiss the open editor overlay; return True if one was showing."""
        layer = getattr(self, "_edit_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._edit_layer = None
            return True
        return False

    # ------------------------------------------------------------------ chart
    def _draw_chart(self, area, cr):
        """Blit the running-balance chart, re-rendering it only when the live
        allocation or the data has changed.

        On the GPU-less hardware framebuffer every expose repaints in software,
        so the vector + toy-font-text render is done once into an ImageSurface
        and cached; incidental repaints (an overlay opening, a scroll, a focus
        change) then cost a single blit instead of re-shaping every label and
        re-tracing the path. A cache miss keys off the current (W, H), so a
        resize re-renders automatically."""
        alloc = area.get_allocation()
        W, H = alloc.width, alloc.height
        if W < 20 or H < 20:           # not-yet / degenerately allocated
            return False
        cache = self._chart_cache
        if not (cache and cache[0] == W and cache[1] == H):
            try:
                surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
                self._paint_chart(cairo.Context(surf), W, H)
                cache = self._chart_cache = (W, H, surf)
            except Exception:
                # a surface hiccup must never blank the chart — paint direct
                self._chart_cache = None
                try:
                    self._paint_chart(cr, W, H)
                except Exception:
                    pass
                return False
        cr.set_source_surface(cache[2], 0, 0)
        cr.paint()
        return False

    def _paint_chart(self, cr, W, H):
        """Render the running-balance line chart onto `cr` at size W×H.

        All geometry is derived from the passed W/H (never a stale/hardcoded
        size), so it is correct at the real 1920×1080 allocation and after any
        resize. The right gutter is sized to the ACTUAL value labels so large
        balances can't clip off the widget's right edge."""
        cr.set_source_rgb(*nbicons._hex(BG))
        cr.rectangle(0, 0, W, H)
        cr.fill()

        vals = self._balance_series()
        # No cr.select_font_face: every string on this chart is drawn through
        # Pango, which carries its own font description.
        if len(vals) < 2:
            cr.set_source_rgb(*nbicons._hex("#A39D8F"))
            # Drawn through Pango, not cairo's toy-font API. show_text() does
            # no per-glyph fallback, so this had to stay ASCII AND could not be
            # translated — it was the one English sentence left on an otherwise
            # translated Accounting window, in every language. Pango picks a
            # face per glyph, so the catalog string renders whatever script it
            # is in. (sequencer.py made the same move for its lane labels.)
            layout = PangoCairo.create_layout(cr)
            fd = Pango.FontDescription("Nimbus Sans")
            fd.set_absolute_size(12 * Pango.SCALE)   # match the old 12px
            layout.set_font_description(fd)
            layout.set_text(_t("No entries to plot"), -1)
            _w, h = layout.get_pixel_size()
            cr.move_to(4, H / 2 - h / 2)
            PangoCairo.show_layout(cr, layout)
            return

        vmin = min(vals + [0.0])
        vmax = max(vals + [0.0])
        if vmax == vmin:
            vmax = vmin + 1.0

        # Right gutter sized to the real min/max labels (measured at their font
        # size) so a large balance can't spill past the edge, clamped so it can
        # never swallow the whole plot on a narrow allocation.
        lo_s, hi_s = self._cmoney(vmin), self._cmoney(vmax)
        label_w = max(_text_w(cr, lo_s, 10), _text_w(cr, hi_s, 10))
        pad_l, pad_t, pad_b = 4, 12, 20
        pad_r = min(int(label_w) + 14, int(W * 0.5))
        x0, x1 = pad_l, W - pad_r
        y0, y1 = pad_t, H - pad_b
        if x1 - x0 < 8:                # pathologically narrow — skip the plot
            return
        n = len(vals)

        def sx(i):
            return (x0 + x1) / 2 if n == 1 else x0 + (x1 - x0) * i / (n - 1)

        def sy(v):
            return y1 - (y1 - y0) * (v - vmin) / (vmax - vmin)

        # zero baseline, when 0 is within range
        if vmin <= 0 <= vmax:
            yz = sy(0)
            cr.set_line_width(1)
            cr.set_dash([3, 3])
            cr.set_source_rgb(*nbicons._hex(GRID))
            cr.move_to(x0, yz)
            cr.line_to(x1, yz)
            cr.stroke()
            cr.set_dash([])

        # subtle area fill under the line
        cr.move_to(sx(0), y1)
        for i, v in enumerate(vals):
            cr.line_to(sx(i), sy(v))
        cr.line_to(sx(n - 1), y1)
        cr.close_path()
        r, g, b = nbicons._hex(INK)
        cr.set_source_rgba(r, g, b, 0.06)
        cr.fill()

        # the balance line
        cr.set_line_width(2)
        cr.set_source_rgb(*nbicons._hex(INK))
        for i, v in enumerate(vals):
            (cr.move_to if i == 0 else cr.line_to)(sx(i), sy(v))
        cr.stroke()

        # end marker
        cr.arc(sx(n - 1), sy(vals[-1]), 3, 0, 6.2832)
        cr.fill()

        # min / max value labels, right-aligned to the widget edge so they sit
        # flush in the gutter and never overrun it
        rt = W - 6
        cr.set_source_rgb(*nbicons._hex(MUTED))
        _show_text(cr, rt - _text_w(cr, hi_s, 10), sy(vmax) + 3, hi_s, 10)
        _show_text(cr, rt - _text_w(cr, lo_s, 10), sy(vmin) + 3, lo_s, 10)

    # ----------------------------------------------------------------- menus
    def menu_items(self, name):
        if name == "File":
            # accounting.json is the sole source of truth (autosaved on every
            # committed entry). File offers only the in-memory add-entry action,
            # a one-way export of the ledger to a PDF under $NB_HOME/Documents,
            # and Close — no file open / save / save-as.
            # Both exports write their file straight into $NB_HOME/Documents
            # and ask nothing, so neither takes an ellipsis; Print opens the
            # printer dialog, so it does — with the real ellipsis character,
            # not the three ASCII dots this one item used to carry while every
            # other Print… in the OS used "…". Exports come before Print, the
            # order the single-store File menu is written in.
            return [(_t("New Entry"), self._reveal_form),
                    nbapp.SEP,
                    (_t("Export to PDF"), self._export_pdf),
                    (_t("Export to CSV"), self._export_csv),
                    (_t("Print…"), self._print),
                    nbapp.SEP,
                    (_t("Close    Esc"), self.close)]
        if name == "View":
            # A clear toggle: name the action it will actually perform, based
            # on the chart's current visibility.
            shown = True
            try:
                shown = self.chartwrap.get_visible()
            except Exception:
                pass
            return [(_t("Hide Balance Chart") if shown
                     else _t("Show Balance Chart"), self._toggle_chart)]
        if name == "Reports":
            return [(_t("Ledger Summary"), self._report_summary)]
        return super().menu_items(name)

    def _toggle_chart(self):
        """Show or hide the balance-over-time chart (a genuine view toggle)."""
        try:
            if self.chartwrap.get_visible():
                self.chartwrap.hide()
            else:
                self.chartwrap.show()
        except Exception:
            pass

    def _report_summary(self):
        """Overlay a debit / credit / balance summary.

        With a FIND query running it summarises WHAT YOU ARE LOOKING AT — the
        matching entries, titled with the query — because "add up the ones I
        just found" is the whole reason to have gone looking. With no query it
        is the whole ledger, as before."""
        try:
            rows = []
            if self._terms:
                tx = [t for t in self.tx if self._matches(t, self._terms)]
                title = _t("Found: %s") % self.filter
            else:
                tx = self.tx
                title = _t("Ledger Summary")
            credit = round(sum(t["amt"] for t in tx if t["amt"] > 0), 2)
            debit = round(-sum(t["amt"] for t in tx if t["amt"] < 0), 2)
            # (caption, value, colour-class). Opening is only shown when it is
            # non-zero — an always-$0.00 line would just be clutter, since the
            # ledger has no opening-balance control and ships at zero.
            rows.append(("ENTRIES", str(len(tx)), None))
            if self.opening and not self._terms:
                rows.append(("OPENING", self._money(self.opening), None))
            rows += [
                ("CREDIT", "+" + self._money(credit) if credit
                 else self._money(0), "credit"),
                ("DEBIT", (MINUS + self._money(debit)) if debit
                 else self._money(0), "debit"),
            ]
            if self._terms:
                # A subset has no "balance" — it has a net, which is what these
                # entries did to the balance between them.
                net = round(credit - debit, 2)
                rows.append(("NET", ("+" + self._money(net)) if net > 0
                             else self._money(net), "strong"))
            else:
                total = round(self.opening + sum(t["amt"] for t in tx), 2)
                rows.append(("BALANCE", self._money(total), "strong"))
            self._report_card(title, rows)
        except Exception:
            pass

    def _report_card(self, title, rows):
        """Draw a dismissable summary overlay: a serif title over aligned
        caption/value rows (values right-aligned and colour-coded to match the
        sidebar). Dismissed by Esc or a click on the scrim."""
        self._close_menu()
        self._close_report()   # never stack two report cards
        layer = Gtk.Fixed()
        W, H = self._overlay_size()
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.set_size_request(W, H)
        scrim.connect("button-press-event",
                      lambda *a: (self._close_report(), True)[1])
        layer.put(scrim, 0, 0)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        card.get_style_context().add_class("sumcard")
        card.set_size_request(300, -1)
        nm = Gtk.Label(label=title, xalign=0)
        nm.get_style_context().add_class("sumtitle")
        card.pack_start(nm, False, False, 0)
        for cap, val, cls in rows:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            line.get_style_context().add_class("sumrow")
            k = Gtk.Label(label=cap, xalign=0)
            k.get_style_context().add_class("sumkey")
            line.pack_start(k, True, True, 0)
            v = Gtk.Label(label=val, xalign=1)
            vctx = v.get_style_context()
            vctx.add_class("sumval")
            if cls:
                vctx.add_class(cls)
            line.pack_end(v, False, False, 0)
            card.pack_start(line, False, False, 0)
        card_win = Gtk.EventBox()   # own GdkWindow so it blits (see nbapp)
        card_win.add(card)
        layer.put(card_win, 0, 0)
        self._overlay.add_overlay(layer)
        layer.show_all()
        # Centre on the ACTUAL window (never a fixed 1920x1080, which lands the
        # card off-centre / off-screen on a smaller panel).
        self._center_card(layer, card_win, W, H)
        self._report_layer = layer

    def _close_report(self):
        """Dismiss the open report overlay; return True if one was showing."""
        layer = getattr(self, "_report_layer", None)
        if layer is not None:
            try:
                self._overlay.remove(layer)
            except Exception:
                pass
            self._report_layer = None
            return True
        return False

    def _on_key(self, w, ev):
        # Esc dismisses the topmost overlay first — the delete-confirm card,
        # then the Edit editor, then an open Report overlay, then the open entry
        # form — before falling through to the base handling (About / menu close
        # / quit). The form matters: Esc is what a person reaches for to back
        # out of a half-typed entry, and without this it closed the whole
        # application from under them instead.
        if ev.keyval == Gdk.KEY_Escape:
            if self._close_confirm():
                return True
            if self._close_edit():
                return True
            if self._close_report():
                return True
            try:
                if self.form_reveal.get_reveal_child():
                    self.form_reveal.set_reveal_child(False)
                    self._form_error("")
                    return True
            except Exception:
                pass
            # ...and then the search, so a filtered ledger is one key from whole
            # again rather than looking permanently half-empty.
            if self._terms:
                self.search.set_text("")
                return True
        return super()._on_key(w, ev)

    # -------------------------------------------------------------------- css
    def _install_css(self):
        css = b"""
        .sidebar { background: #F1EEE6; border-right: 1px solid #D7D2C5; }
        .sidebar *, .ledger * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .sidehead { padding: 24px 24px 18px; border-bottom: 1px solid #D7D2C5; }
        .caption { font-size: 11px; letter-spacing: 2px; color: #A39D8F;
                   font-weight: 600; margin-bottom: 6px; }
        .balance { font-size: 34px; font-weight: 700; color: #1A1916; }

        .statlist { padding: 16px 24px 8px; }
        .statrow { padding: 9px 0; border-bottom: 1px solid #E4DECF; }
        .statcap { font-size: 11px; letter-spacing: 1.5px; color: #A39D8F;
                   font-weight: 600; }
        .statval { font-size: 15px; color: #1A1916; font-weight: 500; }
        .statval.credit { color: #4F6B45; }
        .statval.debit { color: #A23B2B; }
        .statusline { padding: 14px 24px; font-size: 12px; color: #8A857A;
                      border-top: 1px solid #D7D2C5; }

        /* FIND: the sidebar's search field and its answer. Same boxed-field
           idiom as the Contacts search header, on the sidebar's beige. */
        .findbox { padding: 18px 24px 8px; }
        .searchbox { background: #FCFBF8; border: 1px solid #C4BFB1;
                     border-radius: 2px; min-height: 36px; }
        .searchentry { background: transparent; border: none; box-shadow: none;
                       font-size: 14px; color: #1A1916; }
        .findsum { padding: 11px 0 0; }
        .findn { font-size: 12px; color: #8A857A; }
        .findnet { font-size: 14px; color: #1A1916; font-weight: 500; }
        .findnet.credit { color: #4F6B45; }
        .findnet.debit { color: #A23B2B; }

        .ledger { background: #FCFBF8; }
        .chartwrap { padding: 26px 48px 6px; }
        .chartwrap .caption { margin-bottom: 10px; }

        .addrow { margin: 10px 48px 10px; padding: 13px 18px;
                  border: 1px dashed #C9C4B6; border-radius: 3px;
                  background: transparent; box-shadow: none; }
        .addrow label { font-size: 14px; color: #8A857A; }
        .addrow:hover { border-color: #B0AA99; background: #FCFBF8; }
        .entryform { margin: 0 48px 12px; padding: 8px 14px; min-height: 52px;
                     border: 1px solid #CFC9BA; border-radius: 2px;
                     background: #FCFBF8; }
        /* inline validation under the entry form: signage-red as an alert,
           sitting in the form's own gutter so it lines up with the fields */
        .formerr { color: #C8341E; font-size: 12px; font-weight: 600;
                   margin: 0 48px 10px; }
        .fdate { font-size: 13px; color: #8A857A; }
        .finput { min-height: 36px; border: 1px solid #DCD7C9; border-radius: 2px;
                  background: #FCFBF8; padding: 0 10px; font-size: 14px;
                  color: #1A1916; }
        .finput:focus { border-color: #B8B2A2; }
        .segbox { border: 1px solid #DCD7C9; border-radius: 2px; }
        .seg { min-height: 34px; padding: 0 13px; font-size: 13px; font-weight: 600;
               color: #3A362E; background: #FCFBF8; border: none; border-radius: 0;
               box-shadow: none; }
        /* selected direction: darker-beige chrome with the signage-red active
           marker (never a black fill) - matches the OS selected-state idiom */
        .segon { background: #EAE3D2; color: #C8341E; font-weight: 700; }
        .segon:hover { background: #EAE3D2; }
        /* primary commit button: a solid darker-beige paper button (signage-red
           is reserved for the active marker and the destructive Delete) */
        .addbtn { min-height: 36px; padding: 0 18px; background: #ECE7DA;
                  color: #1A1916; border: 1px solid #C4BFB1; border-radius: 2px;
                  font-size: 13px; font-weight: 600; box-shadow: none; }
        .addbtn:hover { background: #E2DBCB; border-color: #B7B0A0; }

        .colhead label { font-size: 11px; letter-spacing: 1px; color: #8A857A;
                         font-weight: 600; }
        /* Ledger column header: a hairline underline, not the heavy ink rule it
           used to be. A solid #1A1916 line read as a black slab across the full
           table width - the one thing the papertone language never does; #C9C4B6
           gives the header clear-but-soft definition (matches the Finder/table
           header treatment) while staying on paper. */
        .ledgerhead { padding: 12px 48px; margin-top: 4px;
                      border-bottom: 1px solid #C9C4B6; }
        .rowscroll { background: #FCFBF8; }
        .rows { padding: 0 48px 50px; }
        /* each row is a relief-less button so it is clickable + focusable;
           strip the button chrome so it reads as a plain ledger line. */
        .txrow { min-height: 52px; padding: 0; margin: 0;
                 border: none; border-bottom: 1px solid #F0ECE0;
                 border-radius: 0; background: transparent; box-shadow: none; }
        .txrow:hover { background: #F4F2EC; }
        .txrow:focus { background: #F1EEE6; }
        .txdate { font-size: 14px; color: #8A857A; }
        .txdesc { font-size: 15px; color: #1A1916; }
        .txdebit { font-size: 15px; font-weight: 600; color: #A23B2B; }
        .txcredit { font-size: 15px; font-weight: 600; color: #4F6B45; }
        .txbal { font-size: 15px; color: #3A362E; }
        .emptystate { padding: 60px 0; font-size: 14px; color: #A39D8F; }
        /* paged-ledger footer: reads as the next ledger line, not as a button
           bolted onto the bottom of the table */
        .morerow { min-height: 62px; padding: 0; margin: 0; border: none;
                   border-radius: 0; background: transparent; box-shadow: none; }
        .morerow:hover { background: #F4F2EC; }
        .morelab { font-size: 14px; font-weight: 600; color: #3A362E; }
        .morecount { font-size: 12px; color: #A39D8F; }

        /* in-place row editor (drawn as an overlay card) */
        .editcard { background: #FCFBF8; border: 1px solid #C4BFB1;
                    padding: 26px 30px; }
        .editcard * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .edittitle { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                     font-size: 22px; font-weight: 600; color: #1A1916;
                     margin-bottom: 2px; }
        .editcard .caption { font-size: 10px; letter-spacing: 1.5px;
                             color: #A39D8F; font-weight: 600;
                             margin-bottom: 0; }
        /* inline validation line in the editor: signage-red as an alert */
        .editerr { color: #C8341E; font-size: 12px; font-weight: 600; }
        .savebtn { min-height: 36px; padding: 0 20px; background: #ECE7DA;
                   color: #1A1916; border: 1px solid #C4BFB1; border-radius: 2px;
                   font-size: 13px; font-weight: 600; box-shadow: none; }
        .savebtn:hover { background: #E2DBCB; border-color: #B7B0A0; }
        .cancelbtn { min-height: 36px; padding: 0 14px; background: transparent;
                     color: #8A857A; border: none; box-shadow: none;
                     font-size: 13px; font-weight: 600; }
        .cancelbtn:hover { color: #1A1916; }
        /* Delete is destructive - signage-red as an alert. An outline in the
           editor (it only opens the confirm), solid-fill in the confirm card. */
        .delbtn { min-height: 36px; padding: 0 18px; background: transparent;
                  color: #C8341E; border: 1px solid #E7C7C1; border-radius: 2px;
                  font-size: 13px; font-weight: 600; box-shadow: none; }
        .delbtn:hover { background: #C8341E; color: #FCFBF8;
                        border-color: #C8341E; }

        /* delete-confirm overlay: paper card, darker-beige border; the only
           red is the destructive primary button */
        .confirmcard { background: #FCFBF8; border: 1px solid #C4BFB1;
                       padding: 24px 28px 20px; }
        .confirmcard * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .confirmtitle { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                        font-size: 20px; font-weight: 600; color: #1A1916; }
        .confirmmsg { font-size: 13px; color: #57534B; }
        .confirmdel { min-height: 36px; padding: 0 18px; background: #C8341E;
                      color: #FCFBF8; border: 1px solid #C8341E; border-radius: 2px;
                      font-size: 13px; font-weight: 600; box-shadow: none; }
        .confirmdel:hover { background: #A82A18; border-color: #A82A18; }

        /* Reports > Ledger Summary overlay: serif title over aligned
           caption/value rows, values colour-coded like the sidebar stats */
        .sumcard { background: #FCFBF8; border: 1px solid #C4BFB1;
                   padding: 26px 32px 20px; }
        .sumcard * { font-family: "Nimbus Sans","Helvetica",sans-serif; }
        .sumtitle { font-family: "Newsreader","Liberation Serif","Georgia",serif;
                    font-size: 22px; font-weight: 600; color: #1A1916;
                    margin-bottom: 10px; }
        .sumrow { padding: 8px 0; border-bottom: 1px solid #EFEADD; }
        .sumkey { font-size: 11px; letter-spacing: 1.5px; color: #A39D8F;
                  font-weight: 600; }
        .sumval { font-size: 16px; color: #1A1916; font-weight: 500; }
        .sumval.credit { color: #4F6B45; }
        .sumval.debit { color: #A23B2B; }
        .sumval.strong { font-size: 19px; font-weight: 700; color: #1A1916; }
        /* Tabular figures: the ledger's money columns must align on the
           decimal point. The design language reserves the mono family for
           counters, so the figures (not the descriptions/dates) use it. The
           amounts sit in the fixed-width cells _GRID sets, which fit mono at
           these sizes. */
        .balance, .statval, .txdebit, .txcredit, .txbal, .sumval {
            font-family: "Liberation Mono","DejaVu Sans Mono",monospace; }
        """
        prov = Gtk.CssProvider()
        try:
            prov.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), prov,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        except Exception:
            # styling is cosmetic; a bad screen/provider must not stop launch
            pass


if __name__ == "__main__":
    nbapp.run(Accounting)
