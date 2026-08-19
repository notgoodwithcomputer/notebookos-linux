#!/usr/bin/env python3
"""Static contracts for udev entry points that cannot be driven on the host."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "buildroot/board/notebookos/rootfs-overlay"


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("PASS", message)


def automount_contract():
    path = OVERLAY / "etc/udev/rules.d/99-notebook-automount.rules"
    rows = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for action in ("add", "remove"):
        for devtype in ("disk", "partition"):
            found = [
                row for row in rows
                if f'ACTION=="{action}"' in row
                and f'ENV{{DEVTYPE}}=="{devtype}"' in row
            ]
            check(len(found) == 1, f"one {action} rule handles USB {devtype}s")
            row = found[0]
            check('SUBSYSTEM=="block"' in row, f"{action}/{devtype} is block-only")
            check('SUBSYSTEMS=="usb"' in row, f"{action}/{devtype} is USB-only")
            check('KERNEL=="sd*"' in row, f"{action}/{devtype} accepts whole/multi-digit sd names")
            check(f'automount.sh {action} %k' in row, f"{action}/{devtype} passes the kernel name")


def display_contract():
    rule = (
        OVERLAY / "etc/udev/rules.d/99-notebook-display.rules"
    ).read_text(encoding="utf-8")
    rows = [line.strip() for line in rule.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]
    matching = [row for row in rows
                if 'ACTION=="change"' in row
                and 'SUBSYSTEM=="drm"' in row
                and 'ENV{HOTPLUG}=="1"' in row
                and 'RUN+="/opt/notebook/display-hotplug.sh"' in row]
    check(len(matching) == 1,
          "one active rule handles kernel-marked DRM connector hotplugs")
    check(len(rows) == 1,
          "no duplicate or broader display-change rule also reconfigures outputs")

    wrapper = (
        OVERLAY / "opt/notebook/display-hotplug.sh"
    ).read_text(encoding="utf-8")
    required = (
        "export DISPLAY=:0",
        "export NB_HOME=/root",
        "exec 9>/tmp/nb-display-hotplug.lock",
        "flock 9",
        "sleep 1",
        "/opt/notebook/display.sh",
        "nbprefs.py",
        "nbaudio.py apply",
    )
    for token in required:
        check(token in wrapper, f"display wrapper retains {token!r}")
    commands = "\n".join(
        line.strip() for line in wrapper.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    command_lines = commands.splitlines()
    positions = [
        next(i for i, line in enumerate(command_lines) if line.startswith(prefix))
        for prefix in ("flock 9", "sleep 1", "/opt/notebook/display.sh ",
                       "python3 /opt/notebook/de/nbprefs.py ",
                       "python3 /opt/notebook/de/nbaudio.py apply")
    ]
    check(positions == sorted(positions),
          "settle, topology, saved display preferences, and audio run in order")
    check(not re.search(r"(?s)\([^)]*sleep\s+1[^)]*\)\s*&", wrapper),
          "hotplug work stays foreground for udev")
    busybox = (ROOT / "buildroot/package/busybox/busybox.config").read_text()
    check("CONFIG_FLOCK=y" in busybox,
          "the image provides the automatically released hotplug lock")


def main():
    automount_contract()
    display_contract()
    print("hotplug contract: PASS")


if __name__ == "__main__":
    main()
