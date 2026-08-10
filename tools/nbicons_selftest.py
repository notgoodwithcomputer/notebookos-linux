#!/usr/bin/env python3
"""Headless geometry and family checks for every Notebook OS icon.

NBICONS_MODULE_DIR may point at a scratch directory containing nbicons.py.  The
mutation proofs use that route, so they can make checks fail without ever
rewriting the source tree.
"""
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
SIZES = (16, 24, 48)


def load_module():
    module_dir = Path(os.environ.get("NBICONS_MODULE_DIR", DEFAULT_MODULE_DIR))
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
    rows = [raw[y * stride:y * stride + side] for y in range(side)]
    return b"".join(rows)


def checks(module):
    failures = []
    names = sorted(module.ICONS)
    if not names:
        failures.append("FAIL coverage: ICONS has no keys")
        return failures

    for name in names:
        if not module.ICONS[name]:
            failures.append(f"FAIL coverage: {name} has an empty op list")
            continue
        for size in SIZES:
            try:
                first = pixels(module, name, size)
                second = pixels(module, name, size)
            except Exception as exc:  # noqa: BLE001 - report the glyph by name
                failures.append(f"FAIL draw: {name}@{size}: {exc}")
                continue
            if first != second:
                failures.append(f"FAIL deterministic: {name}@{size}")
            coverage = sum(first) / (255.0 * size * size)
            if coverage == 0:
                failures.append(f"FAIL coverage: {name}@{size} is empty")
            if not 0.012 <= coverage <= 0.42:
                failures.append(
                    f"FAIL family-weight: {name}@{size} coverage={coverage:.4f}"
                )

            pad = max(4, size // 4)
            padded = pixels(module, name, size, padding=pad)
            side = size + 2 * pad
            outside = 0
            for y in range(side):
                for x in range(side):
                    if pad <= x < pad + size and pad <= y < pad + size:
                        continue
                    # Ignore only the faintest antialias fringe.
                    if padded[y * side + x] > 8:
                        outside += 1
            if outside:
                failures.append(f"FAIL bounds: {name}@{size} ({outside} pixels)")

            semantic_mirror = pixels(
                module, name, size, mirror=name in module._DIRECTIONAL
            )
            if name in module._DIRECTIONAL and semantic_mirror == first:
                failures.append(f"FAIL mirror: {name}@{size} did not change")
            if name not in module._DIRECTIONAL and semantic_mirror != first:
                failures.append(f"FAIL mirror: {name}@{size} changed unexpectedly")
    return failures


def run_mutant(source, assignment, expected):
    with tempfile.TemporaryDirectory(prefix="nbicons-mutant-", dir=ROOT / ".codex-scratch") as tmp:
        target = Path(tmp) / "nbicons.py"
        shutil.copy2(source, target)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(f"\n# self-test mutation\n{assignment}\n")
        env = os.environ.copy()
        env["NBICONS_MODULE_DIR"] = tmp
        proc = subprocess.run(
            [sys.executable, __file__, "--checks-only"],
            cwd=ROOT, env=env, text=True, capture_output=True, check=False,
        )
        output = proc.stdout + proc.stderr
        if proc.returncode == 0 or expected not in output:
            print(f"FAIL pass-mutant: expected {expected!r}")
            if output:
                print(output.rstrip())
            return False
        print(f"PASS pass-mutant: {expected}")
        return True


def main():
    module = load_module()
    failures = checks(module)
    if failures:
        print("\n".join(failures))
        return 1
    print(f"PASS icons: {len(module.ICONS)} keys x {len(SIZES)} sizes")
    if "--checks-only" in sys.argv:
        return 0

    source = Path(module.__file__).resolve()
    ok_bounds = run_mutant(
        source,
        'ICONS["writer"] = [("M", -4, 12), ("L", 5, 12)]',
        "FAIL bounds: writer",
    )
    ok_empty = run_mutant(
        source,
        'ICONS["writer"] = []',
        "FAIL coverage: writer has an empty op list",
    )
    if not (ok_bounds and ok_empty):
        return 1
    print("PASS nbicons_selftest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
