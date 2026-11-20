"""C-28 (data-safety 4.1, 4.2; whitelist manager 11-20): the channels' state, by a whitelist, into the instance.

`dcctl backup` writes <instance>/backup/dcbot-channels.json from state/channels.json: only the fields KEEP names, each
string through altention/redact.py, keys sorted, written only when it changed (the sync commits it: no empty commit
every round), by a temp file and a rename. A field neither KEEP nor DROP names (a new one) is left out all the same,
and named in the file's "unclassified" and in state/channel-backup.json, where the system check sees it.

Only the machine that is the bot now writes it: without a standby, the bot's machine (config.bot_machine); with one
(lease.enabled), the machine whose bot runs and holds the lease (its state/lease-true-at fresh): after a takeover the
other machine's channels.json is an old one and must not overwrite the backup (manager 11-20).

`dcctl restore-state [FILE]` puts the backed-up channels back that state/channels.json lacks, only while the bot is
not running (bot.pid, the supervisor's lock, held meanwhile), the old file copied aside first; a channel there already
is never touched. It puts back what the backup holds: strings redacted on the way in stay redacted.
"""
import fcntl
import json
import os
import re
import shutil
import time

from . import lease

VERSION = 1
FILE = os.path.join("backup", "dcbot-channels.json")  # in the instance
STATUS = "channel-backup.json"  # in the bot's state: the last run that wrote or found it unchanged
CHANNELS = "channels.json"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def scrub(value):
    """Every string in `value` through altention/redact.py."""
    from altention import redact
    if isinstance(value, str):
        return redact.redact(value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def plain(value):
    return scrub(value)


def ids(value):
    """history, shown: conversation ids only (uuid-shaped); anything else in the list is left out."""
    return [s for s in value if isinstance(s, str) and UUID.fullmatch(s)] if isinstance(value, list) else None


def pick(*keys):
    def take(value):
        return {k: scrub(value[k]) for k in keys if k in value} if isinstance(value, dict) else None
    return take


def archived(value):
    """An archived channel's record: which conversation, where it worked, its kind, when and from which category;
    never its pane, socket or session (the place it had) nor the free words (why, topic)."""
    if not isinstance(value, dict):
        return None
    out = pick("session_id", "cwd", "kind", "at")(value)
    came = value.get("from")
    if isinstance(came, dict):
        out["from"] = pick("id", "name")(came)
    return out


KEEP = {  # what a restore needs to bring a channel back as it was set up
    "cwd": plain, "cwd_from": plain, "kind": plain, "connector": plain, "configured": plain, "mode": plain,
    "permission": plain, "talk_limit": plain, "talk_free": plain, "agent_answers": plain, "plain": plain,
    "home": plain, "session_id": plain, "boards": plain, "boards_v2": plain, "system": plain,
    "history": ids, "shown": ids,
    "files": pick("root", "host"),  # (its browser: a message id)
    "where": pick("follow"),  # (the rest is made again from the fields at every save; place is the live one)
    "archived": archived,
}

DROP = frozenset((  # never backed up
    "secret_boxes", "activity",  # tokens, a plugin's page arguments
    "pane", "pane_session", "tmux", "took_from",  # the live place; a takeover's pending (not to come back by a restore)
    "named", "rooms", "subs",
    "talk_held", "unsent", "note_next", "doing", "term_done",  # words
    "turns", "turn_ids", "turn_hosts", "calls", "links", "screens", "context", "more", "cmds", "cmd_seq",  # turns
    "busy", "bg", "last_active", "cc_version", "asking", "btw", "workflows", "browsers", "board_fails",  # running
    "blocked", "paused", "parked", "reset_after", "move_after", "move_background", "start_failed", "limit_msg", "rewriting",
    "own_panel", "setup_picks", "user_options", "talk_continued", "cc_pane_said", "cc_stale", "cc_told",
    "bits", "carded", "granted", "host",  # (found by the test's scan in perms.py and ask.py: not a channel's state)
))


def snapshot(channels):
    """(the backup's channels, the keys found that neither KEEP nor DROP names, sorted)."""
    out, unknown = {}, set()
    for cid, st in (channels or {}).items():
        if not isinstance(st, dict):
            continue
        rec = {}
        for key, value in st.items():
            if key in KEEP:
                got = KEEP[key](value)
                if got is not None and got != {} and got != []:
                    rec[key] = got
            elif key not in DROP:
                unknown.add(key)
        if rec:
            out[str(cid)] = rec
    return out, sorted(unknown)


def text_of(channels):
    snap, unknown = snapshot(channels)
    return json.dumps({"v": VERSION, "channels": snap, "unclassified": unknown}, ensure_ascii=False, indent=1,
                      sort_keys=True) + "\n", len(snap), unknown


def write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def pid_alive(state):
    try:
        with open(os.path.join(state, "bot.pid")) as f:
            os.kill(int(f.read().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def holds(state, machines, bot_here, clock=lease.clock):
    """(is this machine the bot now, why not). machines: the list's {name: entry}; bot_here(): the list names this
    machine as the bot's (config.bot_machine()[2])."""
    if not lease.enabled(machines):
        return (True, "") if bot_here() else (False, "not the bot's machine")
    if not pid_alive(state):
        return False, "has a standby and the bot does not run here"
    now = clock()
    at = lease.true_at(os.path.join(state, "lease-true-at"), now)
    if at is None or now - at > lease.FRESH_SECS:
        return False, "has a standby and this machine does not hold the lease"
    return True, ""


def backup(instance, state, now=None):
    """Write the backup; returns the status written to state/STATUS ({at, changed, channels, unclassified, file})."""
    with open(os.path.join(state, CHANNELS), encoding="utf-8") as f:
        channels = json.load(f)
    text, n, unknown = text_of(channels)
    path = os.path.join(instance, FILE)
    try:
        with open(path, encoding="utf-8") as f:
            changed = f.read() != text
    except OSError:
        changed = True
    if changed:
        write_atomic(path, text)
    status = {"at": time.time() if now is None else now, "changed": changed, "channels": n, "unclassified": unknown,
              "file": path}
    write_atomic(os.path.join(state, STATUS), json.dumps(status, ensure_ascii=False, sort_keys=True) + "\n")
    return status


class Refused(Exception):
    pass


def restore(src, state, now=None):
    """Put the channels of backup file `src` back that state/channels.json lacks. Returns (added ids, the copy of the
    old file or None). Refused (nothing written) while the bot runs, or when either file cannot be read."""
    if pid_alive(state):
        raise Refused("the bot is running (bot.pid): stop it first")
    lock = open(os.path.join(state, "supervisor.lock"), "a")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Refused("the bot's supervisor is running (supervisor.lock): stop it first")
        try:
            with open(src, encoding="utf-8") as f:
                got = json.load(f)
        except (OSError, ValueError) as e:
            raise Refused("cannot read %s: %s" % (src, e))
        if not isinstance(got, dict) or got.get("v") != VERSION or not isinstance(got.get("channels"), dict):
            raise Refused("%s is not a channel backup of version %d" % (src, VERSION))
        path = os.path.join(state, CHANNELS)
        try:
            with open(path, encoding="utf-8") as f:
                current = json.load(f)
        except FileNotFoundError:
            current = None
        except (OSError, ValueError) as e:
            raise Refused("cannot read %s (%s): left as it is" % (path, e))
        if current is not None and not isinstance(current, dict):
            raise Refused("%s is not a channels' state: left as it is" % path)
        have = current or {}
        added = sorted(cid for cid, rec in got["channels"].items() if cid not in have and isinstance(rec, dict))
        if not added:
            return [], None
        kept = None
        if current is not None:
            kept = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now)))
            shutil.copy2(path, kept)
        merged = dict(have)
        for cid in added:
            merged[cid] = got["channels"][cid]
        write_atomic(path, json.dumps(merged, ensure_ascii=False))
        return added, kept
    finally:
        lock.close()
