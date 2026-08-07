#!/usr/bin/env python3
"""sequencer_smoothness_selftest — what the Sequencer repaints per transport tick.

    python3 tools/sequencer_smoothness_selftest.py        # no DISPLAY needed

The Sequencer's 100ms transport tick calls refresh() ten times a second for as
long as the tape rolls, and on this hardware every one of those repaints is
traced by the CPU. So what a tick is allowed to invalidate is a correctness
property in its own right: a canvas whose picture did not change must not be
re-rasterised, or a long take spends the machine on frames that come out
identical to the ones before them — against the renderer that is trying to keep
the sound going.

The take editor holds exactly one piece of per-tick content: the playhead,
drawn only while the transport is inside the clip being edited. This drives
Sequencer._sync_edit_playhead directly with a stand-in object, so no X display,
no GTK widget and no sound device is needed — the stand-in's canvas just counts
the redraws it is asked for.
"""
import inspect
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay"
                        "/opt/notebook/de")
sys.path.insert(0, DE)

FAIL = []
PASS = [0]


def check(name, cond, detail=""):
    if cond:
        PASS[0] += 1
        print("  ok   %s" % name)
    else:
        FAIL.append(name)
        print("  FAIL %s %s" % (name, detail))


class Canvas(object):
    """A stand-in for a DrawingArea that only remembers being invalidated."""

    def __init__(self):
        self.draws = 0

    def queue_draw(self):
        self.draws += 1


class App(object):
    """The parts of Sequencer _sync_edit_playhead reads, and nothing else."""

    def __init__(self, clip):
        self.clip = clip
        self.transport = "stop"
        self.pos = 0.0
        self.view = "edit"
        self._rendered = {}
        self.wave_edit = Canvas()

    def sel_clip(self):
        return self.clip

    def tick(self, pos=None, transport=None):
        if pos is not None:
            self.pos = pos
        if transport is not None:
            self.transport = transport
        Q.Sequencer._sync_edit_playhead(self)

    def drawn(self):
        return self.wave_edit.draws


def main():
    global Q
    import sequencer as Q

    clip = {"s": 8.0, "e": 12.0, "off": 0.0, "wav": None}

    print("sequencer — the editors' per-tick repaint")

    # 1. the transport running INSIDE the edited clip: the head moves every
    #    tick, so both canvases must be repainted every tick.
    a = App(clip)
    for i in range(20):
        a.tick(pos=8.0 + i * 0.1, transport="play")
    check("the head inside the clip repaints the editor every tick",
          a.wave_edit.draws == 20, "wave=%d" % a.wave_edit.draws)

    # 2. THE REGRESSION. The transport is rolling somewhere else on the tape:
    #    neither editor draws a playhead there, so every one of these frames
    #    is identical to the last and none of them may be queued.
    a = App(clip)
    for i in range(200):            # 20 seconds of tape at the 100ms tick
        a.tick(pos=20.0 + i * 0.1, transport="play")
    check("a transport outside the edited clip repaints nothing",
          a.drawn() == 0, "%d redraws of an unchanged picture" % a.drawn())

    # ...and the same while recording, which is when the machine can least
    # afford the work.
    a = App(clip)
    for i in range(200):
        a.tick(pos=0.1 * i, transport="rec")
    inside = sum(1 for i in range(200) if 8.0 <= round(0.1 * i, 6) <= 12.0)
    check("recording past the clip only repaints while the head is in it",
          a.wave_edit.draws <= inside + 1,
          "%d redraws for %d frames with a head" % (a.wave_edit.draws, inside))

    # 3. The head leaving must be rubbed out: exactly one more frame after the
    #    playhead runs off the end of the clip, then nothing.
    a = App(clip)
    a.tick(pos=11.9, transport="play")
    before = a.wave_edit.draws
    a.tick(pos=12.4)
    check("the frame after the head leaves still repaints (it erases it)",
          a.wave_edit.draws == before + 1)
    after = a.wave_edit.draws
    for i in range(50):
        a.tick(pos=12.5 + i * 0.1)
    check("...and the frames after THAT do not",
          a.wave_edit.draws == after, "%d extra" % (a.wave_edit.draws - after))

    # 4. Stopping erases the head too, once.
    a = App(clip)
    a.tick(pos=10.0, transport="play")
    before = a.wave_edit.draws
    a.tick(transport="stop")
    check("stopping repaints once to take the head off",
          a.wave_edit.draws == before + 1)
    before = a.wave_edit.draws
    for _ in range(10):
        a.tick()
    check("a stopped transport repaints nothing at all",
          a.wave_edit.draws == before)

    # 5. Nothing selected: the editors show the empty state, which has no
    #    playhead in it at any position.
    a = App(None)
    for i in range(30):
        a.tick(pos=0.1 * i, transport="play")
    check("a clipless editor is never repainted by the tick", a.drawn() == 0,
          "%d redraws" % a.drawn())

    # 6. Static: refresh() must route the editors through the gate rather than
    #    invalidating them on every tick of its own accord.
    src = inspect.getsource(Q.Sequencer.refresh)
    body = src[src.index("self.view"):] if "self.view" in src else src
    check("refresh() does not queue the editors unconditionally per tick",
          "_sync_edit_playhead" in src
          and "queue_draw" not in body.replace("_sync_edit_playhead", ""),
          body.strip()[:200])

    print("\n%d passed, %d failed" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s" % f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
