# 07. Planning and records

Eight applications keep ongoing records. All of them are single-store
applications: each owns one file that is rewritten on every change, and none of
them has Open, Save or Save As commands. See
[05. How applications work](05-how-applications-work.md).

Six of them also supply a card on the desktop board. See
[03. The desktop](03-the-desktop.md).

---

## Tasks

Three columns: a Lists sidebar, the task list, and a Schedule rail.

**Lists sidebar** holds three built-in views and any lists the user creates.

| View | Contents |
|---|---|
| Today | Tasks due today |
| Upcoming | Tasks due later |
| Inbox | Tasks with no list |

**The task list** shows the tasks in the selected view. A quick-add row at the
top adds a task without opening a dialog. A task carries a title, a due date,
and a completion state.

**The Schedule rail** on the right shows today's events and a small month
calendar. The events come from Calendar; **View > Look for New Events** picks up
anything added in Calendar since the Tasks window was opened.

| Menu command | Effect |
|---|---|
| File > New Task | Adds a task |
| File > New List | Creates a list |
| View > Clear Completed | Removes completed tasks. Greyed out when there are none. |
| Lists > Remove List… | Removes the selected list after confirmation, and moves its tasks to Inbox |

Removing a list never deletes the tasks in it.

The desktop board's Tasks card shows the checklist with a count of how many are
done.

---

## Calendar

A month grid with a sidebar. The sidebar holds a small month calendar, the list
of named calendars, and a New Event control.

**Views** — Day, Week and Month, selected from the View menu.

**Navigation** — the Go menu holds Today, Previous and Next. The unit changes
with the view: in Month view they move by month, in Week view by week, in Day
view by day.

**Named calendars** — every event belongs to a named calendar. Each calendar has
a colour, and each can be shown or hidden from the View menu. A new installation
has one calendar, named "Personal".

| Menu command | Effect |
|---|---|
| File > New Event… | Creates an event |
| File > Add a Shift… | Creates a work shift |
| File > New Calendar… | Creates a named calendar |

Events are stored in a single file that the Tasks application and the desktop
board both read, so an event added here appears in both without further action.

---

## Academics

Academics holds one term's classwork. Three views are selected from the
sidebar.

### Notes

The lecture-note editor. The sidebar lists classes and, under each, its
numbered lectures. The main area is the note canvas with a format bar: paragraph
style, bold, italic, highlight, bulleted and numbered lists. A live word count
and save state are shown.

A new lecture is filled in automatically: the class it belongs to is the one
meeting now or next according to the timetable, and its number is the one after
the last lecture taken for that class.

### Schedule

The week as a timetable. Each class is drawn in its own colour at the hours it
meets. Today's column is marked.

Classes are entered here — name, colour, and the days and times they meet.

### Homework

Every assignment against the day it is due, grouped by how soon that is.
Overdue assignments are marked in red; that is the only use of red on this
screen.

The desktop board's Classes and Homework cards read the same file.

---

## Contacts

An address book in two panes: a searchable, alphabetically sorted list on the
left and the selected contact's card on the right.

A card holds name, phone, email, address, birthday and notes.

The book is written on every add, edit and delete. **Export to PDF** writes a
copy of the whole book into `Documents`.

---

## Accounting

A single-account cash book. Each entry is either a debit (money out) or a
credit (money in), and the ledger keeps a running balance accurate to the cent.

The left panel shows the current balance and the totals for the period. The
right panel shows a balance-over-time chart above the transaction table, with an
entry form beneath it.

| Action | Method |
|---|---|
| Add an entry | Fill in the form and submit it |
| Edit an entry | Click the row; it becomes editable in place |
| Delete an entry | Select the row and press `Delete`, then confirm |

Deletion is confirmed because it cannot be undone. The balance, the chart and
the stored file are all recalculated afterwards.

**Export to PDF** writes the ledger into `Documents`.

The desktop board's Accounts card shows the balance and the most recent
entries.

---

## Cookbook

A recipe library in two panes: category chips and a recipe list on the left, the
recipe itself on the right.

A recipe page has a caption band, a category, a title, a description, a strip of
Time / Makes / Effort fields, and Ingredients and Method columns.

| Menu command | Effect |
|---|---|
| File > New Recipe | Adds a recipe |
| File > Export to PDF | Writes the current recipe to `Documents` |

The library is written on every edit.

---

## Meal Planner

The week's meals: three meals down, seven days across.

Each slot holds one of three things:

- A recipe already in Cookbook.
- A takeaway.
- A line of the user's own text, for anything else.

A slot stores the recipe's title rather than its position in Cookbook, so
adding, reordering or deleting recipes never changes what the plan says. A slot
naming a recipe that no longer exists still reads correctly; it stops linking to
the recipe page.

The desktop board's Meals card shows today's breakfast, lunch and dinner.

---

## Workout

Workout records sets and reps against a daily goal.

The exercise list holds each exercise with its goal — for example, Push-ups, 5
sets of 10. Finishing a set is logged with one click, and the number of reps
actually completed is recorded, so a short set is recorded as a short set.

The screen answers one question first: whether today's work is done. Today's
progress is the largest element on the screen. The week beside it shows which
days were completed.

### Streaks

A streak counts consecutive days on which the goal was fully met. Partial days
do not count.

A past day keeps the goal it was performed against. Changing the daily goal
therefore does not rewrite history or break an existing streak.

### Desktop card

The Workout card on the desktop board is off by default and is turned on with
**View > Show on Desktop** in the application, or in Widget Settings.
