# Notebook OS Native — program plan

**Goal:** a Notebook OS that is behaviourally and visually identical to the
shipping product, built entirely on code we own, from the boot stub to the
glyph rasteriser, with hardware coverage equal in practice to what the current
Linux build achieves on real machines.

**Status:** plan. Nothing below is built yet. **Written:** 2026-08-06. **
Reference implementation:** the current tree (Linux 7.2-rc3 fork + Buildroot +
GTK3/X11/Python). It is not the thing we are replacing *later* — it is the
executable specification we are replacing it *against*.

- - -
## 1\. Context — why do this at all

The product works. The reason to rewrite it is not features; it is ownership of
the attack surface and of the maintenance future.

Today Notebook OS is assembled from **182 third-party source projects** (296
enabled package options) on a **29.3-million-line** kernel fork, with the desktop
written in **103,905 lines of Python** across 66 modules driving GTK3, X11,
Cairo, Pango, HarfBuzz, GStreamer, ffmpeg, poppler, Ghostscript, LLVM and CUPS.
Every one of those is code we did not write, cannot fully audit, and do not
control the release cadence of.

Measured, not estimated: the third-party source underneath this product is **≈60
million lines** — 29.3M of kernel plus 30.3M of userspace, counting only the code
that is actually built into the image.

For a machine whose entire premise is that **it has no networking** — the kernel
fork already strips `drivers/net` to zero source files, and `CONFIG_INET`, `
CONFIG_PACKET` and `CONFIG_WIRELESS` are all unset — carrying 29 million lines
of general-purpose kernel is a strange bargain. We inherit the CVE stream of a
codebase built for servers, phones, mainframes and 25 architectures, in order
to run a paper-themed writing desk on a laptop.

Three concrete consequences drive the decision:

1.  **Supply chain.** 182 upstreams is 182 places a compromise can enter that we
    would not detect. An air-gapped machine's threat model is dominated by what
    arrives *inside the image* and over *USB* — precisely the two paths a
    dependency compromise travels.
2.  **Memory safety.** The overwhelming majority of exploitable defects in every
    component we currently ship — kernel, image codecs, font engine, PDF
    parser, media demuxers — are memory-safety bugs in C. Rewriting in a
    memory-safe language deletes that class rather than patching instances of
    it.
3.  **Longevity.** The kernel fork is already a manual strip of an upstream that
    moves weekly. Every rebase costs more than the last, and the strip is a
    subtraction we have to redo forever. Owning the code turns an unbounded
    recurring cost into a bounded one-time one.

There is also a gap we should close while we are here: **the current build has no
disk encryption at all.** Verified in the shipping desktop kernel config — no `
DM_CRYPT`, no `CRYPTO_XTS`, no `TCG_TPM`, and no `cryptsetup` or LUKS anywhere
in the package set. For a device sold on data safety, an unencrypted store is
the largest single hole in the product, and it is far easier to design in than
to retrofit.

- - -
## 2\. What "identical" means, precisely

"Identical" is only useful if it is testable. It resolves into five contracts,
each of which already has an artefact in-tree that can be turned into a gate.


|Contract |Means                                               |Source of truth today                                                       |
|---------|----------------------------------------------------|----------------------------------------------------------------------------|
|**Pixel**|Same window renders the same pixels at the same size|`tools/uishot.py`, `tools/appshot.py`, `tools/dialogshot.py` via `tools/guestrun.sh`|
|**Behaviour**|Same input sequence produces the same state change  |the 250 scripts in `tools/*.py`                                             |
|**Format**|Files written are byte-compatible in both directions|`$NB_HOME/.config/notebook/\<app>.json`, `.writer`, `.nbm2`, `.gba`         |
|**Text** |Same string in all 17 languages, same layout        |`lang_*.json` × 17, `docs/MENU-CONVENTIONS.md`                              |
|**Paper**|Printed output matches page-for-page                |PDF diff of the print pipeline                                              |

The rule that governs the whole program is the project's existing one, and it
applies to the rewrite itself:

> **An OS may do less than the user hoped. It may never lie about what it does.**

So the native build ships an app only when it passes the conformance gate for
that app. Until then the app is *hidden*, exactly as `finder.HIDDEN_APPS` does
today — the module and its tests exist, the tile does not. The native OS is
released when the hidden list is empty, not when the code is written.

### 2.1 Where identity is explicitly not the goal

Three current behaviours are artefacts of the Linux stack, not decisions. The
native build must not reproduce them:

- **Panel dropdowns render behind a focused window.** This is a matchbox WM
  limitation; six approaches failed against it. Our own compositor fixes it by
  construction — the panel is a layer, not a window that has to win a stacking
  argument.
- **AMD and NVIDIA laptops run software rendering permanently**, because the
  kernel source for `radeonsi`/`nouveau` was stripped. Our driver plan changes
  what is reachable here (§4.3).
- **TV/ATSC is unbuildable** — no `MEDIA_SUPPORT`, no DVB, no Kconfig to add it.
  In the native tree this becomes an ordinary scheduling question again.

Everything else is identity, including things that look like bugs and are not.
The prior audits established that most apparent inconsistencies were
deliberate; the native build inherits the decision, not a fresh opinion. 
**Verify, don't unify.**

- - -
## 3\. The central claim: why "all computers" is reachable

This is the part of the request that sounds impossible, so it gets the most
rigour. It is reachable — but only because of what this product *isn't*.

### 3.1 The number that matters is 1,430, not 19,863,503

Linux's `drivers/` tree in our fork is **19.9 million lines**. Nobody is
rewriting that, and nobody needs to: it is 30 years of support for 25
architectures, every device ever manufactured, server and datacentre hardware,
virtualisation, and a networking stack this product deliberately does not have.

The honest measure of our obligation is the **shipping kernel config**: `
kbuild-desktop/.config` enables **1,430** `CONFIG_*=y` symbols. That is the
hardware surface the product actually stands on, and the surface the native
kernel must reproduce. The core of it is remarkably small, and — this is the
key structural fact — **almost all of it is standardised class interfaces, not
per-vendor code**:


|Enabled today                      |What it is                          |Vendor-specific?|
|-----------------------------------|------------------------------------|----------------|
|`CONFIG_EFI`, `CONFIG_ACPI`        |firmware + platform                 |No — specs      |
|`CONFIG_BLK_DEV_NVME`, `SATA_AHCI` |storage controllers                 |No — specs      |
|`CONFIG_USB_XHCI_HCD`              |every USB controller since ~2010    |No — spec       |
|`CONFIG_USB_STORAGE`, `MMC_SDHCI`  |sticks, SD cards                    |No — specs      |
|`CONFIG_HID_GENERIC`               |keyboards, mice, touchpads, gamepads|No — spec       |
|`CONFIG_DRM_SIMPLEDRM`             |**display on any GPU, any vendor**  |No — UEFI GOP   |
|`CONFIG_SND_HDA_INTEL`, `SND_USB_AUDIO`|audio in/out                        |Mostly spec     |
|`CONFIG_USB_PRINTER`               |printing over USB                   |No — spec       |
|`CONFIG_EXT4_FS`, `CONFIG_VFAT_FS` |filesystems                         |No — formats    |
|`CONFIG_DRM_I915`                  |Intel GPU acceleration              |**Yes**         |

Roughly **fifteen public specifications** cover the machine. Only the GPU
acceleration tier is genuinely per-vendor.

`REAL-HARDWARE.md` already proved the thesis empirically without meaning to: the
UEFI test image runs on **any GPU vendor with no per-vendor driver and no firmware**
, via the firmware framebuffer. That is not a degraded fallback we tolerate —
it is the architectural floor that makes "all computers" a claim we can
actually stand behind.

### 3.2 The three-tier hardware promise

Rather than a binary "supported / unsupported", the native OS makes a tiered
promise it can keep on any machine, and states the tier in Settings so the user
is never lied to about what their hardware is doing.

- **Tier 0 — Universal.** Any UEFI x86_64 machine. Boots, full desktop, every
  app, printing, audio, USB. Display via the firmware framebuffer, rendering in
  software. Requires no vendor driver and no firmware blob. *This tier is the
  product.* Everything else is an optimisation.
- **Tier 1 — Accelerated.** Intel (Gen7+) and AMD (GCN+) integrated graphics get
  a native KMS + GPU driver: hardware compositing, faster first frame, video
  decode offload. Covers the large majority of laptops in the field.
- **Tier 2 — Certified.** Specific machines on a rack that run the full
  conformance suite every night, including lid, battery, brightness, sleep,
  audio jacks, touchpad gestures and external displays. The certified list
  ships in the product and grows over time.

The claim we make in public is Tier 0: **"runs on any 64-bit PC made in the last
fifteen years."** That is true, testable, and not what anyone else can say.

### 3.3 Printers

Same structure. "All printers" resolves to four tiers, and the current build
already discovered the right primary path (`package/ippusb`, plus two upstream
CUPS bugs found on the way):

- **Driverless (IPP Everywhere / AirPrint / Mopria)** over IPP-over-USB. Covers
  effectively every printer sold since ~2015, with no per-model code. We
  generate PWG-Raster and Apple Raster ourselves. This is the main path.
- **Legacy page languages:** our own PCL5e, PCL6/XL and ESC/P2 back-ends. Covers
  most of 2000–2015.
- **Host-based families:** the three we already carry — Brother (`brlaser`),
  Canon (`captdriver`), Samsung (`splix`) — reimplemented natively. These are
  the "modern printer gets a 20-year-old driver preselected" case that bit us
  before.
- **Unknown:** name it. A printer we cannot drive says so, and offers "save as
  PDF to a USB stick" rather than pretending.

The halftoning, dithering and colour path is ours (replacing Ghostscript and
cups-filters), which is a meaningful chunk of work but entirely spec-driven.

- - -
## 4\. Architecture

### 4.1 Language and toolchain decision

**Rust, `\#\![no_std]` with `core` \+ `alloc` only, and zero third-party crates
anywhere in the tree.** Everything above `core` — allocator, collections we need
beyond `alloc`, string handling, all I/O — is ours.

Rationale: the entire point of the rewrite is to delete the memory-safety
defect class from the kernel, the drivers, and every parser (fonts, PDF,
images, USB descriptors, filesystems). Writing it in C would re-import exactly
the class we are paying to escape. Rust is the only mature systems language
that removes it without a garbage collector.

**Unsafe policy.** `\#\![forbid(unsafe_code)]` in every crate except an
explicit, audited set (`hal-*`, `mmio`, `alloc-phys`, and the DMA layer). The
total count of `unsafe` blocks in the tree is a tracked number with a budget. It
may shrink; a change that grows it needs a written justification in the commit.

**The compiler is a build-time dependency, and we say so.** We do not write our
own Rust compiler; that would cost a year and buy little. The trusting-trust
exposure is mitigated instead by (a) fully reproducible builds — same input,
same bytes, verified by an independent rebuilder — and (b) diverse
double-compiling against a second toolchain build. Both are cheap and both are
gates, not aspirations.

**We do write our own build system.** Cargo is a network-fetching dependency
resolver; a project with zero external dependencies does not need one, and
should not ship a tool whose default instinct is to download code.

### 4.2 System shape

A **monolithic kernel with hard internal isolation**, not a microkernel. A
microkernel's IPC cost is paid on every frame and every disk block, and its
benefit — driver fault isolation — is bought more cheaply here by memory safety
plus IOMMU-enforced DMA. Where isolation genuinely matters, we get it in
userspace instead:

- **Every parser runs sandboxed.** Fonts, PDFs, images, audio and video decode,
  EPUB, and USB descriptors are all processed in a capability-restricted
  process with no filesystem and no IPC beyond one pipe. This is the single
  highest-value security decision in the design, because untrusted files
  arriving by USB are the realistic attack path for this machine.
- **Capability-based syscall surface**, roughly 60 calls. A process holds
  handles; there is no ambient authority, no global namespace to walk, and no `
  fork` \+ `exec` inheritance surprise.
- **There is no socket syscall.** Not disabled — *absent*, with a test that
  fails if one appears. This costs nothing to adopt: the shipping desktop
  kernel already has no `CONFIG_BT`, no bluez in the package set, and no
  networking, so the socket surface is empty in practice today. The native
  build only makes the current de-facto state structural, so it cannot drift
  back. **Product decision to confirm:** this forecloses ever adding Bluetooth.
  Its only consumer was removed with BitChat, so the cost is presently zero —
  but it is a door being welded shut, and that should be a decision rather than
  a side effect.
- **W^X throughout, no runtime code generation, no dynamic loader in the TCB.**
  Applications are statically linked. There is no `LD_PRELOAD` because there is
  no `ld.so`.

### 4.3 Layer map and honest sizing

Estimates are of *our* code, in thousands of lines of Rust, after the reductions
above. They are for planning the shape of the fleet, not a promise.


|Layer         |Contents                                                                                                  |~KLOC |
|--------------|----------------------------------------------------------------------------------------------------------|------|
|**Boot**      |Own UEFI application, measured boot, TPM PCR extension, image verify, kernel load                         |15    |
|**HAL x86_64**|GDT/IDT/APIC/IOAPIC, SMP bring-up, paging, TSC/HPET, MSRs, microcode load                                 |40    |
|**Memory**    |Physical allocator, VM, slab, page cache, DMA + IOMMU (VT-d / AMD-Vi)                                     |45    |
|**Kernel core**|Scheduler, threads, capabilities, IPC, syscalls, process model                                            |55    |
|**ACPI**      |Table parsing **and an AML interpreter** — battery, lid, thermal, sleep, buttons                          |110   |
|**PCIe**      |Enumeration, config space, MSI/MSI-X, ASPM, hotplug                                                       |20    |
|**USB**       |xHCI, core, hub, HID, mass storage/UAS, audio class, printer class, UVC, CDC                              |120   |
|**Storage**   |NVMe, AHCI/SATA, SDHCI/eMMC, block layer, GPT/MBR                                                         |65    |
|**Input**     |i8042, I2C-HID, precision touchpad protocol, event layer, hotplug                                         |35    |
|**Filesystems**|Own journaling FS, FAT32/exFAT, ISO9660/UDF, ext4 (read, for migration)                                   |110   |
|**Encryption**|AES-XTS, Argon2id, TPM sealing, full-disk + per-app keys                                                  |20    |
|**Display Tier 0**|GOP framebuffer, EDID, mode selection, multi-head, HiDPI                                                  |25    |
|**Display Tier 1**|Intel + AMD KMS: link training, DP/HDMI/eDP, planes, GPU submission                                       |350   |
|**Audio**     |HDA controller + codec graph, USB Audio Class, SOF/I2S DSP, mixer, resampler, low-latency capture         |110   |
|**Platform**  |Battery, AC, thermal, backlight, lid, power buttons, suspend/resume                                       |25    |
|**2D renderer**|Paths, AA, gradients, compositing, clipping, transforms (Cairo's job)                                     |45    |
|**Font engine**|TrueType `glyf` **and CFF/Type2** (we ship Nimbus Sans OTF), hinting, caching                             |40    |
|**Text shaping**|GSUB/GPOS, Devanagari reordering, Hebrew, CJK — HarfBuzz's job                                            |60    |
|**Unicode**   |Bidi (UAX#9), line breaking (#14), segmentation (#29), normalisation (#15), collation                     |40    |
|**Compositor**|Own display server + window manager + compositing (X11 + matchbox + picom)                                |45    |
|**Toolkit**   |Windows, buttons, entries, text view, tree view, dialogs, drawing areas, scrolling, the token theme system|120   |
|**Input methods**|IME framework, Pinyin, Kana/Romaji, Hangul, press-and-hold diacritics                                     |35    |
|**Image codecs**|PNG, JPEG (dec+enc), GIF, BMP, TIFF, WebP, SVG                                                            |90    |
|**Audio codecs**|WAV, FLAC, Vorbis, MP3, AAC; encode for WAV/FLAC/Vorbis                                                   |70    |
|**Video**     |H.264 decode + encode, MPEG-4, VP8/9, MP4/MKV/AVI/OGG containers                                          |180   |
|**Documents** |PDF write (subsetting, transparency), PDF read/render, EPUB + an HTML/CSS subset engine                   |140   |
|**Print**     |Spooler, IPP client, IPP-over-USB, PWG/URF raster, PCL5e/PCL6/ESC-P2, halftone + colour                   |80    |
|**GBA SDK**   |ARM7TDMI + GBA emulator; **C compiler, assembler, linker** for `arm-none-eabi`                            |160   |
|**Apps**      |All 66 modules — 104K lines of Python becomes ~350K of Rust                                               |350   |
|**Build + image**|Build system, image builder, installer, update path, reproducibility                                      |30    |
|              |**Total**                                                                                                 |**~2,530**|

Two and a half million lines is a real number, not a shrug. It is also about **4%
of the ~60M lines we currently ship** to do the same job, which is the whole
argument in one figure.

### 4.4 Migration

App data is already JSON under `$NB_HOME/.config/notebook/\<app>.json`, so user
data migration is nearly free — the native apps read the same files. The native
installer must also **read ext4** to import an existing Linux-era install, which
is why ext4 (read-only) is on the list. Documents (`.writer`), maps (`.nbm2`),
courses and ROMs keep their formats byte-for-byte; the format contract in §2 is
what enforces that.

- - -
## 5\. Phases and gates

Each phase ends at a gate that is a *demonstration*, not a checklist. The fleet
does not advance a phase until the demo runs on real hardware.

### Phase 0 — Specification and harness (weeks 1–4)

Nothing is written in Rust yet. We extract the specification from the running
product, because every day spent here removes a month of ambiguity later.

- **Mechanical spec extraction.** Dump every UI string × 17 languages, every menu
  and its ordering, every keyboard binding, every file format with a worked
  example, every design token, every window's minimum size, from the running
  build. Output is `spec/` — machine-readable, regenerated from the reference on
  demand, diffable.
- **Golden corpus.** Screenshots of every window and dialog in every language at
  1024×740 and at HiDPI scale, via `tools/guestrun.sh` so they carry the real
  theme and fonts — the trap that once made 15.6% of pixels wrong. Golden PDFs
  from every print path. Golden store files from every app.
- **Dual-run conformance harness.** One scenario file drives the reference build
  and the native build and diffs pixels, bytes and PDFs. This harness is the
  most important artefact in the program and it is built first.
- **Device trace capture.** A tracing shim on the reference build records real
  register and DMA traffic for NVMe, xHCI, HDA, i915 and SDHCI on the rack
  machines. These traces become deterministic replay tests, which is how thirty
  agents write drivers without thirty laptops. See §6.3.
- **Gate:** the harness runs the reference build against *itself* and reports
  zero diffs; then a deliberately sabotaged reference build makes it go red. A
  gate that has never been proven able to fail is not a gate.

### Phase 1 — Vertical slice (months 2–5)

One narrow path through every layer, on real hardware, before any breadth.

Boot our own UEFI stub → own kernel → own scheduler → NVMe → own FS →
framebuffer → compositor → toolkit → **Calculator**, in English, with a real
keyboard, matching the golden screenshot.

- **Gate:** a Tier-0 laptop cold-boots to Calculator, pixel-identical to the
  reference, in under 10 seconds, with no Linux code anywhere in the image.

This gate is the whole program's risk retired. Everything after it is volume.

### Phase 2 — Universal baseline (months 5–12)

Tier 0 on any UEFI x86_64 machine, full desktop shell.

USB stack, HID, storage breadth, ACPI/AML, audio, the full toolkit, text
shaping and Unicode for all 17 languages, IMEs, the compositor's real
behaviours (panel layers, transitions, the paper physics in 
`docs/PAPER-PHYSICS.md`), login, Finder, Settings, the widget board, installer,
disk encryption.

- **Gate:** ten different laptops from five vendors, 2012–2026, all boot to a
  usable desktop from the same image with no per-machine configuration. The
  desktop shell passes conformance in all 17 languages.

### Phase 3 — Application parity (months 8–20, overlapping Phase 2)

All 66 modules. Ordered by dependency depth, not by size: the shared layers
first (`nbapp`, `nbi18n`, `nbicons`, `nbprint`, `nbaudio`, `nbstate`, `nbmotion`
, `nbtransitions`), then leaf apps in parallel, then the four heavy ones (`gbasdk`
, `sequencer`, `video`, `finder`) which each own a lane.

Media codecs, the document stack, and print land here because the apps need
them.

- **Gate:** the hidden-app list is empty. Every app passes pixel, behaviour,
  format, text and paper conformance in all 17 languages.

### Phase 4 — Acceleration and breadth (months 14–24)

Intel and AMD KMS + GPU acceleration. Audio DSP topologies for modern laptops.
The legacy printer tiers. Video encode. Suspend/resume. External displays and
docks.

- **Gate:** Tier 1 machines show measurably faster first-frame than Tier 0 — the
  app-launch latency that is currently 10× on software rendering. Printer rack
  passes across all four tiers.

### Phase 5 — Hardening and release (months 20–30)

Continuous fuzzing of every parser, an external security review, the
reproducible build gate, the diverse double-compile, measured boot with TPM
sealing, signed USB updates, the certification programme, and the full
documentation set (`guide/` rewritten where behaviour differs).

- **Gate:** Notebook OS Native 1.0 — conformance-identical, on its own code, on
  the certified list, with a published hardware promise it keeps.

**Honest range: 20–30 months to 1.0-native, with a usable Tier-0 desktop at 9–12
months.** The schedule risk is not code volume — the fleet is fast at that. It is
hardware bring-up, which is serial, physical, and does not parallelise.

### 5.6 Recalibration — what a dual-fleet month actually changes

The phase durations above were written before the throughput question was asked
properly, and they inherit an assumption that turns out to be wrong: that code
volume sets the schedule. Working out one month of both fleets running
continuously — **`docs/NATIVE-MONTH-1.md`**, the full 30-day execution plan —
shows it does not.

The model there lands on **500K–900K merged lines in the first month**, which is
a large fraction of the whole 2.5M-line estimate. Two consequences follow, and
they point in opposite directions:

- **Phase 1's vertical slice moves from month 5 to month 1** — in QEMU on
  virtio rather than on a laptop, because the rack cannot arrive that fast. The
  program's core risk is retired almost immediately, and everything after it is
  breadth on a proven spine.
- **The remaining phases barely move**, because what binds them is hardware
  bring-up, conformance convergence and one human's decision latency — none of
  which respond to fleet size at all.

Provisional revision, to be rewritten against measured data on Day 30 rather
than against arithmetic: **12–18 months to 1.0-native**, with all code written
and unit-gated by months 3–6. The reduction comes from exactly one place, and
the fact that it is not larger is the more important half of the finding.

There is also a corollary about how to spend the fleets later: throughput is
**bimodal**. Specified work — Unicode tables, format parsers, the port of 104K
lines of existing Python — scales with agent count. Novel, tightly coupled work
— the scheduler, the AML interpreter, text shaping, the GPU drivers — runs at a
few thousand lines a day no matter how many agents are pointed at it. Brooks's
law did not stop applying because the developers are models. So the novel lanes
start on **Day 1** and are protected from churn, and once generation saturates
the fleets get re-tasked to verification rather than to more code.

- - -
## 6\. How the fleet works

Two max-plan fleets (Claude Code and Codex) running continuously. The scarce
resource is not tokens; it is **coherence**. Thirty agents editing one OS will
produce thirty incompatible OSes unless the structure forbids it.

### 6.1 One crate, one lane, one owner

The tree is ~50 crates. Each maps to exactly one lane, and a lane is the only
thing permitted to modify its crate. Cross-lane changes are requests, not
edits. Lanes correspond to the rows in §4.3.

### 6.2 The interface freeze

All cross-crate traits and wire formats live in a single `contracts/` crate that **
only the architect lane may edit.** A change there is an RFC file, a version
bump, and a regeneration of conformance stubs for every consumer.

This is the anti-collision mechanism and it is not optional. The failure mode
it prevents is the one this project has already documented: *an isolated test
cannot see a contract the rest of the system breaks*, and whoever writes it last
wins. Freezing the interface means nobody writes it last.

### 6.3 Every crate is testable on the host, without hardware

This is what makes a large fleet possible.

- **Kernel crates** test against a mock HAL in a userspace harness.
- **Driver crates** test against the **recorded device traces** from Phase 0. An
  NVMe driver is developed against a replay of real NVMe traffic and is correct
  or not before it ever touches a board. This also makes driver tests
  deterministic, which hardware tests never are.
- **Toolkit and app crates** test through the offscreen renderer, the same way `
  tools/uishot.py` works today — real theme, real fonts, no display.
- **Parsers** are fuzzed continuously from day one, not at the end.

An agent that cannot verify its own work on the host is an agent producing
unreviewed code. No lane opens until its host harness exists.

### 6.4 Adversarial pairing between the fleets

The project's own history is unambiguous: four adversarial audit rounds found
23, 22, 20 and 16 real defects, and the severity never dropped to zero. Reviews
find what tests do not.

So the fleets are paired, not merged: **fleet A implements a lane, fleet B writes
the adversarial conformance suite for it, and they swap by phase.** The reviewer
never sees the implementation before writing the tests — the tests are derived
from `spec/`, which came from the reference build.

### 6.5 The ratchet

Carried forward from the current project's hard-won rules, because every one of
these was learned by being burned:

- Every gate must be **demonstrated able to go red** by sabotaging the thing it
  guards. A green gate that has never failed is decoration.
- Coverage may not decrease. A crate's proven-red gate list only grows.
- A crashed test suite is a **failure**, never a pass — the baseline must not
  launder it as one.
- Nightly, the full image builds from scratch and boots on the rack. A crate
  that breaks boot is reverted automatically, not triaged.
- Reproducibility is a gate from Phase 1, not a Phase 5 aspiration.
  Retrofitting determinism into a 2.5M-line tree is far more expensive than
  never losing it.

### 6.6 The hardware rack

Serial, physical, and the actual critical path — so it is built in Phase 0, not
when it is needed.

- **~15 laptops**: Intel (Gen 7 → current), AMD (GCN → current), NVIDIA discrete
  hybrid, 2012–2026, at least three with HiDPI panels, at least two with SOF
  audio, one Secure Boot machine with a real TPM.
- **~10 printers**: driverless IPP, plus Brother, Canon, Samsung host-based, plus
  one PCL5 and one ESC/P2 legacy unit.
- **Automation**: network-controlled power, USB boot-media switching, and capture
  cards for framebuffer output, so nightly boot testing needs no human. Without
  automation the rack is a bottleneck; with it, it is a gate.

- - -
## 7\. Risk register — what will not work, stated plainly

Writing these down now is the difference between an engineering plan and a
brochure. Each has a decided response.


|Risk                         |Reality                                                                                                                   |Response                                                                                                                                                                     |
|-----------------------------|--------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
|**NVIDIA discrete acceleration**|Needs signed firmware; even nouveau cannot reclock modern parts. Not solvable by effort.                                  |Tier 0 framebuffer + the integrated GPU on hybrid laptops. Settings says which GPU is driving the display. Never claim otherwise.                                            |
|**Modern laptop audio (SOF)**|Needs vendor DSP firmware and topology files                                                                              |**Firmware is data, not code.** Loading a vendor blob into a device does not put third-party code in our TCB. We load it; we do not write it. Same for Intel GuC/HuC and AMD PSP.|
|**AML interpreter**          |~110 KLOC of interpreting vendor-authored bytecode whose bugs are worked around inside Linux's ACPICA by decades of quirks|Highest-uncertainty item in the plan. Start it in Phase 1, not Phase 2, and budget a quirk table from day one.                                                               |
|**Text shaping for 17 languages**|Devanagari reordering and CJK are where naive shapers die                                                                 |Golden text rendering per script is a Phase 0 artefact, so regressions are visible immediately. The existing catalog-coherence work already knows the traps.                 |
|**H.264 patents**            |Core patents have expired; some peripheral claims have not                                                                |Legal review before Phase 3. Fallback: decode-only, or ship VP9/AV1-first with H.264 decode as a hidden app until cleared.                                                   |
|**HTML/CSS subset for EPUB** |"A subset" is how rendering engines start before they eat a decade                                                        |Hard scope limit written into `contracts/`: EPUB 3 core, no scripting, no arbitrary CSS. If a book needs more, say so and render the text.                                   |
|**Trusting trust in `rustc`**|We do not own the compiler                                                                                                |Reproducible builds + diverse double-compiling, both as gates. Revisit a self-hosted toolchain only after 1.0.                                                               |
|**Hardware bring-up is serial**|The one thing thirty agents cannot parallelise                                                                            |Rack automation in Phase 0; device trace replay so 90% of driver work happens on the host.                                                                                   |
|**Fleet coherence decays**   |Thirty agents drift                                                                                                       |The interface freeze (§6.2), one-crate-one-lane, and the nightly boot gate. If drift appears, phases slow — lanes are never widened to compensate.                           |

- - -
## 8\. Cut order

If the schedule must give, it gives in this order — top items cut first, bottom
items never. The rule is that a cut item is *hidden and stated*, never silently
degraded.

1.  **arm64 port** — deferred entirely to post-1.0. The HAL boundary is designed
    for it now; nothing else is spent on it.
2.  **GBA C toolchain** — keep shipping the prebuilt `arm-none-eabi` on-guest
    and write our own compiler after 1.0. It is a self-contained project that
    does not block the OS.
3.  **Video encode** — the editor becomes export-to-lossless until it lands.
4.  **Tier 1 GPU acceleration** — Tier 0 software rendering is a shipping
    configuration, not a failure. This is a large cut (~350 KLOC) available
    late.
5.  **Legacy printer tiers** — driverless covers post-2015; older printers get
    the honest "save as PDF" path.
6.  **ext4 read** — migration by USB export instead of in-place import.
7.  **Never cut:** the conformance harness, disk encryption, the parser sandbox,
    reproducible builds, the absence of a socket syscall, or the honesty rule.

- - -
## 9\. Immediate next actions

Week 1, in this order:

1.  **Freeze the reference.** Tag the current tree and the 1.3 ISO as `
    reference/1.3-golden`. Every conformance claim in the program is measured
    against exactly this build, forever. It must be byte-reproducible from the
    tag before anything else starts.
2.  **Write `spec/` extraction.** Mechanical dumps of strings, menus, bindings,
    formats, tokens and window minimums from the reference. No hand-authored
    specification — hand-authored specs drift from the product on day two.
3.  **Build the dual-run harness** and prove it can go red against a sabotaged
    reference.
4.  **Order the rack** — laptops and printers, per §6.6. Longest lead time in the
    program; ordering it in month six costs six months.
5.  **Write `contracts/` v0.1** — the syscall surface, the driver model, the
    toolkit's widget and event contracts, the app lifecycle. Thin, but frozen.
6.  **Start the ACPI/AML lane immediately.** It is the longest pole that is not
    hardware, and it is the item most likely to be underestimated.
7.  **Stand up device trace capture** on the reference build, and record the
    first traces (NVMe, xHCI, HDA) from two rack machines.

Phase 1's vertical slice does not begin until items 1, 2, 3 and 5 are done.
Starting the kernel before the specification exists is the one mistake that
would make the whole program unfalsifiable.

These seven are Days 1–2 of **`docs/NATIVE-MONTH-1.md`**, which works the first
30 days out in full: the throughput model, the 48-lane roster with fleet
assignments, the day-by-day schedule, the operating protocol that keeps twenty
concurrent agents coherent, and the Month-1 gate.

- - -
## 10\. The one-paragraph version

We are replacing ~60 million lines of borrowed code with about 2.5 million
lines of our own, for a machine that has no network, one architecture, and one
job. The rewrite is affordable because the product's hardware surface is
fifteen public specifications rather than thirty years of vendor drivers — the
shipping kernel config proves it at 1,430 options, and the existing UEFI test
image already runs on every GPU vendor with no vendor driver at all. Identity
with the current product is enforced mechanically by a dual-run harness built
before any Rust is written, so "identical" is a diff, not an opinion. Two agent
fleets run continuously against frozen interfaces, one crate per lane, with
every lane testable on the host and every gate proven able to fail. Working out
the first month in full (`docs/NATIVE-MONTH-1.md`) shows code volume stops
binding almost immediately — the vertical slice lands in month one, not month
five — so the honest estimate is **12–18 months**, and the reason it is not
shorter is that hardware bring-up, conformance convergence and one human's
decision latency do not care how many agents you have. The things that will not
work — NVIDIA acceleration, some DSP audio — are written down here rather than
discovered by a user.

