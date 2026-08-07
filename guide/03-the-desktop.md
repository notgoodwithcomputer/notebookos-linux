# 03. The desktop

The desktop is what is shown when no application is open. It consists of three
things: the menu bar across the top of the screen, the desktop board filling the
area beneath it, and a Finder window floating over the board.

## The menu bar

The menu bar is present at all times, on the desktop and in every application.
It is 46 pixels tall and spans the full width of the screen. Windows do not
overlap it.

Its contents from left to right:

| Item | Contents |
|---|---|
| Logo | The system menu |
| Application name | On the desktop this reads **Finder** and opens a list of folders. In an application it shows the application's name. |
| Application menus | The menus belonging to whatever is currently active |
| Clock and date | At the right-hand end, with the battery percentage on a portable computer |

### The logo menu

The logo menu is the same in every application.

| Item | Action |
|---|---|
| About This Notebook… | Shows the system version and basic hardware details |
| System Settings… | Opens Settings |
| System Monitor | Opens System Monitor |
| Terminal | Opens Terminal |
| Sleep | Turns off the display and locks the screen |
| Restart… | Restarts the computer, after confirmation |
| Shut Down… | Switches off the computer, after confirmation |

### The Finder button

On the desktop, the button next to the logo reads **Finder** and lists folders,
not applications. Each item opens a Finder window on that folder.

Applications · Home · Desktop · Documents · Music · Pictures · Videos · Trash

In an application, this button shows that application's name.

### The desktop menus

While the desktop is active, the menu bar carries four menus.

**File**

| Item | Action |
|---|---|
| New Finder Window | Opens an additional Finder window |
| Open Terminal | Opens Terminal |
| About This Notebook… | Shows the system version and hardware details |

**Edit**

| Item | Action |
|---|---|
| Copy Date & Time | Copies the current date and time to the clipboard |
| Show Clipboard… | Displays the current clipboard contents |
| Clear Clipboard | Empties the clipboard |

The Edit menu carries no Cut, Copy or Paste commands, because the desktop
contains no editable text field for them to act on.

**View**

| Item | Action |
|---|---|
| 24-Hour Clock | Switches the menu bar clock between 12-hour and 24-hour display |
| Show Seconds | Shows or hides seconds in the clock |
| Show Date | Shows or hides the date beside the clock |
| About This Notebook… | Shows the system version and hardware details |

Each of the three toggles shows a check mark when it is on, and each is
remembered across restarts.

**Label**

Six coloured labels, plus **Edit Labels…** for renaming them. The labels start
unnamed. The chosen label is remembered for the session and across restarts.

### Menu behaviour

A menu that is left open with no interaction closes by itself after 15 seconds.
Moving the pointer over the menu restarts that timer.

## The desktop board

The desktop board fills the area under the menu bar. It shows current
information read from the applications that produce it. Nothing on it is
seeded, sampled or estimated.

The board is a grid of eight cards:

```
+---------+---------+---------+----------+---------+
| Classes | Homework|  Meals  |          |         |
+---------+---------+---------+ Calendar |  Tasks  |
| Workout | Journal | Accounts|          |         |
+---------+---------+---------+----------+---------+
```

| Card | Contents | Source application |
|---|---|---|
| Classes | Today's classes with times and rooms | Academics |
| Homework | Assignments and their due dates | Academics |
| Meals | Breakfast, lunch and dinner for today | Meal Planner |
| Workout | Today's sets against the daily goal, by exercise | Workout |
| Journal | Whether today's entry has been written | Journal |
| Accounts | Cash balance and recent entries | Accounting |
| Calendar | The month grid with today marked, plus today's agenda | Calendar |
| Tasks | A checklist with a "done of total" count | Tasks |

Calendar and Tasks are fixed in place and cannot be turned off. The other six
are optional.

### Choosing which cards appear

Right-click the desktop board and select **Widget Settings…**. Each of the six
optional cards has a switch and a description of what it shows. A change takes
effect immediately; there is no need to close and reopen anything.

### Card behaviour

Cards do not scroll. Each one shows as many rows as it has room for and stops.
A card whose application holds no data shows a short statement of what it would
show, rather than an empty frame or a fabricated example.

## The backdrop

The desktop backdrop is a single flat colour and cannot be changed. There is no
wallpaper feature and no colour picker for it.

## Running applications

Notebook OS runs one application at a time in the foreground. Opening an
application hides the desktop board and the Finder window; closing it brings
them back.

Applications are opened from:

- The **Applications** folder in the Finder.
- The application icons on the desktop, if any have been placed there.
- Double-clicking a document, which opens the application that handles that
  file type.
- The logo menu, for Settings, System Monitor and Terminal.

Pressing `Esc`, or clicking the logo in an application's menu bar, closes the
application and returns to the desktop.
