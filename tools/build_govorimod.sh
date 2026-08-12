#!/bin/sh
# Build the Govorimo daemon as a STATIC musl binary and vendor it where the
# rootfs build (post-build.sh) and the protocol suite (govorimo_selftest.py)
# both expect it. Static-musl means the buildroot glibc version can never
# disagree with the binary.
#
#   tools/build_govorimod.sh
#
# Needs: rustup toolchain with the x86_64-unknown-linux-musl target
# (`rustup target add x86_64-unknown-linux-musl` once).
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
cd "$HERE/linux-ebyte-lora-chat"
cargo build --release --target x86_64-unknown-linux-musl -p govorimo-daemon
mkdir -p "$HERE/vendor/govorimo"
install -m 0755 target/x86_64-unknown-linux-musl/release/govorimod \
    "$HERE/vendor/govorimo/govorimod"
strip "$HERE/vendor/govorimo/govorimod"
file "$HERE/vendor/govorimo/govorimod"
ls -la "$HERE/vendor/govorimo/govorimod"
