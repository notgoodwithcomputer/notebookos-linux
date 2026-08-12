# Govorimo — LoRa mesh chat

The communications app of the no-internet OS: chat, group chat, and public
local boards over LoRa radio, through the Ebyte E22-900T22U dongle the OS
already supports (`docs/LORA-DONGLE.md`). No internet, no accounts, no
servers; the radio horizon is the community.

Upstream lives at `linux-ebyte-lora-chat/` (spec-first; `spec/` is normative,
CC0). Notebook OS is a **Tier-1 client** of that protocol: the reference
daemon does everything hard, and the app is pure interface.

## Architecture

```
de/govorimo.py          the GTK app (four surfaces + ceremonies)
de/govorimolib.py       DaemonLink (async NDJSON client on the GLib loop),
                        BlockingClient (suites/tools), E22 provisioner
/usr/bin/govorimod      the daemon: radio, framing, crypto, mesh, store
                        (static musl build of linux-ebyte-lora-chat/daemon)
/opt/notebook/govorimod-run.sh   session supervisor (radio matching)
```

- The app **never touches key material** — that is the point of the split
  (`linux-ebyte-lora-chat/spec/08-local-api.md`, the only contract the app
  speaks: newline-JSON over `$GOVORIMO_SOCKET`).
- `session.sh` exports `GOVORIMO_SOCKET=/run/govorimo.sock` and
  `GOVORIMO_STAMP=/run/govorimo-provisioned.stamp`, and starts the
  supervisor before sign-in.
- The daemon's state (identity, contacts, messages, boards) lives in
  `$NB_HOME/.config/notebook/govorimo.d/` — its own crash-safe store, NOT an
  nbapp JSON store. The app's `govorimo.json` holds interface state only.

## The supervisor's radio matching

`govorimod-run.sh` keeps one daemon alive and matched to the hardware:

| world                          | daemon runs with        |
|--------------------------------|-------------------------|
| no `/dev/lora`                 | `--radio none`          |
| `/dev/lora`, provisioned stick | `--radio e22:/dev/lora` |
| `/dev/lora`, factory stick     | `--radio none` until the app's provisioning ceremony stamps completion |

With `--radio none` everything except transmission works — identity,
reading, drafts — and the app says so honestly. The app's socket link
retries once a second, so daemon restarts (plug events, provisioning) are
invisible beyond a brief banner.

**Probing is event-gated, never periodic.** A factory dongle sits in
transparent mode on 868.125 MHz and TRANSMITS any bytes written to it —
including a probe's register reads. The supervisor probes once per plug
event and once per provisioning stamp, and never in a loop.

## Provisioning (the one-time hardware ceremony)

The E22-900T22U ships tuned to the EU band with software mode switching
off. Before first use, once per dongle, in the app's Radio surface:

1. hold the dongle's button ~2 s until the LED turns red (config mode);
2. Probe — reads state, changes nothing;
3. Provision — writes `C0 00 09 FF FF 00 E4 24 44 93 00 00`
   (address FFFF, UART **115200**, air 9.6k, CH 68 = 918.125 MHz, RSSI
   byte, LBT, software mode switching), verifies the readback, switches
   the module to transfer mode, and stamps `$GOVORIMO_STAMP` so the
   supervisor restarts the daemon onto the stick.

After that the host has software control permanently; the daemon manages
modes itself. Config mode always answers at 9600; transparent mode runs at
115200 once provisioned (this supersedes LORA-DONGLE.md's factory-default
9600 note for Govorimo-provisioned sticks).

## Building the daemon

```
tools/build_govorimod.sh     # cargo (musl target) -> vendor/govorimo/govorimod
```

Static musl, so the buildroot glibc version can never disagree with it.
`post-build.sh` installs it at `/usr/bin/govorimod` and FAILS the build if
the vendored binary is missing. `vendor/govorimo/` and
`linux-ebyte-lora-chat/target/` are gitignored; the binary is reproducible
from the vendored source.

## Verification

| suite | proves |
|---|---|
| `tools/govorimo_selftest.py` | protocol conformance against the REAL daemon over a wire radio: 45 checks + 5 mutants (identity, bundles, safety numbers, receipts, groups/awaiting-key/rekey, boards, moderation, restart persistence, malformed input) |
| `tools/govorimo_app_selftest.py` | the REAL app driven against real daemons: 25 checks + 2 module mutants (wizard, transcripts, delivery glyphs scoped by msgid, unread accounting, error surfacing, prefs restore, store damage, daemon-restart survival) |
| `tools/govorimo_provision_selftest.py` | the E22 provisioner against a pty fake dongle |

Both suites spawn `vendor/govorimo/govorimod` with `--radio wire:NAME`
(a local multi-process medium — no RF, no hardware). A missing binary is a
FAILURE naming the build command, never a skip.

## First real-hardware day (when the antennas arrive)

1. Plug the dongle in; `dmesg` shows ch341/cp210x, `/dev/lora` appears
   (already gated by `tools/lora_guest_check.sh`).
2. In Govorimo's Radio surface: hold the button until red, Probe,
   Provision. The service picks the stick up by itself.
3. On the second machine, the same; then People → Add Contact — bundles
   cross by USB stick or retyping, and the safety numbers must match aloud.
4. `local.general` is the customary first board.

Never transmit without an antenna attached.
