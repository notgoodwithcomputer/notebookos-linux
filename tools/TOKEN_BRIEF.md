# Design-token conformance brief

You are conforming a small set of Notebook OS app modules to the OS design
tokens. Read this whole file before editing anything.

## The problem you are fixing

Every app in `de/` embeds its own CSS. Measured across the 58 modules: **255
distinct colours, 34 font sizes, 11 border radii — including 220 hardcoded
`border-radius: 2px`**. App CSS loads at `STYLE_PROVIDER_PRIORITY_APPLICATION`,
which BEATS the Papertone theme, so the OS-wide geometry the theme sets is
overridden in hundreds of places. The result is rounded controls in the widgets
an app didn't style and square ones everywhere it did — often in the same
window. No single file looks wrong. The system does.

This is **not a repaint**. Every token is drawn from what the OS already uses
most. Conforming should change nothing about a screen's intent and everything
about its consistency.

## The tokens

`tools/design_tokens.py` is the single source of truth — read its header. In
short:

- **Colour**: the 18 papertone values listed in `COLOURS`. Nothing else.
- **Radius**: `0` (square by intent) · `4` (small marks inside text: chips,
  checkboxes, badges) · `8` (CONTROLS: buttons, entries, spins, toggles, tabs) ·
  `12` (CONTAINERS: cards, dialogs, popovers, grouped lists) · `100` (pills,
  switches, slider knobs).

## Your scope — colour and radius ONLY

**Do not change `font-size`.** Type is a separate pass because it moves layout;
the orchestrator is handling it centrally. If the checker reports font-size
drift, leave it.

## How to work

1. `python3 tools/design_tokens.py <yourfile.py>` lists every off-token value
   with a line number and a suggested token.
2. The suggestion is the NEAREST token, which is right for colour almost always
   and for radius only sometimes. **Radius is a judgement about what the element
   IS.** A 2px radius on a button becomes 8; on a small badge, 4; on a full-width
   band, 0. Read the selector and the surrounding code before choosing.
3. Edit the CSS. Keep every rule's structure, selectors and comments intact —
   you are changing values, not rewriting stylesheets.
4. Where a colour is genuinely carrying MEANING that the palette cannot express
   — a drawing app's spectrum, per-course identity badges, chart series, a
   syntax highlighter, a load meter — **keep it** and say so in your report. The
   checker already exempts saturated colours in known files; trust your reading
   over the checker where they disagree, and explain.

## The accessibility constraint — read this twice

`nbapp.py` implements a **high-contrast mode** as a colour substitution table
(`_HC_TEXT` for `color:`, `_HC_LINE` for borders). Its VALUES were chosen for
measured WCAG ratios; its KEYS enumerate the tones apps actually use. Two
consequences for you:

1. **Never touch `nbapp.py`.** Rewriting those values would silently destroy a
   measured accessibility feature. It is not in anyone's file list.
2. **Only conform a colour onto a token the table already covers.** The tokens
   with a mapping are `#9A9484`, `#8A857A`, `#6E695E`, `#C9C4B6`, `#D7D2C5`,
   `#B3AD9E` (text tier) plus the line tones in `_HC_LINE`. Ink, paper, the
   rails and the accent are correctly unmapped — ink already passes at 16.99:1,
   backgrounds are not contrast-boosted, and the accent is deliberately left
   alone.

If conforming some drift tone would land it on a token with no mapping AND it is
used as text, **stop and report it** instead of changing it. A tidying change
that quietly removes contrast boosting from a caption is the worst possible
outcome of this work.

## Hard rules

- **App CSS lives inside Python `b"""..."""` byte literals.** A non-ASCII
  character there is a `SyntaxError` that takes the whole app down, and so is a
  stray triple quote. After editing, run:
  `python3 tools/ascii_css_check.py` — it must say "clean".
- Run `python3 -m py_compile <yourfile.py>` on every file you touch.
- Do **not** touch: `nbapp.py`, the theme
  (`usr/share/themes/Papertone/gtk-3.0/gtk.css`), or any file not in your list.
  Other agents own the other files; overlapping edits will collide.
- Do not change layout properties (padding, margin, min-width/height, spacing).
- Do not "improve" anything beyond token conformance. If you spot a real defect,
  report it; do not fix it.

## Verify before you report

For each app you changed, from the repo root:

```
tools/guestrun.sh python3 tools/construct_one.py <app>    # must print "OK <app> constructs"
python3 tools/design_tokens.py <yourfile.py>              # colour+radius should be ~0
python3 tools/ascii_css_check.py                          # must say clean
```

Use `construct_one`, **not** `construct_all_host.py`. Several agents are working
in parallel and each full sweep builds 37 windows on the shared display; the
orchestrator runs the full sweep centrally once everyone is done.

If your app has a selftest (`tools/<app>_selftest.py`), run it.

A visual before/after is strong evidence and cheap:

```
NB_ACCEL=1 tools/guestrun.sh python3 tools/appshot.py /tmp/before 1024x740 <app>
# ...make your edits...
NB_ACCEL=1 tools/guestrun.sh python3 tools/appshot.py /tmp/after 1024x740 <app>
```

Compare them. The layout must be identical; only corners and tones change. If
anything moved, you changed something you shouldn't have.

## Report back

- counts: colours changed, radii changed, per file
- every colour you deliberately KEPT off-palette, and why
- any radius where the nearest token was wrong and you chose differently, and why
- verification output (construct count, checker totals, selftest results)
- any real defect you noticed and did **not** fix
