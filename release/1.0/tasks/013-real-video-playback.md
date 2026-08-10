# 013 — Real video playback: the engine, proven, before it is wired

**Lane:** A (video) · **Streams:** S1 truth defects, S8
**Status:** CLOSED — engine landed, transport migrated, ROADMAP #29 struck

## What Play does today

`_on_play` starts a 100ms GLib clock over the storyboard. Every **one second**
(`PLAY_STEP = 1.0`) it spawns an **ffmpeg process to extract a single frame**,
because "each is a whole ffmpeg process, and only one runs at a time". So the
Play button is roughly 1fps and completely silent. Export was always real; only
playback was pretend (ROADMAP #29).

## Everything needed is already on the image

Checked against `buildroot/output/target/usr/lib/gstreamer-1.0/` rather than
this host, because guestrun uses the HOST's GStreamer and would have answered
the wrong question:

    libgstplayback.so   playbin
    libgstlibav.so      the decoders
    libgstisomp4.so     mp4/mov demux
    libgstmatroska.so   mkv demux
    libgstgtk.so        gtksink — hands back a real GtkWidget
    libgstvideoconvertscale.so, libgstalsa.so

**Nothing new ships for this.** (`x264enc` is absent — gst-plugins-ugly is not
in the image — which is why the test fixture is encoded with `avenc_mpeg4` into
`qtmux`, both of which the guest can decode. A fixture built with x264 would
have been testing the developer's machine.)

## `de/nbvideo.py`

One `Playback` class: playbin with gtksink as its video sink, exposing `widget`
for the preview stage to pack, plus `open/play/pause/stop/seek/position/
duration/has_video/teardown` and `on_eos` / `on_error` callbacks.

Three decisions worth recording:

* **gtksink, not gtkglsink.** The target hardware is second-hand laptops, and
  the kernel tree carries no AMD or Nouveau source, so most of them fall to
  software rendering permanently. A GL sink there is slower, not faster.
* **`open()` waits for PAUSED before seeking.** A seek issued before the
  pipeline has pre-rolled is silently dropped — the call still returns True, so
  a trimmed clip would appear to honour its trim-in and play from the start
  instead. The red-proof below shows this is load-bearing.
* **Degrades, never raises** — sequencer.AudioOut's contract exactly. No
  GStreamer, no gtksink, or an unopenable file leaves `available` False and
  every method a no-op, so a build host gets a still preview and a working
  editor rather than a traceback.

## Gate: `tools/nbvideo_selftest.py`

Builds a real 4-second clip and drives the real pipeline. 14 checks. The one
that matters is **"the picture actually advances"**: the position is sampled
twice and must have MOVED (0.60s → 1.61s). A pipeline can reach PLAYING and show
nothing, so "it did not error" is not evidence of a picture.

**Red-proof, two mutations:**

| mutation | result |
|---|---|
| `play()` sets PAUSED instead of PLAYING | 4 fail — position 0.00 → 0.00, EOS never fires |
| `open()` skips the pre-roll wait | 2 fail — `has_video()` false and duration 0.00, because the file has not been parsed yet |

The second is the interesting one: it confirms the pre-roll wait is not
defensive padding but the thing that makes duration, track counts and seeking
answerable at all.

Also fixed while testing: `teardown()` was not idempotent — a second call ran
`remove_signal_watch()` on a bus that no longer had one and printed a
`GStreamer-CRITICAL`. Harmless in itself, and that is exactly the problem: noise
on stderr is how a real CRITICAL goes unread.

## The transport, migrated

Split across two sittings on purpose — engine proven first, then the change to
a 4700-line file — rather than half-wired in one.

**Where the surface lives.** gtksink's widget is packed into the SAME box as the
still `Gtk.Image`, both with a size request and `no_show_all`, and exactly one
is ever visible. Swapping a widget in and out of the stage would have let the
16:9 box resize the moment playback started; sharing the box means it cannot.

**What streams and what does not.** Only a `video` clip with a readable file
goes to the player. A still, a title card and an audio clip have no moving
picture, and the existing frame path already draws them correctly — so the
migration is additive, not a replacement.

**The clock stays authoritative.** The storyboard's GLib clock still owns the
running order, because stills and titles have no stream position to read.
`on_eos` only PAUSES the picture; it does not advance. A clip whose stream ends
a shade early would otherwise jump the timeline ahead of itself.

**Per-clip speed is honoured.** The editor stores a speed and export applies it,
so `Playback.seek()` gained a rate: `seek_simple()` cannot carry one, and a
preview locked at 1x would show a different film from the one that renders.

**Stop releases the device.** A paused playbin holds the audio card, so Stop
takes the pipeline to NULL — otherwise the next app to want sound finds it busy.
`teardown()` on close drops the bus watch first, so an EOS still queued on the
main loop cannot reach a dying window.

## Gate: `tools/video_playback_selftest.py`

Drives the REAL editor with a real project: `_on_play`, the real pipeline. 9
checks, including that the clip reached the player (`_live_clip == 0`), that its
**trim-in was honoured** (source at 3.12s from a 2.0s trim), and that the
position advances.

It also asserts the FALLBACK: with the player removed, the frame path must still
run. A migration that only works where the new engine is present would strand
every machine it is missing on.

**Red-proof:** streaming disabled → 3 of 9 fail, with two reporting
`[not reached: the clip never went to the player]`.

## Standing
All 10 video suites pass, including the two new ones. `self_attr_audit` and
`undefined_names_audit` clean across 65 files (nbvideo.py included).
