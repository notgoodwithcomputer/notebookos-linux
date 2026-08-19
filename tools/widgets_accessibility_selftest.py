#!/usr/bin/env python3
"""Static guards for keyboard-reachable desktop widget actions."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/widgets.py"
text = SRC.read_text()

ok = True


def check(name, condition):
    global ok
    print(("PASS " if condition else "FAIL ") + name)
    ok = ok and condition


clickable = text[text.index("    def _clickable("):
                 text.index("    # -- Tasks card --")]
check("shared launch targets use native buttons", "hit = Gtk.Button()" in clickable)
check("launch buttons retain neutral papertone styling",
      'add_class("boardhit")' in clickable and ".boardhit {" in text)
check("launch buttons activate through clicked semantics",
      'connect("clicked", self._on_open_clicked, mod, arg)' in clickable)
check("launch targets are not raw pointer-only EventBoxes",
      "Gtk.EventBox" not in clickable and "button-press-event" not in clickable)
check("launch tooltips are retained", "hit.set_tooltip_text(tip)" in clickable)

tasks = text[text.index("    def _rebuild_tasks("):
             text.index("    @staticmethod\n    def _find_task")]
check("task rows use native keyboard-focusable toggle buttons",
      "hit = Gtk.ToggleButton()" in tasks)
check("task rows retain opaque framebuffer-safe styling",
      'add_class("boardhit")' in tasks and 'add_class("taskrow")' in tasks)
check("task rows expose their action", 'set_tooltip_text(_t("Toggle task"))' in tasks)
check("task rows activate through native clicked semantics",
      'connect("clicked", self._on_task_button_clicked, i)' in tasks)
check("task rows have a visible focus treatment", ".taskrow:focus" in text)

print("RESULT: " + ("ALL PASS" if ok else "SOME FAILED"))
raise SystemExit(0 if ok else 1)
