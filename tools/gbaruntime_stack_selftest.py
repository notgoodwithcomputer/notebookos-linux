#!/usr/bin/env python3
"""IWRAM allocations must stop below both descending runtime stacks."""

from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RT = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/gbaruntime"
GCC = ROOT / "vendor-dl/gba-toolchain-min/bin/arm-none-eabi-gcc"
LIMIT = 0x7B00


def link(script, size):
    with tempfile.TemporaryDirectory(prefix="gba-stack-") as tmp:
        src = Path(tmp) / "boundary.c"
        elf = Path(tmp) / "boundary.elf"
        src.write_text("unsigned char arena[%d]; void _start(void) {}\n" % size)
        return subprocess.run([
            str(GCC), "-nostdlib", "-ffreestanding", "-T", str(script),
            str(src), "-o", str(elf)], capture_output=True, text=True)


def main():
    checks = []
    for name in ("gba.ld", "gba_mb.ld"):
        text = (RT / name).read_text()
        checks.append(("LENGTH = 0x7B00" in text, name + " reserves stacks"))
        if GCC.exists():
            checks.append((link(RT / name, LIMIT).returncode == 0,
                           name + " accepts the exact data boundary"))
            checks.append((link(RT / name, LIMIT + 4).returncode != 0,
                           name + " rejects four bytes into the stack"))
    crt = (RT / "crt0.s").read_text()
    checks.append(("=__sp_sys" in crt and "=__sp_irq" in crt,
                   "startup uses linker-owned stack symbols"))
    for ok, label in checks:
        print(("PASS" if ok else "FAIL") + ": " + label)
    all_ok = all(ok for ok, _ in checks)
    print("RESULT: %s" % ("ALL PASS" if all_ok else "FAILED"))
    return not all_ok


if __name__ == "__main__":
    raise SystemExit(main())
