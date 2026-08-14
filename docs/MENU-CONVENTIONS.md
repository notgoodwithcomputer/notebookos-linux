# Menu conventions

The rules the whole OS follows, so a person who learns one app has learned the
others. Derived from what 24 apps already do; where they disagreed, the rule
below picks the reading that is true to what the control actually *does*.

## 1. The ellipsis means "this will ask you something"

`…` on a menu item promises a dialog, a picker or a confirm before anything
happens. No ellipsis promises the action happens immediately.

    Open…              opens a file picker              -> ellipsis
    Save As…           asks for a name                  -> ellipsis
    Delete Chapter…    asks you to confirm              -> ellipsis
    Save              writes now                        -> none
    Export to PDF     writes to Documents now           -> none
    Close             closes now                        -> none

**Do not unify "Export to PDF" and "Export to PDF…" by picking one.** They are
different promises. Check what the code does and label it accordingly: an
export that writes straight to `$NB_HOME/Documents` takes NO ellipsis; one that
opens the shared picker takes one.

## 2. Two legitimate File menus, chosen by the app's data model

There is no single File menu, and forcing one would be a lie about how the app
stores things. Which one an app gets is decided by ONE question: does the user
have *documents* they name and keep, or ONE store the app owns?

**A. Document apps** (Writer, Novel, Screenplay, Illustrator, Sequencer…) — the
user makes files, names them, keeps several:

    New    Ctrl+N
    Open…    Ctrl+O
    ---
    Save    Ctrl+S
    Save As…    Ctrl+Shift+S
    ---
    Export to PDF   (or Export…, per rule 1)
    Print…
    ---
    Close    Esc

**B. Single-store apps** (Academics, Tasks, Journal, Workout, Calendar,
Contacts, Cookbook, Accounting…) — one autosaved store, no file management.
They must NOT offer New/Open/Save/Save As: there is nothing to save, and a Save
that does nothing is worse than no Save at all.

    New <Thing>            the app's create action(s)
    Delete <Thing>…
    ---
    Export … to PDF        only if the app can produce one
    Print…
    ---
    Close    Esc

An app in group B whose File menu currently offers Save or Save As has a bug:
remove the item rather than wiring it to the autosave.

## 3. Accelerators are part of the label

Written with FOUR spaces before the key, e.g. `Close    Esc`, `Save    Ctrl+S`.

The gap comes AFTER a label. A run of spaces at the START of a label is
padding, not a separator: an item that carries its own state is written
`"✓ " + label` when on and `"    " + label` when off, four spaces being
exactly the width of a tick plus its space, so the words stay in one column.
Anything reading a label for its accelerator must strip the leading padding
before it splits, or an unticked item parses as a nameless command bound to a
key named after itself.
If an app binds a key, the menu item must show it — a shortcut nobody can
discover is not a feature. Conversely, never print an accelerator the app does
not actually bind.

## 4. Menu titles

`File`, `Edit`, `View` in that order when present, then any app-specific menu,
then `Help` if it exists. `Edit` carries Undo/Redo first when the app has an
undo history, then the standard Cut/Copy/Paste/Select All.

An app-specific menu named for the app's own subject (`Cook`, `Library`,
`Track`, `Layer`, `Transport`) is CORRECT and should be kept — it is what makes
the app legible. Do not flatten these into `Tools`.

## 5. Disabled, never absent

An action that exists but cannot run right now stays visible and greys out (a
`None` callback). Removing it makes the menu shift under the user's hand and
hides what the app can do.

## 6. Wording

Title Case for menu items and window titles. Sentence case for body text,
labels, tooltips and empty states. A menu item names the OUTCOME, not the
mechanism ("Look again", not "Rescan").
