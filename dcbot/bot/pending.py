"""Messages dcctl send put on Discord itself, waiting to go into the bot's ledger (C-172a P1b batch 4). dcctl send
stays straight to Discord (the supervisor tells the owner through it while the bot is down), so the ledger, the bot's,
is written later: dcctl leaves one small file per message in state/ledger-pending/, the bot takes them in on each
round of its loop. dcctl pin leaves one the same way (C-172a P1c: a pinned post is kept in the ledger, ledger.pin()).
Standard library only: tools/dcctl writes here with the system Python."""
import json
import os
import time

from . import ledger


def folder():
    """Next to the ledger (state/; the tests' own with DCBOT_LEDGER, tests/tmuxguard.py)."""
    return os.path.join(os.path.dirname(ledger.path()), "ledger-pending")


def bad_folder():
    """Files the bot could not read: kept for a look, not read again each round."""
    return os.path.join(os.path.dirname(ledger.path()), "ledger-pending-bad")


def write(channel_id, message_id, frm, intent="post", framed=()):
    """Leave one message for the ledger; the file appears whole (written aside, then renamed). `framed`: what dcctl
    put round it ("source": the line saying where it comes from, C-172a P1d)."""
    _leave(dict({"channel": str(channel_id), "message": str(message_id), "from": frm, "intent": intent, "at": time.time()},
                **({"framed": list(framed)} if framed else {})))


def write_pin(channel_id, message_id, on):
    """Leave dcctl pin of a message (`on`) or --unpin, for ledger.pin()."""
    _leave({"channel": str(channel_id), "message": str(message_id), "pin": bool(on)})


def _leave(got):
    where = folder()
    os.makedirs(where, exist_ok=True)
    name = "%020d-%d.json" % (time.time_ns(), os.getpid())
    tmp = os.path.join(where, "." + name)
    with open(tmp, "w") as f:
        json.dump(got, f, ensure_ascii=False)
    os.rename(tmp, os.path.join(where, name))


def _read(path):
    """(channel, message, entry fields) of one file, or None when it is not one write() makes."""
    try:
        with open(path) as f:
            got = json.load(f)
    except ValueError:
        return None
    if not isinstance(got, dict) or not str(got.get("channel") or "").isdigit() \
            or not str(got.get("message") or "").isdigit():
        return None
    if isinstance(got.get("pin"), bool):
        return got["channel"], got["message"], {"pin": got["pin"]}
    if not isinstance(got.get("intent"), str):
        return None
    at = got.get("at")
    framed = got.get("framed") if isinstance(got.get("framed"), list) else []
    return got["channel"], got["message"], {"intent": got["intent"], "frm": got.get("from") if isinstance(got.get("from"), str) else None,
                                            "at": at if isinstance(at, (int, float)) else None,
                                            "framed": [str(x) for x in framed if x == "source"]}


def drain(note, pin=None):
    """Each waiting file into the ledger (`note`: ledger.note; `pin`: ledger.pin), oldest first, then removed; one
    that cannot be read is moved to bad_folder(). Returns how many were taken in."""
    where = folder()
    try:
        names = sorted(n for n in os.listdir(where) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        return 0
    n = 0
    for name in names:
        path = os.path.join(where, name)
        try:
            got = _read(path)
        except OSError:
            continue  # (gone meanwhile, or not readable now: the next round)
        if got is None:
            try:
                os.makedirs(bad_folder(), exist_ok=True)
                os.replace(path, os.path.join(bad_folder(), name))
            except OSError:
                pass
            continue
        channel, message, fields = got
        if "pin" in fields:
            (pin or ledger.pin)(int(channel), int(message), fields["pin"])
        else:
            note(int(channel), int(message), fields["intent"], frm=fields["frm"], at=fields["at"], framed=fields["framed"])
        try:
            os.remove(path)
        except OSError:
            pass
        n += 1
    return n
