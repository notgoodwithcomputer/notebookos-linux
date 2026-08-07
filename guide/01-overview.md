# 01. Overview

## What Notebook OS is

Notebook OS is a desktop operating system for personal computers. It provides a
graphical desktop, a file manager, and 30 built-in applications for writing,
planning, record-keeping, drawing, audio and video editing, reading, study, and
system administration.

The system is self-contained. All applications are installed as part of the
system image. There is no application store, no package download, and no update
service.

Version 1.0 is the current release.

## Networking

Notebook OS has no networking. The kernel is built without TCP/IP, Wi-Fi,
Ethernet, and all other network protocol support. There is no web browser, no
email client, and no online account.

This is a design decision, not a missing feature. It has the following
consequences:

- No data leaves the computer. Nothing is uploaded, synchronised, or reported.
- No data enters the computer over a network. Files arrive by USB storage
  device or are created on the machine.
- No software can be installed after the system is installed.
- No security updates are delivered. The system as installed does not change.
- Applications that would ordinarily require a network — maps, language
  courses, reference material — carry their data on disk instead.

USB storage devices are the only route for moving files in and out of the
computer. See [04. The Finder](04-the-finder.md) and
[14. Where data is stored](14-where-data-is-stored.md).

## Hardware requirements

| Component | Requirement |
|---|---|
| Firmware | UEFI. BIOS/legacy boot is not supported. Secure Boot is supported and may remain enabled. |
| Processor | 64-bit x86 (x86_64). |
| Memory | 2 GB or more. The live image keeps written files in memory until the system is installed, so a live session with less memory has correspondingly less room for new files. |
| Storage | The installer calculates the minimum disk size from the system payload plus any swap partition requested, and disks below that size cannot be selected. In practice this is approximately 4 GB. 16 GB or more leaves usable room for documents and media. |
| Display | 1024 × 740 or larger. Every screen in the system is laid out to fit within this size. |
| Graphics | Any GPU. Where no accelerated driver is available the system draws to the firmware framebuffer (`simpledrm`) and renders in software. Intel, AMD, and NVIDIA hardware with in-tree drivers is accelerated. |
| Sound | Intel HD Audio or USB audio. |
| Input | USB or PS/2 keyboard and mouse, or a laptop trackpad. |

Peripheral support:

- **USB storage** — mounted automatically when connected.
- **USB printers** — supported through CUPS. See [12. Printing](12-printing.md).
- **USB game controllers** — recognised as generic HID devices; used by the
  GBA Emulator.
- **External displays** — detected at startup and when a cable is connected.
- **Bluetooth** — the kernel supports Bluetooth, but no application uses it in
  version 1.0.

## System composition

| Layer | Component |
|---|---|
| Kernel | Linux, modified to remove all networking except the Bluetooth socket family |
| Base system | Buildroot-produced root filesystem |
| Display server | X11 |
| Window manager | Matchbox |
| Toolkit | GTK 3, with the Papertone theme |
| Desktop shell | Native GTK menu bar, Finder, and desktop board |
| Applications | Native GTK, written in Python 3 |
| Printing | CUPS, with the IPP-over-USB and Gutenprint driver sets |
| Audio | ALSA, with GStreamer for playback |

## Design principles

The system is built to three rules, which explain behaviour described
throughout this guide.

**1. The interface does not claim capabilities the system lacks.**
A control that appears on screen performs the action its label states. A
feature that cannot be delivered is removed rather than shown in a
non-functional state. Where a capability depends on hardware that is not
present, the relevant screen states this — for example, the Sound page reports
"No speakers or sound card found" rather than showing disabled volume
controls.

**2. Displayed values are read from real sources.**
No application ships with sample data, placeholder records, or example
content. A newly installed system opens every application empty. Figures shown
on the desktop and in application summaries are read from the files those
applications write.

**3. User data is preserved in preference to application state.**
Applications write files atomically, keep one backup generation, and do not
overwrite a file they were unable to read correctly. See
[14. Where data is stored](14-where-data-is-stored.md).

## Interface language

The interface is available in 18 languages: English, German, Greek, Esperanto,
Spanish, French, Hindi, Italian, Japanese, Korean, Dutch, Polish, Portuguese,
Russian, Serbian, Turkish, Yiddish, and Simplified Chinese. The language is
selected in **Settings > Region & Language**. See
[13. Language and keyboard](13-language-and-keyboard.md).
