#!/usr/bin/env python3
"""Build the two real acceptance pieces for Notebook OS Animation.

This is deliberately a model/export driver, not a screenshot fixture.  It
creates ordinary cels, takes, exposure runs, sounds, and markers through the
same public helpers the application uses, saves reopenable ``.anim`` files,
and invokes the shipping exporters when ffmpeg is available.
"""
from __future__ import annotations

import argparse
import array
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import wave

REPO = Path(__file__).resolve().parents[1]
DE = REPO / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de"
sys.path.insert(0, str(DE))

import animation  # noqa: E402

SEED = 62014
SR = 48000
INK = "#1A1916"
RED = "#C8341E"
GREEN = "#7FA98C"
PAPER = "#FCFBF8"
WASH = "#EAE3D2"
FIELD = "#DED4C2"
MUTED = "#9A9484"


def fill(image, colour, pattern="solid"):
    """Fill an entire cel through Animation's byte span writer."""
    for y in range(image.get_height()):
        animation.write_span(image, y, 0, image.get_width() - 1,
                             colour, pattern)


def rect(image, x, y, width, height, colour, pattern="solid"):
    for row in range(y, y + height):
        animation.write_span(image, row, x, x + width - 1, colour, pattern)


def add_cel(document, name, painter):
    cel = document.add_cel(name)
    if cel is None:
        raise RuntimeError("cel cap reached while building benchmark")
    painter(cel.decoded())
    cel.version += 1
    return cel


FONT = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "X": ("10001", "01010", "00100", "00100", "00100", "01010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "N": ("10001", "11001", "11001", "10101", "10011", "10011", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
}


def text_width(text, scale):
    """Width in pixels of pixel_text's lettering at this scale."""
    width = 0
    for character in text:
        width += 4 * scale if character == " " else 6 * scale
    return width - scale if text and text[-1] != " " else width


def fitted_text(image, text, y, colour, scale=7, margin=8):
    """Centre the lettering, stepping the scale down until it fits."""
    canvas_width = image.get_width()
    while scale > 1 and text_width(text, scale) > canvas_width - 2 * margin:
        scale -= 1
    x = (canvas_width - text_width(text, scale)) // 2
    pixel_text(image, text, x, y, scale, colour)
    return scale


def pixel_text(image, text, x, y, scale, colour):
    """Draw compact block lettering using horizontal runs only."""
    cursor = x
    for character in text:
        if character == " ":
            cursor += 4 * scale
            continue
        glyph = FONT[character]
        for row, bits in enumerate(glyph):
            start = None
            for column, bit in enumerate(bits + "0"):
                if bit == "1" and start is None:
                    start = column
                elif bit == "0" and start is not None:
                    for thick in range(scale):
                        animation.write_span(
                            image, y + row * scale + thick,
                            cursor + start * scale,
                            cursor + column * scale - 1, colour)
                    start = None
        cursor += 6 * scale


def write_wav(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SR)
        output.writeframes(samples.tobytes())
    # The signature is stored in the project. Pinning mtime makes rebuilding
    # into the same output directory byte-for-byte deterministic as promised.
    os.utime(path, (SEED, SEED))


def dialogue_tape(path, seconds=178):
    """Make deterministic alternating speech-shaped phrases and their spans."""
    rng = random.Random(SEED)
    total = seconds * SR
    samples = array.array("h", [0]) * total
    phrases = []
    cursor = 0.0
    speaker = 0
    while cursor < seconds - 2.5:
        silence = rng.uniform(.4, 1.2)
        cursor += silence
        duration = min(rng.uniform(2.0, 6.0), seconds - cursor)
        if duration < 1:
            break
        start = cursor
        end = cursor + duration
        phrases.append((speaker, start, end))
        fundamental = 110 if speaker == 0 else 180
        syllables = rng.uniform(3.0, 6.0)
        first = round(start * SR)
        last = min(total, round(end * SR))
        for index in range(first, last):
            local = (index - first) / SR
            envelope = min(1.0, local / .035, (last - index) / (SR * .055))
            syllable = max(0.0, math.sin(math.pi * syllables * local)) ** .65
            phrase_shape = .72 + .28 * math.sin(2 * math.pi * .43 * local)
            carrier = (math.sin(2 * math.pi * fundamental * local) +
                       .32 * math.sin(2 * math.pi * fundamental * 2 * local) +
                       .14 * math.sin(2 * math.pi * fundamental * 3 * local))
            value = round(8500 * envelope * syllable * phrase_shape * carrier)
            samples[index] = max(-32768, min(32767, value))
        cursor = end
        speaker = 1 - speaker
    write_wav(path, samples)
    return samples, phrases


def beat_tape(path, seconds=30):
    """Make a deterministic 112 BPM eight-step kick/snare/hat pattern."""
    rng = random.Random(SEED + 1)
    samples = array.array("h", [0]) * (seconds * SR)
    step_seconds = 60 / 112 / 2
    step = 0
    while step * step_seconds < seconds:
        start = round(step * step_seconds * SR)
        kind = step % 8
        if kind in (0, 4):
            length = round(.22 * SR)
            for offset in range(min(length, len(samples) - start)):
                t = offset / SR
                value = 15000 * math.exp(-18 * t) * math.sin(2 * math.pi * (72 - 90 * t) * t)
                samples[start + offset] = max(-32768, min(32767, samples[start + offset] + round(value)))
        if kind in (2, 6):
            length = round(.13 * SR)
            for offset in range(min(length, len(samples) - start)):
                t = offset / SR
                value = rng.uniform(-1, 1) * 10500 * math.exp(-28 * t)
                samples[start + offset] = max(-32768, min(32767, samples[start + offset] + round(value)))
        length = round(.035 * SR)
        for offset in range(min(length, len(samples) - start)):
            t = offset / SR
            value = rng.uniform(-1, 1) * 4200 * math.exp(-75 * t)
            samples[start + offset] = max(-32768, min(32767, samples[start + offset] + round(value)))
        step += 1
    write_wav(path, samples)
    return samples, step_seconds


def therapist_background(image):
    fill(image, WASH)
    rect(image, 0, 170, 320, 70, FIELD)
    rect(image, 32, 42, 92, 72, PAPER)
    rect(image, 38, 48, 80, 60, GREEN, "sparse")
    rect(image, 188, 148, 132, 12, INK)
    rect(image, 214, 160, 12, 62, INK)
    rect(image, 294, 160, 12, 62, INK)
    rect(image, 270, 62, 8, 72, INK)
    animation.stamp(image, 274, 58, 24, "round", RED)
    # Bookshelf and uneven book spines, using the room's existing six inks.
    for shelf_y in (58, 76, 94, 112):
        animation.write_span(image, shelf_y, 136, 180, INK)
    for book_x, book_y, book_width, colour in (
            (139, 61, 5, RED), (146, 65, 7, GREEN),
            (156, 60, 4, RED), (163, 64, 8, GREEN), (173, 61, 5, RED)):
        rect(image, book_x, book_y, book_width, 12, colour)
    # The pale rectangle was already the window; give it a hairline frame.
    for frame_y in (42, 113):
        animation.write_span(image, frame_y, 32, 123, INK)
    for frame_x in (32, 123):
        rect(image, frame_x, 42, 1, 72, INK)


def therapist_body(image):
    animation.stamp(image, 224, 92, 42, "round", INK)
    animation.stamp(image, 224, 92, 34, "round", "#C98E70")
    rect(image, 194, 114, 62, 54, GREEN)
    rect(image, 202, 132, 8, 30, INK)
    rect(image, 240, 132, 8, 30, INK)
    rect(image, 213, 84, 5, 5, INK)
    rect(image, 231, 84, 5, 5, INK)


def patient_background(image):
    fill(image, FIELD)
    rect(image, 0, 178, 320, 62, WASH)
    rect(image, 20, 128, 274, 64, GREEN)
    rect(image, 28, 118, 256, 18, GREEN)
    rect(image, 38, 190, 14, 34, INK)
    rect(image, 264, 190, 14, 34, INK)
    rect(image, 232, 28, 62, 74, PAPER)
    rect(image, 238, 34, 50, 62, RED, "checker")
    # A sparse rug and a tiny side table keep the room readable in wide shots.
    rect(image, 64, 202, 170, 22, RED, "sparse")
    rect(image, 278, 124, 30, 7, INK)
    rect(image, 282, 131, 5, 42, INK)
    rect(image, 299, 131, 5, 42, INK)


def patient_body(image):
    animation.stamp(image, 102, 102, 44, "round", INK)
    animation.stamp(image, 102, 102, 36, "round", "#D6A07E")
    rect(image, 116, 112, 106, 40, RED)
    rect(image, 146, 146, 78, 15, INK)
    rect(image, 91, 94, 5, 5, INK)
    rect(image, 108, 94, 5, 5, INK)


def blink_painter(x, y):
    """Return a transparent two-eye closed-lid drawing."""
    def paint(image):
        animation.write_span(image, y, x - 13, x - 5, INK)
        animation.write_span(image, y, x + 5, x + 13, INK)
    return paint


def gesture_painter(setup):
    """Draw one emphatic raised forearm over the matching body setup."""
    def paint(image):
        if setup == 0:
            rect(image, 188, 90, 9, 45, GREEN)
            animation.stamp(image, 192, 84, 12, "round", "#C98E70")
        else:
            rect(image, 202, 80, 42, 9, RED)
            animation.stamp(image, 247, 82, 12, "round", "#D6A07E")
    return paint


def mouth_painter(x, y, state):
    def paint(image):
        if state == 0:
            animation.write_span(image, y, x - 7, x + 7, INK)
        elif state == 1:
            rect(image, x - 7, y - 2, 15, 5, INK)
            rect(image, x - 5, y - 1, 11, 3, PAPER)
        else:
            rect(image, x - 8, y - 5, 17, 11, INK)
            rect(image, x - 5, y - 2, 11, 5, PAPER)
    return paint


def add_wobble(cel):
    source = cel.decoded(0)
    cel.takes = [source,
                 animation.wobble_take(source, cel.id, 2, 1.1),
                 animation.wobble_take(source, cel.id, 3, 1.1)]
    cel.version += 1


def deterministic_save(document, project):
    """Save one build; main compares two independently rebuilt documents."""
    animation.save_document(document, str(project))


def group_shots(phrases, total_seconds):
    """Group phrase boundaries into readable 4-12 second alternating shots."""
    starts = [0.0] + [phrase[1] for phrase in phrases]
    starts = sorted(set(round(value, 6) for value in starts if value < total_seconds))
    shots = []
    start = 0.0
    index = 1
    while start < total_seconds:
        candidates = [value for value in starts[index:]
                      if 4 <= value - start <= 12]
        end = candidates[-1] if candidates else min(total_seconds, start + 12)
        if total_seconds - end < 4:
            end = total_seconds
        shots.append((start, end))
        while index < len(starts) and starts[index] <= end:
            index += 1
        start = end
    return shots


def phrase_starts_from_rms(samples, fps=12):
    """Recover phrase onsets from frame-aligned RMS, as the app's assists do."""
    spf = animation.SPF[fps]
    levels = []
    for offset in range(0, len(samples), spf):
        block = samples[offset:offset + spf]
        levels.append(math.sqrt(sum(value * value for value in block) /
                                max(1, len(block))))
    peak = max(levels or [1]) or 1
    active = [level / peak >= .045 for level in levels]
    starts = []
    quiet_run = fps
    for frame, speaking in enumerate(active):
        if speaking and quiet_run >= max(2, round(.3 * fps)):
            starts.append(frame / fps)
        quiet_run = 0 if speaking else quiet_run + 1
    return starts


def mouth_runs(samples, start_sample, frames, slots):
    end = start_sample + frames * animation.SPF[12]
    lane = animation.loudness_slots(samples[start_sample:end], animation.SPF[12],
                                    .10, .45)
    if len(lane) < frames:
        lane.extend([1] * (frames - len(lane)))
    return animation.slots_to_runs(lane[:frames], slots)


def phrase_energy(samples, start, end):
    """Return mean-square phrase energy without allocating a float buffer."""
    first = max(0, round(start * SR))
    last = min(len(samples), round(end * SR))
    if last <= first:
        return 0
    return sum(value * value for value in samples[first:last]) / (last - first)


def jittered_blinks(length, cel_id, seed):
    """Make two-frame blinks separated by deterministic 60-110 frame rests."""
    rng = random.Random(seed)
    runs = []
    frame = rng.randint(60, 110)
    while frame + 2 <= length:
        runs.append(animation.make_run(cel_id, frame, 2))
        frame += rng.randint(60, 110)
    return runs


def build_couch(output):
    wav_path = output / "couch-dialogue.wav"
    samples, phrases = dialogue_tape(wav_path)
    samples = animation.decode_samples(
        str(wav_path), [wav_path.stat().st_size, int(wav_path.stat().st_mtime)])
    document = animation.AnimationDocument(canvas=(320, 240), fps=12,
                                           boil_every=2,
                                           palette=[INK, RED, GREEN, PAPER,
                                                    WASH, FIELD, "#C98E70",
                                                    "#D6A07E"])
    document.scenes = []
    title = add_cel(document, "THE COUCH title", lambda image: (
        fill(image, PAPER), fitted_text(image, "THE COUCH", 88, INK)))
    backgrounds = [add_cel(document, "Therapist room", therapist_background),
                   add_cel(document, "Patient room", patient_background)]
    bodies = [add_cel(document, "Therapist", therapist_body),
              add_cel(document, "Patient", patient_body)]
    add_wobble(bodies[0])
    add_wobble(bodies[1])
    mouth_sets = [
        [add_cel(document, "Therapist mouth %d" % state,
                 mouth_painter(224, 104, state)).id for state in range(3)],
        [add_cel(document, "Patient mouth %d" % state,
                 mouth_painter(102, 113, state)).id for state in range(3)],
    ]
    blinks = [add_cel(document, "Therapist blink", blink_painter(224, 92)),
              add_cel(document, "Patient blink", blink_painter(102, 102))]
    gestures = [add_cel(document, "Therapist raised hand", gesture_painter(0)),
                add_cel(document, "Patient raised hand", gesture_painter(1))]
    loud_onsets = {}
    for speaker in (0, 1):
        ranked = sorted(
            ((phrase_energy(samples, start, end), start)
             for owner, start, end in phrases if owner == speaker),
            reverse=True)
        loud_onsets[speaker] = {start for _energy, start in ranked[:3]}

    title_scene = animation.new_scene("THE COUCH", 24)
    title_scene["layers"][0]["runs"] = [animation.make_run(title.id, 0, 24)]
    title_scene["markers"] = [{"frame": 0, "text": "Title"}]
    document.scenes.append(title_scene)

    detected_starts = phrase_starts_from_rms(samples)
    detected_phrases = [(0, start, start) for start in detected_starts]
    # Credits occupy the last 24 frames, so dialogue shots stop at 176 seconds.
    shots = group_shots(detected_phrases, 176)
    for shot_index, (start, end) in enumerate(shots):
        setup = shot_index % 2
        start_frame = round(start * 12)
        end_frame = round(end * 12)
        length = max(1, end_frame - start_frame)
        scene = animation.new_scene(
            "Therapist" if setup == 0 else "Patient", length)
        scene["layers"] = [animation.new_layer("Room"),
                           animation.new_layer("Boiling character"),
                           animation.new_layer("Mouth"),
                           animation.new_layer("Blink"),
                           animation.new_layer("Gesture")]
        scene["layers"][0]["runs"] = [
            animation.make_run(backgrounds[setup].id, 0, length, take=1)]
        scene["layers"][1]["runs"] = [
            animation.make_run(bodies[setup].id, 0, length, take=0)]
        scene["layers"][2]["mouth_slots"] = mouth_sets[setup]
        sample_start = start_frame * animation.SPF[12]
        scene["layers"][2]["runs"] = mouth_runs(
            samples, sample_start, length, mouth_sets[setup])
        scene["layers"][3]["runs"] = jittered_blinks(
            length, blinks[setup].id, SEED + shot_index * 17 + setup)
        gesture_runs = []
        for owner, phrase_start, phrase_end in phrases:
            if (owner == setup and phrase_start in loud_onsets[setup] and
                    start <= phrase_start < end):
                gesture_length = 8 + (round(phrase_start * 1000) % 7)
                local_frame = round((phrase_start - start) * 12)
                gesture_length = min(gesture_length, length - local_frame)
                if gesture_length > 0:
                    gesture_runs.append(animation.make_run(
                        gestures[setup].id, local_frame, gesture_length))
        scene["layers"][4]["runs"] = gesture_runs
        total_samples = len(samples)
        scene["sounds"][0] = {
            "path": str(wav_path), "start": 0, "in_smp": sample_start,
            "out_smp": max(0, total_samples -
                           (sample_start + length * animation.SPF[12])),
            "mute": False, "peaks": "", "sig": [wav_path.stat().st_size,
                                                     int(wav_path.stat().st_mtime)],
            "duration_smp": total_samples,
        }
        scene["markers"] = [
            {"frame": round((phrase_start - start) * 12),
             "text": "Line %d" % (phrase_index + 1)}
            for phrase_index, phrase_start in enumerate(detected_starts)
            if start <= phrase_start < end]
        document.scenes.append(scene)

    credits = add_cel(document, "THE COUCH credits", lambda image: (
        fill(image, PAPER),
        fitted_text(image, "THE COUCH", 54, MUTED, scale=4),
        fitted_text(image, "NOTEBOOK OS ANIMATION", 112, INK, scale=4)))
    credits_scene = animation.new_scene("Credits", 24)
    credits_scene["layers"][0]["runs"] = [
        animation.make_run(credits.id, 0, 24)]
    credits_scene["markers"] = [{"frame": 0, "text": "Credits"}]
    document.scenes.append(credits_scene)

    frames = [(scene, frame) for scene in document.scenes
              for frame in range(scene["length"])]
    project = output / "couch.anim"
    deterministic_save(document, project)
    audio_specs = [{"path": str(wav_path), "in_smp": 0,
                    "out_smp": len(samples),
                    "delay_smp": 24 * animation.SPF[12]}]
    return document, frames, project, wav_path, audio_specs


def creature(image, pose, palette):
    fill(image, palette[4])
    animation.stamp(image, 80, 60, 58, "round", palette[0], "checker")
    animation.stamp(image, 80, 60, 48, "round", palette[1])
    eye_y = 51 - (3 if pose == 1 else 0)
    rect(image, 67, eye_y, 6, 7, palette[0])
    rect(image, 88, eye_y, 6, 7, palette[0])
    if pose == 2:
        rect(image, 66, 72, 30, 4, palette[3])
    else:
        animation.write_span(image, 72, 70, 91, palette[0])
    rect(image, 55, 88, 50, 20, palette[2], "sparse")


def swapped_creature(image, palette):
    """Duplicate the stare design, then invert its main chips and move eyes."""
    creature(image, 0, palette)
    animation.stamp(image, 80, 60, 58, "round", palette[1], "checker")
    animation.stamp(image, 80, 60, 48, "round", palette[0])
    rect(image, 61, 57, 7, 6, palette[1])
    rect(image, 93, 45, 7, 6, palette[1])
    animation.write_span(image, 73, 68, 93, palette[1])
    rect(image, 55, 88, 50, 20, palette[2], "sparse")


def floor_painter(palette, pattern):
    """Paint only the lower checkerboard band so the creature remains visible."""
    def paint(image):
        rect(image, 0, 102, 160, 18, palette[5], pattern)
        for y in range(102, 120, 6):
            animation.write_span(image, y, 0, 159, palette[6], pattern)
    return paint


def build_buttercup(output):
    wav_path = output / "buttercup-beat.wav"
    samples, step_seconds = beat_tape(wav_path)
    samples = animation.decode_samples(
        str(wav_path), [wav_path.stat().st_size, int(wav_path.stat().st_mtime)])
    palette = [INK, "#D9A21B", GREEN, RED, PAPER, WASH, FIELD, MUTED]
    document = animation.AnimationDocument(canvas=(160, 120), fps=12,
                                           palette=palette, palette_only=True)
    scene = document.scenes[0]
    scene["name"] = "BUTTERCUP LOOP"
    scene["length"] = 360
    scene["layers"] = [animation.new_layer("Creature"),
                       animation.new_layer("Floor beat"),
                       animation.new_layer("Title crawl")]
    poses = [add_cel(document, "Stare", lambda image: creature(image, 0, palette)),
             add_cel(document, "Jolt", lambda image: creature(image, 1, palette)),
             add_cel(document, "Open", lambda image: creature(image, 2, palette))]
    swap = add_cel(document, "Hard-swap creature",
                   lambda image: swapped_creature(image, palette))
    checker_floor = add_cel(document, "Checker floor",
                            floor_painter(palette, "checker"))
    sparse_floor = add_cel(document, "Sparse floor",
                           floor_painter(palette, "sparse"))
    title = add_cel(document, "PIXEL title",
                    lambda image: pixel_text(image, "PIXEL", 8, 92, 3, palette[0]))

    runs = []
    frame = 0
    cycle = 0
    while frame < 359:
        stare = min(30 if cycle % 2 == 0 else 24, 359 - frame)
        runs.append(animation.make_run(poses[0].id, frame, stare))
        frame += stare
        if frame < 359:
            jolt = min(2 if cycle % 2 == 0 else 3, 359 - frame)
            runs.append(animation.make_run(poses[1 + cycle % 2].id, frame, jolt))
            frame += jolt
        cycle += 1
    runs.append(animation.make_run(poses[0].id, 359, 1))
    scene["layers"][0]["runs"] = runs

    beat_frames = sorted(set(round(index * step_seconds * 12)
                             for index in range(112)
                             if round(index * step_seconds * 12) < 360))
    swap_starts = [min(beat_frames, key=lambda value: abs(value - target))
                   for target in (120, 240)]
    sheet = animation.Sheet(document)
    for start in swap_starts:
        sheet.clear(0, start, start + 6)
        sheet.stamp(0, animation.make_run(swap.id, start, 6))
    scene["layers"][1]["runs"] = [
        animation.make_run(checker_floor.id, 0, 360)]
    for start in swap_starts:
        sheet.clear(1, start, start + 6)
        sheet.stamp(1, animation.make_run(sparse_floor.id, start, 6))

    sheet.stamp(2, animation.make_run(title.id, 60, 1, dx=160, take=1))
    sheet.stamp(2, animation.make_run(title.id, 108, 1, dx=-80, take=1))
    if not sheet.slide(2, 60, 108):
        raise RuntimeError("title crawl slide did not stamp its gap")
    scene["markers"] = [{"frame": frame, "text": "Beat"}
                        for frame in beat_frames]
    scene["sounds"][0] = {
        "path": str(wav_path), "start": 0, "in_smp": 0, "out_smp": 0,
        "mute": False, "peaks": "", "sig": [wav_path.stat().st_size,
                                                 int(wav_path.stat().st_mtime)],
        "duration_smp": len(samples),
    }
    frames = [(scene, frame) for frame in range(360)]
    project = output / "buttercup.anim"
    deterministic_save(document, project)
    audio_specs = [{"path": str(wav_path), "in_smp": 0,
                    "out_smp": len(samples), "delay_smp": 0}]
    return document, frames, project, wav_path, audio_specs


def verify_project(document, project, expected_frames):
    reopened, reports = animation.open_document(str(project))
    if reopened is None or reports:
        raise AssertionError("%s did not reopen cleanly: %r" % (project, reports))
    frame_count = sum(scene["length"] for scene in reopened.scenes)
    if frame_count != expected_frames:
        raise AssertionError("%s frames=%d expected=%d" %
                             (project, frame_count, expected_frames))
    first = project.read_bytes()
    second = project.with_suffix(".repeat.anim")
    animation.save_document(reopened, str(second))
    if first != second.read_bytes():
        raise AssertionError(project.name + " serialization is not byte-identical")
    second.unlink()
    print("PASS REOPEN %s reports=0 frames=%d duration=%.3fs" %
          (project.name, frame_count, frame_count / document.fps))


UNVERIFIED = []


def probe(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        # Remembered, not forgotten: without this the run prints every
        # export line, checks NOTHING about the files, and still ends in an
        # unqualified PASS. The machine this ships to may well be one that
        # has no ffprobe.
        UNVERIFIED.append(path.name)
        print("SKIP FFPROBE %s - ffprobe is absent" % path.name)
        return None
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,r_frame_rate",
         "-of", "json", str(path)], capture_output=True, text=True, check=True)
    parsed = json.loads(result.stdout)
    video = next(stream for stream in parsed["streams"]
                 if stream["codec_type"] == "video")
    duration = float(parsed["format"]["duration"])
    print("PROBE %s duration=%.3fs fps=%s size=%sx%s" %
          (path.name, duration, video["r_frame_rate"], video["width"],
           video["height"]))
    return duration, video


def export_piece(label, document, frames, output, audio_specs):
    if not shutil.which("ffmpeg"):
        print("SKIP EXPORT %s - ffmpeg is absent" % label)
        return
    if label == "A":
        path = output / "couch.mp4"
        print("EXPORT A video frames=%d" % len(frames))
        animation.export_video(document, frames, str(path), 1920, 1080,
                               audio_specs=audio_specs)
        result = probe(path)
        if result:
            duration, video = result
            expected = len(frames) / 12
            if abs(duration - expected) > 1 / 24:
                raise AssertionError("couch mp4 duration %.3f expected %.3f" %
                                     (duration, expected))
            if video["r_frame_rate"] != "24/1":
                raise AssertionError("couch mp4 rate is " + video["r_frame_rate"])
    else:
        gif = output / "buttercup.gif"
        video_path = output / "buttercup.mp4"
        print("EXPORT B gif frames=360 scale=3")
        animation.export_gif(document, frames, str(gif), 3)
        print("EXPORT B video frames=360")
        animation.export_video(document, frames, str(video_path), 640, 480,
                               audio_specs=audio_specs)
        result = probe(video_path)
        if result:
            duration, video = result
            if abs(duration - 30.0) > 1 / 24:
                raise AssertionError("buttercup mp4 duration %.3f" % duration)
            if video["r_frame_rate"] != "24/1":
                raise AssertionError("buttercup mp4 rate is " + video["r_frame_rate"])
        # The gif is the piece this genre actually ships. Its probe line was
        # printed and thrown away, which reads exactly like verification and
        # is not: a gif at the wrong rate or a third of the size would have
        # passed this benchmark every time.
        animated = probe(gif)
        if animated:
            duration, picture = animated
            if abs(duration - 30.0) > 1 / 12:
                raise AssertionError("buttercup gif duration %.3f" % duration)
            if picture["r_frame_rate"] != "12/1":
                raise AssertionError("buttercup gif rate is " +
                                     picture["r_frame_rate"])
            wide, high = document.canvas
            if (int(picture["width"]), int(picture["height"])) != (wide * 3, high * 3):
                raise AssertionError("buttercup gif is %sx%s, expected %dx%d" %
                                     (picture["width"], picture["height"],
                                      wide * 3, high * 3))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("piece", choices=("A", "B", "all"))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    print("SEED %d" % SEED)
    if args.piece in ("A", "all"):
        print("BUILD A The couch")
        document, frames, project, _wav, audio_specs = build_couch(args.out)
        first_build = project.read_bytes()
        document, frames, project, _wav, audio_specs = build_couch(args.out)
        if first_build != project.read_bytes():
            raise AssertionError("couch.anim differs across independent builds")
        print("PASS DETERMINISM couch.anim independent-build bytes identical")
        if len(frames) < 1980:
            raise AssertionError("couch is shorter than 2:45")
        verify_project(document, project, len(frames))
        export_piece("A", document, frames, args.out, audio_specs)
    if args.piece in ("B", "all"):
        print("BUILD B The buttercup bar")
        document, frames, project, _wav, audio_specs = build_buttercup(args.out)
        first_build = project.read_bytes()
        document, frames, project, _wav, audio_specs = build_buttercup(args.out)
        if first_build != project.read_bytes():
            raise AssertionError("buttercup.anim differs across independent builds")
        print("PASS DETERMINISM buttercup.anim independent-build bytes identical")
        if len(frames) != 360:
            raise AssertionError("buttercup must be exactly 360 frames")
        first = animation._rgb24(animation.composite(
            document, document.scenes[0], 0))
        last = animation._rgb24(animation.composite(
            document, document.scenes[0], 359))
        if first != last:
            raise AssertionError("buttercup first and last frames differ")
        verify_project(document, project, 360)
        export_piece("B", document, frames, args.out, audio_specs)
    if UNVERIFIED:
        print("PASS BENCHMARK %s - BUILT BUT UNVERIFIED: %s (ffprobe absent, "
              "so no duration, rate or size was checked)"
              % (args.piece, ", ".join(sorted(set(UNVERIFIED)))))
    else:
        print("PASS BENCHMARK " + args.piece)


if __name__ == "__main__":
    main()
