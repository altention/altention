"""Where posts wait for the bot (state/posts/). Standard library only: tools/dcctl writes here with the system
Python, which has no discord module."""
import json
import os
import re
import time

from . import config

SPOOL = os.path.join(config.STATE, "posts")


def write(post, spool=None):
    """Leave a post for the bot. The file appears whole, never half written."""
    spool = spool or SPOOL
    os.makedirs(spool, exist_ok=True)
    name = "%020d-%d.json" % (time.time_ns(), os.getpid())
    tmp = os.path.join(spool, "." + name)
    with open(tmp, "w") as f:
        json.dump(post, f, ensure_ascii=False)
    os.rename(tmp, os.path.join(spool, name))


def waiting(spool=None):
    """[(path, post)] oldest first, left in the spool until done(path): see events.waiting()."""
    spool = spool or SPOOL
    try:
        names = sorted(n for n in os.listdir(spool) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        return []
    out = []
    for n in names:
        path = os.path.join(spool, n)
        try:
            with open(path) as f:
                out.append((path, json.load(f)))
        except OSError:
            pass
        except ValueError:
            done(path)
    return out


def done(path):
    try:
        os.remove(path)
    except OSError:
        pass


FAILED = os.path.join(config.STATE, "posts-failed")
FAILED_KEEP = 200            # the newest kept; a notification is small
RETRY_FOR = 24 * 3600        # how long one is tried again, from the first time it could not be shown
RETRY_EVERY = 300


def fail(path, post, folder=None):
    """A notification that could be shown nowhere (not in its channel, not in the default one, not in #audit):
    out of the spool into posts-failed/, what is left of it and when it first failed with it, for retry() and
    for doctor. `path`: its spool file, or its file in posts-failed/ already (rewritten in place)."""
    folder = folder or FAILED
    os.makedirs(folder, exist_ok=True)
    post = dict(post, failed_at=post.get("failed_at") or time.time())
    name = os.path.basename(path) if path else "%020d-%d.json" % (time.time_ns(), os.getpid())
    tmp = os.path.join(folder, "." + name)
    with open(tmp, "w") as f:
        json.dump(post, f, ensure_ascii=False)
    os.rename(tmp, os.path.join(folder, name))
    if path and os.path.dirname(os.path.abspath(path)) != os.path.abspath(folder):
        done(path)
    for old in sorted(n for n in os.listdir(folder) if n.endswith(".json") and not n.startswith("."))[:-FAILED_KEEP]:
        done(os.path.join(folder, old))


def failed(folder=None, now=None):
    """[(path, post)] of posts-failed/ still to be tried again (first failed less than RETRY_FOR ago), oldest
    first. The older ones stay where they are, for the owner to read (doctor says they are there)."""
    now = time.time() if now is None else now
    out = []
    for path, post in waiting(folder or FAILED):
        at = post.get("failed_at") if isinstance(post, dict) else None
        if isinstance(at, (int, float)) and not isinstance(at, bool) and 0 <= now - at < RETRY_FOR:
            out.append((path, post))
    return out


RECEIPTS = os.path.join(config.STATE, "receipts")
RECEIPT_KEEP = 24 * 3600  # one nobody came for (its dcctl gave up waiting) is removed after this
RECEIPT = re.compile(r"[0-9a-f]{16}")  # the name a post asks its receipt to be written under (dcctl: token_hex(8))


def write_receipt(token, receipt, folder=None):
    """What became of a post that asked for a receipt (altention dc post --wait, C-87): {"ok": True, "message_id",
    "channel_id"} or {"ok": False, "why"}, for its dcctl to read. A token not of RECEIPT's shape is not written."""
    if not isinstance(token, str) or not RECEIPT.fullmatch(token):
        return
    folder = folder or RECEIPTS
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, ".%s.%d" % (token, os.getpid()))
    with open(tmp, "w") as f:
        json.dump(dict(receipt, at=time.time()), f, ensure_ascii=False)
    os.rename(tmp, os.path.join(folder, token + ".json"))
    for name in os.listdir(folder):  # the ones nobody came for
        path = os.path.join(folder, name)
        try:
            if time.time() - os.path.getmtime(path) > RECEIPT_KEEP:
                os.remove(path)
        except OSError:
            pass


def take_receipt(token, folder=None):
    """The receipt written for `token`, removed; None while there is none (or it cannot be read yet)."""
    path = os.path.join(folder or RECEIPTS, token + ".json")
    try:
        with open(path) as f:
            got = json.load(f)
    except (OSError, ValueError):
        return None
    done(path)
    return got if isinstance(got, dict) else None


def heartbeat_age():
    """Seconds since the bot last said it was alive (None: never)."""
    try:
        with open(os.path.join(config.STATE, "heartbeat")) as f:
            return max(0, int(time.time()) - int(f.read().strip()))
    except (OSError, ValueError):
        return None


def drain(spool=None):
    """The waiting posts, oldest first, removed from the spool."""
    spool = spool or SPOOL
    try:
        names = sorted(n for n in os.listdir(spool) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        return []
    out = []
    for n in names:
        path = os.path.join(spool, n)
        try:
            with open(path) as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            pass
        try:
            os.remove(path)
        except OSError:
            pass
    return out
