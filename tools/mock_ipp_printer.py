#!/usr/bin/env python3
"""A fake IPP Everywhere printer on a Unix socket, for testing ippusb.

The build host has no printer attached, and there is no way to emulate a USB
printer in QEMU. But everything above the wire in the ippusb backend — HTTP
framing, IPP encode/decode, PPD generation, option mapping, job polling, the
CUPS backend contract — is transport-independent, so the backend takes
IPPUSB_MOCK=<socket> and speaks the exact same bytes to this program that it
would push down a USB bulk pipe.

Run it, point cupsd at an ippusb:// queue with IPPUSB_MOCK set, and a real print
job travels the whole path. The document that comes out the far end is written
to --save, so the test can check the printer received an intact PDF.

    mock_ipp_printer.py --socket /tmp/mock.sock --save /tmp/received.pdf

The attribute set below is a realistic modern colour inkjet MFC: the same shape
of Get-Printer-Attributes response a driverless printer returns in the field.
"""

import argparse
import os
import socket
import struct
import sys
import threading

# ---- IPP tags ---------------------------------------------------------------
TAG_OPERATION = 0x01
TAG_JOB = 0x02
TAG_END = 0x03
TAG_PRINTER = 0x04
TAG_UNSUPPORTED_GROUP = 0x05

TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_STRING = 0x30
TAG_RESOLUTION = 0x32
TAG_RANGE = 0x33
TAG_TEXT = 0x41
TAG_NAME = 0x42
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_CHARSET = 0x47
TAG_LANGUAGE = 0x48
TAG_MIMETYPE = 0x49

OP_PRINT_JOB = 0x0002
OP_VALIDATE_JOB = 0x0004
OP_CREATE_JOB = 0x0005
OP_SEND_DOCUMENT = 0x0006
OP_CANCEL_JOB = 0x0008
OP_GET_JOB_ATTRIBUTES = 0x0009
OP_GET_PRINTER_ATTRIBUTES = 0x000B

OK = 0x0000
ERR_NOT_FOUND = 0x0406


# ---- encoding ---------------------------------------------------------------
def _val(tag, value):
    if tag in (TAG_INTEGER, TAG_ENUM):
        return struct.pack(">i", int(value))
    if tag == TAG_BOOLEAN:
        return struct.pack(">B", 1 if value else 0)
    if tag == TAG_RESOLUTION:
        x, y, u = value
        return struct.pack(">iiB", x, y, u)
    if tag == TAG_RANGE:
        lo, hi = value
        return struct.pack(">ii", lo, hi)
    return str(value).encode("utf-8")


def attr(tag, name, values):
    """One attribute, possibly multi-valued (additional values carry no name)."""
    if not isinstance(values, (list, tuple)) or tag in (TAG_RESOLUTION,
                                                        TAG_RANGE):
        values = [values]
    out = b""
    for i, v in enumerate(values):
        nm = name.encode("ascii") if i == 0 else b""
        raw = _val(tag, v)
        out += struct.pack(">BH", tag, len(nm)) + nm
        out += struct.pack(">H", len(raw)) + raw
    return out


def response(status, request_id, groups):
    """groups: list of (delimiter_tag, encoded_attribute_bytes)."""
    out = struct.pack(">BBHI", 2, 0, status, request_id)
    for delim, body in groups:
        out += struct.pack(">B", delim) + body
    out += struct.pack(">B", TAG_END)
    return out


# ---- decoding ---------------------------------------------------------------
def decode(data):
    """-> (opid, request_id, {name: [values]}, document_bytes)"""
    if len(data) < 8:
        raise ValueError("short IPP message")
    _maj, _min, opid, rid = struct.unpack(">BBHI", data[:8])
    pos = 8
    attrs = {}
    last = None
    while pos < len(data):
        tag = data[pos]
        pos += 1
        if tag == TAG_END:
            break
        if tag < 0x10:          # delimiter
            continue
        (nlen,) = struct.unpack(">H", data[pos:pos + 2])
        pos += 2
        name = data[pos:pos + nlen].decode("utf-8", "replace")
        pos += nlen
        (vlen,) = struct.unpack(">H", data[pos:pos + 2])
        pos += 2
        raw = data[pos:pos + vlen]
        pos += vlen
        if tag in (TAG_INTEGER, TAG_ENUM):
            val = struct.unpack(">i", raw)[0] if len(raw) == 4 else 0
        elif tag == TAG_BOOLEAN:
            val = bool(raw and raw[0])
        else:
            val = raw.decode("utf-8", "replace")
        if nlen:
            last = name
            attrs[name] = [val]
        elif last:
            attrs[last].append(val)
    return opid, rid, attrs, data[pos:]


# ---- the printer ------------------------------------------------------------
MAKE_AND_MODEL = "Brother MFC-J1355DW"

# Overridden by --formats. The AirPrint-only case (no application/pdf) is a real
# class of printer and takes a completely different filter chain, so it has to
# be testable.
FORMATS = ["application/pdf", "image/urf", "image/pwg-raster", "image/jpeg",
           "application/octet-stream"]


def printer_attrs():
    a = b""
    a += attr(TAG_CHARSET, "attributes-charset", "utf-8")
    a += attr(TAG_LANGUAGE, "attributes-natural-language", "en")
    a += attr(TAG_NAME, "printer-name", "MFC-J1355DW")
    a += attr(TAG_TEXT, "printer-info", MAKE_AND_MODEL)
    a += attr(TAG_TEXT, "printer-make-and-model", MAKE_AND_MODEL)
    a += attr(TAG_TEXT, "printer-location", "")
    a += attr(TAG_URI, "printer-uri-supported", "ipp://localhost/ipp/print")
    a += attr(TAG_KEYWORD, "uri-security-supported", "none")
    a += attr(TAG_KEYWORD, "uri-authentication-supported", "requesting-user-name")
    a += attr(TAG_ENUM, "printer-state", 3)
    a += attr(TAG_KEYWORD, "printer-state-reasons", "none")
    a += attr(TAG_BOOLEAN, "printer-is-accepting-jobs", True)
    a += attr(TAG_KEYWORD, "ipp-versions-supported", ["1.1", "2.0"])
    a += attr(TAG_ENUM, "operations-supported",
              [OP_PRINT_JOB, OP_VALIDATE_JOB, OP_CREATE_JOB, OP_SEND_DOCUMENT,
               OP_CANCEL_JOB, OP_GET_JOB_ATTRIBUTES, OP_GET_PRINTER_ATTRIBUTES])
    a += attr(TAG_CHARSET, "charset-configured", "utf-8")
    a += attr(TAG_CHARSET, "charset-supported", "utf-8")
    a += attr(TAG_LANGUAGE, "natural-language-configured", "en")
    a += attr(TAG_LANGUAGE, "generated-natural-language-supported", "en")
    a += attr(TAG_MIMETYPE, "document-format-default", "application/octet-stream")
    a += attr(TAG_MIMETYPE, "document-format-supported", FORMATS)
    a += attr(TAG_BOOLEAN, "color-supported", True)
    a += attr(TAG_KEYWORD, "print-color-mode-default", "color")
    a += attr(TAG_KEYWORD, "print-color-mode-supported",
              ["auto", "color", "monochrome"])
    a += attr(TAG_KEYWORD, "pwg-raster-document-type-supported",
              ["sgray_8", "srgb_8"])
    a += attr(TAG_RESOLUTION, "pwg-raster-document-resolution-supported",
              (300, 300, 3))
    a += attr(TAG_KEYWORD, "pwg-raster-document-sheet-back", "normal")
    a += attr(TAG_KEYWORD, "urf-supported",
              ["CP1", "IS1-4", "MT1-3-4-5-8", "OB10", "PQ4", "RS300-600",
               "SRGB24", "V1.4", "W8", "DM1"])
    a += attr(TAG_RESOLUTION, "printer-resolution-default", (300, 300, 3))
    a += attr(TAG_RESOLUTION, "printer-resolution-supported", (300, 300, 3))
    a += attr(TAG_KEYWORD, "media-supported",
              ["na_letter_8.5x11in", "na_legal_8.5x14in", "iso_a4_210x297mm",
               "na_index-4x6_4x6in"])
    a += attr(TAG_KEYWORD, "media-default", "na_letter_8.5x11in")
    a += attr(TAG_KEYWORD, "media-source-supported", ["auto", "main"])
    a += attr(TAG_KEYWORD, "media-type-supported", ["stationery", "photographic"])
    a += attr(TAG_INTEGER, "media-left-margin-supported", 300)
    a += attr(TAG_INTEGER, "media-right-margin-supported", 300)
    a += attr(TAG_INTEGER, "media-top-margin-supported", 300)
    a += attr(TAG_INTEGER, "media-bottom-margin-supported", 300)
    a += attr(TAG_KEYWORD, "sides-default", "one-sided")
    a += attr(TAG_KEYWORD, "sides-supported",
              ["one-sided", "two-sided-long-edge", "two-sided-short-edge"])
    a += attr(TAG_ENUM, "print-quality-default", 4)
    a += attr(TAG_ENUM, "print-quality-supported", [3, 4, 5])
    a += attr(TAG_RANGE, "copies-supported", (1, 99))
    a += attr(TAG_INTEGER, "copies-default", 1)
    a += attr(TAG_ENUM, "finishings-supported", 3)
    a += attr(TAG_ENUM, "finishings-default", 3)
    a += attr(TAG_ENUM, "orientation-requested-supported", [3, 4, 5, 6])
    a += attr(TAG_KEYWORD, "output-bin-supported", "face-down")
    a += attr(TAG_KEYWORD, "output-bin-default", "face-down")
    a += attr(TAG_KEYWORD, "job-creation-attributes-supported",
              ["copies", "media", "orientation-requested", "print-color-mode",
               "print-quality", "printer-resolution", "sides"])
    a += attr(TAG_TEXT, "printer-device-id",
              "MFG:Brother;MDL:MFC-J1355DW;CMD:URF,PWGRaster,PDF;CLS:PRINTER;")
    a += attr(TAG_KEYWORD, "pdf-versions-supported", "adobe-1.7")
    a += attr(TAG_KEYWORD, "compression-supported", "none")
    a += attr(TAG_INTEGER, "printer-up-time", 1)
    return a


class Printer:
    """Job bookkeeping. Jobs report 'processing' once and then 'completed', so
    the backend's polling loop is genuinely exercised rather than short-circuited
    by an instant finish."""

    def __init__(self, save_path, fail_job=False):
        self.save_path = save_path
        self.fail_job = fail_job
        self.next_id = 101
        self.jobs = {}
        self.received = []
        self.lock = threading.Lock()

    def print_job(self, attrs, doc):
        with self.lock:
            jid = self.next_id
            self.next_id += 1
            self.jobs[jid] = {"polls": 0, "attrs": attrs, "size": len(doc)}
            self.received.append(doc)
        if self.save_path:
            with open(self.save_path, "wb") as fh:
                fh.write(doc)
        return jid

    def job_state(self, jid):
        with self.lock:
            j = self.jobs.get(jid)
            if j is None:
                return None
            j["polls"] += 1
            if j["polls"] < 2:
                return (5, "job-printing")          # processing
            if self.fail_job:
                return (8, "media-empty-error")     # aborted
            return (9, "job-completed-successfully")


def serve_request(pr, body, log):
    opid, rid, attrs, doc = decode(body)

    if opid == OP_GET_PRINTER_ATTRIBUTES:
        log("Get-Printer-Attributes")
        return response(OK, rid, [(TAG_OPERATION,
                                   attr(TAG_CHARSET, "attributes-charset",
                                        "utf-8") +
                                   attr(TAG_LANGUAGE,
                                        "attributes-natural-language", "en")),
                                  (TAG_PRINTER, printer_attrs())])

    if opid == OP_PRINT_JOB:
        fmt = attrs.get("document-format", ["?"])[0]
        name = attrs.get("job-name", [""])[0]
        log("Print-Job format=%s name=%r bytes=%d opts=%s"
            % (fmt, name,
               len(doc),
               {k: v for k, v in attrs.items()
                if k in ("copies", "media", "sides", "print-color-mode",
                         "print-quality", "printer-resolution",
                         "orientation-requested")}))
        jid = pr.print_job(attrs, doc)
        ops = (attr(TAG_CHARSET, "attributes-charset", "utf-8") +
               attr(TAG_LANGUAGE, "attributes-natural-language", "en"))
        job = (attr(TAG_INTEGER, "job-id", jid) +
               attr(TAG_URI, "job-uri", "ipp://localhost/ipp/print/%d" % jid) +
               attr(TAG_ENUM, "job-state", 3) +
               attr(TAG_KEYWORD, "job-state-reasons", "job-incoming"))
        return response(OK, rid, [(TAG_OPERATION, ops), (TAG_JOB, job)])

    if opid == OP_GET_JOB_ATTRIBUTES:
        jid = attrs.get("job-id", [0])[0]
        st = pr.job_state(int(jid))
        if st is None:
            return response(ERR_NOT_FOUND, rid,
                            [(TAG_OPERATION,
                              attr(TAG_CHARSET, "attributes-charset", "utf-8"))])
        state, reason = st
        log("Get-Job-Attributes job=%s -> state=%d (%s)" % (jid, state, reason))
        ops = (attr(TAG_CHARSET, "attributes-charset", "utf-8") +
               attr(TAG_LANGUAGE, "attributes-natural-language", "en"))
        job = (attr(TAG_INTEGER, "job-id", int(jid)) +
               attr(TAG_ENUM, "job-state", state) +
               attr(TAG_KEYWORD, "job-state-reasons", reason))
        return response(OK, rid, [(TAG_OPERATION, ops), (TAG_JOB, job)])

    log("unsupported operation 0x%04x" % opid)
    return response(0x0501, rid,
                    [(TAG_OPERATION,
                      attr(TAG_CHARSET, "attributes-charset", "utf-8"))])


def read_http_request(conn, buf):
    """Read one request. Returns (path, body, buf) or (None, None, buf)."""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            return None, None, buf
        buf += chunk
    head, buf = buf.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    path = lines[0].split(" ")[1] if len(lines[0].split(" ")) > 1 else "/"
    clen = 0
    chunked = False
    for ln in lines[1:]:
        if ln.lower().startswith("content-length:"):
            clen = int(ln.split(":", 1)[1].strip())
        elif ln.lower().startswith("transfer-encoding:") and "chunked" in ln.lower():
            chunked = True
    if chunked:
        body = b""
        while True:
            while b"\r\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    return path, body, buf
                buf += chunk
            line, buf = buf.split(b"\r\n", 1)
            n = int(line.split(b";")[0], 16)
            if n == 0:
                while len(buf) < 2:
                    buf += conn.recv(65536)
                buf = buf[2:]
                break
            while len(buf) < n + 2:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
            body += buf[:n]
            buf = buf[n + 2:]
        return path, body, buf
    while len(buf) < clen:
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
    body, buf = buf[:clen], buf[clen:]
    return path, body, buf


def handle(conn, pr, paths, log):
    buf = b""
    try:
        while True:
            path, body, buf = read_http_request(conn, buf)
            if path is None:
                return
            if path not in paths:
                log("404 for %s" % path)
                conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n"
                             b"Connection: keep-alive\r\n\r\n")
                continue
            try:
                out = serve_request(pr, body, log)
            except Exception as exc:                # noqa: BLE001
                log("bad request: %s" % exc)
                conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0"
                             b"\r\n\r\n")
                return
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/ipp\r\n"
                         b"Content-Length: " + str(len(out)).encode() +
                         b"\r\nConnection: keep-alive\r\n\r\n" + out)
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", required=True)
    ap.add_argument("--save", default="")
    ap.add_argument("--log", default="")
    ap.add_argument("--path", default="/ipp/print",
                    help="the only resource path that answers IPP")
    ap.add_argument("--fail-job", action="store_true",
                    help="abort jobs with media-empty-error, to test reporting")
    ap.add_argument("--formats", default="",
                    help="comma-separated document-format-supported; use this "
                         "to model an AirPrint-only printer that will not "
                         "take a PDF")
    args = ap.parse_args()

    if args.formats:
        global FORMATS
        FORMATS = [f.strip() for f in args.formats.split(",") if f.strip()]

    logfh = open(args.log, "a", buffering=1) if args.log else sys.stderr

    def log(msg):
        logfh.write("mock: %s\n" % msg)
        logfh.flush()

    try:
        os.unlink(args.socket)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(args.socket)
    os.chmod(args.socket, 0o666)
    srv.listen(8)
    log("listening on %s (answers on %s)" % (args.socket, args.path))

    pr = Printer(args.save, fail_job=args.fail_job)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn, pr, {args.path}, log),
                         daemon=True).start()


if __name__ == "__main__":
    main()
