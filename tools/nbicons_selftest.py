#!/usr/bin/env python3
"""Headless provenance, generation, geometry, and mutation checks for nbicons."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import cairo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODULE_DIR = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
DEFAULT_VENDOR = ROOT / "vendor/lucide"
SIZES = (16, 24, 48)
APP_ICONS = {
    "animation", "burner",
    "writer", "novel", "comics", "academic", "journal", "screenplay",
    "tasks", "calendar", "workout", "cookbook", "mealplanner", "ebook",
    "calculator", "accounting", "bills", "contacts", "messages", "g2048",
    "tetris", "gamepad", "mappin", "globe", "cartridge", "illustrator",
    "sequencer", "composer", "video", "media", "music", "packages", "sys",
    "terminal", "sysmon", "installer", "gbasdk", "usbwriter",
}


def load_module():
    module_dir = Path(os.environ.get("NBICONS_MODULE_DIR", DEFAULT_MODULE_DIR))
    sys.path.insert(0, str(module_dir))
    sys.modules.pop("nbicons_data", None)
    path = module_dir / "nbicons.py"
    spec = importlib.util.spec_from_file_location("nbicons_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pixels(module, name, size, mirror=False, padding=0):
    side = size + 2 * padding
    surface = cairo.ImageSurface(cairo.FORMAT_A8, side, side)
    ctx = cairo.Context(surface)
    ctx.translate(padding, padding)
    module.draw(ctx, name, size, mirror=mirror)
    surface.flush()
    stride = surface.get_stride()
    raw = bytes(surface.get_data())
    return b"".join(raw[y*stride:y*stride+side] for y in range(side))


def check_mapping(module, vendor):
    failures = []
    keys, mapped = set(module.ICONS), set(module.MAPPING)
    for name in sorted(keys - mapped):
        failures.append(f"FAIL mapping: {name} has no Lucide mapping")
    for name in sorted(mapped - keys):
        failures.append(f"FAIL mapping: stale mapped key {name}")
    for name, stem in sorted(module.MAPPING.items()):
        if not (vendor / "icons" / f"{stem}.svg").is_file():
            failures.append(f"FAIL mapping: {name} missing vendor SVG {stem}.svg")
    return failures


def check_generation(module_dir, vendor):
    with tempfile.TemporaryDirectory(prefix="nbicons-drift-", dir=ROOT / ".codex-scratch") as tmp:
        output = Path(tmp) / "nbicons_data.py"
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools/gen_nbicons.py"),
             "--vendor", str(vendor), "--output", str(output)],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        if proc.returncode:
            return ["FAIL generator-current: regeneration failed: " +
                    (proc.stderr or proc.stdout).strip()]
        if output.read_bytes() != (module_dir / "nbicons_data.py").read_bytes():
            return ["FAIL generator-current: nbicons_data.py has drifted"]
    return []


def check_license(module_dir, vendor):
    failures = []
    if not (vendor / "LICENSE").is_file():
        failures.append("FAIL license: vendor/lucide/LICENSE is missing")
    data = (module_dir / "nbicons_data.py").read_text(encoding="utf-8")
    if "Lucide 1.31.0, ISC license" not in data:
        failures.append("FAIL license: generated data lacks ISC provenance")
    return failures


def check_app_uniqueness(module):
    failures = []
    used = {}
    for name in sorted(APP_ICONS):
        stem = module.MAPPING.get(name)
        if stem in used:
            failures.append(
                f"FAIL app-uniqueness: {name} and {used[stem]} both map to {stem}"
            )
        used[stem] = name
    return failures


def check_renders(module):
    failures = []
    for name in sorted(module.ICONS):
        for size in SIZES:
            try:
                first = pixels(module, name, size)
                second = pixels(module, name, size)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"FAIL render: {name}@{size}: {exc}")
                continue
            if not any(first):
                failures.append(f"FAIL render: {name}@{size} has no ink")
            if first != second:
                failures.append(f"FAIL deterministic: {name}@{size}")
            padding = max(4, size // 4)
            padded = pixels(module, name, size, padding=padding)
            side = size + 2 * padding
            outside = sum(
                padded[y*side+x] > 8
                for y in range(side) for x in range(side)
                if not (padding <= x < padding+size and padding <= y < padding+size)
            )
            if outside:
                failures.append(f"FAIL bounds: {name}@{size} ({outside} pixels)")
            semantic = pixels(module, name, size, mirror=name in module._DIRECTIONAL)
            if name in module._DIRECTIONAL and semantic == first:
                failures.append(f"FAIL mirror: {name}@{size} did not change")
            if name not in module._DIRECTIONAL and semantic != first:
                failures.append(f"FAIL mirror: {name}@{size} changed unexpectedly")
    return failures


def checks(module):
    module_dir = Path(os.environ.get("NBICONS_MODULE_DIR", DEFAULT_MODULE_DIR))
    vendor = Path(os.environ.get("NBICONS_VENDOR", DEFAULT_VENDOR))
    failures = []
    failures += check_mapping(module, vendor)
    failures += check_generation(module_dir, vendor)
    failures += check_license(module_dir, vendor)
    failures += check_app_uniqueness(module)
    failures += check_renders(module)
    return failures


def run_mutant(kind, expected):
    with tempfile.TemporaryDirectory(prefix="nbicons-mutant-", dir=ROOT / ".codex-scratch") as tmp:
        scratch = Path(tmp)
        shutil.copy2(DEFAULT_MODULE_DIR / "nbicons.py", scratch / "nbicons.py")
        shutil.copy2(DEFAULT_MODULE_DIR / "nbicons_data.py", scratch / "nbicons_data.py")
        vendor = DEFAULT_VENDOR
        if kind == "bounds":
            with (scratch / "nbicons_data.py").open("a", encoding="utf-8") as handle:
                handle.write("\nPATHS['writer'] = (('m', 12, 12), ('l', -20, 12))\n")
        elif kind == "duplicate":
            with (scratch / "nbicons_data.py").open("a", encoding="utf-8") as handle:
                handle.write("\nMAPPING['novel'] = MAPPING['writer']\n")
        elif kind == "license":
            vendor = scratch / "vendor/lucide"
            (vendor / "icons").mkdir(parents=True)
            for stem in set(load_module().MAPPING.values()):
                shutil.copy2(DEFAULT_VENDOR / "icons" / f"{stem}.svg", vendor / "icons" / f"{stem}.svg")
        else:
            raise ValueError(kind)
        env = os.environ.copy()
        env["NBICONS_MODULE_DIR"] = str(scratch)
        env["NBICONS_VENDOR"] = str(vendor)
        proc = subprocess.run(
            [sys.executable, __file__, "--checks-only"], cwd=ROOT, env=env,
            text=True, capture_output=True, check=False,
        )
        output = proc.stdout + proc.stderr
        if proc.returncode == 0 or expected not in output:
            print(f"FAIL pass-mutant: expected {expected!r}")
            if output: print(output.rstrip())
            return False
        print(f"PASS pass-mutant: {expected}")
        return True


def main():
    module = load_module()
    failures = checks(module)
    if failures:
        print("\n".join(failures))
        return 1
    print(f"PASS icons-lucide: {len(module.ICONS)} keys x {len(SIZES)} sizes")
    if "--checks-only" in sys.argv:
        return 0
    mutants = (
        run_mutant("bounds", "FAIL bounds: writer"),
        run_mutant("duplicate", "FAIL app-uniqueness:"),
        run_mutant("license", "FAIL license: vendor/lucide/LICENSE is missing"),
    )
    if not all(mutants): return 1
    print("PASS nbicons_selftest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
