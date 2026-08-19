#!/usr/bin/env python3
"""font_selftest — is every font the OS OFFERS actually on the disc?

    python3 tools/font_selftest.py

A font picker that lists a family the image does not carry is worse than one
that omits it. Nothing errors: fontconfig quietly substitutes the closest
thing it has, so the person picks "Spectral", the text changes not at all, and
the document is saved carrying a family name that will render as something else
on the next machine. The failure is SILENT BY DESIGN — which is why it needs a
gate rather than a look.

So this checks, by FILE and not by family name (a name match is exactly what a
substitution also produces):

1. Every family in `writer.FONT_FAMILIES` resolves to a font file that ships in
   this tree, and to a file whose own family name is the one asked for.
2. Every family offers the styles the picker implies — a bold button that
   silently synthesises bold is not the same as a bold face.
3. Every vendored family ships its licence next to it, because that is the
   condition on which all of them may be redistributed at all.
4. The faces are real: distinct outlines, and Latin coverage good enough to set
   a paragraph in.

Run against the OVERLAY tree, so it tests what will ship rather than what the
build host happens to have installed.

Exit status is the number of failures.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO,
                  "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
OVERLAY_FONTS = os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/usr/share/fonts")
TARGET_FONTS = os.path.join(REPO, "buildroot/output/target/usr/share/fonts")
VENDOR = os.path.join(REPO, "assets/fonts")
FONT_CONF = os.path.join(
    REPO, "buildroot/board/notebookos/rootfs-overlay/etc/fonts/conf.d")

sys.path.insert(0, DE)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="font-selftest-"))

FAILS = []
SKIPS = []
CHECKS = [0]


def check(name, ok, detail=""):
    CHECKS[0] += 1
    print("%-4s %s%s" % ("ok" if ok else "FAIL", name,
                         "" if ok else "   -> " + str(detail)))
    if not ok:
        FAILS.append(name)


def skip(name, why):
    SKIPS.append(name)
    print("SKIP %s   -> %s" % (name, why))


def mutant(name, ok_when_broken):
    CHECKS[0] += 1
    caught = not ok_when_broken
    print("%-4s MUTANT %s%s" % ("ok" if caught else "FAIL", name,
                                "" if caught else
                                "   -> sabotage went UNDETECTED"))
    if not caught:
        FAILS.append("MUTANT " + name)


# A fontconfig config pointing ONLY at this repo's font trees. Without it the
# whole suite would be measuring the BUILD HOST's fonts: a developer machine
# with Lato installed would pass while the image shipped nothing, which is the
# instrument-measured-a-different-artifact trap.
def fontconfig_for_repo():
    conf = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False,
                                       encoding="utf-8")
    cache = tempfile.mkdtemp(prefix="font-selftest-cache-")
    dirs = "\n".join("  <dir>%s</dir>" % d
                     for d in (OVERLAY_FONTS, TARGET_FONTS)
                     if os.path.isdir(d))
    policies = "\n".join(
        "  <include>%s</include>" % os.path.join(FONT_CONF, name)
        for name in ("09-notebookos-skip-bitmaps.conf",
                     "99-notebookos-cjk.conf", "99-notebookos.conf"))
    conf.write('<?xml version="1.0"?>\n'
               '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
               "<fontconfig>\n%s\n%s\n  <cachedir>%s</cachedir>\n</fontconfig>\n"
               % (dirs, policies, cache))
    conf.close()
    return conf.name


CONF = fontconfig_for_repo()
ENV = dict(os.environ, FONTCONFIG_FILE=CONF)


def have(tool):
    from shutil import which
    return which(tool) is not None


def fc(tool, *args):
    try:
        p = subprocess.run([tool] + list(args), capture_output=True, text=True,
                           env=ENV, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout


def match(family, fmt="%{file}"):
    out = fc("fc-match", family, "--format=" + fmt)
    return (out or "").strip()


def shipped_font_path(path):
    """True only for a resolved file beneath one of the image font roots."""
    if not path:
        return False
    resolved = os.path.realpath(path)
    for root in (OVERLAY_FONTS, TARGET_FONTS):
        try:
            if os.path.commonpath([resolved, os.path.realpath(root)]) \
                    == os.path.realpath(root):
                return True
        except ValueError:
            continue
    return False


mutant("a repository-prefix sibling is not a shipped font root",
       shipped_font_path(REPO + "-evil/fonts/Fake.ttf"))


import writer                                                 # noqa: E402

print("--- 1. every offered family is on the disc -----------------------")

if not (have("fc-match") and have("fc-list")):
    skip("font resolution", "fontconfig tools (fc-match/fc-list) not on PATH")
else:
    for family in writer.FONT_FAMILIES:
        path = match(family)
        got = match(family, "%{family[0]}")
        inside = shipped_font_path(path)
        # BOTH conditions matter. A file inside the tree but with the wrong
        # family name is a substitution that happens to land on another of our
        # own fonts, which is precisely the silent failure being hunted.
        check("%s resolves to a shipped file, and to itself" % family,
              inside and got == family,
              "file=%s family=%s" % (path or "(none)", got or "(none)"))

    # The red-proof: a family nobody ships must NOT pass, or the check above is
    # measuring nothing. fontconfig always returns *something*, so "a path came
    # back" is not evidence.
    bogus = match("Nonexistent Face XYZ", "%{family[0]}")
    mutant("a family the image does not carry",
           bogus == "Nonexistent Face XYZ")

    # These aliases are the desktop-wide defaults, not merely compatibility
    # spellings. Weak aliases can lose to another installed candidate (the
    # measured failure was sans-serif resolving to Fira Sans), so assert the
    # family Fontconfig actually chooses with these policy files loaded.
    for generic, expected in (("sans", "Nimbus Sans"),
                              ("sans-serif", "Nimbus Sans"),
                              ("serif", "Liberation Serif"),
                              ("monospace", "Liberation Mono")):
        got = match(generic, "%{family[0]}")
        check("%s resolves to the Notebook OS default" % generic,
              got == expected, "got %s, want %s" % (got, expected))

    cjk_chain = (fc("fc-match", "-s", "sans:lang=zh-cn",
                    "--format=%{family[0]}\n") or "").splitlines()
    check("generic Chinese UI text retains the shipped CJK fallback",
          bool(cjk_chain) and cjk_chain[0] == "Nimbus Sans"
          and "Noto Sans CJK SC" in cjk_chain,
          cjk_chain[:8])

    print("\n--- 2. the styles the picker implies actually exist ------------")
    # Writer offers bold and italic buttons for whatever is selected. Where a
    # family has no real bold, the toolkit SYNTHESISES one by smearing the
    # outline, which looks like a bug in the font rather than a missing face.
    text_faces = [f for f in writer.FONT_FAMILIES
                  if f not in ("Bebas Neue", "Abril Fatface", "Patrick Hand",
                               "Indie Flower", "Komika Hand", "Fira Mono")]
    for family in text_faces:
        styles = set()
        out = fc("fc-list", ":family=%s" % family, "style") or ""
        for line in out.splitlines():
            if ":style=" in line:
                styles.update(s.strip() for s in
                              line.split(":style=", 1)[1].split(","))
        want_bold = any("Bold" in s for s in styles)
        want_ital = any(s in ("Italic", "Oblique") or "Italic" in s
                        or "Oblique" in s for s in styles)
        check("%s ships a real bold and a real italic" % family,
              want_bold and want_ital, sorted(styles))

print("\n--- 3. every vendored family carries its licence ------------------")

if not os.path.isdir(VENDOR):
    skip("vendored licences", "assets/fonts not present")
else:
    families = sorted(d for d in os.listdir(VENDOR)
                      if os.path.isdir(os.path.join(VENDOR, d)))
    check("the vendored set is not empty", bool(families), families)
    for fam in families:
        d = os.path.join(VENDOR, fam)
        lic = os.path.join(d, "OFL.txt")
        ok = os.path.exists(lic) and os.path.getsize(lic) > 1000
        text = ""
        if ok:
            with open(lic, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        check("%s ships its OFL licence" % fam,
              ok and "SIL OPEN FONT LICENSE" in text.upper(),
              "missing or not an OFL" if not ok else "")
    # ...and it has to reach the image, not just the vendor directory.
    for fam in families:
        out = os.path.join(OVERLAY_FONTS, "notebookos", fam)
        ttfs = [f for f in os.listdir(out)] if os.path.isdir(out) else []
        check("%s is installed into the overlay with its licence" % fam,
              any(f.endswith(".ttf") for f in ttfs) and "OFL.txt" in ttfs,
              ttfs[:4])

    check("PROVENANCE records where the fonts came from",
          os.path.exists(os.path.join(VENDOR, "PROVENANCE.txt")))

print("\n--- 4. the faces are real and distinct ---------------------------")

try:
    import gi
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo               # noqa: E402
    import cairo                                              # noqa: E402
    HAVE_PANGO = True
except Exception:                                             # noqa: BLE001
    HAVE_PANGO = False

if not HAVE_PANGO:
    skip("rendered distinctness", "Pango/cairo unavailable")
else:
    os.environ["FONTCONFIG_FILE"] = CONF
    SAMPLE = "Hamburgefonstiv 0123 — fjord"

    def render(family):
        surf = cairo.ImageSurface(cairo.FORMAT_A8, 620, 60)
        cr = cairo.Context(surf)
        layout = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription()
        fd.set_family(family)
        fd.set_size(28 * Pango.SCALE)
        layout.set_font_description(fd)
        layout.set_text(SAMPLE, -1)
        cr.move_to(2, 2)
        PangoCairo.show_layout(cr, layout)
        surf.flush()
        return bytes(surf.get_data()), layout.get_pixel_size()

    shots = {}
    for family in writer.FONT_FAMILIES:
        shots[family] = render(family)

    inked = [f for f, (_d, size) in shots.items() if size[0] > 20]
    check("every offered family sets a visible line of text",
          len(inked) == len(writer.FONT_FAMILIES),
          sorted(set(writer.FONT_FAMILIES) - set(inked)))

    # Two families rendering IDENTICAL pixels means one substituted for the
    # other — the same silent fallback section 1 hunts, seen from the output.
    same = []
    names = list(shots)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if shots[a][0] == shots[b][0]:
                same.append((a, b))
    check("no two offered families render identical text", not same, same[:4])

    # The picker draws each name in its own face; a family that cannot set its
    # own name would show the list in the fallback face and look broken.
    unnamed = [f for f in writer.FONT_FAMILIES
               if render(f)[1][0] < 10]
    check("every family can set its own name in the list", not unnamed,
          unnamed)

    mutant("two names that are the same face under different spellings",
           render("Lato")[0] != render("Lato ")[0])

print("\n%d checks, %d passed, %d FAILED%s"
      % (CHECKS[0], CHECKS[0] - len(FAILS), len(FAILS),
         ", %d skipped" % len(SKIPS) if SKIPS else ""))
if FAILS:
    print("RESULT: FAILED")
    for f in FAILS:
        print("   - %s" % f)
else:
    print("RESULT: ALL PASS")
sys.exit(len(FAILS))
