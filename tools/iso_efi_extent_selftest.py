#!/usr/bin/env python3
"""An EFI MBR entry must describe real sectors inside the image."""
import importlib.util
import sys
from pathlib import Path
p = Path(__file__).with_name("iso_boot_check.py")
s = importlib.util.spec_from_file_location("iso_boot", p)
m = importlib.util.module_from_spec(s); sys.modules[s.name] = m; s.loader.exec_module(m)
assert m.valid_efi_partition((0xEF, 1, 8), 10)
assert not m.valid_efi_partition((0xEF, 0, 8), 10)
assert not m.valid_efi_partition((0xEF, 1, 0), 10)
assert not m.valid_efi_partition((0xEF, 8, 4), 10)
assert not m.valid_efi_partition((0x83, 1, 8), 10)
print("PASS EFI partition extent is nonempty and contained by the image")
print("RESULT: PASS")
