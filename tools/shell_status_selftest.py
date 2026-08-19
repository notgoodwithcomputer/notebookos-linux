#!/usr/bin/env python3
"""Display-free regressions for panel status and dropdown activity."""

from io import StringIO
from pathlib import Path
import builtins
import sys
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DE = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import shell  # noqa: E402


def battery(files, entries):
    def fake_open(path, *args, **kwargs):
        key = str(path)
        if key not in files:
            raise OSError(key)
        return StringIO(str(files[key]))

    with mock.patch.object(shell.os, "listdir", return_value=entries), \
            mock.patch.object(builtins, "open", side_effect=fake_open):
        return shell.Panel._battery_pct(object())


def fixture(name, **values):
    base = "/sys/class/power_supply/" + name + "/"
    return {base + key: value for key, value in values.items()}


def check(label, condition):
    assert condition, label
    print("PASS", label)


def main():
    files = {}
    files.update(fixture("BAT0", type="Battery", scope="System", capacity=0,
                         energy_now=0, energy_full=20, status="Discharging"))
    files.update(fixture("BAT1", type="Battery", scope="System", capacity=80,
                         energy_now=40, energy_full=50, status="Discharging"))
    text, tip = battery(files, ["BAT0", "BAT1"])
    check("dual batteries use total energy, not the first pack", text == "57%")
    check("dual-battery tooltip identifies the aggregate", "2 batteries" in tip)

    files = {}
    files.update(fixture("BAT0", type="Battery", capacity=20,
                         charge_now=10, charge_full=50, status="Discharging"))
    files.update(fixture("BAT1", type="Battery", capacity=80,
                         charge_now=80, charge_full=100, status="Charging"))
    text, tip = battery(files, ["BAT0", "BAT1"])
    check("charge totals are weighted", text == "60%+")
    check("any charging pack marks the aggregate charging", "Charging" in tip)

    files = {}
    files.update(fixture("BAT0", type="Battery", capacity=20,
                         status="Discharging"))
    files.update(fixture("BAT1", type="Battery", capacity=80,
                         status="Discharging"))
    files.update(fixture("mouse", type="Battery", scope="Device", capacity=1,
                         status="Discharging"))
    text, _tip = battery(files, ["BAT0", "BAT1", "mouse"])
    check("capacity fallback averages system packs only", text == "50%")

    files = {}
    files.update(fixture("BAT0", type="Battery", energy_now=100,
                         energy_full=100, status="Discharging"))
    files.update(fixture("BAT1", type="Battery", capacity=20,
                         status="Discharging"))
    text, _tip = battery(files, ["BAT0", "BAT1"])
    check("mixed energy and capacity schemas include every pack", text == "60%")

    files = {}
    files.update(fixture("BAT0", type="Battery", charge_now=25,
                         charge_full=100, status="Discharging"))
    files.update(fixture("BAT1", type="Battery", capacity=75,
                         status="Discharging"))
    text, _tip = battery(files, ["BAT0", "BAT1"])
    check("mixed charge and capacity schemas include every pack", text == "50%")

    source = (DE / "shell.py").read_text(encoding="utf-8")
    for signal in ("motion-notify-event", "scroll-event", "button-press-event",
                   "button-release-event", "key-press-event",
                   "key-release-event", "touch-event"):
        handler = "_menu_key" if signal == "key-press-event" else "_menu_activity"
        check(f"dropdown activity includes {signal}",
              f'menu.connect("{signal}", self.{handler})' in source)

    print("shell status contract: PASS")


if __name__ == "__main__":
    main()
