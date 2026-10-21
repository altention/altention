"""Messages a channel's Claude could not be handed for now (C-74): a dialog open, the owner's text in the box, the
`!` mode, a machine that does not answer: the codes messages.py marks temporary. They used to be said ("⚠️ … 這則沒有
送出") and dropped; a post another Claude sent was lost for good, nobody there to send it again.

- The "not sent" notice gets a 「再送一次」 button (`resend:<channel>:<token>`, the owner's alone): the words go into
  the channel's queue again (TmuxBridge.submit). Through: "✅ 已再送一次"; held up again: a new notice, a new button.
- A post (altention dc post: its sender is another Claude, which presses no button) is handed again by itself once
  that channel moves on (an event of its turn, a menu button pressed) and its screen shows the plain input box
  (screen.pane_state(); an event moving on may be the next dialog's: manager on C-74): in the order they came, and
  posts that come meanwhile wait behind them. The notice then says "✅ 對話框關了，已補送".
- What the owner typed is never sent again by itself: they may well have typed it again already.
Kept in the channel's state (`unsent`: token -> {text, at, msg, auto, from}), so a restart keeps the buttons; KEEP a
channel, for KEEP_SECS. A post that runs out of time is said where it came from, as a hand-over that broke."""
import asyncio
import re
import time
import traceback
import uuid

import discord

from altention.connectors.claude_code import screen

from . import config, messages, tmuxio
from .util import target

KEEP = 20             # notices with a button kept in a channel (the oldest go)
KEEP_SECS = 24 * 3600
AGAIN = re.compile(r"再(?:把兩則)?重?發(?:一次|這一則)?(?:就好)?")  # how the not-sent words end: "send it again yourself"


def worded(words, code, auto):
    """The not-sent words for a notice with the button (manager on C-74): their "再重發一次" (in any of its forms)
    becomes 「…處理好之後按下面的「再送一次」」, or for a post handed again by itself 「對話框關掉後會自己補送」;
    words that have none get it as a line of their own."""
    if auto:
        said = "對話框關掉後會自己補送" if code == "claude_code.dialog" else "終端機回到一般的輸入框後會自己補送"
    else:
        said = "按下面的「再送一次」"
    found = list(AGAIN.finditer(words))
    if not found:
        return "%s\n-# %s" % (words, said if auto else "處理好之後" + said)
    m = found[-1]
    before = words[:m.start()]
    if before.endswith("後"):  # "回到對話後" / "連得上之後": what it waits for is said already
        said = "會自己補送" if auto else said
    elif not auto:
        said = "處理好之後" + said
    return before + said + words[m.end():]



def view(cid, token):
    v = discord.ui.View(timeout=None)
    v.add_item(discord.ui.Button(label="再送一次", style=discord.ButtonStyle.primary, custom_id="resend:%d:%s" % (cid, token)))
    return v


class Unsent(object):
    """`bridge`: the TmuxBridge (host, chan, rt, submit, say)."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.going = set()  # channel ids whose posts are being handed again (one round at a time)

    def kept(self, st):
        d = st.get("unsent")
        return d if isinstance(d, dict) else {}

    def waiting_posts(self, st):
        """[(token, entry)] of the posts waiting to be handed again, oldest first."""
        return sorted(((t, e) for t, e in self.kept(st).items() if isinstance(e, dict) and e.get("auto")),
                      key=lambda te: te[1].get("at") or 0)

    async def offer(self, ch, st, text, words, message, code=None):
        """The "not sent" notice, with the button; the words kept. `message`: what submit() was given (a post's
        posts.Origin says auto and from; the owner's Discord message its id, for 👀 once it went in)."""
        token = uuid.uuid4().hex[:12]
        auto = bool(getattr(message, "auto", False))
        try:
            msg = await ch.send(worded(words, code, auto), view=view(ch.id, token))
        except Exception:
            traceback.print_exc()
            msg = None
        entry = {"text": text, "at": time.time(), "msg": msg.id if msg is not None else None, "auto": auto}
        sender = getattr(message, "sender", None)
        if sender is not None:
            entry["from"] = sender
        src = getattr(message, "src", None) or getattr(message, "id", None)  # the owner's message (a post has none)
        if src:
            entry["src"] = src
        self.keep(st, token, entry)

    def park(self, st, text, message):
        """A post that came while others wait to be handed again: behind them, no notice of its own (its card is
        up); handed with them."""
        entry = {"text": text, "at": time.time(), "msg": None, "auto": True}
        if getattr(message, "sender", None) is not None:
            entry["from"] = message.sender
        self.keep(st, uuid.uuid4().hex[:12], entry)

    def keep(self, st, token, entry):
        d = st.get("unsent")
        if not isinstance(d, dict):
            st["unsent"] = d = {}
        d[token] = entry
        for old, _ in sorted(d.items(), key=lambda te: (te[1] or {}).get("at") or 0)[:-KEEP]:
            d.pop(old, None)
        self.bridge.host.save()

    async def on_interaction(self, interaction):
        """The 「再送一次」 button. True if it was ours."""
        cid = ((interaction.data or {}).get("custom_id") or "") if interaction.type == discord.InteractionType.component else ""
        if not cid.startswith("resend:"):
            return False
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return True
        _, _, token = cid.split(":", 2)
        ch = interaction.channel
        st = self.bridge.host.chan(ch)
        entry = self.kept(st).pop(token, None)  # taken here before anything is awaited: a round handing posts again
        if not isinstance(entry, dict) or time.time() - (entry.get("at") or 0) > KEEP_SECS:  # that took it first
            self.bridge.host.save()                                                         # has it sent already
            await interaction.response.edit_message(view=None)
            await interaction.followup.send(messages.note("resend.expired"), ephemeral=True)
            return True
        self.bridge.host.save()
        await interaction.response.edit_message(view=None)  # within Discord's 3 seconds; the sending may take longer
        try:
            r = await self.bridge.submit(Again(ch, entry), entry["text"])
        except Exception as e:  # kept again, with a button again: never lost over a failure on the way (#code-review)
            try:
                msg = await ch.send(messages.note("resend.press_failed", why=error_of(e)), view=view(ch.id, token))
                entry["msg"] = msg.id
            except Exception:
                traceback.print_exc()
            self.keep(st, token, entry)
            raise
        done = getattr(r, "code", None) is None
        try:
            await interaction.message.edit(content=(interaction.message.content or "") + "\n" + messages.note("resend.done" if done else "resend.still"))
        except Exception:
            traceback.print_exc()
        return True

    def moved(self, ch, st):
        """The channel moved on (an event of its turn, a menu button pressed): posts waiting are handed again, in a
        task of its own (this is called from the loop of hook events)."""
        if ch is not None and self.waiting_posts(st) and ch.id not in self.going:
            asyncio.ensure_future(self.hand_again(ch, st))

    async def hand_again(self, ch, st):
        """The posts waiting, oldest first, once the screen shows the plain input box; the first held up again stops
        the round (the rest stay behind it). Any that ran out of time is given up and said where it came from."""
        self.going.add(ch.id)
        try:
            for token, entry in self.waiting_posts(st):
                if time.time() - (entry.get("at") or 0) > KEEP_SECS:
                    self.kept(st).pop(token, None)
                    self.bridge.host.save()
                    await self.give_up(ch, entry)
                    continue
                try:
                    shown = screen.pane_state(await tmuxio.raw_screen(target(st)))
                except Exception:
                    traceback.print_exc()
                    return
                if shown != "box":  # the next dialog, the owner's text, a list: not yet
                    return
                if self.kept(st).pop(token, None) is None:  # taken meanwhile: its button pressed, sent from there
                    continue                                 # (#code-review on 5a9fe8d: it went in twice)
                self.bridge.host.save()
                try:
                    r = await self.bridge.submit(Again(ch, entry, quiet=True), entry["text"])
                except Exception as e:  # back in its place, and said where it came from; tried again next time
                    why = error_of(e)
                    told = entry.get("failed") != why  # said once for the same error, not every 10 minutes
                    entry["failed"] = why              # (manager on 0b4f5f4: up to 144 times a day)
                    entry.setdefault("failed_at", time.time())
                    self.keep(st, token, entry)
                    if told:
                        await self.say_from(ch, entry, messages.note("resend.failed", to=ch.name, why=why,
                                                                     text=(entry.get("text") or "")[:300]))
                    raise
                if getattr(r, "code", None) is not None:  # held up after all: back in its place, the round ends
                    self.keep(st, token, entry)
                    return
                if entry.get("failed"):  # its sender was told it broke on the way: told it went after all
                    await self.say_from(ch, entry, messages.note("resend.recovered", to=ch.name,
                                                                 text=(entry.get("text") or "")[:300]))
                if entry.get("msg"):
                    try:
                        msg = ch.get_partial_message(entry["msg"])
                        await msg.edit(view=None)
                        await ch.send(messages.note("resend.handed"), silent=True, reference=msg, mention_author=False)
                    except Exception:
                        traceback.print_exc()
        finally:
            self.going.discard(ch.id)

    async def give_up(self, ch, entry):
        await self.say_from(ch, entry, messages.note("resend.given_up", to=ch.name, hours=KEEP_SECS // 3600,
                                                     text=(entry.get("text") or "")[:300]))

    async def say_from(self, ch, entry, text):
        """Said where a post came from (its sender's channel), else in #audit."""
        sender = self.bridge.host.get_channel(int(entry["from"])) if str(entry.get("from") or "").isdigit() else None
        try:
            if sender is not None:
                await sender.send(text, silent=True)
            else:
                await self.bridge.host.audit(text)
        except Exception:
            traceback.print_exc()

    def kick_all(self):
        """moved() for every channel with posts waiting: at the bot's start and with the 10-minute chores. A dialog
        answered while the bot was down, or a look that found no box and no event after it, left them waiting for
        nothing (#code-review on 5a9fe8d); moved() looks at the screen first and presses no key."""
        for cid, st in list(self.bridge.host.channels_state.items()):
            if isinstance(st, dict) and self.waiting_posts(st) and str(cid).isdigit():
                self.moved(self.bridge.host.get_channel(int(cid)), st)

    async def expire(self):
        """The 10-minute chores: what ran out of time in every channel (posts said where they came from); then the
        posts still waiting are looked at again (kick_all)."""
        await self.drop_old()
        self.kick_all()

    async def drop_old(self):
        for cid, st in list(self.bridge.host.channels_state.items()):
            if not isinstance(st, dict) or not self.kept(st):
                continue
            ch = self.bridge.host.get_channel(int(cid)) if str(cid).isdigit() else None
            for token, entry in list(self.kept(st).items()):
                if not isinstance(entry, dict) or time.time() - (entry.get("at") or 0) > KEEP_SECS:
                    self.kept(st).pop(token, None)
                    if isinstance(entry, dict) and entry.get("auto") and ch is not None:
                        await self.give_up(ch, entry)
            self.bridge.host.save()


def error_of(e):
    return ("%s: %s" % (type(e).__name__, e))[:200]


class Again(object):
    """What submit() is given for words sent again: stands in for a message. `quiet`: held up again, nothing is said
    and no new notice made (the post waits for the next time); `entry`: what it was kept as (auto, from, src). Its
    receipt's 👀 goes on the owner's own message (src); a post has none, and gets none (its "已補送" says it)."""
    def __init__(self, channel, entry, quiet=False):
        self.channel, self.entry, self.quiet = channel, entry, quiet
        self.auto = bool(entry.get("auto"))
        self.sender = entry.get("from")
        self.src = entry.get("src")

    async def add_reaction(self, emoji):
        if self.src:
            await self.channel.get_partial_message(self.src).add_reaction(emoji)
