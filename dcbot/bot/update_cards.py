"""#update's cards (C-57 D2; the writer is altention/dc/card.py, the same fields). A card is one message kept up to
date in place (same card id, same message; posted again only when Discord says it is gone), with buttons that act on
a fixed set of actions, never a command the post names: anything on this machine can write the spool, and a button
the owner presses must do what the bot knows it does.

  update_go   "vX.Y.Z": the buttons go first (one press), then `bin/altention update go <v> --card <id>` runs in a
              session of its own (setsid: the update restarts this bot), output appended to
              ~/.local/state/altention/update-go.log; what it does next it tells through card.show()
  later       the card says it was put off; buttons away (nothing written for the engine: it tells a version once)
  details     the card's details posted under it (a file when long); the card stays
  ask_claude  the text handed to the setup channel's Claude (SETUP_CHANNEL; a link on the card); no such channel: the
              text posted as a file with what to do with it; over ASK_MAX characters: not sent, said on the card

The cards live in state/update-cards/<id>.json (channel, message, text, buttons, details), so a press works after a
restart, including the one the update itself causes. Only the owner presses (as every button here). A card post that
fails (Discord down a moment) is kept and tried again like a notification (posts.safely, retry); each post carries
when it was written, and one older than what the card shows is skipped, so a late retry never puts back old words."""
import asyncio
import io
import json
import os
import re
import subprocess
import time
import traceback

import discord

from . import config, posts

PREFIX = "upd"
FOLDER = os.path.join(config.STATE, "update-cards")
ID = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
VERSION = re.compile(r"^v\d+\.\d+\.\d+$")
ACTIONS = ("update_go", "later", "details", "ask_claude")
STYLES = {"primary": discord.ButtonStyle.primary, "secondary": discord.ButtonStyle.secondary,
          "danger": discord.ButtonStyle.danger}
TEXT_MAX, ASK_MAX = 1800, 6000
ENGINE = os.path.dirname(config.ROOT)


def path(card_id):
    return os.path.join(FOLDER, card_id + ".json")


def load(card_id):
    if not ID.match(card_id or ""):
        return None
    try:
        with open(path(card_id)) as f:
            got = json.load(f)
        return got if isinstance(got, dict) else None
    except (OSError, ValueError):
        return None


def save(card_id, card):
    os.makedirs(FOLDER, exist_ok=True)
    tmp = path(card_id) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(card, f, ensure_ascii=False)
    os.replace(tmp, path(card_id))


def usable(buttons):
    """The buttons a card shows: only those whose action the bot knows, with a version for update_go; (kept, dropped)."""
    kept, dropped = [], []
    for b in buttons or []:
        ok = isinstance(b, dict) and isinstance(b.get("id"), str) and re.match(r"^[A-Za-z0-9._-]{1,20}$", b["id"]) \
            and isinstance(b.get("label"), str) and b["label"].strip() and b.get("action") in ACTIONS \
            and (b["action"] != "update_go" or (isinstance(b.get("arg"), str) and VERSION.match(b["arg"])))
        (kept if ok else dropped).append(b)
    return kept[:5], dropped


def view(card_id, buttons):
    if not buttons:
        return None
    v = discord.ui.View(timeout=None)
    for b in buttons:
        v.add_item(discord.ui.Button(label=b["label"][:80], custom_id="%s:%s:%s" % (PREFIX, card_id, b["id"]),
                                     style=STYLES.get(b.get("style"), discord.ButtonStyle.secondary)))
    return v


def body(card):
    text = (card.get("text") or "").strip() or "（沒有內容）"
    notes = "".join("\n" + n for n in card.get("notes") or [])
    whole = text + notes
    return whole if len(whole) <= 1990 else whole[:1960].rstrip() + "\n…（太長，後面截掉了）"


def log_path():
    return os.path.join(os.path.expanduser("~/.local/state/altention"), "update-go.log")


def start_update(version, card_id):
    """bin/altention update go <version> --card <card_id> in a session of its own (it outlives this bot, which the
    update restarts): stdin from /dev/null, output appended to update-go.log. The pid, or raises OSError."""
    os.makedirs(os.path.dirname(log_path()), exist_ok=True)
    with open(log_path(), "a") as log:
        log.write("%s 按下「更新」：%s（卡片 %s）\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), version, card_id))
        log.flush()
        proc = subprocess.Popen([os.path.join(ENGINE, "bin", "altention"), "update", "go", version, "--card", card_id],
                                cwd=ENGINE, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
    return proc.pid


class Cards(object):
    def __init__(self, bot):
        self.bot = bot
        self.locks = {}  # card id -> asyncio.Lock: a post and a press on one card one at a time

    def lock(self, card_id):
        return self.locks.setdefault(card_id, asyncio.Lock())

    async def target(self, post, card):
        """The channel of a card: where it already is; else the channel the post names; else (not there) the default
        notification channel, with a line saying where it was meant to go."""
        if card.get("channel"):
            ch = self.bot.get_channel(int(card["channel"]))
            if ch is not None:
                return ch, ""
        ch = await self.bot.posts.channel(post.get("channel") or config.UPDATE_CHANNEL, None, create=False)
        if ch is not None:
            return ch, ""
        ch, note = await self.bot.posts.notify_channel(dict(post, notify=True))
        return ch, note or "（#%s 不存在，改貼在這裡）" % str(post.get("channel") or config.UPDATE_CHANNEL).lstrip("#")

    async def show(self, post):
        """A card from the spool (altention/dc/card.py): posted, or its message edited in place."""
        c = post.get("card") or {}
        card_id = str(c.get("id") or "")
        if not ID.match(card_id):
            return await self.bot.audit("卡片的 id 不能用：%r，沒有貼" % card_id[:60])
        async with self.lock(card_id):
            card = load(card_id) or {}
            at = c.get("at") if type(c.get("at")) in (int, float) else None
            if at is not None and type(card.get("shown_at")) in (int, float) and at < card["shown_at"]:
                print("[cards] %s: a post older than what is shown (a retry come late), skipped" % card_id, flush=True)
                return
            if c.get("text") is not None:
                card["text"], card["notes"] = str(c["text"])[:TEXT_MAX + 100], []
            if c.get("close"):
                card["buttons"] = []
            else:
                card["buttons"], dropped = usable(c.get("buttons"))
                if dropped:
                    await self.bot.audit("卡片 %s 有 %d 顆按鈕機器人不認得（action 或欄位不對），沒有顯示" % (card_id, len(dropped)))
            if "details" in c and not c.get("close"):
                card["details"] = c.get("details")
            ch, note = await self.target(post, card)
            if ch is None:
                return await self.bot.audit("卡片 %s 找不到可以貼的頻道" % card_id)
            if note and note not in (card.get("notes") or []):
                card.setdefault("notes", []).insert(0, note.strip())
            if at is not None:
                card["shown_at"] = at
            await self.put(card_id, card, ch)

    async def put(self, card_id, card, ch=None):
        """Show `card` as it is now: its message edited, or posted (first time, or deleted); saved with its IDs."""
        ch = ch or self.bot.get_channel(int(card["channel"]))
        content, v = body(card), view(card_id, card.get("buttons"))
        msg = None
        if card.get("message") and str(card.get("channel")) == str(ch.id):
            try:
                msg = await ch.get_partial_message(int(card["message"])).edit(content=content, view=v)
                msg = msg or card["message"]
            except discord.NotFound:
                msg = None
        if msg is None:
            sent = await ch.send(content, view=v)
            card["message"] = sent.id
        if v is not None:
            v.stop()
        card["channel"], card["at"] = ch.id, time.time()
        save(card_id, card)

    async def on_interaction(self, interaction):
        if interaction.type != discord.InteractionType.component:
            return False
        parts = ((interaction.data or {}).get("custom_id") or "").split(":")
        if len(parts) != 3 or parts[0] != PREFIX:
            return False
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return True
        card_id, bid = parts[1], parts[2]
        async with self.lock(card_id):
            card = load(card_id)
            b = next((x for x in (card or {}).get("buttons") or [] if x.get("id") == bid), None)
            if card is None or b is None:  # an old row of buttons (the card changed since): nothing done
                await interaction.response.edit_message(view=None) if card is None else \
                    await interaction.response.send_message("這顆按鈕已經換掉了，看卡片現在的樣子。", ephemeral=True)
                return True
            await interaction.response.defer()
            try:
                await getattr(self, "do_" + b["action"])(interaction, card_id, card, b)
            except Exception as e:
                traceback.print_exc()
                card.setdefault("notes", []).append("❌ 按「%s」時出錯了（%s），沒有做完。" % (b["label"], str(e)[:200]))
                await self.put(card_id, card)
            await self.bot.audit("#%s 卡片 %s：按了「%s」" % (getattr(interaction.channel, "name", "?"), card_id, b["label"]))
        return True

    async def do_update_go(self, interaction, card_id, card, b):
        card["buttons"] = []  # taken away first: one press is one update (two at once: update go's own lock)
        card.setdefault("notes", []).append("⏳ 開始更新…（進度會寫在這張卡上）")
        await self.put(card_id, card)
        try:
            start_update(b["arg"], card_id)
        except OSError as e:
            card["notes"][-1] = "❌ 更新沒有開始：%s。可以跟 Claude 說「更新引擎」。" % e
            await self.put(card_id, card)

    async def do_later(self, interaction, card_id, card, b):
        card["buttons"] = []
        card.setdefault("notes", []).append("之後再說。想更新的時候，跟 Claude 說「更新引擎」就可以。")
        await self.put(card_id, card)

    async def do_details(self, interaction, card_id, card, b):
        text = (card.get("details") or "").strip() or "（沒有更多說明）"
        ch = self.bot.get_channel(int(card["channel"]))
        if len(text) <= 1900:
            await ch.send(text, reference=discord.MessageReference(message_id=int(card["message"]), channel_id=ch.id,
                                                                   fail_if_not_exists=False))
        else:
            await ch.send("要做的事（完整的在附檔）：\n" + text[:600] + "…",
                          file=discord.File(io.BytesIO(text.encode("utf-8")), "details.txt"))

    async def do_ask_claude(self, interaction, card_id, card, b):
        text = (b.get("arg") or "").strip()
        notes = card.setdefault("notes", [])
        if len(text) > ASK_MAX:
            notes.append("❌ 交給 Claude 的內容太長（%d 字），沒有送出。" % len(text))
            return await self.put(card_id, card)
        guild = self.bot.get_guild(config.GUILD_ID)
        named = [c for c in getattr(guild, "text_channels", []) if c.name == config.SETUP_CHANNEL]
        setup = next((c for c in named if (self.bot.channels_state.get(str(c.id)) or {}).get("configured")), None)
        if setup is None and not named and hasattr(self.bot, "open_setup"):  # an older installation: #setup opened for
            setup = await self.bot.open_setup(guild, first=None)  # this (C-57 D3), without the setup skill's first line
        card["buttons"] = [x for x in card.get("buttons") or [] if x.get("id") != b["id"]]
        if setup is not None:
            await self.bot.tmux.submit(posts.Origin(setup), text)
            notes.append("已經交給 <#%d> 的 Claude，請到那裡跟它一起處理。" % setup.id)
            return await self.put(card_id, card)
        ch = self.bot.get_channel(int(card["channel"]))
        await ch.send("把附檔交給任一頻道的 Claude（例如說「請讀這個檔，照 update skill 處理」），它會帶你處理。",
                      file=discord.File(io.BytesIO(text.encode("utf-8")), "for-claude.txt"))
        notes.append(("#%s 頻道還沒有可以交給的 Claude" if named else "還沒有 #%s 頻道") % config.SETUP_CHANNEL
                     + "：要交給 Claude 的內容貼在下面的附檔。")
        await self.put(card_id, card)
