#!/usr/bin/env python3
"""Rejected Widget Settings actions restore the durable board model."""
import os
import sys

DE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "buildroot",
                                  "board", "notebookos", "rootfs-overlay",
                                  "opt", "notebook", "de"))
sys.path.insert(0, DE)
import widgetsettings  # noqa: E402


class Preview:
    def queue_draw(self): pass


def main():
    app = widgetsettings.WidgetSettings.__new__(widgetsettings.WidgetSettings)
    app.data = {tid: (i < 2) for i, tid in enumerate(widgetsettings.widgets.TILE_ORDER)}
    app.order = list(widgetsettings.widgets.TILE_ORDER)
    original_data, original_order = dict(app.data), list(app.order)
    app.preview = Preview(); app._switches = {}
    app._save = lambda: False
    app._refresh_status = lambda: None
    app._fill_rows = lambda: None
    app._after_change(before=(dict(app.data), list(app.order)))
    assert app.data == original_data and app.order == original_order
    # Exercise an actual bulk mutation's rollback boundary.
    app._set_all(False)
    assert app.data == original_data and app.order == original_order
    app.order.reverse()
    changed = list(app.order)
    app._after_change(before=(original_data, original_order))
    assert app.data == original_data and app.order == original_order
    assert changed != app.order
    print("PASS failed bulk/order writes restore the durable board model")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
