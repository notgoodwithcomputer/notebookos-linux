#!/usr/bin/env python3
"""The kernel options the walled garden rests on (docs/APP-TRUST.md, layer L0).

Every one of these closes a way for root to switch a userspace policy off. They
were all absent until 2026-08-14, which is why app signing had to be built up
from the kernel rather than down from the Packages window. A `make
olddefconfig` after a kernel bump can silently drop any of them, and nothing
else in the build would notice: the image still boots, the desktop still works,
and the lockout is simply gone.

Checks the BUILT config (kbuild-desktop/.config), not the seed, because that is
what the shipped bzImage was compiled from.

Exit status is the number of options that are wrong.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "kbuild-desktop/.config")
IMAGE = os.path.join(ROOT, "kbuild-desktop/arch/x86/boot/bzImage")
KBUILD = os.path.join(ROOT, "kbuild-desktop")
EXTRACT = os.path.join(ROOT, "linux/scripts/extract-ikconfig")

MUST_BE_ON = {
    "SECURITY_LOCKDOWN_LSM": "root can otherwise write kernel memory, load "
                             "unsigned modules and kexec, Secure Boot or not",
    "SECURITY_LOCKDOWN_LSM_EARLY": "without it the lockdown starts after early "
                                   "init, leaving a window",
    "LOCK_DOWN_KERNEL_FORCE_INTEGRITY": "the level the product ships in",
    "SECURITYFS": "lockdown's own state file needs it; without it the LSM's "
                  "core initcall fails with -ENODEV on every boot",
    "MODULE_SIG": "modules are signed at modules_install",
    "MODULE_SIG_FORCE": "without FORCE an unsigned module still loads",
    "MODULE_SIG_ALL": "signs them during the build rather than by hand",
    "SECURITY_LOADPIN": "pins module and firmware loads to one filesystem",
    "SECURITY_LOADPIN_ENFORCE": "LoadPin only warns without it",
    "IO_STRICT_DEVMEM": "keeps driver-owned MMIO out of /dev/mem",
    "SECURITY_LANDLOCK": "L3's confinement is Landlock-only on this kernel "
                         "(seccomp needs net/core/filter.c, which the "
                         "no-internet fork deleted — SECURITY-MODEL F9)",
    "DM_VERITY": "L1 seals the root with it; enabled early so L1 needs no "
                 "second kernel rebuild",
}

MUST_BE_OFF = {
    "KEXEC": "root could boot an unsigned kernel; Secure Boot verified only "
             "the first one",
    "KEXEC_FILE": "the same, through the newer syscall",
    "PROC_KCORE": "hands out kernel memory for the asking",
    "DEBUG_FS": "surface with no consumer in this product",
    "USER_NS": "a standing local-privilege-escalation surface, unused here",
    "BPF_SYSCALL": "a large privileged surface; nothing in the product uses it",
}


def state(text, opt):
    if re.search(r"^CONFIG_%s=y$" % re.escape(opt), text, re.M):
        return "y"
    if re.search(r"^CONFIG_%s=m$" % re.escape(opt), text, re.M):
        return "m"
    return "n"


def artifact_config(image=IMAGE, extractor=EXTRACT, runner=None):
    """Return the configuration embedded in the exact kernel we ship."""
    try:
        if not os.path.isfile(image) or not os.path.isfile(extractor):
            return None
        run = runner or subprocess.run
        result = run([extractor, image], capture_output=True, text=True,
                     timeout=30)
        if result.returncode != 0 or "CONFIG_" not in result.stdout:
            return None
        return result.stdout
    except OSError:
        return None


def main():
    if not os.path.exists(CONFIG):
        print("no built kernel config at %s" % CONFIG)
        return 2
    text = artifact_config()
    bad = 0
    if text is None:
        print("FAIL  shipped bzImage has no extractable embedded config; "
              "enable CONFIG_IKCONFIG and rebuild before release")
        bad += 1
        text = ""
    for opt, why in sorted(MUST_BE_ON.items()):
        got = state(text, opt)
        if got == "n":
            print("FAIL  CONFIG_%-32s must be ON — %s" % (opt, why))
            bad += 1
    for opt, why in sorted(MUST_BE_OFF.items()):
        if state(text, opt) != "n":
            print("FAIL  CONFIG_%-32s must be OFF — %s" % (opt, why))
            bad += 1
    print("%s: %d required on, %d required off, %d wrong"
          % ("FAIL" if bad else "OK", len(MUST_BE_ON), len(MUST_BE_OFF), bad))
    return bad


if __name__ == "__main__":
    sys.exit(main())
