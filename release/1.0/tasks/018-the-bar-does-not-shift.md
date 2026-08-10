# 018 — The menu bar does not shift, in any language

**Lane:** C (i18n) · **Streams:** S1 truth defects, S4 polish
**Status:** CLOSED

Second find from the same sweep as 017: `shell.py` — the panel, 1337 lines, the
one surface on screen at all times in all seventeen languages — had **no suite
importing it**.

## The defect

`Panel._pin_widths` reserves, for each read-out in the right cluster, the width
of the widest text it can ever show, so the cluster does not jitter as the
clock ticks and the date rolls over. Its docstring is emphatic and correct
about why: *"a single hand-picked sample kept missing the real maximum by a
pixel … and a pixel is exactly the drift this method exists to prevent."*

It measured with `Gtk.Label.create_pango_layout` — a **raw Pango call**, which
does not pass through nbi18n the way `set_markup` does. So it measured the
English sample and the label displayed the translation. `set_size_request` is a
MINIMUM, so the label grew past its reservation and the cluster moved.

Measured before the fix, **eight of the seventeen languages were over**:

| lang | over by | widest date |
|---|---|---|
| es | +25px | Dom 28 de mayo |
| it | +20px | Dom 28 maggio |
| zh | +12px | 10月28日 周一 |
| el | +10px | Παρ 28 Μαΐου |
| tr | +10px | Cum 28 Mayıs |
| pt | +6px | Dom 28 maio |
| fr | +5px | Sam 28 mars |
| yi | +4px | שבת 28 אָקט |

English was right by luck — it is the language the samples are generated in.

The fix is one call: measure `_t(s)`, the string that will actually be shown.

## What I checked and was wrong about

I expected the date to be **untranslated**: `_tick` builds it with
`time.strftime("%a %-d %b")` and no `_t()`, and the image ships only the C and
en_US locales (`BR2_ENABLE_LOCALE_WHITELIST="C en_US"`), so strftime can only
ever write English names. It is translated, and well — nbi18n wraps
`Gtk.Label.set_markup` and `set_tooltip_text`, and `_t` falls through to
`_date_lookup`, which reorders as well as translates: `8月6日 木` in Japanese,
`2026年8月6日 星期四` in the Chinese tooltip. Worth recording as a thing that
already works.

## Gate

`tools/panel_cluster_selftest.py`, 10 checks. It builds a **real Panel** and
measures against the real domain rather than a sample: all 366 days of a leap
year through the bar's own `%a %-d %b`, and all 1440 minutes of the day in each
of the four clock forms (12/24 hour × seconds on/off), plus every battery
reading 0–100 with and without the charging suffix. "Widest possible" can only
mean the maximum over what can actually be shown.

**Each language runs in its own process.** nbi18n fixes the language at import
and caches its date regexp on first use, so switching in-process measures the
first language seventeen times — measured, and it reported English's 67px for
all seventeen, which would have made this suite a very convincing way to
measure nothing.

It also asserts the date really is translated. Without that, every width above
would agree with English for the wrong reason.

**Red-proof, two mutations:**

| mutation | result |
|---|---|
| measure the untranslated sample (the shipped code) | 1 fail, naming el/eo/es/fr/it/pt/tr/zh |
| one hand-picked sample instead of the whole domain | 1 fail, and it catches **en** too, +1px |

The second is the docstring's own claim about hand-picked samples, now enforced.

## One check I dropped, deliberately

I wanted "the whole bar fits 1024px". The panel lays out in a `Gtk.Fixed`,
which does not propagate a child's width, so `get_preferred_width()` returns
the screen the window spans — 1920 in the harness — and says nothing about the
content. It duly "failed" for all eighteen languages, which was my measurement
being wrong, not the panel. Replaced with the cluster's reserved width against
a third of a 1024px bar: that is the number that actually grew, and English
uses 160px of the 341px budget.
