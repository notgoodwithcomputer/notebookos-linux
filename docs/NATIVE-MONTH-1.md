# Notebook OS Native — Month 1, worked out in full

Companion to `docs/NATIVE-OS-PLAN.md`. That document is the multi-year program.
This one is the first 30 days, at the resolution needed to actually run them.

**Assumption:** both Claude Code and Codex on max plans, running continuously
for 30 days. Ben available ~1–2 hours per day for decisions, plus hands-on
hardware work when the rack lands.

**Start:** Day 1 = 2026-08-07.

---

## 1. The honest throughput model

Everything in this plan depends on one number nobody actually knows yet: how
much merged, gated code two fleets produce in a day. Guessing it badly in either
direction wrecks the plan, so here is the model, its assumptions, and — more
importantly — the instruction to replace it with measurement.

### 1.1 The arithmetic

| Parameter | Value used | Basis |
|---|---|---|
| Sustained concurrent sessions per fleet | 8–12 | max-plan rate limits, running 24/7 |
| Both fleets, with duty cycle ~75% | **~15 effective agents** | backoff, queue gaps, restarts |
| Task cycle (implement + test to green) | ~90 min | well-specified module with a host harness |
| Task slots per day | ~240 | 15 agents × 16 cycles |
| Fraction producing net-new merged code | ~40% | rest is debugging, integration, review, failed attempts |
| Lines per productive task (code + tests) | ~600 | module plus its suite |
| Survives integration without rewrite | ~75% | |

That yields roughly **40,000 net merged lines per day at full width.**

### 1.2 Why that number is a lie if you apply it uniformly

Throughput is **bimodal**, and the difference between the two modes is larger
than the difference between one fleet and two.

- **Specified work** — Unicode tables, image and audio format parsers, PWG
  raster generation, the port of 104K lines of existing Python app logic, test
  suites. There is a document that says what correct means, and a harness that
  says whether you got it. This work genuinely runs at tens of thousands of
  lines per day and scales with agent count. Call it **55% of the tree**.
- **Novel work** — the scheduler and VM, the AML interpreter, text shaping, GPU
  KMS, H.264, the ARM7TDMI C compiler. Correctness is discovered by iterating
  against reality, the parts are tightly coupled, and adding agents does not
  help. Brooks's law did not stop applying because the developers are models.
  This runs at maybe **3,000–8,000 lines per day in total, no matter how many
  agents you point at it.** Call it **45% of the tree**.

So the month's realistic band is **500K–900K merged lines**, and the spread is
dominated by how much of the hard half gets unblocked early. Both ends of that
band are a large fraction of the 2.5M-line estimate, which is itself the
headline finding: **code volume stops being the binding constraint almost
immediately.**

### 1.3 Month 1 is a measurement instrument

Because the model above is inference rather than data, Month 1 has a second job
alongside building: **calibrate the constant.** Instrument from Day 1 and
report daily:

- merged LOC/day, split by lane class (specified vs novel)
- task success rate, and rework rate at integration
- gate pass rate on first submission
- **decision-queue latency** — how long a lane waits on a human answer
- **agent-hours blocked** — the number that tells us whether the fleet is
  actually saturated or just busy

On Day 30 the program schedule in `NATIVE-OS-PLAN.md` gets rewritten against
these numbers rather than against my arithmetic. That is the single most
valuable artefact the month produces, because every subsequent month is planned
with it.

---

## 2. What compute cannot buy in 30 days

Stating this up front prevents the plan from quietly assuming otherwise.

| Not compressible | Why | Month-1 response |
|---|---|---|
| **Rack lead time** | Laptops and printers arrive when they arrive | Order in hour one; make Month 1 depend on **QEMU + virtio**, not hardware |
| **Hardware bring-up** | Each machine's firmware quirks are found serially, by a human with the machine | Deferred out of Month 1 entirely; device-trace replay substitutes |
| **Ben's decision latency** | One human, 1–2 h/day | Batched decision queue (§6.2); lanes are required to proceed on a stated assumption rather than block |
| **Conformance convergence** | The long tail of pixel-identity is discovered, not scheduled | Month 1 targets *one app* at pixel-identity, not sixty-six |
| **Coupled novel work** | The scheduler cannot be written by twelve agents at once | Small senior lanes, started Day 1, protected from churn |

The plan below is arranged so that **nothing in Month 1 is blocked by anything
in this table.**

---

## 3. The Month-1 gate

One primary demonstration. If it lands, the program's core risk is retired four
months earlier than `NATIVE-OS-PLAN.md` scheduled it.

> **Primary:** Notebook OS Native boots in QEMU — our UEFI stub, our kernel, our
> scheduler and VM, our filesystem on virtio-blk, our compositor, our toolkit —
> and renders **Calculator** pixel-identical to the reference golden at
> 1024×740, in English, driven by a real keyboard, cold boot to usable in under
> 10 seconds, with zero Linux-derived code in the image.

Secondary gates, each independently verifiable and independently valuable:

| # | Gate | Verified by |
|---|---|---|
| S1 | `spec/` complete and regenerating from the frozen reference | Re-run produces byte-identical output |
| S2 | Dual-run harness live, with ≥40 gates **proven able to go red** | Sabotage suite |
| S3 | Text stack renders all **17 languages** golden-identical offscreen | Golden corpus diff |
| S4 | Image codecs complete and fuzzed to zero crashes over 10⁹ cases | Continuous fuzzer |
| S5 | PDF writer produces byte-identical output to the reference for every print path | Paper contract diff |
| S6 | ≥10 leaf apps' logic passing behaviour conformance headlessly | Ported selftests |
| S7 | NVMe, xHCI and HDA drivers passing recorded-trace replay | Trace harness |
| S8 | Reproducible build: two independent rebuilds, identical bytes | CI |

**Stretch:** first boot on real hardware, if the rack lands before Day 25.

Note what is deliberately *not* in the gate: no apps beyond Calculator rendering,
no real hardware, no acceleration, no printing to a physical printer. Month 1
buys depth through the whole stack and breadth only where breadth is free.

---

## 4. Day 0 — bootstrap (before the fleets scale up)

Roughly two days, human-led, agents assisting. Nothing else starts until these
are done, because every one of them is a thing that gets 40× more expensive to
fix once 40 lanes are running against it.

1. **Order the rack.** Hour one, before anything else. 15 laptops per
   `NATIVE-OS-PLAN.md` §6.6, 10 printers, network-controlled power, USB boot
   switching, capture cards. Longest lead time in the program.
2. **Freeze the reference.** Tag the tree and the 1.3 ISO as
   `reference/1.3-golden`, and prove it rebuilds byte-identically from the tag.
   If it does not rebuild reproducibly, **fix that first** — a moving reference
   makes every conformance claim in the program meaningless.
3. **Stand up the monorepo**: ~55 crates, lane ownership enforced in CI, own
   build tool, nightly image job, no network access in the build.
4. **Write and freeze `contracts/` v0.1** — syscall surface, driver model,
   widget and event contracts, app lifecycle, file-format schemas.
5. **Write the lane charters.** One file per lane: what it owns, what it may not
   touch, its harness, its definition of done. An agent's first read.
6. **Fill the task queue to ≥3 days deep** on every lane. An idle agent is the
   only truly wasted resource in this setup.

---

## 5. The lane roster

48 lanes. Fleet assignment follows the split in §6.4. "HW" marks anything
needing physical hardware — note that **nothing in Month 1 does**.

### Group A — Foundation (Days 1–7, then dissolves)

| Lane | Owns | Fleet | Month-1 deliverable |
|---|---|---|---|
| A1 | Reference freeze + reproducibility | Claude | Tag, byte-identical rebuild, checksum manifest |
| A2 | Spec: strings, menus, bindings | Codex | 17 catalogs, menu trees, accelerators → `spec/ui/` |
| A3 | Spec: file formats | Claude | Every format + worked examples + fuzz corpus |
| A4 | Spec: pixel goldens | Claude | ~160 surfaces × 17 langs × 2 DPI ≈ **5,400 PNGs** |
| A5 | Spec: print goldens | Codex | Golden PDFs from every print path |
| A6 | Dual-run conformance harness | Claude | Pixel/byte/PDF diff engine + tolerance policy |
| A7 | Harness red-proof | Codex | Sabotage suite; ≥40 gates proven able to fail |
| A8 | **`contracts/` (architect lane)** | Claude | v0.1 frozen Day 2; v0.2 at Day 15 if needed |
| A9 | Build system, CI, nightly image | Codex | Reproducible build gate green |
| A10 | Device trace capture | Claude | NVMe/xHCI/HDA/SDHCI traces recorded from reference |

A4 extends the existing tooling rather than replacing it: `uishot_all.py`
already batch-renders app windows offscreen under the real Papertone theme and
guest fonts via `guestrun.sh`, and `construct_all_host.py` already derives its
app list from `finder.APP_MODULES` so coverage cannot silently drift. A4's work
is widening that from one main window per app to every window, dialog and panel
state, across 17 languages and both DPI scales.

### Group B — Kernel and drivers (host-simulator and trace-replay tested)

| Lane | Owns | Fleet | Month-1 deliverable |
|---|---|---|---|
| B1 | UEFI boot stub | Claude | Own EFI app, GOP init, memory map, measured boot |
| B2 | HAL x86_64 | Claude | GDT/IDT/APIC, paging, SMP bring-up, TSC |
| B3 | Memory: phys, virtual, slab | Claude | Allocators passing the simulator suite |
| B4 | Scheduler, threads, capabilities, IPC | Claude | **Novel lane — small, protected** |
| B5 | Syscalls + process model | Claude | ~60 calls, no socket, invariant test |
| B6 | **ACPI + AML interpreter** | Codex | **Longest non-hardware pole — starts Day 1**; tables + AML core |
| B7 | IOMMU + DMA | Codex | VT-d/AMD-Vi, DMA protection |
| B8 | Block layer + GPT/MBR | Codex | Partition parsing, fuzzed |
| B9 | Own filesystem | Claude | Journaling, checksummed, crash-safe; crash-injection tested |
| B10 | FAT32/exFAT | Codex | Read/write, fuzzed |
| B11 | **virtio (blk, input, gpu)** | Claude | **The slice's fast path — no rack needed** |
| B12 | NVMe | Codex | Passing trace replay |
| B13 | xHCI + USB core | Codex | Passing trace replay |
| B14 | USB HID | Codex | Keyboard, mouse, touchpad |
| B15 | AHCI/SATA | Codex | Passing trace replay |

### Group C — Graphics and text (100% host-testable, no hardware ever)

| Lane | Owns | Fleet | Month-1 deliverable |
|---|---|---|---|
| C1 | 2D rasteriser | Claude | Paths, AA, gradients, compositing, clipping |
| C2 | Font engine: TrueType `glyf` | Codex | Parse, hint, rasterise, cache |
| C3 | Font engine: CFF/Type2 | Codex | Required — Nimbus Sans ships as OTF/CFF |
| C4 | Shaping: GSUB/GPOS core | Claude | **Novel lane** |
| C5 | Shaping: Devanagari + Hebrew | Claude | Hindi reordering, Yiddish RTL |
| C6 | Shaping: CJK | Codex | Chinese, Japanese, Korean |
| C7 | Unicode algorithms + tables | Codex | Bidi, line break, segmentation, normalisation, collation |
| C8 | Colour management / ICC | Codex | |
| C9 | Compositor + window management | Claude | Panel as a **layer** — fixes the matchbox stacking defect by construction |
| C10 | Toolkit core: layout + events | Claude | **Novel lane** |
| C11 | Toolkit: buttons, entries, dialogs | Codex | Golden-identical offscreen |
| C12 | Toolkit: text view | Claude | Writer depends on it; hardest widget |
| C13 | Toolkit: tree view | Codex | Finder depends on it |
| C14 | Theme + design token system | Codex | Papertone reproduced; token checker ported |
| C15 | IME framework + Pinyin/Kana/Hangul | Codex | |

### Group D — Media and documents (host-testable)

| Lane | Owns | Fleet | Month-1 deliverable |
|---|---|---|---|
| D1 | PNG, BMP, GIF | Codex | Complete + fuzzed |
| D2 | JPEG decode + encode | Codex | Complete + fuzzed |
| D3 | TIFF, WebP | Codex | Complete + fuzzed |
| D4 | SVG renderer | Claude | Icons |
| D5 | Audio: WAV, FLAC, Vorbis | Codex | Decode + encode |
| D6 | Audio: MP3, AAC decode | Codex | |
| D7 | Audio engine: mixer, resampler | Claude | |
| D8 | Containers: MP4, MKV, AVI, OGG | Codex | Demux + mux |
| D9 | H.264 decode | Codex | **Novel lane**; started, not finished in Month 1 |
| D10 | PDF writer | Claude | Font subsetting, images, transparency |
| D11 | PDF reader/renderer | Claude | |
| D12 | EPUB + HTML/CSS subset | Claude | Hard scope limit in `contracts/` |
| D13 | Print: IPP + PWG/URF + halftone | Claude | Byte-identical raster vs reference |

### Group E — Applications (host-testable through the offscreen renderer)

| Lane | Owns | Fleet | Month-1 deliverable |
|---|---|---|---|
| E1 | `nbapp`, `nbstate`, `nbprefs` | Claude | App framework, persistence, **the data-safety read path** |
| E2 | `nbi18n` | Codex | 17 catalogs, the auto-translate contract |
| E3 | `nbicons`, `nbmotion`, `nbtransitions` | Codex | Visual language, paper physics |
| E4 | **Calculator** | Claude | **The slice target — pixel-identical** |
| E5 | Leaf apps: journal, contacts, cookbook, workout | Codex | Logic + behaviour conformance |
| E6 | Leaf apps: tasks, bills, mealplanner, g2048 | Codex | Logic + behaviour conformance |
| E7 | Records: calendar, academics, accounting | Codex | Logic only in Month 1 |
| E8 | Writing: writer, novel, screenplay | Claude | Blocked on C12; logic first |
| E9 | Shell: finder, settings, login, firstrun | Claude | Blocked on C13; logic first |

### Group F — Cross-cutting, continuous

| Lane | Owns | Fleet | Month-1 deliverable |
|---|---|---|---|
| F1 | Adversarial audit of Claude lanes | Codex | Weekly findings report |
| F2 | Adversarial audit of Codex lanes | Claude | Weekly findings report |
| F3 | Fuzzing infrastructure + corpus | Codex | Every parser, continuously, from Day 5 |
| F4 | Integration and boot gate | Claude | Nightly; auto-revert on boot break |
| F5 | Instrumentation and daily metrics | Codex | §1.3 numbers, reported daily |

---

## 6. Operating protocol

Forty-eight lanes and twenty concurrent agents fail by incoherence long before
they fail by capacity. These five rules are what prevent that, and none of them
is optional.

### 6.1 The interface freeze

`contracts/` is frozen on Day 2 and only lane A8 may edit it. Any other lane
needing a change files a request; A8 absorbs the pain of versioning and
regenerating consumer stubs. Interface churn is the failure mode that turns 40
productive lanes into 40 lanes rebasing, and it compounds daily.

### 6.2 The decision queue — how a human stays out of the critical path

Agents never block on Ben. A lane that hits a genuine product question writes
`decisions/NNN-<slug>.md` containing the question, the options, **its
recommendation, and the assumption it is proceeding on**, then keeps working.

Ben answers the queue in one batch per day. If an answer contradicts the
assumption, the lane reworks — and the rework cost is nearly always lower than
the cost of 15 agents idling for six hours.

Two things Ben must decide in Week 1, because they are load-bearing and already
identified:

- **Bluetooth.** The "no socket syscall" invariant permanently forecloses it.
  The desktop kernel already has no `CONFIG_BT` and no bluez, so the cost today
  is zero — but it is a door being welded shut and needs to be a decision.
- **Screenshot tolerance policy.** Exactly how identical is identical? Antialiasing
  and hinting may not be bit-reproducible even between two runs of the
  *reference*. This must be settled before A4 generates 5,400 goldens against
  the wrong standard.

### 6.3 No lane opens before its harness exists

The defining risk of high-throughput generation is plausible, unverified code —
and at 40,000 lines a day, unverified code accumulates faster than anyone can
read it. So a lane's first task is always its test harness, and a lane whose
harness does not yet exist stays closed. This is the rule most likely to be
quietly violated under schedule pressure, and it is the one that would cost the
most.

### 6.4 Fleet split and adversarial pairing

- **Claude Code** takes lanes needing deep cross-repo context, multi-file
  coherence, and architectural judgement: `contracts/`, kernel core, compositor,
  toolkit core, the document stack, integration. `AGENTS.md` already establishes
  the `ccp` path for large multi-file work.
- **Codex** takes lanes that are spec-implementation with crisp boundaries:
  codecs, Unicode tables, format parsers, class drivers, leaf apps.
- **Each fleet writes the adversarial conformance suite for the other's lanes**,
  derived from `spec/` and never from the implementation. Swap weekly.

This is not symmetry for its own sake. Four adversarial audit rounds on the
current product found 23, 22, 20 and 16 real defects, and the severity never
dropped to zero. Review finds what tests do not.

### 6.5 Merge protocol and the nightly gate

A merge requires: lane harness green, contract conformance green, fuzzer clean,
and no coverage decrease. Nightly, the full image builds from scratch and boots
in QEMU; a crate that breaks boot is **reverted automatically**, not triaged.
From Day 14 the boot gate is the heartbeat of the program.

---

## 7. The 30 days

### Days 1–2 — Bootstrap
Section 4. Rack ordered, reference frozen and proven reproducible, monorepo and
CI up, `contracts/` v0.1 frozen, 48 lane charters written, task queue 3 days
deep. Agents assist; Ben leads. **Exit:** an empty tree that builds, tests, and
enforces lane ownership.

### Days 3–7 — Spec, harness, and first fan-out
Group A runs to completion — this is the week that determines whether the other
29 days mean anything. Groups B and C open behind it as their harnesses land;
the novel lanes (B4 scheduler, B6 AML, C4 shaping, C10 toolkit core) all start
**now**, because they are the ones that cannot be accelerated later.

**Exit:** `spec/` complete and regenerating; harness proven red-capable against
a sabotaged reference; ~20 lanes producing.

### Days 8–14 — Width, and the first boot
All 48 lanes active. Group D and E open. The kernel reaches its first
milestone: **boots in QEMU to a serial shell on virtio-blk, around Day 12–14.**
This is deliberately early and deliberately crude — its purpose is to force
integration before there are 600,000 unintegrated lines.

**Exit:** nightly boot gate live and green.

### Days 15–21 — Depth
The text stack converges: rasteriser, both font backends, shaping and Unicode
reach golden-identical rendering across all 17 languages (**S3**). The toolkit
renders its first widgets offscreen matching goldens. Codecs and the PDF writer
complete. Leaf app logic lands in volume. Trace-replay drivers go green.

**Exit:** S3, S4, S5 met; the compositor draws a window.

### Days 22–27 — Assembly
The vertical slice is assembled and debugged: bootloader, kernel, virtio,
filesystem, compositor, toolkit, Calculator. **This is where the month's
schedule risk actually lives** — integration debugging does not parallelise, and
every prior week exists to make this one short. Hard-freeze all lanes feeding
the slice at Day 22; other lanes keep running.

**Exit:** Calculator draws on our own stack.

### Days 28–30 — The gate, and the recalibration
Pixel-identity chased to zero diff. Secondary gates confirmed. Instrumentation
data compiled and `NATIVE-OS-PLAN.md` rewritten against measured throughput
rather than §1's arithmetic. If the rack arrived, first real-hardware boot
attempt as a stretch.

---

## 8. Month-1 risks

| Risk | Probability | Response |
|---|---|---|
| **The reference is not deterministic** — goldens differ run to run on hinting or AA | High | Settle the tolerance policy in Week 1 (§6.2). Where nondeterminism is real, define the tolerance explicitly. Never hide it inside a fuzzy compare that also hides real regressions. |
| **`contracts/` churns** and 40 lanes rebase | Medium | Hard freeze; only A8 edits; v0.2 at a scheduled point (Day 15), not on demand |
| **Integration debt** — 600K lines that never ran together | High | The Day-12 QEMU boot exists specifically to prevent this. Do not let it slip. |
| **Plausible unverified code** accumulates | High | §6.3, without exception. It is the rule that pressure will attack. |
| **Ben becomes the bottleneck** | Medium | Decision queue with stated assumptions; measure decision latency daily as a first-class metric |
| **Novel lanes starve** while specified lanes flood the merge queue | Medium | Novel lanes get dedicated capacity and priority on review; they are the critical path, not the volume lanes |
| **Rack slips past Day 30** | Medium | Already assumed. Nothing in the Month-1 gate needs it. |
| **The throughput model is wrong by 3×** | Certain, direction unknown | That is what §1.3 is for. Plan Month 2 on data. |

---

## 9. What Month 2 looks like

**If the gate lands:** the program's core risk is retired, and Month 2 is
breadth on a proven spine — the full toolkit, the desktop shell, the remaining
apps, and hardware bring-up beginning the moment the rack is racked. The
`NATIVE-OS-PLAN.md` schedule compresses substantially, because Phase 1 will have
completed in Month 1.

**If the gate does not land**, the diagnosis matters more than the slip:

- *Slipped on integration* — expected, not alarming. Widen the assembly window
  and keep the width.
- *Slipped on the novel lanes* (scheduler, AML, shaping) — the serious case. It
  means the hard 45% is the real schedule and more agents will not fix it. The
  response is to narrow scope, not to add capacity.
- *Slipped on verification* — the harness was not ready and code piled up
  unverified. Stop generating, and spend Month 2 verifying what exists. This is
  the only failure mode that gets worse if you keep running the fleets.

---

## 10. Provisional schedule revision

Pending Day-30 data, the model in §1 implies the program is not bounded by code
volume, which is what `NATIVE-OS-PLAN.md` §5 implicitly assumed when it
estimated 20–30 months.

| Milestone | Original | Revised (provisional) |
|---|---|---|
| Phase 1 vertical slice | Months 2–5 | **Month 1** (in QEMU) |
| All 2.5M lines written and unit-gated | — | Months 3–6 |
| Tier-0 conformance-complete in QEMU | Month 12 | Months 5–8 |
| Hardware bring-up, Tier 1, certification | Months 14–24 | Months 8–15 |
| **1.0-native** | **20–30 months** | **12–18 months** |

The reduction comes from one place only: code generation stops being the
constraint. Everything that remains — hardware bring-up, conformance
convergence, physical certification — is unchanged by fleet size, which is why
the revised figure is not smaller still.

The corollary matters for how the fleets are used later: **once generation
saturates, adding agents to generation buys nothing.** At that point the fleets
should be re-tasked to verification — fuzzing, adversarial audit, conformance
breadth across languages and machines — where their marginal value stays high
for the rest of the program.
