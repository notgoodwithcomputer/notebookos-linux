#!/bin/sh
# Put the TARGET's ffmpeg/ffprobe on PATH so tools/video_selftest.py checks the
# binaries that will actually run on the machine, not the host's.
#
#   eval "$(tools/video_target_ffmpeg.sh)"
#   DISPLAY=:0 python3 tools/video_selftest.py
#
# The target's dynamic loader can run the target's binaries on this host — the
# same trick a font audit used to prove a guest-only bug — so a whole render can
# be checked without booting anything.
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
T="${NB_FFMPEG_TARGET:-$ROOT/buildroot/output/target}"
LD="$T/lib64/ld-linux-x86-64.so.2"
if [ ! -x "$LD" ]; then
    echo "echo 'no target loader at $LD — build the target first' >&2; false"
    exit 1
fi
for t in ffmpeg ffprobe; do
    if [ ! -x "$T/usr/bin/$t" ]; then
        echo "echo 'no $t in the target image' >&2; false"
        exit 1
    fi
done

# Allocate only after the full preflight. A partial Buildroot output used to
# leak one wrapper directory on every failed invocation.
D=$(mktemp -d)
for t in ffmpeg ffprobe; do
    cat > "$D/$t" <<EOF
#!/bin/sh
exec "$LD" --library-path "$T/usr/lib:$T/lib" "$T/usr/bin/$t" "\$@"
EOF
    chmod +x "$D/$t"
done
echo "export PATH=\"$D:\$PATH\""
