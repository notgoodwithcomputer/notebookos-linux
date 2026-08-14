# Disc Burner

`de/burner.py`. Puts music on a CD, or video on a DVD that plays in a DVD
player. Two jobs that share a window and almost nothing else.

Proved by `tools/burner_selftest.py` — 78 checks, 8 of them mutants that must go
red, and a final section that builds a real DVD-Video ISO with the tools that
ship.

## What it does

**Music CD.** The chosen songs are decoded to Red Book PCM — 44100 Hz, 16-bit,
stereo, no exceptions, because that is the only thing a CD player can read —
and written **disc-at-once**, so the tracks run into each other with no
two-second hole between them. An 80-minute disc is offered as 79:57: burners
differ in the last few sectors and a round 80:00 fails on the final track.

**Video DVD.** The chosen videos are re-encoded to DVD-compliant MPEG-2, given
a menu, and authored into the `VIDEO_TS` structure a player actually looks for.

The difference between that and a data disc is the point of most of this app. A
disc with the video files merely copied onto it is a folder: a computer opens
it, a DVD player refuses it. What makes it a DVD-Video disc is the IFO
navigation tables, and building those is what `dvdauthor` is for.

## The menu

Deliberately the plainest thing that can still be navigated: **white text on
black**, the disc name, then a numbered list of titles. That is a choice, not
an unfinished state.

It is drawn with cairo at the frame size the standard demands — 720x480 for
NTSC, 720x576 for PAL — so the type is the OS's own type and there is no theme,
gradient or image to go wrong on a television. Everything sits inside a
title-safe inset of a tenth of the frame, because televisions overscan and text
at the true edge of the frame is behind the bezel in the living room.

How a DVD menu actually works, since none of it is guessable:

- the menu is an **MPEG still** with a **subpicture** stream layered over it;
- the subpicture is what lights up under the selected row — a 2-bit image, four
  colours including transparency, so the highlight layer is hard-edged on
  nothing (anti-aliased edges quantise to noise);
- `spumux` muxes that layer in and needs **button rectangles in frame
  coordinates**;
- `dvdauthor` then writes the IFO tables mapping each button to a title.

`menu_layout()` is the single source of those coordinates. The text is drawn
from it, the highlight blocks are drawn from it, and the rectangles handed to
spumux come from it — so the row a person's remote highlights is by
construction the row the text was drawn in. The gate checks exactly that,
because the two are consumed by different tools and a drift between them
produces a disc that looks right and navigates wrong.

A menu holds nine titles: one screen, and a remote has ten digits.

## The pipeline

```
music   songs ──ffmpeg──▶ 44.1k/16/stereo WAV ──wodim -audio -dao -pad──▶ CD

video   videos ─ffmpeg -target ntsc-dvd─▶ MPEG-2 ─┐
        cairo menu.png ─ffmpeg─▶ menu.mpg ─spumux─┼─dvdauthor─▶ VIDEO_TS
                                                  │
                          genisoimage -dvd-video -udf ──▶ ISO ──growisofs──▶ DVD
```

Every slow step runs on an `nbjobs` worker with a cancel token checkpointed
between files. One job key means a second burn cannot start on top of a running
one, and no callback reaches a widget after the window has gone.

### Flags that are not optional

| flag | why |
|------|-----|
| `wodim -dao` | disc-at-once; without it every song gets a 2-second gap |
| `wodim -pad` | a track whose length is not a whole sector is refused |
| `ffmpeg -ac 2` | `-target` does not force it, and a mono title is out of spec |
| `genisoimage -udf` | what a set-top player reads; ISO9660 alone is a coin toss |
| `genisoimage -dvd-video` | lays the files out where a player looks for them |
| `growisofs -dvd-compat` | closes the disc so it plays outside the drive that wrote it |

The video bitrate is **solved from the disc, not picked**: a twenty-minute film
and a two-hour film cannot share a constant, and a fixed "DVD quality" number
either wastes half the disc or overruns at the end of a long film — which is
only discovered after the encode. Below a watchable floor the app says the
programme does not fit rather than burning something not worth watching.

## Packages this added

None of the burning software was in the image. Three buildroot packages, all
with the new app as their consumer:

- **cdrkit** — `genisoimage` (ISO + UDF + DVD-Video layout), `wodim` (CD
  writing, including audio), `icedax`
- **dvdrw-tools** — `growisofs` (DVD writing), `dvd+rw-mediainfo` (what is in
  the drive)
- **dvdauthor** — `dvdauthor` and `spumux`

cdrkit is the media backend rather than xorriso for one reason, and buildroot's
own help text says it: **xorriso does not support UDF.**

ffmpeg needed nothing added — the shipped 4.4.4 already has the `dvd` muxer,
`mpeg2video`, `ac3`/`mp2`, `pcm_s16le`, and decoders for mp3, flac, aac, alac,
opus and vorbis.

The kernel needed nothing either: `CONFIG_BLK_DEV_SR` gives `/dev/sr0` and
`CONFIG_CHR_DEV_SG` gives the SCSI-generic path the burn commands ride on. Both
were already on.

## Traps, each of which cost a run

- **From a VMGM menu you may only `jump title N`.** `jump titleset 1 title 1`
  is rejected — "That form of jumping is not allowed" — and dvdauthor exits 1
  *after* writing a partial `VIDEO_TS`, which looks like a successful build
  until a player refuses the disc. Titles are numbered across the whole disc
  from the menu's point of view.
- **Silence still has to be stereo.** A menu over a mono or absent audio track
  is out of spec and players show black. The menu gets a real silent 48 kHz
  stereo track from `anullsrc`.
- **`/tmp` is a tmpfs on this OS.** Building a DVD there would try to hold some
  nine gigabytes of transcodes, `VIDEO_TS` and ISO in RAM. The scratch
  directory goes in `$NB_HOME` under a dot-name, and the room is checked
  **before** the first frame is encoded — finding out at the ISO step throws
  away the whole encode.
- **`-vn` when decoding audio.** Cover art is a video stream as far as ffmpeg
  is concerned, and without dropping it the WAV is unreadable.

## What is NOT here

- **No data discs.** This burns music and video. A disc of files is a different
  job with a different window.
- **No disc copying, no ripping, no re-authoring an existing DVD.**
- **No blanking of rewritable discs**, so a used CD-RW has to be blanked
  elsewhere before it can be reused.
- **NTSC only in the interface.** The PAL path exists and is checked end to end
  (`burner.PAL`), but nothing yet chooses it — it wants a Settings control or a
  region guess, and guessing wrong wastes a disc.
- **Titles are whole files.** No chapters, no trimming, no ordering beyond the
  list. The Video Editor is where a video gets cut.

## Testing without a burner

There is no drive on a build machine and no blank media in a test, so the gate
covers everything up to the moment the laser turns on — which is where the
defects are, since an unplayable disc was already wrong long before it was
written. Section 6 goes further and runs the app's own generated commands and
XML through the real cross-built `ffmpeg`, `spumux`, `dvdauthor` and
`genisoimage` from `output/target`, then checks the ISO that comes out is
ISO9660 + UDF with a populated `VIDEO_TS` and the right volume label. It skips
visibly when `output/target` has not been built.

**Still owed on hardware:** an actual burn. Everything above proves the disc
image is correct; nothing proves this machine's drive writes it, that the burn
speed is sane, or that the progress parsing tracks `wodim` and `growisofs`
output as they really print it.
