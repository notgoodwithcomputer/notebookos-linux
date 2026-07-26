import sys
d = bytearray(open(sys.argv[1], "rb").read())
if len(d) < 0xC0:
    d += bytes(0xC0 - len(d))
s = 0
for i in range(0xA0, 0xBD):
    s = (s + d[i]) & 0xFF
d[0xBD] = (-(0x19 + s)) & 0xFF
open(sys.argv[1], "wb").write(d)
print("gbafix: complement=0x%02X size=%d" % (d[0xBD], len(d)))
