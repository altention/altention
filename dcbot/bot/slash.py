"""Discord's own "/" menu: a small set of commands registered in the owner's server.

Typing "/" in Discord then offers them with descriptions and argument hints. Two kinds:
- the bot's built-ins (the same code as the "!" commands, which stay: they need no registration and are
  the lifeline when this breaks);
- a few Claude Code commands, typed into the channel's tmux session exactly like a "/…" text message.
Anything not registered here (/model, skills, …) is still sent as an ordinary message starting with "/".

A registered command arrives as an interaction, not a message, and must be answered within 3 seconds:
every handler defers first and replies through the follow-up.
"""
import hashlib
import json
import os
import traceback
from typing import Optional

import discord
from discord import app_commands

from altention.connectors.claude_code import sessions as cc_sessions

from . import catalog, config, routes, sessions, tmuxio
from .util import split_message

SYNCED = os.path.join(config.STATE, "slash-synced")  # digest of what was last registered
# name -> (description, text handed to Bot.command, answer visible only to the owner)
PLAIN = {
    "status": ("機器人和這個頻道的狀態", "!status", True),
    "reset": ("這個頻道開新對話（舊的可以用 /resume 接回）", "!reset", False),
    "stop": ("中止這個頻道正在跑的工作", "!stop", False),
    "screen": ("看 Claude 的終端機畫面（圖片，按 📝 看文字）", "!screen", False),
    "setup": ("重新叫出這個頻道的設定卡（一般頻道要改成 Claude 或終端機時）", "!setup", False),
    "detach": ("卸下這個頻道接的 tmux 對話（對話本身不受影響），頻道移到封存", "!detach", False),
}
# name -> (description, what is typed into Claude Code)
CLAUDE = {
    "usage": ("Claude Code：用量面板", "/usage"),
    "context": ("Claude Code：目前對話佔用的 context", "/context"),
}


class Origin(object):
    """Stands in for a Discord message where only its channel matters."""
    def __init__(self, channel):
        self.channel = channel


def setup(bot):
    """Build the command tree on `bot` (needs command(), chan(), mode(), tmux, audit())."""
    tree = app_commands.CommandTree(bot)
    guild = discord.Object(id=config.GUILD_ID)

    async def begin(interaction, private):
        """Check the caller and defer. Returns the function that sends the answer, or None if refused."""
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用這個機器人。", ephemeral=True)
            return None
        await interaction.response.defer(ephemeral=private)

        async def send(text, view=None, file=None):
            parts, msg = split_message(text), None
            for i, part in enumerate(parts):
                extra = {"view": view} if view is not None and i == len(parts) - 1 else {}
                if file is not None and i == len(parts) - 1:
                    extra["file"] = file
                msg = await interaction.followup.send(part, ephemeral=private, wait=True, **extra)
            return msg
        return send

    async def builtin(interaction, text, private):
        send = await begin(interaction, private)
        if send:
            await bot.command(interaction.channel, text, send)

    async def claude(interaction, text):
        if interaction.user.id == config.OWNER_ID and bot.mode(bot.chan(interaction.channel)) != "tmux":
            return await interaction.response.send_message(
                "這個頻道是 print 模式，Claude Code 的指令只有 tmux 模式能用（`/mode`）。", ephemeral=True)
        send = await begin(interaction, False)
        if send:
            await bot.tmux.submit(Origin(interaction.channel), text, out=send)
            await bot.audit("#%s 指令：%s" % (getattr(interaction.channel, "name", "DM"), text[:100]))

    def plain(name, desc, text, private):
        @tree.command(name=name, description=desc, guild=guild)
        async def run(interaction: discord.Interaction):
            await builtin(interaction, text, private)

    def passthrough(name, desc, text):
        @tree.command(name=name, description=desc, guild=guild)
        async def run(interaction: discord.Interaction):
            await claude(interaction, text)

    for name, (desc, text, private) in PLAIN.items():
        plain(name, desc, text, private)
    for name, (desc, text) in CLAUDE.items():
        passthrough(name, desc, text)

    @tree.command(name="compact", description="Claude Code：壓縮目前的對話，騰出 context", guild=guild)
    @app_commands.describe(instructions="（可省略）壓縮時要特別保留什麼")
    async def compact(interaction: discord.Interaction, instructions: Optional[str] = None):
        await claude(interaction, ("/compact " + " ".join((instructions or "").split())).strip())

    @tree.command(name="btw", description="順便問一句：開一個討論串回答，可以在裡面繼續追問；不打斷正在跑的工作", guild=guild)
    @app_commands.describe(question="要問的事")
    async def btw(interaction: discord.Interaction, question: str):
        if interaction.user.id == config.OWNER_ID and bot.mode(bot.chan(interaction.channel)) != "tmux":
            return await interaction.response.send_message("這個頻道是 print 模式，`/btw` 只有 tmux 模式能用。", ephemeral=True)
        send = await begin(interaction, True)  # the answer goes into a thread; this reply only points at it
        if send:
            await bot.tmux.btw_open(interaction.channel, " ".join(question.split()), out=send)

    @tree.command(name="files", description="檔案瀏覽器：從這個頻道的資料夾開始，點進資料夾、看檔案、找檔案", guild=guild)
    @app_commands.describe(path="（可省略）從哪個資料夾或檔案開始，可以是相對路徑，檔案可加 :行號")
    async def files(interaction: discord.Interaction, path: Optional[str] = None):
        await builtin(interaction, "!files " + (path or ""), False)

    @tree.command(name="key", description="送按鍵到 Claude 的終端機（卡在選單或面板時用）", guild=guild)
    @app_commands.describe(keys="按鍵名稱，用空白分開，例如 Escape、Enter、Down Enter")
    async def key(interaction: discord.Interaction, keys: str):
        await builtin(interaction, "!key " + keys, True)

    @tree.command(name="mode", description="看或切換這個頻道的模式", guild=guild)
    @app_commands.describe(mode="不選就顯示目前的模式")
    @app_commands.choices(mode=[
        app_commands.Choice(name="tmux：常駐對話，可中途補充，背景工作會回報", value="tmux"),
        app_commands.Choice(name="print：每則訊息跑一次（備用）", value="print")])
    async def mode(interaction: discord.Interaction, mode: Optional[app_commands.Choice[str]] = None):
        await builtin(interaction, "!mode " + (mode.value if mode else ""), mode is None)

    @tree.command(name="recap", description="顯示這個頻道的對話最近幾輪（只有你看得到）", guild=guild)
    @app_commands.describe(turns="要看幾輪，預設 3，最多 20")
    async def recap(interaction: discord.Interaction, turns: Optional[app_commands.Range[int, 1, 20]] = None):
        await builtin(interaction, "!recap %s" % (turns or ""), True)

    @tree.command(name="rename", description="這個頻道和它的 Claude Code 對話一起改名（來源仍寫在頻道主題）", guild=guild)
    @app_commands.describe(name="新名字，例如 回測")
    async def rename(interaction: discord.Interaction, name: str):
        await builtin(interaction, "!rename " + name, False)

    @tree.command(name="run", description="終端機頻道：執行一行指令（可以從跑過的指令裡搜尋）", guild=guild)
    @app_commands.describe(command="要執行的指令；打字會列出這個頻道跑過、符合的指令")
    async def run(interaction: discord.Interaction, command: str):
        st = bot.chan(interaction.channel)
        if interaction.user.id == config.OWNER_ID and st.get("kind") != "terminal":
            return await interaction.response.send_message("`/run` 只能在終端機頻道用（新頻道的設定卡選 💻）。", ephemeral=True)
        send = await begin(interaction, True)
        if send:
            await send("▶️ `%s`" % command[:300])
            await bot.terminal.run(interaction.channel, command)

    @run.autocomplete("command")
    async def run_choices(interaction: discord.Interaction, current: str):
        if interaction.user.id != config.OWNER_ID:
            return []
        from . import terminal
        return [app_commands.Choice(name=c[:100], value=c[:100]) for c in terminal.history(bot.chan(interaction.channel), current)][:25]

    @tree.command(name="talklimit", description="這個頻道的 Claude 跟別的頻道的 Claude 最多連續來回幾次（off：不限制）", guild=guild)
    @app_commands.describe(limit="次數，或 off（不限制）、default（回到預設 10 次）；不填就顯示目前的設定")
    async def talklimit(interaction: discord.Interaction, limit: Optional[str] = None):
        await builtin(interaction, "!talklimit " + (limit or ""), limit is None)

    @tree.command(name="cwd", description="設定這個頻道的工作目錄（會開新對話）", guild=guild)
    @app_commands.describe(path="資料夾路徑，例如 ~/projects/foo")
    async def cwd(interaction: discord.Interaction, path: str):
        await builtin(interaction, "!cwd " + path, False)

    @tree.command(name="resume", description="接回之前的對話（不選就列出最近的對話）", guild=guild)
    @app_commands.describe(conversation="要接回的對話")
    async def resume(interaction: discord.Interaction, conversation: Optional[str] = None):
        await builtin(interaction, "!resume " + (conversation or ""), conversation is None)

    @tree.command(name="attach", description="把這台機器 tmux 裡的 Claude Code 接到一個新頻道（不選就列出來）", guild=guild)
    @app_commands.describe(session="要接上的對話", project="（可省略）分類名稱，預設用專案資料夾的名字")
    async def attach(interaction: discord.Interaction, session: Optional[str] = None, project: Optional[str] = None):
        text = "!attach " + (session or "") + (" " + " ".join(project.split()) if session and project else "")
        await builtin(interaction, text, session is None)

    @attach.autocomplete("session")
    async def attach_choices(interaction: discord.Interaction, current: str):
        if interaction.user.id != config.OWNER_ID:
            return []
        table = routes.load()
        panes = await tmuxio.claude_panes(everywhere=True)
        picks = [("📁 整個專案 %s（%d 個對話）" % (name, len(ps)), routes.PROJECT + name)
                 for name, ps in routes.by_project(panes, table).items() if len(ps) > 1]
        picks += [(routes.label(p), routes.key(p)) for p in panes if not routes.routed_to(p, table)]
        return [app_commands.Choice(name=text[:100], value=value[:100]) for text, value in picks
                if current.lower() in text.lower()][:25]

    @resume.autocomplete("conversation")
    async def resume_choices(interaction: discord.Interaction, current: str):
        if interaction.user.id != config.OWNER_ID:
            return []
        st = bot.chan(interaction.channel)
        if sessions.elsewhere(st):
            return []  # (another machine's conversations: not this machine's folder; !resume says so)
        labelled = [(sessions.label(s, st.get("session_id")), s["id"]) for s in cc_sessions.recent(st["cwd"], 25)]
        return [app_commands.Choice(name=text[:100], value=sid) for text, sid in labelled
                if current.lower() in text.lower() or sid.startswith(current.lower())][:25]

    @tree.error
    async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        traceback.print_exception(type(error), error, error.__traceback__)
        text = "出錯了：%s" % str(error)[:300]
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass

    @tree.command(name="cc", description="Claude Code：執行任何一個 / 指令或 skill（選單裡沒有的也可以）", guild=guild)
    @app_commands.describe(command="指令名稱，可以打字搜尋", args="（可省略）要交給這個指令的內容")
    async def cc(interaction: discord.Interaction, command: str, args: Optional[str] = None):
        await claude(interaction, ("/%s %s" % (command.strip().lstrip("/"), " ".join((args or "").split()))).strip())

    @cc.autocomplete("command")
    async def cc_choices(interaction: discord.Interaction, current: str):
        names = (catalog.load() or {}).get("all") or []
        return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names if current.lower().lstrip("/") in n.lower()][:25]

    @tree.command(name="commands", description="Claude Code 的指令和 skill 清單；重新整理選單", guild=guild)
    @app_commands.describe(refresh="重新向 Claude Code 查一次（裝了新的 skill 之後用）")
    async def commands(interaction: discord.Interaction, refresh: Optional[bool] = False):
        await builtin(interaction, "!commands" + (" refresh" if refresh else ""), True)

    tree.guild, tree.claude, tree.dynamic = guild, claude, []
    tree.own = [c.name for c in tree.get_commands(guild=guild)]  # names Claude Code's entries may not take
    return tree


def add_catalog(tree, cat):
    """Put Claude Code's commands and skills (catalog.load()) into the menu, replacing the previous set."""
    for name in tree.dynamic:
        tree.remove_command(name, guild=tree.guild)
    tree.dynamic = []

    def entry(e):
        @tree.command(name=e["name"], description=e["description"], guild=tree.guild)
        @app_commands.describe(args="（可省略）要交給這個指令的內容，例如研究主題")
        async def run(interaction: discord.Interaction, args: Optional[str] = None):
            await tree.claude(interaction, ("/%s %s" % (e["run"], " ".join((args or "").split()))).strip())

    for e in (cat or {}).get("entries") or []:
        if e["name"] not in tree.own and catalog.NAME.match(e["name"]):
            entry(e)
            tree.dynamic.append(e["name"])


def digest(tree):
    payload = [c.to_dict(tree) for c in tree.get_commands(guild=tree.guild)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


async def sync(tree):
    """Register the commands with Discord if they changed since last time. Returns how many, or None."""
    want = digest(tree)
    try:
        with open(SYNCED) as f:
            if f.read().strip() == want:
                return None
    except OSError:
        pass
    done = await tree.sync(guild=tree.guild)
    with open(SYNCED, "w") as f:
        f.write(want)
    return len(done)
