#!/usr/bin/env python3
"""Truncated HTTP bodies fail promptly instead of hanging printer threads."""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "mock_ipp_printer.py")
spec = importlib.util.spec_from_file_location("mock_ipp_printer", PATH)
printer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(printer)


class Ended:
    def recv(self, _size):
        return b""


def truncated(payload):
    try:
        printer.read_http_request(Ended(), payload)
        return False
    except EOFError:
        return True


head_chunked = b"POST /ipp HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
assert truncated(head_chunked + b"0\r\n")
assert truncated(head_chunked + b"4\r\nab")
assert truncated(b"POST /ipp HTTP/1.1\r\nContent-Length: 5\r\n\r\nab")

path, body, rest = printer.read_http_request(
    Ended(), head_chunked + b"4\r\ntest\r\n0\r\n\r\nNEXT")
assert (path, body, rest) == ("/ipp", b"test", b"NEXT")

path, body, rest = printer.read_http_request(
    Ended(), b"POST /p HTTP/1.1\r\nContent-Length: 3\r\n\r\nabcTAIL")
assert (path, body, rest) == ("/p", b"abc", b"TAIL")

print("MOCK IPP HTTP FRAMING SELFTEST: 5 checks, all pass")
print("RESULT: ALL PASS")
