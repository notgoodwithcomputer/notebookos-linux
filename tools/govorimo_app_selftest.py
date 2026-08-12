#!/usr/bin/env python3
"""Display battery for the Govorimo app: the real GTK window driven against
real govorimod daemons on a wire radio. Protocol conformance lives in
govorimo_selftest.py; THIS suite owes the truth about the app itself — the
wizard ceremony, live transcripts, honest delivery glyphs, unread accounting,
error surfacing, prefs restoration, store damage, and survival of a daemon
restart. Two module mutants prove the load-bearing detectors can fail."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
BIN = os.environ.get("GOVORIMOD_BIN", os.path.join(REPO, "vendor/govorimo/govorimod"))
sys.path.insert(0, DE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("FONTCONFIG_FILE", os.path.join(REPO, "tools/guest-fonts.conf"))

PASSES: list[str] = []
FAILS: list[str] = []
SKIPS: list[str] = []
MUTANTS: list[str] = []
UNCAUGHT: list[str] = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print("PASS " + name)
    else:
        FAILS.append(name)
        print("FAIL %s - %s" % (name, str(detail)[:220]))


def mutant(name, caught, detail=""):
    if caught:
        MUTANTS.append(name)
        print("PASS-MUTANT " + name)
    else:
        UNCAUGHT.append(name)
        print("FAIL-MUTANT %s - %s" % (name, str(detail)[:180]))


def finish():
    print("\nRESULT: %d passed, %d failed, %d skipped, %d mutants (%d uncaught)"
          % (len(PASSES), len(FAILS), len(SKIPS), len(MUTANTS), len(UNCAUGHT)))
    sys.exit(1 if FAILS or UNCAUGHT else 0)


if not os.path.exists(BIN):
    check("daemon binary vendored", False,
          "%s missing; run tools/build_govorimod.sh" % BIN)
    finish()

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

if not Gtk.init_check()[0]:
    # An honest blocked measurement, loudly: this battery cannot run headless.
    print("SKIP display battery - Gtk.init_check() is False (no display)")
    SKIPS.append("display")
    print("\nRESULT: 0 passed, 0 failed, 1 skipped, 0 mutants (0 uncaught)")
    sys.exit(0)

import govorimolib  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="govapp-")
os.environ["NB_HOME"] = os.path.join(ROOT, "home")
os.makedirs(os.path.join(os.environ["NB_HOME"], ".config", "notebook"),
            exist_ok=True)
WIRE = "apptest%d" % os.getpid()
DAEMONS: dict[str, subprocess.Popen] = {}


def spawn(who, radio=None):
    st = os.path.join(ROOT, who)
    os.makedirs(st, exist_ok=True)
    sock = os.path.join(ROOT, who + ".sock")
    p = subprocess.Popen([BIN, "--socket", sock, "--state-dir", st,
                          "--radio", radio or ("wire:" + WIRE)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    DAEMONS[who] = p
    for _ in range(100):
        if os.path.exists(sock):
            break
        time.sleep(0.05)
    return sock


def pump(secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.005)


def pump_until(cond, secs=10.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        if cond():
            return True
        time.sleep(0.005)
    return False


def key_event(keyval, state=0):
    ev = Gdk.EventKey()
    ev.type = Gdk.EventType.KEY_PRESS
    ev.keyval = keyval
    ev.state = Gdk.ModifierType(state)
    return ev


def fresh_app(module, sock):
    os.environ["GOVORIMO_SOCKET"] = sock
    app = module.GovorimoWindow()
    return app


def main():
    import importlib

    # ---------------------------------------------------------------- seed
    sock_a = spawn("vera")
    sock_b = spawn("miro")
    A = govorimolib.BlockingClient(sock_a, "seed-a")
    B = govorimolib.BlockingClient(sock_b, "seed-b")
    A.ok("create_identity", display_name="Vera")
    B.ok("create_identity", display_name="Miro")
    A.ok("add_contact", bundle=B.ok("get_contact_bundle")["bundle"])
    B.ok("add_contact", bundle=A.ok("get_contact_bundle")["bundle"])
    conv = A.ok("list_conversations")[0]["conv"]
    B.ok("send_text", conv=conv, text="market runs till two")
    time.sleep(0.5)
    bid = A.ok("follow_board", name="local.general")["board_id"]
    B.ok("follow_board", name="local.general")
    B.ok("post", board_id=bid, text="free pallets behind the co-op")
    time.sleep(0.5)
    A.close()

    import govorimo
    app = fresh_app(govorimo, sock_a)
    ok = pump_until(lambda: app.link.state == govorimolib.READY
                    and app._convs and app._boards and app._contacts, 12)
    check("app reaches READY with data", ok,
          (app.link.state, len(app._convs), len(app._boards)))
    check("hello identity cached", app._me_name == "Vera", app._me_name)

    # ------------------------------------------------------- chats surface
    app._pick_conv(conv)
    pump(1.0)
    rows = [c for c in app._chat_rows.get_children()
            if isinstance(c, Gtk.EventBox)]
    check("transcript renders the stored message", len(rows) >= 1, len(rows))
    check("composer enabled with a conversation open",
          app._entry.get_sensitive() and app._send_btn.get_sensitive())

    app._entry.set_text("jars are in the truck")
    pump(0.1)
    check("price line prices the draft",
          "209" in app._price.get_text(), app._price.get_text())
    before_ids = {e.get("msgid") for e in app._messages.get(conv, [])
                  if e.get("out")}
    app._send_current()

    def newest_own():
        # The optimistic append is ASYNC; only an id that was not there
        # before the send is the new message (history may already carry
        # delivered rows, and racing this capture made a mutant pass once).
        own = [e for e in app._messages.get(conv, []) if e.get("out")
               and e.get("msgid") is not None
               and e.get("msgid") not in before_ids]
        return own[-1] if own else {}
    pump_until(lambda: newest_own().get("msgid") is not None, 8)
    sent_id = newest_own().get("msgid")
    # Scoped to THIS send's msgid: history already holds delivered rows, and
    # an unscoped any() passes against them with state events broken (the
    # vacuous probe mutant M1 originally exposed).
    got_delivered = pump_until(
        lambda: any(e.get("out") and e.get("msgid") == sent_id
                    and e.get("state") == "delivered"
                    for e in app._messages.get(conv, [])), 15)
    check("own send reaches delivered (receipt observed)", got_delivered,
          [(e.get("msgid"), e.get("state"))
           for e in app._messages.get(conv, []) if e.get("out")])
    glyphs = []
    for ebox in app._chat_rows.get_children():
        row = ebox.get_child() if isinstance(ebox, Gtk.EventBox) else None
        if row is None:
            continue
        for side in row.get_children():
            if isinstance(side, Gtk.Box):
                for w in side.get_children():
                    if getattr(w, "_gv_msgid", None) is not None:
                        glyphs.append(w.get_label() or w.get_text())
    check("delivered glyph drawn in the transcript",
          any("✓" in (g or "") for g in glyphs), glyphs)

    over = "x" * 240
    app._entry.set_text(over)
    pump(0.1)
    check("over-frame draft told the truth",
          app._price.get_style_context().has_class("gvover"),
          app._price.get_text())
    app._entry.set_text("")

    # Unread accounting: a message arrives while looking elsewhere.
    app._show_surface("radio")
    pump(0.3)
    Bc = govorimolib.BlockingClient(sock_b, "seed-b2")
    Bc.ok("send_text", conv=conv, text="bring the jars back")
    got_unread = pump_until(
        lambda: any(int(c.get("unread") or 0) > 0 for c in app._convs), 12)
    check("unread increments while not viewing", got_unread, app._convs)
    badge = app._rail_rows["chats"][1]
    check("rail badge shows the unread count",
          badge.get_visible() and badge.get_text() != "", badge.get_text())
    app._pick_conv(conv)
    pump_until(lambda: all(int(c.get("unread") or 0) == 0
                           for c in app._convs), 8)
    check("opening the conversation clears unread",
          all(int(c.get("unread") or 0) == 0 for c in app._convs), app._convs)

    # ------------------------------------------------------- boards surface
    app._pick_board(bid)
    pump(1.0)
    posts = [c for c in app._board_rows.get_children()
             if isinstance(c, Gtk.EventBox)]
    check("board posts render", len(posts) >= 1, len(posts))
    app._post_entry.set_text("y" * 140)
    pump(0.1)
    check("board over-limit told the truth",
          app._post_note.get_style_context().has_class("gvover"),
          app._post_note.get_text())
    app._post_entry.set_text("found a grey dog by the weir")
    app._post_current()
    landed = pump_until(lambda: any(p.get("own")
                                    for p in app._posts.get(bid, [])), 10)
    check("own post lands in the board view", landed)

    # ------------------------------------------------------- people surface
    app._show_surface("people")
    pump(0.5)
    check("contact row renders",
          len(app._contact_rows.get_children()) >= 1)
    pump_until(lambda: app._neighbours, 8)
    app._render_people()
    check("neighbour heard after traffic", bool(app._neighbours),
          app._neighbours)

    # ------------------------------------------------------------ overlays
    app._open_exchange()
    pump(0.5)
    check("exchange card opens with the bundle",
          app._card is not None and getattr(app, "_my_bundle", "")
          .startswith("govorimo:"), getattr(app, "_my_bundle", "")[:24])
    handled = app._on_key(app, key_event(Gdk.KEY_Escape))
    check("Esc closes the card (the ladder's first rung)",
          handled and app._card is None)

    # ------------------------------------------- prefs persistence + damage
    app._show_surface("boards")
    app.destroy()
    pump(0.3)
    del sys.modules["govorimo"]
    govorimo2 = importlib.import_module("govorimo")
    app2 = fresh_app(govorimo2, sock_a)
    pump_until(lambda: app2.link.state == govorimolib.READY, 10)
    check("surface restored on reopen", app2._surface == "boards",
          app2._surface)
    app2.destroy()
    pump(0.3)

    store = os.path.join(os.environ["NB_HOME"], ".config", "notebook",
                         "govorimo.json")
    with open(store, "w") as f:
        f.write('{"surface": "chats", INVALID')
    del sys.modules["govorimo"]
    govorimo3 = importlib.import_module("govorimo")
    app3 = fresh_app(govorimo3, sock_a)
    pump(0.5)
    damaged = [n for n in os.listdir(os.path.dirname(store))
               if n.startswith("govorimo.json.damaged-")]
    check("damaged prefs preserved, never overwritten blind",
          damaged, os.listdir(os.path.dirname(store)))
    check("app runs on defaults after damage",
          app3._surface in ("chats", "boards", "people", "radio"))

    # -------------------------------------------------- daemon restart path
    DAEMONS["vera"].terminate()
    DAEMONS["vera"].wait()
    down = pump_until(lambda: app3.link.state == govorimolib.CONNECTING, 8)
    check("link notices the daemon leaving", down, app3.link.state)
    check("waiting banner shown while down",
          app3._link_banner.get_visible())
    spawn("vera")   # same state-dir and socket
    back = pump_until(lambda: app3.link.state == govorimolib.READY, 12)
    check("link recovers after daemon restart", back, app3.link.state)
    pump_until(lambda: not app3._link_banner.get_visible(), 5)
    check("banner leaves when the daemon returns",
          not app3._link_banner.get_visible())
    app3.destroy()
    pump(0.3)

    # ------------------------------------------------------------- mutants
    # Module mutants: a scratch copy of the app with one load-bearing line
    # broken must redden the exact check that guards it.
    src = open(os.path.join(DE, "govorimo.py")).read()

    def module_mutant(name, old, new, probe):
        assert old in src, "mutant target drifted: " + old[:60]
        mdir = tempfile.mkdtemp(prefix="govmut-")
        with open(os.path.join(mdir, name + ".py"), "w") as f:
            f.write(src.replace(old, new, 1))
        sys.path.insert(0, mdir)
        try:
            mod = importlib.import_module(name)
            return probe(mod)
        finally:
            sys.path.remove(mdir)
            sys.modules.pop(name, None)

    def probe_state(mod):
        m_app = fresh_app(mod, os.path.join(ROOT, "vera.sock"))
        pump_until(lambda: m_app.link.state == govorimolib.READY
                   and m_app._convs, 10)
        m_app._pick_conv(conv)
        pump(0.8)
        prior = {e.get("msgid") for e in m_app._messages.get(conv, [])
                 if e.get("out")}
        m_app._entry.set_text("mutant probe")
        m_app._send_current()

        def own():
            return [e for e in m_app._messages.get(conv, []) if e.get("out")
                    and e.get("msgid") is not None
                    and e.get("msgid") not in prior]
        pump_until(lambda: own() and own()[-1].get("msgid") is not None, 8)
        mid = own()[-1].get("msgid") if own() else None
        got = pump_until(
            lambda: any(e.get("msgid") == mid and e.get("state") == "delivered"
                        for e in own()), 10)
        m_app.destroy()
        pump(0.2)
        return got

    # M1: message_state events dropped -> delivered can never be observed.
    caught = not module_mutant(
        "govmut_state",
        'elif name == "message_state":',
        'elif name == "message_state_NEVER":',
        probe_state)
    mutant("M1 dropped state events redden the delivered check", caught)

    def probe_unread(mod):
        m_app = fresh_app(mod, os.path.join(ROOT, "vera.sock"))
        pump_until(lambda: m_app.link.state == govorimolib.READY
                   and m_app._convs, 10)
        m_app._show_surface("radio")
        Bc2 = govorimolib.BlockingClient(os.path.join(ROOT, "miro.sock"), "m")
        Bc2.ok("send_text", conv=conv, text="unread mutant probe")
        got = pump_until(
            lambda: any(int(c.get("unread") or 0) > 0 for c in m_app._convs), 8)
        Bc2.close()
        m_app.destroy()
        pump(0.2)
        return got

    # M2: unread accounting removed -> the badge check must fail.
    caught2 = not module_mutant(
        "govmut_unread",
        'c["unread"] = int(c.get("unread") or 0) + 1',
        'pass  # mutant: unread never counted',
        probe_unread)
    mutant("M2 dropped unread accounting reddens the badge check", caught2)

    Bc.close()
    finish()


try:
    main()
finally:
    for p in DAEMONS.values():
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            p.kill()
