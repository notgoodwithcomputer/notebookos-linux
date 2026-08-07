# 17. Limitations

This chapter lists what Notebook OS does not do. Everything here is a known,
deliberate limitation rather than a defect.

## Networking

There is no networking of any kind. The kernel is built without TCP/IP, Wi-Fi
and Ethernet support.

Not present, and not addable:

- Web browser, email, messaging
- File sharing, network printing, remote access
- Online accounts, synchronisation, cloud storage
- Software installation or updates from any source
- Automatic clock synchronisation
- Map, dictionary, or reference data fetched on demand

Files move in and out of the computer on USB storage devices.

## Software installation

The installed set of software is fixed and read-only. Nothing can be installed
and nothing can be removed. The Packages application reports what is installed
and can verify that it is intact; it has no install or remove function, because
there is no source to install from.

Applications can be hidden from the Applications folder, which removes them from
the launcher without uninstalling anything.

## Updates

No updates are delivered, because there is no network to deliver them over. The
system does not check for updates and does not report an update status other
than that it is up to date.

A newer version of Notebook OS is installed the same way as the first: by
writing a new image to a USB stick and running the installer.

## Multiple applications

One application runs in the foreground at a time. There is no taskbar, no
window switching between applications, and no side-by-side arrangement of two
applications.

Opening an application hides the desktop; closing it returns to the desktop.

The GBA SDK splits its own window into panes, which is a facility within that
one application rather than a general window management feature.

## Accounts

Notebook OS is a single-user system. Accounts are created during installation.
There is no facility for adding, removing, or switching accounts afterwards, and
no facility for changing a password after installation.

There is no password recovery. A forgotten password cannot be reset from within
the system.

## Installation options

The installer erases the selected disk and replaces its partition table. There
is no option to install alongside another operating system, to resize an
existing partition, or to install to an existing partition.

Only UEFI firmware is supported. BIOS and legacy boot are not.

## Printing

Only USB printers are supported. Network printers, shared printers, and
wireless printers are not.

Printers that require a manufacturer-supplied driver not included with the
system will not work. Exporting to PDF and printing the file from another
computer is the alternative.

## Bluetooth

The kernel supports Bluetooth, but no application in version 1.0 uses it. There
is no pairing interface and no Bluetooth device support at the desktop level.

## Sound

There is no recorded speech anywhere in the system. This affects two things:

- The Language application teaches pronunciation with the International
  Phonetic Alphabet rather than with audio.
- There are no system sounds or alert sounds.

Music playback, video playback, and audio recording all work normally.

## Television and video capture

There is no television tuner support. The kernel has no media subsystem, and
tuner and demodulator drivers cannot be added to it.

Video files play normally; only live broadcast capture is absent.

## Game controllers

USB game controllers are supported as generic HID devices, which covers most
commercially available pads. Controllers requiring a manufacturer-specific
kernel driver — some Xbox pads, some Logitech models in their proprietary modes
— are not supported.

## Accessibility

Large text and high contrast are available in **Settings > Accessibility**.

No screen reader, screen magnifier, or on-screen keyboard is included.

## Backup

The only backup facility is a copy to a USB storage device from **Settings >
Backup**. There is no scheduled backup, no incremental backup, and no versioned
history.

## Desktop customisation

The desktop backdrop is a single fixed colour. There is no wallpaper, no theme
selection, and no colour scheme choice.

The interface scale and text size are adjustable in Settings; the colours are
not.

## File systems

USB storage is supported when formatted as FAT32, exFAT, NTFS, or ext2/3/4.

Encrypted volumes, RAID arrays, and logical volume management are not
supported.

## What this leaves

The system does everything described in chapters 03 to 12 without any of the
above. The limitations are a consequence of the machine having no network, which
is the point of the product rather than a stage on the way to something else.
