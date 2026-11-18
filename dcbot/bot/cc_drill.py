"""cc-drill: rehearse what the bot does when Claude Code breaks (C-162, design section 5.1). Run from dcbot/ with the
bot's Python: `python -m bot.cc_drill [--real|--json]` (a module of the bot, not a tool: it uses the bridge, which the
tools must not import, or every change of the bridge would have to be deployed as one that runs before a restart).

  cc_drill                  A: every way of breaking, one at a time, with no login and no usage: the real Claude
                            Code of this machine talks to a fake API on 127.0.0.1 in a scratch HOME; broken versions
                            are small wrappers around it (a screen with no box, stream-json that is not JSON, a
                            claude that exits 1, --resume that fails, the login screen). What runs is the bot's own
                            probe and decisions (cc_watch.Watch, cc_probe.Probe) on a tmux server of the drill's own.
  cc_drill --real           B: the installed Claude Code checked with the real login, as !cctest does (about 5 Haiku
                            turns); passing keeps it as cc_last_good. By hand only.
  cc_drill --json           A, with one line of JSON before "done" ({"ok", "screen", "print", "version"}: the bot
                            reads it)
  cc_drill --help

Each step prints 接住了 / 沒接住 and what it saw; the last line is "done". Exit status: 0 every step caught, 1 one or
more not, 2 the drill could not start (no Claude Code, no tmux). The scratch HOME, the tmux server and the fake
API are the drill's own and removed at the end; the real HOME is only read (A, step 10: a copy of ~/.claude.json,
never the login, which is in .credentials.json and is not copied).
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


KEY = "sk-ant-api03-drilldrilldrilldrilldrilldrilldrilldrilldrill-AAAAAAAAAAAAAAAAAAAA"


# ------------------------------------------------------------------ the fake API
class Api(BaseHTTPRequestHandler):
    """Answers every turn with text: "ok", or, asked what it was told to remember, the word (the probe's step 4)."""
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        if "count_tokens" in self.path:
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            return self.wfile.write(b'{"input_tokens": 10}')
        said = json.dumps(req.get("messages") or [], ensure_ascii=False)
        text = "ok"
        if "記住的字是什麼" in said and "記住這個字：" in said:
            text = said.split("記住這個字：", 1)[1][:7]
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()

        def ev(name, data):
            self.wfile.write(("event: %s\ndata: %s\n\n" % (name, json.dumps(data))).encode())
            self.wfile.flush()
        ev("message_start", {"type": "message_start", "message": {
            "id": "msg_drill", "type": "message", "role": "assistant", "model": req.get("model", "x"), "content": [],
            "stop_reason": None, "usage": {"input_tokens": 10, "output_tokens": 1}}})
        ev("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        ev("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})
        ev("content_block_stop", {"type": "content_block_stop", "index": 0})
        ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}})
        ev("message_stop", {"type": "message_stop"})


# ------------------------------------------------------------------ the broken versions
WRAPPERS = {
    "ok": 'exec "$REAL" "$@"',
    "screen": ('case " $* " in *" -p "*) exec "$REAL" "$@";; esac\n'
               'printf "\\033[2J\\033[H╭──── a screen the bot does not know ────╮\\n│ hello │\\n╰────╯\\n"; exec sleep 600'),
    "json": ('case " $* " in *" stream-json "*) echo "this is not json"; cat >/dev/null; exit 0;; esac\nexec "$REAL" "$@"'),
    "start": 'echo "Error: could not start" >&2; exit 1',
    "resume": 'case " $* " in *" --resume "*) echo "No conversation found with session ID" >&2; exit 1;; esac\nexec "$REAL" "$@"',
    "login": ('case " $* " in *" -p "*) echo "Invalid API key · Please run /login" >&2; exit 1;; esac\n'
              'printf "\\033[2J\\033[HInvalid API key · Please run /login\\n"; exec sleep 600'),
}


def wrapper(folder, version, kind, real):
    path = os.path.join(folder, version)
    with open(path, "w") as f:
        f.write("#!/bin/sh\nREAL=%s\n%s\n" % (shell_quote(real), WRAPPERS[kind]))
    os.chmod(path, 0o755)
    return path


def shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def real_claude():
    from . import config
    got = os.path.realpath(config.CLAUDE)
    return got if os.path.isfile(got) and os.access(got, os.X_OK) else None


def version_key(name):
    try:
        return tuple(int(x) for x in name.split(".")[:3])
    except ValueError:
        return None


def older_version(real):
    """The newest Claude Code version older than `real` kept by its installer next to it (step 10), or None. Compared
    as numbers (2.1.99 before 2.1.100: #code-review-2)."""
    folder, mine = os.path.dirname(real), version_key(os.path.basename(real))
    if mine is None:
        return None
    older = [n for n in os.listdir(folder) if version_key(n) is not None and version_key(n) < mine]
    return os.path.join(folder, max(older, key=version_key)) if older else None


# ------------------------------------------------------------------ the world
def last_path():
    from . import config
    return os.path.join(config.STATE, "cc-drill-last.json")  # (read before the world points config.STATE elsewhere)


def record(root, servers=()):
    """The world of the drill running now (None once closed): its folder, its tmux servers (the drill's and step
    10's) and its pid. A drill killed before it closed its world leaves this, and the next one cleans what it names
    (that only); one still running is left alone (#code-review-2 on 20ebccb)."""
    path = last_path()
    try:
        if root is None:
            os.remove(path)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump({"root": root, "servers": list(servers), "pid": os.getpid(), "at": time.time()}, f)
    except OSError:
        pass


GONE_AFTER = 30 * 60 + 300  # a record older than the bot's limit on a drill (cc_watch.DRILL_SECS) and some: none runs


def alive(pid):
    try:
        os.kill(pid, 0)
    except (OSError, TypeError, ValueError, OverflowError):
        return False
    return True


def leftover():
    """Clean what the last drill left; "running" when it is still running (then nothing is cleaned or started)."""
    try:
        with open(last_path()) as f:
            got = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict):
        got = {}
    if isinstance(got.get("pid"), int) and got["pid"] != os.getpid() and alive(got["pid"]) and \
            isinstance(got.get("at"), (int, float)) and time.time() - got["at"] < GONE_AFTER and \
            isinstance(got.get("root"), str) and os.path.isdir(got["root"]):
        return "running"  # (a pid alive alone is not enough: after a reboot /tmp is empty and the number may be
        # another program's; too old a record, or its folder gone, is a drill that is no more: manager 11-08)
    root = got.get("root")
    if isinstance(root, str) and os.path.basename(root).startswith("ccd.") and os.path.dirname(root) == tempfile.gettempdir():
        for server in got.get("servers") or ():
            if isinstance(server, str) and server.startswith("dcbot-test-drill"):
                subprocess.run(["tmux", "-L", server, "kill-server"], stderr=subprocess.DEVNULL, timeout=30,
                               env=dict(os.environ, TMUX_TMPDIR=os.path.join(root, "t")))
        shutil.rmtree(root, ignore_errors=True)
    record(None)
    return None


def summary(results, screen_ok, print_ok, version):
    """The --json line: every step caught, step 0's two halves, the steps not caught."""
    failed = [i for i, ok in enumerate(results) if not ok]
    return {"ok": bool(results) and not failed, "started": screen_ok is not None, "screen": bool(screen_ok),
            "print": bool(print_ok), "version": version, "failed": failed,
            "why": ("沒接住的步驟：%s" % "、".join(str(i) for i in failed)) if failed else None}



class World(object):
    """A scratch HOME and state, the fake API, the drill's tmux server, the bot's modules pointed at them."""

    def __init__(self, real):
        self.real = real
        self.root = tempfile.mkdtemp(prefix="ccd.")  # short: tmux's socket path has a length limit
        record(self.root, ("dcbot-test-drill-%d" % os.getpid(), "dcbot-test-drill10-%d" % os.getpid(),
                           "dcbot-test-drill11-%d" % os.getpid()))
        self.home = os.path.join(self.root, "h")
        self.state = os.path.join(self.root, "s")
        self.versions = os.path.join(self.root, "v")
        for d in (self.home, self.state, self.versions, os.path.join(self.root, "t")):
            os.makedirs(d)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Api)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]
        with open(os.path.join(self.home, ".claude.json"), "w") as f:
            json.dump({"hasCompletedOnboarding": True, "theme": "dark", "numStartups": 5, "autoUpdates": False,
                       "customApiKeyResponses": {"approved": [KEY[-20:]], "rejected": []}}, f)
        self.env = {"HOME": self.home, "ANTHROPIC_API_KEY": KEY, "ANTHROPIC_BASE_URL": "http://127.0.0.1:%d" % port,
                    "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "TMUX_TMPDIR": os.path.join(self.root, "t"),
                    "DCBOT_CC_HEALTH": os.path.join(self.state, "cc_health.json")}
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        from . import cc_health, cc_probe, config, tmux_bridge, tmuxio
        self.was = (config.ROOT, config.STATE, tmux_bridge.SPOOL, tmux_bridge.SETTINGS_FILE, cc_probe.START_SECS,
                    cc_probe.REPLY_SECS)
        config.ROOT, config.STATE = self.root, self.state  # (the hooks of the probe spool here: DCBOT_ROOT)
        tmux_bridge.SPOOL = os.path.join(self.root, "state", "events")
        tmux_bridge.SETTINGS_FILE = os.path.join(self.state, "claude-settings.json")
        cc_probe.START_SECS, cc_probe.REPLY_SECS = 30, 60
        tmuxio.configure(socket="dcbot-test-drill-%d" % os.getpid())
        tmux_bridge.write_settings()
        self.cc_health, self.tmuxio = cc_health, tmuxio

    def close(self):
        from . import cc_probe, config, tmux_bridge
        for server in ("dcbot-test-drill-%d" % os.getpid(), "dcbot-test-drill10-%d" % os.getpid(),  # (steps 10, 11 too)
                       "dcbot-test-drill11-%d" % os.getpid()):
            subprocess.run(["tmux", "-L", server, "kill-server"], stderr=subprocess.DEVNULL, env=dict(os.environ), timeout=30)
        self.server.shutdown()
        (config.ROOT, config.STATE, tmux_bridge.SPOOL, tmux_bridge.SETTINGS_FILE, cc_probe.START_SECS,
         cc_probe.REPLY_SECS) = self.was
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.root, ignore_errors=True)
        record(None)

    def fresh(self):
        """A clean health file and probe folder for the next step."""
        for name in ("cc_health.json", "cc-probe", "claude-keep"):
            p = os.path.join(self.state, name)
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else (os.path.exists(p) and os.remove(p))


class Bridge(object):
    """What the watch needs of the bridge: the channels it holds, the notification channel, #audit."""
    def __init__(self):
        self.said, self.audits, self.reopened = [], [], []
        outer = self

        class Host(object):
            channels_state = {"1": {"tmux": "dc-a", "cc_version": "9.9.1"}}

            async def audit(self, text):
                outer.audits.append(text)

            def save(self):
                pass
        self.host = Host()

    def held_channels(self):
        return ["1"]

    def reopen_off(self, version):
        self.reopened.append(version)

    async def notify_channel(self):
        outer = self

        class Ch(object):
            id = 1

            async def send(self, text, **kw):
                outer.said.append(text)
                return type("Sent", (), {"id": len(outer.said), "edit": staticmethod(lambda **k: asyncio.sleep(0))})()
        return Ch()


# ------------------------------------------------------------------ the steps
async def pump(drill, stop):
    from . import cc_probe, events, tmux_bridge
    while not stop.is_set():
        for path, ev in events.waiting(tmux_bridge.SPOOL):
            if ev.get("channel") == cc_probe.PROBE:
                drill.probe.event(ev)  # (the probe of the step running now)
            events.done(path)
        await asyncio.sleep(0.2)


class Drill(object):
    def __init__(self, world):
        self.world, self.results = world, []
        from . import calls, cc_watch
        cc_watch.ACTIVE, cc_watch.RETRY_SECS = True, 1
        calls.system_path_was = getattr(calls, "_system_path", None)
        calls._system_path = lambda: os.path.join(world.state, "calls-system.json")

    def watch(self):
        from . import cc_probe, cc_watch, tmux_bridge
        self.probe = cc_probe.Probe(tmux_bridge.SETTINGS_FILE, lambda st: "auto")
        self.bridge = Bridge()
        return cc_watch.Watch(self.bridge, probe=self.probe, started=1)

    def use(self, version, kind):
        from . import config
        config.CLAUDE = wrapper(self.world.versions, version, kind, self.world.real)
        return config.CLAUDE

    def step(self, n, name, ok, saw):
        self.results.append(ok)
        print("%2d %s：%s — %s" % (n, name, "接住了" if ok else "沒接住", saw), flush=True)

    async def settle(self):
        for _ in range(600):
            others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not getattr(t, "drill_pump", False)]
            if not others:
                return
            await asyncio.wait(others, timeout=1)

    async def run(self):
        from . import cc_health
        stop = asyncio.Event()
        self.watch()
        t = asyncio.ensure_future(pump(self, stop))
        t.drill_pump = True
        try:
            await self.steps()
        finally:
            stop.set()
            await t
        return all(self.results), cc_health

    async def fails(self, w, kind, times, channels=(1,), why="x"):
        for i in range(times):
            await w.failed(kind, channels[i % len(channels)], why=why)
            await self.settle()

    async def steps(self):
        h = self.world.cc_health
        # 0. the healthy version passes the probe (else every other step says nothing)
        self.world.fresh()
        self.watch()
        self.use("9.9.0", "ok")
        got = await self.probe.run(h.binary())
        self.screen_ok = got.get("ok") is True
        printed = await self.probe.print_check(h.binary())
        self.print_ok = printed.get("ok") is True
        self.step(0, "這台的 Claude Code 本身：畫面、簡易模式", self.screen_ok and self.print_ok,
                  json.dumps({"probe": {k: got.get(k) for k in ("ok", "step", "why")}, "print": printed}, ensure_ascii=False))
        # 1. the screen not known: probe fails twice, no kept version -> simple mode, said once
        self.world.fresh()
        w = self.watch()
        self.use("9.9.1", "screen")
        await self.fails(w, "tmux", 1)
        self.step(1, "畫面認不得 → 簡易模式", h.effective("tmux", {"tmux": "x"}) == "print" and len(self.bridge.said) == 1,
                  "broken=%s said=%d" % (list(h.broken()), len(self.bridge.said)))
        # 2. stream-json broken: print channels -> text, tmux channels untouched
        self.world.fresh()
        w = self.watch()
        self.use("9.9.2", "json")
        await self.fails(w, "json", 3)
        self.step(2, "stream-json 壞了 → 純文字", h.effective("print", {}) == "text" and h.effective("tmux", {"tmux": "x"}) == "tmux",
                  "broken=%s" % list(h.broken()))
        # 3. claude that does not start: back to the kept version; then a good new one lets the pin go
        self.world.fresh()
        w = self.watch()
        kept = self.use("9.9.0", "ok")
        why = await asyncio.get_running_loop().run_in_executor(None, h.keep, "9.9.0", kept)
        self.use("9.9.3", "start")
        await self.fails(w, "start", 2, channels=(1, 2), why="exit 1")
        pinned = (h.pin() or {}).get("version")
        self.use("9.9.4", "ok")
        w.version_seen = (0, None)
        await w.tick()
        await self.settle()
        self.step(3, "起不來 → 退回留下的版本，新版好了再放開", why is None and pinned == "9.9.0" and h.pin() is None and self.bridge.reopened == ["9.9.0"],
                  "keep=%s pinned=%s now=%s reopened=%s" % (why, pinned, (h.pin() or {}).get("version"), self.bridge.reopened))
        # 4. a restart: failures in the first minutes wait, one probe after them (healthy: nothing)
        self.world.fresh()
        w = self.watch()
        w.started = time.time()
        self.use("9.9.0", "ok")
        got = await w.failed("tmux", 1, why="x")
        w.started = 1
        await w.after_grace()
        self.step(4, "剛啟動時的失敗先記著，之後探測一次", got == "held" and not h.broken(), "first=%s broken=%s" % (got, list(h.broken())))
        # 5. a machine too busy: not counted
        self.world.fresh()
        w = self.watch()
        was = h.busy_machine
        h.busy_machine = lambda: "load 99"
        try:
            got = await w.failed("tmux", 1, why="x")
        finally:
            h.busy_machine = was
        self.step(5, "機器太忙的失敗不算", got == "not_the_version" and not h.entry().get("versions"), "got=%s" % got)
        # 6. --resume broken in channels: suspected, the probe's step 4 fails -> the kept version
        self.world.fresh()
        w = self.watch()
        kept = self.use("9.9.0", "ok")
        await asyncio.get_running_loop().run_in_executor(None, h.keep, "9.9.0", kept)
        self.use("9.9.6", "resume")
        h.note_ok("9.9.6", 9)
        await self.fails(w, "resume", 2, channels=(1, 2))
        probe = h.entry().get("versions", {}).get("9.9.6", {}).get("probe") or {}
        self.step(6, "多個頻道接不回 → 探測第 4 步失敗 → 退回", (h.pin() or {}).get("version") == "9.9.0" and probe.get("step") == 4,
                  "probe=%s pinned=%s" % (probe, (h.pin() or {}).get("version")))
        # 7. the kept copy damaged: no pin, simple mode, said as it is
        self.world.fresh()
        w = self.watch()
        kept = self.use("9.9.0", "ok")
        await asyncio.get_running_loop().run_in_executor(None, h.keep, "9.9.0", kept)
        with open(h.last_good()["path"], "r+b") as f:
            f.write(b"\0")
        self.use("9.9.7", "screen")
        await self.fails(w, "tmux", 1)
        self.step(7, "留下的那份壞了 → 不退回、改簡易模式", h.pin() is None and "tmux" in h.broken(), "pin=%s broken=%s" % (h.pin(), list(h.broken())))
        # 8. the login screen: none of the version's
        self.world.fresh()
        w = self.watch()
        self.use("9.9.8", "login")
        got = await w.failed("tmux", 1, text="Invalid API key · Please run /login")
        h.note_ok("9.9.8", 9)
        await self.fails(w, "start", 2, why="Invalid API key · Please run /login")
        self.step(8, "登入失效不算版本壞了", got == "not_the_version" and not h.broken() and h.pin() is None,
                  "got=%s broken=%s" % (got, list(h.broken())))
        # 9. a new version's first failure is probed at once
        self.world.fresh()
        w = self.watch()
        self.use("9.9.9", "screen")
        got = await w.failed("tmux", 1)
        await self.settle()
        self.step(9, "新版第一次失敗就探測", got == "suspect" and "tmux" in h.broken(), "got=%s" % got)
        # 10. an older version on a copy of the real ~/.claude.json (no login): keys it drops, a settings error
        self.step10()
        # 11. a folder handed in the settings, as a channel's attachments folder, asks nothing about trusting it
        self.step11()

    def step11(self):
        ok, saw = step11_view(self)
        self.step(11, "設定裡給的資料夾不問信不信任（頻道的附件資料夾就是這樣給的）", ok is True, saw)

    def step10(self):
        older = older_version(self.world.real)
        if not older:
            return self.step(10, "舊版會不會改壞 ~/.claude.json", True, "沒有別的版本可以量，跳過")
        real_json = os.path.join(self.world.saved.get("HOME") or os.path.expanduser("~"), ".claude.json")
        home = os.path.join(self.world.root, "h10")
        os.makedirs(home)
        try:
            with open(real_json) as f:
                before = json.load(f)
        except (OSError, ValueError) as e:
            return self.step(10, "舊版會不會改壞 ~/.claude.json", True, "讀不到 ~/.claude.json（%s），跳過" % e)
        before.pop("oauthAccount", None)  # (who the owner is: not needed, not copied)
        before.pop("mcpServers", None)  # (the owner's MCP servers would be started by it: #code-review-2)
        before["customApiKeyResponses"] = {"approved": [KEY[-20:]], "rejected": []}  # (else it stops at the key
        # question and what it does to the file is not measured: #code-review-2)
        with open(os.path.join(home, ".claude.json"), "w") as f:
            json.dump(before, f)
        sock = "dcbot-test-drill10-%d" % os.getpid()
        env = dict(os.environ, HOME=home, DISABLE_AUTOUPDATER="1")
        subprocess.run(["tmux", "-L", sock, "new-session", "-d", "-s", "o", "-x", "120", "-y", "40", "-c", home, older],
                       env=env, timeout=30)
        time.sleep(8)
        view = subprocess.run(["tmux", "-L", sock, "capture-pane", "-p", "-t", "o"], env=env, capture_output=True, text=True,
                              timeout=10).stdout
        subprocess.run(["tmux", "-L", sock, "kill-server"], env=env, stderr=subprocess.DEVNULL, timeout=10)
        try:
            with open(os.path.join(home, ".claude.json")) as f:
                after = json.load(f)
        except (OSError, ValueError):
            after = {}
        lost = sorted(set(before) - set(after))
        error = "settings" in view.lower() and "error" in view.lower()
        self.step(10, "舊版會不會改壞 ~/.claude.json（%s）" % os.path.basename(older), not lost and not error,
                  "少了的鍵：%s%s" % ("、".join(lost) or "沒有", "；畫面有設定錯誤" if error else ""))


def step11_view(drill):
    """Step 11's screen: this machine's Claude Code (through the healthy wrapper) opened as a channel is, in a
    working folder it trusts, with a folder in a git repository it does not trust handed to it the way the bot
    hands an attachments folder (cc_launch.command: in the settings). (verdict, what it saw): True its input box
    came with no dialog before it, False a dialog came (2.1.326 asks about a folder given by --add-dir: a channel
    would wait on it), None neither within 30 seconds. A dialog is one of the questions Claude Code opens with (its
    two answers, "No, exit" and a Yes, or a choice: screen.startup_key()/startup_choice()), or that question's own
    first line; a screen still being drawn, with no box yet, is neither (pane_state() calls it "dialog")."""
    from altention.connectors.claude_code import launch as cc_launch, screen
    from . import cc_probe, tmux_bridge
    root = os.path.join(drill.world.root, "r11")
    repo, work = os.path.join(root, "repo"), os.path.join(root, "work")
    folder = os.path.join(repo, "attachments")
    os.makedirs(folder)
    os.makedirs(work)
    subprocess.run(["git", "init", "-q", repo], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    path = os.path.join(drill.world.home, ".claude.json")
    with open(path) as f:
        d = json.load(f)
    d.setdefault("projects", {})[work] = {"hasTrustDialogAccepted": True}  # (the channel's own folder: trusted)
    with open(path, "w") as f:
        json.dump(d, f)
    argv = cc_launch.command(drill.use("9.9.0", "ok"), "drill 11", cc_probe.MODEL, "auto",
                             settings=tmux_bridge.SETTINGS_FILE, dirs=[folder])
    sock = "dcbot-test-drill11-%d" % os.getpid()
    env = dict(os.environ)
    subprocess.run(["tmux", "-L", sock, "new-session", "-d", "-s", "o", "-x", "160", "-y", "45", "-c", work] + argv,
                   env=env, timeout=30)
    verdict, view = None, ""
    try:
        for _ in range(60):
            time.sleep(0.5)
            view = subprocess.run(["tmux", "-L", sock, "capture-pane", "-p", "-t", "o"], env=env, capture_output=True,
                                  text=True, timeout=10).stdout
            if screen.startup_key(view) or screen.startup_choice(view) or "Adding a new directory" in view:
                verdict = False
                break
            if screen.has_prompt(view):
                verdict = True
                break
    finally:
        subprocess.run(["tmux", "-L", sock, "kill-server"], env=env, stderr=subprocess.DEVNULL, timeout=10)
    rows = [r.strip() for r in view.split("\n") if r.strip()]
    if verdict:
        return verdict, "輸入框直接出來"
    return verdict, "%s：%s" % ("跳出對話框" if verdict is False else "30 秒內沒有輸入框，畫面是",
                               " / ".join(rows[-6:])[:300] or "（空的）")


def real_mode():
    """B (--real): as !cctest, in the real HOME, the real bot's health file (the bot's own probe folder); passing
    keeps the installed version as cc_last_good. A process of its own writing the running bot's cc_health.json:
    meant for a new install (#setup), where no pin is held; a pin it lets go does not mark the bot's windows to be
    reopened (its Bridge is a stand-in), the bot's own !cctest does (#code-review on f743a61)."""
    from . import cc_health, cc_probe, cc_watch, config, events, tmux_bridge, tmuxio
    os.environ.setdefault("DCBOT_CC_HEALTH", cc_health.path())  # the bot's own health file and kept copy, as !cctest
    scratch = tempfile.mkdtemp(prefix="ccr.")  # the probe's hook events go here, not to the running bot's spool
    config.ROOT = scratch                      # (its pump would take them: DCBOT_ROOT)
    tmux_bridge.SPOOL = os.path.join(scratch, "state", "events")
    tmux_bridge.SETTINGS_FILE = os.path.join(scratch, "claude-settings.json")
    tmuxio.configure(socket="dcbot-test-drillreal-%d" % os.getpid())
    tmux_bridge.write_settings()
    probe = cc_probe.Probe(tmux_bridge.SETTINGS_FILE, tmux_bridge.permission_of)
    cc_watch.ACTIVE = True
    w = cc_watch.Watch(Bridge(), probe=probe)

    async def go():
        stop = asyncio.Event()

        async def pump_real():  # (the bot's own pump takes them too when it runs: the probe sees what it gets)
            while not stop.is_set():
                for path, ev in events.waiting(tmux_bridge.SPOOL):
                    if ev.get("channel") == cc_probe.PROBE:
                        probe.event(ev)
                        events.done(path)
                await asyncio.sleep(0.2)
        t = asyncio.ensure_future(pump_real())

        async def say(text):
            print(text, flush=True)
        try:
            said = await w.cctest(say)
            print(said, flush=True)
            return said.startswith("✅")
        finally:
            stop.set()
            await t
            subprocess.run(["tmux", "-L", "dcbot-test-drillreal-%d" % os.getpid(), "kill-server"], stderr=subprocess.DEVNULL,
                           timeout=30)
            shutil.rmtree(scratch, ignore_errors=True)
    ok = asyncio.run(go())
    print("done")
    return 0 if ok else 1


def main(argv):
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    if "--real" in argv:
        return real_mode()
    real = real_claude()
    if not real or not shutil.which("tmux"):
        print("沒有 Claude Code 或 tmux，演練不了")
        return 2
    if leftover() == "running":  # (the last one still runs: neither cleaned nor a second one beside it)
        if "--json" in argv:
            print(json.dumps({"busy": True}))
        print("上一次的演練還在跑，這次不跑")
        print("done")
        return 3
    world = World(real)
    drill = Drill(world)
    import signal

    def ended(signum, frame):  # the bot ends a drill that runs too long with SIGTERM: the world is closed all the same
        raise SystemExit(1)
    signal.signal(signal.SIGTERM, ended)
    try:
        ok, _ = asyncio.run(drill.run())
    finally:
        world.close()
    if "--json" in argv:  # (for the bot's check of a new version: section 5.2)
        print(json.dumps(summary(drill.results, getattr(drill, "screen_ok", None), getattr(drill, "print_ok", None),
                                 os.path.basename(real))))
    print("done")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
