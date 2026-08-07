#!/usr/bin/env python3
"""
nbaudio — which speakers the sound comes out of.

THE BUG THIS EXISTS FOR: sound over HDMI never worked. The kernel side was
complete all along (SND_HDA_CODEC_HDMI, the Intel/ATI/NVIDIA HDMI codecs and
SND_HDA_I915 are all built in), and nothing in userspace ever pointed at it, in
three separate ways, each of which is enough on its own to produce silence from
a television:

  1. NOTHING EVER OPENED AN HDMI DEVICE. A sound card's HDMI output is a
     different PCM device from its headphone socket -- on an Intel machine the
     analog jack is device 0 and the HDMI ports are devices 3, 7 and 8. Every
     player here (GStreamer playbin -> alsasink) opens ALSA's "default", and
     with no configuration "default" means device 0. Audio therefore always
     went to the headphone socket, whatever was on screen.

  2. THE DIGITAL OUTPUT WAS LEFT MUTED. session.sh un-mutes Master, Speaker,
     Headphone, PCM, Front and Line-Out -- every one of them an analog control.
     HDMI's mute lives on "IEC958 Playback Switch", which a fresh ALSA state
     brings up muted, exactly like the analog ones it already knew to fix.

  3. "default" FOLLOWS CARD 0, WHICH NEED NOT BE THE SOUND CARD. Plug in a USB
     microphone and it enumerates as card 0, ahead of the built-in codec. ALSA's
     default then points at a capture-only device, so NOTHING plays anywhere --
     and `amixer` with no -c reads that card too, so session.sh's un-mute
     silently did nothing and the Settings ▸ Sound page reported "No speakers or
     sound card found" on a machine with perfectly good speakers.

The fix is one file of ALSA configuration, written from what /proc/asound says
is really there, plus the un-mute sweep that goes with it. Everything here is
stdlib and reads /proc -- no new package, and no daemon.

Nothing raises. A machine with no sound card at all must reach the Settings page
and be told so, not crash on the way.
"""
import os
import re
import subprocess
import time

PROC = "/proc/asound"
HOME = os.environ.get("NB_HOME", os.path.expanduser("~"))
# WHERE THE CONFIGURATION GOES, and why it is NOT ~/.asoundrc.
#
# alsa-lib loads /etc/asound.conf and then "~/.asoundrc", and it expands that
# tilde with getenv("HOME") (src/userfile.c; this build has no wordexp, and the
# wordexp path consults HOME first too). On this machine HOME is "/" -- the
# kernel hands init HOME=/, busybox init deliberately leaves it alone, and
# session.sh sets NB_HOME=/root without touching HOME. A file written to
# $NB_HOME/.asoundrc is therefore a file alsa-lib never opens: the whole fix
# would be a no-op on the target, silently, with the selftest still green.
#
# /etc/asound.conf depends on no environment variable at all, is read by every
# ALSA client including ones udev starts with an empty environment, and this is
# a single-account appliance where "per-user" buys nothing. The root filesystem
# is mounted rw at boot (see /etc/inittab), and a failed write is survivable --
# apply() falls back to the per-user file and then just returns None.
#
# NB_ASOUND_CONF exists so the selftest can point this at a throwaway path
# instead of rewriting the build host's real audio configuration.
CONF = os.environ.get("NB_ASOUND_CONF", "/etc/asound.conf")
# Older builds wrote here. alsa-lib loads it AFTER /etc/asound.conf, so a stale
# copy would silently outrank the live one; apply() removes it (ours only).
LEGACY_ASOUNDRC = os.path.join(HOME, ".asoundrc")
MARKER = "Written by Notebook OS"
# Which output the user chose. Kept beside the file it generates so a stale
# choice and a stale asound.conf can never disagree.
CHOICE = os.path.join(HOME, ".config", "notebook", "audio-output")

# The mixer switches that carry sound on each kind of output. IEC958 is the
# digital one (HDMI and S/PDIF); the rest are analog. Numbered variants exist
# because a card with three HDMI ports has three of them.
ANALOG_CTLS = ("Master", "Speaker", "Headphone", "PCM", "Front",
               "Front Speaker", "Line-Out")
DIGITAL_CTLS = tuple(["IEC958"] + ["IEC958,%d" % i for i in range(1, 8)])


def _run(cmd, timeout=4):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception:
        return 1, ""


def _read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


# ------------------------------------------------------------------ discovery
def cards(proc=PROC):
    """{index: (id, name)} for every sound card, from /proc/asound/cards.

    Lines look like

         1 [PCH            ]: HDA-Intel - HDA Intel PCH
                              HDA Intel PCH at 0x1ff1010000 irq 131
    """
    out = {}
    for ln in _read(os.path.join(proc, "cards")).splitlines():
        m = re.match(r"\s*(\d+)\s*\[([^\]]*)\]\s*:\s*(.*)$", ln)
        if m:
            idx, cid, rest = m.groups()
            name = rest.split(" - ", 1)[-1].strip() or cid.strip()
            out[int(idx)] = (cid.strip(), name)
    return out


def playback_pcms(proc=PROC):
    """[(card, device, name, is_digital, raw)] for every PCM that can PLAY.

    `name` is what to show; `raw` is every name field the kernel printed, joined,
    because which KIND of digital output this is (a television or an S/PDIF
    socket) is sometimes only stated in the field that is not the display name.

    From /proc/asound/pcm, whose lines look like

        01-00: CX20632 Analog : CX20632 Analog : playback 1 : capture 1
        01-03: HDMI 0 : HDMI 0 : playback 1

    A capture-only device has no "playback" field, which is what makes a USB
    microphone sortable out of the outputs instead of being offered as one.
    """
    out = []
    for ln in _read(os.path.join(proc, "pcm")).splitlines():
        m = re.match(r"\s*(\d+)-(\d+):\s*(.*)$", ln)
        if not m:
            continue
        card, dev, rest = m.groups()
        if "playback" not in rest:
            continue
        # `01-01: ALC892 Digital : IEC958 : playback 1` — the two name fields
        # need not agree, and on a Realtek S/PDIF socket it is the SECOND one
        # that says IEC958. Judge on both; show the first.
        fields = [f.strip() for f in rest.split(":")]
        names = [f for f in fields
                 if f and not f.startswith(("playback", "capture"))]
        name = names[0] if names else rest.strip()
        digital = bool(re.search(r"HDMI|DisplayPort|IEC958|SPDIF|S/PDIF",
                                 " ".join(names), re.I))
        out.append((int(card), int(dev), name, digital, " ".join(names)))
    return out


def capture_pcms(proc=PROC):
    """[(card, device, name)] for every PCM that can RECORD, lowest card first.

    Needed because ALSA's "default" is a *duplex* name. The card-specific
    definition this file replaces (cards/HDA-Intel.conf and friends) is a
    `type asym` with a separate capture leg, so pointing "default" at a
    playback-only PCM -- an HDMI device has no capture stream at all -- does not
    merely fail to record, it makes `arecord -D default` impossible. The
    replacement has to carry a capture leg of its own.
    """
    out = []
    for ln in _read(os.path.join(proc, "pcm")).splitlines():
        m = re.match(r"\s*(\d+)-(\d+):\s*(.*)$", ln)
        if not m:
            continue
        card, dev, rest = m.groups()
        if "capture" not in rest:
            continue
        fields = [f.strip() for f in rest.split(":")]
        names = [f for f in fields
                 if f and not f.startswith(("playback", "capture"))]
        out.append((int(card), int(dev), names[0] if names else rest.strip()))
    out.sort()
    return out


def capture_device(proc=PROC):
    """(card, device) of the microphone "default" should record from, or None.

    Lowest card index first, which is what ALSA's own "default" did before this
    file existed: on a machine with a USB microphone that is the USB microphone,
    and on a machine without one it is the built-in codec's input.
    """
    caps = capture_pcms(proc)
    return (caps[0][0], caps[0][1]) if caps else None


def capture_card(proc=PROC):
    """Card index of the recording device, or None -- so `amixer` can be told
    which card's Capture control to move. ctl.!default is pinned to the card
    that PLAYS, which need not be the card that records."""
    cap = capture_device(proc)
    return cap[0] if cap is not None else None


_JACK_CACHE = {}          # card -> (when, {device: present})
_JACK_TTL = 2.0           # seconds


def _jack_map(card):
    """{pcm device: is something plugged in} straight from the kernel.

    The HDA driver publishes one control per HDMI/DisplayPort pin NAMED WITH ITS
    PCM DEVICE NUMBER -- `HDMI/DP,pcm=3 Jack` -- so this is the kernel stating
    the pin-to-device mapping outright instead of us inferring it. Preferred over
    the ELD heuristic below, which can only guess at the order and on this build
    host has nine ELD files for three pins.

    Returns {} when the tool or the controls are absent, and the caller falls
    back to the ELD.

    Cached for a couple of seconds because this is on the VOLUME KEY path:
    nbmediakeys asks has_volume() for every press, the key auto-repeats while it
    is held, and a subprocess per repeat is not something a keyboard should cost.
    A cable is not plugged in twice in two seconds.
    """
    hit = _JACK_CACHE.get(card)
    if hit is not None and (time.monotonic() - hit[0]) < _JACK_TTL:
        return hit[1]
    rc, out = _run(["amixer", "-c", str(card), "contents"], timeout=2)
    if rc != 0:
        _JACK_CACHE[card] = (time.monotonic(), {})
        return {}
    res, pending = {}, None
    for ln in out.splitlines():
        m = re.search(r"name='HDMI/DP,pcm=(\d+) Jack'", ln)
        if m:
            pending = int(m.group(1))
            continue
        if pending is None:
            continue
        m = re.search(r"values=(on|off)\s*$", ln.strip())
        if m:
            res[pending] = (m.group(1) == "on")
            pending = None
    _JACK_CACHE[card] = (time.monotonic(), res)
    return res


def _eld_index(name):
    """Sort key for `eld#2.10` -- the number after the dot is a decimal index,
    so a plain string sort puts the tenth pin between the first and the second.
    """
    m = re.match(r"eld#(\d+)\.(\d+)$", name)
    return (int(m.group(1)), int(m.group(2))) if m else (1 << 30, 0)


def _hdmi_live(card, hdmi_index, proc=PROC):
    """Is a television plugged into this card's Nth HDMI port? (ELD fallback.)

    The kernel publishes one /proc/asound/card<N>/eld#<codec>.<slot> file per
    HDMI pin per DisplayPort-MST slot, carrying `monitor_present`. This assumes
    the HDMI PCMs come up in the same order as their pins, so the Nth distinct
    pin belongs to the Nth HDMI PCM. That is a HEURISTIC, not a fact: on Intel
    the pin-to-converter binding is made on demand (the codec dump says
    "Devices: 0" for every pin), and nothing in /proc states the mapping. It is
    only consulted when _jack_map, which does state it, has nothing to say.

    Returns True (something is attached), False (nothing is), or None when the
    card publishes no ELD at all -- in which case the port is still offered,
    because refusing to list an output on a machine whose driver is quiet about
    it would take away the only way to reach a working television.
    """
    d = os.path.join(proc, "card%d" % card)
    try:
        elds = sorted((f for f in os.listdir(d) if f.startswith("eld#")),
                      key=_eld_index)
    except OSError:
        return None
    if not elds:
        return None
    pins = {}          # pin nid -> present?
    order = []
    for f in elds:
        body = _read(os.path.join(d, f))
        pin = re.search(r"codec_pin_nid\s+(\S+)", body)
        pres = re.search(r"monitor_present\s+(\d+)", body)
        if not pin:
            continue
        key = pin.group(1)
        if key not in pins:
            pins[key] = False
            order.append(key)
        if pres and pres.group(1) != "0":
            pins[key] = True
    if hdmi_index >= len(order):
        return None
    return pins[order[hdmi_index]]


def outputs(proc=PROC):
    """Every place sound can come out of, best first.

    [{"key": "hw:1,3", "card": 1, "device": 3, "label": "Television (HDMI)",
      "kind": "hdmi"|"analog", "live": True|False|None}]

    The label is what a person is called upon to choose between, so it says
    "Television (HDMI)" and "Speakers and headphones" rather than repeating the
    codec's part number at them.
    """
    cs = cards(proc)
    outs = []
    hdmi_seen = {}
    jacks = {}
    for card, dev, name, digital, raw in playback_pcms(proc):
        cid, cname = cs.get(card, ("", "Sound card"))
        if digital:
            n = hdmi_seen.get(card, 0)
            hdmi_seen[card] = n + 1
            if card not in jacks:
                jacks[card] = _jack_map(card)
            # The kernel's own pin-to-device statement first; the ELD ordering
            # heuristic only when it has none for this device.
            live = jacks[card].get(dev)
            if live is None:
                live = _hdmi_live(card, n, proc)
            label = "Television (HDMI)" if n == 0 else "Television (HDMI %d)" % (n + 1)
            if (re.search(r"IEC958|SPDIF|S/PDIF", raw, re.I)
                    and not re.search(r"HDMI|DisplayPort", raw, re.I)):
                label = "Digital output (S/PDIF)"
            outs.append({"key": "hw:%d,%d" % (card, dev), "card": card,
                         "device": dev, "label": label, "kind": "hdmi",
                         "live": live, "pcm": name, "cardname": cname})
        else:
            usb = "usb" in cname.lower() or "usb" in cid.lower()
            label = "USB speakers" if usb else "Speakers and headphones"
            outs.append({"key": "hw:%d,%d" % (card, dev), "card": card,
                         "device": dev, "label": label, "kind": "analog",
                         "live": None, "pcm": name, "cardname": cname})
    # An HDMI port with a television on it first, then the built-in speakers,
    # then everything else -- the order somebody scanning the list would expect.
    def rank(o):
        return (0 if (o["kind"] == "hdmi" and o["live"]) else
                1 if o["kind"] == "analog" else 2, o["card"], o["device"])
    outs.sort(key=rank)
    return outs


def auto_pick(proc=PROC):
    """Where sound should go with nobody having said. A television that is
    plugged in wins: it is the screen the picture is on, and its speakers are
    the ones the viewer is sitting in front of. Otherwise the built-in ones."""
    outs = outputs(proc)
    for o in outs:
        if o["kind"] == "hdmi" and o["live"]:
            return o["key"]
    for o in outs:
        if o["kind"] == "analog":
            return o["key"]
    return outs[0]["key"] if outs else None


def _token(key, proc=PROC):
    """"hw:1,3" -> "PCH:3": the card's ID STRING and the device.

    CARD INDEXES ARE NOT STABLE ACROSS BOOTS. They are handed out in probe-
    completion order, and on this machine snd_hda_intel waits for the i915
    component to bind (CONFIG_SND_HDA_I915) while a USB audio device is claimed
    the moment USB enumerates -- which is exactly why the built-in codec is card
    1 and a microphone is card 0 on the build host. Storing "hw:1,3" in a file
    that outlives the boot therefore stores a number that can come back meaning
    different hardware. The id in /proc/asound/cards ("PCH", "HDMI", "H2") comes
    from the driver and the card itself and does not move.

    The index form is still what everything in memory passes around, because
    that is what ALSA takes; only the SAVED form is by id.
    """
    m = re.match(r"hw:(\d+),(\d+)$", key or "")
    if not m:
        return None
    idx, dev = int(m.group(1)), int(m.group(2))
    cid = cards(proc).get(idx, ("", ""))[0]
    return "%s:%d" % (cid, dev) if cid else None


def _untoken(tok, proc=PROC):
    """"PCH:3" -> "hw:1,3" against the cards present NOW, or None if that card
    is not in this machine any more. Also accepts the old index form so an
    upgrade does not lose the setting."""
    tok = (tok or "").strip()
    if not tok:
        return None
    if re.match(r"hw:\d+,\d+$", tok):
        return tok
    cid, _, dev = tok.rpartition(":")
    if not cid or not dev.isdigit():
        return None
    for idx, (this_id, _name) in cards(proc).items():
        if this_id == cid:
            return "hw:%d,%s" % (idx, dev)
    return None


def saved_choice(proc=PROC):
    """The output the user picked as "hw:<card>,<device>" for the machine as it
    is now, or None. Empty means "follow the television", so unplugging it goes
    back to the speakers by itself."""
    return _untoken(_read(CHOICE), proc)


def current(proc=PROC):
    """The output in force: what was chosen if it is still there, else auto."""
    keys = [o["key"] for o in outputs(proc)]
    saved = saved_choice(proc)
    if saved and saved in keys:
        return saved
    return auto_pick(proc)


# ------------------------------------------------------------------- applying
def _conf_text(card, device, cap=None):
    """The ALSA configuration that sends sound to one PCM.

    This does what ALSA's own per-card default (cards/HDA-Intel.conf,
    cards/USB-Audio.conf) does, and changes only WHICH card and device it aims
    at. Deviating from that shape is what a first cut of this file got wrong, in
    two ways that both end in silence:

      * `plug` is not optional. An HDMI output need not accept 44.1 kHz, so
        pointing an app straight at hw:1,3 can make a 44.1 kHz song fail to open
        at all -- silence with an error nobody sees. plug converts.

      * `dmix` is not optional either, because `type hw` under the plug takes the
        device EXCLUSIVELY. "One app at a time" is not true of sound: the
        Sequencer's render engine holds its pipeline in PLAYING for as long as the
        window is open, Music and Video hold the device open while PAUSED, and
        the GBA emulator opens it through SDL. Measured on the build host, two
        openers of plughw:1,3 give the second "Device or resource busy", and
        through dmix both play. Worse, the Sequencer latches its failure for the
        life of the process, so it would stay silent even after the other app
        closed. dmix is what the stock configuration used, so this keeps it.

      * `type asym` with a capture leg, because "default" is a DUPLEX name. An
        HDMI device has no capture stream, so a playback-only "default" does not
        merely fail to record -- it makes recording from "default" impossible.

    ctl.!default matters just as much: it is what `amixer` with no -c talks to,
    so pinning it to the card that is playing is what makes the volume control
    and the un-mute sweep act on the right hardware instead of on a USB
    microphone that happened to enumerate first.

    (The two ancient M-Audio devices that cards/USB-Audio.conf excludes from
    dmix are not honoured here; naming them would be copying a blacklist that
    alsa-lib already applies to its own default and this file replaces.)
    """
    head = ("# Written by Notebook OS (Settings > Sound). Sound plays through\n"
            "# card %d device %d. Regenerated at every start-up from what\n"
            "# /proc/asound reports, so editing it by hand does not last.\n"
            % (card, device))
    out = ('pcm.nbout {\n'
           '    type plug\n'
           '    slave.pcm "dmix:CARD=%d,DEV=%d"\n'
           '}\n' % (card, device))
    ctl = ("ctl.!default {\n"
           "    type hw\n"
           "    card %d\n"
           "}\n" % card)
    if cap is None:
        # Nothing on this machine can record. `type asym` insists on both legs,
        # so ask for the one that exists rather than a broken duplex name.
        return head + 'pcm.!default {\n    type plug\n    slave.pcm "nbout"\n}\n' \
            + out + ctl
    return (head
            + 'pcm.!default {\n'
              '    type asym\n'
              '    playback.pcm "nbout"\n'
              '    capture.pcm "nbin"\n'
              '}\n'
            + out
            + 'pcm.nbin {\n'
              '    type plug\n'
              '    slave.pcm "dsnoop:CARD=%d,DEV=%d"\n'
              '}\n' % cap
            + ctl)


def unmute(card=None):
    """Take the mute off every output this card has, analog AND digital.

    The digital half is the part that was missing: IEC958 is HDMI's mute, it
    comes up off, and no amount of turning Master up moves it.
    """
    base = ["amixer", "-q", "-M"] + (["-c", str(card)] if card is not None else [])
    for ctl in ANALOG_CTLS:
        _run(base + ["sset", ctl, "unmute"])
    for ctl in DIGITAL_CTLS:
        _run(base + ["sset", ctl, "unmute"])
    # A level only exists on the analog side; HDMI carries no volume of its own
    # (the television's remote is the volume control), so there is nothing to
    # raise there and asking would only print an error.
    for ctl, level in (("Master", "85%"), ("Speaker", "90%"), ("PCM", "90%")):
        _run(base + ["sset", ctl, level, "unmute"])


def _write(path, text):
    """Replace `path` with `text` atomically. False on any filesystem refusal --
    a read-only /etc must not be fatal."""
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = path + ".new"
        with open(tmp, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(path + ".new")
        except OSError:
            pass
        return False


def apply(key, proc=PROC):
    """Send sound to `key` ("hw:<card>,<device>"), or auto when key is None.

    Writes /etc/asound.conf and un-mutes what that output needs. Returns the key
    actually applied, or None when the machine has no output at all -- or when
    nowhere would take the file, because a route nobody can read is not applied.
    """
    if key is None:
        key = auto_pick(proc)
    if not key:
        return None
    m = re.match(r"hw:(\d+),(\d+)$", key)
    if not m:
        return None
    card, device = int(m.group(1)), int(m.group(2))
    text = _conf_text(card, device, capture_device(proc))
    if _write(CONF, text):
        # A leftover per-user file from an older build loads AFTER this one and
        # would win with stale contents. Remove ours; never anyone else's.
        try:
            if MARKER in _read(LEGACY_ASOUNDRC):
                os.unlink(LEGACY_ASOUNDRC)
        except OSError:
            pass
    elif not _write(LEGACY_ASOUNDRC, text):
        # Nowhere to put it (read-only root AND no home): un-mute anyway, since
        # that half needs no file, but say the route was not applied.
        unmute(card)
        return None
    unmute(card)
    return key


def choose(key, proc=PROC):
    """Remember the user's choice (None = follow the television) and apply it.

    Stored by card ID rather than card index -- see _token: the index can mean
    different hardware after a reboot, and a setting that quietly re-points at
    another card is worse than one that is forgotten."""
    tok = "" if key is None else (_token(key, proc) or "")
    _write(CHOICE, tok)
    return apply(key, proc)


def has_volume(key=None, proc=PROC):
    """Can this output's loudness be changed here at all?

    False for HDMI: the level belongs to the television. Saying so is the
    difference between a slider that does nothing and a sentence that explains
    who has the volume control."""
    key = key or current(proc)
    for o in outputs(proc):
        if o["key"] == key:
            return o["kind"] != "hdmi"
    return True


def describe(proc=PROC):
    """One line per output for the log/diagnostics, e.g.

        * hw:1,3  Television (HDMI)          HDMI 0            attached
          hw:1,0  Speakers and headphones    CX20632 Analog
    """
    cur = current(proc)
    lines = []
    for o in outputs(proc):
        live = ("attached" if o["live"] else
                "nothing plugged in" if o["live"] is False else "")
        lines.append("%s %-7s %-26s %-18s %s"
                     % ("*" if o["key"] == cur else " ", o["key"], o["label"],
                        o["pcm"], live))
    return "\n".join(lines) or "no sound card found"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "apply":
        # current(), NOT saved_choice(): this is the call session.sh makes on
        # every boot, and a remembered output can be GONE by the time it runs --
        # the USB headset it was chosen on is unplugged, or the disk was moved to
        # another machine. apply() takes a key on trust, so handing it a stale
        # one writes a pcm.!default pointing at a card that does not exist, and
        # then NOTHING plays anywhere and the un-mute sweep talks to no card
        # either: exactly the silence this file exists to prevent. current()
        # falls back to a real output when the remembered one has gone.
        print(apply(current()) or "no output")
    elif len(sys.argv) > 1 and sys.argv[1] == "unmute":
        cur = current()
        unmute(int(cur.split(":")[1].split(",")[0]) if cur else None)
    else:
        print(describe())
