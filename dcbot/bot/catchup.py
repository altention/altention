"""Messages the owner sent while the bot was down (a deploy or restart takes 10-20 seconds; a crash longer).

Discord does not replay what a bot missed, so the bot remembers, per channel, the newest owner message it has
handled (state/seen.json). After connecting it reads each such channel's history after that message and
hands what it missed to on_message, oldest first, as if it had just arrived. Only recent ones (WINDOW): after
a long outage an old instruction acted on out of the blue would do more harm than good; those are listed
instead. A message that arrives live while the catch-up runs is not handled twice (mark()).

Only plain messages and the commands that change nothing (REPLAYED) are handed on. Any other command, the
bot's own ("!…") or Claude Code's ("/…", "!" for its shell), is not done again, told in the channel (a line each; many, in one message: told()): a
!restart sent before the restart restarted it once more, !key or !stop would land on a screen or a turn that is not
the one it was sent for, !cwd or /clear change what the owner may have seen otherwise by now (C-124, manager
2026-11-03: a list of what is replayed, so a command added later is not, until it is put on it). In a terminal
channel every message is run by its shell, so none is, but for REPLAYED.
"""
import asyncio
import collections
import datetime
import json
import os
import re
import traceback

import discord

from . import config

SEEN = os.path.join(config.STATE, "seen.json")
WINDOW = 30 * 60   # seconds: older missed messages are only pointed out
LIMIT = 30         # per channel
REPLAYED = ("!help", "!status", "!recap", "!screen", "/btw")  # the commands done from a catch-up: they only show
NOT_DONE = "-# ⏸️ 離線時收到的 %s 沒有執行，要的話再送一次。"
NOT_DONE_MANY = "-# ⏸️ 離線時收到的 %d 則沒有執行，要的話再送一次：%s"
ONE_BY_ONE = 3   # up to this many are said one line each; more, in one message (manager: no flood)
LISTED = 5       # what that one message lists of them


def told(skipped):
    """The channel's lines for the messages not done (their words as typed): one each, or one for them all."""
    shown = ["`%s`" % h.replace("`", "'") for h in skipped]
    if len(shown) <= ONE_BY_ONE:
        return [NOT_DONE % s for s in shown]
    return [NOT_DONE_MANY % (len(shown), "、".join(shown[:LISTED]) + ("…" if len(shown) > LISTED else ""))]


def held(message, terminal=False):
    """The command `message` is, as typed (its first words, for the channel to be told), when it is one a catch-up
    does not do; None for a plain message or one of REPLAYED. A command is a message whose first word starts with
    "!" (not "!!": that is a plain message read at once), or is "/" and a name (not a path); a message that only
    mentions one further on is a plain message. In a terminal channel (`terminal`) every message is a command:
    the shell runs it (manager 2026-11-03)."""
    text = (getattr(message, "content", "") or "").strip()
    first = text.split()[0].lower() if text else ""
    if terminal:  # a terminal channel runs every message in its shell (`rm -rf build`, `git push`): none again
        return None if not text or first in REPLAYED else " ".join(text.split()[:3])[:60]
    if text.startswith("!!"):  # "!!…" is a message Claude reads at once (main.py), not a command
        return None
    command = first.startswith("!") or re.match(r"^/[a-z][\w:.-]*$", first)  # "/tmp/x 是什麼" is a question
    if not command or first in REPLAYED:
        return None
    return " ".join(text.split()[:3])[:60]


class Catchup(object):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open(SEEN) as f:
                self.seen = {k: int(v) for k, v in json.load(f).items()}
        except (OSError, ValueError, AttributeError):
            self.seen = {}
        self.at_start = dict(self.seen)  # where each channel stood when the bot went down (live messages move seen on)
        self.handled = collections.deque(maxlen=1000)
        self.done = False

    def save(self):
        tmp = SEEN + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self.seen, f)
            os.replace(tmp, SEEN)
        except OSError:
            pass

    def mark(self, message):
        """Note that `message` is being handled. False if it already was (live and caught up both)."""
        if message.id in self.handled:
            return False
        self.handled.append(message.id)
        cid = str(message.channel.id)
        if message.id > self.seen.get(cid, 0):
            self.seen[cid] = message.id
            self.save()
        return True

    async def run(self):
        """Once per process, after connecting: deliver what was missed."""
        if self.done:
            return
        self.done = True
        now = datetime.datetime.now(datetime.timezone.utc)
        total, held_total = 0, []
        for cid, last in list(self.at_start.items()):
            ch = self.bot.get_channel(int(cid))
            if ch is None or isinstance(ch, discord.Thread) or not hasattr(ch, "history"):
                continue
            try:
                missed = [m async for m in ch.history(after=discord.Object(id=last), limit=LIMIT, oldest_first=True)
                          if m.author.id == config.OWNER_ID and m.id not in self.handled]
            except Exception:
                traceback.print_exc()
                continue
            if not missed:
                continue
            total += len(missed)
            fresh = [m for m in missed if (now - m.created_at).total_seconds() <= WINDOW]
            stale = [m for m in missed if m not in fresh]
            terminal = ((getattr(self.bot, "channels_state", None) or {}).get(cid) or {}).get("kind") == "terminal"
            kept = [(m, held(m, terminal)) for m in fresh]
            fresh = [m for m, h in kept if h is None]
            skipped = [(m, h) for m, h in kept if h is not None]
            try:
                if stale:
                    await ch.send("-# ⚠️ 機器人離線期間你在這裡發的 %d 則訊息超過 %d 分鐘了，沒有送給 Claude，需要的話請重發：%s"
                                  % (len(stale), WINDOW // 60, " ".join(m.jump_url for m in stale[:5])), silent=True)
                    for m in stale:
                        self.mark(m)
                if fresh:
                    await ch.send("-# 🔁 補送機器人重啟期間你發的 %d 則訊息" % len(fresh), silent=True)
                for m, _ in skipped:
                    self.mark(m)
                for line in told([h for _, h in skipped]):
                    await ch.send(line, silent=True)
                held_total.extend(h for _, h in skipped)
            except Exception:
                traceback.print_exc()
            for m in fresh:
                try:
                    await self.bot.on_message(m)
                except Exception:
                    traceback.print_exc()
                await asyncio.sleep(0.5)
        if total:
            await self.bot.audit("重啟後補收了 %d 則離線期間的訊息%s" % (total, "，其中 %d 則指令沒有執行（%s）" % (
                len(held_total), "、".join(held_total[:5])) if held_total else ""))
