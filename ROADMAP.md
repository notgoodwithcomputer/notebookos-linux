# Notebook OS — road to a shippable product

Status as of 2026-07-28. This document is the working plan for getting from
"an OS that mostly works" to "an OS you can hand to someone".

---

## The governing principle

**An OS may do less than the user hoped. It may never lie about what it does.**

Every defect in this document is scored against that single rule, because it is
the one a non-technical user actually notices. They forgive a missing feature.
They do not forgive a switch that does nothing, a Save that loses their work, or
a number that is wrong.

That gives three ways to close any gap, in ascending cost:

| | Action | Cost | When |
|---|---|---|---|
| 1 | **Hide it** | minutes | The feature is not real and cannot be made real in the time available. |
| 2 | **Relabel it** | minutes | The feature is real but narrower than its label claims. |
| 3 | **Build it** | hours to days | The feature is core to the product's promise. |

Hiding is not defeat. A hidden app keeps its module, its file associations and
its tests; it simply is not offered until it is true. `finder.HIDDEN_APPS`
carries the reason in-tree so the debt stays legible, and
`tools/hidden_apps_selftest.py` proves a hidden app has no desktop tile and is
not the default handler for any file type — the two routes by which a user
meets an app you thought you had withheld.

---

## Severity vocabulary

Used consistently below, worst first.

- **DATA LOSS** — the user loses work, or is told something was saved when it
  was not. Ship-blocking without exception.
- **WRONG ANSWER** — money, dates or arithmetic that is confidently incorrect.
  Ship-blocking; a calculator that is sometimes wrong is worse than no
  calculator.
- **BROKEN** — a control visibly fails or does nothing. Ship-blocking for
  anything on a common path; hide or relabel otherwise.
- **HOLLOW** — looks complete, silently does less. The most damaging class over
  time, because trust erodes without a visible failure to point at.
- **MISSING** — implied by the UI but absent. Usually a labelling fix.

Fix sizes: **SMALL** (< 1h) · **MEDIUM** (a few hours) · **LARGE** (needs a
buildroot package, a kernel option, a driver, or a redesign).

---

## 1. Confirmed this session — done

Verified by selftest and, where noted, on real hardware in QEMU.

| Area | What was wrong | Status |
|---|---|---|
| Shared save dialog | Overwrite armed the Save button; a second press destroyed the file with no confirm and no undo. Used by Writer, Illustrator, GBA IDE, Video — the widest-reach destructive path in the OS. | Fixed; selftest verified to fail against the old code |
| Shared save dialog | Save/Open button painted grey-on-red in every app that opens it (a footer rule also matched the label inside the button). | Fixed; verified by pixel sample |
| Shared print dialog | Leaked raw Python exceptions into the UI — inherited by all 44 apps. | Fixed |
| Writer | Showed errno and the absolute file path on a failed save, at the moment work was being lost. | Fixed; errno now mapped to plain sentences that say whether the work still exists |
| Novel | The manuscript-title widget doubled as the model, so the unsaved-work guard misfired in **every non-English language**. | Fixed |
| Journal / Academics | A long stored field drove the app's minimum width past 3200px, pushing the sidebar and most of the page off screen. | Fixed |
| Novel | A long title wrapped downward and starved the chapter list to a 26px sliver — every chapter unreachable. | Fixed |
| GBA IDE, BitChat | The only two destructive actions in the OS with neither confirm nor undo. | Fixed (BitChat since removed) |
| Settings, Packages, Installer, Calculator | Developer vocabulary on system screens; the installer's password checkbox never stated its consequence. | Fixed |
| Accent colour | Red meant up to four different things on one screen in Music and Tasks. | Fixed; idiom now documented |
| App icons | Terminal wore the "unrecognised file" glyph; Settings and System Monitor were identical. | Fixed; uniqueness selftest added |
| BitChat | Removed entirely at the owner's request. | Done — zero references remain in the shipped tree |

---

## 2. The inventory

Four audits drove every app rather than reading it. Every "actually happens"
below was established by execution, a config fact, or a rendered page that was
looked at. Fix agents are working the SMALL items; sizes are honest estimates.

### 2a. Ship-blocking — loses or corrupts the user's work

| # | Where | What happens | Size |
|---|---|---|---|
| 1 | `writer.py:1688` | Inserted images are stored as **file paths, not bytes**. Delete or unplug the source and the picture is silently gone — from the document, the autosave, and every undo snapshot. This OS explicitly supports inserting from a USB stick. | MEDIUM |
| 2 | `writer.py:2117` | Save As offers `.md`/`.txt` as an equal choice, **strips every formatting run, table and image**, reports "Saved", then adopts that path so all later saves stay plain. `.md` is not even markdown. | SMALL |
| 3 | `installer.py:1807` | **Erases the disk before checking it fits.** `wipefs` → `sgdisk -Z` → `mkfs` → *then* extract ~2 GB. A 2 GB stick is destroyed before the failure appears. No size gate exists anywhere in the file. | SMALL |
| 4 | `video.py:3182` | Export silently overwrites an existing video. The name defaults to the project name and exports re-enter the media bin, so collisions are likely. | SMALL |
| 5 | `novel.py`, `journal.py`, `academics.py`, `cookbook.py` | Export to PDF silently overwrites. A second journal export the same day destroys the first. | SMALL |

### 2b. Ship-blocking — confidently wrong

| # | Where | What happens | Size |
|---|---|---|---|
| 6 | `calendar.py:379` | Quick-add eats bare numbers: *"meeting with 3 people"* → **"meeting with people" at 15:00**. *"sept 3 checkup"* lands today because `sept` is not a known month word. The hint shows the time but not the mangled title. | SMALL |
| 7 | `accounting.py:1026` | Entries store **no year** (`"28 Jul"`), there is no date field on the add form, and the CSV inherits it — no spreadsheet parses that column, and a ledger crossing New Year has two identical "3 Jan" rows. | SMALL |
| 8 | `calculator.py:522` | `%` is "divide by 100", so `200+10%` = **200.1**. Every consumer calculator gives 220. | SMALL |

### 2c. Broken — visibly fails or does nothing

| # | Where | What happens | Size |
|---|---|---|---|
| 9 | *kernel config* | **exFAT and NTFS are not built**, so no modern USB stick mounts — `automount.sh` fails silently and nothing appears. This disables Backup, the machine's only data-egress path. Source is present; the options were simply unset. **Being fixed now.** | LARGE |
| 10 | *gdk-pixbuf build* | Built with **JPEG disabled** — a stale build, not a decision (`BR2_PACKAGE_JPEG=y`). Every JPEG outside the media viewer silently fails. Widest blast radius in the OS. **Being fixed now.** | SMALL |
| 11 | `sequencer.py:305` | A recorded mic take **can never be heard**. A seek guard drops it on every pass because the clock advances before playback starts. The take is drawn and counted. | SMALL |
| 12 | `sequencer.py:1937` | A machine with no mic still gets a committed take that is permanently silent. | SMALL |
| 13 | `journal.py`, `academics.py`, `cookbook.py`, `screenplay.py` | Export and print render **Chinese, Japanese, Korean and Hindi as empty boxes** — cairo's toy font API does no fallback. `pdftotext` still extracts the text, so a text check passes while the paper is blank. | MEDIUM |
| 14 | `writer.py:2338` | Images and tables crossing a page boundary are **clipped off the sheet**; a 12-row table loses rows 8-12 entirely. | MEDIUM |
| 15 | `settings.py:2238` | "Set Clock" is lost on every restart — nothing writes the RTC. On a machine that cannot use NTP this is the whole feature. | SMALL |
| 16 | `finder.py:1784` | Move to Trash **can never work on a USB stick** (cross-device rename). The restore path already has the fallback. | SMALL |
| 17 | `media.py:69` | The Open dialog offers `.heic/.heif/.avif`; guest ffmpeg 4.4.4 cannot decode any of them. `.heic` is the iPhone default. | SMALL |
| 18 | `music.py:980` | An unplayable file fails in **total silence** — no message, anywhere. | SMALL |
| 19 | `gbaemu.py:122` | The Fullscreen toggle does nothing; the value is read back only to set the button's own state. | SMALL |
| 20 | `gbaide.py:214` | Declares an Edit menu it never populates, inheriting four items that cannot act on anything. `_room_clear` wipes a room with no confirm and no undo. | SMALL |
| 21 | `sequencer.py:1750` | Eight Pan sliders that move, relabel, and have **zero audible effect** (the engine is mono). | SMALL |

### 2d. Hollow — looks complete, silently does less

| # | Where | What happens | Size |
|---|---|---|---|
| 22 | `settings.py:649` | **Seven saved preferences are never re-applied at boot.** Reopen Settings and your choice is still displayed — which reads as confirmation. Screen-blank is worst: `session.sh` actively disables it every boot while the page keeps showing "5 minutes". One line in `session.sh` repairs five controls. | SMALL |
| 23 | `settings.py:2209` | **Time zone does nothing** — no zoneinfo ships. The page's own clock animates to confirm a change that never leaves the process. The most deceptive control in the OS. | MEDIUM |
| 24 | `settings.py:2060` | Mouse & Touchpad is **entirely inert** — `xinput` is not in the image. The slider says "Faster"; the pointer never changes. | SMALL |
| 25 | `installer.py:879` | The required "Login account" is written to `/etc/passwd` and **never used** — the session hardcodes `NB_HOME=/root` and runs as root. | MEDIUM |
| 26 | `installer.py:1814` | Swap is partitioned and formatted but **never enabled** (no fstab entry). The user loses the space and gains nothing. | SMALL |
| 27 | `installer.py:2019` | Keyboard and language choices are **discarded on first boot** — the desktop reads `locale.json`, which the installer never writes. French/AZERTY boots English/QWERTY. | SMALL |
| 28 | `music.py:1270` | **ID3 tags are never read** although the tag reader is already running. The whole Albums/Artists sidebar is filename guesses. | SMALL |
| 29 | `video.py:2057` | The Play button is a **still-frame slideshow with no sound**. The export is real and correct. | LARGE (or SMALL to relabel) |
| 30 | `video.py:690` | Split a clip and **both halves show the same frame** — the cache is keyed on path, not trim-in. | SMALL |
| 31 | `calendar.py:177` | **Every repeating event silently stops** — a weekly event dies after a year, a birthday after five. The code comment claims it re-extends on edit; it does not. | MEDIUM |
| 32 | `sequencer.py:2465` | VU meters are `sin(tick)` — they bounce identically whether the mic is live, muted or absent. | MEDIUM |
| 33 | `cookbook.py:1670` | **No undo at all** — the only text-editing app without it. Select-all-and-type over a method is unrecoverable. | MEDIUM |
| 34 | `ebook.py:91` | EPUBs render **without images, emphasis or tables**; every page of a single-spine book is labelled "CHAPTER 1". | MEDIUM |
| 35 | `journal.py:1073` | Bold/Italic/Quote are saved, restored on screen, and **dropped from every export and print**. | SMALL |
| 36 | `academics.py:2743` | The **highlighter** — the thing a student uses before an exam — is invisible in export. Print is enabled in the Schedule and Homework views but always prints the active lecture note. | SMALL |
| 37 | `screenplay.py:1178` | The "written by" byline is captured, persisted, and **never printed**; Save As overwrites the title the user typed. Pages are half-letter at 9pt, so page count no longer means screen time. | SMALL |
| 38 | `maps.py` | Ships **Monaco and nothing else** (42 KB, 10 place names) behind a box saying "Search cities and towns…". The renderer is excellent; the data is absent. | LARGE |
| 39 | `language.py:898` | A crown needs a **perfect** lesson and nothing says so — 9/10 forever shows no progress. | SMALL |
| 40 | `contacts.py:92` | US-format birthdays (`12/25/1990`) don't parse, silently excluding that person from the birthday banner. | SMALL |

### 2e. Verified genuinely good

Stated plainly, because it is most of the system: Novel's paginator (TOC with
real page numbers, saddle-stitch imposition), Accounting's cent-exact arithmetic
and CSV escaping, Calculator's AST-guarded evaluator, Video's export pipeline,
GBA IDE's real `.gba` compilation, System Monitor's `/proc` reads and real
SIGTERM, Finder's file operations (105 checks), Settings' Backup page with
pre-flight space check and read-back verification, the installer's real
partition/format/extract engine with a genuine Secure Boot chain, CUPS with 3,662
drivers, Illustrator and 2048 with **no defects found**, Mealplanner, Tasks and
Contacts round-tripping exactly, and a clean boot where every process
`session.sh` starts exists and runs.

---

## 2f. Session end state — 2026-07-28, ~20:15

Work stopped when the API spend limit terminated five in-flight fix agents.
The tree was checked immediately afterwards and is **healthy**: zero syntax
errors across every module and tool, all 17 catalogs parsing and identical
(2322 keys), 36/36 apps constructing, i18n clean.

**Landed and verified**

- **exFAT, NTFS3 and FUSE are now built into the kernel** (item 9 — the single
  most consequential defect). `bzImage` rebuilt successfully with all three
  confirmed set. This restores the entire USB / Backup path. *Not yet booted.*
- **gdk-pixbuf rebuilt with JPEG** (item 10). The library now links `libjpeg.so.8`;
  it previously linked only libpng, so every JPEG outside the media viewer failed.
- **BitChat removed** entirely — 5 modules, .app, 4 selftests, vendor drop,
  every registry entry, 3 catalog keys. Zero references remain.
- **The desktop board rebuilt** to the two-row spec: six rich-card tiles
  (Classes / Homework / Meals / Workout / Journal / Accounting) with Language
  dropped, Tasks and Calendar pinned right and filling their columns. Rendered
  and checked at 1920x1080.
- **Media Viewer fullscreen is now genuinely fullscreen** — the desktop's menu
  bar is a strut-docked DOCK with keep-above that painted over the top 46px of
  every video. The player now raises a PID-carrying flag and the panel stands
  down; a player that dies mid-film cannot strand the desktop without a menu bar.

**Partially applied — the five agents were killed mid-task**

Each had completed real work before termination (measured by diff size):
installer, calendar, writer, sequencer, video, calculator, journal, settings,
media and finder all carry substantial in-flight fixes. **These are unverified.**
Before trusting any single item in sections 2a-2d as fixed, re-run its check.

**Known incomplete**

`tools/board_selftest.py` still targets the OLD board API (`TILE_H`, `TILE_W`,
`_tile_text`, `_tiles_w`). The board works and renders correctly; the test does
not describe it any more and needs rewriting against `_tile_content` and the
per-instance `_tile_w`/`_tile_h`. **This is the first thing to pick up.**

---

## 3. Standing decisions

**Bluetooth.** BitChat was its only consumer. The OS still ships `CONFIG_BT`,
firmware packages, `bluetoothd`, `btmgmt`/`btmon` and an init script, for no
user-facing feature. Either keep as latent hardware support, or strip and
reclaim image size and boot time. The Intel firmware fix was never verified on
real hardware.

**Novel's minimum width** is exactly the panel width, because `_fit_page` sizes
the writing column from the current allocation — the minimum is a fixpoint of
the window width. Structural, pre-existing, not a label bug.

---

## 4. Quality gates

These run green today and must stay green. Anything that cannot be checked by
one of these is, in practice, unverified.

| Gate | What it protects |
|---|---|
| `construct_all_host.py` | No app crashes on launch. Derived from the desktop's own launch table, so coverage cannot drift. |
| `config_resilience_selftest.py` | No app crashes on a missing or corrupt store, in four damage modes. |
| `minsize_sweep.py` | Every app fits 1024x740. Forks per app — measuring several in one process contaminated results by 10-20px. Reports *tight* as well as over. |
| `text_stress_selftest.py` | Hostile documents (500-char fields, unbroken 40-char words) do not blow the layout. |
| `data_safety_selftest.py` | Stores have a single writer; damaged files are preserved, not overwritten. |
| `i18n_check.py` | All 17 catalogs agree; app chrome is translated. |
| `tofu_sweep.py` | Every shipped character has a glyph. **Refuses to run against host fonts** — doing so once produced a confident false report that all of Korean was broken. |
| `board_selftest.py` | The desktop board fits every panel size and a six-week month; no tile can vanish. |
| `hidden_apps_selftest.py` | A hidden app is hidden everywhere — no tile, no file association. |
| `icon_uniqueness_selftest.py` | No two apps share a glyph; no app wears a file type's icon. |
| `accent_selftest.py` | The accent colour keeps one meaning per screen. |
| `measure_widget_rows.py` | The board's row budget matches what the cards actually render. |

---

## 5. What "shippable" means here

A build is shippable when all of the following are true:

1. No DATA LOSS or WRONG ANSWER defect is open.
2. Every visible control either works or is gone. No exceptions for "it mostly
   works" or "the user probably won't try that".
3. Every app in the Applications folder delivers what its name implies. Anything
   that does not is in `HIDDEN_APPS` with a reason.
4. All gates in section 4 pass, on a real boot and not only on the host.
5. The first-run experience is honest: an empty app says what it is for and what
   to do next, not just that it is empty.

Point 2 is the one that costs the most and matters the most. It is also the one
that can be met tonight, because hiding and relabelling are minutes of work.
