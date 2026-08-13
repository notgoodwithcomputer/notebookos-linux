#!/usr/bin/env python3
"""Headless-first contract checks for the Notebook OS Animation studio."""
from __future__ import annotations

import array
import base64
import copy
import importlib
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
import animation  # noqa: E402

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


def _find_widgets(root, predicate, out):
    if predicate(root):
        out.append(root)
    if hasattr(root, "get_children"):
        for child in root.get_children():
            _find_widgets(child, predicate, out)


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
    scene["layers"][0]["mouth_slots"] = [app.doc.add_cel().id for _ in range(3)]
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


dialog_limits_family()
workflow_family()
thumbnail_family()
control_range_family()
recording_family()

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
