#!/usr/bin/env python3
"""Reject known visible prose that silently falls back to English.

Key-presence coverage cannot detect ``English key == translated value``. This
small focused gate pins the stable-interface sentences repaired here without
misclassifying legitimate identical tokens such as Linux, GIF, or BPM.
"""
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
KEYS = (
    "A slideshow needs at least two images in the folder.",
    "Choose a disk to continue.",
    "Choose an image file first.",
    "Installation is in progress.",
    "No document is open.",
    "Select a program to end it.",
    "Some installation details need attention.",
    "Choose a USB stick first.",
    "No file is open.",
    "No image is open.",
    "Open a journal entry to add a bullet.",
    "Open a journal entry to format a quote.",
    "Open a lecture to add a bullet list.",
    "Open a lecture to add a numbered list.",
    "Open a lecture to choose a paragraph style.",
    "Open a lecture to highlight text.",
    "The image is too large for this stick.",
    "The installation requirements are not met.",
    "The Background layer cannot be deleted.",
    "The only calendar cannot be deleted.",
    "The system to install is not available.",
    "This is the first page.",
    "This is the first step.",
    "This is the last page.",
    "This layer is already at the back.",
    "This layer is already at the front.",
    "This track has no clips to remove.",
    "Writing is already in progress.",
)

checked = 0


def unique_object(pairs):
    out = {}
    for key, value in pairs:
        assert key not in out, ("duplicate translation key", key)
        out[key] = value
    return out


for path in sorted(glob.glob(os.path.join(DE, "lang_*.json"))):
    with open(path, encoding="utf-8") as fh:
        catalog = json.load(fh, object_pairs_hook=unique_object)
    for key in KEYS:
        value = catalog.get(key)
        assert isinstance(value, str) and value.strip() and value != key, (
            os.path.basename(path), key, value)
        checked += 1

assert checked == 17 * len(KEYS), checked
print("PASS: %d visible translations do not fall back to English" % checked)
print("RESULT: PASS")
