# 05. How applications work

Every application in Notebook OS uses the same window structure, the same menu
conventions and the same rules for storing data. Learning one application
teaches the behaviour of the others.

## The application window

An application fills the screen beneath the menu bar. There are no title bars,
no minimise or maximise buttons, and no overlapping application windows.

While an application is open, the menu bar shows:

- The logo, with the system menu, unchanged from the desktop.
- The application's name.
- The application's own menus.
- The clock and date.

## Closing an application

Any of the following closes the current application and returns to the desktop:

- Pressing `Esc`.
- Clicking the logo in the menu bar.
- Choosing **File > Close**.

`Esc` never deletes anything, in any application, anywhere in the system. It
leaves the current screen: it closes a dialog, cancels an edit in progress, or
closes the application. Removing something is always `Delete`, or a named
command in a menu.

## Menus

### Ellipsis

An ellipsis on a menu item means the command will ask something before it acts —
it opens a dialog, a file picker, or a confirmation.

| Item | Behaviour |
|---|---|
| `Open…` | Opens a file picker |
| `Save As…` | Asks for a name |
| `Delete Chapter…` | Asks for confirmation |
| `Save` | Writes immediately |
| `Export to PDF` | Writes to Documents immediately |
| `Close` | Closes immediately |

`Export to PDF` and `Export…` are therefore different commands, not
inconsistent spellings of one command. The first writes a file to Documents
without asking; the second opens a picker.

### Two kinds of File menu

Which File menu an application has depends on how it stores data.

**Document applications** — Writer, Novel, Screenplay, Illustrator, Sequencer,
GBA SDK. The user creates named files and keeps as many as wanted.

```
New              Ctrl+N
Open…            Ctrl+O
Save             Ctrl+S
Save As…         Ctrl+Shift+S
Export to PDF
Print…
Close            Esc
```

**Single-store applications** — Tasks, Calendar, Academics, Journal, Contacts,
Cookbook, Meal Planner, Accounting, Workout, Language, Music, Maps. The
application owns one continuously saved store, and the user does not manage
files.

```
New <item>
Delete <item>…
Export to PDF
Print…
Close            Esc
```

Single-store applications have no Save, Save As, or Open commands. There is
nothing to save: every change is written as it is made. A Save command in these
applications would do nothing, so none is offered.

### Keyboard shortcuts

Every keyboard shortcut an application binds is printed on the matching menu
item. An application does not print a shortcut it has not bound.

### Unavailable commands

A command that exists but cannot run at this moment stays on the menu and is
greyed out. Commands are not removed and re-added according to context, because
that changes the position of everything else on the menu.

### Menu order

`File`, `Edit`, `View` in that order where present, then any menus specific to
the application — `Cook`, `Library`, `Track`, `Layer`, `Transport` — then
`Help`.

`Edit` carries Undo and Redo first in applications that have an undo history,
followed by Cut, Copy, Paste and Select All.

## Empty applications

Every application opens empty on a new installation. No application contains
sample documents, example records, demonstration data, or placeholder content.

An empty application shows a short statement of what it holds and names the
command that creates the first item. It does not show a blank screen.

## Saving

**Single-store applications** write on every change. The store is the only copy;
there is no separate document.

**Document applications** write the named file when Save is used, and
separately keep a session recovery snapshot that is updated continuously. If
the computer is switched off with unsaved changes, reopening the application
restores the working state from the snapshot; the document file on disk is
unchanged until Save is used.

Saved documents go to `Documents` unless another folder is chosen.

## Printing and PDF export

Applications that produce a printable page offer **Print…** and, where
applicable, **Export to PDF**. Both render the document with the same page
layout. Export writes a PDF into `Documents`. See [12. Printing](12-printing.md).

## The application list

| Application | Kind | Purpose |
|---|---|---|
| Writer | Word Processor | Formatted documents on a page layout |
| Novel | Word Processor | Long manuscripts in chapters |
| Screenplay | Scriptwriting | Screenplays in standard format |
| Journal | Diary | Dated journal entries |
| Academics | School | Class notes, timetable and homework |
| Tasks | Productivity | Task lists and schedule |
| Calendar | Productivity | Events by day, week and month |
| Contacts | Utility | Address book |
| Accounting | Finance | Cash ledger |
| Cookbook | Reference | Recipes |
| Meal Planner | Cooking | A week of meals |
| Workout | Health | Daily sets and reps against a goal |
| Calculator | Utility | Scientific calculator |
| Illustrator | Graphics | Pixel-art editor |
| Sequencer | Audio | Multitrack music studio |
| Video Editor | Video | Video editing and export |
| Media Viewer | Media | Images and video playback |
| Music | Music | Music library and player |
| E-book Reader | Reader | EPUB and PDF reading |
| Language | Education | Offline language courses |
| Maps | Reference | Offline street maps |
| 2048 | Game | Sliding-tile puzzle |
| GBA Emulator | Game | Game Boy, Game Boy Color and Game Boy Advance emulation |
| GBA SDK | Development | Game creation, producing a real cartridge image |
| Packages | System | What is installed on this computer |
| Settings | System | System configuration |
| System Monitor | System | Running programs, processor and memory |
| Terminal | Utility | Command shell |
| USB Writer | System | Writes a disk image to a USB stick |
| Install Notebook OS | System | Installs the system to a disk |
