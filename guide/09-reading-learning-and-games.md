# 09. Reading, learning and games

---

## E-book Reader

E-book Reader displays EPUB and PDF documents. It opens empty; the library
starts with no books.

### Adding books

**File > Open** adds a book to the library and opens it. Books normally arrive
on a USB storage device and are copied into the computer with the Finder first.

Double-clicking an `.epub` or `.pdf` file in the Finder also opens it here.

### The library

The Library sheet lists the books on the computer. The shelf, the book
currently open, and each book's reading position are all kept, so reopening a
book returns to the page it was left on.

### Reading

| Format | Rendering |
|---|---|
| EPUB | Parsed on the computer and laid out as reading paragraphs. The `A−` and `A+` controls change the reading type size. |
| PDF | Rendered page by page. The `A−` and `A+` controls zoom the page, and the `‹` and `›` controls page through the document. |

---

## Language

Language is an offline language course. Courses are stored on the computer;
nothing is downloaded.

### Courses

| Course | Units |
|---|---|
| Esperanto | 10 |
| Spanish | 10 |
| French | 10 |
| Serbo-Croatian | 10 |
| Mandarin | 6 |

### Structure

A course is a path of skills. Each skill takes five crowns to complete, and a
lesson gets harder at each crown:

| Crown | Exercise type |
|---|---|
| First | New words are taught, then their meanings are chosen from options |
| Middle | Progressively fewer options and more recall |
| Last | Words are typed from English, with no options shown |

Each lesson runs a small set of exercises generated from the course's
vocabulary, phrases and grammar notes.

### Hearts, XP and streaks

- **Hearts** are lost on a wrong answer. They return over time, or through
  Practice.
- **XP** is earned per lesson, against a daily goal.
- **The streak** counts consecutive days on which the daily XP goal was met.

### Word strength and practice

Every word encountered carries a strength that decreases while it is not
practised. **Practice** selects whichever words have decayed the most.

### Pronunciation

The computer has no recorded speech, so pronunciation is taught with the
International Phonetic Alphabet. Every target word carries its transcription,
and the "which word is this?" exercise shows the transcription alone.

---

## Maps

Maps displays street maps stored on the computer. There is no network, so no
map is ever fetched; the whole of what can be shown is on disk.

### Controls

| Action | Method |
|---|---|
| Pan | Drag the map |
| Zoom | Scroll wheel, the `+` and `−` buttons, or View > Zoom In / Zoom Out |
| Fit Region | View > Fit Region shows the whole of the loaded map pack |
| Search | Type a city or town name to jump to it |

### What is drawn

Roads by class, water, railways, parks and land use, and place names. The
detail shown changes with the zoom level: at low zoom only major roads and
large places are drawn; at high zoom the full street network appears.

### Map packs

Maps reads `.nbm2` map packs. A pack is divided into cells and only the cells
covering the current view are read from disk, so a map pack covering a continent
can be browsed on a computer that could not hold it in memory.

The shipped system includes a Monaco pack. Larger packs — up to continental
coverage — are supplied separately and are copied into the map folder from a USB
storage device.

---

## Calculator

A scientific calculator on a single card: a right-aligned display with a
running-history line above it, and a six-column keypad.

It performs trigonometry in either degrees or radians, powers, roots,
logarithms, factorials, and the standard constants.

The display opens at 0.

---

## 2048

A 4×4 sliding-tile puzzle. Tiles of equal value merge when they collide; the
target is a tile of 2048.

| Control | Action |
|---|---|
| Arrow keys, or `W` `A` `S` `D` | Slide the board |
| New Game | Starts a new board |

The score and best score are displayed. An overlay appears when the target is
reached or when no moves remain.

The board in play, its score and the best score are all kept, so leaving the
game and returning resumes the same board rather than discarding it.

---

## GBA Emulator

GBA Emulator runs Game Boy, Game Boy Color and Game Boy Advance software.

### The library

The Home folder is scanned for `.gb`, `.gbc`, `.sgb` and `.gba` files, which are
shown as a grid of cartridges. Selecting one starts it. A ROM can also be opened
from the file browser, or by double-clicking it in the Finder.

Nothing appears in the library that is not a real file on disk. No game is
supplied with the system.

### Controllers

Connected USB game controllers are listed and work in-game. They are handled as
generic HID devices, which covers most commercially available pads.

Keyboard control is available for all buttons when no controller is connected.

---

## GBA SDK

GBA SDK builds Game Boy Advance games. Its output is a real `.gba` cartridge
image, which runs in the GBA Emulator, on a flash cartridge, or on original
hardware.

No programming is required to use it, though C code can be written where it is
wanted.

### Assets

The asset browser down the left side holds six kinds of thing, each shown as
what it is:

| Kind | Editor |
|---|---|
| Sprites | A pixel canvas |
| Tile sets | A pixel canvas for background tiles |
| Sounds | A piano roll |
| Objects | An events-and-actions sheet |
| Rooms | A placement grid |
| Scripts | C code shared across the whole project |

Every editor has the same layout: a head stating what is being edited and the
actions that apply to it, then a single tool row, then the work surface.

### Objects

An object is a thing in the game. It has a sprite and a set of events; each
event holds a list of actions performed when that event occurs.

**Events** include Create, Step, Destroy, individual button held / pressed /
released events, alarms, and collisions.

**Actions** are grouped:

| Group | Contains |
|---|---|
| Motion | Movement, speeds, gravity, jumps, screen wrapping |
| Instances | Creating and destroying objects, changing sprite and animation speed |
| Variables | Setting and adding to variables |
| Flow | Conditionals, repeats, alarms, room changes, exiting an event |
| Score | Score, lives and health — setting, adding, and testing |
| Sound | Playing and stopping sounds |
| Text | Drawing text and numbers, clearing text |
| Advanced | Saving and loading the game, and Execute Code |

### Rooms

A room is a screen of the game. Objects are placed on a grid. One room is
marked as the starting room.

### Writing C

Two routes are available for code:

- **Execute Code** — an action that emits C inside the event it sits in.
- **Scripts** — file-scope C, emitted once before every object, and visible to
  all of them. Functions, lookup tables, constants and interrupt handlers go
  here, because an Execute Code action cannot define them.

### Help

The Help pane holds reference material for the whole toolkit:

| Section | Contents |
|---|---|
| Course in C | A numbered course, with progress tracked |
| Recipes | Complete worked examples, such as a platformer |
| Actions | Reference for every action in the palette |
| Engine Calls | The runtime functions available from C |
| Hardware | The Game Boy Advance hardware registers |

**Show C for This Event** displays the C that the current event compiles to,
which is how the events-and-actions sheet and the code relate to each other.

### Building

**Compile & Export…** builds the whole project into a `.gba` file with the
bundled ARM toolchain and saves it. **Build Details…** shows the build log.

Notebook OS runs one application at a time, so a built game is played by
closing GBA SDK and opening GBA Emulator.

### Panes and files

The workspace splits into panes — **Split Right**, **Split Down**, **Close This
Pane**, **Reset Layout** — so an editor and the help reference can be shown side
by side.

Projects are saved as `.gbaproj` files in `Documents`. **Open Example Game**
loads a complete worked project.
