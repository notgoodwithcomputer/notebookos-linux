# Notebook OS — App Trust & the Walled Garden (v0)

Governing design for **which code is allowed to run on a Notebook OS machine**.
Companion to `docs/SECURITY-MODEL.md` (which ranks what an attacker can reach)
and `docs/NBPKG.md` (which defines the package format). Read this before
building anything that loads code from off-device.

Direction, from the product owner (2026-08-14): a tightly secured walled-garden
ecosystem, "as tight as an iPhone". The primary concern is **apps developed
outside the ecosystem being run on the platform**. The lockout must be
invisible in normal use — no key management, no trust prompts.

## 1. The standard, stated in engineering terms

"Anything-proof" is not a state a system reaches; `SECURITY-MODEL.md §1` already
commits us to saying so rather than shipping theatre. The achievable target that
delivers what was actually asked for is:

> **No code runs on the machine unless it was signed by the Notebook OS release
> key, and that rule survives an attacker who already has root.**

That second clause is the whole difficulty, and it is what separates this
document from the signature checking we have today. It is worth having: it is
exactly the iPhone property — jailbreaking an iPhone is hard *because* root on
iOS does not let you run unsigned code.

### The ceiling, on commodity x86

An iPhone's guarantee rests on a fused boot ROM and a Secure Enclave that the
holder of the device cannot disable. A PC's does not. Someone with the machine
in their hands can enter firmware setup and switch Secure Boot off, or take the
drive out. **We cannot close that, and no amount of work will.** What we can
close, completely:

* every path that runs unsigned code **through software**, including with root;
* every path a delivered file (USB, LoRa, a document) can take to execution.

Measured boot with TPM sealing narrows the physical case further (the machine
refuses to unseal its data if the boot chain changed), and a firmware password
raises the bar again — but both are *deterrents against a person holding the
hardware*, never proofs. Scope the promise to software attack and it is honest
and achievable. Say "anything-proof" in marketing and the first person to pull
the drive makes a liar of us.

## 2. Today's reality: the walls are missing, not the lock

The `.nbpkg` chain (Ed25519 over a canonical manifest, payloads content-addressed
by sha256, verified before a byte is written, release private key offline) is
sound **as a lock**. It is checked once, at install, and then never again — and
the machine around it has no walls:

| Bypass | Present today | Effect |
|---|---|---|
| Load an unsigned kernel module | `CONFIG_MODULE_SIG` **not set** | root inserts arbitrary kernel code; every rule above it is void |
| `kexec` a different kernel | `CONFIG_KEXEC=y`, no `KEXEC_SIG` | root boots an unsigned kernel; Secure Boot verified only the *first* one |
| No kernel lockdown | `CONFIG_SECURITY_LOCKDOWN_LSM` **not set** | nothing restrains root from the kernel-modifying interfaces, Secure Boot or not |
| Writable root filesystem | installed systems boot `root=PARTUUID=… rw` | a `.py` dropped into `/opt/notebook/de/` is an app |
| Unsigned app registry | `installed_apps.json` is plain JSON | three lines make any module a launchable app |
| No check at launch | `finder._launch_module`, `shell.launch` | `Popen(["python3", script])` — whatever is on disk runs, as root |
| "Verify" verifies nothing | `packages._verify_module` | parses the file as Python and returns True; a tampered app passes |

The last three are app-level and cheap to fix. **The first four are the reason
fixing them alone would be decorative**: any of them lets an attacker with root
— which is every app on this OS — turn the signature policy off. Signing must be
built up from the kernel, not down from the Packages window.

Enabling assets we already have: `CONFIG_BLK_DEV_DM=y` (dm-verity is a config
flip away), `CONFIG_SQUASHFS=y` + `CONFIG_OVERLAY_FS=y` (the Live ISO already
runs a read-only root), Secure Boot with an offline MOK, and no IP networking at
all — which is a stronger position than an iPhone starts from.

## 3. The layers

Each layer is only as good as the one below it. Build upward, not downward.

### L0 — Close the kernel bypasses *(foundational; nothing above matters without it)*

Kernel config, `tools/desktop.config`:

* `SECURITY_LOCKDOWN_LSM` + `SECURITY_LOCKDOWN_LSM_EARLY`, lockdown in
  **integrity** mode, engaged automatically under Secure Boot.
* `MODULE_SIG` + `MODULE_SIG_FORCE` + `MODULE_SIG_ALL`, signed at build with a
  key held offline like the MOK. Unsigned modules stop loading.
* `KEXEC` **off** (nothing in the product uses it), or `KEXEC_SIG_FORCE`.
* `SECURITY_LOADPIN` — pins module and firmware loads to the verity volume.
* `PROC_KCORE` off, `DEBUG_FS` off, `IO_STRICT_DEVMEM` on, `USER_NS` off:
  surface with no consumer in this product.
* `SECCOMP` and `SECURITY_LANDLOCK` **on** — not used at L0, but L3 cannot exist
  without them and a kernel rebuild is the expensive step.

Cost: kernel config + rebuild + a signing step in `mkrelease`. No userspace
change. Forecloses: the entire "root turns the policy off" class.

### L1 — Sealed system volume

`dm-verity` over the root filesystem: a Merkle tree built at image time, the
root hash passed on the kernel command line, which is itself covered by the
Secure Boot signature (embed the cmdline in the signed EFI stub — a GRUB
`linux` line an attacker can edit is not covered, see D2/F2).

The root becomes **read-only and tamper-evident to the block**: modifying
`/opt/notebook/de/anything.py` on the drive makes the block fail verification at
read time, as root, with no way to re-seal without the release key. Writable
state moves to a separate `/data` volume mounted `nosuid,nodev,noexec`.

This is the single highest-value layer: it converts "we check signatures" into
"the system volume physically cannot be modified", and it makes most of L2
redundant for shipped apps.

Cost: `veritysetup` in buildroot, an image-time hashing step, an installer
change (two volumes, not one), and moving every writable path off `/`. The last
item is the real work — `NB_HOME`, `/tmp`, app stores, `installed_apps.json`,
the CUPS spool, fontconfig caches.

### L2 — The app trust chain *(what "loading apps from off-device" means)*

For apps that legitimately arrive after the image is sealed:

1. **One trust anchor** — the pinned release public key. No user-visible trust
   decisions, ever. An unauthorised app does not prompt; it does not appear.
2. **Load-time verification, not install-time.** Every launch path resolves the
   module against a signed manifest before spawning.
3. **The registry is signed.** Replace plain `installed_apps.json` with a
   manifest + detached signature; the list of what may run is itself signed.
4. **Installed apps live on their own volume**, `/data/apps/<module>/`, never
   mixed into the sealed `de/` tree. Uninstall is removing a directory.
5. **Version monotonicity + revocation.** A manifest carries a version and the
   image carries a revocation list, so a signed-but-withdrawn app stops running
   and an old vulnerable version cannot be re-installed over a new one.
6. **Ed25519, not OpenPGP.** Same guarantee; a keyring, trust database and
   packet parser running as root is surface we would be adding for nothing.
   Revocation, expiry and multiple signers are manifest fields, not reasons to
   take GnuPG.

Cost: a day or so of app work; all of it useless on its own without L0/L1.

### L3 — Sandboxing and entitlements *(SECURITY-MODEL Decision D1)*

The garden decides *what may run*; the sandbox decides *what a running app may
touch*. iPhone-class means both. Today every one of the ~76 modules runs as root
with no confinement, so one parser bug in one app is total compromise —
signature or not.

Shape: an `entitlements` block in the package manifest (files, devices, spawn),
enforced in `nbapp.py` at startup, defaulting to *no* filesystem access outside
the app's own store. Shipped apps get the same treatment, so the mechanism is
exercised by 75 apps rather than by the rare third-party one.

**Landlock only.** SECURITY-MODEL's D1 names "seccomp/landlock around the
parsers" as the interim containment; half of that is off the table on this
kernel (see L0 above — classic BPF went out with the internet stack), so the
filesystem half has to carry it. Landlock confines *what an app can open*,
which is the right axis for the primary threat (a parser reached through a
malicious file); it does nothing about *which syscalls* that parser can make
once it is running. Worth knowing before anyone plans on syscall filtering.

Cost: the largest item here, and the one most likely to break working apps.
Needs L0's kernel options first.

### L4 — Data at rest *(SECURITY-MODEL F3/D2)*

LUKS on `/data`, key sealed to the TPM against the measured boot state, so a
pulled drive reads as noise and a tampered boot chain will not unseal. Without
this, "walled garden" still means "walled until someone removes the drive".

### L5 — The gates in the garden wall

A walled garden with a shell in it is not walled. Each of these is a **product
decision**, not an engineering one, and each is a full bypass of L2 by design:

* **The Terminal app — REMOVED 2026-08-14** (product owner's decision). The
  module, its `.app` stub, both menu-bar entries, its two suites and its rows in
  eleven tool registries are gone, and the **VTE package is deselected from the
  image**: nothing else used it, so a large terminal-escape parser leaves the
  machine with the app. An iPhone has no terminal; that is not an oversight.
* **The GBA SDK — KEPT AS IS** (product owner's decision). It compiles arbitrary
  C on-device and runs it, and the emulator runs arbitrary ARM. This is a
  **standing, deliberate hole in the garden**: anyone who can open the SDK can
  execute code the release key never signed. It is contained only by whatever
  L3 eventually confines it to, and no claim about the walled garden should be
  made without naming it.
* **The serial debug shell** (`nbdebug` on the kernel command line) and any
  future recovery path.
* **Physical access** (F2 — no GRUB password; no firmware password).

## 4. Sequencing

L0 → L1 → L2 → L3, strictly. Any other order produces a checkbox that an
attacker with root removes. L2 alone — the app-side work — is worth doing early
only because it is cheap and it is where the *product* behaviour lives; it must
not be described as a lockout until L0 and L1 are under it.

**Decision, 2026-08-14 (product owner): L0 and L2 now, L1 (verity) after the
release.** Status:

* **L0 — LANDED and VERIFIED ON TARGET** (2026-08-14, `notebookos-2.6-trust.iso`).
  Asked of the running machine over the `nbdebug` serial shell:
  `/sys/kernel/security/lockdown` reads **`none [integrity] confidentiality`**;
  `dmesg` carries "Kernel is locked down from Kernel configuration" at
  0.000000 and "Lockdown: hibernation is restricted" — enforcing, not merely
  compiled in; zero failed initcalls; 24/24 shipped modules signed. Gate:
  `tools/kernel_hardening_check.py` (12 options on, 6 off), fatal in mkrelease.
  Lockdown LSM
  (early, forced to integrity mode), module signing forced with SHA-256,
  `KEXEC`/`KEXEC_FILE` off, LoadPin enforcing, `IO_STRICT_DEVMEM` on,
  `PROC_KCORE`/`DEBUG_FS`/`USER_NS` off. **Landlock** is ON but unused — L3
  needs it and a kernel rebuild is the expensive step. `DM_VERITY` +
  `DM_VERITY_VERIFY_ROOTHASH_SIG` are ON for the same reason: L1 becomes an
  image-and-installer change with no second kernel rebuild.

  **SECCOMP IS NOT AVAILABLE ON THIS KERNEL, and that is structural.** Turning
  it on fails to link twice over. First `include/linux/filter.h` references
  `bpf_stats_enabled_key`, defined in `kernel/bpf/syscall.c`, so with
  `BPF_SYSCALL=n` the dead statistics branch still emits a `__jump_table` entry
  for a symbol nothing provides — fixable with a four-line `#ifdef`. Underneath
  it is the real one: `kernel/seccomp.c` calls `bpf_prog_create_from_user()`
  and `bpf_prog_destroy()`, which live in `net/core/filter.c` — **a file the
  no-internet fork deleted** along with the internet stack (`net/core/Makefile`
  is now a hand-written "minimal socket substrate"). Classic BPF went with the
  network stack, and seccomp is a classic-BPF consumer.

  So seccomp costs restoring `filter.c` into a fork that deliberately removed
  it, plus that `#ifdef` — not a config flip. The `#ifdef` was written, proved
  to fix the first error, and then **reverted**: it unblocks nothing on its own,
  and a fork patch that buys nothing today is a patch that rots. Whoever
  restores `filter.c` will need it again; that is what this paragraph is for.
* **L2 — LANDED.** `de/nbtrust.py` (verify), `tools/gen_trust_manifest.py`
  (sign, wired into `mkrelease.sh` as step 0 and fatal on failure), enforcement
  at all three launch paths (`finder._launch_module`, `shell.launch`,
  `packages._on_open`), package signatures retained in `de/.trust/` on install,
  and `packages._verify_module` now answering from the signature instead of
  from a syntax check. Gate: `tools/app_trust_selftest.py`, 10 checks.

  **What the first boot check found: the image had no `openssl`.**
  `BR2_PACKAGE_OPENSSL` was unset — no binary, no libraries — and both
  `nbtrust` and `nbpkg_install` verify by running `openssl pkeyutl`. On the
  machine every verification threw and returned False, so the lockout refused
  **every app**, and the signed-package install path could never have worked
  on a real device at all (a pre-existing defect: `NBPKG.md` claimed "openssl
  is on-device"; nobody had asked the image). Every suite passed because they
  run on the build host, where the command is on PATH.

  The fix is openssl in the image, **not** a fallback when the verifier is
  absent: "run it anyway if we cannot check" is an attacker deleting one file.
  A fail-closed design trades availability for safety on the device and pays
  for it in the BUILD instead — `tools/shipped_binaries_check.py` now fails the
  release if any command the desktop runs unguarded is missing from the image.

  A build-order rule came out of the same check: the manifest is signed from
  the **post-build hook**, over `output/target`, because that is the only
  moment "what is signed" and "what ships" are the same bytes — signing the
  overlay let another session's edit land in between (`tasks.py`, measured).

  **Order within the hooks matters as much.** `BR2_ROOTFS_POST_BUILD_SCRIPT`
  runs `board/notebookos/post-build.sh` and *then* `tools/mkrelease.sh`, and
  post-build.sh does two things that change what the image contains: it prunes
  any `de/*.py` with no overlay counterpart (how a removed app stops shipping),
  and it then bundles the current Govorimo in **on purpose**, after the prune.
  Signing must come last, or it signs a tree that is not the one packed.
  (A guard refusing "modules not in the overlay" was written here and removed:
  it mistook that deliberate bundling for leftovers and failed two builds. The
  prune is what keeps a hand-dropped file out; the overlay is not the only
  legitimate source.)
* **L1 — NOT DONE.** Until it lands, the root filesystem is writable and an
  attacker with root can switch L2 off. Do not describe the product as sealed.
* **L3 — NOT DONE.** Every app still runs as root with no confinement.

## 5. Gates

Per `SECURITY-MODEL.md §1.5`, every layer needs a build gate that can go red:

* L0: assert each kernel option in the built `.config`; boot-probe that an
  unsigned module fails to load and `kexec_load` returns `EPERM`.
* L1: assert the shipped image has a verity superblock and that the root hash in
  the boot entry matches the image; corrupt one block in a copy and prove the
  read fails.
* L2: assert every launchable module is covered by a signed manifest; a modified
  module and an unsigned registry are both refused (red-proved).
* L3: assert an app cannot open a path outside its entitlement.
