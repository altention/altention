"""Posting to a channel, from anywhere: reports, notes, and one Claude talking to another.

`altention dc post <channel> …` (any machine) runs `dcctl post`, which leaves the post as a file in
state/posts/. The bot picks it up and does both halves itself, so nothing ever has to tell the bot's own
Discord messages apart:
- it posts the text to the channel (long text split over several messages, up to 10 files attached if given),
  as cards (embeds) headed with where it comes from, "📨 來自 #st-1", in a colour of that sender's own, so they
  stand apart from the channel's Claude's own replies (Discord groups a bot's messages under one avatar); a
  Claude posting into its own channel gets plain messages instead, as its replies (C-73: text and pictures);
- if the channel has a Claude conversation, the same text, headed the same way plus how to answer, is handed
  to that Claude like a message typed there. A plain channel, a terminal or an archived channel only keeps it.
A name that matches no channel is an error (dcctl post refuses it, with the closest names), unless the post asks
for it to be made (`altention dc post --create`): then it becomes a plain channel, a place for records. So a renamed channel
is never silently replaced by a new one under its old name; scripts name channels by ID.

Two Claudes answering each other politely forever is the one real risk. Between the same two channels, at
most LOOP_LIMIT posts are handed over in a row without the owner saying anything in either of them; then the
bot holds the next one and asks. Long discussions on purpose: a channel can raise its limit or switch it off
(!talklimit, st["talk_limit"]: a number, 0 = none), and a pair can be let go from the hold message itself
(st["talk_free"]: the channels this one may talk to without a limit). A channel that sets none has the installation's
(setting "dcbot.talk_limit", asked at install), else LOOP_LIMIT. A hold rings the owner and tells the sending Claude.
"""
import asyncio
import base64
import io
import os
import time
import traceback

import discord

from altention.connectors.claude_code import sessions as cc_sessions

from . import config, events, files, kinds, layout, messages, postbox, remote
from .util import card_color, same_socket, split_message
LOOP_LIMIT = 10  # when the installation says nothing


def default_limit():
    """The limit of a channel that sets none: the installation's dcbot.talk_limit as messages.load() last read it
    (in a thread, never here), else LOOP_LIMIT."""
    said = messages.loaded.get("talk_limit")
    return LOOP_LIMIT if said is None else said
CARD = 4000         # characters in one card (Discord allows 4096)
SLOW_POST = 5       # seconds from the spool to done: more is said in supervisor.out (queued how long, sent how long)
FROM_POST = 6 * 3600  # a question this long after a post came in is no longer about that post


drain, write = postbox.drain, postbox.write
waiting, done = postbox.waiting, postbox.done


def listens(st):
    """Does this channel have a Claude that should read what is posted to it? (A conversation channel with a
    conversation behind it: kinds.py.)"""
    return bool(st) and kinds.channel_kind(st) == kinds.CONVERSATION and kinds.has_conversation(st)


def pair_limit(channels_state, a, b):
    """How many posts in a row two channels' Claudes may exchange (0: no limit). Either side switching it off,
    or letting the other one go, lifts it; otherwise the larger of their limits counts."""
    sa, sb = channels_state.get(str(a)) or {}, channels_state.get(str(b)) or {}
    if b in sa.get("talk_free", []) or a in sb.get("talk_free", []):
        return 0
    la, lb = sa.get("talk_limit", default_limit()), sb.get("talk_limit", default_limit())
    return 0 if la == 0 or lb == 0 else max(la, lb)


def pick_channel(channels, name, category=None, sender_category=None):
    """Among channels called `name`: the one in `category` if given; else the only one; else the only one in
    the sender's category. Several and nothing to tell them apart: None (dcctl refuses such a post before it
    gets here, listing them), never just the first."""
    where = lambda c: getattr(getattr(c, "category", None), "name", None)  # noqa: E731
    named = [c for c in channels if c.name == name and (not category or where(c) == category)]
    if len(named) == 1:
        return named[0]
    same = [c for c in named if sender_category and where(c) == sender_category]
    return same[0] if len(same) == 1 else None


def notify_default():
    """The channel a notification goes to when it names none, or one that is not there: this machine's
    core.notify_channel (as tools/dcctl reads it), "notifications" when the settings cannot be read."""
    try:
        from . import remote
        return str(remote.alt().setting("core.notify_channel") or "notifications")
    except Exception:  # broken settings must not stop the path that reports problems
        return "notifications"


def ambiguous(channels, name, category=None):
    where = lambda c: getattr(getattr(c, "category", None), "name", None)  # noqa: E731
    return len([c for c in channels if c.name == name and (not category or where(c) == category)]) > 1


def chain_of(post):
    """Which posts keep their order among themselves: one sender's to one channel (C-73), sender by channel when
    it has one, else machine and name; the channel as the post names it (a name one time, the ID the next: two)."""
    sender = post.get("from_channel") or "%s|%s" % (post.get("from_host") or "", post.get("from_name") or "")
    return (str(sender), str(post.get("channel") or ""))


def attached(post):
    """[(name, bytes)] of a post's files: file_name/file_b64 is the first (what a bot before C-73 still shows),
    more_files the rest."""
    out = [(post.get("file_name") or "file.txt", base64.b64decode(post["file_b64"]))] if post.get("file_b64") else []
    for f in post.get("more_files") or []:
        out.append((f.get("name") or "file.txt", base64.b64decode(f.get("b64") or "")))
    return out


def as_files(got):
    """discord.File objects (each can be sent once: made afresh for every send)."""
    return [discord.File(io.BytesIO(data), name) for name, data in got]


def spooled_at(path):
    """When a spool file was written (its name starts with time.time_ns()), or None."""
    try:
        return int(os.path.basename(path or "").split("-")[0]) / 1e9
    except ValueError:
        return None


def headed(sender, text):
    return "📨 **來自 %s**\n%s" % (sender, text)


def cards(sender, text):
    """A post as Discord embeds: one card per CARD characters, the first headed with the sender."""
    parts = split_message(text or "（附件）", CARD)
    out = []
    for i, part in enumerate(parts):
        e = discord.Embed(description=part, color=card_color(sender))
        e.set_author(name=("📨 來自 %s" % sender) + ("（%d/%d）" % (i + 1, len(parts)) if len(parts) > 1 else ""))
        out.append(e)
    return out


def for_claude(sender, reply_to, text, cmd="altention"):
    """What a channel's Claude is handed for a post from elsewhere; `cmd`: the altention command on its machine."""
    how = "要回覆就用 `%s dc post %s \"…\"`（那個頻道的 ID，改名也找得到）" % (cmd, reply_to) if reply_to else "對方沒有可以回覆的頻道"
    return "（這是 %s 傳到這個頻道的訊息；%s。）\n%s" % (sender, how, text)


class Posts(object):
    """`bot` is the Discord bot (get_channel, get_guild, chan, channels_state, save, tmux, audit)."""

    def __init__(self, bot):
        self.bot, self.streak, self.held = bot, {}, {}  # {pair: count} / {target channel id: post}
        self.inflight = set()  # spool files being handled: left in place until done, not picked up twice
        self.retrying = False  # a round of retry() is going: the next waits for it
        self.retried = 0  # when state/posts-failed/ was last gone through (0: at the first tick after a start)
        self.from_post = {}  # channel id -> (sender channel id, when): its Claude is working on another's post
        self.chains = {}  # chain_of(post) -> [(path, post)] still to do, in order: one task works through each
        self.showing = {}  # id(post) -> asyncio.Event set once the post is on Discord (its handing over may go on)

    def owner_spoke(self, channel_id):
        """The owner said something in a channel: the conversations it is part of may go on."""
        for pair in list(self.streak):
            if channel_id in pair:
                del self.streak[pair]
        self.from_post.pop(channel_id, None)  # what its Claude does next is the owner's business, not a post's

    async def channel(self, ref, sender_ch=None, create=True):
        """The text channel a post is for: an ID, "category/name", or a name. Several channels can share a name
        (each project its own #notifications): the one in the sender's own category wins, then one outside
        any project. A name nobody has yet becomes a plain channel, in the sender's category (or in the category
        named, if there is one by that name)."""
        guild = self.bot.get_guild(config.GUILD_ID)
        ref = str(ref).strip().lstrip("#")
        if ref.isdigit():
            return self.bot.get_channel(int(ref))
        category_name, _, name = ref.rpartition("/")
        name = name.lstrip("#")
        found = pick_channel(guild.text_channels, name, category_name or None,
                             getattr(getattr(sender_ch, "category", None), "name", None))
        home = getattr(sender_ch, "category", None)
        if found is None and category_name:  # "category/name": made there if the category exists, else say so
            home = next((c for c in guild.categories if c.name == category_name), None)
            if home is None:
                return None
        if found is None and (not create or ambiguous(guild.text_channels, name, category_name or None)):
            return None  # several channels by that name: not one more
        if found is None:
            found = await guild.create_text_channel(name, category=home,
                                                    topic="紀錄用的頻道（由 altention dc post 建立，機器人不在這裡對話）")
            self.bot.chan(found).update({"plain": True, "configured": True})
            self.bot.save()
        return found

    async def notify_channel(self, post):
        """(channel, a line to put before the text) for a notification whose channel is not there: the default
        notification channel, saying where it was meant to go. Only when the default itself is not there is a
        channel made (the default; in its category if that exists, else outside any). Until 2026-10-11 such a
        notification was dropped with one line in #audit while altention dc notify said it went: a renamed channel or a
        typing mistake made an alert disappear, on the very path problems are reported on (#issues 7)."""
        guild = self.bot.get_guild(config.GUILD_ID)
        wanted, default = str(post.get("channel") or "").strip().lstrip("#"), notify_default().strip().lstrip("#")
        base = default.rpartition("/")[2].lstrip("#") or "notifications"
        ch, made = await self.channel(default, None, create=False), False
        category = default.rpartition("/")[0]
        if ch is None and not (category and any(c.name == category for c in guild.categories)):
            # by its name alone: the default names no category, or one that is gone (several by that name: the
            # first). A category that is there gets the channel made in it, never another project's of that name
            named = [c for c in guild.text_channels if c.name == base]
            ch = named[0] if named else None
        if ch is None:
            ch, made = await self.channel(default, None, create=True), True
        if ch is None:
            ch = await guild.create_text_channel(base, category=None, topic="通知用的頻道（機器人建立，不在這裡對話）")
            self.bot.chan(ch).update({"plain": True, "configured": True})
            self.bot.save()
        if made:
            await self.bot.audit("altention dc notify：預設的通知頻道 %s 不存在，已建立 #%s" % (default, ch.name))
        if wanted in (default, base, str(ch.id)) or not wanted:
            return ch, ""
        return ch, "（原本要送到 %s，找不到這個頻道，改送到這裡）\n" % (wanted if wanted.isdigit() else "#" + wanted)

    async def send_parts(self, ch, head, body):
        """Send `head` + `body` to `ch` as messages. Returns (what of the body did not go, why): ("", None) when
        all of it went. A channel object without send (an ID that is a category's) fails like any send."""
        parts = split_message(head + body)
        for i, part in enumerate(parts):
            try:
                await ch.send(part)
            except Exception as e:
                traceback.print_exc()
                return (body if i == 0 else "\n".join(parts[i:])), "%s: %s" % (type(e).__name__, str(e)[:200])
        return "", None

    async def to_audit(self, text):
        """Into #audit, knowing whether it got there (bot.audit goes on without a word when it does not)."""
        ch = getattr(self.bot, "audit_ch", None)
        if ch is None:
            return False
        try:
            for part in split_message(text, 1900):
                await ch.send(part)
            return True
        except Exception:
            traceback.print_exc()
            return False

    async def notify(self, post, sender_ch):
        """A notification is shown somewhere the owner reads, or it is not done (manager 2026-10-11, #issues 7):
        its channel; when that is not there or does not take it, the default notification channel, saying where
        it was meant to go; when that does not take it either, #audit, with its words; and when not even #audit
        can be written (Discord is not reachable), Undelivered: safely() keeps what is left of it in
        state/posts-failed/ and it is tried again. What already went is never sent twice: only the rest goes on,
        marked as a continuation."""
        who = "🔔 **[%s]** " % (post.get("from_name") or "?")
        rest = (post.get("text") or "").strip()
        wanted = str(post.get("channel") or "").strip().lstrip("#")
        named = wanted if wanted.isdigit() else "#" + wanted
        more = "（接續先前沒送完的通知）\n" if post.get("continued") else ""
        why = []  # what was tried and how it failed
        ch = None
        try:
            ch = await self.channel(wanted, sender_ch, create=False) if wanted else None
        except Exception as e:
            why.append("找 %s 時出錯：%s" % (named, e))
        if ch is not None:
            left, err = await self.send_parts(ch, who + more, rest)
            if err is None:
                return
            why.append("%s 送不出去（%s）" % (named, err))
            if left != rest:
                more = "（接續：前面的部分已送到 %s）\n" % named
            rest = left
        elif not why and wanted:
            why.append("找不到頻道 %s" % named)
        try:
            other, note = await self.notify_channel(post)
        except Exception as e:
            traceback.print_exc()
            other, note = None, ""
            why.append("預設的通知頻道用不了（%s）" % e)
        if other is not None and (ch is None or getattr(other, "id", None) != getattr(ch, "id", object())):
            if ch is not None:
                note = "（原本要送到 %s，送不出去，改送到這裡）\n" % named
            left, err = await self.send_parts(other, who + note + more, rest)
            if err is None:
                return await self.bot.audit("altention dc notify：%s，通知改送到 #%s" % ("；".join(why), other.name)) if why else None
            why.append("預設的通知頻道 #%s 送不出去（%s）" % (other.name, err))
            if left != rest:
                more = "（接續：前面的部分已送到 #%s）\n" % other.name
            rest = left
        if await self.to_audit("⚠️ altention dc notify 的通知送不出去（%s）。來自 %s，內容：\n%s%s"
                               % ("；".join(why), post.get("from_name") or "?", more, rest)):
            return
        print("[audit] altention dc notify 的通知送不出去（%s），#audit 也寫不進去：留在 state/posts-failed/ 重試" % "；".join(why), flush=True)
        raise Undelivered(dict(post, text=rest, continued=bool(post.get("continued") or more)))

    async def handle(self, post):
        sender_id = post.get("from_channel")
        sender_ch = self.bot.get_channel(int(sender_id)) if str(sender_id or "").isdigit() else None
        if post.get("attach"):  # dcpost --attach: a Claude asks for a tmux pane to get its own channel
            return await self.attach(post, sender_ch)
        if post.get("new_claude"):  # dcpost --new-claude: a Claude asks for a channel with a Claude of its own
            return await self.new_claude(post, sender_ch)
        if post.get("resume") or post.get("history"):  # dcpost --resume / --history: as the owner's !resume
            return await self.switch(post, sender_ch)
        if post.get("notify") and not post.get("board"):  # dcnotify: shown, never handed to a Claude, never dropped
            return await self.notify(post, sender_ch)
        if post.get("open_chat") or post.get("open_setup"):  # ops/setup.py (C-48): done, or the reconcile's questions
            guild = self.bot.get_guild(config.GUILD_ID)
            if post.get("open_chat"):
                return await self.bot.first_chat(guild)  # #chat there and set up already: nothing (done run again)
            return await self.bot.open_setup(guild, resend=True)
        if post.get("card"):  # altention.dc.card: #update's card, edited in place, with buttons (C-57)
            return await self.bot.cards.show(post)
        if post.get("move_to"):  # dcpost --move-to: a channel into a category (C-57: the reconcile's)
            return await self.move_to(post, sender_ch)
        if post.get("archive"):  # dcctl archive: as /archive (C-63), said in #audit
            ch = await self.channel(post.get("channel") or "", sender_ch, create=False)
            if ch is None:
                return await self.bot.audit("dcctl archive：找不到頻道 %s，沒有封存" % post.get("channel"))
            text, _ = await self.bot.archive.put(ch)  # (one at work is not stopped: put() says so, no button here)
            return await self.bot.audit("dcctl archive #%s：%s" % (ch.name, text))
        ch = await self.channel(post.get("channel") or "", sender_ch, create=bool(post.get("create")))
        if ch is None:
            return await self.bot.audit("altention dc post：找不到頻道 %s" % post.get("channel"))
        sender = "#%s" % sender_ch.name if sender_ch is not None else (post.get("from_name") or "別的地方")
        text = (post.get("text") or "").strip()
        if post.get("board"):  # one message kept up to date in place, not a conversation
            return await self.board(ch, post["board"], text, post)
        got = attached(post)
        if sender_ch is not None and sender_ch.id == ch.id:  # a Claude posting into its own channel (C-73): plain
            return await self.own(ch, text, got)               # messages, as its replies; never handed back to it
        embeds = cards(sender, text)
        for i, e in enumerate(embeds):
            if i == len(embeds) - 1 and got:
                await ch.send(embed=e, files=as_files(got))
            else:
                await ch.send(embed=e)
        self.shown(post)
        st = self.bot.channels_state.get(str(ch.id)) or {}
        if not listens(st):
            return
        if sender_ch is not None:  # Claude to Claude: count, and stop a conversation nobody is watching
            pair = tuple(sorted((ch.id, sender_ch.id)))
            self.streak[pair] = self.streak.get(pair, 0) + 1
            limit = pair_limit(self.bot.channels_state, ch.id, sender_ch.id)
            if limit and self.streak[pair] > limit:
                return await self.hold(ch, sender, post, limit, sender_ch)
        try:
            await self.give(ch, sender, sender_ch, text, post)
        except Exception as e:
            raise NotHanded(ch, sender_ch, e)

    async def own(self, ch, text, got):
        """A post into the sender's own channel: content (split at Discord's 2000), the files on the last message
        (alone when there is no text), no card. Pings nobody: a card never did, content would."""
        parts, quiet = split_message(text) if text else [], discord.AllowedMentions.none()
        if not parts:
            return await ch.send(files=as_files(got), allowed_mentions=quiet) if got else None
        for i, part in enumerate(parts):
            if i == len(parts) - 1 and got:
                await ch.send(part, files=as_files(got), allowed_mentions=quiet)
            else:
                await ch.send(part, allowed_mentions=quiet)

    def shown(self, post):
        """The post is on Discord: the next of its chain may go (what is left is handing it to a Claude)."""
        ev = self.showing.get(id(post))
        if ev is not None:
            ev.set()

    async def move_to(self, post, sender_ch):
        """altention dc post <channel> --move-to <category>: the channel (never made here) into that category, which is
        made when there is none (the system category at the bottom, any other kept above it: layout). Said in #audit,
        with the post's text when it has one; a channel not found is said there too."""
        ch = await self.channel(post.get("channel") or "", sender_ch, create=False)
        want = str(post["move_to"]).strip()
        if ch is None:
            return await self.bot.audit("altention dc post --move-to：找不到頻道 %s，沒有移" % post.get("channel"))
        if getattr(getattr(ch, "category", None), "name", None) == want:
            return
        guild = self.bot.get_guild(config.GUILD_ID)
        await ch.edit(category=await layout.category(guild, want))
        text = (post.get("text") or "").strip()
        await self.bot.audit("#%s 移到分類「%s」（%s）%s" % (ch.name, want, post.get("from_name") or "altention dc post",
                                                       "：" + text if text else ""))

    async def attach(self, post, sender_ch):
        """Attach the pane a post names (as /attach would, in the category the post names), and tell the sender."""
        from . import remote, routes, tmuxio
        want, machine = post["attach"], remote.machine_of(post.get("from_host") or "")
        panes = await tmuxio.claude_panes(everywhere=True)
        pane = next((p for p in panes if p.get("host") == machine and same_socket(p["socket"], want.get("socket"), here=not machine)
                     and p["id"] == want.get("pane")), None)  # macOS: /tmp is /private/tmp
        category = (post.get("channel") or "").strip().lstrip("#") or None
        if pane is None:
            said = "找不到那個 tmux 窗格裡的 Claude Code（%s %s）。" % (machine or "這台", want.get("pane"))
        else:
            said = await self.bot.attach(None, routes.key(pane), category or "")
        await self.bot.audit("altention dc post --attach（%s）：%s" % (post.get("from_name") or "?", said))
        if sender_ch is not None and listens(self.bot.channels_state.get(str(sender_ch.id)) or {}):
            await self.bot.tmux.submit(Origin(sender_ch), "（機器人回覆你的 altention dc post --attach）" + said)

    async def switch(self, post, sender_ch):
        """--resume: switch a channel's Claude to an earlier conversation (and post its history); --history: post
        the history of the one it is on. The answer goes back to the sender."""
        from . import history, sessions
        guild = self.bot.get_guild(config.GUILD_ID)
        ref = (post.get("channel") or "").strip().lstrip("#")
        category_name, _, name = ref.rpartition("/")
        ch = self.bot.get_channel(int(ref)) if ref.isdigit() else pick_channel(
            guild.text_channels, name, category_name or None, getattr(getattr(sender_ch, "category", None), "name", None))
        st = self.bot.channels_state.get(str(ch.id)) if ch is not None else None
        if ch is None or not listens(st or {}):
            said = "找不到有 Claude 的頻道 %s。" % ref
        elif st.get("pane") and not st.get("home"):  # an attached pane, here or on another machine: the owner's own
            said = "#%s 接的是 tmux 裡別人開的對話，不能從這裡換對話或貼紀錄。" % ch.name
        elif post.get("resume"):
            said = await self.bot.resume(ch, self.bot.chan(ch), post["resume"].strip())
        elif sessions.elsewhere(st) and st.get("session_id"):
            # a window the bot opened on another machine: the conversation's file is there (#issues 35; until then
            # such a channel was refused as "somebody else's")
            far, sid = sessions.elsewhere(st), st["session_id"]
            found, why = await history.far_sessions(far, st["cwd"], sid)
            if why or found is None:
                said = "#%s 的對話在 %s 上，%s，所以沒有貼紀錄。" % (ch.name, far, why or "那台找不到 `%s` 這個對話" % sid[:8])
            else:
                done = await history.show_remote(self.bot, ch, self.bot.chan(ch), far, sid, found["path"], force=True)
                said = "已在 #%s 貼出對話 `%s` 的紀錄。" % (ch.name, sid[:8]) if done else \
                    "#%s 的對話在 %s 上，紀錄沒有貼成（原因貼在那個頻道裡）。" % (ch.name, far)
        else:
            sid = st.get("session_id")
            done = await history.show(self.bot, ch, self.bot.chan(ch), sid, cc_sessions.transcript(st["cwd"], sid) if sid else None,
                                      force=True)
            said = "已在 #%s 貼出對話 `%s` 的紀錄。" % (ch.name, (sid or "")[:8]) if done else "#%s 目前的對話沒有紀錄可以貼。" % ch.name
        await self.bot.audit("altention dc post %s（%s）：%s" % ("--resume" if post.get("resume") else "--history",
                                                    post.get("from_name") or "?", said))
        if sender_ch is not None and sender_ch.id != getattr(ch, "id", None) and \
                listens(self.bot.channels_state.get(str(sender_ch.id)) or {}):
            await self.bot.tmux.submit(Origin(sender_ch), "（機器人回覆你的 altention dc post）" + said)

    async def new_claude(self, post, sender_ch):
        """Make (or take, if it has never been set up) the channel, give it a Claude in the bot's tmux working in
        the folder, start it, and tell the sender."""
        import os
        from . import setup
        folder = os.path.abspath(os.path.expanduser(post["new_claude"]))
        guild = self.bot.get_guild(config.GUILD_ID)
        category_name, _, name = (post.get("channel") or "").strip().lstrip("#").rpartition("/")
        ch = pick_channel(guild.text_channels, name, category_name or None,
                          getattr(getattr(sender_ch, "category", None), "name", None))
        if not os.path.isdir(folder):
            said = "找不到資料夾 %s，頻道沒有建。" % folder
        elif ch is not None and setup.configured(self.bot.chan(ch)) and not self.bot.chan(ch).get("plain"):
            said = "#%s 已經設定過了，沒有改它。" % ch.name
        else:
            topic = (post.get("text") or "").strip()[:1000] or None
            if ch is None:
                home = getattr(sender_ch, "category", None)
                if category_name:  # the category named: made if it does not exist yet
                    home = await layout.category(guild, category_name)
                ch = await guild.create_text_channel(name, category=home, topic=topic)
            elif topic:
                await ch.edit(topic=topic)
            st = self.bot.chan(ch)
            st.pop("plain", None)
            st.update({"cwd": folder, "configured": True, "mode": "tmux"})
            self.bot.save()
            card = self.bot.setup.cards.pop(ch.id, None) if getattr(self.bot, "setup", None) else None
            if card is not None:
                try:
                    await ch.get_partial_message(card).edit(content="✅ 已設定成 Claude 對話。", view=None)
                except discord.HTTPException:
                    pass
            await ch.send("🤖 這個頻道的 Claude 開在機器人自己管的 tmux，工作目錄 `%s`（由 %s 建立）。"
                          % (folder.replace(os.path.expanduser("~"), "~"), "#" + sender_ch.name if sender_ch else post.get("from_name") or "altention dc post"))
            await self.bot.tmux.start_now(ch)
            said = "已建好 #%s（<#%d>），Claude 在 %s 開好了。" % (ch.name, ch.id, folder)
        await self.bot.audit("altention dc post --new-claude（%s）：%s" % (post.get("from_name") or "?", said))
        if sender_ch is not None and listens(self.bot.channels_state.get(str(sender_ch.id)) or {}):
            await self.bot.tmux.submit(Origin(sender_ch), "（機器人回覆你的 altention dc post --new-claude）" + said)

    async def board(self, ch, name, text, post=None):
        """dcpost --board: the board's messages are edited in place (more parts than before are added, fewer
        are deleted), so the channel always shows its latest state without a trail of old copies.

        Only a message Discord says is gone (NotFound: deleted by hand) is posted again. Any other failure (a 5xx,
        a timeout, the network) keeps its ID and is tried again next time: posting anew then left the old copy
        behind, stuck on old content (a second 圖表 board in #dashboard, 2026-10-17). A board that fails so
        BOARD_FAILS times running (a 403 never gets better by itself) is said to the owner once, as a
        notification; one time that works starts the count again."""
        st = self.bot.chan(ch)
        boards = st.setdefault("boards", {})
        stamp = time.strftime("%m-%d %H:%M")
        parts = split_message("📌 **%s**\n-# 更新於 %s\n%s" % (name, stamp, text or "（空白）"))
        old, kept, errors = list(boards.get(name) or []), [], []
        attach = (post or {}).get("file_b64")  # a chart and the like: on the board's first message, replaced each time
        for i, part in enumerate(parts):
            msg, extra = None, {}
            if attach and i == 0:
                extra = {"file": discord.File(io.BytesIO(base64.b64decode(attach)), post.get("file_name") or "board.png")}
            if i < len(old):
                try:
                    edit = {"attachments": [extra["file"]]} if extra else {}
                    msg = await ch.get_partial_message(old[i]).edit(content=part, **edit)
                except discord.NotFound as e:
                    print("[board] #%s 的看板「%s」第 %d 則不見了（%s），重貼" % (ch.name, name, i + 1, e), flush=True)
                except Exception as e:  # a 5xx, a timeout, the network (aiohttp's errors): anything but NotFound
                    print("[board] #%s 的看板「%s」第 %d 則這次改不了（%s: %s），留著原本那則，下一次再改"
                          % (ch.name, name, i + 1, type(e).__name__, e), flush=True)
                    errors.append(e)
                    kept.append(old[i])
                    continue
            if msg is None:
                if extra:
                    extra["file"] = discord.File(io.BytesIO(base64.b64decode(attach)), post.get("file_name") or "board.png")
                msg = await ch.send(part, silent=True, **extra)
            kept.append(msg.id)
        for mid in old[len(parts):]:
            try:
                await ch.get_partial_message(mid).delete()
            except discord.NotFound:
                pass
            except Exception as e:  # a 5xx, a timeout, the network (aiohttp's errors): anything but NotFound
                print("[board] #%s 的看板「%s」多出來的一則這次刪不掉（%s: %s），下一次再刪"
                      % (ch.name, name, type(e).__name__, e), flush=True)
                errors.append(e)
                kept.append(mid)  # beyond the parts, so the next time deletes it again
        boards[name] = kept
        fails = st.setdefault("board_fails", {})
        if errors:
            fails[name] = fails.get(name, 0) + 1
        else:
            fails.pop(name, None)
        self.bot.save()
        if fails.get(name) == BOARD_FAILS:  # said once; Undelivered goes on to safely(), which keeps it for retry
            await self.notify({"notify": True, "from_name": "機器人", "text":
                               "#%s 的看板「%s」連續 %d 次更新失敗（%s），停在舊的內容。機器人每次都會再試，好了就不用管；"
                               "一直這樣的話，看看機器人在 #%s 的權限（要能看、能發訊息），或那個頻道是不是被封存了。"
                               % (ch.name, name, BOARD_FAILS, error_code(errors[-1]), ch.name)}, None)

    async def give(self, ch, sender, sender_ch, text, post):
        names = [name for name, _ in attached(post)]
        body = text + ("\n（附件：%s，在 Discord 的這則訊息上）" % "、".join(names) if names else "")
        reply_to = sender_ch.id if sender_ch is not None else None
        if sender_ch is not None:
            self.from_post[ch.id] = (sender_ch.id, time.time())
        cmd = remote.altention_cmd(files.host_of(self.bot.channels_state.get(str(ch.id)) or {}))
        await self.bot.tmux.submit(Origin(ch), for_claude(sender, reply_to, body, cmd))

    async def asking(self, ch, ask_id, message_id, plan=False):
        """The Claude of `ch` asks the owner something (buttons) while working on another channel's post: that
        channel's conversation now waits on the owner too. Say so there (it rings), with a link to the question,
        and tell its Claude if it is in the middle of a turn, so it does not sit waiting for an answer."""
        src = self.from_post.get(ch.id)
        if not src or time.time() - src[1] > FROM_POST:
            return
        sender_ch = self.bot.get_channel(src[0])
        if sender_ch is None:
            return
        link = "https://discord.com/channels/%d/%d/%d" % (config.GUILD_ID, ch.id, message_id)
        what = "核准計畫" if plan else "回答問題"
        try:
            msg = await sender_ch.send("⏸️ <#%d> 的 Claude 在等你%s，它回覆這裡之前會先停住：%s" % (ch.id, what, link))
        except discord.HTTPException:
            return
        st = self.bot.chan(ch)
        st.setdefault("asking", {})[ask_id] = [sender_ch.id, msg.id]
        self.bot.save()
        sst = self.bot.channels_state.get(str(sender_ch.id)) or {}
        if listens(sst) and sst.get("busy"):  # a task of its own: this runs in the loop of hook events (pump), and
            asyncio.ensure_future(self.bot.tmux.submit(  # typing waits for that channel's queue and receipt (C-73)
                Origin(sender_ch), "（機器人通知）#%s 的 Claude 正在等使用者%s，暫時不會回你。不用空等它，"
                                   "先做別的或結束這一輪；它回覆時你會收到。" % (ch.name, what)))

    async def answered(self, ch, ask_id, how):
        """The question asking() announced is settled (answered or cancelled): update the notice."""
        st = self.bot.channels_state.get(str(ch.id)) or {}
        where = (st.get("asking") or {}).pop(ask_id, None)
        if not where:
            return
        self.bot.save()
        sender_ch = self.bot.get_channel(where[0])
        if sender_ch is None:
            return
        try:
            await sender_ch.get_partial_message(where[1]).edit(
                content="✅ <#%d> 的提問已回答，它會接著做。" % ch.id if how == "已回答" else "⌛ <#%d> 的提問%s。" % (ch.id, how))
        except discord.HTTPException:
            pass

    async def hold(self, ch, sender, post, limit, sender_ch=None):
        """Keep the post back and ask the owner (it rings: nobody may be watching); tell the sending Claude, if it is
        in a turn, that it waits on the owner now (as asking() does), so it neither sits waiting nor sends again."""
        self.held[ch.id] = post
        v = discord.ui.View(timeout=None)
        v.add_item(discord.ui.Button(label="繼續轉交", custom_id="post:%d:go" % ch.id, style=discord.ButtonStyle.primary))
        v.add_item(discord.ui.Button(label="這兩個頻道不再限制", custom_id="post:%d:free" % ch.id,
                                     style=discord.ButtonStyle.secondary))
        v.add_item(discord.ui.Button(label="停下來", custom_id="post:%d:stop" % ch.id, style=discord.ButtonStyle.secondary))
        await ch.send("⏸️ <@%d> %s 和這個頻道的 Claude 已經連續來回 %d 次，你都沒有插話。上面這則先不交給 Claude，要繼續嗎？\n"
                      "-# 要讓它們長時間討論：按「這兩個頻道不再限制」，或用 `/talklimit` 調整這個頻道的上限"
                      % (config.OWNER_ID, sender, limit), view=v)
        v.stop()
        sst = (self.bot.channels_state.get(str(sender_ch.id)) or {}) if sender_ch is not None else {}
        if listens(sst) and sst.get("busy"):
            await self.bot.tmux.submit(Origin(sender_ch), "（機器人通知）你剛傳給 #%s 的訊息先暫停了：你們兩個頻道的 Claude 已經連續來回 %d 次，"
                                                          "使用者都沒有插話，正在等使用者決定要不要繼續。不要空等，也不要重送；先做別的或結束這一輪。"
                                                          "使用者決定繼續時，對方會收到這則並回覆你。" % (ch.name, limit))

    async def on_interaction(self, interaction):
        if interaction.type != discord.InteractionType.component:
            return False
        parts = ((interaction.data or {}).get("custom_id") or "").split(":")
        if len(parts) != 3 or parts[0] != "post":
            return False
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return True
        ch, post = interaction.channel, self.held.pop(int(parts[1]), None)
        if post is None:
            await interaction.response.edit_message(view=None)
            return True
        self.owner_spoke(ch.id)  # deciding counts as taking part
        if parts[2] in ("go", "free"):
            if parts[2] == "free" and str(post.get("from_channel") or "").isdigit():
                st = self.bot.chan(ch)
                st["talk_free"] = sorted(set(st.get("talk_free", []) + [int(post["from_channel"])]))
                self.bot.save()
            await interaction.response.edit_message(
                content="▶️ 繼續轉交。" if parts[2] == "go" else "▶️ 繼續轉交，這兩個頻道之後不再限制來回次數。", view=None)
            sender_ch = self.bot.get_channel(int(post["from_channel"])) if str(post.get("from_channel") or "").isdigit() else None
            sender = "#%s" % sender_ch.name if sender_ch is not None else (post.get("from_name") or "別的地方")
            await self.give(ch, sender, sender_ch, (post.get("text") or "").strip(), post)
        else:
            await interaction.response.edit_message(content="⏹️ 停下來了，那則沒有交給 Claude。", view=None)
        return True

    async def tick(self):
        """Called from the loop that also reads Claude's hook events. Handing a post over waits for Claude to
        confirm it arrived, and that confirmation is one of those events: so nothing here waits for a post. One
        sender's posts to one channel go in the order sent (chain_of: a task of their own works through them);
        the others' go on meanwhile."""
        for path, post in waiting():
            if path not in self.inflight:
                self.inflight.add(path)
                key = chain_of(post)
                if key not in self.chains:
                    self.chains[key] = []
                    asyncio.ensure_future(self.chain(key))
                self.chains[key].append((path, post))
        if time.time() - self.retried > postbox.RETRY_EVERY:
            self.retried = time.time()
            asyncio.ensure_future(self.retry())

    async def chain(self, key):
        """One sender's posts to one channel shown on Discord one after another (C-73): the next goes once this one
        is there. Handing them to that channel's Claude is a queue of its own, the channel's (TmuxBridge.submit
        holds the channel's lock from typing to receipt, for anyone's message): a slow hand-over (a machine that
        does not answer) holds only that, never what is shown (manager on C-73's rehearsal)."""
        try:
            while self.chains.get(key):
                path, post = self.chains[key].pop(0)
                ev = self.showing[id(post)] = asyncio.Event()
                task = asyncio.ensure_future(self.safely(post, path))
                seen = asyncio.ensure_future(ev.wait())
                try:
                    await asyncio.wait({task, seen}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    seen.cancel()
        except Exception:
            traceback.print_exc()
        finally:
            self.chains.pop(key, None)  # (nothing awaited since the last check: no post was added in between)

    async def safely(self, post, path=None):
        began = time.time()
        try:
            await self.handle(post)
        except NotHanded as e:  # shown, but handing it to the channel's Claude broke: kept aside, and said (C-73)
            traceback.print_exc()
            if path:
                events.set_aside(path, os.path.dirname(path))
                self.inflight.discard(path)
            await self.not_handed(post, e)
            return
        except Undelivered as e:  # a notification that could be shown nowhere: kept, tried again (retry)
            try:
                postbox.fail(path, e.post)
            except Exception:  # not even kept: its spool file stays (and stays taken: this run of the bot does not
                traceback.print_exc()  # send its first part again every half second); the next start takes it again
                return
        except Exception:
            traceback.print_exc()
            # not what notify() foresaw: kept all the same; and a card (#code-review of 12a1556: a 5xx on the last
            # "✅ updated" left the card at "⏳" for good); retry() shows it again, update_cards skips it if a newer came
            if (post.get("notify") and not post.get("board")) or post.get("card"):
                try:
                    postbox.fail(path, post)
                except Exception:  # not even kept: its spool file stays, as above
                    traceback.print_exc()
                    return
        finally:
            self.shown(post)
            self.showing.pop(id(post), None)
            self.slow(post, path, began)
        if path:  # removed only once handled: a restart in between delivers it again rather than losing it
            done(path)
            self.inflight.discard(path)

    async def not_handed(self, post, e):
        """Where the post came from (else #audit): it is on Discord, its Claude never got it."""
        text = "⚠️ 這則沒交到 #%s 的 Claude（%s）：訊息在 Discord 上，但它沒收到；內容留在機器人的 state/posts-bad/。" % (
            e.ch.name, error_code(e.cause))
        try:
            if e.sender_ch is not None and e.sender_ch.id != e.ch.id:
                await e.sender_ch.send(text, silent=True)
            else:
                await self.bot.audit("%s：%s" % (post.get("from_name") or "altention dc post", text))
        except Exception:
            traceback.print_exc()

    def slow(self, post, path, began):
        """One line in supervisor.out for a post that took more than SLOW_POST from the spool to done: how long it
        waited (behind its chain, or for the loop) and how long it took itself (C-73; quiet otherwise)."""
        at, now = spooled_at(path), time.time()
        if at is None or now - at <= SLOW_POST:
            return
        print("[posts] #%s%s：%.1f 秒（排隊 %.1f、處理 %.1f）" % (post.get("channel"), "（看板）" if post.get("board") else "",
                                                       now - at, began - at, now - began), flush=True)

    async def retry(self):
        """The notifications in state/posts-failed/ once more (at the bot's start, then every RETRY_EVERY): shown
        now, or what is left stays for the next time, until RETRY_FOR after the first failure. One round at a
        time, and each file only while it is still there: a round held up by a slow channel was overtaken by the
        next, which showed and removed a notification the first then showed again from its old list (#code-review
        2026-10-11)."""
        if self.retrying:
            return
        self.retrying = True
        try:
            for path, post in postbox.failed():
                if path in self.inflight or not os.path.exists(path):
                    continue
                self.inflight.add(path)
                try:
                    await self.handle(post)
                    done(path)
                except Undelivered as e:
                    postbox.fail(path, e.post)
                except Exception:
                    traceback.print_exc()
                finally:
                    self.inflight.discard(path)
        finally:
            self.retrying = False


BOARD_FAILS = 3  # times running a board fails to update (not NotFound) before the owner is told, once


def error_code(e):
    """How a failed Discord call is said: HTTP status and Discord's own code for an HTTPException, else its type."""
    status = getattr(e, "status", None)
    if isinstance(status, int):
        code = getattr(e, "code", 0)
        text = str(getattr(e, "text", "") or "")[:200]
        return "HTTP %d%s%s" % (status, "，錯誤碼 %d" % code if code else "", "：" + text if text else "")
    return "%s: %s" % (type(e).__name__, str(e)[:200]) if str(e) else type(e).__name__


class NotHanded(Exception):
    """A post shown in `ch` whose handing over to that channel's Claude raised `cause`."""
    def __init__(self, ch, sender_ch, cause):
        Exception.__init__(self, "post not handed over: %r" % (cause,))
        self.ch, self.sender_ch, self.cause = ch, sender_ch, cause


class Undelivered(Exception):
    """A notification that could be shown nowhere; `post`: what is left of it."""
    def __init__(self, post):
        Exception.__init__(self, "notification not delivered")
        self.post = post


class Origin(object):
    """Stands in for a Discord message: a post handed to Claude has no message of its own to react to."""
    def __init__(self, channel):
        self.channel = channel

    async def add_reaction(self, emoji):
        pass
