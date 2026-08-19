#!/usr/bin/env python3
"""Headless regression checks for bounded EPUB member reads."""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(ROOT, "buildroot", "board", "notebookos",
                  "rootfs-overlay", "opt", "notebook", "de")
sys.path.insert(0, DE)

import ebook  # noqa: E402


class FakeZip:
    def __init__(self, size, payload=b""):
        self.info = zipfile.ZipInfo("chapter.xhtml")
        self.info.file_size = size
        self.payload = payload
        self.reads = 0

    def getinfo(self, _name):
        return self.info

    def read(self, _info):
        self.reads += 1
        return self.payload


failed = []


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        failed.append(name)


large = FakeZip(ebook.EPUB_CHAPTER_MAX + 1)
try:
    ebook._epub_read_limited(large, "chapter.xhtml", ebook.EPUB_CHAPTER_MAX)
    refused = False
except ValueError:
    refused = True
check("an oversized expanded member is refused before decompression",
      refused and large.reads == 0)

small = FakeZip(4, b"test")
check("an in-budget member still reads normally",
      ebook._epub_read_limited(small, "chapter.xhtml", 4) == b"test"
      and small.reads == 1)

# Do not trust inconsistent metadata: the post-read bound is a second guard.
lying = FakeZip(1, b"too long")
try:
    ebook._epub_read_limited(lying, "chapter.xhtml", 4)
    refused = False
except ValueError:
    refused = True
check("a member that expands past its advertised size is refused", refused)

print("RESULT: " + ("ALL PASS" if not failed else "%d FAILED" % len(failed)))
raise SystemExit(bool(failed))
