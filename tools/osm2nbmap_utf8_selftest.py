#!/usr/bin/env python3
"""Map string byte limits preserve complete UTF-8 code points."""
import importlib.util
import os
import struct
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tools", "osm2nbmap.py")
spec = importlib.util.spec_from_file_location("osm2nbmap", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

region = "😀" * 16384                 # 65,536 bytes; limit cuts byte 65,535
name = "é" * 128                       # 256 bytes; feature limit is 255
features = [(module.CAT["road_minor"], False, name,
             [(1.0, 2.0), (1.5, 2.5)])]

with tempfile.TemporaryDirectory(prefix="nbmap-utf8-") as td:
    out = os.path.join(td, "unicode.nbmap")
    module._write_map(out, region, features, (1.0, 2.0, 1.5, 2.5))
    raw = open(out, "rb").read()

assert raw[:7] == b"NBMAP1\n"
nlen = struct.unpack_from("<H", raw, 7)[0]
assert nlen == 65532
region_raw = raw[9:9 + nlen]
assert region_raw.decode("utf-8") == "😀" * 16383

pos = 9 + nlen + 32 + 4
_cat, _flags, feature_len = struct.unpack_from("<BBB", raw, pos)
assert feature_len == 254
feature_raw = raw[pos + 3:pos + 3 + feature_len]
assert feature_raw.decode("utf-8") == "é" * 127
assert b"\xef\xbf\xbd" not in region_raw + feature_raw

print("OSM2NBMAP UTF8 SELFTEST: 7 checks, all pass")
print("RESULT: ALL PASS")
