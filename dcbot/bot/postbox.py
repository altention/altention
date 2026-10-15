"""Where posts wait for the bot (state/posts/). Standard library only: tools/dcctl writes here with the system
Python, which has no discord module."""
import json
import os
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
