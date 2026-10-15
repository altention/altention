"""Claude Code's own on-screen menus, answered with Discord buttons.

Some dialogs have no hook the bot can answer through: permission prompts, the plan dialog in a session that
started before the plan hook existed, a picker opened by a slash command. Claude Code announces them with a
Notification event. The bot then reads the menu off the screen and posts it with one button per numbered
option (pressing a number in a Claude Code menu picks that option), plus "type text…" (for options that turn
into a text box, like "tell Claude what to change"), Enter and Escape. After a click the message shows the
screen again, or the buttons go away once the dialog is gone. Buttons belong to one menu only; a stale one
does nothing.
"""
import asyncio
import re
import time
import traceback

import discord

from altention.connectors.claude_code import panels, screen

from . import awaiting, config, messages, tmuxio
from .util import target

PREFIX = "key"
MOVES = {"previous": "上一個", "next": "下一個"}  # the connector names a list's moves; the words are the bot's


DIALOG_TOP = re.compile(r"^\s*(─{20,}|▔{20,})")  # the full-width rule a dialog is drawn under (a permission's, /model's; the last cell may be drawn otherwise)


def asked(view):
    """What a dialog on the screen `view` asks, to tell one question from the next (F3): its rows from the rule it is
    drawn under down to its first option (a permission's title and the command or file, another menu's title and
    words). What runs above it (output, timers, spinners) is not part of it. No such rule: the three rows above the
    first option."""
    rows = view.rstrip("\n").split("\n")
    first = [i for i, r in enumerate(rows) if (lambda m: m is not None and m.group(1) == "1")(panels.OPTION.match(r))]
    if not first:
        return ""
    first = first[-1]
    top = [i for i in range(first) if DIALOG_TOP.match(rows[i])]
    part = rows[top[-1] + 1:first] if top else rows[max(0, first - 3):first]
    return "\n".join(r.strip() for r in part if r.strip() and not panels.MENU_RULE.match(r))


def panel_of(view, has_box):
    """panels.parse_panel(), with the moves of a list worded for the owner."""
    content = panels.parse_panel(view, has_box)
    for action in (content or {}).get("actions", []):
        if action.get("move") in MOVES:
            action["label"] = "%s %s" % (action["label"], MOVES[action["move"]])
    return content
def cut(text, n):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def body(title, excerpt, note=""):
    return ("🔔 **%s**\n```\n%s\n```%s" % (title, excerpt.replace("```", "'''")[-1500:] or "（畫面是空的）", note))[:1990]


class TypeModal(discord.ui.Modal):
    """"輸入文字…": text typed into the terminal, then Enter."""
    def __init__(self, menus, token):
        super().__init__(title="打字送進終端機", timeout=1800)
        self.menus, self.token = menus, token
        self.text = discord.ui.TextInput(label="要輸入的內容", style=discord.TextStyle.paragraph, max_length=1500)
        self.add_item(self.text)

    async def on_submit(self, interaction):
        await self.menus.act(interaction, self.token, "text", str(self.text.value))


class Menus(object):
    """`bridge` is the TmuxBridge (for host, target lookup); menus live in memory: a restart leaves old
    buttons dead, and /screen and /key still work."""

    def __init__(self, bridge):
        self.bridge, self.open = bridge, awaiting.Shown()  # token -> {"channel", "msg", "title", "since"[, "actions"]}
        awaiting.menus = self.open  # what is shown waits for the owner: state/waiting-on-owner.json follows this dict
        self.locks = {}  # token -> asyncio.Lock, one click at a time on a panel

    def view(self, token, options):
        v = discord.ui.View(timeout=None)
        for n, label in options[:9]:
            v.add_item(discord.ui.Button(label=cut("%d. %s" % (n, label), 80), custom_id="%s:%s:%d" % (PREFIX, token, n),
                                         style=discord.ButtonStyle.primary if n == 1 else discord.ButtonStyle.secondary))
        for what, label in (("text", "輸入文字…"), ("enter", "Enter"), ("esc", "Esc")):
            v.add_item(discord.ui.Button(label=label, custom_id="%s:%s:%s" % (PREFIX, token, what),
                                         style=discord.ButtonStyle.secondary))
        return v

    def panel_view(self, token, actions):
        """Buttons for a panel's actions (panels.parse_panel): exactly the keys its hint line names."""
        v = discord.ui.View(timeout=None)
        for a in actions:  # the key itself in the id, not a position: checked against the screen when pressed
            v.add_item(discord.ui.Button(label=cut(a["label"], 80), custom_id="%s:%s:k%s" % (PREFIX, token, a["keys"][0]),
                                         style=discord.ButtonStyle.secondary))
        return v

    async def show_panel(self, ch, t, send, title):
        """A slash command left a panel open: post its screen with one button per action its hint line names
        (none named: just the screen, and /key). `send(text, view=None)` posts and returns the message."""
        view = await tmuxio.raw_screen(t)
        content = panel_of(view, screen.has_prompt(view))
        if content is None:
            return False
        await self.resolve(ch)
        token = "%d" % (time.time() * 1000)
        note = "" if content["actions"] else "\n-# 看不出這個面板接受哪些按鍵：用 `/key` 送按鍵，或直接發下一則訊息（會自動關掉）"
        v = self.panel_view(token, content["actions"]) if content["actions"] else None
        try:
            msg = await send(body(title, content["text"], note), view=v)
        except Exception:
            traceback.print_exc()
            return False
        if v is not None:
            v.stop()
            self.open[token] = {"channel": ch.id, "msg": getattr(msg, "id", None), "title": title,
                                "actions": content["actions"]}
        return True

    async def post(self, ch, st, ev):
        """A Notification: Claude Code wants the owner. Show the dialog with buttons."""
        t = target(st)
        title = cut(ev.get("message") or "Claude 需要你注意", 200)
        if not t or not await tmuxio.alive(t):
            far = self.bridge.unanswered(ch)  # its machine did not answer: the dialog is there, only it cannot be read
            return await self.bridge.say(ch, "🔔 %s%s" % (title, messages.far("menu", said=far) if far else ""))
        await asyncio.sleep(0.5)  # the dialog finishes drawing just after the notification
        view = await tmuxio.raw_screen(t)
        excerpt, options = panels.parse_menu(view)
        await self.resolve(ch)
        token = "%d" % (time.time() * 1000)
        try:
            v = self.view(token, options)
            msg = await ch.send(body(title, excerpt, "" if options else "\n-# 看不出選項：用下面的按鍵，或 `/key` 送其他按鍵"), view=v)
            v.stop()
        except Exception:
            traceback.print_exc()
            return
        self.open[token] = {"channel": ch.id, "msg": msg.id, "title": title, "shown": [asked(view), options]}

    async def resolve(self, ch):
        """The session moved on: drop the buttons of whatever menu is still shown in this channel."""
        for token, m in list(self.open.items()):
            if m["channel"] != ch.id:
                continue
            self.open.pop(token, None)
            try:
                await ch.get_partial_message(m["msg"]).edit(view=None)
            except Exception:
                pass

    def gone(self, ch):
        """The channel was deleted: its menus went with it (nothing to edit), and wait for nobody."""
        for token, m in list(self.open.items()):
            if m["channel"] == ch.id:
                self.open.pop(token, None)
                self.locks.pop(token, None)

    async def on_interaction(self, interaction):
        """A click on one of our buttons. Returns True if it was ours."""
        if interaction.type != discord.InteractionType.component:
            return False
        parts = ((interaction.data or {}).get("custom_id") or "").split(":")
        if len(parts) != 3 or parts[0] != PREFIX:
            return False
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return True
        token, what = parts[1], parts[2]
        if token not in self.open:
            await interaction.response.edit_message(view=None)
            return True
        if what == "text":
            await interaction.response.send_modal(TypeModal(self, token))
            return True
        await self.act(interaction, token, what)
        return True

    async def act(self, interaction, token, what, text=""):
        m = self.open.get(token)
        st = self.bridge.host.chan(interaction.channel)
        t = target(st)
        alive = bool(m is not None and t and await tmuxio.alive(t))
        if not alive and m is not None and tmuxio.unanswered(t):
            # asked and not answered: the dialog may well be open still. The buttons stay; nothing was pressed.
            return await interaction.response.send_message(
                messages.far("button", said=self.bridge.unanswered(interaction.channel)), ephemeral=True)
        if not alive:
            return await interaction.response.edit_message(view=None)
        await interaction.response.defer()
        if what != "esc" and not what.startswith("k"):
            # A key onto a dialog goes only onto the dialog the buttons were made for: answered at the terminal, timed
            # out, or another question in its place, a number pressed now answers that other one (or lands in the input
            # box). Read again: the same choices and the same question (asked(): two permission dialogs in a row
            # offer the same choices for another command), or nothing is pressed (F3: as the panels' buttons already
            # were, act_panel()). What runs above the dialog may change meanwhile.
            view = await tmuxio.raw_screen(t)
            if not view.strip() and not await tmuxio.alive(t) and tmuxio.unanswered(t):
                # Its machine did not answer this time (a failed read is an empty screen): the dialog may well be
                # open still. Nothing pressed, the buttons stay (as above).
                return await interaction.followup.send(
                    messages.far("button", said=self.bridge.unanswered(interaction.channel)), ephemeral=True)
            excerpt, options = panels.parse_menu(view)
            if "shown" in m and [asked(view), list(options)] != [m["shown"][0], list(m["shown"][1])]:
                return await self.changed(interaction, token, m, view, excerpt, options)
        if what == "text":
            await tmuxio.type_text(t, text)
            await asyncio.sleep(0.3)
            await tmuxio.keys(t, "Enter")
            did = "輸入：%s" % cut(text, 200)
        elif what == "esc":  # only onto a panel still open: on an empty input box two of them open Rewind
            did = "按了 Esc" if await tmuxio.escape(t, screen.panel_shown) else "面板已經關了，沒有送 Esc"
        elif what.startswith("k") and "actions" in m:  # a panel's own key
            return await self.act_panel(interaction, token, m, t, what[1:])
        else:
            await tmuxio.keys(t, {"enter": "Enter"}.get(what, what))
            did = {"enter": "按了 Enter"}.get(what, "選了 %s" % what)
        await asyncio.sleep(1.2)
        view = await tmuxio.raw_screen(t)
        excerpt, options = panels.parse_menu(view)
        msg = interaction.channel.get_partial_message(m["msg"])
        try:
            if options:  # still a menu on screen (or a new one): show it again, buttons follow it
                v = self.view(token, options)
                await msg.edit(content=body(m["title"], excerpt, "\n-# " + did), view=v)
                v.stop()
                m["shown"] = [asked(view), options]
            else:
                self.open.pop(token, None)
                await msg.edit(content=body(m["title"], excerpt, "\n✅ " + did), view=None)
        except Exception:
            traceback.print_exc()
        await self.bridge.host.audit("#%s 選單：%s" % (getattr(interaction.channel, "name", "DM"), did))

    async def changed(self, interaction, token, m, view, excerpt, options):
        """The dialog on screen is not the one the buttons were made for: nothing was pressed. The message shows what
        is there now, with buttons for it if it is a dialog still, and says so."""
        msg = interaction.channel.get_partial_message(m["msg"])
        note = "\n⚠️ 選單已經變了，沒有送出。上面是現在的畫面%s。" % ("，按鈕已換成它的選項" if options else "")
        try:
            if options:
                v = self.view(token, options)
                await msg.edit(content=body(m["title"], excerpt, note), view=v)
                v.stop()
                m["shown"] = [asked(view), options]
            else:
                self.open.pop(token, None)
                await msg.edit(content=body(m["title"], excerpt, note), view=None)
        except Exception:
            traceback.print_exc()
        await self.bridge.host.audit("#%s 選單：畫面已經變了，按鈕沒有送出" % getattr(interaction.channel, "name", "DM"))

    async def act_panel(self, interaction, token, m, t, key):
        """One of a panel's hint-line keys was pressed. Sent only if the screen, read again now, still shows a panel
        offering that same key (a second click, a panel closed at the terminal, a turn running: nothing goes out,
        since a stale Ctrl+C or Shift+Tab would land on the session itself); then the panel is shown as it is."""
        lock = self.locks.setdefault(token, asyncio.Lock())
        async with lock:  # a second click waits for the first to be done and the screen read again
            if token not in self.open:
                return
            view = await tmuxio.raw_screen(t)
            now = panel_of(view, screen.has_prompt(view))
            action = next((a for a in (now or {}).get("actions", []) if a["keys"] == [key]), None)
            if action is None:
                did = "面板已經變了，沒有送出"
            elif key == "Escape":
                did = ("按了「%s」" % action["label"]) if await tmuxio.escape(t, screen.panel_shown) else "面板已經關了，沒有送 Esc"
            else:
                await tmuxio.keys(t, key)
                did = "按了「%s」" % action["label"]
            if action is not None:
                await asyncio.sleep(1.2)
                view = await tmuxio.raw_screen(t)
                now = panel_of(view, screen.has_prompt(view))
            msg = interaction.channel.get_partial_message(m["msg"])
            try:
                if now is not None and now["actions"]:
                    v = self.panel_view(token, now["actions"])
                    await msg.edit(content=body(m["title"], now["text"], "\n-# " + did), view=v)
                    v.stop()
                else:
                    self.open.pop(token, None)
                    self.locks.pop(token, None)
                    excerpt = now["text"] if now else panels.parse_menu(view)[0]
                    await msg.edit(content=body(m["title"], excerpt, "\n✅ " + did), view=None)
            except Exception:
                traceback.print_exc()
        await self.bridge.host.audit("#%s 面板：%s" % (getattr(interaction.channel, "name", "DM"), did))
