"""Discord bot: forwards the owner's messages to a persistent Claude Code session per channel.

Two ways to run a channel's session (`!mode`): "tmux" keeps an interactive Claude Code alive in tmux
(tmux_bridge.py); "print" runs one `claude -p --resume` per message (claude_bridge.py) and is the fallback.

Only the owner (OWNER_ID) is obeyed. Built-in commands (handled without Claude, so they work even when
Claude or a bad deploy misbehaves): !help !status !reset !resume !recap !rename !commands !attach !detach !stop !cwd !mode !screen !key !restart !rollback
"""
import asyncio
import datetime
import io
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import traceback

import discord

from altention import config as config_mod
from altention.connectors.claude_code import sessions as cc_sessions
from altention.connectors.claude_code import transcript

from . import activity, archive, attachments, awaiting, calls, catalog, catchup, lease as lease_mod, cc_health, cc_watch, config, events, files, history, kinds, layout, messages, peers, posts, process, remote, routes, screens, secretbox, sessions, sources, setup, slash, terminal, tmuxio
from .claude_bridge import run_text, run_turn
from .tmux_bridge import NotOpened, TmuxBridge, fence
from . import resets
from . import agentanswer, ask as ask_mod, envfile, frames, ledger, masking, msgtext, perms, postbox, statewrite, update_cards, util, where as where_mod, reconcile, rename as rename_mod, moving
from .util import split_message
from .pauses import clock as clock_shown  # (a time as the owner reads it: the instance's zone)
from . import deliver as outgoing  # (C-172a P1b; the Bot has a deliver() of its own)

STATE = config.STATE


def crashing_from_env(env=os.environ):
    """The supervisor's word (DCBOT_CRASHING): started while crashing (lease.py's note). Taken out of the
    environment, so nothing this bot starts carries it."""
    return env.pop("DCBOT_CRASHING", "") == "1"


CRASHING = crashing_from_env()
CHANNELS = os.path.join(STATE, "channels.json")
RESTART_FLAG = os.path.join(STATE, "restart-requested")
ROLLBACK_FLAG = os.path.join(STATE, "rollback-requested")
NOTICE = os.path.join(STATE, "notice.txt")
EXIT_RESTART = 42
RESTART_WAIT = 60  # seconds a restart waits for half-done work (posts, events) before going anyway
WINDOW_LOCK_SECS = 60  # C-162: how long the first held turn waits for the window's queue (then it goes on in print)
WINDOW_SAY_SECS = 5  # C-162: a held channel waiting for its window's turn says so after this long
SELFTEST = "selftest"  # the channel of the made-up event Bot.self_test() sends through the hook
BUILTINS = ("!help", "!status", "!setup", "!talklimit", "!reset", "!resume", "!recap", "!rename", "!where", "!move", "!commands", "!attach", "!detach", "!archive", "!stop",
            "!cwd", "!mode", "!screen", "!key", "!files", "!restart", "!rollback", "!cctest",
            "!agentanswers")

AGENT_ANSWERS_ON = "別的頻道的 Claude 可以替你回答這個頻道的提問、終端機選單和計畫（每次都記在 #audit）。要關掉：`!agentanswers off`"  # C-176; #release
AGENT_ANSWERS_OFF = "這個頻道不讓別的頻道的 Claude 代答，提問、選單和計畫只有你能回答。要打開：`!agentanswers on`"
AGENT_ANSWERS_USAGE = "用法：`!agentanswers on`、`!agentanswers off`，不加字是看現在的設定"

HELP = """**內建指令**（不經過 Claude，Claude 壞掉時也能用）
`!status` 機器人狀態　`!reset` 這個頻道開新對話　`!stop` 中止這個頻道正在跑的工作
`!resume` 列出最近的對話　`!resume <編號或 id 開頭>` 接回那個對話（`!reset` 之後想回去時用）
`!rename <名字>` 頻道和 Claude Code 對話一起改名　`!where` 這個頻道的 Claude 現在在哪裡（機器、tmux、對話、資料夾）　`!move session <名字>`／`!move tmux bot`／`!move tmux user <session>` 把這個頻道的 Claude 搬到別的 session 或 tmux　`!recap [幾輪]` 顯示這個頻道的對話最近幾輪（預設 3）　`!commands [refresh]` Claude Code 的指令和 skill 清單
`!attach` 列出這台機器 tmux 裡的 Claude Code　`!attach <編號> [專案名]` 幫它開一個頻道接上　`!attach project:<專案名>` 或 `!attach all` 一次接上多個　`!detach` 卸下這個頻道　`!archive` 封存這個頻道（任何種類；打字可以接回或恢復）
`!cwd <路徑>` 設定這個頻道的工作目錄　`!restart` 重啟機器人　`!rollback` 退回上一個正常版本
`!mode tmux|print` 切換這個頻道的模式（tmux：常駐對話，可中途補充、背景工作會回報；print：每則訊息跑一次，備用）　`!mode retry` 再試一次一般模式
`!cctest` 用真的 Claude Code 檢查一次（約 5 次 Haiku 對話；通過的版本會留一份，之後新版壞了可以退回）
`!screen` 看 Claude 的終端機畫面　`!key <按鍵…>` 送按鍵（例如 `!key Down Enter`、`!key Escape`）
`!files [路徑]` 檔案瀏覽器（從這個頻道的資料夾開始，可以點進資料夾、看檔案、找檔案）
其他訊息都會交給這個頻道的 Claude 對話處理；`/` 開頭的訊息是 Claude Code 的指令（例如 `/model`）。
打 `/` 會跳出選單的那幾個（`/status` `/reset` `/resume` `/stop` `/screen` `/key` `/mode` `/cwd` `/attach` `/detach` `/archive` `/usage` `/context` `/compact`）是註冊在 Discord 的版本，功能跟對應的 `!` 指令或 Claude Code 指令相同。"""

SYSTEM_NOTE = """你是使用者在 Discord 上的助理機器人，背後是 Claude Code，可以在這台機器（{host}）上執行指令、讀寫檔案、用 SSH 連到其他機器。
- 現在在 Discord 頻道 #{channel}，工作目錄 {cwd}。你的回覆會直接貼到 Discord：用繁體中文、精簡、先講結論；長內容放程式碼區塊或分點。
- 管理 Discord（頻道、分類、主題、發訊息）用 `{root}/tools/dcctl`，先跑 `{root}/tools/dcctl --help`。對話內容不在你的上下文裡時（新對話、使用者提到「剛剛講的」），用 `{root}/tools/dcctl history {channel}` 讀這個頻道最近的訊息；討論串（子代理、workflow、/btw）用 `dcctl threads {channel}` 列出，再用 `dcctl history <討論串 ID>` 讀。刪除頻道、刪檔、強制推送等破壞性動作，先在對話中問使用者並得到明確同意。
- 機器人自己的程式碼在 {root}（git repo）。修改機器人請照 {root}/CLAUDE.md 的流程（測試 → `./deploy.sh` → 自動重啟，失敗會自動退回）。
- 機器清單和設定在實例（{instance}：machines.json、plugins.json），服務管理在引擎 {engine}（ops/apply.py、ops/doctor.py）；進度插件整理的各專案進度在 {progress}（STATUS.md、projects/*/STATUS.md）。
- 絕對不要讀取、輸出或修改 ~/.config/altention/secrets/ 和 ~/.config/dcbot/ 裡的任何東西（那裡是 token 和金鑰）。
- 最後的回覆裡有要使用者本人決定或動手、不回就沒辦法往下的事時，回覆的第一行用「{mark}」開頭，接一句重點：機器人看到它才會 @ 使用者。在等別的頻道或別的 Claude、已經交給別人去問、報告做完、列出之後可以做的事，都不算。同一件事使用者還沒回之前只加一次，不要每則都加；用 AskUserQuestion 按鈕提問或貼出計畫時也不要加。"""

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


def drop_board_marks():
    """C-181: state/heartbeat and state/lease-view.json taken away as the bot steps down (give_up(), fenced_out()), so
    the status boards here stop at once instead of posting up to a minute more (#code-review on 68d35325). Not taken
    away is not a failure: stepping down goes on."""
    for name in ("heartbeat", "lease-view.json"):
        try:
            os.remove(os.path.join(STATE, name))
        except OSError:
            pass


MADE = set()  # folders save_later() made sure of (once each: a check on NFS is a request too)


def save_later(path, obj):
    """`obj` as JSON at `path`, written off the event loop (statewrite: C-175). The text is made here, so it is what
    `obj` is now; the write follows in the writer thread, a newer save of the same file folding an older one in."""
    folder = os.path.dirname(path)
    if folder not in MADE:
        os.makedirs(folder, exist_ok=True)
        MADE.add(folder)
    statewrite.WRITER.put(path, json.dumps(obj, ensure_ascii=False, indent=1))


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


def talk_limit_said(st, default):
    """What !talklimit answers: this channel's limit, and (#release 11-03) that between two channels the larger of
    their two limits counts (posts.pair_limit()), so setting one side alone does not stop a pair whose other side
    has the default (#discord-control met it). Off is said as before."""
    limit = st.get("talk_limit", default)
    free = "".join(" <#%d>" % c for c in st.get("talk_free", []))
    free = "跟這些頻道不限制：%s" % free if free else ""
    if limit == 0:
        return "這個頻道的 Claude 跟別的頻道的 Claude 連續來回：不限制。%s" % free
    unset = "沒設的頻道不限制" if default == 0 else "沒設的頻道是 %d 次" % default  # (an installation that chose none)
    return ("這個頻道的上限：最多 %d 次（你一插話就重新計算）。兩個頻道互傳時，以兩邊上限比較大的為準（%s），"
            "任一邊設成 off 就不限制。要在 %d 次停下，對方頻道也要設成 %d 次以下。%s" % (limit, unset, limit, limit, free))


RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+$")


def version_note():
    """What is said after the version in "已上線，版本 `abc1234`（…）" and !status (C-76): the release when HEAD is one
    (a vX.Y.Z tag on it), else HEAD's own subject. It was the last commit that touched dcbot/ (`log -- .`): the
    release merge or heading on top was not what was said, and a version looked older than it was."""
    tags = sorted((t for t in git("tag", "--points-at", "HEAD").split() if RELEASE_TAG.match(t)),
                  key=lambda t: tuple(int(n) for n in t[1:].split(".")))  # v0.3.10 after v0.3.9
    return tags[-1] if tags else git("log", "-1", "--format=%s", "HEAD")


FIND_MIN = 8  # characters of an id's start that name a conversation not in the list (the connector's sessions.find())


def holds_board(cid):
    """Whether a channel ID is the one a plugin on this machine posts its board in (its plugin.json "channels" entry
    of kind "board", the instance setting it names): read from the files, so run in a thread."""
    try:
        from altention import plugin as alt_plugin
        for _, _, d, where, _ in alt_plugin.on_this_machine()[0]:
            for c in (d or {}).get("channels") or [] if where == "on" else []:
                if isinstance(c, dict) and c.get("kind") == "board" and isinstance(c.get("setting"), str):
                    if str(config_mod.setting(c["setting"])) == str(cid):
                        return True
    except Exception:
        traceback.print_exc()
    return False


RECONCILE_SECS = 300  # setup.py reconcile at a start: given up after this long (it moves channels, may run dcctl guide)
SETUP_FIRST = "請用 setup skill 帶使用者做完設定。"  # what #setup's Claude is told first (setup-in-discord.md 2)
SETUP_WAIT_EVERY = 5  # seconds between looks at #setup's folder while the install has not made it yet


def setup_folder():
    """#setup's working folder (install.py makes it), from HOME at call time."""
    return os.path.expanduser("~/.local/state/altention/setup")


def setup_folder_ready():
    """Has the install made #setup's folder? Its .claude/settings.json is there (setup.py write_setup_folder writes
    it first thing after the folder; a Claude started in the bare folder would lack its allow rule)."""
    return os.path.isfile(os.path.join(setup_folder(), ".claude", "settings.json"))


def setup_done():
    """Is the installation's setup done? The instance's setup.done; no such key (an installation from before C-48),
    or settings that cannot be read: done (nothing is opened on a guess)."""
    try:
        return remote.alt().setting("setup.done") is not False
    except Exception:
        traceback.print_exc()
        return True


def client_options():
    """discord.Client's options. Mentions ping nobody unless a send says otherwise (messaging review P0-1): a reply,
    a card or a quoted <@…> or @everyone in what Claude wrote pings no one; the owner is pinged only through
    bot/calls.py, whose sends pass calls.allowed(). From the environment only DCBOT_PROXY (the C-163 drill's rig puts
    Discord behind a proxy it can stop: the gateway and REST alike go through it); never an instance setting, nor in
    the manual: for the drill alone."""
    proxy = os.environ.get("DCBOT_PROXY", "").strip()
    return dict({"allowed_mentions": discord.AllowedMentions.none()}, **({"proxy": proxy} if proxy else {}))


def proxy_shown(proxy):
    """The proxy as #audit may show it: host:port, never a user or password in it (#code-review on e01a8c2)."""
    try:
        import urllib.parse
        u = urllib.parse.urlsplit(proxy)
        return "%s:%s" % (u.hostname, u.port) if u.hostname else "?"
    except ValueError:
        return "?"


def in_drill_rig():
    """The C-163 drill's rig and nothing else (altention.standby.drill_rig: ALTENTION_STANDBY_TEST=1 and a scratch
    HOME): what only the drill may change asks this (manager 11-08)."""
    try:
        from altention import standby as alt_standby
        return bool(alt_standby.drill_rig())
    except Exception:
        return False


def drill_failback_taken():
    """DCBOT_DRILL_FAILBACK_SECS is set right (seconds, at least 60) and this is the drill's rig."""
    raw = os.environ.get("DCBOT_DRILL_FAILBACK_SECS", "").strip()
    return raw.isdigit() and int(raw) >= 60 and in_drill_rig()


def drill_failback_secs():
    """How long the primary must be up before the bot goes back to it: lease.FAILBACK_SECS, or in the drill's rig
    alone DCBOT_DRILL_FAILBACK_SECS (seconds, at least 60), so items after a takeover need not wait half an hour
    each (manager 11-08; item 5 itself still runs the real 30 minutes). Set anywhere else: ignored, said in #audit."""
    if drill_failback_taken():
        return int(os.environ["DCBOT_DRILL_FAILBACK_SECS"].strip())
    return lease_mod.FAILBACK_SECS


def drill_sender():
    """The webhook whose messages the C-163 drill's rig sends for the owner (DCBOT_DRILL_SENDER, its ID), None when not
    set or not in the rig. It lets a webhook tell Claude what to do: only behind the same gate as the standby's test
    switch (ALTENTION_STANDBY_TEST=1 and a scratch HOME: altention.standby.drill_rig), never in a real installation
    (manager 11-08); set there, it is ignored and on_ready says so in #audit."""
    raw = os.environ.get("DCBOT_DRILL_SENDER", "").strip()
    if not raw.isdigit() or not in_drill_rig():
        return None
    return int(raw)


async def say_lane_kept(bot):
    """At a handover (fenced_out(), give_up()): another machine's events still kept for a lane, said in #audit
    (TmuxBridge.say_lane_kept()). Nothing here may keep the bot from closing: never raises."""
    try:
        tmux = getattr(bot, "tmux", None)
        if tmux is not None:
            await tmux.say_lane_kept(handover=True)
    except Exception:
        traceback.print_exc()


def stepped_down_note(bot):
    """After a step-down within minutes of `bot` saying it is up: that "已上線" was this machine's, which steps down at
    once (the drill's item 11, manager: the line stays, it is the deploy's sign). Said once."""
    at = getattr(bot, "online_at", None)
    if not isinstance(at, (int, float)):
        return ""
    bot.online_at = None
    return messages.lease("stepped_down_note") if time.time() - at < 300 else ""


def engines_note(got):
    """Section 3.4 as the manager ruled (11-08): the engine versions of the two may differ (not blocking); when
    they do, the takeover's notice and #audit line say both."""
    e = (got or {}).get("engines")
    if not (isinstance(e, dict) and e.get("theirs") and e.get("mine")):
        return ""
    return messages.lease("engines_differ", primary=got.get("primary"), standby=remote.this_machine(),
                          theirs=e["theirs"], mine=e["mine"])  # (said already: standby.version_said())


USER_PREFIX = "user:"  # custom_ids a tool's message carries (bot/rules.py); the bot's own never begin so
USER_BUTTON_SAID = "這個按鈕還沒有接處理（是用工具發的訊息上的按鈕，目前按了不會有動作）。"


class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, **client_options())
        self.new_install = not os.path.exists(CHANNELS)  # no channel state at all, not one that could not be read
        self.channels_state = load_json(CHANNELS, {})
        where_mod.context = self.where_context  # (where.put(): a channel's place and its record written together)
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
        self.perms = perms.Perms(self)  # its own permissions: #audit, the #update card once, doctor (C-191)
        self.renamer = rename_mod.Renamer(self)  # C-201 2b: a rename taken along by all the channel's names
        self.mover = moving.Mover(self)  # C-201 2c1: /move session, /move tmux
        self.looks = reconcile.Looks(self)  # C-201: whether each channel's pane is where its record says
        self.launcher = activity.Activity(self)  # the 打開 buttons of plugins' Activity pages (C-54a; discord.Client has an `activity`)
        self.secrets = secretbox.Secrets(self)  # the 填入 buttons for plugins' secrets (C-77 G3b)
        self.tmux.asks.on_done = self.posts.answered  # a question announced in another channel: update that notice
        self.files = files.Files(self)
        self.catchup = catchup.Catchup(self)
        self.lease = None  # C-163: only with a standby (lease_mod.enabled()); without one no lease code ever runs
        self.held_back = []  # (clock, message) held back while leader_ok() is false (C-163 section 1.3)
        slash.add_catalog(self.tree, catalog.load())  # Claude Code's own commands, as last asked
        self.pumping = False
        self.procs = {}
        self.active = 0
        self.sem = asyncio.Semaphore(config.MAX_PARALLEL)
        self.sem_waiting = 0  # print turns waiting for a place (C-162: said to a channel held down)
        self.audit_ch = None
        self.exit_code = 0
        self.started = time.time()

    # ------------------------------------------------------------------ lifecycle
    async def setup_hook(self):
        self.loop.create_task(self.heartbeat())
        self.loop.create_task(self.watch_restart())

    async def on_ready(self):
        remote.on_stale = lambda host: self.loop.create_task(self.stale_owner(host))  # (C-163: #audit, once a machine)
        if client_options().get("proxy"):  # (the drill's DCBOT_PROXY left behind must not go unseen: #code-review-2)
            self.loop.create_task(self.audit(messages.lease("proxy_on", proxy=proxy_shown(client_options()["proxy"]))))
        if os.environ.get("DCBOT_DRILL_SENDER", "").strip():  # (said either way: taken in the rig, ignored elsewhere)
            self.loop.create_task(self.audit(messages.lease("drill_sender_on" if drill_sender() else "drill_sender_off")))
        if os.environ.get("DCBOT_DRILL_LEASE_READ_FAIL", "").strip():  # (said either way: the drill's item 15 switch)
            self.loop.create_task(self.audit(messages.lease("drill_read_fail_on" if in_drill_rig() else "drill_read_fail_off")))
        if os.environ.get("DCBOT_DRILL_FAILBACK_SECS", "").strip():
            self.loop.create_task(self.audit(  # (taken or not, never by comparing with the default: 1800 is taken too)
                messages.lease("drill_failback_on", secs=drill_failback_secs()) if drill_failback_taken()
                else messages.lease("drill_failback_off")))
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
        msg = "已上線，版本 `%s`（%s）" % (head, version_note())
        if notice:
            msg = "%s\n%s" % (notice, msg)
        if req and req.get("channel"):
            ch = self.get_channel(int(req["channel"]))
            if ch:
                await outgoing.deliver(ch, msg, intent="status")
        await self.audit(msg)
        self.online_at = time.time()  # (a step-down soon after says the line above was this machine's)
        if not self.pumping:  # on_ready fires again after a reconnect
            self.pumping = True
            pump = self.loop.create_task(self.tmux.pump())
            pump.add_done_callback(self.pump_ended)
            self.loop.create_task(self.self_test())  # "ready" only once a hook event has made it all the way
            self.loop.create_task(self.cc_start())  # C-162: a probe left by the last run closed; the grace after a start
            self.start_lease(guild)  # C-163: nothing at all without a standby
            self.loop.create_task(self.settle_settings())  # C-110: the old settings file, in the background
            await self.sync_menu()
            if catalog.stale(catalog.load()):
                self.loop.create_task(self.refresh_catalog())
            self.loop.create_task(self.reconcile())  # what this engine version declares, once it is not the one compared
            self.perms.soon()  # C-191: in the background, from the cache; never in the pump's way
            self.loop.create_task(self.looks.run())  # C-201: now and every 2 minutes, a task of its own
            self.loop.create_task(self.launcher.watch())  # whether the Activities are on, known before a press (C-54a)
            if guild:
                self.loop.create_task(self.adopt_archived(guild))  # channels moved into the archive by hand (C-63)
        # What the owner said while the bot was down, or while the connection was lost (C-170: every on_ready; a
        # new session after a long outage gets nothing replayed by Discord)
        self.loop.create_task(self.catchup.run())

    async def on_disconnect(self):
        self.catchup.cut()  # where the next catch-up reads from, if the session is not resumed

    async def on_resumed(self):
        self.catchup.resumed()  # Discord replays a resumed session's events

    async def cc_start(self):
        """Once per start (C-162): the probe session the last run wrote down and left (a restart in the middle of a
        probe) is closed, that one only; then what failed during the grace after the start is looked into."""
        try:
            left = cc_health.entry().get("probe_session")
            if left:
                await tmuxio.kill(left)
                cc_health.note_probe_session(None)
            await self.tmux.cc.after_grace()
            await self.tmux.cc.ticking()
        except Exception:
            traceback.print_exc()

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
            self.keep_audit(self.audit_ch)
            if setup_done():
                await self.first_chat(guild)  # an installation whose setup was done in the terminal (or before C-48)
            else:
                await self.open_setup(guild)  # #chat comes when setup.py done asks for it (open_chat)
        else:
            self.audit_ch = self.recorded_audit(guild) or \
                await self.ensure_channel(guild, config.AUDIT_CHANNEL, "機器人做的每件事都記在這裡")
            self.keep_audit(self.audit_ch)
            if not setup_done():  # setup not finished and its channel gone (deleted, or never made): again
                await self.open_setup(guild, resend=False)

    def recorded_audit(self, guild):
        """#audit by the ID the bot keeps of it (C-201 6.2): renamed in Discord, it is still the one; found by its
        name only when there is no such record or its channel is gone."""
        for cid, st in self.channels_state.items():
            if isinstance(st, dict) and st.get("system") == "audit" and str(cid).isdigit():
                ch = discord.utils.get(guild.text_channels, id=int(cid))
                if ch is not None:
                    return ch
        return None

    def keep_audit(self, ch):
        """Keep #audit's ID: its channel state marked plain (a record channel: the bot stays out of it, it asks for no
        setup) and system "audit"; a record left on another channel (an #audit deleted since) is dropped."""
        if getattr(ch, "id", None) is None:
            return
        for cid, st in self.channels_state.items():
            if isinstance(st, dict) and st.get("system") == "audit" and cid != str(ch.id):
                st.pop("system")
        st = self.chan(ch)
        if (st.get("system"), st.get("plain"), st.get("configured")) != ("audit", True, True):
            st.update({"system": "audit", "plain": True, "configured": True})
            self.save()

    def is_audit(self, ch):
        """Is it the bot's #audit: the one it uses, or the one its record names (not by the name)."""
        return ch is not None and (ch.id == getattr(getattr(self, "audit_ch", None), "id", None) or
                                   (self.channels_state.get(str(ch.id)) or {}).get("system") == "audit")

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
                if self.leader_ok() and not getattr(self, "lease_over", False):  # C-163: written only while this machine
                    # is the bot (dc post, the boards and doctor read it as "the bot is here"); never again once it steps
                    # down (C-181: drop_board_marks() took it away)
                    with open(os.path.join(STATE, "heartbeat"), "w") as f:
                        f.write(str(int(time.time())))
                if getattr(self, "standby_on", False) is True and getattr(self, "lease", None) is None and self.gateway_up():
                    # C-163, only with a standby whose #bot-lease is gone (nothing changes without one: #code-review-2
                    # on 4dc8fe8, manager): no lease reads to say Discord
                    self.discord_ok()  # was reached (#bot-lease gone): the gateway's own word, else a standby's watch
                    # would call this bot cut off from Discord (#code-review on bb2f2f2, manager)
            except OSError:
                pass
            await asyncio.sleep(20)

    # ------------------------------------------------------------------ C-163: the lease
    def leader_ok(self):
        """Is this machine the bot right now? Always, without a standby (no Lease: nothing of C-163 runs). Not once
        state/fenced names a bigger epoch than its own (the bot that took over wrote it: section 1.4); the bot then
        ends (fenced_out())."""
        if getattr(self, "lease", None) is None:
            return True
        if getattr(self, "failing_back", False) is True:  # C-163 section 5: handing the place back; nothing more handled
            return False  # here (messages held, pump, posts and pushes stopped: #code-review, #code-review-2 on 8778f8d)
        fenced = lease_mod.fenced_epoch(os.path.join(STATE, "fenced")) if self.lease.epoch_known() else None
        if fenced is not None and fenced > self.lease.epoch:  # (once its own epoch is known: lease.epoch_known())
            if not getattr(self, "lease_over", False):
                self.lease_over = True
                self.loop.create_task(self.fenced_out(fenced))
            return False
        return self.lease.ok()

    async def fenced_out(self, epoch):
        drop_board_marks()  # C-181: the boards here stop at once, not when these grow old
        await self.audit("另一台已經接手機器人，這台的機器人結束" + stepped_down_note(self))
        self.held_back = []
        self.exit_code = 4
        self.arm_end()
        await say_lane_kept(self)  # (C-174: the replies among them are posted again by replay_all there)
        await self.close()  # (no leader: false edit: the place is taken already; the supervisor waits while fenced)
        self.end_now()  # (#code-review on ac4bed6: a reconnect asleep kept a fenced bot too, and failback waited on it)

    def gate_note(self):
        """What tools/dclease could not do before this start (state/lease-gate-note.json), once: a setup trouble at
        once, a connection trouble when it lasted CONNECTION_SAY seconds (manager 11-08)."""
        path = os.path.join(STATE, "lease-gate-note.json")
        try:
            with open(path) as f:
                got = json.load(f)
            os.remove(path)
        except (OSError, ValueError):
            return None
        if not isinstance(got, dict):
            return None
        mins = int((time.time() - (got.get("since") or time.time())) // 60)
        if got.get("kind") == "connection" and mins < 10:
            return None
        return messages.lease("gate_" + ("setup" if got.get("kind") == "setup" else "connection"),
                              why=got.get("why") or "?", minutes=mins, channel=config.LEASE_CHANNEL)

    def took_over(self):
        """A takeover by tools/dclease on this machine (state/took-over.json): its #audit line, once."""
        path = os.path.join(STATE, "took-over.json")
        try:
            with open(path) as f:
                got = json.load(f)
            os.remove(path)
        except (OSError, ValueError):
            return None
        if not isinstance(got, dict) or got.get("stopped"):
            return None
        self.taken_from = got  # (adopt_primary(): the old bot machine's channels, once this one is the bot)
        self.mark_pending(got.get("primary"))
        self.take_questions(got.get("primary"))
        return messages.lease("audit_took_over", standby=remote.this_machine(), epoch=got.get("epoch"),
                              primary=got.get("primary"), beat=got.get("primary_lt") or "?", ssh=got.get("ssh") or "ssh ?",
                              checks=(messages.lease("checks_stale", when=got["stale"]) if got.get("stale") else
                                      messages.lease("checks_ok")) + engines_note(got))

    def start_lease(self, guild):
        """With a standby (a machine with the role lease_mod.ROLE), the lease round, on a task of its own."""
        machines = remote.inventory().get("machines") or {}
        if not lease_mod.enabled(machines):
            return None
        name = config.LEASE_CHANNEL

        def channel():
            return discord.utils.get(guild.text_channels, name=name) if guild else None
        self.lease = lease_mod.Lease(remote.this_machine(), list(machines), channel, self.user.id, crashing=CRASHING,
                                     mark=os.path.join(STATE, "lease-true-at"))
        remote.LEASED = True  # (nothing reaches another machine before the first round's epoch: remote.run())
        self.standby_on = True  # (the list has a standby: kept when the lease goes for want of #bot-lease)
        self.loop.create_task(self.push_rounds())  # C-163 3.1: the state a takeover needs, to the other machines
        line = self.took_over()
        if line:
            self.loop.create_task(self.audit(line))
            self.loop.create_task(self.adopt_primary(self.taken_from))
        else:  # a restart after a takeover: channels the old bot machine did not answer for are tried again (no
            left = self.pending_machine()  # notice again; #code-review on 9d2b7bd)
            if left:
                self.adopting = True
                self.loop.create_task(self.adopt_primary({"primary": left}, notify=False))
        line = self.gate_note()
        if line:
            self.loop.create_task(self.audit(line))
        line = self.recovered_note()
        if line:
            self.loop.create_task(self.audit(line))
        wanted = load_json(os.path.join(STATE, "replay-wanted.json"), None)
        if wanted is not None:  # a failback, or a standby gone down (the gate's mark): the questions waiting there
            try:  # taken in at once, replies left unposted once this machine is the bot
                os.remove(os.path.join(STATE, "replay-wanted.json"))
            except OSError:
                pass
            self.take_questions((wanted or {}).get("from") if isinstance(wanted, dict) else None)
            self.loop.create_task(self.replay_when_leading())
        task = self.loop.create_task(self.lease_rounds())
        task.add_done_callback(self.lease_ended)
        return task

    def lease_ended(self, task):
        """The lease's task ended while the bot runs (an error it did not expect): leader_ok() would stay false
        for good, every message held, nothing giving the place up. The bot ends instead (#code-review-2 on
        6550957), as pump_ended does."""
        if self.is_closed() or self.lease is None or getattr(self, "lease_over", False):
            return
        print("[lease] ended unexpectedly: %r; exiting" % (task.exception() if not task.cancelled() else "cancelled"),
              flush=True)
        self.exit_code = 3  # a crash, as pump_ended's (4 is the place given up: #code-review-2 on ccd8baa)
        self.loop.create_task(self.close())

    async def lease_rounds(self):
        """Every ROUND_SECS: write, read (lease.round()). Holding: what was held back is let through, Discord
        reached noted (state/discord-ok). Another machine holds it with a bigger epoch, or leader_ok() false for
        YIELD_SECS: the place is given up, the bot ends (the supervisor follows the lease from there)."""
        while not self.is_closed():
            try:
                if await self.lease_round():
                    return
            except Exception:  # (one round's trouble is the next round's to see; leader_ok() lapses meanwhile)
                traceback.print_exc()
            await asyncio.sleep(lease_mod.ROUND_SECS)

    async def lease_round(self):
        """One lease round (lease_rounds()); True when the rounds are over (no #bot-lease, or the place given up)."""
        reads = self.lease.reads
        holder = await self.lease.round()
        if self.lease.reads != reads:  # a good read this round: what it saw, for the status boards here (C-181),
            save_later(os.path.join(STATE, "lease-view.json"), lease_mod.view(  # written off the event loop
                self.lease.entries, self.lease.now, self.lease.me, holder, time.time()))
        if self.lease.channel() is None:
            standbys = [n for n, m in (remote.inventory().get("machines") or {}).items()
                        if lease_mod.role_of(n, {n: m}) == "standby"] or ["<備援的機器>"]
            await self.audit("設定了備援，但找不到 #%s，備援不會接手（在這台跑 %s 會建好，跑完會重啟機器人一次%s）" % (  # (the standby's mode
                config.LEASE_CHANNEL, "、".join("altention standby setup %s auto" % n for n in standbys),  # is not known
                "；原本設成只通知的，auto 換成 notify"))  # here: only doctor asks the standby; #release 11-12)
            save_later(os.path.join(STATE, "lease-view.json"), {  # C-181: the boards here go by the heartbeat alone
                "at": time.time(), "machine": self.lease.me, "holder": None, "leaders": [], "machines": {},
                "no_lease": config.LEASE_CHANNEL})
            self.lease = None  # nothing to hold: the bot as without a standby
            remote.LEASED = False
            await self.let_through()
            return True
        if holder is not None:
            self.discord_ok()
        if self.lease.epoch_known():
            remote.EPOCH = self.lease.epoch  # C-163 section 1.4: every request to another machine says it
            remote.on_old = self.old_bot
        if self.lease.other:
            await self.audit("%s 已經在負責機器人，這台退下" % self.lease.other + stepped_down_note(self))
            await self.give_up()
            return True
        if self.leader_ok():
            await self.let_through()
            self.in_background("announce", self.announce_host)  # (nothing that waits on ssh in a round after the
            self.in_background("primary-ssh", self.primary_ssh_check)  # read: it would make the next read late and
            if await self.failback_round():  # leader_ok() lapse; #code-review-2 on 4844bb5, manager)
                return True
        elif self.lease.giving_up():
            await self.audit("C-163：這台連續 %d 秒不能確定自己是機器人，讓出來" % lease_mod.YIELD_SECS)
            await self.give_up()
            return True
        return False

    def in_background(self, name, fn):
        """Run `fn()` on a task of its own, unless the last one of that name is still running (one at a time)."""
        tasks = getattr(self, "bg_tasks", None)
        if tasks is None:
            tasks = self.bg_tasks = {}
        if tasks.get(name) is not None and not tasks[name].done():
            return None
        tasks[name] = asyncio.get_running_loop().create_task(fn())
        return tasks[name]

    TELL_SECS = 10  # bot-host to every machine: all at once, at most this long in all (manager on 4844bb5)

    async def tell_hosts(self, machine, epoch, names):
        """Every machine of `names` told that the bot runs on `machine` since `epoch` (bot-host): here directly,
        elsewhere by `dcremote bot-host`, all at once, TELL_SECS in all. The names done (written, or a newer
        epoch there already, or no way to reach)."""
        me = remote.this_machine()
        machines = remote.inventory().get("machines") or {}
        done = set()

        async def one(name):
            m = machines.get(name) or {}
            if name == me:
                config_mod.write_bot_host(machine, epoch)  # (an older epoch: a newer bot wrote it; nothing to do)
                if machine == me:  # this machine is the bot now: its own fencing record up to the new epoch too
                    config_mod.raise_dcremote_owner(epoch)  # (drill 11-08, item 10)
                    from . import routecore
                    own = [st.get("pane") for st in self.channels_state.values() if isinstance(st, dict)]
                    routecore.drop_older(epoch, ["%s|%s" % (p["socket"], p["id"]) for p in own  # (its own panes here,
                                                 # host-less or still marked with this machine before localize_own()
                                                 # took the mark away: #code-review on 0286bb0, kept)
                                                 if isinstance(p, dict) and p.get("host") in (None, "", me)
                                                 and p.get("socket") and p.get("id")])
            elif any(a != "local" for a in m.get("ssh") or []):
                code, _ = await remote.dcremote(name, "bot-host", machine, str(epoch), timeout=self.TELL_SECS - 2)
                # (inside the time limit in all: rarely anything left to cancel; #code-review on 7377217)
                if code not in (0, remote.OLD):
                    return
            done.add(name)  # (no way there: nothing to do either)
        tasks = [asyncio.ensure_future(one(n)) for n in names]
        if tasks:
            _, late = await asyncio.wait(tasks, timeout=self.TELL_SECS)
            for t in late:
                t.cancel()
        return done

    async def announce_host(self):
        """Section 4.4: this machine is the bot (with this epoch): every machine's bot-host file says so (dc post,
        dcctl read it: config.bot_machine()). Once per epoch for each machine; one not reached is tried again next
        round (in_background(): never in the round itself)."""
        epoch = self.lease.epoch
        done = getattr(self, "hosts_told", None)
        if done is None or done[0] != epoch:
            done = self.hosts_told = (epoch, set())
        left = [n for n in (remote.inventory().get("machines") or {}) if n not in done[1]]
        done[1].update(await self.tell_hosts(remote.this_machine(), epoch, left))
        await self.rebind_far(epoch)

    async def rebind_far(self, epoch):
        """The routes of this bot's windows and attached panes on other machines, bound again with this epoch, once
        per epoch for each (a machine not reached: again next round). A machine's dchook leaves alone a route older
        than the newest bot it saw (live(), drill 11-08); without this, every route the bot before bound there (an
        attached pane, a window opened there) would go silent at the first request of this one (#code-review-2 on
        7ab6d1a, manager). What is left older after this is what no bot now names: rightly left alone. The attached
        panes of this machine too, bound here without an epoch (drill item 12 on the mac, 11-10, manager)."""
        done = getattr(self, "routes_rebound", None)
        if done is None or done[0] != epoch:
            done = self.routes_rebound = (epoch, set())
        me = remote.this_machine()
        for cid, st in list(self.channels_state.items()):
            pane = st.get("pane") if isinstance(st, dict) else None
            if not isinstance(pane, dict) or not pane.get("id") or not pane.get("socket") or cid in done[1]:
                continue
            took = st.get("took_from") if isinstance(st.get("took_from"), dict) else None
            if pane.get("host") in (None, "") and took and not took.get("local"):
                continue  # the old bot machine's, not taken over yet (pending: its host comes with adopt_one()):
                # neither here nor there now, left for a round once it is (#code-review-2 on 3272a00: bound here and
                # marked done, it was never bound on that machine; manager: only took_from.local or none, here)
            if pane.get("host") in (None, "", me):  # an attached pane of this machine (a window this bot opened
                if not st.get("home"):  # here reports through its --settings, no route): bound again here, the
                    try:                # one the old bot bound (by dcremote, its epoch) being left alone by the hook
                        from . import routes  # now (drill item 12 on the mac, 11-10, manager)
                        routes.bind(dict(pane, host=None), cid)
                    except Exception:
                        traceback.print_exc()
                        continue
                done[1].add(cid)
                continue
            try:  # (routes.bind_any's remote half, its answer looked at: a machine not reached is tried again)
                code, _ = await remote.dcremote(pane["host"], "bind", "%s|%s" % (pane["socket"], pane["id"]), str(cid),
                                                str(pane.get("pid") or ""), timeout=20)
            except Exception:
                traceback.print_exc()
                continue
            if code == 0:
                done[1].add(cid)

    async def primary_ssh_check(self):
        """On a standby that is the bot: is the primary reached by ssh (`dcremote info`, once in 5 minutes)? For
        failback_due(); in_background(), never in the round."""
        machines = remote.inventory().get("machines") or {}
        if lease_mod.role_of(remote.this_machine(), machines) != "standby":
            return
        primary = next((n for n, m in machines.items() if "dcbot" in (m.get("roles") or [])), None)
        if not primary or time.time() - getattr(self, "primary_ssh_at", 0) < 300:
            return
        self.primary_ssh_at = time.time()
        code, _ = await remote.dcremote(primary, "info", timeout=30)
        self.primary_ssh_ok = code == 0
        if not self.primary_ssh_ok:
            self.back_since = None

    def failback_due(self, primary, now_clock):
        """Section 5: the primary up (its lease message edited lately: lease.alive) and reached by ssh, without a
        break, for lease.FAILBACK_SECS. Called each round; True once it has been."""
        entry = next((e for e in self.lease.entries if e["machine"] == primary), None)
        ok = lease_mod.alive(entry, self.lease.now) and not (entry or {}).get("down") and \
            getattr(self, "primary_ssh_ok", False)  # (marked down: its bot cannot run, drill item 20)
        if not ok:
            self.back_since = None
            return False
        if getattr(self, "back_since", None) is None:
            self.back_since = now_clock
        return now_clock - self.back_since >= drill_failback_secs()

    def day_stopped(self):
        """24 hours with lease.DAY_MOST takeovers: the bot stays where it is, no failback either (section 5)."""
        got = load_json(os.path.join(STATE, "lease-takeovers.json"), [])
        return isinstance(got, list) and len([t for t in got if isinstance(t, (int, float)) and
                                              time.time() - t < 86400]) >= lease_mod.DAY_MOST

    DRAIN_SECS = 300  # C-188: posts on their way at failback, waited for at most this long
    RUNNING_WAIT = 30  # posts begun here when the bot goes back: waited for at most this long before the rest move

    async def stop_draining(self):
        """A failback that waited for posts on their way (C-188) called off: every machine's bot-host back here, so
        new posts come to this bot again. A machine not reached now is told again by announce_host() each round
        (hosts_told keeps only those done: #code-review on 0fd1323e)."""
        if getattr(self, "draining", None) is None:
            return
        primary = self.draining_to if isinstance(getattr(self, "draining_to", None), str) else None
        self.draining = self.draining_to = None
        epoch = self.lease.epoch
        done = await self.tell_hosts(remote.this_machine(), epoch, list(remote.inventory().get("machines") or {}))
        self.hosts_told = (epoch, set(done or ()))
        if primary:  # the posts sent to the primary meanwhile: back here, where the bot runs (manager 11-16, A)
            self.in_background("posts-back", lambda: self.posts_back(primary))

    async def posts_back(self, primary):
        """C-188 (manager 11-16): the posts left in `primary`'s spool while bot-host named it, moved into this one's
        (their names kept: their order too), then taken away there; what could not be is said in #audit."""
        code, out = await remote.dcremote(primary, "spool-out", timeout=60)
        try:
            got = json.loads(out) if code == 0 else None
        except ValueError:
            got = None
        if not isinstance(got, list):
            return await self.audit(messages.lease("failback_back_failed", primary=primary, why=code,
                                                   standby=remote.this_machine()))
        names = [i["name"] for i in got if isinstance(i, dict) and postbox.put(i.get("name"), i.get("post"))]
        if not names:
            return
        code, _ = await remote.dcremote(primary, "spool-drop", *names, timeout=60)
        if code != 0:  # (here now, and there still: they may go twice once that bot runs)
            return await self.audit(messages.lease("failback_back_left", primary=primary, n=len(names),
                                                   standby=remote.this_machine(),
                                                   names="、".join(names[:5])))
        await self.audit(messages.lease("failback_back", primary=primary, n=len(names), standby=remote.this_machine()))

    async def posts_out(self, primary, late=None):
        """C-188 (manager 11-16): posts still here when the bot goes back go to `primary`'s spool (it runs the bot
        next), their names kept, then taken away here; not taken there, they stay here and #audit says which. Called
        once the state is pushed and every machine told, before this bot steps down (fail_back's on_done). What is
        moved is looked up then (C-193, reviews on c1d83b77): the spool as it is now and what is on its way, so that
        posts come meanwhile (the push takes seconds) go too, with a wait before it or not (`late`: its posts).
        Those begun here (posts.running: shown in part, maybe) are waited for first, RUNNING_WAIT at most: moved,
        they would be shown twice, here as they end and there from the start (B10 drill: 6 of 1600). No new one
        begins meanwhile (leader_ok() is false: Posts.safely leaves them in the spool); one still under way then is
        moved all the same (at least once)."""
        running = getattr(getattr(self, "posts", None), "running", None)
        until = time.monotonic() + self.RUNNING_WAIT
        while isinstance(running, set) and running and time.monotonic() < until:
            await asyncio.sleep(0.2)
        inflight = getattr(getattr(self, "posts", None), "inflight", None)
        paths = sorted(set(late or ()) | {p for p, _ in postbox.waiting()} | (set(inflight) if isinstance(inflight, set) else set()))
        items = []
        for path in paths:
            try:
                with open(path) as f:
                    items.append({"name": os.path.basename(path), "post": json.load(f)})
            except (OSError, ValueError):
                continue
        if not items:
            return
        me = remote.this_machine()
        code, out = await remote.dcremote(primary, "spool-in", input=json.dumps(items, ensure_ascii=False), timeout=60)
        try:
            n = (json.loads(out) or {}).get("posts") if code == 0 else None
        except ValueError:
            n = None
        waited = bool(late)
        if n != len(items):
            return await self.audit(messages.lease("failback_drain_late" if waited else "failback_left", primary=primary,
                                                   n=len(items), standby=me, mins=self.DRAIN_SECS // 60,
                                                   names="、".join(i["name"] for i in items[:5])))
        for path in paths:
            postbox.done(path)
        await self.audit(messages.lease("failback_drain_moved" if waited else "failback_moved", primary=primary,
                                        n=len(items), mins=self.DRAIN_SECS // 60, standby=me))

    async def failback_round(self):
        """On a standby that is the bot (section 5): the primary back for 30 minutes → the bot goes back to it
        (`dcbot.failback` auto, the default), or the owner is asked once with a button (ask). True when this bot
        stepped down."""
        machines = remote.inventory().get("machines") or {}
        me = remote.this_machine()
        if lease_mod.role_of(me, machines) != "standby":
            return False
        primary = next((n for n, m in machines.items() if "dcbot" in (m.get("roles") or [])), None)
        if not primary:
            return False
        if not self.failback_due(primary, lease_mod.clock()) or self.day_stopped():
            await self.stop_draining()  # (the primary away again meanwhile: posts come here again)
            return False
        if time.time() < getattr(self, "failback_retry_at", 0):
            return False  # (the last push was not taken: again 10 minutes after it, not each round: #code-review-2)
        ask = remote.alt().setting("dcbot.failback", "auto") == "ask"
        on_way = sorted(self.posts.inflight) if isinstance(getattr(getattr(self, "posts", None), "inflight", None), set) else []
        late = None  # (posts still here when the wait is over: C-188)
        if on_way and ask:
            return False  # a post still on its way: asked once it is through (a round later; #code-review-2)
        if on_way:  # C-188: posts keep coming and the round would wait for ever: new ones go to the primary's spool
            if getattr(self, "draining", None) is None:  # (taken there by its bot once it is back), those on their
                self.draining, self.draining_to = lease_mod.clock(), primary  # way here are waited for, DRAIN_SECS at most
                await self.tell_hosts(primary, self.lease.epoch, list(machines))
                await self.audit(messages.lease("failback_draining", primary=primary, n=len(on_way),
                                                mins=self.DRAIN_SECS // 60))
                return False
            if lease_mod.clock() - self.draining < self.DRAIN_SECS:
                return False
            late = on_way  # (moved to the primary's spool once the state is pushed: fail_back()'s on_done)
        if ask:
            if getattr(self, "failback_asked", None) != self.lease.epoch:
                self.failback_asked = self.lease.epoch
                ch = await self.tmux.notify_channel()
                if ch is not None:
                    view = discord.ui.View(timeout=None)
                    view.add_item(discord.ui.Button(label=messages.lease("failback_button", primary=primary)[:80],
                                                    custom_id="failback:%d" % self.lease.epoch,
                                                    style=discord.ButtonStyle.primary))
                    await outgoing.deliver(ch, messages.lease("failback_ask", primary=primary, standby=me), view=view, intent="card")
            return False
        if await self.fail_back(primary, on_done=lambda: self.posts_out(primary, late)):
            return True
        await self.stop_draining()  # (not handed back: the posts come here again until the next try)
        return False

    async def fail_back(self, primary, on_done=None):
        """Section 5's steps: the channels as the primary is to have them (standby.failback_channels) pushed to it,
        every machine's bot-host back to it, said in the notification channel (no mention), then this bot gives the
        place up (leader: false, exit 4). The push not taken: nothing changes, tried again next round. A marker
        (state/failed-back.json) keeps this machine's gate from taking over while the primary starts."""
        from . import standby
        me = remote.this_machine()
        self.failing_back = True  # first: from here on this machine handles nothing (leader_ok() false: the owner's
        # messages held, then dropped when it gives the place up, for the primary's catch-up; pump, posts, pushes
        # stopped), so nothing done here after the snapshot is lost or done twice (#code-review, #code-review-2)
        try:
            loop = asyncio.get_running_loop()
            found = await loop.run_in_executor(None, standby.bot_panes, envfile.bot_server())
            channels = standby.failback_channels(self.channels_state, me, primary, found)
            info = dict(standby.meta(config.ROOT, me), epoch=self.lease.epoch, **standby.failback_marks(channels, STATE))
            text = await loop.run_in_executor(None, lambda: standby.failback_bundle(STATE, channels, info, ask_mod.bundled(),
                                                                                  [ask_mod.folder_name()]))
            err = []
            code, _ = await remote.dcremote(primary, "state-in", input=text, timeout=120, err=err)
        except Exception:
            code, err = None, [traceback.format_exc().splitlines()[-1]]
        if code != 0:
            self.failing_back = False  # not handed back: as before, what waited goes on
            self.failback_retry_at = time.time() + 600
            try:  # a round at once: the push may have taken two minutes, and the last read would make leader_ok()
                await self.lease.round()  # false for another one (#code-review-2 on 7377217, manager)
            except Exception:
                traceback.print_exc()
            if time.time() - getattr(self, "failback_said_at", 0) >= 3600:  # (#audit once an hour at most)
                self.failback_said_at = time.time()
                await self.audit(messages.lease("failback_push_failed", primary=primary, why="；".join(err)[:200] or code))
            await self.let_through()
            return False
        save_json(os.path.join(STATE, "failed-back.json"), {"primary": primary, "at": time.time(), "epoch": self.lease.epoch})
        await self.tell_hosts(primary, self.lease.epoch, list(remote.inventory().get("machines") or {}))  # (the primary
        # starts with a bigger epoch and writes them again; this is for the half minute between)
        ch = await self.tmux.notify_channel()
        if ch is not None:
            try:
                await outgoing.deliver(ch, messages.lease("failed_back", primary=primary), intent="status")
            except Exception:
                traceback.print_exc()
        if on_done is not None:  # (before give_up(): its close() ends this bot's connection to Discord, and its
            try:                 # end_now() the task: #code-review, #code-review-2 on 3d57410)
                await on_done()
            except Exception:
                traceback.print_exc()
        await self.audit(messages.lease("audit_failed_back", primary=primary, standby=me, epoch=self.lease.epoch))
        await self.give_up()
        return True

    async def failback_pressed(self, interaction):
        """The ask mode's button ("failback:<epoch>"): only the owner; only this bot's epoch."""
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return
        epoch = ((interaction.data or {}).get("custom_id") or "").split(":")[-1]
        machines = remote.inventory().get("machines") or {}
        primary = next((n for n, m in machines.items() if "dcbot" in (m.get("roles") or [])), None)
        if self.lease is None or str(self.lease.epoch) != epoch or not primary or not self.leader_ok():
            await interaction.response.send_message(messages.lease("failback_stale"), ephemeral=True)
            return
        await interaction.response.defer()

        async def done():  # the button done with: taken off, the message says so (drill item 5, #release 11-10)
            await outgoing.deliver(getattr(interaction, "channel", None), content=messages.lease("failback_pressed", primary=primary),
                                   view=None, intent="card", edit=interaction.message)
            await self.posts_out(primary)  # (posts still here go with it: C-193)
        if not await self.fail_back(primary, on_done=done):
            await interaction.followup.send(messages.lease("failback_not_now", primary=primary), ephemeral=True)

    def from_primary(self, st):
        """A channel the old bot machine had as its own (C-163 4.1): standby.unplaced()."""
        from . import standby
        return standby.unplaced(st)

    def take_questions(self, other):
        """The questions waiting that `other` pushed (ask.take_from: through ask.save(), where each answer goes set
        as this machine sees it). At start, before anything can answer one (C-163 3.1)."""
        if not other:
            return
        try:
            ask_mod.take_from(os.path.join(config.ROOT, "state-standby"), remote.this_machine(), other)
        except Exception:
            traceback.print_exc()

    def print_tail(self, cid, st, session_id):
        """A print- or text-mode reply's footer end, with a standby (C-163 1.4): " · machine · turn id" as a tmux
        reply's (the id the last turn begun in its transcript; #code-review on 43bfe53), the turn claimed as posted;
        "" without a standby or a transcript."""
        if getattr(self, "lease", None) is None or not session_id:
            return ""
        pid = transcript.last_begun(cc_sessions.transcript(st["cwd"], session_id))
        if pid:
            self.claim_turn(str(cid), pid)
        return " · " + events.footer_tail(remote.this_machine(), pid)

    async def stale_owner(self, host):
        """remote.on_stale: `host` refused this bot, which runs without a lease, as an older one (it remembers a bot
        with a standby: #bot-lease deleted, the standby role taken away). #audit once a machine, saying what to do."""
        said = getattr(self, "stale_said", None)
        if said is None:
            said = self.stale_said = set()
        if host in said:
            return
        said.add(host)
        await self.audit(messages.lease("stale_owner", host=host))

    def claim_turn(self, cid, pid):
        """A turn's reply is about to be posted in channel `cid`: True when nobody posted it yet (now noted), False
        when it was. One set per channel shared by every way a reply is posted (the Stop, print mode, replay_one()),
        checked and added with no await between (#code-review-2 on 43bfe53: a turn ended while the bot was away
        comes both by the stream's Stop and by the replay)."""
        turns = getattr(self, "posted_turns", None)
        if turns is None:
            turns = self.posted_turns = {}
        seen = turns.setdefault(str(cid), {})  # (a dict: in the order added, the oldest dropped first; #code-review-2)
        key = (pid or "")[:8]
        if not key:
            return True
        if key in seen:
            return False
        seen[key] = True
        if len(seen) > 500:
            del seen[next(iter(seen))]
        return True

    SEED_READ = 50  # messages read back in a channel before its first reply posted by this process

    async def seed_turns(self, ch):
        """Before this process posts its first reply in channel `ch` (a Stop, from any spool): the turn ids the bot's
        own last SEED_READ messages there already carry, claimed. The bot before it (the old bot machine, or this
        machine before a restart) may have posted that very turn: claim_turn() alone remembers this process only
        (C-163 drill 11-08, item 3: a turn posted by the primary just before it was killed came again by the old
        bot's spool and was posted twice; manager). Once per channel per process; read again after a failed read."""
        seeded = getattr(self, "turns_seeded", None)
        if seeded is None:
            seeded = self.turns_seeded = set()
        cid = str(ch.id)
        if cid in seeded:
            return
        texts = []
        try:
            async for m in ch.history(limit=self.SEED_READ):
                if getattr(m.author, "id", None) == getattr(self.user, "id", None):
                    texts.append(m.content or "")
        except Exception:
            traceback.print_exc()
            return
        seeded.add(cid)
        for i in events.posted_ids(texts):
            self.claim_turn(cid, i)

    async def replay_when_leading(self):
        while not self.is_closed() and not self.leader_ok():
            await asyncio.sleep(1)
        if not self.is_closed():
            await self.replay_all()

    REPLAY_READ = 1000  # messages read back at most in one channel
    REPLAY_BACK = 10 * 60  # from the last reply the bot posted in a channel, this far back (section 3.1: the clocks of
    # Discord and of the machine the transcript is on may differ)

    async def replay_all(self):
        """After a takeover, a failback or a standby gone down (C-163 1.4 point 4, 3.1): in every channel with a
        conversation, the replies its transcript has that the channel does not (by turn id: replay_one())."""
        n = 0
        for cid, st in list(self.channels_state.items()):
            if not isinstance(st, dict) or not st.get("session_id") or (st.get("took_from") or {}).get("pending") \
                    or kinds.channel_kind(st) != kinds.CONVERSATION:
                continue
            try:
                n += await self.replay_one(cid, st)
            except Exception:
                traceback.print_exc()
        return n

    async def replay_one(self, cid, st):
        """One channel: the bot's last 50 messages there give the turn ids already posted (events.posted_ids) and
        the time of the last; the transcript's turns ended since REPLAY_BACK before it (where the conversation is:
        here, or that machine's `dcremote replies`) whose id is not among them are posted, marked. Nothing when no
        message there carries an id (posted before the ids were: nothing to tell them apart by). How many posted."""
        ch = self.get_channel(int(cid)) if str(cid).isdigit() else None
        if ch is None or not st.get("cwd"):
            return 0
        texts, last = [], None
        async for m in ch.history(limit=self.REPLAY_READ):  # newest first, back to REPLAY_BACK before the bot's last
            if last is not None and m.created_at < last - datetime.timedelta(seconds=self.REPLAY_BACK):
                break  # (not a fixed 50: every message of the window is read; #code-review-2 on 43bfe53)
            if getattr(m.author, "id", None) != getattr(self.user, "id", None):
                continue
            texts.append(m.content or "")
            last = m.created_at if last is None or m.created_at > last else last
        ids = events.posted_ids(texts)
        if not ids or last is None:
            return 0
        for i in ids:
            self.claim_turn(cid, i)
        since = (last - datetime.timedelta(seconds=self.REPLAY_BACK)).isoformat()
        host = (st.get("home") or {}).get("host") or (st.get("pane") or {}).get("host")
        if host:
            code, out = await remote.dcremote(host, "replies", st["cwd"], st["session_id"], since, timeout=30)
            try:
                got = json.loads(out) if code == 0 else []
            except ValueError:
                got = []
        else:
            path = cc_sessions.transcript(st["cwd"], st["session_id"])
            got = await asyncio.get_running_loop().run_in_executor(None, transcript.replies_since, path, since)
        n, me = 0, remote.this_machine()
        for r in got or []:
            pid = r.get("pid") or ""
            if not pid or not self.claim_turn(cid, pid):  # (posted already, here or by the stream's Stop)
                continue
            text = masking.masked(r.get("final") or "") + "\n" + messages.lease(
                "replayed_footer", tail=events.footer_tail(me, pid))
            for chunk in split_message(text):
                await outgoing.deliver(ch, chunk, intent="reply")
            n += 1
        return n

    def recovered_note(self):
        """The gate took the channels a standby pushed while it was the bot (it went down without failing back):
        the #audit line, once (state/standby-recovered.json)."""
        path = os.path.join(STATE, "standby-recovered.json")
        got = load_json(path, None)
        if not isinstance(got, dict):
            return None
        try:
            os.remove(path)
        except OSError:
            pass
        at = got.get("at")
        when = time.strftime("%m-%d %H:%M", time.localtime(at)) if isinstance(at, (int, float)) else "?"
        return messages.lease("audit_recovered", primary=remote.this_machine(), standby=got.get("standby") or "?", when=when)

    def pending_machine(self):
        """The machine of channels still waiting to be taken over (took_from.pending), or None."""
        for st in self.channels_state.values():
            t = st.get("took_from") if isinstance(st, dict) else None
            if isinstance(t, dict) and t.get("pending") and t.get("machine"):
                return t["machine"]
        return None

    def mark_pending(self, primary):
        """At once, when the takeover is read (before the lease rounds, pump and anything else can touch them): the
        old bot machine's channels are marked waiting (#code-review-2 on 9d2b7bd): ensure() refuses, pump() does
        not look for them here, the owner's messages to them wait (adopt_waits()) until adopt_primary() is done."""
        if not primary:
            return
        n = 0
        for st in self.channels_state.values():
            if self.from_primary(st):
                st["took_from"] = {"machine": primary, "pending": True}
                n += 1
        self.adopting = True
        if n:
            self.save()

    def adopt_waits(self, message):
        """While the old bot machine's channels are being taken over: a message to one of them waits (sent again
        when adopt_primary() has tried once). True when held."""
        if not getattr(self, "adopting", False):
            return False
        ch = message.channel
        st = self.channels_state.get(str(ch.id)) or self.channels_state.get(str(getattr(ch, "parent_id", None))) or {}
        if not (st.get("took_from") or {}).get("pending"):
            return False
        self.after_adopt = getattr(self, "after_adopt", []) + [message]
        return True

    async def adopt_primary(self, got, notify=True):
        """After a takeover (C-163 4.1, 4.5): the old bot machine's channels become windows there, reached by ssh like
        any other machine's: never opened on this machine (another disk of the same path, section 0). A window of
        the bot's own tmux server there that is still up is taken as it is; one gone, or never opened, is opened
        there (home on that server's socket; --resume when there is a conversation). That machine out of reach:
        those channels stay marked waiting (ensure() says it cannot be reached, as 📡) and it is tried each minute.
        After the first try the messages that waited go on (to that machine's window, or told it cannot be
        reached). The takeover is said once, with the owner's mention (not again after a restart: `notify`)."""
        primary = (got or {}).get("primary")
        try:
            if primary:
                await self.adopt_rounds(got, primary, notify)
        finally:  # whatever ends this (an error among it): nothing left waiting for good (#code-review-2 on 65ee18f)
            await self.adopt_done()

    async def adopt_done(self):
        """The messages that waited go on, and posts again (once)."""
        if not getattr(self, "adopting", False):
            return
        self.adopting = False
        waiting, self.after_adopt = getattr(self, "after_adopt", []), []
        for m in waiting:
            try:
                await self.on_message(m)
            except Exception:
                traceback.print_exc()

    async def adopt_rounds(self, got, primary, notify):
        while not self.is_closed() and not self.leader_ok():
            await asyncio.sleep(1)
        if self.localize_own(remote.this_machine()):
            self.save()
        said, said_replay = not notify, False
        while not self.is_closed():
            mine = [cid for cid, st in self.channels_state.items() if (st.get("took_from") or {}).get("pending")]
            found = None
            if mine:
                code, out = await remote.dcremote(primary, "bot-panes", timeout=30)
                try:
                    found = json.loads(out) if code == 0 else None
                except ValueError:
                    found = None
                for cid in mine:
                    ch = self.get_channel(int(cid)) if str(cid).isdigit() else None
                    self.adopt_one(self.channels_state[cid], primary, found,
                                   tmuxio.session_name(getattr(ch, "name", None) or "ch", cid))
                self.save()
            if not said:
                said = True
                await self.say_takeover(got, len(mine), found is not None)
            if found is not None:
                for cid in mine:  # a turn that was running there when the bot went down: watched, or said gone
                    try:
                        await self.tmux.look_again(cid, self.channels_state[cid])
                    except Exception:
                        traceback.print_exc()
                self.save()
            if found is not None or not said_replay:  # replies the old bot left unposted (1.4 point 4): every channel
                said_replay = True  # once after the first try, and again once the old bot machine's are taken over
                self.in_background("replay", self.replay_all)
            await self.adopt_done()
            if found is not None or not mine:
                break
            await asyncio.sleep(60)

    def localize_own(self, me):
        """Section 4.3: this machine's own channels (home.host, pane.host or files.host naming it) go here, not by
        ssh to itself; which ones is kept in took_from["local"] for the failback (section 5). How many."""
        n = 0
        for st in self.channels_state.values():
            if not isinstance(st, dict) or st.get("took_from"):
                continue
            moved = [k for k in ("home", "pane", "files") if isinstance(st.get(k), dict) and st[k].get("host") == me]
            for k in moved:
                st[k] = {f: v for f, v in st[k].items() if f != "host"}
            if moved:
                st["took_from"] = {"local": moved, "machine": me}
                n += 1
        return n

    def adopt_one(self, st, primary, found, session=None):
        """One channel of the old bot machine: its window there (found: dcremote bot-panes; None: not reached, the
        channel stays waiting). `session`: the name for a window opened there when the channel had none."""
        if found is None:
            st["took_from"] = dict(st.get("took_from") or {}, machine=primary, pending=True)
            return
        took = {"machine": primary}
        if st.get("mode") == "print":  # a print turn would run here, on another disk: as tmux until the failback
            st["mode"] = "tmux"
            took["mode"] = "print"
        if kinds.channel_kind(st) == kinds.FILES:
            st["files"] = dict(st["files"], host=primary)
            took["files"] = True
        elif st.get("home") or st.get("pane"):  # a window in the owner's tmux there, or a pane of theirs attached
            for k in ("home", "pane"):
                if st.get(k):
                    where_mod.put(st, **{k: dict(st[k], host=primary)})
                    took[k] = True
        else:  # a session of the bot's own tmux server there, or none yet: there, never here
            name = st.get("tmux")
            took["tmux"] = name
            pane = next((p for p in found.get("panes") or [] if name and p.get("session") == name), None)
            where_mod.put(st, tmux=None, home={"host": primary, "socket": (pane or {}).get("socket") or found.get("socket"),
                                               "session": name or session or "dc-taken", "window": ""},
                          **({"pane": {"host": primary, "socket": pane["socket"], "id": pane["id"], "pid": pane.get("pid")}}
                             if pane else {}))
        st["took_from"] = took

    async def say_takeover(self, got, n, reached):
        """Section 4.5, the owner's mention once (C-103's system call), what stopped said as it is."""
        primary, me = got.get("primary"), remote.this_machine()
        code = "took_over_joined" if reached else "took_over"
        if self.day_stopped():  # (this one the DAY_MOST-th in 24 hours: said, #release 11-10)
            code += "_last"
        head = messages.lease(code, primary=primary, standby=me, n=n) + engines_note(got)
        try:
            await calls.system(self.tmux.notify_channel, "takeover", str(got.get("epoch")), "%s %s" % (head, calls.mention()))
        except Exception:
            traceback.print_exc()

    async def push_rounds(self):
        """While this machine is the bot (with a standby): its state to every other machine of the lease when what a
        takeover needs of the channels changed (at most every standby.PUSH_SECS), and all of it every FULL_SECS
        whatever changed (C-163 section 3.1, manager 11-08). Noted per machine in state/standby-sync.json (doctor)."""
        from . import standby
        wake, loop = asyncio.Event(), asyncio.get_running_loop()
        ask_mod.CHANGED = lambda: loop.call_soon_threadsafe(wake.set)  # a question opened or closed: looked at now
        last, seen, placed, asked = 0.0, None, None, None
        try:
            while not self.is_closed() and self.lease is not None:
                try:  # every CHANGED_SECS, or at once when a question opened or closed
                    await asyncio.wait_for(wake.wait(), standby.CHANGED_SECS)
                except asyncio.TimeoutError:
                    pass
                wake.clear()
                if self.lease is None:  # (#bot-lease gone meanwhile: leader_ok() is then true, and push_state() has no
                    break               # epoch to send; drill 11-08: the task died on it)
                if not self.leader_ok():
                    continue
                stamp = standby.what_matters(self.channels_state, ask_mod.ids())  # (in memory: the state file has one writer: test_kinds)
                where = standby.what_placed(self.channels_state)
                waiting = tuple(ask_mod.ids())  # (a question opened or closed: pushed at once, as a placing is: drill
                # item 12, a question 7 s after a push, the bot down 26 s later: the standby had no such question)
                now = (placed is not None and where != placed) or (asked is not None and waiting != asked)
                if not standby.due(time.time() - last, stamp != seen, now):
                    continue
                last, seen, placed, asked = time.time(), stamp, where, waiting
                await self.push_state()
        finally:
            ask_mod.CHANGED = None

    async def push_state(self):
        from . import standby
        machines = remote.inventory().get("machines") or {}
        me = remote.this_machine()
        others = [n for n in lease_mod.members(machines) if n != me]
        epoch = getattr(getattr(self, "lease", None), "epoch", None)
        if not others or epoch is None:  # (no lease, or none yet: nothing a takeover could be told it came from)
            return {}
        for st in self.channels_state.values():  # (as save() makes the file: what is in memory now, C-175)
            kinds.stamp(st)
        channels = json.dumps(self.channels_state, ensure_ascii=False, indent=1)
        text = await asyncio.get_running_loop().run_in_executor(
            None, lambda: standby.bundle(STATE, dict(standby.meta(config.ROOT, me), epoch=epoch,
                                                     bot_socket=standby.bot_panes_socket(envfile.bot_server())),
                                         ask_mod.bundled(), [ask_mod.folder_name()], channels_text=channels))
        said = {}
        for host in others:
            err = []
            code, _ = await remote.dcremote(host, "state-in", input=text, timeout=120, err=err)
            said[host] = {"at": time.time(), "ok": code == 0, "why": "；".join(err)[:200] if code else None}
        try:
            save_json(os.path.join(STATE, "standby-sync.json"), dict(load_json(os.path.join(STATE, "standby-sync.json"), {}), **said))
        except Exception:
            traceback.print_exc()
        return said

    def old_bot(self, host):
        """Another machine refused this bot as an older one (a newer epoch reached it: C-163 section 1.4): the place
        is someone else's; given up."""
        if getattr(self, "lease_over", False):
            return
        self.loop.create_task(self.audit("%s 說這台的機器人已經不是負責的那台，這台退下" % host
                                         + stepped_down_note(self)))
        self.loop.create_task(self.give_up())

    async def give_up(self):
        self.lease_over = True
        drop_board_marks()  # C-181: before the leader: false edit (a failback has told every machine already)
        try:
            await self.lease.write(False)
        except Exception:
            traceback.print_exc()
        self.held_back = []
        self.exit_code = 4
        self.arm_end()
        await say_lane_kept(self)  # (C-174, as in fenced_out())
        await self.close()
        self.end_now()

    def run(self, token, **kw):
        """discord.Client.run() (no log handler: discord_warnings() has its own), its start in a task of its own:
        run_bot(), so give_up() can end the process at once (end_now())."""
        try:
            asyncio.run(run_bot(self, token))
        except KeyboardInterrupt:
            pass

    def end_now(self):
        """After close(): the process ends now, not when discord.py's reconnect loop next looks (it may be asleep for
        many minutes: the drill's 16:11, 930 s, the place given up 20 minutes before the process ended and the
        supervisor could follow the lease). The start task (run_bot()) is cancelled; still here END_SECS later, the
        process is ended hard with the exit code it has (manager 11-10)."""
        task = getattr(self, "start_task", None)
        if task is not None and not task.done():
            task.cancel()
        self.arm_end()

    def arm_end(self):
        """The backstop of end_now(), once: started before close() (#code-review-2 on ac4bed6: a close() that hangs
        would never reach it), the process ended END_SECS later with the exit code it has then."""
        if getattr(self, "end_armed", False):
            return
        self.end_armed = True

        def hard():
            print("[lease] still running %d s after giving up: ending (exit %s)" % (END_SECS, self.exit_code), flush=True)
            statewrite.WRITER.flush(5)  # (the channels' state handed over is written first, unless the disk hangs)
            os._exit(self.exit_code)
        timer = threading.Timer(END_SECS, hard)
        timer.daemon = True
        timer.start()

    def gateway_up(self):
        """Connected to Discord's gateway now: ready, not closed, its heartbeat's latency a number."""
        import math
        try:
            return bool(self.is_ready()) and not self.is_closed() and math.isfinite(self.latency)
        except Exception:
            return False

    def discord_ok(self):
        """state/discord-ok: Discord was read (a lease read, a message received; writes do not count)."""
        try:
            with open(os.path.join(STATE, "discord-ok"), "w") as f:
                f.write(str(int(time.time())))
        except OSError:
            pass

    def hold(self, message):
        """While leader_ok() is false: the owner's message waits (in order, QUEUE_MAX at most, none older than
        QUEUE_SECS; more, all of them go and the catch-up reads them back). True when held."""
        letting = getattr(self, "letting", set())
        if getattr(self, "lease", None) is None or id(message) in letting:
            return False  # (the one being let through now)
        if not letting and self.leader_ok():
            return False
        # not the bot, or older ones still being let through: this one waits behind them (#code-review on 4e035df)
        now = lease_mod.clock()
        self.held_back = [(t, m) for t, m in self.held_back if now - t < lease_mod.QUEUE_SECS]
        if len(self.held_back) >= lease_mod.QUEUE_MAX:
            self.held_back = []
        self.held_back.append((now, message))
        return True

    async def let_through(self):
        if getattr(self, "letting", None):
            return  # (already letting them through: what arrived meanwhile is behind them, and goes too)
        try:
            while self.held_back and self.leader_ok():  # to the end, new ones included; not the bot again: the rest wait
                _, m = self.held_back.pop(0)
                self.letting = {id(m)}  # (this one alone goes past hold(); one arriving now waits behind the rest)
                try:
                    await self.on_message(m)
                except Exception:
                    traceback.print_exc()
        finally:
            self.letting = set()

    def busy_with_work(self):
        """Is something half done that a restart would make happen twice? A post being handed over (dcpost; it
        stays in the spool until done, so a restart redoes it: another --new-claude channel, a message typed
        twice), or a hook event being posted, or a final reply being laid out. A print-mode turn as before."""
        return self.active > 0 or bool(getattr(self.posts, "inflight", None)) or getattr(self.tmux, "handling", False) \
            or bool(getattr(self.tmux, "rewriting", None)) \
            or bool(getattr(self.tmux, "lane_held", None))  # another machine's events kept for a lane (C-174)

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
        its text (bot/screens.py); on a picture the bot posted with what it was made from: that (bot/sources.py)."""
        if payload.user_id != config.OWNER_ID or str(payload.emoji) not in (process.EMOJI, screens.EMOJI):
            return
        try:
            if str(payload.emoji) == process.EMOJI:
                await process.show(self, payload)
            elif not await screens.show_text(self, payload):
                await sources.show(self, payload)
        except Exception:
            traceback.print_exc()

    async def on_interaction(self, interaction):
        """Buttons and menus under Claude's questions (slash commands go through self.tree, not here)."""
        if getattr(self, "lease", None) is not None and not self.leader_ok() and interaction.type in (
                discord.InteractionType.component, discord.InteractionType.modal_submit):  # C-163: no time to wait (3
            # s): said, not taken. A slash command is answered by slash.begin(), an autocomplete cannot be (#code-review)
            try:
                return await interaction.response.send_message(messages.lease("busy"), ephemeral=True)
            except Exception:
                return
        if interaction.type == discord.InteractionType.component and \
                str((interaction.data or {}).get("custom_id") or "").startswith(USER_PREFIX):
            return await self.user_button(interaction)  # (a tool's button: first, no handler of the bot's is its)
        if interaction.type == discord.InteractionType.component and \
                ((interaction.data or {}).get("custom_id") or "").startswith("failback:"):
            return await self.failback_pressed(interaction)  # C-163 section 5, dcbot.failback: ask
        try:
            if await self.tmux.asks.on_interaction(interaction) or await self.tmux.menus.on_interaction(interaction) \
                    or await self.tmux.startup.on_interaction(interaction) \
                    or await self.setup.on_interaction(interaction) or await self.archive.on_interaction(interaction) \
                    or await self.terminal.on_interaction(interaction) or await self.posts.on_interaction(interaction) \
                    or await self.files.on_interaction(interaction) or await self.cards.on_interaction(interaction) \
                    or await self.launcher.on_interaction(interaction) or await self.secrets.on_interaction(interaction) \
                    or await self.tmux.on_interaction(interaction) or await self.tmux.cc.on_interaction(interaction) \
                    or await self.renamer.on_interaction(interaction) or await self.mover.on_interaction(interaction):
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

    async def user_button(self, interaction):
        """A button or menu of a message a tool posted (custom_id "user:…", C-172a P1c): nothing takes it yet
        (C-172b will hand it to a plugin or a Claude), so the one who pressed it is told, alone, instead of Discord's
        "interaction failed"."""
        try:
            await interaction.response.send_message(USER_BUTTON_SAID, ephemeral=True)
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
            if not setup_folder_ready():
                if first:
                    await self.open_setup_later(guild, first)
                else:
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
            self.hand_later(ch, first)
        return ch

    async def open_setup_later(self, guild, first):
        """#setup's folder not made yet: the bot came up in the middle of the install (install.sh discord starts it
        before the install makes the folder; test-3 2026-11-03). #setup is opened once the folder is there, looked
        at every SETUP_WAIT_EVERY seconds for as long as the bot runs (an install stopped half way and run again
        later too), without anyone restarting the bot. One wait at a time; said in #audit once."""
        if self.__dict__.get("setup_waiting") is not None:
            return

        async def wait():  # set as setup_waiting before its first await: a second call never starts a second wait
            try:
                await self.audit("#%s 等安裝做好設定資料夾 %s 就會自己開（安裝沒跑完的話，在機器人那台的引擎再跑一次 ./install.sh）"
                                 % (config.SETUP_CHANNEL, setup_folder().replace(os.path.expanduser("~"), "~")))
                while not setup_folder_ready():
                    await asyncio.sleep(SETUP_WAIT_EVERY)
                self.setup_waiting = None
                if not setup_done():  # done in the terminal meanwhile: setup.py done asks for #chat itself
                    await self.open_setup(guild, first=first)
            except Exception:
                traceback.print_exc()
            finally:
                self.setup_waiting = None
        self.setup_waiting = asyncio.ensure_future(wait())

    def hand_later(self, ch, text):
        """`text` handed to the channel's Claude in a task of its own; the task is returned. Handing it over waits
        for that Claude to come up, which the pump tells (its SessionStart), and a caller may be what the pump waits
        for: on_ready (it starts the pump only after own_channels, which opens #setup: every start with setup not done
        waited the 90 s out and said "Claude 沒有啟動成功", test-3 2026-11-03) or a round of the pump itself (a chore
        that opens #setup). What goes wrong is printed; the channel has said it already (submit())."""
        async def hand():
            try:
                await self.tmux.submit(posts.Origin(ch), text)
            except Exception:
                traceback.print_exc()
        task = asyncio.ensure_future(hand())
        handing = self.__dict__.setdefault("handing", set())  # (held: a task nothing refers to may be collected)
        handing.add(task)
        task.add_done_callback(handing.discard)
        return task

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
            await outgoing.deliver(ch, "💬 在這裡打字就是跟 Claude 對話（工作目錄 `%s`）。說明在 #%s；不懂的地方直接在這裡問。"
                                  % (st["cwd"].replace(os.path.expanduser("~"), "~"), config.GUIDE_CHANNEL), intent="status")
        except discord.HTTPException:
            pass

    async def settle_settings(self):
        """C-110: the bot's old settings file moved (altention/botmove.py) once the bot is on Discord, in a worker
        thread: copied first; deleted once the supervisor has marked a version that reads the new places good, so
        tried again every MOVE_EVERY seconds while it waits, MOVE_TRIES times. Each new line goes to #audit."""
        for _ in range(MOVE_TRIES):
            try:
                line, done = await asyncio.get_running_loop().run_in_executor(None, move_settings)
            except Exception as e:  # never the bot's concern beyond a line in its log
                print("[botmove] %s" % type(e).__name__, flush=True)
                return
            if line:
                await self.audit(line)
            if done:
                return
            await asyncio.sleep(MOVE_EVERY)

    async def audit(self, text):
        print("[audit] " + text, flush=True)
        if self.audit_ch:
            for part in split_message(text, 1900):
                try:
                    await outgoing.deliver(self.audit_ch, part, intent="status", keep=False)  # (#audit is the bot's own
                    # record, not kept: under load every line would write the whole ledger; as lease.py, manager 11-16)
                except Exception:  # a dropped connection too: the record is printed above, and callers go on
                    pass

    async def keep_in_audit(self, text, name, data):
        """One message in #audit with a file (C-205: a message saved whole before it is taken away): True when it is
        there, False when it is not (no #audit, Discord refused, a dropped connection): then the caller does not go
        on. `text` at most one message long; nobody rung."""
        print("[audit] " + text.split("\n", 1)[0], flush=True)
        if not self.audit_ch:
            return False
        try:
            await outgoing.deliver(self.audit_ch, text[:1990], file=discord.File(io.BytesIO(data), filename=name),
                                   allowed_mentions=discord.AllowedMentions.none(), intent="status", keep=False)
            return True
        except Exception:
            traceback.print_exc()
            return False

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
        try:  # C-201: each channel's `where` from its fields (bot/where.py); one that cannot be made is left as it was
            where_mod.stamp(self.channels_state, self.channel_name_of, *self.where_context(),
                            say=lambda line: print(line, flush=True))
        except Exception:
            traceback.print_exc()
        save_later(CHANNELS, self.channels_state)  # (a slow NFS write no longer stops the event loop: C-175)

    async def where_card(self, ch, st):
        """/where's card for a channel (C-201): its `where` as the last save wrote it, the conversation's name read
        from its file when it is on this machine (in a thread: a long file)."""
        await rename_mod.learn_agent(self, st, getattr(ch, "name", None))  # (pi, Codex: said as they are, C-198)
        self.save()  # (the record as the fields are now)
        rec = st.get("where")
        here, bot_socket = self.where_context()
        title = None
        conversation = ((rec or {}).get("agent") or {}).get("conversation")
        if conversation and ((rec or {}).get("place") or {}).get("machine") == here and st.get("cwd"):
            try:
                from altention.connectors.claude_code import sessions as cc_sessions
                got = await asyncio.to_thread(cc_sessions.summarize, cc_sessions.transcript(st["cwd"], conversation))
                title = (got or {}).get("title") or None
            except Exception:
                traceback.print_exc()
        return where_mod.card(rec, getattr(ch, "name", "?"), here, bot_socket, title=title, clock=clock_shown, say=messages.where)

    def recorded_on(self, pane):
        """The channel whose record points to this very pane (its machine, socket, number and process) though no
        route says so (a route lost: !attach went by the table only, and two records came to point to one pane:
        #code-review on 1a4cad50, manager 11-18), or None."""
        here = self.where_context()[0]

        def machine(p):
            return None if p.get("host") in (None, "", here) else p["host"]
        if not pane.get("pid"):
            return None
        for cid, st in self.channels_state.items():
            p = st.get("pane") if isinstance(st, dict) else None
            if isinstance(p, dict) and p.get("id") == pane.get("id") and machine(p) == machine(pane) \
                    and str(p.get("pid") or "") == str(pane["pid"]) \
                    and util.same_socket(p.get("socket"), pane.get("socket"), machine(pane) is None):
                return cid
        return None

    def where_context(self):
        """(this machine's name, the bot's own tmux server's socket) for bot/where.py: looked up once."""
        if getattr(self, "_where_context", None) is None:
            self._where_context = (remote.this_machine(), os.path.join(tmuxio.socket_dir(), config.TMUX_SOCKET))
        return self._where_context

    def channel_name_of(self, cid):
        ch = self.get_channel(int(cid)) if str(cid).isdigit() else None
        return getattr(ch, "name", None)

    def mode(self, st):
        """How the channel's turns run: "tmux", "print", or "text" (C-162: its own mode, or lower while this
        machine's channels are held down because Claude Code cannot be used as it is; cc_health.effective())."""
        return cc_health.effective(st.get("mode") or config.BRIDGE, st)

    def held(self, st):
        """Is the channel run lower than its own mode (C-162)? Then "print" or "text", else None."""
        now = self.mode(st)
        return now if now != (st.get("mode") or config.BRIDGE) else None

    def note(self, ch, st):
        """The system-prompt addition that tells Claude where it is."""
        place = (where_mod.put(st) or {}).get("place") or {}  # (C-201 2a: from its record, made now)
        machine = place.get("machine") or (st.get("home") or {}).get("host") or (st.get("pane") or {}).get("host") \
            or remote.this_machine()
        alt = remote.alt()
        data = os.environ.get("ALT_DATA") or os.path.join(alt.root(), "data")
        text = SYSTEM_NOTE.format(host=machine, channel=getattr(ch, "name", "DM"), cwd=st["cwd"], mark=messages.DISCUSS_MARK,
                                  root=config.ROOT, engine=os.path.dirname(config.ROOT),
                                  instance=alt.instance_dir() or "這台還沒有實例", progress=os.path.join(data, "progress"))
        if self.mode(st) == "tmux":
            session = place.get("session") or (st.get("home") or {}).get("session") or st.get("tmux", "")
            text += TMUX_NOTE.format(alt=remote.altention_cmd(machine), where="tmux attach -t %s" % session + (
                                         "（在 %s）" % st["home"]["host"] if st["home"].get("host") else "") if st.get("home") else
                                     "tmux -L %s attach -t %s" % (config.TMUX_SOCKET, session))
        return text

    # ------------------------------------------------------------------ messages
    @staticmethod
    def answer_in(ch):
        """What a command answers with in `ch` (the send it is handed): a message of the bot's, through deliver()."""
        return lambda *args, **kw: outgoing.deliver(ch, *args, intent="status", **kw)

    async def on_message(self, message):
        drill = drill_sender()
        if drill and message.webhook_id == drill:  # (the drill's rig speaking for the owner: only in the rig)
            pass
        elif message.author.bot:
            return
        elif message.author.id != config.OWNER_ID:
            if self.user in message.mentions:
                await self.audit("忽略非擁有者的訊息：%s 在 #%s" % (message.author, getattr(message.channel, "name", "DM")))
            return
        if getattr(self, "lease", None) is not None:  # C-163 (only with a standby)
            self.discord_ok()
            if self.hold(message):
                return
            if self.adopt_waits(message):
                return
        if not self.catchup.mark(message):
            return  # already handled (it came in live while the catch-up read it too)
        if getattr(message.channel, "name", None) == config.GUIDE_CHANNEL:
            return  # the manual: not a place to talk
        self.posts.owner_spoke(getattr(message.channel, "parent_id", None) or message.channel.id)
        if (self.channels_state.get(str(message.channel.id)) or {}).get("archived"):  # any kind (a record channel keeps
            # `plain`: C-63). The record decides, not the category: a channel set up again whose move out of the
            # archive Discord refused is used where it is (owner's report 2026-10-20); one moved into an archive
            # category by hand gets its record at the next start (adopt_all) and is used as it is until then
            return await self.archive.offer(message.channel)
        if (self.channels_state.get(str(message.channel.id)) or {}).get("plain") or \
                self.is_audit(message.channel) or getattr(message.channel, "name", None) == "notifications":  # inboxes
            if message.content.strip().split(" ")[0] in ("!setup", "!help", "!status", "!files", "!archive"):
                return await self.command(message.channel, message.content.strip(), self.answer_in(message.channel))
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
            return await outgoing.deliver(message.channel, "這個討論串是子代理或 workflow 的過程紀錄，這裡的訊息不會送給 Claude。要說話請回到 <#%d>。"
                                          % message.channel.parent_id, intent="status")
        text = message.content.strip()
        if (self.channels_state.get(str(message.channel.id)) or {}).get("kind") == "files":
            if text.split(" ")[0] in BUILTINS:
                return await self.command(message.channel, text, self.answer_in(message.channel))
            return await self.files.typed(message.channel, text) if text else None
        if (self.channels_state.get(str(message.channel.id)) or {}).get("kind") == "terminal":
            if text.split(" ")[0] in BUILTINS:  # the bot's own commands still work; "!!" and the like go to the shell
                return await self.command(message.channel, text, self.answer_in(message.channel))
            text = "\n".join([text] + await attachments.save(message, self.works_on(message.channel))).strip()
            return await self.terminal.run(message.channel, text) if text else None
        if not text and (getattr(message, "stickers", None) or getattr(message, "poll", None)):  # a sticker or a poll
            text = msgtext.text_of(msgtext.of_discord(message), reply=False)  # alone: said in words (C-172a P1a)
        now = text.startswith("!!")  # "!!…": Claude reads it right away, not after the tool call in progress
        if now:
            text = text[2:].strip()
        elif text.startswith("!"):
            return await self.command(message.channel, text, self.answer_in(message.channel))
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
        text = "\n".join([text] + await attachments.save(message, self.works_on(message.channel))).strip()
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
                board = util.board_of(st, ref.message_id)
                what = frames.of(s.content or "\n".join(msgtext.component_rows(msgtext.as_dicts(getattr(s, "components", None)))), embeds,
                                 by_bot=author.id == getattr(self.user, "id", None), by_owner=author.id == config.OWNER_ID,
                                 remembered=str(ref.message_id) in (st.get("turns") or {}),
                                 author=getattr(author, "display_name", None) or getattr(author, "name", None), board=board,
                                 entry=ledger.entry(ref.message_id, getattr(ref, "channel_id", None)),
                                 name_of=lambda cid: getattr(self.get_channel(cid), "name", None))
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
            saved = await attachments.save(shim, self.works_on(message.channel)) if s.attachments else []
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
        waits on a single question, the message is the answer to it, unless it is sent right away ("!!…", `now`: see
        TmuxBridge.submit)."""
        if peers.MENTION.search(text):  # "#st-0" means the session behind that channel
            text = peers.spell_mentions(text, self.channels_state, self.channel_label,
                                        remote.altention_cmd(files.host_of(self.chan(message.channel))))
        st = self.chan(message.channel)
        if self.needs_setup(message.channel, st):  # a new channel: ask where its Claude should live first
            return await self.setup.offer(message.channel, (message, text))
        if st.pop("cwd_from", None):  # say once where a channel opened by hand will work
            await outgoing.deliver(message.channel, "📁 這個頻道會在 `%s` 工作（沿用「%s」分類裡其他頻道的資料夾）。要換別的地方用 `/cwd`。"
                                   % (routes.short_cwd(st["cwd"]), message.channel.category.name), intent="status")
            self.save()
        if self.mode(st) == "tmux":
            if not now and await self.tmux.asks.typed(message.channel, text):  # ("!!…" is said right away, never an answer)
                try:
                    return await message.add_reaction("✅")
                except discord.HTTPException:
                    return
            return await self.tmux.submit(message, text, now=now, typed=lambda: self.woken(message.channel))
        await self.print_turn(message, text)  # (print or text mode: what was held waits for the resume)

    def woken(self, ch):
        """C-199 (manager 11-17): the owner's message went in to a paused channel's Claude as a plain message (typed and
        Enter pressed: TmuxBridge.submit's `typed`; a "/" command, one waiting on a confirmation, /logout, /btw, one not
        sent: never): what the pause held goes in after it, in a task of its own (bot/pauses.py wake())."""
        pauses = self.posts.pauses
        if pauses.of(ch.id) is not None:
            self.posts.later(pauses.wake(ch))

    async def print_turn(self, message, text):
        """A turn of a channel run in print or text (its own mode, or held down: C-162), one at a time per
        channel. Whatever else hands a channel's Claude words (a post, a button, a message sent again) comes here
        through TmuxBridge.submit() when the channel runs so."""
        key = str(message.channel.id)
        lock = self.locks.setdefault(key, asyncio.Lock())
        if lock.locked() and hasattr(message, "add_reaction"):  # (a stand-in for a message may have none)
            await message.add_reaction("⏳")
        async with lock:
            st = self.chan(message.channel)
            if self.mode(st) != "tmux":  # the mode first, then a place among the turns that run at once (#code-review-2:
                # a channel back at tmux does not wait for other channels' print turns before its window opens)
                held = self.held(st)
                if held:
                    await self.leave_window(message.channel, st, held)  # before a place is taken (#code-review on e30eba7)
                if held and self.sem.locked():  # held down, every place taken: said once (manager: no more at once)
                    await outgoing.deliver(message.channel, messages.cc("queued", mode=messages.cc("mode_" + held),
                                                                   most=config.MAX_PARALLEL, n=self.sem_waiting + 1),
                                           intent="status")
                self.sem_waiting += 1
                try:
                    await self.sem.acquire()
                finally:
                    self.sem_waiting -= 1
                try:
                    return await self.turn(message, text)
                finally:
                    self.sem.release()
        # back at tmux while it waited (`!mode retry`, `!mode tmux`): typed into the window, once the queue is let go
        # of (the window waits for it: TmuxBridge.print_done())
        await self.tmux.submit(message, text)

    async def leave_window(self, ch, st, held):
        """The first turn of a channel held down (C-162): its window is closed (its Claude Code keeps the conversation
        as it was before the print turns; going back up reopens it with --resume, cc_stale). Marked before anything is
        waited on (#code-review on ac37146); a turn running in the window ends first, not cut off, while it is still
        running there (a window gone or its Claude Code ended stops the wait); nothing is being typed meanwhile
        (rt.lock). Done before a place among the print turns is taken: waiting here holds no other channel up
        (#code-review and #code-review-2 on e30eba7)."""
        if (st.get("mode") or config.BRIDGE) != "tmux":
            return
        if st.get("cc_stale") and not await self.tmux.live_target(ch):  # closed already (cc_stale also marks a live
            return  # window to reopen after a pin, which is waited on and closed like any: #code-review on 7780fb3)
        st["cc_stale"] = True
        self.save()
        lock = self.tmux.rt(ch).lock
        try:  # bounded: a message that took rt.lock just before the channel was held down waits in print_done() for
            # this channel's queue, which this turn holds; each waiting on the other forever (#code-review on e36e823)
            await asyncio.wait_for(lock.acquire(), WINDOW_LOCK_SECS)
        except asyncio.TimeoutError:
            print("[cc] #%s：等不到 rt.lock（%d 秒），不關視窗，照樣用簡易模式（cc_stale 已標，回 tmux 時重開）"
                  % (getattr(ch, "name", "?"), WINDOW_LOCK_SECS), flush=True)
            return
        try:
            t0, said = time.time(), False
            while st.get("busy") and time.time() - t0 < config.TURN_TIMEOUT and await self.tmux.live_target(ch):
                if not said and time.time() - t0 > WINDOW_SAY_SECS:
                    said = True
                    await outgoing.deliver(ch, messages.cc("wait_window", mode=messages.cc("mode_" + held)), intent="status")
                await asyncio.sleep(2)
            await self.tmux.close(ch)
        finally:
            lock.release()

    def queue_turn(self, message, text):
        """print_turn() on a task of its own (TmuxBridge.submit(), for whatever is handed to a channel run in print
        or held down: the hand-over is done at once, the turn waits its place). What goes wrong is said in the
        channel. A post's spool file is gone once it is handed over: a restart while its turn waits or runs loses
        that turn (the post's card stays in the channel), as a restart always lost the owner's own message waiting
        in a print channel; neither shown again nor run twice (#code-review-2 on ac37146)."""
        async def run():
            try:
                await self.print_turn(message, text)
            except Exception as e:
                traceback.print_exc()
                try:
                    await outgoing.deliver(message.channel, "出錯了：%s" % e, intent="status")
                except Exception:
                    traceback.print_exc()
        return self.loop.create_task(run())

    def needs_setup(self, ch, st):
        return (isinstance(ch, discord.TextChannel) and not setup.configured(st)
                and not self.is_audit(ch) and ch.name != "notifications")

    async def on_guild_channel_delete(self, channel):
        """A channel was deleted: forget it. Its Claude is left running wherever it is (a tmux window may hold
        work in progress); #audit says where, so it can be closed or attached again by hand."""
        ledger.forget_channel(channel.id)  # (its messages are gone with it: C-172a P1a)
        try:
            await self.tmux.asks.gone(channel)  # a question it still asks: nobody can answer it now (#issues 21)
        except Exception:
            traceback.print_exc()
        try:
            self.tmux.menus.gone(channel)  # a menu shown there waits for nobody now
        except Exception:
            traceback.print_exc()
        st = self.channels_state.pop(str(channel.id), None)
        await routes.unbind_any(channel.id, ((st or {}).get("pane") or {}).get("host"), (st or {}).get("pane"))
        self.setup.cards.pop(channel.id, None)
        self.setup.held.pop(channel.id, None)
        self.tmux.rts.pop(str(channel.id), None)
        self.save()
        if st and (st.get("pane") or st.get("tmux")):
            where = ("tmux `%s`" % st["home"]["session"]) if st.get("home") else \
                tmuxio.label(st["pane"]) if st.get("pane") else "`tmux -L %s attach -t %s`" % (config.TMUX_SOCKET, st["tmux"])
            await self.audit("#%s 被刪了。它的 Claude 沒有關，還在 %s；不需要的話請自己關掉，或用 `/attach` 接到新頻道。"
                             % (channel.name, where))
        self.in_background("board-gone-%s" % channel.id, lambda: self.board_gone(channel.id))  # C-181: never waited on

    async def board_gone(self, cid):
        """C-181: a deleted channel that held a board (a plugin's "board" channel setting names it): ops/setup.py
        board-gone ID in a process of its own, which makes #dashboard again through dcctl and the spool (the bot must
        not wait on it) and tells #audit itself, at most once an hour; any other channel: nothing started."""
        script = os.path.join(os.path.dirname(config.ROOT), "ops", "setup.py")
        try:
            if not os.path.exists(script) or not await asyncio.to_thread(holds_board, cid):
                return
            proc = await asyncio.create_subprocess_exec(sys.executable, script, "board-gone", str(cid), stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), RECONCILE_SECS)
        except asyncio.TimeoutError:
            proc.kill()
            return await self.audit("重建看板頻道（setup.py board-gone）%d 秒沒跑完，這次先不管" % RECONCILE_SECS)
        except Exception as e:
            return await self.audit("重建看板頻道（setup.py board-gone）跑不起來：%s" % e)
        print("[board-gone] exit %s: %s" % (proc.returncode, out.decode("utf-8", "replace").strip()[-1000:]), flush=True)

    async def on_guild_role_update(self, before, after):
        """A role of the bot's changed (its permissions given or taken back, the link opened again): looked at again
        in the background, said in #audit (C-191: Discord's audit log does not record it)."""
        guild = getattr(after, "guild", None)
        me = getattr(guild, "me", None)
        if guild is not None and guild.id == config.GUILD_ID and me is not None and after in me.roles \
                and getattr(before, "permissions", None) != getattr(after, "permissions", None):
            self.perms.soon()

    async def on_member_update(self, before, after):
        """The bot given a role or one taken away (when Discord says so: no members intent): looked at again (C-191)."""
        if self.user is not None and after.id == self.user.id and getattr(before, "roles", None) != getattr(after, "roles", None):
            self.perms.soon()

    async def on_guild_channel_update(self, before, after):
        """A channel renamed (by hand or !rename): its Claude was told the old name when it started, so the next
        message it gets says the new one (no turn of its own just for that). Nothing keeps the old name: other
        places find the channel by its ID, and an old name now fails loudly instead of making a new channel."""
        if getattr(before, "name", None) == getattr(after, "name", None):
            return
        if self.is_audit(after):  # (C-201 6.2) kept by its ID: still the bot's record, said there
            return await self.audit("#%s 改名為 #%s：機器人照樣記在這個頻道（認的是頻道 ID），不會另開一個" % (before.name, after.name))
        st = self.channels_state.get(str(after.id))
        if not st or not posts.listens(st):
            return
        st["note_next"] = "（機器人通知：這個頻道已從 #%s 改名為 #%s，頻道 ID %d。之後提到或讀這個頻道請用新名字或 ID。）" % (
            before.name, after.name, after.id)
        self.save()
        if self.renamer.mine_said(after.id, after.name):
            return  # (the bot's own rename: said in its #audit line, the others renamed with it)
        if not st.get("where"):
            return await self.audit("#%s 改名為 #%s" % (before.name, after.name))
        await self.renamer.follow_discord(after, st, before.name)  # (C-201 2b, owner 11-17 point 2 a)

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

    def works_on(self, ch):
        """The machine the channel's Claude works on, for its attachments (None: this one)."""
        return files.host_of(self.channels_state.get(str(ch.id)) or {})

    def channel_label(self, cid):
        c = self.get_channel(cid)
        return "#%s" % c.name if c is not None else None

    async def rename(self, ch, st, name):
        """`!rename <name>` (/rename): the channel, its Claude Code conversation, its tmux window and (the bot's own
        for it alone) its session, as its record follows (C-201 2b, bot/rename.py). Its card."""
        return (await self.renamer.rename(ch, st, name))[1]  # (a channel with no conversation: its name alone)

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
        st = self.channels_state.get(str(getattr(message.channel, "id", ""))) or {}
        if who is None and replied.author.bot and str(replied.id) not in (st.get("turns") or {}):
            who = "機器人先前的這則訊息"  # not a reply the bot has on record as the channel's agent's (P0-6)
        content = msgtext.text_of(msgtext.of_discord(replied), reply=False)  # (C-172a P1a: every reader the same way)
        return util.reply_context(replied.author.bot, content, len(replied.attachments), who=who) + text

    async def turn(self, message, text):
        ch = message.channel
        st = self.chan(ch)
        name = getattr(ch, "name", "DM")
        note = self.note(ch, st)
        try:  # the channel's model and permission mode, as its window would be opened with (C-162: the model too)
            model, permission = await self.tmux.chosen_for(st)
        except NotOpened as e:
            return await outgoing.deliver(ch, str(e), intent="status")
        held = self.held(st)
        if held:
            await self.tmux.cc.maybe_back()  # (a version other than the one that brought it down: section 2.3)
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
                    status["msg"] = await outgoing.deliver(ch, body, intent="status", keep=False)  # (deleted at the end: not kept)
                else:
                    await outgoing.deliver(ch, content=body, intent="status", edit=status["msg"], keep=False)
            except discord.HTTPException:
                pass

        self.active += 1
        try:
            async with ch.typing():
                dirs = await attachments.folders(None, ch.id)
                if self.mode(st) == "text":  # layer 3 (C-162): the reply only
                    res = await run_text(text, st.get("session_id"), st["cwd"], note, permission, procs=self.procs,
                                         key=str(ch.id), channel_id=ch.id, dirs=dirs, model=model)
                else:
                    res = await run_turn(text, st.get("session_id"), st["cwd"], note, permission, on_progress=progress,
                                         procs=self.procs, key=str(ch.id), channel_id=ch.id, dirs=dirs, model=model)
        finally:
            self.active -= 1
        st["session_id"] = res.session_id
        self.save()
        if status["msg"] is not None:
            try:
                await outgoing.remove(ch, status["msg"])
            except discord.HTTPException:
                pass
        if res.error and getattr(res, "broken", False):  # claude ran, its output was not stream-json (C-162 section 2.1)
            await self.tmux.cc.failed("json", ch.id, why=res.error, text=res.error)
        elif res.error and getattr(res, "not_started", False):  # claude exited non-zero (section 3.2)
            await self.tmux.cc.failed("start", ch.id, why=res.error, text=res.error)
        elif not res.error and self.mode(st) == "print":
            self.tmux.cc.worked(ch.id, "json")
        body = frames.defused(res.text) if not res.error else "出錯了：%s" % res.error  # (P0-6: no reserved frame)
        tools = ", ".join("%s×%d" % kv for kv in sorted(res.tools.items(), key=lambda kv: -kv[1]))
        tail = self.print_tail(ch.id, st, res.session_id)
        footer = "-# %s%.0f 秒 · $%.2f%s" % ((tools + " · ") if tools else "", time.time() - t0, res.cost, tail)
        if held:  # C-162: said on every reply run lower; what is missing, on the first one at this layer
            footer += "\n" + messages.cc("footer_" + held)
            if st.get("cc_told") != held:
                footer += "\n-# " + messages.cc("missing")
                st["cc_told"] = held
                self.save()
        elif st.pop("cc_told", None):
            self.save()
        for part in split_message((body or "（沒有文字回覆）") + "\n" + footer):
            await outgoing.deliver(ch, part, intent="reply")
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
        words = (ref or "").split()
        quiet = "--quiet" in words  # switched without posting its history (owner's report 2026-10-20: the channel holds it already)
        ref = " ".join(w for w in words if w != "--quiet")
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
        held = list(st.get("history") or [])  # the channel's conversations before this switch (history.seen())
        sessions.remember(st, current)
        sessions.remember(st, s["id"])
        st["session_id"] = s["id"]
        self.save()
        try:
            if quiet:
                pass
            elif far:  # read over there, by the file that machine named (and holds to its own rule again when asked)
                asyncio.ensure_future(history.show_remote(self, ch, st, far, s["id"], s["path"], held=held))
            else:
                await history.show(self, ch, st, s["id"], cc_sessions.transcript(st["cwd"], s["id"]), held=held)
        except Exception:
            traceback.print_exc()
        return "已切到對話 `%s`（%s）%s。下一則訊息會接回它；原本的對話一樣可以用 `!resume` 換回來。" % (
            s["id"][:8], s["title"] or " ".join(s["first"].split())[:40], "，沒有貼紀錄" if quiet else "")

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
        taken = routes.routed_to(pane, routes.load()) or self.recorded_on(pane)
        was = ((self.channels_state.get(str(taken)) or {}).get("pane") or {}).get("pid") if taken else None
        if taken and self.get_channel(int(taken)) and not (was and pane.get("pid") and str(was) != str(pane["pid"])):
            return "這個對話已經接在 <#%s> 了。" % taken  # (a route from another machine's table: not when the number was the old pane's)
        st = self.chan(ch) if isinstance(ch, discord.TextChannel) else None
        if st is not None and not setup.configured(st) and not self.is_audit(ch) and ch.name != "notifications":
            return await self.attach_here(guild, pane, ch)  # a new channel nobody set up yet: it becomes this pane's
        return await self.attach_one(guild, pane, project or routes.project_of(pane))

    async def attach_here(self, guild, pane, ch):
        """/attach in a channel still waiting on its setup card: that channel takes the pane, where it is."""
        card = self.setup.cards.pop(ch.id, None)
        if card is not None:
            try:
                await outgoing.deliver(ch, content="✅ 接上了 tmux 的 `%s`。" % routes.window(pane), view=None, intent="card", edit=card)
            except discord.HTTPException:
                pass
        said = await self.attach_one(guild, pane, None, into=ch)
        for message, text in self.setup.held.pop(ch.id, []):  # typed before it was set up
            await self.tmux.submit(message, text)
        return said

    async def pane_session_id(self, pane):
        """The conversation a pane holds, as attach_one() reads it further on (None: not known)."""
        if pane.get("host"):
            code, out = await remote.dcremote(pane["host"], "backfill", pane["id"], pane["session"], pane["socket"], timeout=60)
            try:
                return (json.loads(out) if code == 0 else {}).get("session_id")
            except ValueError:
                return None
        rec = cc_sessions.of_pane(pane["id"], pane["session"]) or {"cwd": pane["cwd"]}
        screen = (await tmuxio.tmux("capture-pane", "-p", "-J", "-S", "-200", "-t", pane["id"], socket=pane["socket"]))[1]
        return cc_sessions.live_session(rec.get("cwd") or pane["cwd"], screen, rec)

    async def left_behind(self, pane, host):
        """C-112a: the channel this pane's conversation or tmux window had, whose own pane is gone (a reboot, tmux
        started anew): attached again, not opened anew. Only a channel attached to a pane, not archived (those are
        attach_one()'s other way), on the same machine: the same conversation first, else the same window and folder
        (the topic). A pane still alive, or a machine that does not say, keeps its channel."""
        found = [(c, st) for c, st in ((self.get_channel(int(cid)), st) for cid, st in self.channels_state.items())
                 if c is not None and st.get("pane") and not st.get("home") and kinds.channel_kind(st) == kinds.CONVERSATION
                 and st["pane"].get("host") == pane.get("host") and not archive.same_pane(st["pane"], pane)]
        if not found:
            return None
        sid, want = await self.pane_session_id(pane), routes.topic(host, pane)
        same = [x for x in found if sid and x[1].get("session_id") == sid] + [x for x in found if getattr(x[0], "topic", None) == want]
        for c, st in sorted(same, key=lambda x: (not (sid and x[1].get("session_id") == sid), -(x[1].get("last_active") or 0))):
            if await tmuxio.standing(st["pane"]) == "gone":
                return c
        return None

    async def attach_one(self, guild, pane, project, into=None):
        """Give one pane its channel under the project's category (reusing the one it had: archived, or left behind by
        a pane that is gone). `into`: a channel to use as it is instead."""
        host, kept_here = pane.get("host") or remote.this_machine(), None
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
            kept_here = await self.left_behind(pane, host) if new is None else None
            if kept_here is not None:
                new, back = kept_here, True
        if into is not None or kept_here is not None:
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
        had = st.get("session_id")
        if kept_here is not None:  # its old pane's route goes, what ran there ended with it; its conversation stays listed
            gone = st.get("pane")
            where_mod.put(st, pane=None)  # (out of the record before the awaits: a look meanwhile binds no route again, #code-review-2 on 1a4cad50)
            await routes.unbind_any(new.id, pane.get("host"), gone)  # (its tag too: C-201)
            st.pop("bg", None)
            sessions.remember(st, st.get("session_id"))
        st.pop("cwd_from", None)  # an attached session works where it already is
        rec = st.pop("archived", None)
        if into is not None:  # a channel not set up yet, maybe one in the archive (owner's report 2026-10-20): out of it first
            await self.archive.leave(new, rec, pane.get("cwd"))
        self.archive.offered.discard(new.id)
        address = {"socket": pane["socket"], "id": pane["id"], "pid": pane.get("pid")}
        if pane.get("host"):
            address["host"] = pane["host"]
        st.update({"cwd": pane["cwd"], "mode": "tmux",
                   "busy": False, "last_active": time.time()})
        where_mod.put(st, getattr(new, "name", None), pane=address, pane_session=pane["session"])  # (after its folder: in its record too)
        await rename_mod.learn_agent(self, st, getattr(new, "name", None))  # (an adapter's pane: its name in the record, C-198)
        # it was started outside the bot and knows nothing of Discord: told with the first message it gets here
        st["note_next"] = ("（機器人通知：這個對話已接到 Discord 的 #%s（頻道 ID %d），使用者會在那裡跟你對話。你每輪最後的回覆會貼到 "
                           "Discord：Discord 不支援表格，用清單。要給使用者看檔案（PDF、圖片、報告）就用 `%s dc post %d "
                           "--file <路徑> \"說明\"` 傳到頻道（上限 8 MB），不要只在這台電腦上打開。）"
                           % (new.name, new.id, remote.altention_cmd(pane.get("host")), new.id))
        self.save()
        await routes.bind_any(pane, new.id)
        await outgoing.deliver(new, "🔗 這個頻道%s接上了 **%s** 的 tmux `%s:%s`（`%s`）。\n"
                       "在這裡打字會送進那個對話，它的回覆會出現在這裡；你在終端機那邊打的內容也會顯示。"
                       "終端機照常可以用。不需要時用 `!detach` 卸下。"
                       % ("重新" if back else "", host, pane["session"], pane["window"], routes.short_cwd(pane["cwd"], pane.get("host"))),
                              intent="status")
        if pane.get("host"):  # its transcript is over there: dcremote reads it
            code, out = await remote.dcremote(pane["host"], "backfill", pane["id"], pane["session"], pane["socket"], timeout=60)
            try:
                got = json.loads(out) if code == 0 else {}
            except ValueError:
                got = {}
            if got.get("session_id"):
                st["session_id"] = got["session_id"]
                if not back:
                    history.mark(st, got["session_id"])  # the channel holds it now (owner's report 2026-10-20: a resume posted it again)
                    self.loop.create_task(self.backfill(new, sessions.backfilled(got)))
                self.save()
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
                    await outgoing.deliver(new, part, silent=True, intent="status")
            else:
                history.mark(st, sid)  # the channel holds it now (owner's report 2026-10-20)
                self.save()
                self.loop.create_task(self.backfill(new, sessions.history_messages(path)))
        if kept_here is not None and st.get("session_id") != had:  # started anew in that window: the old one is not lost
            await outgoing.deliver(new, "這次接上的是另一個對話。要換回原本的：在終端機那個窗格結束 Claude，再跑 `claude --resume%s`。"
                                   % (" %s" % had[:8] if had else ""), intent="status")
        return "已接上 `%s`：<#%d>（分類「%s」%s）。" % (routes.window(pane), new.id, project,
                                                   "，沿用原本的頻道" if kept_here is not None else "，沿用之前封存的頻道" if back else "")

    async def backfill(self, ch, messages):
        """Post a session's whole earlier conversation into its new channel, without notifications. Runs on
        its own: a long conversation is a few hundred messages and Discord takes about one per second."""
        try:
            if not messages:
                return
            await outgoing.deliver(ch, "📜 **以下是接上之前的完整對話**（共 %d 則訊息，貼完會有結尾標記；這段期間照常可以打字）" % len(messages),
                                  silent=True, intent="status", keep=False)  # (a backfill is not kept: a few hundred would push
            for part in messages:                                # the channel's cards and boards out of its 500; manager 11-16)
                await outgoing.deliver(ch, part, silent=True, intent="status", keep=False)
            await outgoing.deliver(ch, "📜 **以上是接上之前的對話**，從這裡開始是即時的。", silent=True, intent="status", keep=False)
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
        place = archive.where_from(ch, ch.guild)  # where it was, for 接回 to put it back there (C-63)
        rec = dict(archive.record(st, why), **place)
        gone = st.get("pane")
        where_mod.put(st, pane=None)  # (out of the record before the awaits: a look meanwhile binds no route again, #code-review-2 on 1a4cad50)
        await routes.unbind_any(ch.id, (gone or {}).get("host"), gone)
        self.channels_state[str(ch.id)] = dict(((k, st[k]) for k in archive.KEPT_HISTORY if k in st), archived=rec,
                                               cwd=st.get("cwd"))  # for 接回; what it has shown and held stays (owner's report 2026-10-20)
        self.save()
        try:
            shelf = await archive.shelf(ch.guild)
            await ch.edit(category=shelf, topic=archive.topic_with(place.get("topic") or "", rec))
        except discord.HTTPException as e:
            return "已卸下，但頻道沒有移到封存（%s）。對話還在：%s" % (e, where)
        reuse = "" if home else "；之後對同一個 tmux 視窗 `!attach`，會把這個頻道搬回去沿用"
        return "已卸下。對話還在原處繼續跑（%s），機器人不再跟著它。這個頻道移到「%s」，不會再處理這裡的訊息%s。" % (
            where, shelf.name, reuse)

    async def attached_may_switch(self, st, cmd, arg):
        """`!mode print` in a channel attached to the owner's own pane here, once that pane runs no Claude Code (C-162:
        its screen not known, the owner closed it; the channel goes on with the same conversation in print), and
        `!mode tmux` back from that. Never while the pane runs: two Claudes on one conversation."""
        want = arg.strip()
        if cmd != "!mode" or (st.get("pane") or {}).get("host"):
            return False
        if want == "tmux":
            return st.get("mode") == "print"
        return want == "print" and await tmuxio.standing(st["pane"]) == "gone"

    async def command(self, ch, text, send):
        """A built-in command, from a "!" message or from Discord's "/" menu. `send` posts the answer."""
        cmd, _, arg = text.strip().partition(" ")
        st = self.chan(ch)
        tmux = self.mode(st) == "tmux"
        if st.get("kind") == "files" and cmd in ("!reset", "!resume", "!cwd", "!mode", "!screen", "!key", "!stop", "!detach"):
            await send("這是檔案頻道，`%s` 不適用。直接打字找檔案或打路徑；要改成別的用途，刪掉頻道重開比較乾淨。" % cmd)
        elif st.get("pane") and not st.get("home") and cmd in ("!reset", "!resume", "!cwd", "!mode") and \
                not await self.attached_may_switch(st, cmd, arg):
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
            await send(talk_limit_said(st, posts.default_limit()))
        elif cmd == "!agentanswers":  # C-176: may other channels' Claude answer this one's questions, menus, plans
            want = arg.strip().lower()
            if want in ("off", "關"):
                st[agentanswer.FLAG] = False
            elif want in ("on", "開"):
                st.pop(agentanswer.FLAG, None)
            elif want:
                return await send(AGENT_ANSWERS_USAGE)
            self.save()
            await send(AGENT_ANSWERS_ON if agentanswer.allowed(st) else AGENT_ANSWERS_OFF)
        elif cmd == "!rename":
            await send(await self.rename(ch, st, arg))
        elif cmd == "!move":  # C-201 2c1 (bot/moving.py)
            await self.mover.command(ch, st, arg, send)
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
        elif cmd == "!where":  # C-201 (2b: its 〔改名…〕 button)
            text = await self.where_card(ch, st)
            if st.get("where") and posts.listens(st):
                view = self.renamer.view(ch)
                view.add_item(self.mover.view(ch))  # (2c1: 〔搬到…〕)
                await send(text, view=view)
                view.stop()  # clicks arrive in on_interaction by custom_id
            else:
                await send(text)
        elif cmd == "!status":
            up = int(time.time() - self.started)
            busy = sum(1 for c in self.channels_state.values() if c.get("busy")) + self.active
            held = self.held(st)
            shown = messages.cc(("status_json" if cc_health.json_alone() else "status_" + held),  # (the screen is fine)
                                version=cc_health.broken_version("json" if cc_health.json_alone() else "tmux") or "?") if held else \
                "`%s`" % self.mode(st)
            info = "版本 `%s`（%s）· 已上線 %d 分鐘 · 進行中 %d 個工作\n這個頻道：模式 %s，工作目錄 `%s`，對話 `%s`" % (
                git("rev-parse", "--short", "HEAD"), version_note(), up // 60, busy,
                shown, st["cwd"], st.get("session_id", "（尚未開始）"))
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
            await resets.new_conversation(self, ch, st)  # (the same way as a Claude's --reset)
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
                sessions.remember(st, st.get("session_id"))  # still the channel's own: !resume can take it up again
                st["cwd"] = path
                st.pop("session_id", None)  # a session is tied to its directory
                self.save()
                await send("工作目錄改為 `%s`，並開始新的對話。" % path)
        elif cmd == "!cctest":  # C-162 section 5.1 B: by hand only (it spends a few Haiku turns)
            await self.audit("C-162：`!cctest`（#%s）" % getattr(ch, "name", "?"))
            await send(await self.tmux.cc.cctest(send))
        elif cmd == "!mode":
            want = arg.strip()
            if want == "retry":  # C-162 section 2.3: the probe now; it passing lets this machine's channels go back up
                await self.audit("C-162：`!mode retry`（#%s）" % getattr(ch, "name", "?"))
                await send(await self.tmux.cc.retry(say=send))
            elif want not in ("tmux", "print"):
                await send(messages.cc("mode_usage", mode=messages.cc("mode_" + self.mode(st))))
            else:
                await self.tmux.close(ch)  # never two Claude processes on one conversation
                st["mode"] = want
                self.save()
                held = self.held(st)  # C-162: the machine's level still holds it lower (#code-review-2 on 7c0ac52)
                alone = cc_health.json_alone()  # stream-json broken, the screen fine: said so (#code-review-2 on e36e823)
                await send(messages.cc("mode_held_json" if alone else "mode_held", want=want, mode=messages.cc("mode_" + held),
                                       version=cc_health.broken_version("json" if alone else "tmux") or "?") if held else
                           "這個頻道改為 `%s` 模式，對話會接續。" % want)
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


MOVE_EVERY = 60     # seconds between the bot's tries while the old settings file waits (proven(), C-110)
MOVE_TRIES = 60     # then left to the next start or install


def move_settings():
    """C-110: one try of altention/botmove.py's move, in a worker thread (never on the bot's loop, never before it is
    on Discord: manager 2026-11-03). (line to post in #audit or "", done): nothing raised (the settings were read
    already, the same way whether or not they moved); a line the same as the last one said is not said again (a
    move that stays stuck, or keeps waiting, is said once, not at every start or try)."""
    try:
        from altention import botmove
        _code, line, done = botmove.move()
    except Exception as e:  # its kind only (botmove itself says nothing else either)
        from altention.text import t
        line, done = t("botmove.kept", why=t("botmove.why_error", kind=type(e).__name__), todo=t("botmove.todo_again")), True
    if not line:
        return "", done
    print("[botmove] %s" % line, flush=True)
    said = os.path.join(config.STATE, "botmove-said")
    try:
        with open(said, encoding="utf-8") as f:
            last = f.read()
    except OSError:
        last = ""
    if line == last:
        return "", done
    try:
        os.makedirs(config.STATE, exist_ok=True)
        with open(said, "w", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    return line, done


END_SECS = 30  # give_up(): the process still here this long after its start task was cancelled → os._exit


async def run_bot(bot, token):
    """discord.Client.run()'s runner, with the start in a task of its own: give_up() cancels it (end_now())."""
    async with bot:
        bot.start_task = asyncio.ensure_future(bot.start(token))
        try:
            await bot.start_task
        except asyncio.CancelledError:
            if not getattr(bot, "lease_over", False):
                raise


def ended(signum, frame):
    """SIGTERM (uninstall.sh, SUPERVISOR-RESCUE.md: kill of bot.pid): ended as by sys.exit, so main()'s finally
    runs and the channels' state handed to the writer is written (C-175; by default the signal ended the process
    there and then)."""
    raise SystemExit(128 + signum)


def main():
    if not config.TOKEN or not config.OWNER_ID:
        sys.exit("缺少設定：機器人的 token 和擁有者的 ID 都要有。在自己的終端機跑 %s discord"
                 % os.path.join(os.path.dirname(config.ROOT), "install.sh"))
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "bot.pid"), "w") as f:
        f.write(str(os.getpid()))
    tmuxio.configure(socket=config.TMUX_SOCKET, user_tmux=config.USER_TMUX)  # the one place the bot's own server is named
    bot = Bot()
    cc_watch.start()  # C-162: failures counted, the probe run (never in a test: they do not run main())
    awaiting.start()  # state/waiting-on-owner.json: as things are now (the bot's state is read), before Discord is reached
    discord_warnings()
    signal.signal(signal.SIGTERM, ended)  # (C-175: a kill of bot.pid still writes the last channels' state)
    try:
        bot.run(config.TOKEN, log_handler=None)
    finally:
        awaiting.stop()  # ending in the ordinary way: nothing waits on a bot that is not there
        statewrite.WRITER.flush(30)  # the last channels' state written before the process goes (C-175)
    sys.exit(bot.exit_code)


if __name__ == "__main__":
    main()
