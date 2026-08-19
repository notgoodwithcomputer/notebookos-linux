#!/usr/bin/env python3
"""An anchor mention is whole-word, framed, and in the anchor's own SENSE.

The sense half is the one that cost a false finding. `Music` is the name on the
Places row; `music` is the audio, and

    The disc in the drive cannot be used. A music CD needs a blank CD-R or CD-RW.

is about a kind of disc. Japanese writes 音楽 CD there and ミュージック for the
folder — correct, and reported as a defect until the anchor stopped matching
lowercased. Every assertion below states which sense it is asserting.
"""
import importlib.util
from pathlib import Path

path = Path(__file__).with_name("anchored_term_check.py")
spec = importlib.util.spec_from_file_location("anchored", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

NAME, NOUN = True, False

# whole words only, inside a locative frame
assert not mod.mentions("Save in musical notation", "Music", (), True, NAME)
assert not mod.mentions("Save in Musical notation", "Music", (), True, NAME)
assert mod.mentions("Save in Music", "Music", (), True, NAME)
assert mod.mentions("Save in Music folder", "Music", (), True, NAME)
assert not mod.mentions("Keep this in folderish form", "Folder", (), True, NOUN)
print("PASS locative anchors do not match derivative words")

# the ordinary noun is NOT the app: a music CD is a disc, background music is
# audio, and the music an effect layers over is a track.
assert not mod.mentions("The disc in the drive cannot be used. A music CD needs "
                        "a blank CD-R or CD-RW.", "Music", (), True, NAME)
assert not mod.mentions("Put music on a CD, or video on a DVD.",
                        "Music", (), True, NAME)
assert not mod.mentions("An effect layers over the music; music replaces it",
                        "Music", (), True, NAME)
# nor is a name inside a Title Case label, where the capital is the label's
assert not mod.mentions("Add Background Music", "Music", (), True, NAME)
assert not mod.mentions("Add Music", "Music", (), True, NAME)
assert not mod.mentions("Music CD", "Music", (), True, NAME)
assert not mod.mentions("Music Box", "Music", (), True, NAME)
print("PASS the ordinary noun and the Title Case label are not the app name")

# ...while the folder itself still is, framed or standing among lowercase words
assert mod.mentions("The recording is saved in Music.", "Music", (), True, NAME)
assert mod.mentions("Tracks are read from Home / Music.", "Music", (), True, NAME)
assert mod.mentions("Kept as %s in Documents", "Documents", (), True, NAME)
assert mod.mentions("put them in your Home folders (Documents works well), or "
                    "use Open Game to pick one.", "Documents", (), True, NAME)
assert mod.mentions("Moved “%s” to the Trash", "Trash", (), True, NAME)
print("PASS a name in prose is the place, with or without a frame")

# a COMMON NOUN anchor is the same word in either case: `Move to Folder` and
# `A folder cannot be copied` name one thing, and both must be judged.
assert mod.mentions("A folder cannot be copied inside itself",
                    "Folder", (), True, NOUN)
assert mod.mentions("Move to Folder", "Folder", (), True, NOUN)
assert mod.mentions("Add a printer", "Printer", (), True, NOUN)
assert mod.mentions("Add to playlist", "Playlist", (), True, NOUN)
# and a rival word still decides the sense for the anchors that declare one
assert not mod.mentions("This map could not be read", "Folder",
                        ("map", "maps"), True, NOUN)
print("PASS common-noun anchors match in either case; rivals still decide sense")
print("RESULT: PASS")
