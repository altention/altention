"""A channel whose conversation is held up for now (C-101): one field of its state, `blocked`, written and taken away
only here, so whoever shows it (the status board, the channels' read-only command) reads one shape:

    {"kind": "usage_limit" | "login" | "api_error" | "dialog" | …, "since": <unix seconds>, "until": <unix seconds>,
     "machine": "<name>", "detail": "<one line, masked>", "msg": <the message the bot posted about it>}

`until` only when it is known to clear by itself then (a usage limit's reset); `machine` when the conversation is
on another machine (none: the bot's); `detail` and `msg` when there are. The kinds are open: a reader shows one it
does not know as written. One at a time: a later one takes the place of the one before.

Taken away when the conversation moves again (a turn's input, a tool, a turn's end: TmuxBridge.handle), and by what
ends that kind (Claude Code continuing at a reset, a dialog's buttons dropped)."""
import time

from . import masking

DETAIL_MAX = 200


def put(st, kind, machine=None, until=None, detail=None, msg=None, now=None, why=None):
    """The channel's state `st` says it is held up by `kind` (instead of whatever held it before). `why`: the API's
    error code behind an api_error (rate_limit: a usage limit Claude Code does not wait out, which holds every
    channel up as one it waits out does: bot/calls.py counts both, C-103)."""
    b = {"kind": kind, "since": time.time() if now is None else now}
    if why:
        b["why"] = str(why)
    if machine:
        b["machine"] = machine
    if isinstance(until, (int, float)):
        b["until"] = int(until)
    if detail:
        b["detail"] = masking.mask(" ".join(str(detail).split()))[0][:DETAIL_MAX]
    if msg is not None:
        b["msg"] = msg
    st["blocked"] = b
    return b


def usage(st):
    """Is the channel held up by a usage limit, one Claude Code waits out (usage_limit) or one it does not (an
    api_error of rate_limit)?"""
    b = of(st)
    return bool(b) and (b["kind"] == "usage_limit" or (b["kind"] == "api_error" and b.get("why") == "rate_limit"))


def of(st, kind=None):
    """What holds the channel up (of that `kind`, when given), or None."""
    b = st.get("blocked")
    if not isinstance(b, dict) or not b.get("kind"):
        return None
    return b if kind is None or b["kind"] == kind else None


def clear(st, *kinds):
    """Take it away (only when it is one of `kinds`, when given). What was taken away, or None."""
    b = of(st)
    if b is None or (kinds and b["kind"] not in kinds):
        return None
    return st.pop("blocked")
