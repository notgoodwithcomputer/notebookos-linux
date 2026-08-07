#!/usr/bin/env python3
"""Merge per-language translation fragments into the 17 shipped catalogs.

    python3 tools/i18n_merge.py FRAGDIR [--rename OLD=NEW ...] [--apply]

FRAGDIR holds one <lang>.json per language, each a flat {english: translation}
map. Without --apply this is a dry run and prints what would change.

TWO THINGS THIS EXISTS TO PREVENT, both learned the hard way:

  * A HALF-WRITTEN SET. Seventeen catalogs edited in a loop, with a raise in the
    middle, leaves the shipped tree with some catalogs holding the new keys and
    some not -- and every check that compares the catalogs to each other then
    reports a deficit in the wrong files. So every catalog is built in memory
    and validated first; nothing is written until all seventeen are ready.

  * A PADDED KEY. A translation filed under "Check " instead of "Check" is not
    an error anywhere: the lookup simply misses and the app shows English. It
    reddens no test and is invisible on screen unless you happen to be reading
    that language. Keys are compared byte for byte against the English source.
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DE = os.path.abspath(os.path.join(HERE, "..", "buildroot", "board", "notebookos",
                                  "rootfs-overlay", "opt", "notebook", "de"))
LANGS = ["de", "el", "eo", "es", "fr", "hi", "it", "ja", "ko", "nl", "pl", "pt",
         "ru", "sr", "tr", "yi", "zh"]
PH = re.compile(r"%[-#0 +]*[0-9]*(?:\.[0-9]+)?[dsfx%]")

# A THIRD thing this exists to prevent, found when the first plural string in a
# while came through: the placeholder check refused every counted string.
#
# "%d item%s could not be deleted." carries two specs, but the second is the
# English plural hack -- the `"s" if n != 1 else ""` that no other language
# forms plurals with. nbi18n consumes it on the way in and never emits it, so a
# correct German translation is "%d Element|%d Elemente": one spec, and the pipe
# giving both grammatical numbers. Compared naively that reads as a lost
# placeholder and was rejected, which would have pushed every future plural
# string around this tool and straight back into the half-written-set failure it
# exists to prevent.
#
# The classifier is IMPORTED from the runtime rather than reimplemented here. If
# the two ever disagreed about which %s is a plural marker, this would either
# refuse a correct translation or admit one the app cannot format -- and the
# disagreement would show up as a crash in a language nobody on the project
# reads.
sys.path.insert(0, DE)
import nbi18n  # noqa: E402


def key_placeholders(english):
    """The specs a translation must carry: every one except the slots that only
    exist to make English grammar agree with a count."""
    sp = nbi18n._split_spec(english)
    kinds = nbi18n._spec_kinds(sp)
    return ([p for (k, p), pl in zip(sp, kinds) if k == "spec" and not pl],
            any(kinds))


def placeholders(s):
    return [p for p in PH.findall(s) if p != "%%"]


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    fragdir = argv[0]
    apply_ = "--apply" in argv
    renames = {}
    for a in argv[1:]:
        if a.startswith("--rename="):
            old, _, new = a[len("--rename="):].partition("=")
            renames[old] = new

    problems = []
    staged = {}
    added = {}
    for lang in LANGS:
        cat_path = os.path.join(DE, "lang_%s.json" % lang)
        frag_path = os.path.join(fragdir, "%s.json" % lang)
        try:
            with io.open(cat_path, encoding="utf-8") as fh:
                cat = json.load(fh)
        except Exception as e:
            problems.append("%s: catalog will not load: %s" % (lang, e))
            continue
        try:
            with io.open(frag_path, encoding="utf-8") as fh:
                frag = json.load(fh)
        except Exception as e:
            problems.append("%s: fragment will not load: %s" % (lang, e))
            continue
        n = 0
        for k, v in frag.items():
            k = renames.get(k, k)
            if k.strip() != k:
                problems.append("%s: key %r has padding" % (lang, k))
                continue
            if not isinstance(v, str) or not v.strip():
                problems.append("%s: %r is empty" % (lang, k))
                continue
            want, counted = key_placeholders(k)
            # A counted string may give both grammatical numbers as
            # "singular|plural"; each form is a complete sentence and each must
            # carry the specs on its own. Splitting only when the key really is
            # counted keeps a literal pipe in ordinary prose from being read as
            # a plural split -- the same condition nbi18n applies.
            forms = (v.split("|") if counted and v.count("|") == 1 else [v])
            if any(sorted(placeholders(f)) != sorted(want) for f in forms):
                problems.append("%s: %r placeholders %s -> %s"
                                % (lang, k, want,
                                   [placeholders(f) for f in forms]))
                continue
            if k not in cat:
                n += 1
            cat[k] = v
        staged[lang] = cat
        added[lang] = n

    sizes = {len(c) for c in staged.values()}
    if len(staged) != len(LANGS):
        problems.append("only %d of %d catalogs staged"
                        % (len(staged), len(LANGS)))
    if len(sizes) > 1:
        problems.append("catalogs would end at different sizes: %s"
                        % sorted(sizes))

    for p in problems:
        print("x %s" % p)
    for lang in LANGS:
        if lang in staged:
            print("  %-3s %d keys (+%d)" % (lang, len(staged[lang]),
                                            added[lang]))
    if problems:
        print("\n%d problem(s); NOTHING written" % len(problems))
        return 1
    if not apply_:
        print("\ndry run; pass --apply to write")
        return 0
    for lang in LANGS:
        path = os.path.join(DE, "lang_%s.json" % lang)
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(staged[lang], fh, ensure_ascii=False, indent=1,
                      sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    print("\nwrote %d catalogs" % len(LANGS))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
