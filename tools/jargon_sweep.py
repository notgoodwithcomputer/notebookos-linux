#!/usr/bin/env python3
"""jargon_sweep — find machinery language that leaked into user-facing text.

The audience is mature adults who make things (see docs/PAPER-PHYSICS.md §0):
precise domain vocabulary — export, render, emulator, sample rate in a
recorder — is CORRECT and stays. What must never leak is the machinery UNDER
the product: filesystems, process plumbing, toolkit names, serialization
formats. "PARTUUID" and "stderr" are not respect, they are the pipes showing.
Where a technical term IS the app's subject matter (the System Monitor's PID
column, the Terminal's own vocabulary), it is allowlisted EXPLICITLY, one
string, one reason — never a whole file. An earlier version exempted four
modules outright and discarded every single-word string as "probably an
identifier" (698 of 2,941 strings, measured 2026-08-07), which is exactly
where a leaked "Encode" button hides.

It reads STRING LITERALS ONLY, and deliberately skips docstrings and anything
that is plainly not UI (paths, URLs, format specifiers, lowercase identifier
shapes like css classes and signal names — a Title-case or UPPER single word
IS scanned, because that is what a label looks like).

A bare run is a GATE (task 026): tools/jargon_ledger.json holds the reviewed
state — "allow" (subject-matter uses, with reasons) and "pending" (real
leaks, awaiting rewrite by the owning app lane) — and both shelves ratchet:
a new leak fails, a fixed string whose entry remains fails.

    python3 tools/jargon_sweep.py [app ...]
"""
import json
import os
import re
import ast
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

# Machinery a user should never meet uninvited. Grouped so the report can say
# WHY each is a problem. These are UNDER-the-product words; domain words the
# product deliberately speaks (export, render, emulator) do not belong here.
JARGON = {
    "storage/plumbing": [
        "PARTUUID", "UUID", "ext4", "ext2", "vfat", "FAT32", "squashfs",
        "initrd", "initramfs", "bootloader", "GRUB", "EFI", "ESP", "partition table", "mount point", "unmount", "umount", "fsck", "inode", "sysfs", "procfs",
        "tmpfs", "overlayfs", "loopback", "blkid", "dd ",
    ],
    "process/system": [
        "daemon", "PID", "stdout", "stderr", "stdin", "subprocess", "SIGTERM",
        "SIGKILL", "kill -9", "exit code", "segfault", "core dump", "fork()",
        "thread", "mutex", "race condition", "deadlock",
    ],
    "graphics/X": [
        "X11", "Xorg", "xrandr", "framebuffer", "DRM", "KMS", "compositor",
        "vsync", "DPI", "subpixel", "GLX", "OpenGL", "swrast", "virgl",
        "pixbuf", "cairo", "GTK", "widget", "viewport",
    ],
    "code/data": [
        "JSON", "XML", "YAML", "regex", "parse error", "null", "NoneType",
        "traceback", "exception", "stack trace", "serialize", "deserialize",
        "hash", "checksum", "encoding", "UTF-8", "codec", "buffer", "cache miss", "malloc", "API", "callback", "boolean", "integer", "string literal",
    ],
    "audio/video internals": [
        "codec", "bitrate", "sample rate", "ALSA", "PulseAudio", "ffmpeg",
        "libav", "demux", "transcode", "keyframe", "timebase",
    ],
}

# Substrings that mean "this is not UI text". A LOWERCASE identifier shape
# (css class, signal name, GTK property, dict key) is skipped; a single word
# with any capital letter is scanned — that is what a label looks like in
# this OS, and single words are where jargon hides best.
NOT_UI = re.compile(
    r"^(/|\./|~/|[A-Za-z]:\\)"            # a path
    r"|^\w+://"                           # a URL
    r"|^[a-z_]+\.[a-z]{2,4}$"             # a filename
    r"|^%[sdrfx]"                          # a bare format spec
    r"|^\s*$"
    r"|^[a-z_][a-z0-9_.:-]*$"             # a lowercase identifier / css class
    r"|^-{1,2}[a-z]"                      # a command-line flag
    r"|^\*?\.?[a-z0-9_-]+\.(json|txt|log|png|conf|ini|css)$"   # a file glob
)


def ui_strings(path):
    """(line, text) for string literals that plausibly reach the screen."""
    src = open(path, encoding="utf-8").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    # collect docstring nodes so we can skip them
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docs:
            continue
        text = node.value
        if len(text) < 2 or NOT_UI.match(text):
            continue
        # CSS blocks are not prose
        if "{" in text and "}" in text and (";" in text or ":" in text):
            continue
        out.append((node.lineno, text))
    return out


def load_ledger():
    """{'allow': {string: reason}, 'pending': {file: [string...]}} — the
    reviewed state. A missing ledger means every finding fails; the gate
    cannot go vacuously green."""
    try:
        with open(os.path.join(REPO, "tools", "jargon_ledger.json"),
                  encoding="utf-8") as fh:
            data = json.load(fh)
        return (dict(data.get("allow", {})),
                {f: set(v) for f, v in data.get("pending", {}).items()})
    except (OSError, ValueError):
        return {}, {}


def main():
    names = sys.argv[1:] or sorted(
        f for f in os.listdir(DE) if f.endswith(".py"))
    allow, pending = load_ledger()
    partial = bool(sys.argv[1:])
    total = 0
    fails = 0
    seen_pending = {}
    for name in names:
        if not name.endswith(".py"):
            name += ".py"
        path = os.path.join(DE, name)
        if not os.path.isfile(path):
            continue
        hits = []
        for (ln, text) in ui_strings(path):
            for group, words in JARGON.items():
                for w in words:
                    # A term written WITH a trailing space ("dd ") means the
                    # space is part of it: the disk tool followed by its
                    # arguments, never the DD in a YYYY-MM-DD placeholder.
                    # Stripping it here once made a date format read as a
                    # block-copy command.
                    term = w.rstrip()
                    tail = r"\s" if w != term else r"\b"
                    if re.search(r"\b" + re.escape(term) + tail, text,
                                 re.IGNORECASE):
                        hits.append((ln, group, term, text))
                        break
        if hits:
            print("\n=== %s ===" % name)
            for (ln, group, w, text) in hits:
                total += 1
                if text in allow:
                    status = "allow"
                elif text in pending.get(name, ()):
                    status = "pending"
                    seen_pending.setdefault(name, set()).add(text)
                else:
                    status = "NEW"
                    fails += 1
                snippet = text if len(text) <= 130 else text[:127] + "..."
                print("  %s:%d  [%s: %s] (%s)\n      %r"
                      % (name, ln, group, w, status, snippet))
    # the ratchet's other direction — skipped on a partial run, which cannot
    # see every file a pending entry might live in
    if not partial:
        for name, texts in sorted(pending.items()):
            for text in sorted(texts - seen_pending.get(name, set())):
                print("STALE pending entry (%s): %r — fixed in source, delete "
                      "it from jargon_ledger.json" % (name, text[:60]))
                fails += 1
    print("\n%d flagged strings" % total)
    print("RESULT: " + ("CLEAN" if not fails
                        else "FAILED: %d unaccounted (new or stale)" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
