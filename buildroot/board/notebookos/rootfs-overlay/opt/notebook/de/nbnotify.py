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
import math
import re
import time

# Where the spool lives. Same $NB_HOME/.config/notebook root every app persists
# under, so a notification travels with the rest of a person's session state and
# survives a restart the way an unread message should.
HOME = os.environ.get("NB_HOME", "/root")
CFG_DIR = os.path.join(HOME, ".config", "notebook")
SPOOL = os.path.join(CFG_DIR, "notifications")
SEEN_FILE = os.path.join(CFG_DIR, "notifications-seen.json")

# ...and a spool OUTSIDE it, for the one case the primary cannot serve.
#
# The messages that matter most are the ones about a disk that will not take a
# write -- "Your recipes could not be saved", "The disc was not written". Every
# one of those is posted by an app whose config directory is exactly what just
# refused, and the spool lives INSIDE that directory: the notice about the
# failure failed to be written, silently, and the person was told nothing. It
# was found by a skeptic driving a calculator with a read-only config dir: the
# app recorded the error on itself and posted a notification that went nowhere.
#
# So there is a second spool on the temp filesystem, which is a tmpfs on this
# image and writable when the home is not. It is keyed by NB_HOME so two
# sessions (or two suites) never read each other's tray, and readers merge the
# two. Records here do not survive a reboot, which is the right trade: a
# message about a disk that is full right now is news, not history.
_KEY = "".join(c if c.isalnum() else "-" for c in os.path.abspath(HOME))[-64:]
FALLBACK_SPOOL = os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "nb-notify-%d" % os.getuid(), _KEY)


def spools():
    """Both spool directories, primary first. Readers walk both; a writer uses
    the fallback only when the primary refuses."""
    return (SPOOL, FALLBACK_SPOOL)


def _records():
    """(name, directory) for every record file in either spool, so a reader
    never has to know there are two. Names carry a microsecond stamp, so
    sorting by NAME still orders the merged set correctly."""
    out = []
    for directory in spools():
        try:
            for name in os.listdir(directory):
                if _is_record(name):
                    out.append((name, directory))
        except OSError:
            continue
    return out

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
_RECORD_RE = re.compile(r"^[0-9]{16,}-[0-9]+-[0-9]+\.json$")


def _valid_seen_time(value):
    """A usable read watermark; a future wall time means the clock rolled back."""
    try:
        at = float(value)
    except (TypeError, ValueError):
        return 0.0
    # A read marker is written from this machine's current clock. If it is now
    # in the future, keeping it would hide every new normal-time notification
    # until the clock catches up, which can take days after an RTC correction.
    return at if math.isfinite(at) and at <= time.time() else 0.0


def _stamp_name(at, seq):
    """The spool file name for a notification: zero-padded microseconds, the
    sending pid, and a per-process counter. Sorts chronologically as PLAIN TEXT,
    which is what lets prune() order records without parsing them."""
    return "%016d-%d-%d.json" % (int(at * 1000000), os.getpid(), seq)


def _is_record(name):
    # Only ever consider our own records. atomic_write_json writes through a
    # ".nbw-XXXX.tmp" temp file in the same directory, and a half-written temp
    # caught mid-rename must never be read as a notification.
    return isinstance(name, str) and _RECORD_RE.fullmatch(name) is not None


def _fsync_spool():
    """Best-effort durability for record deletion/rename directory entries."""
    try:
        fd = os.open(SPOOL, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


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
    try:
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
        os.makedirs(SPOOL, exist_ok=True)
        # The house crash-safe writer: temp + fsync + replace + directory
        # fsync. Imported here rather than at module level because nbapp
        # imports GTK — a sender already has it loaded, and this keeps
        # nbnotify importable by a test (or a future headless job) that does
        # not, without a circular import back through nbapp.
        import nbapp
        nbapp.atomic_write_json(os.path.join(SPOOL, name), rec)
    except Exception:                                             # noqa: BLE001
        # The primary spool is inside the very directory that just refused.
        # Say it on the filesystem that can still take a write, rather than
        # dropping the one message the person most needs (see FALLBACK_SPOOL).
        try:
            import nbapp
            os.makedirs(FALLBACK_SPOOL, exist_ok=True)
            nbapp.atomic_write_json(os.path.join(FALLBACK_SPOOL, name), rec)
        except Exception:                                         # noqa: BLE001
            return ""
    # Protect this arrival from a concurrent/bad-clock pruning pass.  The
    # filename clock can move backwards; prune() also treats records dated
    # ahead of the current clock as the oldest arrivals until the spool is
    # back under its bound.
    prune(protect=name)
    return name[:-len(".json")]


def prune(protect=None):
    """Enforce MAX_KEEP and MAX_AGE_S. Write-path only — see the module note.

    Ordering and age both come from the file NAME, so a record whose JSON is
    damaged is still pruned on schedule. A prune driven by parsed contents would
    leave exactly the unreadable files behind forever, which is the shape of
    litter that eventually fills a directory nobody looks in.
    """
    # Both spools, pruned as ONE tray: MAX_KEEP is what a person can read, and
    # a fallback record is just as much a row in the list as a primary one.
    where = dict(_records())            # name -> directory it lives in
    names = sorted(where)
    if not names:
        return
    now_us = time.time() * 1000000
    cutoff = now_us - MAX_AGE_S * 1000000
    stamps = {}
    for name in names:
        try:
            stamps[name] = int(name.split("-", 1)[0])
        except ValueError:
            continue

    # A corrected RTC can leave a full spool whose old records all appear to
    # be from the future.  Plain lexical pruning would then delete every new
    # normal-time post.  When space is needed, retire those impossible-future
    # records before genuine current-clock history.  `protect` also closes the
    # small race where another writer prunes while this post is being made.
    excess = max(0, len(names) - MAX_KEEP)
    candidates = [n for n in names if n != protect]
    candidates.sort(key=lambda n: (0 if stamps.get(n, 0) > now_us else 1,
                                   stamps.get(n, 0), n))
    drop = candidates[:excess]
    keep = [n for n in names if n not in set(drop)]
    for name in keep:
        try:
            if stamps.get(name, int(name.split("-", 1)[0])) < cutoff:
                drop.append(name)
        except ValueError:
            pass          # not one of ours after all; leave it alone
    removed = False
    for name in drop:
        try:
            os.remove(os.path.join(where.get(name, SPOOL), name))
            removed = True
        except OSError:
            pass          # another process pruned it first: that is the goal
    if removed:
        _fsync_spool()


def load():
    """Every live notification, NEWEST FIRST. Never deletes, never raises.

    A record that is missing, unparseable or the wrong shape is skipped and the
    rest of the tray still loads: one corrupt file must not empty the centre.
    """
    records = sorted(_records(), reverse=True)
    if not records:
        return []
    import json
    cutoff = time.time() - MAX_AGE_S
    out = []
    for name, directory in records:
        try:
            with open(os.path.join(directory, name)) as fh:
                rec = json.load(fh)
        except Exception:                                         # noqa: BLE001
            continue
        if not isinstance(rec, dict) or not isinstance(rec.get("title"), str):
            continue
        try:
            at = float(rec.get("at", 0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(at):
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
    if not _is_record(nid + ".json"):
        return None
    return os.path.join(SPOOL, nid + ".json")


def dismiss(nid):
    """Remove one notification. True if it went."""
    path = _record_path(nid)
    if path is None:
        return False
    try:
        os.remove(path)
        _fsync_spool()
        return True
    except OSError:
        return False


def clear_all():
    """Empty the tray. Returns how many went — the caller says so out loud."""
    records = _records()
    if not records:
        return 0
    gone = 0
    for name, directory in records:
        try:
            os.remove(os.path.join(directory, name))
            gone += 1
        except OSError:
            pass
    if gone:
        _fsync_spool()
    return gone


def seen_at():
    """The timestamp the tray was last opened at. Anything newer is unread."""
    try:
        import json
        with open(SEEN_FILE) as fh:
            data = json.load(fh)
        at = _valid_seen_time(data.get("at", 0)) if isinstance(data, dict) else 0.0
        # Python's JSON reader accepts NaN and infinities.  They are numbers
        # syntactically, but not timestamps: every `fresh_at > NaN` comparison
        # is False, which made one damaged seen file suppress the unread badge
        # for every notification that arrived afterwards.  Damage to the read
        # marker must mean "nothing has been seen", never "everything has".
        return at
    except Exception:                                             # noqa: BLE001
        return 0.0


def _seen_state():
    """(wall-time watermark, exact future record ids) from the read marker."""
    try:
        import json
        with open(SEEN_FILE) as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return 0.0, set()
        at = _valid_seen_time(data.get("at", 0))
        ids = data.get("ids")
        ids = ({item for item in ids if isinstance(item, str)}
               if isinstance(ids, list) else set())
        return at, ids
    except Exception:                                             # noqa: BLE001
        return 0.0, set()


def mark_seen(at=None, ids=None):
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
        record = {"at": time.time() if at is None else at}
        if ids:
            record["ids"] = list(dict.fromkeys(
                item for item in ids if isinstance(item, str)))[:MAX_KEEP]
        nbapp.atomic_write_json(SEEN_FILE, record)
        return True
    except Exception:                                             # noqa: BLE001
        return False


def open_tray(with_status=False):
    """Load the rows being shown and mark exactly those arrivals as seen.

    Capture the watermark BEFORE the directory read.  If another process posts
    while load() is in flight, that record may or may not make this rendering,
    but its timestamp is newer than the watermark either way and the bell will
    still announce it.  Loading first and marking at ``time.time()`` afterwards
    silently marked a not-yet-visible arrival as read in that race window.
    """
    watermark = time.time()
    items = load()
    # A clock correction can leave displayed rows dated beyond wall time. Keep
    # the normal scalar watermark (so a concurrent normal-time arrival stays
    # unread) and mark only those actually displayed future rows by exact id.
    future_ids = [rec.get("id") for rec in items
                  if isinstance(rec.get("at"), (int, float))
                  and math.isfinite(rec.get("at"))
                  and rec.get("at") > watermark]
    marked = mark_seen(watermark, future_ids)
    return (items, marked) if with_status else items


def unread_count(items=None):
    """How many notifications arrived after the last look. Capped for display
    at 100 by the caller, not here — this returns the true number."""
    if items is None:
        items = load()
    mark, exact = _seen_state()
    return sum(1 for rec in items
               if rec.get("at", 0) > mark and rec.get("id") not in exact)


def state_key():
    """A cheap value that CHANGES whenever the tray changes, for a poller.

    The panel checks this once a second. Reading every record once a second to
    find out that nothing happened would be a directory walk plus N file opens
    per second, on a machine whose menu bar is drawn by the CPU; the directory's
    own mtime plus its entry names answers the same question without opening a
    single record, and is what keeps the tick from touching a widget when
    nothing changed (Constitution B8).

    The bounded name tuple is carried as well as the mtime because a dismiss
    followed by a post inside the same mtime granularity keeps the SAME count.
    Names detect that replacement exactly; count alone does not.
    """
    def _stat_key(path):
        try:
            st = os.stat(path)
            # atomic_write_json replaces the seen file. Inode identity detects
            # that replacement even on filesystems whose timestamp granularity
            # gives old and new files the same mtime.
            return (getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)),
                    st.st_ino, st.st_size)
        except OSError:
            return (0, 0, 0)
    names = tuple(sorted(name for name, _dir in _records()))
    # The read mark is stat'ed, not parsed: this runs once a second and its
    # VALUE is never needed here, only whether it has moved since last time.
    return (_stat_key(SPOOL), _stat_key(FALLBACK_SPOOL), names,
            _stat_key(SEEN_FILE))
