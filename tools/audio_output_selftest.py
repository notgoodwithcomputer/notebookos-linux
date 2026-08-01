#!/usr/bin/env python3
"""
Selftest for de/nbaudio.py — which speakers the sound comes out of.

  PYTHONPATH=<overlay>/opt/notebook/de python3 tools/audio_output_selftest.py

THE BUG THIS EXISTS FOR: sound over HDMI never worked, and could not have. No
code in the OS ever opened an HDMI PCM, nothing ever un-muted IEC958 (HDMI's
mute, which comes up off), and ALSA's "default" follows card 0 — which on any
machine with a USB microphone plugged in is a capture-only device, so NOTHING
played anywhere and `amixer` with no -c read the wrong card, which is why the
boot-time un-mute silently did nothing and Settings ▸ Sound said "No speakers or
sound card found" on a machine with working speakers.

Every case here is a synthetic /proc/asound tree taken from real hardware
layouts, because the one thing this must get right is reading a machine it has
never seen. The cards/PCM layout below is copied from the build host (CX20632
analog on device 0, HDMI on 3/7/8, USB microphone enumerated ahead of it as card
0); the ELD trees are hand-made, because the real machine publishes NINE eld
files for its three pins (three DisplayPort-MST slots each) and the cases worth
testing are the shapes, not that one machine.

NOTHING HERE TOUCHES REAL HARDWARE. `nbaudio._run` is replaced before the first
call, so no amixer runs; and NB_ASOUND_CONF redirects the ALSA configuration
into the throwaway home instead of rewriting the build host's /etc/asound.conf.
Both matter: an earlier version of this file consulted the real card 1 through
amixer while claiming to read a synthetic tree.

Writes only into a throwaway NB_HOME.
"""
import os
import sys
import shutil
import tempfile

HOME = tempfile.mkdtemp(prefix="nbaudio-selftest-")
os.makedirs(os.path.join(HOME, ".config", "notebook"))
os.environ["NB_HOME"] = HOME
os.environ["NB_ASOUND_CONF"] = os.path.join(HOME, "asound.conf")

import nbaudio                                             # noqa: E402

# ---- no real commands, ever ------------------------------------------------
# Recorded so the un-mute sweep can be asserted on, and canned so the
# `HDMI/DP,pcm=N Jack` probe can be driven. A card with no canned answer looks
# like a machine whose driver publishes no jack controls, which is what sends
# nbaudio to the ELD fallback the rest of this file exercises.
CALLS = []
JACKS = {}          # card index -> canned `amixer -c N contents` output


def fake_run(cmd, timeout=4):
    CALLS.append(list(cmd))
    if "contents" in cmd:
        try:
            card = int(cmd[cmd.index("-c") + 1])
        except (ValueError, IndexError):
            return 1, ""
        txt = JACKS.get(card)
        return (0, txt) if txt is not None else (1, "")
    return 0, ""


nbaudio._run = fake_run


def jack_contents(pairs):
    """`amixer contents` as the HDA driver really prints it: the control's name
    on one line, its type on the next, its value on the third."""
    out = []
    for dev, on in pairs:
        out.append("numid=30,iface=CARD,name='HDMI/DP,pcm=%d Jack'" % dev)
        out.append("  ; type=BOOLEAN,access=r-------,values=1")
        out.append("  : values=%s" % ("on" if on else "off"))
    return "\n".join(out) + "\n"

RESULTS = []
FAILED = []


def check(name, cond, detail=""):
    ok = bool(cond)
    RESULTS.append(ok)
    if not ok:
        FAILED.append(name)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "   <- %s" % (detail,)))
    return ok


def section(title):
    print("\n--- %s" % title)


def fake_proc(cards, pcm, elds=None):
    """A /proc/asound tree. `elds` is {card: [(pin, present), ...]}."""
    root = tempfile.mkdtemp(prefix="proc-", dir=HOME)
    with open(os.path.join(root, "cards"), "w") as fh:
        fh.write(cards)
    with open(os.path.join(root, "pcm"), "w") as fh:
        fh.write(pcm)
    for card, entries in (elds or {}).items():
        d = os.path.join(root, "card%d" % card)
        os.makedirs(d, exist_ok=True)
        for i, (pin, present) in enumerate(entries):
            with open(os.path.join(d, "eld#2.%d" % i), "w") as fh:
                fh.write("monitor_present\t\t%d\n"
                         "eld_valid\t\t%d\n"
                         "codec_pin_nid\t\t%s\n"
                         "codec_dev_id\t\t0x0\n" % (present, present, pin))
    return root


# The build host, verbatim: a USB microphone as card 0, the real sound card as
# card 1, analog on device 0 and three HDMI ports on 3, 7 and 8.
INTEL_CARDS = (
    " 0 [H2             ]: USB-Audio - HyperX SoloCast 2\n"
    "                      HP, Inc HyperX SoloCast 2 at usb-0000:00:14.0-11\n"
    " 1 [PCH            ]: HDA-Intel - HDA Intel PCH\n"
    "                      HDA Intel PCH at 0x1ff1010000 irq 131\n")
INTEL_PCM = (
    "00-00: USB Audio : USB Audio : capture 1\n"
    "01-00: CX20632 Analog : CX20632 Analog : playback 1 : capture 1\n"
    "01-02: CX20632 Alt Analog : CX20632 Alt Analog : capture 1\n"
    "01-03: HDMI 0 : HDMI 0 : playback 1\n"
    "01-07: HDMI 1 : HDMI 1 : playback 1\n"
    "01-08: HDMI 2 : HDMI 2 : playback 1\n")
PINS_NONE = {1: [("0x5", 0), ("0x6", 0), ("0x7", 0)]}
PINS_TV_ON_FIRST = {1: [("0x5", 1), ("0x6", 0), ("0x7", 0)]}
PINS_TV_ON_SECOND = {1: [("0x5", 0), ("0x6", 1), ("0x7", 0)]}


# =========================================================== reading the machine
section("reading a real machine")
p = fake_proc(INTEL_CARDS, INTEL_PCM, PINS_NONE)
outs = nbaudio.outputs(p)
keys = [o["key"] for o in outs]
check("every playback device is found", sorted(keys) ==
      ["hw:1,0", "hw:1,3", "hw:1,7", "hw:1,8"], keys)
check("a capture-only USB microphone is NOT offered as an output",
      "hw:0,0" not in keys, keys)
check("the HDMI ports are known to be digital",
      [o["kind"] for o in outs if o["key"] != "hw:1,0"] == ["hdmi"] * 3,
      [(o["key"], o["kind"]) for o in outs])
check("the analog output is labelled for a person, not by codec part number",
      next(o["label"] for o in outs if o["key"] == "hw:1,0")
      == "Speakers and headphones",
      next(o["label"] for o in outs if o["key"] == "hw:1,0"))
check("an HDMI output is labelled as a television",
      "Television" in next(o["label"] for o in outs if o["key"] == "hw:1,3"),
      next(o["label"] for o in outs if o["key"] == "hw:1,3"))
check("with no television plugged in, every HDMI port says so",
      all(o["live"] is False for o in outs if o["kind"] == "hdmi"),
      [(o["key"], o["live"]) for o in outs])

# ================================================================== choosing
section("where sound goes with nobody having said")
check("with nothing on HDMI, sound goes to the built-in speakers",
      nbaudio.auto_pick(p) == "hw:1,0", nbaudio.auto_pick(p))
p_tv = fake_proc(INTEL_CARDS, INTEL_PCM, PINS_TV_ON_FIRST)
check("with a television plugged in, sound follows the picture to it",
      nbaudio.auto_pick(p_tv) == "hw:1,3", nbaudio.auto_pick(p_tv))
check("...and that port is the one reported as attached",
      [o["live"] for o in nbaudio.outputs(p_tv) if o["kind"] == "hdmi"]
      == [True, False, False],
      [(o["key"], o["live"]) for o in nbaudio.outputs(p_tv)])
p_tv2 = fake_proc(INTEL_CARDS, INTEL_PCM, PINS_TV_ON_SECOND)
check("a television in the SECOND HDMI port is found, not the first",
      nbaudio.auto_pick(p_tv2) == "hw:1,7", nbaudio.auto_pick(p_tv2))
check("the attached television is listed first",
      nbaudio.outputs(p_tv2)[0]["key"] == "hw:1,7",
      [o["key"] for o in nbaudio.outputs(p_tv2)])

# a card that publishes no ELD at all must still offer its HDMI port
p_noeld = fake_proc(INTEL_CARDS, INTEL_PCM)
check("an HDMI port on a driver that says nothing is still offered",
      "hw:1,3" in [o["key"] for o in nbaudio.outputs(p_noeld)])
check("...as 'not known' rather than 'nothing plugged in'",
      next(o["live"] for o in nbaudio.outputs(p_noeld) if o["key"] == "hw:1,3")
      is None)

# ============================================================== odd machines
section("machines that are not the common one")
hdmi_only = fake_proc(
    " 0 [HDMI           ]: HDA-Intel - HDA ATI HDMI\n",
    "00-03: HDMI 0 : HDMI 0 : playback 1\n", {0: [("0x5", 1)]})
check("a machine whose ONLY output is HDMI still has an output",
      nbaudio.auto_pick(hdmi_only) == "hw:0,3", nbaudio.auto_pick(hdmi_only))
check("...and its loudness is known to belong to the television",
      nbaudio.has_volume("hw:0,3", hdmi_only) is False)
check("an analog output's loudness can be changed here",
      nbaudio.has_volume("hw:1,0", p) is True)

usb_spk = fake_proc(
    " 0 [Headset        ]: USB-Audio - Jabra USB Headset\n",
    "00-00: USB Audio : USB Audio : playback 1 : capture 1\n")
check("USB speakers are named as USB speakers",
      nbaudio.outputs(usb_spk)[0]["label"] == "USB speakers",
      nbaudio.outputs(usb_spk)[0]["label"])

spdif = fake_proc(
    " 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n",
    "00-00: ALC892 Analog : ALC892 Analog : playback 1 : capture 1\n"
    "00-01: ALC892 Digital : IEC958 : playback 1\n")
check("an S/PDIF socket is named as one, not as a television",
      any(o["label"] == "Digital output (S/PDIF)" for o in nbaudio.outputs(spdif)),
      [o["label"] for o in nbaudio.outputs(spdif)])

empty = fake_proc("--- no soundcards ---\n", "")
check("a machine with no sound card at all reports no outputs",
      nbaudio.outputs(empty) == [], nbaudio.outputs(empty))
check("...and picks nothing rather than raising",
      nbaudio.auto_pick(empty) is None)
check("...and describes itself in words the Settings page can show",
      "no sound card" in nbaudio.describe(empty), nbaudio.describe(empty))
missing = os.path.join(HOME, "not-a-proc-tree")
check("a /proc that is not there is survived",
      nbaudio.outputs(missing) == [] and nbaudio.auto_pick(missing) is None)

# ================================================== the kernel's own jack state
section("which HDMI port the television is really on")
# The ELD ordering above is a guess: nothing in /proc states which pin feeds
# which PCM, and on Intel the binding is made on demand. The driver DOES state it
# in a control named after the PCM. When it does, that must win -- otherwise a
# television on the third port gets sound sent to the first, which is the
# original bug all over again.
JACKS[1] = jack_contents([(3, False), (7, False), (8, True)])
# The probe is cached for a couple of seconds (it sits on the volume-key path),
# so a test that changes the answer has to say the cable moved.
nbaudio._JACK_CACHE.clear()
check("the kernel's own 'HDMI/DP,pcm=N Jack' control decides which port has the "
      "television, not the order of the ELD files",
      nbaudio.auto_pick(p_tv) == "hw:1,8", nbaudio.auto_pick(p_tv))
check("...even though the ELDs claim it is on the first port",
      nbaudio._hdmi_live(1, 0, p_tv) is True)
JACKS[1] = jack_contents([(3, False), (7, False), (8, False)])
nbaudio._JACK_CACHE.clear()
check("with every jack reported empty, no HDMI port is offered as attached",
      all(o["live"] is False for o in nbaudio.outputs(p_tv)
          if o["kind"] == "hdmi"),
      [(o["key"], o["live"]) for o in nbaudio.outputs(p_tv)])
check("...so sound goes to the built-in speakers",
      nbaudio.auto_pick(p_tv) == "hw:1,0", nbaudio.auto_pick(p_tv))
JACKS.clear()

# nbmediakeys asks has_volume() on every volume press and the key auto-repeats
# while it is held, so the probe must not cost a subprocess per repeat.
JACKS[1] = jack_contents([(3, True), (7, False), (8, False)])
nbaudio._JACK_CACHE.clear()
del CALLS[:]
for _ in range(20):
    nbaudio.has_volume(None, p_tv)
probes = len([c for c in CALLS if "contents" in c])
check("twenty volume keypresses in a row cost ONE jack probe, not twenty",
      probes == 1, probes)
JACKS.clear()
nbaudio._JACK_CACHE.clear()

# A card with more than ten ELD slots: "eld#2.10" sorts before "eld#2.2" as a
# string, which silently reorders the pins and points at the wrong port.
many = fake_proc(INTEL_CARDS, INTEL_PCM,
                 {1: [("0x%x" % (5 + i // 4), 1 if i >= 8 else 0)
                      for i in range(12)]})
check("a card with twelve ELD slots orders them by number, not as text",
      nbaudio._hdmi_live(1, 0, many) is False
      and nbaudio._hdmi_live(1, 2, many) is True,
      [nbaudio._hdmi_live(1, i, many) for i in range(3)])

# =============================================== the configuration it writes
section("the ALSA configuration")
del CALLS[:]
applied = nbaudio.apply("hw:1,3", p_tv)
check("applying an output reports what it applied", applied == "hw:1,3", applied)
check("...and writes the ALSA configuration", os.path.isfile(nbaudio.CONF))
rc = open(nbaudio.CONF).read()
check("the configuration goes somewhere alsa-lib reads with no HOME set, "
      "because on the target HOME is '/' and ~/.asoundrc would never be opened",
      nbaudio.CONF.endswith("asound.conf")
      and ".asoundrc" not in nbaudio.CONF, nbaudio.CONF)
check("the default device is redirected", "pcm.!default" in rc, rc)
check("...through plug, so a 44.1 kHz song can open a 48 kHz-only HDMI port",
      "type plug" in rc, rc)
check("...and through dmix, so a second app is not locked out of the speakers "
      "by the first one that is merely PAUSED",
      "dmix:CARD=1,DEV=3" in rc, rc)
check("...with no bare 'type hw' playback slave, which takes the device "
      "exclusively", "type hw\n    card 1\n    device" not in rc, rc)
check("the default is a DUPLEX name, so recording from it is still possible",
      "type asym" in rc and "capture.pcm" in rc, rc)
check("...and its capture leg is the microphone, not the HDMI port",
      "dsnoop:CARD=0,DEV=0" in rc, rc)
check("the mixer default is pinned to the card that is playing, so amixer and "
      "the volume slider stop reading a USB microphone",
      "ctl.!default" in rc and "card 1" in rc.split("ctl.!default")[1], rc)
check("the file says what wrote it and why", "Notebook OS" in rc, rc.splitlines()[:1])

# a machine with nothing to record from must not be given a broken duplex name
del CALLS[:]
nbaudio.apply("hw:0,3", hdmi_only)
rc_nocap = open(nbaudio.CONF).read()
check("a machine with no microphone gets a playback-only default, not an asym "
      "with a capture leg that cannot open",
      "type asym" not in rc_nocap and "dmix:CARD=0,DEV=3" in rc_nocap, rc_nocap)

# a stale per-user file from an older build loads AFTER /etc/asound.conf and
# would win, so ours has to go -- and only ours
legacy = nbaudio.LEGACY_ASOUNDRC
with open(legacy, "w") as fh:
    fh.write("# Written by Notebook OS\npcm.!default { type hw card 9 }\n")
nbaudio.apply("hw:1,3", p_tv)
check("a stale ~/.asoundrc written by an older build is removed, so it cannot "
      "outrank the live configuration", not os.path.exists(legacy))
with open(legacy, "w") as fh:
    fh.write("# the user's own\n")
nbaudio.apply("hw:1,3", p_tv)
check("...but a ~/.asoundrc somebody else wrote is left alone",
      os.path.isfile(legacy) and "user's own" in open(legacy).read())
os.unlink(legacy)

# read-only /etc: the route still has somewhere to go
saved_conf = nbaudio.CONF
nbaudio.CONF = "/proc/definitely-not-writable/asound.conf"
del CALLS[:]
nbaudio.apply("hw:1,3", p_tv)
check("with nowhere in /etc to write, the per-user file is used as a fallback "
      "rather than nothing being written at all",
      os.path.isfile(legacy) and "dmix:CARD=1,DEV=3" in open(legacy).read(),
      os.path.exists(legacy))
check("...and the un-mute still happens, because that half needs no file",
      any("sset" in c for c in CALLS))
nbaudio.CONF = saved_conf
os.unlink(legacy)
nbaudio.apply("hw:1,3", p_tv)

del CALLS[:]
nbaudio.apply("hw:1,3", p_tv)
sset = [" ".join(c) for c in CALLS if "sset" in c]
check("applying an output un-mutes IEC958 — HDMI's mute, and the reason a "
      "television was silent",
      any("IEC958" in s for s in sset), sset[:4])
check("...including the numbered ones a multi-port card has",
      any("IEC958,1" in s for s in sset), [s for s in sset if "IEC958" in s][:4])
check("...and the analog controls as before",
      any("Master" in s for s in sset), sset[:4])
check("every un-mute names the card, so it cannot act on the wrong one",
      all("-c 1" in s for s in sset), [s for s in sset if "-c 1" not in s][:3])
check("HDMI is not asked for a volume level it does not have",
      not any("IEC958" in s and "%" in s for s in sset),
      [s for s in sset if "%" in s])

check("an unparseable output key is refused rather than written",
      nbaudio.apply("garbage", p) is None)

# ============================================================ remembering it
section("remembering the choice")
nbaudio.choose("hw:1,7", p_tv)
check("a chosen output is remembered", nbaudio.saved_choice(p_tv) == "hw:1,7")
check("...and is what is in force", nbaudio.current(p_tv) == "hw:1,7",
      nbaudio.current(p_tv))
check("...and was written to the ALSA configuration",
      "DEV=7" in open(nbaudio.CONF).read())
# CARD INDEXES ARE NOT STABLE ACROSS BOOTS -- the built-in codec waits for i915
# to bind while a USB device is claimed as soon as USB enumerates, which is why
# the microphone is card 0 here at all. A choice stored as an index comes back
# meaning different hardware.
check("the choice is stored by the card's ID, not by its index",
      open(nbaudio.CHOICE).read().strip() == "PCH:7",
      open(nbaudio.CHOICE).read())
SHUFFLED_CARDS = (
    " 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n"
    "                      HDA Intel PCH at 0x1ff1010000 irq 131\n"
    " 1 [H2             ]: USB-Audio - HyperX SoloCast 2\n"
    "                      HP, Inc HyperX SoloCast 2 at usb-0000:00:14.0-11\n")
SHUFFLED_PCM = (
    "00-00: CX20632 Analog : CX20632 Analog : playback 1 : capture 1\n"
    "00-03: HDMI 0 : HDMI 0 : playback 1\n"
    "00-07: HDMI 1 : HDMI 1 : playback 1\n"
    "00-08: HDMI 2 : HDMI 2 : playback 1\n"
    "01-00: USB Audio : USB Audio : capture 1\n")
shuffled = fake_proc(SHUFFLED_CARDS, SHUFFLED_PCM,
                     {0: [("0x5", 0), ("0x6", 0), ("0x7", 0)]})
check("...so after a reboot that enumerates the cards the other way round, the "
      "same choice still means the same hardware",
      nbaudio.saved_choice(shuffled) == "hw:0,7",
      nbaudio.saved_choice(shuffled))
check("...and it is still what is in force there",
      nbaudio.current(shuffled) == "hw:0,7", nbaudio.current(shuffled))
nbaudio.choose(None, p_tv)
check("choosing 'follow the television' forgets the fixed choice",
      nbaudio.saved_choice(p_tv) is None)
check("...and goes back to following it", nbaudio.current(p_tv) == "hw:1,3",
      nbaudio.current(p_tv))
# the case that matters: a choice pointing at hardware that has gone
with open(nbaudio.CHOICE, "w") as fh:
    fh.write("hw:9,9")
check("a remembered output that no longer exists falls back to a real one",
      nbaudio.current(p) == "hw:1,0", nbaudio.current(p))
check("...and applying it writes a working file anyway",
      nbaudio.apply(nbaudio.current(p), p) == "hw:1,0")
with open(nbaudio.CHOICE, "w") as fh:
    fh.write("GONE:3")
check("a card that is not in this machine any more is forgotten, not guessed at",
      nbaudio.saved_choice(p) is None and nbaudio.current(p) == "hw:1,0",
      nbaudio.current(p))
with open(nbaudio.CHOICE, "w") as fh:
    fh.write("hw:1,7")
check("a choice saved by an OLDER build, as a bare index, is still honoured "
      "rather than silently dropped on upgrade",
      nbaudio.current(p_tv) == "hw:1,7", nbaudio.current(p_tv))

# ============================================================== the microphone
section("the microphone")
check("the recording device is the lowest-numbered capture PCM, which is what "
      "ALSA's own default meant before this file existed",
      nbaudio.capture_device(p) == (0, 0), nbaudio.capture_device(p))
check("...and its card is offered separately, because the volume slider is "
      "pinned to the card that PLAYS and the microphone is a different one",
      nbaudio.capture_card(p) == 0, nbaudio.capture_card(p))
check("a machine with nothing to record from says so rather than guessing",
      nbaudio.capture_device(hdmi_only) is None
      and nbaudio.capture_card(hdmi_only) is None)
check("a microphone is never offered as somewhere sound comes OUT of",
      all(o["card"] != 0 for o in nbaudio.outputs(p)),
      [o["key"] for o in nbaudio.outputs(p)])

print("\n%d/%d checks passed" % (sum(RESULTS), len(RESULTS)))
if FAILED:
    print("\nFAILED:")
    for n in FAILED:
        print("  - " + n)
shutil.rmtree(HOME, ignore_errors=True)
sys.exit(0 if all(RESULTS) else 1)
