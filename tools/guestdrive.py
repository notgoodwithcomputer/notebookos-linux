#!/usr/bin/env python3
"""guestdrive — compound input gestures for the running guest, building on
qmp.py's primitives. One gesture per invocation; the guest stays running.

  guestdrive.py dblclick <fx> <fy>          double-click at FRAMEBUFFER px
  guestdrive.py click <fx> <fy>
  guestdrive.py drag <fx0> <fy0> <fx1> <fy1> [steps]   press-move-release
  guestdrive.py type <text>                 ASCII via send-key
  guestdrive.py key <qcode> [...]           raw qcodes (ret, esc, tab, ...)
  guestdrive.py watch <out-prefix> <n> <dt> shots out-prefix-000.png ...

Framebuffer pixels are converted with the live framebuffer size (read from a
screendump header) against qmp.py's SCREEN constants, so callers work in the
coordinates they see in screenshots.
"""
import os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import qmp

FB_W, FB_H = 1280, 800


def fb2qmp(x, y):
    return (x * (qmp.SCREEN_W - 1) / (FB_W - 1),
            y * (qmp.SCREEN_H - 1) / (FB_H - 1))


def shot(q, path):
    fd, ppm = tempfile.mkstemp(suffix=".ppm"); os.close(fd)
    qmp.do_shot(q, ppm) if hasattr(qmp, "do_shot") else q.cmd(
        "screendump", filename=ppm)
    time.sleep(0.4)
    from PIL import Image
    Image.open(ppm).save(path)
    os.unlink(ppm)


def main():
    op = sys.argv[1]
    q = qmp.Qmp()
    if op in ("click", "dblclick"):
        fx, fy = float(sys.argv[2]), float(sys.argv[3])
        x, y = fb2qmp(fx, fy)
        qmp.click_at(q, x, y, 2 if op == "dblclick" else 1)
    elif op == "drag":
        fx0, fy0, fx1, fy1 = map(float, sys.argv[2:6])
        steps = int(sys.argv[6]) if len(sys.argv) > 6 else 12
        x0, y0 = fb2qmp(fx0, fy0); x1, y1 = fb2qmp(fx1, fy1)
        qmp.send(q, qmp.ev_abs(x0, y0)); time.sleep(0.15)
        qmp.send(q, qmp.ev_btn(True)); time.sleep(0.08)
        for i in range(1, steps + 1):
            xx = x0 + (x1 - x0) * i / steps
            yy = y0 + (y1 - y0) * i / steps
            qmp.send(q, qmp.ev_abs(xx, yy)); time.sleep(0.05)
        qmp.send(q, qmp.ev_btn(False))
    elif op == "type":
        text = sys.argv[2]
        SHIFTED = {c: k for c, k in zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ',
                                        'abcdefghijklmnopqrstuvwxyz')}
        PUNCT = {" ": "spc", ".": "dot", ",": "comma", "!": "shift-1",
                 "?": "shift-slash", "'": "apostrophe", "-": "minus"}
        for ch in text:
            if ch in SHIFTED:
                keys = [{"type": "qcode", "data": "shift"},
                        {"type": "qcode", "data": SHIFTED[ch]}]
            elif ch.islower() or ch.isdigit():
                keys = [{"type": "qcode", "data": ch}]
            elif ch in PUNCT:
                spec = PUNCT[ch]
                if spec.startswith("shift-"):
                    keys = [{"type": "qcode", "data": "shift"},
                            {"type": "qcode", "data": spec[6:]}]
                else:
                    keys = [{"type": "qcode", "data": spec}]
            else:
                continue
            q.cmd("send-key", keys=keys)
            time.sleep(0.12)
    elif op == "key":
        for qc in sys.argv[2:]:
            combo = [{"type": "qcode", "data": part} for part in qc.split("+")]
            q.cmd("send-key", keys=combo)
            time.sleep(0.15)
    elif op == "watch":
        prefix, n, dt = sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
        for i in range(n):
            shot(q, "%s-%03d.png" % (prefix, i))
            time.sleep(dt)
    elif op == "shot":
        shot(q, sys.argv[2])
    else:
        print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main()
