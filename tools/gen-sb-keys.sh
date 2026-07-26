#!/bin/bash
# Generate (once) the Machine Owner Key that Notebook OS uses to sign its GRUB
# and kernel for UEFI Secure Boot.
#
# The chain on real hardware is:
#   firmware --(MS UEFI CA)--> shim  --(this MOK)--> grub --(this MOK)--> kernel
# The user enrolls this MOK's certificate once via MokManager on first boot;
# thereafter every rebuilt/re-signed grub+kernel is trusted without re-enrolling
# -- which is WHY these keys must be stable and are generated only once.
#
#   MOK.key  private signing key         (SECRET -- never ship on the image)
#   MOK.crt  X.509 certificate, PEM      (used by sbsign)
#   MOK.cer  same certificate, DER       (shipped on the ESP; enrolled by the user)
#
#   tools/gen-sb-keys.sh            -> secureboot/MOK.{key,crt,cer}
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
KEYDIR="${NB_SB_KEYDIR:-$ROOT/secureboot}"
CN="${NB_SB_CN:-Notebook OS Secure Boot MOK}"
mkdir -p "$KEYDIR"

if [ -f "$KEYDIR/MOK.key" ] && [ -f "$KEYDIR/MOK.crt" ] && [ -f "$KEYDIR/MOK.cer" ]; then
    echo "MOK already present in $KEYDIR (reusing -- do NOT regenerate, it would"
    echo "invalidate the certificate the user has already enrolled):"
    openssl x509 -in "$KEYDIR/MOK.crt" -noout -subject -enddate | sed 's/^/   /'
    exit 0
fi

echo "== generating a new MOK in $KEYDIR =="
# 20-year, 4096-bit RSA, code-signing EKU + the CA/BasicConstraints shim wants.
openssl req -new -x509 -newkey rsa:4096 -nodes \
    -keyout "$KEYDIR/MOK.key" -out "$KEYDIR/MOK.crt" \
    -days 7300 -sha256 -subj "/CN=$CN/" \
    -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=digitalSignature" \
    -addext "extendedKeyUsage=codeSigning,1.3.6.1.4.1.311.10.3.6"
openssl x509 -in "$KEYDIR/MOK.crt" -outform DER -out "$KEYDIR/MOK.cer"
chmod 600 "$KEYDIR/MOK.key"

echo "   created:"
openssl x509 -in "$KEYDIR/MOK.crt" -noout -subject -enddate -fingerprint -sha256 | sed 's/^/     /'
echo
echo "Keep secureboot/MOK.key SECRET. secureboot/MOK.cer is the public cert the"
echo "user enrolls at first boot (MokManager)."
