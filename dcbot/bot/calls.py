"""When the bot @-mentions the owner, and how (C-103, owner 2026-11-03): only when something needs them, never because a
turn took long. One place decides it.

Each mention is a line of its own: what is needed first, a short gist, the mention last (a phone shows a notification's
first words; the words before the gist are never cut, only the gist, to GIST characters). In a channel the line is a
reply to the message it is about (the buttons, the plan, the menu), so a tap leads there; the reply itself mentions
nobody else.

Once for each thing (`key`: a question's ask_id, a menu's token, a hold, a failure's start): the keys mentioned are kept
in the channel's state ("calls": {key: [message id, when, done, the line]}), so a restart does not mention them again. Whoever
closes the thing calls done(): the line is edited to end with ✅ (an edit rings nobody), which tells at a look back what
still waits. A thing already over when it would be mentioned (a question answered before its event came round, after
a restart) is not.

What stops the whole installation (a usage limit, a lost login, the bot that cannot stay up, a machine that cannot be
reached) is said once, in the default notification channel (`altention dc notify`'s), not in every channel it stops:
system() with a `kind` and the episode it belongs to; the channels it stops are listed below and added as they come.
The episode is kept in state/calls-system.json; over() says it is over there, mentioning nobody, and a later episode of
the same kind mentions again. The channels show their own state as before, without a mention.

What Claude itself says needs the owner (the first line of a final reply starts with messages.DISCUSS_MARK, which the
system note asks for) is mentioned too: discussed(). The owner's next message in that channel closes it."""
import json
import os
import time
import traceback

import discord

from . import config, deliver, messages

GIST = 60          # characters of the gist; the purpose before it is never cut
KEEP_DAYS = 7      # a channel's mentions are forgotten after this long (only that they were made: the lines stay)
SYSTEM_FILE = "calls-system.json"  # in config.STATE: {kind: {"episode", "msg", "channel", "channels": [ids], "at",
#                                    "head", "more": the words as first said, kept when channels are added}}


def cut(text, n=GIST):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def mention():
    return "<@%d>" % config.OWNER_ID if config.OWNER_ID else ""


def line(code, gist=None, **data):
    """The words of a mention: messages.call(code) with the gist cut, and the owner's mention at the end."""
    if gist is not None:
        data["gist"] = cut(gist)
    return ("%s %s" % (messages.call(code, **data), mention())).rstrip()


def allowed():
    """Pings the owner and nobody else (a reply's author neither)."""
    return discord.AllowedMentions(everyone=False, roles=False, replied_user=False,
                                   users=[discord.Object(config.OWNER_ID)] if config.OWNER_ID else False)


def discussed(final):
    """The gist of a final reply that says it needs the owner, or None: its first line starts with the marker, or the
    first line of its last paragraph does (Claude put it at the end: #rulesets 11-08 went without a mention; manager).
    The marker anywhere else (quoted in the text) says nothing."""
    text = (final or "").strip()
    mark = messages.DISCUSS_MARK
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    for line in (text.split("\n", 1)[0].strip(), paragraphs[-1].strip().split("\n", 1)[0].strip() if paragraphs else ""):
        for m in (mark, mark.replace("：", ":")):
            if line.startswith(m):
                gist = line[len(m):].strip()
                if gist:
                    return gist
    return None


def _calls(st):
    calls = st.get("calls")
    if not isinstance(calls, dict):
        calls = st["calls"] = {}
    now = time.time()
    for key in [k for k, v in calls.items() if not isinstance(v, list) or len(v) < 3 or now - (v[1] or 0) > KEEP_DAYS * 86400]:
        calls.pop(key, None)
    return calls


def mentioned(st, key):
    """Was `key` mentioned in this channel already (whether or not it is over)?"""
    return key in _calls(st)


async def call(ch, st, key, code, gist=None, about=None, save=None, **data):
    """Mention the owner once for `key` in channel `ch` (state `st`): the line of `code` (messages.CALLS), as a reply
    to message id `about` when given. Returns the message sent, or None (mentioned before, or it could not be sent:
    what it is about was posted all the same). `save`: the bot's save(), after the key is written."""
    calls = _calls(st)
    if key in calls:
        return None
    text = line(code, gist, **data)
    calls[key] = [None, time.time(), False, text]  # (written first: a second event for it while this is sent mentions nothing)
    msg = None
    try:
        kw = {"allowed_mentions": allowed()}
        if about:
            kw.update(reference=discord.MessageReference(message_id=int(about), channel_id=ch.id, fail_if_not_exists=False),
                      mention_author=False)
        msg = await deliver.deliver(ch, text, **kw, intent="status", key=key)
        calls[key][0] = msg.id
    except Exception:
        traceback.print_exc()
    if save is not None:
        save()
    return msg


async def done(ch, st, key, save=None):
    """What `key` asked for is over (answered, cancelled, cleared): its line ends with ✅. Nothing when it was never
    mentioned, or is marked already. True when it was marked now."""
    calls = _calls(st)
    rec = calls.get(key)
    if not rec or rec[2]:
        return False
    rec[2] = True
    if save is not None:
        save()
    if rec[0] is None or len(rec) < 4:
        return True
    try:
        await deliver.deliver(ch, content=rec[3] + messages.call("done"), allowed_mentions=discord.AllowedMentions.none(),
                              intent="status", key=key, edit=rec[0])
    except Exception:
        traceback.print_exc()
    return True


async def done_all(ch, st, prefix, save=None):
    """done() for every open key of this channel that starts with `prefix`."""
    marked = False
    for key in [k for k, v in _calls(st).items() if k.startswith(prefix) and not v[2]]:
        marked = await done(ch, st, key, save) or marked
    return marked


# ------------------------------------------------------------------ the whole installation

def _system_path():
    return os.path.join(config.STATE, SYSTEM_FILE)


def system_state():
    try:
        with open(_system_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _system_save(data):
    os.makedirs(config.STATE, exist_ok=True)
    tmp = _system_path() + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _system_path())


def channels_text(ids):
    return messages.call("channels", channels="、".join("<#%s>" % i for i in ids))


async def system(where, kind, episode, head, more=(), channel=None):
    """Something that stops the whole installation (or a whole machine): said once for its `episode` (anything that
    tells this time from the last one: a reset time, a machine's name and when it went), in `where` (the notification
    channel; a coroutine that gives it, as Posts.channel("@notify")). `head` is the line of the mention (line()),
    `more` the lines after it; `channel`: a channel it stops, listed below and added to the list when the same episode
    stops one more (that message edited, mentioning nobody). True when the mention was sent now."""
    data = system_state()
    rec = data.get(kind)
    if isinstance(rec, dict) and rec.get("episode") == episode:
        if channel is not None and str(channel) not in rec.get("channels", []):
            rec.setdefault("channels", []).append(str(channel))
            _system_save(data)
            # its words as first said (the first channel's reset time, #code-review-2 on 3c7ad8f): only the list grows
            await _relist(where, rec, rec.get("head") or head, rec.get("more", more))
        return False
    try:
        ch = await where()
    except Exception:
        traceback.print_exc()
        ch = None
    if ch is None:  # nowhere to say it (no notification channel to be had): nothing kept, the next try says it
        return False
    data = system_state()
    if isinstance(data.get(kind), dict) and data[kind].get("episode") == episode:
        return False  # (said meanwhile, while the channel was looked for)
    rec = {"episode": episode, "msg": None, "channel": None, "channels": [str(channel)] if channel is not None else [],
           "at": time.time(), "head": head, "more": list(more)}
    data[kind] = rec
    _system_save(data)  # (first: a second channel stopped while this is sent is listed, not mentioned again)
    try:
        msg = await deliver.deliver(ch, _system_text(rec, head, more), allowed_mentions=allowed(), intent="status")
        rec["msg"], rec["channel"] = msg.id, ch.id
        data = system_state()
        if isinstance(data.get(kind), dict) and data[kind].get("episode") == episode:
            data[kind].update(msg=msg.id, channel=ch.id)
            _system_save(data)
        return True
    except Exception:
        traceback.print_exc()
        return False


def _system_text(rec, head, more):
    rows = [head] + list(more)
    if rec.get("channels"):
        rows.append(channels_text(rec["channels"]))
    return "\n".join(rows)


async def _relist(where, rec, head, more):
    if not rec.get("msg"):
        return
    try:
        ch = await where()
        if ch is not None:
            await deliver.deliver(ch, content=_system_text(rec, head, more), allowed_mentions=discord.AllowedMentions.none(),
                                  intent="status", edit=rec["msg"])
    except Exception:
        traceback.print_exc()


def open_episode(kind):
    """The episode of `kind` said and not over yet, or None."""
    rec = system_state().get(kind)
    return rec.get("episode") if isinstance(rec, dict) else None


async def over(where, kind, text):
    """The episode of `kind` is over: `text` (no mention) in the same channel, and the next one mentions again.
    False when none was open."""
    data = system_state()
    rec = data.pop(kind, None)
    if not isinstance(rec, dict):
        return False
    _system_save(data)
    try:
        ch = await where()
        if ch is not None:
            kw = {"allowed_mentions": discord.AllowedMentions.none()}
            if rec.get("msg") and rec.get("channel") == ch.id:
                kw.update(reference=discord.MessageReference(message_id=int(rec["msg"]), channel_id=ch.id,
                                                             fail_if_not_exists=False), mention_author=False)
            await deliver.deliver(ch, text, silent=True, **kw, intent="status")
    except Exception:
        traceback.print_exc()
    return True
