# Notebook OS — UI/UX audit + fix brief (agent shared context)

You are auditing **Notebook OS**, a from-scratch Linux desktop OS. Your job is to
find and fix every user-facing flaw in the apps assigned to you, so a
**non-technical mainstream user** sees a 100% clean, coherent, beautiful product.
The user has been finding bugs on real hardware that we missed — we must catch
them first.

## Where the code is

Source of truth (edit HERE, nowhere else):

```
/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/<app>.py
```

Never edit `buildroot/output/target/...` — that is build output, it gets
overwritten. Theme CSS:
`.../rootfs-overlay/usr/share/themes/Papertone/gtk-3.0/gtk.css`.
Shared app chrome/base class: `de/nbapp.py`. Icons: `de/nbicons.py` (drawn
vector glyphs). Translation helper: `de/nbi18n.py` (`_t()`).

## THE #1 BUG CLASS THIS ROUND: small-screen overflow

Real hardware runs at the **firmware's** resolution and matchbox maximises every
app to the screen. Previous audits rendered at 1920x1080 only and dismissed
narrow-width breakage as a "harness artifact". **That was wrong** — on a
1366x768 or 1024x768 panel it is a real bug: the bottom/right of the UI is
simply unreachable, because GTK cannot shrink a window below its minimum size.

Measured minimum sizes (`min_w x min_h`) — anything over **1024 x 740** is a
defect you must fix (740 = 768 minus the 28px top panel):

```
writer 1321x156   journal 1060x161   academic 1080x236  cookbook 1065x521
calendar 1203x450 illustrator 1124x370  sequencer 488x1029  video 1160x676
packages 1142x475 settings 1372x130  tasks 1079x398    gbasdk 889x1053
```

**Target: every app must lay out correctly at 1024x740 and look right at
1366x740 and 1920x1052.** Typical fixes, in order of preference:

1. Put the over-tall/over-wide region in a `Gtk.ScrolledWindow` (usually the
   right fix for editor panes, long forms, settings pages).
2. `set_size_request(-1, ...)` instead of a hard width, or lower the hard
   minimum, when a fixed size was arbitrary.
3. `label.set_ellipsize(Pango.EllipsizeMode.END)` + `set_max_width_chars()` on
   labels that force a toolbar wide.
4. `set_line_wrap(True)` on long text.
5. For a row of buttons that cannot fit, let it wrap or scroll — never clip.

Do NOT "fix" it by shrinking fonts or cramming; the design language must hold.

## How to LOOK at an app (this is the whole point — do not skip it)

`tools/uishot.py` renders any app **offscreen** on the host under the real
Papertone theme + the real guest fonts, and saves a PNG you can `Read` and
actually look at. No VM boot needed, nothing appears on screen.

```bash
SP=<your scratch dir>
DISPLAY=:0 FONTCONFIG_FILE=/home/ben/Documents/notebookos-linux/tools/guest-fonts.conf \
PYTHONPATH=/home/ben/Documents/notebookos-linux/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de:/home/ben/Documents/notebookos-linux/tools \
python3 /home/ben/Documents/notebookos-linux/tools/appshot.py $SP/out 1366x740,1920x1052 music
```

`tools/appshot.py OUTDIR WxH[,WxH...] app [app...]` writes
`OUTDIR/<app>_<W>x<H>.png`. **`Read` every PNG you produce.** If the saved PNG
is TALLER or WIDER than you asked for, that app's minimum size exceeds the
budget — that is the overflow bug above, and the PNG height tells you by how
much.

**`appshot.py` pins `nbapp.screen_size()` to the render size** (fixed after
three separate audits each reported the same false positive as an app bug). Apps
legitimately ask the screen how big it is — to size a scrim, centre a modal, or
choose a tile size — and unpinned they got the developer's 1080p monitor while
being drawn into a 1024x740 image. That is what made g2048's board look clipped,
gave illustrator a phantom mat rail, and threw confirm cards off the edge. If you
write your OWN render driver rather than using appshot, pin it yourself:
`import nbapp; nbapp.screen_size = lambda: (w, h)` before constructing.

**Measure with height-for-width, or believe the render.** If you write your own
size probe, use `get_preferred_height_for_width(w)`, never the width-agnostic
`get_preferred_height()`. A wrapping label asked for its height with no width
reports the tall single-column figure GTK never actually uses: `packages`
measured 1328 that way while laying out at 488 for a 1024 width, and the render
fit fine. The width-agnostic number invents overflow bugs that do not exist.

**Static first-render screenshots are not enough.** Write a small driver script
that constructs the app, populates realistic data, and calls its own methods to
reach the states a user actually sees, then render each state:

* every tab / sidebar section / stack page (`_editor_stack.set_visible_child_name(...)`)
* **populated** state, not just the empty state (create records, open a file,
  add a track/recipe/event/contact)
* every dialog the app can open (find the `_dialog`/`_prompt`/`_confirm` helpers)
* error/edge states: no data, one item, a very long name, a huge number
* any pane behind a button you can invoke directly

Use `uishot.shot_window(win, w, h, path, after_show=cb)` when the state must be
set **after** `show_all` (Gtk.Stack ignores `set_visible_child_name` before it is
shown — this has bitten us).

## What counts as a defect

Judge like a design-obsessed product reviewer, then verify in the render:

* **Layout**: clipped/unreachable content; dead vertical or horizontal space; a
  fixed bottom bar floating mid-window; misaligned baselines; inconsistent
  padding between sibling rows; a control jammed against a window edge; a
  column that squeezes another to nothing; overlapping widgets.
* **Text**: truncated labels ("…Application"); typos, doubled words, sentence
  fragments, jargon a normal person would not know ("PARTUUID", "vexpand",
  "buffer", "stderr", "JSON"); inconsistent sentence case vs Title Case;
  missing empty-state guidance; a button whose label does not say what it does.
* **Translation catalogs** (`de/lang_<code>.json`) have one house format, so
  diffs stay readable: `json.dumps(data, ensure_ascii=False, indent=1,
  sort_keys=True) + "\n"`. Sorted keys, one-space indent, and a trailing
  newline. Keys are the exact English source strings — never add or trim
  whitespace on one (some legitimately begin with a space, e.g. `"About "`).
* **Colour**: the design language is papertone surfaces (#FCFBF8 / #F8F7F2),
  ink text (#1A1916), taupe hairlines (#C9C4B6 / #C4BFB1), serif Newsreader for
  editorial moments, and **ONE** signage-red accent **#C8341E reserved for
  today/alerts/the single primary action**. Two competing reds on one screen is
  a defect. A heavy black rule across a data table is a defect (use a #C9C4B6
  hairline). Random new colours are defects.
* **Iconography**: **there is NO colour-emoji font on the guest** — any literal
  emoji (🔇 🌍 👋) renders as a tofu box. Use `nbicons.pixbuf(name, size, colour)`
  or plain words instead. Grep your apps for non-ASCII pictographs.
* **Behaviour**: a control that does nothing; a state that cannot be exited; a
  destructive action with no confirmation; data that silently fails to save.

## KNOWN GOTCHAS — read these, they have each cost hours

* **A `Gtk.TextTag` left/right-margin REPLACES the TextView's own margin, it
  does not add.** Bake the base margin into every tag.
* **GTK3 propagates `vexpand` UP from descendants** — a fixed bottom bar
  containing a `Gtk.Scale` becomes an expanding child and floats mid-window.
  Pin such bars with `set_vexpand(False)`. (This was the Music bug.)
* `Gtk.TreeViewColumn` needs `sizing=FIXED` for `ellipsize` to work; the default
  GROW_ONLY makes it grow instead of ellipsizing.
* `Gtk.Editable.insert_text` in PyGObject is 2-arg `(text, position)`.
* `de/calendar.py` shadows Python's stdlib `calendar` on the guest PYTHONPATH —
  never `import calendar` or use `time.strptime` in a DE module.
* A `Gtk.ScrolledWindow`'s Viewport bin-window cannot be styled by CSS; paint in
  a `draw` handler on a windowless child instead.
* In the uishot harness only, `Gtk.Scale` fill renders host-blue. That is a
  harness artifact — the guest is correct. Do not "fix" slider colours.
* **The 1px grey `#A29E9B` frame around every `ScrolledWindow` is a harness
  artifact. SETTLED — do not re-open it.** Three separate agents have now
  "found" it. Yes, `Gtk.Viewport` defaults to `SHADOW_IN` and styles as
  `frame`; but Papertone declares no `.frame` border, so nothing draws on the
  guest. Measured on a bare ScrolledWindow, same pixel: under the harness
  `#A29E9B`, under `GTK_THEME=Papertone` `#FCFBF8` paper. It is the HOST theme
  leaking (Papertone loads at priority 500 and only sets `background-color`).
  Reproduce before doubting: `GTK_THEME=Papertone XDG_DATA_DIRS=<overlay>/usr/share`.
  Corollary: the per-widget `set_shadow_type(NONE)` calls in finder.py and
  installer.py were fixing this artifact; they are harmless, and their comments
  are what keep sending people down this path. Do NOT add a `.frame` rule to
  the theme to "fix" it.
* Pango per-glyph fallback to DejaVu works for symbols (✓ ▸ → ● ♪). Only true
  emoji are tofu. Don't chase symbol tofu.

## Rules of engagement

* **Fix at source, minimally.** Match the surrounding code's style, naming and
  comment density. No refactors, no new dependencies, no invented design
  language, no renaming things that work.
* **Only touch the apps assigned to you.** Shared files (`nbapp.py`,
  `nbicons.py`, the theme `gtk.css`) are edited by the lead only — if you need a
  change there, report it instead of making it.
* **Do not manufacture churn.** If something is already good, say so and move
  on. A previous 12-hour run already polished a lot; your value is the things it
  missed, especially at small screen sizes and in populated/dialog states.
* **Verify every fix**: re-render the same states and `Read` the PNG to confirm
  the pixels changed the way you intended, and run

  ```bash
  python3 -m py_compile <file>
  DISPLAY=:0 python3 /home/ben/Documents/notebookos-linux/tools/construct_all_host.py
  ```

  Construct-all must stay green (it catches launch crashes).

## What to report back

A precise list. For each finding:

`app · state/size where it shows · what is wrong · root cause · the fix you made
· how you verified it (which PNG, what changed)`

Separate **FIXED** from **FOUND BUT NOT FIXED** (with why). Include a final
line: the min-size numbers for your apps after your fixes. Be concrete and
honest — "swept clean" is a fine result if it is true, but say what states you
actually rendered to justify it.
