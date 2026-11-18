"""Who posted a message of the bot's, and what kind it is (C-172a P1a, instance docs/refactor/c172a-design.md).

state/ledger.json: {"channels": {channel id: [[message id, entry], ...]}}, oldest first, each entry
{"from", "as", "intent", "key", "framed", "at"}:
- intent: talk | post | notify | board | card | reply | status (what the message is for);
- from: the sender as looked up, a key of bot/senders.py ("channel:<id>", "claude:<machine>:<folder>",
  "program:<machine>:<folder>"; words written before P1d that bot/fromkeys.py could not turn are left), or None for
  the bot itself;
- key: a board's name, a card's id;
- pinned: true for a tool's post that is pinned (dcctl pin, and the ones pinned before: bot/pinimport.py): kept
  however many come after it, up to PINNED_KEEP a channel (C-172a P1c, manager 11-16), so its sender may still edit
  it; taken off by dcctl pin --unpin, left on when it is unpinned by hand on Discord;
- framed: what the bot put round the words, so a reader takes off exactly that and no more: "card" (a post's 📨
  card), "notice_head" (🔔 **[…]**), "board_head" (📌 **name** and its 更新於 line), "status" (a reply's last line of
  tools and seconds), "interim" (">>> ").
Readers ask entry() first and guess from how a message looks only when it is not there (an old message, a file
deleted): frames.of() says so ("guessed").
Kept: PER_CHANNEL a channel, TOTAL in all (the oldest go first). Written SAVE_SECS after a note, made into text and
written off the event loop (a timer's thread: aside, then renamed), all of it each time, notes meanwhile folded
in; in standby.SYNCED, so a standby that takes over reads the same.
Standard library only; tools that run with the system Python may read it.
"""
import json
import os
import threading
import time

from . import config

PER_CHANNEL = 500
PINNED_KEEP = 50  # pinned entries a channel keeps beyond PER_CHANNEL (Discord pins 50 a channel at most)
TOTAL = 20000
INTENTS = ("talk", "post", "notify", "board", "card", "reply", "status")
SAVE_SECS = 1.0  # notes this close together go in one write
_lock = threading.Lock()
_data = None
_timer = None


def path():
    """state/ledger.json (looked up at each call; DCBOT_LEDGER: the tests' own, tests/tmuxguard.py)."""
    return os.environ.get("DCBOT_LEDGER") or os.path.join(config.STATE, "ledger.json")


def read(file=None):
    """The ledger as it is on disk ({"channels": {...}}); an empty one when it is missing or not readable."""
    try:
        with open(file or path(), encoding="utf-8") as f:
            got = json.load(f)
    except (OSError, ValueError):
        return {"channels": {}}
    chans = got.get("channels") if isinstance(got, dict) else None
    return {"channels": {str(c): [r for r in rows if isinstance(r, list) and len(r) == 2 and isinstance(r[1], dict)]
                         for c, rows in chans.items() if isinstance(rows, list)}} if isinstance(chans, dict) else {"channels": {}}


def _loaded():
    global _data
    if _data is None:
        _data = read()
    return _data


def reset():
    """Forget what is in memory (tests; a file taken in by a takeover): the next call reads the file again. A write
    not made yet is dropped."""
    global _data, _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
        _data = None


def note(channel_id, message_id, intent, frm=None, as_=None, key=None, framed=None, at=None):
    """Record one message of the bot's (sent or edited: the entry of an id is replaced). Never raises: a ledger that
    cannot be written only leaves readers to guess. `frm`, `as_`, `key` or `framed` left None keeps the entry's own (an edit that says
    nothing of them); `framed=()` says there is none."""
    if channel_id is None or message_id is None or intent not in INTENTS:
        return
    try:
        entry = {"from": frm, "as": as_, "intent": intent, "key": key, "framed": sorted(set(framed or ())),  # (None: see below)
                 "at": time.time() if at is None else at}
        with _lock:
            data = _loaded()
            was = next((r[1] for r in data["channels"].get(str(channel_id), []) if str(r[0]) == str(message_id)), None)
            for k, given in (("from", frm), ("as", as_)):  # (who it is from, too: manager 11-16)
                if given is None and was is not None:
                    entry[k] = was.get(k)
            if was is not None and was.get("pinned"):
                entry["pinned"] = True  # (an edit keeps the pin: only pin() takes it off)
            if key is None and was is not None:  # (an edit that only takes the buttons off says no key: the card's
                entry["key"] = was.get("key")      # stays; #code-review-2 on 8be878b6, manager 11-16)
            if framed is None and was is not None:  # (and what the bot put round it: 📂 added under a reply keeps its
                entry["framed"] = list(was.get("framed") or [])  # status line; both reviews on 99392598, manager 11-16)
            if was is not None and all(was.get(k) == entry[k] for k in ("from", "as", "intent", "key", "framed")):
                return  # (an edit that changes nothing on record: nothing written; #code-review on ed28927e)
            rows = [r for r in data["channels"].get(str(channel_id), []) if str(r[0]) != str(message_id)]
            rows.append([str(message_id), entry])
            data["channels"][str(channel_id)] = _kept(rows)
            _total(data)
        _soon()
    except Exception:
        pass


def _kept(rows):
    """A channel's rows as kept: the newest PER_CHANNEL that are not pinned, and the newest PINNED_KEEP pinned ones
    whatever their age (in their order)."""
    plain = [r for r in rows if not r[1].get("pinned")]
    pinned = [r for r in rows if r[1].get("pinned")]
    keep = set(id(r) for r in plain[-PER_CHANNEL:] + pinned[-PINNED_KEEP:])
    return [r for r in rows if id(r) in keep]


def _total(data):
    """No more than TOTAL entries in all, pinned ones aside: the oldest of all go first."""
    total = sum(1 for rows in data["channels"].values() for r in rows if not r[1].get("pinned"))
    while total > TOTAL:
        heads = {c: next((r for r in rows if not r[1].get("pinned")), None) for c, rows in data["channels"].items()}
        oldest = min((c for c in heads if heads[c] is not None), key=lambda c: heads[c][1].get("at", 0))
        data["channels"][oldest].remove(heads[oldest])
        total -= 1


def pin(channel_id, message_id, on):
    """dcctl pin of a message on record: kept however many come after it (`on`), or back among the rest. False when
    the ledger does not have it (nothing to keep)."""
    if channel_id is None or message_id is None:
        return False
    with _lock:
        data = _loaded()
        rows = data["channels"].get(str(channel_id), [])
        e = next((r[1] for r in rows if str(r[0]) == str(message_id)), None)
        if e is None:
            return False
        if bool(e.get("pinned")) == bool(on):
            return True
        if on:
            e["pinned"] = True
        else:
            e.pop("pinned", None)
        data["channels"][str(channel_id)] = _kept(rows)
    _soon()
    return True


def _soon():
    """The whole ledger written SAVE_SECS from now, in a thread of its own (made into text there, not in the event
    loop: it may be megabytes); notes meanwhile go in the same write."""
    global _timer
    with _lock:
        if _timer is not None:
            return
        _timer = threading.Timer(SAVE_SECS, save)
        _timer.start()


def rewrite(change):
    """`change(data)` on the ledger in memory, under the lock, then written at once (bot/fromkeys.py: a mark is
    left only once it is on disk). (what `change` returned, whether it was written)."""
    with _lock:
        got = change(_loaded())
    return got, save()


def save():
    """Write what is in memory now (aside, then renamed). The tests call it to wait no longer. False when it could
    not be written."""
    global _timer
    try:
        with _lock:
            _timer = None
            if _data is None:
                return True
            text = json.dumps(_data, ensure_ascii=False)
        tmp = "%s.tmp%d" % (path(), os.getpid())  # (this thread is off the event loop already: written here, whole)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path())
        return True
    except Exception:
        return False


def entry(message_id, channel_id, data=None):
    """The entry of a message of the bot's in that channel, or None (not on record, or the channel not known)."""
    if message_id is None or channel_id is None:
        return None
    with _lock:
        for mid, e in (data or _loaded())["channels"].get(str(channel_id), []):
            if mid == str(message_id):
                return e
    return None


def forget(channel_id, message_id):
    """A message of the bot's deleted: its entry goes (it would hold a place of PER_CHANNEL for nothing)."""
    if channel_id is None or message_id is None:
        return
    with _lock:
        data = _loaded()
        rows = data["channels"].get(str(channel_id))
        if not rows or not any(str(r[0]) == str(message_id) for r in rows):
            return
        data["channels"][str(channel_id)] = [r for r in rows if str(r[0]) != str(message_id)]
    _soon()


def forget_channel(channel_id):
    """A channel deleted: its entries go too."""
    with _lock:
        data = _loaded()
        if data["channels"].pop(str(channel_id), None) is None:
            return
    _soon()
