#!/usr/bin/env python3
"""installer_update_selftest — the Update path in de/installer.py.

    tools/guestrun.sh python3 tools/installer_update_selftest.py

The installer offers two things now, and the second one is a promise: replace
the system on a disk that already carries one and KEEP everything of the
user's. A promise about somebody's files, made on the same screen whose other
button erases them, is only worth what a test can show:

  * an Update is offered only where a Notebook OS install has actually been
    READ off the disk — never on a blank one, never on a foreign one, and never
    on the strength of a partition label anybody can type;
  * /root survives it byte for byte, because /root IS the home directory on
    this appliance (session.sh pins NB_HOME=/root), and so do /home and /data;
  * the password, the computer name, the keyboard and the locale — which live
    in /etc, inside the tree being replaced — come back on the other side, and
    de/login.py still lets the same password in;
  * a run that stops says what state the machine is ACTUALLY in. That is the
    whole reason the new system is unpacked beside the old one instead of over
    it: a failure while unpacking has changed nothing, and telling somebody
    their machine is broken when it is not is how they end up reinstalling —
    erasing the files this path exists to keep.

Nothing here partitions, formats or mounts anything. The engine's destructive
half has no counterpart on this path at all, and one of the checks below is
exactly that claim, read off the source.
"""
import os
import re
import ast
import sys
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="nb-instupd-")
os.environ.setdefault("NB_HOME", _HOME)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OVERLAY = os.path.join(REPO, "buildroot", "board", "notebookos",
                       "rootfs-overlay")
DE = os.path.join(OVERLAY, "opt", "notebook", "de")
if "--de" in sys.argv:
    DE = os.path.abspath(sys.argv[sys.argv.index("--de") + 1])
sys.path.insert(0, DE)

import gi                                                        # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk                                    # noqa: E402


def _install_guest_crypt():
    """Make `crypt` available when the interpreter running this has none.

    The shipped image's Python 3.11 has the stdlib module; 3.13 dropped it, and
    the harness may be either. Without this, login.verify() catches the
    ImportError and answers False for EVERY password -- so "the password still
    opens the machine" and "a wrong password does not" would both pass while
    measuring nothing at all. installer_target_selftest installs the same
    glibc-crypt(3) shim for the same reason."""
    try:
        import crypt                                            # noqa: PLC0415
        return crypt
    except ImportError:
        pass
    import ctypes                                               # noqa: PLC0415
    import types                                                # noqa: PLC0415
    lib = ctypes.CDLL("libcrypt.so.1")
    lib.crypt.restype = ctypes.c_char_p
    lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]

    def _c(word, salt):
        r = lib.crypt(word.encode(), salt.encode())
        return r.decode() if r else None
    m = types.ModuleType("crypt")
    m.crypt = _c
    m.METHOD_SHA512 = "6"
    m.mksalt = lambda _m=None: "$6$nbupdateselftest"
    sys.modules["crypt"] = m
    return m


_install_guest_crypt()

import installer                                                 # noqa: E402
import login                                                     # noqa: E402

GB = 1024 ** 3
PASSWORD = "a real password"
FAILED, N = [], [0]


def check(name, cond, detail=""):
    N[0] += 1
    print(("PASS " if cond else "FAIL ") + name
          + (("  " + detail) if detail else ""))
    if not cond:
        FAILED.append(name)
    return bool(cond)


def not_reached(reason, *names):
    for n in names:
        check("%s  [not reached: %s]" % (n, reason), False)


def blank_installer(**cfg):
    """The real Installer with no GTK construction. The update engine's parts
    are plain file and directory work and have to be exercised as themselves;
    the pieces that need a window get one further down."""
    app = installer.Installer.__new__(installer.Installer)
    app._post_log = lambda *a, **k: None
    app._swapped = []
    app._update_state = ""
    app.tools = {}
    app.payload_bytes = 2 * GB
    app.medium_ok = True
    app.missing_tools = []
    app.can_hash = True
    app.cfg = {"disk": "/dev/sda", "disk_model": "Acme SSD",
               "disk_size": 64 * GB, "disk_contents": "Linux",
               "mode": "install", "existing": None,
               "hostname": "notebook", "username": "", "kbd": 0, "locale": 0,
               "password": "", "root_passwordless": False, "oem": False,
               "swap": False, "swap_mib": 2048}
    app.cfg.update(cfg)
    return app


def tree(*parts):
    p = os.path.join(*parts)
    os.makedirs(p, exist_ok=True)
    return p


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    return path


def read(path):
    with open(path) as fh:
        return fh.read()


def installed_root(base, name, marker=True, build="2026-01-01"):
    """A directory shaped like an installed Notebook OS root: the two things
    _read_marker insists on, plus a home directory with something in it."""
    root = tree(base, name)
    if marker:
        put(os.path.join(root, "etc", "os-release"),
            'NAME="Notebook OS"\nID=notebookos\nVERSION="1.0"\n'
            'BUILD_ID="%s"\n' % build)
        tree(root, "opt", "notebook", "de")
    return root


# ---------------------------------------------------------------- detection
def t_marker():
    print("-- what counts as an install, and what does not")
    base = tree(_HOME, "markers")
    app = blank_installer()

    blank = tree(base, "blank")
    check("a blank filesystem is not an install",
          app._probe_install(blank) is None)

    foreign = tree(base, "foreign")
    put(os.path.join(foreign, "etc", "os-release"),
        'NAME="Debian GNU/Linux"\nID=debian\n')
    tree(foreign, "opt", "notebook")
    check("another operating system is not an install",
          app._probe_install(foreign) is None,
          "even with /opt/notebook sitting beside it")

    # The case a partition LABEL alone would get wrong. os-release is a text
    # file anybody can copy onto a disk; the system it names has to be there.
    faked = tree(base, "faked")
    put(os.path.join(faked, "etc", "os-release"),
        'NAME="Notebook OS"\nID=notebookos\n')
    check("an os-release with no desktop beside it is not an install",
          app._probe_install(faked) is None)

    real = installed_root(base, "real", build="2026-08-15")
    info = app._probe_install(real)
    got = check("a real Notebook OS root is recognised", info is not None)
    if got:
        check("...and carries the build it is on",
              info.get("build") == "2026-08-15", repr(info.get("build")))
        check("...and is not mistaken for a half-finished update",
              info.get("unfinished") is False and info.get("halfway") is False,
              repr(info))
    else:
        not_reached("no info", "...and carries the build it is on",
                    "...and is not mistaken for a half-finished update")

    # A machine whose last update stopped after the swap began: /etc is inside
    # the working directory. Refusing to recognise it would take away the one
    # action that repairs it, which is what the failure page tells its owner
    # to do.
    halfway = tree(base, "halfway")
    installed_root(halfway, installer.UPDATE_OLD_DIR)
    info = app._probe_install(halfway)
    got = check("a machine left half-updated is still recognised",
                info is not None)
    if got:
        check("...and is reported as not startable as it stands",
              info.get("halfway") is True, repr(info))
        check("...with its settings read from where they actually are",
              info.get("config_sub") == installer.UPDATE_OLD_DIR,
              repr(info.get("config_sub")))
    else:
        not_reached("no info", "...and is reported as not startable as it "
                    "stands", "...with its settings read from where they "
                    "actually are")


def t_no_update_without_proof():
    print("-- a disk is not offered an update on the strength of a name")
    app = blank_installer()
    app.tools = {"lsblk": "/bin/lsblk", "mount": "/bin/mount",
                 "umount": "/bin/umount"}
    mounted = []
    app._mount_probe = lambda dev: (mounted.append(dev), False)[1]

    real_run = installer.run_cmd
    try:
        # A blank disk: one whole-disk row, no partitions at all.
        installer.run_cmd = lambda *a, **k: (
            0, 'NAME="sdb" FSTYPE="" LABEL="" PARTUUID="" PARTTYPE="" '
               'TYPE="disk"\n')
        check("a blank disk offers no update",
              app._detect_install("sdb") is None)
        check("...and is not even mounted to find that out",
              mounted == [], repr(mounted))

        # A Notebook OS root with no EFI system partition beside it. The kernel
        # and the bootloader live on the ESP, so an update could not finish.
        installer.run_cmd = lambda *a, **k: (
            0, 'NAME="sdb1" FSTYPE="ext4" LABEL="notebookos" PARTUUID="%s" '
               'PARTTYPE="0fc63daf-8483-4772-8e79-3d69d8477de4" TYPE="part"\n'
               % installer.ROOT_PARTUUID)
        check("a root with no start-up partition offers no update",
              app._detect_install("sdb") is None)
        check("...and is still not mounted", mounted == [], repr(mounted))

        # A Windows disk. Nothing here is ours.
        installer.run_cmd = lambda *a, **k: (
            0, 'NAME="sdb1" FSTYPE="ntfs" LABEL="Windows" PARTUUID="abc" '
               'PARTTYPE="" TYPE="part"\n')
        check("another system's disk offers no update",
              app._detect_install("sdb") is None)
    finally:
        installer.run_cmd = real_run

    # Mode alone can never turn the destructive engine into the safe one, or
    # the safe one into a run against a disk nothing was read off.
    app2 = blank_installer(mode="update", existing=None)
    check("\"update\" with nothing found is not an update",
          app2._is_update() is False)
    app2.cfg["existing"] = {"root": "/dev/sda2", "esp": "/dev/sda1",
                            "free": 64 * GB}
    check("...and is one once there is something to update",
          app2._is_update() is True)


def t_room_is_refused_before_the_run():
    print("-- an update with nowhere to unpack is refused on the Summary")
    info = {"root": "/dev/sda2", "esp": "/dev/sda1", "free": 200 * 1024 ** 2,
            "unfinished": False}
    app = blank_installer(mode="update", existing=info)
    need = app._update_free_bytes()
    check("an update states how much room it needs (%s)"
          % installer.human_bytes(need), need > app.payload_bytes)
    ok, why = app._install_ready()
    check("a root with no room for the new system is refused", not ok)
    check("...and the refusal gives both figures",
          "free" in why and "needed" in why, repr(why))
    app.cfg["existing"] = dict(info, free=40 * GB)
    ok, why = app._install_ready()
    check("a root with room is accepted", ok, repr(why))
    # An update sets no password, so the gate that refuses an install this
    # image cannot hash a password for must not refuse an update.
    app.can_hash = False
    ok, why = app._install_ready()
    check("an update is not refused for a password it never sets", ok,
          repr(why))
    # ...and the same machine still refuses a fresh install for it.
    app.cfg["mode"] = "install"
    ok, _why = app._install_ready()
    check("...while a fresh install still is", not ok)


# ------------------------------------------------------- what survives a swap
def make_pair(base):
    """An installed machine and the new system about to replace it."""
    target = installed_root(base, "target", build="2026-01-01")
    put(os.path.join(target, "usr", "bin", "removed-in-the-new-release"), "x")
    put(os.path.join(target, "opt", "notebook", "de", "writer.py"), "OLD")
    put(os.path.join(target, "bin", "sh"), "OLD")
    # the user's own machine
    put(os.path.join(target, "root", "Documents", "novel.txt"), "chapter one")
    put(os.path.join(target, "root", ".config", "notebook", "locale.json"),
        '{"keyboard": "fr", "lang": "fr"}')
    tree(target, "home")
    put(os.path.join(target, "data", "maps", "europe.nbm2"), "a continent")

    stage = installed_root(base, "stage", build="2026-08-15")
    put(os.path.join(stage, "opt", "notebook", "de", "writer.py"), "NEW")
    put(os.path.join(stage, "usr", "bin", "new-in-this-release"), "y")
    put(os.path.join(stage, "bin", "sh"), "NEW")
    # the shipped skeleton, which must NOT land on top of the user's
    put(os.path.join(stage, "root", "Documents", ".keep"), "")
    tree(stage, "home")
    return target, stage


def t_swap_keeps_the_user():
    print("-- replacing the system keeps everything that is not the system")
    base = tree(_HOME, "swap")
    target, stage = make_pair(base)
    app = blank_installer()
    app._swap_trees(target, stage)

    check("the user's document is exactly as it was",
          read(os.path.join(target, "root", "Documents",
                            "novel.txt")) == "chapter one")
    check("the desktop's own settings are untouched",
          "fr" in read(os.path.join(target, "root", ".config", "notebook",
                                    "locale.json")))
    check("the map packs are still there",
          os.path.exists(os.path.join(target, "data", "maps", "europe.nbm2")))
    check("/home is left alone", os.path.isdir(os.path.join(target, "home")))
    check("the shipped home skeleton did not land on the user's",
          not os.path.exists(os.path.join(target, "root", "Documents",
                                          ".keep")))

    check("the system files are the new ones",
          read(os.path.join(target, "opt", "notebook", "de",
                            "writer.py")) == "NEW")
    check("...including outside /opt",
          read(os.path.join(target, "bin", "sh")) == "NEW"
          and os.path.exists(os.path.join(target, "usr", "bin",
                                          "new-in-this-release")))
    check("a file dropped from the new release is gone",
          not os.path.exists(os.path.join(target, "usr", "bin",
                                          "removed-in-the-new-release")))

    old = os.path.join(target, installer.UPDATE_OLD_DIR)
    kept = [n for n in installer.PRESERVED_DIRS
            if os.path.exists(os.path.join(old, n))]
    check("nothing of the user's is ever moved aside at all", not kept,
          "found in %s: %r" % (installer.UPDATE_OLD_DIR, kept))
    check("...which is what makes the old system safe to delete",
          sorted(os.listdir(old)) == ["bin", "etc", "opt", "usr"],
          repr(sorted(os.listdir(old))))


def t_nb_home_is_in_the_kept_list():
    print("-- the kept list is the one the session actually uses")
    sess = read(os.path.join(OVERLAY, "opt", "notebook", "session.sh"))
    m = re.search(r"^export NB_HOME=(\S+)", sess, re.M)
    got = check("session.sh still names a home directory", m is not None)
    if not got:
        not_reached("no NB_HOME", "the home directory the desktop uses is kept")
        return
    home = m.group(1).strip().strip('"').strip("/")
    # Read from session.sh rather than written down here: the day NB_HOME
    # moves, an update that keeps the old path keeps an empty directory and
    # quietly destroys every document on the machine.
    check("the home directory the desktop uses is kept",
          home in installer.PRESERVED_DIRS,
          "NB_HOME=/%s, kept=%r" % (home, installer.PRESERVED_DIRS))


# ---------------------------------------------------------- a run that stops
def t_a_swap_that_stops_is_put_back():
    print("-- a replacement that stops half way is undone, and says so")
    base = tree(_HOME, "rollback")
    target, stage = make_pair(base)
    app = blank_installer()
    check("nothing has been replaced before the swap starts",
          app._update_state == "")

    real_rename = os.rename
    calls = [0]

    def flaky(a, b, *rest, **kw):
        calls[0] += 1
        if calls[0] == 3:
            raise OSError(5, "Input/output error")
        return real_rename(a, b, *rest, **kw)

    os.rename = flaky
    try:
        app._swap_trees(target, stage)
    except installer.InstallError as e:
        stopped = str(e)
    else:
        stopped = ""
    finally:
        os.rename = real_rename

    check("the swap stops on the first move it cannot make", bool(stopped),
          stopped)
    check("...and the machine is marked as not startable",
          app._update_state == "broken", repr(app._update_state))
    check("the failure says the machine will not start up",
          "will not start up" in app._update_failure_state(),
          repr(app._update_failure_state()))

    restored = app._restore_trees(target)
    check("the old system can be put back", restored is True)
    check("...and it IS back",
          read(os.path.join(target, "bin", "sh")) == "OLD",
          repr(read(os.path.join(target, "bin", "sh"))))
    check("...with the user's files never having moved",
          read(os.path.join(target, "root", "Documents",
                            "novel.txt")) == "chapter one")
    app._update_state = "restored"
    words = app._update_failure_state()
    check("and the failure page then says the machine still starts up",
          "still starts up" in words and "will not start up" not in words,
          repr(words))

    # The machine that arrived already half-updated. _do_update marks it
    # broken before it does anything, so a run that then fails on its own
    # preflight asks the same question — and the answer must be no. Claiming
    # a restore here would tell somebody their machine starts up when nothing
    # this run did could possibly have made it start up.
    idle = blank_installer()
    idle._update_state = "broken"
    check("undoing nothing is not the same as putting the system back",
          idle._restore_trees(tree(_HOME, "nothing-to-undo")) is False)


def t_the_four_sentences():
    print("-- a stopped update says what state the machine is in")
    app = blank_installer()
    said = {}
    for state in ("safe", "restored", "broken", "finish"):
        app._update_state = state
        said[state] = app._update_failure_state()
    check("each state has its own sentence",
          len(set(said.values())) == 4)
    check("a failure before anything moved does not frighten anybody",
          "still starts up" in said["safe"]
          and "will not start" not in said["safe"], repr(said["safe"]))
    check("a failure after the swap does not comfort anybody",
          "will not start up" in said["broken"]
          and "still starts up" not in said["broken"], repr(said["broken"]))
    check("an unfinished replacement is honest that it may not start",
          "may not start up" in said["finish"], repr(said["finish"]))
    for state, words in said.items():
        check("...and %r still promises the files are there" % state,
              "files" in words, repr(words))


def t_the_update_engine_destroys_nothing():
    print("-- the update path has no destructive half at all")
    src = read(os.path.join(DE, "installer.py"))
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_do_update")
    # The CODE, not the source text: ast.unparse drops the comments and the
    # docstring, so a method that merely EXPLAINS that it does not partition
    # anything cannot pass this by saying so. Reading the raw lines is how a
    # check ends up measuring prose.
    if ast.get_docstring(fn) is not None:
        fn = ast.Module(body=[ast.FunctionDef(
            name=fn.name, args=fn.args, body=fn.body[1:], decorator_list=[],
            returns=None, type_comment=None, lineno=1, col_offset=0)],
            type_ignores=[])
        code = ast.unparse(ast.fix_missing_locations(fn))
    else:
        code = ast.unparse(fn)
    for tool in ("sgdisk", "wipefs", "mkfs", "mkswap"):
        check("the update never reaches for %s" % tool, tool not in code)
    check("...and the install engine still does",
          "sgdisk" in src and "wipefs" in src)


# ------------------------------------------------- settings across a new /etc
def old_machine(base):
    """An installed machine configured the way _configure_target leaves one."""
    root = installed_root(base, "old")
    put(os.path.join(root, "etc", "hostname"), "studio\n")
    put(os.path.join(root, "etc", "notebookos-user"), "Ada Lovelace\n")
    put(os.path.join(root, "etc", "X11", "xorg.conf.d", "00-keyboard.conf"),
        'Section "InputClass"\n    Option "XkbLayout" "fr"\nEndSection\n')
    put(os.path.join(root, "etc", "profile"),
        "# /etc/profile\n\n# Notebook OS installer — system locale\n"
        "export LANG=C.UTF-8\nexport LC_ALL=C.UTF-8\n")
    put(os.path.join(root, "etc", "fstab"),
        "# /etc/fstab\nproc\t/proc\tproc\tdefaults\t0\t0\n"
        "LABEL=%s\tswap\tswap\tdefaults\t0\t0\n" % installer.SWAP_LABEL)
    put(os.path.join(root, "etc", "inittab"),
        "::sysinit:/etc/init.d/rcS\n"
        "tty2::respawn:/sbin/getty 38400 tty2\n")
    return root


def new_system(base):
    """A freshly unpacked tree: the shipped inittab and a LOCKED root, which is
    exactly the state that makes carrying the password across compulsory."""
    root = installed_root(base, "new", build="2026-08-15")
    shutil.copy(os.path.join(OVERLAY, "etc", "inittab"),
                os.path.join(root, "etc", "inittab"))
    put(os.path.join(root, "etc", "shadow"), "root:*:19000:0:99999:7:::\n")
    put(os.path.join(root, "etc", "passwd"), "root:x:0:0:root:/root:/bin/sh\n")
    put(os.path.join(root, "etc", "fstab"), "# /etc/fstab (new release)\n")
    put(os.path.join(root, "etc", "profile"), "# /etc/profile (new release)\n")
    return root


def t_settings_cross_the_replaced_etc():
    print("-- the machine's own name, keyboard, locale and password come back")
    base = tree(_HOME, "carry")
    app = blank_installer()
    old = old_machine(base)
    pwhash = app._hash_password(PASSWORD)
    put(os.path.join(old, "etc", "shadow"),
        "root:%s:19100:0:99999:7:::\ndaemon:*:19000:0:99999:7:::\n" % pwhash)

    keep = app._read_carried(old)
    new = new_system(base)
    app._apply_carried(new, keep)

    check("the computer name comes across",
          read(os.path.join(new, "etc", "hostname")).strip() == "studio")
    check("the owner's name comes across",
          read(os.path.join(new, "etc",
                            "notebookos-user")).strip() == "Ada Lovelace")
    check("the keyboard layout comes across",
          'XkbLayout" "fr"' in read(os.path.join(
              new, "etc", "X11", "xorg.conf.d", "00-keyboard.conf")))
    prof = read(os.path.join(new, "etc", "profile"))
    check("the locale comes across", "export LANG=C.UTF-8" in prof)
    check("...on top of the NEW release's profile, not instead of it",
          "new release" in prof)
    fstab = read(os.path.join(new, "etc", "fstab"))
    check("the swap line comes across",
          "LABEL=%s" % installer.SWAP_LABEL in fstab)
    check("...on top of the NEW release's fstab", "new release" in fstab)
    getty = [ln for ln in read(os.path.join(new, "etc", "inittab")).splitlines()
             if "getty" in ln and not ln.lstrip().startswith("#")]
    check("the text console comes back, and exactly once",
          len(getty) == 1 and getty[0].startswith("tty2::"), repr(getty))

    # The proof that matters: the code that will ask for this password on the
    # updated machine's first start still accepts it.
    login.SHADOW = os.path.join(new, "etc", "shadow")
    login.PASSWD = os.path.join(new, "etc", "passwd")
    check("the sign-in screen still asks for the same account",
          login.desktop_user() == "root")
    check("THE password the machine had still opens it after an update",
          login.verify("root", PASSWORD) is True)
    check("...and a near miss still does not",
          login.verify("root", PASSWORD + " ") is False)
    check("...and the ageing fields are the machine's own, not today's",
          read(os.path.join(new, "etc", "shadow")).startswith(
              "root:%s:19100:" % pwhash))
    check("...and the other accounts in the new release are left alone",
          "daemon" not in read(os.path.join(new, "etc", "shadow")))

    # A machine set up to start straight into the desktop has a LOCKED root,
    # and an update must not quietly give it a password or take one away.
    locked = installed_root(base, "locked")
    put(os.path.join(locked, "etc", "shadow"), "root:*:19000:0:99999:7:::\n")
    put(os.path.join(locked, "etc", "hostname"), "notebook\n")
    put(os.path.join(locked, "etc", "inittab"), "::sysinit:/etc/init.d/rcS\n")
    keep2 = app._read_carried(locked)
    new2 = new_system(tree(base, "second"))
    app._apply_carried(new2, keep2)
    login.SHADOW = os.path.join(new2, "etc", "shadow")
    login.PASSWD = os.path.join(new2, "etc", "passwd")
    check("a machine that asks for no password still asks for none",
          login.has_password("root") is False)
    check("...and no console is invented for it",
          not [ln for ln in read(os.path.join(new2, "etc",
                                              "inittab")).splitlines()
               if "getty" in ln and not ln.lstrip().startswith("#")])

    # The one thing that cannot be reconstructed. Losing it silently would
    # change how the machine is opened, so the run has to stop while the old
    # system is still in place.
    damaged = installed_root(base, "damaged")
    put(os.path.join(damaged, "etc", "shadow"), "daemon:*:19000:0:99999:7:::\n")
    try:
        app._read_carried(damaged)
    except installer.InstallError as e:
        stopped = str(e)
    else:
        stopped = ""
    check("an unreadable stored password stops the update before it starts",
          "password" in stopped, repr(stopped))


# ----------------------------------------------------------- the wizard says so
def t_the_wizard_offers_it_only_where_it_exists():
    print("-- the choice appears on the disk that has one, and nowhere else")
    app = installer.Installer()
    app.medium_ok = True
    app.missing_tools = []
    app.payload_bytes = 2 * GB
    disks = [("sda", 64 * GB, "Acme SSD 240G", "Windows")]

    def pick():
        for w in walk(app):
            if isinstance(w, Gtk.RadioButton) and "/dev/sda" in (w.get_label()
                                                                 or ""):
                w.set_active(True)
                return True
        return False

    app._populate_disks(app._scan_gen, disks, {})
    got = check("the disk list draws a disk with nothing of ours on it",
                pick())
    if got:
        check("a disk with no install offers no update",
              app._is_update() is False and app.cfg["mode"] == "install")
        check("...and the choice is not even on screen",
              not app._mode_card.get_visible())
        check("...and the erase warning is the red one",
              "erased for good" in app._disk_erase.get_text()
              and not app._disk_erase.get_style_context().has_class("calm"),
              repr(app._disk_erase.get_text()))
    else:
        not_reached("no disk row", "a disk with no install offers no update",
                    "...and the choice is not even on screen",
                    "...and the erase warning is the red one")

    info = {"root": "/dev/sda2", "esp": "/dev/sda1", "build": "2026-01-01",
            "version": "1.0", "user": "Ada", "config_sub": "",
            "halfway": False, "leftover": False, "unfinished": False,
            "free": 40 * GB, "disk": "/dev/sda"}
    app._populate_disks(app._scan_gen, disks, {"sda": info})
    got = check("the disk list draws a disk that already has the system",
                pick())
    if not got:
        not_reached("no disk row", "the choice appears, on Update")
        return
    check("the choice appears, on Update",
          app._mode_card.get_visible() and app._is_update()
          and app._rb_update.get_active(), repr(app.cfg["mode"]))
    check("...and the row says so before anything is clicked",
          "already installed" in app._existing_line(info),
          repr(app._existing_line(info)))
    check("...and the warning under it is not the erase one",
          "kept" in app._disk_erase.get_text()
          and app._disk_erase.get_style_context().has_class("calm"),
          repr(app._disk_erase.get_text()))

    app._set_step(app._steps_index("summary"))
    vals = []
    labels = []
    for w in walk(app._summary_card):
        if isinstance(w, Gtk.Label):
            ctx = w.get_style_context()
            (vals if ctx.has_class("inst-value") else labels).append(
                w.get_text())
    check("the review says what is kept and what is replaced",
          "Kept" in labels and "Replaced" in labels, repr(labels))
    check("...and never claims the disk will be erased",
          not any("eras" in v.lower() for v in vals + labels),
          repr([v for v in vals if "eras" in v.lower()]))
    check("the review's warning is paper, not signage red",
          app._summary_danger.get_style_context().has_class("calm"))
    check("the forward button says what it does",
          app.next_btn.get_label() == "Update the system",
          repr(app.next_btn.get_label()))
    check("...and is not the red one",
          not app.next_btn.get_style_context().has_class("inst-primary"))

    # A choice about THIS disk survives leaving the step and coming back
    # (which rescans, and re-selects the same disk). Reverting to Update would
    # be the wizard changing its mind about somebody's disk in silence.
    app._prev_disk, app._prev_mode = "/dev/sda", "install"
    app._populate_disks(app._scan_gen, disks, {"sda": info})
    pick()
    check("a deliberate fresh install survives a rescan of the same disk",
          app.cfg["mode"] == "install" and app._rb_fresh.get_active(),
          repr(app.cfg["mode"]))
    app._prev_disk, app._prev_mode = "/dev/sdb", "install"
    app._populate_disks(app._scan_gen, disks, {"sda": info})
    pick()
    check("...but a choice about a DIFFERENT disk does not carry over",
          app.cfg["mode"] == "update" and app._rb_update.get_active(),
          repr(app.cfg["mode"]))
    app._prev_disk, app._prev_mode = None, ""

    # The other half of the card still erases, and still says so in red.
    app._rb_fresh.set_active(True)
    check("choosing a fresh install goes back to erasing",
          app.cfg["mode"] == "install" and not app._is_update())
    check("...and the warning turns red again",
          "erased for good" in app._disk_erase.get_text()
          and not app._disk_erase.get_style_context().has_class("calm"))
    app._set_step(app._steps_index("summary"))
    check("...and the button is the red erase one again",
          app.next_btn.get_label() == "Erase disk and install"
          and app.next_btn.get_style_context().has_class("inst-primary"),
          repr(app.next_btn.get_label()))


def t_the_options_page_stops_asking():
    print("-- an update answers the Options page from the disk")
    app = installer.Installer()
    app.cfg["existing"] = {"root": "/dev/sda2", "esp": "/dev/sda1",
                           "free": 40 * GB}
    app.cfg["mode"] = "update"
    app._refresh_row_states()
    dead = [r for r in app._update_rows if r.get_sensitive()]
    check("every row on the page is greyed out", not dead,
          "%d rows still live" % len(dead))
    check("...and a sentence on the page says why",
          app._update_note.get_visible()
          and "kept" in app._update_note.get_text())
    ok, hint = app._validate_options()
    check("...so there is nothing left to get wrong", ok and not hint,
          repr(hint))
    # Whatever was typed before the disk was chosen must not reach the config
    # the Summary then describes.
    app._e_host.set_text("typed-before-the-disk-was-picked")
    app._step = app._steps_index("options")
    app._commit_step()
    check("a stale answer is not committed from a page nobody was asked",
          app.cfg["hostname"] != "typed-before-the-disk-was-picked",
          repr(app.cfg["hostname"]))

    app.cfg["mode"] = "install"
    app._refresh_row_states()
    live = [r for r in app._update_rows if r.get_sensitive()]
    check("and a fresh install gets the whole page back",
          len(live) == len(app._update_rows),
          "%d of %d live" % (len(live), len(app._update_rows)))
    check("...with the update note gone",
          not app._update_note.get_visible())


def walk(w):
    yield w
    if isinstance(w, Gtk.Container):
        for c in w.get_children():
            for x in walk(c):
                yield x


def main():
    t_marker()
    t_no_update_without_proof()
    t_room_is_refused_before_the_run()
    t_swap_keeps_the_user()
    t_nb_home_is_in_the_kept_list()
    t_a_swap_that_stops_is_put_back()
    t_the_four_sentences()
    t_the_update_engine_destroys_nothing()
    t_settings_cross_the_replaced_etc()
    t_the_wizard_offers_it_only_where_it_exists()
    t_the_options_page_stops_asking()
    print("\nINSTALLER UPDATE SELFTEST: %d checks, %d passed, %d FAILED"
          % (N[0], N[0] - len(FAILED), len(FAILED)))
    if FAILED:
        print("RESULT: FAILED")
        for f in FAILED:
            print("  " + f)
        return 1
    print("RESULT: ALL PASS")
    return 0


try:
    rc = main()
finally:
    shutil.rmtree(_HOME, ignore_errors=True)
sys.exit(rc)
