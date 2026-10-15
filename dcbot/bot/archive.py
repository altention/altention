"""Archived channels: what !detach leaves behind, and how to bring a channel back.

!detach keeps a record in the channel's state: {"archived": {"pane", "pane_session", "home", "session_id",
"cwd"}}. Typing in an archived channel then offers two buttons instead of a flat refusal:
- 接回原本的對話: the pane it followed, if it still runs Claude and no other channel follows it; if another
  channel does, the owner is asked whether to move it here (that channel is then detached); if the pane is
  gone, the conversation is opened again in a new window of the same tmux session (--resume).
- 在這裡開新對話: the channel leaves the archive and shows the setup card, like a new one.
A pane is followed by one channel at most. /attach of a pane that an archived channel remembers brings that
channel back instead of making a new one, so the normal way never ends with two channels on one pane.
"""
import traceback

import discord

from . import config, kinds, routes, tmuxio

PREFIX = "arch"


def record(st, why=None):
    """What an archived channel remembers, from the state it had while attached; and why it was put away (`why`:
    kinds.DETACHED, kinds.MOVED; a record of before stage 3b step 2.5 does not say)."""
    rec = {"pane": st.get("pane"), "pane_session": st.get("pane_session"), "home": st.get("home"),
           "session_id": st.get("session_id"), "cwd": st.get("cwd")}
    if why:
        rec["why"] = why
    return rec


def same_pane(a, b):
    return bool(a and b) and a.get("host") == b.get("host") and a.get("socket") == b.get("socket") and \
        a.get("id") == b.get("id") and \
        (not a.get("pid") or not b.get("pid") or str(a["pid"]) == str(b["pid"]))


def remembered_by(channels_state, pane):
    """The archived channel (id) that remembers this pane, if any."""
    for cid, st in channels_state.items():
        if same_pane((st.get("archived") or {}).get("pane"), pane):
            return int(cid)
    return None


def followed_by(channels_state, pane, besides=None):
    """The channel (id) following this pane right now, if any."""
    for cid, st in channels_state.items():
        if int(cid) != besides and same_pane(st.get("pane"), pane):
            return int(cid)
    return None


def view(cid, can_return=True):
    v = discord.ui.View(timeout=None)
    if can_return:
        v.add_item(discord.ui.Button(label="接回原本的對話", custom_id="%s:%d:back" % (PREFIX, cid),
                                     style=discord.ButtonStyle.primary))
    v.add_item(discord.ui.Button(label="在這裡開新對話", custom_id="%s:%d:new" % (PREFIX, cid),
                                 style=discord.ButtonStyle.secondary))
    return v


class Archive(object):
    """`bot` is the Discord bot (channels_state, chan(), save(), tmux, setup, detach(), audit())."""

    def __init__(self, bot):
        self.bot, self.offered = bot, set()

    async def offer(self, ch):
        """Someone typed in an archived channel: say so, with the ways back (once per channel per run)."""
        if ch.id in self.offered:
            return
        self.offered.add(ch.id)
        rec = (self.bot.channels_state.get(str(ch.id)) or {}).get("archived")
        v = view(ch.id, can_return=bool(rec and (rec.get("pane") or rec.get("session_id"))))
        await ch.send("🗄️ 這個頻道已經封存，訊息不會送給 Claude。要怎麼繼續？", view=v)
        v.stop()

    async def unarchive(self, ch, cwd):
        """Move the channel out of the archive, into the category of its project."""
        project = routes.project_name(cwd) if cwd else None
        guild = ch.guild
        category = None
        if project:
            category = discord.utils.get(guild.categories, name=project) or await guild.create_category(project)
        await ch.edit(category=category)

    async def on_interaction(self, interaction):
        if interaction.type != discord.InteractionType.component:
            return False
        parts = ((interaction.data or {}).get("custom_id") or "").split(":")
        if len(parts) != 3 or parts[0] != PREFIX:
            return False
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return True
        ch, what = interaction.channel, parts[2]
        st = self.bot.channels_state.get(str(ch.id)) or {}
        if st.get("pane") or st.get("tmux") or st.get("home"):
            await interaction.response.edit_message(content="這個頻道已經在用了。", view=None)
            return True
        rec = st.get("archived") or {}
        try:
            if what == "new":
                await interaction.response.edit_message(content="🗄️ 這個頻道會開新的對話。", view=None)
                self.bot.channels_state.pop(str(ch.id), None)
                await self.unarchive(ch, rec.get("cwd"))
                self.offered.discard(ch.id)
                await self.bot.setup.offer(ch)
            elif what in ("back", "move"):
                await self.back(interaction, ch, rec, move=what == "move")
        except Exception as e:
            traceback.print_exc()
            try:
                await interaction.followup.send("出錯了：%s" % e, ephemeral=True)
            except Exception:
                pass
        return True

    async def back(self, interaction, ch, rec, move=False):
        pane = rec.get("pane")
        how = await tmuxio.standing(pane) if pane else "gone"
        if how == "unknown":  # its machine does not answer: the window may be there and running. Taken for gone, a new
            # window was opened on the same conversation. Nothing is changed; the card stays as it is.
            from . import remote
            return await interaction.response.send_message(
                "⚠️ %s，所以不知道原本的視窗還在不在，沒有接回。等那台連得上之後再按一次。" % remote.unreachable(pane.get("host")), ephemeral=True)
        alive = how == "alive"
        other = followed_by(self.bot.channels_state, pane, besides=ch.id) if alive else None
        other_ch = self.bot.get_channel(other) if other else None
        if other_ch is not None and not move:
            v = discord.ui.View(timeout=None)
            v.add_item(discord.ui.Button(label="移到這裡", custom_id="%s:%d:move" % (PREFIX, ch.id),
                                         style=discord.ButtonStyle.danger))
            await interaction.response.send_message(
                "這個對話現在接在 <#%d>。要把它移到這個頻道嗎？<#%d> 會被卸下並封存（對話本身不受影響）。" % (other, other),
                view=v, ephemeral=True)
            return v.stop()
        await interaction.response.edit_message(content="🗄️ 接回中…", view=None)
        if other_ch is not None:  # move: the channel that has it now lets go first
            await self.bot.detach(other_ch, self.bot.chan(other_ch), kinds.MOVED)
        st = self.bot.channels_state.setdefault(str(ch.id), {})
        st.clear()
        st.update({"cwd": rec.get("cwd") or self.bot.chan(ch)["cwd"], "mode": "tmux", "configured": True})
        if rec.get("session_id"):
            st["session_id"] = rec["session_id"]
        if alive:
            st.update({"pane": pane, "pane_session": rec.get("pane_session")})
            if rec.get("home"):
                st["home"] = rec["home"]
            else:
                await routes.bind_any(pane, ch.id)
            self.bot.save()
            await self.unarchive(ch, st["cwd"])
            self.offered.discard(ch.id)
            return await ch.send("🔗 已接回原本的對話（%s）。" % tmuxio.label(pane))
        if not rec.get("session_id"):
            self.bot.channels_state.pop(str(ch.id), None)
            self.bot.save()
            return await ch.send("原本的對話已經結束，也沒有記下對話 ID，接不回來。可以按「在這裡開新對話」。")
        # the window is gone: open the conversation again, in a new window of the same tmux session, on the machine
        # it was on (a pane of another machine's: its tmux there, where the conversation's file is; until 2026-10-14
        # one was opened on the bot's machine, which has no such conversation)
        far = (pane or {}).get("host")
        st["home"] = rec.get("home") or dict({"socket": pane.get("socket") if far else tmuxio.user_socket(), "window": "",
                                              "session": rec.get("pane_session") or tmuxio.safe_session(ch.name)},
                                             **({"host": far} if far else {}))
        self.bot.save()
        await self.unarchive(ch, st["cwd"])
        self.offered.discard(ch.id)
        where = st["home"].get("host")
        await ch.send("🔗 原本的視窗已經不在了，在%s tmux `%s` 開新視窗接回同一個對話…" % (" %s 的" % where if where else "", st["home"]["session"]))
        if await self.bot.tmux.start_now(ch):  # (not opened: start_now has said why)
            # said when it is there: "opening…" alone, and the owner took a window that had opened and taken the
            # conversation back for one that had failed (manager 2026-10-14, #real-stochastic-0)
            await ch.send("🔗 已接回原本的對話（%stmux `%s` 的新視窗），可以在這裡繼續。" % ("%s 的 " % where if where else "", st["home"]["session"]))
