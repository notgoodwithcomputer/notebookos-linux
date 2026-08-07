# 15. Installing

Notebook OS is distributed as two files:

| File | Use |
|---|---|
| `notebookos-<version>.iso` | A live image that runs from a USB stick or DVD, and contains the installer |
| `notebookos-<version>.img` | A raw disk image, written directly to the target disk |

Most installations use the ISO.

## Writing the installation stick

The image is written to a USB stick as a raw byte copy. Copying the file onto a
formatted stick does not work; the stick must be written as a whole device.

**From Notebook OS** — use the USB Writer application. See
[10. System tools](10-system-tools.md).

**From another operating system** — use any raw image writing tool.

Writing an image erases everything on the target stick.

## Starting from the stick

1. Connect the stick and switch the computer on.
2. Open the firmware boot menu — usually `F12`, `F11`, `F9`, or `Esc`, depending
   on the manufacturer.
3. Select the USB device.

The system starts as a live session. Everything works, but nothing is kept: the
system files are read-only and all changes are held in memory until the computer
is restarted.

The live session has no password and signs in directly.

## Secure Boot

Notebook OS boots with UEFI Secure Boot enabled. It does not need to be
disabled in firmware.

The boot chain uses a Microsoft-signed shim, which loads a Debian-signed boot
loader, which loads a kernel signed with the Notebook OS key.

### Enrolling the key

The Notebook OS kernel signing key is not one the firmware already trusts, so
the first boot needs a one-time enrolment.

1. On the first boot, the boot menu shows an entry reading **Enroll Secure Boot
   key (first boot: run once, then reboot)**. Select it.
2. MokManager opens — a blue text screen.
3. Select **Enroll key from disk**.
4. Browse to `EFI/BOOT/MOK.cer`.
5. Select **Continue**, then **Yes**, then **Reboot**.

The system boots normally from then on. The enrolment is done once per computer;
it is not repeated after an update or a reinstall.

If Secure Boot is disabled in firmware, no enrolment is required.

## Installing to a disk

Open **Install Notebook OS** from the live session.

The installer is a wizard with the steps listed down the left, the current step
in the centre, and Back and Next at the bottom. Nothing is written to any disk
until the final step, and then only after an explicit confirmation.

### Steps

| Step | What it asks |
|---|---|
| Language | The interface language for the installed system |
| Keyboard | The keyboard layout |
| Disk | Which disk to install onto |
| Swap | Whether to create a swap partition, and its size |
| Account | User name, full name and password |
| Computer name | The name of the machine |
| Confirm | A summary of everything about to happen |
| Progress | The install, with a live log |

### Choosing a disk

Every disk in the computer is listed with its make, model and size.

A disk too small to hold the system cannot be selected. The minimum is
calculated from the system payload plus any swap partition requested, and is
stated when a disk is too small.

**Installing erases the selected disk.** The existing partition table is
replaced. There is no option to install alongside another operating system.

### Setting up for someone else

The installer offers a mode for preparing a computer that will be handed to
someone else — by a parent, a school, or a shop.

In this mode the installer does not ask for a language, a keyboard layout, a
computer name or a password. It configures everything that does not depend on
who owns the machine, and the installed system asks the new owner for those four
things the first time it starts.

This avoids the installer's operator having to invent a password on someone
else's behalf and then pass it on.

### What is installed

| Partition | Contents |
|---|---|
| EFI System Partition | The boot loader and kernel |
| Root | The system and the user's home folder |
| Swap | Optional, at the size chosen |

The root partition is identified by a fixed partition identifier rather than a
device name, so the system boots regardless of what the disk is called or where
it is connected.

### If the install fails

Every command's output is streamed into a live log. If a step fails, the
install stops there and the failure is shown. The disk is left in whatever state
the failed step reached, and the install can be run again from the start.

## After installing

1. Remove the installation stick.
2. Restart.
3. Sign in with the account created during installation, or complete first-run
   setup if the computer was set up for someone else.

There are no updates to apply. The system has no network and does not change
after installation.
