#!/usr/bin/env bash
# tools/sync-govorimo.sh — bake the CURRENT Govorimo (daemon + app) into a rootfs
# tree, so every OS build ships the latest version.
#
# Invoked by board/notebookos/post-build.sh with $TARGET (the assembled rootfs),
# AFTER the overlay's deletion-prune so the freshly-synced app is never pruned.
# Can also be run by hand:  tools/sync-govorimo.sh buildroot/output/target
#
# Graceful by design: if the Govorimo source tree or the Rust toolchain is
# absent, it warns and exits 0 — a missing optional app must never fail the OS
# build. Override the source location with GOVORIMO_SRC.
#
# What lands where (all under /opt/notebook, the DE's home):
#   bin/govorimo-daemon         the static x86_64-musl daemon (keys/radio/store)
#   de/govorimo.py + _i18n.py   the GTK app (pure UI over the daemon socket)
#   de/lang/<code>.json         the app's own 17-language catalogs
#   data/gazetteer/*.json       the place codes the app offers
#   de/installed_apps.json      the launcher registration (finder reads this)
set -euo pipefail

TARGET="${1:?usage: sync-govorimo.sh <rootfs-target-dir>}"
SRC="${GOVORIMO_SRC:-$HOME/Documents/govorimo}"

say()  { printf '\033[1;35m[govorimo]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[govorimo] WARNING:\033[0m %s\n' "$*" >&2; }

DE="$TARGET/opt/notebook/de"
BIN="$TARGET/opt/notebook/bin"
DATA="$TARGET/opt/notebook/data"
APPS="$TARGET/root/Applications"

# ---- 0. is Govorimo withheld from this image? ----------------------------
# finder.HIDDEN_APPS is the ONE list of what a stable image does not offer, and
# it names apps from BOTH bundling routes (see the comment above that dict).
# Ask it here rather than keeping a second answer — and ask it first, before the
# ~90s cargo build below.
#
# Parsed from the source with ast, never imported: this runs inside a Buildroot
# post-build hook that must not construct GTK. Same way i18n_check and
# menu_conformance_check read that dict.
#
# If python3 cannot be found to parse it the sync PROCEEDS, which is deliberate
# and safe: finder filters a hidden app out of every launch surface at runtime
# whether or not its files shipped, so the cost is wasted bytes rather than a
# withheld app becoming reachable.
_REPO="$(cd "$(dirname "$0")/.." && pwd)"
_FINDER="$_REPO/buildroot/board/notebookos/rootfs-overlay/opt/notebook/de/finder.py"
if [ -f "$_FINDER" ] && command -v python3 >/dev/null 2>&1; then
    if python3 - "$_FINDER" <<'PYHIDE'
import ast, sys
try:
    tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
except (OSError, SyntaxError):
    sys.exit(1)                  # unreadable: never CLAIM an app is withheld
for node in ast.walk(tree):
    if (isinstance(node, ast.Assign) and node.targets
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "HIDDEN_APPS"):
        sys.exit(0 if "Govorimo" in {k.value for k in node.value.keys} else 1)
sys.exit(1)
PYHIDE
    then
        # Withheld — and an earlier build's copy has to GO, not merely stop
        # being refreshed. output/target is incremental, and post-build's
        # deletion-prune only covers de/*.py and the .app stubs: the daemon,
        # the catalogs, the gazetteer and the launcher registration all survive
        # it. A stale daemon is the one that bites, because session.sh starts
        # whatever binary it finds — an image would run a background daemon for
        # an app nobody can open.
        say "withheld by finder.HIDDEN_APPS — not bundling it"
        rm -f  "$BIN/govorimo-daemon" "$DE/govorimo.py" "$DE/govorimo_i18n.py"
        rm -f  "$APPS/Govorimo.app"
        rm -rf "$DE/lang" "$DATA/gazetteer"
        # Drop only OUR row: a future data-driven app's registration must
        # survive, and an emptied registry is removed rather than left behind
        # as a file that says {}.
        if [ -f "$DE/installed_apps.json" ]; then
            python3 - "$DE/installed_apps.json" <<'PYDEREG' 2>/dev/null \
                || rm -f "$DE/installed_apps.json"
import json, os, sys
p = sys.argv[1]
try:
    reg = json.load(open(p, encoding="utf-8"))
    reg = reg if isinstance(reg, dict) else {}
except Exception:
    os.remove(p); sys.exit(0)
reg.pop("Govorimo", None)
if reg:
    json.dump(reg, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
else:
    os.remove(p)
PYDEREG
        fi
        say "removed any earlier build's Govorimo from $TARGET"
        exit 0
    fi
fi
unset _REPO _FINDER

if [ ! -f "$SRC/app/govorimo.py" ]; then
    warn "Govorimo source not found at $SRC (set GOVORIMO_SRC) — not bundling it"
    exit 0
fi

# ---- 1. the daemon: a static x86_64-musl binary (self-contained on target) ----
CARGO="${CARGO:-$(command -v cargo || echo "$HOME/.cargo/bin/cargo")}"
TRIPLE="x86_64-unknown-linux-musl"
DBIN="$SRC/target/$TRIPLE/release/govorimo-daemon"
if [ -x "$CARGO" ]; then
    say "building govorimo-daemon ($TRIPLE, release)…"
    if ! ( cd "$SRC" && "$CARGO" build --release --quiet \
              --target "$TRIPLE" -p govorimo-daemon ); then
        warn "daemon build failed — using a prior binary if one exists"
    fi
else
    warn "cargo not found — using a prior daemon binary if one exists"
fi
if [ -x "$DBIN" ]; then
    install -D -m 0755 "$DBIN" "$BIN/govorimo-daemon"
    say "daemon -> /opt/notebook/bin/govorimo-daemon ($(du -h "$DBIN" | cut -f1))"
else
    warn "no daemon binary at $DBIN — the app will fall back to its demo view"
fi

# ---- 2. the app (pure UI over the socket) + its own 17-language catalogs ----
install -D -m 0644 "$SRC/app/govorimo.py"      "$DE/govorimo.py"
install -D -m 0644 "$SRC/app/govorimo_i18n.py" "$DE/govorimo_i18n.py"
if [ -d "$SRC/app/lang" ]; then
    mkdir -p "$DE/lang"                       # de/lang/<code>.json, per govorimo_i18n
    cp -a "$SRC/app/lang/." "$DE/lang/"
fi
say "app  -> /opt/notebook/de/govorimo.py (+ i18n, $(ls "$SRC/app/lang" 2>/dev/null | wc -l | tr -d ' ') catalogs)"

# ---- 3. the gazetteer the app offers places from (de/../data/gazetteer) ----
if [ -d "$SRC/data/gazetteer" ]; then
    mkdir -p "$DATA/gazetteer"
    cp -a "$SRC/data/gazetteer/." "$DATA/gazetteer/"
    say "data -> /opt/notebook/data/gazetteer/"
fi

# ---- 4. register in the launcher, ONLY when the module was actually synced ----
# finder._merge_installed_apps() reads installed_apps.json; writing it here (not
# in the overlay) keeps the row honest — Govorimo appears iff its module shipped.
# Merge into any existing registry so a future data-driven app is not clobbered.
mkdir -p "$DE"
if ! python3 - "$DE/installed_apps.json" <<'PYREG' 2>/dev/null
import json, sys
p = sys.argv[1]
try:
    reg = json.load(open(p, encoding="utf-8"))
    reg = reg if isinstance(reg, dict) else {}
except Exception:
    reg = {}
reg["Govorimo"] = {"module": "govorimo", "kind": "Messages"}
json.dump(reg, open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
PYREG
then
    # Python unavailable in this hook env: Govorimo is the only data-driven app
    # today, so a fresh single-entry registry is correct.
    printf '{\n  "Govorimo": {"module": "govorimo", "kind": "Messages"}\n}\n' \
        > "$DE/installed_apps.json"
fi
say "registered in Applications (installed_apps.json)"

# ---- 5. the Applications folder stub the launcher actually lists ----
# The Finder shows one ".app" marker per app; finder.py maps the name to the
# module via APP_MODULES (which installed_apps.json just extended). Create it
# here so the row appears ONLY when Govorimo was installed, and — because this
# runs after post-build's deletion-prune — it is not swept away as "not in the
# overlay". The stub is the same 44-byte marker every other app uses.
mkdir -p "$APPS"
printf '#!/bin/sh\n# Notebook OS application package\n' > "$APPS/Govorimo.app"
chmod 0755 "$APPS/Govorimo.app"
say "Applications -> Govorimo.app"

# NOTE: bytecode is left to post-build.sh's compileall, which runs AFTER this
# hook with the TARGET-matched host python — so govorimo.py gets a .pyc of the
# right CPython version rather than the build host's. (Run standalone, the
# target simply recompiles from .py on first launch; correct, just not cached.)

say "bundled the current Govorimo into $TARGET"
