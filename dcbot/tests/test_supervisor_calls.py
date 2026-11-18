"""C-103: what the supervisor tells the owner itself, with a mention, while the bot may well be down: the bot that keeps
restarting (CRASH_COUNT times within CRASH_WINDOW, never up STABLE_SECS in between; told once for each time it
happens, and said over when it is up and stable), and a version that did not work, rolled back. Sent straight to
Discord (dcctl send @notify), never through the bot."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location("supervisor_calls_t", os.path.join(HERE, "supervisor.py"))
    sup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sup)
    return sup


class NothingCalledBot(unittest.TestCase):
    def test_the_supervisor_takes_nothing_called_bot_and_leaves_none(self):
        """#code-review-2 on 3c7ad8f: test_supervisor's worlds have a bot package of their own; the supervisor took it
        through `from bot import …` and left it in sys.modules, and the next test file got it."""
        import sys as sys_
        before = {k: v for k, v in sys_.modules.items() if k == "bot" or k.startswith("bot.")}
        sup = load()
        self.assertEqual(sup.said_words("restarting", "default"), "🛠️ 機器人一直重啟，沒辦法上線")
        self.assertTrue(sup.bot_module("envfile").__file__.startswith(HERE), "this checkout's own")
        after = {k: v for k, v in sys_.modules.items() if k == "bot" or k.startswith("bot.")}
        self.assertEqual(after, before)


class RestartLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sup = load()
        self.sup.STATE = self.tmp.name
        self.sent = []
        self.sup.ping = lambda text: self.sent.append(("ping", text)) or True
        self.sup.say_quietly = lambda text: self.sent.append(("quiet", text)) or True
        self.sup.this_machine = lambda: "box"
        self.sup.log = lambda msg: None

    def test_three_in_the_window_once_then_over_then_again(self):
        sup, crashes, t = self.sup, [], 1000.0
        for i in range(2):
            crashes.append(t + i * 60)
            self.assertFalse(sup.restart_loop(crashes, now=t + i * 60))
        crashes.append(t + 120)
        self.assertTrue(sup.restart_loop(crashes, now=t + 120))
        (kind, text), = self.sent
        self.assertEqual(kind, "ping")
        self.assertEqual(text.split("\n"), ["🛠️ 機器人一直重啟，沒辦法上線",
                                            "原因在 box 的 %s" % os.path.join(self.tmp.name, "supervisor.out")])
        crashes.append(t + 300)
        self.assertFalse(sup.restart_loop(crashes, now=t + 300), "the same time it happens: told once")
        self.assertTrue(sup.restart_over())
        self.assertEqual(self.sent[-1], ("quiet", "✅ 機器人恢復了"))
        self.assertFalse(sup.restart_over(), "over once")
        crashes[:] = []
        for i in range(3):  # the same version keeps restarting again, later: told again (manager 2026-11-03)
            crashes.append(t + 5000 + i)
            sup.restart_loop(crashes, now=t + 5000 + i)
        self.assertEqual([k for k, _ in self.sent], ["ping", "quiet", "ping"])

    def test_spread_out_is_no_loop(self):
        crashes = []
        for i in range(5):
            crashes.append(1000.0 + i * (self.sup.CRASH_WINDOW / 2 + 1))
            self.sup.restart_loop(crashes, now=crashes[-1])
        self.assertEqual(self.sent, [], "never three within the window")

    def test_what_the_words_are_when_the_bots_words_cannot_be_read(self):
        with mock.patch.object(self.sup, "bot_module", side_effect=ImportError("a broken bot/")):
            self.assertEqual(self.sup.said_words("restarting", "🛠️ default {x}", x=1), "🛠️ default 1")


class Ping(unittest.TestCase):
    """ping() itself: the owner's mention after the first line, dcctl send @notify --mentions."""

    def test_the_command_and_the_mention(self):
        sup = load()
        ran = []

        def run(cmd, **kw):
            ran.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        envfile = mock.Mock(load=lambda *a, **k: {"OWNER_ID": "42"})
        with mock.patch.object(sup.subprocess, "run", run), mock.patch.object(sup, "bot_module", lambda name: envfile):
            self.assertTrue(sup.ping("🛠️ 機器人一直重啟，沒辦法上線\n原因在 box 的 /x"))
        cmd, = ran
        self.assertEqual(cmd[2:4], ["send", "@notify"])
        self.assertEqual(cmd[4], "🛠️ 機器人一直重啟，沒辦法上線 <@42>\n原因在 box 的 /x", "purpose first, the mention ends the first line")
        self.assertEqual(cmd[5], "--mentions", "or dcctl send pings nobody (C-89)")


class WhileTheBotIsDown(unittest.TestCase):
    """The rig the manager asked for: no bot running at all; a scratch HOME with the bot's settings where C-110 keeps
    them (a made-up token and owner); the real supervisor.ping() runs the real dcctl, whose request to Discord is
    caught before it leaves (sitecustomize stands in front of urllib). What it would send, and where from."""

    def test_the_supervisor_sends_without_the_bot(self):
        with tempfile.TemporaryDirectory() as home:
            store = os.path.join(home, ".config", "altention", "secrets", "core")
            os.makedirs(store)
            for name, value in (("discord-token", "rig-token-not-real"), ("owner-id", "42"), ("guild-id", "7")):
                with open(os.path.join(store, name), "w") as f:
                    f.write(value + "\n")
                os.chmod(os.path.join(store, name), 0o600)
            stub = os.path.join(home, "stub")
            os.makedirs(stub)
            caught = os.path.join(home, "caught.jsonl")
            with open(os.path.join(stub, "sitecustomize.py"), "w") as f:
                f.write('''import io, json, os, urllib.request
def urlopen(req, *a, **k):
    with open(%r, "a") as f:
        f.write(json.dumps({"url": req.full_url, "method": req.get_method(), "auth": req.headers.get("Authorization"),
                            "body": json.loads(req.data.decode()) if req.data else None}) + "\\n")
    url = req.full_url
    if url.endswith("/channels"):
        body = [{"id": "900", "name": "notifications", "type": 0, "position": 0}]
    else:
        body = {"id": "901"}
    class R(io.BytesIO):
        status = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *e): return False
        def getcode(self): return 200
    return R(json.dumps(body).encode())
urllib.request.urlopen = urlopen
''' % caught)
            code = ("import importlib.util, sys; spec = importlib.util.spec_from_file_location('s', %r); s = importlib.util.module_from_spec(spec); "
                    "spec.loader.exec_module(s); print(s.ping('🛠️ 機器人一直重啟，沒辦法上線'))" % os.path.join(HERE, "supervisor.py"))
            env = {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": stub, "ALT_INSTANCE": "-",
                   "LANG": "C.UTF-8", "DCBOT_LEDGER": os.path.join(home, "state", "ledger.json")}  # (C-172a P1b batch 4:
            # dcctl send leaves its message for the bot's ledger next to it, never in this checkout's state/)
            r = subprocess.run([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=60, cwd=home)
            self.assertEqual(r.stdout.strip().splitlines()[-1:], ["True"], r.stdout + r.stderr)
            with open(caught) as f:
                reqs = [json.loads(line) for line in f]
            (left,) = os.listdir(os.path.join(home, "state", "ledger-pending"))
            with open(os.path.join(home, "state", "ledger-pending", left)) as f:
                self.assertEqual({k: v for k, v in json.load(f).items() if k in ("channel", "message", "intent")},
                                 {"channel": "900", "message": "901", "intent": "post"}, "for the ledger once the bot is back")
        post = [q for q in reqs if q["method"] == "POST"]
        self.assertEqual(len(post), 1, reqs)
        self.assertTrue(post[0]["url"].endswith("/channels/900/messages"), "the notification channel, by its name")
        self.assertEqual(post[0]["auth"], "Bot rig-token-not-real", "the token from where C-110 keeps it")
        self.assertEqual(post[0]["body"]["content"], "🛠️ 機器人一直重啟，沒辦法上線 <@42>")
        self.assertEqual(post[0]["body"]["allowed_mentions"], {"parse": [], "users": ["42"]}, "--mentions: the owner alone is pinged")


if __name__ == "__main__":
    unittest.main()
