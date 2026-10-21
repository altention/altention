"""The setup card of a new channel: where its Claude Code should live.

When the owner opens a text channel (or first talks in one that has never had a conversation), the bot asks
before starting anything:
- 🆕 a new tmux session in the owner's own tmux: session name, window name (may be empty), folder;
- ➕ a new window in one of the owner's tmux sessions (picked from a menu): window name, folder;
- or the default: a session on the bot's own tmux server, as channels have always worked.
📁 picks the folder by clicking through it (the file browser, bot/files.py, in "pick" mode, drawn on the card
itself); the modals then offer it as the working folder. A files channel (📂) is a browser of one folder.
The first two put Claude where the owner sees it from a terminal (`tmux attach -t <session>`). Messages sent
before the choice is made wait and are delivered right after.
"""
import os
import traceback

import discord

import json

from . import config, files, remote, routes, tmuxio

PREFIX = "setup"


def configured(st):
    """Has this channel ever been given a place for its Claude (or a conversation)?"""
    return bool(st.get("tmux") or st.get("pane") or st.get("home") or st.get("session_id") or st.get("mode") == "print"
                or st.get("configured") or st.get("plain"))


def card_text(channel_name, cwd):
    """Step 1: what the channel is for."""
    return ("🛠️ **#%s 要拿來做什麼？**\n"
            "- 🤖 **Claude 對話**：跟 Claude Code 對話\n"
            "- 💻 **終端機**：不開 Claude，這個頻道就是一個 shell，每則訊息是一行指令\n"
            "- 📂 **檔案**：瀏覽一台機器上的一個資料夾，打字就是找檔案\n"
            "- 📝 **一般頻道**：機器人不回應，拿來記東西；之後要改用 `/setup`\n"
            "-# 選 Claude 或終端機之後，下一步選要開在哪裡。工作目錄預設是 `%s`。" % (channel_name, routes.short_cwd(cwd)))


def kind_of(terminal):
    """The card's `terminal` argument: False (a Claude), True (a terminal) or "files"."""
    return "files" if terminal == "files" else "term" if terminal else "claude"


def where_text(channel_name, cwd, terminal, host=None, hosts=(), folder=None):
    """Step 2: where it lives (and on which machine)."""
    if kind_of(terminal) == "files":
        return ("📂 **#%s 要看哪個資料夾？**　機器：**%s**%s\n"
                "- 📁 **點選資料夾**：從 `%s` 開始一層層點進去，到了按「✅ 用這個資料夾」\n"
                "- ✍️ **輸入路徑**：直接打資料夾的路徑"
                % (channel_name, host or remote.this_machine(), "（下拉選單可以換）" if hosts else "",
                   "~" if host else routes.short_cwd(cwd)))
    picked = "\n-# 📁 已選工作目錄 `%s`，下面的選項都會用它。" % (folder if host else routes.short_cwd(folder)) if folder else \
        "\n-# 📁 **選工作目錄**：先點選資料夾，下面的選項就用它（也可以在下一步直接打路徑）。"
    return where_body(channel_name, cwd, terminal, host, hosts) + picked


def where_body(channel_name, cwd, terminal, host=None, hosts=()):
    if host:
        return ("%s **#%s 要開在哪裡？**　機器：**%s**\n"
                "- 🆕 **開新的 tmux**：在你 %s 上的 tmux 開一個新的 session（工作目錄預設是家目錄 `~`）\n"
                "- ➕ **加到現有的 tmux**：在 %s 上已經開著的 tmux session 裡加一個視窗\n"
                "-# 在 %s 上用 `tmux attach -t <名稱>` 就看得到。別台機器沒有「交給機器人管」。"
                % ("💻" if terminal else "🤖", channel_name, host, host, host, host))
    machines = "　機器：**%s**（下拉選單可以換）" % remote.this_machine() if hosts else ""
    return ("%s **#%s 要開在哪裡？**%s\n"
            "- 🆕 **開新的 tmux**：在你的 tmux 開一個新的 session（可以改工作目錄）\n"
            "- ➕ **加到現有的 tmux**：在你已經開著的 tmux session 裡加一個視窗（可以改工作目錄）\n"
            "- 🛠️ **交給機器人管**：放在機器人自己的 tmux，工作目錄 `%s`\n"
            "-# 開在你的 tmux 的，電腦上用 `tmux attach -t <名稱>` 就看得到；機器人管的用 `tmux -L %s ls`。"
            % ("💻" if terminal else "🤖", channel_name, machines, routes.short_cwd(cwd), config.TMUX_SOCKET))


def card_view(cid):
    v = discord.ui.View(timeout=None)
    for what, label, style in (("claude", "🤖 Claude 對話", discord.ButtonStyle.primary),
                               ("term", "💻 終端機", discord.ButtonStyle.primary),
                               ("files", "📂 檔案", discord.ButtonStyle.primary),
                               ("plain", "📝 一般頻道", discord.ButtonStyle.secondary)):
        v.add_item(discord.ui.Button(label=label, custom_id="%s:%d:%s" % (PREFIX, cid, what), style=style))
    return v


def where_view(cid, terminal, host=None, hosts=()):
    t = {"files": "f", "term": "t", "claude": ""}[kind_of(terminal)]
    v = discord.ui.View(timeout=None)
    if hosts:  # it can live on another machine
        here = remote.this_machine()
        v.add_item(discord.ui.Select(custom_id="%s:%d:%shost" % (PREFIX, cid, t), placeholder="機器",
                                     options=[discord.SelectOption(label="%s（這台）" % here, value="-", default=not host)] +
                                     [discord.SelectOption(label=h, value=h, default=h == host) for h in hosts]))
    buttons = (("new", "🆕 開新的 tmux", discord.ButtonStyle.primary),
               ("add", "➕ 加到現有的 tmux", discord.ButtonStyle.primary),
               ("default", "🛠️ 交給機器人管", discord.ButtonStyle.secondary),
               ("browse", "📁 選工作目錄", discord.ButtonStyle.secondary),
               ("back", "← 返回", discord.ButtonStyle.secondary))
    if t == "f":
        buttons = (("browse", "📁 點選資料夾", discord.ButtonStyle.primary),
                   ("type", "✍️ 輸入路徑", discord.ButtonStyle.secondary),
                   ("back", "← 返回", discord.ButtonStyle.secondary))
    for what, label, style in [b for b in buttons if not (host and b[0] == "default")]:
        v.add_item(discord.ui.Button(label=label, custom_id="%s:%d:%s" % (PREFIX, cid, what if what == "back" else t + what),
                                     style=style))
    return v


def shown(path, host=None):
    """A path as the card and its dialogs show it: on this machine with ~ for its home; on another machine as it is
    (this machine's home means nothing there: /mnt/…/me/x shortened to ~/x is another folder where the home is
    elsewhere; 2026-10-07, a channel set up on another machine)."""
    return routes.short_cwd(path, host)


class Picks(object):
    """What was picked on a channel's card (the machine, the 📁 folder), kept in the channel's state so a restart
    halfway through the setup keeps it (2026-10-07: lost, the next click went to this machine). Used like a dict
    keyed by channel id."""
    def __init__(self, bot, key):
        self.bot, self.key = bot, key

    def _st(self, cid, make=False):
        st = self.bot.channels_state.get(str(cid))
        if st is None and make:
            st = self.bot.channels_state.setdefault(str(cid), {})
        return st

    def get(self, cid, default=None):
        return ((self._st(cid) or {}).get("setup_picks") or {}).get(self.key, default)

    def __getitem__(self, cid):
        return self.get(cid)

    def __setitem__(self, cid, value):
        self._st(cid, make=True).setdefault("setup_picks", {})[self.key] = value
        self.bot.save()

    def pop(self, cid, default=None):
        st = self._st(cid) or {}
        picks = st.get("setup_picks") or {}
        if self.key not in picks:
            return default
        value = picks.pop(self.key)
        if not picks:
            st.pop("setup_picks", None)
        self.bot.save()
        return value


def where_searched(host):
    """"on <machine>": a folder not found always says where it was looked for."""
    return host or remote.this_machine()


def expand(path, fallback):
    path = os.path.expanduser((path or "").strip()) or fallback
    return path if os.path.isdir(path) else None


class NewModal(discord.ui.Modal):
    def __init__(self, setup, ch, cwd, terminal=False, host=None):
        super().__init__(title="開新的 tmux session" + ("（終端機）" if terminal else ""), timeout=1800)
        self.setup, self.ch, self.terminal = setup, ch, terminal
        self.session = discord.ui.TextInput(label="tmux session 名稱", default=tmuxio.safe_session(ch.name), max_length=50)
        self.window = discord.ui.TextInput(label="視窗名稱（可留空）", required=False, max_length=50)
        self.cwd = discord.ui.TextInput(label="工作目錄", default=shown(cwd, host), max_length=300)
        for item in (self.session, self.window, self.cwd):
            self.add_item(item)

    async def on_submit(self, interaction):
        await self.setup.choose(interaction, self.ch, tmuxio.safe_session(str(self.session.value)),
                                str(self.window.value).strip(), str(self.cwd.value), new=True, terminal=self.terminal)


class AddModal(discord.ui.Modal):
    def __init__(self, setup, ch, session, cwd, terminal=False, host=None):
        super().__init__(title="加到 tmux「%s」" % session[:30], timeout=1800)
        self.setup, self.ch, self.session_name, self.terminal = setup, ch, session, terminal
        self.window = discord.ui.TextInput(label="視窗名稱（可留空）", required=False, max_length=50)
        self.cwd = discord.ui.TextInput(label="工作目錄", default=shown(cwd, host), max_length=300)
        for item in (self.window, self.cwd):
            self.add_item(item)

    async def on_submit(self, interaction):
        await self.setup.choose(interaction, self.ch, self.session_name, str(self.window.value).strip(),
                                str(self.cwd.value), new=False, terminal=self.terminal)


class FolderModal(discord.ui.Modal):
    def __init__(self, setup, ch, default):
        super().__init__(title="檔案頻道要看哪個資料夾", timeout=1800)
        self.setup, self.ch = setup, ch
        self.path = discord.ui.TextInput(label="資料夾路徑", default=default, max_length=300)
        self.add_item(self.path)

    async def on_submit(self, interaction):
        host = self.setup.host_of(self.ch.id)
        await interaction.response.defer()
        got = await files.call(host, {"op": "resolve", "cwd": "~", "paths": [str(self.path.value).strip() or "~"]})
        found = [f for f in got.get("found") or [] if f[2]]
        if not found:
            return await interaction.followup.send("%s 上找不到資料夾（或不能看）：`%s`" % (where_searched(host), self.path.value),
                                                   ephemeral=True)
        await self.setup.files_done(self.ch, self.setup.bot.chan(self.ch), host, found[0][1])


class Setup(object):
    """`bot` is the Discord bot (chan(), save(), tmux, audit())."""

    def __init__(self, bot):
        self.bot, self.held, self.cards = bot, {}, {}  # channel id -> [(message, text)] / setup card message id
        self.machine = Picks(bot, "machine")  # channel id -> the other machine picked on the card (none: this one)
        self.folder = Picks(bot, "folder")    # channel id -> the folder picked with 📁 (on that machine)

    def host_of(self, cid):
        """The machine picked on the card, while it is still one to attach to (kept through a restart, the list may
        have changed since: then it is dropped, with its folder, and the card is on this machine again)."""
        host = self.machine.get(cid)
        if host and host not in remote.hosts() and remote.problem is None:  # a broken list says nothing about it
            self.machine.pop(cid, None)
            self.folder.pop(cid, None)
            return None
        return host

    async def offer(self, ch, held=None):
        """Show the card (once) and keep `held` = (message, text) to deliver once a place is chosen."""
        if held:
            self.held.setdefault(ch.id, []).append(held)
        if ch.id in self.cards:
            return
        st = self.bot.chan(ch)
        try:
            v = card_view(ch.id)
            msg = await ch.send(card_text(ch.name, st["cwd"]), view=v)
            v.stop()
            self.cards[ch.id] = msg.id
        except Exception:
            traceback.print_exc()

    async def on_interaction(self, interaction):
        """A click on a setup card. Returns True if it was one."""
        if interaction.type != discord.InteractionType.component:
            return False
        parts = ((interaction.data or {}).get("custom_id") or "").split(":")
        if len(parts) != 3 or parts[0] != PREFIX:
            return False
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能設定。", ephemeral=True)
            return True
        ch, what = interaction.channel, parts[2]
        st = self.bot.chan(ch)
        if configured(st):
            await interaction.response.edit_message(content="這個頻道已經設定好了。", view=None)
            return True
        hosts, host = sorted(remote.hosts()), self.host_of(ch.id)
        if what in ("claude", "term", "files", "back", "host", "thost", "fhost"):  # between the card's steps, or a machine
            terminal = "files" if what in ("files", "fhost") else what in ("term", "thost")
            self.folder.pop(ch.id, None)  # a folder belongs to its machine
            if what in ("host", "thost", "fhost"):
                picked = ((interaction.data or {}).get("values") or ["-"])[0]
                host = self.machine[ch.id] = picked if picked in hosts else None
            else:
                host = self.machine.pop(ch.id, None) and None
            v = card_view(ch.id) if what == "back" else where_view(ch.id, terminal, host, hosts)
            text = card_text(ch.name, st["cwd"]) if what == "back" else \
                where_text(ch.name, st["cwd"], terminal, host, hosts)
            await interaction.response.edit_message(content=text, view=v)
            v.stop()
        elif what in ("browse", "tbrowse", "fbrowse"):  # 📁: the card becomes a folder picker
            await interaction.response.defer()
            purpose = {"browse": "claude", "tbrowse": "term", "fbrowse": "files"}[what]
            root = self.folder.get(ch.id) or ("~" if host else st["cwd"])
            b = self.bot.files.new_state(host, root, "pick", purpose=purpose)
            content, comps, _ = await self.bot.files.render(b)
            v = files.view(comps)
            await interaction.edit_original_response(content=content, view=v)
            v.stop()
            self.bot.files.remember(ch, interaction.message.id, b)
        elif what == "ftype":
            await interaction.response.send_modal(FolderModal(self, ch, "~" if host else shown(st["cwd"])))
        elif what == "plain":
            await interaction.response.defer()
            st["plain"] = True
            await self.done(ch, st, "📝 一般頻道：機器人不會回應這裡的訊息。之後要改成 Claude 或終端機，打 `/setup`。")
        elif what == "tdefault":  # a shell on the bot's own tmux server
            await interaction.response.defer()
            if self.folder.get(ch.id):
                st["cwd"] = self.folder.pop(ch.id)
            st.update({"home": {"socket": os.path.join(tmuxio.socket_dir(), config.TMUX_SOCKET),
                                "session": tmuxio.session_name(ch.name, ch.id), "window": ""}, "kind": "terminal",
                       "mode": "tmux"})
            await self.done(ch, st, "💻 終端機會開在機器人自己管的 tmux（`tmux -L %s attach -t %s`），工作目錄 `%s`。" % (
                config.TMUX_SOCKET, st["home"]["session"], routes.short_cwd(st["cwd"])))
        elif what in ("new", "tnew"):
            cwd = self.folder.get(ch.id) or ("~" if host else st["cwd"])
            await interaction.response.send_modal(NewModal(self, ch, cwd, terminal=what == "tnew", host=host))
        elif what in ("add", "tadd"):
            if host:
                await interaction.response.defer(ephemeral=True, thinking=True)
                info = await self.info(host)
                found = await tmuxio.user_sessions(info.get("socket"), host) if info else []
                if not found:
                    return await interaction.followup.send("%s 的 tmux 現在沒有開著的 session（或連不上），請改選「開新的 tmux」。" % host,
                                                           ephemeral=True) or True
                v = self.session_menu(ch, "tpick" if what == "tadd" else "pick", found, host)
                await interaction.followup.send("要加到 %s 上哪個 tmux session？" % host, view=v, ephemeral=True)
                v.stop()
                return True
            found = await tmuxio.user_sessions()
            if not found:
                await interaction.response.send_message("你的 tmux 現在沒有開著的 session，請改選「開新的 tmux」。", ephemeral=True)
                return True
            v = self.session_menu(ch, "tpick" if what == "tadd" else "pick", found)
            await interaction.response.send_message("要加到哪個 tmux session？", view=v, ephemeral=True)
            v.stop()
        elif what in ("pick", "tpick"):
            name = ((interaction.data or {}).get("values") or [""])[0]
            if host:
                info = await self.info(host)
                cwd = dict(await tmuxio.user_sessions(info.get("socket"), host) if info else []).get(name) or "~"
            else:
                cwd = dict(await tmuxio.user_sessions()).get(name) or st["cwd"]
            cwd = self.folder.get(ch.id) or cwd
            await interaction.response.send_modal(AddModal(self, ch, name, cwd, terminal=what == "tpick", host=host))
        elif what == "default":
            await interaction.response.defer()
            if self.folder.get(ch.id):
                st["cwd"] = self.folder.pop(ch.id)
            await self.done(ch, st, "會開在機器人自己管的 tmux（`tmux -L %s`），工作目錄 `%s`。" % (
                config.TMUX_SOCKET, routes.short_cwd(st["cwd"])))
        return True

    def session_menu(self, ch, what, found, host=None):
        v = discord.ui.View(timeout=None)
        v.add_item(discord.ui.Select(custom_id="%s:%d:%s" % (PREFIX, ch.id, what), placeholder="選一個 tmux session",
                                     options=[discord.SelectOption(label=name[:100], value=name[:100],
                                                                   description=shown(cwd, host)[:100])
                                              for name, cwd in found[:25]]))
        return v

    async def picked(self, interaction, b, path):
        """The folder picker on the card is done: `path` was chosen (None: ← 返回, nothing chosen)."""
        ch = interaction.channel
        st = self.bot.chan(ch)
        purpose, host = b.get("purpose") or "claude", b.get("host")
        self.bot.files.states(ch).pop(str(interaction.message.id), None)
        if path and purpose == "files":
            await interaction.response.defer()
            return await self.files_done(ch, st, host, path)
        if path:
            self.folder[ch.id] = path
        terminal = {"files": "files", "term": True}.get(purpose, False)
        hosts = sorted(remote.hosts())
        v = where_view(ch.id, terminal, host, hosts)
        await interaction.response.edit_message(
            content=where_text(ch.name, st["cwd"], terminal, host, hosts, self.folder.get(ch.id)), view=v, attachments=[])
        v.stop()

    async def files_done(self, ch, st, host, path):
        st.update({"kind": "files", "files": {"host": host, "root": path}})
        if not host:
            st["cwd"] = path
        self.machine.pop(ch.id, None)
        await self.done(ch, st, "📂 檔案頻道：%s`%s`。直接打字就是找檔案，打路徑就跳過去。" % (
            "%s 上的 " % host if host else "", path if host else routes.short_cwd(path)))

    async def info(self, host, folder=None):
        """{"socket", "home", "folder"} of the owner on another machine (dcremote info), or {} if unreachable."""
        code, out = await remote.dcremote(host, "info", *([folder] if folder else []), timeout=20)
        try:
            return json.loads(out) if code == 0 else {}
        except ValueError:
            return {}

    async def choose(self, interaction, ch, session, window, cwd_text, new, terminal=False):
        st = self.bot.chan(ch)
        host = self.host_of(ch.id)
        if not session:
            return await interaction.response.send_message("tmux session 名稱不能是空的。", ephemeral=True)
        if host:
            await interaction.response.defer()
            info = await self.info(host, cwd_text.strip() or "~")
            if not info:
                return await interaction.followup.send("%s。" % remote.unreachable(host), ephemeral=True)
            if not info.get("folder"):
                return await interaction.followup.send("%s 上找不到資料夾：`%s`" % (host, cwd_text), ephemeral=True)
            if new and session in dict(await tmuxio.user_sessions(info["socket"], host)):
                return await interaction.followup.send(
                    "%s 的 tmux 裡已經有叫 `%s` 的 session 了。換個名字，或改用「加到現有的 tmux」。" % (host, session), ephemeral=True)
            st.update({"home": {"socket": info["socket"], "session": session, "window": window, "host": host},
                       "cwd": info["folder"], "mode": "tmux"})
            if terminal:
                st["kind"] = "terminal"
            self.machine.pop(ch.id, None)
            return await self.done(ch, st, "%s會開在 %s 上你的 tmux `%s` 的%s視窗%s，工作目錄 `%s`。在 %s 上用 `tmux attach -t %s` 就看得到。" % (
                "💻 終端機" if terminal else "", host, session, "第一個" if new else "新", "「%s」" % window if window else "",
                info["folder"].replace(info.get("home") or "\0", "~"), host, session))
        cwd = expand(cwd_text, st["cwd"])
        if cwd is None:
            return await interaction.response.send_message("%s 上找不到資料夾：`%s`" % (where_searched(None), cwd_text),
                                                           ephemeral=True)
        exists = session in dict(await tmuxio.user_sessions())
        if new and exists:
            return await interaction.response.send_message(
                "tmux 裡已經有叫 `%s` 的 session 了。換個名字，或改用「加到現有的 tmux」。" % session, ephemeral=True)
        await interaction.response.defer()
        st.update({"home": {"socket": tmuxio.user_socket(), "session": session, "window": window}, "cwd": cwd,
                   "mode": "tmux"})
        if terminal:
            st["kind"] = "terminal"
        await self.done(ch, st, "%s會開在你的 tmux `%s` 的%s視窗%s，工作目錄 `%s`。電腦上用 `tmux attach -t %s` 就看得到。%s" % (
            "💻 終端機" if terminal else "", session, "第一個" if new else "新", "「%s」" % window if window else "",
            routes.short_cwd(cwd), session, "\n之後每則訊息就是一行指令；`/run` 可以從跑過的指令裡挑。" if terminal else ""))

    async def left(self, ch, rec, cwd):
        """archive.leave() for a channel set up here; what it says (False: it was not in the archive)."""
        archive = getattr(self.bot, "archive", None)
        if archive is None:
            return False
        try:
            return await archive.leave(ch, rec, cwd)
        except Exception:  # (Discord refused the move: set up all the same, said in the log)
            traceback.print_exc()
            return False

    async def done(self, ch, st, where):
        st["configured"] = True
        self.folder.pop(ch.id, None)
        self.machine.pop(ch.id, None)
        st.pop("cwd_from", None)
        rec = st.pop("archived", None)  # a channel brought out of the archive by hand no longer stands for its old pane
        self.bot.save()
        out = await self.left(ch, rec, st.get("cwd"))
        if out is not False:  # it was in the archive: moved back where it came from (owner's report 2026-10-20)
            where += "\n📦 頻道從封存搬回「%s」。" % (out.name if out is not None else "沒有分類")
        card = self.cards.pop(ch.id, None)
        if card is not None:
            try:
                await ch.get_partial_message(card).edit(content="✅ " + where, view=None)
            except Exception:
                pass
        await self.bot.audit("#%s 設定：%s" % (getattr(ch, "name", "DM"), where))
        held = self.held.pop(ch.id, [])
        if st.get("plain"):
            return  # what was typed before the choice stays in the channel, unanswered, as wanted
        if st.get("kind") == "files":
            await self.bot.files.start_channel(ch)
            for message, text in held:
                await self.bot.files.typed(ch, text)
            return
        if st.get("kind") == "terminal":
            try:
                await self.bot.terminal.ensure(ch, st)
            except Exception as e:
                return await ch.send("出錯了：%s" % e)
            for message, text in held:
                await self.bot.terminal.run(ch, text)
            return
        if not held:  # nothing to say yet: open it now, so it is already there in tmux
            return await self.bot.tmux.start_now(ch)
        for message, text in held:
            await self.bot.tmux.submit(message, text)
