"""Messages the owner sent while the bot was down (a deploy or restart takes 10-20 seconds; a crash longer).

Discord does not replay what a bot missed, so the bot remembers, per channel, the newest owner message it has
handled (state/seen.json). After connecting it reads each such channel's history after that message and
hands what it missed to on_message, oldest first, as if it had just arrived. Only recent ones (WINDOW): after
a long outage an old instruction acted on out of the blue would do more harm than good; those are listed
instead. A message that arrives live while the catch-up runs is not handled twice (mark()).
"""
import asyncio
import collections
import datetime
import json
import os
import traceback

import discord

from . import config

SEEN = os.path.join(config.STATE, "seen.json")
WINDOW = 30 * 60   # seconds: older missed messages are only pointed out
LIMIT = 30         # per channel


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
        total = 0
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
            try:
                if stale:
                    await ch.send("-# ⚠️ 機器人離線期間你在這裡發的 %d 則訊息超過 %d 分鐘了，沒有送給 Claude，需要的話請重發：%s"
                                  % (len(stale), WINDOW // 60, " ".join(m.jump_url for m in stale[:5])), silent=True)
                    for m in stale:
                        self.mark(m)
                if fresh:
                    await ch.send("-# 🔁 補送機器人重啟期間你發的 %d 則訊息" % len(fresh), silent=True)
            except Exception:
                traceback.print_exc()
            for m in fresh:
                try:
                    await self.bot.on_message(m)
                except Exception:
                    traceback.print_exc()
                await asyncio.sleep(0.5)
        if total:
            await self.bot.audit("重啟後補收了 %d 則離線期間的訊息" % total)
