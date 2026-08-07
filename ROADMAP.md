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
| ~~1~~ | **Hide it** | minutes | **FIXED — verified 2026-08-06.** A .writer document carries the picture ITSELF, base64 in the JSON (`_b64_of` / `_pixbuf_from_b64`), not a path. Deleting the original or pulling the stick it came from no longer empties the document, its autosaves or its undo history. |
| ~~2~~ | **Relabel it** | minutes | **FIXED — verified 2026-08-06.** `_confirm_plain_text` names what a .txt/.md save would drop ("the %s in this document will not be saved") and asks first — and only when there is something to lose, so a plain document saves with no friction. |
| ~~3~~ | **Build it** | hours to days | **FIXED — verified 2026-08-06.** The payload is measured up front (`payload_bytes`) and `_disk_too_small` refuses the disk on the screen where it is chosen, before wipefs/sgdisk/mkfs run. Re-checked after the swap step too, because swap is chosen AFTER the disk and raising it can push a disk that fitted past what it holds. `tools/installer_writes_selftest.py`. |

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

> **Reconciliation status — 2026-08-06.** This inventory was written on
> 2026-07-28 and has NOT been kept current: the campaign plan counts 26 items
> closed while the table below marked 2. Items struck through carry the evidence
> that closed them. The rest are **unverified against the current tree, not
> known-open** — a grep pass on 2026-08-06 found the machinery a fix would need
> already present for #7, #17, #22, #23, #26, #27, #28, #30, #32, #35, #36, #39
> and #40, and absent for #21 and #29. (That pass also mis-read #20: it probed `gbaide.py`, which no longer exists, and a missing file reported as "feature absent" rather than "cannot check" — the same blind-spot shape the gates keep turning up, in the audit script itself.) Grep is a hint,
> not a verdict: #8 looked present and *was* genuinely correct only once run.
> Treat every unstruck row as needing a real check before it is scheduled.

### 2a. Ship-blocking — loses or corrupts the user's work

| # | Where | What happens | Size |
|---|---|---|---|
| ~~1~~ | `writer.py:1688` | ~~Inserted images are stored as **file paths, not bytes**. Delete or unplug the source and the picture is silently gone — from the document, the autosave, and every undo snapshot. This OS explicitly supports inserting from a USB stick.~~ | **FIXED — verified 2026-08-06.** `_insert_image` reads the FILE and stores `_b64_of(raw)`; `_serialize` writes it as `rec["data"]`, with `path` kept only as the fallback that keeps old path-only documents readable. **The gate was the stale part**: `writer_selftest` hand-built `_img_meta` with just `{path, ow}`, so it never touched the embedding and round-tripped through the fallback — it would have gone on passing with the fix removed. It now drives the real `_insert_image` (picker patched out), DELETES the source file, and asserts the picture that comes back is 16x10 #3366ff — not merely that a pixbuf is present, because writer draws a visible placeholder when it cannot find the bytes and that is a pixbuf too (measured: the loose check passed against the mutation). Red-proof: drop `rec["data"]` → 3 fail, reporting `160x96 — the placeholder`. The suite also gained the `--de DIR` convention, without which the mutation run silently measured the real writer.py and reported clean. |
| ~~2~~ | `writer.py:2117` | ~~Save As offers `.md`/`.txt` as an equal choice, **strips every formatting run, table and image**, reports "Saved", then adopts that path so all later saves stay plain. `.md` is not even markdown.~~ | **FIXED — gate written 2026-08-06.** `_confirm_plain_text` names what will be lost, COUNTED ("3 formatting runs, 1 table and 2 pictures"), focuses Cancel so a stray Return cannot drop them, and the chip afterwards reads "Saved as text 19:43" instead of the "Saved 19:43" that made a lossy write indistinguishable from a lossless one. The FIX was already in; **nothing in tools/ referenced it**, so it was untested. New `tools/writer_plaintext_selftest.py`, 13 checks. It patches `Gtk.Dialog.run`, not the app's own method, so the REAL dialog is built and the test reads its sentence and its focused button before answering — patching the confirm away and then checking the file measures the mock, since the file survives because nothing tried to write it. A decoy is planted at the destination. Red-proof, four mutations: guard removed (the shipped bug) → 6 fail including the decoy destroyed; the sentence stops counting pictures → 1; the chip says plain "Saved" → 1; focus moved to the destructive button → 1. |
| ~~3~~ | `installer.py:1807` | ~~**Erases the disk before checking it fits.** `wipefs` → `sgdisk -Z` → `mkfs` → *then* extract ~2 GB. A 2 GB stick is destroyed before the failure appears. No size gate exists anywhere in the file.~~ | **FIXED — verified 2026-08-06.** `_disk_too_small` gates the disk list before anything is written, against `payload_bytes` plus ext4 metadata headroom; a disk that cannot hold the system cannot be selected at all. `tools/installer_target_selftest.py`, 73 checks, including the interaction the size gate is easiest to get wrong: a 2 GB stick that fits a 900 MB payload STOPS fitting once 8 GB of swap is asked for. |
| ~~4~~ | `video.py:3182` | Export silently overwrites an existing video. The name defaults to the project name and exports re-enter the media bin, so collisions are likely. | **FIXED** — `Replace video?` guard; the model task 002 copied to the four PDF exports |
| ~~5~~ | `novel.py`, `journal.py`, `academics.py`, `cookbook.py` | ~~Export to PDF silently overwrites. A second journal export the same day destroys the first.~~ | **FIXED — verified 2026-08-06.** All four exporters guard the destination and offer to keep both. `tools/export_overwrite_selftest.py`, 28 checks: it plants a decoy at the real destination, runs the real export, and reads the bytes back, asserting DECLINE leaves the decoy intact, ACCEPT replaces it, and a fresh name is used where one is asked for. |

### 2b. Ship-blocking — confidently wrong

| # | Where | What happens | Size |
|---|---|---|---|
| ~~6~~ | `calendar.py:379` | Quick-add eats bare numbers: *"meeting with 3 people"* → **"meeting with people" at 15:00**. *"sept 3 checkup"* lands today because `sept` is not a known month word. The hint shows the time but not the mangled title. | **NOT A DEFECT — executed 2026-08-06.** Quick-add keeps bare numbers: "meeting with 3 people" stays whole at the 9:00 default, "call 5 folks at 2pm" keeps the 5 and takes only the time, "review 4 designs" is untouched. "dinner at 7" reads as 19:00. |
| ~~7~~ | `accounting.py:1026` | Entries store **no year** (`"28 Jul"`), there is no date field on the add form, and the CSV inherits it — no spreadsheet parses that column, and a ledger crossing New Year has two identical "3 Jan" rows. | **FIXED 2026-08-06.** New entries carry an `iso` date (YYYY-MM-DD) beside the short display string, and the CSV leads with it, so an exported ledger sorts and reconciles. The SHOWN date is deliberately unchanged: measured at 9.5pt in the report face, "26 Sep 2026" is 61pt against the 58pt the PDF gives that column and would run into DESCRIPTION. Entries written before this keep their "28 Jul" and get NO iso — a year cannot be inferred from a row that never recorded one — so their CSV cell is left empty rather than guessed. `tools/accounting_dates_selftest.py`, 17 checks, red-proofed three ways including the silent one (an edit dropping the field) and the tempting one (filling the gap with a plausible year). |
| ~~8~~ | `calculator.py:522` | `%` is "divide by 100", so `200+10%` = **200.1**. Every consumer calculator gives 220. | **FIXED, EXECUTED 2026-08-06** — 200+10%=220, 200-10%=180, 50%=0.5, 200*10%=20 through the real evaluator |

### 2c. Broken — visibly fails or does nothing

| # | Where | What happens | Size |
|---|---|---|---|
| ~~9~~ | *kernel config* | **exFAT and NTFS are not built**, so no modern USB stick mounts — `automount.sh` fails silently and nothing appears. This disables Backup, the machine's only data-egress path. Source is present; the options were simply unset. **Being fixed now.** | **NOT A DEFECT — checked against the built artefacts 2026-08-06.** The buildroot PACKAGES (exfat-utils, ntfs-3g) are off, but those are formatting tools, not drivers. The kernel `mkrelease.sh` builds — `kbuild-desktop`, not `kbuild` — has CONFIG_EXFAT_FS=y and CONFIG_NTFS3_FS=y, with compiled objects under fs/exfat and fs/ntfs3, and automount.sh calls `mount` with no -t so the kernel picks the filesystem itself. `tools/image_capability_check.py`. |
| ~~10~~ | *gdk-pixbuf build* | Built with **JPEG disabled** — a stale build, not a decision (`BR2_PACKAGE_JPEG=y`). Every JPEG outside the media viewer silently fails. Widest blast radius in the OS. **Being fixed now.** | **NOT A DEFECT — checked against the built artefacts 2026-08-06.** BR2_PACKAGE_JPEG=y is set and the shipped libgdk_pixbuf-2.0.so.0 links libjpeg.so.8 and libpng16 directly. The misleading signal is the loaders directory: modern gdk-pixbuf compiles PNG and JPEG INTO the library, so `ls .../loaders/` lists every format except the two that always work. Same suite. |
| ~~11~~ | `sequencer.py:305` | ~~A recorded mic take **can never be heard**. A seek guard drops it on every pass because the clock advances before playback starts. The take is drawn and counted.~~ | **SUPERSEDED BY REWRITE — verified 2026-08-07.** The frame-player and its seek guard no longer exist: playback streams `nbsynth.Mixdown` through `AudioOut` (sequencer.py:1806), and `tools/sequencer_mix_selftest.py` executes real on-disk takes through that shared render path at measured levels (0.8/0.05 takes come out 16× apart). The cited line and mechanism are gone from the tree. |
| ~~12~~ | `sequencer.py:1937` | ~~A machine with no mic still gets a committed take that is permanently silent.~~ | **SUPERSEDED BY REWRITE — verified 2026-08-07.** The stop path now returns no take at all rather than committing a silent clip (see sequencer.py:795 and surrounding code). Open harness item, not an app defect: the live GStreamer `AudioOut` path itself is not yet execution-proven — S2 backlog. |
| ~~13~~ | `journal.py`, `academics.py`, `cookbook.py`, `screenplay.py` | ~~Export and print render **Chinese, Japanese, Korean and Hindi as empty boxes** — cairo's toy font API does no fallback. `pdftotext` still extracts the text, so a text check passes while the paper is blank.~~ | **FIXED — verified 2026-08-06.** The exporters draw through PangoCairo, which does per-character fallback. Two gates, and both are needed: `toyfont_check.py` reads the source (pycairo's Context is an immutable C type and cannot be wrapped at run time, so the toy API has to be caught statically) and `pango_render_selftest.py` RUNS the exports in Japanese, Chinese, Korean, Hindi and Yiddish and asserts every layout resolved 0 unknown glyphs. Note the trap recorded in that file: a green `tofu_sweep` is not evidence here — it answers "does some shipped face have this character", which was true throughout and had nothing to do with the face `show_text` bound. |
| ~~14~~ | `writer.py:2338` | Images and tables crossing a page boundary are **clipped off the sheet**; a 12-row table loses rows 8-12 entirely. | **FIXED — verified 2026-08-06.** Objects are measured against the page bottom BEFORE being drawn: a picture moves whole to the next sheet and `_pdf_table` breaks between rows. Driven end to end — a 12-row table placed at a page foot yields all 12 rows across 4 pages. `tools/export_fidelity_selftest.py`; red-proofed by removing the intra-table break. |
| ~~15~~ | `settings.py:2238` | "Set Clock" is lost on every restart — nothing writes the RTC. On a machine that cannot use NTP this is the whole feature. | **FIXED 2026-08-06** — Set Clock now writes the RTC too (`hwclock -w`), and reports honestly when it cannot. `tools/settings_rtc_selftest.py`. **This entry's stated cause was wrong**: busybox provides hwclock (CONFIG_HWCLOCK=y) and the image has it at /sbin/hwclock. The symptom was real — on x86 the kernel reads the CMOS at boot via read_persistent_clock64, so `date -s` alone was discarded on every restart, with no NTP anywhere to correct it. |
| ~~16~~ | `finder.py:1784` | Move to Trash **can never work on a USB stick** (cross-device rename). The restore path already has the fallback. | **FIXED 2026-08-06** — `_trash_across` + `_restore_across`; `tools/finder_crossfs_trash_selftest.py`, real EXDEV |
| ~~17~~ | `media.py:69` | The Open dialog offers `.heic/.heif/.avif`; guest ffmpeg 4.4.4 cannot decode any of them. `.heic` is the iPhone default. | **FIXED — verified 2026-08-06.** `.heic/.heif/.avif` are gone from the Open dialog, with the reason recorded in media.py: "deliberately ABSENT. Nothing in this image can decode them." Offering a format the machine cannot open is the truth defect; not offering it is the fix. |
| ~~18~~ | `music.py:980` | An unplayable file fails in **total silence** — no message, anywhere. | **FIXED 2026-08-06** — `tools/music_failure_selftest.py` |
| ~~19~~ | `gbaemu.py:122` | The Fullscreen toggle does nothing; the value is read back only to set the button's own state. | **FIXED 2026-08-06** — toggle and both dead keys removed; `tools/dead_setting_check.py` |
| ~~20~~ | `gbaide.py:214` | Declares an Edit menu it never populates, inheriting four items that cannot act on anything. `_room_clear` wipes a room with no confirm and no undo. | **CLOSED 2026-08-06 — the file is gone.** `gbaide.py` was deleted in 3a75345d and folded into `gbasdk.py`, which populates its Edit menu with undo/redo and guards the room clear with BOTH a confirm and an undo checkpoint. Both halves of this entry are answered. |
| ~~21~~ | `sequencer.py:1750` | Eight Pan sliders that move, relabel, and have **zero audible effect** (the engine is mono). | **NOT A DEFECT — measured 2026-08-06.** The premise is wrong: nbsynth is STEREO (`CHANNELS = 2`) with an equal-power pan. Rendered and measured: hard left L=3939/R=0, hard right L=0/R=3939, centre equal at 0.733 of a hard-panned side (equal-power = 0.707). `tools/sequencer_mix_selftest.py`. |

| 41 | `nbgame.py:258` | The emulator picture is a **fixed 4×**. vbam runs with `-f 17` (kStretch4x), so the game is always 960×640 and `_embed` centres it at that natural size inside a fullscreen stage. On the 1366×768 laptops this targets that is a reasonable 70% of the width; on 1920×1080 it is half; on the HiDPI panels now supported it is a postage stamp in a field of black. vbam's largest enlarging filters are xbrz5x/6x (indices 20/21), so 6× — 1440×960 — is the ceiling available without changing the embed to scale. **Not fixed: unverifiable here.** No ROM has ever been executed by this project's harnesses (see the standing GBA sprite bug) and a video-path change that cannot be run is a guess. | MEDIUM |

| ~~42~~ | `nbsynth.py:716` | ~~**Every drum accent was flattened to one velocity**, in playback and in every exported file. `normalize_song` emitted `(beat, row, velocity)` but read velocity only from index 3, so a second pass substituted the default 100 — and there is always a second pass: `render_wav` normalises, then hands the result to `Mixdown`, which normalises again. The Sequencer's velocity control wrote a value that never reached the sound.~~ | **FIXED 2026-08-06** — normalize_song is idempotent; `tools/sequencer_mix_selftest.py`. Found because the meter read 0.8375 for both a velocity-127 and a velocity-8 track, and 0.8375 is exactly render_drum's factor for velocity 100. |

### 2d. Hollow — looks complete, silently does less

| # | Where | What happens | Size |
|---|---|---|---|
| ~~22~~ | `settings.py:649` | ~~**Seven saved preferences are never re-applied at boot.** Reopen Settings and your choice is still displayed — which reads as confirmation. Screen-blank is worst: `session.sh` actively disables it every boot while the page keeps showing "5 minutes". One line in `session.sh` repairs five controls.~~ | **FIXED 2026-08-06.** The premise was exact, including the detail that made it worst: `_apply_saved_prefs` ran in Settings' `__init__`, so a preference took effect only while Settings was open, and `session.sh` then undid the blanking choice on every boot with `xset s off s noblank -dpms`. New `de/nbprefs.py` applies screen blanking, key repeat and render scale; session.sh runs it straight AFTER that default line, so the appliance default still governs a machine whose owner has never chosen, and a saved value wins where one exists. It imports no Gtk, so it costs the boot path nothing, and settings.py's own `_apply_blank`/`_apply_repeat`/`_x_output` now delegate to it — one implementation, because two copies of "what does 5 minutes mean" is the shape of the original bug. `tools/session_prefs_selftest.py`, 15 checks, reading `xset q` off the real X server rather than capturing the subprocess calls; red-proofed four ways (no boot call / called before the default instead of after / settings.py keeps its own copy / an unset key applied at a default) — 2/1/1/1 failures. The other four of the seven were already deliberate: `background` and the two Mouse keys are ignored by design (their pages were removed), and accessibility is read by nbapp at import so it reaches every app, not just this one. |
| ~~23~~ | `settings.py:2209` | ~~**Time zone does nothing** — no zoneinfo ships. The page's own clock animates to confirm a change that never leaves the process. The most deceptive control in the OS.~~ | **FIXED 2026-08-06.** The premise was half right: no zoneinfo ships, but `_apply_tz` already fell back to a POSIX TZ string and called `time.tzset()`, so Settings' own clock was genuinely correct. The real defect was the second half — it set `os.environ["TZ"]`, which is one process's memory, so the panel clock, Calendar and Journal kept the boot zone. Both handlers (Date & Time and Region & Language) now persist `tz_posix` beside `tz`, and `session.sh` exports TZ from it before anything starts, so every app inherits it. Apps already on screen cannot be handed a new environment, and the page now says so in the Language setting's existing wording (translated to all 17). `tools/settings_timezone_selftest.py`, 16 checks; red-proofed against four separate mutations (drop either handler's save, delete the session export, delete the note) — 2/1/4/2 failures. The Region check runs in a SECOND PROCESS: reusing one window let `_on_region_tz`'s combo sync fire `_on_tz`, which did the save for it, so a broken Region handler passed. |
| ~~24~~ | `settings.py:2060` | Mouse & Touchpad is **entirely inert** — `xinput` is not in the image. The slider says "Faster"; the pointer never changes. | **FIXED** — the inert page was removed; its orphaned re-apply call was the Settings launch crash (task 001) |
| ~~25~~ | `installer.py:879` | The required "Login account" is written to `/etc/passwd` and **never used** — the session hardcodes `NB_HOME=/root` and runs as root. | **FIXED — verified 2026-08-06.** `_create_user` is gone entirely: nothing writes /etc/passwd or /etc/shadow for a second account. This is a single-user appliance (session.sh pins NB_HOME=/root), and the password collected in Options now guards the desktop AND the console through `_configure_login`. `tools/installer_writes_selftest.py`. |
| ~~26~~ | `installer.py:1814` | Swap is partitioned and formatted but **never enabled** (no fstab entry). The user loses the space and gains nothing. | **FIXED — verified 2026-08-06.** `_configure_fstab` appends the swap line, **by LABEL** rather than a device path, so /dev/sda2 becoming /dev/sdb2 cannot break it. Declining swap writes nothing. Same suite; red-proofed three ways including a device-path regression. |
| ~~27~~ | `installer.py:2019` | Keyboard and language choices are **discarded on first boot** — the desktop reads `locale.json`, which the installer never writes. French/AZERTY boots English/QWERTY. | **FIXED — verified 2026-08-06.** `_write_locale_json` writes /root/.config/notebook/locale.json with the chosen keyboard and language — under /root, which is where NB_HOME points, so the desktop actually reads it. Same suite. |
| ~~28~~ | `music.py:1270` | **ID3 tags are never read** although the tag reader is already running. The whole Albums/Artists sidebar is filename guesses. | **NOT A DEFECT — measured 2026-08-06.** `_info_tags` reads title/artist/album off the DiscovererInfo that already runs for the duration. Driven end to end against a hand-tagged `01 track.mp3`: the scan shows `01 track / Unknown Artist`, then the Discoverer replaces it with `Blue Monday / New Order / Power Corruption and Lies` and fills the duration. `tools/music_tags_selftest.py`. |
| ~~29~~ | `video.py:2057` | The Play button is a **still-frame slideshow with no sound**. The export is real and correct. | **FIXED 2026-08-06** — a real `video` clip now STREAMS through `nbvideo.Playback` (playbin -> gtksink, picture and sound), opened at its trim-in and at its per-clip speed. The old one-ffmpeg-process-per-second frame path is kept as the fallback for a machine whose GStreamer cannot open the file. Everything needed was already on the image — no new package. `tools/nbvideo_selftest.py` (14) proves the engine; `tools/video_playback_selftest.py` (9) proves the transport uses it, honours the trim-in, and still falls back. |
| ~~30~~ | `video.py:690` | Split a clip and **both halves show the same frame** — the cache is keyed on path, not trim-in. | **NOT A DEFECT — executed 2026-08-06.** `_frame_key` carries the trim-in, and `_menu_split` advances the second half's `start`. Driven through the real editor: split an 8s clip and the halves come out at start 0.00 / 4.00 with different keys. Covered permanently in `tools/video_playback_selftest.py`; red-proofed by reverting the key to path-only, which reproduces the identical-picture symptom exactly. |
| ~~31~~ | `calendar.py:177` | **Every repeating event silently stops** — a weekly event dies after a year, a birthday after five. The code comment claims it re-extends on edit; it does not. | **FIXED, EXECUTED 2026-08-06** — `_extend_series`; `tools/calendar_selftest.py` 18/18, incl. the short-month clamp |
| ~~32~~ | `sequencer.py:2465` | VU meters are `sin(tick)` — they bounce identically whether the mic is live, muted or absent. | **NOT A DEFECT — measured 2026-08-06.** `Mixdown.track_peak` is taken off the rendered block (`max(max(a), -min(a))`), pre-fader. Two audible tracks at velocity 127 and 8 meter 1.000 and 0.431; a generated waveform could not tell them apart. Same suite. |
| ~~33~~ | `cookbook.py:1670` | **No undo at all** — the only text-editing app without it. Select-all-and-type over a method is unrecoverable. | **FIXED 2026-08-06** — wired to the shared `nbapp.UndoHistory`; Ctrl+Z bound ahead of cook-mode so it works from an ingredient field. The delete confirm is DELETED, not unified: its own sentence read "This cannot be undone", which is now false. `tools/cookbook_undo_selftest.py` — 14 checks. |
| 34 | `ebook.py:91` | EPUBs render **without images, emphasis or tables**; every page of a single-spine book is labelled "CHAPTER 1". | **IMAGES + EMPHASIS DONE 2026-08-06; tables remain.** `<em>/<i>/<cite>` and `<strong>/<b>` travel as Pango markup, and `<img>` is now a block of its own: the href is resolved against the DOCUMENT it appears in (not the .opf), checked against the archive, and read from the zip on demand when the page is built — a picture book is not held in memory. Scaled to the reading measure, never enlarged. **Tables still arrive as a stack of cells**, which the suite pins as a known limit (cells readable and in order). `tools/ebook_formatting_selftest.py` — 18 checks. |
| ~~35~~ | `journal.py:1073` | Bold/Italic/Quote are saved, restored on screen, and **dropped from every export and print**. | **NOT A DEFECT — measured 2026-08-06.** The exported PDF embeds DejaVuSerif, -Bold and -Italic, and the same entry rendered with and without its tags rasterises DIFFERENTLY. Quote is a separate path (emit's italic= and indent=) and is checked on its own. `tools/export_fidelity_selftest.py`. |
| ~~36~~ | `academics.py:2743` | The **highlighter** — the thing a student uses before an exam — is invisible in export. Print is enabled in the Schedule and Homework views but always prints the active lecture note. | **NOT A DEFECT — measured 2026-08-06.** A highlighted run puts **1118 pixels of #FBE7A0** on the rasterised page. Driven through the real buffer and the real capture path. Same suite. |
| ~~37~~ | `screenplay.py:1178` | The "written by" byline is captured, persisted, and **never printed**; Save As overwrites the title the user typed. Pages are half-letter at 9pt, so page count no longer means screen time. | **FIXED 2026-08-06** — the byline is drawn under the title; `tools/screenplay_titlepage_selftest.py` renders a real PDF and reads it back with pdftotext. The *Save As overwrites the title* half is NOT a defect: `_file_save_as` documents it as deliberate ("the title page takes the chosen filename so it always reflects the open file"). Worth a product decision — it loses an authored title as a side effect of naming a file — but it is a choice, not a bug. |
| 38 | `maps.py` | Ships **Monaco and nothing else** (42 KB, 10 place names) behind a box saying "Search cities and towns…". The renderer is excellent; the data is absent. | LARGE |
| ~~39~~ | `language.py:898` | A crown needs a **perfect** lesson and nothing says so — 9/10 forever shows no progress. | **FIXED 2026-08-06** — the end-of-lesson screen now says "A lesson with no mistakes earns the next crown", and only where it can be acted on: not on a perfect run, not on a skill already at CROWN_MAX, not on practice or a unit test. `tools/language_crown_rule_selftest.py` — 8 checks, red-proofed both ways (never shown, and always shown). |
| ~~40~~ | `contacts.py:92` | US-format birthdays (`12/25/1990`) don't parse, silently excluding that person from the birthday banner. | **NOT A DEFECT — executed 2026-08-06.** `parse_birthday` handles both orders and several spellings: 12/25/1990 -> (12,25), 25/12/1990 -> (12,25), 1990-12-25, "25 Dec 1990", "Dec 25 1990", "25 December", "12/25". The ambiguous 3/4/1990 resolves day-first, which is a choice rather than a failure. |

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
