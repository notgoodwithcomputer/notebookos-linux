# 04. The Finder

The Finder is the file manager. It opens automatically on the desktop and can
also be opened from **File > New Finder Window** in the menu bar, or from any
folder listed on the Finder button.

The window has three parts: a sidebar on the left, a navigation bar across the
top of the file area, and the file listing itself.

## The sidebar

The sidebar has two groups.

**Places** — the user's own folders:

| Folder | Contents |
|---|---|
| Home | The top of the user's storage; contains all the folders below |
| Desktop | Items shown on the desktop |
| Documents | Documents saved by applications |
| Music | Audio files; the Music application reads its library from here |
| Pictures | Image files |
| Videos | Video files |
| Trash | Items removed but not yet erased |

Desktop, Documents, Music, Pictures and Videos are created when the Finder
starts, so no sidebar entry ever points at a folder that does not exist.

**Devices** — storage and applications:

| Item | Contents |
|---|---|
| Applications | The installed applications |
| Local Disk | The system disk |
| *(USB devices)* | Any connected USB storage, listed by name |

## Navigating

- **Double-click a folder** to open it.
- **Back and Forward** move through the folders already visited.
- **The path bar** shows the location as a chain of folder names. Clicking any
  name in the chain opens that folder.

## Views

Two views are available, selected from the buttons in the navigation bar.

**List view** shows one row per item with four columns: Name, Size, Date
Modified, and Kind. Clicking a column heading sorts by that column; clicking it
again reverses the order.

**Grid view** shows large icons in a grid, with the name beneath each one. Long
names wrap to the width of the cell.

**Kind** describes what an item is in plain words — "Folder", "Document",
"Image", "Audio", "Word Processor" for the Writer application, and so on.

## Search

Typing in the search field filters the current folder immediately. A moment
later the same text is searched across the whole of Home, and matches found
elsewhere are added with the folder they were found in.

Searching begins at two characters. Results are capped, and the search stops
after examining a set number of items, so a large folder tree cannot make the
Finder unresponsive.

If nothing matches, the Finder states whether nothing in the current folder
matched or nothing anywhere in Home matched. These are different results and
are reported differently.

## Type-ahead

Typing letters while the file list has focus jumps to the first item beginning
with those letters. The typed letters are discarded after a short pause, so the
next word typed starts a fresh jump.

## File operations

Available from the **Actions** menu, the right-click menu, and the keyboard.

| Command | Effect |
|---|---|
| Open | Opens the item |
| Get Info | Shows name, kind, size, location and dates |
| New Folder | Creates a folder in the current location |
| Rename | Renames the item in place |
| Duplicate | Creates a copy in the same folder |
| Cut | Marks the item to be moved on the next Paste |
| Copy | Marks the item to be copied on the next Paste |
| Paste | Places the marked item in the current folder |
| Move to Trash | Moves the item to the Trash |
| Delete Immediately… | Erases the item without using the Trash, after confirmation |

Cut, Copy and Paste act across folders: cut or copy in one folder, open
another, and paste.

If a paste would overwrite an item with the same name, the Finder reports the
conflict instead of replacing the existing item.

### Undo

`Ctrl+Z` reverses the last file operation. After a move, copy, rename,
duplicate or Trash operation, the Finder states what happened and notes that
`Ctrl+Z` will undo it.

### Copying large items

Copying anything above a few megabytes shows a progress dialog with a working
Cancel button. Cancelling stops the copy and leaves nothing behind; the Finder
confirms that nothing was changed.

## The Trash

**Move to Trash** moves an item into the Trash folder. Nothing is erased at
that point.

In the Trash:

- **Put Back** returns an item to the folder it came from.
- **Delete Immediately…** erases the selected item after confirmation.
- **Empty Trash** erases everything in the Trash after confirmation.

Both confirmations state that the action cannot be undone, and both name what
is about to be erased — the item's name, or the number of items.

## Applications folder

The Applications folder lists the installed applications. Double-clicking one
opens it.

Applications can be removed from this list, which hides them without
uninstalling anything; they are restored from the Actions menu. Only
applications can be removed this way — documents placed in the folder cannot.

## USB storage

A USB storage device is mounted automatically when it is connected and appears
under **Devices** in the sidebar. FAT32, exFAT, NTFS and ext2/3/4 are
supported.

Files written to a USB device are written straight through to the device rather
than being held in memory, so a file that has finished copying is on the stick.

**Eject** unmounts the device and appears beside it in the sidebar. If a copy
is still in progress, the Finder declines to eject and states that the write
must finish first.

Ejecting before disconnecting a device is the supported procedure.

## Opening documents

Double-clicking a document opens the application registered for its file type.

| File types | Opens in |
|---|---|
| `.png` `.jpg` `.jpeg` `.gif` `.bmp` `.webp` `.tiff` `.ico` `.svg` `.heic` `.avif` | Media Viewer |
| `.mp4` `.m4v` `.mkv` `.mov` `.webm` `.avi` | Media Viewer |
| `.txt` `.md` `.writer` | Writer |
| `.epub` `.pdf` | E-book Reader |
| `.mp3` `.wav` `.ogg` `.flac` `.m4a` | Music |
| `.gba` `.gbc` `.gb` `.sgb` | GBA Emulator |

These assignments can be changed in **Settings > Default Applications**. Only
applications that actually accept a document can be chosen there, so a file
type cannot be assigned to an application that would ignore it.

Double-clicking a file type with no registered application reports that there
is no application for it.
