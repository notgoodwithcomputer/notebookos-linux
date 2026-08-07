#!/usr/bin/env python3
"""
ellipsis_sweep — which UI labels are CUT SHORT on the smallest panel?

    tools/guestrun.sh python3 tools/ellipsis_sweep.py [--langs=de,ja] [app ...]
    tools/guestrun.sh python3 tools/ellipsis_sweep.py --all-langs

WHY THIS EXISTS ALONGSIDE minsize_sweep. They are the same defect seen from
opposite sides, and only one of them was covered.

`minsize_sweep` asks "does the window's MINIMUM size exceed the panel". An
ellipsizing label has almost no minimum — that is the whole point of setting
`Pango.EllipsizeMode.END`, and this OS does it deliberately and correctly in
many places (a user's list name, a track title, a filename must never be
allowed to set the window width). So when a translated UI string outgrows its
slot, the window does NOT overflow. The label silently shortens instead, and
minsize_sweep reports ALL FIT **because** the text is being cut. The gate is
green precisely when this defect happens.

Measured 2026-08-06 at 1024x740, with every gate in the tree green:

    calendar  caltitle   el         'Αυγούστου 2026'  ->  "Αυγούστου 2…"
    tasks     viewtitle  de, el     today's date, cut mid-word
    calendar  dowcell    de, ru, pl 'DONNERSTAG', 'ПОНЕДЕЛЬНИК', 'PONIEDZIAŁEK'
    bills     bl-footlabel  el      'ΠΡΟΣ ΠΛΗΡΩΜΗ ΑΥΤΟΝ ΤΟΝ ΜΗΝΑ'

The calendar one is the argument for this file: the app whose single job is to
tell you the date lost the YEAR out of its own heading, while the mini-calendar
in its sidebar printed the same month and year in full a few inches away.

ASK PANGO, DO NOT MEASURE IT YOURSELF. `Pango.Layout.is_ellipsized()` on the
label's OWN layout is the authoritative answer and the only one worth trusting.
The obvious alternative — build a fresh layout from the label's font and compare
its width to the allocation — is WRONG, and wrong in the direction that hides
the bug: a fresh `create_pango_layout()` does not carry the attributes GTK
applies from CSS. `.bl-footlabel` sets `letter-spacing: 0.12em`, so that method
measured the Greek caption at 193px against a 219px allocation and called it
comfortable, while the screen showed it cut. The label's own layout has the
spacing in it.

EMPTY PROFILE ON PURPOSE. Each app is measured against a throwaway NB_HOME, so
there is no user content anywhere in the tree. Every string a label holds is
therefore one the OS itself authored, which is what makes a cut worth
reporting — an ellipsized filename is a feature, an ellipsized caption is a
defect. It also means this tool cannot see data-driven cuts, the same blind
spot minsize_sweep has.

ONE APP PER PROCESS, for the reason minsize_sweep documents: GTK CSS providers
attach to the SCREEN, so an app measured after another inherits its stylesheet.

Exit status is non-zero if any label is cut.
"""
import importlib
import inspect
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DE = os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
W, H = 1024, 740

# The languages worth paying for. English is near the SHORTEST of the seventeen
# shipped, so a sweep that measures only the developer's locale sees almost
# nothing: it found ONE cut, in a list column that ellipsizes by design.
# Same set and same reasoning as minsize_sweep.RISK_LANGS.
LANGS = ("en", "de", "el", "ru", "pl")

# A label narrower than this has not been LAID OUT, whatever GTK says about it
# being mapped, and `is_ellipsized()` on it is meaningless — at a 1px allocation
# every non-empty string is "ellipsized". This guard exists because the page
# traversal below produced 136 confident findings without it: switching
# sequencer's stack to `mix` maps the channel strips but never allocates them
# (the app's own view change does more than set_visible_child, and the rendered
# page still showed ARRANGE), so all eight strip names came back cut in all
# seventeen languages — including `Bass` and `Drums` in English. Measured
# alloc_w=1 on every one. A probe must not report a verdict it did not earn,
# and an unallocated widget is "cannot check", never "cut".
MIN_ALLOC = 8

# Slots where an ellipsis is the correct behaviour, not a defect: a column of
# names in a table is SUPPOSED to cut a long one, and the user can read it in
# the detail pane beside it. Listed by style class so the exemption is narrow
# and visible rather than a threshold nobody can see.
BY_DESIGN = {
    # packages' installed-list name column — cuts in English too, at a width
    # the user chose, with the full name shown in the pane to its right.
    "cell-name",
}


def _one(app):
    """Measure one app in THIS process; only ever called in a --one child."""
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gtk, Pango

    sys.path.insert(0, DE)
    sys.path.insert(0, HERE)
    import uishot
    import dialogshot
    import nbapp

    uishot.load_theme()
    nbapp.screen_size = lambda: (W, H)
    mod = importlib.import_module(app)
    dialogshot.install_app_css(mod)
    cls = None
    for _n, c in inspect.getmembers(mod, inspect.isclass):
        if c.__module__ == mod.__name__ and issubclass(c, Gtk.Window):
            cls = c
            break
    if cls is None:
        return []
    win = cls()
    child = win.get_child()
    win.remove(child)
    off = Gtk.OffscreenWindow()
    off.set_size_request(W, H)
    off.add(child)
    off.show_all()
    for _ in range(80):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def pump(n=40):
        for _ in range(n):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)

    def relayout():
        """Force the page that was just switched to be ALLOCATED, not merely
        mapped. set_visible_child + a pump leaves the new page's widgets mapped
        with a 1px allocation, and `is_ellipsized()` on a 1px label is True for
        any text at all — which is how a first version of this traversal
        produced 136 confident findings, eight sequencer channel strips in all
        seventeen languages, `Bass` and `Drums` among them. Asking for the
        resize explicitly gives the real numbers (those strips get 88px and are
        not cut)."""
        child.queue_resize()
        off.check_resize()
        pump(120)

    unlaid = []

    def walk(kind):
        """Every mapped, laid-out, ellipsized label reachable right now."""
        out, stack = [], [child]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Container):
                stack.extend(w.get_children())
            if not isinstance(w, Gtk.Label):
                continue
            txt = (w.get_text() or "").strip()
            # get_mapped() is the load-bearing filter and the reason pages have
            # to be switched rather than merely walked: a Gtk.Stack keeps every
            # page in the widget tree, but only the VISIBLE page's labels are
            # mapped and laid out, and an unmapped label has no allocation to be
            # ellipsized against.
            if not txt or not w.get_mapped():
                continue
            lay = w.get_layout()
            if lay is None or not lay.is_ellipsized():
                continue
            classes = list(w.get_style_context().list_classes())
            if BY_DESIGN.intersection(classes):
                continue
            if w.get_allocated_width() < MIN_ALLOC:
                # Counted, never silently dropped: a page this tool could not
                # measure has to be visible in the output, or the guard above
                # turns a would-be finding into silence and the run looks clean
                # for a reason nobody can see.
                unlaid.append({"cls": " ".join(classes), "view": kind})
                continue
            out.append({"cls": " ".join(classes), "text": txt, "view": kind})
        return out

    def stacks():
        out, stack = [], [child]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Container):
                stack.extend(w.get_children())
            if isinstance(w, Gtk.Stack):
                out.append(w)
        return out

    found = walk("")
    # Then every other page of every Gtk.Stack. Width measurement does not need
    # this — a Stack is hhomogeneous by default and already reports the widest
    # page (see minsize_sweep.measure_one) — but ELLIPSIS is a property of the
    # laid-out label, so a heading that is cut on the Schedule tab is invisible
    # while Notes is showing. academics, cookbook and settings all keep text
    # behind tabs.
    for st in stacks():
        pages = st.get_children()
        if len(pages) < 2:
            continue
        opened = st.get_visible_child()
        for k in pages:
            if k is opened:
                continue
            try:
                st.set_visible_child(k)
            except Exception:                                  # noqa: BLE001
                continue
            relayout()
            found += walk(st.child_get_property(k, "name") or "?")
        if opened is not None:
            st.set_visible_child(opened)
            relayout()

    # One entry per distinct label: a page reached through two different stacks
    # is still one defect, and the first sighting names the view it was on.
    seen, uniq = set(), []
    for hit in found:
        key = (hit["cls"], hit["text"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(hit)
    off.destroy()
    seenu = set()
    for u in unlaid:
        key = (u["cls"], u["view"])
        if key not in seenu:
            seenu.add(key)
            uniq.append({"cls": u["cls"], "text": "", "view": u["view"],
                         "unlaid": True})
    return uniq


def main():
    import finder as _f
    argv = sys.argv[1:]
    langs = LANGS
    rest = []
    for a in argv:
        if a.startswith("--langs="):
            langs = tuple(x for x in a.split("=", 1)[1].split(",") if x)
        elif a == "--all-langs":
            # Every catalog in the tree. Slow (17x), but the default five are
            # Latin/Greek/Cyrillic only and say nothing about how CJK, Devanagari
            # or Hebrew metrics land in the same slots.
            langs = tuple(sorted(
                os.path.basename(p)[5:-5]
                for p in __import__("glob").glob(os.path.join(DE, "lang_*.json"))
            ) + ["en"])
        else:
            rest.append(a)
    apps = rest or sorted(set(_f.APP_MODULES.values()) | {"finder"})
    cuts = []
    for lang in langs:
        home = tempfile.mkdtemp(prefix="nbhome-ellip-%s-" % lang)
        env = dict(os.environ, NB_LANG=lang, NB_HOME=home)
        for app in apps:
            try:
                r = subprocess.run(
                    [sys.executable, os.path.abspath(__file__), "--one", app],
                    capture_output=True, text=True, timeout=180, env=env)
            except subprocess.TimeoutExpired:
                print("  %-11s %-3s TIMED OUT" % (app, lang))
                continue
            for ln in reversed((r.stdout or "").strip().splitlines()):
                if ln.startswith("["):
                    try:
                        for hit in json.loads(ln):
                            cuts.append((app, lang, hit["cls"], hit["text"],
                                         hit.get("view", "")))
                    except ValueError:
                        pass
                    break

    real = [c for c in cuts if c[3] != ""]
    unmeasured = sorted({(c[0], c[4], c[2]) for c in cuts if c[3] == ""})
    for app, lang, cls, txt, view in real:
        print("CUT  %-11s %-3s %-9s [%-22s] %r"
              % (app, lang, ("@" + view) if view else "", cls[:22], txt[:50]))
    if unmeasured:
        print("\nNOT MEASURED — mapped but never allocated (<%dpx), so whether "
              "the text is cut is unknown, not clean:" % MIN_ALLOC)
        for app, view, cls in unmeasured:
            print("  %-11s %-9s [%s]"
                  % (app, ("@" + view) if view else "", cls[:34]))
    cuts = real
    print("\n%d UI label(s) cut short at %dx%d across %s"
          % (len(cuts), W, H, "/".join(langs)))
    if not cuts:
        print("RESULT: every UI label spells itself out")
        return 0
    print("RESULT: %d CUT" % len(cuts))
    return 1


if __name__ == "__main__":
    sys.path.insert(0, DE)
    if len(sys.argv) > 2 and sys.argv[1] == "--one":
        print(json.dumps(_one(sys.argv[2])))
        sys.exit(0)
    sys.exit(main())
