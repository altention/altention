"""A rename taken along at once (C-201 2b, design instance docs/refactor/c201-design.md section 3 points 1-3 and section
4's 〔改名…〕; owner 11-17: point 1 as proposed, point 2 a; manager 11-18): the channel, its Claude Code conversation
(/rename typed in), its tmux window and, only when the bot opened it for this channel alone, its tmux session, as the
record's `follow` says (bot/where.py), each said done (✅), tried and not done (❌, why in plain words) or not for this
channel (➖: no failure; #discord-lab 11-18).

From where a rename starts:
- `/rename` (`!rename`), the 〔改名…〕 button of /where's card, `altention dc post <channel> --rename <name> --why …`
  (another channel's Claude: the card and #audit say whose, --why needed): everything it follows.
- the channel renamed by hand in Discord: the others follow, the channel as it is. A rename of the bot's own is known by
  the name it wrote (`mine`), and not taken along a second time.
- the window named by hand in tmux (the look, bot/reconcile.py): the others follow, the window as it is. Only a name
  the owner gave (tmux's automatic-rename off) of the very pane seen there at the last look too, and only a name that
  differs from the one the bot wrote last (its own rename-window turns automatic-rename off as well: manager 11-18).
  Nothing is retried (Discord's two renames in ten minutes: said in #audit, left as it is).

The names the bot wrote are kept in the channel's state as `named` ({channel, window}); not a place field (where.put()
writes those)."""
import asyncio
import re
import time
import traceback

import discord

from . import config, messages, tmuxio, where as where_mod

CHANNEL_SECS = 8  # a channel rename Discord holds back past this long: its limit of two in ten minutes (discord.py
# would sleep till then: nothing waits, the channel's name is read once instead)
MINE_SECS = 120  # a channel update this long after the bot's own rename to that name is the bot's own
READ_SECS = 5  # seconds to read the channel's name once after CHANNEL_SECS passed with no answer
PREFIX = "c201rn"  # the 〔改名…〕 button: c201rn:<channel id>
ITEMS = ("channel", "conversation", "window", "session")


def slug(name):
    """A name as a Discord text channel takes it (lower case, a run of anything but letters, digits, _ and - one -),
    also what the window and session are named: one name in every place."""
    return re.sub(r"[^\w-]+", "-", (name or "").strip().lower(), flags=re.UNICODE).strip("-")[:90]


def tmux_named(st, old, new, channel):
    """The look found the channel's window named otherwise (C-201 2b, point 3): the name to take along, or None. Only
    the very pane seen there at the last look too (no window opened again), named by the owner (tmux's own name, which
    follows the program, is no name: reconcile's window_of() gives the index then), not the name the bot wrote last,
    and not one that is the channel's already. The name seen is kept as the bot's, so it is taken along once."""
    if not isinstance(st, dict) or (old or {}).get("state") != "here" or (new or {}).get("state") != "here":
        return None
    if (old.get("machine"), old.get("pane")) != (new.get("machine"), new.get("pane")) or not new.get("pane"):
        return None
    name, was = new.get("window"), old.get("window")
    if not name or name == new.get("index") or name == was:
        return None
    named = st.get("named") if isinstance(st.get("named"), dict) else {}
    if name == named.get("window"):
        return None  # (the bot's own rename-window)
    st["named"] = dict(named, window=name)
    return name if slug(name) and slug(name) != channel else None


async def learn_agent(bot, st, name=None):
    """The agent behind a channel attached to a pane, asked of the pane now (tmuxio.agent_now(): an adapter by its
    own mark) and kept in the pane's record (`agent`, read by where.record()); what was kept when it cannot be told
    now. "" for Claude Code (and for the bot's own windows, which run it). C-198 drill of d0f26f25, A: a pi pane
    was taken for Claude Code, /rename typed into it."""
    pane = st.get("pane") if isinstance(st, dict) and isinstance(st.get("pane"), dict) else None
    if pane is None or pane.get("shell"):
        return ""
    try:
        now = await tmuxio.agent_now(pane)
    except Exception:
        traceback.print_exc()
        now = None
    if now is not None and now != (pane.get("agent") or "") and st.get("pane") is pane:  # (the pane not changed
        kept = {k: v for k, v in pane.items() if k != "agent"}                           # meanwhile)
        where_mod.put(st, name, pane=dict(kept, agent=now) if now else kept)
        bot.save()
    return now if now is not None else (pane.get("agent") or "")



def reason(text):
    """Why a rename was not typed in, for its card's row: the first sentence of what the bridge says, without its
    consequences (the row says it was not sent: "沒送進去：…"; manager 11-20, words said twice) or its bold."""
    why = str(text or "").replace("**", "").lstrip("⚠️ ").split("。")[0]
    for tail in ("，所以", "，訊息沒有送出", "，這則沒有送出"):
        why = why.split(tail)[0]
    return why[:160]

class Renamer:
    def __init__(self, bot):
        self.bot = bot
        self.mine = {}  # channel id -> (name, when): a channel rename of the bot's own, not taken along again

    def mine_said(self, cid, name):
        """Was the channel's update to `name` the bot's own rename? (once: the next one to that name is the owner's)"""
        got = self.mine.get(str(cid))
        if got and got[0] == name and time.time() - got[1] < MINE_SECS:
            self.mine.pop(str(cid), None)
            return True
        return False

    async def rename(self, ch, st, name, take=None, by=None, why=None, source=None, old=None):
        """Rename what the channel's record follows (`take`: only these of them; None: all). `by`: the channel whose
        Claude asked (--rename), with `why`; `source`: "discord" or "tmux" when the owner renamed one of them by hand
        there (said in #audit only), `old` then the channel's name before it (Discord) and `name` as the owner wrote
        it (tmux). (all done, the card) -- the card also when nothing could be done."""
        bot = self.bot
        new = slug(name)
        if not new:
            return False, messages.rename("usage")
        bot.save()  # (its record as the fields are now)
        agent = await learn_agent(bot, st, getattr(ch, "name", None))
        rec = st.get("where") if isinstance(st.get("where"), dict) else {}
        if not rec:  # (no conversation behind it: a record, terminal or files channel; its name alone, as before)
            take = ["channel"]
        follow = list(rec.get("follow") or where_mod.FOLLOW)
        old = old or getattr(ch, "name", "?")
        rows = []
        for item in ITEMS:
            if take is not None and item not in take:
                continue
            if item not in follow:
                rows.append((item, "skipped", self.not_followed(st, item), None))
                continue
            if item == "conversation" and agent:  # an adapter (pi, Codex): no conversation name of its own; nothing
                rows.append(("agent_conversation", "skipped", messages.rename("adapter_none"), None))  # typed (a
                continue  # /rename there is a message to its model: drill of d0f26f25, A)
            try:
                rows.append((item,) + await getattr(self, "do_" + item)(ch, st, rec, new))
            except Exception as e:  # (one breaking leaves the others done)
                traceback.print_exc()
                rows.append((item, "failed", messages.rename("tmux_refused" if item in ("window", "session")
                                                             else "not_sent", why=str(e)[:120]), None))
            rec = st.get("where") if isinstance(st.get("where"), dict) else rec
        named = new if any(r[0] == "channel" and r[1] == "done" for r in rows) else getattr(ch, "name", None)
        where_mod.put(st, named or new, ctx=bot.where_context())  # (discord.py's channel takes its new name only with
        # the update event)
        bot.save()
        card, audit = self.said(old, name if source == "tmux" else new, rows, by, why, source)
        await bot.audit(audit)
        return all(r[1] != "failed" for r in rows), card

    def not_followed(self, st, item):
        if item == "session":
            return messages.rename("users_session" if st.get("home") or st.get("pane_session") else "not_bots")
        return messages.rename("not_followed")

    async def do_channel(self, ch, st, rec, new):
        if getattr(ch, "name", None) == new:
            return "skipped", messages.rename("same_name"), None
        self.mine[str(ch.id)] = (new, time.time())
        try:
            await asyncio.wait_for(ch.edit(name=new), CHANNEL_SECS)
        except asyncio.TimeoutError:  # (mostly discord.py sleeping out the limit of two in ten minutes: the request
            # that met it is not sent again once cancelled; #code-review-2 on d81cd6f2): the channel read once (manager
            # 11-18): renamed, done; not, not done; not read, not known (`mine` kept till MINE_SECS: its update, should
            # it come late, is the bot's own)
            now = await self.name_now(ch)
            if now is not None and slug(now) == slug(new):
                st["named"] = dict(st.get("named") or {}, channel=new)
                return "done", None, None
            if now is not None:
                self.mine.pop(str(ch.id), None)
                return "failed", messages.rename("limited_not_done"), None
            return "late", messages.rename("late", secs=CHANNEL_SECS), None
        except discord.HTTPException as e:
            self.mine.pop(str(ch.id), None)
            if getattr(e, "status", None) == 429:
                return "failed", messages.rename("rate_limited"), None
            return "failed", messages.rename("discord_refused", why=(getattr(e, "text", "") or str(e))[:120]), None
        st["named"] = dict(st.get("named") or {}, channel=new)
        return "done", None, None

    async def name_now(self, ch):
        """The channel's name as Discord has it now, or None (not read in READ_SECS, or refused)."""
        try:
            got = await asyncio.wait_for(self.bot.fetch_channel(ch.id), READ_SECS)
        except Exception:
            return None
        return getattr(got, "name", None)

    async def do_conversation(self, ch, st, rec, new):
        if self.bot.mode(st) != "tmux":
            return "skipped", messages.rename("not_tmux"), None
        from . import slash
        try:
            got = await self.bot.tmux.submit(slash.Origin(ch), "/rename " + new, out=self.bot.quiet)
        except Exception as e:  # (NotOpened: its pane gone, a machine not reached; said as it says it)
            return "failed", messages.rename("not_sent", why=reason(str(e))), None
        if got is not None and not got:  # not typed in (manager 11-20, X: a pane restarted and still being made sure
            # of, the box not what was typed, …): a Typing refused, or False; never said as done
            why = messages.not_sent(got.code, **got.data) if getattr(got, "code", None) else messages.rename("not_entered")
            return "failed", messages.rename("not_sent", why=reason(why)), None
        return "done", None, None

    async def do_window(self, ch, st, rec, new):
        place = rec.get("place") or {}
        here, bot_socket = self.bot.where_context()
        host = place.get("machine") if place.get("machine") not in (None, "", here) else None
        home = st.get("home") if isinstance(st.get("home"), dict) else None
        if place.get("pane"):
            target = place["pane"]
        elif place.get("session") and st.get("tmux") and place.get("socket") == bot_socket and not home:
            target = "=%s:" % place["session"]  # (the bot's own session: this channel's alone)
        elif home:  # (a window the bot opened in the owner's tmux, closed now: "=session:" is the owner's window
            # then, #code-review-2 on 74b5e7da; one opened by name is opened by the new one next time)
            if home.get("window") and not str(home["window"]).isdigit():
                where_mod.put(st, ctx=self.bot.where_context(), home=dict(home, window=new))
            return "skipped", messages.rename("window_closed"), None
        else:
            return "skipped", messages.rename("no_window"), None
        err = []
        code = await tmuxio.rename_window(place.get("socket"), target, new, host=host, err=err)
        if host and code == 255:
            return "failed", messages.rename("unreachable", item="window "), None
        if code != 0:
            return "failed", messages.rename("tmux_refused", why=(" ".join(err) or "exit %s" % code)[:120]), None
        st["named"] = dict(st.get("named") or {}, window=new)
        home = st.get("home") if isinstance(st.get("home"), dict) else None
        if home and home.get("window") and not str(home["window"]).isdigit():  # (opened again by this name)
            where_mod.put(st, ctx=self.bot.where_context(), home=dict(home, window=new))
        return "done", None, new

    async def do_session(self, ch, st, rec, new):
        was = st.get("tmux")
        if not was:
            return "skipped", self.not_followed(st, "session"), None
        name = tmuxio.session_name(new, ch.id)
        if name == was:
            return "skipped", messages.rename("same_name"), None
        socket = (rec.get("place") or {}).get("socket")  # (the bot's own server)
        if await tmuxio.has_session(socket, was):
            err = []
            code = await tmuxio.rename_session(socket, was, name, err=err)
            if code != 0:
                return "failed", messages.rename("tmux_refused", why=(" ".join(err) or "exit %s" % code)[:120]), None
        where_mod.put(st, ctx=self.bot.where_context(), tmux=name)  # (not open now: opened by this name next time)
        return "done", None, name

    def said(self, old, new, rows, by, why, source):
        say = messages.rename
        done = [r for r in rows if r[1] == "done"]
        failed = [r for r in rows if r[1] == "failed"]
        late = [r for r in rows if r[1] == "late"]
        head = say("failed", old=old) if failed and not done and not late else say("partly", old=old) if failed else \
            say("late_head", old=old) if late else say("done", old=old, new=new)
        lines = [head]
        for item, how, words, as_ in rows:
            label = say("item_" + item)
            if how == "done":
                lines.append(say("row_done_as", item=label, name=as_) if as_ else say("row_done", item=label))
            else:
                lines.append(say("row_" + how, item=label, why=words))
        if by is not None:
            lines.append(say("by", channel=getattr(by, "name", by), why=why))

        def items(of):
            return "、".join(say("item_" + r[0]) + ("（%s）" % r[2] if r[1] != "done" else "") for r in of)
        skipped = [r for r in rows if r[1] == "skipped"]
        parts = dict(done=say("audit_done", items=items(done) or say("audit_none")) if source is None else
                     (items(done) or say("audit_none")),
                     skipped=say("audit_skipped", items=items(skipped)) if skipped else "",
                     failed=(say("audit_failed", items=items(failed)) if failed else "") +
                     (say("audit_late", items=items(late)) if late else ""))
        if source == "discord":
            audit = say("audit_discord", old=old, new=new, **parts)
        elif source == "tmux":
            audit = say("audit_tmux", channel=old, name=new, **parts)
        else:
            audit = say("audit", old=old, new=new, by=say("audit_by", channel=getattr(by, "name", by), why=why)
                        if by is not None else "", **parts)
        return "\n".join(lines), audit

    def view(self, ch):
        """/where's card buttons: 〔改名…〕 (the move ones come with the next batches)."""
        v = discord.ui.View(timeout=None)
        v.add_item(discord.ui.Button(label=messages.rename("button"), style=discord.ButtonStyle.secondary,
                                     custom_id="%s:%s" % (PREFIX, ch.id)))
        return v

    async def on_interaction(self, interaction):
        """The 〔改名…〕 button: the box with the name now in it. True when it was ours."""
        if interaction.type != discord.InteractionType.component:
            return False
        parts = ((interaction.data or {}).get("custom_id") or "").split(":")
        if len(parts) != 2 or parts[0] != PREFIX:
            return False
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return True
        ch = interaction.channel
        if ch is None or str(ch.id) != parts[1]:
            await interaction.response.send_message(messages.rename("gone"), ephemeral=True)
            return True
        await interaction.response.send_modal(RenameModal(self, ch))
        return True

    async def from_modal(self, interaction, ch, name):
        st = self.bot.chan(ch)
        await interaction.response.defer(thinking=True)
        _, card = await self.rename(ch, st, name)
        await interaction.followup.send(card)

    async def by_agent(self, ch, sender_ch, post):
        """`altention dc post <channel> --rename <name> --why …` (another channel's Claude, or the channel's own):
        as /rename, the card in the channel saying whose it is. (all done, the card) for the asker."""
        st = self.bot.chan(ch)
        ok, card = await self.rename(ch, st, post.get("rename") or "", by=sender_ch or post.get("from_name") or "?",
                                     why=post.get("why"))
        try:
            from . import deliver as outgoing
            await outgoing.deliver(ch, card, intent="status")
        except Exception:
            traceback.print_exc()
        return ok, card

    async def follow_discord(self, ch, st, old):
        """The channel renamed by hand in Discord: the others follow (owner 11-17, point 2 a)."""
        await self.rename(ch, st, ch.name, take=[i for i in ITEMS if i != "channel"], source="discord", old=old)

    async def follow_tmux(self, cid, name):
        """The window named by hand in tmux (the look found it): the others follow."""
        st = self.bot.channels_state.get(str(cid))
        ch = self.bot.get_channel(int(cid)) if str(cid).isdigit() else None
        if ch is None or not isinstance(st, dict):
            return
        await self.rename(ch, st, name, take=[i for i in ITEMS if i != "window"], source="tmux")


class RenameModal(discord.ui.Modal):
    """〔改名…〕: one field, the name now in it (manager 11-18: what follows as the record says)."""
    def __init__(self, renamer, ch):
        super().__init__(title=messages.rename("modal_title", channel=ch.name)[:45], timeout=900)
        self.renamer, self.ch = renamer, ch
        self.name = discord.ui.TextInput(label=messages.rename("modal_field"), default=ch.name, max_length=90)
        self.add_item(self.name)

    async def on_submit(self, interaction):
        await self.renamer.from_modal(interaction, self.ch, str(self.name.value))
