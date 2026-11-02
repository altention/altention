"""C-71b: a plugin rewrites Claude's final reply before it is posted (bot/rewrite.py): which plugin, how it is run
and read, and on the reply: in a task of its own, the channel's events waiting behind it, the old way when it fails."""
import asyncio
import os
import re
import shlex
import sys
import tempfile
import time
import unittest
from unittest import mock

from bot import ask, config, rewrite, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_turn_end import Host, Msg

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples", "rewriter", "fake.py")
TABLE = "看這個：\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"


def spec(mode, *more, timeout=10, when=None):
    command = " ".join([shlex.quote(sys.executable), shlex.quote(FAKE), mode] + list(more) + ["-o", "{out}"])
    return {"plugin": "fake", "command": command, "timeout": timeout, "when": when, "others": 0}


class State(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rewrite state ")
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(config, "STATE", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)


class Choose(unittest.TestCase):
    def setUp(self):
        self.saved = dict(rewrite.chosen)
        self.addCleanup(lambda: rewrite.chosen.update(self.saved))

    def test_the_lowest_order_among_those_on_here(self):
        r = {"command": "{python} \"{plugin}/bin/x\" - -o {out}", "timeout": 20, "when": "^\\|"}
        rows = [("late", "/p/late", {"order": 90, "rewrite": r}, "on", ""),
                ("draft", "/p/draft", {"order": 1, "rewrite": r}, "draft", ""),
                ("off", "/p/off", {"order": 2, "rewrite": r}, "disabled", ""),
                ("there", "/p/there", {"order": 3, "rewrite": r}, "off", ""),
                ("early", "/p/early", {"order": 10, "rewrite": r}, "on", ""),
                ("plain", "/p/plain", {"order": 0}, "on", "")]
        got = rewrite.choose(rows)
        self.assertEqual((got["plugin"], got["timeout"], got["others"]), ("early", 20, 1))
        self.assertTrue(got["command"].endswith('"/p/early/bin/x" - -o {out}'), "{plugin} filled in, {out} kept for each reply")
        self.assertTrue(got["when"].search("text\n| a | b |"), "MULTILINE")
        self.assertIs(rewrite.wanted("no table here"), None)
        self.assertIs(rewrite.wanted("| a |"), got)

    def test_none_and_bad_values(self):
        self.assertIsNone(rewrite.choose([("x", "/p/x", {"order": 1}, "on", "")]))
        self.assertIsNone(rewrite.wanted("anything"))
        got = rewrite.choose([("x", "/p/x", {"rewrite": {"command": "c {out}", "timeout": 999}}, "on", "")])
        self.assertEqual((got["timeout"], got["when"]), (rewrite.TIMEOUT, None), "a timeout past the limit: the default")
        self.assertIs(rewrite.wanted("anything"), got, "no when: every reply")


class Run(State):
    def run_(self, s, text=TABLE):
        return asyncio.run(rewrite.run(s, text))

    def test_messages_and_their_pictures(self):
        msgs, why, token = self.run_(spec("ok"))
        self.assertIsNone(why)
        self.assertEqual([t for t, _ in msgs], ["前面的話", "", "後面的話"])
        self.assertEqual([len(p) for _, p in msgs], [1, 1, 0])
        self.assertTrue(all(os.path.basename(p).startswith(token + "-") for _, ps in msgs for p in ps))
        rewrite.drop(token)
        self.assertEqual(os.listdir(rewrite.out_dir()), [], "dropped once posted")

    def test_what_is_not_used(self):
        for mode, why in (("fail", "結束碼 2：這台沒有 Chrome"), ("junk", "輸出不是 JSON"), ("elsewhere", "圖片不在機器人給的地方")):
            msgs, said, _ = self.run_(spec(mode))
            self.assertIsNone(msgs, mode)
            self.assertIn(why, said, mode)
        msgs, _, _ = self.run_(spec("same"))
        self.assertTrue(rewrite.unchanged(msgs), "one message and no picture: nothing drawn")

    def test_past_its_time_the_whole_group_is_killed(self):
        t0 = time.time()
        msgs, why, token = self.run_(spec("hang", timeout=1))
        self.assertLess(time.time() - t0, 5)
        self.assertEqual((msgs, why), (None, "1 秒沒有結束"))
        with open(os.path.join(rewrite.out_dir(), token + ".png.child")) as f:
            child = int(f.read())
        for _ in range(50):
            try:
                os.kill(child, 0)
            except OSError:
                break
            time.sleep(0.05)
        else:
            self.fail("the plugin's child is still running")

    def test_out_quoted_or_not(self):
        path = "/tmp/a b/x.png"
        for command in ('d -o {out}', 'd -o "{out}"', "d -o '{out}'"):
            self.assertEqual(shlex.split(rewrite.fill_out(command, path)), ["d", "-o", path], command)
        quoted = dict(spec("ok"), command=spec("ok")["command"].replace("{out}", '"{out}"'))
        msgs, why, _ = asyncio.run(rewrite.run(quoted, TABLE))
        self.assertIsNone(why, "a state folder with a space, {out} quoted by the plugin (a drawing plugin's own way)")

    def test_old_pictures_are_pruned(self):
        os.makedirs(rewrite.out_dir())
        old = os.path.join(rewrite.out_dir(), "x-1.png")
        open(old, "w").close()
        os.utime(old, (1, 1))
        rewrite.prune()
        self.assertEqual(os.listdir(rewrite.out_dir()), [])


class Ch(object):
    id, name = 5, "c"

    def __init__(self):
        self.sent, self.deleted = [], []

    async def send(self, content=None, view=None, silent=False, files=None):
        m = Msg(len(self.sent) + 1, content, view, silent)
        m.files = [os.path.basename(f.filename) for f in files or []]
        ch = self

        async def delete():
            ch.deleted.append(m.id)
        m.delete = delete
        self.sent.append(m)
        return m

    async def typing(self):
        pass


class OnTheReply(State):
    def setUp(self):
        super().setUp()
        self.saved = (ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM, dict(rewrite.chosen))
        ask.ASKS, ask.ANSWERS = os.path.join(self.tmp.name, "asks"), os.path.join(self.tmp.name, "answers")
        config.OWNER_ID, config.INTERIM = 42, 0
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))

    def tearDown(self):
        ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM, chosen = self.saved
        rewrite.chosen.clear()
        rewrite.chosen.update(chosen)

    def ev(self, kind, **kw):
        return dict({"channel": "5", "hook_event_name": kind, "session_id": "abc"}, **kw)

    async def turn(self, reply):
        await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
        await self.bridge.handle(self.ev("Stop", last_assistant_message=reply))

    async def settle(self):
        for _ in range(200):
            if not self.bridge.rewriting:
                return
            await asyncio.sleep(0.02)
        self.fail("the rewrite never ended")

    def test_laid_out_in_order_with_the_footer_on_the_last(self):
        rewrite.chosen["spec"] = spec("ok", when=re.compile(r"^\|", re.M))

        async def go():
            await self.turn(TABLE)
            self.assertIn("5", self.bridge.rewriting)
            self.assertIn("rewriting", self.bridge.host.chan(self.ch))
            self.assertTrue(self.bridge.waits({"channel": "5", "hook_event_name": "UserPromptSubmit"}), "this machine's wait")
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="next", host="lap"))
            self.assertEqual(len(self.bridge.kept["5"][1]), 1, "another machine's is kept, in order")
            await self.settle()
            await asyncio.sleep(0.05)
        asyncio.run(go())
        posted = [(m.content, m.files) for m in self.ch.sent if not (m.content or "").startswith("⌨️")]
        self.assertEqual(posted[0][0], "前面的話")
        self.assertEqual(len(posted[0][1]), 1)
        self.assertEqual((posted[1][0], len(posted[1][1])), (None, 1), "a picture alone")
        self.assertTrue(posted[2][0].startswith("後面的話\n-# "), "the footer on the last")
        self.assertNotIn("rewriting", self.bridge.host.chan(self.ch))
        self.assertNotIn("5", self.bridge.kept, "the kept event handled once the reply was up")
        self.assertEqual(os.listdir(rewrite.out_dir()), [], "its pictures dropped")

    def test_no_match_no_plugin_failing_or_nothing_drawn_is_the_old_way(self):
        for s in (spec("ok", when=re.compile(r"^\$\$", re.M)), None, spec("fail"), spec("same")):
            self.ch.sent.clear()
            rewrite.chosen["spec"] = s

            async def go():
                await self.turn(TABLE)
                await self.settle()
            asyncio.run(go())
            reply = self.ch.sent[-1].content
            self.assertTrue(reply.startswith("看這個：\n\n```\na  b\n-  -\n1  2\n```"), (s and s["command"], reply))

    def test_a_slow_one_says_it_is_being_laid_out(self):
        rewrite.chosen["spec"] = spec("slow", "0.6")

        async def go():
            await self.turn(TABLE)
            await asyncio.sleep(0.3)
            job = self.bridge.host.chan(self.ch)["rewriting"]
            self.assertIsInstance(job.get("pgid"), int, "its process group kept with the reply, for a restart")
            await self.settle()
        with mock.patch.object(rewrite, "SHOW_AFTER", 0.2):
            asyncio.run(go())
        note = next(m for m in self.ch.sent if m.content == "-# ⏳ 整理排版中…")
        self.assertTrue(note.silent)
        self.assertEqual(self.ch.deleted, [note.id], "gone once the reply is up")

    def test_the_receipt_of_the_next_message_is_not_held_and_the_owners_words_are_not_taken_for_the_terminals(self):
        """#code-review-2 on f2ee5d3: Q2 typed while the reply was laid out; its receipt waited in the spool, submit
        said "maybe not received", and the late receipt was posted as "⌨️ 終端機那邊輸入". The receipt is handled at
        once (this machine's spool and another's stream alike); input the bot did not type still waits."""
        import json
        from bot import events as bot_events
        rewrite.chosen["spec"] = spec("slow", "0.8")
        spool = tempfile.mkdtemp(dir=self.tmp.name)

        async def go():
            await self.turn(TABLE)
            self.assertIn("5", self.bridge.rewriting)
            rt = self.bridge.rt(self.ch)
            p = tmux_bridge.Pending(type("M", (), {"channel": self.ch, "add_reaction": staticmethod(lambda e: asyncio.sleep(0))})(), "Q2")
            rt.pending.append(p)
            terminal = self.ev("UserPromptSubmit", prompt="typed at the terminal")
            cc = tmux_bridge.cc_hooks
            for ev in (terminal,):
                cc.normalize(ev)
                self.assertTrue(self.bridge.waits(ev), "input the bot did not type waits (it would post ⌨️)")
            with open(os.path.join(spool, "01.json"), "w") as f:
                json.dump(self.ev("UserPromptSubmit", prompt="Q2"), f)
            with mock.patch.object(tmux_bridge, "SPOOL", spool):
                self.bridge.last_reap = self.bridge.streams_checked = 1e18
                await self.bridge.pump_once()
            seen = {"local": p.got.is_set(), "spool": os.listdir(spool), "going": "5" in self.bridge.rewriting}
            other = self.ev("UserPromptSubmit", prompt="Q3", host="lap")
            p3 = tmux_bridge.Pending(p.message, "Q3")
            rt.pending.append(p3)
            await self.bridge.handle(other)
            seen["far"] = p3.got.is_set()
            await self.settle()  # (asserted after the layout is over: a failure must not leave it running)
            return seen
        seen = asyncio.run(go())
        self.assertTrue(seen["local"], "received within RECEIPT_SECS, not after the layout")
        self.assertEqual(seen["spool"], [])
        self.assertTrue(seen["going"], "the layout goes on")
        self.assertTrue(seen["far"], "another machine's receipt too")
        said = [m.content or "" for m in self.ch.sent]
        self.assertFalse([t for t in said if "終端機那邊輸入" in t and ("Q2" in t or "Q3" in t)], said)
        self.assertFalse([t for t in said if "可能沒有送進" in t], said)
        reply = [t for t in said if t.startswith("後面的話")]
        self.assertEqual(len(reply), 1)
        self.assertIn("-# ", reply[0], "the first turn's reply, its footer, as laid out: the next turn took nothing of it")

    def test_the_next_turns_tool_calls_held_meanwhile_post_no_working_message(self):
        """#code-review-2 on f0f950b: the next turn ran its tools while the reply was laid out; once posted, its held
        tool calls came at once and posted "處理中…（29 秒）", deleted again by its Stop right behind them."""
        rewrite.chosen["spec"] = spec("slow", "0.4", when=re.compile(r"^\|", re.M))  # (REPLY-2 goes as it is)

        async def go():
            await self.turn(TABLE)
            rt = self.bridge.rt(self.ch)
            p = tmux_bridge.Pending(type("M", (), {"channel": self.ch, "add_reaction": staticmethod(lambda e: asyncio.sleep(0))})(), "Q2")
            rt.pending.append(p)
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="Q2", host="lap"))
            rt.turn.t0 -= 30  # (it has run for a while by the time the reply is up)
            for _ in range(3):
                await self.bridge.handle(self.ev("PreToolUse", tool_name="Bash", tool_input={"command": "ls"}, host="lap"))
            await self.bridge.handle(self.ev("Stop", last_assistant_message="REPLY-2", host="lap"))
            self.assertEqual(len(self.bridge.kept["5"][1]), 4)
            await self.settle()
            for _ in range(100):
                if "5" not in self.bridge.kept:
                    break
                await asyncio.sleep(0.02)
        asyncio.run(go())
        said = [m.content or "" for m in self.ch.sent]
        self.assertFalse([t for t in said if t.startswith("處理中")], said)
        self.assertTrue(said[-1].startswith("REPLY-2\n-# "), said)

    def test_a_reply_being_laid_out_when_the_bot_went_down_is_posted_at_the_start(self):
        st = self.bridge.host.chan(self.ch)
        st["rewriting"] = {"reply": TABLE, "foot": "-# 1 秒", "final": TABLE, "src": None, "prompt": "go"}
        asyncio.run(self.bridge.posted_left())
        self.assertTrue(self.ch.sent[-1].content.startswith("看這個："))
        self.assertTrue(self.ch.sent[-1].content.endswith("-# 1 秒"))
        self.assertNotIn("rewriting", st)


if __name__ == "__main__":
    unittest.main()


class LeftRunning(unittest.TestCase):
    """The plugin's process group, when the bot died in the middle: stopped at the start, only when it is plainly
    that one (its leader's start time as recorded)."""
    def test_killed_only_when_it_is_the_one_recorded(self):
        import subprocess
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        start = rewrite.started_at(proc.pid)
        if start is None:
            self.skipTest("no /proc here")
        self.assertFalse(rewrite.stop_left(proc.pid, start + 1), "another process since: left alone")
        self.assertFalse(rewrite.stop_left(None, start))
        self.assertFalse(rewrite.stop_left(proc.pid, None))
        self.assertIsNone(proc.poll())
        self.assertTrue(rewrite.stop_left(proc.pid, start))
        self.assertEqual(proc.wait(timeout=5), -9)

    def test_posted_left_stops_it_first(self):
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        st = bridge.host.chan(ch)
        st["rewriting"] = {"reply": "x", "foot": None, "final": "x", "src": None, "prompt": "go", "pgid": 4242, "pgid_start": 7}
        stopped = []
        with mock.patch.object(rewrite, "stop_left", lambda pgid, start: stopped.append((pgid, start))):
            asyncio.run(bridge.posted_left())
        self.assertEqual(stopped, [(4242, 7)])
        self.assertEqual(ch.sent[-1].content, "x")
