#!/usr/bin/env python3
"""jargon_sweep — find developer language that leaked into user-facing text.

Notebook OS is for a mainstream, non-technical user. Words that are obvious to
whoever wrote the code ("buffer", "stderr", "PARTUUID", "daemon") are noise or
alarm to that user. This pulls every string that can reach the screen and flags
the ones carrying jargon, so they can be rewritten in the product's own voice.

It reads STRING LITERALS ONLY, and deliberately skips docstrings and anything
that is plainly not UI (log lines, shell commands, format specifiers, paths).

    python3 tools/jargon_sweep.py [app ...]
"""
import os
import re
import ast
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")

# Terms a mainstream user should never have to meet. Grouped so the report can
# say WHY each is a problem.
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

# Strings that are legitimately technical because the user asked for them
# (Terminal output, the System Monitor's own subject matter, build logs).
EXEMPT_FILES = {"terminal.py", "sysmon.py", "gbabuild.py", "nbgame.py"}
# Substrings that mean "this is not UI text".
NOT_UI = re.compile(
    r"^(/|\./|~/|[A-Za-z]:\\)"            # a path
    r"|^\w+://"                           # a URL
    r"|^[a-z_]+\.[a-z]{2,4}$"             # a filename
    r"|^%[sdrfx]"                          # a bare format spec
    r"|^\s*$"
    r"|^[A-Za-z_][A-Za-z0-9_]*$"          # a bare identifier / css class
    r"|^-{1,2}[a-z]"                      # a command-line flag
    r"|^\*?\.?[a-z0-9_-]+\.(json|txt|log|png|conf|ini|css)$"   # a file glob
    r"|^utf-8$"                           # an encoding argument
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
        if len(text) < 4 or NOT_UI.match(text):
            continue
        # CSS blocks are not prose
        if "{" in text and "}" in text and (";" in text or ":" in text):
            continue
        out.append((node.lineno, text))
    return out


def main():
    names = sys.argv[1:] or sorted(
        f for f in os.listdir(DE) if f.endswith(".py"))
    total = 0
    for name in names:
        if not name.endswith(".py"):
            name += ".py"
        if name in EXEMPT_FILES:
            continue
        path = os.path.join(DE, name)
        if not os.path.isfile(path):
            continue
        hits = []
        for (ln, text) in ui_strings(path):
            for group, words in JARGON.items():
                for w in words:
                    if re.search(r"\b" + re.escape(w.strip()) + r"\b", text,
                                 re.IGNORECASE):
                        hits.append((ln, group, w.strip(), text))
                        break
        if hits:
            print("\n=== %s ===" % name)
            for (ln, group, w, text) in hits:
                total += 1
                snippet = text if len(text) <= 150 else text[:147] + "..."
                print("  %s:%d  [%s: %s]\n      %r" % (name, ln, group, w, snippet))
    print("\n%d flagged strings" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
