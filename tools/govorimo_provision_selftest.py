#!/usr/bin/env python3
"""The E22 provisioner against a scripted dongle on a pty.

govorimolib's provisioning half speaks the Ebyte register protocol
byte-for-byte (spec/07-radio-profile.md §3, mirrored from the reference
driver's regs.rs). The app suite proves the ceremony's UI and the
supervisor rehearsal proves the plumbing; THIS suite owes the serial
truth: which bytes leave the host, which replies mean what, and that a
probe never writes. A FakeE22 answers on the master end of a pty exactly
as the module's manual says — config-mode reads/writes, FF FF FF on a
malformed frame, the C0C1C2C3 command channel gated on the REG1 bits the
profile is supposed to set — and three mutants make the fake lie to prove
the detectors can fire. A pty has no real baud, so the fake answers at
any rate; the 9600-vs-115200 fallback order is exercised, not the wire
speed itself (that needs the physical stick)."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DE = os.path.join(REPO, "buildroot/board/notebookos/rootfs-overlay/opt/notebook/de")
sys.path.insert(0, DE)

import govorimolib  # noqa: E402

PASSES: list[str] = []
FAILS: list[str] = []
MUTANTS: list[str] = []
UNCAUGHT: list[str] = []

FACTORY = bytes((0x00, 0x00, 0x00, 0x62, 0x00, 0x12, 0x03, 0x00, 0x00))
PROFILE = govorimolib.PROFILE_US


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print("PASS " + name)
    else:
        FAILS.append(name)
        print("FAIL %s - %s" % (name, str(detail)[:200]))


def mutant(name, caught, detail=""):
    if caught:
        MUTANTS.append(name)
        print("PASS-MUTANT " + name)
    else:
        UNCAUGHT.append(name)
        print("FAIL-MUTANT %s - %s" % (name, str(detail)[:160]))


class FakeE22:
    """The module's manual, executable. Runs on the pty master; the code
    under test opens the slave like a real port."""

    def __init__(self, mode="config", regs=FACTORY):
        self.master, self.slave = os.openpty()
        self.path = os.ttyname(self.slave)
        self.mode = mode
        self.regs = bytearray(regs)
        self.frames = []          # every command frame parsed off the wire
        self.swallowed = b""      # transfer-mode bytes that would go ON AIR
        self.inject_write_error = False   # answer FF FF FF to a C0 write
        self.lie_readback = None  # regs served to reads, if not the truth
        self.lie_ambient = False  # answer the ambient query without REG1.5
        self.mangle_store = None  # fn(bytearray) run after a write stores
        self._stop = False
        self._buf = b""
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def close(self):
        # Join BEFORE closing: freed fd numbers are reused by the very next
        # openpty, and a serve thread still parked in select() would then
        # read the NEXT fake's wire and answer it with THIS fake's stale
        # registers. Seen live — check 13 received another test's factory
        # bytes — so the order here is load-bearing, not tidiness.
        self._stop = True
        self._t.join(timeout=2.0)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass

    # -- wire

    def _serve(self):
        import select
        while not self._stop:
            try:
                r, _, _ = select.select([self.master], [], [], 0.05)
            except OSError:
                return
            if self._stop or not r:
                if self._stop:
                    return
                continue
            try:
                data = os.read(self.master, 256)
            except OSError:
                return
            if not data:
                continue
            self._buf += data
            self._consume()

    def _reply(self, b):
        try:
            os.write(self.master, bytes(b))
        except OSError:
            pass

    def _consume(self):
        while self._buf:
            b = self._buf
            # The in-transfer command channel exists in BOTH modes for the
            # mode switch; the ambient query only in transfer mode.
            if b[:4] == b"\xc0\xc1\xc2\xc3":
                if len(b) < 6:
                    return
                frame, self._buf = b[:6], b[6:]
                self.frames.append(bytes(frame))
                addr, val = frame[4], frame[5]
                if addr == 0x02 and (self.regs[4] & 0x04):
                    self.mode = "config" if val == 0x01 else "transfer"
                elif addr == 0x00 and val == 0x01 and self.mode == "transfer":
                    if (self.regs[4] & 0x20) or self.lie_ambient:
                        self._reply(b"\xc1\x00\x01\x40")
                continue
            if self.mode == "transfer":
                # Transparent: everything else would be TRANSMITTED.
                self.swallowed += b[:1]
                self._buf = b[1:]
                continue
            # -- configuration mode
            if b[0] == 0xC1:
                if len(b) < 3:
                    return
                frame, self._buf = b[:3], b[3:]
                self.frames.append(bytes(frame))
                start, n = frame[1], frame[2]
                if start + n > len(self.regs) or n == 0:
                    self._reply(b"\xff\xff\xff")
                    continue
                src = self.lie_readback if self.lie_readback is not None \
                    else self.regs
                self._reply(bytes((0xC1, start, n)) + bytes(src[start:start + n]))
            elif b[0] in (0xC0, 0xC2):
                if len(b) < 3 or len(b) < 3 + b[2]:
                    return
                n = b[2]
                frame, self._buf = b[:3 + n], b[3 + n:]
                self.frames.append(bytes(frame))
                start = frame[1]
                if self.inject_write_error or start + n > len(self.regs):
                    self._reply(b"\xff\xff\xff")
                    continue
                self.regs[start:start + n] = frame[3:3 + n]
                if self.mangle_store is not None:
                    self.mangle_store(self.regs)
                # Echo the written values; CRYPT reads back as zeros.
                echo = bytearray(self.regs[start:start + n])
                for i in range(start, start + n):
                    if i in (7, 8):
                        echo[i - start] = 0
                self._reply(bytes((0xC1, start, n)) + bytes(echo))
            else:
                self._buf = b[1:]   # noise byte; a real module ignores it


def writes_of(fake):
    return [f for f in fake.frames if f[0] in (0xC0, 0xC2)
            and f[:4] != b"\xc0\xc1\xc2\xc3"]


def main():
    # 1-2: probe of a factory stick in CONFIG mode (button was held).
    f = FakeE22("config", FACTORY)
    r = govorimolib.probe(f.path)
    check("1 factory config-mode probe reads honestly",
          r["mode"] == "config" and r["provisioned"] is False
          and r["registers"] == FACTORY[:7], r)
    check("2 probe never writes a register", not writes_of(f)
          and bytes(f.regs) == FACTORY, [x.hex() for x in f.frames])
    f.close()

    # 3-5: provision that same stick.
    f = FakeE22("config", FACTORY)
    stamp = os.path.join(tempfile.mkdtemp(prefix="govprov-"), "stamp")
    os.environ["GOVORIMO_STAMP"] = stamp
    rep = govorimolib.provision(f.path)
    check("3 provision writes and verifies the profile",
          rep["before"] == FACTORY[:7] and rep["after"] == PROFILE[:7]
          and bytes(f.regs) == PROFILE, rep)
    check("4 the write frame is byte-exact against spec 07 §3.2",
          any(fr == b"\xc0\x00\x09" + PROFILE for fr in f.frames),
          [x.hex() for x in f.frames])
    time.sleep(0.15)
    check("5 provision ends in transfer mode and stamps completion",
          f.mode == "transfer" and os.path.exists(stamp), f.mode)
    f.close()

    # 6: the provisioned stick, back in transfer mode: probe says so via the
    # ambient query, and transmits NOTHING but that query.
    f = FakeE22("transfer", PROFILE)
    r = govorimolib.probe(f.path)
    check("6 provisioned transfer-mode stick reported in service",
          r["mode"] == "transfer" and r["provisioned"] is True, r)
    check("7 transfer-mode probe leaks only the C1 read bytes as data",
          all(x in b"\xc1\x00\x07" for x in set(f.swallowed)),
          f.swallowed.hex())
    f.close()

    # 8-9: a FACTORY stick in transfer mode is silent (REG1.5 unset), and
    # provisioning it raises the button instruction.
    f = FakeE22("transfer", FACTORY)
    r = govorimolib.probe(f.path)
    check("8 factory transfer-mode stick reads silent", r["mode"] == "silent", r)
    try:
        govorimolib.provision(f.path)
        check("9 provision without config mode refuses with the button", False)
    except govorimolib.ProvisionError as e:
        check("9 provision without config mode refuses with the button",
              "button" in str(e) and "red" in str(e), e)
    f.close()

    # 10: no device at all.
    try:
        govorimolib.probe(os.path.join(tempfile.gettempdir(), "no-such-lora"))
        check("10 missing device refuses with the plug-in", False)
    except govorimolib.ProvisionError as e:
        check("10 missing device refuses with the plug-in",
              "plugged" in str(e), e)

    # 11: the module rejects the write (FF FF FF): honest error, regs kept.
    f = FakeE22("config", FACTORY)
    f.inject_write_error = True
    try:
        govorimolib.provision(f.path)
        check("11 rejected write surfaces honestly", False)
    except govorimolib.ProvisionError as e:
        check("11 rejected write surfaces honestly",
              "rejected" in str(e) and bytes(f.regs) == FACTORY, e)
    f.close()

    # 12: the write lands but the readback disagrees.
    f = FakeE22("config", FACTORY)
    f.lie_readback = bytearray(FACTORY)
    try:
        govorimolib.provision(f.path)
        check("12 readback mismatch surfaces honestly", False)
    except govorimolib.ProvisionError as e:
        check("12 readback mismatch surfaces honestly",
              "read back" in str(e), e)
    f.close()

    # 13: idempotent re-provision of an already-provisioned config stick.
    f = FakeE22("config", PROFILE)
    rep = govorimolib.provision(f.path)
    check("13 re-provision is idempotent",
          rep["before"] == PROFILE[:7] and rep["after"] == PROFILE[:7], rep)
    f.close()

    # 14: a truncated reply reads as silence, never a crash.
    f = FakeE22("config", FACTORY)
    real_reply = f._reply
    f._reply = lambda b: real_reply(bytes(b)[:4])
    r = govorimolib.probe(f.path)
    check("14 truncated replies degrade to silent", r["mode"] == "silent", r)
    f.close()

    # 15: airtime arithmetic sanity (~1.04 ms/byte slope at 9.6k).
    a0, a1 = govorimolib.airtime_ms(0), govorimolib.airtime_ms(100)
    slope = (a1 - a0) / 100.0
    check("15 airtime model sane and monotonic",
          a0 > 0 and a1 > a0 and 0.66 <= slope <= 1.0, (a0, a1, slope))

    # 16: socket path resolution order.
    saved = {k: os.environ.get(k) for k in ("GOVORIMO_SOCKET", "XDG_RUNTIME_DIR")}
    try:
        os.environ["GOVORIMO_SOCKET"] = "/tmp/explicit.sock"
        os.environ["XDG_RUNTIME_DIR"] = "/tmp/xdgdir"
        p1 = govorimolib.socket_path()
        del os.environ["GOVORIMO_SOCKET"]
        p2 = govorimolib.socket_path()
        del os.environ["XDG_RUNTIME_DIR"]
        p3 = govorimolib.socket_path()
        check("16 socket path resolution order",
              p1 == "/tmp/explicit.sock"
              and p2 == "/tmp/xdgdir/govorimo.sock"
              and p3.endswith("govorimo-%d.sock" % os.getuid()), (p1, p2, p3))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # ------------------------------------------------------------- mutants
    # M1: the fake stores a flipped profile byte -> the verify must refuse.
    f = FakeE22("config", FACTORY)

    def flip(regs):
        regs[5] ^= 0x01           # wrong channel comes back
    f.mangle_store = flip
    try:
        govorimolib.provision(f.path)
        mutant("M1 flipped stored byte caught by the readback verify", False)
    except govorimolib.ProvisionError:
        mutant("M1 flipped stored byte caught by the readback verify", True)
    f.close()

    # M2: a factory stick that ANSWERS the ambient query (a lying module)
    # must be visibly different from the honest silent one in check 8.
    f = FakeE22("transfer", FACTORY)
    f.lie_ambient = True
    r = govorimolib.probe(f.path)
    mutant("M2 lying ambient reply flips the verdict check 8 pins",
           r["mode"] == "transfer", r)
    f.close()

    # M3: reserved REG3 bits zeroed on store -> readback verify refuses.
    f = FakeE22("config", FACTORY)

    def zero_reserved(regs):
        regs[6] &= 0xF0
    f.mangle_store = zero_reserved
    try:
        govorimolib.provision(f.path)
        mutant("M3 zeroed reserved bits caught by the readback verify", False)
    except govorimolib.ProvisionError:
        mutant("M3 zeroed reserved bits caught by the readback verify", True)
    f.close()

    print("\nRESULT: %d passed, %d failed, %d mutants (%d uncaught)"
          % (len(PASSES), len(FAILS), len(MUTANTS), len(UNCAUGHT)))
    sys.exit(1 if FAILS or UNCAUGHT else 0)


main()
