# 063 — Govorimo: LoRa mesh chat over the E22 dongle

**Status: IN PROGRESS** (opened 2026-08-12, lane `govorimo` claimed in CLAIMS.md)

User directive: implement Govorimo fully — one of the OS's killer apps; antennas
arrive within days and the app must be ready when they do. Both Claude and Codex
coding. UI/UX bar: perfect, per the letterpress design thesis.

## What Govorimo is

Peer-to-peer messaging over LoRa radio for the no-internet OS: 1:1 and group
chat, Usenet-style radio-local boards. Spec-first project vendored at
`linux-ebyte-lora-chat/` (untracked upstream source; specs in `spec/`,
reference Rust implementation `core/ daemon/ client/ gui/`). The wire is
915 MHz LoRa through the Ebyte E22-900T22U USB dongle already supported by the
OS (`docs/LORA-DONGLE.md`, `/dev/lora` udev contract, kernel serial modules).

## Architecture decision (taken)

**Ship the reference daemon; write a native client.** `govorimod` (Rust) owns
radio, framing, crypto, mesh, storage behind a local NDJSON Unix-socket API
(`spec/08-local-api.md`). The NOS app is a pure-UI GTK client — it never
touches key material. Rationale: the daemon is tested (e2e over SimRadio),
the split is the spec's own Tier-1 path, and a Python crypto rewrite would be
risk with no upside.

- Daemon build: **static musl** (`cargo build --release --target
  x86_64-unknown-linux-musl -p govorimo-daemon`), so buildroot-glibc vs host
  glibc never matters. Vendored stripped at `vendor/govorimo/govorimod`
  (2.7MB). Host smoke test: 18/18 green (identity, mnemonic, bundles, safety
  numbers agree, 1:1 chat + real delivery receipts, boards with verified
  pseudonyms, status shapes, error codes) via two daemons on `--radio
  wire:NAME`.
- Client: `de/govorimo.py` (app) + `de/govorimolib.py` (async GLib NDJSON
  client + E22 provisioning serial helpers, stdlib only).
- Radio absence is a first-class state: daemon runs `--radio none` when
  /dev/lora is absent (identity, history, drafts all work); session
  supervision restarts it as `e22:/dev/lora` when the dongle appears.
- Provisioning: the E22 needs a one-time button+register ceremony
  (`spec/07-radio-profile.md` §3): profile `C0 00 09 FF FF 00 E4 24 44 93 00
  00` (addr FFFF, UART 115200, air 9.6k, CH 68 = 918.125 MHz, RSSI byte, LBT,
  sw-mode-switch). The app's Radio surface owns this ceremony — no shell
  scripts for users. NOTE: post-provision transparent UART is 115200, not the
  9600 factory default recorded in LORA-DONGLE.md (addendum owed).

## Files (lane-owned)

- `de/govorimo.py` — the app
- `de/govorimolib.py` — daemon client + provisioner
- `tools/govorimo_selftest.py` — protocol conformance suite (Codex R1, against
  the real vendored daemon over wire radio)
- further `tools/govorimo_*_selftest.py` — display battery etc.
- `vendor/govorimo/govorimod` — vendored static daemon
- `release/1.0/i18n-fragments/063-govorimo.*` — catalogs x17
- Shared files additive-only, riding the sweep uncommitted: finder.py,
  gen_nbicons/nbicons_data, session.sh, post-build.sh, .gitignore
  (+linux-ebyte-lora-chat/target/), data_safety/store_damage/design_tokens/
  perf_baseline rows.

## UX (docs/ux.md of the upstream repo, translated into NOS voice)

Four surfaces: Chats, Boards, People, Radio. Honest delivery states
(queued/sent/relayed/delivered/failed — never a confirmation not received);
airtime meter always visible; composer prices each message in ms of shared
airtime; group ops quote their airtime cost before running; radio horizon =
the community (no directory, no search-users); ceremony where trust is made
(mnemonic shown once; bundle exchange in person; safety number read aloud);
calm by construction (no typing indicators, no presence polling — the
protocol forbids them). Empty states teach the physics in NOS voice (facts +
create action, no second person).

## Progress log

- 2026-08-12 14:2x — musl daemon built + vendored; host smoke 18/18; lane
  claimed; Codex R1 dispatched (protocol conformance suite, display-free).
- 2026-08-12 15:0x — DaemonLink proven 12/12 (async calls, events, fast-fail,
  reconnect-with-identity across daemon restart). App built: four surfaces,
  wizard ceremony, exchange/group/members/role/provisioning cards. Renders
  audited via seeded two-daemon scenes; defects found BY RENDERING and fixed:
  Radio-surface crash on reopen (link built after first _show_surface),
  Gtk.Stack refusing to switch to unshown children (the harness scramble),
  wrapped-label minimums blowing grid columns off-screen (deterministic
  340px cards now), overlay cards laid out at Fixed-minimum (explicit body
  widths), empty red badge pills, mnemonic card mis-centred (recentre on
  size-allocate, overlay-alloc based).
- 2026-08-12 15:2x — Codex R1 landed: 45 checks + 5 mutants vs the real
  daemon, 28s. One suite expectation corrected by me (daemon canonicalises
  board-name case; the stronger check is same-board-id). Registrations:
  finder APP_MODULES/APP_KIND ("Messaging"), Lucide "radio" glyph (139
  icons regenerated), Govorimo.app stub, perf_baseline, data_safety
  _ALLOWED row (stamp file), store_damage coverage row, design-token radii.
  Guest wiring: govorimod-run.sh supervisor (event-gated probing — a probe
  of a factory stick TRANSMITS on 868 MHz, so once per plug event + once
  per provisioning stamp, never periodic; host-rehearsed 4/4), session.sh
  block, post-build installs vendor/govorimo/govorimod and FAILS without it.
- 2026-08-12 16:0x — fragments 063 x17 written BY HAND (157 new keys + 7
  reused injected byte-identical; spec-parity validated mechanically),
  merged x17 -> 3723 keys, UNHIDDEN. Battery green: i18n_check 100% x17,
  menu_conformance 1006 PASS, minsize 1001x324 (was 1041 — Radio cards
  360->340), construct_all 42/42, data_safety 126/126, store_damage ALL
  PASS, tokens clean, Russian full-surface visual pass clean. App display
  suite tools/govorimo_app_selftest.py: 25 checks + 2 module mutants, both
  red-proved (M1 caught a VACUOUS PROBE of my own — delivered-check passed
  against history when the msgid capture raced the async append; scoped to
  new msgids now).

## Owed / riding

- ISO spin + on-guest boot-verify: build train is campaign-owned (LANES
  rule 2) — request filed in HANDOFF with the exact steps (ffmpeg-dirclean
  for x264 first; post-build now REQUIRES the vendored daemon). Everything
  on the overlay side ships automatically with the next spin.
- Codex R2 (tools/govorimo_provision_selftest.py, pty fake-E22) in flight.
- Physical-stick day runbook: docs/GOVORIMO.md "First real-hardware day".
