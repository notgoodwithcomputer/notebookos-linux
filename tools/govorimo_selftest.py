#!/usr/bin/env python3
"""Protocol conformance suite for the Govorimo newline-JSON local API."""
from __future__ import annotations

import json
import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BIN = os.path.join(ROOT, "vendor", "govorimo", "govorimod")
BIN = os.environ.get("GOVORIMOD_BIN", DEFAULT_BIN)
WORDS_PATH = os.path.join(ROOT, "linux-ebyte-lora-chat", "daemon", "src", "bip39-english.txt")
BUILD_COMMAND = ("cd linux-ebyte-lora-chat && cargo build --release --target "
                 "x86_64-unknown-linux-musl -p govorimo-daemon && cp "
                 "target/x86_64-unknown-linux-musl/release/govorimod "
                 "../vendor/govorimo/")
PASSES: list[str] = []
FAILS: list[str] = []
MUTANTS: list[str] = []
UNCAUGHT: list[str] = []


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
        print("PASS " + name)
    else:
        FAILS.append(name)
        print("FAIL " + name + ((" - " + str(detail)[:500]) if detail else ""))


def mutant(name, caught, detail=""):
    if caught:
        MUTANTS.append(name)
        print("PASS-MUTANT " + name)
    else:
        UNCAUGHT.append(name)
        print("FAIL-MUTANT " + name + ((" - " + str(detail)[:500]) if detail else ""))


def error_code(value, code):
    return value.get("ok") is False and value.get("error", {}).get("code") == code


def valid_bundle(value):
    return isinstance(value, str) and value.startswith("govorimo:") and len(value) > 12


def valid_mnemonic(value, words):
    parts = value.split() if isinstance(value, str) else []
    return len(parts) == 24 and all(word in words for word in parts)


def same_safety(a, b):
    return isinstance(a, str) and bool(a) and a == b


def pages_do_not_overlap(first, second):
    return not ({x.get("seq") for x in first} & {x.get("seq") for x in second})


def genuine_delivered(events, sent_ids):
    return any(e.get("event") == "message_state"
               and e.get("data", {}).get("state") == "delivered"
               and e.get("data", {}).get("msgid") in sent_ids for e in events)


class Client:
    def __init__(self, path, hello=True):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2.0)
        self.sock.connect(path)
        self.buf = b""
        self.next_id = 0
        self.events: list[dict] = []
        self.responses: dict[int, dict] = {}
        if hello:
            self.ok("hello", client="govorimo-selftest", version="0.1")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def send_request(self, method, params=None, request_id=None):
        if request_id is None:
            self.next_id += 1
            request_id = self.next_id
        request = {"id": request_id, "method": method, "params": params or {}}
        self.sock.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
        return request_id

    def read_one(self, timeout=2.0):
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("socket response timeout")
            ready, _, _ = select.select([self.sock], [], [], left)
            if not ready:
                raise TimeoutError("socket response timeout")
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("daemon closed socket")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def response(self, request_id, timeout=15.0):
        if request_id in self.responses:
            return self.responses.pop(request_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.read_one(max(0.05, deadline - time.monotonic()))
            if value.get("id") == request_id:
                return value
            if "event" in value:
                self.events.append(value)
            elif isinstance(value.get("id"), int):
                self.responses[value["id"]] = value
        raise TimeoutError("no response for id %r" % request_id)

    def call(self, method, **params):
        return self.response(self.send_request(method, params))

    def ok(self, method, **params):
        value = self.call(method, **params)
        if value.get("ok") is not True:
            raise AssertionError("%s: %r" % (method, value.get("error")))
        return value.get("result")

    def pump(self, duration=0.15):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            try:
                value = self.read_one(max(0.01, deadline - time.monotonic()))
            except TimeoutError:
                break
            if "event" in value:
                self.events.append(value)

    def wait_event(self, name, timeout=15.0, where=None, consume=True):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for index, event in enumerate(self.events):
                data = event.get("data", {})
                if event.get("event") == name and (where is None or where(data)):
                    if consume:
                        self.events.pop(index)
                    return data
            try:
                value = self.read_one(max(0.02, deadline - time.monotonic()))
            except TimeoutError:
                continue
            if "event" in value:
                self.events.append(value)
        return None

    def assert_no_event(self, name, timeout, where=None):
        return self.wait_event(name, timeout, where, consume=False) is None


class Daemon:
    def __init__(self, root, name, wire):
        self.name = name
        self.state = os.path.join(root, name + "-state")
        self.path = os.path.join(root, name + ".sock")
        os.makedirs(self.state, exist_ok=True)
        self.wire = wire
        self.proc = None
        self.clients: list[Client] = []
        try:
            self.start()
        except Exception:
            self.stop()
            raise

    def start(self):
        child_env = os.environ.copy()
        child_env["XDG_RUNTIME_DIR"] = os.path.dirname(self.path)
        self.proc = subprocess.Popen(
            [BIN, "--socket", self.path, "--state-dir", self.state,
             "--radio", "wire:" + self.wire],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=child_env)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("daemon %s exited %s" % (self.name, self.proc.returncode))
            if os.path.exists(self.path):
                try:
                    probe = Client(self.path, hello=False)
                    probe.close()
                    return
                except OSError:
                    pass
            time.sleep(0.03)
        raise TimeoutError("socket did not appear: " + self.path)

    def client(self, hello=True):
        client = Client(self.path, hello)
        self.clients.append(client)
        return client

    def stop(self, kill=False):
        for client in self.clients:
            client.close()
        self.clients.clear()
        if self.proc is None or self.proc.poll() is not None:
            return
        if kill:
            self.proc.kill()
        else:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)

    def restart(self):
        self.stop()
        self.start()


def record(name, function):
    try:
        function()
    except Exception as exc:
        check(name, False, "%s: %s" % (type(exc).__name__, exc))


def main():
    started = time.monotonic()
    if not os.path.isfile(BIN) or not os.access(BIN, os.X_OK):
        check("binary available", False, "%s missing; build with: %s" % (BIN, BUILD_COMMAND))
        finish(started)
    with open(WORDS_PATH, encoding="ascii") as stream:
        bip39_words = frozenset(line.strip() for line in stream if line.strip())
    wire = "selftest-%d-%s" % (os.getpid(), os.urandom(5).hex())
    daemons: list[Daemon] = []
    clients: list[Client] = []
    temp = tempfile.TemporaryDirectory(prefix="govorimo-selftest-")
    try:
        a_d = Daemon(temp.name, "a", wire); daemons.append(a_d)
        b_d = Daemon(temp.name, "b", wire); daemons.append(b_d)
        c_d = Daemon(temp.name, "c", wire); daemons.append(c_d)
        r_d = Daemon(temp.name, "restore", wire); daemons.append(r_d)

        # Handshake & identity
        raw = a_d.client(hello=False); clients.append(raw)
        h = raw.ok("hello", client="selftest", version="0.1")
        check("handshake/01 hello identity", str(h.get("api_version", "")).startswith("0")
              and h.get("provisioned") is False and h.get("node_id") is None, h)
        first = b_d.client(hello=False); clients.append(first)
        refused = first.call("list_conversations")
        recovered = first.ok("hello", client="selftest", version="0.1")
        check("handshake/02 mandatory hello is recoverable",
              error_code(refused, "bad_request")
              and "first call must be `hello`" in refused.get("error", {}).get("message", "")
              and recovered.get("provisioned") is False, (refused, recovered))
        a = raw
        b = first
        before_id = c_d.client(); clients.append(before_id)
        check("handshake/08 identity-required call", error_code(before_id.call("get_contact_bundle"), "not_provisioned"))
        ia = a.ok("create_identity", display_name="Ana")
        check("handshake/03 create identity and mnemonic",
              isinstance(ia.get("node_id"), str) and len(ia["node_id"]) == 8
              and ia["node_id"] == ia["node_id"].lower()
              and all(ch in "0123456789abcdef" for ch in ia["node_id"])
              and valid_mnemonic(ia.get("recovery_mnemonic"), bip39_words), ia)
        check("handshake/04 duplicate identity", error_code(a.call("create_identity", display_name="Again"), "already_provisioned"))
        anew = a_d.client(); clients.append(anew)
        ah = anew.ok("hello", client="selftest", version="0.1")
        check("handshake/05 re-hello provisioned", ah.get("provisioned") is True
              and ah.get("node_id") == ia["node_id"] and ah.get("display_name") == "Ana", ah)
        r = r_d.client(); clients.append(r)
        restored = r.ok("restore_identity", recovery_mnemonic=ia["recovery_mnemonic"])
        restore_warning = restored.get("warning", "").lower()
        check("handshake/06 mnemonic restore warning", restored.get("node_id") == ia["node_id"]
              and ("re-exchange" in restore_warning or "exchanged again" in restore_warning), restored)
        garbage_d = Daemon(temp.name, "garbage", wire); daemons.append(garbage_d)
        garbage = garbage_d.client(); clients.append(garbage)
        check("handshake/07 garbage mnemonic", error_code(garbage.call("restore_identity", recovery_mnemonic="garbage " * 24), "bad_request"))

        ib = b.ok("create_identity", display_name="Bo")
        c = before_id
        ic = c.ok("create_identity", display_name="Cy")
        ba = a.ok("get_contact_bundle"); bb = b.ok("get_contact_bundle"); bc = c.ok("get_contact_bundle")

        # Bundles & contacts
        check("contacts/09 bundle and QR", valid_bundle(ba.get("bundle")) and "<svg" in ba.get("qr_svg", ""), ba)
        check("contacts/10 reject own bundle", error_code(a.call("add_contact", bundle=ba["bundle"]), "bad_request"))
        payload = ba["bundle"][len("govorimo:"):]
        tampered = "govorimo:" + ("!" if not payload.startswith("!") else "?") + payload[1:]
        damaged = b.call("add_contact", bundle=tampered)
        alive = b.ok("hello", client="selftest", version="0.1")
        check("contacts/11 tampered bundle survives", damaged.get("ok") is False and alive.get("node_id") == ib["node_id"], damaged)
        ab = a.ok("add_contact", bundle=bb["bundle"]); ba_contact = b.ok("add_contact", bundle=ba["bundle"])
        check("contacts/12 mutual safety number", same_safety(ab.get("safety_number"), ba_contact.get("safety_number"))
              and ab.get("display_name") == "Bo" and ba_contact.get("display_name") == "Ana", (ab, ba_contact))
        listed = a.ok("list_contacts")
        check("contacts/13 contact listing fields", any(x.get("node_id") == ib["node_id"]
              and x.get("display_name") == "Bo" and x.get("safety_number") for x in listed), listed)

        # Direct chat. Connect the second subscriber before traffic.
        a2 = a_d.client(); clients.append(a2)
        conv_a = a.ok("list_conversations"); conv_b = b.ok("list_conversations")
        tag = ab_tag = next(x["conv"] for x in conv_a if x.get("kind") == "direct" and ib["node_id"] in x.get("members", []))
        check("chat/14 matching direct conversation", any(x.get("conv") == tag and x.get("kind") == "direct" for x in conv_b), (conv_a, conv_b))
        sent = a.ok("send_text", conv=tag, text="hello Bo")
        bev = b.wait_event("message", where=lambda d: d.get("msgid") == sent["msgid"])
        a2ev = a2.wait_event("message_state", where=lambda d: d.get("msgid") == sent["msgid"], timeout=15)
        required = {"conv", "from", "msgid", "kind", "text", "clock", "at"}
        check("chat/15 text message event", bev is not None and required <= set(bev)
              and bev.get("conv") == tag and bev.get("from") == ia["node_id"]
              and bev.get("kind") == "text" and bev.get("text") == "hello Bo", bev)
        delivered = a.wait_event("message_state", timeout=15,
                                 where=lambda d: d.get("msgid") == sent["msgid"] and d.get("state") == "delivered")
        check("chat/16 real delivered receipt", delivered is not None, a.events[-8:])
        bh = b.ok("get_messages", conv=tag, limit=50); ahist = a.ok("get_messages", conv=tag, limit=50)
        check("chat/17 history direction and merged state",
              any(x.get("msgid") == sent["msgid"] and x.get("out") is False and x.get("kind") == "text" and x.get("text") == "hello Bo" for x in bh)
              and any(x.get("msgid") == sent["msgid"] and x.get("out") is True and x.get("state") == "delivered" for x in ahist), (bh, ahist))
        # The first get_messages cleared unread; create a fresh unread message.
        unread_sent = a.ok("send_text", conv=tag, text="unread marker")
        b.wait_event("message", where=lambda d: d.get("msgid") == unread_sent["msgid"])
        unread_before = next(x["unread"] for x in b.ok("list_conversations") if x["conv"] == tag)
        b.ok("get_messages", conv=tag, limit=50)
        unread_after = next(x["unread"] for x in b.ok("list_conversations") if x["conv"] == tag)
        check("chat/18 unread clearing", unread_before >= 1 and unread_after == 0, (unread_before, unread_after))
        reply = a.ok("send_reply", conv=tag, target_msgid=sent["msgid"], text="reply text")
        reply_ev = b.wait_event("message", where=lambda d: d.get("msgid") == reply["msgid"])
        check("chat/19 reply target", reply_ev is not None and reply_ev.get("kind") == "reply"
              and reply_ev.get("target") == sent["msgid"] and reply_ev.get("text") == "reply text", reply_ev)
        reaction = a.ok("send_reaction", conv=tag, target_msgid=sent["msgid"], emoji="👍")
        reaction_ev = b.wait_event("message", where=lambda d: d.get("msgid") == reaction["msgid"])
        oversized_emoji = a.call("send_reaction", conv=tag, target_msgid=sent["msgid"], emoji="🙂" * 5)
        check("chat/20 reaction and emoji cap", reaction_ev is not None and reaction_ev.get("kind") == "reaction"
              and reaction_ev.get("emoji") == "👍" and error_code(oversized_emoji, "too_large"), (reaction_ev, oversized_emoji))
        page_ids = []
        for index in range(8):
            page_ids.append(a.ok("send_text", conv=tag, text="page-%d" % index)["msgid"])
        for msgid in page_ids:
            b.wait_event("message", timeout=15, where=lambda d, mid=msgid: d.get("msgid") == mid)
        page1 = b.ok("get_messages", conv=tag, limit=3)
        page2 = b.ok("get_messages", conv=tag, before=min(x["seq"] for x in page1), limit=3)
        check("chat/21 message pagination", len(page1) == 3 and len(page2) == 3
              and pages_do_not_overlap(page1, page2)
              and [x.get("text") for x in page1] == ["page-5", "page-6", "page-7"], (page1, page2))
        check("chat/22 unknown conversation", error_code(a.call("send_text", conv="deadbeef", text="x"), "unknown_conversation"))

        # Make A/B/C mutual contacts for group checks.
        ac = a.ok("add_contact", bundle=bc["bundle"]); c.ok("add_contact", bundle=ba["bundle"])
        b.ok("add_contact", bundle=bc["bundle"]); c.ok("add_contact", bundle=bb["bundle"])
        group = a.ok("create_group", name="Three Friends", members=[ib["node_id"], ic["node_id"]])["conv"]
        deadline = time.monotonic() + 15
        bg = None
        while time.monotonic() < deadline:
            bg = next((x for x in b.ok("list_conversations") if x.get("conv") == group), None)
            if bg and bg.get("name") == "Three Friends": break
            time.sleep(.15)
        check("groups/23 group propagation", bg is not None and bg.get("kind") == "group" and bg.get("name") == "Three Friends", bg)
        gsent = a.ok("send_text", conv=group, text="group hello")
        gev = b.wait_event("message", timeout=15, where=lambda d: d.get("conv") == group and d.get("msgid") == gsent["msgid"])
        # Drain ten seconds while asserting that a forbidden receipt never appears.
        no_group_delivery = a.assert_no_event("message_state", 10,
                                              lambda d: d.get("msgid") == gsent["msgid"] and d.get("state") == "delivered")
        check("groups/24 group text without delivered ack", gev is not None and gev.get("text") == "group hello" and no_group_delivery, (gev, a.events[-8:]))

        # Awaiting-key topology uses a fresh daemon D: A knows all, B and D do not know each other.
        d_d = Daemon(temp.name, "d", wire); daemons.append(d_d)
        d = d_d.client(); clients.append(d); ident_d = d.ok("create_identity", display_name="Dee")
        bd = d.ok("get_contact_bundle")
        a.ok("add_contact", bundle=bd["bundle"]); d.ok("add_contact", bundle=ba["bundle"])
        isolated_group = a.ok("create_group", name="Missing Key", members=[ib["node_id"], ident_d["node_id"]])["conv"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not any(x.get("conv") == isolated_group for x in d.ok("list_conversations")):
            time.sleep(.15)
        dsend = d.ok("send_text", conv=isolated_group, text="cipher only")
        awaiting = b.wait_event("awaiting_key", timeout=15,
                                where=lambda data: data.get("conv") == isolated_group and data.get("from") == ident_d["node_id"])
        no_plain = b.assert_no_event("message", 2,
                                     lambda data: data.get("conv") == isolated_group and data.get("from") == ident_d["node_id"] and data.get("msgid") == dsend["msgid"])
        check("groups/25 awaiting key without plaintext", awaiting is not None and no_plain, (awaiting, b.events[-10:]))

        # Membership costs and removal exclusion use D in a separate group.
        mutable = a.ok("create_group", name="Mutable", members=[ib["node_id"]])["conv"]
        add_result = a.ok("add_member", conv=mutable, node_id=ident_d["node_id"])
        time.sleep(.5)
        remove_result = a.ok("remove_member", conv=mutable, node_id=ib["node_id"])
        check("groups/26 membership airtime costs", add_result.get("airtime_cost_s", 0) > 0
              and remove_result.get("airtime_cost_s", 0) > 0, (add_result, remove_result))
        time.sleep(1)
        excluded = a.ok("send_text", conv=mutable, text="after removal")
        check("groups/27 removed member excluded", b.assert_no_event("message", 8,
              lambda data: data.get("conv") == mutable and data.get("msgid") == excluded["msgid"]), b.events[-8:])

        # Boards
        board_a = a.ok("follow_board", name="local.general")
        board_b = b.ok("follow_board", name="local.general")
        check("boards/28 deterministic board id", board_a.get("board_id") == board_b.get("board_id"), (board_a, board_b))
        # The daemon CANONICALISES case before validating and deriving, so an
        # uppercase spelling must land on the SAME board — the wire never
        # carries uppercase and no alias board can exist. Structurally bad
        # names (over 64 bytes, empty, non-hierarchical characters) reject.
        upper = a.call("follow_board", name="LOCAL.general")
        check("boards/29a uppercase canonicalises to the same board",
              upper.get("ok") and upper["result"].get("board_id") == board_a.get("board_id"),
              (upper, board_a))
        bad_board_results = [a.call("follow_board", name=name)
                             for name in ("x" * 65, "", "local general", "local_general")]
        check("boards/29b structurally bad names reject",
              all(error_code(x, "bad_request") for x in bad_board_results), bad_board_results)
        bid = board_a["board_id"]
        post = a.ok("post", board_id=bid, text="first board post")
        pev = b.wait_event("post", timeout=15, where=lambda data: data.get("msgid") == post["msgid"])
        check("boards/30 verified post event", pev is not None and pev.get("text") == "first board post"
              and pev.get("author") == "Ana" and pev.get("verified") is True and pev.get("board_id") == bid, pev)
        check("boards/31 post size cap", error_code(a.call("post", board_id=bid, text="é" * 67), "too_large"))
        post_ids = []
        for index in range(7):
            post_ids.append(a.ok("post", board_id=bid, text="board-%d" % index)["msgid"])
        ppage1 = a.ok("get_posts", board_id=bid, limit=3)
        ppage2 = a.ok("get_posts", board_id=bid, before=min(x["seq"] for x in ppage1), limit=3)
        check("boards/32 post pagination and ownership", len(ppage1) == len(ppage2) == 3
              and pages_do_not_overlap(ppage1, ppage2)
              and all(x.get("board_id") == bid and x.get("own") is True for x in ppage1 + ppage2), (ppage1, ppage2))
        check("boards/33 unknown board", error_code(a.call("post", board_id="deadbeef", text="x"), "unknown_board"))
        parent = post_ids[-1]
        threaded = a.ok("post", board_id=bid, text="thread reply", parent_msgid=parent)
        thread_ev = b.wait_event("post", timeout=15, where=lambda data: data.get("msgid") == threaded["msgid"])
        thread_entries = a.ok("get_posts", board_id=bid, limit=30)
        check("boards/34 reply threading", thread_ev is not None and thread_ev.get("parent") == parent
              and any(x.get("msgid") == threaded["msgid"] and x.get("parent") == parent for x in thread_entries), (thread_ev, thread_entries[-3:]))

        # Moderation & status
        a.ok("block", node_id=ib["node_id"])
        blocked = a.ok("list_blocked")
        blocked_send = b.ok("send_text", conv=ab_tag, text="blocked traffic")
        blocked_quiet = a.assert_no_event("message", 4, lambda data: data.get("msgid") == blocked_send["msgid"] and data.get("from") == ib["node_id"])
        a.ok("unblock", node_id=ib["node_id"])
        resumed_send = b.ok("send_text", conv=ab_tag, text="resumed traffic")
        resumed = a.wait_event("message", timeout=15, where=lambda data: data.get("msgid") == resumed_send["msgid"] and data.get("from") == ib["node_id"])
        check("moderation/35 block and unblock", blocked_quiet
              and any(x.get("id") == ib["node_id"] and x.get("kind") == "node" for x in blocked)
              and resumed is not None and resumed.get("text") == "resumed traffic", (blocked, resumed))
        status = a.ok("get_status")
        airtime = status.get("airtime", {}); radios = status.get("radios", [])
        check("status/36 status shape", status.get("node_id") == ia["node_id"] and status.get("role") == "leaf"
              and bool(radios) and "path" in radios[0]
              and {"used_pct", "budget_pct", "window_s"} <= set(airtime)
              and "queue_depth" in status and "channel_busy_pct" in status, status)
        relay = a.ok("set_role", role="relay"); relay_status = a.ok("get_status")
        bad_role = a.call("set_role", role="garbage")
        check("status/37 roles", relay == {"role": "relay"} and relay_status.get("role") == "relay"
              and error_code(bad_role, "bad_request"), (relay, relay_status, bad_role))
        content = [a.call("send_attachment", conv=tag, path="/tmp/nope"),
                   a.call("fetch_content", content_id="deadbeef"),
                   a.call("request_backfill", board_id=bid)]
        check("status/38 honest content methods", all(error_code(x, "unimplemented") for x in content), content)

        # Robustness & persistence
        malformed = a_d.client(); clients.append(malformed)
        malformed.sock.sendall(b"{broken json\n")
        bad_line = malformed.read_one(5)
        good_after = malformed.ok("hello", client="selftest", version="0.1")
        check("robustness/39 malformed JSON recovery", bad_line.get("id") is None
              and error_code(bad_line, "bad_request") and good_after.get("node_id") == ia["node_id"], bad_line)
        check("robustness/40 unknown method", error_code(a.call("definitely_not_a_method"), "unknown_method"))
        pipe = a_d.client(); clients.append(pipe)
        ids = [101, 102, 103]
        for request_id in ids:
            pipe.send_request("hello", {"client": "pipeline", "version": "0.1"}, request_id)
        responses = [pipe.response(request_id) for request_id in ids]
        check("robustness/41 pipelining", {x.get("id") for x in responses} == set(ids) and all(x.get("ok") for x in responses), responses)

        before_restart = a.ok("send_text", conv=tag, text="before restart")
        b.wait_event("message", timeout=15, where=lambda data: data.get("msgid") == before_restart["msgid"])
        a_d.restart(); a = a_d.client(); clients.append(a)
        restart_h = a.ok("hello", client="selftest", version="0.1")
        restart_convs = a.ok("list_conversations")
        restart_hist = a.ok("get_messages", conv=tag, limit=100)
        after_restart = a.ok("send_text", conv=tag, text="after restart")
        check("robustness/42 restart persistence", restart_h.get("provisioned") is True
              and restart_h.get("node_id") == ia["node_id"]
              and any(x.get("conv") == tag for x in restart_convs)
              and any(x.get("msgid") == before_restart["msgid"] for x in restart_hist)
              and after_restart["msgid"] > before_restart["msgid"], (restart_h, after_restart, before_restart))

        b_d.stop(kill=True)
        stale_exists = os.path.exists(b_d.path)
        b_d.start(); b = b_d.client(); clients.append(b)
        stale_h = b.ok("hello", client="selftest", version="0.1")
        check("robustness/43 stale socket takeover", stale_exists and stale_h.get("node_id") == ib["node_id"], stale_h)

        subscriber1 = b_d.client(); subscriber2 = b_d.client(); clients.extend([subscriber1, subscriber2])
        both_send = a.ok("send_text", conv=tag, text="two subscribers")
        ev1 = subscriber1.wait_event("message", timeout=15, where=lambda data: data.get("msgid") == both_send["msgid"])
        ev2 = subscriber2.wait_event("message", timeout=15, where=lambda data: data.get("msgid") == both_send["msgid"])
        both_calls = subscriber1.ok("get_status") and subscriber2.ok("list_conversations")
        check("robustness/44 two clients receive and call", ev1 is not None and ev2 is not None and bool(both_calls), (ev1, ev2))

        # In-process detector mutants.
        fake = [{"event": "message_state", "data": {"msgid": 999999, "state": "delivered"}}]
        mutant("M1 fabricated delivered receipt", not genuine_delivered(fake, {sent["msgid"]}))
        mutant("M2 different safety numbers", not same_safety("1111 2222", "3333 4444"))
        mutant("M3 bundle without prefix", not valid_bundle(ba["bundle"][len("govorimo:"):]))
        mutant("M4 malformed mnemonic", not valid_mnemonic(" ".join(ia["recovery_mnemonic"].split()[:23]), bip39_words)
               and not valid_mnemonic(" ".join(ia["recovery_mnemonic"].split()[:23] + ["not-a-bip39-word"]), bip39_words))
        mutant("M5 overlapping pagination", not pages_do_not_overlap([{"seq": 8}, {"seq": 9}], [{"seq": 7}, {"seq": 8}]))
    except Exception as exc:
        check("suite execution", False, "%s: %s" % (type(exc).__name__, exc))
    finally:
        for client in clients:
            client.close()
        for daemon in reversed(daemons):
            daemon.stop()
        temp.cleanup()
    finish(started)


def finish(started):
    elapsed = time.monotonic() - started
    print("RESULT: %d passed, %d failed, %d mutants (%d uncaught), %.1fs" %
          (len(PASSES), len(FAILS), len(MUTANTS), len(UNCAUGHT), elapsed))
    raise SystemExit(1 if FAILS or UNCAUGHT else 0)


if __name__ == "__main__":
    main()
