# 11. Settings

Settings configures the computer. It is opened from **Logo menu > System
Settings…**, or from the Applications folder.

The window has a list of pages on the left and the selected page on the right.
The pages are listed in this order.

---

## System

*Device information and name.*

**Device name** — the name of this computer. Type a new name and press Apply or
`Enter`. Names may contain letters, numbers and hyphens; anything else is
rejected with a statement of what is allowed.

Below that, a read-only list: the operating system, the system core version, and
the machine's basic hardware details.

---

## Displays

*Screen resolution and size.*

**Resolution** — the display mode. On hardware that boots to the firmware
framebuffer the mode is fixed by the firmware; the page states that the screen
cannot be adjusted from here rather than offering a control that would have no
effect.

**Size of everything** — how much of the interface fits on the screen.

| Option | Effect |
|---|---|
| Normal (1.0×) | The default |
| Smaller (1.25×) | Everything is drawn smaller, so more fits on screen |
| Smaller still (1.5×) | |
| Smallest (2.0×) | |

For larger text rather than smaller, see **Accessibility**.

External displays are detected at startup and when a cable is connected. A
television connected by HDMI is treated as a second display.

---

## Sound

*Where sound comes out, and how loud it is.*

| Setting | Effect |
|---|---|
| Play sound through | Which output device sound is sent to |
| Volume | The output level |
| Silence all sound | Mutes all output |
| Recording level | The microphone input level |

A television connected by HDMI has its own sound device and its own volume
control; when sound is routed there, this page states that the volume is set on
the television.

If the computer has no sound hardware, the page states "No speakers or sound
card found" rather than showing controls that would do nothing.

---

## Printers

*Add and manage USB printers.*

| Control | Effect |
|---|---|
| Find printers | Looks for connected USB printers |
| Add printer | Adds the selected printer |
| Set default | Makes the selected printer the one applications use by default |
| Test page | Prints a test page |
| Remove | Removes the printer |

Each configured printer shows its name and the driver in use.

If the printing system is not available on this computer, the page states so.

See [12. Printing](12-printing.md).

---

## Power

*Battery, screen and switching off.*

**Battery** — present only on a computer that has one. Shows the power source,
the charge level, and whether the battery is charging, discharging or in use.
Batteries belonging to peripherals are excluded. A desktop computer shows a
single row reading "Mains power".

**Screen**

| Setting | Options |
|---|---|
| Blank screen after | Never, 1 minute, 5 minutes, 10 minutes, 30 minutes |

**Sleep and shut down** — Sleep, Restart and Shut Down, the same three commands
as the logo menu. Restart and Shut Down ask for confirmation.

---

## Keyboard

*Layout and key repeat.*

| Setting | Effect |
|---|---|
| Layout | Which letters and symbols the keys type |
| Wait before repeating | How long a key must be held before it starts repeating |
| How fast it repeats | The repeat rate once it has started |

The layout list here is the same list offered on the Region & Language page, and
a layout chosen on either page is saved and applied on the other.

---

## Date & Time

*Clock and time zone.*

| Setting | Effect |
|---|---|
| Current time | The clock as it stands now |
| Time zone | The time zone |
| Date | The date, set manually |
| Time | The time, set manually |

**Set Clock** applies the date and time entered.

The clock is set by hand. There is no network, so there is no time
synchronisation service and the clock cannot be corrected automatically.

---

## Region & Language

*Language, keyboard, and time zone.*

| Setting | Effect |
|---|---|
| Language | The interface language, from the 18 available |
| Keyboard layout | The keyboard layout |
| Time zone | The time zone |

Changing the language affects every application opened after the change. An
application already open stays in the language it was started in. The desktop
itself changes language at the next restart. The page states this.

See [13. Language and keyboard](13-language-and-keyboard.md).

---

## Users

*Accounts on this computer.*

**Signed in as** — the current account's user name and full name. The full name
can be edited and applied here. The user name cannot be changed.

**All accounts** — every account on the computer, marked with which is the
administrator and which is the current user. The page states that every account
listed can sign in, and that the administrator account can change anything on
the computer.

Accounts are created during installation. There is no facility here for adding
or removing an account, and no facility for changing a password; passwords are
set during installation or first-run setup. Notebook OS has no password
recovery — a forgotten password cannot be reset from within the system.

---

## Storage

*Disk space in use.*

**This computer's disk** — a bar showing how much of the disk is used, with the
figures in full and as a percentage. The bar changes appearance when the disk is
90% full or more.

**Other drives and memory sticks** — any other mounted storage, with the same
figures.

If the disk cannot be read, the page says so rather than showing a figure of
zero.

---

## Backup

*Copy files to a USB stick.*

Backup copies the user's folders and application data to a USB storage device.

| Section | Contents |
|---|---|
| What gets copied | The total size and number of files, measured when the page opens |
| Where to copy it | The connected USB devices. **Look again** rescans. |

**Copy files** performs the copy. If no USB device is connected, the page
states "No USB stick found" rather than offering a Copy button that would fail.

This is the only backup facility on the system, because there is no network to
back up to. See [14. Where data is stored](14-where-data-is-stored.md).

---

## Accessibility

*Text size and contrast.*

| Setting | Effect |
|---|---|
| Large text | Increases the interface text size throughout the system |
| High contrast | Increases the contrast between text and background throughout the system |

No screen reader or magnifier is included.

---

## Default Applications

*Which app opens each file type.*

A list of file types, each with the application that opens it. Changing an
entry changes what a double-click in the Finder does.

Only applications that actually accept a document can be chosen. An application
that opens only its own format is not offered for other file types, so a file
type cannot be assigned to an application that would ignore it.

---

## About This Notebook

*System information.*

| Row | Contents |
|---|---|
| Version | The Notebook OS version |
| System core | The kernel version |
| Device name | The name of this computer |
| Memory | Installed memory |
| Disk | Free space and total space |

There is no network row, because this system has no network hardware or
software to report on.
