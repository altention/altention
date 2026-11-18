"""The probe (C-162, design section 2.1 and 5.3): is a Claude Code version itself broken, or was it the channels?

A fresh conversation, opened exactly as a channel's is (the same file, settings and hooks, the installation's
permission mode, the same startup-dialog answers), in a fixed empty folder of the bot's state, on the bot's own tmux
server, with Haiku. Four steps:
  1 it comes up and shows its prompt box;
  2 one message, and its Stop hook;
  3 the prompt box again after the reply (a common break: it opens, and after one turn the box is not one the bot
    knows);
  4 closed, opened again with --resume, asked what it was told: it says it (--resume broken otherwise).
Its hook events carry DCBOT_CHANNEL=PROBE: the pump hands them here and nowhere else (not posted, not in #audit).
ALTENTION_BACKGROUND=1 keeps chatlog (and progress) away; ALT_PROBE=1 keeps `dchook --global` away.
The real HOME and the one login are used, as every channel does (section 5.3: never a second copy of the login).
The session it made is removed by its own transcript path afterwards; the folder is emptied before each run.
"""
import asyncio
import json
import os
import secrets
import shutil
import time

from altention import background
from altention.connectors.claude_code import launch as cc_launch
from altention.connectors.claude_code import screen
from altention.connectors.claude_code import sessions as cc_sessions

from . import cc_health, config, tmuxio

PROBE = "probe"            # the DCBOT_CHANNEL of its events
SESSION = "cc-probe"       # its tmux session, on the bot's own server
MODEL = "haiku"
START_SECS = 90
REPLY_SECS = 180
STEPS = {1: "開不起來，或畫面上找不到輸入框", 2: "送出的話沒有得到回覆", 3: "回覆之後畫面上找不到輸入框",
         4: "接回剛才的對話之後，答不出剛才說過的話"}  # (said to the owner: plain words, #release 11-07)


def folder():
    return os.path.join(config.STATE, "cc-probe")


def attachments():
    """The folder the probe's conversation reads without asking, as a channel reads its attachments folder."""
    return os.path.join(folder(), "attachments")


class Probe(object):
    def __init__(self, settings_file, permission_of):
        self.settings_file, self.permission_of = settings_file, permission_of
        self.lock = asyncio.Lock()
        self.session_id = self.transcript = None
        self.stopped = None
        self.said = ""

    def event(self, ev):
        """An event of the probe's conversation (the pump's, channel PROBE)."""
        if ev.get("session_id"):
            self.session_id = ev["session_id"]
        if ev.get("transcript_path"):
            self.transcript = ev["transcript_path"]
        if ev.get("hook_event_name") == "Stop" and self.stopped is not None:
            self.said = ev.get("last_assistant_message") or ""
            self.stopped.set()

    def env(self):
        return background.env(dict(tmuxio_env(), DCBOT_ROOT=config.ROOT, DCBOT_CHANNEL=PROBE, ALT_PROBE="1",
                                   DISABLE_AUTOUPDATER="1"))  # (an older version probed must not install the new one)

    async def opened(self, binary, permission, resume):
        await tmuxio.kill(SESSION)
        os.makedirs(attachments(), exist_ok=True)  # a folder handed as a channel's attachments folder is (in the
        # engine's repository, as theirs: a question about trusting it fails the probe as it would stop a channel)
        argv = cc_launch.command(binary, "這是 altention 機器人的自動檢查，不是使用者的對話。", MODEL, permission,
                                 settings=self.settings_file, resume=resume, dirs=[attachments()])
        if not await tmuxio.start(SESSION, folder(), argv, self.env()):
            return "tmux 開不起視窗"
        t0 = time.time()
        while time.time() - t0 < START_SECS:
            end = await tmuxio.ended(SESSION)
            if end:
                return "Claude Code 一開就結束了（%s）" % (("結束碼 %s" % end["status"]) if end["status"] else
                                                       ("訊號 %s" % end["signal"]) if end["signal"] else "原因不明")
            view = await tmuxio.raw_screen(SESSION)
            key = screen.startup_key(view)
            if key:
                await tmuxio.keys(SESSION, key)
            elif screen.has_prompt(view):
                return None
            await asyncio.sleep(0.5)
        return "等了 %d 秒，畫面上一直找不到輸入框" % START_SECS

    async def ask(self, text):
        self.stopped, self.said = asyncio.Event(), ""
        await tmuxio.type_text(SESSION, text)
        await asyncio.sleep(0.3)
        await tmuxio.keys(SESSION, "Enter")
        try:
            await asyncio.wait_for(self.stopped.wait(), REPLY_SECS)
        except asyncio.TimeoutError:
            return False
        return True

    async def screen_now(self):
        try:
            return await tmuxio.raw_screen(SESSION)
        except Exception:
            return ""

    async def run(self, binary):
        """Probe the Claude Code file `binary`: {"ok": bool, "step": 1-4 (the one that failed), "why": words,
        "screen": what the pane showed then}; {"ok": None, "why": …} when it could not be run at all (no permission
        mode set: nothing is opened without one, C-121)."""
        async with self.lock:
            try:
                permission = self.permission_of({})
            except Exception as e:
                return {"ok": None, "why": str(e)}
            shutil.rmtree(folder(), ignore_errors=True)
            os.makedirs(folder(), exist_ok=True)
            self.session_id = self.transcript = None
            cc_health.note_probe_session(SESSION)
            word = "w" + secrets.token_hex(3)
            try:
                return await self.steps(binary, permission, word)
            finally:
                await tmuxio.kill(SESSION)
                self.forget_session()
                cc_health.note_probe_session(None)

    async def steps(self, binary, permission, word):
        def failed(step, why, view=""):
            return {"ok": False, "step": step, "why": why or STEPS[step], "screen": view}
        why = await self.opened(binary, permission, None)
        if why:
            return failed(1, why, await self.screen_now())
        if not await self.ask("[altention 探測，可忽略] 請只回 ok，並記住這個字：%s" % word):
            return failed(2, None, await self.screen_now())
        await asyncio.sleep(1)
        view = await self.screen_now()
        if not screen.has_prompt(view):
            return failed(3, None, view)
        if not self.session_id:
            return failed(4, "不知道剛才那個對話的編號")
        why = await self.opened(binary, permission, self.session_id)
        if why:
            return failed(4, "接回剛才的對話時" + why, await self.screen_now())
        if not await self.ask("[altention 探測，可忽略] 剛才要你記住的字是什麼？只回那個字。") or word not in self.said:
            return failed(4, None, await self.screen_now())
        return {"ok": True}

    async def print_check(self, binary):
        """stream-json on its own (C-162, manager 11-07): one `claude -p --output-format stream-json` turn in the
        probe's folder, as a print channel's is (the same settings, the installation's permission mode, Haiku).
        {"ok": True} when a result event came that is not an error; {"ok": False, "why": …}; {"ok": None, …} when
        it could not be run (no permission mode)."""
        async with self.lock:
            try:
                permission = self.permission_of({})
            except Exception as e:
                return {"ok": None, "why": str(e)}
            os.makedirs(folder(), exist_ok=True)
            self.transcript = None
            cmd = [binary, "-p", "--model", MODEL, "--output-format", "stream-json", "--verbose",
                   "--permission-mode", permission, "--settings", self.settings_file]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    cwd=folder(), env=dict(os.environ, **self.env()))  # (this process's environment, and the probe's marks)
                out, err = await asyncio.wait_for(proc.communicate("[altention 探測，可忽略] 請只回 ok".encode()), REPLY_SECS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {"ok": False, "why": "等了 %d 秒沒有回覆" % REPLY_SECS}
            except OSError as e:
                return {"ok": False, "why": "跑不起來（%s）" % e}
            finally:
                for _ in range(20):  # its hooks' events name its conversation file (the pump hands them here)
                    if self.transcript:
                        break
                    await asyncio.sleep(0.2)
                self.forget_session()
            for line in out.decode("utf-8", "replace").splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("type") == "result" and not ev.get("is_error"):
                    return {"ok": True}
            why = cc_health.tail(err.decode("utf-8", "replace"), 3) or "沒有拿到結果（結束碼 %s）" % proc.returncode
            return {"ok": False, "why": why}

    async def text_check(self, binary):
        """`claude -p --output-format text` gives words (layer 3; !cctest only)."""
        async with self.lock:
            try:
                permission = self.permission_of({})
            except Exception as e:
                return {"ok": None, "why": str(e)}
            os.makedirs(folder(), exist_ok=True)
            self.transcript = None
            cmd = [binary, "-p", "--model", MODEL, "--output-format", "text", "--permission-mode", permission,
                   "--settings", self.settings_file]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    cwd=folder(), env=dict(os.environ, **self.env()))  # (this process's environment, and the probe's marks)
                out, err = await asyncio.wait_for(proc.communicate("[altention 探測，可忽略] 請只回 ok".encode()), REPLY_SECS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {"ok": False, "why": "等了 %d 秒沒有回覆" % REPLY_SECS}
            except OSError as e:
                return {"ok": False, "why": "跑不起來（%s）" % e}
            finally:
                for _ in range(20):
                    if self.transcript:
                        break
                    await asyncio.sleep(0.2)
                self.forget_session()
            if proc.returncode == 0 and out.strip():
                return {"ok": True}
            return {"ok": False, "why": cc_health.tail(err.decode("utf-8", "replace"), 3) or "沒有輸出（結束碼 %s）" % proc.returncode}

    def forget_session(self):
        """Remove the probe's conversation file: only the one its own events named, and only where Claude Code
        keeps its conversations."""
        path = self.transcript
        if not path or not path.endswith(".jsonl"):
            return
        projects = os.path.realpath(os.path.join(cc_sessions.config_dir(), "projects"))
        real = os.path.realpath(path)
        if real.startswith(projects + os.sep):
            try:
                os.remove(real)
            except OSError:
                pass


def tmuxio_env():
    from .tmux_bridge import RESUME_ENV  # (the bridge imports this module's users: no import at the top)
    env = dict(RESUME_ENV)
    if not (os.environ.get("LC_ALL") or os.environ.get("LANG")):
        env["LANG"] = "C.UTF-8"
    return env
