# 16. Interface conventions

The interface follows one set of conventions throughout. This chapter records
them, so that behaviour seen in one application can be relied on in the others.

## Colour

The interface is drawn as ink on paper. Surfaces are warm off-whites; text is
near-black rather than pure black.

| Use | Value |
|---|---|
| Desktop backdrop | `#DED4C2` |
| Card and panel surfaces | `#F1EEE6`, `#F8F7F2`, `#FCFBF8` |
| Hairline frames and dividers | `#C9C4B6`, `#B3AD9E` |
| Text | `#2A2620`, `#2E2A24` |
| Accent | `#C8341E` |

The accent colour has one job on each screen and is not used decoratively.
Examples: today's date in the month grid, an overdue assignment in Academics,
the destructive button in a confirmation, the active state of a running
slideshow.

The desktop backdrop is a single colour and cannot be changed.

## Type

| Face | Use |
|---|---|
| Nimbus Sans | The interface: menus, labels, controls, lists |
| Liberation Serif / Newsreader | Editorial text: journal entries, manuscript bodies, calendar event titles |
| Liberation Mono | Terminal, and screenplay pages |
| Noto Sans CJK | Chinese, Japanese and Korean text |
| Noto Sans Devanagari | Hindi text |

Text is rendered with grayscale anti-aliasing and stem darkening, which
compensates for the lightening effect that anti-aliasing has on thin letter
strokes at small sizes.

## Window structure

Every application window has the same structure:

1. The menu bar across the top of the screen, 46 pixels tall.
2. The application's content filling everything below it.

There are no title bars, no window controls, no overlapping application
windows, and no taskbar. One application is in the foreground at a time.

The Finder is the exception: it floats over the desktop board as a window that
can be moved and resized.

## The card

The repeated shape throughout the system is a card: a light surface with a
hairline frame, a header, and a body of rows.

A card header has a title on the left and a summary figure on the right. A card
row reads left to right as lead, name, value — for example a time, a task name,
and a due date.

Settings pages, the desktop board, and the detail panes of most applications are
all built from this one shape.

## Reading width

Content is capped at a comfortable reading width — approximately 1040 pixels for
Settings pages — rather than stretching a label and its control to opposite ends
of a wide screen.

The cap is a maximum, not a minimum. On a screen narrower than the cap, content
narrows to fit. No screen in the system requires horizontal scrolling.

## Minimum screen size

Every screen in the system is laid out to fit within 1024 × 740 pixels. No
control is placed where a 1024-wide or 1366-wide laptop panel cannot reach it.

## Menu rules

These are documented in full in
[05. How applications work](05-how-applications-work.md). In summary:

- An ellipsis means the command will ask something first.
- A command that cannot run now stays visible and greys out. Menus do not
  change length according to context.
- Every keyboard shortcut an application binds is printed on its menu item, and
  no shortcut is printed that is not bound.
- Menus run `File`, `Edit`, `View`, then any application-specific menu, then
  `Help`.
- An application-specific menu is named for its own subject — `Cook`,
  `Library`, `Track`, `Layer`, `Transport` — rather than being collapsed into a
  generic `Tools`.

## Wording

| Element | Case |
|---|---|
| Menu items, window titles | Title Case |
| Body text, labels, tooltips, empty states | Sentence case |

A menu item names the outcome, not the mechanism: "Look again", not "Rescan".

Interface text states what a control does. It does not address the user
directly, offer reassurance, or describe the system's intentions.

## Empty states

An application with no data shows a statement of what it holds and the name of
the command that creates the first item. It does not show a blank area, a
sample record, or placeholder content.

The same applies within an application: a card on the desktop board whose
application holds no data shows written empty-state text and keeps its place on
the board.

## Confirmations

A confirmation is required before anything destructive and irreversible: erasing
a file, emptying the Trash, deleting a ledger entry, restarting, shutting down,
writing to a USB stick.

A confirmation names what is about to happen to what, and its confirming button
is labelled with the action — "Shut Down", "Delete", "Erase" — not "OK".
Destructive confirming buttons are drawn in the accent colour.

## Keys

| Key | Meaning, everywhere in the system |
|---|---|
| `Esc` | Leave. Closes a dialog, cancels an edit, or closes the application. Never deletes anything. |
| `Delete` | Remove the selected thing. |
| `Ctrl+Z` | Undo the last action, in applications that keep an undo history and in the Finder. |

## Icons

Icons are drawn as vector glyphs at the exact size they are displayed, rather
than scaled from a fixed-size image, so they stay sharp at every size on
hardware without graphics acceleration.

No icon is used for two different things. Every application and every file type
has its own glyph.

Every icon-only button carries a tooltip naming what it does.
