# The Notification Centre

**Status:** normative for anything that wants to leave a message in the menu
bar. Landed 2026-08-13.

**Where it is:** `de/nbnotify.py` (the spool), `shell.Panel._notify_*` (the
surface), `nbapp.AppWindow.notify()` (how an app posts). Gate:
`tools/notify_selftest.py`.

---

## 1. Why it exists

One app, one process, fullscreen (Constitution §0.6). While somebody is reading
in the Ebook, the USB Writer finishing its stick has nowhere to say so: its own
status line is behind another window and its process may already be gone. The
menu bar is the only surface always on screen, so that is where the result goes.

That gap is the whole justification, and it is also the boundary of the
feature. **The notification centre is for what finishes while the person is
somewhere else.** Nothing else.

## 2. What may be posted, and what may not

| Post it | Do not post it |
|---|---|
| a stick written, a disc burned, a film exported | a document saved |
| a long job that failed, with why | a validation error on the field being typed in |
| something that needs the person to act later | anything already visible in the app's own status line |

Feedback about what somebody just did belongs where they are already looking —
`writer._flash`, the status chip, the inline card (Article IV §1). A
notification is the *heavier* form: it outlives the app, it marks the menu bar,
and it has to be cleared. Spending a shared surface on something that was
already on screen is how a tray becomes noise, and a tray that is noise is one
nobody reads.

**There is no banner and no sound.** Nothing in this OS appears over what
somebody is doing to announce itself. The spot on the bell is the arrival, and
it waits.

## 3. Posting

```python
self.notify(_t("The disc is written"), _t("It can be taken out."))
```

`AppWindow.notify(title, body="")` fills in the sending module and its display
name from the window itself. Anything that is not an `AppWindow` calls
`nbnotify.post(title, body, app=…, app_name=…)` directly.

- **Title is a headline** — the tray gives it one line and then ellipsizes.
- **Body is a line or two** — two lines, then ellipsized.
- Both are already translated by the caller: `_t()` at the post, not at the
  render. A stored message keeps the language it was posted in, which is the
  same rule as every window in this OS.
- It never raises and never blocks. A notification is a courtesy: an app whose
  disk is full still finishes reporting the job it was doing.

Clicking a message opens the app that posted it and takes the message away, so
`app` must be a real module in `de/`. When it is not, the row still dismisses
and its tooltip says so rather than promising a window that never appears.

## 4. The spool

```
$NB_HOME/.config/notebook/notifications/<stamp>-<pid>-<n>.json   one message
$NB_HOME/.config/notebook/notifications-seen.json                the read mark
```

**One file per message, not one store.** There is no session bus here
(Constitution §0.2), so senders and the panel agree through the filesystem —
but unlike an app's own store this one has *many writers*: the Disc Burner and
the Video Editor can finish inside the same second. A single JSON file
read-modify-written by N processes loses whichever write lands second. Each
sender only ever creates its own file, and `os.replace` onto a name nobody else
uses is atomic. The panel is the only process that ever deletes one.

The file **name** carries the timestamp, zero-padded, so the directory sorts
chronologically without opening anything — which is what lets pruning stay
honest about a record it cannot parse.

**Read and write are separated on purpose.** `load()` never deletes. Expiry
FILTERS on the read path and only unlinks on the write path, where a person has
just done something. This OS has already produced its worst defect the other
way: opening and closing an app destroyed a damaged store with no user action
at all. A damaged record is skipped and the rest of the tray still loads
(Article II §3).

Bounds: `MAX_KEEP = 64` messages, `MAX_AGE_S = 7 days`, `MAX_TITLE = 120`,
`MAX_BODY = 400` characters.

## 5. Unread

One read mark, not a flag per record. Opening the tray sets it to *now*, so
everything in view stops counting as new; a message that lands while the tray is
open is later than the mark and is still new when it closes.

The alternative — rewriting every file to flip a flag — would also mean the
panel rebuilding the list under the pointer, which resets the scroll of the
thing being read (Article III §2).

The count is **not** drawn on the bar. The mark carries a signage-red spot and
darkens from the muted register to full ink (two carriers, per Article VII §3);
the exact number is in the tooltip, which is also the accessible name. That
keeps the mark one fixed size, so the cluster beside it never moves — the same
reasoning as `Panel._pin_widths`.

## 6. The surface

- A menu title like File or View: same `.menuitem` chrome, same hover swatch,
  same dropdown drawn inside the panel window.
- The card rests its **right edge on `screen_w - RIGHT_MARGIN`** — the line the
  clock, date and battery already end on, not the bare screen edge (§E2).
- One width, full or empty (`.nbn { min-width }`): the tray is a fixed surface
  in this bar, not a box that shrinks to its contents.
- Heading row 48px, message rows 64px rendered — declared as interiors of 32
  and 48 plus an 8px padding pair, both on the open ladder (§E3.2). They are
  **floors**: a second line of body, or a script with taller line boxes, grows
  the row. Pinning text lines to exact multiples of the unit would clip
  Devanagari and CJK, and a grid bought with cut-off Chinese is a bug in eleven
  languages, not a grid.
- The idle timeout is 45s, not the menus' 15s: this is a surface somebody
  *reads*, and a card that vanishes mid-sentence is a defect, not a safeguard.
- Empty state names the surface and what fills it, in sentence case (§4).
- Clear All appears only when there is something to clear — this bar has no
  permanently dead controls.

## 7. Gate

`tools/guestrun.sh python3 tools/notify_selftest.py` — 95 checks over the store
(atomicity, all-or-nothing loading, expiry deleting only on the write path,
bounds, id traversal, unread arithmetic, poll cost) and the surface (where the
mark sits, where the card rests, rows, dismissal, empty state, relative time).

Red-proofed against nine sabotages of the real source, each recorded as making
it fail: `load()` giving up on the whole tray for one damaged record; the id
guard removed (the victim file was measurably deleted); a constant poll key; the
mark packed on the far side of the clock; the card not aligned to the margin; an
unconditional repaint every tick; the empty card shrinking to its text; the
dismiss cross clearing everything; the two idle spans collapsed into one.

Two of those found the *test* wrong rather than the code: the traversal check
and the position check both passed with the guard removed, for reasons that had
nothing to do with the guard. A refusal that cannot be told apart from a miss is
not evidence of a refusal.
