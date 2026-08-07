# 08. Pictures, sound and video

---

## Media Viewer

Media Viewer displays images and plays video. It opens empty; nothing is loaded
until a file is chosen or a file is double-clicked in the Finder.

The window has a toolbar, a viewing stage, an Info panel, and a thumbnail
filmstrip along the bottom.

### Toolbar

| Control | Effect |
|---|---|
| Open | Chooses a file, starting at Home |
| Zoom in / Zoom out | Scales the displayed image; the current factor is shown as a percentage |
| The zoom percentage | Clicking it fits the image to the window |
| Rotate | Rotates the image 90° clockwise |
| Previous / Next | Steps through the other image files in the same folder |
| Slideshow | Advances automatically at a fixed interval; the button shows an active state while running |

### Keyboard

| Key | Action |
|---|---|
| `←` `→`, `Page Up` `Page Down` | Previous and next image |
| `+` `−` | Zoom |
| `0` | Fit to window |
| `Ctrl+O` | Open |

Any manual navigation — a key press, Previous or Next, or a filmstrip click —
stops a running slideshow.

### Info panel

Shows the file's details. Fields with no value read `—` rather than being left
blank.

### Filmstrip

A thumbnail of every image in the folder. The current image is marked, and
clicking a thumbnail jumps to it. Thumbnails are decoded in the background, so
opening a large folder does not delay the first image.

### Formats

Images: PNG, JPEG, GIF, BMP, TIFF, ICO, WebP, SVG, HEIC and AVIF. Video: MP4,
M4V, MKV, MOV, WebM and AVI.

---

## Music

Music plays the audio files in the `Music` folder.

The library is read at launch from `Music`, including its subfolders. Each track
is named from its file, and its album from the folder containing it. Nothing is
downloaded and no track is listed that is not on disk.

Supported formats: MP3, FLAC, OGG, WAV and M4A.

### Layout

| Area | Contents |
|---|---|
| Sidebar | Songs, Albums and Artists views, and the user's playlists |
| Main pane | A search field and the track list |
| Playback bar | Fixed along the bottom |

### Playback bar

Play and pause, previous and next, a progress bar that can be dragged to seek,
elapsed and remaining time, a volume control, shuffle and repeat.

### Playlists

| Menu command | Effect |
|---|---|
| File > New Playlist | Creates a playlist |
| File > Rename Playlist… | Renames the selected playlist |
| File > Delete Playlist… | Deletes the selected playlist, after confirmation |
| File > Open Music Folder | Opens the `Music` folder in the Finder |

Playlists are stored by the application and persist across restarts. Deleting a
playlist does not delete the audio files in it.

### Adding music

Copy audio files into the `Music` folder — from a USB stick, using the Finder —
and reopen Music. The library is read at launch.

---

## Illustrator

Illustrator is a pixel-art editor. Every stroke writes exact pixels: a
one-pixel line is one pixel wide, and a painted pixel is either exactly the
chosen colour or untouched. Nothing is anti-aliased and no edge is softened.

### Tools

| Tool | Key | Effect |
|---|---|---|
| Pencil | `P` | Freehand, square tip |
| Brush | `B` | Freehand, round tip |
| Eraser | `E` | Clears pixels |
| Fill | `F` | Fills a contiguous area |
| Colour Picker | `I` | Takes the colour under the pointer |
| Line | `L` | Straight line |
| Rectangle | `R` | Rectangle |
| Ellipse | `O` | Ellipse |

`[` and `]` change the brush size by one pixel. `G` shows and hides the pixel
grid.

### View

| Command | Shortcut |
|---|---|
| Zoom In | `Ctrl++` |
| Zoom Out | `Ctrl+−` |
| Actual Size | `Ctrl+0` |
| Fit in Window | `Ctrl+9` |
| Show / Hide Pixel Grid | `G` |

### Layers

| Command | Effect |
|---|---|
| New Layer | Adds a layer above the current one |
| Delete Layer | Removes the current layer |
| Bring Forward / Send Back | Reorders the current layer |
| Clear Layer | Empties the current layer |
| Hide Active Layer / Show All Layers | Controls layer visibility |
| Opacity 100% / 50% / 25% | Sets the current layer's opacity |

### Image

| Command | Effect |
|---|---|
| Canvas Size… | Changes the canvas dimensions |
| Flip Horizontal / Flip Vertical | Flips the image |
| Outline Shapes | Draws shapes as outlines rather than filled |

### Files

New, Open, Save and Save As work on image files in `Documents`. **Copy Image**
places the current image on the clipboard.

---

## Sequencer

Sequencer is an eight-track recorder. Everything in a project was played into a
microphone or an audio interface and recorded here; nothing in the program
makes a sound of its own except the metronome and the count-in. Three views are
switched from the bar under the deck.

### Arrange

The transport deck — rewind, fast-forward, stop, play, record — above eight
timeline lanes carrying the takes.

The lanes **zoom**, from the whole song across the window down to a fraction of
a second, so an edit can be placed by eye. A scrollbar under the lanes shows
which part of the song is on screen.

| Doing this | Does this |
| --- | --- |
| Click a clip | Selects it |
| Double-click a clip | Opens it in Edit |
| Drag a clip | Moves it along its lane, or up and down onto another lane |
| Drag either **end** of a clip | Trims that end; nothing recorded is thrown away, and dragging back out brings it in again |
| Right-click a clip | Removes it |
| Drag across empty tape | Chooses the bars to loop |
| `+` / `−`, or Ctrl+wheel | Zooms in and out |
| Shift+wheel | Scrolls the tape sideways |
| Arrow keys | Nudges the selected clip along the lane, or onto the lane above or below |
| Delete | Removes the selected clip |
| S | Splits every lane at the playhead |
| C | Switches between the SELECT and CUT tools |
| Ctrl+C / Ctrl+X / Ctrl+V | Copies, cuts and pastes a clip. Paste lands on the selected track at the playhead |
| Ctrl+0 | Shows the whole song |

### The tools, and the grid

**SELECT** is the ordinary tool. **CUT** turns the pointer into a knife: a
click cuts the clip under it in two. Neither half copies any audio — they are
the same recording read from two places — so a cut costs nothing and Undo takes
it back.

**SNAP** decides where every one of those edits may land: on a bar, on a beat,
on a division of a beat, or **FREE**, which is exactly where the pointer is.
The lanes draw the grid they are snapping to, so the answer is visible before
the edit is made. FREE is what a breath before a word needs; rounding that cut
to the nearest sixteenth is what makes it audible.

### Edit

The selected take, whole. The part the clip plays is drawn solid and everything
trimmed off it is still there, faint, one drag away from coming back. Bar lines
are drawn at the position they fall in the arrangement, so whether a take's
downbeat lands on a bar can be seen rather than guessed at.

The bar above carries the take's own level, its fade in and fade out,
**Normalise** (sets the level so the loudest moment of the part being played
sits just below full scale), **Snap to Grid** and **Loop This**.

### Mix

One channel strip per track — level, pan, low cut, high cut, compression,
reverb send, echo send, mute and solo — plus the master strip: room size, echo
time, echo feedback, tape wobble and the master fader.

**File > Export as Audio…** uses the same renderer that plays the arrangement,
so the exported file is exactly what was heard, including the reverb tail past
the last note.

### Recording audio

1. Choose the **input device** from the Input menu — the built-in microphone, a
   USB microphone or an audio interface.
2. **Arm** a track with its REC button. That is the lane the take lands on.
3. Press Record.

**MONITOR**, beside the record button, plays the input through the speakers
while it is being recorded, so what is being played can be heard. It is on
unless it is turned off — and it needs turning off when the microphone can hear
the speakers, which otherwise howls. The meter beside it reads the level coming
in, live, so a take that is clipping or barely there is visible before it is
over rather than after.

**Count-in** (Transport menu) counts one bar in before the take starts; the
recording keeps the count-in inside itself and the clip simply begins after it.

The capture level is set on each take, so a microphone connected after the
computer started is picked up.

### Files

Projects — tracks, mix settings and recorded takes — are saved as `.json` files
in `Documents` through the File menu. A rolling autosave provides session
recovery.

---

## Video Editor

Video Editor assembles clips, images and audio into a finished video file.

The workspace has three panes: the Media bin on the left, a 16:9 preview with
transport controls in the centre, and Properties on the right. Beneath them is a
strip that switches between Storyboard and Timeline.

### Media bin

**Import Media…** scans the Home folder for video, image and audio files and
adds them to the bin. The bin also holds the palette of transitions.

### Building a video

Select a clip in the bin, then click a slot in the storyboard to place it
there.

| Command | Effect |
|---|---|
| Split Clip | Divides the selected clip at the playhead |
| Move Left / Move Right | Reorders the selected clip |
| Delete Clip… | Removes the clip, after confirmation |
| Add Transition | Applies a transition between clips |
| Add Title Card | Inserts a title card |
| Add Credits | Inserts a credits sequence |
| Add Music… | Adds an audio track |

**Edit > Undo Delete Clip** reverses the last deletion.

### Properties

Per-clip settings: title, duration, and transition. Effects and Ken Burns
motion are set here, along with captions.

### Export

**File > Export Video…** renders the project to a video file. The export is
encoded with H.264.

### Files

Projects are saved and opened through the File menu. The current project is
also written continuously for session recovery.
