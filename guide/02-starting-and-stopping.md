# 02. Starting and stopping

## Startup

When the computer is switched on, the firmware loads the boot loader, which
loads the Notebook OS kernel. The kernel starts the base system, which starts
the graphical session.

The loading screen appears as soon as the graphical session begins. It shows
the system name and a progress bar, and remains on screen until the desktop is
ready. Work that the user cannot see — keyboard layout, audio mixer settings,
display configuration — runs behind it.

The loading screen closes when the menu bar has been drawn. On hardware with
graphics acceleration the desktop is normally ready within a few seconds. On
hardware without it, the first drawing of each screen is performed by the
processor and takes longer.

## First-run setup

First-run setup appears once, on the first startup after an installation
performed in "set it up for someone else" mode. It does not appear on a system
installed by the person who will use it, and it never appears again after it
has been completed.

The installer, in that mode, configures everything about the computer that does
not depend on who owns it, and leaves the remaining four questions for first-run
setup:

| Step | Setting |
|---|---|
| Language | The interface language, from the 18 available |
| Keyboard | The keyboard layout |
| Computer name | The name of the machine |
| Password | The sign-in password for the account, or no password |

First-run setup runs before the sign-in screen and before any part of the
desktop is drawn. When it finishes, it writes the four answers, removes the
marker that caused it to run, and hands over to the normal startup sequence.

## Signing in

The sign-in screen appears at startup when the account has a password set. It
shows the account name and a password field. The desktop is not started until
the correct password is entered.

If the account has no password, the sign-in screen does not appear and the
desktop starts directly. This is the case on the live image, which has no
password.

The account and password are those created during installation, or during
first-run setup on a system installed for someone else. The password is stored
as a SHA-512 hash in the system password file. There is no password recovery: a
forgotten password cannot be reset from within Notebook OS.

To change the password, use **Settings > Users**.

## Sleep

**Logo menu > Sleep** turns off the display immediately and locks the screen.
No confirmation is requested, because nothing is closed and no work is at risk.

Moving the mouse or pressing a key turns the display back on. The sign-in
screen is then shown, and the password must be entered before the desktop
becomes visible again. Applications continue running throughout; they are not
closed and their state is not changed.

If the account has no password, waking from sleep returns directly to the
desktop.

The display also turns itself off after a period of inactivity. The delay is
set in **Settings > Power** under "Blank screen after".

## Restart and shut down

**Logo menu > Restart…** and **Logo menu > Shut Down…** both ask for
confirmation before proceeding. The confirmation states that unsaved work in
open applications will be lost.

Neither command saves open documents. Applications that keep a single
continuously saved store — Tasks, Calendar, Journal, Contacts, and others — have
already written their data and lose nothing. Applications that manage named
documents — Writer, Novel, Screenplay, Illustrator, Sequencer — keep a session
recovery snapshot, but any changes made since the last explicit save are not
written to the document file itself.

There is no separate "log out" command. Notebook OS is a single-account system,
and shutting down and restarting are the only ways to end a session.

## Power and battery

On a portable computer, the menu bar shows the battery charge as a percentage
at the right-hand end. Batteries belonging to peripherals such as a wireless
mouse are excluded; only the computer's own battery is reported.

**Settings > Power** shows the battery state in detail and holds the
screen-blanking delay. See [11. Settings](11-settings.md).

## Startup problems

| Symptom | Cause and action |
|---|---|
| The computer boots to firmware setup or to another operating system | The Notebook OS boot entry is not first in the firmware boot order. Change the boot order in firmware setup. |
| The boot loader reports a signature verification failure | The Secure Boot key has not been enrolled. Select the "Enroll Secure Boot key" entry in the boot menu and follow the enrolment prompts, then restart. See [15. Installing](15-installing.md). |
| The screen stays blank after the firmware logo | The display is connected to an output that the firmware does not use as its console. Connect the display to the primary output. |
| The desktop appears but is slow to draw | The graphics hardware has no in-tree driver and the system is rendering in software. This affects the first drawing of each screen most. |
