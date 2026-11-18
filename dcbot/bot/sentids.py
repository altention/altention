"""The messages dcctl sent (messaging review P0-4): the newest SENT_KEEP that dcctl send or send-json posted. Written
by dcctl (send) and, since C-172a P1b batch 4, by the bot when it posts a queued send-json. Since C-172a P1c dcctl edit
goes by the ledger, not by this list; it is still how bot/pinimport.py knows which pinned messages were dcctl's, with
the old list of those dcctl pin pinned (dcctl-pinned.json, no longer written: a pin is kept in the ledger now). Standard library only: tools/dcctl runs this with the system Python."""
import contextlib
import fcntl
import json
import os
import sys

SENT_KEEP = 500


def state_file(name):
    """In this machine's state folder under HOME (looked up each time: a test's own HOME keeps its writes)."""
    folder = os.path.join(os.path.expanduser("~"), ".local", "state", "altention")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, name)


def sent_path():
    return state_file("dcctl-sent.json")


def pinned_path():
    """The dcctl-sent messages dcctl pin pinned before C-172a P1c (read once by bot/pinimport.py, then renamed aside)."""
    return state_file("dcctl-pinned.json")


@contextlib.contextmanager
def _locked():
    """One lock for every writer of these lists, the bot and dcctl alike: each reads, changes and writes them back
    inside it, so two at once lose no ID (#code-review on e41c4b2e)."""
    with open(state_file("dcctl-sent.lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _ids(path):
    try:
        with open(path) as f:
            got = json.load(f)
        return [str(i) for i in got] if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def _write_ids(path, ids):
    tmp = path + ".tmp%d" % os.getpid()
    with open(tmp, "w") as f:
        json.dump(ids, f)
    os.replace(tmp, path)


def pinned_ids():
    return _ids(pinned_path())


def sent_ids():
    """The newest SENT_KEEP dcctl sent, and the ones of them dcctl pin pinned before C-172a P1c."""
    return _ids(sent_path()) + [i for i in _ids(pinned_path()) if i not in _ids(sent_path())]


def remember_sent(mid):
    """Keep the ID of a message dcctl sent (the newest SENT_KEEP), so a later dcctl edit may change it."""
    if not mid:
        return
    try:
        with _locked():
            ids = [i for i in _ids(sent_path()) if i != str(mid)] + [str(mid)]
            _write_ids(sent_path(), ids[-SENT_KEEP:])
    except OSError as e:
        print("dcctl：記不下送出的訊息 ID（%s）" % e, file=sys.stderr)
