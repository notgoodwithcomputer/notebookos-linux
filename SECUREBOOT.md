# Notebook OS — UEFI Secure Boot

Notebook OS boots with UEFI **Secure Boot enabled**, with no need to disable it
in firmware. The trust chain is the standard, well-trodden one:

```
UEFI firmware  --(Microsoft UEFI CA)-->  shim
shim           --(Debian Secure Boot CA)-->  GRUB   (grubx64.efi, has shim_lock)
GRUB           --(Notebook OS MOK)-->  kernel  (bzImage)
```

* **shim** is Debian's Microsoft-signed shim (`shimx64.efi.signed`) — every
  UEFI machine already trusts Microsoft's key, so shim runs with no firmware
  changes.
* **GRUB** is Debian's signed GRUB (`grubx64.efi.signed`), which shim trusts via
  the Debian CA it embeds. It carries the `shim_lock` verifier, so it refuses to
  boot an unverified kernel under Secure Boot.
* **The kernel** is signed with the **Notebook OS Machine Owner Key (MOK)**,
  which you enroll **once** on first boot (below). Every future re-signed kernel
  is then trusted without re-enrolling.

## Keys

`tools/gen-sb-keys.sh` generates the MOK once into `secureboot/`:

| file          | what it is                          | shipped?                    |
|---------------|-------------------------------------|-----------------------------|
| `MOK.key`     | private signing key                 | **no — keep secret** (gitignored) |
| `MOK.crt`     | certificate (PEM), used by `sbsign` | no                          |
| `MOK.cer`     | certificate (DER), you enroll this  | yes — on the ESP at `EFI/BOOT/MOK.cer` |

The key is generated only once and reused; regenerating it would invalidate the
certificate you have already enrolled. Back up `secureboot/` if you want to
re-sign kernels on another machine.

## Building

Secure Boot is on by default in both release artifacts:

* `tools/mkrelease.sh` → `release/notebookos-<ver>.iso` (installer) and
  `release/notebookos-<ver>.img` (raw dd-able disk). The installer lays the full
  shim→grub→signed-kernel chain onto the target ESP.
* `tools/mkimage-uefi.sh` → `boot-work/notebookos-uefi.img` (raw disk only).

Build host needs: `shim-signed`, `grub-efi-amd64-signed`, `sbsigntool`
(`mokutil`/`efitools` are handy but not required to build). Set
`NB_SECUREBOOT=0` to build the old unsigned GRUB image instead (dev/BIOS only).

## First boot — enroll the key (one time)

The kernel is signed with our MOK, which the firmware doesn't trust yet, so the
**first** boot needs a one-time enrollment. There are two ways:

**A. From the GRUB menu (easiest).** On first boot GRUB shows an entry:

> **Enroll Secure Boot key (first boot: run once, then reboot)**

Select it. MokManager (a blue text screen) opens → **Enroll key from disk** →
browse to `EFI/BOOT/MOK.cer` → **Continue** → **Yes** → **Reboot**. Done — from
then on Notebook OS boots normally under Secure Boot.

**B. From firmware.** If your firmware doesn't show GRUB first, boot
`EFI/BOOT/mmx64.efi` from the firmware boot menu and follow the same
"Enroll key from disk → MOK.cer" steps.

After enrollment, pick the normal **Notebook OS** entry; the kernel now verifies
and boots.

## Verifying signatures

```sh
sbverify --cert secureboot/MOK.crt <esp>/bzImage        # kernel
sbverify --list  <esp>/EFI/BOOT/BOOTX64.EFI             # shim (Microsoft-signed)
sbverify --list  <esp>/EFI/BOOT/grubx64.efi             # grub (Debian-signed)
```

`tools/run-uefi-secureboot.sh` boots the raw image under QEMU + OVMF with Secure
Boot **enforced** (Microsoft keys pre-loaded) to sanity-check the chain. Note it
stops at the kernel unless the MOK is enrolled — enrollment is interactive and
ultimately happens on real hardware.

## Scope

The **installed system** boots under Secure Boot. The Live ISO's own boot loader
(from `grub-mkrescue`) is not itself shim-wrapped yet, so to *install* you may
need Secure Boot off during the live session, then enable it (and enroll) for the
installed system. Wrapping the live ISO in shim is a possible future step.
