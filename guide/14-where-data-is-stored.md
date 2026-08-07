# 14. Where data is stored

All data is stored on the computer. There is no network, so there is no cloud
storage, no synchronisation and no remote backup. The file on the disk is the
only copy of anything until it is copied to a USB device.

## The user's folders

| Folder | Contents |
|---|---|
| `Documents` | Documents saved from applications. PDF exports are written here. |
| `Music` | Audio files. The Music application reads its library from here. |
| `Pictures` | Image files. |
| `Videos` | Video files. |
| `Desktop` | Items shown on the desktop. |
| `.Trash` | Items moved to the Trash but not yet erased. |

These folders are created when the Finder first starts, so no sidebar entry
points at a folder that does not exist.

## Application data

Single-store applications — Tasks, Calendar, Journal, Contacts, Cookbook, Meal
Planner, Accounting, Academics, Workout, Language, Music, Maps and others — keep
their data in a private file rather than in a document the user manages.

These files are in `.config/notebook` in the home folder, one per application,
named after the application.

They are not intended to be opened or edited directly. They are included in a
backup.

Document applications — Writer, Novel, Screenplay, Illustrator, Sequencer, Video
Editor, GBA SDK — write named documents to `Documents`, and additionally keep a
session recovery file in the same private location.

## Session recovery

Document applications write their working state continuously to a recovery
file, separately from the document itself.

If the computer is switched off, or an application is stopped, with unsaved
changes, reopening that application restores the working state from the recovery
file. The document file on disk is unchanged until Save is used.

## How files are written

Three rules apply to every application's data file.

**Writes are atomic.** A file is written in full to a temporary location and
then moved into place. An interruption during a save — a power loss, a program
stopped — leaves either the complete old file or the complete new one, never a
half-written file.

**One backup generation is kept.** The first time an application opens its data
file in a session, the previous contents are copied to a `.bak` file beside it.
The backup is only replaced when the new contents are not smaller than what is
already backed up, so a session that opens on empty data does not overwrite a
backup containing real work.

**A file that cannot be read is never overwritten.** If an application cannot
read its data file, it moves the file aside under a name ending
`.damaged-<date>-<time>` before doing anything else. The application then opens
empty, but the original bytes are still on disk.

This last rule exists for a specific failure. An application that could not
read its file used to open empty and then, on its next save, write that empty
state over the file. Opening and closing the application was enough to destroy a
journal that still plainly contained its text. The file under the home folder is
the only copy, so it is never replaced by something the system could not read.

## Recovering a damaged file

If an application opens empty when it should not:

1. Open Terminal.
2. Look in `.config/notebook` for a file named after the application with a
   `.damaged-` suffix, or a `.bak` file.
3. The `.damaged-` file holds the exact bytes that could not be read. The
   `.bak` file holds the last contents that were read successfully.

Copying the `.bak` file over the application's data file, with the application
closed, restores the last good state.

## Backup

**Settings > Backup** copies the user's folders and application data to a USB
storage device.

The page shows the total size and file count before the copy starts, and lists
the connected USB devices. **Look again** rescans for a device. If none is
connected, the page states so.

This is the only backup facility on the system.

## USB storage

A USB storage device is mounted automatically when it is connected, and appears
under **Devices** in the Finder sidebar.

Files written to a USB device are written straight through to the device rather
than being held in memory. A file that has finished copying is on the stick.

**Eject** in the Finder unmounts the device. Ejecting before disconnecting is
the supported procedure. If a copy is still running, the Finder declines to
eject and states that the write must finish first.

## The live image

When the system is running from a live USB stick or DVD, the system files are
read-only and everything written is held in memory. Nothing survives a restart.

To keep work created during a live session, copy it to a separate USB device
before shutting down, or install the system to a disk. See
[15. Installing](15-installing.md).
