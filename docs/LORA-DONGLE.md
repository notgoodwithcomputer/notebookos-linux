# LoRa dongle: Ebyte E22-900T22U

The sanctioned long-range radio for Notebook OS. The machine has no network
stack by design; this dongle gives it kilometres of low-rate, off-grid
communication as a **plain serial device** — no networking code in the kernel,
no sockets, nothing for the no-internet policy to argue with.

## What the hardware is

- Semtech **SX1262** LoRa radio (850.125–930.125 MHz band, 22 dBm max TX,
  −122 dBm sensitivity at 2.4 kbps air rate, ~5 km line-of-sight).
- An onboard MCU in front of the radio. The host never speaks SX1262 SPI; it
  speaks **9600-8N1 serial** (configurable 1200–115200) to the MCU, which does
  packetising (240-byte subpackets from a 1000-byte buffer), addressing,
  optional relay/repeat, LBT, and a 16-bit XOR-style air encryption key.
- A USB-UART bridge in front of the MCU. Ebyte has shipped **two different
  bridges** across batches: the WCH **CH340** (`1a86:7523`) and the SiLabs
  **CP2102** (`10c4:ea60`). Same product name either way.
- One button (long-press ≥1.5 s toggles mode), one dual-colour LED
  (**green = transparent transmission mode**, **red = configuration mode**;
  flashing = busy). SMA-K antenna jack — do not transmit without an antenna.
- Siblings E22-230T22U / E22-400T22U differ only in band; everything below
  applies to them too.

## What the OS provides

Kernel (desktop config, all `=m`, autoloaded by MODALIAS when plugged in):

| module      | covers                                              |
|-------------|-----------------------------------------------------|
| `usbserial` | USB-serial core + generic fallback (`new_id` sysfs) |
| `ch341`     | CH340-bridged E22 batches                           |
| `cp210x`    | CP2102-bridged E22 batches                          |
| `ftdi_sio`  | FTDI adapters (also QEMU's `-device usb-serial`)    |
| `pl2303`    | Prolific adapters                                   |
| `cdc-acm`   | CDC-ACM class devices (`/dev/ttyACM*`)              |

udev (`/etc/udev/rules.d/99-notebook-serial.rules`):

- every USB tty gets `root:dialout 0660`;
- both known E22 bridge identities get a stable **`/dev/lora`** symlink.
  Software should open `/dev/lora`, not guess at `ttyUSB` numbers — with one
  stick plugged in it is correct even when some other adapter grabbed
  `ttyUSB0` first. `/dev/lora0`, `/dev/lora1`, … also exist (named by kernel
  tty number) to tell two sticks apart.

## The serial contract (for the future comms app)

- Open `/dev/lora` at **9600 8N1, raw** (factory default; the OS does not
  reconfigure the stick). Bytes written in transparent mode are broadcast on
  the module's channel; received packets appear as plain bytes.
- Module factory defaults (900-band): address `0x0000`, NETID 0, channel
  `0x12` → **868.125 MHz**, air rate 2.4 kbps, 240-byte subpackets, 22 dBm.
  Two sticks at factory defaults hear each other with zero setup.
- **US operation note:** 868 MHz is the European band. For US use, set a
  channel inside 902–928 MHz ISM — frequency = 850.125 + CH MHz, so CH 52–77;
  CH 65 = 915.125 MHz is the natural default.
- Configuration mode (red LED; enter by long-press, or `C0 C1 C2 C3 02 01` if
  software switching was enabled) always runs at **9600 8N1** regardless of
  the configured data rate. Registers, read `C1 addr len`, write
  `C0 addr len data…`, volatile write `C2 …`; the module echoes `C1 …` on
  success and `FF FF FF` on a malformed frame. Register map: `00/01`
  ADDH/ADDL, `02` NETID, `03` baud/parity/air-rate, `04` packet-size /
  RSSI-noise / TX-power, `05` channel, `06` RSSI-byte / fixed-point / relay /
  LBT, `07/08` crypt key (write-only), `80–86` product info. Full tables:
  Ebyte "E22-230/400/900T22U Product Specification".
- Fixed-point (targeted) transmission prefixes each payload with
  `ADDH ADDL CH`; address `FFFF` broadcasts/listens. The relay mode can
  bridge two NETIDs for multi-hop range.

## What was verified, and how

The runnable gate is:

```
tools/lora_guest_check.sh
```

It boots the built rootfs headless and proves, live in the guest, every link
the OS controls: the six modules ship for the running kernel;
`80-drivers.rules` + udevd's kmod builtin wire uevent → modprobe;
`modules.alias` maps the real dongle's `1a86:7523` / `10c4:ea60` to
ch341/cp210x (the exact lookup eudev does on plug-in); `modprobe ch341 /
cp210x / cdc_acm` insert into the running kernel; and the udev rule ships
with the `/dev/lora` contract.

The one link no QEMU run here can supply is the plug-in uevent itself:
QEMU's only USB-UART model is an FTDI FT232, and on this TCG rig it never
completes enumeration — cold-attached devices predate the UHCI driver's
root-hub poll and sit at address 0; a `qemu-xhci` attach is clean but its
MSI-X interrupts never arrive (0 in `/proc/interrupts` while virtio MSI-X on
the same guest works); hot-adds behind QEMU's auto-inserted hub need the hub
interrupt endpoint, i.e. the same dead IRQs. `--with-ftdi` runs that leg
anyway for a rig where it works (KVM host, newer QEMU). None of this touches
real hardware — a physical stick talks to the machine's real xHCI, which this
OS already runs USB storage and input on.

First time a physical stick is plugged into real hardware, confirm with:

```
dmesg | tail          # expect "ch341-uart ... now attached to ttyUSB0"
                      # or     "cp210x converter ... attached to ttyUSB0"
ls -l /dev/lora       # udev symlink -> ttyUSBn, group dialout, 0660
stty -F /dev/lora 9600 cs8 -cstopb -parenb raw
cat /dev/lora &       # on machine A
echo hello > /dev/lora    # on machine B (antennas on, same defaults)
```

If a future batch enumerates with an ID none of the drivers claim, the
generic fallback still works without a rebuild:
`echo <vid> <pid> > /sys/bus/usb-serial/drivers/generic/new_id`, then add the
ID to the udev rule and, properly, to the right driver.
