"""Discord bot: forwards the owner's messages to a persistent Claude Code session per channel.

Two ways to run a channel's session (`!mode`): "tmux" keeps an interactive Claude Code alive in tmux
(tmux_bridge.py); "print" runs one `claude -p --resume` per message (claude_bridge.py) and is the fallback.

Only the owner (OWNER_ID) is obeyed. Built-in commands (handled without Claude, so they work even when
Claude or a bad deploy misbehaves): !help !status !reset !resume !recap !rename !commands !attach !detach !stop !cwd !mode !screen !key !restart !rollback
"""
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
import traceback

import discord

from altention.connectors.claude_code import sessions as cc_sessions
from altention.connectors.claude_code import transcript

from . import archive, attachments, awaiting, catalog, catchup, config, events, files, history, kinds, layout, messages, peers, posts, process, remote, routes, screens, sessions, setup, slash, terminal, tmuxio
from .claude_bridge import run_turn
from .tmux_bridge import TmuxBridge, fence
from . import frames, update_cards, util
from .util import split_message

STATE = config.STATE
CHANNELS = os.path.join(STATE, "channels.json")
RESTART_FLAG = os.path.join(STATE, "restart-requested")
ROLLBACK_FLAG = os.path.join(STATE, "rollback-requested")
NOTICE = os.path.join(STATE, "notice.txt")
EXIT_RESTART = 42
RESTART_WAIT = 60  # seconds a restart waits for half-done work (posts, events) before going anyway
SELFTEST = "selftest"  # the channel of the made-up event Bot.self_test() sends through the hook
BUILTINS = ("!help", "!status", "!setup", "!talklimit", "!reset", "!resume", "!recap", "!rename", "!commands", "!attach", "!detach", "!archive", "!stop",
            "!cwd", "!mode", "!screen", "!key", "!files", "!restart", "!rollback")

HELP = """**內建指令**（不經過 Claude，Claude 壞掉時也能用）
`!status` 機器人狀態　`!reset` 這個頻道開新對話　`!stop` 中止這個頻道正在跑的工作
`!resume` 列出最近的對話　`!resume <編號或 id 開頭>` 接回那個對話（`!reset` 之後想回去時用）
`!rename <名字>` 頻道和 Claude Code 對話一起改名　`!recap [幾輪]` 顯示這個頻道的對話最近幾輪（預設 3）　`!commands [refresh]` Claude Code 的指令和 skill 清單
`!attach` 列出這台機器 tmux 裡的 Claude Code　`!attach <編號> [專案名]` 幫它開一個頻道接上　`!attach project:<專案名>` 或 `!attach all` 一次接上多個　`!detach` 卸下這個頻道　`!archive` 封存這個頻道（任何種類；打字可以接回或恢復）
`!cwd <路徑>` 設定這個頻道的工作目錄　`!restart` 重啟機器人　`!rollback` 退回上一個正常版本
`!mode tmux|print` 切換這個頻道的模式（tmux：常駐對話，可中途補充、背景工作會回報；print：每則訊息跑一次，備用）
`!screen` 看 Claude 的終端機畫面　`!key <按鍵…>` 送按鍵（例如 `!key Down Enter`、`!key Escape`）
`!files [路徑]` 檔案瀏覽器（從這個頻道的資料夾開始，可以點進資料夾、看檔案、找檔案）
其他訊息都會交給這個頻道的 Claude 對話處理；`/` 開頭的訊息是 Claude Code 的指令（例如 `/model`）。
打 `/` 會跳出選單的那幾個（`/status` `/reset` `/resume` `/stop` `/screen` `/key` `/mode` `/cwd` `/attach` `/detach` `/archive` `/usage` `/context` `/compact`）是註冊在 Discord 的版本，功能跟對應的 `!` 指令或 Claude Code 指令相同。"""

SYSTEM_NOTE = """你是使用者在 Discord 上的助理機器人，背後是 Claude Code，可以在這台機器（{host}）上執行指令、讀寫檔案、用 SSH 連到其他機器。
- 現在在 Discord 頻道 #{channel}，工作目錄 {cwd}。你的回覆會直接貼到 Discord：用繁體中文、精簡、先講結論；長內容放程式碼區塊或分點。
- 管理 Discord（頻道、分類、主題、發訊息）用 `{root}/tools/dcctl`，先跑 `{root}/tools/dcctl --help`。對話內容不在你的上下文裡時（新對話、使用者提到「剛剛講的」），用 `{root}/tools/dcctl history {channel}` 讀這個頻道最近的訊息；討論串（子代理、workflow、/btw）用 `dcctl threads {channel}` 列出，再用 `dcctl history <討論串 ID>` 讀。刪除頻道、刪檔、強制推送等破壞性動作，先在對話中問使用者並得到明確同意。
- 機器人自己的程式碼在 {root}（git repo）。修改機器人請照 {root}/CLAUDE.md 的流程（測試 → `./deploy.sh` → 自動重啟，失敗會自動退回）。
- 機器清單和設定在實例（{instance}：machines.json、plugins.json），服務管理在引擎 {engine}（ops/apply.py、ops/doctor.py）；進度插件整理的各專案進度在 {progress}（STATUS.md、projects/*/STATUS.md）。
- 絕對不要讀取、輸出或修改 ~/.config/dcbot/ 裡的任何東西（那裡是 token）。"""

TMUX_NOTE = """
- 你是跑在 tmux 裡的互動式 Claude Code（使用者也可以用 `{where}` 直接接手）。每一輪結束時，你**最後一則**文字會貼到 Discord 當作正式回覆；你在工具呼叫之前說的話也會即時以引用的樣式貼出來當進度，所以中途的話保持簡短，結論和要使用者決定的事都放在最後的回覆。使用者可能在你處理到一半時補充訊息。
- 使用者看不到終端機畫面，但 AskUserQuestion 可以用：問題會變成 Discord 上的按鈕（多選是下拉選單，另有「其他…」讓使用者自己打字），使用者點了你才會拿到答案。需要使用者從幾個明確的選項裡挑時用它；開放式的問題照樣用文字問。plan mode 也可以用：ExitPlanMode 的計畫會貼到 Discord，使用者按按鈕核准或寫下要改什麼。
- 要把東西貼到某個頻道（報告、紀錄），或跟另一個頻道的 Claude 說話：`{alt} dc post <頻道> "內容"`（長文用 stdin，`--file` 附檔）。對方頻道有 Claude 的話它會收到，並知道是你傳的、怎麼回覆你。收到「這是 #xxx 傳到這個頻道的訊息」時，要回覆就照裡面寫的用 `{alt} dc post` 傳回去。Discord 不支援表格，貼過去的內容用清單。
- 背景工作（Bash 的 run_in_background）在這個對話裡會一直活著，完成時你會自動被叫起來，把結果回報給使用者即可，不要為了等它而卡住這一輪。機器重開後也要在的常駐服務，仍然走 systemd／ops 流程。"""


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def fresh(path, seen):
    """Is there a flag file other than the one seen when this process started (`seen`: its mtime then, or
    None)? That one started this process (on_ready reads and removes it a few seconds in): acting on it would
    loop. Compared with itself, not with this machine's clock: state/ is on NFS, whose clock may differ."""
    now = mtime(path)
    return now is not None and now != seen


def git(*args):
    r = subprocess.run(["git", "-C", config.ROOT] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, universal_newlines=True)
    return r.stdout.strip()


FIND_MIN = 8  # characters of an id's start that name a conversation not in the list (the connector's sessions.find())


RECONCILE_SECS = 300  # setup.py reconcile at a start: given up after this long (it moves channels, may run dcctl guide)
SETUP_FIRST = "請用 setup skill 帶使用者做完設定。"  # what #setup's Claude is told first (setup-in-discord.md 2)


def setup_folder():
    """#setup's working folder (install.py makes it), from HOME at call time."""
    return os.path.expanduser("~/.local/state/altention/setup")


def setup_done():
    """Is the installation's setup done? The instance's setup.done; no such key (an installation from before C-48),
    or settings that cannot be read: done (nothing is opened on a guess)."""
    try:
        return remote.alt().setting("setup.done") is not False
    except Exception:
        traceback.print_exc()
        return True


class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.new_install = not os.path.exists(CHANNELS)  # no channel state at all, not one that could not be read
        self.channels_state = load_json(CHANNELS, {})
        self.locks = {}
        self.tmux = TmuxBridge(self)
        self.tree = slash.setup(self)
        self.setup = setup.Setup(self)
        self.archive = archive.Archive(self)
        self.terminal = terminal.Terminal(self)
        self.forwards = {}  # channel id -> (forwarded message, its text, the timer that sends it alone)
        self.merging = {}   # channel id -> a future: a comment is being put together with the forwards before it
        self.posts = posts.Posts(self)
        self.cards = update_cards.Cards(self)
        self.tmux.asks.on_done = self.posts.answered  # a question announced in another channel: update that notice
        self.files = files.Files(self)
        self.catchup = catchup.Catchup(self)
        slash.add_catalog(self.tree, catalog.load())  # Claude Code's own commands, as last asked
        self.pumping = False
        self.procs = {}
        self.active = 0
        self.sem = asyncio.Semaphore(config.MAX_PARALLEL)
        self.audit_ch = None
        self.exit_code = 0
        self.started = time.time()

    # ------------------------------------------------------------------ lifecycle
    async def setup_hook(self):
        self.loop.create_task(self.heartbeat())
        self.loop.create_task(self.watch_restart())

    async def on_ready(self):
        guild = self.get_guild(config.GUILD_ID)
        if guild:
            await self.own_channels(guild)
        head = git("rev-parse", "--short", "HEAD")
        notice = ""
        if os.path.exists(NOTICE):
            notice = open(NOTICE).read().strip()
            os.remove(NOTICE)
        req = load_json(RESTART_FLAG, None) if os.path.exists(RESTART_FLAG) else None
        if req is not None:
            os.remove(RESTART_FLAG)
        msg = "已上線，版本 `%s`（%s）" % (head, git("log", "-1", "--format=%s", "--", "."))
        if notice:
            msg = "%s\n%s" % (notice, msg)
        if req and req.get("channel"):
            ch = self.get_channel(int(req["channel"]))
            if ch:
                await ch.send(msg)
        await self.audit(msg)
        if not self.pumping:  # on_ready fires again after a reconnect
            self.pumping = True
            pump = self.loop.create_task(self.tmux.pump())
            pump.add_done_callback(self.pump_ended)
            self.loop.create_task(self.self_test())  # "ready" only once a hook event has made it all the way
            await self.sync_menu()
            self.loop.create_task(self.catchup.run())  # what the owner said while the bot was down
            if catalog.stale(catalog.load()):
                self.loop.create_task(self.refresh_catalog())
            self.loop.create_task(self.reconcile())  # what this engine version declares, once it is not the one compared
            if guild:
                self.loop.create_task(self.adopt_archived(guild))  # channels moved into the archive by hand (C-63)

    async def adopt_archived(self, guild):
        """archive.Archive.adopt_all() once per start, on its own task (it edits channels one by one; Discord paces
        that). Whatever goes wrong is printed and said in #audit; the bot goes on."""
        try:
            await self.archive.adopt_all(guild)
        except Exception as e:
            traceback.print_exc()
            await self.audit("整理封存分類沒有做完：%s" % e)

    async def reconcile(self):
        """ops/setup.py reconcile --if-changed (C-57 S3) once per start, in a process of its own: it does nothing when
        the engine is the one it compared last, and fills in or asks in #setup what a newer version needs (it talks
        to the bot through the spool and dcctl, so the bot must not wait on it). Not there (an engine before S3):
        nothing. Its output goes to the log; a failure to #audit, once."""
        script = os.path.join(os.path.dirname(config.ROOT), "ops", "setup.py")
        if not os.path.exists(script):
            return
        try:
            proc = await asyncio.create_subprocess_exec(sys.executable, script, "reconcile", "--if-changed", stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), RECONCILE_SECS)
        except asyncio.TimeoutError:
            proc.kill()
            return await self.audit("補齊（setup.py reconcile）%d 秒沒跑完，這次先不管，下次啟動再試" % RECONCILE_SECS)
        except Exception as e:
            return await self.audit("補齊（setup.py reconcile）跑不起來：%s" % e)
        text = out.decode("utf-8", "replace").strip()
        print("[reconcile] exit %s: %s" % (proc.returncode, text[-1000:]), flush=True)
        if proc.returncode != 0:
            await self.audit("補齊（setup.py reconcile）沒有做完（結束碼 %s）：%s" % (proc.returncode, text[-500:]))

    async def own_channels(self, guild):
        """#audit at every start. A new installation (no channel ever set up) gets its basic layout first (C-57: the
        system category at the bottom with #audit and #guide in it, #update in the main area; layout.py), then #chat;
        any other installation is left as it is (its channels are moved by the reconcile, through the spool)."""
        if self.new_install and not self.channels_state:
            self.audit_ch = await layout.first_layout(guild)
            if setup_done():
                await self.first_chat(guild)  # an installation whose setup was done in the terminal (or before C-48)
            else:
                await self.open_setup(guild)  # #chat comes when setup.py done asks for it (open_chat)
        else:
            self.audit_ch = await self.ensure_channel(guild, config.AUDIT_CHANNEL, "機器人做的每件事都記在這裡")
            if not setup_done():  # setup not finished and its channel gone (deleted, or never made): again
                await self.open_setup(guild, resend=False)

    async def self_test(self):
        """Run the real hook (tools/dchook) with a made-up event, as Claude Code would. pump() sees it come out of
        the spool and calls ready(). Logging in is not enough to call a version good: if the way from hook to
        spool to pump is broken every channel goes silent, and the supervisor only rolls back a version that
        never writes `ready`."""
        self.selftest_token = "%d-%d" % (os.getpid(), time.time())
        try:
            proc = await asyncio.create_subprocess_exec(
                os.path.join(config.ROOT, "tools", "dchook"), stdin=asyncio.subprocess.PIPE,
                env=dict(os.environ, DCBOT_ROOT=config.ROOT, DCBOT_CHANNEL=SELFTEST))
            await asyncio.wait_for(proc.communicate(json.dumps(
                {"hook_event_name": "SelfTest", "token": self.selftest_token}).encode()), 30)
        except Exception:
            traceback.print_exc()

    def pump_ended(self, task):
        """pump() never returns. If it ends all the same, the bot would stay online yet silent in every channel:
        exit instead, and the supervisor starts it again."""
        if self.is_closed():
            return
        print("[pump] ended unexpectedly: %r; exiting for the supervisor to restart" % (
            task.exception() if not task.cancelled() else "cancelled"), flush=True)
        self.exit_code = 3
        self.loop.create_task(self.close())

    def ready(self, ev):
        """The self test's event came through: this version works, tell the supervisor (dcbot/ tree = version)."""
        if ev.get("token") != getattr(self, "selftest_token", None):
            return False
        with open(os.path.join(STATE, "ready"), "w") as f:
            f.write("%s %d\n" % (git("rev-parse", "HEAD:./"), int(time.time())))
        print("[selftest] hook → spool → pump ok", flush=True)
        return True

    async def sync_menu(self):
        try:
            n = await slash.sync(self.tree)
            if n is not None:
                await self.audit("已向 Discord 註冊 %d 個 `/` 指令" % n)
            return n
        except Exception as e:  # the "!" commands and plain messages work without it
            await self.audit("註冊 `/` 指令失敗：%s" % str(e)[:300])

    async def refresh_catalog(self):
        """Ask Claude Code which commands and skills it has, and bring Discord's menu in line."""
        cat = await catalog.refresh(os.path.dirname(config.ROOT), self.tree.own)
        if cat is None:
            return None
        slash.add_catalog(self.tree, cat)
        await self.sync_menu()
        return cat

    async def heartbeat(self):
        while True:
            try:
                with open(os.path.join(STATE, "heartbeat"), "w") as f:
                    f.write(str(int(time.time())))
            except OSError:
                pass
            await asyncio.sleep(20)

    def busy_with_work(self):
        """Is something half done that a restart would make happen twice? A post being handed over (dcpost; it
        stays in the spool until done, so a restart redoes it: another --new-claude channel, a message typed
        twice), or a hook event being posted. A print-mode turn as before."""
        return self.active > 0 or bool(getattr(self.posts, "inflight", None)) or getattr(self.tmux, "handling", False)

    async def watch_restart(self):
        """deploy.sh drops a flag; restart once nothing is half done (busy_with_work), or after RESTART_WAIT
        seconds whatever happens. Sessions in tmux are not children of the bot and keep running across it."""
        asked, seen = None, dict((f, mtime(f)) for f in (RESTART_FLAG, ROLLBACK_FLAG))
        while True:
            await asyncio.sleep(1)
            wanted = any(fresh(f, seen[f]) for f in (RESTART_FLAG, ROLLBACK_FLAG))
            asked = (asked or time.time()) if wanted else None
            if wanted and (not self.busy_with_work() or time.time() - asked > RESTART_WAIT):
                self.exit_code = EXIT_RESTART
                await self.audit("重啟中…（%s）" % ("退回上一版" if os.path.exists(ROLLBACK_FLAG) else "套用新版本"))
                await self.tmux.shutdown()
                await self.close()
                return

    async def on_raw_reaction_add(self, payload):
        """📋 on a Claude reply: show the turn behind it in a thread (bot/process.py). 📝 on a /screen picture:
        its text (bot/screens.py)."""
        if payload.user_id != config.OWNER_ID or str(payload.emoji) not in (process.EMOJI, screens.EMOJI):
            return
        try:
            await (process.show if str(payload.emoji) == process.EMOJI else screens.show_text)(self, payload)
        except Exception:
            traceback.print_exc()

    async def on_interaction(self, interaction):
        """Buttons and menus under Claude's questions (slash commands go through self.tree, not here)."""
        try:
            if await self.tmux.asks.on_interaction(interaction) or await self.tmux.menus.on_interaction(interaction) \
                    or await self.tmux.startup.on_interaction(interaction) \
                    or await self.setup.on_interaction(interaction) or await self.archive.on_interaction(interaction) \
                    or await self.terminal.on_interaction(interaction) or await self.posts.on_interaction(interaction) \
                    or await self.files.on_interaction(interaction) or await self.cards.on_interaction(interaction) \
                    or await self.tmux.on_interaction(interaction):
                return
            ref = routes.button_ref((interaction.data or {}).get("custom_id"))
            if interaction.type == discord.InteractionType.component and ref is not None:
                if interaction.user.id != config.OWNER_ID:
                    return await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
                if not ref:
                    return await interaction.response.edit_message(
                        content="這份清單已經過期，按鈕不再有作用。要接上對話請重新打 `/attach`。", view=None)
                await interaction.response.edit_message(view=None)  # one list, one click
                await interaction.followup.send(await self.attach(interaction.channel, ref))
                await self.audit("#%s 按鈕：!attach %s" % (getattr(interaction.channel, "name", "DM"), ref))
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------------ helpers
    async def ensure_channel(self, guild, name, topic):
        ch = discord.utils.get(guild.text_channels, name=name)
        if ch is None:
            try:
                ch = await guild.create_text_channel(name, topic=topic)
            except discord.HTTPException:
                return None
        return ch

    async def open_setup(self, guild, resend=True, first=SETUP_FIRST):
        """#setup (C-48): a Claude in the bot's tmux, in ~/.local/state/altention/setup/ (install.py makes it: the
        setup skill and the one allow rule), permission always auto, given `first` once it is up. Already there with
        its conversation: `first` sent again when `resend` (the reconcile has new questions: open_setup), else left
        alone; there but never set up (a channel of the owner's by that name): left alone, said in #audit. `first`
        None: only opened (the update card's ask_claude hands it its own words). The channel, or None."""
        ch = discord.utils.get(guild.text_channels, name=config.SETUP_CHANNEL)
        st = self.channels_state.get(str(ch.id)) if ch is not None else None
        if ch is not None and not (st and st.get("configured")):
            await self.audit("#%s 已經有了，但不是機器人開的設定頻道，沒有動它" % ch.name)
            return None
        folder = setup_folder()
        if ch is None:
            if not os.path.isdir(folder):
                await self.audit("要開 #%s，可是設定資料夾 %s 還沒準備好（在機器人那台的引擎跑 ./install.sh），先不開"
                                 % (config.SETUP_CHANNEL, folder.replace(os.path.expanduser("~"), "~")))
                return None
            ch = await self.ensure_channel(guild, config.SETUP_CHANNEL, "設定：一題一題回答，答完機器人的功能就照你的選擇開好")
            if ch is None:
                return None
            self.chan(ch).update({"configured": True, "mode": "tmux", "cwd": folder, "permission": "auto"})
            self.save()
        elif not resend:
            return ch
        if first:
            await self.tmux.submit(posts.Origin(ch), first)
        return ch

    async def first_chat(self, guild):
        """A new installation's first start: #chat, a Claude conversation in the bot's own tmux working in the instance
        folder (what the instance's CLAUDE.md is written for), set up already: no settings card. Nothing else is made;
        a channel for changing the bot is opened when wanted (the guide says how)."""
        ch = await self.ensure_channel(guild, config.CHAT_CHANNEL, "跟 Claude 對話的地方（工作目錄是實例資料夾）")
        if ch is None:
            return
        st = self.chan(ch)
        if setup.configured(st):
            return
        inst = remote.alt().instance_dir()
        if inst:
            st["cwd"] = inst
        st.update({"configured": True, "mode": "tmux"})
        self.save()
        try:
            await ch.send("💬 在這裡打字就是跟 Claude 對話（工作目錄 `%s`）。說明在 #%s；不懂的地方直接在這裡問。"
                          % (st["cwd"].replace(os.path.expanduser("~"), "~"), config.GUIDE_CHANNEL))
        except discord.HTTPException:
            pass

    async def audit(self, text):
        print("[audit] " + text, flush=True)
        if self.audit_ch:
            for part in split_message(text, 1900):
                try:
                    await self.audit_ch.send(part)
                except Exception:  # a dropped connection too: the record is printed above, and callers go on
                    pass

    def chan(self, channel):
        st = self.channels_state.setdefault(str(channel.id), {})
        if "cwd" not in st:
            category = getattr(channel, "category", None)
            inherited = None
            if category is not None and not config.not_a_project(category.name):
                inherited = routes.sibling_cwd([c.id for c in category.text_channels if c.id != channel.id],
                                               self.channels_state)
            if inherited:  # a new channel inside a project's category works in that project
                st["cwd"], st["cwd_from"] = inherited, category.name
            else:
                st["cwd"] = os.path.dirname(config.ROOT)  # the engine: shared CLAUDE.md, knowledge and memory
        return st

    def save(self):
        """The one way the channels' state is written (a test holds that nothing else in bot/ writes that file):
        `kind` and `connector` are put on each channel as its flags tell them, for whoever reads the file."""
        for st in self.channels_state.values():
            kinds.stamp(st)
        save_json(CHANNELS, self.channels_state)

    def mode(self, st):
        return st.get("mode") or config.BRIDGE

    def note(self, ch, st):
        """The system-prompt addition that tells Claude where it is."""
        machine = (st.get("home") or {}).get("host") or (st.get("pane") or {}).get("host") or remote.this_machine()
        alt = remote.alt()
        data = os.environ.get("ALT_DATA") or os.path.join(alt.root(), "data")
        text = SYSTEM_NOTE.format(host=machine, channel=getattr(ch, "name", "DM"), cwd=st["cwd"],
                                  root=config.ROOT, engine=os.path.dirname(config.ROOT),
                                  instance=alt.instance_dir() or "這台還沒有實例", progress=os.path.join(data, "progress"))
        if self.mode(st) == "tmux":
            text += TMUX_NOTE.format(alt=remote.altention_cmd(machine), where="tmux attach -t %s" % st["home"]["session"] + (
                                         "（在 %s）" % st["home"]["host"] if st["home"].get("host") else "") if st.get("home") else
                                     "tmux -L %s attach -t %s" % (config.TMUX_SOCKET, st.get("tmux", "")))
        return text

    # ------------------------------------------------------------------ messages
    async def on_message(self, message):
        if message.author.bot:
            return
        if message.author.id != config.OWNER_ID:
            if self.user in message.mentions:
                await self.audit("忽略非擁有者的訊息：%s 在 #%s" % (message.author, getattr(message.channel, "name", "DM")))
            return
        if not self.catchup.mark(message):
            return  # already handled (it came in live while the catch-up read it too)
        if getattr(message.channel, "name", None) == config.GUIDE_CHANNEL:
            return  # the manual: not a place to talk
        self.posts.owner_spoke(getattr(message.channel, "parent_id", None) or message.channel.id)
        if (self.channels_state.get(str(message.channel.id)) or {}).get("archived") and \
                archive.is_shelf(getattr(message.channel, "category", None)):  # any kind (a record channel keeps `plain`: C-63)
            return await self.archive.offer(message.channel)
        if (self.channels_state.get(str(message.channel.id)) or {}).get("plain") or \
                getattr(message.channel, "name", None) in (config.AUDIT_CHANNEL, "notifications"):  # inboxes
            if message.content.strip().split(" ")[0] in ("!setup", "!help", "!status", "!files", "!archive"):
                return await self.command(message.channel, message.content.strip(), message.channel.send)
            return  # a plain channel: for records, the bot stays out of it
        if isinstance(message.channel, discord.Thread):
            parent = message.channel.parent
            pst = self.channels_state.get(str(message.channel.parent_id)) or {}
            if parent is not None and str(message.channel.id) in (pst.get("btw") or {}) and self.mode(pst) == "tmux":
                text = message.content.strip()
                text = text[4:].strip() if text.startswith("/btw") else text
                if text:  # a follow-up in a /btw thread
                    return await self.tmux.btw_ask(parent, message.channel, text)
                return
            # a subagent's or workflow's log, not a conversation
            return await message.channel.send("這個討論串是子代理或 workflow 的過程紀錄，這裡的訊息不會送給 Claude。要說話請回到 <#%d>。"
                                              % message.channel.parent_id)
        if archive.is_shelf(getattr(message.channel, "category", None)) and \
                getattr(message.channel.category, "name", None) != archive.LEGACY:  # (the old "archive": until C-63's adopt)
            return await self.archive.offer(message.channel)
        text = message.content.strip()
        if (self.channels_state.get(str(message.channel.id)) or {}).get("kind") == "files":
            if text.split(" ")[0] in BUILTINS:
                return await self.command(message.channel, text, message.channel.send)
            return await self.files.typed(message.channel, text) if text else None
        if (self.channels_state.get(str(message.channel.id)) or {}).get("kind") == "terminal":
            if text.split(" ")[0] in BUILTINS:  # the bot's own commands still work; "!!" and the like go to the shell
                return await self.command(message.channel, text, message.channel.send)
            text = "\n".join([text] + await attachments.save(message)).strip()
            return await self.terminal.run(message.channel, text) if text else None
        now = text.startswith("!!")  # "!!…": Claude reads it right away, not after the tool call in progress
        if now:
            text = text[2:].strip()
        elif text.startswith("!"):
            return await self.command(message.channel, text, message.channel.send)
        ahead = self.merging.get(message.channel.id)
        if ahead is not None:  # the message before this one is still being put together with a forward: it goes first
            try:
                await asyncio.shield(ahead)
            except Exception:
                pass
        forward = bool(getattr(message, "message_snapshots", None))
        if forward and not text and not message.attachments:
            # A forward alone: the comment usually comes a moment later, wait for it. Its place is taken now, before
            # anything is waited for: who said the original is read meanwhile (forwarded(): a call to Discord), and
            # the message that comes next must find the forward waiting, or it goes ahead of it and alone
            # (#code-review of 6d02f07).
            return self.hold_forward(message, asyncio.ensure_future(self.forwarded(message)))
        text = "\n".join([text] + await attachments.save(message)).strip()
        forwarded = await self.forwarded(message) if forward else ""
        if forwarded and not text:
            return self.hold_forward(message, forwarded)
        held = self.forwards.pop(message.channel.id, None)
        mine = None
        if held:  # this message comments on what was just forwarded: one prompt with both
            held[2].cancel()
            mine = self.merging[message.channel.id] = asyncio.get_event_loop().create_future()
        try:
            if held:
                forwarded = await self.held_text(held[1]) + forwarded
            text = (forwarded + (util.THEN + "\n" if forwarded and text else "") + text).strip()
            if not text:
                return
            text = await self.with_reply(message, text)
            await self.deliver(message, text, now)
        finally:
            if mine is not None:  # (whatever happened: the messages behind it are let on)
                if self.merging.get(message.channel.id) is mine:
                    self.merging.pop(message.channel.id, None)
                if not mine.done():
                    mine.set_result(None)

    FORWARD_WAIT = 3  # seconds a forwarded message waits for the comment that usually follows it

    FORWARD_LOOKUP = 5  # seconds the original of a forwarded message may take to be read (who said it)

    async def original(self, message, ref):
        """The message a forward was made of, read once to learn who said it (Discord's snapshot of it holds its
        text, its time, its embeds and attachments, and no author): (the message, its channel, None), or (None, the
        channel or None, why it could not be read). Waited for in this message's own handling, FORWARD_LOOKUP
        seconds at most: other channels' messages and the bot's round of events do not wait on it."""
        if getattr(ref, "guild_id", None) and getattr(getattr(message, "guild", None), "id", None) not in (None, ref.guild_id):
            return None, None, "在別的伺服器，機器人讀不到"
        ch = self.get_channel(ref.channel_id)
        try:
            if ch is None:
                ch = await asyncio.wait_for(self.fetch_channel(ref.channel_id), self.FORWARD_LOOKUP)  # (a thread gone to sleep)
            return await asyncio.wait_for(ch.fetch_message(ref.message_id), self.FORWARD_LOOKUP), ch, None
        except asyncio.TimeoutError:
            return None, ch, "Discord 沒有回應"
        except discord.NotFound:
            return None, ch, "原訊息或它的頻道已經不在"
        except discord.Forbidden:
            return None, ch, "機器人讀不到那個頻道"
        except Exception as e:
            return None, ch, "讀不到（%s）" % type(e).__name__

    def forward_place(self, ch):
        """(how the channel a forwarded message comes from is called, the state its replies are remembered in)."""
        if ch is None:
            return None, {}
        parent = getattr(ch, "parent", None) if getattr(ch, "parent_id", None) else None
        if getattr(ch, "parent_id", None):  # a thread: its replies, if any, are its parent channel's
            name = "#%s 的討論串「%s」" % (getattr(parent, "name", None) or "?", getattr(ch, "name", "?"))
            return name, self.channels_state.get(str(ch.parent_id)) or {}
        return "#%s" % getattr(ch, "name", "?"), self.channels_state.get(str(ch.id)) or {}

    async def forwarded(self, message):
        """Messages forwarded into this one from another channel, as text for the channel's agent. Whatever goes
        wrong in telling who said them and what else they held (what Discord hands over here was written from the
        library's classes, no real forward at hand), the forward itself is never lost: its text goes plain, saying
        that the rest could not be read."""
        snaps = getattr(message, "message_snapshots", None) or []
        if not snaps:
            return ""
        try:
            return await self.forwarded_whole(message, snaps)
        except Exception:
            # Only what the snapshot surely holds: the first line, the content quoted, and a word that the rest was
            # not found out. Never an empty prompt, never the forward dropped (manager, 2026-10-13).
            traceback.print_exc()
            return "".join(util.forward_context("轉發的訊息", s.content if isinstance(getattr(s, "content", None), str) else "",
                                                notes=["（查不到其他資訊：內部錯誤，已記下）"]) for s in snaps)

    async def forwarded_whole(self, message, snaps):
        """forwarded(), when nothing goes wrong: who said each (the channel's agent, the owner, another conversation,
        a notification…), when, its id and link, and its content without the frame the bot put round it
        (bot/frames.py); attachments saved."""
        ref = getattr(message, "reference", None)
        original, ch, why = (await self.original(message, ref)) if ref is not None and getattr(ref, "channel_id", None) else (None, None, "沒有來源")
        place, st = self.forward_place(ch)
        if ch is None and why and why.startswith("在別的伺服器"):
            place = "別的伺服器"
        out = ""
        for s in snaps:
            embeds = [{"author": getattr(getattr(e, "author", None), "name", None), "title": getattr(e, "title", None),
                       "description": getattr(e, "description", None), "url": getattr(e, "url", None)} for e in getattr(s, "embeds", None) or []]
            if original is not None:
                author = original.author
                what = frames.of(s.content, embeds, by_bot=author.id == getattr(self.user, "id", None), by_owner=author.id == config.OWNER_ID,
                                 remembered=str(ref.message_id) in (st.get("turns") or {}),
                                 author=getattr(author, "display_name", None) or getattr(author, "name", None))
            else:  # who said it is not known: nothing is guessed from how it looks; what a card holds is still its text
                card = next((e["description"] for e in embeds if frames.CARD.match(str(e.get("author") or ""))), None)
                what = {"kind": frames.UNKNOWN, "why": why, "text": s.content or card or ""}
            notes = []
            answered = getattr(getattr(original, "reference", None), "message_id", None) if original is not None else None
            if answered and getattr(original.reference, "type", None) != discord.MessageReferenceType.forward:
                notes.append("（原訊息是回覆 id %s 的那一則）" % answered)
            if getattr(s, "edited_at", None):
                notes.append("（原訊息編輯過）")
            for e in embeds:
                if not frames.CARD.match(str(e.get("author") or "")):
                    bits = [str(x).strip() for x in (e.get("title"), (e.get("description") or "")[:300], e.get("url")) if x]
                    notes.append("（embed：%s）" % "｜".join(bits) if bits else "（embed，沒有文字）")
            for sticker in getattr(s, "stickers", None) or []:
                notes.append("（貼圖：%s）" % getattr(sticker, "name", "?"))
            when = s.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S") if getattr(s, "created_at", None) else None
            shim = type("Forwarded", (), {"attachments": list(s.attachments), "channel": message.channel, "id": message.id})
            saved = await attachments.save(shim) if s.attachments else []
            out += util.forward_context(frames.worded(what, place), what.get("text"), when, getattr(ref, "message_id", None),
                                        getattr(ref, "jump_url", None), notes) + "".join(line + "\n" for line in saved)
        return out

    @staticmethod
    async def held_text(parts):
        """The text of the forwards that wait, in the order they came. A part is its text, or the reading of it
        still under way (forwarded(), which has its own limit of time): waited for, and not cancelled with whoever
        waits (the timer of a forward alone is, when a comment comes)."""
        out = ""
        for part in parts:
            if isinstance(part, str):
                out += part
                continue
            try:
                out += await asyncio.shield(part)
            except asyncio.CancelledError:
                raise
            except Exception:
                traceback.print_exc()
        return out

    def hold_forward(self, message, forwarded):
        """A forward waits for the comment that usually follows it (FORWARD_WAIT seconds), then goes alone.
        `forwarded`: its text, or the reading of it under way. Several in a row go together, in their order."""
        old = self.forwards.pop(message.channel.id, None)
        parts = [forwarded]
        if old:  # several forwards in a row: they go together
            old[2].cancel()
            parts = old[1] + parts

        async def alone():
            await asyncio.sleep(self.FORWARD_WAIT)
            text = await self.held_text(parts)  # (still held meanwhile: a comment that comes now takes it along)
            if self.forwards.get(message.channel.id, (None,))[0] is message:
                self.forwards.pop(message.channel.id, None)
                if text.strip():  # (nothing at all is not handed to the agent)
                    await self.deliver(message, text.strip())
        self.forwards[message.channel.id] = (message, parts, asyncio.ensure_future(alone()))

    async def deliver(self, message, text, now=False):
        """Hand a message (already expanded: attachments, forwards, reply) to the channel's Claude. While Claude
        waits on a single question, the message is the answer to it. `now`: see TmuxBridge.submit."""
        if peers.MENTION.search(text):  # "#st-0" means the session behind that channel
            text = peers.spell_mentions(text, self.channels_state, self.channel_label,
                                        remote.altention_cmd(files.host_of(self.chan(message.channel))))
        st = self.chan(message.channel)
        if self.needs_setup(message.channel, st):  # a new channel: ask where its Claude should live first
            return await self.setup.offer(message.channel, (message, text))
        if st.pop("cwd_from", None):  # say once where a channel opened by hand will work
            await message.channel.send("📁 這個頻道會在 `%s` 工作（沿用「%s」分類裡其他頻道的資料夾）。要換別的地方用 `/cwd`。"
                                       % (routes.short_cwd(st["cwd"]), message.channel.category.name))
            self.save()
        if self.mode(st) == "tmux":
            if await self.tmux.asks.typed(message.channel, text):
                try:
                    return await message.add_reaction("✅")
                except discord.HTTPException:
                    return
            return await self.tmux.submit(message, text, now=now)
        key = str(message.channel.id)
        lock = self.locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            await message.add_reaction("⏳")
        async with lock, self.sem:
            await self.turn(message, text)

    def needs_setup(self, ch, st):
        return (isinstance(ch, discord.TextChannel) and not setup.configured(st)
                and ch.name not in (config.AUDIT_CHANNEL, "notifications"))

    async def on_guild_channel_delete(self, channel):
        """A channel was deleted: forget it. Its Claude is left running wherever it is (a tmux window may hold
        work in progress); #audit says where, so it can be closed or attached again by hand."""
        try:
            await self.tmux.asks.gone(channel)  # a question it still asks: nobody can answer it now (#issues 21)
        except Exception:
            traceback.print_exc()
        try:
            self.tmux.menus.gone(channel)  # a menu shown there waits for nobody now
        except Exception:
            traceback.print_exc()
        st = self.channels_state.pop(str(channel.id), None)
        await routes.unbind_any(channel.id, ((st or {}).get("pane") or {}).get("host"))
        self.setup.cards.pop(channel.id, None)
        self.setup.held.pop(channel.id, None)
        self.tmux.rts.pop(str(channel.id), None)
        self.save()
        if st and (st.get("pane") or st.get("tmux")):
            where = ("tmux `%s`" % st["home"]["session"]) if st.get("home") else \
                tmuxio.label(st["pane"]) if st.get("pane") else "`tmux -L %s attach -t %s`" % (config.TMUX_SOCKET, st["tmux"])
            await self.audit("#%s 被刪了。它的 Claude 沒有關，還在 %s；不需要的話請自己關掉，或用 `/attach` 接到新頻道。"
                             % (channel.name, where))

    async def on_guild_channel_update(self, before, after):
        """A channel renamed (by hand or !rename): its Claude was told the old name when it started, so the next
        message it gets says the new one (no turn of its own just for that). Nothing keeps the old name: other
        places find the channel by its ID, and an old name now fails loudly instead of making a new channel."""
        if getattr(before, "name", None) == getattr(after, "name", None):
            return
        st = self.channels_state.get(str(after.id))
        if not st or not posts.listens(st):
            return
        st["note_next"] = "（機器人通知：這個頻道已從 #%s 改名為 #%s，頻道 ID %d。之後提到或讀這個頻道請用新名字或 ID。）" % (
            before.name, after.name, after.id)
        self.save()
        await self.audit("#%s 改名為 #%s" % (before.name, after.name))

    async def on_guild_channel_create(self, channel):
        """The owner opened a text channel: show the setup card straight away."""
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != config.GUILD_ID:
            return
        try:
            async for entry in channel.guild.audit_logs(limit=5, action=discord.AuditLogAction.channel_create):
                if entry.target is not None and entry.target.id == channel.id:
                    if entry.user is not None and entry.user.id == config.OWNER_ID:
                        if self.needs_setup(channel, self.chan(channel)):
                            await self.setup.offer(channel)
                    return
        except discord.HTTPException:
            pass  # no audit log access: the card comes with the first message instead

    def channel_label(self, cid):
        c = self.get_channel(cid)
        return "#%s" % c.name if c is not None else None

    async def rename(self, ch, st, name):
        """`!rename <name>`: the channel and the Claude Code session get the same name. Where the session comes
        from (machine, tmux, folder) stays in the channel topic and in !status."""
        name = re.sub(r"[^\w-]+", "-", name.strip().lower(), flags=re.UNICODE).strip("-")[:90]
        if not name:
            return "用法：`!rename <新名字>`（例如 `!rename 回測`）"
        try:
            await ch.edit(name=name)
        except discord.HTTPException as e:
            return "頻道改名失敗（Discord 限制每 10 分鐘最多改兩次名字）：%s" % e
        if self.mode(st) == "tmux":
            await self.tmux.submit(slash.Origin(ch), "/rename " + name, out=self.quiet)
        return "已改名為 #%s，Claude Code 那邊的對話名稱也一起改。來源照舊寫在頻道主題和 `!status` 裡。" % name

    async def quiet(self, text):
        pass

    async def with_reply(self, message, text):
        """A message sent as a reply (Discord's "reply" on another message) carries what it answers: Claude only
        sees text, so the replied-to message goes along in front, shortened."""
        ref = getattr(message, "reference", None)
        if ref is None or not getattr(ref, "message_id", None) or text.startswith("/") or \
                getattr(ref, "type", None) == discord.MessageReferenceType.forward:  # a forward is not a reply
            return text
        replied = ref.resolved if isinstance(getattr(ref, "resolved", None), discord.Message) else None
        if replied is None:
            try:
                replied = await message.channel.fetch_message(ref.message_id)
            except discord.HTTPException:
                return text
        cards = util.embed_text(replied.embeds)
        who = "另一個頻道傳來的這則訊息" if cards.startswith("📨") else None
        return util.reply_context(replied.author.bot, "\n".join(x for x in (replied.content, cards) if x),
                                  len(replied.attachments), who=who) + text

    async def turn(self, message, text):
        ch = message.channel
        st = self.chan(ch)
        name = getattr(ch, "name", "DM")
        note = self.note(ch, st)
        status = {"msg": None, "last": 0.0, "lines": []}
        t0 = time.time()

        async def progress(line):
            status["lines"] = (status["lines"] + [line])[-6:]
            now = time.time()
            if now - t0 < 15 or now - status["last"] < 5:
                return
            status["last"] = now
            body = "處理中…（%d 秒）\n```\n%s\n```" % (now - t0, "\n".join(status["lines"]))
            try:
                if status["msg"] is None:
                    status["msg"] = await ch.send(body)
                else:
                    await status["msg"].edit(content=body)
            except discord.HTTPException:
                pass

        self.active += 1
        try:
            async with ch.typing():
                res = await run_turn(text, st.get("session_id"), st["cwd"], note, on_progress=progress,
                                     procs=self.procs, key=str(ch.id), channel_id=ch.id)
        finally:
            self.active -= 1
        st["session_id"] = res.session_id
        self.save()
        if status["msg"] is not None:
            try:
                await status["msg"].delete()
            except discord.HTTPException:
                pass
        body = res.text if not res.error else "出錯了：%s" % res.error
        tools = ", ".join("%s×%d" % kv for kv in sorted(res.tools.items(), key=lambda kv: -kv[1]))
        footer = "-# %s%.0f 秒 · $%.2f" % ((tools + " · ") if tools else "", time.time() - t0, res.cost)
        for part in split_message((body or "（沒有文字回覆）") + "\n" + footer):
            await ch.send(part)
        await self.audit("#%s：%s → %s（%s）" % (name, text.splitlines()[0][:80],
                                                "錯誤" if res.error else "完成", footer[3:]))

    async def resume(self, ch, st, ref):
        """`!resume`: list the directory's recent conversations, or switch this channel to one of them. One at a time
        for a channel: where the conversations are on another machine this waits for that machine, and a second
        `!resume` meanwhile would close the window and switch the channel across the first (#code-review on
        02708ed); it is told to wait."""
        busy = self.__dict__.setdefault("resuming", set())
        if ch.id in busy:
            return "這個頻道正在接回另一個對話，等它完成再下一次 `!resume`。"
        busy.add(ch.id)
        try:
            return await self.resume_now(ch, st, ref)
        finally:
            busy.discard(ch.id)

    async def resume_now(self, ch, st, ref):
        far = sessions.elsewhere(st)
        if far:  # its conversations are in that machine's folder (this machine's folder of the same path, if there
            # is one, holds others: they were listed, and one of them "switched to": #issues 35). Asked of that
            # machine; the list and every answer are worded as for a conversation here.
            items, why = await history.far_sessions(far, st["cwd"])
            if why:
                return "這個頻道的對話在 %s 上，%s，所以列不出那台的對話、也沒有換對話。" % (far, why)
        else:
            items = cc_sessions.recent(st["cwd"])
        current = st.get("session_id")
        taken = [c.get("session_id") for k, c in self.channels_state.items() if k != str(ch.id)]
        if not ref:
            return sessions.listing(items, current, st.get("history", []), taken)
        s = sessions.pick(items, ref)
        if s is None and far:  # an older one than the list shows, by id: asked of that machine, never looked for
            # in this machine's folder (whose conversations are others); too short a ref names none, as here
            if len(ref) >= FIND_MIN:
                s, why = await history.far_sessions(far, st["cwd"], ref)
                if why:
                    return "這個頻道的對話在 %s 上，%s，所以找不了 `%s`、也沒有換對話。" % (far, why, ref)
        elif s is None:
            s = cc_sessions.find(st["cwd"], ref)
        if s is None:
            return "找不到 `%s`。先用 `!resume` 看清單。" % ref
        if s["id"] == current:
            return "這個頻道已經在 `%s` 這個對話上。" % s["id"][:8]
        if s["id"] in taken:
            return "`%s` 正被另一個頻道使用，同一個對話不能同時開兩處。" % s["id"][:8]
        await self.tmux.close(ch)  # one Claude per channel; background work in the old session ends with it
        sessions.remember(st, current)
        sessions.remember(st, s["id"])
        st["session_id"] = s["id"]
        self.save()
        try:
            if far:  # read over there, by the file that machine named (and holds to its own rule again when asked)
                asyncio.ensure_future(history.show_remote(self, ch, st, far, s["id"], s["path"]))
            else:
                await history.show(self, ch, st, s["id"], cc_sessions.transcript(st["cwd"], s["id"]))
        except Exception:
            traceback.print_exc()
        return "已切到對話 `%s`（%s）。下一則訊息會接回它；原本的對話一樣可以用 `!resume` 換回來。" % (
            s["id"][:8], s["title"] or " ".join(s["first"].split())[:40])

    async def attach(self, ch, ref, project=""):
        """`!attach`: list the Claude Code panes in the owner's tmux, or give one its own channel."""
        panes = await tmuxio.claude_panes(everywhere=True)
        if not ref:
            return routes.listing(panes, routes.load())  # command() adds the buttons
        guild = getattr(ch, "guild", None) or self.get_guild(config.GUILD_ID)
        if ref == "all" or ref.startswith(routes.PROJECT):  # everything not attached yet, or one project's share
            groups = routes.by_project(panes, routes.load())
            if ref != "all":
                groups = dict((k, v) for k, v in groups.items() if k == ref[len(routes.PROJECT):])
            done = [await self.attach_one(guild, p, project or name) for name, ps in groups.items() for p in ps]
            return "\n".join(done) if done else "沒有還沒接上的對話%s。" % ("" if ref == "all" else "屬於這個專案")
        pane = routes.pick(panes, ref)
        if pane is None:
            return "找不到 `%s`。先用 `!attach` 看清單。" % ref
        taken = routes.routed_to(pane, routes.load())
        if taken and self.get_channel(int(taken)):
            return "這個對話已經接在 <#%s> 了。" % taken
        st = self.chan(ch) if isinstance(ch, discord.TextChannel) else None
        if st is not None and not setup.configured(st) and ch.name not in (config.AUDIT_CHANNEL, "notifications"):
            return await self.attach_here(guild, pane, ch)  # a new channel nobody set up yet: it becomes this pane's
        return await self.attach_one(guild, pane, project or routes.project_of(pane))

    async def attach_here(self, guild, pane, ch):
        """/attach in a channel still waiting on its setup card: that channel takes the pane, where it is."""
        card = self.setup.cards.pop(ch.id, None)
        if card is not None:
            try:
                await ch.get_partial_message(card).edit(content="✅ 接上了 tmux 的 `%s`。" % routes.window(pane), view=None)
            except discord.HTTPException:
                pass
        said = await self.attach_one(guild, pane, None, into=ch)
        for message, text in self.setup.held.pop(ch.id, []):  # typed before it was set up
            await self.tmux.submit(message, text)
        return said

    async def attach_one(self, guild, pane, project, into=None):
        """Give one pane its channel under the project's category (reusing the one it had, if archived).
        `into`: a channel to use as it is instead."""
        host = pane.get("host") or remote.this_machine()
        if into is not None:
            new, back = into, False
            project = getattr(getattr(into, "category", None), "name", None) or "無"
        else:
            category = await layout.category(guild, project)
            archived = discord.utils.get(guild.categories, name=config.ARCHIVE_CATEGORY)
            kept = archive.remembered_by(self.channels_state, pane)  # an archived channel that followed this pane
            new = self.get_channel(kept) if kept else None
            if new is None:
                new = routes.archived_channel(archived.text_channels if archived else [], host, pane, project)
            back = new is not None
        if into is not None:
            pass
        elif back:  # this pane had a channel before: take it out of the archive (where it came from), its history comes along
            await self.archive.unarchive(new, (self.channels_state.get(str(new.id)) or {}).get("archived") or {}, None,
                                         fallback=category)
        else:
            name = routes.channel_name(pane, project)
            if discord.utils.get(category.text_channels, name=name):  # two Claude panes in one window
                name += "-p" + pane["id"].lstrip("%")
            new = await guild.create_text_channel(name, category=category, topic=routes.topic(host, pane))
        st = self.chan(new)
        st.pop("cwd_from", None)  # an attached session works where it already is
        st.pop("archived", None)
        self.archive.offered.discard(new.id)
        address = {"socket": pane["socket"], "id": pane["id"], "pid": pane.get("pid")}
        if pane.get("host"):
            address["host"] = pane["host"]
        st.update({"pane": address, "pane_session": pane["session"],
                   "cwd": pane["cwd"], "mode": "tmux",
                   "busy": False, "last_active": time.time()})
        # it was started outside the bot and knows nothing of Discord: told with the first message it gets here
        st["note_next"] = ("（機器人通知：這個對話已接到 Discord 的 #%s（頻道 ID %d），使用者會在那裡跟你對話。你每輪最後的回覆會貼到 "
                           "Discord：Discord 不支援表格，用清單。要給使用者看檔案（PDF、圖片、報告）就用 `%s dc post %d "
                           "--file <路徑> \"說明\"` 傳到頻道（上限 8 MB），不要只在這台電腦上打開。）"
                           % (new.name, new.id, remote.altention_cmd(pane.get("host")), new.id))
        self.save()
        await routes.bind_any(pane, new.id)
        await new.send("🔗 這個頻道%s接上了 **%s** 的 tmux `%s:%s`（`%s`）。\n"
                       "在這裡打字會送進那個對話，它的回覆會出現在這裡；你在終端機那邊打的內容也會顯示。"
                       "終端機照常可以用。不需要時用 `!detach` 卸下。"
                       % ("重新" if back else "", host, pane["session"], pane["window"], routes.short_cwd(pane["cwd"], pane.get("host"))))
        if pane.get("host"):  # its transcript is over there: dcremote reads it
            code, out = await remote.dcremote(pane["host"], "backfill", pane["id"], pane["session"], pane["socket"], timeout=60)
            try:
                got = json.loads(out) if code == 0 else {}
            except ValueError:
                got = {}
            if got.get("session_id"):
                st["session_id"] = got["session_id"]
                self.save()
                if not back:
                    self.loop.create_task(self.backfill(new, sessions.backfilled(got)))
            rec = None
        else:
            rec = cc_sessions.of_pane(pane["id"], pane["session"]) or {"cwd": pane["cwd"]}  # no record: the screen decides
        if rec:  # what was said before the channel existed (from the transcript it writes to now)
            screen = (await tmuxio.tmux("capture-pane", "-p", "-J", "-S", "-200", "-t", pane["id"], socket=pane["socket"]))[1]
            sid = cc_sessions.live_session(rec.get("cwd") or pane["cwd"], screen, rec)
            if sid:
                st["session_id"] = sid
                self.save()
            path = cc_sessions.transcript(rec.get("cwd") or pane["cwd"], sid) if sid else None
            if path is None:
                pass
            elif back:  # the channel already holds the earlier history: just say where things stand
                for part in split_message("📜 **目前的最後幾輪**\n\n" + sessions.recap_text(sessions.recap(path))):
                    await new.send(part, silent=True)
            else:
                self.loop.create_task(self.backfill(new, sessions.history_messages(path)))
        return "已接上 `%s`：<#%d>（分類「%s」%s）。" % (routes.window(pane), new.id, project,
                                                   "，沿用之前封存的頻道" if back else "")

    async def backfill(self, ch, messages):
        """Post a session's whole earlier conversation into its new channel, without notifications. Runs on
        its own: a long conversation is a few hundred messages and Discord takes about one per second."""
        try:
            if not messages:
                return
            await ch.send("📜 **以下是接上之前的完整對話**（共 %d 則訊息，貼完會有結尾標記；這段期間照常可以打字）" % len(messages),
                          silent=True)
            for part in messages:
                await ch.send(part, silent=True)
            await ch.send("📜 **以上是接上之前的對話**，從這裡開始是即時的。", silent=True)
        except Exception:
            traceback.print_exc()

    def recap(self, st, arg):
        """`!recap [n]`: the channel's conversation so far, read from Claude Code's own transcript."""
        n = max(1, min(int(arg), 20)) if arg.isdigit() else 3
        sid, cwd = st.get("session_id"), st["cwd"]
        if st.get("pane"):  # the session in the pane may have changed (/clear, a new claude) since attaching
            rec = cc_sessions.of_pane(st["pane"]["id"], st.get("pane_session"))
            sid, cwd = (rec["sessionId"], rec.get("cwd") or cwd) if rec else (sid, cwd)
        if not sid:
            return "這個頻道還沒有對話。"
        pairs = sessions.recap(cc_sessions.transcript(cwd, sid), n)
        return "📜 **最近 %d 輪**\n\n%s" % (len(pairs), sessions.recap_text(pairs)) if pairs else "讀不到這個對話的內容。"

    async def detach(self, ch, st, why=kinds.DETACHED):
        """`!detach`: stop following the session (it keeps running, also a window the bot opened, which from
        then on is just one of the owner's windows) and put the channel away."""
        if not st.get("pane"):
            return "這個頻道沒有接在你的 tmux 上，沒有東西可以卸下（要結束對話用 `/reset`）。"
        home = st.get("home")
        where = ("tmux `%s` 裡的視窗" % home["session"]) if home else tmuxio.label(st["pane"])
        await self.tmux.close(ch, kill=False)
        await routes.unbind_any(ch.id, (st.get("pane") or {}).get("host"))
        place = archive.where_from(ch, ch.guild)  # where it was, for 接回 to put it back there (C-63)
        rec = dict(archive.record(st, why), **place)
        self.channels_state[str(ch.id)] = {"archived": rec, "cwd": st.get("cwd")}  # for 接回
        self.save()
        try:
            shelf = await archive.shelf(ch.guild)
            await ch.edit(category=shelf, topic=archive.topic_with(place.get("topic") or "", rec))
        except discord.HTTPException as e:
            return "已卸下，但頻道沒有移到封存（%s）。對話還在：%s" % (e, where)
        reuse = "" if home else "；之後對同一個 tmux 視窗 `!attach`，會把這個頻道搬回去沿用"
        return "已卸下。對話還在原處繼續跑（%s），機器人不再跟著它。這個頻道移到「%s」，不會再處理這裡的訊息%s。" % (
            where, shelf.name, reuse)

    async def command(self, ch, text, send):
        """A built-in command, from a "!" message or from Discord's "/" menu. `send` posts the answer."""
        cmd, _, arg = text.strip().partition(" ")
        st = self.chan(ch)
        tmux = self.mode(st) == "tmux"
        if st.get("kind") == "files" and cmd in ("!reset", "!resume", "!cwd", "!mode", "!screen", "!key", "!stop", "!detach"):
            await send("這是檔案頻道，`%s` 不適用。直接打字找檔案或打路徑；要改成別的用途，刪掉頻道重開比較乾淨。" % cmd)
        elif st.get("pane") and not st.get("home") and cmd in ("!reset", "!resume", "!cwd", "!mode"):
            await send("這個頻道接的是你自己在 tmux 開的對話，`%s` 不適用。要清空對話就直接發 `/clear`；"
                       "要換對話請 `!detach` 後再 `!attach`。" % cmd)
        elif cmd == "!help":
            await send(HELP)
        elif cmd == "!attach":
            ref, _, project = arg.strip().partition(" ")
            view = None if ref else routes.buttons(await tmuxio.claude_panes(everywhere=True), routes.load())
            text = await self.attach(ch, ref, project.strip())
            if view is None:
                await send(text)
            else:
                await send(text, view=view)
                view.stop()  # clicks arrive in on_interaction by custom_id
        elif cmd == "!detach":
            await send(await self.detach(ch, st))
        elif cmd == "!archive":
            text, view = await self.archive.put(ch)
            await (send(text, view=view) if view is not None else send(text))
        elif cmd == "!setup":
            pane = st.get("pane")
            if st.get("tmux") or (pane and (not st.get("home") or await tmuxio.exists(pane))):
                await send("這個頻道已經有 Claude 或終端機在用了。要換掉它先 `/reset`（或 `/detach`），再打 `/setup`。")
            else:
                self.channels_state[str(ch.id)] = {"cwd": st.get("cwd") or os.path.dirname(config.ROOT)}
                self.save()
                self.setup.cards.pop(ch.id, None)
                await self.setup.offer(ch)
                await send("🛠️ 設定卡在上面。")  # from the "/" menu, an unanswered command shows as failed
        elif cmd == "!talklimit":
            want = arg.strip().lower()
            if want in ("off", "0", "none", "關"):
                st["talk_limit"] = 0
            elif want.isdigit():
                st["talk_limit"] = int(want)
            elif want in ("default", "預設"):
                st.pop("talk_limit", None)
                st.pop("talk_free", None)
            elif want:
                return await send("用法：`!talklimit <次數>`、`!talklimit off`（不限制）、`!talklimit default`")
            self.save()
            limit = st.get("talk_limit", posts.default_limit())
            free = "".join(" <#%d>" % c for c in st.get("talk_free", []))
            await send("這個頻道的 Claude 跟別的頻道的 Claude 連續來回：%s。%s" % (
                "不限制" if limit == 0 else "最多 %d 次（你一插話就重新計算）" % limit,
                "跟這些頻道不限制：%s" % free if free else ""))
        elif cmd == "!rename":
            await send(await self.rename(ch, st, arg))
        elif cmd == "!commands":
            cat = await self.refresh_catalog() if arg.strip() == "refresh" else catalog.load()
            if cat is None:
                await send("還沒有清單（Claude Code 沒有回應）。稍後再試 `!commands refresh`。")
            else:
                menu = "　".join("`/%s`" % e["name"] for e in cat["entries"])
                rest = "　".join("`%s`" % n for n in cat["all"] if n not in [e["run"] for e in cat["entries"]])
                for part in split_message("**選單裡有的 Claude Code 指令和 skill**（%d 個）\n%s\n\n"
                                          "**其他的用 `/cc` 執行**（`command` 欄可以打字搜尋）\n%s\n"
                                          "-# 清單每天更新一次；裝了新的 skill 之後用 `/commands refresh:True` 立刻更新"
                                          % (len(cat["entries"]), menu, rest)):
                    await send(part)
        elif cmd == "!recap":
            for part in split_message(self.recap(st, arg.strip())):
                await send(part)
        elif cmd == "!status":
            up = int(time.time() - self.started)
            busy = sum(1 for c in self.channels_state.values() if c.get("busy")) + self.active
            info = "版本 `%s`（%s）· 已上線 %d 分鐘 · 進行中 %d 個工作\n這個頻道：模式 `%s`，工作目錄 `%s`，對話 `%s`" % (
                git("rev-parse", "--short", "HEAD"), git("log", "-1", "--format=%s", "--", "."), up // 60, busy,
                self.mode(st), st["cwd"], st.get("session_id", "（尚未開始）"))
            if remote.problem:
                info += "\n⚠️ 機器清單讀不出來（%s），先當成只有這台：%s" % (time.strftime("%H:%M", time.localtime(remote.problem[0])), remote.problem[1])
            bad = events.bad_summary(os.path.join(STATE, "events"))
            if bad:
                info += "\n⚠️ 處理失敗、移到 `state/events-bad/` 的事件：%d 個（最新 %s）" % bad
            ctx = st.get("context")
            if st.get("session_id") and not (st.get("pane") or {}).get("host"):  # read it fresh where it is readable
                ctx = transcript.context(cc_sessions.transcript(st["cwd"], st["session_id"])) or ctx
            if ctx:
                info += "\ncontext：%dk / %dk tokens（%s，模型 `%s`）" % (
                    ctx[0] // 1000, transcript.window(ctx[1]) // 1000, events.context_share(ctx)[8:], ctx[1])
            if st.get("pane"):
                how = await tmuxio.standing(st["pane"])
                info += "\nClaude Code 對話名稱：`%s`（其他對話用這個名字傳訊息給它）" % (peers.name_of(st) or "不明")
                info += "\n接上的對話：%s（%s）" % (tmuxio.label(st["pane"]), messages.far("status") if how == "unknown" else
                                               "Claude 不在這個窗格了" if how == "gone" else "處理中" if st.get("busy") else "待命")
            elif tmux and st.get("tmux"):
                shown = await self.tmux.screen(ch, 1)
                info += "\ntmux：`tmux -L %s attach -t %s`（%s%s）" % (
                    config.TMUX_SOCKET, st["tmux"],
                    "未啟動，下一則訊息會自動開" if shown is None else ("處理中" if st.get("busy") else "待命"),
                    "，背景工作 %d 個" % st["bg"] if st.get("bg") else "")
            await send(info)
        elif cmd == "!reset":
            await self.tmux.close(ch)
            sessions.remember(st, st.pop("session_id", None))
            self.save()
            await send("這個頻道已開始新的對話。原本的對話可以用 `!resume` 接回。")
        elif cmd == "!resume":
            await send(await self.resume(ch, st, arg.strip()))
        elif cmd == "!stop" and st.get("kind") == "terminal":
            if st.get("pane"):
                await tmuxio.keys(st["pane"], "C-c")
            await send("已送出 Ctrl-C。")
        elif cmd == "!stop" and tmux:
            done = await self.tmux.stop(ch)
            far = None if done else self.tmux.unanswered(ch)
            await send("已中止。" if done else messages.far("command", said=far) if far else "這個頻道沒有正在跑的工作。")
        elif cmd == "!stop":
            p = self.procs.get(str(ch.id))
            if p:
                p.kill()
                await send("已中止。")
            else:
                await send("這個頻道沒有正在跑的工作。")
        elif cmd == "!cwd":
            path = os.path.expanduser(arg.strip())
            if not os.path.isdir(path):
                await send("找不到資料夾：`%s`" % path)
            else:
                await self.tmux.close(ch)
                st["cwd"] = path
                st.pop("session_id", None)  # a session is tied to its directory
                self.save()
                await send("工作目錄改為 `%s`，並開始新的對話。" % path)
        elif cmd == "!mode":
            want = arg.strip()
            if want not in ("tmux", "print"):
                await send("這個頻道目前是 `%s` 模式。用法：`!mode tmux` 或 `!mode print`" % self.mode(st))
            else:
                await self.tmux.close(ch)  # never two Claude processes on one conversation
                st["mode"] = want
                self.save()
                await send("這個頻道改為 `%s` 模式，對話會接續。" % want)
        elif cmd == "!screen":
            t = await self.tmux.live_target(ch)
            far = None if t else self.tmux.unanswered(ch)
            await screens.post(self, ch, t, send) if t else await send(
                messages.far("command", said=far) if far else "這個頻道沒有正在跑的 tmux 對話。")
        elif cmd == "!files":
            root = (st.get("files") or {}).get("root") or st["cwd"]
            await self.files.open(ch, files.host_of(st), root, send=send, target=arg.strip() or None)
        elif cmd == "!key":
            shown = await self.tmux.key(ch, arg.split())
            far = self.tmux.unanswered(ch) if shown is None and arg.split() else None
            await send(fence(shown) if shown is not None else messages.far("command", said=far) if far else
                       "用法：`!key Down Enter`（這個頻道要有正在跑的 tmux 對話）")
        elif cmd == "!restart":
            save_json(RESTART_FLAG, {"channel": str(ch.id)})
            await send("重啟中（tmux 裡的對話不受影響）。")
        elif cmd == "!rollback":
            save_json(ROLLBACK_FLAG, {"channel": str(ch.id)})
            await send("等目前的工作結束後退回上一個正常版本。")
        else:
            await send("不認得的指令，輸入 `!help` 看說明。")
        await self.audit("#%s 指令：%s" % (getattr(ch, "name", "DM"), text[:100]))


def discord_warnings(out=None):
    """discord.py's own warnings into supervisor.out, one line each, its info and debug lines not: a 429 with its
    route and retry_after ("We are being rate limited…"), a heartbeat blocked, a reconnect. Before C-73 nothing of
    discord.py was written anywhere (log_handler=None), so whether a post had waited on Discord could not be told."""
    log = logging.getLogger("discord")
    if any(getattr(h, "dcbot", False) for h in log.handlers):
        return
    h = logging.StreamHandler(out or sys.stdout)
    h.dcbot = True
    h.setFormatter(logging.Formatter("[discord] %(asctime)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.WARNING)
    log.propagate = False


def main():
    if not config.TOKEN or not config.OWNER_ID:
        sys.exit("缺少設定：請在 %s 填入 DISCORD_TOKEN、OWNER_ID、GUILD_ID" % config.ENV_FILE)
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "bot.pid"), "w") as f:
        f.write(str(os.getpid()))
    tmuxio.configure(socket=config.TMUX_SOCKET, user_tmux=config.USER_TMUX)  # the one place the bot's own server is named
    bot = Bot()
    awaiting.start()  # state/waiting-on-owner.json: as things are now (the bot's state is read), before Discord is reached
    discord_warnings()
    try:
        bot.run(config.TOKEN, log_handler=None)
    finally:
        awaiting.stop()  # ending in the ordinary way: nothing waits on a bot that is not there
    sys.exit(bot.exit_code)


if __name__ == "__main__":
    main()
