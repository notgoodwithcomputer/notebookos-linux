#!/usr/bin/env python3
"""picom_conf_check — is etc/picom.conf still valid libconfig?

    python3 tools/picom_conf_check.py [path ...]

WHY THIS EXISTS. A syntax error in picom.conf does not degrade the desktop, it
REMOVES the compositor: session.sh starts picom, picom refuses the file and
exits, and every window loses the shadow that tells it apart from the window
behind it. Nothing on screen says why, and it only shows on NB_ACCEL=1 machines
(the only ones that start a compositor at all), which are exactly the machines
a developer on a software-rendered VM never sees.

AND PICOM ITSELF CANNOT BE USED TO CHECK IT. The obvious test --

    DISPLAY=:99 picom --config picom.conf     # expect a parse error

-- is a FALSE GREEN. picom opens the display BEFORE it reads the config, so a
file with unbalanced braces and a missing semicolon reports exactly what a
perfect file reports: "Can't open display." Measured, both ways, on the target's
own binary. Any check whose red and green look identical is not a check.

So this parses the libconfig subset the file actually uses. It is deliberately
small: it answers "will libconfig accept this", not "is every setting name one
picom knows".
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/"
                             "etc/picom.conf")

# A libconfig scalar: number, boolean, quoted string, or a list/group opener.
_NAME = r"[A-Za-z][-A-Za-z0-9_]*"
_SCALAR = re.compile(
    r'^(?:""|true|false|[-+]?(?:0[xX][0-9A-Fa-f]+|'
    r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?))$')


def strip_comments(text):
    """Remove #, // and /* */ comments WITHOUT touching quoted strings.

    Done in one pass rather than with three regexes: a '#' inside a shadow
    colour ("#1A1916") is not a comment, and stripping it turned the rest of
    that line into nothing, which reads as a missing semicolon.
    """
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "#" or text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                return "".join(out), "unterminated /* comment"
            # keep newlines so line numbers stay honest
            out.append("\n" * text.count("\n", i, j))
            i = j + 2
            continue
        out.append(c)
        i += 1
    if in_str:
        return "".join(out), "unterminated string"
    return "".join(out), ""


def check(path):
    errs = []
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    text, err = strip_comments(raw)
    if err:
        return ["%s: %s" % (os.path.basename(path), err)]

    # 1. balanced delimiters, with the line of the one that never closed
    stack = []
    pairs = {"}": "{", "]": "[", ")": "("}
    line = 1
    for ch in text:
        if ch == "\n":
            line += 1
        elif ch in "{[(":
            stack.append((ch, line))
        elif ch in pairs:
            if not stack:
                errs.append("line %d: stray '%s'" % (line, ch))
            elif stack[-1][0] != pairs[ch]:
                errs.append("line %d: '%s' closes '%s' opened on line %d"
                            % (line, ch, stack[-1][0], stack[-1][1]))
                stack.pop()
            else:
                stack.pop()
    for ch, ln in stack:
        errs.append("line %d: '%s' is never closed" % (ln, ch))

    # 2. every setting is terminated. libconfig wants `name = value;` and a
    #    group/list member closed with `};` — a missing semicolon after the
    #    wintypes block is the realistic mistake, and it is silent.
    #
    #    STRING CONTENTS ARE MASKED FIRST. picom's own match expressions are
    #    settings-looking text INSIDE a string: opacity-rule holds
    #    "100:class_g = 'Shell.py'", and scanning into it reported class_g as
    #    an unterminated setting on a file that is perfectly valid. A checker
    #    that cries wolf on the shipped file is one nobody will run.
    masked = re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)
    flat = re.sub(r"\s+", " ", masked).strip()
    for m in re.finditer(r"(%s)\s*[=:]" % _NAME, flat):
        tail = flat[m.end():].lstrip()
        if not tail:
            errs.append("setting '%s' has no value" % m.group(1))
            continue
        if tail[0] in "{[":
            continue                      # a group/list; its close is checked
        seg = tail.split(";")[0]
        if ";" not in tail or "=" in seg.replace("\\=", ""):
            errs.append("setting '%s' is not terminated with ';'"
                        % m.group(1))
        elif not _SCALAR.fullmatch(seg.strip()):
            errs.append("setting '%s' has an invalid scalar value"
                        % m.group(1))

    # 3. blocks close with '};' — libconfig accepts '}' only at end of file
    for m in re.finditer(r"\}(?!\s*[;\]])", flat):
        if flat[m.end():].strip():
            errs.append("a '}' is not followed by ';'")
            break
    return ["%s: %s" % (os.path.basename(path), e) for e in errs]


def main(argv):
    paths = argv[1:] or [DEFAULT]
    bad = []
    for p in paths:
        if not os.path.exists(p):
            bad.append("%s: no such file" % p)
            continue
        bad += check(p)
    if bad:
        print("picom.conf: %d problem(s)" % len(bad))
        for b in bad:
            print("   - %s" % b)
        return 1
    print("picom.conf: valid libconfig (%s)"
          % ", ".join(os.path.basename(p) for p in paths))
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
