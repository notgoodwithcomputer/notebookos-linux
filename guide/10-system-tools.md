# 10. System tools

Six applications administer the computer. Settings is documented separately in
[11. Settings](11-settings.md).

---

## System Monitor

System Monitor shows what the computer is doing.

| Area | Contents |
|---|---|
| Gauges | Processor and memory use, updated live |
| Table | Every running program, with its name, memory use and processor share |

Clicking a column heading sorts the table by that column.

**End Program** (`Delete`) stops the selected program. A program stopped this
way loses any unsaved work, exactly as it would if the computer were switched
off.

All figures are read directly from the kernel. No external tools are involved.

The window says "program" throughout rather than "process".

---

## Packages

Packages lists what is installed on this computer.

| View | Contents |
|---|---|
| Installed | Every application and system component, with its name, size and date |
| Updates | Update status |
| Sources | The local disk, and any mounted USB storage |

The Installed list is read from the system image on disk as the window opens.
It lists the applications and system components that can be named; internal
components are not listed.

**Verify Package** re-reads the selected component's files and confirms they
are intact. A component that cannot be read reports a verification failure.

### What Packages cannot do

Notebook OS has no network, so there is no install path for new software. The
installed set is fixed and read-only:

- Nothing can be installed.
- Nothing can be removed.
- **Updates** reports that the system is up to date. The system never checks
  for updates, because there is nothing to check against.

**Sources** reports the local disk and any mounted USB media, read live.

---

## Terminal

Terminal runs a command shell. It is a full terminal emulator running `bash`.

It provides access to the parts of the system that the graphical applications
do not cover.

**View preferences** — the font size and whether the cursor blinks — are kept
between sessions. Shell output and command history are not stored by the
application.

---

## USB Writer

USB Writer copies a disk image onto a USB storage device. It is used to make a
Notebook OS installation stick, and will write any `.iso`, `.img`, `.raw` or
`.bin` file.

**This application erases the target device.** Everything on the USB stick is
destroyed and cannot be recovered.

### How the target is protected

Four rules apply, in this order:

1. **A disk this computer is running from is never listed.** Not greyed out —
   absent. Every disk holding a mounted filesystem, a swap partition, or the
   live medium is excluded from the list.
2. **Only removable, USB-attached devices are offered.** A second internal hard
   disk cannot be selected.
3. **The device is named in full in the confirmation** — make, model, size and
   device node — and the confirmation is answered by pressing a button that
   states what it will do, not a button labelled "OK".
4. **Everything on the target is unmounted before the write**, and the write is
   flushed to the device before it reports completion. When it says the write is
   finished, the stick can be removed.

### Writing

Select the image file, select the target device, read the confirmation, and
confirm. Progress is shown throughout. A 2 GB image takes several minutes on
USB 2.

---

## Install Notebook OS

Install Notebook OS installs the running live system permanently onto a disk.

It appears when the computer has been started from a live USB stick or DVD. On
an already-installed system it opens in a state that reports there is no
install medium, with the install action disabled.

See [15. Installing](15-installing.md) for the full procedure.

### Structure

The installer is a wizard: a list of steps down the left, the current step in
the centre, and Back and Next at the bottom.

Nothing is written to any disk until the final step, and then only after an
explicit confirmation. Every step before that can be revisited with Back.

### Steps

| Step | Contents |
|---|---|
| Language | The interface language for the installed system |
| Keyboard | The keyboard layout |
| Disk | The disk to install onto. Disks too small to hold the system cannot be selected. |
| Swap | Whether to create a swap partition, and its size |
| Account | The user name, full name and password |
| Computer name | The name of the machine |
| Confirm | A summary of everything about to happen, and the confirmation |
| Progress | The install itself, with a live log |

### Set up for someone else

The installer offers a mode for setting up a computer that will be handed to
someone else. In this mode the installer does not ask for a password, a
computer name, a language or a keyboard layout. The installed system asks the
new owner for those four things the first time it starts. See
[02. Starting and stopping](02-starting-and-stopping.md).

### If the install fails

The install runs on a worker thread and streams every command's output into a
live log. If any step fails, the install stops at that point and the failure is
shown. The disk is left in whatever state the failed step reached; the install
can be run again.
