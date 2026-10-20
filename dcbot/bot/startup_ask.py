"""Startup dialogs the bot does not answer for the owner (the connector's screen.startup_choice()): Claude Code 2.1.303
asks, before a conversation starts, whether to allow the folder's CLAUDE.md to import files outside it. Yes reads
those files into the conversation with no tool, past the secrets guard; so unlike the trust and bypass dialogs
(screen.startup_key(), answered Yes by the bot) the owner decides. The dialog is posted in the channel with one
button per answer; the conversation waits (wait_ready() does not time out meanwhile, up to ASK_SECS). A press reads
the screen again: the same dialog still there, the arrows and Enter pick the answer (it takes no number key);
anything else, nothing is pressed and the message says so. Held in memory: a restart leaves old buttons dead (the
pane still shows the dialog; /screen and /key reach it)."""
import time
import traceback

import discord

from altention.connectors.claude_code import screen

from . import config, tmuxio

PREFIX = "sta"
ASK_SECS = 1800  # how long a conversation waits on the owner's answer before its opening is given up


def body(choice, note=""):
    text = choice["text"].replace("```", "'''")[-1400:]
    return ("🔔 **Claude Code 啟動前要你決定**\n```\n%s\n```"
            "這個資料夾的 CLAUDE.md 引用了資料夾外面的檔案。選 Yes，Claude 會把那些檔案讀進對話（不經過讀檔工具，秘密防護擋不到）；"
            "只有你信任那些檔案時才選 Yes。選 No 也可以開始對話，只是不讀那些檔。你決定之前，這個頻道的 Claude 會停在這裡。%s"
            % (text, note))[:1990]


class StartupAsk(object):
    def __init__(self, bridge):
        self.bridge, self.open = bridge, {}  # token -> {"channel", "pane", "title", "options", "msg"}

    async def post(self, ch, pane, choice):
        """Post the dialog with its answers as buttons; the token (None if it could not be posted)."""
        token = "%d" % (time.time() * 1000)
        v = discord.ui.View(timeout=None)
        for i, label in enumerate(choice["options"]):
            v.add_item(discord.ui.Button(label=label[:80], custom_id="%s:%s:%d" % (PREFIX, token, i),
                                         style=discord.ButtonStyle.danger if label.startswith("Yes") else discord.ButtonStyle.secondary))
        try:
            msg = await ch.send(body(choice), view=v)
        except Exception:
            traceback.print_exc()
            return None
        v.stop()
        self.open[token] = {"channel": ch.id, "pane": getattr(pane, "t", pane), "title": choice["title"],
                            "options": list(choice["options"]), "msg": msg.id}
        return token

    async def done(self, token, note):
        """The dialog is no longer waiting (the conversation came up, or its opening was given up): buttons away."""
        m = self.open.pop(token, None)
        if m is None:
            return
        ch = self.bridge.host.get_channel(m["channel"])
        try:
            await ch.get_partial_message(m["msg"]).edit(view=None, content=None if not note else
                                                         "🔔 Claude Code 啟動前的問題：%s" % note)
        except Exception:
            traceback.print_exc()

    async def on_interaction(self, interaction):
        if interaction.type != discord.InteractionType.component:
            return False
        parts = ((interaction.data or {}).get("custom_id") or "").split(":")
        if len(parts) != 3 or parts[0] != PREFIX:
            return False
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return True
        m = self.open.get(parts[1])
        if m is None or not parts[2].isdigit() or int(parts[2]) >= len(m["options"]):
            await interaction.response.edit_message(view=None)
            return True
        await interaction.response.defer()
        index = int(parts[2])
        pane = tmuxio.pane(m["pane"])
        view = await pane.raw_screen()
        now = screen.startup_choice(view)
        msg = interaction.channel.get_partial_message(m["msg"])
        if now is None or now["title"] != m["title"] or now["options"] != m["options"]:
            self.open.pop(parts[1], None)
            await msg.edit(view=None, content="🔔 Claude Code 啟動前的問題已經不在畫面上了（在終端機答掉了，或 Claude 已經結束），沒有送出。")
            return True
        for key in screen.keys_for(now, index):
            await pane.keys(key)
        self.open.pop(parts[1], None)
        await msg.edit(view=None, content="🔔 Claude Code 啟動前的問題：%s\n✅ 選了「%s」" % (m["title"], m["options"][index]))
        await self.bridge.host.audit("#%s 啟動前的問題「%s」：選了「%s」" % (getattr(interaction.channel, "name", "?"), m["title"],
                                                                     m["options"][index]))
        return True
