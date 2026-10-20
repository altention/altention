"""Archived channels: what !detach leaves behind, and how to bring a channel back.

!detach keeps a record in the channel's state: {"archived": {"pane", "pane_session", "home", "session_id",
"cwd"}}. Typing in an archived channel then offers two buttons instead of a flat refusal:
- 接回原本的對話: the pane it followed, if it still runs Claude and no other channel follows it; if another
  channel does, the owner is asked whether to move it here (that channel is then detached); if the pane is
  gone, the conversation is opened again in a new window of the same tmux session (--resume).
- 在這裡開新對話: the channel leaves the archive and shows the setup card, like a new one.
A pane is followed by one channel at most. /attach of a pane that an archived channel remembers brings that
channel back instead of making a new one, so the normal way never ends with two channels on one pane.

/archive (C-63) puts away a channel of any kind (put()): a conversation as !detach does, except that a window the
bot opened is closed, as the idle reaper does, and its conversation taken back later; a terminal's shell window is
closed (not while a command runs); a record channel, a board, a files channel only move. Every archived channel
remembers where it was (record: "from", the category {id, name}, None for none; "at"; "topic", the topic it had)
and its topic starts with a note saying so; taking it back puts it there again (home_category()). The archive is
the category config.ARCHIVE_CATEGORY, then "<name> 2", "<name> 3"…: a Discord category holds 50 channels (shelf()).
A conversation's state while archived is {"archived": record, "cwd"} (and a board's keys); any other kind keeps its
state as it was, with "archived" added (rec "kind").
"""
import json
import os
import re
import time
import traceback

import discord

from . import config, kinds, layout, routes, tmuxio

PREFIX = "arch"
SHELF_MAX = 50  # Discord's limit of channels in one category
LEGACY = "archive"  # the category tools/dcctl archive moved channels into before C-63: archived as well
KEEP = ("permission", "talk_limit", "talk_free")  # a conversation's own settings, back with it
STAY = ("boards", "board_fails")  # kept in an archived conversation's state: a board in it is still edited in place
NOTE = "📦 原分類：%s · 封存於 %s"
NONE, UNKNOWN = "（沒有分類）", "（不明）"
NOTE_RE = re.compile(r"^📦 原分類：(.*?) · 封存於 (\d{4}-\d{2}-\d{2})(?:\n|$)")
SYSTEM_NAMES = lambda: (config.AUDIT_CHANNEL, config.GUIDE_CHANNEL, config.UPDATE_CHANNEL, config.SETUP_CHANNEL,  # noqa: E731
                        "notifications")


LEFT_SAID = "archive-left-said.json"  # in config.STATE: the channels adopt_all() left alone and has said so (ids)


def left_said():
    """The ids (str) adopt_all() last said it left alone; empty when there is no such file or it cannot be read."""
    try:
        with open(os.path.join(config.STATE, LEFT_SAID)) as f:
            return set(str(i) for i in json.load(f))
    except (OSError, ValueError, TypeError):
        return set()


def note_left_said(ids):
    path = os.path.join(config.STATE, LEFT_SAID)
    try:
        os.makedirs(config.STATE, exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump([str(i) for i in ids], f)
        os.replace(path + ".tmp", path)
    except OSError:
        traceback.print_exc()


def record(st, why=None):
    """What an archived channel remembers, from the state it had while attached; and why it was put away (`why`:
    kinds.DETACHED, kinds.MOVED, kinds.BY_OWNER; a record of before stage 3b step 2.5 does not say). `tmux`: the
    session on the bot's own server it had (opened again there, by the channel's name, when taken back)."""
    rec = {"pane": st.get("pane"), "pane_session": st.get("pane_session"), "home": st.get("home"),
           "session_id": st.get("session_id"), "cwd": st.get("cwd")}
    if st.get("tmux"):
        rec["tmux"] = st["tmux"]
    kept = dict((k, st[k]) for k in KEEP if k in st)
    if kept:
        rec["kept"] = kept
    if why:
        rec["why"] = why
    return rec


def shelf_number(name):
    """1 for the archive category (and the old "archive"), N for "<archive> N", None for any other name."""
    if name in (config.ARCHIVE_CATEGORY, LEGACY):
        return 1
    m = re.match(r"^%s (\d+)$" % re.escape(config.ARCHIVE_CATEGORY), name or "")
    return int(m.group(1)) if m and int(m.group(1)) >= 2 else None


def is_shelf(category):
    return category is not None and shelf_number(getattr(category, "name", None)) is not None


def shelves(guild):
    """[(N, category)] of the archive categories new channels go into (not the old "archive"), by N."""
    out = [(shelf_number(c.name), c) for c in guild.categories if c.name != LEGACY or LEGACY == config.ARCHIVE_CATEGORY]
    return sorted(((n, c) for n, c in out if n is not None), key=lambda x: (x[0], x[1].position))


async def shelf(guild):
    """The archive category to put one more channel in: the first with room, else the next one made (right after
    the last, the system category kept as layout keeps it). Empty ones are left there (nothing is deleted)."""
    have = shelves(guild)
    for n, cat in have:
        if len(cat.channels) < SHELF_MAX:
            return cat
    n = have[-1][0] + 1 if have else 1
    cat = await layout.new_category(guild, config.ARCHIVE_CATEGORY if n == 1 else "%s %d" % (config.ARCHIVE_CATEGORY, n))
    if have and cat.position != have[-1][1].position + 1:
        try:
            await cat.edit(position=have[-1][1].position + 1)  # (discord.py: before the first at that position or past)
        except discord.HTTPException as e:
            print("[archive] could not put %s after %s: %s" % (cat.name, have[-1][1].name, e), flush=True)
    return cat


def day(at):
    return time.strftime("%Y-%m-%d", time.localtime(at))


def topic_with(topic, rec):
    """The topic of an archived channel: the note of where it was, then the one it had (cut to Discord's 1024)."""
    name = ((rec["from"] or {}).get("name") or NONE) if "from" in rec else UNKNOWN
    note = NOTE % (name, day(rec.get("at") or time.time()))
    return (note + ("\n" + topic if topic else ""))[:1024]


def topic_without(topic):
    return NOTE_RE.sub("", topic or "", count=1)


def noted(topic):
    """(category name or None for none, the day's time) from an archived channel's note, or None without one."""
    m = NOTE_RE.match(topic or "")
    if not m:
        return None
    try:
        at = time.mktime(time.strptime(m.group(2), "%Y-%m-%d"))
    except ValueError:
        at = None
    return (None if m.group(1) == NONE else m.group(1)), at  # (UNKNOWN: matches no category, taken back by its folder)


def where_from(ch, guild):
    """The record's "from", "at", "topic" for a channel being archived. One that is in an archive category already
    (moved there by hand: the note its topic has, if any) says where the note says; without a note "from" is left
    out, and taking it back goes by its folder."""
    cat, now = getattr(ch, "category", None), time.time()
    if not is_shelf(cat):
        return {"from": {"id": cat.id, "name": cat.name} if cat is not None else None, "at": now, "topic": ch.topic or ""}
    out = {"at": now, "topic": topic_without(ch.topic)}
    said = noted(ch.topic)
    if said:
        name, at = said
        found = next((c for c in guild.categories if c.name == name and not is_shelf(c)), None) if name else None
        if name != UNKNOWN:
            out["from"] = {"id": found.id if found is not None else None, "name": name} if name else None
        out["at"] = at or now
    return out


async def home_category(guild, rec, cwd, fallback=None):
    """Where a channel taken out of the archive goes: the category it came from (by id; one made again by the same
    name), no category if it had none; for a record that does not say (or whose category is gone): `fallback` when
    given, else the category of its folder's project, else none."""
    if "from" in rec:
        f = rec["from"]
        if f is None:
            return None
        cat = next((c for c in guild.categories if f.get("id") and c.id == f["id"]), None)
        cat = cat or next((c for c in guild.categories if c.name == f.get("name")), None)
        if cat is not None and not is_shelf(cat):
            return cat
    if fallback is not None:
        return fallback
    project = routes.project_name(cwd) if cwd else None
    return await layout.category(guild, project) if project else None


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


def view(cid, can_return=True, restore=False):
    v = discord.ui.View(timeout=None)
    if restore:  # not a conversation: put back as it was
        v.add_item(discord.ui.Button(label="恢復這個頻道", custom_id="%s:%d:restore" % (PREFIX, cid),
                                     style=discord.ButtonStyle.primary))
        return v
    if can_return:
        v.add_item(discord.ui.Button(label="接回原本的對話", custom_id="%s:%d:back" % (PREFIX, cid),
                                     style=discord.ButtonStyle.primary))
    v.add_item(discord.ui.Button(label="在這裡開新對話", custom_id="%s:%d:new" % (PREFIX, cid),
                                 style=discord.ButtonStyle.secondary))
    return v


def conversation_record(rec):
    """Is this the record of a conversation's channel (one of !detach, or of /archive of a conversation)?"""
    return (rec or {}).get("kind", kinds.CONVERSATION) == kinds.CONVERSATION


class Archive(object):
    """`bot` is the Discord bot (channels_state, chan(), save(), tmux, terminal, setup, detach(), audit())."""

    def __init__(self, bot):
        self.bot, self.offered = bot, set()

    async def offer(self, ch):
        """Someone typed in an archived channel: say so, with the ways back (once per channel per run)."""
        if ch.id in self.offered:
            return
        self.offered.add(ch.id)
        rec = (self.bot.channels_state.get(str(ch.id)) or {}).get("archived")
        if rec and not conversation_record(rec):
            v = view(ch.id, restore=True)
            await ch.send("📦 這個頻道已經封存。要恢復嗎？", view=v)
            return v.stop()
        v = view(ch.id, can_return=bool(rec and (rec.get("pane") or rec.get("session_id"))))
        await ch.send("🗄️ 這個頻道已經封存，訊息不會送給 Claude。要怎麼繼續？", view=v)
        v.stop()

    async def unarchive(self, ch, rec, cwd, fallback=None):
        """Move the channel out of the archive: where it came from (home_category()), its topic without the note.
        The category it ends in (None: none)."""
        guild = ch.guild
        category = await home_category(guild, rec or {}, cwd, fallback)
        topic = ch.topic or ""
        if rec and "topic" in rec and topic == topic_with(rec["topic"], rec):
            topic = rec["topic"]  # untouched since: the whole of it (the note may have cut its end)
        else:
            topic = topic_without(topic)
        if topic != (ch.topic or ""):
            await ch.edit(category=category, topic=topic)
        else:
            await ch.edit(category=category)
        return category

    def refused(self, ch, st):
        """Why this channel cannot be archived, or None."""
        if isinstance(ch, discord.Thread):
            return "討論串不能封存，要封存就在它的頻道裡下。"
        if st.get("archived"):
            return "這個頻道已經封存了。"
        if getattr(getattr(ch, "category", None), "name", None) == config.SYSTEM_CATEGORY or ch.name in SYSTEM_NAMES():
            return "這是機器人自己用的頻道，不能封存。"
        return None

    async def put(self, ch, force=False, adopt=False):
        """/archive: put the channel away (see the module's note). (what to say, a view or None): a conversation
        working right now is not touched unless `force` (the view asks for it). `adopt` (adopt_all(): nobody asked,
        so only what the idle reaper itself closes is closed): a session on the bot's own server is closed, a window
        in the owner's tmux (the bot's or attached) only let go of, a terminal's shell left as it is."""
        st = self.bot.chan(ch)
        why = self.refused(ch, st)
        if why:
            return why, None
        guild, kind = ch.guild, kinds.channel_kind(st)
        conversation = kind == kinds.CONVERSATION
        if conversation and (st.get("busy") or st.get("bg")) and not force:
            v = discord.ui.View(timeout=None)
            v.add_item(discord.ui.Button(label="還是封存", custom_id="%s:%d:force" % (PREFIX, ch.id),
                                         style=discord.ButtonStyle.danger))
            return "這個頻道的 Claude 正在工作（或有背景工作在跑），封存會把它中斷。要等它做完，還是現在就封存？", v
        if kind == kinds.TERMINAL and ch.id in getattr(self.bot.terminal, "running", {}):
            return "這個終端機有指令在跑，封存會把 shell 關掉。等它跑完（或按 ⏹ 停掉）再封存。", None
        place = where_from(ch, guild)
        if conversation:
            rec = dict(record(st, kinds.ADOPTED if adopt else kinds.BY_OWNER), **place)
            attached = bool(st.get("pane") and not st.get("home"))
            where = tmuxio.label(st["pane"]) if attached else None
            host = (st.get("pane") or {}).get("host")
            kill = not st.get("pane") if adopt else not attached
            await self.bot.tmux.close(ch, kill=kill)  # a window the bot opened: closed as the idle reaper does
            await routes.unbind_any(ch.id, host)
            kept = dict((k, st[k]) for k in STAY if k in st)
            self.bot.channels_state[str(ch.id)] = dict(kept, archived=rec, cwd=st.get("cwd"))
            said = ("你接上的對話還在原處繼續跑（%s），機器人不再跟著它" % where if attached
                    else "Claude 的視窗關掉了，對話留著" if rec.get("session_id") else "這個頻道還沒有對話")
        else:
            rec = dict(place, why=kinds.ADOPTED if adopt else kinds.BY_OWNER, kind=kind)
            said = ""
            if kind == kinds.TERMINAL and st.get("pane") and not adopt:
                gone = st.pop("pane")
                if await tmuxio.exists(gone):
                    await tmuxio.kill_pane(gone)
                said = "shell 視窗關掉了（指令紀錄留著），"
            elif st.get("plain"):
                said = "插件和 altention dc post 貼來的訊息照樣會貼進來，"
            st["archived"] = rec
        self.bot.save()
        self.offered.discard(ch.id)
        try:
            here = getattr(ch, "category", None)  # in an archive category already (moved there by hand): it stays there
            cat = here if is_shelf(here) and here.name != LEGACY else await shelf(guild)
            topic = topic_with(place.get("topic") or "", rec)
            if cat is here:
                if topic != (ch.topic or ""):
                    await ch.edit(topic=topic)
            else:
                await ch.edit(category=cat, topic=topic)
        except discord.HTTPException as e:
            await self.bot.audit("封存 #%s：沒有移到封存分類（%s）" % (ch.name, e))
            return "已封存（%s），但頻道沒有移到封存分類：%s" % (said or "記下來了", e), None
        await self.bot.audit("%s #%s（原分類：%s）→ 「%s」" % ("補記封存" if adopt else "封存", ch.name,
                                                       (rec.get("from") or {}).get("name") or (NONE if "from" in rec else UNKNOWN), cat.name))
        if conversation:
            back = "在這裡打字可以接回原本的對話，或開新對話" if rec.get("pane") or rec.get("session_id") else "在這裡打字可以開新對話"
            return "📦 已封存：%s。頻道移到「%s」，%s，會搬回原本的分類。" % (said, cat.name, back), None
        return "📦 已封存：%s頻道移到「%s」。在這裡打字可以恢復，會搬回原本的分類。" % (said, cat.name), None

    async def adopt_all(self, guild):
        """At every start (C-63): a channel in an archive category without a record (moved there by hand: the owner's
        batch of 2026-10-20 with dcctl move and a note written in its topic) gets one, as /archive would make it,
        its category taken from the note (put(adopt=True)); one in the old "archive" category moves into "封存" (50 to
        a category). Left as it is, and listed: a conversation at work or spoken to within IDLE_HOURS (the owner may
        be using it there). What was done is said in #audit, in one line (each channel's own line comes from put());
        nothing when nothing was adopted or moved and no channel left alone is one not said before (LEFT_SAID keeps
        those said: the same list again at every start was noise, manager 2026-10-20). The "archive" category
        emptied is said, never deleted."""
        idle, adopted, from_legacy, moved, left, kept_open = config.IDLE_HOURS * 3600, [], [], [], [], []
        legacy = [c for c in guild.categories if c.name == LEGACY and LEGACY != config.ARCHIVE_CATEGORY]
        for cat in sorted((c for c in guild.categories if is_shelf(c)), key=lambda c: c.position):
            for ch in list(cat.text_channels):
                st = self.bot.channels_state.get(str(ch.id)) or {}
                try:
                    if st.get("archived"):
                        if cat in legacy:  # put away before, then moved by hand: only the category changes
                            await ch.edit(category=await shelf(guild))
                            moved.append(ch.name)
                        continue
                    if kinds.channel_kind(st) == kinds.CONVERSATION and (
                            st.get("busy") or st.get("bg") or time.time() - st.get("last_active", 0) <= idle):
                        left.append((ch.id, ch.name))
                        continue
                    if st.get("pane") and kinds.channel_kind(st) == kinds.CONVERSATION:
                        kept_open.append(ch.name)
                    await self.put(ch, adopt=True)
                    adopted.append(ch.name)
                    if cat in legacy:
                        from_legacy.append(ch.name)
                except Exception as e:
                    traceback.print_exc()
                    left.append((ch.id, "%s（%s）" % (ch.name, e)))
        said = left_said()
        new_left = [i for i, _ in left if str(i) not in said]
        note_left_said([i for i, _ in left])
        if not (adopted or moved or new_left):
            return
        parts = ["整理封存分類：補記 %d 個%s" % (len(adopted), "（其中 %d 個是從「%s」搬進來的）" % (len(from_legacy), LEGACY)
                                              if from_legacy else "")]
        if moved:
            parts.append("已經有封存紀錄、從「%s」搬進「%s」的 %d 個" % (LEGACY, config.ARCHIVE_CATEGORY, len(moved)))
        if kept_open:
            parts.append("視窗還開著、只是不再跟著的：%s（要關的話在 tmux 裡關，或接回後 /reset）" % "、".join("#" + n for n in kept_open))
        if left:
            parts.append("還在用（%d 小時內有說話或正在跑），這次沒動：%s" % (config.IDLE_HOURS, "、".join("#" + n for _, n in left)))
        if (moved or from_legacy) and legacy and not any(c.channels for c in legacy):
            parts.append("「%s」分類已經空了，沒有刪；要刪的話跟 Claude 說" % LEGACY)
        await self.bot.audit("；".join(parts))

    async def restore(self, interaction, ch, st):
        """「恢復這個頻道」: a channel that is not a conversation, back where it was, its state as before."""
        rec = st.pop("archived", None) or {}
        self.bot.save()
        await interaction.response.edit_message(content="📦 恢復中…", view=None)
        cat = await self.unarchive(ch, rec, st.get("cwd"))
        self.offered.discard(ch.id)
        await ch.send("📦 已恢復，頻道搬回「%s」。" % (cat.name if cat is not None else NONE))

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
        rec = st.get("archived") or {}
        try:
            if what == "force":  # /archive of a channel at work: the owner says go
                await interaction.response.edit_message(content="📦 封存中…", view=None)  # (closing can take longer than Discord waits)
                text, _ = await self.put(ch, force=True)
                await ch.send(text)
                return True
            if what == "restore":
                if not st.get("archived") or conversation_record(rec):
                    await interaction.response.edit_message(content="這個頻道已經不在封存了。", view=None)
                    return True
                await self.restore(interaction, ch, st)
                return True
            if st.get("pane") or st.get("tmux") or st.get("home"):
                await interaction.response.edit_message(content="這個頻道已經在用了。", view=None)
                return True
            if what == "new":
                await interaction.response.edit_message(content="🗄️ 這個頻道會開新的對話。", view=None)
                self.bot.channels_state.pop(str(ch.id), None)
                await self.unarchive(ch, rec, rec.get("cwd"))
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
        st.update(rec.get("kept") or {})
        if rec.get("session_id"):
            st["session_id"] = rec["session_id"]
        if alive:
            st.update({"pane": pane, "pane_session": rec.get("pane_session")})
            if rec.get("home"):
                st["home"] = rec["home"]
            else:
                await routes.bind_any(pane, ch.id)
            self.bot.save()
            await self.unarchive(ch, rec, st["cwd"])
            self.offered.discard(ch.id)
            return await ch.send("🔗 已接回原本的對話（%s）。" % tmuxio.label(pane))
        if not rec.get("session_id"):
            self.bot.channels_state.pop(str(ch.id), None)
            self.bot.save()
            return await ch.send("原本的對話已經結束，也沒有記下對話 ID，接不回來。可以按「在這裡開新對話」。")
        if rec.get("tmux") and not rec.get("home") and not pane:  # a session on the bot's own server (/archive): opened
            # again there, as after the idle reaper, resuming the conversation
            self.bot.save()
            await self.unarchive(ch, rec, st["cwd"])
            self.offered.discard(ch.id)
            await ch.send("🔗 接回原本的對話中…")
            if await self.bot.tmux.start_now(ch):
                await ch.send("🔗 已接回原本的對話，可以在這裡繼續。")
            return
        # the window is gone: open the conversation again, in a new window of the same tmux session, on the machine
        # it was on (a pane of another machine's: its tmux there, where the conversation's file is; until 2026-10-14
        # one was opened on the bot's machine, which has no such conversation)
        far = (pane or {}).get("host")
        st["home"] = rec.get("home") or dict({"socket": pane.get("socket") if far else tmuxio.user_socket(), "window": "",
                                              "session": rec.get("pane_session") or tmuxio.safe_session(ch.name)},
                                             **({"host": far} if far else {}))
        self.bot.save()
        await self.unarchive(ch, rec, st["cwd"])
        self.offered.discard(ch.id)
        where = st["home"].get("host")
        await ch.send("🔗 原本的視窗已經不在了，在%s tmux `%s` 開新視窗接回同一個對話…" % (" %s 的" % where if where else "", st["home"]["session"]))
        if await self.bot.tmux.start_now(ch):  # (not opened: start_now has said why)
            # said when it is there: "opening…" alone, and the owner took a window that had opened and taken the
            # conversation back for one that had failed (manager 2026-10-14, #real-stochastic-0)
            await ch.send("🔗 已接回原本的對話（%stmux `%s` 的新視窗），可以在這裡繼續。" % ("%s 的 " % where if where else "", st["home"]["session"]))
