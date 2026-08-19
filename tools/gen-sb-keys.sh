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

if [ -f "$KEYDIR/MOK.key" ] && [ -f "$KEYDIR/MOK.crt" ]; then
    # A complete-looking directory is not enough: after a partial restore the
    # private key and certificate can belong to different identities. Signing
    # would succeed, but the certificate copied to the ESP could never
    # authorize that kernel after enrollment.
    KEY_FP=$(openssl pkey -in "$KEYDIR/MOK.key" -pubout -outform DER 2>/dev/null \
        | openssl dgst -sha256 2>/dev/null) || {
        echo "unreadable MOK private key in $KEYDIR; refusing to replace it" >&2
        exit 2
    }
    CRT_FP=$(openssl x509 -in "$KEYDIR/MOK.crt" -pubkey -noout 2>/dev/null \
        | openssl pkey -pubin -outform DER 2>/dev/null \
        | openssl dgst -sha256 2>/dev/null) || {
        echo "unreadable MOK certificate in $KEYDIR; refusing to replace it" >&2
        exit 2
    }
    if [ "$KEY_FP" != "$CRT_FP" ]; then
        echo "MOK key and certificate do not match in $KEYDIR; refusing to replace them" >&2
        exit 2
    fi
    unset KEY_FP CRT_FP

    # MOK.cer is only the DER encoding of the public certificate.  Recreate it
    # from that certificate if it was lost; generating a whole new key here
    # would silently invalidate the identity already enrolled in firmware.
    if [ ! -f "$KEYDIR/MOK.cer" ]; then
        echo "MOK.cer missing; recovering it from the existing certificate"
        openssl x509 -in "$KEYDIR/MOK.crt" -outform DER \
            -out "$KEYDIR/.MOK.cer.tmp"
        chmod 0644 "$KEYDIR/.MOK.cer.tmp"
        mv -f -- "$KEYDIR/.MOK.cer.tmp" "$KEYDIR/MOK.cer"
    fi
    echo "MOK already present in $KEYDIR (reusing -- do NOT regenerate, it would"
    echo "invalidate the certificate the user has already enrolled):"
    openssl x509 -in "$KEYDIR/MOK.crt" -noout -subject -enddate | sed 's/^/   /'
    exit 0
fi

# An interrupted first generation can leave one irreplaceable half behind.
# Never guess that it is safe to rotate it: the surviving certificate may
# already be enrolled, and the surviving private key may be the only way to
# keep signing for it.  Require deliberate recovery/removal by the operator.
if [ -e "$KEYDIR/MOK.key" ] || [ -e "$KEYDIR/MOK.crt" ] || \
        [ -e "$KEYDIR/MOK.cer" ]; then
    echo "incomplete MOK set in $KEYDIR; refusing to replace the existing identity" >&2
    exit 2
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
