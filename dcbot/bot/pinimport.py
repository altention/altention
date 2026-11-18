"""The messages dcctl sent and someone pinned before the ledger kept pins, taken into it once (C-172a P1c, manager
11-16). dcctl edit goes by the ledger since P1c, and a pinned to-do list a channel's Claude keeps up to date was sent
before the ledger wrote dcctl's messages down: without this, it could no longer be edited.

Which of them are pinned is asked of Discord, channel by channel, slowly (a task of its own beside the loop, never
waited for), since dcctl-sent.json says no channel. Taken in: a message dcctl remembers (dcctl-sent.json, and the old
dcctl-pinned.json) that is pinned now and is the bot's own; as a post of that channel's Claude (its key, bot/senders.py),
pinned. Not the others. When done: a mark next to the ledger, dcctl-pinned.json renamed aside (not deleted), one
#audit line. Stopped half way (a restart, or a channel's pins not read for any reason but no permission or the
channel gone), it starts again from the beginning at the next start: noting one twice changes nothing."""
import asyncio
import os
import time
import traceback

import discord

from . import ledger, senders, sentids

GAP = 1.0  # seconds between two channels' pins asked of Discord


def mark_path():
    return os.path.join(os.path.dirname(ledger.path()), "ledger-pins-imported")


def done():
    return os.path.exists(mark_path())


def remembered():
    """The IDs dcctl remembers: the newest it sent, and the old list of those it pinned."""
    return set(sentids.sent_ids()) | set(sentids.pinned_ids())


def claude_of(channel_id):
    """A channel's Claude as the ledger keeps it (bot/senders.py's key, since C-172a P1d)."""
    return senders.key(channel=channel_id)


async def run(channels, me, audit, sleep=None):
    """Ask each channel's pins (`channels`: text channels with pins(); `me`: the bot's user id) and take in what is
    pinned and remembered. Returns {channel id: how many}."""
    want = remembered()
    got = {}
    if want:
        for ch in channels:
            try:
                pins = await ch.pins()
            except (discord.Forbidden, discord.NotFound):  # (no permission to read it, or gone: nothing pinned there
                pins = []                                   # that dcctl could edit. Anything else (Discord or the network
                # failing) goes up: no mark, the old list not renamed, all of it again at the next start; #code-review-2)
            for m in pins:
                if str(m.id) not in want or getattr(m.author, "id", None) != me:
                    continue
                if ledger.entry(m.id, ch.id) is None:
                    ledger.note(ch.id, m.id, "post", frm=claude_of(ch.id), framed=(),
                                at=m.created_at.timestamp() if getattr(m, "created_at", None) else None)
                if ledger.pin(ch.id, m.id, True):
                    got[ch.id] = got.get(ch.id, 0) + 1
            await (sleep or asyncio.sleep)(GAP)
    ledger.save()
    old = sentids.pinned_path()
    if os.path.exists(old):
        os.replace(old, old + ".merged")  # (its IDs are in the ledger now; kept for a look)
    os.makedirs(os.path.dirname(mark_path()), exist_ok=True)
    with open(mark_path(), "w") as f:
        f.write("%d\n" % time.time())
    n = sum(got.values())
    await audit("[ledger] 先前釘選的 dcctl 訊息匯入完成：%d 則%s（之後 dcctl edit 照 ledger 判斷）"
                % (n, "（%s）" % "、".join("<#%s> %d 則" % (c, k) for c, k in sorted(got.items())) if got else ""))
    return got


async def once(bot, guild_id):
    """run() over the server's text channels, unless done before; any failure: the log, and again next start."""
    if done():
        return
    try:
        guild = bot.get_guild(guild_id)
        if guild is None:
            return
        await run(list(guild.text_channels), bot.user.id, bot.audit)
    except Exception:
        traceback.print_exc()
