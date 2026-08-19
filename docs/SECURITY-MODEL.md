# Notebook OS — Security Model & Hardening Campaign (v0)

Governing document for the effort to make Notebook OS hard to exploit,
particularly by an AI-driven attacker. Read this before scheduling security
work; it is the yardstick everything else is measured against.

## 1. The goal, stated honestly

**"Entirely unexploitable" is not an achievable engineering state.** No
non-trivial system reaches it, and a campaign that claims it is the target will
produce security theatre — effort spent where it is easy to spend rather than
where an attacker actually goes. Saying so is not defeatism; it is the
precondition for spending the effort well.

The achievable, worth-having version is a **defensible posture**:

1. a written **threat model** — who we defend against and who we do not;
2. **mapped trust boundaries**, each with its enforcement named;
3. the **smallest reachable attack surface** we can ship;
4. **defense in depth** so that no single reachable bug yields full compromise;
5. every one of the above **checked by a gate in the build**, so a regression
   goes red the way the existing data-safety and i18n gates do.

### What "against AI-driven effort" actually means here

The useful interpretation is not "an AI lives on the machine" — Notebook OS is
offline and ships no model. It is: **assume the adversary uses AI to find and
exploit bugs.** Three consequences drive the whole design:

- **The source is public** (two public GitHub repos). Security-through-obscurity
  is worth exactly zero. Everything here must hold up with the attacker reading
  every line — which, encouragingly, is already how the codebase's own comments
  reason.
- **Tireless fuzzing.** Every parser reachable from untrusted input will be
  fuzzed harder than a human would bother to. The defense is to have *few*
  parsers reachable as a privileged user, and to contain the ones that are.
- **Cheap chaining.** A path-quirk here plus a symlink there plus a parser
  oddity becomes one exploit. The defense is to reduce the *count* of small
  flaws, not just the severe ones.

None of this is exotic. It is ordinary defensive security, prioritized by the
fact that the attacker is patient, well-informed, and automated.

## 2. Baseline — what Notebook OS already gets right

This campaign starts from an unusually good place. Do not undo any of it.

- **No IP networking.** The kernel is a no-internet fork with no `AF_INET`;
  `S40network` is stripped; no ssh/dropbear/wget/curl/nginx are built. The
  entire *remote network* attack class — the source of most OS CVEs — simply
  does not apply. This is the single largest attack-surface reduction available,
  and it is already done.
- **Secure Boot** on installed systems: shim → Debian GRUB → MOK-signed kernel
  (`SECUREBOOT.md`). Kernel integrity is enforced.
- **USB automount is defensive** (`automount.sh`): `nosuid,nodev,noexec`,
  attacker-controlled-label sanitising (dot-labels, control chars, path
  separators), write-through sync. Direct execution off a stick is already off.
- **The obvious local root shells are closed:** no `getty` in the shipped
  inittab (the Ctrl+Alt+F1 → root shell is documented and removed); the serial
  debug shell is gated on `nbdebug` on the kernel command line.
- **`login.py` is carefully built** and, notably, lockout-proof: it refuses to
  present a prompt no password can satisfy.
- **Compiler hardening is on:** `SSP_STRONG`, `RELRO_FULL`, `PIC/PIE`,
  `FORTIFY_SOURCE` (currently level 1).
- **Data-safety work is underway:** apps quarantine malformed on-disk stores
  rather than destroying them on load.

## 3. Trust boundaries

| Boundary | What crosses it | Enforced by | Gap |
|---|---|---|---|
| Physical ↔ machine | Keyboard, boot menu, the drive itself | Secure Boot (kernel only) | **No GRUB password; no disk encryption** |
| Removable media ↔ OS | Files on USB; the USB device itself | `noexec,nosuid,nodev` mount | Files are still **parsed as root** |
| Radio ↔ OS | LoRa serial frames (BLE removed 2026-08) | no in-kernel radio stacks; LoRa is plain USB-serial | LoRa frame **parser unaudited** |
| Untrusted file ↔ app | Images, audio/video, ROMs, map packs, JSON stores, documents | app-level validation (partial) | **Runs as root; no sandbox** |
| Guest workload ↔ OS | GBA ROMs (emulated), user C (compiled) | process boundary only | untrusted code by design |
| User ↔ admin | The sign-in password | `login.py` (UX gate) | **not a privilege boundary — session is root** |

The last row is the one to internalize: `login.py` gates *the desktop*, not
*privilege*. The session runs as root whether or not anyone signs in, so login
is meaningful only while the paths *around* it (boot menu, debug shell, a pulled
drive) are closed.

## 4. Threat model — ranked by what a remote/AI attacker can reach

1. **Malicious data files parsed as root (PRIMARY).** A crafted image, audio,
   video, GBA ROM, `.nbm2` map pack, PDF, or JSON store reaches an app that
   parses it with root privilege and no sandbox. One memory-safety bug in any of
   those parsers (many are C: GdkPixbuf, GStreamer/ffmpeg, poppler, libarchive)
   is a full, silent root compromise. This is where an AI fuzzer goes first, and
   it is the least-defended surface. **Highest priority.**
2. **Malicious USB device.** Not the files — the *device*: HID injection (typing
   as root once past sign-in), and the kernel driver surface a device presents.
   `noexec` does nothing against either.
3. **Radios.** With Bluetooth removed (2026-08, see F5) the only remaining radio
   is the **LoRa serial dongle**, which takes attacker-shaped frames over the
   air; whatever parses `/dev/lora` is reachable without touching the machine.
4. **Guest workloads.** The GBA emulator runs untrusted ARM; the toolchain
   compiles untrusted C. This is untrusted-code-execution *by design*, so the
   question is blast radius, not prevention.
5. **Physical access.** GRUB command-line edit (`init=/bin/sh`) → root shell,
   bypassing `login.py` even under Secure Boot; unencrypted disk → data readable
   on theft. **Out of reach for a remote AI**; in scope only if physical theft
   is a threat we choose to counter (see Decision D2).

## 5. The structural crux

**Everything runs as root.** All 69 desktop modules, the session, every app.
There is no unprivileged user, no seccomp, no namespace, no landlock, no
sandbox of any kind (`nbapp.py`, the 2203-line shared base, is where isolation
would hook if we add it). The consequence is stark and it sets the ceiling on
everything else: **any parser RCE anywhere is instant, total, root.** Reducing
the surface (Section 6) lowers the odds of that bug; it does not change the
outcome when one is found. Changing the outcome requires containment or
privilege separation — the campaign's central strategic choice (Decision D1).

## 6. Findings (this pass)

- **F1 — root-lock regression alarm is disconnected.** `post-build.sh` verifies
  root is locked (`grep -qE '^root:!'`), but the block sits *after* `exit 0`, so
  it never runs — and even if reconnected it runs in the wrong phase (Buildroot
  regenerates `/etc/shadow` *after* post-build, so the check would false-warn).
  Root *is* locked (via `# BR2_TARGET_ENABLE_ROOT_LOGIN is not set`), so this is
  low severity, but the alarm that would catch a regression is dead. A
  green-gate-that-cannot-go-red. **Fix: move the check to a post-image gate that
  reads the final `output/target/etc/shadow`.**
- **F2 — no GRUB password.** Nothing sets `superusers`/`password_pbkdf2`
  anywhere. An attacker at the keyboard presses `e`, appends `init=/bin/sh`, and
  boots to a root shell that never reaches `login.py`. Secure Boot does not
  prevent this (it verifies the kernel, not its command line). *Physical-access;
  see D2.*
- **F3 — no disk encryption.** `cryptsetup` is not built; the installer writes a
  plaintext root. A pulled drive reads out entirely; `login.py` provides no
  confidentiality. *Physical-access; see D2.*
- **F4 — getty config contradicts the inittab.** `BR2_TARGET_GENERIC_GETTY=y` on
  `tty1` still *requests* the getty the overlay inittab exists to suppress. The
  overlay wins today (and root is locked, so the getty would prompt a locked
  account, not hand out a shell), but it is a two-sources-of-truth latent hole.
  **Fix: turn the buildroot getty off so there is one source of truth.**
- **F5 — BLE stack shipped with no consumer. DONE (2026-08-09): removed.**
  BitChat's removal left the in-kernel Bluetooth stack and `bluetoothd` shipping
  with nothing using them. Removed entirely: `CONFIG_BT` and all HCI transports /
  controller drivers dropped from the kernel config seeds (`tools/desktop.config`,
  `tools/phase1.config`); the bluez userspace, its D-Bus policy and init scripts
  purged in `post-build.sh`; BT firmware no longer staged into the initramfs
  (`mkiso.sh`); and `CONFIG_CRYPTO_USER_API` (AF_ALG — enabled *only* for BlueZ's
  LE crypto) unset with it. The socket-probe boot gate now asserts AF_BLUETOOTH
  and AF_ALG must fail. LoRa is untouched. *Kernel-fork follow-up: purge the
  `net/bluetooth/`, `drivers/bluetooth/` and `include/net/bluetooth/` source in
  the `linux/` fork — the config change already keeps them uncompiled.*
- **F6 — cheap crypto/hardening upgrades.** `FORTIFY_SOURCE` is level 1 (→ 2 or
  3); generic password hash is SHA-256 (→ SHA-512, which the installer already
  uses). Both are one-line config changes.
- **F7 — untrusted-input parsing is unmitigated.** No fuzzing harness, no
  sandbox around the parsers of Section 4 item 1. This is F-anything's root
  cause and the primary structural risk, not a point bug.
- **F8 — root could switch off any userspace policy. FIXED in config
  (2026-08-14), boot check pending.** Found while building the walled garden
  (`docs/APP-TRUST.md`): `CONFIG_MODULE_SIG` unset (root inserts arbitrary
  kernel code), `CONFIG_KEXEC=y` with no `KEXEC_SIG` (root boots an unsigned
  kernel — Secure Boot verified only the first one), and no
  `SECURITY_LOCKDOWN_LSM` (nothing restrains root from those interfaces at
  all). Any one of them makes app signing decorative, which is why L0 of the
  app-trust work had to precede the app-side work. Now: lockdown LSM early and
  forced to integrity, module signing forced (SHA-256), `KEXEC`/`KEXEC_FILE`
  off, LoadPin enforcing, `IO_STRICT_DEVMEM` on, `PROC_KCORE`/`DEBUG_FS`/
  `USER_NS` off.
- **F9 — seccomp is not available on this kernel, and D1's interim containment
  has to be Landlock-only.** `kernel/seccomp.c` needs
  `bpf_prog_create_from_user()`/`bpf_prog_destroy()` from `net/core/filter.c`,
  and **the no-internet fork deleted that file** with the internet stack
  (`net/core/Makefile` is now a hand-written minimal socket substrate); a
  second, smaller break sits above it in `include/linux/filter.h`. So classic
  BPF left with the network stack, and syscall filtering costs restoring
  `filter.c` into a fork that removed it on purpose. **Landlock is unaffected**
  (LSM + filesystem, no BPF) and is enabled. D1 chose "seccomp/landlock around
  the parsers" as the stepping stone to privilege separation: read that as
  Landlock, confining *what an app can open* — the right axis for the primary
  threat — with nothing constraining *which syscalls* a compromised parser can
  make.

## 7. Roadmap

- **Phase 0 — Agree the model & build the ruler.** This doc + an attack-surface
  inventory (every format each app parses, and the underlying C library) + a
  reproducible fuzzing harness for those parsers. You cannot harden what you
  have not enumerated.
- **Phase 1 — Attack-surface reduction.** Remove packages/formats/radios with no
  consumer (**F5 Bluetooth: DONE 2026-08-09**), fix the config contradictions
  (F1, F4), the cheap hardening bumps (F6). Boot lockdown (F2/F3) is in scope per
  D2 but scheduled after Phase 2. Lowest risk, immediate surface shrink.
- **Phase 2 — Untrusted-input containment.** The highest-leverage work against
  an AI fuzzer: run the parsers of the primary threat (images, media, ROMs, map
  packs, archives) under a sandbox — seccomp + no_new_privs + a landlock/namespace
  jail around the decode step — so a parser RCE is contained instead of root.
  Hooks in `nbapp.py`.
- **Phase 3 — Privilege separation (Decision D1 = C).** The structural fix for
  Section 5: the desktop runs as an unprivileged user, root only where required,
  landed over the native-OS rewrite. Phase 2 containment is the interim on the
  current tree.
- **Phase 4 — Radio parsers.** Audit + fuzz the BLE and LoRa frame paths (or
  delete them per F5).
- **Phase 5 — Continuous gates.** A security gate in the build so each of the
  above stays fixed, in the same spirit as the existing gates.

## 8. Decisions taken (2026-08-09)

- **D1 — Privilege model → (C) privilege-separate the session.** The desktop
  moves to an unprivileged user; root is kept only for the few operations that
  need it (mount, USB writer, installer). Highest assurance, largest change —
  **reconciled with the native-OS rewrite program** (docs/NATIVE-OS-PLAN.md) so
  the two do not fight. Interim containment (option B: seccomp/landlock around
  the parsers) is still worth doing on the current tree as a stepping stone,
  since C lands over the rewrite's timeline rather than this week.
- **D2 — Physical-access theft IS in scope, but secondary.** F2 (GRUB password)
  and F3 (disk encryption) stay on the roadmap and are real, but rank behind the
  software/radio surface an AI attacker can reach without touching the machine.
  Scheduled after Phase 2.
