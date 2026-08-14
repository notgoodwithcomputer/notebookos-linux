#!/usr/bin/env python3
"""nbnotify — the notification spool shared by every Notebook OS app.

WHAT THIS IS FOR. One app, one process, fullscreen (Constitution §0.6): while a
person is reading in the Ebook, the USB Writer finishing its stick has nowhere
to say so. Its own status line is behind another window and its process may
already be gone. The menu bar is the one surface that is always on screen, so
the notification centre lives there and this module is how anything reaches it.

WHY A DIRECTORY AND NOT A JSON STORE. There is no session bus on this machine —
no D-Bus, no notification daemon, no portal (Constitution §0.2) — so senders and
the panel agree through the filesystem, like every other cross-process contract
here. But unlike an app's own store, this one has MANY WRITERS: the Disc Burner
and the Video Editor can both finish inside the same second. A single
notifications.json read-modify-written by N processes loses whichever write
lands second. One file per notification has no such window: each sender only
ever creates its OWN file, and `os.replace` onto a name nobody else uses is
atomic. The panel is the only process that ever deletes one.

    $NB_HOME/.config/notebook/notifications/<stamp>-<pid>-<n>.json   one message
    $NB_HOME/.config/notebook/notifications-seen.json                the read mark

The file NAME carries the timestamp, zero-padded, so the directory sorts
chronologically without opening a single file — which is what lets pruning stay
honest about a record it cannot parse (see prune()).

READ AND WRITE ARE SEPARATED ON PURPOSE. `load()` never deletes anything. This
OS has already produced its worst defect that way once — opening and closing an
app destroyed a damaged store with no user action at all — so expiry FILTERS on
the read path and only ever unlinks on the write path, where a person just did
something. A record that cannot be parsed is skipped and the rest of the tray
still loads (Constitution Article II §3: loading is never all-or-nothing).

Apps do not normally call post() directly. `nbapp.AppWindow.notify()` fills in
the sending app's module and display name from the window itself, which is the
call site every app should use:

    self.notify(_t("The disc is written."), _t("It can be taken out."))
"""
import os
import time

# Where the spool lives. Same $NB_HOME/.config/notebook root every app persists
# under, so a notification travels with the rest of a person's session state and
# survives a restart the way an unread message should.
HOME = os.environ.get("NB_HOME", "/root")
CFG_DIR = os.path.join(HOME, ".config", "notebook")
SPOOL = os.path.join(CFG_DIR, "notifications")
SEEN_FILE = os.path.join(CFG_DIR, "notifications-seen.json")

# The tray is bounded in both directions, and neither bound is arbitrary:
#
# MAX_KEEP is what the panel can present as a LIST somebody actually reads. Past
# a couple of screenfuls a notification centre is not a list any more, it is a
# log, and this OS has no log viewer pretending to be one.
#
# MAX_AGE_S is when a message stops being news. "The disc is written" is worth
# reading on the day it happened and is noise a fortnight later; a tray that
# accumulates forever teaches people to ignore it, which is the one failure a
# notification centre cannot survive.
MAX_KEEP = 64
MAX_AGE_S = 7 * 24 * 3600

# Caps on what one sender can put in the tray. A title is a line, a body is a
# sentence or two: the panel wraps them into a 330px card, so an app that hands
# over a whole traceback (or a 4000-character file path) would otherwise inflate
# the dropdown past the bottom of the screen. Truncation happens at POST time so
# the cap is in the file, not re-applied by every reader.
MAX_TITLE = 120
MAX_BODY = 400

_SEQ = 0            # per-process counter: two posts in the same microsecond
#                     still get different file names


def _stamp_name(at, seq):
    """The spool file name for a notification: zero-padded microseconds, the
    sending pid, and a per-process counter. Sorts chronologically as PLAIN TEXT,
    which is what lets prune() order records without parsing them."""
    return "%016d-%d-%d.json" % (int(at * 1000000), os.getpid(), seq)


def _is_record(name):
    # Only ever consider our own records. atomic_write_json writes through a
    # ".nbw-XXXX.tmp" temp file in the same directory, and a half-written temp
    # caught mid-rename must never be read as a notification.
    return name.endswith(".json") and not name.startswith(".")


def post(title, body="", app="", app_name="", icon=""):
    """Put one message in the tray. Returns its id, or "" if it could not be
    written.

    `app` is the DE module name (used for the row's icon, and to open the app
    when its notification is clicked); `app_name` is what the row calls it.
    Both are recorded by the sender because the panel must not need a table of
    every app in the OS to render a row — that table would be a second copy of
    finder.APP_MODULES, and the two would drift.

    Never raises. A notification is a courtesy, not the user's work: an app
    whose disk is full must still finish the job it is reporting on.
    """
    global _SEQ
    at = time.time()
    _SEQ += 1
    rec = {
        "at": at,
        "app": str(app or "")[:64],
        "app_name": str(app_name or "")[:64],
        "icon": str(icon or "")[:32],
        "title": str(title or "")[:MAX_TITLE],
        "body": str(body or "")[:MAX_BODY],
    }
    name = _stamp_name(at, _SEQ)
    try:
        os.makedirs(SPOOL, exist_ok=True)
        # The house crash-safe writer: temp + fsync + replace + directory
        # fsync. Imported here rather than at module level because nbapp
        # imports GTK — a sender already has it loaded, and this keeps
        # nbnotify importable by a test (or a future headless job) that does
        # not, without a circular import back through nbapp.
        import nbapp
        nbapp.atomic_write_json(os.path.join(SPOOL, name), rec)
    except Exception:                                             # noqa: BLE001
        return ""
    prune()
    return name[:-len(".json")]


def prune():
    """Enforce MAX_KEEP and MAX_AGE_S. Write-path only — see the module note.

    Ordering and age both come from the file NAME, so a record whose JSON is
    damaged is still pruned on schedule. A prune driven by parsed contents would
    leave exactly the unreadable files behind forever, which is the shape of
    litter that eventually fills a directory nobody looks in.
    """
    try:
        names = sorted(n for n in os.listdir(SPOOL) if _is_record(n))
    except OSError:
        return
    cutoff = (time.time() - MAX_AGE_S) * 1000000
    drop = names[:-MAX_KEEP] if len(names) > MAX_KEEP else []
    keep = names[len(drop):]
    for name in keep:
        try:
            if int(name.split("-", 1)[0]) < cutoff:
                drop.append(name)
        except ValueError:
            pass          # not one of ours after all; leave it alone
    for name in drop:
        try:
            os.remove(os.path.join(SPOOL, name))
        except OSError:
            pass          # another process pruned it first: that is the goal


def load():
    """Every live notification, NEWEST FIRST. Never deletes, never raises.

    A record that is missing, unparseable or the wrong shape is skipped and the
    rest of the tray still loads: one corrupt file must not empty the centre.
    """
    try:
        names = sorted((n for n in os.listdir(SPOOL) if _is_record(n)),
                       reverse=True)
    except OSError:
        return []
    import json
    cutoff = time.time() - MAX_AGE_S
    out = []
    for name in names:
        try:
            with open(os.path.join(SPOOL, name)) as fh:
                rec = json.load(fh)
        except Exception:                                         # noqa: BLE001
            continue
        if not isinstance(rec, dict) or not isinstance(rec.get("title"), str):
            continue
        try:
            at = float(rec.get("at", 0))
        except (TypeError, ValueError):
            continue
        if at < cutoff:
            # Expired. Filtered, NOT unlinked: see the module note on why the
            # read path never deletes. The next post() sweeps it.
            continue
        rec["at"] = at
        rec["id"] = name[:-len(".json")]
        for key in ("app", "app_name", "icon", "body"):
            if not isinstance(rec.get(key), str):
                rec[key] = ""
        out.append(rec)
        if len(out) >= MAX_KEEP:
            break
    return out


def _record_path(nid):
    """The spool path for an id, or None if the id is not one we issued.

    ids come from load(), so in the product this only ever sees our own names —
    but it is the one place a caller's string becomes a path to unlink, so it
    checks rather than trusts. A component separator or a parent reference here
    would delete a file outside the spool entirely.
    """
    if not nid or not isinstance(nid, str):
        return None
    if os.path.basename(nid) != nid or nid in (".", ".."):
        return None
    if nid.startswith("."):
        return None
    return os.path.join(SPOOL, nid + ".json")


def dismiss(nid):
    """Remove one notification. True if it went."""
    path = _record_path(nid)
    if path is None:
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def clear_all():
    """Empty the tray. Returns how many went — the caller says so out loud."""
    try:
        names = [n for n in os.listdir(SPOOL) if _is_record(n)]
    except OSError:
        return 0
    gone = 0
    for name in names:
        try:
            os.remove(os.path.join(SPOOL, name))
            gone += 1
        except OSError:
            pass
    return gone


def seen_at():
    """The timestamp the tray was last opened at. Anything newer is unread."""
    try:
        import json
        with open(SEEN_FILE) as fh:
            data = json.load(fh)
        return float(data.get("at", 0)) if isinstance(data, dict) else 0.0
    except Exception:                                             # noqa: BLE001
        return 0.0


def mark_seen(at=None):
    """Record that the tray has been looked at, as of `at` (default now).

    ONE mark rather than a read flag per record, and that is a design decision
    with a user-visible consequence: opening the centre clears the count for
    everything currently IN it, and a notification that arrives while it is open
    is still unread when it closes. The alternative — rewriting every file to
    flip a flag — would also mean the panel rebuilding the list under the
    pointer, which resets the scroll position of the thing being read
    (Constitution Article III §2).
    """
    try:
        import nbapp
        os.makedirs(CFG_DIR, exist_ok=True)
        nbapp.atomic_write_json(SEEN_FILE,
                                {"at": time.time() if at is None else at})
        return True
    except Exception:                                             # noqa: BLE001
        return False


def unread_count(items=None):
    """How many notifications arrived after the last look. Capped for display
    at 100 by the caller, not here — this returns the true number."""
    if items is None:
        items = load()
    mark = seen_at()
    return sum(1 for rec in items if rec.get("at", 0) > mark)


def state_key():
    """A cheap value that CHANGES whenever the tray changes, for a poller.

    The panel checks this once a second. Reading every record once a second to
    find out that nothing happened would be a directory walk plus N file opens
    per second, on a machine whose menu bar is drawn by the CPU; the directory's
    own mtime plus its entry count answers the same question without opening a
    single record, and is what keeps the tick from touching a widget when
    nothing changed (Constitution B8).

    The count is carried as well as the mtime because a dismiss followed by a
    post inside the same mtime granularity leaves the directory looking
    untouched — filesystems here report whole seconds, and the panel's tick is
    one second wide.
    """
    def _mtime(path):
        try:
            return os.stat(path).st_mtime
        except OSError:
            return 0
    try:
        n = sum(1 for name in os.listdir(SPOOL) if _is_record(name))
    except OSError:
        n = 0
    # The read mark is stat'ed, not parsed: this runs once a second and its
    # VALUE is never needed here, only whether it has moved since last time.
    return (_mtime(SPOOL), n, _mtime(SEEN_FILE))
