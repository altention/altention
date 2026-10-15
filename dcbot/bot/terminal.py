"""Terminal channels: a plain shell in a tmux window, driven from Discord.

Chosen on the setup card (💻), the channel's window runs the owner's own shell instead of Claude Code. Every
message is a command line typed into it. Its output is posted as one code block, edited every few seconds
while the command runs and marked with the exit status when it ends (a long output also comes as a file).
Buttons: Ctrl-C, run again, screen. `/run` autocompletes from the commands this channel has run.

How the end of a command is known: the shell is started with a small rc of ours that loads the owner's own
configuration and then adds a prompt hook writing the last exit status to state/term/<channel>/done. One
more line in that file = one more prompt = the command is over. The owner's dotfiles are not touched.
While a command runs, further messages are typed into it as input (answers to prompts and the like).
"""
import asyncio
import io
import json
import os
import shlex
import time
import traceback

import discord

from . import config, remote, tmuxio
from .termrc import done_count, rc_files, shell  # noqa: F401  (shared with tools/dcremote)

PREFIX = "term"
DIR = os.path.join(config.STATE, "term")
EDIT_SECS = 3          # how often a running command's output message is updated
WATCH_SECS = 30 * 60   # after this, stop updating the message (the command may run on; /screen still works)
SHOWN = 1800           # characters of output shown in the message (the tail)
KEEP = 200             # commands remembered per channel, for /run and "run again"


def remember(st, command):
    """Add a command to the channel's history; returns its number (for "run again")."""
    st["cmd_seq"] = st.get("cmd_seq", 0) + 1
    st["cmds"] = ([c for c in st.get("cmds", []) if c[1] != command] + [[st["cmd_seq"], command]])[-KEEP:]
    return st["cmd_seq"]


def history(st, current=""):
    """Past commands, newest first, matching `current` (for /run's autocomplete)."""
    return [c for _, c in reversed(st.get("cmds", [])) if current.lower() in c.lower()]


def body(text, status, limit=SHOWN):
    text = text.replace("```", "'''").rstrip()
    if len(text) > limit:
        text = "…\n" + text[-limit:]
    return "```\n%s\n```%s" % (text or " ", status)


async def screen_since(pane, start):
    """Lines of the pane from absolute line `start` (history + screen) to the cursor."""
    code, out = await tmuxio.tmux("display-message", "-p", "-t", pane["id"], "#{history_size} #{cursor_y}",
                                  **tmuxio.on(pane))
    try:
        hist, cy = (int(x) for x in out.split())
    except ValueError:
        return ""
    rel = start - hist
    code, text = await tmuxio.tmux("capture-pane", "-p", "-J", "-t", pane["id"], "-S", str(rel), "-E", str(cy),
                                   **tmuxio.on(pane))
    return text.rstrip("\n") if code == 0 else ""


async def cursor_line(pane):
    code, out = await tmuxio.tmux("display-message", "-p", "-t", pane["id"], "#{history_size} #{cursor_y}",
                                  **tmuxio.on(pane))
    try:
        hist, cy = (int(x) for x in out.split())
        return hist + cy
    except ValueError:
        return None


class Terminal(object):
    """`bot` is the Discord bot (chan(), save(), audit())."""

    def __init__(self, bot):
        self.bot, self.running, self.locks = bot, {}, {}

    def paths(self, cid):
        folder = os.path.join(DIR, str(cid))
        return folder, os.path.join(folder, "done")

    async def codes(self, st, done):
        """The exit statuses written so far: from the file here, or (a shell on another machine) over there."""
        host = (st.get("home") or {}).get("host")
        if not host:
            return done_count(done)
        code, out = await remote.dcremote(host, "done", st.get("term_done") or done, timeout=15)
        try:
            return json.loads(out) if code == 0 else []
        except ValueError:
            return []

    async def ensure(self, ch, st):
        """The channel's shell window: there, or opened (in the tmux session chosen on the setup card)."""
        pane = st.get("pane")
        if pane and await tmuxio.exists(pane):
            return pane
        folder, done = self.paths(ch.id)
        home = st["home"]
        if home.get("host"):  # another machine: the rc files and the done file live over there
            code, out = await remote.dcremote(home["host"], "termrc", str(ch.id), timeout=20)
            try:
                info = json.loads(out) if code == 0 else None
            except ValueError:
                info = None
            if not info:
                raise RuntimeError(remote.unreachable(home["host"]))
            argv, env, st["term_done"] = info["argv"], info["env"], info["done"]
        else:
            argv, env = rc_files(folder, done)
        before = len(await self.codes(st, done))
        why = []
        pane = await tmuxio.open_window(home["socket"], home["session"], home.get("window", ""), st["cwd"], argv, env,
                                        host=home.get("host"), why=why)
        if not pane:
            raise RuntimeError("在 tmux `%s` 開不了 shell%s" % (home["session"], "（%s）" % why[-1] if why else ""))
        for _ in range(50):  # the first prompt says the shell is ready
            if len(await self.codes(st, done)) > before:
                break
            await asyncio.sleep(0.2)
        pane["shell"] = True  # not Claude: tmuxio.alive() only checks it is there
        st["pane"] = pane
        self.bot.save()
        return pane

    def view(self, cid, seq, running):
        v = discord.ui.View(timeout=None)
        if running:
            v.add_item(discord.ui.Button(label="Ctrl-C", custom_id="%s:%d:ctrlc:%d" % (PREFIX, cid, seq),
                                         style=discord.ButtonStyle.danger))
        v.add_item(discord.ui.Button(label="🔁 再跑一次", custom_id="%s:%d:rerun:%d" % (PREFIX, cid, seq),
                                     style=discord.ButtonStyle.secondary))
        v.add_item(discord.ui.Button(label="📺 畫面", custom_id="%s:%d:screen:%d" % (PREFIX, cid, seq),
                                     style=discord.ButtonStyle.secondary))
        return v

    async def run(self, ch, text):
        """A message in a terminal channel: a command, or input for the command that is running."""
        st = self.bot.chan(ch)
        lock = self.locks.setdefault(ch.id, asyncio.Lock())
        if ch.id in self.running:  # a command is running: this is input for it
            pane = st.get("pane")
            if pane:
                await tmuxio.tmux("send-keys", "-t", pane["id"], "-l", "--", text, **tmuxio.on(pane))
                await tmuxio.tmux("send-keys", "-t", pane["id"], "Enter", **tmuxio.on(pane))
            return
        async with lock:
            try:
                pane = await self.ensure(ch, st)
            except Exception as e:
                return await ch.send("出錯了：%s" % e)
            folder, done = self.paths(ch.id)
            lines = [line for line in text.split("\n") if line.strip()]
            seq = remember(st, text.strip())
            self.bot.save()
            start = await cursor_line(pane)
            before = len(await self.codes(st, done))
            for line in lines:
                await tmuxio.tmux("send-keys", "-t", pane["id"], "-l", "--", line, **tmuxio.on(pane))
                await tmuxio.tmux("send-keys", "-t", pane["id"], "Enter", **tmuxio.on(pane))
            v = self.view(ch.id, seq, True)
            msg = await ch.send(body("$ " + text.strip(), "\n-# ⏳ 執行中"), view=v)
            v.stop()
            self.running[ch.id] = {"msg": msg, "seq": seq}
        asyncio.ensure_future(self.watch(ch, pane, start, before, len(lines), msg, seq, text.strip()))

    async def watch(self, ch, pane, start, before, expect, msg, seq, command):
        """Follow one command until its prompt comes back, keeping its message up to date."""
        folder, done = self.paths(ch.id)
        st = self.bot.chan(ch)
        t0, last, shown = time.time(), 0.0, None
        try:
            while True:
                codes = await self.codes(st, done)
                finished = len(codes) >= before + expect
                if not await tmuxio.exists(pane):
                    finished, codes = True, codes + ["?"]
                if finished or (time.time() - last >= EDIT_SECS and time.time() - t0 < WATCH_SECS):
                    last = time.time()
                    text = await screen_since(pane, start) if start is not None else ""
                    if finished and "\n" in text:
                        text = text.rsplit("\n", 1)[0]  # the new prompt
                    if finished:
                        code = codes[-1] if codes else "?"
                        status = "\n-# %s 結束碼 %s · %.0f 秒" % ("✅" if code == "0" else "❌", code, time.time() - t0)
                    else:
                        status = "\n-# ⏳ 執行中 %.0f 秒" % (time.time() - t0)
                    if (text, status) != shown or finished:
                        shown = (text, status)
                        v = self.view(ch.id, seq, not finished)
                        try:
                            await msg.edit(content=body(text, status), view=v)
                        except discord.HTTPException:
                            pass
                        v.stop()
                    if finished:
                        if len(text) > SHOWN:  # the whole output, as a file
                            await ch.send("完整輸出：", file=discord.File(io.BytesIO(text.encode("utf-8")), "output.txt"))
                        return
                await asyncio.sleep(0.5)
        except Exception:
            traceback.print_exc()
        finally:
            self.running.pop(ch.id, None)

    async def on_interaction(self, interaction):
        if interaction.type != discord.InteractionType.component:
            return False
        parts = ((interaction.data or {}).get("custom_id") or "").split(":")
        if len(parts) != 4 or parts[0] != PREFIX:
            return False
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return True
        ch, what, seq = interaction.channel, parts[2], int(parts[3])
        st = self.bot.chan(ch)
        pane = st.get("pane")
        if what == "ctrlc":
            if pane:
                await tmuxio.tmux("send-keys", "-t", pane["id"], "C-c", **tmuxio.on(pane))
            await interaction.response.defer()
        elif what == "screen":
            shown = await tmuxio.screen(pane, 40) if pane and await tmuxio.exists(pane) else "（沒有開著的 shell）"
            await interaction.response.send_message(body(shown, "", 1900), ephemeral=True)
        elif what == "rerun":
            command = next((c for s, c in st.get("cmds", []) if s == seq), None)
            if command is None:
                return await interaction.response.send_message("找不到這個指令了。", ephemeral=True) or True
            await interaction.response.defer()
            await self.run(ch, command)
        return True
