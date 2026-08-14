# .nbpkg — the Notebook OS package format

Status: the deployment mechanism for apps that ship OUTSIDE the base image
(Govorimo first; every app graduates to its own repo before release). Notebook
OS is a fixed image with no in-place package install; `.nbpkg` adds a signed,
verify-before-parse install path through the Packages app (USB, offline).

## The format

A `.nbpkg` is a gzip tar containing:

```
manifest.json      the package's identity, app registration, and file table
manifest.sig       Ed25519 detached signature over CANONICAL(manifest)
files/<sha256>     each payload, content-addressed by its own hash
```

`manifest.json`:

```json
{ "name": "Govorimo", "version": "2.0.0",
  "app": { "display": "Govorimo", "module": "govorimo",
           "kind": "Messaging", "icon": "radio" },
  "service": "govorimod-run.sh",
  "files": [ { "src": "app/govorimo.py",
               "dest": "opt/notebook/de/govorimo.py",
               "mode": "0755", "sha256": "…" }, … ] }
```

CANONICAL(manifest) is the sorted, compact JSON of the manifest minus its `sig`
field — deterministic, so a rebuild verifies against the same signature.

## Trust

The release **public** key is pinned in the OS image at
`packaging/nbpkg-release.pub`. The **private** key is offline and gitignored
(`*.key`) — a SEPARATE key from the kernel Secure Boot MOK (`secureboot/`),
because signing app packages and signing the kernel are different authorities
with different lifetimes.

## Verify-before-parse (the security order)

`tools/nbpkg.py install` writes NOTHING to the system until, in this order:
1. the manifest's Ed25519 signature verifies against the pinned public key;
2. every payload is present and its sha256 matches the manifest exactly.

Only then are files written (temp + atomic rename each), and the data-driven
registry (`opt/notebook/de/installed_apps.json`) is updated LAST, so a
half-install is never registered. Member names are checked against absolute
paths and `..` traversal; destinations likewise. Removable media on this OS is
parsed as root (`docs/SECURITY-MODEL.md`), so nothing may reach an unpacker
that trusts unverified input.

## Registration without a source edit

The installer appends the app to `installed_apps.json`; `finder.py` merges it
(defensively — a malformed or absent registry leaves the built-in apps
untouched, and an installed entry can never HIDE a shipped one). This is the
data-driven path that lets a package register an app without editing
`APP_MODULES` / `APP_KIND` in source. `packages.py` reads the same registry to
list installed-from-package apps and to uninstall them.

## Tooling

```
tools/nbpkg.py build   --manifest M --root SRC --out P.nbpkg --key PRIV
tools/nbpkg.py verify  P.nbpkg [--pub PUB]
tools/nbpkg.py install P.nbpkg --target ROOT [--pub PUB]
```

Gate: `tools/nbpkg_selftest.py` proves the full cycle end to end — build →
verify → install → register — and the refusals: a tampered payload, a package
signed by the wrong key, and a traversal destination are all rejected without
writing to the target.

## Still owed (the last mile)

The Packages app's USB "Sources" view should offer to install a verified
`.nbpkg` and show its identity before installing; uninstall should remove the
registry entry and the files. The mechanism, the format, and the gate are done;
the `packages.py` UI wiring is a coordinated follow-up (the app is under the
active apple-quality lane).
