"""One whole turn, end to end, on both roads an event takes (the safety net stage 3b asked for, 2026-10-11).

A message is submitted; a stand-in for Claude Code takes it from the fake pane, writes its conversation file in the
shape the real one has (tests/transcript.py) and fires its hooks by running the real tools/dchook; the events come
back through the spool and pump_once() (this machine), or through tools/dcremote's relay() and stream() (another
machine); and what reaches the channel is checked: the receipt, what Claude says along the way, the reply with its
footer, where the turn lies in the file for 📋.

Stage 3b moves the reading of the conversation file out of handle() into one step both roads share. These tests say
what must come out the same; they name no function that will move, only submit(), pump_once() and stream().
"""
import asyncio
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import transcript
from bot import ask, config, process, remote, tmux_bridge
from tests import fakepane
from tests.test_turn_end import Ch, Host
from tests.transcript import MODEL, TOKENS, Transcript

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = importlib.machinery.SourceFileLoader("dcremote_whole_turn", os.path.join(ROOT, "tools", "dcremote"))
dcremote = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcremote_whole_turn", _loader))
_loader.exec_module(dcremote)

A, B, C = "我先看看有哪些檔。", "有兩個檔，接著讀第一個。", "收到補充，連日期一起改。"
FOOT = re.compile(r"^-# (.+) · \d+ 秒( · 背景工作 (\d+) 個進行中)? · context 70%$")
real_sleep = asyncio.sleep


async def nap(seconds, result=None):
    """Every wait in the bridge, shortened: the order of things stays, the seconds do not."""
    await real_sleep(min(seconds, 0.02))
    return result


class Message(object):
    def __init__(self, ch, text, mid):
        self.channel, self.content, self.id, self.reactions = ch, text, mid, []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)


class TheHost(Host):
    def note(self, ch, st):
        return "you are in #%s" % ch.name


class Claude(fakepane.Screens):
    """Claude Code in the fake pane: what is typed shows in its input box; Enter takes it as a prompt; the test
    then has it say things, use tools and stop.

    In the order measured on the real one (2.1.296, #code-review-2 2026-10-12): **the hook is called first, the
    line it is about is written to the conversation file about a tenth of a second later**. UserPromptSubmit
    comes before the prompt's "user" line; PreToolUse before the text said ahead of the tool and the tool_use
    line; Stop before the last reply's line. A message sent while a turn runs fires UserPromptSubmit at once,
    with the prompt_id of the turn that is running, and only an enqueue line is written.

    The bot looks at its events every half second, so it mostly gets to an event after its lines are written,
    sometimes before. Both are played: by default the lines are written as soon as the hook has returned (before
    the bot can have handled the event); with `hold` they stay unwritten until write_now() (the test first lets
    the bot handle the event)."""

    def __init__(self, world):
        fakepane.Screens.__init__(self, [fakepane.box("❯ ")], echo=True)
        self.world, self.running, self.busy, self.started = world, False, False, []
        self.script = Transcript(world.transcript_path, cwd=world.tmp)
        self.turn_id, self.said, self.unwritten, self.hold = None, [], [], False

    # ---- what the bridge sees and does
    async def alive(self, t):
        return self.running

    async def start(self, name, cwd, argv, env=None):
        self.started.append((name, cwd, list(argv), dict(env or {})))
        self.running = True
        self.script.opened()
        self.hook("SessionStart", source="startup")
        return True

    async def kill(self, t):
        self.running = False

    async def raw_screen(self, name):
        box = "❯ " + (self.typed_now or "")
        under = "  esc to interrupt" if self.busy and not self.typed_now else "  ? for shortcuts"
        self.last = "\n".join((["", "● working…", ""] if self.busy else [""]) + [fakepane.RULE, box, fakepane.RULE, under, ""])
        return self.last

    async def keys(self, name, *names):
        self.log.extend(("key", n) for n in names)
        if "Enter" in names and self.typed_now is not None:
            text, self.typed_now = self.typed_now, None
            if self.busy:  # waits in the queue: the hook fires at once, with the running turn's id
                self.hook("UserPromptSubmit", prompt=text, prompt_id=self.turn_id)
                self.later(lambda: self.script.queued(text))
            else:
                self.turn_id, self.busy = "prompt-%d" % (len(self.log) + 1), True
                turn = self.turn_id
                self.hook("UserPromptSubmit", prompt=text, prompt_id=turn)
                self.later(lambda: self.script.prompt(text, turn))

    # ---- the order of hook and file
    def hook(self, kind, **more):
        payload = dict({"session_id": self.script.session, "transcript_path": self.script.path, "cwd": self.world.tmp,
                        "permission_mode": "bypassPermissions", "hook_event_name": kind}, **more)
        env = self.world.hook_env()
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "dchook")], input=json.dumps(payload).encode(),
                       env=env, check=True)
        self.world.emitted += 1

    def later(self, write):
        """A line that Claude Code writes after the hook it belongs to: now (the bot has not handled the event
        yet), or with `hold` only at write_now()."""
        self.unwritten.append(write)
        if not self.hold:
            self.write_now()

    def write_now(self):
        todo, self.unwritten = self.unwritten, []
        for write in todo:
            write()

    # ---- what the test has it do
    def say(self, text):
        """Words said ahead of the next tool call: in the file when that call's lines are."""
        self.said.append(text)

    def tool(self, name, tool_input, result="ok"):
        tid = "toolu_%04d" % (self.world.emitted + 1)
        said, self.said = self.said, []
        turn = self.turn_id
        self.hook("PreToolUse", tool_name=name, tool_input=tool_input, tool_use_id=tid, prompt_id=turn)

        def lines():
            self.script.think()
            for text in said:
                self.script.say(text)
            self.script.tool(name, tool_input, tid)
            self.script.result(tid, result, prompt_id=turn)
        self.later(lines)

    def stop(self, final, background=()):
        said, self.said = self.said, []
        self.busy = False
        self.hook("Stop", stop_hook_active=False, last_assistant_message=final, background_tasks=list(background),
                  prompt_id=self.turn_id)

        def lines():
            self.script.think()
            for text in said:
                self.script.say(text)
            self.script.say(final, stop="end_turn")
            self.script.noise()
        self.later(lines)


class World(object):
    """What both roads share: the channel, the bridge, the stand-in Claude, and the turns that are played."""
    remote_host = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="whole turn ")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bot_root = os.path.join(self.tmp, "bot")          # the bot's machine: its state/events is its spool
        self.hook_root = self.bot_root                         # where dchook writes: the same, on this machine
        self.transcript_path = os.path.join(self.tmp, "claude", "projects", "-w", "0f6e1c1e.jsonl")
        self.emitted = self.handled = 0
        self.kinds = []
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(TheHost(self.ch))
        self.bridge.last_reap = time.time()
        self.st = self.bridge.host.chan(self.ch)
        self.st["cwd"] = self.tmp
        self.claude = Claude(self)
        handle = self.bridge.handle

        async def counted(ev):
            self.kinds.append(ev.get("dc_kind"))  # (stage 3b step 2.1: every event that gets here says what it is)
            try:
                return await handle(ev)
            finally:
                self.handled += 1
        self.patches = [
            mock.patch.object(self.bridge, "handle", counted),
            mock.patch.object(tmux_bridge, "SPOOL", os.path.join(self.bot_root, "state", "events")),
            mock.patch.object(config, "STATE", os.path.join(self.bot_root, "state")),
            mock.patch.object(config, "INTERIM", True), mock.patch.object(config, "OWNER_ID", 42),
            mock.patch.object(ask, "ASKS", os.path.join(self.tmp, "asks")), mock.patch.object(ask, "ANSWERS", os.path.join(self.tmp, "answers")),
            mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), mock.patch.object(tmux_bridge.cc_typing, "CLEAR_PAUSE", 0),
            mock.patch.object(tmux_bridge.cc_typing, "BOX_PAUSE", 0.01), mock.patch.object(tmux_bridge.cc_typing, "PANEL_PAUSE", 0.01), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 3),
            mock.patch.object(asyncio, "sleep", nap)]
        os.makedirs(os.path.join(self.bot_root, "state"))

    def play(self, turns):
        """Run `turns` (a coroutine function) with the pump going, as the bot runs."""
        async def pumping():
            while True:
                await self.bridge.pump_once()
                await real_sleep(0.01)

        async def go():
            tasks = [asyncio.ensure_future(pumping())] + [asyncio.ensure_future(c) for c in self.others()]
            try:
                await self.before()
                await turns()
            finally:
                await self.bridge.shutdown()
                for t in tasks:
                    t.cancel()
        with fakepane.use(self.claude), contextlib.ExitStack() as stack:
            for p in self.patches + self.more_patches():
                stack.enter_context(p)
            asyncio.run(go())

    def hook_env(self, pane=None):
        """The environment Claude Code's hooks run in: the channel the window was opened for, and the tmux pane
        they are in (`pane`: another one than the stand-in's). Never this test run's own TMUX."""
        env = dict((k, v) for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE"))
        env.update(DCBOT_ROOT=self.hook_root, DCBOT_CHANNEL=str(self.ch.id))
        pane = pane or self.pane()
        if pane:
            env.update(TMUX="%s,4321,0" % pane["socket"], TMUX_PANE=pane["id"])
        return env

    def pane(self):
        """Where the stand-in Claude runs: the channel's window, or its session on the bot's own tmux server."""
        st = self.bridge.host.channels_state.get(str(self.ch.id)) or {}
        return st.get("pane") or {"socket": "/tmp/tmux-1000/" + config.TMUX_SOCKET, "id": "%0"}

    def others(self):
        return []

    def more_patches(self):
        return []

    async def before(self):
        pass

    async def settled(self):
        """Every event the hooks reported has been handled (and what handling it started has had its turn)."""
        t0 = time.time()
        while self.handled < self.emitted:
            if time.time() - t0 > 10:
                raise AssertionError("only %d of %d events were handled" % (self.handled, self.emitted))
            await real_sleep(0.01)
        for _ in range(5):
            await real_sleep(0)

    async def send(self, text, **kw):
        m = Message(self.ch, text, 1000 + len(self.ch.sent))
        await self.bridge.submit(m, text, **kw)
        await self.settled()
        return m

    def said(self, since=0):
        return [(m.content, m.silent) for m in self.ch.sent[since:]]

    # ------------------------------------------------------------------ the turns
    def first_turn_recorded(self, reply, end):
        """📋: the reply's message leads to the stretch of the file its turn lies in: from the owner's own message
        (the row with the turn's promptId, wherever the file stood when the bot got to the event: #issues 18 was
        that the first turn mostly began after it) to the turn's own last reply (#issues 22: written after the Stop
        hook, it used to fall into the next turn's)."""
        pieces = self.pieces(reply)
        self.assertEqual([p.split("\n")[0] for p in pieces],
                         ["🧑 **你**", "💬 " + A, "🔧 **Bash**（列出檔案）", "```", "💬 " + B, "🔧 **Read** `/w/notes/a.md`", "```",
                          "🔧 **Read** `/w/notes/b.md`", "```", "💬 整理好了：兩個檔都歸到 2026/。"])
        self.assertEqual(pieces[0], "🧑 **你**\n整理一下 notes 資料夾")
        self.assertFalse(any("subagent" in p or "reminder" in p for p in pieces), "lines that are no message stay out")

    def second_turn_recorded(self, replies, first_end, prompt):
        spans = [self.st["turns"][str(m.id)] for m in replies]
        self.assertEqual(len(replies), 2)
        self.assertEqual(spans[0], spans[1], "both messages of the reply lead to the same turn")
        self.assertGreater(spans[0][1], first_end - 1, "it begins at its own first row, after all of the turn before")
        pieces = self.pieces(replies[0])
        self.assertEqual(pieces[0], "🧑 **你**\n" + prompt)
        self.assertEqual([p.split("\n")[0] for p in pieces[1:3]], ["💬 更新索引。", "🔧 **Edit** `/w/notes/index.md`（改 1 字 → 2 字）"])
        self.assertFalse(any("整理好了" in p for p in pieces), "the reply of the turn before is not in this one")
        self.first_turn_recorded(self.ch.sent[2], first_end)  # and the first turn's 📋 now ends where this one begins

    def test_a_whole_turn_and_the_one_after(self):
        async def turns():
            m = await self.send("整理一下 notes 資料夾")
            self.assertIn(("typed", "整理一下 notes 資料夾"), self.claude.log)
            self.assertEqual(self.claude.log[-1], ("key", "Enter"))
            self.assertEqual(m.reactions, ["👀"], "the receipt: Claude Code took it")
            self.assertTrue(self.st["busy"])
            self.claude.say(A)
            self.claude.tool("Bash", {"command": "ls notes", "description": "列出檔案"}, "a.md\nb.md")
            await self.settled()
            self.assertEqual(self.said(), [(">>> " + A, True)], "what was said before the tool call, as a quiet quote")
            self.claude.say(B)
            self.claude.tool("Read", {"file_path": "/w/notes/a.md"}, "# a")
            self.claude.tool("Read", {"file_path": "/w/notes/b.md"}, "# b")
            await self.settled()
            self.assertEqual(self.said(1), [(">>> " + B, True)], "once: the second Read has nothing new before it")
            self.claude.stop("整理好了：兩個檔都歸到 2026/。")
            await self.settled()
            reply, loud = self.said(2)[0][0], [silent for _, silent in self.said(2)]
            self.assertEqual(loud, [False], "one message, and it rings: the reply (its own words are not quoted again)")
            body, foot = reply.rsplit("\n", 1)
            self.assertEqual(body, "整理好了：兩個檔都歸到 2026/。")
            self.assertEqual(FOOT.match(foot).group(1, 3), ("Read×2, Bash×1", None), foot)
            self.assertFalse(self.st["busy"])
            self.assertEqual((self.st["context"], self.st["bg"]), ([TOKENS, MODEL], 0))
            self.assertEqual(self.bridge.host.audits[-1].split("（")[0], "#c：整理一下 notes 資料夾 → 完成")
            first_end = self.claude.script.size()
            self.first_turn_recorded(self.ch.sent[2], first_end)

            # the next turn: a long reply (two messages), a background task left running
            before = len(self.ch.sent)
            m = await self.send("再把索引也更新")
            self.assertEqual(m.reactions, ["👀"])
            self.claude.say("更新索引。")
            self.claude.tool("Edit", {"file_path": "/w/notes/index.md", "old_string": "a", "new_string": "bb"})
            await self.settled()  # (a reply written before its turn's last tool call was looked at would be quoted too)
            long = "\n".join("索引第 %03d 行 %s" % (i, "x" * 50) for i in range(45))
            self.claude.stop(long, background=[{"id": "bg1", "status": "running"}])
            await self.settled()
            said = self.said(before)
            self.assertEqual(said[0], (">>> 更新索引。", True))
            self.assertEqual([silent for _, silent in said[1:]], [False, False], "the reply in two messages")
            whole = "\n".join(text for text, _ in said[1:])
            body, foot = whole.rsplit("\n", 1)
            self.assertEqual(body, long)
            self.assertEqual(FOOT.match(foot).group(1, 3), ("Edit×1", "1"), foot)
            self.assertEqual(self.st["bg"], 1)
            self.second_turn_recorded(self.ch.sent[before + 1:], first_end, "再把索引也更新")
            self.assertEqual([k for k in self.kinds if k not in ("ready", "input", "progress", "turn_end")], [], "by this road too")
            self.assertEqual((self.kinds.count("input"), self.kinds.count("turn_end"), self.kinds.count("progress")), (2, 2, 4))
        self.play(turns)

    def test_what_was_said_before_a_message_sent_meanwhile(self):
        """#issues 8 (2026-10-11): A, a tool call, B, then the owner sends another message while Claude works (its
        UserPromptSubmit comes at once), C, a tool call. B must reach the channel.

        In the measured order B is lost on the other machine's road when its event was passed on before B's line
        was written (the tenth of a second after the hook), and the owner's message comes before the next tool
        call: the relay moves its place to the end of the file at every UserPromptSubmit."""
        async def turns():
            await self.send("整理一下 notes 資料夾")
            self.claude.say(A)
            self.claude.tool("Bash", {"command": "ls notes"})
            await self.settled()
            self.claude.say(B)
            self.claude.hold = True  # the call's event is handled before its lines are in the file
            self.claude.tool("Bash", {"command": "cat notes/a.md"})
            await self.settled()
            self.claude.write_now()
            self.claude.hold = False
            m = await self.send("日期也一起改")
            self.assertEqual(m.reactions, ["👀"])
            self.claude.say(C)
            self.claude.tool("Bash", {"command": "mv a b"})
            await self.settled()
            self.assertEqual([text for text, _ in self.said()], [">>> " + A, ">>> " + B, ">>> " + C])
            self.claude.stop("好了。")
            await self.settled()
            self.assertEqual(len(self.ch.sent), 4)
            self.assertTrue(self.ch.sent[3].content.startswith("好了。\n-# Bash×3"))
        self.play(turns)


class ThisMachine(World, unittest.TestCase):
    """dchook → state/events → pump_once() → handle(), which reads the conversation file itself."""

    def test_the_session_is_started_for_the_first_message(self):
        async def turns():
            self.assertFalse(self.claude.running)
            m = await self.send("hello")
            name, cwd, argv, env = self.claude.started[0]
            self.assertEqual((len(self.claude.started), cwd), (1, self.tmp))
            self.assertNotIn("--resume", argv)
            self.assertEqual(env["DCBOT_CHANNEL"], str(self.ch.id))
            self.assertEqual(self.st["session_id"], self.claude.script.session, "from SessionStart: the next start resumes it")
            self.assertEqual(m.reactions, ["👀"])
            self.claude.stop("hi")
            await self.settled()
            self.assertTrue(self.ch.sent[-1].content.startswith("hi\n-# "))
        self.play(turns)

    def pieces(self, reply):
        """What 📋 on this reply shows: the turn from its own first row to the next turn's (found now, in the file
        as it is; nothing was recorded for the end, only the turn's id beside the record)."""
        path, start, stop, _ = self.st["turns"][str(reply.id)]
        self.assertEqual((path, stop), (self.claude.script.path, None))
        return process.pieces_of(self.st, reply.id)

    def test_the_first_turns_start_does_not_depend_on_when_its_event_is_handled(self):
        """The other timing of #issues 18: the bot gets to UserPromptSubmit before the prompt's line is written
        (in the whole-turn test above the line is there first). The turn has the owner's message both ways."""
        async def turns():
            self.claude.hold = True
            sending = asyncio.ensure_future(self.send("整理一下 notes 資料夾"))
            while self.handled < self.emitted or not self.emitted:  # SessionStart, then the prompt's event: handled
                await real_sleep(0.01)
            for _ in range(20):
                await real_sleep(0.01)
            self.claude.write_now()  # only now is the prompt's line in the file
            self.claude.hold = False
            await sending
            self.claude.stop("好。")
            await self.settled()
            pieces = self.pieces(self.ch.sent[-1])
            self.assertEqual(pieces, ["🧑 **你**\n整理一下 notes 資料夾", "💬 好。"])
        self.play(turns)

    def test_a_call_waits_for_its_row_and_brings_the_words_said_before_it(self):
        """Step 1.6b, on both roads: the tool call's hook has fired and its lines are not written yet (a tenth of a
        second for one call, 1.5 s and more for several in one reply). The event is not handled until they are; then
        the words said ahead of the call come with it, before the tool's own line. Until 1.6b the event was handled
        at once and the words came with the next one (after the tool had run)."""
        async def turns():
            await self.send("hello")
            self.claude.say(A)
            self.claude.hold = True
            self.claude.tool("Bash", {"command": "ls"})
            for _ in range(30):  # several rounds of the pump (and of the relay): it stays unhandled
                await real_sleep(0.01)
            self.assertEqual((self.emitted - self.handled, self.said()), (1, []), "waiting for its lines")
            self.claude.write_now()
            self.claude.hold = False
            await self.settled()
            self.assertEqual(self.said(), [(">>> " + A, True)], "with its own event, once")
            self.claude.tool("Bash", {"command": "pwd"})
            self.claude.stop("done")
            await self.settled()
            self.assertEqual([q for q, _ in self.said() if q == ">>> " + A], [">>> " + A], "and not again")
        self.play(turns)


class AnotherMachine(World, unittest.TestCase):
    """dchook → that machine's spool → dcremote's relay() (which reads the conversation file there) → the line the
    bot's stream() reads → handle(), which may not open the file: it is on the other machine."""
    remote_host = "lap"

    def setUp(self):
        World.setUp(self)
        self.hook_root = os.path.join(self.tmp, "that machine")
        self.st.pop("tmux", None)
        self.st["pane"] = {"socket": "/tmp/s", "id": "%1", "host": "lap"}  # a pane attached on the other machine
        self.claude.running = True
        self.claude.script.opened()
        self.lines = None

    def others(self):
        self.lines = asyncio.Queue()

        async def that_machine():
            """tools/dcremote stream, there: a heartbeat at once, then every event its relay() sends."""
            await self.lines.put(b'{"heartbeat": 1}\n')
            pos = {}
            while True:
                out = io.StringIO()
                dcremote.relay(os.path.join(self.hook_root, "state", "events"), pos, out)
                for line in out.getvalue().splitlines():
                    await self.lines.put(line.encode("utf-8") + b"\n")
                await real_sleep(0.01)
        return [that_machine(), self.bridge.stream("lap")]

    def more_patches(self):
        world = self

        class Line(object):  # the SSH process bringing that machine's events
            def __init__(self):
                self.stdout = self

            async def readline(self):
                return await world.lines.get()

            def kill(self):
                pass

        async def spawn(*argv, **kw):
            return Line()
        return [mock.patch.object(remote, "reach", mock.AsyncMock(return_value="LAP")),
                mock.patch.object(remote, "stream_command", return_value="x"),
                mock.patch.object(remote, "argv", lambda host, cmd, alias: ["the-line-to", host]),
                mock.patch.object(tmux_bridge, "STREAM_MIN_GAP", 0),
                mock.patch.object(asyncio, "create_subprocess_exec", spawn)]

    async def before(self):
        t0 = time.time()
        while "lap" in self.bridge.stream_down or "lap" not in self.bridge.streams:  # the line is up: its heartbeat came
            self.assertLess(time.time() - t0, 5)
            await real_sleep(0.01)

    def pieces(self, reply):
        """What 📋 on this reply shows (stage 3b step 1.10): the record is kept as for a turn of this machine, with
        the machine's name beside it; the turn is read over there by `dcremote turn` (the real function, with that
        machine's Claude Code folder), sent as JSON, and worded here. The same pieces as when the same file is read
        on this machine. The bot itself never opened the file."""
        path, start, stop, _ = self.st["turns"][str(reply.id)]
        self.assertEqual((path, stop), (self.claude.script.path, None))
        self.assertEqual(self.st["turn_hosts"][str(reply.id)], "lap")
        self.assertEqual(self.bridge.rt(self.ch).pos, {}, "the bot never opens the other machine's file")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude")}), contextlib.redirect_stdout(out):
            dcremote.turn(path, str(start), self.st["turn_ids"][str(reply.id)])
        sent = json.loads(out.getvalue())
        self.assertTrue(all(isinstance(i, list) for i in sent), "plain data")
        there = process.pieces(sent)
        self.assertEqual(there, process.pieces_of(self.st, reply.id), "as the same file reads on its own machine")
        return there


if __name__ == "__main__":
    unittest.main()
