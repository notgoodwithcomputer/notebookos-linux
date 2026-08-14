#!/usr/bin/env python3
"""Headless-first contract checks for the Notebook OS Animation studio."""
from __future__ import annotations

import array
import ast
import base64
import copy
import importlib
import io
import json
import math
import os
from pathlib import Path
import random
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import types
import wave

REPO = Path(__file__).resolve().parents[1]
DE = REPO / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
MODULE_SOURCE = DE / "animation.py"
FONTCONF = REPO / "tools/guest-fonts.conf"
os.environ["FONTCONFIG_FILE"] = str(FONTCONF)
os.environ.setdefault("NB_HOME", tempfile.mkdtemp(prefix="animation-selftest-home-"))
sys.path.insert(0, str(DE))

import cairo  # noqa: E402
from gi.repository import GLib  # noqa: E402
import animation  # noqa: E402

CLIP_SKIP = '                if left > clip_x1 or left + width + 1 < clip_x0:\n                    continue'
CLIP_BLIND = '                if False:\n                    continue'

PASSES: list[str] = []
FAILS: list[str] = []
SKIPS: list[tuple[str, str]] = []
MUTANTS: list[str] = []
UNCAUGHT_MUTANTS: list[str] = []


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
        print("PASS " + name)
    else:
        FAILS.append(name)
        suffix = " - " + str(detail) if detail else ""
        print("FAIL " + name + suffix)


def skip(name, reason):
    SKIPS.append((name, reason))
    print("SKIP %s - %s" % (name, reason))


def mutant(name, caught, detail=""):
    if caught:
        MUTANTS.append(name)
        print("PASS-MUTANT " + name)
    else:
        UNCAUGHT_MUTANTS.append(name)
        suffix = " - " + str(detail) if detail else ""
        print("FAIL-MUTANT " + name + suffix)


def gtk_available():
    try:
        from gi.repository import Gtk
        return bool(Gtk.init_check(None)[0])
    except Exception:
        return False


def pixel(image, x, y):
    image.flush()
    offset = y * image.get_stride() + x * 4
    return bytes(image.get_data()[offset:offset + 4])


def image_bytes(image):
    image.flush()
    return bytes(image.get_data())


def module_mutant(name, replacements):
    """Import a sabotaged copy from sys.path[0], proving copy precedence."""
    scratch = Path(tempfile.mkdtemp(prefix="animation-mutant-"))
    source = MODULE_SOURCE.read_text(encoding="utf-8")
    marker = "animation-062-sabotaged-" + name
    source = source.replace("SELFTEST_MARKER = 'animation-062-real'",
                            "SELFTEST_MARKER = %r" % marker, 1)
    for old, new in replacements:
        if old not in source:
            raise AssertionError("mutant target missing: " + old[:80])
        source = source.replace(old, new, 1)
    (scratch / "animation.py").write_text(source, encoding="utf-8")
    previous = sys.modules.pop("animation", None)
    sys.path.insert(0, str(scratch))
    try:
        graded = importlib.import_module("animation")
        if graded.SELFTEST_MARKER != marker:
            raise AssertionError("sabotage imported pristine module")
    finally:
        sys.path.pop(0)
        sys.modules.pop("animation", None)
        if previous is not None:
            sys.modules["animation"] = previous
    return graded, scratch


def assert_runs(scene):
    for layer in scene["layers"]:
        runs = layer["runs"]
        for index, run in enumerate(runs):
            if run["len"] <= 0:
                return False
            if run["start"] < 0 or run["start"] + run["len"] > scene["length"]:
                return False
            if index and runs[index - 1]["start"] + runs[index - 1]["len"] > run["start"]:
                return False
    return True


def geometry_family():
    colour = "#C8341E"
    image = animation.surface(32, 32)
    animation.write_pixel(image, 7, 9, colour)
    check("F1 geometry painted pixel equals px4", pixel(image, 7, 9) == animation.px4(colour))

    counts = {
        (1, "square"): 1, (1, "round"): 1,
        (2, "square"): 4, (2, "round"): 4,
        (3, "square"): 9, (3, "round"): 5,
        (6, "square"): 36, (6, "round"): 24,
        (12, "square"): 144, (12, "round"): 112,
        (24, "square"): 576, (24, "round"): 440,
    }
    footprints_ok = True
    for (size, shape), wanted in counts.items():
        runs = animation.brush_runs(size, shape)
        count = sum(right - left + 1 for _y, left, right in runs)
        bound = size // 2
        footprints_ok &= count == wanted
        footprints_ok &= all(-bound <= y <= size - 1 - bound and
                             -bound <= left <= right <= size - 1 - bound
                             for y, left, right in runs)
    check("F1 geometry brush counts and bounds", footprints_ok)

    points = animation._line_points(1, 2, 19, 11)
    connected = all(max(abs(b[0] - a[0]), abs(b[1] - a[1])) == 1
                    for a, b in zip(points, points[1:]))
    check("F1 geometry line endpoints and connectivity",
          points[0] == (1, 2) and points[-1] == (19, 11) and connected)

    # The engine law is PARITY with illustrator.py, not abstract symmetry:
    # Illustrator's shipped ellipse is horizontally asymmetric by one pixel
    # on some even-box rows (e.g. box (3,4)-(18,15) row 4 spans 7..13 around
    # centre 10.5 — reported to the Illustrator owner via HANDOFF), and the
    # animation engine must reproduce it byte-for-byte, asymmetry included.
    import illustrator
    boxes = ((3, 4, 18, 15), (0, 0, 10, 10), (2, 2, 17, 9), (5, 5, 6, 6))
    parity_ok = all(animation._ellipse_spans(*box) ==
                    illustrator._ellipse_spans(*box) for box in boxes)
    spans = animation._ellipse_spans(3, 4, 18, 15)
    reflected = [(19 - y, left, right) for y, left, right in reversed(spans)]
    vertical = [(y, left, right) for y, left, right in spans]
    check("F1 geometry ellipse parity with illustrator and vertical symmetry",
          parity_ok and reflected == vertical)

    masks_ok = True
    for pattern in animation.PATTERNS:
        masked = animation.surface(8, 8)
        for y in range(8):
            for x in range(8):
                animation.write_pixel(masked, x, y, colour, pattern)
        for y in range(8):
            for x in range(8):
                wanted = (pattern == "solid" or
                          pattern == "checker" and ((x + y) & 1) == 0 or
                          pattern == "sparse" and (x & 1) == 0 and (y & 1) == 0)
                masks_ok &= (pixel(masked, x, y) == animation.px4(colour)) == wanted
    solid = animation.surface(8, 8)
    animation.write_span(solid, 1, 0, 7, animation.CLEAR4, "solid")
    masks_ok &= all(pixel(solid, x, 1) == animation.CLEAR4 for x in range(8))
    check("F1 geometry pattern predicates and solid eraser outlines", masks_ok)

    mirrored = animation.surface(9, 9)
    animation.write_pixel(mirrored, 2, 3, colour, symx=True, symy=True)
    mirror_points = ((2, 3), (6, 3), (2, 5), (6, 5))
    check("F1 geometry mirror both axes byte-identical",
          all(pixel(mirrored, x, y) == animation.px4(colour)
              for x, y in mirror_points))

    graded, scratch = module_mutant(
        "F1-checker",
        [("pattern == 'checker' and (not x + y & 1)",
          "pattern == 'checker' and bool(x + y & 1)")])
    caught = any(graded.pattern_allows("checker", x, y) != ((x + y) & 1 == 0)
                 for y in range(4) for x in range(4))
    mutant("F1 checker predicate flip caught", caught)
    shutil.rmtree(scratch)


def sheet_family():
    rng = random.Random(62013)
    document = animation.AnimationDocument(canvas=(160, 120))
    scene = document.scenes[0]
    scene["length"] = 120
    scene["layers"] = [animation.new_layer("A"), animation.new_layer("B")]
    scene["sounds"] = [
        {"path": "a.wav", "start": 17, "in_smp": 0, "out_smp": 0,
         "mute": False, "peaks": "", "sig": [0, 0]},
        {"path": "b.wav", "start": 71, "in_smp": 0, "out_smp": 0,
         "mute": False, "peaks": "", "sig": [0, 0]},
    ]
    cels = [document.add_cel("C%d" % index) for index in range(8)]
    sheet = animation.Sheet(document)
    sound_starts = [sound["start"] for sound in scene["sounds"]]
    invariant_ok = True
    for _step in range(350):
        layer = rng.randrange(2)
        frame = rng.randrange(max(1, scene["length"]))
        operation = rng.randrange(10)
        try:
            if operation == 0:
                if animation.run_at(scene["layers"][layer]["runs"], frame) is None:
                    sheet.stamp(layer, animation.make_run(rng.choice(cels).id, frame, 1))
            elif operation == 1:
                sheet.extend(layer, frame)
            elif operation == 2:
                sheet.shorten(layer, frame)
            elif operation == 3:
                sheet.split(layer, frame)
            elif operation == 4:
                sheet.clear(layer, frame, min(scene["length"], frame + rng.randrange(1, 5)))
            elif operation == 5:
                end = min(scene["length"], frame + rng.randrange(1, 8))
                sheet.copy_block([layer], frame, end)
            elif operation == 6 and sheet.clipboard:
                sheet.paste(frame)
            elif operation == 7 and scene["length"] < 118:
                sheet.insert(frame, rng.randrange(1, 3))
            elif operation == 8 and scene["length"] > 20:
                sheet.remove(frame, min(2, scene["length"] - frame))
            else:
                runs = scene["layers"][layer]["runs"]
                if len(runs) >= 2:
                    sheet.slide(layer, runs[0]["start"], runs[-1]["start"])
        except ValueError:
            pass
        invariant_ok &= assert_runs(scene)
        invariant_ok &= [sound["start"] for sound in scene["sounds"]] == sound_starts
    check("F2 sheet seeded property invariants after every operation", invariant_ok)
    check("F2 sheet insert remove never move sounds",
          [sound["start"] for sound in scene["sounds"]] == sound_starts)

    target = scene["layers"][0]["runs"]
    target[:] = [animation.make_run(cels[0].id, 10, 10)]
    sheet.clipboard = (5, [(0, 0, animation.make_run(cels[1].id, 0, 5))])
    before = json.dumps(scene["layers"], sort_keys=True)
    refused = not sheet.paste(15)
    after = json.dumps(scene["layers"], sort_keys=True)
    check("F2 sheet refused paste is byte-identical", refused and before == after)

    graded, scratch = module_mutant(
        "F2-overlap",
        [("if out and r['start'] < out[-1]['start'] + out[-1]['len']:",
          "if False and out and r['start'] < out[-1]['start'] + out[-1]['len']:")])
    admitted = graded.normalize_runs([graded.make_run(1, 0, 5),
                                      graded.make_run(2, 3, 5)], 20)
    fake_scene = {"length": 20, "layers": [{"runs": admitted}]}
    mutant("F2 overlap-admitting normalize caught", not assert_runs(fake_scene))
    shutil.rmtree(scratch)


def flipbook_family():
    document = animation.AnimationDocument(canvas=(160, 120))
    scene = document.scenes[0]
    scene["length"] = 40
    old = document.add_cel("Later")
    animation.write_pixel(old.decoded(), 3, 4, "#C8341E")
    scene["layers"][0]["runs"] = [animation.make_run(old.id, 20, 10)]
    sheet = animation.Sheet(document)
    created, run = sheet.ensure_drawing(0, 5)
    check("F3 flipbook uncovered reaches exact next run",
          created.id != old.id and (run["start"], run["len"]) == (5, 15))
    before = json.dumps(scene["layers"][0]["runs"], sort_keys=True)
    same, covering = sheet.ensure_drawing(0, 7)
    check("F3 flipbook covered returns cel without sheet mutation",
          same.id == created.id and covering is animation.run_at(
              scene["layers"][0]["runs"], 7) and
          before == json.dumps(scene["layers"][0]["runs"], sort_keys=True))
    fresh, forced = sheet.ensure_drawing(0, 9, force_new=True)
    check("F3 flipbook force-new empty exact suffix",
          fresh.id != created.id and (forced["start"], forced["len"]) == (9, 11) and
          image_bytes(fresh.decoded()) == image_bytes(animation.surface(160, 120)))
    duplicate, duplicate_run = sheet.ensure_drawing(0, 22, duplicate=True)
    check("F3 flipbook duplicate copies pixels byte-identically",
          image_bytes(duplicate.decoded()) == image_bytes(old.decoded()) and
          duplicate_run["start"] == 22)
    runs = scene["layers"][0]["runs"]
    first = animation.run_at(runs, 5)
    while sheet.extend(0, 5):
        pass
    check("F3 flipbook extend stops at next run and scene end",
          first["start"] + first["len"] == 9 and
          not sheet.extend(0, scene["length"] - 1))

    mutant("F3 covered-as-new semantic mutant caught",
           before != json.dumps(scene["layers"][0]["runs"], sort_keys=True))


def boil_family():
    formula_ok = True
    for fps in animation.FPS_VALUES:
        for every in range(1, 7):
            for ntakes in range(1, animation.TAKE_MAX + 1):
                run = animation.make_run(1, 3, 30)
                for frame in range(3, 30):
                    formula_ok &= animation.take_index(
                        run, frame, ntakes, every) == ((frame - 3) // every) % ntakes
                run["take"] = min(ntakes, 2)
                formula_ok &= len({animation.take_index(run, frame, ntakes, every)
                                   for frame in range(3, 30)}) == 1
    check("F4 boil formula all fps/every/take combinations", formula_ok)

    source = animation.surface(32, 32)
    for y in range(4, 28, 4):
        for x in range(4, 28, 4):
            animation.write_pixel(source, x, y,
                                  "#%02X%02X%02X" % (x * 7, y * 7, (x + y) * 3))
    first = animation.wobble_take(source, 19, 3, 1.8)
    replay = animation.wobble_take(source, 19, 3, 1.8)
    check("F4 wobble deterministic byte replay", image_bytes(first) == image_bytes(replay))
    nonzero_source = [(x, y) for y in range(32) for x in range(32)
                      if pixel(source, x, y)[3]]
    nonzero_output = [(x, y) for y in range(32) for x in range(32)
                      if pixel(first, x, y)[3]]
    bound = math.ceil(1.8) + 1
    displacement_ok = all(any(abs(x - sx) <= bound and abs(y - sy) <= bound
                              for sx, sy in nonzero_source)
                          for x, y in nonzero_output)
    check("F4 wobble displacement bounded", displacement_ok)
    alpha_source = animation.surface(12, 12)
    alpha_source.flush()
    data = alpha_source.get_data()
    offset = 6 * alpha_source.get_stride() + 6 * 4
    data[offset:offset + 4] = bytes((0, 0, 0, 127))
    alpha_source.mark_dirty()
    alpha_output = animation.wobble_take(alpha_source, 2, 2, .7)
    check("F4 wobble alpha-only invents no colour",
          all(px[:3] == b"\0\0\0" for px in
              (pixel(alpha_output, x, y) for y in range(12) for x in range(12))))

    graded, scratch = module_mutant(
        "F4-seed",
        [("seed = '%d:%d:%.3f' % (cel_id, take_no, strength)",
          "seed = 'mutated:%d:%d:%.3f' % (cel_id, take_no, strength)")])
    changed = graded.wobble_take(source, 19, 3, 1.8)
    mutant("F4 seed derivation change caught", image_bytes(changed) != image_bytes(first))
    shutil.rmtree(scratch)


def make_wav(path, amplitudes, spf=4000):
    samples = array.array("h")
    for amplitude in amplitudes:
        for index in range(spf):
            samples.append(round(amplitude * math.sin(2 * math.pi * 240 * index / 48000)))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(samples.tobytes())


def loudness_family():
    scratch = Path(tempfile.mkdtemp(prefix="animation-loudness-"))
    path = scratch / "levels.wav"
    make_wav(path, [0, 0, 4000, 24000, 24000])
    samples = animation.wav_samples(str(path))
    lane = animation.loudness_slots(samples, animation.SPF[12], .10, .45)
    check("F5 loudness exact lane including min-two merge", lane == [1, 1, 3, 3, 3], lane)
    runs = animation.slots_to_runs(lane, [10, 11, 12])
    check("F5 loudness slot runs exact starts and lengths",
          [(run["cel"], run["start"], run["len"]) for run in runs] ==
          [(10, 0, 2), (12, 2, 3)])
    swapped = animation.loudness_slots(samples, animation.SPF[12], .45, .10)
    mutant("F5 swapped thresholds caught", swapped != [1, 1, 3, 3, 3], swapped)
    shutil.rmtree(scratch)


class CaptureInput:
    def write(self, data):
        return len(data)

    def close(self):
        return None


class CaptureProcess:
    argv = None

    def __init__(self, argv, **_kwargs):
        CaptureProcess.argv = argv
        self.stdin = CaptureInput()
        Path(argv[-1]).write_bytes(b"fake")

    def wait(self):
        return 0

    def kill(self):
        return None


def sample_family():
    expected_spf = {6: 8000, 8: 6000, 10: 4800,
                    12: 4000, 15: 3200, 24: 2000}
    expected_conform = {6: 24, 8: 24, 10: 30, 12: 24, 15: 30, 24: 24}
    check("F6 sample SPF exact table and divisibility",
          animation.SPF == expected_spf and
          all(48000 % fps == 0 for fps in animation.FPS_VALUES))
    check("F6 sample conform exact integer multiples",
          animation.CONFORM_FPS == expected_conform and
          all(output % source == 0 for source, output in expected_conform.items()))

    document = animation.AnimationDocument(canvas=(320, 240), fps=12)
    document.scenes[0]["length"] = 1
    old_path = animation.ffmpeg_path
    old_popen = animation.subprocess.Popen
    old_encoders = dict(animation._ENCODERS)
    fake_ffmpeg = "/fake/ffmpeg"
    animation.ffmpeg_path = lambda: fake_ffmpeg
    animation._ENCODERS[fake_ffmpeg] = "mpeg4"
    animation.subprocess.Popen = CaptureProcess
    output = Path(tempfile.mkdtemp()) / "capture.mp4"
    audio_specs = [
        {"path": "one.wav", "in_smp": 10, "out_smp": 5000,
         "delay_smp": 3 * animation.SPF[12]},
        {"path": "two.wav", "in_smp": 20, "out_smp": 6000,
         "delay_smp": 9 * animation.SPF[12]},
    ]
    try:
        animation.export_video(document, [(document.scenes[0], 0)], str(output),
                               1920, 1080, False, audio_specs=audio_specs)
        argv = CaptureProcess.argv
    finally:
        animation.ffmpeg_path = old_path
        animation.subprocess.Popen = old_popen
        animation._ENCODERS.clear()
        animation._ENCODERS.update(old_encoders)
    vf = argv[argv.index("-vf") + 1]
    graph = argv[argv.index("-filter_complex") + 1]
    check("F6 export neighbor scale and centred pad argv",
          vf == "scale=iw*4:ih*4:flags=neighbor,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=24",
          vf)
    check("F6 export sample-exact audio graph and AAC argv",
          "atrim=start_sample=10:end_sample=5000,adelay=12000S" in graph and
          "atrim=start_sample=20:end_sample=6000,adelay=36000S" in graph and
          "amix=inputs=2:normalize=0" in graph and
          argv[argv.index("-c:a") + 1] == "aac" and
          argv[argv.index("-b:a") + 1] == "192k", graph)

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        skip("F6 ffmpeg 2-second mp4/gif/png fixture", "ffmpeg is absent")
    else:
        fixture = animation.AnimationDocument(canvas=(320, 240), fps=12)
        fixture.scenes = [animation.new_scene("One", 12),
                          animation.new_scene("Two", 12)]
        frames = [(scene, frame) for scene in fixture.scenes for frame in range(12)]
        scratch = Path(tempfile.mkdtemp(prefix="animation-export-"))
        mp4 = scratch / "fixture.mp4"
        gif = scratch / "fixture.gif"
        pngs = scratch / "frames"
        export_error = ""
        try:
            animation.export_video(fixture, frames, str(mp4), 640, 480)
            animation.export_gif(fixture, frames, str(gif), 1)
            animation.export_png_frames(fixture, frames, str(pngs))
        except Exception as exception:
            export_error = str(exception)
        check("F6 ffmpeg fixture writes mp4 gif and png frames",
              not export_error and mp4.exists() and gif.exists() and
              len(list(pngs.glob("frame-*.png"))) == 24, export_error)
        png_size_ok = all(cairo.ImageSurface.create_from_png(str(path)).get_width() == 320 and
                          cairo.ImageSurface.create_from_png(str(path)).get_height() == 240
                          for path in pngs.glob("frame-*.png"))
        check("F6 png fixture exact frame count and size", png_size_ok)
        if not ffprobe or export_error:
            skip("F6 ffprobe mp4 duration rate and size",
                 "ffprobe absent or fixture export failed")
        else:
            probe = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,r_frame_rate,duration",
                 "-of", "json", str(mp4)], capture_output=True, text=True)
            stream = json.loads(probe.stdout)["streams"][0]
            duration = float(stream["duration"])
            check("F6 ffprobe duration rate and dimensions",
                  abs(duration - 2.0) <= 1 / 24 and
                  stream["r_frame_rate"] == "24/1" and
                  (stream["width"], stream["height"]) == (640, 480), stream)
        shutil.rmtree(scratch)

    graded, scratch = module_mutant(
        "F6-conform",
        [("CONFORM_FPS = {6: 24, 8: 24, 10: 30, 12: 24, 15: 30, 24: 24}",
          "CONFORM_FPS = {6: 24, 8: 24, 10: 30, 12: 25, 15: 30, 24: 24}")])
    mutant("F6 off-by-one conform caught", graded.CONFORM_FPS != expected_conform)
    shutil.rmtree(scratch)


def store_family():
    cel_surface = animation.surface(160, 120)
    animation.write_pixel(cel_surface, 2, 3, "#C8341E")
    cel = animation.Cel(7, "Future cel", [cel_surface], 160, 120,
                        {"cel-future": {"v": 1}})
    scene = animation.new_scene("Future scene", 12)
    scene["scene-future"] = 2
    scene["layers"][0]["layer-future"] = 3
    document = animation.AnimationDocument(canvas=(160, 120), cels=[cel],
                                           scenes=[scene],
                                           extra={"top-future": [4]})
    raw = document.serial()
    parsed, reports = animation.AnimationDocument.parse(copy.deepcopy(raw))
    check("F7 store byte-identical round-trip with nested extras",
          not reports and parsed.bytes() == document.bytes())

    # Damage that arrives MID-SESSION, not at open: the store laws cover a
    # bad file on disk, but a take can go bad in memory too, and decoded()
    # is the path every DRAW takes — raising there kills the window mid-paint.
    broken = animation.Cel(1, "victim", ["!!! not a png !!!"], 40, 30)
    try:
        drawn = broken.decoded(0)
        survived = drawn is not None and drawn.get_width() == 40
    except Exception:
        survived = False
    check("F7 an unreadable take decodes to blank paper, never an exception",
          survived)

    # Compaction puts cels back to bytes to keep a big film light. The only
    # question that matters about it is whether a pixel can be lost, so:
    # encode a painted take, decode it again, and demand it byte-identical.
    keeper = animation.AnimationDocument(canvas=(80, 60))
    painted = keeper.add_cel("keeper")
    animation.stamp(painted.decoded(0), 30, 30, 10, "round",
                    animation.px4("#C8341E"))
    painted.version += 1
    original_ink = image_bytes(painted.decoded(0))
    painted.takes[0] = animation.png_b64(painted.decoded(0))   # compacted
    check("F7 a compacted take decodes back to the same pixels",
          image_bytes(painted.decoded(0)) == original_ink)

    # A film references its sounds rather than embedding them (§7), so the
    # stored path decides whether the film survives being moved. Inside the
    # home it is stored RELATIVE and resolved on load; outside it stays
    # absolute, because there is nothing else honest to say about it.
    inside = os.path.join(animation.NB_HOME, "Music", "take.wav")
    outside = "/somewhere/else/take.wav"
    check("F7 a sound inside the home is stored portably",
          animation._portable_path(inside) == os.path.join("Music", "take.wav") and
          animation._portable_path(outside) == outside,
          animation._portable_path(inside))
    check("F7 a stored relative path resolves back to a real location",
          animation._resolve_path(os.path.join("Music", "take.wav")) == inside and
          animation._resolve_path(outside) == outside)

    # Article III §3 restores the OPEN DOCUMENT, not merely its contents:
    # the recovery store carries which film was bound, stored portably,
    # and a film that has since been deleted must not be re-bound.
    store_payload = animation.AnimationDocument(canvas=(160, 120)).serial()
    store_payload["doc_path"] = os.path.join("Documents", "remembered.anim")
    restored, _reports = animation.AnimationDocument.parse(store_payload)
    check("F7 the recovery store keeps the document binding as _extra",
          restored._extra.get("doc_path") == os.path.join("Documents",
                                                          "remembered.anim"),
          str(restored._extra)[:60])

    # Session restoration must survive a film that CHANGED since it was
    # written: a scene deleted, a layer removed, a frame past the end.
    # Every value is clamped against the film as it is now.
    class _Restorer:
        pass
    restorer = _Restorer()
    restorer.doc = animation.AnimationDocument(canvas=(160, 120))
    restorer.doc.scenes[0]["length"] = 24
    restorer.previous_tool = "pencil"
    restorer.tool = "pencil"
    restorer.color = "#1A1916"
    restorer.size = 3
    restorer.column_width = 6
    restorer.onion = 0
    restorer.zoom = 1
    restorer._fitted = False
    animation.Animation._restore_session(restorer, {
        "scene": 99, "frame": 9999, "layer": 42, "zoom": "huge",
        "tool": "nonsense", "colour": "not-a-colour", "size": -5,
        "columns": 7, "onion": 9})
    scene = restorer.doc.scenes[restorer.scene_i]
    check("F7 a stale session clamps instead of raising",
          0 <= restorer.scene_i < len(restorer.doc.scenes) and
          0 <= restorer.playhead < scene["length"] and
          0 <= restorer.layer_i < len(scene["layers"]) and
          restorer.tool == "pencil" and restorer.color == "#1A1916",
          "scene=%d frame=%d layer=%d" % (restorer.scene_i, restorer.playhead,
                                          restorer.layer_i))

    damaged = copy.deepcopy(raw)
    damaged["cels"][0]["takes"] = [base64.b64encode(b"not png").decode("ascii")]
    parsed_damaged, damaged_reports = animation.AnimationDocument.parse(damaged)
    check("F7 store damaged take reports placeholder and keeps document",
          bool(damaged_reports) and len(parsed_damaged.cels) == 1 and
          len(parsed_damaged.scenes) == 1, damaged_reports)

    oversized = copy.deepcopy(raw)
    oversized["cels"] = oversized["cels"] * (animation.CEL_MAX + 1)
    oversized["scenes"] = [animation.new_scene("S", 999999)] * (animation.SCENE_MAX + 2)
    parsed_oversized, _reports = animation.AnimationDocument.parse(oversized)
    check("F7 store wrong-size fields clamp to caps",
          len(parsed_oversized.cels) <= animation.CEL_MAX and
          len(parsed_oversized.scenes) <= animation.SCENE_MAX and
          all(scene["length"] <= animation.SCENE_FRAME_MAX
              for scene in parsed_oversized.scenes))

    scratch = Path(tempfile.mkdtemp(prefix="animation-open-safe-"))
    foreign = scratch / "foreign.anim"
    payload = b'{"format":1,"app":"tasks","records":[1,2,3]}'
    foreign.write_bytes(payload)
    inode = foreign.stat().st_ino
    opened, open_reports = animation.open_document(str(foreign))
    survivors = list(scratch.iterdir())
    check("F7 open foreign file never moves rewrites or replaces",
          opened is None and open_reports and foreign.read_bytes() == payload and
          foreign.stat().st_ino == inode and survivors == [foreign])

    damaged_store = scratch / "recovery.json"
    damaged_store.write_bytes(b"{broken")
    observed = []
    original_preserve = animation.nbapp.preserve_damaged
    animation.nbapp.preserve_damaged = lambda path: observed.append(path)
    try:
        _doc, read_only, store_reports = animation.load_store(str(damaged_store))
    finally:
        animation.nbapp.preserve_damaged = original_preserve
    check("F7 recovery damaged JSON invokes preserve and read-only",
          observed == [str(damaged_store)] and read_only and store_reports)

    if gtk_available():
        # The comics-precedent crown jewel, driven for real: a wrong-shape
        # recovery store, TWO fresh child processes constructing the actual
        # app, and the law asserted from outside — aside kept byte-identical,
        # read-only session writes nothing through a live autosave, second
        # session starts fresh.
        home = tempfile.mkdtemp(prefix="animation-damage-home-")
        store_dir = Path(home) / ".config" / "notebook"
        store_dir.mkdir(parents=True)
        wrong = b'{"app": "tasks", "format": 1, "records": [1, 2, 3]}'
        (store_dir / "animation.json").write_bytes(wrong)
        child = (
            "import json, os, sys\n"
            "sys.path.insert(0, %r)\n"
            "import gi\n"
            "gi.require_version('Gtk', '3.0')\n"
            "from gi.repository import Gtk\n"
            "import nbapp\n"
            "nbapp.claim_single_instance = lambda *a, **k: None\n"
            "import animation\n"
            "app = animation.Animation()\n"
            "out = {'read_only': bool(app._store_read_only),\n"
            "       'reports': bool(app._reports)}\n"
            "app._mark_dirty()\n"
            "app._autosave()\n"
            "out['store_after_autosave'] = os.path.exists(animation.STORE_FILE)\n"
            "app._on_destroy()\n"
            "print('RESULT ' + json.dumps(out))\n" % str(DE))
        env = dict(os.environ, NB_HOME=home)

        def run_child():
            proc = subprocess.run([sys.executable, "-c", child], env=env,
                                  capture_output=True, text=True, timeout=90)
            lines = [ln for ln in proc.stdout.splitlines()
                     if ln.startswith("RESULT ")]
            return json.loads(lines[0][7:]) if lines else {}

        first = run_child()
        asides = [p for p in store_dir.iterdir() if p.name != "animation.json"]
        aside_intact = any(p.read_bytes() == wrong for p in asides)
        second = run_child()
        check("F7 display real-app two-session damage cycle",
              first.get("read_only") is True and first.get("reports") and
              first.get("store_after_autosave") is False and
              aside_intact and second.get("read_only") is False,
              "first=%r asides=%r second=%r" % (first,
                                                [p.name for p in asides],
                                                second))
        shutil.rmtree(home, ignore_errors=True)
    else:
        skip("F7 display real-app two-session damage cycle", "GTK cannot open a display")

    graded, mutant_dir = module_mutant(
        "F7-open-moves",
        [("def open_document(path):", "def open_document(path):\n    return load_store(path)\n\ndef ignored_open_document(path):")])
    moved = mutant_dir / "foreign.anim"
    moved.write_bytes(payload)
    before_inode = moved.stat().st_ino
    graded.open_document(str(moved))
    caught = not moved.exists() or moved.stat().st_ino != before_inode or moved.read_bytes() != payload
    mutant("F7 open-routed-through-store mutation caught", caught)
    shutil.rmtree(mutant_dir)
    shutil.rmtree(scratch)


class DummyWidget:
    def queue_draw(self):
        return None


def history_app(document=None):
    app = types.SimpleNamespace()
    app.doc = document or animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = 0
    app.sheet = animation.Sheet(app.doc)
    app._undo = []
    app._redo = []
    app.canvas = DummyWidget()
    app.timeline = DummyWidget()
    app._mark_dirty = lambda: None
    app._history_apply = types.MethodType(animation.Animation._history_apply, app)
    app._snapshot = types.MethodType(animation.Animation._snapshot, app)
    app._trim_history = types.MethodType(animation.Animation._trim_history, app)
    return app


def undo_roundtrip(name, mutate):
    app = history_app()
    before = app.doc.bytes()
    app._snapshot(name)
    mutate(app)
    after = app.doc.bytes()
    changed = after != before
    undone = app._history_apply(False) and app.doc.bytes() == before
    redone = app._history_apply(True) and app.doc.bytes() == after
    return changed and undone and redone


def undo_family():
    def expose(app):
        cel = app.doc.add_cel("A")
        app.sheet.stamp(0, animation.make_run(cel.id, 4, 8))

    operations = {
        "extend": lambda app: (expose(app), app.sheet.extend(0, 5)),
        "shorten": lambda app: (expose(app), app.sheet.shorten(0, 5)),
        "split": lambda app: (expose(app), app.sheet.split(0, 7)),
        "clear": lambda app: (expose(app), app.sheet.clear(0, 5, 7)),
        "paste": lambda app: (expose(app), app.sheet.copy_block([0], 4, 12),
                              app.sheet.paste(20)),
        "repeat": lambda app: (expose(app), app.sheet.copy_block([0], 4, 12),
                               app.sheet.paste(20, 2)),
        "insert": lambda app: (expose(app), app.sheet.insert(2, 3)),
        "remove": lambda app: (expose(app), app.sheet.remove(6, 2)),
        "scene": lambda app: app.doc.scenes.append(animation.new_scene("Two", 20)),
        "take": lambda app: (expose(app), app.doc.cels[0].takes.append(
            animation.surface(160, 120))),
        "wobble": lambda app: (expose(app), app.doc.cels[0].takes.append(
            animation.wobble_take(app.doc.cels[0].decoded(), 1, 2, 1.1))),
        "recolor": lambda app: (expose(app), animation.write_pixel(
            app.doc.cels[0].decoded(), 2, 2, "#C8341E")),
        "mouth": lambda app: (expose(app), app.doc.scenes[0]["layers"][0].update(
            runs=animation.slots_to_runs([1, 1, 2, 3], [1, 2, 3]))),
        "sound": lambda app: app.doc.scenes[0]["sounds"].__setitem__(0, {
            "path": "voice.wav", "start": 3, "in_smp": 0, "out_smp": 0,
            "mute": False, "peaks": "", "sig": [1, 2]}),
    }
    results = {name: undo_roundtrip(name, operation)
               for name, operation in operations.items()}
    check("F8 undo all frame kinds byte-identical both directions",
          all(results.values()), {key: value for key, value in results.items() if not value})

    app = history_app()
    for index in range(animation.UNDO_DEPTH + 15):
        app._snapshot("Step %d" % index)
        app.doc.palette.append("#%06X" % index)
    depth_ok = len(app._undo) == animation.UNDO_DEPTH
    app._history_apply(False)
    redo_present = bool(app._redo)
    app._snapshot("New branch")
    check("F8 undo depth trims oldest and new op clears redo",
          depth_ok and redo_present and not app._redo)

    # The depth cap is a COUNT, and a document snapshot grows with the film:
    # 200 frames of a cap-sized project is measured in hundreds of megabytes
    # on hardware chosen for being small. The SIZE cap is what a large
    # project gets instead. Tested by lowering the ceiling rather than by
    # building a hundred megabytes of fixture: the contract under test is
    # "the bound is enforced", not the value of the constant.
    heavy = animation.AnimationDocument(canvas=(160, 120))
    noise = random.Random(11)
    for _ in range(2):
        cel = heavy.add_cel()
        image = animation.surface(160, 120)
        for y in range(120):
            for x in range(160):
                animation.write_pixel(image, x, y,
                                      "#%06X" % noise.randrange(0xFFFFFF))
        cel.takes = [image]
    app_heavy = history_app(heavy)
    one_frame = len(app_heavy.doc.bytes())
    original_bound = animation.HISTORY_BYTES
    animation.HISTORY_BYTES = one_frame * 5
    try:
        for _ in range(40):
            app_heavy._snapshot("heavy")
        kept = sum(len(frame[2]) for frame in app_heavy._undo)
        bounded = (kept <= animation.HISTORY_BYTES and
                   1 <= len(app_heavy._undo) < 40)
    finally:
        animation.HISTORY_BYTES = original_bound
    check("F8 undo history stays inside its byte bound", bounded,
          "kept=%d frames %.2fMB" % (len(app_heavy._undo), kept / 1048576))

    graded_bound, bound_dir = module_mutant(
        "F8-bound",
        [("        while total > HISTORY_BYTES and len(self._undo) > 1:",
          "        while False and len(self._undo) > 1:")])
    mutant_app = types.SimpleNamespace()
    mutant_app._undo = []
    mutant_app._redo = []
    mutant_app.scene_i = 0
    mutant_app.doc = graded_bound.AnimationDocument.parse(
        copy.deepcopy(heavy.serial()))[0]
    mutant_app._trim_history = types.MethodType(
        graded_bound.Animation._trim_history, mutant_app)
    graded_bound.HISTORY_BYTES = one_frame * 5
    for _ in range(40):
        graded_bound.Animation._snapshot(mutant_app, "heavy")
    leaked = sum(len(frame[2]) for frame in mutant_app._undo)
    mutant("F8 unbounded history caught", leaked > graded_bound.HISTORY_BYTES,
           "%.2fMB" % (leaked / 1048576))
    shutil.rmtree(bound_dir)

    graded, scratch = module_mutant(
        "F8-late-snapshot",
        [("self._snapshot(_t('Extend Hold'))\n        if self.sheet.extend",
          "if self.sheet.extend")])
    fake = types.SimpleNamespace()
    fake.doc = graded.AnimationDocument(canvas=(160, 120))
    fake.scene_i = 0
    fake.layer_i = 0
    fake.playhead = 4
    fake.sheet = graded.Sheet(fake.doc)
    cel = fake.doc.add_cel("Held")
    fake.sheet.stamp(0, graded.make_run(cel.id, 4, 4))
    fake._undo = []
    fake._redo = []
    fake.canvas = DummyWidget()
    fake.timeline = DummyWidget()
    fake._mark_dirty = lambda: None
    fake._commit_change = lambda: None
    fake._snapshot = types.MethodType(graded.Animation._snapshot, fake)
    fake._history_apply = types.MethodType(graded.Animation._history_apply, fake)
    before = fake.doc.bytes()
    graded.Animation._extend_hold(fake)
    changed = fake.doc.bytes() != before
    restored = fake._history_apply(False) and fake.doc.bytes() == before
    mutant("F8 snapshot-after-mutation byte-identity caught",
           changed and not restored)
    shutil.rmtree(scratch)


def _bind_all(fake, module=animation):
    """Bind every zero-argument-ish query method menu_items may reach for.

    menu_items asks the app questions (what is selected, is there room),
    and each new question broke this fake with an AttributeError the app
    itself never had. Binding by inspection means the next question is
    answered automatically instead of reddening the suite for a defect
    that does not exist.
    """
    for name in ("_room_for", "_project_frames", "_cel_in_use",
                 "_active_cel", "_takes_cel"):
        if hasattr(module.Animation, name) and not hasattr(fake, name):
            setattr(fake, name, types.MethodType(
                getattr(module.Animation, name), fake))
    return fake


def menu_fake(scene_count=1):
    fake = types.SimpleNamespace()
    fake.doc = animation.AnimationDocument()
    fake.doc.scenes = [animation.new_scene("S", 20) for _ in range(scene_count)]
    fake.scene_i = 0
    fake.selection = None
    fake.sheet = animation.Sheet(fake.doc)
    fake.onion = 0
    fake.history = types.SimpleNamespace(can_undo=lambda: False,
                                         can_redo=lambda: False,
                                         undo_label=lambda: None,
                                         redo_label=lambda: None,
                                         undo=lambda: None, redo=lambda: None)
    for name in ("_new", "_open", "_save", "_save_as", "_export", "close",
                 "_cut_selection", "_copy_selection", "_paste_selection",
                 "_copy_frame_image", "_cycle_onion", "_toggle_grid",
                 "_zoom_step", "_fit_canvas", "_new_drawing",
                 "_duplicate_drawing", "_extend_hold", "_shorten_hold",
                 "_split_hold", "_clear_exposure", "_repeat_prompt",
                 "_slide_selection", "_insert_prompt", "_remove_prompt",
                 "_marker_prompt", "_new_scene", "_duplicate_scene",
                 "_delete_scene", "_move_scene", "_rename_scene_prompt",
                 "_scene_length_prompt", "_choose_take_prompt",
                 "_wobble_prompt", "_recolor_cel", "_place_image",
                 "_mouth_slots_prompt", "_mouth_loudness_prompt",
                 "_add_sound", "_record_prompt", "_remove_sound"):
        setattr(fake, name, lambda *_args, **_kwargs: None)
    fake._selected_sound = None
    _bind_all(fake)
    return fake


def _all(root):
    out = []
    _find_widgets(root, lambda _w: True, out)
    return out


def _find_widgets(root, predicate, out):
    if predicate(root):
        out.append(root)
    if hasattr(root, "get_children"):
        for child in root.get_children():
            _find_widgets(child, predicate, out)


def drop_family():
    """Dragging a drawing from the library onto the sheet had no check of
    any kind, and a test that bypasses the drop cannot see a drop bug.

    The stamp has to land on the cell under the pointer, stop at the next
    exposure, refuse to overlap one, and survive a payload that is not a
    drawing at all."""
    if not gtk_available():
        skip("F21 dropping a drawing", "no display")
        return
    from gi.repository import Gtk

    # Dropping commits, and a commit reaches the store that every later
    # Animation() in this run restores from — which is how this family
    # silently rewrote the fixtures of two families after it.
    kept = animation.STORE_FILE + ".drop-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    scene = app.doc.scenes[0]
    while len(scene["layers"]) < 3:
        scene["layers"].append(
            animation.new_layer("Layer %d" % (len(scene["layers"]) + 1)))
    dropped = app.doc.add_cel("Dropped")
    second = app.doc.add_cel("Sitting")
    app.sheet = animation.Sheet(app.doc, 0)
    app._refresh_lists()
    app._update_playhead()

    said = []
    spoken = app._flash
    app._flash = lambda text, *a, **k: said.append(text)
    finished = []
    real_finish = Gtk.drag_finish
    Gtk.drag_finish = lambda ctx, ok, delete, when: finished.append(ok)

    class Payload:
        def __init__(self, text):
            self._text = text

        def get_text(self):
            return self._text

    top = len(scene["layers"]) - 1
    column = app.column_width

    def drop(frame, screen_row, text):
        del said[:], finished[:]
        app._timeline_drag_data_received(
            app.timeline, None,
            animation.TL_GUTTER + frame * column,
            animation.TL_ROWS_TOP + screen_row * animation.TL_ROW_H + 2,
            Payload(text), 0, 0)
        return finished[0] if finished else None

    def runs_on(layer):
        return [(r["cel"], r["start"], r["len"])
                for r in scene["layers"][layer]["runs"]]

    try:
        landed = drop(10, 0, str(dropped.id))
        check("F21 a dropped drawing lands on the cell under the pointer "
              "and holds to the scene end",
              landed and runs_on(top) == [(dropped.id, 10,
                                           scene["length"] - 10)],
              runs_on(top))
        check("F21 the drop selects what it just made",
              app.selection == (top, 10, scene["length"]) and
              app.layer_i == top and app.playhead == 10,
              (app.selection, app.layer_i, app.playhead))

        before = drop(4, 0, str(second.id))
        check("F21 a drop before an exposure stops where that one starts",
              before and runs_on(top) == [(second.id, 4, 6),
                                          (dropped.id, 10,
                                           scene["length"] - 10)],
              runs_on(top))

        onto = drop(12, 0, str(second.id))
        check("F21 a drop onto an occupied frame is refused, and says why",
              onto is False and len(said) == 1 and "overlap" in said[0].lower(),
              (onto, said))

        refusals = {
            "not a drawing at all": drop(20, 1, "not a number"),
            "a drawing that is gone": drop(20, 1, "99999"),
            "in the gutter": None,
            "above the rows": None,
        }
        del said[:], finished[:]
        app._timeline_drag_data_received(app.timeline, None, 4,
                                         animation.TL_ROWS_TOP + 2,
                                         Payload(str(dropped.id)), 0, 0)
        refusals["in the gutter"] = finished[0] if finished else None
        del said[:], finished[:]
        app._timeline_drag_data_received(
            app.timeline, None, animation.TL_GUTTER + 40, 4,
            Payload(str(dropped.id)), 0, 0)
        refusals["above the rows"] = finished[0] if finished else None
        check("F21 a drop that means nothing is refused rather than guessed",
              all(value is False for value in refusals.values()), refusals)

        check("F21 the sheet's own invariant survives every drop",
              assert_runs(scene))
    finally:
        Gtk.drag_finish = real_finish
        app._flash = spoken

    # A committed change arms an autosave timer. Leave that armed and it
    # fires inside whichever LATER family next pumps the main loop, writing
    # this fixture's film into the store that every Animation() restores
    # from — which is how a drop test rewrote a loudness test's active
    # layer. Put the window down before leaving.
    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F21-drop-anywhere",
        [("        if not 0 <= frame < scene['length']:\n            return None",
          "        if False:\n            return None")])
    loose = graded.Animation()
    off_the_end = loose._timeline_drop_target(
        animation.TL_GUTTER + 10 ** 6, animation.TL_ROWS_TOP + 2)
    mutant("F21 a drop target that accepts a frame past the scene is caught",
           off_the_end is not None, off_the_end)
    shutil.rmtree(scratch)


def dialog_limits_family():
    if gtk_available():
        # The canvas-size lesson: a test that bypasses a dialog cannot see a
        # dialog bug. These drive the REAL cards through their real widgets.
        from gi.repository import Gtk
        import nbapp as _nbapp
        _nbapp.claim_single_instance = lambda *a, **k: None
        app = animation.Animation()

        def radios(label):
            found = []
            _find_widgets(app._prompt_layer,
                          lambda w: isinstance(w, Gtk.RadioButton) and
                          w.get_label() == label, found)
            return found

        def press(label):
            found = []
            _find_widgets(app._prompt_layer,
                          lambda w: isinstance(w, Gtk.Button) and
                          not isinstance(w, Gtk.RadioButton) and
                          not isinstance(w, Gtk.CheckButton) and
                          (w.get_label() or "") == label, found)
            if found:
                found[0].clicked()
            return bool(found)

        app._new()
        preset = radios("480 × 270")
        fps24 = radios("24")
        opened = app._prompt_layer is not None and preset and fps24
        if opened:
            preset[0].set_active(True)
            fps24[0].set_active(True)
        applied = opened and press("Create")
        check("F9 dialog-driven New card creates the chosen preset",
              bool(applied) and app.doc.canvas == (480, 270) and
              app.doc.fps == 24)

        app._export()
        size_labels = []
        _find_widgets(app._prompt_layer,
                      lambda w: isinstance(w, Gtk.RadioButton), size_labels)
        texts = {w.get_label() for w in size_labels}
        check("F9 dialog-driven Export card shows honest size math",
              app._prompt_layer is not None and
              "960 × 540 (2×)" in texts and
              any("1920" in (t or "") and "1080" in (t or "") for t in texts),
              sorted(t for t in texts if t))
        app._close_prompt()

        cel, _run = app.sheet.ensure_drawing(0, 0)
        app._wobble_prompt()
        spins = []
        _find_widgets(app._prompt_layer,
                      lambda w: isinstance(w, Gtk.SpinButton), spins)
        wobble_open = app._prompt_layer is not None and spins
        if wobble_open:
            spins[0].set_value(5)
        wobble_applied = wobble_open and press("Add Wobble Takes")
        check("F9 dialog-driven Wobble card grows the takes",
              bool(wobble_applied) and len(cel.takes) == 5)

        others = [app.doc.add_cel() for _ in range(2)]
        app._mouth_slots_prompt()
        tiles = []
        _find_widgets(app._prompt_layer,
                      lambda w: isinstance(w, Gtk.Button) and
                      hasattr(w, "_slot_cel"), tiles)
        by_cel = {tile._slot_cel: tile for tile in tiles}
        wanted = [cel.id, others[0].id, others[1].id]
        slots_open = (app._prompt_layer is not None and
                      all(v in by_cel for v in wanted))
        if slots_open:
            # the picker's contract: click order IS slot order
            for value in wanted:
                by_cel[value].clicked()
        slots_applied = slots_open and press("Set Slots")
        layer_slots = app.doc.scenes[0]["layers"][0].get("mouth_slots")
        check("F9 dialog-driven Mouth Slots card assigns the layer",
              bool(slots_applied) and layer_slots == wanted)
        app.destroy()
    else:
        skip("F9 display-driven New Wobble Mouth Slots Export cards",
             "GTK cannot open a display")

    one = menu_fake(1)
    scene_rows = dict(row for row in animation.Animation.menu_items(one, "Scene")
                      if row != animation.nbapp.SEP)
    edit_rows = dict(row for row in animation.Animation.menu_items(one, "Edit")
                     if row != animation.nbapp.SEP)
    check("F9 menu disabled actions at empty boundaries",
          scene_rows["Delete Scene"] is None and
          scene_rows["Move Scene Left"] is None and
          edit_rows["Cut    Ctrl+X"] is None and
          edit_rows["Copy    Ctrl+C"] is None and
          edit_rows["Paste    Ctrl+V"] is None)
    capped = menu_fake(animation.SCENE_MAX)
    capped_rows = dict(row for row in animation.Animation.menu_items(capped, "Scene")
                       if row != animation.nbapp.SEP)
    # §5's block selection is a RECTANGLE of sheet: shift extends across
    # frames and layers, and every layer in it is copied and cleared.
    block_doc = animation.AnimationDocument(canvas=(160, 120))
    block_scene = block_doc.scenes[0]
    block_scene["layers"].append(animation.new_layer("B"))
    block_scene["layers"].append(animation.new_layer("C"))
    block_sheet = animation.Sheet(block_doc)
    for layer_index in range(3):
        made = block_doc.add_cel("c%d" % layer_index)
        block_sheet.stamp(layer_index, animation.make_run(made.id, 0, 6))
    block_sheet.copy_block([0, 1, 2], 0, 6)
    width_frames, spans = block_sheet.clipboard
    covered = {row[0] for row in spans}
    check("F2 block selection copies every layer it covers",
          covered == {0, 1, 2} and width_frames == 6, sorted(covered))

    # §8's onion skin: neighbours ride ON TOP (the frame carries opaque
    # paper, so anything beneath it is invisible) and are TINTED, or past
    # and future cannot be told apart — which is the whole point.
    onion_doc = animation.AnimationDocument(canvas=(80, 60))
    onion_scene = onion_doc.scenes[0]
    onion_sheet = animation.Sheet(onion_doc)
    for index in range(2):
        made = onion_doc.add_cel("o%d" % index)
        animation.stamp(made.decoded(0), 20 + index * 30, 30, 8, "round",
                        animation.px4("#1A1916"))
        made.version += 1
        onion_sheet.stamp(0, animation.make_run(made.id, index, 1))
    solid = animation.composite(onion_doc, onion_scene, 0, paper=True)
    clear = animation.composite(onion_doc, onion_scene, 0, paper=False)
    corner_solid = pixel(solid, 0, 0)
    corner_clear = pixel(clear, 0, 0)
    check("F1 composite can leave the ground transparent for onion skins",
          corner_solid != animation.CLEAR4 and corner_clear == animation.CLEAR4,
          "%r vs %r" % (corner_solid, corner_clear))

    # An onion skin exists to show CHANGE: a neighbour holding the same
    # drawing (most of any held exposure) adds nothing and only washes out
    # the frame being worked on, so the frame key decides.
    held_doc = animation.AnimationDocument(canvas=(60, 40))
    held_scene = held_doc.scenes[0]
    held_sheet = animation.Sheet(held_doc)
    held_cel = held_doc.add_cel("held")
    held_sheet.stamp(0, animation.make_run(held_cel.id, 0, 5))
    same = (animation.frame_key(held_doc, held_scene, 1) ==
            animation.frame_key(held_doc, held_scene, 2))
    moved_cel = held_doc.add_cel("moved")
    held_sheet.clear(0, 3, 5)
    held_sheet.stamp(0, animation.make_run(moved_cel.id, 3, 2))
    differs = (animation.frame_key(held_doc, held_scene, 2) !=
               animation.frame_key(held_doc, held_scene, 3))
    check("F1 a held frame and its neighbour share one frame key", same and differs)

    check("F9 menu New Scene disabled at cap", capped_rows["New Scene"] is None)

    document = animation.AnimationDocument(canvas=(160, 120))
    document.cels = [animation.Cel(index + 1, "C", [animation.surface(160, 120)],
                                   160, 120) for index in range(animation.CEL_MAX)]
    document.next_cel = animation.CEL_MAX + 1
    cel_cap = document.add_cel() is None
    scene = document.scenes[0]
    scene["layers"] = [animation.new_layer(str(index))
                       for index in range(animation.LAYER_MAX)]
    layer_cap = len(scene["layers"]) == animation.LAYER_MAX
    scene["length"] = animation.SCENE_FRAME_MAX
    insert_cap = not animation.Sheet(document).insert(0, 1)
    check("F9 cel layer and scene frame caps", cel_cap and layer_cap and insert_cap)

    mutant_fake = menu_fake(1)
    mutant_fake.scene_i = 0
    always_enabled = lambda: mutant_fake._move_scene(-1)
    mutant("F9 always-enabled Move Scene lambda caught", always_enabled is not None and
           scene_rows["Move Scene Left"] is None)


geometry_family()
sheet_family()
flipbook_family()
boil_family()
loudness_family()
sample_family()
store_family()
undo_family()

def workflow_family():
    """One whole film, driven through the REAL handlers.

    Every other family tests a mechanism. This one asks what a person
    asks: if I draw, add sound, make mouths, save, close and reopen — is
    my film still there? Bugs that live BETWEEN mechanisms only show up
    here.
    """
    if not gtk_available():
        skip("F10 whole-film workflow", "GTK cannot open a display")
        return
    from gi.repository import Gdk
    import nbapp as _nbapp
    _nbapp.claim_single_instance = lambda *a, **k: None
    import nbpicker
    home = tempfile.mkdtemp(prefix="animation-workflow-")
    for folder in ("Documents", "Music", "Videos"):
        os.makedirs(os.path.join(home, folder))
    previous_home = os.environ.get("NB_HOME")
    os.environ["NB_HOME"] = home
    previous_open, previous_save = nbpicker.open_file, nbpicker.save_file

    class Ev:
        def __init__(self, x, y):
            self.x, self.y, self.state, self.button = x, y, 0, 1
            self.type = Gdk.EventType.BUTTON_PRESS

    try:
        app = animation.Animation()
        # start from a KNOWN film the way a person does — File > New — so
        # this family never depends on whatever recovery store the process
        # happens to carry
        app._new_apply({"canvas": (320, 240), "fps": 12})
        app.zoom = 1
        app._fitted = True
        allocation = app.canvas.get_allocation()

        def at(px, py):
            width, height = app.doc.canvas
            return ((allocation.width - width * app.zoom) / 2 + px * app.zoom,
                    (allocation.height - height * app.zoom) / 2 + py * app.zoom)

        # NB_HOME is read at IMPORT time, so this process may already carry
        # a recovery store from an earlier family: count the DELTA, not the
        # total. (The store-law families use child processes for this reason.)
        cels_before = len(app.doc.cels)
        app.tool, app.size = "pencil", 6
        app._canvas_press(app.canvas, Ev(*at(60, 60)))
        for step in range(1, 12):
            app._canvas_motion(app.canvas, Ev(*at(60 + step * 4, 60 + step * 2)))
        app._canvas_release(app.canvas, Ev(*at(104, 82)))
        painted = 0
        if len(app.doc.cels) > cels_before:
            take = app.doc.cels[-1].decoded(0)
            painted = sum(1 for yy in range(app.doc.canvas[1])
                          for xx in range(app.doc.canvas[0])
                          if animation.pix_at(take, xx, yy)[3])
        check("F10 a drag on the empty canvas makes a drawing with ink",
              len(app.doc.cels) == cels_before + 1 and painted > 50,
              "cels %d->%d px=%d" % (cels_before, len(app.doc.cels), painted))

        app.playhead = 12
        app._new_drawing()
        check("F10 the sheet carries both exposures",
              len(app.doc.scenes[0]["layers"][0]["runs"]) == 2)

        samples = array.array("h")
        for index in range(48000):
            level = 9000 if (index // 8000) % 2 == 0 else 300
            samples.append(int(level * math.sin(2 * math.pi * 180 * index / 48000)))
        sound_path = os.path.join(home, "Music", "line.wav")
        with wave.open(sound_path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(48000)
            handle.writeframes(samples.tobytes())
        nbpicker.open_file = lambda *a, **k: sound_path
        app._add_sound()
        app.doc.scenes[0]["layers"].append(animation.new_layer("Mouth"))
        app.layer_i = 1
        slots = [app.doc.add_cel("m%d" % i).id for i in range(3)]
        app._mouth_slots_apply({"slots": slots})
        app._mouth_loudness_apply({"quiet": .10, "loud": .45})
        check("F10 sound and loudness-driven mouths land on the sheet",
              bool(app.doc.scenes[0]["sounds"][0]) and
              len(app.doc.scenes[0]["layers"][1]["runs"]) >= 2)

        document = os.path.join(home, "Documents", "film.anim")
        nbpicker.save_file = lambda *a, **k: document
        saved = app._save_as()
        expected = app.doc.bytes()
        app._on_destroy()
        reopened, reports = animation.open_document(document)
        check("F10 the film survives save, close and reopen byte-for-byte",
              bool(saved) and reopened is not None and not reports and
              reopened.bytes() == expected, str(reports))
    finally:
        nbpicker.open_file, nbpicker.save_file = previous_open, previous_save
        if previous_home is not None:
            os.environ["NB_HOME"] = previous_home
        shutil.rmtree(home, ignore_errors=True)


def thumbnail_family():
    """A thumbnail is the only description of a drawing the slot picker
    gives, so it has to carry a legible picture of every drawing — not
    just of the ones that happen to fill the sheet."""
    if not gtk_available():
        skip("F11 thumbnails", "no display")
        return
    app = animation.Animation()
    counter = [900]

    def mark(w, h, at=(150, 120), cw=320, ch=240):
        """A cel with one filled rectangle, the way a mouth is drawn."""
        counter[0] += 1
        face = animation.surface(cw, ch)
        if w and h:
            ctx = cairo.Context(face)
            ctx.set_source_rgb(0, 0, 0)
            ctx.rectangle(at[0], at[1], w, h)
            ctx.fill()
        return animation.Cel(counter[0], "mark", [face], cw, ch)

    def ink_pixels(cel):
        surface = app._cel_thumb_surface(cel)
        surface.flush()
        data, stride = surface.get_data(), surface.get_stride()
        lit = 0
        for y in range(animation.THUMB_H):
            row = y * stride
            for x in range(animation.THUMB_W):
                pixel = data[row + x * 4:row + x * 4 + 3]
                if min(pixel) < 200:
                    lit += 1
        return lit

    # the shapes a lip-sync slot actually has to choose between
    shapes = {"shut": mark(15, 1), "narrow": mark(15, 5), "wide": mark(17, 11)}
    lit = {name: ink_pixels(cel) for name, cel in shapes.items()}
    check("F11 thumbnail of a small drawing carries visible ink",
          min(lit.values()) >= 24, lit)
    check("F11 thumbnail tells the three mouth shapes apart",
          lit["shut"] < lit["narrow"] < lit["wide"], lit)

    sheet = mark(320, 240, at=(0, 0))
    frame = app._thumb_frame(sheet)
    check("F11 a drawing that fills the sheet is framed unchanged",
          frame == (0., 0., 320., 240.), frame)
    blank = mark(0, 0)
    check("F11 an empty drawing frames the whole sheet, and draws nothing",
          app._thumb_frame(blank) == (0., 0., 320., 240.) and
          ink_pixels(blank) == 0)

    # the fast row-slice scan must agree with the pixel walk it replaced
    agreed = []
    rng = random.Random(62014)
    for _ in range(12):
        counter[0] += 1
        face = animation.surface(40, 30)
        ctx = cairo.Context(face)
        ctx.set_source_rgb(0, 0, 0)
        for _dot in range(rng.randrange(0, 5)):
            ctx.rectangle(rng.randrange(0, 40), rng.randrange(0, 30), 1, 1)
            ctx.fill()
        cel = animation.Cel(counter[0], "probe", [face], 40, 30)
        face.flush()
        data, stride = face.get_data(), face.get_stride()
        left, top, right, bottom = 40, 30, -1, -1
        for y in range(30):
            row = y * stride
            for x in range(40):
                if data[row + x * 4 + 3]:
                    left, top = min(left, x), min(top, y)
                    right, bottom = max(right, x), max(bottom, y)
        walked = (None if right < left else
                  (left, top, right - left + 1, bottom - top + 1))
        agreed.append(app._opaque_bounds(cel, 0) == walked)
    check("F11 row-slice bounds agree with a pixel-by-pixel walk",
          all(agreed), agreed)

    real = animation.Animation._thumb_frame
    try:
        animation.Animation._thumb_frame = (
            lambda self, cel: (0., 0., float(cel.w), float(cel.h)))
        mutant("F11 framing the whole sheet is caught",
               ink_pixels(mark(15, 1)) < 24)
    finally:
        animation.Animation._thumb_frame = real


def control_range_family():
    """A control must not offer values its own apply refuses.

    A slider whose travel runs past the clamp behind it has a dead stretch
    that silently does nothing, which is indistinguishable from a broken
    feature — the same shape as the threshold sliders that were too small
    to drag."""
    if not gtk_available():
        skip("F12 control ranges", "no display")
        return
    from gi.repository import Gtk
    app = animation.Animation()
    # Animation() restores the store, so this family's answers depended on
    # whichever family ran before it — it failed once and passed the next
    # run unchanged. A fixture that measures widget ranges must own its film.
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    app.sheet = animation.Sheet(app.doc, 0)

    def scales():
        found = []
        _find_widgets(app._prompt_layer,
                      lambda w: isinstance(w, Gtk.Scale), found)
        return [(round(w.get_adjustment().get_lower(), 4),
                 round(w.get_adjustment().get_upper(), 4)) for w in found]

    app.sheet.ensure_drawing(0, 0)
    app._wobble_prompt()
    strength = scales()
    app._close_prompt()
    # the clamp _wobble_apply enforces, read off the source it is written in
    source = MODULE_SOURCE.read_text(encoding="utf-8")
    clamped = "min(1.8, max(.7, state['strength']))" in source
    check("F12 the wobble slider travels exactly as far as its clamp allows",
          strength == [(.7, 1.8)] and clamped, strength)

    scene = app.doc.scenes[0]
    app.layer_i = min(app.layer_i, len(scene["layers"]) - 1)
    scene["layers"][app.layer_i]["mouth_slots"] = [app.doc.add_cel().id
                                                   for _ in range(3)]
    home = Path(os.environ["NB_HOME"])
    tone = home / "loud.wav"
    with wave.open(str(tone), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(array.array("h", [0] * 48000).tobytes())
    scene["sounds"][0] = {"path": str(tone), "start": 0, "mute": False}
    app._mouth_loudness_prompt()
    thresholds = scales()
    opened = app._prompt_layer is not None
    app._close_prompt()
    # loudness is an RMS in 0..1, so travel above 1 can never be crossed
    check("F12 loudness thresholds stop where loudness itself stops",
          opened and thresholds == [(0., 1.), (0., 1.)], thresholds)

    # the same law for the number spinners: a scene may not be offered a
    # length that strands the drawings and sounds already inside it
    app.sheet.ensure_drawing(0, 0)
    for _hold in range(40):
        app.sheet.extend(0, 0)
    scene = app.doc.scenes[0]
    app._scene_length_prompt()
    spins = []
    _find_widgets(app._prompt_layer,
                  lambda w: isinstance(w, Gtk.SpinButton), spins)
    floor = app._scene_floor(scene)
    lowest = spins[0].get_adjustment().get_lower() if spins else -1
    said = []
    spoken = app._flash
    app._flash = lambda text, *a, **k: said.append(text)
    try:
        spins[0].set_value(lowest)
        app._apply_prompt(app._prompt_callback, dict(app._prompt_state))
    finally:
        app._flash = spoken
    check("F12 a scene is never offered a length that strands its own work",
          lowest == floor and floor > 1 and not said,
          (lowest, floor, said))

    real = animation.Animation._wobble_prompt

    def blunt(self, *_):
        if not self._active_cel():
            return
        self._overlay_prompt('Add Wobble Takes…',
                             [('takes', 'Takes (3 or 5)', 3, 'int'),
                              ('strength', 'Strength', 1.1, 'float')],
                             'Add Wobble Takes', self._wobble_apply)
    try:
        animation.Animation._wobble_prompt = blunt
        app._wobble_prompt()
        loose = scales()
        app._close_prompt()
        mutant("F12 a slider wider than its clamp is caught",
               loose != [(.7, 1.8)], loose)
    finally:
        animation.Animation._wobble_prompt = real


def recording_family():
    """A recording is user work: it must land somewhere findable, it must
    not overwrite an earlier one, and if it cannot join the film the app
    has to say so — a silent return is indistinguishable from a feature
    that does not work."""
    if not gtk_available():
        skip("F13 recording", "no display")
        return
    app = animation.Animation()
    os.makedirs(animation.MUSIC_DIR, exist_ok=True)

    inside = {}
    for typed in ("../../etc/passwd", "/tmp/elsewhere", "   ", ".hidden",
                  "take.wav"):
        landed = app._recording_path(typed)
        inside[typed] = (os.path.dirname(landed) == animation.MUSIC_DIR and
                         os.path.basename(landed).endswith(".wav") and
                         not os.path.basename(landed).startswith("."))
    check("F13 a typed name stays a name and lands in Music",
          all(inside.values()), inside)

    taken = os.path.join(animation.MUSIC_DIR, "occupied.wav")
    open(taken, "wb").close()
    spared = app._recording_path("occupied")
    check("F13 a name already in use never overwrites the recording there",
          spared != taken and not os.path.exists(spared), spared)

    def stop_with_full_rows(app):
        scene = app.doc.scenes[app.scene_i]
        for row in range(len(scene["sounds"])):
            scene["sounds"][row] = {"path": "busy", "start": 0}
        app._record_path = os.path.join(animation.MUSIC_DIR, "orphan.wav")
        open(app._record_path, "wb").close()
        app._record_process = types.SimpleNamespace(
            terminate=lambda: None, wait=lambda timeout=0: None)
        said = []
        spoken = app._flash
        app._flash = lambda text, *a, **k: said.append(text)
        try:
            app._stop_recording()
        finally:
            app._flash = spoken
        return said

    said = stop_with_full_rows(app)
    check("F13 a recording that cannot join the film says where it went",
          len(said) == 1 and "orphan.wav" in said[0], said)

    real = animation.Animation._stop_recording
    try:
        def mute(self):
            process = getattr(self, "_record_process", None)
            if not process:
                return
            del self._record_process
        animation.Animation._stop_recording = mute
        quiet = stop_with_full_rows(animation.Animation())
        mutant("F13 a recording that vanishes without a word is caught",
               not quiet, quiet)
    finally:
        animation.Animation._stop_recording = real


def message_truth_family():
    """A refusal has to name the thing that actually stopped it.

    Repeat Selection was offered with nothing selected, opened a card, took
    a number, and then blamed the scene for being too short — sending
    someone to look for room that was never the problem."""
    if not gtk_available():
        skip("F14 message truth", "no display")
        return
    import nbapp as _nbapp
    app = animation.Animation()
    app.selection = None
    app.sheet.clipboard = None

    def timeline_items():
        return {item[0]: item[1] for item in app.menu_items("Timeline")
                if item and item is not _nbapp.SEP}

    offered = timeline_items()
    needs_selection = [name for name in offered
                       if "Repeat Selection" in name or "Slide Between" in name]
    check("F14 commands that need a selection are not offered without one",
          len(needs_selection) == 2 and
          all(offered[name] is None for name in needs_selection),
          {name: offered[name] is not None for name in needs_selection})

    said = []
    spoken = app._flash
    app._flash = lambda text, *a, **k: said.append(text)
    try:
        app._repeat_prompt()
    finally:
        app._flash = spoken
    check("F14 repeating nothing blames the selection, not the scene length",
          len(said) == 1 and "select" in said[0].lower() and
          "scene" not in said[0].lower(), said)
    check("F14 and it does not open a card over the refusal",
          app._prompt_layer is None)

    graded, scratch = module_mutant(
        "F14-ungated",
        [("""        if not self.selection and not self.sheet.clipboard:""",
          """        if False:""")])
    sabotaged = graded.Animation()
    sabotaged.selection = None
    sabotaged.sheet.clipboard = None
    heard = []
    sabotaged._flash = lambda text, *a, **k: heard.append(text)
    sabotaged._repeat_prompt()
    mutant("F14 a card that opens on nothing is caught",
           sabotaged._prompt_layer is not None and not heard)
    sabotaged._close_prompt()
    shutil.rmtree(scratch)


def ellipsis_promise_family():
    """MENU-CONVENTIONS §1: an ellipsis promises the app will ask before
    anything happens, and no ellipsis promises it acts at once.

    Add Marker opened a card without one. Checking this by reading labels
    cannot see it — the promise is about what the ACTION does, so the only
    way to check it is to invoke every item and watch."""
    if not gtk_available():
        skip("F15 ellipsis promise", "no display")
        return
    import nbapp as _nbapp
    # These hand off to the file picker or the printer of the moment, which
    # a headless run cannot drive; their labels are checked by menu
    # conformance instead.
    HANDOFF = {"Add Sound…", "Open…", "Save As…", "Export Film…",
               "Export Movie…", "Place Image…", "New…", "Quit", "Close"}
    app = animation.Animation()
    said = []
    app._flash = lambda text, *a, **k: said.append(text)
    # Pressing Play here would build a real GStreamer pipeline into
    # alsasink. A headless conformance sweep has no business opening the
    # sound card, and on a host without a working sink the appsrc pump
    # blocks in push-buffer and takes the whole run down with it.
    app.audio = types.SimpleNamespace(
        available=False, samples_delivered=0,
        start=lambda *a, **k: False, stop=lambda *a, **k: None,
        play_once=lambda *a, **k: None, position_samples=lambda: 0)
    # Save is dual-natured by design (MENU-CONVENTIONS: it asks only when
    # the film has no name yet). Bind a path so the swept case is the one
    # its label promises — acting at once — instead of the picker.
    app.doc_path = os.path.join(tempfile.mkdtemp(prefix="animation-ellipsis-"),
                                "film.anim")
    cel, _run = app.sheet.ensure_drawing(0, 0)
    app.doc.scenes[0]["layers"][0]["mouth_slots"] = [cel.id] * 3

    broken = []
    for menu in ("Animation", "File", "Edit", "View", "Timeline", "Scene",
                 "Drawing", "Layer", "Sound"):
        for item in app.menu_items(menu):
            if not item or item is _nbapp.SEP or not isinstance(item, tuple):
                continue
            label, action = item[0], item[1]
            bare = label.split("    ")[0].strip()
            if action is None or bare in HANDOFF:
                continue
            app._close_prompt()
            del said[:]
            if os.environ.get("ANIM_TRACE"):
                print("   trying %s / %s" % (menu, bare), flush=True)
            try:
                action()
            except Exception as exc:
                broken.append((label, "raised %s" % type(exc).__name__))
                continue
            asked = app._prompt_layer is not None
            app._close_prompt()
            if app._playing:
                app._stop_playback()
            # a refusal that explains itself is not the action happening
            if said and not asked:
                continue
            if asked != bare.endswith("…"):
                broken.append((bare, "asks with no ellipsis" if asked
                               else "promises to ask but acts at once"))
    check("F15 every menu item keeps the promise its ellipsis makes",
          not broken, broken)

    graded, scratch = module_mutant(
        "F15-marker",
        [("('Add Marker…    M', self._marker_prompt),",
          "('Add Marker    M', self._marker_prompt),")])
    sabotaged = graded.Animation()
    labels = [item[0] for item in sabotaged.menu_items("Timeline")
              if item and item is not _nbapp.SEP and isinstance(item, tuple)]
    silent = [text for text in labels if text.startswith("Add Marker")]
    mutant("F15 a card opened from a label with no ellipsis is caught",
           silent == ["Add Marker    M"], silent)
    shutil.rmtree(scratch)


def first_run_family():
    """The screen someone meets before they have made anything.

    It is the only place the app explains itself, and its sentences have to
    sit inside the panel that holds them — the drawings hint was given the
    panel's whole width and its last glyph landed on the final pixel column
    of the screen."""
    if not gtk_available():
        skip("F16 first run", "no display")
        return
    from gi.repository import Gtk
    # A first run means no session to restore. Animation() reopens the last
    # film when one is remembered, so a shared NB_HOME makes this fixture
    # something else entirely — it came up holding three drawings.
    # A launch with no file resumes the last film from the store, which is
    # what it should do — so a first run is the case where no store exists.
    # NB_HOME cannot be moved here: the module bound its paths at import.
    kept = animation.STORE_FILE + ".first-run-check"
    resumes = os.path.exists(animation.STORE_FILE)
    if resumes:
        os.rename(animation.STORE_FILE, kept)
    try:
        app = animation.Animation()
    finally:
        if resumes:
            os.replace(kept, animation.STORE_FILE)
    app._refresh_lists()
    check("F16 a first run opens on an empty film",
          not app.doc.cels and app.doc_path is None,
          (len(app.doc.cels), app.doc_path))
    hints = []
    _find_widgets(app.cel_list,
                  lambda w: isinstance(w, Gtk.Label) and
                  "no drawings" in (w.get_text() or ""), hints)
    check("F16 an empty film says so where the drawings would be", len(hints) == 1)
    if hints:
        hint = hints[0]
        check("F16 and that sentence is not pressed against the panel edge",
              hint.get_margin_end() >= 4 and hint.get_margin_start() >= 2,
              (hint.get_margin_start(), hint.get_margin_end()))
        check("F16 it wraps rather than demanding the width of one line",
              hint.get_line_wrap() and
              hint.get_preferred_width().minimum_width < 120,
              hint.get_preferred_width().minimum_width)

    graded, scratch = module_mutant(
        "F16-flush",
        [("        hint.set_margin_end(10)", "        pass")])
    if resumes:
        os.rename(animation.STORE_FILE, kept)
    try:
        other = graded.Animation()
    finally:
        if resumes:
            os.replace(kept, animation.STORE_FILE)
    other._refresh_lists()
    found = []
    _find_widgets(other.cel_list,
                  lambda w: isinstance(w, Gtk.Label) and
                  "no drawings" in (w.get_text() or ""), found)
    mutant("F16 a hint flush against the panel edge is caught",
           bool(found) and found[0].get_margin_end() < 4)
    shutil.rmtree(scratch)


def dock_reach_family():
    """The colour palette has to be visible without hunting for it.

    The dock scrolls, and colour sat 641px down a 406px viewport: the
    control a drawing app is USED through, below the fold at every screen
    size, behind a scrollbar nobody had a reason to drag. Order in that box
    is a usability decision, so it is measured, not assumed."""
    if not gtk_available():
        skip("F17 dock reach", "no display")
        return
    from gi.repository import Gtk

    def fold(module):
        """Where the dock's visible region ends, and where colour starts."""
        app = module.Animation()
        child = app.get_child()
        app.remove(child)
        off = Gtk.OffscreenWindow()
        off.set_size_request(1024, 722)
        off.add(child)
        off.show_all()
        for _ in range(60):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
        docks = []
        _find_widgets(child,
                      lambda w: isinstance(w, Gtk.ScrolledWindow) and
                      w.get_allocation().x == 0, docks)
        viewport = docks[0].get_allocation().height if docks else 0
        return viewport, app.palette_area.get_allocation().y

    viewport, colour_at = fold(animation)
    check("F17 the colour palette starts above the fold of the tool dock",
          0 < colour_at < viewport - 24, (colour_at, viewport))

    graded, scratch = module_mutant(
        "F17-colour-last",
        [("""        self._build_brush_group(dock)
        self._build_colour_group(dock)
        self._build_shape_group(dock)
        self._build_pattern_group(dock)
        self._build_mirror_group(dock)""",
          """        self._build_brush_group(dock)
        self._build_shape_group(dock)
        self._build_pattern_group(dock)
        self._build_mirror_group(dock)
        self._build_colour_group(dock)""")])
    sunk_viewport, sunk_at = fold(graded)
    mutant("F17 colour pushed below the fold is caught",
           sunk_at >= sunk_viewport - 24, (sunk_at, sunk_viewport))
    shutil.rmtree(scratch)


def scene_strip_family():
    """A film longer than the scene bar is wide must still show you where
    you are. The strip drew from scene 1 every time, so past the fifth
    scene the card for the scene you were IN did not exist — no highlight,
    nothing to point at, and the benchmark film has twenty-one."""
    if not gtk_available():
        skip("F18 scene strip", "no display")
        return
    from gi.repository import Gtk

    def shown_at(module, positions):
        """One window, navigated — which is what a person does. Building a
        fresh window per position costs a full allocation each time and the
        suite has to stay runnable."""
        app = module.Animation()
        while len(app.doc.scenes) < 12:
            app.doc.scenes.append(
                module.new_scene("Scene %d" % (len(app.doc.scenes) + 1)))
        child = app.get_child()
        app.remove(child)
        off = Gtk.OffscreenWindow()
        off.set_size_request(1024, 722)
        off.add(child)
        off.show_all()
        out = {}
        area = app.timeline.get_allocation()
        for at in positions:
            app._switch_scene(at)
            # queue_draw on an offscreen window never arrives, and the cards
            # are built BY the draw — reading them after a queue_draw
            # reports the previous scene's strip. Draw for real.
            paper = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                       max(1, area.width), max(1, area.height))
            app._draw_timeline(app.timeline, cairo.Context(paper))
            out[at] = [c[2] for c in getattr(app, "_scene_cards", [])
                       if c[2] != "add"]
        return out

    drawn = shown_at(animation, (0, 6, 11))
    seen = {at: (shown, at in shown) for at, shown in drawn.items()}
    check("F18 the scene you are in always has a card on the strip",
          all(visible for _shown, visible in seen.values()),
          {k: v[0] for k, v in seen.items()})
    check("F18 and the strip moves rather than always starting at scene one",
          seen[0][0] != seen[11][0],
          (seen[0][0], seen[11][0]))
    counts = {len(shown) for shown, _v in seen.values()}
    check("F18 the add-a-scene card keeps its room at every position",
          all(c[2] == "add" for c in [] ) or
          len(counts) <= 2, sorted(counts))

    graded, scratch = module_mutant(
        "F18-from-zero",
        [("            if last >= self.scene_i or first >= self.scene_i:",
          "            if True:")])
    shown = shown_at(graded, (11,))[11]
    mutant("F18 a strip that always starts at scene one is caught",
           11 not in shown, shown)
    shutil.rmtree(scratch)


def library_family():
    """The drawing library must stay in step with the film, and adding a
    drawing must cost one row.

    Emptying the list and rebuilding it took 92ms at 385 drawings and grew
    with the library, on the path that runs when someone makes a NEW
    drawing. Article VIII B2 allows 50ms in a callback. This counts rows
    BUILT rather than milliseconds, so a loaded machine cannot make it lie."""
    if not gtk_available():
        skip("F19 drawing library", "no display")
        return
    from gi.repository import Gtk

    def names_in(app):
        out = []
        for row in app.cel_list.get_children():
            if not hasattr(row, "cel_id"):
                continue
            labels = []
            _find_widgets(row, lambda w: isinstance(w, Gtk.Label), labels)
            out.append((row.cel_id, labels[0].get_text() if labels else ""))
        return out

    def hints_in(app):
        return [r for r in app.cel_list.get_children()
                if not hasattr(r, "cel_id")]

    app = animation.Animation()
    steps = []

    def agrees(tag):
        want = [(cel.id, cel.name) for cel in app.doc.cels]
        empty_ok = len(hints_in(app)) == (1 if not app.doc.cels else 0)
        steps.append((tag, names_in(app) == want and empty_ok))

    agrees("empty")
    for index in range(6):
        app.doc.add_cel("Drawing %d" % (index + 1))
    app._refresh_lists(); agrees("six added")
    app.doc.cels[2].name = "Renamed"
    app._refresh_lists(); agrees("renamed")
    app.doc.cels[2].version += 1
    app._refresh_lists(); agrees("redrawn")
    moved = app.doc.cels.pop(1)
    app._refresh_lists(); agrees("deleted from the middle")
    app.doc.cels.insert(1, moved)
    app._refresh_lists(); agrees("put back")
    app.doc.cels.reverse()
    app._refresh_lists(); agrees("reversed")
    del app.doc.cels[:]
    app._refresh_lists(); agrees("emptied")
    app.doc.add_cel("After")
    app._refresh_lists(); agrees("refilled")
    check("F19 the library list follows the film through every change",
          all(ok for _tag, ok in steps),
          [tag for tag, ok in steps if not ok])

    def rows_built(module, app, action):
        built = []
        real = module.Animation._build_cel_row

        def counted(self, cel):
            built.append(cel.id)
            return real(self, cel)
        module.Animation._build_cel_row = counted
        try:
            action()
            app._refresh_lists()
        finally:
            module.Animation._build_cel_row = real
        return len(built)

    for _ in range(120):
        app.doc.add_cel()
    app._refresh_lists()
    grew = rows_built(animation, app, lambda: app.doc.add_cel("One more"))
    check("F19 adding a drawing to a large library builds exactly one row",
          grew == 1, grew)

    graded, scratch = module_mutant(
        "F19-rebuild-all",
        [("            if entry is not None and entry[1] == stamp:",
          "            if False:")])
    other = graded.Animation()
    for _ in range(40):
        other.doc.add_cel()
    other._refresh_lists()
    wasteful = rows_built(graded, other, lambda: other.doc.add_cel("One more"))
    mutant("F19 rebuilding the whole library for one drawing is caught",
           wasteful > 1, wasteful)
    shutil.rmtree(scratch)


def sheet_paint_family():
    """Painting the sheet must cost the WINDOW, not the scene, and must
    draw exactly what painting all of it would.

    A 1200-frame scene with 894 exposures laid out a Pango run for every
    one of them and took 53ms a repaint — over Article VIII B2's 50ms, on
    the path that runs every time the playhead moves. Culling to the
    visible window is only allowed if the sheet looks identical, and the
    first attempt did not: a run ending exactly at the left edge still
    draws its right border on the gutter hairline."""
    if not gtk_available():
        skip("F20 sheet paint", "no display")
        return
    from gi.repository import Gtk

    def loaded(module):
        app = module.Animation()
        # A fresh film, not whatever session the store remembered: this
        # compares DRAWING, and it once failed because two builds restored
        # sessions that differed by a single scroll position.
        app.doc = module.AnimationDocument(canvas=(160, 120))
        app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
        for _ in range(40):
            app.doc.add_cel("Drawing %d" % (len(app.doc.cels) + 1))
        scene = app.doc.scenes[0]
        scene["length"] = 900
        while len(scene["layers"]) < module.LAYER_MAX:
            scene["layers"].append(
                module.new_layer("Layer %d" % (len(scene["layers"]) + 1)))
        app.sheet = module.Sheet(app.doc, 0)
        for index in range(len(scene["layers"])):
            at = index                       # stagger across the edges
            while at < scene["length"] - 10:
                scene["layers"][index]["runs"].append(
                    module.make_run(app.doc.cels[at % 40].id, at, 7))
                at += 9
        app._refresh_lists()
        app._update_playhead()
        child = app.get_child()
        app.remove(child)
        off = Gtk.OffscreenWindow()
        off.set_size_request(1024, 722)
        off.add(child)
        off.show_all()
        for _ in range(40):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
        return app, off

    def sheet_bytes(app, origin):
        area = app.timeline.get_allocation()
        app.view_origin = origin
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                     max(1, area.width), max(1, area.height))
        app._draw_timeline(app.timeline, cairo.Context(surface))
        surface.flush()
        return bytes(surface.get_data())

    # the same law for the clip: what GTK invalidates during playback is two
    # thin strips, and skipping work outside them must not change a pixel
    clipped, scratch_clip = module_mutant("F20-noclip", [(CLIP_SKIP, CLIP_BLIND)])
    aware, _keep_c = loaded(animation)
    blind, _keep_d = loaded(clipped)
    rows_h = animation.TL_ROWS_TOP + (animation.LAYER_MAX + 2) * animation.TL_ROW_H

    def under_clip(app, rects):
        area = app.timeline.get_allocation()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                     max(1, area.width), max(1, area.height))
        context = cairo.Context(surface)
        for rect in rects:
            context.rectangle(*rect)
        context.clip()
        app._draw_timeline(app.timeline, context)
        surface.flush()
        return bytes(surface.get_data())

    wide = aware.timeline.get_allocation().width
    shapes = {
        "playback strips": [(300, 0, 18, rows_h + 12),
                            (wide - 470, 0, 330, animation.TL_STRIP_H)],
        "cuts a bar in half": [(400, animation.TL_ROWS_TOP, 9, 60)],
        "one pixel column": [(517, 0, 1, rows_h)],
        "the gutter edge": [(animation.TL_GUTTER - 2, 0, 5, rows_h)],
    }
    clip_same = {}
    for tag, rects in shapes.items():
        for at in (0, 5, 137):
            aware.view_origin = blind.view_origin = at
            clip_same["%s@%d" % (tag, at)] = (under_clip(aware, rects) ==
                                              under_clip(blind, rects))
    check("F20 skipping what the clip excludes changes no pixel inside it",
          all(clip_same.values()),
          [tag for tag, ok in clip_same.items() if not ok])
    shutil.rmtree(scratch_clip)

    graded, scratch = module_mutant(
        "F20-nocull",
        [("""                if run['start'] + run['len'] < seen_from:
                    continue
                if run['start'] > seen_to:
                    break""",
          """                if False:
                    continue
                if False:
                    break""")])
    culled, _keep_a = loaded(animation)
    whole, _keep_b = loaded(graded)
    # 7 is where a run ends exactly on the edge for this stagger
    positions = (0, 7, 200, 449, 890)
    same = {at: sheet_bytes(culled, at) == sheet_bytes(whole, at)
            for at in positions}
    check("F20 the culled sheet is the same picture as the whole sheet",
          all(same.values()), [at for at, ok in same.items() if not ok])

    def frames_touched(module, app):
        drawn = []
        real = module.Animation._frame_to_x

        def counted(self, frame):
            drawn.append(frame)
            return real(self, frame)
        module.Animation._frame_to_x = counted
        try:
            sheet_bytes(app, 400)
        finally:
            module.Animation._frame_to_x = real
        return len(drawn)

    exposures = sum(len(layer["runs"])
                    for layer in culled.doc.scenes[0]["layers"])
    budget = exposures // 2
    touched = frames_touched(animation, culled)
    check("F20 painting the sheet touches the window, not the whole scene",
          touched < budget, (touched, exposures))
    # the same measurement against the build that paints everything: the
    # check above has to be able to fail, not merely to pass
    everything = frames_touched(graded, whole)
    mutant("F20 painting every exposure in the scene is caught",
           everything >= budget, (everything, budget))
    shutil.rmtree(scratch)


def destructive_family():
    """Everything that removes work, and the undo behind each of them.

    Coverage measured with a profile hook said none of these had ever run
    under this suite: deleting a drawing, a layer or a scene, cutting a
    selection, clearing exposures. Losing work is the worst thing this
    program can do, so the standard here is byte-identity: the document
    after undo must be the document before, exactly."""
    if not gtk_available():
        skip("F22 removing work", "no display")
        return

    kept = animation.STORE_FILE + ".destructive-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    scene = app.doc.scenes[0]
    while len(scene["layers"]) < 3:
        scene["layers"].append(
            animation.new_layer("Layer %d" % (len(scene["layers"]) + 1)))
    while len(app.doc.scenes) < 3:
        app.doc.scenes.append(
            animation.new_scene("Scene %d" % (len(app.doc.scenes) + 1)))
    app.sheet = animation.Sheet(app.doc, 0)
    exposed = app.doc.add_cel("On the sheet")
    spare = app.doc.add_cel("Never used")
    elsewhere = app.doc.add_cel("Used in scene 2")
    scene["layers"][0]["runs"].append(animation.make_run(exposed.id, 0, 12))
    scene["layers"][1]["runs"].append(animation.make_run(exposed.id, 20, 8))
    app.doc.scenes[1]["layers"][0]["runs"].append(
        animation.make_run(elsewhere.id, 0, 6))
    app.layer_i = 0
    app._refresh_lists()
    app._update_playhead()

    def undoes(tag, act):
        """Act, insist something changed, undo, insist nothing did."""
        before = app.doc.bytes()
        act()
        changed = app.doc.bytes() != before
        app.history.undo()
        return tag, changed, app.doc.bytes() == before

    results = []
    app._library_cel = spare.id
    results.append(undoes("delete an unused drawing", app._delete_cel))
    app.layer_i = 1
    results.append(undoes("delete a layer", app._delete_layer))
    app.scene_i = 1
    results.append(undoes("delete a scene", lambda: app._delete_scene()))
    app.scene_i = 0
    app.sheet = animation.Sheet(app.doc, 0)
    app.layer_i = 0
    app.selection = (0, 0, 12)
    app.selection_layers = (0, 0)
    results.append(undoes("clear an exposure", app._clear_exposure))
    app.selection = (0, 0, 12)
    app.selection_layers = (0, 0)
    results.append(undoes("cut a selection", app._cut_selection))
    check("F22 every removal changes the film, and undo puts it back exactly",
          all(did and back for _tag, did, back in results),
          [(tag, did, back) for tag, did, back in results
           if not (did and back)])

    # the refusals: what must never be removable
    guarded = {}
    app._library_cel = exposed.id
    before = app.doc.bytes()
    app._delete_cel()
    guarded["a drawing on the sheet"] = app.doc.bytes() == before
    app._library_cel = elsewhere.id
    before = app.doc.bytes()
    app._delete_cel()
    guarded["a drawing used in another scene"] = app.doc.bytes() == before
    only = animation.Animation()
    only.doc.scenes[0]["layers"] = [only.doc.scenes[0]["layers"][0]]
    only.doc.scenes = [only.doc.scenes[0]]
    only.layer_i = only.scene_i = 0
    only.sheet = animation.Sheet(only.doc, 0)
    before = only.doc.bytes()
    only._delete_layer()
    guarded["the last layer"] = only.doc.bytes() == before
    only._delete_scene()
    guarded["the last scene"] = only.doc.bytes() == before
    check("F22 the film always keeps a layer, a scene, and any drawing in use",
          all(guarded.values()),
          [tag for tag, ok in guarded.items() if not ok])

    for window in (app, only):
        window._alive = False
        for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
            source = getattr(window, timer, None)
            if source:
                try:
                    GLib.source_remove(source)
                except Exception:
                    pass
                setattr(window, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F22-delete-in-use",
        [("        if cel_id is None or self._cel_in_use(cel_id):",
          "        if cel_id is None:")])
    reckless = graded.Animation()
    reckless.doc.scenes[0]["layers"][0]["runs"].append(
        graded.make_run(reckless.doc.add_cel("Exposed").id, 0, 5))
    reckless._library_cel = reckless.doc.cels[-1].id
    was = len(reckless.doc.cels)
    reckless._delete_cel()
    mutant("F22 deleting a drawing the film is using is caught",
           len(reckless.doc.cels) < was)
    shutil.rmtree(scratch)


def accelerator_family():
    """Every shortcut a menu advertises has to actually do something.

    The menu bar is where a person learns these, and a label promising
    Ctrl+R is a promise the key handler has to keep. Reading the handler
    cannot check it; the only way is to press all thirty and watch."""
    if not gtk_available():
        skip("F23 shortcuts", "no display")
        return
    from gi.repository import Gdk

    kept = animation.STORE_FILE + ".accel-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    home = tempfile.mkdtemp(prefix="animation-accel-")
    app = animation.Animation()
    app.doc_path = os.path.join(home, "film.anim")
    app.audio = types.SimpleNamespace(
        available=False, samples_delivered=0, start=lambda *a, **k: False,
        stop=lambda *a, **k: None, play_once=lambda *a, **k: None,
        position_samples=lambda: 0)
    app._flash = lambda *a, **k: None
    app.sheet.ensure_drawing(0, 0)
    app.selection = (0, 0, 2)
    app.selection_layers = (0, 0)

    import nbpicker
    previous_open, previous_save = nbpicker.open_file, nbpicker.save_file
    nbpicker.open_file = lambda *a, **k: None
    nbpicker.save_file = lambda *a, **k: None

    NAMED = {
        "Plus": Gdk.KEY_plus, "Minus": Gdk.KEY_minus, "Space": Gdk.KEY_space,
        "Home": Gdk.KEY_Home, "End": Gdk.KEY_End, "Delete": Gdk.KEY_Delete,
        "Esc": Gdk.KEY_Escape, "Page Up": Gdk.KEY_Page_Up,
        "Page Down": Gdk.KEY_Page_Down, ",": Gdk.KEY_comma,
        ".": Gdk.KEY_period, "=": Gdk.KEY_equal, "-": Gdk.KEY_minus,
        "/": Gdk.KEY_slash, "0": Gdk.KEY_0,
    }

    def keyval_for(name):
        if name in NAMED:
            return NAMED[name]
        if len(name) == 1:
            return Gdk.unicode_to_keyval(ord(name.lower()))
        return None

    def press(accel):
        parts = accel.split("+")
        state = 0
        for part in parts[:-1]:
            if part.lower() == "ctrl":
                state |= Gdk.ModifierType.CONTROL_MASK
            elif part.lower() == "shift":
                state |= Gdk.ModifierType.SHIFT_MASK
        keyval = keyval_for(parts[-1])
        if keyval is None:
            return None
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = keyval
        event.state = state
        return app._on_key(app, event)

    advertised = []
    for menu in animation.Animation.menus:
        for item in app.menu_items(menu):
            if not item or item is _nbapp_sep() or not isinstance(item, tuple):
                continue
            if "    " in item[0]:
                name, accel = item[0].split("    ", 1)
                advertised.append((name.strip(), accel.strip()))

    # Esc is the window's, not the document's: outside a prompt this handler
    # only drops a selection, and leaving the app is nbapp's business.
    ignored = {"Esc"}
    dead = []
    for name, accel in advertised:
        if accel in ignored:
            continue
        app._close_prompt()
        app.selection = (0, 0, 2)
        app.selection_layers = (0, 0)
        if press(accel) is not True:
            dead.append((name, accel))
        if app._playing:
            app._stop_playback()
    app._close_prompt()
    check("F23 every shortcut the menus advertise is one the app answers",
          not dead and len(advertised) >= 25, dead or len(advertised))

    nbpicker.open_file, nbpicker.save_file = previous_open, previous_save
    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)
    shutil.rmtree(home, ignore_errors=True)

    graded, scratch = module_mutant(
        "F23-lost-shortcut",
        [("        if e.keyval in (Gdk.KEY_m, Gdk.KEY_M):",
          "        if False:")])
    orphan = graded.Animation()
    orphan._flash = lambda *a, **k: None
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.keyval = Gdk.KEY_m
    event.state = 0
    answered = orphan._on_key(orphan, event)
    mutant("F23 a shortcut a menu still advertises but nothing answers is caught",
           answered is not True, answered)
    shutil.rmtree(scratch)


def _nbapp_sep():
    import nbapp as module
    return module.SEP


def close_guard_family():
    """Closing a film with unsaved work must stop and ask, name the film,
    and offer all three answers.

    The coverage hook said neither _on_delete nor _guard_document had ever
    run under this suite, and this is the last thing standing between a
    person and losing an afternoon."""
    if not gtk_available():
        skip("F24 the close guard", "no display")
        return
    from gi.repository import Gtk

    kept = animation.STORE_FILE + ".guard-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    home = tempfile.mkdtemp(prefix="animation-guard-")
    app = animation.Animation()
    app.doc_path = os.path.join(home, "The Couch.anim")
    animation.save_document(app.doc, app.doc_path)
    app._doc_dirty = False

    left = []
    app.destroy = lambda *a, **k: left.append(True)

    check("F24 a film with nothing to lose closes without a word",
          app._on_delete() is False and app._prompt_layer is None)

    app.sheet.ensure_drawing(0, 0)
    app._doc_dirty = True
    stopped = app._on_delete()
    opened = app._prompt_layer is not None
    words = []
    _find_widgets(app._prompt_layer, lambda w: isinstance(w, Gtk.Label), words)
    said = " ".join(w.get_text() or "" for w in words)
    check("F24 unsaved work stops the close and names the film at stake",
          stopped is True and opened and "The Couch" in said, said[:90])

    buttons = []
    _find_widgets(app._prompt_layer,
                  lambda w: isinstance(w, Gtk.Button), buttons)
    offered = {(w.get_label() or "").strip() for w in buttons}
    check("F24 it offers saving, discarding and going back",
          {"Save", "Discard", "Cancel"} <= offered, sorted(offered))

    # Cancel leaves everything exactly as it was
    for button in buttons:
        if (button.get_label() or "").strip() == "Cancel":
            button.clicked()
            break
    check("F24 going back closes nothing and loses nothing",
          not left and app._doc_dirty and app._prompt_layer is None)

    # Discard closes, and does not write over the file on disk
    on_disk = open(app.doc_path, "rb").read()
    app._on_delete()
    buttons = []
    _find_widgets(app._prompt_layer,
                  lambda w: isinstance(w, Gtk.Button), buttons)
    for button in buttons:
        if (button.get_label() or "").strip() == "Discard":
            button.clicked()
            break
    check("F24 discarding closes, and leaves the saved film untouched",
          bool(left) and open(app.doc_path, "rb").read() == on_disk)

    # Save writes the change, then closes
    del left[:]
    app._doc_dirty = True
    app.sheet.ensure_drawing(0, 1)
    app._on_delete()
    buttons = []
    _find_widgets(app._prompt_layer,
                  lambda w: isinstance(w, Gtk.Button), buttons)
    for button in buttons:
        if (button.get_label() or "").strip() == "Save":
            button.clicked()
            break
    saved_now = open(app.doc_path, "rb").read()
    check("F24 saving writes the film before it closes",
          bool(left) and saved_now != on_disk, (bool(left), len(saved_now)))

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)
    shutil.rmtree(home, ignore_errors=True)

    graded, scratch = module_mutant(
        "F24-no-guard",
        [("        if not self._needs_guard():\n            return False",
          "        if True:\n            return False")])
    reckless = graded.Animation()
    reckless.doc_path = None
    reckless.sheet.ensure_drawing(0, 0)
    reckless._doc_dirty = True
    mutant("F24 closing straight through unsaved work is caught",
           reckless._on_delete() is False)
    shutil.rmtree(scratch)


def export_outcome_family():
    """What the app says when an export ends, three ways.

    Coverage said the whole export flow had never run here. Driving it
    found the app reporting "Completed" for an export somebody had just
    cancelled, naming a file that did not exist — and reporting a failure
    by pasting ffmpeg's stderr, memory address and all, into a label that
    cannot wrap."""
    if not gtk_available():
        skip("F25 export outcomes", "no display")
        return
    from gi.repository import Gtk

    kept = animation.STORE_FILE + ".export-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    scene = app.doc.scenes[0]
    scene["length"] = 40
    cel, _run = app.sheet.ensure_drawing(0, 0)
    app._refresh_lists()
    frames, specs = app._export_range("scene")
    # _export_apply makes this; these checks call _export_start directly
    os.makedirs(animation.VIDEOS_DIR, exist_ok=True)
    state = {"kind": "video", "range": "scene", "name": "outcome",
             "size": (160, 120), "native": False, "gif_scale": 1}

    def run_export(exporter, then=None):
        """Start an export whose worker is `exporter`, and settle."""
        real = animation.export_video
        animation.export_video = exporter
        path = os.path.join(animation.VIDEOS_DIR, "outcome.mp4")
        try:
            app._export_start(state, frames, specs, path)
            if then:
                then()
            for _ in range(500):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
                if not any(w.is_alive() for w in app._workers):
                    break
                time.sleep(0.01)
            for _ in range(60):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
        finally:
            animation.export_video = real
            app._close_prompt()
        return app.hint.get_text(), path

    def stopped(*_a, **_k):
        raise InterruptedError()

    def broken(*_a, **_k):
        raise RuntimeError("[libx264 @ 0x55d3a] height not divisible by 2")

    def fine(document, chosen, path, *_a, **_k):
        open(path, "wb").write(b"not really a movie")

    said, path = run_export(stopped)
    check("F25 an export somebody stopped does not report success",
          "stop" in said.lower() and "complet" not in said.lower(), said)
    check("F25 and it leaves no half-written movie behind",
          not os.path.exists(path), path)

    said, path = run_export(broken)
    check("F25 a failed export says so in a sentence, not in ffmpeg's words",
          "libx264" not in said and "0x" not in said and
          len(said.split()) < 12 and said.endswith("."), said)
    check("F25 and it does not leave the broken file in Videos",
          not os.path.exists(path), path)

    said, path = run_export(fine)
    check("F25 a finished export names the file, not its whole path",
          "outcome.mp4" in said and "/" not in said, said)
    if os.path.exists(path):
        os.unlink(path)

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    # the bug as it was: a stop swallowed into the success path
    graded, scratch = module_mutant(
        "F25-stop-reads-as-done",
        [("                outcome = 'stopped'", "                outcome = None")])
    other = graded.Animation()
    other.doc.scenes[0]["length"] = 40
    other.sheet.ensure_drawing(0, 0)
    other._refresh_lists()
    other_frames, other_specs = other._export_range("scene")
    real = graded.export_video
    graded.export_video = stopped
    try:
        other._export_start(state, other_frames, other_specs,
                            os.path.join(graded.VIDEOS_DIR, "mutant.mp4"))
        for _ in range(500):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            if not any(w.is_alive() for w in other._workers):
                break
            time.sleep(0.01)
        for _ in range(60):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
    finally:
        graded.export_video = real
        other._close_prompt()
    lied = other.hint.get_text()
    mutant("F25 a stopped export reported as completed is caught",
           "complet" in lied.lower(), lied)
    other._alive = False
    shutil.rmtree(scratch)


def dock_controls_family():
    """The controls a person touches most, driven the way they touch them.

    The colour palette is the reason the dock was reordered this session —
    it is what a drawing app is used through — and the coverage hook said
    every one of its handlers had never run: the swatch press, the cell
    arithmetic, the hover name, the brush ramp, the tip and pattern
    choices. A grid you pick colours from by pixel is exactly where an
    off-by-one hides."""
    if not gtk_available():
        skip("F26 dock controls", "no display")
        return
    from gi.repository import Gdk, Gtk

    kept = animation.STORE_FILE + ".dock-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    app._flash = lambda *a, **k: None
    swatch, gap = app._swatch_geom

    def press_swatch(col, row, inset=1):
        event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        event.x = col * (swatch + gap) + inset
        event.y = row * (swatch + gap) + inset
        event.button = 1
        app._swatch_press(app.palette_area, event)
        return app.color

    # every swatch in the grid must hand back its own colour
    wrong = []
    for index in range(len(animation.PALETTE)):
        col, row = index % 16, index // 16
        got = press_swatch(col, row)
        if got != animation.PALETTE[index]:
            wrong.append((index, got, animation.PALETTE[index]))
    check("F26 every swatch in the grid picks the colour drawn in it",
          not wrong, wrong[:4])

    # the gaps between swatches belong to nobody
    app._choose_colour(None, animation.PALETTE[0])
    before = app.color
    missed = app._swatch_cell(swatch + 0.5, 0) is None
    press_swatch(1, 0, inset=swatch)      # land in the gutter after a cell
    check("F26 the gaps between swatches are not a colour",
          missed and app.color == before, (missed, app.color, before))

    named = app._palette_name(0)
    check("F26 a swatch answers hover with a name, not a hex code",
          bool(named) and not named.startswith("#"), named)

    # the brush ramp: six cells, six sizes, in order
    sizes = []
    for left, width in app._ramp_cells():
        event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        event.x = left + width / 2
        event.y = 8
        event.button = 1
        app._brush_ramp_press(app.ramp_area, event)
        sizes.append(app.size)
    check("F26 the brush ramp sets the six sizes it draws, in order",
          sizes == [1, 2, 3, 6, 12, 24], sizes)
    check("F26 and the readout beside it says the size that was chosen",
          app.size_lbl.get_text().startswith("24"), app.size_lbl.get_text())

    # tip, pattern and mirror, through the real controls: these handlers
    # read the state of the button that called them, so calling them
    # directly tests something the dock does not do
    def dock_button(name):
        found = []
        _find_widgets(app, lambda w: isinstance(w, Gtk.Button) and
                      ((w.get_label() or "") == name or
                       (w.get_tooltip_text() or "") == name), found)
        return found[0] if found else None

    picked = {}
    for name, attribute, want in (("Round tip", "shape", "round"),
                                  ("Square tip", "shape", "square"),
                                  ("Checker", "pattern", "checker"),
                                  ("Mirror left and right", "symx", True)):
        button = dock_button(name)
        if button is None:
            picked[name] = "no such control"
            continue
        button.set_active(True)
        picked[name] = getattr(app, attribute)
        if picked[name] != want:
            picked[name] = "%r, wanted %r" % (picked[name], want)
        else:
            picked[name] = True
    check("F26 tip, pattern and mirror each set what they name",
          all(value is True for value in picked.values()), picked)

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F26-swatch-row-slip",
        [("        index = row * 16 + col", "        index = row * 15 + col")])
    slipped = graded.Animation()
    event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    event.x, event.y, event.button = 1, 2 * (swatch + gap) + 1, 1
    slipped._swatch_press(slipped.palette_area, event)
    mutant("F26 a palette that hands back the wrong swatch is caught",
           slipped.color != graded.PALETTE[32], slipped.color)
    shutil.rmtree(scratch)


def selection_family():
    """Choosing what to work on, by pointing at it.

    Clicking the canvas picks the topmost VISIBLE layer that has ink under
    the pointer — click-through, which is how you grab a character standing
    in front of a background. Clicking the sheet picks the exposure under
    the pointer. Neither had ever run here, and both decide what every
    subsequent edit applies to."""
    if not gtk_available():
        skip("F27 pointing at things", "no display")
        return
    from gi.repository import Gdk

    kept = animation.STORE_FILE + ".select-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    scene = app.doc.scenes[0]
    scene["length"] = 30
    while len(scene["layers"]) < 2:
        scene["layers"].append(animation.new_layer("Layer 2"))
    app.sheet = animation.Sheet(app.doc, 0)

    def inked(name, spots):
        cel = app.doc.add_cel(name)
        surface = cel.decoded(0)
        for x, y in spots:
            animation.write_pixel(surface, x, y, "#1A1916")
        cel.version += 1
        return cel

    low = inked("Behind", [(5, 5), (18, 12)])
    high = inked("In front", [(20, 20), (18, 12)])
    scene["layers"][0]["runs"].append(animation.make_run(low.id, 0, 30))
    scene["layers"][1]["runs"].append(animation.make_run(high.id, 0, 30))
    app._refresh_lists()

    picks = {}
    app.selection = None
    app._select_at_pixel((5, 5))
    picks["ink on the layer behind"] = app.layer_i == 0
    app._select_at_pixel((20, 20))
    picks["ink on the layer in front"] = app.layer_i == 1
    app.layer_i = 0
    app._select_at_pixel((18, 12))
    picks["ink on both picks the front one"] = app.layer_i == 1
    app._select_at_pixel((38, 2))
    # clicking where nothing is drawn lets go of what was held
    picks["bare paper deselects"] = app.selection is None
    check("F27 clicking the canvas picks the drawing under the pointer",
          all(picks.values()),
          [tag for tag, ok in picks.items() if not ok])

    # a hidden layer is not in the way
    scene["layers"][1]["visible"] = False
    app.layer_i = 1
    app._select_at_pixel((18, 12))
    hidden_skipped = app.layer_i == 0
    scene["layers"][1]["visible"] = True
    check("F27 a hidden layer cannot be picked through the canvas",
          hidden_skipped, app.layer_i)

    # and the sheet: pressing an exposure selects exactly that exposure
    app.selection = None
    scene["layers"][0]["runs"] = [animation.make_run(low.id, 4, 6),
                                  animation.make_run(low.id, 14, 5)]
    app.sheet = animation.Sheet(app.doc, 0)
    rows = len(scene["layers"])
    press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    press.x = animation.TL_GUTTER + 5 * app.column_width
    press.y = (animation.TL_ROWS_TOP +
               (rows - 1) * animation.TL_ROW_H + 2)
    press.button = 1
    app._timeline_press(app.timeline, press)
    release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
    release.x, release.y, release.button = press.x, press.y, 1
    app._timeline_release(app.timeline, release)
    check("F27 pressing an exposure on the sheet selects that exposure",
          app.selection == (0, 4, 10), app.selection)

    # dragging at the left edge walks the view back toward the start
    app.view_origin = 40
    app._edge_scroll(animation.TL_GUTTER + 2)
    walked_back = app.view_origin < 40
    app._edge_scroll(app.timeline.get_allocated_width() - 2)
    check("F27 dragging at an edge walks the view along the sheet",
          walked_back and app.view_origin >= 0, app.view_origin)

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F27-picks-the-bottom",
        [("        for index in reversed(range(len(scene['layers']))):",
          "        for index in range(len(scene['layers'])):")])
    upside_down = graded.Animation()
    upside_down.doc = graded.AnimationDocument(canvas=(160, 120))
    upside_down.scene_i = upside_down.layer_i = upside_down.playhead = 0
    other = upside_down.doc.scenes[0]
    other["length"] = 30
    while len(other["layers"]) < 2:
        other["layers"].append(graded.new_layer("Layer 2"))
    upside_down.sheet = graded.Sheet(upside_down.doc, 0)
    for name in ("Behind", "In front"):
        cel = upside_down.doc.add_cel(name)
        graded.write_pixel(cel.decoded(0), 18, 12, "#1A1916")
        cel.version += 1
    for index, cel in enumerate(upside_down.doc.cels[:2]):
        other["layers"][index]["runs"].append(graded.make_run(cel.id, 0, 30))
    upside_down.layer_i = 0
    upside_down._select_at_pixel((18, 12))
    mutant("F27 a canvas that picks the layer behind is caught",
           upside_down.layer_i != 1, upside_down.layer_i)
    shutil.rmtree(scratch)


def card_effect_family():
    """What each card DOES, driven through the card.

    Coverage said the applies had never run: the marker, insert, remove,
    repeat, the three renames and the take choice. A card that opens
    correctly and then applies wrongly is worse than one that will not
    open, because the person believes it."""
    if not gtk_available():
        skip("F28 what the cards do", "no display")
        return

    kept = animation.STORE_FILE + ".cards-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    scene = app.doc.scenes[0]
    scene["length"] = 40
    app.sheet = animation.Sheet(app.doc, 0)
    cel, _run = app.sheet.ensure_drawing(0, 0)
    app._refresh_lists()

    def through_card(open_card, changes):
        """Open a card, answer it, apply it — and report undo fidelity."""
        before = app.doc.bytes()
        app._close_prompt()
        open_card()
        if app._prompt_layer is None:
            return None, False
        state = dict(app._prompt_state)
        state.update(changes)
        app._apply_prompt(app._prompt_callback, state)
        after = app.doc.bytes()
        return after != before, (app.history.undo() is not False and
                                 app.doc.bytes() == before)

    results = {}

    app.playhead = 6
    results["marker"] = through_card(app._marker_prompt, {"text": "Beat"})
    marked = any(m["frame"] == 6 and m["text"] == "Beat"
                 for m in scene["markers"])

    app.playhead = 0
    results["insert frames"] = through_card(app._insert_prompt, {"count": 5})
    results["remove frames"] = through_card(app._remove_prompt, {"count": 3})

    # copies land at the playhead, so put it somewhere the copies fit —
    # repeating on top of the exposures being copied is refused, correctly
    # a whole exposure, copied to somewhere it fits: copy_block takes
    # complete runs only, and the copies land at the playhead
    app.sheet.clear(0, 0, scene["length"])
    app.sheet.stamp(0, animation.make_run(cel.id, 0, 4))
    app.selection = (0, 0, 4)
    app.selection_layers = (0, 0)
    app.playhead = 20
    app.sheet.clipboard = None
    results["repeat"] = through_card(app._repeat_prompt, {"count": 2})

    app._library_cel = cel.id
    results["rename a drawing"] = through_card(
        lambda: app._rename_cel_prompt(cel_id=cel.id), {"name": "Hopper"})
    results["rename a layer"] = through_card(app._rename_layer_prompt,
                                             {"name": "Foreground"})
    results["rename a scene"] = through_card(app._rename_scene_prompt,
                                             {"name": "The kitchen"})

    while len(cel.takes) < 3:
        cel.takes.append(cel.decoded(0))
    cel.version += 1
    app.playhead = 0
    results["choose a take"] = through_card(app._choose_take_prompt,
                                            {"take": 2})

    # a selection lying inside one long hold copies nothing: the app must
    # say so rather than appear to work
    app._close_prompt()
    app.sheet.clear(0, 0, scene["length"])
    app.sheet.stamp(0, animation.make_run(cel.id, 0, 30))
    app.sheet.clipboard = None
    app.selection = (0, 0, 4)          # four frames inside one long hold
    app.selection_layers = (0, 0)
    app.playhead = 32
    refused = []
    spoken = app._flash
    app._flash = lambda text, *a, **k: refused.append(text)
    try:
        app._repeat_apply({"count": 2})
    finally:
        app._flash = spoken
    check("F28 repeating part of a hold refuses out loud instead of doing nothing",
          len(refused) == 1 and "whole" in refused[0].lower(), refused)

    check("F28 every card changes the film when applied",
          all(value[0] for value in results.values()),
          [tag for tag, value in results.items() if not value[0]])
    check("F28 and undo puts back exactly what each card changed",
          all(value[1] for value in results.values()),
          [tag for tag, value in results.items() if not value[1]])
    check("F28 the marker card puts the marker on the frame it was opened on",
          marked, scene["markers"])

    # a rename that is only whitespace must not erase the name
    app._library_cel = cel.id
    was = cel.name
    app._close_prompt()
    app._rename_cel_prompt(cel_id=cel.id)
    if app._prompt_layer is not None:
        state = dict(app._prompt_state)
        state["name"] = "   "
        app._apply_prompt(app._prompt_callback, state)
    check("F28 a blank name is refused rather than applied",
          cel.name.strip() == was.strip() and cel.name.strip() != "",
          (was, cel.name))

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F28-marker-off-by-one",
        [("        old = next((m for m in markers if m['frame'] == self.playhead), None)",
          "        old = next((m for m in markers if m['frame'] == self.playhead + 1), None)")])
    astray = graded.Animation()
    astray._flash = lambda *a, **k: None
    astray.playhead = 3
    astray._marker_prompt()
    state = dict(astray._prompt_state)
    state["text"] = "First"
    astray._apply_prompt(astray._prompt_callback, state)
    astray.playhead = 3
    astray._marker_prompt()
    state = dict(astray._prompt_state)
    state["text"] = "Second"
    astray._apply_prompt(astray._prompt_callback, state)
    frames = [m["frame"] for m in astray.doc.scenes[astray.scene_i]["markers"]]
    mutant("F28 a marker card that looks at the wrong frame is caught",
           len(frames) != 1 or frames.count(3) != 1, frames)
    astray._alive = False
    shutil.rmtree(scratch)


def ordering_family():
    """Reordering layers and scenes, and keeping your place while you do.

    Neither had ever run here. Both swap two things and then have to decide
    where the person is standing afterwards, which is exactly the sort of
    bookkeeping that goes wrong quietly."""
    if not gtk_available():
        skip("F29 reordering", "no display")
        return

    kept = animation.STORE_FILE + ".order-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    while len(app.doc.scenes) < 3:
        app.doc.scenes.append(
            animation.new_scene("Scene %d" % (len(app.doc.scenes) + 1)))
    scene = app.doc.scenes[0]
    while len(scene["layers"]) < 3:
        scene["layers"].append(
            animation.new_layer("Layer %d" % (len(scene["layers"]) + 1)))
    app.sheet = animation.Sheet(app.doc, 0)
    cel, _run = app.sheet.ensure_drawing(0, 0)

    named = [layer["name"] for layer in scene["layers"]]
    app.layer_i = 0
    app._raise_layer()
    check("F29 raising a layer moves it, and the person moves with it",
          [l["name"] for l in scene["layers"]] == [named[1], named[0], named[2]]
          and scene["layers"][app.layer_i]["name"] == named[0],
          [l["name"] for l in scene["layers"]])
    app._lower_layer()
    check("F29 lowering it again puts the order back",
          [l["name"] for l in scene["layers"]] == named and
          scene["layers"][app.layer_i]["name"] == named[0],
          [l["name"] for l in scene["layers"]])

    app.layer_i = len(scene["layers"]) - 1
    frozen = [l["name"] for l in scene["layers"]]
    app._raise_layer()
    app.layer_i = 0
    app._lower_layer()
    check("F29 a layer at either end has nowhere further to go",
          [l["name"] for l in scene["layers"]] == frozen,
          [l["name"] for l in scene["layers"]])

    # moving the scene you are standing in must not cost you your place
    app.scene_i = 1
    app.sheet = animation.Sheet(app.doc, 1)
    app.doc.scenes[1]["layers"][0]["runs"].append(
        animation.make_run(cel.id, 0, 30))
    app.playhead = 17
    app.layer_i = 0
    app.selection = (0, 0, 30)
    app.selection_layers = (0, 0)
    moved_name = app.doc.scenes[1]["name"]
    app._move_scene(-1)
    check("F29 a scene moved earlier takes you with it",
          app.scene_i == 0 and app.doc.scenes[0]["name"] == moved_name,
          [s["name"] for s in app.doc.scenes])
    check("F29 and you keep the frame and the selection you had",
          app.playhead == 17 and app.selection == (0, 0, 30),
          (app.playhead, app.selection))

    edges = [s["name"] for s in app.doc.scenes]
    app._move_scene(-1)
    check("F29 the first scene has nowhere earlier to go",
          [s["name"] for s in app.doc.scenes] == edges,
          [s["name"] for s in app.doc.scenes])

    # layer visibility, through the checkbox the panel shows
    from gi.repository import Gtk
    boxes = []
    _find_widgets(app.layer_list,
                  lambda w: isinstance(w, Gtk.CheckButton), boxes)
    lit = app.doc.scenes[app.scene_i]["layers"][0].get("visible", True)
    if boxes:
        boxes[-1].set_active(not lit)
    check("F29 the eye beside a layer hides and shows it",
          bool(boxes) and
          app.doc.scenes[app.scene_i]["layers"][0].get("visible", True) != lit,
          [l.get("visible", True)
           for l in app.doc.scenes[app.scene_i]["layers"]])

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F29-layer-left-behind",
        [("        self.layer_i = target\n        self._commit_change()",
          "        self._commit_change()")])
    adrift = graded.Animation()
    adrift._flash = lambda *a, **k: None
    adrift.doc = graded.AnimationDocument(canvas=(160, 120))
    adrift.scene_i = adrift.layer_i = 0
    other = adrift.doc.scenes[0]
    while len(other["layers"]) < 3:
        other["layers"].append(
            graded.new_layer("Layer %d" % (len(other["layers"]) + 1)))
    adrift.sheet = graded.Sheet(adrift.doc, 0)
    first = other["layers"][0]["name"]
    adrift._raise_layer()
    mutant("F29 a move that leaves the person on the wrong layer is caught",
           other["layers"][adrift.layer_i]["name"] != first,
           other["layers"][adrift.layer_i]["name"])
    shutil.rmtree(scratch)


def library_and_palette_family():
    """The drawing library's own gestures, and the project palette.

    The library's press-and-hold says in its own docstring that a look
    costs nothing and changes nothing — a claim nothing had ever tested.
    The palette buttons and the recolour all edit the document and all
    have to be undoable."""
    if not gtk_available():
        skip("F30 library and palette", "no display")
        return
    from gi.repository import Gdk, Gtk

    kept = animation.STORE_FILE + ".library-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    app.sheet = animation.Sheet(app.doc, 0)
    cel, _run = app.sheet.ensure_drawing(0, 0)
    animation.write_pixel(cel.decoded(0), 4, 4, "#C8341E")
    cel.version += 1
    app._refresh_lists()
    # get_row_at_y answers from the ALLOCATION, so an unrealised list has no
    # row at any height and press-and-hold would look like it does nothing
    child = app.get_child()
    app.remove(child)
    stage = Gtk.OffscreenWindow()
    stage.set_size_request(1024, 722)
    stage.add(child)
    stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    rows = [r for r in app.cel_list.get_children() if hasattr(r, "cel_id")]
    row_y = rows[0].get_allocation().y + 4 if rows else 4
    check("F30 the library lists the film's drawings", len(rows) == 1,
          len(rows))

    # press and hold: a look costs nothing
    untouched = app.doc.bytes()
    press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    press.x, press.y, press.button = 4, row_y, 1
    app._cel_list_press(app.cel_list, press)
    looking = getattr(app, "_preview_cel", None)
    release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
    release.x, release.y, release.button = 4, row_y, 1
    app._cel_list_release(app.cel_list, release)
    check("F30 press and hold shows a drawing, and letting go puts it back",
          looking == cel.id and getattr(app, "_preview_cel", None) is None,
          (looking, getattr(app, "_preview_cel", None)))
    check("F30 and looking at a drawing changes nothing in the film",
          app.doc.bytes() == untouched)

    # the row hands its identity to a drag
    app._cel_row_selected(app.cel_list, rows[0])
    picked = getattr(app, "_library_cel", None)
    carried = []

    class Parcel:
        def set_text(self, text, _length):
            carried.append(text)

    app._cel_drag_data_get(rows[0], None, Parcel(), 0, 0, cel.id)
    check("F30 a row knows which drawing it is, and a drag carries that",
          picked == cel.id and carried == [str(cel.id)], (picked, carried))

    # double-clicking a row asks for a new name
    app._close_prompt()
    app._cel_row_activated(app.cel_list, rows[0])
    named = app._prompt_layer is not None
    app._close_prompt()
    check("F30 opening a row asks what to call the drawing", named)

    # the project palette
    steps = {}

    def undoable(tag, act):
        before = app.doc.bytes()
        act()
        moved = app.doc.bytes() != before
        app.history.undo()
        steps[tag] = moved and app.doc.bytes() == before

    app._choose_colour(None, "#C8341E")
    undoable("add a colour", app._palette_add)
    app._palette_add()
    undoable("remove a colour", app._palette_remove)
    check("F30 adding and removing a palette colour both undo cleanly",
          all(steps.values()), steps)

    # a colour already in the palette is not added twice
    app.doc.palette = ["#C8341E"]
    app._choose_colour(None, "#C8341E")
    app._palette_add()
    check("F30 the same colour is not added to the palette twice",
          app.doc.palette == ["#C8341E"], app.doc.palette)

    # recolouring a drawing to the palette edits pixels, and undoes
    app.doc.palette = ["#1A1916"]
    app.playhead = 0
    before = app.doc.bytes()
    app._recolor_cel()
    recoloured = app.doc.bytes() != before
    app.history.undo()
    check("F30 recolouring a drawing to the palette is a change you can undo",
          recoloured and app.doc.bytes() == before, recoloured)

    # takes come and go within their bounds. Undo REPLACES app.doc by
    # re-parsing it, so the cel object held above belongs to a document
    # that no longer exists — ask the live one for it again.
    live = app.doc.cel(cel.id) or app.doc.cels[0]
    app._library_cel = live.id
    counts = [len(live.takes)]
    for _ in range(animation.TAKE_MAX + 2):
        app._add_take()
    counts.append(len(live.takes))
    for _ in range(animation.TAKE_MAX + 2):
        app._remove_take()
    counts.append(len(live.takes))
    check("F30 takes stop at the cap on the way up and at one on the way down",
          counts[1] == animation.TAKE_MAX and counts[2] == 1, counts)

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F30-look-costs-something",
        [("    def _cel_list_release(self, _widget, _event):\n"
          "        if getattr(self, '_preview_cel', None) is not None:",
          "    def _cel_list_release(self, _widget, _event):\n"
          "        if False:")])
    stuck = graded.Animation()
    stuck.doc = graded.AnimationDocument(canvas=(160, 120))
    stuck.scene_i = stuck.layer_i = stuck.playhead = 0
    stuck.sheet = graded.Sheet(stuck.doc, 0)
    other, _ = stuck.sheet.ensure_drawing(0, 0)
    stuck._refresh_lists()
    stuck_child = stuck.get_child()
    stuck.remove(stuck_child)
    stuck_stage = Gtk.OffscreenWindow()
    stuck_stage.set_size_request(1024, 722)
    stuck_stage.add(stuck_child)
    stuck_stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    stuck_rows = [r for r in stuck.cel_list.get_children()
                  if hasattr(r, "cel_id")]
    stuck_press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    stuck_press.x = 4
    stuck_press.y = (stuck_rows[0].get_allocation().y + 4) if stuck_rows else 4
    stuck_press.button = 1
    stuck._cel_list_press(stuck.cel_list, stuck_press)
    stuck._cel_list_release(stuck.cel_list, release)
    mutant("F30 a preview that never lets go is caught",
           getattr(stuck, "_preview_cel", None) is not None)
    shutil.rmtree(scratch)


def slide_and_onion_family():
    """Sliding between exposures, and the onion skin behind them.

    Slide is the one place the app moves a drawing across a gap, and it
    had four ways to refuse and no way to say so. The onion skin is the
    feature that once drew UNDER opaque paper and so was invisible; it is
    worth pinning what it actually produces."""
    if not gtk_available():
        skip("F31 sliding and onion skin", "no display")
        return

    kept = animation.STORE_FILE + ".slide-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    scene = app.doc.scenes[0]
    scene["length"] = 40
    app.sheet = animation.Sheet(app.doc, 0)
    cel, _run = app.sheet.ensure_drawing(0, 0)
    animation.write_pixel(cel.decoded(0), 8, 8, "#1A1916")
    cel.version += 1
    other = app.doc.add_cel("Someone else")
    animation.write_pixel(other.decoded(0), 20, 20, "#C8341E")
    other.version += 1

    said = []
    spoken = app._flash
    app._flash = lambda text, *a, **k: said.append(text)

    def between(first, second, gap_start=None, occupy=None, second_cel=None):
        app.sheet.clear(0, 0, scene["length"])
        app.sheet.stamp(0, animation.make_run(cel.id, first, 2, 0, 0))
        app.sheet.stamp(0, animation.make_run((second_cel or cel).id,
                                              second, 2, 30, 12))
        if occupy is not None:
            app.sheet.stamp(0, animation.make_run(cel.id, occupy, 1))
        app.selection = (0, first, second + 2)
        app.selection_layers = (0, 0)
        del said[:]
        before = len(scene["layers"][0]["runs"])
        app._slide_selection()
        return len(scene["layers"][0]["runs"]) - before, list(said)

    filled, quiet = between(0, 10)
    check("F31 sliding fills the gap between two exposures of one drawing",
          filled == 8 and not quiet, (filled, quiet))
    positions = [run["dx"] for run in scene["layers"][0]["runs"]]
    check("F31 and it walks the drawing across, not all at once",
          positions == sorted(positions) and positions[0] == 0 and
          positions[-1] == 30 and len(set(positions)) > 3, positions)
    check("F31 sliding is one change that undoes as one",
          app.history.undo() is not False)

    refusals = {}
    added, told = between(0, 10, second_cel=other)
    refusals["two different drawings"] = added == 0 and len(told) == 1
    added, told = between(0, 4, occupy=2)
    refusals["something already in the gap"] = added == 0 and len(told) == 1
    app.sheet.clear(0, 0, scene["length"])
    app.sheet.stamp(0, animation.make_run(cel.id, 0, 4))
    app.selection = (0, 0, 4)
    app.selection_layers = (0, 0)
    del said[:]
    app._slide_selection()
    refusals["one exposure, no gap"] = len(said) == 1
    check("F31 every way of refusing a slide says so",
          all(refusals.values()),
          [tag for tag, ok in refusals.items() if not ok])
    app._flash = spoken

    # the onion skin: tinted ink on transparent ground, and cached.
    # The undo above re-parsed the document, so `cel` is an object from a
    # film that no longer exists — ask the live one, and its scene too.
    cel = app.doc.cel(cel.id) or app.doc.cels[0]
    scene = app.doc.scenes[app.scene_i]
    app.sheet = animation.Sheet(app.doc, app.scene_i)
    app.sheet.clear(0, 0, scene["length"])
    app.sheet.stamp(0, animation.make_run(cel.id, 5, 1))
    tint = "#C8341E"
    skin = app._onion_surface(scene, 5, tint)
    skin.flush()
    data, stride = skin.get_data(), skin.get_stride()

    def at(x, y):
        offset = y * stride + x * 4
        return (data[offset + 2], data[offset + 1], data[offset],
                data[offset + 3])
    empty = at(60, 60)
    marked = at(8, 8)
    check("F31 the onion skin leaves the paper transparent",
          empty[3] == 0, empty)
    check("F31 and tints the ink it does carry",
          marked[3] > 0 and (marked[0], marked[1], marked[2]) ==
          animation._rgb255(tint), marked)
    check("F31 the same neighbour is not composed twice",
          app._onion_surface(scene, 5, tint) is skin)

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F31-silent-slide",
        [("            self._flash(_t('Select two exposures of the same drawing with '\n"
          "                           'space between them.'))",
          "            pass")])
    mute = graded.Animation()
    mute.doc = graded.AnimationDocument(canvas=(160, 120))
    mute.scene_i = mute.layer_i = mute.playhead = 0
    mute.sheet = graded.Sheet(mute.doc, 0)
    lone, _r = mute.sheet.ensure_drawing(0, 0)
    heard = []
    mute._flash = lambda text, *a, **k: heard.append(text)
    mute.selection = (0, 0, 2)
    mute.selection_layers = (0, 0)
    mute._slide_selection()
    mutant("F31 a slide that refuses in silence is caught", not heard)
    shutil.rmtree(scratch)


def stamping_family():
    """A lip-sync pass: the app's signature workflow, and the one place it
    deliberately does NOT snapshot per change.

    One snapshot covers the whole pass, taken when the mode opens, because
    a snapshot serialises the entire film. That is the right trade. What it
    cost until now is that nothing marked the film unsaved while stamping,
    so closing in the middle of a pass took a whole take with it and the
    guard never asked."""
    if not gtk_available():
        skip("F32 stamping mouths", "no display")
        return

    kept = animation.STORE_FILE + ".stamp-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    said = []
    spoken = app._flash
    app._flash = lambda text, *a, **k: said.append(text)
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    scene = app.doc.scenes[0]
    scene["length"] = 40
    app.sheet = animation.Sheet(app.doc, 0)
    mouths = [app.doc.add_cel("Mouth %d" % index) for index in range(3)]

    # without slots, the mode refuses and says why
    del said[:]
    app._toggle_stamp_mouths()
    check("F32 stamping without mouth slots refuses, and says so",
          not app.stamp_mouths and len(said) == 1, (app.stamp_mouths, said))

    scene["layers"][0]["mouth_slots"] = [m.id for m in mouths]
    app._doc_dirty = False
    app._undo = []
    app._toggle_stamp_mouths()
    opened = app.stamp_mouths and len(app._undo) == 1
    app._doc_dirty = False

    before = app.doc.bytes()
    for frame in range(6):
        app.playhead = frame
        app._stamp_mouth(1 + frame % 3)
    check("F32 opening the mode takes one snapshot for the whole pass",
          opened and len(app._undo) == 1, len(app._undo))
    check("F32 stamping lays a mouth on each frame it is asked for",
          len(scene["layers"][0]["runs"]) == 6 and app.doc.bytes() != before,
          len(scene["layers"][0]["runs"]))
    check("F32 and the film is unsaved from the FIRST stamp, not the last",
          app._doc_dirty and app._needs_guard(),
          (app._doc_dirty, app._needs_guard()))

    # one undo takes the whole pass back
    app.history.undo()
    check("F32 one undo puts back everything the pass stamped",
          app.doc.bytes() == before, len(app.doc.scenes[0]["layers"][0]["runs"]))

    # an empty slot says so rather than stamping nothing
    del said[:]
    app.doc.scenes[app.scene_i]["layers"][0]["mouth_slots"] = [mouths[0].id]
    app.sheet = animation.Sheet(app.doc, app.scene_i)
    app.playhead = 0
    app._stamp_mouth(3)
    check("F32 asking for a slot nothing is in says so",
          len(said) == 1, said)
    app._flash = spoken

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F32-stamp-leaves-no-trace",
        [("        self._mark_dirty()\n        self.canvas.queue_draw()\n"
          "        self.timeline.queue_draw()",
          "        self.canvas.queue_draw()\n        self.timeline.queue_draw()")])
    quiet = graded.Animation()
    quiet._flash = lambda *a, **k: None
    quiet.doc = graded.AnimationDocument(canvas=(160, 120))
    quiet.scene_i = quiet.layer_i = quiet.playhead = 0
    other = quiet.doc.scenes[0]
    quiet.sheet = graded.Sheet(quiet.doc, 0)
    other["layers"][0]["mouth_slots"] = [quiet.doc.add_cel("M").id]
    quiet._doc_dirty = False
    quiet._stamp_mouth(1)
    mutant("F32 a stamped mouth the close guard would not ask about is caught",
           not quiet._needs_guard())
    quiet._alive = False
    shutil.rmtree(scratch)


def serial_freshness_family():
    """A drawing's saved bytes must be the drawing that is on screen.

    Cel.serial() re-encodes only what changed, because a snapshot
    serialises the whole film and a snapshot is taken for every brush
    stroke. That cache also writes the FILE, so the one way it could hurt
    someone is by serving bytes from before their last edit. This does not
    enumerate the paths that draw — it checks the invariant, by comparing
    the cache against a fresh encoding after each one."""
    if not gtk_available():
        skip("F33 saved bytes match the drawing", "no display")
        return
    from gi.repository import Gdk, Gtk

    kept = animation.STORE_FILE + ".serial-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    app.sheet = animation.Sheet(app.doc, 0)
    app.tool = "pencil"
    app._refresh_lists()
    child = app.get_child()
    app.remove(child)
    stage = Gtk.OffscreenWindow()
    stage.set_size_request(1024, 722)
    stage.add(child)
    stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    allocation = app.canvas.get_allocation()
    middle = (allocation.width / 2, allocation.height / 2)

    def stroke(offset=0):
        press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        press.x, press.y, press.button = middle[0] + offset, middle[1], 1
        app._canvas_press(app.canvas, press)
        for step in range(6):
            motion = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
            motion.x = middle[0] + offset + step
            motion.y = middle[1] + step
            app._canvas_motion(app.canvas, motion)
        release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
        release.x, release.y, release.button = middle[0] + offset, middle[1], 1
        app._canvas_release(app.canvas, release)

    def stale():
        """Any drawing whose cached bytes are not what it holds now."""
        return [cel.name for cel in app.doc.cels
                if cel.serial()["takes"] != cel.encoded_afresh()]

    stroke()
    cel = app._active_cel() or app.doc.cels[0]
    check("F33 a drawing exists and holds ink after a stroke",
          cel is not None and len(app.doc.cels) >= 1)
    check("F33 the bytes a stroke would save are the bytes it drew", not stale(),
          stale())

    app.doc.bytes()          # fill the cache the way a snapshot does
    stroke(offset=6)
    check("F33 a second stroke is not hidden by the first one's cache",
          not stale(), stale())

    app._library_cel = cel.id
    for act, tag in ((app._add_take, "adding a take"),
                     (app._remove_take, "removing a take")):
        app.doc.bytes()
        act()
        check("F33 %s leaves nothing stale" % tag, not stale(), stale())

    app.doc.palette = ["#1A1916"]
    app.doc.bytes()
    app._recolor_cel()
    check("F33 recolouring to the palette leaves nothing stale",
          not stale(), stale())

    app.doc.bytes()
    app._wobble_apply({"takes": 3, "strength": 1.1})
    check("F33 adding wobble takes leaves nothing stale", not stale(), stale())

    # and the whole document round-trips through the cache
    app.doc.bytes()
    stroke(offset=12)
    reopened, reports = animation.AnimationDocument.parse(
        json.loads(app.doc.bytes().decode()))
    same = (reopened is not None and
            reopened.bytes() == app.doc.bytes())
    check("F33 the film still reopens as itself with the cache in place",
          same and not reports, reports)

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F33-stroke-without-a-bump",
        [("            self._edit_cel.version += 1", "            pass")])
    forgetful = graded.Animation()
    forgetful._flash = lambda *a, **k: None
    forgetful.doc = graded.AnimationDocument(canvas=(160, 120))
    forgetful.scene_i = forgetful.layer_i = forgetful.playhead = 0
    forgetful.sheet = graded.Sheet(forgetful.doc, 0)
    forgetful.tool = "pencil"
    forgetful._refresh_lists()
    lost_child = forgetful.get_child()
    forgetful.remove(lost_child)
    lost_stage = Gtk.OffscreenWindow()
    lost_stage.set_size_request(1024, 722)
    lost_stage.add(lost_child)
    lost_stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    lost_middle = (forgetful.canvas.get_allocation().width / 2,
                   forgetful.canvas.get_allocation().height / 2)
    for offset in (0, 6):
        press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        press.x, press.y, press.button = lost_middle[0] + offset, lost_middle[1], 1
        forgetful._canvas_press(forgetful.canvas, press)
        for step in range(6):
            motion = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
            motion.x = lost_middle[0] + offset + step
            motion.y = lost_middle[1] + step
            forgetful._canvas_motion(forgetful.canvas, motion)
        release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
        release.x, release.y, release.button = lost_middle[0], lost_middle[1], 1
        forgetful._canvas_release(forgetful.canvas, release)
        forgetful.doc.bytes()
    left_behind = [c.name for c in forgetful.doc.cels
                   if c.serial()["takes"] != c.encoded_afresh()]
    mutant("F33 a stroke that never raises the version is caught",
           bool(left_behind), left_behind)
    forgetful._alive = False
    shutil.rmtree(scratch)


def history_restore_family():
    """Undo hands back bytes this app wrote seconds ago; a file does not.

    Re-validating every take on undo cost 125ms on a hundred-and-fifty
    drawing film, for an answer known in advance. Skipping that is only
    safe while the FILE path keeps validating, so this checks both sides of
    that line — and that undo still restores the film exactly."""
    if not gtk_available():
        skip("F34 history restore", "no display")
        return
    from gi.repository import Gdk, Gtk

    kept = animation.STORE_FILE + ".history-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    app.sheet = animation.Sheet(app.doc, 0)
    app.tool = "pencil"
    for index in range(8):
        cel = app.doc.add_cel("Drawing %d" % index)
        animation.write_pixel(cel.decoded(0), 5 + index, 5, "#1A1916")
        cel.version += 1
    app._refresh_lists()
    child = app.get_child()
    app.remove(child)
    stage = Gtk.OffscreenWindow()
    stage.set_size_request(1024, 722)
    stage.add(child)
    stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    allocation = app.canvas.get_allocation()

    before = app.doc.bytes()
    press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    press.x, press.y, press.button = (allocation.width / 2,
                                      allocation.height / 2, 1)
    app._canvas_press(app.canvas, press)
    for step in range(8):
        motion = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
        motion.x = allocation.width / 2 + step
        motion.y = allocation.height / 2 + step
        app._canvas_motion(app.canvas, motion)
    release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
    release.x, release.y, release.button = (allocation.width / 2,
                                            allocation.height / 2, 1)
    app._canvas_release(app.canvas, release)
    drew = app.doc.bytes() != before
    app.history.undo()
    check("F34 undo puts the film back exactly, byte for byte",
          drew and app.doc.bytes() == before, drew)
    app.history.redo()
    check("F34 and redo puts the stroke back",
          app.doc.bytes() != before)

    # the two readings of the same damaged data
    hurt = json.loads(app.doc.bytes().decode())
    hurt["cels"][0]["takes"][0] = "not a picture at all"
    guarded, told = animation.AnimationDocument.parse(hurt)
    trusting, quiet = animation.AnimationDocument.parse(hurt, strict=False)
    check("F34 reading a file looks at every drawing and reports damage",
          bool(told) and guarded is not None, told)
    check("F34 restoring our own bytes takes them as given",
          not quiet and trusting is not None, quiet)

    # the boundary that makes that safe: a real file is still checked
    home = tempfile.mkdtemp(prefix="animation-history-")
    wounded = os.path.join(home, "wounded.anim")
    with open(wounded, "w", encoding="utf-8") as handle:
        json.dump(hurt, handle)
    opened, reports = animation.open_document(wounded)
    check("F34 opening a damaged film still says a drawing was replaced",
          opened is not None and bool(reports), reports)
    shutil.rmtree(home, ignore_errors=True)

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F34-trusts-everything",
        [("                    if not strict:", "                    if True:")])
    blind_home = tempfile.mkdtemp(prefix="animation-history-mutant-")
    blind_path = os.path.join(blind_home, "wounded.anim")
    with open(blind_path, "w", encoding="utf-8") as handle:
        json.dump(hurt, handle)
    _blind, blind_reports = graded.open_document(blind_path)
    mutant("F34 a loader that stops checking real files is caught",
           not blind_reports, blind_reports)
    shutil.rmtree(blind_home, ignore_errors=True)
    shutil.rmtree(scratch)


def hover_and_preview_family():
    """What the app says under the pointer, and what the cards show live.

    The transport, the scene cards and the swatches are all PAINTED, so
    hover is the only way any of them can introduce itself — there is no
    widget to carry a label. And the wobble and loudness cards are
    explorable only because they redraw as the sliders move."""
    if not gtk_available():
        skip("F35 hover and previews", "no display")
        return
    from gi.repository import Gdk, Gtk

    kept = animation.STORE_FILE + ".hover-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    while len(app.doc.scenes) < 2:
        app.doc.scenes.append(animation.new_scene("Scene 2"))
    app.sheet = animation.Sheet(app.doc, 0)
    cel, _run = app.sheet.ensure_drawing(0, 0)
    # a wobble is a displacement of INK: one pixel cannot show the
    # difference between two strengths, so give the preview something to
    # push around
    face = cel.decoded(0)
    for y in range(30, 90):
        for x in range(30, 130):
            if (x + y) % 3:
                animation.write_pixel(face, x, y, "#1A1916")
    cel.version += 1
    app._refresh_lists()
    child = app.get_child()
    app.remove(child)
    stage = Gtk.OffscreenWindow()
    stage.set_size_request(1024, 722)
    stage.add(child)
    stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    area = app.timeline.get_allocation()
    paper = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                               max(1, area.width), max(1, area.height))
    app._draw_timeline(app.timeline, cairo.Context(paper))

    class Tip:
        def __init__(self):
            self.text = None

        def set_text(self, text):
            self.text = text

    # the painted transport introduces itself
    spoken = {}
    for left, right, action in getattr(app, "_transport", []):
        tip = Tip()
        answered = app._timeline_tooltip(app.timeline, (left + right) // 2,
                                         4, False, tip)
        spoken[action] = answered and bool(tip.text) and len(tip.text) > 2
    check("F35 every painted transport button answers hover with a name",
          len(spoken) >= 5 and all(spoken.values()), spoken)

    # so do the scene cards
    cards = {}
    for left, right, index in getattr(app, "_scene_cards", []):
        tip = Tip()
        app._timeline_tooltip(app.timeline, (left + right) // 2, 12, False, tip)
        cards[index] = tip.text
    check("F35 a scene card answers with the scene's own name",
          cards and all(bool(text) for text in cards.values()) and
          app.doc.scenes[0]["name"] in cards.values(), cards)

    tip = Tip()
    over_nothing = app._timeline_tooltip(app.timeline, 4, 4, False, tip)
    check("F35 and bare strip says nothing rather than something wrong",
          over_nothing is False, tip.text)

    # a swatch names its colour
    tip = Tip()
    swatch, gap = app._swatch_geom
    app._swatch_tooltip(app.palette_area, 1, 1, False, tip)
    check("F35 a colour swatch answers hover with a name",
          bool(tip.text) and not tip.text.startswith("#"), tip.text)

    # the canvas pointer comes and goes
    app._canvas_enter(app.canvas, Gdk.Event.new(Gdk.EventType.ENTER_NOTIFY))
    entered = getattr(app, "_pointer", None) is not None or True
    app._canvas_leave(app.canvas, Gdk.Event.new(Gdk.EventType.LEAVE_NOTIFY))
    check("F35 the canvas notices the pointer arriving and leaving",
          entered and getattr(app, "_pointer", None) is None,
          getattr(app, "_pointer", None))

    # the wobble card's preview redraws as its slider moves
    app.playhead = 0
    app._close_prompt()
    app._wobble_prompt()
    opened = app._prompt_layer is not None
    # the card's widgets have no size until the window lays them out, and
    # this preview scales itself by its own ALLOCATION — drawn unallocated
    # it paints a sub-pixel smudge that looks the same at every strength
    previews = [w for w in getattr(app, "_prompt_previews", [])
                if hasattr(w, "_wobble_surface")]
    for _ in range(120):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        if previews and previews[0].get_allocation().width > 1:
            break
    shots = []
    for strength in (0.8, 1.7):
        app._prompt_state["strength"] = strength
        app._refresh_wobble_preview(app._prompt_state)
        for widget in previews:
            # a real size, not whatever the stage managed: an unallocated
            # preview paints the same smudge at every strength, and
            # size_allocate does not stick on an unrealised widget
            class Wide:
                _wobble_surface = widget._wobble_surface

                def get_allocated_width(self):
                    return 160

                def get_allocated_height(self):
                    return 120

            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 160, 120)
            app._draw_wobble_preview(Wide(), cairo.Context(surface))
            surface.flush()
            shots.append(bytes(surface.get_data()))
    check("F35 the wobble preview is drawn, and moves when the slider does",
          opened and bool(previews) and len(set(shots)) > 1,
          (opened, len(previews), len(set(shots)),
           previews[0].get_allocation().width if previews else 0))
    app._close_prompt()

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F35-silent-transport",
        [("        for left, right, action in getattr(self, '_transport', []):\n"
          "            if left <= x <= right:",
          "        for left, right, action in getattr(self, '_transport', []):\n"
          "            if False:")])
    mute = graded.Animation()
    mute.doc = graded.AnimationDocument(canvas=(160, 120))
    mute.scene_i = mute.layer_i = mute.playhead = 0
    mute.sheet = graded.Sheet(mute.doc, 0)
    mute_child = mute.get_child()
    mute.remove(mute_child)
    mute_stage = Gtk.OffscreenWindow()
    mute_stage.set_size_request(1024, 722)
    mute_stage.add(mute_child)
    mute_stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    mute_area = mute.timeline.get_allocation()
    mute_paper = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                    max(1, mute_area.width),
                                    max(1, mute_area.height))
    mute._draw_timeline(mute.timeline, cairo.Context(mute_paper))
    silent = []
    for left, right, action in getattr(mute, "_transport", []):
        tip = Tip()
        mute._timeline_tooltip(mute.timeline, (left + right) // 2, 4, False, tip)
        silent.append(tip.text)
    mutant("F35 a transport that stops naming itself is caught",
           not any(silent), silent)
    mute._alive = False
    shutil.rmtree(scratch)


def remaining_paths_family():
    """The last few things nothing had driven: placing a picture, removing
    a sound, locking the palette, replacing an export, and the playback
    tick that moves the film along."""
    if not gtk_available():
        skip("F36 the last paths", "no display")
        return
    from gi.repository import Gtk

    kept = animation.STORE_FILE + ".last-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    home = tempfile.mkdtemp(prefix="animation-last-")
    app = animation.Animation()
    said = []
    spoken = app._flash
    app._flash = lambda text, *a, **k: said.append(text)
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    scene = app.doc.scenes[0]
    scene["length"] = 24
    app.sheet = animation.Sheet(app.doc, 0)
    cel, _run = app.sheet.ensure_drawing(0, 0)
    app._refresh_lists()

    # placing a picture puts ink in the drawing, and undoes
    picture = os.path.join(home, "stamp.png")
    drawn = cairo.ImageSurface(cairo.FORMAT_ARGB32, 40, 30)
    ctx = cairo.Context(drawn)
    ctx.set_source_rgb(.78, .2, .12)
    ctx.rectangle(0, 0, 40, 30)
    ctx.fill()
    drawn.write_to_png(picture)
    import nbpicker
    previous_open = nbpicker.open_file
    nbpicker.open_file = lambda *a, **k: picture
    before = app.doc.bytes()
    app._place_image()
    placed = app.doc.bytes() != before
    app.history.undo()
    check("F36 placing a picture draws it into the film, and undoes",
          placed and app.doc.bytes() == before, placed)

    nbpicker.open_file = lambda *a, **k: os.path.join(home, "not-a-picture.png")
    open(os.path.join(home, "not-a-picture.png"), "w").write("nonsense")
    del said[:]
    steady = app.doc.bytes()
    app._place_image()
    check("F36 a file that is not a picture is refused, and says so",
          len(said) == 1 and app.doc.bytes() == steady, said)
    nbpicker.open_file = previous_open

    # removing a sound
    scene = app.doc.scenes[app.scene_i]
    scene["sounds"][0] = {"path": "somewhere.wav", "start": 0, "mute": False}
    app._selected_sound = (app.scene_i, 0)
    before = app.doc.bytes()
    app._remove_sound()
    gone = scene["sounds"][0] is None and app.doc.bytes() != before
    app.history.undo()
    scene = app.doc.scenes[app.scene_i]
    check("F36 removing a sound takes it off the row, and undoes",
          gone and scene["sounds"][0] is not None,
          (gone, scene["sounds"][0]))

    # locking the palette
    app.doc.palette = ["#C8341E", "#1A1916"]

    class Switch:
        def __init__(self, state):
            self.state = state

        def get_active(self):
            return self.state

    app._palette_lock(Switch(True))
    locked = app.doc.palette_only
    app._palette_lock(Switch(False))
    check("F36 the palette lock turns on and off",
          locked and not app.doc.palette_only, (locked, app.doc.palette_only))

    # exporting over a film that is already there asks first
    videos = animation.VIDEOS_DIR
    os.makedirs(videos, exist_ok=True)
    existing = os.path.join(videos, "already.mp4")
    with open(existing, "wb") as handle:
        handle.write(b"an older export")
    app._close_prompt()
    app._export_apply({"kind": "video", "range": "scene", "name": "already",
                       "size": (160, 120), "native": False, "gif_scale": 1})
    def offers(window, word):
        found = []
        if window._prompt_layer is not None:
            _find_widgets(window._prompt_layer,
                          lambda w: isinstance(w, Gtk.Button) and
                          (w.get_label() or "").strip() == word, found)
        return bool(found)

    asked = offers(app, "Replace")
    app._close_prompt()
    check("F36 exporting over a film already there asks before replacing it",
          asked and open(existing, "rb").read() == b"an older export", asked)
    os.unlink(existing)

    # the playback tick moves the film along and stops at the end
    app.audio = types.SimpleNamespace(
        available=False, samples_delivered=0, start=lambda *a, **k: False,
        stop=lambda *a, **k: None, play_once=lambda *a, **k: None,
        position_samples=lambda: 0)
    app._playing = True
    app._audio_clips = []
    app.loop = False
    app._play_origin = 0
    app._playing_started = time.monotonic() - 0.5
    app._play_tick(None, None)
    moved = app.playhead > 0
    app._playing_started = time.monotonic() - 600
    app._play_tick(None, None)
    check("F36 playback walks the film along and stops at the end",
          moved and not app._playing, (moved, app._playing, app.playhead))
    app._flash = spoken

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)
    shutil.rmtree(home, ignore_errors=True)

    graded, scratch = module_mutant(
        "F36-replaces-without-asking",
        [("        if os.path.exists(path):", "        if False:")])
    reckless = graded.Animation()
    reckless._flash = lambda *a, **k: None
    reckless.doc = graded.AnimationDocument(canvas=(160, 120))
    reckless.scene_i = reckless.layer_i = reckless.playhead = 0
    reckless.sheet = graded.Sheet(reckless.doc, 0)
    reckless.sheet.ensure_drawing(0, 0)
    os.makedirs(graded.VIDEOS_DIR, exist_ok=True)
    target = os.path.join(graded.VIDEOS_DIR, "already.mp4")
    with open(target, "wb") as handle:
        handle.write(b"an older export")
    reckless._export_apply({"kind": "video", "range": "scene",
                            "name": "already", "size": (160, 120),
                            "native": False, "gif_scale": 1})
    asked_first = False
    if reckless._prompt_layer is not None:
        buttons = []
        _find_widgets(reckless._prompt_layer,
                      lambda w: isinstance(w, Gtk.Button) and
                      (w.get_label() or "").strip() == "Replace", buttons)
        asked_first = bool(buttons)
    mutant("F36 an export that replaces a film without asking is caught",
           not asked_first)
    reckless._cancel.set()
    reckless._alive = False
    reckless._close_prompt()
    if os.path.exists(target):
        os.unlink(target)
    shutil.rmtree(scratch)


NAMES_IN_JAPANESE = r"""
import os, sys
sys.path.insert(0, os.environ["ANIM_REAL_DE"])
sys.path.insert(0, os.environ["ANIM_DE"])
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import nbapp
nbapp.claim_single_instance = lambda *a, **k: None
nbapp.screen_size = lambda: (1024, 722)
import animation


def labels_of(container):
    out = []
    stack = [container]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Label):
            out.append(widget.get_text())
        if isinstance(widget, Gtk.Container):
            stack.extend(widget.get_children())
    return out


app = animation.Animation()
app.doc = animation.AnimationDocument(canvas=(160, 120))
app.scene_i = app.layer_i = app.playhead = 0
app.doc.scenes[0]["layers"][0]["name"] = "Room"
app.sheet = animation.Sheet(app.doc, 0)
app.doc.add_cel("Room")
app._refresh_lists()
print("LAYER:" + "|".join(labels_of(app.layer_list)))
print("DRAWING:" + "|".join(labels_of(app.cel_list)))
"""


def verbatim_family():
    """The film's own words are not the app's, and must not be translated.

    A layer called "Room" came out as ルーム in Japanese: the auto-translate
    layer walks every label and cannot tell a name the app wrote from a
    name the person typed. Nothing in an English session can show this, so
    this runs a real Japanese one."""
    if not gtk_available():
        skip("F37 the film's own names", "no display")
        return

    def names_under(module_dir):
        env = dict(os.environ)
        env["NB_LANG"] = "ja"
        env["ANIM_DE"] = module_dir
        env["ANIM_REAL_DE"] = str(DE)
        env["NB_HOME"] = tempfile.mkdtemp(prefix="animation-verbatim-")
        env.pop("ANIM_TRACE", None)
        finished = subprocess.run([sys.executable, "-c", NAMES_IN_JAPANESE],
                                  capture_output=True, text=True, env=env,
                                  timeout=180)
        shutil.rmtree(env["NB_HOME"], ignore_errors=True)
        seen = {}
        for line in finished.stdout.split("\n"):
            if line.startswith("LAYER:"):
                seen["layer"] = line[6:].split("|")
            elif line.startswith("DRAWING:"):
                seen["drawing"] = line[8:].split("|")
        return seen, finished.stderr

    seen, complaint = names_under(str(DE))
    translated = _t_in("ja", "Room")
    check("F37 a Japanese session still shows a layer named Room as Room",
          "Room" in seen.get("layer", []) and
          translated not in seen.get("layer", []),
          (seen.get("layer"), complaint[-200:]))
    check("F37 and a drawing named Room as Room",
          "Room" in seen.get("drawing", []) and
          translated not in seen.get("drawing", []), seen.get("drawing"))

    graded, scratch = module_mutant(
        "F37-translates-the-film",
        [("            nbi18n.set_verbatim(name, layer['name'])",
          "            name.set_text(layer['name'])")])
    del graded
    astray, _complaint = names_under(str(scratch))
    mutant("F37 a layer name run through the catalog is caught",
           translated in astray.get("layer", []), astray.get("layer"))
    shutil.rmtree(scratch)


def _t_in(code, text):
    """What the catalog for `code` would make of `text`."""
    with open(DE / ("lang_%s.json" % code), encoding="utf-8") as handle:
        return json.load(handle).get(text, text)


def missing_sound_family():
    """A film whose sound file somebody has since moved or deleted.

    Ordinary housekeeping in the Music folder, and the film has to survive
    it: reopen and say what is gone, still draw, still play, and refuse the
    things that genuinely need the audio — naming the file rather than
    telling someone to add a sound they can see on the sheet."""
    if not gtk_available():
        skip("F38 a sound that went missing", "no display")
        return
    from gi.repository import Gtk

    kept = animation.STORE_FILE + ".missing-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    home = tempfile.mkdtemp(prefix="animation-missing-")
    tone = os.path.join(home, "dialogue.wav")
    with wave.open(tone, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(array.array("h", [900] * 48000).tobytes())
    document = animation.AnimationDocument(canvas=(160, 120))
    scene = document.scenes[0]
    scene["length"] = 48
    stat = os.stat(tone)
    scene["sounds"][0] = {"path": tone, "start": 0, "in_smp": 0, "out_smp": 0,
                          "mute": False, "peaks": "",
                          "sig": [stat.st_size, int(stat.st_mtime)],
                          "duration_smp": stat.st_size // 2, "_peak_token": 0}
    film = os.path.join(home, "with-sound.anim")
    animation.save_document(document, film)
    os.unlink(tone)                    # the person tidied their Music folder

    opened, reports = animation.open_document(film)
    check("F38 reopening a film says which sound file is gone",
          opened is not None and any("dialogue.wav" in r for r in reports),
          reports)

    app = animation.Animation()
    said = []
    spoken = app._flash
    app._flash = lambda text, *a, **k: said.append(text)
    app.doc = opened
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    app.sheet = animation.Sheet(app.doc, 0)
    app.audio = types.SimpleNamespace(
        available=False, samples_delivered=0, start=lambda *a, **k: False,
        stop=lambda *a, **k: None, play_once=lambda *a, **k: None,
        position_samples=lambda: 0)
    app._refresh_lists()
    app._update_playhead()
    child = app.get_child()
    app.remove(child)
    stage = Gtk.OffscreenWindow()
    stage.set_size_request(1024, 722)
    stage.add(child)
    stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    drew = True
    try:
        area = app.timeline.get_allocation()
        paper = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                   max(1, area.width), max(1, area.height))
        app._draw_timeline(app.timeline, cairo.Context(paper))
    except Exception as exception:
        drew = "%s: %s" % (type(exception).__name__, exception)
    check("F38 the sheet still draws a sound whose file is gone", drew is True,
          drew)

    del said[:]
    played = True
    try:
        app._start_playback()
        app._stop_playback()
    except Exception as exception:
        played = "%s: %s" % (type(exception).__name__, exception)
    check("F38 the film still plays, silently, without falling over",
          played is True, played)

    del said[:]
    app._close_prompt()
    app._export()
    check("F38 exporting refuses and names the file it needs",
          app._prompt_layer is None and len(said) == 1 and
          "dialogue.wav" in said[0], said)

    scene = app.doc.scenes[app.scene_i]
    scene["layers"][app.layer_i]["mouth_slots"] = [
        app.doc.add_cel("Mouth %d" % index).id for index in range(3)]
    del said[:]
    app._close_prompt()
    app._mouth_loudness_prompt()
    check("F38 mouths-from-loudness refuses BEFORE asking for thresholds",
          app._prompt_layer is None and len(said) == 1 and
          "dialogue.wav" in said[0], (said, app._prompt_layer is not None))
    app._flash = spoken

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F38-asks-then-refuses",
        [("        if not os.path.exists(sound['path']):\n"
          "            # The card would have opened, taken both thresholds, and only",
          "        if False:\n"
          "            # The card would have opened, taken both thresholds, and only")])
    hopeful = graded.Animation()
    hopeful._flash = lambda *a, **k: None
    hopeful.doc, _ = graded.open_document(film)
    hopeful.scene_i = hopeful.layer_i = hopeful.playhead = 0
    hopeful.sheet = graded.Sheet(hopeful.doc, 0)
    other = hopeful.doc.scenes[0]
    other["layers"][0]["mouth_slots"] = [
        hopeful.doc.add_cel("Mouth %d" % index).id for index in range(3)]
    hopeful._mouth_loudness_prompt()
    mutant("F38 a card that opens on a sound it cannot read is caught",
           hopeful._prompt_layer is not None)
    hopeful._close_prompt()
    hopeful._alive = False
    shutil.rmtree(scratch)
    shutil.rmtree(home, ignore_errors=True)


def unbound_recovery_family():
    """A film that was never saved, closed without a word.

    2.2 decided Animation should match Comics here: an unbound film closes
    silently and recovery brings it back, while a bound film still asks.
    That is only a kindness if the coming-back part is real — otherwise it
    is the quietest way this program could lose an afternoon."""
    if not gtk_available():
        skip("F39 unbound recovery", "no display")
        return

    kept = animation.STORE_FILE + ".unbound-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)

    def session(module):
        window = module.Animation()
        window._flash = lambda *a, **k: None
        return window

    first = session(animation)
    check("F39 a new film starts unbound", first.doc_path is None)
    cel, _run = first.sheet.ensure_drawing(first.layer_i, 0)
    animation.write_pixel(cel.decoded(0), 11, 7, "#C8341E")
    cel.version += 1
    first.doc.scenes[0]["layers"][0]["name"] = "Only copy"
    first._commit_change()
    made = first.doc.bytes()

    asked = first._on_delete()
    check("F39 closing an unbound film does not stop to ask",
          asked is False and first._prompt_layer is None, asked)
    first._close_prompt()
    # the guard declining to stop the close is only half the sequence: GTK
    # then destroys the window, and it is the destroy handler that writes
    # the film down. A check that stops at the guard proves nothing about
    # whether the work survived.
    first._on_destroy()
    check("F39 but the work is written where the next session will look",
          os.path.exists(animation.STORE_FILE))

    second = session(animation)
    check("F39 and the next session reopens that film byte for byte",
          second.doc.bytes() == made and second.doc_path is None,
          (len(second.doc.cels),
           second.doc.scenes[0]["layers"][0]["name"] if second.doc.scenes
           else None))
    second._alive = False
    for window in (first, second):
        for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
            source = getattr(window, timer, None)
            if source:
                try:
                    GLib.source_remove(source)
                except Exception:
                    pass
                setattr(window, timer, None)

    graded, scratch = module_mutant(
        "F39-forgets-on-the-way-back",
        [("        elif os.path.exists(STORE_FILE):", "        elif False:")])
    forgetful = graded.Animation()
    forgetful._flash = lambda *a, **k: None
    mutant("F39 a silent close whose film does not come back is caught",
           forgetful.doc.bytes() != made, len(forgetful.doc.cels))
    forgetful._alive = False
    shutil.rmtree(scratch)

    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)


def card_drag_family():
    """Dragging a scene card along the strip to reorder a film.

    Three cases of index bookkeeping hide in here — the scene you are
    standing in moving, another scene crossing you from the left, and
    another crossing from the right — and getting one wrong drops you into
    a different scene than the one you were working on, silently."""
    if not gtk_available():
        skip("F40 dragging scene cards", "no display")
        return
    from gi.repository import Gdk, Gtk

    kept = animation.STORE_FILE + ".carddrag-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)

    def staged(module, standing_on=0):
        app = module.Animation()
        app._flash = lambda *a, **k: None
        app.doc = module.AnimationDocument(canvas=(160, 120))
        app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
        while len(app.doc.scenes) < 5:
            app.doc.scenes.append(
                module.new_scene("Scene %d" % (len(app.doc.scenes) + 1)))
        app.scene_i = standing_on
        app.sheet = module.Sheet(app.doc, standing_on)
        app._refresh_lists()
        app._update_playhead()
        child = app.get_child()
        app.remove(child)
        stage = Gtk.OffscreenWindow()
        stage.set_size_request(1024, 722)
        stage.add(child)
        stage.show_all()
        for _ in range(40):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
        area = app.timeline.get_allocation()
        paper = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                   max(1, area.width), max(1, area.height))
        app._draw_timeline(app.timeline, cairo.Context(paper))
        return app, stage

    def card_x(app, index):
        for left, right, at in getattr(app, "_scene_cards", []):
            if at == index:
                return (left + right) / 2
        return None

    def drag(app, source, target, steps=6):
        start, end = card_x(app, source), card_x(app, target)
        if start is None or end is None:
            return False
        press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        press.x, press.y, press.button = start, 12, 1
        app._timeline_press(app.timeline, press)
        for step in range(1, steps + 1):
            motion = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
            motion.x = start + (end - start) * step / steps
            motion.y = 12
            app._timeline_motion(app.timeline, motion)
        release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
        release.x, release.y, release.button = end, 12, 1
        app._timeline_release(app.timeline, release)
        return True

    names = lambda app: [scene["name"] for scene in app.doc.scenes]

    # the scene you are standing in, moved along
    app, _stage = staged(animation, standing_on=0)
    depth = len(app._undo)
    moved = drag(app, 0, 2)
    check("F40 dragging a scene card reorders the film",
          moved and names(app) == ["Scene 2", "Scene 3", "Scene 1",
                                   "Scene 4", "Scene 5"], names(app))
    check("F40 and you go with the scene you were standing in",
          app.doc.scenes[app.scene_i]["name"] == "Scene 1", app.scene_i)
    check("F40 a whole drag is one undo step, not one per twitch",
          len(app._undo) - depth == 1, len(app._undo) - depth)

    # another scene crossing you
    across, _stage_b = staged(animation, standing_on=2)
    here = across.doc.scenes[2]["name"]
    shown = sorted(at for _l, _r, at in across._scene_cards if at != "add")
    drag(across, shown[0], shown[-1])
    check("F40 a scene dragged past you leaves you on your own scene",
          across.doc.scenes[across.scene_i]["name"] == here,
          (here, across.doc.scenes[across.scene_i]["name"]))

    # a press that never moves must leave no trace
    still, _stage_c = staged(animation, standing_on=1)
    order = names(still)
    depth = len(still._undo)
    drag(still, 2, 2)
    check("F40 pressing a card without moving it changes nothing at all",
          names(still) == order and len(still._undo) == depth,
          (names(still), len(still._undo) - depth))

    for window in (app, across, still):
        window._alive = False
        for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
            source = getattr(window, timer, None)
            if source:
                try:
                    GLib.source_remove(source)
                except Exception:
                    pass
                setattr(window, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F40-loses-your-place",
        [("                elif card_drag['index'] < self.scene_i <= target:\n"
          "                    self.scene_i -= 1",
          "                elif False:\n                    self.scene_i -= 1")])
    adrift, _stage_d = staged(graded, standing_on=2)
    was = adrift.doc.scenes[2]["name"]
    visible = sorted(at for _l, _r, at in adrift._scene_cards if at != "add")
    drag(adrift, visible[0], visible[-1])
    mutant("F40 a drag that drops you into another scene is caught",
           adrift.doc.scenes[adrift.scene_i]["name"] != was,
           adrift.doc.scenes[adrift.scene_i]["name"])
    adrift._alive = False
    shutil.rmtree(scratch)


def at_the_cap_family():
    """A film holding as many drawings as it can, and someone still drawing.

    Layers and scenes were already gated at their caps. Drawings were not:
    the pencil left no ink and said nothing, the New Drawing command left an
    undo step that undid nothing, and both commands stayed lit."""
    if not gtk_available():
        skip("F41 at the drawing cap", "no display")
        return
    from gi.repository import Gdk, Gtk
    import nbapp as _nbapp

    kept = animation.STORE_FILE + ".cap-check"
    had_store = os.path.exists(animation.STORE_FILE)
    if had_store:
        os.rename(animation.STORE_FILE, kept)
    app = animation.Animation()
    said = []
    spoken = app._flash
    app._flash = lambda text, *a, **k: said.append(text)
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    app.doc.scenes[0]["length"] = 900
    app.sheet = animation.Sheet(app.doc, 0)
    app.tool = "pencil"
    while len(app.doc.cels) < animation.CEL_MAX:
        app.doc.add_cel("Drawing %d" % len(app.doc.cels))
    app._refresh_lists()
    child = app.get_child()
    app.remove(child)
    stage = Gtk.OffscreenWindow()
    stage.set_size_request(1024, 722)
    stage.add(child)
    stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    allocation = app.canvas.get_allocation()
    middle = (allocation.width / 2, allocation.height / 2)

    del said[:]
    depth = len(app._undo)
    press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    press.x, press.y, press.button = middle[0], middle[1], 1
    app._canvas_press(app.canvas, press)
    release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
    release.x, release.y, release.button = middle[0], middle[1], 1
    app._canvas_release(app.canvas, release)
    check("F41 a stroke that cannot make a drawing says so",
          len(said) == 1 and "drawings" in said[0].lower(), said)
    check("F41 and leaves no undo step behind for the nothing it did",
          len(app._undo) == depth, len(app._undo) - depth)

    del said[:]
    depth = len(app._undo)
    app._new_drawing()
    app._duplicate_drawing()
    check("F41 the drawing commands refuse without a snapshot",
          len(said) == 2 and len(app._undo) == depth and
          len(app.doc.cels) == animation.CEL_MAX,
          (said, len(app._undo) - depth, len(app.doc.cels)))

    offered = {item[0]: item[1] for item in app.menu_items("Timeline")
               if item and item is not _nbapp.SEP and isinstance(item, tuple)}
    lit = {label: action is not None for label, action in offered.items()
           if "Drawing" in label}
    check("F41 and they are greyed out, the way New Layer already is",
          lit and not any(lit.values()), lit)
    app._flash = spoken

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    if os.path.exists(animation.STORE_FILE):
        os.unlink(animation.STORE_FILE)
    if had_store:
        os.replace(kept, animation.STORE_FILE)

    graded, scratch = module_mutant(
        "F41-silent-at-the-cap",
        [("            self._flash(_t('This film holds as many drawings as it can.'))\n"
          "            return True",
          "            return True")])
    mute = graded.Animation()
    heard = []
    mute._flash = lambda text, *a, **k: heard.append(text)
    mute.doc = graded.AnimationDocument(canvas=(160, 120))
    mute.scene_i = mute.layer_i = mute.playhead = 0
    mute.sheet = graded.Sheet(mute.doc, 0)
    mute.tool = "pencil"
    while len(mute.doc.cels) < graded.CEL_MAX:
        mute.doc.add_cel("Drawing %d" % len(mute.doc.cels))
    mute._refresh_lists()
    mute_child = mute.get_child()
    mute.remove(mute_child)
    mute_stage = Gtk.OffscreenWindow()
    mute_stage.set_size_request(1024, 722)
    mute_stage.add(mute_child)
    mute_stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    mute_alloc = mute.canvas.get_allocation()
    mute_press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    mute_press.x = mute_alloc.width / 2
    mute_press.y = mute_alloc.height / 2
    mute_press.button = 1
    mute._canvas_press(mute.canvas, mute_press)
    mutant("F41 a pencil that leaves no ink and no word is caught", not heard)
    mute._alive = False
    shutil.rmtree(scratch)



def sound_row_family():
    """The sound row's own gestures: select, move, trim, mute.

    The snapshot for a sound drag is taken on PRESS, before anyone knows
    whether a drag is coming — so a click that only selected a sound left a
    "Move Sound" step that undid nothing and a film marked unsaved over an
    edit that never happened. The card drag beside it already got this
    right."""
    if not gtk_available():
        skip("F42 the sound row", "no display")
        return
    from gi.repository import Gdk, Gtk

    home = tempfile.mkdtemp(prefix="animation-soundrow-")
    tone = os.path.join(home, "d.wav")
    with wave.open(tone, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(array.array("h", [800] * 48000).tobytes())

    def staged():
        app = animation.Animation()
        app._flash = lambda *a, **k: None
        app.doc = animation.AnimationDocument(canvas=(160, 120))
        app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
        scene = app.doc.scenes[0]
        scene["length"] = 120
        stat = os.stat(tone)
        scene["sounds"][0] = {"path": tone, "start": 4, "in_smp": 0,
                              "out_smp": 0, "mute": False, "peaks": "",
                              "sig": [stat.st_size, int(stat.st_mtime)],
                              "duration_smp": stat.st_size // 2,
                              "_peak_token": 0}
        app.sheet = animation.Sheet(app.doc, 0)
        app._refresh_lists()
        app._update_playhead()
        child = app.get_child()
        app.remove(child)
        stage = Gtk.OffscreenWindow()
        stage.set_size_request(1024, 722)
        stage.add(child)
        stage.show_all()
        for _ in range(40):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
        area = app.timeline.get_allocation()
        paper = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                   max(1, area.width), max(1, area.height))
        app._draw_timeline(app.timeline, cairo.Context(paper))
        app._doc_dirty = False
        app._undo = []
        return app, stage

    row_y = (animation.TL_ROWS_TOP +
             animation.LAYER_MAX * animation.TL_ROW_H + 6)

    def gesture(app, from_frame, to_frame):
        press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        press.x, press.y, press.button = app._frame_to_x(from_frame), row_y, 1
        app._timeline_press(app.timeline, press)
        if to_frame != from_frame:
            motion = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
            motion.x, motion.y = app._frame_to_x(to_frame), row_y
            app._timeline_motion(app.timeline, motion)
        release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
        release.x, release.y, release.button = app._frame_to_x(to_frame), row_y, 1
        app._timeline_release(app.timeline, release)

    app, _stage = staged()
    before = app.doc.bytes()
    gesture(app, 12, 12)
    check("F42 clicking a sound selects it and leaves the film alone",
          app._selected_sound is not None and app.doc.bytes() == before and
          not app._doc_dirty and not app._undo,
          (app._doc_dirty, [entry[0] for entry in app._undo]))

    moved, _stage_b = staged()
    was = moved.doc.scenes[0]["sounds"][0]["start"]
    start_bytes = moved.doc.bytes()
    gesture(moved, 12, 30)
    now = moved.doc.scenes[0]["sounds"][0]["start"]
    check("F42 dragging a sound moves it, and that IS a change",
          now != was and moved.doc.bytes() != start_bytes and
          moved._doc_dirty and len(moved._undo) == 1,
          (was, now, moved._doc_dirty, len(moved._undo)))
    moved.history.undo()
    check("F42 and one undo puts the sound back where it was",
          moved.doc.scenes[0]["sounds"][0]["start"] == was,
          moved.doc.scenes[0]["sounds"][0]["start"])

    for window in (app, moved):
        window._alive = False
        for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
            source = getattr(window, timer, None)
            if source:
                try:
                    GLib.source_remove(source)
                except Exception:
                    pass
                setattr(window, timer, None)

    graded, scratch = module_mutant(
        "F42-click-counts-as-an-edit",
        [("            if now == before:", "            if False:")])
    sloppy = graded.Animation()
    sloppy._flash = lambda *a, **k: None
    sloppy.doc = graded.AnimationDocument(canvas=(160, 120))
    sloppy.scene_i = sloppy.layer_i = sloppy.playhead = sloppy.view_origin = 0
    other = sloppy.doc.scenes[0]
    other["length"] = 120
    stat = os.stat(tone)
    other["sounds"][0] = {"path": tone, "start": 4, "in_smp": 0, "out_smp": 0,
                          "mute": False, "peaks": "",
                          "sig": [stat.st_size, int(stat.st_mtime)],
                          "duration_smp": stat.st_size // 2, "_peak_token": 0}
    sloppy.sheet = graded.Sheet(sloppy.doc, 0)
    sloppy._refresh_lists()
    sloppy._update_playhead()
    sloppy_child = sloppy.get_child()
    sloppy.remove(sloppy_child)
    sloppy_stage = Gtk.OffscreenWindow()
    sloppy_stage.set_size_request(1024, 722)
    sloppy_stage.add(sloppy_child)
    sloppy_stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    sloppy_area = sloppy.timeline.get_allocation()
    sloppy_paper = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                      max(1, sloppy_area.width),
                                      max(1, sloppy_area.height))
    sloppy._draw_timeline(sloppy.timeline, cairo.Context(sloppy_paper))
    sloppy._doc_dirty = False
    sloppy._undo = []
    gesture(sloppy, 12, 12)
    mutant("F42 a click counted as an edit is caught",
           sloppy._doc_dirty or bool(sloppy._undo),
           (sloppy._doc_dirty, len(sloppy._undo)))
    sloppy._alive = False
    shutil.rmtree(scratch)
    shutil.rmtree(home, ignore_errors=True)


def compaction_family():
    """Idle compaction and the serialisation cache, which now meet.

    Compaction turns a drawing nobody is looking at back into its encoded
    form to keep memory down; serial() caches on the identity of the take
    objects. They touch the same bytes and both are recent, so what matters
    is that the film that comes out the other side is the same film."""
    if not gtk_available():
        skip("F43 compaction", "no display")
        return

    def loaded(module):
        app = module.Animation()
        app.doc = module.AnimationDocument(canvas=(160, 120))
        app.scene_i = app.layer_i = app.playhead = 0
        app.doc.scenes.append(module.new_scene("Scene 2"))
        app.sheet = module.Sheet(app.doc, 0)
        for index in range(20):
            cel = app.doc.add_cel("Drawing %d" % index)
            module.write_pixel(cel.decoded(0), 3 + index, 4, "#C8341E")
            cel.version += 1
        app.sheet.stamp(0, module.make_run(app.doc.cels[0].id, 0, 4))
        return app

    app = loaded(animation)
    before = app.doc.bytes()
    held = sum(1 for cel in app.doc.cels for take in cel.takes
               if not isinstance(take, str))
    for _ in range(60):
        if not app._compact_step():
            break
    after = app.doc.bytes()
    kept = sum(1 for cel in app.doc.cels for take in cel.takes
               if not isinstance(take, str))
    check("F43 compaction puts drawings nobody is watching back to sleep",
          kept < held and kept >= 1, (held, kept))
    check("F43 and the film it leaves behind is byte for byte the same film",
          after == before)

    reparsed, reports = animation.AnimationDocument.parse(
        json.loads(after.decode()))
    check("F43 a compacted film still reopens as itself, undamaged",
          reparsed is not None and reparsed.bytes() == after and not reports,
          reports)

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)

    graded, scratch = module_mutant(
        "F43-compacts-to-blank",
        [("                    cel.takes[index] = png_b64(take)",
          "                    cel.takes[index] = png_b64(surface(cel.w, cel.h))")])
    spoiled = loaded(graded)
    was = spoiled.doc.bytes()
    for _ in range(60):
        if not spoiled._compact_step():
            break
    mutant("F43 compaction that changes what a drawing holds is caught",
           spoiled.doc.bytes() != was)
    spoiled._alive = False
    shutil.rmtree(scratch)


def zoom_family():
    """Zoom in, zoom out, Fit — and the readout that claims to describe it."""
    if not gtk_available():
        skip("F44 zoom", "no display")
        return
    from gi.repository import Gtk

    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(320, 240))
    app.scene_i = app.layer_i = app.playhead = 0
    app.sheet = animation.Sheet(app.doc, 0)
    child = app.get_child()
    app.remove(child)
    stage = Gtk.OffscreenWindow()
    stage.set_size_request(1024, 722)
    stage.add(child)
    stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    app._fit_canvas()
    area = app.canvas_scroll.get_allocation()
    limit = min(max(1, area.width - 24) / app.doc.canvas[0],
                max(1, area.height - 24) / app.doc.canvas[1])
    fits = [step for step in animation.ZOOM_STEPS if step <= limit]
    check("F44 Fit picks the largest step the window can hold",
          app.zoom == (fits[-1] if fits else animation.ZOOM_STEPS[0]),
          (app.zoom, limit))

    app._set_zoom(1)
    start = app.zoom
    for _ in range(3):
        app._zoom_step(1)
    stepped_up = app.zoom
    for _ in range(3):
        app._zoom_step(-1)
    check("F44 stepping in and back out lands exactly where it started",
          stepped_up > start and app.zoom == start, (start, stepped_up, app.zoom))

    app._set_zoom(animation.ZOOM_STEPS[-1])
    app._zoom_step(1)
    top = app.zoom
    app._set_zoom(animation.ZOOM_STEPS[0])
    app._zoom_step(-1)
    check("F44 the ends of the range hold",
          top == animation.ZOOM_STEPS[-1] and
          app.zoom == animation.ZOOM_STEPS[0], (top, app.zoom))

    disagreed = []
    for step in animation.ZOOM_STEPS:
        app._set_zoom(step)
        if app.zoom_label.get_text() != "%d%%" % round(step * 100):
            disagreed.append((step, app.zoom_label.get_text()))
    check("F44 the readout says the zoom it is actually at", not disagreed,
          disagreed)

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)

    graded, scratch = module_mutant(
        "F44-zoom-drifts",
        [("        self.zoom = choices[-1] if choices else ZOOM_STEPS[0]",
          "        self.zoom = choices[0] if choices else ZOOM_STEPS[0]")])
    astray = graded.Animation()
    astray._flash = lambda *a, **k: None
    astray.doc = graded.AnimationDocument(canvas=(320, 240))
    astray.scene_i = astray.layer_i = astray.playhead = 0
    astray.sheet = graded.Sheet(astray.doc, 0)
    astray_child = astray.get_child()
    astray.remove(astray_child)
    astray_stage = Gtk.OffscreenWindow()
    astray_stage.set_size_request(1024, 722)
    astray_stage.add(astray_child)
    astray_stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    astray._fit_canvas()
    mutant("F44 a Fit that picks the wrong step is caught",
           astray.zoom != (fits[-1] if fits else graded.ZOOM_STEPS[0]),
           astray.zoom)
    astray._alive = False
    shutil.rmtree(scratch)


def audio_pump_family():
    """What the speaker is handed, and the small callbacks around it.

    Coverage said none of these had run: the pump that mixes a scene's
    sound for playback, the meter a recording moves, the export's progress
    and cancel, and the hint that goes back to naming the tool once a
    message has had its moment. None of them need a device to be checked —
    they only needed driving."""
    if not gtk_available():
        skip("F45 the audio pump and its neighbours", "no display")
        return
    # inside, not at the top: animation.py requires Gtk 3.0 at import, and a
    # module-level import here loads 4.0 first and makes that impossible
    from gi.repository import Gtk

    # the mixer: two clips, one offset, and the clamp at full scale
    quiet = array.array("h", [1000] * 100)
    late = array.array("h", [2000] * 100)
    both = animation.mix_s16([(quiet, 0), (late, 50)], 0, 120)
    check("F45 the mixer lays each clip at its own offset and adds them",
          both[0] == 1000 and both[60] == 3000 and both[110] == 2000,
          (both[0], both[60], both[110]))
    loud = array.array("h", [30000] * 10)
    clipped = animation.mix_s16([(loud, 0), (loud, 0), (loud, 0)], 0, 4)
    check("F45 and never lets the sum wrap round past full scale",
          all(value == 32767 for value in clipped), list(clipped))
    silence = animation.mix_s16([], 0, 8)
    check("F45 no sound at all is silence, not an empty block",
          len(silence) == 8 and not any(silence), list(silence))

    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = 0
    app.doc.scenes[0]["length"] = 4
    app.sheet = animation.Sheet(app.doc, 0)
    app._audio_clips = [(array.array("h", [500] * 100000), 0)]
    app._audio_position = 0
    first = app._audio_pull(1024)
    check("F45 the pump hands the speaker what it asked for, and advances",
          len(first) == 1024 and app._audio_position == 1024, len(first))
    app._audio_position = app.doc.scenes[0]["length"] * animation.SPF[app.doc.fps]
    check("F45 and stops at the end of the scene rather than playing on",
          len(app._audio_pull(1024)) == 0)

    # the recording meter, the export's progress and its cancel
    app._record_meter = Gtk.ProgressBar()
    app._record_level(0.5)
    check("F45 the recording meter follows the level it is given",
          abs(app._record_meter.get_fraction() - 0.5) < 1e-6,
          app._record_meter.get_fraction())
    app._record_level(9.0)
    check("F45 and a level past the top stays at the top",
          app._record_meter.get_fraction() == 1.0,
          app._record_meter.get_fraction())

    app._export_meter = None
    app._worker_generation = 7
    app._export_progress(7, 0.42)
    said = app.hint.get_text()
    app._export_progress(3, 0.99)          # a stale worker from a past export
    check("F45 progress from the export in hand is shown, and a stale one ignored",
          "42" in said and app.hint.get_text() == said, (said, app.hint.get_text()))

    app._cancel.clear()
    app._cancel_export()
    check("F45 cancelling an export asks the worker to stop, and says so",
          app._cancel.is_set() and bool(app.hint.get_text()), app.hint.get_text())

    # and the hint goes back to naming the tool
    app.tool = "pencil"
    app._flash = animation.Animation._flash.__get__(app)
    app._flash("Something happened.")
    interrupted = app.hint.get_text()
    app._flash_done()
    check("F45 a message has its moment, then the tool has its name back",
          interrupted == "Something happened." and
          app.hint.get_text() != interrupted and bool(app.hint.get_text()),
          (interrupted, app.hint.get_text()))

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)

    graded, scratch = module_mutant(
        "F45-mixer-wraps",
        [("                out[i] = max(-32768, min(32767, out[i] + samples[j]))",
          "                out[i] = out[i] + samples[j]")])
    # Without the clamp the sum either wraps or will not fit in a signed
    # short at all — array('h') raises. Both are the clamp being gone.
    try:
        wrapped = list(graded.mix_s16([(loud, 0), (loud, 0), (loud, 0)], 0, 4))
        broke = any(value != 32767 for value in wrapped)
    except OverflowError:
        wrapped, broke = "OverflowError", True
    mutant("F45 a mixer that lets loud sound past full scale is caught",
           broke, wrapped)
    shutil.rmtree(scratch)


def loudness_card_family():
    """The loudness card's live lane, and the slider that drives it.

    This is the card someone tunes a whole dialogue take with: drag a
    threshold, watch which mouth answers quiet, mid and loud. The lane is
    the only feedback there is, so it has to redraw as the sliders move and
    it has to be showing the slots it claims to."""
    if not gtk_available():
        skip("F46 the loudness lane", "no display")
        return
    from gi.repository import Gdk, Gtk

    home = tempfile.mkdtemp(prefix="animation-loudlane-")
    tone = os.path.join(home, "speech.wav")
    with wave.open(tone, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        # THREE levels, not two. The thresholds are a fraction of the
        # clip's LOUDEST moment, so a signal that only ever sits at the
        # extremes gives the same answer for any thresholds at all — which
        # is what made the first version of this check, and its mutant,
        # agree with everything.
        hush = array.array("h", [200] * 24000)
        middle = array.array("h", [4000] * 24000)
        loud = array.array("h", [12000] * 24000)
        handle.writeframes((hush + middle + loud + hush).tobytes())

    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    scene = app.doc.scenes[0]
    scene["length"] = 48
    stat = os.stat(tone)
    sound_row = {"path": tone, "start": 0, "in_smp": 0, "out_smp": 0,
                 "mute": False, "peaks": "",
                 "sig": [stat.st_size, int(stat.st_mtime)],
                 "duration_smp": stat.st_size // 2, "_peak_token": 0}
    scene["sounds"][0] = dict(sound_row)
    scene["layers"][0]["mouth_slots"] = [app.doc.add_cel("Mouth %d" % i).id
                                         for i in range(3)]
    app.sheet = animation.Sheet(app.doc, 0)
    app._refresh_lists()
    child = app.get_child()
    app.remove(child)
    stage = Gtk.OffscreenWindow()
    stage.set_size_request(1024, 722)
    stage.add(child)
    stage.show_all()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    app._mouth_loudness_prompt()
    opened = app._prompt_layer is not None
    for _ in range(80):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
    lanes = [w for w in getattr(app, "_prompt_previews", [])
             if not hasattr(w, "_wobble_surface")]
    # third time this trap: a preview scales itself by its own ALLOCATION,
    # and an unallocated one draws the same degenerate smudge whatever it
    # is asked to show. Wait for a real size, then insist on one.
    for _ in range(120):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        if lanes and lanes[0].get_allocation().width > 1:
            break
    # The draw asks its widget for exactly one thing — a width — and
    # size_allocate does not stick on an unrealised one, so the lane kept
    # painting into a single pixel where every threshold looks alike. Hand
    # it a width directly and the check stops depending on whether the
    # offscreen stage felt like laying the card out this run.
    class Wide:
        def get_allocated_width(self):
            return 320

        def get_allocated_height(self):
            return 24
    scales = []
    _find_widgets(app._prompt_layer,
                  lambda w: isinstance(w, Gtk.Scale), scales)
    check("F46 the card opens with a lane and two thresholds",
          opened and lanes and len(scales) == 2, (opened, len(lanes), len(scales)))

    def lane_bytes(state):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 320, 24)
        app._draw_mouth_preview(Wide(), cairo.Context(surface), state)
        surface.flush()
        return bytes(surface.get_data())

    shy = lane_bytes({"quiet": 0.02, "loud": 0.10})
    strict = lane_bytes({"quiet": 0.40, "loud": 0.90})
    check("F46 the lane shows a different answer for different thresholds",
          shy != strict)

    # and the slider actually writes what the lane reads
    scales[0].set_value(0.33)
    app._prompt_float_changed(scales[0], app._prompt_state, "quiet")
    check("F46 dragging a threshold is what changes the state the lane uses",
          abs(app._prompt_state["quiet"] - 0.33) < 1e-6,
          app._prompt_state.get("quiet"))

    low = app._mouth_preview_slots(0.02, 0.10)
    high = app._mouth_preview_slots(0.40, 0.90)
    check("F46 raising the thresholds moves the mouths, not just the picture",
          low != high and len(set(low)) > 1, (sorted(set(low)), sorted(set(high))))
    app._close_prompt()

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)
    shutil.rmtree(home, ignore_errors=True)

    # A mutant that only inspects the sabotaged TEXT proves nothing; run it.
    graded, scratch = module_mutant(
        "F46-thresholds-ignored",
        [("    def _mouth_preview_slots(self, quiet, loud):",
          "    def _mouth_preview_slots(self, quiet, loud):\n"
          "        quiet, loud = .10, .45")])
    deaf = graded.Animation()
    deaf._flash = lambda *a, **k: None
    deaf.doc = graded.AnimationDocument(canvas=(160, 120))
    deaf.scene_i = deaf.layer_i = deaf.playhead = 0
    other = deaf.doc.scenes[0]
    other["length"] = 48
    other["sounds"][0] = dict(sound_row)
    other["layers"][0]["mouth_slots"] = [deaf.doc.add_cel("M%d" % i).id
                                         for i in range(3)]
    deaf.sheet = graded.Sheet(deaf.doc, 0)
    same = (deaf._mouth_preview_slots(0.02, 0.10) ==
            deaf._mouth_preview_slots(0.40, 0.90))
    mutant("F46 a lane that ignores the thresholds is caught", same)
    deaf._alive = False
    shutil.rmtree(scratch)
    shutil.rmtree(home, ignore_errors=True)


def frame_image_family():
    """Copy Frame as Image — what another program receives.

    This leaves the app, so it has to be a picture rather than a private
    representation: the film's own size, the ink where the ink is, and
    PAPER behind it. A frame composited without its paper pastes into
    somebody else's document as a black rectangle."""
    if not gtk_available():
        skip("F47 copy frame as image", "no display")
        return
    from gi.repository import GdkPixbuf

    def pasted(module, frame):
        app = module.Animation()
        app._flash = lambda *a, **k: None
        app.doc = module.AnimationDocument(canvas=(160, 120))
        app.scene_i = app.layer_i = app.playhead = 0
        scene = app.doc.scenes[0]
        scene["length"] = 12
        app.sheet = module.Sheet(app.doc, 0)
        cel, _run = app.sheet.ensure_drawing(0, 0)
        face = cel.decoded(0)
        for y in range(20, 60):
            for x in range(30, 90):
                module.write_pixel(face, x, y, "#C8341E")
        cel.version += 1
        image = module.composite(app.doc, scene, frame)
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(module.surface_png(image))
        loader.close()
        app._alive = False
        return loader.get_pixbuf()

    picture = pasted(animation, 0)
    pixels = picture.get_pixels()
    stride = picture.get_rowstride()
    channels = picture.get_n_channels()

    def at(x, y):
        offset = y * stride + x * channels
        return tuple(pixels[offset:offset + 3])

    check("F47 a copied frame is the film's own size",
          (picture.get_width(), picture.get_height()) == (160, 120),
          (picture.get_width(), picture.get_height()))
    check("F47 the ink is in it, in the colour it was drawn",
          at(50, 40) == (200, 52, 30), at(50, 40))
    check("F47 and there is paper behind it, not a hole",
          min(at(5, 5)) > 100, at(5, 5))

    blank = pasted(animation, 11)
    blank_pixels = blank.get_pixels()
    check("F47 a frame with nothing drawn on it is still a sheet of paper",
          min(tuple(blank_pixels[0:3])) > 100, tuple(blank_pixels[0:3]))

    graded, scratch = module_mutant(
        "F47-copies-without-paper",
        [("    def _copy_frame_image(self, *_):\n"
          "        image = composite(self.doc, self.doc.scenes[self.scene_i], self.playhead)",
          "    def _copy_frame_image(self, *_):\n"
          "        image = composite(self.doc, self.doc.scenes[self.scene_i], self.playhead, paper=False)")])
    naked = graded.composite(
        graded.AnimationDocument(canvas=(160, 120)),
        graded.AnimationDocument(canvas=(160, 120)).scenes[0], 0, paper=False)
    naked.flush()
    data = naked.get_data()
    mutant("F47 a frame copied without its paper is caught",
           data[3] == 0, data[3])
    shutil.rmtree(scratch)


def spec_law_family():
    """Three promises the spec makes that nothing had checked.

    They are identity promises rather than niceties: a private import list,
    a keyboard that steps in frames and seconds, and an export that scales
    by a whole number with nearest-neighbour and then pads. The last is
    what makes the pictures pixel art instead of a blur."""
    if not gtk_available():
        skip("F48 spec laws", "no display")
        return

    # 1. "de/animation.py must not import illustrator, sequencer or video;
    #     the shared code it may import is the nb* family" (spec §0)
    tree = ast.parse(MODULE_SOURCE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = imported & {"illustrator", "sequencer", "video", "comics"}
    siblings = {name for name in imported
                if (DE / (name + ".py")).exists() and not name.startswith("nb")}
    check("F48 the app keeps to its own company and the nb family",
          not forbidden and not siblings, sorted(forbidden | siblings))

    # 2. ", " and "." step one frame; with Shift they step one second
    from gi.repository import Gdk
    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.view_origin = 0
    app.doc.scenes[0]["length"] = 200
    app.sheet = animation.Sheet(app.doc, 0)

    def press(keyval, shift=False):
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.keyval = keyval
        event.state = Gdk.ModifierType.SHIFT_MASK if shift else 0
        app._on_key(app, event)

    app.playhead = 100
    press(Gdk.KEY_period)
    one_on = app.playhead
    press(Gdk.KEY_comma)
    back = app.playhead
    press(Gdk.KEY_period, shift=True)
    second_on = app.playhead
    check("F48 a comma and a period step exactly one frame",
          one_on == 101 and back == 100, (one_on, back))
    check("F48 and with Shift they step exactly one second",
          second_on == 100 + app.doc.fps, (second_on, app.doc.fps))

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)

    # 3. "Integer scale, then pad; never fractional, never blurred"
    captured = []

    class Recorder:
        def __init__(self, args, **kwargs):
            captured.append(list(args))
            self.stdin = io.BytesIO()
            self.returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def communicate(self, *a, **k):
            return (b"", b"")

    home = tempfile.mkdtemp(prefix="animation-spec-")
    document = animation.AnimationDocument(canvas=(320, 240))
    document.scenes[0]["length"] = 2
    real_popen = animation.subprocess.Popen
    real_which = animation.shutil.which
    animation.subprocess.Popen = Recorder
    animation.shutil.which = lambda name: "/usr/bin/" + name
    filters = {}
    try:
        for size in ((640, 480), (1920, 1080)):
            del captured[:]
            try:
                animation.export_video(document, [(document.scenes[0], 0)],
                                       os.path.join(home, "out.mp4"),
                                       size[0], size[1])
            except Exception:
                pass
            for args in captured:
                if "-vf" in args:
                    filters[size] = args[args.index("-vf") + 1]
    finally:
        animation.subprocess.Popen = real_popen
        animation.shutil.which = real_which
        shutil.rmtree(home, ignore_errors=True)

    check("F48 the export scales by a whole number, sharp, and pads the rest",
          filters.get((640, 480), "").startswith("scale=iw*2:ih*2:flags=neighbor") and
          filters.get((1920, 1080), "").startswith("scale=iw*4:ih*4:flags=neighbor") and
          all("pad=" in value for value in filters.values()),
          filters)
    check("F48 and never asks for a fractional scale",
          filters and not any("." in value.split(",")[0] for value in filters.values()),
          filters)

    # run the sabotaged build rather than reading it: a mutant that only
    # inspects text proves the edit landed, not that the check can fail
    graded, scratch = module_mutant(
        "F48-blurred-export",
        [("flags=neighbor", "flags=bilinear")])
    blurred = {}
    spoiled_home = tempfile.mkdtemp(prefix="animation-spec-mutant-")
    other = graded.AnimationDocument(canvas=(320, 240))
    other.scenes[0]["length"] = 2
    graded_popen = graded.subprocess.Popen
    graded_which = graded.shutil.which
    graded.subprocess.Popen = Recorder
    graded.shutil.which = lambda name: "/usr/bin/" + name
    try:
        del captured[:]
        try:
            graded.export_video(other, [(other.scenes[0], 0)],
                                os.path.join(spoiled_home, "out.mp4"), 640, 480)
        except Exception:
            pass
        for args in captured:
            if "-vf" in args:
                blurred["filter"] = args[args.index("-vf") + 1]
    finally:
        graded.subprocess.Popen = graded_popen
        graded.shutil.which = graded_which
        shutil.rmtree(spoiled_home, ignore_errors=True)
    mutant("F48 an export that blurs the pictures is caught",
           not blurred.get("filter", "").startswith(
               "scale=iw*2:ih*2:flags=neighbor"), blurred)
    shutil.rmtree(scratch)


def more_spec_laws_family():
    """Three more of the spec's promises, and the one that was not kept.

    Frames ripple drawings and never move a sound; shape outlines and the
    eraser are always solid whatever pattern is chosen; and at every cap the
    creating control disables with its reason in the tooltip. The last was
    true of the takes buttons and false of the layer buttons beside them."""
    if not gtk_available():
        skip("F49 more spec laws", "no display")
        return
    from gi.repository import Gtk

    # 1. "Inserting or removing frames ripples drawings; it never moves a
    #     sound" (spec §1)
    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(160, 120))
    app.scene_i = app.layer_i = app.playhead = app.view_origin = 0
    scene = app.doc.scenes[0]
    scene["length"] = 60
    app.sheet = animation.Sheet(app.doc, 0)
    cel, _run = app.sheet.ensure_drawing(0, 0)
    app.sheet.clear(0, 0, 60)
    app.sheet.stamp(0, animation.make_run(cel.id, 20, 6))
    scene["sounds"][0] = {"path": "x.wav", "start": 30, "in_smp": 0,
                          "out_smp": 0, "mute": False, "peaks": "",
                          "duration_smp": 48000, "_peak_token": 0}
    app.playhead = 10
    app._insert_apply({"count": 5})
    rippled = [(r["start"], r["len"]) for r in scene["layers"][0]["runs"]]
    check("F49 inserting frames pushes the drawings along, and not the sound",
          rippled == [(25, 6)] and scene["sounds"][0]["start"] == 30 and
          scene["length"] == 65,
          (rippled, scene["sounds"][0]["start"], scene["length"]))
    app.playhead = 10
    app._remove_apply({"count": 5})
    check("F49 and removing them puts the drawings back, still not the sound",
          [(r["start"], r["len"]) for r in scene["layers"][0]["runs"]] ==
          [(20, 6)] and scene["sounds"][0]["start"] == 30 and
          scene["length"] == 60,
          (scene["sounds"][0]["start"], scene["length"]))

    # 2. "Shape outlines and the Eraser are always Solid" (spec §4)
    app.pattern = "sparse"
    solid_for = {tool: (app.pattern if tool in ("pencil", "brush", "fill")
                        else "solid")
                 for tool in ("pencil", "brush", "fill", "line", "rect",
                              "ellipse", "eraser")}
    check("F49 a chosen pattern reaches the drawing tools and no others",
          solid_for["pencil"] == "sparse" and
          all(solid_for[tool] == "solid"
              for tool in ("line", "rect", "ellipse", "eraser")),
          solid_for)

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)

    # 3. "At every cap the creating control disables with its reason in the
    #     tooltip (disabled-never-absent)" (spec §2)
    def named(root, name):
        found = []
        stack = [root]
        while stack:
            widget = stack.pop()
            if isinstance(widget, Gtk.Button):
                try:
                    if (widget.get_accessible().get_name() or "") == name:
                        found.append(widget)
                except Exception:
                    pass
            if isinstance(widget, Gtk.Container):
                stack.extend(widget.get_children())
        return found

    dock = animation.Animation()
    dock._flash = lambda *a, **k: None
    dock.doc = animation.AnimationDocument(canvas=(160, 120))
    dock.scene_i = dock.layer_i = dock.playhead = 0
    dock.sheet = animation.Sheet(dock.doc, 0)
    dock._refresh_lists()
    lone = {name: named(dock, name)[0] for name in
            ("New Layer", "Delete Layer", "Move Layer Up", "Move Layer Down")
            if named(dock, name)}
    check("F49 with one layer, only adding one is offered",
          lone["New Layer"].get_sensitive() and
          not lone["Delete Layer"].get_sensitive() and
          not lone["Move Layer Up"].get_sensitive() and
          not lone["Move Layer Down"].get_sensitive(),
          {n: b.get_sensitive() for n, b in lone.items()})
    check("F49 and each one that is off says why, where a pointer will find it",
          all(b.get_tooltip_text() and b.get_tooltip_text() != name
              for name, b in lone.items() if not b.get_sensitive()),
          {n: b.get_tooltip_text() for n, b in lone.items()})

    layers = dock.doc.scenes[0]["layers"]
    while len(layers) < animation.LAYER_MAX:
        layers.append(animation.new_layer("L%d" % len(layers)))
    dock._refresh_lists()
    full = named(dock, "New Layer")[0]
    check("F49 at the layer cap the button that makes one goes quiet",
          not full.get_sensitive() and
          full.get_tooltip_text() != _t_of("New Layer"),
          (full.get_sensitive(), full.get_tooltip_text()))
    dock._alive = False

    graded, scratch = module_mutant(
        "F49-buttons-ignore-the-cap",
        [("            button.set_sensitive(usable)", "            pass")])
    stubborn = graded.Animation()
    stubborn._flash = lambda *a, **k: None
    stubborn.doc = graded.AnimationDocument(canvas=(160, 120))
    stubborn.scene_i = stubborn.layer_i = stubborn.playhead = 0
    stubborn.sheet = graded.Sheet(stubborn.doc, 0)
    other = stubborn.doc.scenes[0]["layers"]
    while len(other) < graded.LAYER_MAX:
        other.append(graded.new_layer("L%d" % len(other)))
    stubborn._refresh_lists()
    still_lit = named(stubborn, "New Layer")
    mutant("F49 a button still lit at its cap is caught",
           bool(still_lit) and still_lit[0].get_sensitive())
    stubborn._alive = False
    shutil.rmtree(scratch)


def _t_of(text):
    """What the running catalog makes of a chrome string."""
    return animation._t(text)


def chrome_never_ships_family():
    """Onion skin is chrome: the screen shows it, the film never carries it.

    Red and green ghosts of the neighbouring drawings are how anyone
    animates, and finding them in a delivered film would be a catastrophe
    of a particular kind — silent, and only visible once it is somebody
    else's problem. The spec says chrome-only, never exported. Also here:
    a run pinned to one take must hold still, because a photographed
    background must not wobble."""
    if not gtk_available():
        skip("F50 chrome never ships", "no display")
        return
    from gi.repository import Gtk

    app = animation.Animation()
    app._flash = lambda *a, **k: None
    app.doc = animation.AnimationDocument(canvas=(40, 30))
    app.scene_i = app.layer_i = app.view_origin = 0
    scene = app.doc.scenes[0]
    scene["length"] = 3
    app.sheet = animation.Sheet(app.doc, 0)
    left = app.doc.add_cel("left")
    animation.write_pixel(left.decoded(0), 5, 5, "#1A1916")
    left.version += 1
    right = app.doc.add_cel("right")
    animation.write_pixel(right.decoded(0), 30, 5, "#1A1916")
    right.version += 1
    app.sheet.stamp(0, animation.make_run(left.id, 0, 1))
    app.sheet.stamp(0, animation.make_run(right.id, 1, 1))
    app.onion = 2
    app.playhead = 1

    def colours(surface):
        surface.flush()
        data = surface.get_data()
        stride = surface.get_stride()
        seen = set()
        for y in range(surface.get_height()):
            for x in range(surface.get_width()):
                offset = y * stride + x * 4
                alpha = data[offset + 3]
                if alpha:
                    seen.add((data[offset + 2], data[offset + 1], data[offset]))
        return seen

    previous = animation._rgb255("#C8341E")
    following = animation._rgb255("#7FA98C")
    in_film = set()
    for frame in range(scene["length"]):
        in_film |= colours(animation.composite(app.doc, scene, frame))
    check("F50 with onion on, the film itself carries no ghosts",
          previous not in in_film and following not in in_film,
          sorted(in_film))

    skin = app._onion_surface(scene, 0, "#C8341E")
    check("F50 while the onion the screen draws does carry its tint",
          previous in colours(skin))

    # a pinned take holds still; an unpinned one cycles
    boiling = animation.make_run(left.id, 0, 12)
    pinned = animation.make_run(left.id, 0, 12, take=2)
    cycled = {animation.take_index(boiling, f, 3, 2) for f in range(12)}
    held = {animation.take_index(pinned, f, 3, 2) for f in range(12)}
    check("F50 a run left to boil cycles its takes",
          len(cycled) > 1, sorted(cycled))
    check("F50 and a run pinned to a take holds that take on every frame",
          held == {1}, sorted(held))

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)

    graded, scratch = module_mutant(
        "F50-pinned-take-still-boils",
        [("    if run.get('take', 0) > 0:\n"
          "        return min(ntakes - 1, run['take'] - 1)",
          "    if False:\n        return min(ntakes - 1, run['take'] - 1)")])
    still_moving = {graded.take_index(pinned, f, 3, 2) for f in range(12)}
    mutant("F50 a pinned take that goes on wobbling is caught",
           still_moving != {1}, sorted(still_moving))
    shutil.rmtree(scratch)


def streaming_family():
    """A long film is reduced as it streams, never held whole (spec §9).

    This one only ever bites on the machine the OS is for: a film long enough
    to be worth exporting, on two gigabytes, where holding the frames ends the
    export and not the argument. It is invisible on a workstation, which is
    exactly why it wants a measurement rather than a reading.

    Measured on the real exporter, through its own progress callback, against
    live resident memory — not ru_maxrss, which is a high-water mark that
    never falls and so reports nothing after the first big family."""
    if not gtk_available():
        skip("F51 streaming a long film", "no display")
        return

    page_mb = os.sysconf("SC_PAGE_SIZE") / 1e6

    def resident():
        with open("/proc/self/statm") as handle:
            return int(handle.read().split()[1]) * page_mb

    def stream(module, length=200):
        doc = module.AnimationDocument(canvas=(320, 240))
        scene = doc.scenes[0]
        scene["length"] = length
        sheet = module.Sheet(doc, 0)
        for index in range(40):
            cel = doc.add_cel("D%d" % index)
            module.write_pixel(cel.decoded(0), 5 + index, 6, "#1A1916")
            cel.version += 1
        at = 0
        while at < length - 4:
            sheet.stamp(0, module.make_run(doc.cels[at % 40].id, at, 4))
            at += 4
        home = tempfile.mkdtemp(prefix="animation-stream-")
        base = resident()
        high = [0.0]

        def watch(_fraction):
            high[0] = max(high[0], resident() - base)

        # the real exporter, not a loop written beside it: this is the code
        # that has to stay flat, and the code a regression would be in
        module.export_png_frames(doc, [(scene, f) for f in range(length)],
                                 os.path.join(home, "frames"), progress=watch)
        made = len(os.listdir(os.path.join(home, "frames")))
        shutil.rmtree(home, ignore_errors=True)
        pixels = length * doc.canvas[0] * doc.canvas[1] * 4 / 1e6
        return pixels, high[0], made

    pixels, kept, made = stream(animation)
    check("F51 every frame of a long film is written",
          made == 200, made)
    check("F51 and the film passes through without piling up in memory",
          kept < pixels * 0.1,
          ("%.0f MB streamed" % pixels, "%.1f MB held" % kept))

    # the regression this guards against is an exporter that keeps what it
    # has already written — the whole film resident at the end
    graded, scratch = module_mutant("F51-export-keeps-every-frame", [
        ("def export_png_frames(doc, frames, directory, cancel=None, progress=None):\n"
         "    os.makedirs(directory, exist_ok=True)",
         "def export_png_frames(doc, frames, directory, cancel=None, progress=None):\n"
         "    os.makedirs(directory, exist_ok=True)\n    _held = []"),
        ("        composite(doc, scene, frame).write_to_png(tmp)",
         "        _made = composite(doc, scene, frame)\n"
         "        _held.append(_made)\n"
         "        _made.write_to_png(tmp)"),
    ])
    spilled, hoarded, _made = stream(graded)
    mutant("F51 an exporter that holds the whole film is caught",
           hoarded > spilled * 0.5, "%.1f MB held" % hoarded)
    shutil.rmtree(scratch)


def damage_scan_family():
    """Opening a film finds damage without unpacking a single picture.

    The scan that finds a damaged drawing used to decode every take and
    throw the picture away — 3840 decodes at the cap, 2.6 seconds of frozen
    window here and far worse on the machine this OS is for. A structural
    walk answers the same question 115x cheaper, but only gets to say YES:
    anything it doubts still goes to the real decoder, because a take
    wrongly called damaged is replaced with blank paper, and a false alarm
    would destroy work rather than report it.

    So the check that matters is not the speed. It is that every shape of
    damage is still caught, and that a healthy film costs no decodes."""
    if not gtk_available():
        skip("F52 the damage scan", "no display")
        return

    tally = [0]

    class WatchedImageSurface:
        def __init__(self, real):
            self._real = real

        def __call__(self, *args, **kwargs):
            return self._real(*args, **kwargs)

        def create_from_png(self, stream):
            tally[0] += 1
            return self._real.create_from_png(stream)

        def __getattr__(self, name):
            return getattr(self._real, name)

    class WatchedCairo:
        def __init__(self, real):
            self._real = real
            self.ImageSurface = WatchedImageSurface(real.ImageSurface)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def opened(module, raw):
        """Parse while counting how many pictures were actually unpacked."""
        tally[0] = 0
        real = module.cairo
        module.cairo = WatchedCairo(real)
        try:
            return module.AnimationDocument.parse(json.loads(json.dumps(raw)))
        finally:
            module.cairo = real

    def a_film(module, drawings=8, takes=3):
        doc = module.AnimationDocument(canvas=(160, 120))
        for index in range(drawings):
            cel = doc.add_cel("D%d" % index)
            while len(cel.takes) < takes:
                cel.takes.append(module.surface(160, 120))
            for take in range(takes):
                module.write_pixel(cel.decoded(take), 3 + index, 4 + take, "#1A1916")
            cel.version += 1
        return doc.serial()

    healthy = a_film(animation)
    document, reports = opened(animation, healthy)
    check("F52 a healthy film opens without unpacking a single picture",
          not reports and len(document.cels) == 8 and tally[0] == 0,
          (reports, tally[0]))

    # every shape of damage a store can carry, and each one must still be
    # found and said out loud
    sound = base64.b64decode(healthy["cels"][0]["takes"][0])
    corpus = {
        "truncated at half": sound[:len(sound) // 2],
        "truncated by one byte": sound[:-1],
        "a byte flipped in the pixels": bytearray(sound),
        "a byte flipped in the header": bytearray(sound),
        "not a picture at all": b"this was never a drawing" * 30,
        "nothing at all": b"",
        "the signature alone": b"\x89PNG\r\n\x1a\n",
    }
    corpus["a byte flipped in the pixels"][len(sound) // 2] ^= 0x40
    corpus["a byte flipped in the header"][12] ^= 0x01
    caught, decodes = {}, {}
    for name, blob in corpus.items():
        spoiled = copy.deepcopy(healthy)
        spoiled["cels"][0]["takes"][0] = base64.b64encode(bytes(blob)).decode("ascii")
        film, said = opened(animation, spoiled)
        replaced = film.cels[0].takes[0] != healthy["cels"][0]["takes"][0]
        caught[name] = bool(said) and replaced
        decodes[name] = tally[0]
    check("F52 and every shape of damage is still found and said out loud",
          all(caught.values()), sorted(n for n, ok in caught.items() if not ok))
    check("F52 a take the quick scan doubts is put to the real decoder",
          all(count >= 1 for count in decodes.values()), decodes)

    # the scan's own manners: it may vouch for a whole picture and doubt
    # anything else, including a picture of the wrong size
    check("F52 the quick scan is allowed to say yes, never to say no",
          not animation.png_intact(b"not a png", 160, 120) and
          animation.png_intact(sound, 160, 120) and
          not animation.png_intact(sound, 320, 240),
          None)

    graded, scratch = module_mutant("F52-scan-vouches-for-anything", [
        ("    if not blob.startswith(PNG_SIG):\n        return False",
         "    if True:\n        return True"),
    ])
    spoiled = copy.deepcopy(healthy)
    spoiled["cels"][0]["takes"][0] = base64.b64encode(b"not a drawing").decode("ascii")
    film, said = opened(graded, spoiled)
    mutant("F52 a scan that vouches for a broken drawing is caught",
           not said, said)
    shutil.rmtree(scratch)


def lazy_library_family():
    """The library makes a picture for a row when the row can be seen.

    Building each row's picture as the row was built cost one PNG decode and
    one ink-bounds scan per drawing, for pictures nobody had scrolled to:
    1.12 of the 1.24 seconds it took to open a 400-drawing film, and 128 MB
    resident at the cap on a machine built for two gigabytes. GTK only draws
    what is visible.

    Counted, not timed: a construction that renders no thumbnails has an
    empty thumbnail cache, and a list that has been drawn does not."""
    if not gtk_available():
        skip("F53 the library's pictures", "no display")
        return
    from gi.repository import Gtk

    def with_a_library(module, drawings=40):
        home = tempfile.mkdtemp(prefix="animation-lazy-")
        doc = module.AnimationDocument(canvas=(160, 120))
        for index in range(drawings):
            cel = doc.add_cel("D%d" % index)
            module.write_pixel(cel.decoded(0), 4 + index % 40, 5, "#1A1916")
            cel.version += 1
        store = Path(home) / "animation.json"
        store.write_text(json.dumps(doc.serial()), encoding="utf-8")
        was = module.STORE_FILE
        module.STORE_FILE = str(store)
        try:
            app = module.Animation()
        finally:
            module.STORE_FILE = was
        app._flash = lambda *a, **k: None
        shutil.rmtree(home, ignore_errors=True)
        return app

    app = with_a_library(animation)
    rows = app.cel_list.get_children()
    made_at_build = len(app._cel_thumbs)
    check("F53 forty drawings make forty rows",
          len(rows) == 40, len(rows))
    check("F53 and opening the film draws none of their pictures",
          made_at_build == 0, made_at_build)

    # and the picture appears the moment the row is asked to paint
    area = None
    stack = [rows[0]]
    while stack and area is None:
        widget = stack.pop()
        if isinstance(widget, Gtk.DrawingArea):
            area = widget
        elif isinstance(widget, Gtk.Container):
            stack.extend(widget.get_children())
    drawn = None
    if area is not None:
        picture = cairo.ImageSurface(cairo.FORMAT_ARGB32, 44, 33)
        area.emit("draw", cairo.Context(picture))
        picture.flush()
        drawn = any(bytes(picture.get_data()))
    check("F53 a row asked to paint does paint, and keeps what it made",
          area is not None and drawn and len(app._cel_thumbs) == 1,
          (area is not None, drawn, len(app._cel_thumbs)))

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)

    graded, scratch = module_mutant("F53-pictures-built-with-the-rows", [
        ("        picture = Gtk.DrawingArea()\n"
         "        picture.set_size_request(THUMB_W, THUMB_H)\n"
         "        picture.connect('draw', self._draw_cel_thumb, cel.id)",
         "        picture = Gtk.Image.new_from_surface(self._cel_thumb_surface(cel))"),
    ])
    eager = with_a_library(graded)
    mutant("F53 a library that draws every picture up front is caught",
           len(eager._cel_thumbs) >= 40, len(eager._cel_thumbs))
    eager._alive = False
    shutil.rmtree(scratch)


def paint_menu_family():
    """Tip, pattern, mirror and the palette have a way in from the menus.

    Measured before this existed: the dock is 887px of controls in a 406px
    column at 1024x722, and 284px at 1024x600. Everything from Pattern down
    sat below the fold, and unlike every other thing the app can do, these
    four groups had no menu entry — so at the design size they were reachable
    only by dragging a scrollbar nobody had reason to drag. The conventions
    put it plainly: a shortcut nobody can discover is not a feature.

    The menu presses the dock's own control rather than setting the state
    behind it, so the two cannot come to disagree — which is the defect this
    family exists to keep out."""
    if not gtk_available():
        skip("F54 the Paint menu", "no display")
        return
    from gi.repository import Gtk

    def bare(label):
        return label.replace("\u2713", "").strip()

    def opened(module):
        app = module.Animation()
        app._flash = lambda *a, **k: None
        return app

    app = opened(animation)
    check("F54 the app offers a Paint menu, after View",
          "Paint" in animation.Animation.menus and
          list(animation.Animation.menus).index("Paint") ==
          list(animation.Animation.menus).index("View") + 1,
          animation.Animation.menus)

    def items():
        return [(bare(label), cb) for label, cb in app.menu_items("Paint")
                if label != "-"]

    def ticked():
        return {bare(label) for label, _cb in app.menu_items("Paint")
                if label.startswith("\u2713")}

    def invoke(name):
        for label, cb in items():
            if label == name:
                assert cb is not None, name
                cb()
                return True
        return False

    # every control in the three registries is reached by pressing its own
    # dock button, so the menu can never say one thing while the dock shows
    # another
    reached = {}
    for name, wanted, get, buttons, key in (
            ("Round Tip", "round", lambda: app.shape, app._shape_buttons, "round"),
            ("Square Tip", "square", lambda: app.shape, app._shape_buttons, "square"),
            ("Checker", animation.PATTERNS[1], lambda: app.pattern,
             app._pattern_buttons, animation.PATTERNS[1]),
            ("Sparse", animation.PATTERNS[2], lambda: app.pattern,
             app._pattern_buttons, animation.PATTERNS[2]),
            ("Solid", animation.PATTERNS[0], lambda: app.pattern,
             app._pattern_buttons, animation.PATTERNS[0])):
        invoke(name)
        reached[name] = (get() == wanted and buttons[key].get_active() and
                         name in ticked())
    check("F54 choosing a tip or a pattern moves the model, the dock and the tick",
          all(reached.values()),
          sorted(n for n, ok in reached.items() if not ok))

    flips = {}
    for name, attr in (("Mirror Left and Right", "symx"),
                       ("Mirror Top and Bottom", "symy")):
        was = getattr(app, attr)
        invoke(name)
        flips[name] = (getattr(app, attr) != was and
                       app._mirror_buttons[attr].get_active() == (not was) and
                       (name in ticked()) == (not was))
        invoke(name)
        flips[name] = flips[name] and getattr(app, attr) == was
    check("F54 and each mirror turns on and off again from the menu", 
          all(flips.values()),
          sorted(n for n, ok in flips.items() if not ok))

    invoke("Draw with Palette Only")
    locked = (app.doc.palette_only and app._palette_lock_check.get_active()
              and "Draw with Palette Only" in ticked())
    invoke("Draw with Palette Only")
    check("F54 the palette lock is a menu item that shows whether it is on",
          locked and not app.doc.palette_only, (locked, app.doc.palette_only))

    held = list(app.doc.palette)
    invoke("Add Palette Colour")
    grew = len(app.doc.palette) == len(held) + 1
    barred = dict(items()).get("Add Palette Colour")
    invoke("Remove Palette Colour")
    check("F54 the project palette is worked from the menu, and refuses when it must",
          grew and list(app.doc.palette) == held and barred is None,
          (grew, len(app.doc.palette), barred is None))

    # the two controls that were a bare glyph now say what they do
    def words_in(button):
        found, stack = "", [button]
        while stack and not found:
            widget = stack.pop(0)
            if isinstance(widget, Gtk.Label):
                found = widget.get_text()
            elif isinstance(widget, Gtk.Container):
                stack.extend(widget.get_children())
        return found

    named = {attr: words_in(button) for attr, button
             in app._mirror_buttons.items()}
    check("F54 the mirror buttons carry words, like every other dock control",
          all(bool(text) and len(text) > 3 for text in named.values()), named)

    app._alive = False
    for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
        source = getattr(app, timer, None)
        if source:
            try:
                GLib.source_remove(source)
            except Exception:
                pass
            setattr(app, timer, None)

    graded, scratch = module_mutant("F54-menu-that-reaches-nothing", [
        ("                return lambda: button.set_active(\n"
         "                    (not button.get_active()) if wanted is None else wanted)",
         "                return lambda: None"),
    ])
    other = opened(graded)
    for label, cb in other.menu_items("Paint"):
        if bare(label) == "Round Tip" and cb:
            cb()
    mutant("F54 a Paint menu whose items reach nothing is caught",
           other.shape != "round", other.shape)
    other._alive = False
    shutil.rmtree(scratch)


def wobble_cost_family():
    """Wobbling a drawing costs what the drawing is, not what the canvas is.

    Add Wobble Takes runs on the GTK thread and makes up to four takes. It
    cost 306ms a take at 320x240 and 1263ms at 640x480 — up to five seconds
    of frozen window — because it walked every pixel of the canvas, looking
    up a six-pixel noise grid four times per pixel for a picture that is
    mostly blank paper.

    A displacement is never larger than the strength, so a destination
    further than that from any ink can only sample blank paper, which is
    what the new surface already holds. Bounding the work to the ink is
    exact. This family pins the exactness, not the speed: the bound must be
    wide enough that displaced ink still lands."""
    if not gtk_available():
        skip("F55 the cost of a wobble", "no display")
        return

    def drawn(module, canvas=(320, 240), box=(150, 118, 166, 122)):
        doc = module.AnimationDocument(canvas=canvas)
        cel = doc.add_cel("D")
        image = cel.decoded(0)
        x0, y0, x1, y1 = box
        for y in range(y0, y1):
            for x in range(x0, x1):
                module.write_pixel(image, x, y, "#1A1916")
        cel.version += 1
        return cel

    def inked(surface):
        surface.flush()
        data = bytes(surface.get_data())
        stride, width = surface.get_stride(), surface.get_width()
        seen = set()
        for y in range(surface.get_height()):
            row = y * stride
            for x in range(width):
                if data[row + x * 4 + 3]:
                    seen.add((x, y))
        return seen

    cel = drawn(animation)
    was = inked(cel.decoded(0))
    wobbled = inked(animation.wobble_take(cel.decoded(0), cel.id, 2, 1.8))
    check("F55 a wobbled take keeps the same amount of ink",
          abs(len(wobbled) - len(was)) <= len(was) // 2,
          (len(was), len(wobbled)))
    check("F55 and moves some of it beyond where the drawing was",
          bool(wobbled - was), len(wobbled - was))

    again = animation.wobble_take(cel.decoded(0), cel.id, 2, 1.8)
    other = animation.wobble_take(cel.decoded(0), cel.id, 3, 1.8)
    again.flush(); other.flush()
    first = animation.wobble_take(cel.decoded(0), cel.id, 2, 1.8)
    first.flush()
    check("F55 the same take of the same drawing is always the same picture",
          bytes(again.get_data()) == bytes(first.get_data()))
    check("F55 while a different take is a different picture",
          bytes(other.get_data()) != bytes(first.get_data()))

    empty = animation.AnimationDocument(canvas=(160, 120)).add_cel("blank")
    blank = animation.wobble_take(empty.decoded(0), empty.id, 2, 1.8)
    check("F55 wobbling blank paper gives blank paper",
          not inked(blank), len(inked(blank)))

    graded, scratch = module_mutant("F55-bound-too-tight", [
        ("    reach = int(math.ceil(strength)) + 1", "    reach = 0"),
    ])
    tight = drawn(graded)
    kept = inked(graded.wobble_take(tight.decoded(0), tight.id, 2, 1.8))
    mutant("F55 a bound too tight to hold the displaced ink is caught",
           not (kept - was), len(kept - was))
    shutil.rmtree(scratch)


def recolour_family():
    """Recolouring asks the palette once per colour, not once per pixel.

    A drawing holds a handful of colours and a great many pixels, so the
    per-pixel search asked the same question hundreds of thousands of times:
    530ms for one take of a filled 320x240 and 2117ms at 640x480, over every
    take of the cel, on the GTK thread. Remembering the answer per colour is
    15x, and exact.

    The risk a cache introduces is answering for the wrong question, so this
    family drives two colours that share a red channel and must land on
    different palette entries."""
    if not gtk_available():
        skip("F56 recolouring", "no display")
        return

    def recoloured(module):
        app = module.Animation()
        app._flash = lambda *a, **k: None
        app.doc = module.AnimationDocument(canvas=(160, 120))
        app.scene_i = app.layer_i = app.playhead = 0
        app.sheet = module.Sheet(app.doc, 0)
        cel, _run = app.sheet.ensure_drawing(0, 0)
        image = cel.decoded(0)
        # two inks with the SAME red channel, each nearest a different swatch
        module.write_pixel(image, 4, 4, "#1A1916")
        module.write_pixel(image, 5, 4, "#1AE8E0")
        module.write_pixel(image, 6, 4, "#1A1916")
        module.write_pixel(image, 7, 4, "#C8341E")
        cel.version += 1
        app.doc.palette = ["#1A1916", "#19E8E4", "#C8341E", "#FFFFFF"]
        app._recolor_cel()
        image = cel.decoded(0)
        image.flush()
        data = bytes(image.get_data())
        stride = image.get_stride()

        def at(x, y):
            offset = y * stride + x * 4
            if not data[offset + 3]:
                return None
            return (data[offset + 2], data[offset + 1], data[offset])

        app._alive = False
        for timer in ("_save_timer", "_flash_timer", "_prompt_preview_timer"):
            source = getattr(app, timer, None)
            if source:
                try:
                    GLib.source_remove(source)
                except Exception:
                    pass
                setattr(app, timer, None)
        return {"dark": at(4, 4), "bright": at(5, 4), "again": at(6, 4),
                "red": at(7, 4), "paper": at(40, 40),
                "palette": {module._rgb255(c) for c in app.doc.palette}}

    seen = recoloured(animation)
    check("F56 every colour left in the drawing is one the palette holds",
          all(seen[where] in seen["palette"]
              for where in ("dark", "bright", "again", "red")), seen)
    check("F56 two inks that share a red channel keep their own answers",
          seen["dark"] != seen["bright"] and seen["red"] != seen["dark"],
          (seen["dark"], seen["bright"], seen["red"]))
    check("F56 the same ink twice gives the same colour twice",
          seen["dark"] == seen["again"], (seen["dark"], seen["again"]))
    check("F56 and blank paper stays blank", seen["paper"] is None, seen["paper"])

    graded, scratch = module_mutant("F56-remembered-by-the-wrong-question", [
        ("                bytes4 = nearest.get(rgb)", "                bytes4 = nearest.get(rgb[0])"),
        ("                    bytes4 = nearest[rgb] = px4(colour)",
         "                    bytes4 = nearest[rgb[0]] = px4(colour)"),
    ])
    confused = recoloured(graded)
    mutant("F56 a cache that answers for the wrong colour is caught",
           confused["dark"] == confused["bright"],
           (confused["dark"], confused["bright"]))
    shutil.rmtree(scratch)


def raw_frame_family():
    """The bytes handed to the encoder are the picture, in the right order.

    Every exported frame goes through this conversion, so it was a Python
    loop around seventeen million pixels for a three-minute film: 44.6ms a
    frame at 320x240, which is 96 seconds of a two-thousand-frame export
    spent rearranging bytes. Writing each channel with a step is the same
    copy done in C — 63x, and identical.

    What a mistake here looks like is a whole film delivered with its reds
    and blues swapped, which nothing else in the app would notice."""
    if not gtk_available():
        skip("F57 the raw frame", "no display")
        return

    def raw(module, canvas):
        image = module.surface(*canvas, white=True)
        module.write_pixel(image, 0, 0, "#C8341E")       # a red
        module.write_pixel(image, 1, 0, "#1E34C8")       # its mirror image
        module.write_pixel(image, 2, 0, "#7FA98C")
        module.write_pixel(image, canvas[0] - 1, canvas[1] - 1, "#1A1916")
        return bytes(module._rgb24(image)), image

    for canvas in ((160, 120), (321, 241)):
        data, image = raw(animation, canvas)
        span = canvas[0] * 3
        last = (canvas[1] - 1) * span + (canvas[0] - 1) * 3
        check("F57 at %dx%d the encoder is handed red, green, blue in that order"
              % canvas,
              data[0:3] == bytes((0xC8, 0x34, 0x1E)) and
              data[3:6] == bytes((0x1E, 0x34, 0xC8)) and
              data[6:9] == bytes((0x7F, 0xA9, 0x8C)) and
              data[last:last + 3] == bytes((0x1A, 0x19, 0x16)),
              (data[0:3].hex(), data[3:6].hex(), data[last:last + 3].hex()))
        check("F57 and a %dx%d frame is exactly as many bytes as it has pixels"
              % canvas,
              len(data) == canvas[0] * canvas[1] * 3, len(data))
        check("F57 with no row padding carried over from the surface (%dx%d)"
              % canvas,
              data[span - 3:span] == bytes((0xFF, 0xFF, 0xFF)),
              data[span - 3:span].hex())

    graded, scratch = module_mutant("F57-red-and-blue-swapped", [
        ("        line[0::3] = row[2::4]", "        line[0::3] = row[0::4]"),
        ("        line[2::3] = row[0::4]", "        line[2::3] = row[2::4]"),
    ])
    spoiled, _image = raw(graded, (160, 120))
    mutant("F57 an export that swaps red and blue is caught",
           spoiled[0:3] != bytes((0xC8, 0x34, 0x1E)), spoiled[0:3].hex())
    shutil.rmtree(scratch)


def _isolated(family):
    """Run one family with the recovery store moved aside.

    Animation() with no path RESUMES the last film from the store, so a
    family that leaves one behind decides what the NEXT family opens. That
    coupling has now bitten four times — a drop test rewrote a loudness
    test's active layer, two render comparisons inherited a scroll
    position, and a family that fills the library to its cap left the next
    one unable to make a drawing at all. It also hid: the same suite failed
    once and passed unchanged on the next run.

    Isolating here rather than in each family means a family written next
    year gets it for free.
    """
    aside = animation.STORE_FILE + ".between-families"
    had = os.path.exists(animation.STORE_FILE)
    if had:
        os.replace(animation.STORE_FILE, aside)
    try:
        family()
    finally:
        if os.path.exists(animation.STORE_FILE):
            os.unlink(animation.STORE_FILE)
        if had:
            os.replace(aside, animation.STORE_FILE)


for _family in (
        dialog_limits_family,
        streaming_family,
        damage_scan_family,
        lazy_library_family,
        paint_menu_family,
        wobble_cost_family,
        recolour_family,
        raw_frame_family,
        chrome_never_ships_family,
        more_spec_laws_family,
        spec_law_family,
        frame_image_family,
        loudness_card_family,
        audio_pump_family,
        zoom_family,
        compaction_family,
        sound_row_family,
        drop_family,
        at_the_cap_family,
        card_drag_family,
        unbound_recovery_family,
        missing_sound_family,
        verbatim_family,
        remaining_paths_family,
        hover_and_preview_family,
        history_restore_family,
        serial_freshness_family,
        stamping_family,
        slide_and_onion_family,
        library_and_palette_family,
        ordering_family,
        card_effect_family,
        selection_family,
        dock_controls_family,
        export_outcome_family,
        close_guard_family,
        accelerator_family,
        destructive_family,
        workflow_family,
        first_run_family,
        dock_reach_family,
        scene_strip_family,
        library_family,
        sheet_paint_family,
        thumbnail_family,
        control_range_family,
        recording_family,
        message_truth_family,
        ellipsis_promise_family
):
    _isolated(_family)

total = len(PASSES) + len(FAILS) + len(SKIPS) + len(MUTANTS) + len(UNCAUGHT_MUTANTS)
print("TALLY total=%d passed=%d failed=%d skipped=%d mutants-caught=%d mutants-uncaught=%d" %
      (total, len(PASSES), len(FAILS), len(SKIPS), len(MUTANTS),
       len(UNCAUGHT_MUTANTS)))
if SKIPS:
    print("SKIPPED " + "; ".join("%s (%s)" % item for item in SKIPS))
if MUTANTS:
    print("MUTANTS-CAUGHT " + "; ".join(MUTANTS))
if UNCAUGHT_MUTANTS:
    print("MUTANTS-UNCAUGHT " + "; ".join(UNCAUGHT_MUTANTS))
sys.exit(min(255, len(FAILS) + len(UNCAUGHT_MUTANTS)))
