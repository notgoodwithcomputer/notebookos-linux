import os
import sys
import tempfile


def fix(path):
    with open(path, "rb") as fh:
        d = bytearray(fh.read())
    if len(d) < 0xC0:
        d += bytes(0xC0 - len(d))
    s = 0
    for i in range(0xA0, 0xBD):
        s = (s + d[i]) & 0xFF
    d[0xBD] = (-(0x19 + s)) & 0xFF

    # Do not truncate the only completed ROM before its replacement is safely
    # written.  A full filesystem or interrupted write must leave the previous
    # build usable, not turn it into an empty/partial cartridge image.
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".gbafix-", dir=directory)
    try:
        os.fchmod(fd, os.stat(path).st_mode & 0o7777)
        with os.fdopen(fd, "wb") as fh:
            fd = -1
            fh.write(d)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        tmp = None
        try:
            dirfd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        except OSError:
            # Some filesystems do not support directory fsync; the atomic
            # replacement itself still provides the important safety property.
            pass
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return d[0xBD], len(d)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: gbafix.py ROM", file=sys.stderr)
        sys.exit(2)
    complement, size = fix(sys.argv[1])
    print("gbafix: complement=0x%02X size=%d" % (complement, size))
