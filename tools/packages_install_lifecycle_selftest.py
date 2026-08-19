#!/usr/bin/env python3
"""Headless regression for non-blocking package installation."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "buildroot/board/notebookos/rootfs-overlay",
                                "opt/notebook/de"))
import nbjobs  # noqa: E402
import packages  # noqa: E402


class Widget:
    def __init__(self): self.sensitive = True; self.text = ""
    def set_sensitive(self, value): self.sensitive = value
    def set_text(self, value): self.text = value
    def set_label(self, value): self.text = value


class Jobs:
    def __init__(self): self.call = None
    def start(self, key, work, **kwargs):
        self.call = (key, work, kwargs)
        return object()


class Probe:
    _on_install = packages.Packages._on_install
    def __init__(self): self._jobs = Jobs(); self.refreshes = 0
    def _refresh_after_install(self): self.refreshes += 1


calls = []
real_install = packages.nbpkg_install.install
packages.nbpkg_install.install = lambda path, target="/": (
    calls.append((path, target)) or {"app": {"display": "Notes"}})
try:
    app = Probe(); button = Widget(); status = Widget()
    app._on_install(button, "/media/notes.nbpkg", status)
    checks = [
        (calls == [] and button.sensitive is False,
         "install button returns before verification/copy work runs"),
        (app._jobs.call[0] == "install-package"
         and app._jobs.call[2]["policy"] == nbjobs.REJECT,
         "only one lifecycle-owned install may run at a time"),
    ]
    _key, work, callbacks = app._jobs.call
    manifest = work(None)
    callbacks["on_done"](manifest)
    checks.extend([
        (calls == [("/media/notes.nbpkg", os.environ.get("NB_PKG_TARGET", "/"))],
         "worker performs the install against the configured target"),
        (button.text == "Installed" and app.refreshes == 1,
         "successful completion updates the button and installed inventory"),
    ])
    failed_button = Widget(); failed_status = Widget(); failed_app = Probe()
    failed_app._on_install(failed_button, "/media/bad.nbpkg", failed_status)
    failed_app._jobs.call[2]["on_error"](
        nbjobs.JobError("ValueError", "signature invalid"))
    checks.append(
        (failed_button.sensitive and "signature invalid" in failed_status.text,
         "failed background install is retryable and explains the error"))
    rejected = Probe()
    rejected._jobs.start = lambda *_a, **_kw: None
    rejected_button, rejected_status = Widget(), Widget()
    rejected._on_install(rejected_button, "/media/other.nbpkg", rejected_status)
    checks.append(
        (rejected_button.sensitive and rejected_status.text != "Preparing…"
         and "ready to install" in rejected_status.text,
         "a concurrently rejected install never remains stuck on Preparing"))
finally:
    packages.nbpkg_install.install = real_install

for ok, name in checks:
    print(("PASS " if ok else "FAIL ") + name)
print("RESULT: %s" % ("PASS" if all(ok for ok, _name in checks) else "FAILED"))
raise SystemExit(not all(ok for ok, _name in checks))
