"""A conversation's earlier history, posted when a channel takes it up (!resume, /resume typed in the session,
altention dc post --resume / --history).

The whole of it goes into the channel, like the backfill of an attached session (the owner wants it there, long
ones included: about one message a second, silently, in the background). A conversation the channel has shown
before (st["shown"], which an attach's backfill counts too) or held before (st["history"]: it went on in this
channel) gets just its last few turns again (owner's report 2026-10-20: a channel attached, detached and set up anew had its whole
history posted a second time).
"""
import asyncio
import json
import os
import traceback

from . import sessions
from .util import split_message

KEEP = 50     # conversations remembered as shown, per channel


def mark(st, session_id):
    """The channel holds the whole history of `session_id` now (posted here, or by an attach's backfill)."""
    if session_id:
        st["shown"] = ([s for s in st.get("shown") or [] if s != session_id] + [session_id])[-KEEP:]


def seen(st, session_id, held=None):
    """Has the channel this conversation's history already: shown whole, or held here before (`held`: the channel's
    conversations as they were before this switch; st["history"] when not given)."""
    held = st.get("history") or [] if held is None else held
    return session_id in (st.get("shown") or []) or session_id in held


AGAIN = "📜 **接回對話 `%s`**（這個頻道之前貼過它的紀錄，或就是在這裡進行的），目前的最後幾輪：\n\n%s"


async def show(bot, ch, st, session_id, path, force=False, held=None):
    """Post the history of `session_id` (transcript `path`) into `ch`. `force`: even if shown before. `held`: see
    seen()."""
    if not session_id or not path or not os.path.exists(path):
        return False
    messages = sessions.history_messages(path)
    if not messages:
        return False
    if seen(st, session_id, held) and not force:
        recap = sessions.recap_text(sessions.recap(path))
        for part in split_message(AGAIN % (session_id[:8], recap)):
            await ch.send(part, silent=True)
        return True
    mark(st, session_id)
    bot.save()
    asyncio.ensure_future(bot.backfill(ch, messages))  # all of it in the channel, as an attach does
    return True


REMOTE_SECS = 20  # how long another machine is given to read a conversation and answer
SEEN_AGAIN = 3    # turns shown of a conversation the channel has shown whole before (as sessions.recap() shows here)


async def show_remote(bot, ch, st, host, session_id, path, force=False, held=None):
    """show(), for a conversation whose transcript is on another machine (a pane attached there took it up with
    /resume: the event says the file; #issues 31, until stage 3b step 2.4c nothing was posted). Read over there by
    `dcremote history`, worded here exactly as show() and an attach word it: the whole of it the first time, its
    last few turns when the channel has shown it before.

    That machine does not answer, runs a dcremote of before this command, or answers what cannot be read: one
    line in the channel says so, and nothing is tried again. Runs as a task of its own (the caller starts it): the
    events of that machine are handled one after the other, and none waits behind this."""
    from . import remote
    if not session_id or not path:
        return False
    try:
        return await _show_remote(bot, ch, st, host, session_id, path, force, remote, held)
    except Exception:  # a task of its own: nobody else would hear of it
        traceback.print_exc()
        return False


UNKNOWN_COMMAND = "不認得"  # how a dcremote of before `history` refuses it (its exit code 1, and this word in what it says)


def still_on(bot, ch, st, session_id):
    """Is the channel still the one this history was asked for? Its state is the same object the bot holds for it
    (not detached and attached again, not deleted), it is not put away, and its conversation is still this one."""
    away = bool(st.get("archived")) and not (st.get("pane") or st.get("tmux") or st.get("home"))  # (tmux_bridge.put_away())
    return bot.channels_state.get(str(ch.id)) is st and not away and st.get("session_id") == session_id


def failed(code, said):
    """Why another machine's dcremote gave nothing, in the owner's words, or None when it answered (exit code 0):
    it does not answer; it is of before the command asked for (exit code 1 and "不認得" in what it wrote); it did
    not run at all (any other exit code 1); it refused or could not read (with the code and the end of what it
    wrote)."""
    detail = "（結束碼 %d%s）" % (code, "：" + said[-1] if said else "")
    if code == 255:
        return "現在連不上"
    if code == 1 and any(UNKNOWN_COMMAND in line for line in said):
        return "那台的程式還沒更新到能讀它"
    if code == 1:  # not "no such command": the tool itself did not run (a traceback: something missing over there)
        return "那台的 dcremote 跑不起來" + detail
    return "那台不給讀或讀不出來" + detail if code != 0 else None


async def _show_remote(bot, ch, st, host, session_id, path, force, remote, held=None):
    said = []
    code, out = await remote.dcremote(host, "history", path, session_id, timeout=REMOTE_SECS, err=said)
    why = failed(code, said)
    if why is None:
        try:
            got = json.loads(out)
        except ValueError:
            got, why = None, "那台回來的內容讀不出來"
        if why is None and got is not None and not (isinstance(got, dict) and isinstance(got.get("turns"), list)
                                                   and all(isinstance(t, list) and len(t) == 4 for t in got["turns"])):
            why = "那台回來的內容讀不出來"
    if not still_on(bot, ch, st, session_id):
        # Up to REMOTE_SECS went by: the channel was detached, put away, or its pane took up another conversation.
        # The history of one it is no longer on is not posted into it, nor a word about not getting it.
        print("history: the history of %s (on %s) came after its channel %s had moved on; not posted" % (session_id[:8], host, ch.id), flush=True)
        return False
    if why:
        await ch.send("📜 接回的對話 `%s` 的紀錄在 %s 上，%s，所以沒有貼出來。" % (session_id[:8], host, why), silent=True)
        return False
    messages = sessions.backfilled(got) if got else []
    if not messages:
        return False  # the file is gone, or nothing was said in it yet: as show()
    if seen(st, session_id, held) and not force:
        pairs = [(sessions.worded(kind, text), reply) for kind, text, reply, _stamp in got["turns"][-SEEN_AGAIN:]]
        for part in split_message(AGAIN % (session_id[:8], sessions.recap_text(pairs))):
            await ch.send(part, silent=True)
        return True
    mark(st, session_id)
    bot.save()
    asyncio.ensure_future(bot.backfill(ch, messages))
    return True


ABOUT = ("title", "first", "last")


def a_session(got):
    """Is this what `dcremote sessions` says of one conversation? (Read from another machine: held to its shape
    before anything of it is shown or kept.)"""
    return isinstance(got, dict) and all(isinstance(got.get(k), str) for k in ABOUT + ("id", "path")) and bool(got["id"]) \
        and isinstance(got.get("mtime"), (int, float)) and not isinstance(got.get("mtime"), bool) \
        and isinstance(got.get("prompts"), int) and not isinstance(got.get("prompts"), bool)


async def far_sessions(host, cwd, ref=""):
    """The conversations another machine keeps for a folder (`dcremote sessions`): (the list, newest first, None);
    with `ref` (the one whose id begins so, or None, None). (None, why) when that machine gave nothing (failed())
    or answered what cannot be read."""
    from . import remote
    said = []
    code, out = await remote.dcremote(host, "sessions", cwd, *([ref] if ref else []), timeout=REMOTE_SECS, err=said)
    why = failed(code, said)
    if why:
        return None, why
    try:
        got = json.loads(out)
    except ValueError:
        return None, "那台回來的內容讀不出來"
    if ref:
        return (got, None) if got is None or a_session(got) else (None, "那台回來的內容讀不出來")
    return (got, None) if isinstance(got, list) and all(a_session(s) for s in got) else (None, "那台回來的內容讀不出來")
