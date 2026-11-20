"""C-201 2c2: /move machine and /move folder (bot/moving.py) and the record carried (bot/transcripts.py), over
test_moving's fake tmux, with another machine ("far") that has its own HOME and answers dcremote as the real one
would (bot/transcripts.py run on its folders): at no point two Claudes on one conversation; the record copied (its
folder of subagents with a folder in it too), the old one kept; refused only on what is known."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import base64  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import tarfile  # noqa: E402
import tempfile  # noqa: E402
import asyncio  # noqa: E402
import stat  # noqa: E402
import types  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from tests import fakepane  # noqa: E402,F401 (the tmux guard: tests/test_tmuxguard.py)
from tests.test_moving import BOT, USER, Base  # noqa: E402

from bot import moving, transcripts  # noqa: E402

FAR = "/tmp/tmux-test/far"  # the owner's tmux on the other machine


def record(home, folder, sid, lines=("a", "b"), side=True):
    base = transcripts.project_dir(folder, home)
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, sid + ".jsonl"), "w") as f:
        f.write("".join('{"n": "%s"}\n' % x for x in lines))
    if side:
        os.makedirs(os.path.join(base, sid, "subagents", "deep"), exist_ok=True)
        with open(os.path.join(base, sid, "subagents", "agent-1.jsonl"), "w") as f:
            f.write("sub\n")
        with open(os.path.join(base, sid, "subagents", "deep", "tool.txt"), "w") as f:
            f.write("deep\n")
    return base


class Far(Base):
    def setUp(self):
        super().setUp()
        self.home = os.environ["HOME"]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.far_home = tmp.name
        self.folder = os.path.join(self.home, "work", "proj")
        self.far_folder = os.path.join(self.far_home, "work", "proj")
        os.makedirs(self.folder)
        os.makedirs(self.far_folder)
        self.far = {"reach": True, "claude": "/far/bin/claude", "login": True, "probe": None, "engine_old": False}
        self.calls = []

        async def dcremote(host, *args, input=None, timeout=30, err=None):
            self.calls.append(args[0])
            assert host == "far", host
            if not self.far["reach"]:
                if err is not None:
                    err.append("ssh: connect to host far: timed out")
                return 255, ""
            if args[0] == "info":
                folder = os.path.abspath(os.path.expanduser(args[1].replace("~", self.far_home, 1))) if len(args) > 1 else None
                return 0, json.dumps({"socket": FAR, "home": self.far_home, "claude": self.far["claude"],
                                      "folder": folder if folder and self.there(folder) else None, "login": self.far["login"]})
            if args[0] == "move-probe":
                return (0, json.dumps(self.far["probe"])) if self.far["probe"] is not None else (1, "")
            if args[0] == "transcript-size":
                try:
                    return 0, json.dumps({"bytes": transcripts.size(args[1], args[2], home=self.far_home)})
                except transcripts.Refused as e:
                    return 3, json.dumps({"error": str(e)})
            if args[0] in ("transcript-out", "transcript-in"):
                if self.far["engine_old"]:
                    if err is not None:
                        err.append("dcremote：不認得 %s" % args[0])
                    return 1, ""
                try:
                    if args[0] == "transcript-out":
                        return 0, transcripts.pack(args[1], args[2], home=self.far_home)
                    return 0, json.dumps(transcripts.unpack(input, args[1], args[2], home=self.far_home))
                except transcripts.Refused as e:
                    return 3, json.dumps({"error": str(e)})
            return 1, ""
        for name, f in (("dcremote", dcremote), ("this_machine", lambda: "lap"), ("hosts", lambda channels=None: ["far"]),
                        ("inventory", lambda: {"machines": {"lap": {}, "far": {"hostname": "far.lab"}, "box": {}}})):
            q = mock.patch.object(moving.remote, name, f)
            q.start()
            self.addCleanup(q.stop)

        async def claude_on(host):
            return self.far["claude"]
        q = mock.patch.object(moving.remote, "claude_on", claude_on)
        q.start()
        self.addCleanup(q.stop)

    def there(self, folder):
        """A folder that machine has (one file system for both here: only what lies under its HOME)."""
        return os.path.isdir(folder) and (folder + "/").startswith(self.far_home + "/")

    def card(self):
        cards = []
        msg = types.SimpleNamespace()

        async def edit(content=None, view=None):
            cards.append(content)
        msg.edit = edit
        return msg, cards

    def bot(self, st):
        b, ch = super().bot(st)
        b.mover.here_dir = lambda path: os.path.isdir(path) and not (path + "/").startswith(self.far_home + "/")
        return b, ch

    def far_pane(self, claude="s1"):
        k = self.world.add(FAR, "work", claude=claude, shell=True)
        return k, {"socket": FAR, "id": k, "pid": self.world.panes[k]["pid"], "host": "far"}


class ToAnotherMachine(Far):
    def test_the_bots_session_to_another_machine_the_record_carried(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        base = record(self.home, self.folder, "s1")
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": self.folder})
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "far", "card": msg}))
        st = b.channels_state["7"]
        self.assertTrue(cards[-1].startswith("✅"), cards)
        self.assertNotIn("tmux", st)
        self.assertEqual((st["pane"]["host"], st["pane"]["socket"], st["cwd"]), ("far", FAR, self.far_folder),
                         "the old HOME turned into that machine's")
        self.assertEqual(st["pane_session"], "dc-a-7")
        new = self.world.panes[st["pane"]["id"]]
        self.assertEqual((new["claude"], new["cwd"]), ("s1", self.far_folder))
        self.assertEqual(self.world.windows[-1], ["/bin/sh", "-c", '/far/bin/claude --permission-mode default --resume s1; '
                                                  'exec "${SHELL:-/bin/sh}" -l'],
                         "that machine's claude, the owner's way, never the bot's settings")
        far = transcripts.project_dir(self.far_folder, self.far_home)
        for rel in ("s1.jsonl", "s1/subagents/agent-1.jsonl", "s1/subagents/deep/tool.txt"):
            with open(os.path.join(base, rel)) as a, open(os.path.join(far, rel)) as c:
                self.assertEqual(a.read(), c.read(), rel)
        self.assertTrue(os.path.exists(os.path.join(base, "s1.jsonl")), "the old record kept")
        self.assertIn("搬到", st["note_next"])
        self.assertEqual(self.world.max_same, 1)
        self.assertEqual(len(b.audits), 1)
        self.assertIn("far", b.audits[0])

    def test_from_another_machines_pane_to_the_bot_ended_over_there(self):
        """The old Claude ended on the machine it is on (manager 11-20): its pane's processes asked there."""
        k, pane = self.far_pane()
        self.far["probe"] = {"shell": True, "argv": ["claude", "--permission-mode", "acceptEdits", "hi"], "daemon": False}
        record(self.far_home, self.far_folder, "s1")
        b, ch = self.bot({"pane": pane, "pane_session": "work", "session_id": "s1", "cwd": self.far_folder})
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "lap", "card": msg}))
        st = b.channels_state["7"]
        self.assertTrue(cards[-1].startswith("✅"), cards)
        self.assertEqual(st["tmux"], "dc-a-7")
        self.assertEqual(st["cwd"], self.folder)
        self.assertIsNone(self.world.panes[k]["claude"], "/exit typed over there")
        self.assertIn(k, self.world.panes, "the owner's pane stays")
        self.assertEqual(st["user_options"], ["--permission-mode", "acceptEdits"])
        self.assertIn("move-probe", self.calls)
        self.assertEqual(self.calls.count("move-probe"), 1, "asked once per move")
        self.assertIn(("unbind", k, "7"), self.routes)
        self.assertTrue(os.path.exists(os.path.join(transcripts.project_dir(self.folder, self.home), "s1", "subagents", "deep", "tool.txt")))
        self.assertEqual(self.world.max_same, 1)

    def test_a_daemon_over_there_nothing_sent(self):
        k, pane = self.far_pane()
        self.far["probe"] = {"shell": True, "argv": ["claude"], "daemon": True}
        record(self.far_home, self.far_folder, "s1")
        b, ch = self.bot({"pane": pane, "pane_session": "work", "session_id": "s1", "cwd": self.far_folder})
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "lap", "card": msg, "by": "x", "why": "w"}))
        self.assertIn("daemon", cards[-1])
        self.assertEqual(self.world.panes[k]["claude"], "s1")
        self.assertEqual(self.world.panes[k]["typed"], "")
        self.assertIn("什麼都沒動", b.audits[-1])

    def test_that_machine_not_answering_about_its_pane_nothing_sent(self):
        k, pane = self.far_pane()
        self.far["probe"] = None
        b, ch = self.bot({"pane": pane, "pane_session": "work", "session_id": "s1", "cwd": self.far_folder})
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "lap", "card": msg}))
        self.assertIn("問不到", cards[-1])
        self.assertEqual(self.world.panes[k]["claude"], "s1")

    def test_the_new_side_not_up_back_in_the_bots_tmux_in_its_own_folder(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        record(self.home, self.folder, "s1")
        self.world.outcome[FAR] = "down"
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": self.folder})
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "far", "card": msg}))
        st = b.channels_state["7"]
        self.assertTrue(cards[-1].startswith("❌"), cards)
        self.assertIn("原本的位置接回", cards[-1])
        self.assertEqual((st.get("tmux"), st["cwd"]), ("dc-a-7", self.folder))
        self.assertNotIn("pane", st)
        self.assertEqual(self.world.find("dc-a-7")["claude"], "s1")
        self.assertEqual(b.tmux.cwds[-1], self.folder, "taken up again in its own folder")
        self.assertFalse(any(p["socket"] == FAR and p["claude"] for p in self.world.panes.values()))
        self.assertIn(("unbind", self.world.windows and None, "7"), [(a, None, c) for a, _, c in self.routes])
        self.assertEqual(self.world.max_same, 1)

    def test_asked_to_the_machine_it_is_on_nothing_touched(self):
        """A card pressed after it got there another way: looked at again when it runs."""
        self.world.add(BOT, "dc-a-7", claude="s1")
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": self.folder})
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "lap", "card": msg}))
        self.assertIn("已經在 lap", cards[-1])
        self.assertEqual(self.world.find("dc-a-7")["claude"], "s1")
        self.assertEqual((self.world.opened, self.calls), ([], []))

    def from_far(self):
        k, pane = self.far_pane()
        self.far["probe"] = {"shell": True, "argv": ["claude"], "daemon": False}
        record(self.far_home, self.far_folder, "s1")
        b, ch = self.bot({"pane": pane, "pane_session": "work", "session_id": "s1", "cwd": self.far_folder})
        return k, b, ch

    def test_the_line_drops_as_exit_is_sent_nothing_opened(self):
        """#code-review-2 on af0c86cd: /exit not sent (ssh 255), the next asking unanswered; it was taken for ended."""
        k, b, ch = self.from_far()
        msg, cards = self.card()
        real = self.world.tmux

        async def tmux(*args, socket=None, host=None, err=None):
            if args[0] == "send-keys" and host and "/exit" in args:
                self.world.dropped = True
            return await real(*args, socket=socket, host=host, err=err)
        self.world.tmux = tmux
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "lap", "card": msg}))
        self.assertTrue(cards[-1].startswith("❌"), cards)
        self.assertIn("送 /exit 到 far 時連線斷了，不確定送到沒有", cards[-1])
        self.assertNotIn("舊的沒動", cards[-1], "not known: nor said as untouched (drill B)")
        self.assertIn("輸入框可能留著 /exit", cards[-1])
        self.assertEqual(self.world.panes[k]["claude"], "s1")
        self.assertIsNone(self.world.find("dc-a-7"), "nothing opened on the bot's machine")
        self.assertIn(("bind", k, "7"), self.routes, "the old pane bound again")
        self.assertEqual(self.world.max_same, 1)

    def test_exit_sent_and_that_machine_no_longer_answering_nothing_opened(self):
        k, b, ch = self.from_far()
        msg, cards = self.card()
        self.world.exit_hangs = True  # (it may have ended: not known)
        real = self.world.tmux

        async def tmux(*args, socket=None, host=None, err=None):
            got = await real(*args, socket=socket, host=host, err=err)
            if args[0] == "send-keys" and host and args[-1] == "Enter":
                self.world.unanswered = True
            return got
        self.world.tmux = tmux
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "lap", "card": msg}))
        self.assertIn("問不到 far，不確定舊的 Claude 停了沒有", cards[-1])
        self.assertNotIn("舊的沒動", cards[-1], "not known: nor said as untouched (drill B)")
        self.assertIsNone(self.world.find("dc-a-7"))
        self.assertEqual(self.world.max_same, 1)

    def test_that_machine_not_answering_at_first_nothing_sent(self):
        k, b, ch = self.from_far()
        self.world.unanswered = True
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "lap", "card": msg}))
        self.assertIn("問不到 far", cards[-1])
        self.assertFalse([x for x in self.world.log if x[0] == "send-keys"], "nothing typed over there")
        self.assertNotIn("transcript-out", self.calls)
        self.assertIsNone(self.world.find("dc-a-7"))

    def test_two_moves_at_once_each_its_own_answers(self):
        """The move-probe answers are the move's own (#code-review, #code-review-2 lows on af0c86cd)."""
        k, b, ch = self.from_far()
        pane = b.channels_state["7"]["pane"]

        async def one():
            moving.PROBED.set({})
            await b.mover.probe(pane)
            await asyncio.sleep(0.01)
            await b.mover.probe(pane)

        async def both():
            await asyncio.gather(asyncio.ensure_future(one()), asyncio.ensure_future(one()))
            self.assertIsNone(moving.PROBED.get(), "outside a move nothing kept")
        self.go(both())
        self.assertEqual(self.calls.count("move-probe"), 2, "asked once by each move")

    def test_an_older_engine_there_back_where_it_was(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        record(self.home, self.folder, "s1")
        self.far["engine_old"] = True
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": self.folder})
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "far", "card": msg}))
        self.assertIn("引擎太舊", cards[-1])
        self.assertIn("原本的位置接回", cards[-1])
        self.assertEqual(self.world.find("dc-a-7")["claude"], "s1")
        self.assertFalse(any(p["socket"] == FAR for p in self.world.panes.values()), "nothing opened there")
        self.assertEqual(self.world.max_same, 1)


class TooBig(Far):
    from_far = ToAnotherMachine.from_far
    """Manager 11-20 (drill B: a 213 MiB record took the bot to 583 MB): a record over BIG is not carried when the
    bot's machine has less than MEM_TIMES its size free; nothing touched, the card says it, #audit has a line."""
    def small(self, free):
        for name, value in (("BIG", 10), ("MEM_TIMES", 4)):
            q = mock.patch.object(moving, name, value)
            q.start()
            self.addCleanup(q.stop)
        q = mock.patch.object(moving.transcripts, "mem_available", lambda: free)
        q.start()
        self.addCleanup(q.stop)

    def test_a_big_record_on_that_machine_refused_nothing_touched(self):
        k, b, ch = self.from_far()
        self.small(free=20)  # the record ~40 bytes: 4 times that is more than 20
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "lap", "card": msg}))
        self.assertTrue(cards[-1].startswith("❌"), cards)
        self.assertIn("這個對話紀錄太大", cards[-1])
        self.assertIn("機器人這台記憶體不夠", cards[-1])
        self.assertIn("什麼都沒動", cards[-1])
        self.assertEqual(self.world.panes[k]["claude"], "s1", "the old Claude untouched")
        self.assertFalse([x for x in self.world.log if x[0] == "send-keys"], "nothing typed over there")
        self.assertNotIn("transcript-out", self.calls)
        self.assertEqual(len(b.audits), 1, b.audits)
        self.assertIn("太大", b.audits[0])

    def test_a_big_record_here_refused_for_an_agent_too(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        record(self.home, self.folder, "s1")
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": self.folder})
        self.small(free=20)
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "far", "card": msg, "by": "#x", "why": "w"}))
        self.assertIn("這個對話紀錄太大", cards[-1])
        self.assertEqual(self.world.find("dc-a-7")["claude"], "s1")
        self.assertEqual(len(b.audits), 1, b.audits)
        self.assertNotIn("transcript-out", self.calls)

    def test_enough_memory_carried(self):
        k, b, ch = self.from_far()
        self.small(free=10 ** 9)
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "machine", "target": "lap", "card": msg}))
        self.assertTrue(cards[-1].startswith("✅"), cards)

    def test_size_counts_the_record_and_its_folder(self):
        base = record(self.home, self.folder, "s1")
        want = sum(os.path.getsize(os.path.join(r, n)) for r, _, ns in os.walk(base) for n in ns)
        self.assertEqual(transcripts.size(self.folder, "s1", home=self.home), want)
        with self.assertRaises(transcripts.Refused):
            transcripts.size(self.folder, "s2", home=self.home)


class Refusals(Far):
    def ask(self, st, arg):
        b, ch = self.bot(st)
        sent = []

        async def send(text, view=None):
            sent.append((text, view))
        self.go(b.mover.command(ch, b.channels_state["7"], arg, send))
        return b, sent

    def test_refused_on_what_is_known_nothing_touched(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        st = {"tmux": "dc-a-7", "session_id": "s1", "cwd": self.folder}
        _, sent = self.ask(dict(st), "machine lap")
        self.assertIn("已經在 lap", sent[0][0])
        _, sent = self.ask(dict(st), "machine nowhere")
        self.assertIn("能搬的機器：lap、far", sent[0][0])
        self.far["claude"] = None
        _, sent = self.ask(dict(st), "machine far")
        self.assertIn("找不到 claude", sent[0][0])
        self.far["claude"] = "/far/bin/claude"
        _, sent = self.ask(dict(st), "machine far /not/there")
        self.assertIn("找不到資料夾 `/not/there`", sent[0][0])
        self.far["reach"] = False
        _, sent = self.ask(dict(st), "machine far")
        self.assertIn("連不上 far", sent[0][0])
        _, sent = self.ask(dict(st), "folder relative/x")
        self.assertIn("完整路徑", sent[0][0])
        _, sent = self.ask(dict(st), "folder " + self.folder)
        self.assertIn("已經在", sent[0][0])
        self.assertEqual(self.world.find("dc-a-7")["claude"], "s1")
        self.assertEqual(self.world.opened, [])

    def test_no_login_file_seen_is_said_not_refused(self):
        """A mac keeps the login in its keychain, an API key needs none (manager 11-20)."""
        self.world.add(BOT, "dc-a-7", claude="s1")
        self.far["login"] = False
        _, sent = self.ask({"tmux": "dc-a-7", "session_id": "s1", "cwd": self.folder}, "machine far.lab")
        (text, view), = sent
        self.assertTrue(text.startswith("❓"), text)
        self.assertIn("看不到 Claude Code 的登入檔", text)
        self.assertIn("會替你答「是」", text)
        self.assertIn(self.far_folder, text)
        self.assertEqual([i.label for i in view.children], ["搬", "取消"])

    def test_an_adapter_refused(self):
        _, sent = self.ask({"tmux": "dc-a-7", "session_id": "s1", "cwd": self.folder,
                            "where": {"agent": {"kind": "pi"}}}, "machine far")
        self.assertIn("這種對話還不能搬", sent[0][0])

    def test_a_session_move_on_another_machine_still_refused(self):
        k, pane = self.far_pane()
        _, sent = self.ask({"pane": pane, "pane_session": "work", "session_id": "s1", "cwd": self.far_folder}, "session x")
        self.assertIn("別台機器上", sent[0][0])


class FolderMove(Far):
    def test_the_owners_pane_in_a_subshell_its_shell_left_where_it_was(self):
        k = self.world.add(USER, "main", claude="s1", shell=True)
        pane = {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]}
        new = os.path.join(self.home, "work", "new place")
        os.makedirs(new)
        base = record(self.home, self.folder, "s1")
        b, ch = self.bot({"pane": pane, "pane_session": "main", "session_id": "s1", "cwd": self.folder})

        async def shell_under(p):
            return True
        b.mover.shell_under = shell_under
        b.mover.claude_line = lambda p: ["claude", "--permission-mode", "plan"]
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "folder", "target": new, "card": msg}))
        st = b.channels_state["7"]
        self.assertTrue(cards[-1].startswith("✅"), cards)
        self.assertEqual(self.world.resumed, ["( cd -- '%s' && claude --permission-mode plan --resume s1 )" % new])
        self.assertEqual((st["cwd"], st["pane"]["id"]), (new, k))
        self.assertEqual(self.world.panes[k]["claude"], "s1")
        self.assertTrue(os.path.exists(os.path.join(transcripts.project_dir(new, self.home), "s1.jsonl")))
        self.assertTrue(os.path.exists(os.path.join(base, "s1.jsonl")), "the old record kept")
        self.assertIn(self.folder, st["note_next"])
        self.assertIn(new, st["note_next"])
        self.assertEqual(self.world.max_same, 1)

    def test_the_bots_session_opened_again_in_the_new_folder(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        new = os.path.join(self.home, "other")
        os.makedirs(new)
        record(self.home, self.folder, "s1", side=False)
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": self.folder})
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "folder", "target": "~/other", "card": msg}))
        self.assertTrue(cards[-1].startswith("✅"), cards)
        self.assertEqual(b.tmux.cwds[-1], new)
        self.assertEqual(self.world.find("dc-a-7")["claude"], "s1")
        self.assertEqual(self.world.max_same, 1)

    def test_no_record_nothing_opened_back_where_it_was(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        new = os.path.join(self.home, "other")
        os.makedirs(new)
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": self.folder})
        msg, cards = self.card()
        self.go(b.mover.go(ch, {"cid": "7", "kind": "folder", "target": new, "card": msg}))
        self.assertIn("沒有複製過去", cards[-1])
        self.assertEqual(b.channels_state["7"]["cwd"], self.folder)
        self.assertEqual(b.tmux.cwds, [self.folder])
        self.assertEqual(self.world.max_same, 1)


class TypedIntoTheOwnersPane(Far):
    """#code-review on af0c86cd: /move folder in the owner's pane, the new one not taken up: nothing typed into a
    Claude that is still there."""
    def setUp(self):
        super().setUp()
        k = self.world.add(USER, "main", claude="s1", shell=True)
        self.k = k
        self.new = os.path.join(self.home, "fresh")
        os.makedirs(self.new)
        record(self.home, self.folder, "s1")
        self.world.outcome[USER] = "other"  # (the new place comes up with another conversation; the old place not)
        first = self.world.start_claude

        def start_claude(socket, sid):
            got = first(socket, sid)
            self.world.outcome.pop(USER, None)
            return got
        self.world.start_claude = start_claude
        self.b, self.ch = self.bot({"pane": {"socket": USER, "id": k, "pid": self.world.panes[k]["pid"]},
                                    "pane_session": "main", "session_id": "s1", "cwd": self.folder})

        async def shell_under(p):
            return True
        self.b.mover.shell_under = shell_under
        self.b.mover.claude_line = lambda p: ["claude"]

    def test_it_does_not_end_nothing_more_typed(self):
        self.world.typed_hangs = True
        msg, cards = self.card()
        self.go(self.b.mover.go(self.ch, {"cid": "7", "kind": "folder", "target": self.new, "card": msg}))
        self.assertEqual(len(self.world.resumed), 1, "only the line that opened it: no resume line typed into it")
        self.assertEqual(self.world.panes[self.k]["typed"], "")
        self.assertIn("沒結束，留著沒動，原本的位置也沒接回", cards[-1])
        self.assertIn("沒結束", self.b.audits[-1])
        self.assertEqual(self.world.max_same, 1)
        self.assertEqual(self.b.channels_state["7"]["cwd"], self.folder, "the owner's shell is where it was")

    def test_it_ends_and_the_old_place_takes_it_up(self):
        msg, cards = self.card()
        self.go(self.b.mover.go(self.ch, {"cid": "7", "kind": "folder", "target": self.new, "card": msg}))
        self.assertEqual(self.world.resumed, ["( cd -- %s && claude --permission-mode default --resume s1 )" % self.new,
                                              "claude --permission-mode default --resume s1"])
        self.assertEqual(self.world.panes[self.k]["claude"], "s1")
        self.assertEqual(self.b.channels_state["7"]["cwd"], self.folder)
        self.assertIn("原本的位置接回", cards[-1])
        self.assertEqual(self.world.max_same, 1)

    def steps(self):
        """What happens to the pane's route around ending the Claude typed into it, in order."""
        got, end, unbind, bind = [], self.b.mover.end_typed, moving.routes.unbind_any, moving.routes.bind_any

        async def end_typed(pane):
            got.append("end")
            return await end(pane)

        async def unbind_any(cid, host=None, pane=None):
            got.append("unbind")
            return await unbind(cid, host, pane)

        async def bind_any(pane, cid, tag=True):
            got.append("bind")
            return await bind(pane, cid, tag)
        self.b.mover.end_typed = end_typed
        for name, f in (("unbind_any", unbind_any), ("bind_any", bind_any)):
            q = mock.patch.object(moving.routes, name, f)
            q.start()
            self.addCleanup(q.stop)
        return got

    def test_the_other_conversations_end_is_no_news(self):
        """Drill B: the Claude that came up with another conversation, ended in the owner's pane, sent its
        SessionEnd while the pane was still the channel's (by the route table): it was said as "the conversation
        ended in the terminal" right under the card that said it was taken up again. The pane is let go of before it
        is ended, and bound again when the old place takes the conversation up."""
        got = self.steps()
        msg, cards = self.card()
        self.go(self.b.mover.go(self.ch, {"cid": "7", "kind": "folder", "target": self.new, "card": msg}))
        self.assertEqual(got, ["unbind", "bind", "unbind", "end", "bind"], "end_old, the line typed, its end, reopen()")
        self.assertIn("原本的位置接回", cards[-1])

    def test_one_that_does_not_end_keeps_the_channels_pane(self):
        got = self.steps()
        self.world.typed_hangs = True
        msg, cards = self.card()
        self.go(self.b.mover.go(self.ch, {"cid": "7", "kind": "folder", "target": self.new, "card": msg}))
        self.assertEqual(got, ["unbind", "bind", "unbind", "end", "bind"], "bound again for the owner to look at")
        self.assertIn("沒結束", cards[-1])

    def test_no_start_said_plainly_on_the_card(self):
        """Drill B: wait_ready()'s why is small print of its own line; in the card's line it read "沒搬成：-# 等了 90
        秒…。；在新資料夾…"."""
        from bot import messages

        async def wait_ready(pane, rt, ask=None):
            rt.ready_why = messages.note("claude_code.no_start", secs=90)
            return False
        self.b.tmux.wait_ready = wait_ready
        self.world.typed_hangs = True
        msg, cards = self.card()
        self.go(self.b.mover.go(self.ch, {"cid": "7", "kind": "folder", "target": self.new, "card": msg}))
        self.assertIn("沒搬成：等了 90 秒", cards[-1])
        self.assertNotIn("-#", cards[-1].splitlines()[0])
        self.assertNotIn("。；", cards[-1])

    def test_the_card_says_the_trust_question_is_answered(self):
        sent = []

        async def send(text, view=None):
            sent.append(text)
        self.go(self.b.mover.command(self.ch, self.b.channels_state["7"], "folder " + self.new, send))
        self.assertIn("會替你答「是」", sent[0])


class ByAgent(Far):
    def test_a_claude_asks_for_a_machine_move_with_a_folder(self):
        self.world.add(BOT, "dc-a-7", claude="s1")
        record(self.home, self.folder, "s1")
        other = os.path.join(self.far_home, "elsewhere")
        os.makedirs(other)
        b, ch = self.bot({"tmux": "dc-a-7", "session_id": "s1", "cwd": self.folder})
        posted = []

        async def post(c, text):
            msg, cards = self.card()
            posted.append(cards)
            cards.append(text)
            return msg
        b.mover.post = post
        ok, said = self.go(b.mover.by_agent(ch, types.SimpleNamespace(name="ops"), {
            "move_place": "machine", "move_target": "far", "move_folder": other, "why": "GPU"}))
        self.assertTrue(ok, said)
        self.go(self._drain())
        self.assertTrue(posted[0][-1].startswith("✅"), posted)
        self.assertEqual(b.channels_state["7"]["cwd"], other)
        self.assertIn("ops 要求的：GPU", b.audits[-1])

    async def _drain(self):
        import asyncio
        for _ in range(200):
            await asyncio.sleep(0.05)
            if not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
                return


class Transcripts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.a, self.b = os.path.join(tmp.name, "a"), os.path.join(tmp.name, "b")

    def test_the_record_and_its_folder_with_folders_in_it(self):
        record(self.a, "/w", "s1")
        got = transcripts.unpack(transcripts.pack("/w", "s1", home=self.a), "/x y", "s1", home=self.b)
        self.assertEqual(got, {"files": 3, "kept": None})
        for rel in ("s1.jsonl", "s1/subagents/deep/tool.txt"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(transcripts.project_dir("/x y", self.b), rel)).st_mode), 0o600, rel)
        base = transcripts.project_dir("/x y", self.b)
        self.assertEqual(self.read(base, "s1", "subagents", "deep", "tool.txt"), "deep\n")
        self.assertFalse([n for n in os.listdir(base) if n.startswith(".move-")], "no temporary folder left")

    def test_one_there_that_it_began_as_replaced_another_kept(self):
        record(self.b, "/w", "s1", lines=("a",), side=False)
        record(self.a, "/w", "s1", lines=("a", "b"))
        self.assertIsNone(transcripts.unpack(transcripts.pack("/w", "s1", home=self.a), "/w", "s1", home=self.b)["kept"])
        record(self.b, "/w", "s1", lines=("z",))
        got = transcripts.unpack(transcripts.pack("/w", "s1", home=self.a), "/w", "s1", home=self.b, now=0)
        self.assertTrue(got["kept"].startswith("s1.jsonl.before-move-"), got)
        base = transcripts.project_dir("/w", self.b)
        self.assertEqual(self.read(base, got["kept"]), '{"n": "z"}\n')
        self.assertEqual(self.read(base, "s1.jsonl"), '{"n": "a"}\n{"n": "b"}\n')

    def read(self, *parts):
        with open(os.path.join(*parts)) as f:
            return f.read()

    def tar_of(self, entries):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data, kind in entries:
                info = tarfile.TarInfo(name)
                if kind == "link":
                    info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                    tar.addfile(info)
                else:
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
        return base64.b64encode(buf.getvalue()).decode()

    def test_what_is_not_ours_refused_nothing_written(self):
        for bad in ([("s1.jsonl", b"x", "f"), ("../evil", b"x", "f")], [("s1.jsonl", b"x", "f"), ("s1/../../evil", b"x", "f")], [("s1.jsonl", b"x", "f"), ("/s1/x", b"x", "f")],
                    [("s1.jsonl", b"x", "f"), ("s1/x", b"", "link")],
                    [("s1.jsonl", b"x", "f"), ("other.jsonl", b"x", "f")], [("s1/x", b"x", "f")], "not base64 !"):
            data = bad if isinstance(bad, str) else self.tar_of(bad)
            with self.assertRaises(transcripts.Refused):
                transcripts.unpack(data, "/w", "s1", home=self.b)
            self.assertFalse(os.path.exists(os.path.join(transcripts.project_dir("/w", self.b), "s1.jsonl")))
        with self.assertRaises(transcripts.Refused):
            transcripts.pack("/w", "../s1", home=self.a)
        with self.assertRaises(transcripts.Refused):
            transcripts.pack("/w", "s9", home=self.a)

    def test_a_size_not_the_tars_refused(self):
        record(self.a, "/w", "s1", side=False)
        data = transcripts.pack("/w", "s1", home=self.a)
        real = transcripts.shutil.copyfileobj

        def short(src, dst):
            real(src, dst)
            dst.write(b"!")
        with mock.patch.object(transcripts.shutil, "copyfileobj", short):
            with self.assertRaises(transcripts.Refused):
                transcripts.unpack(data, "/w", "s1", home=self.b)
        self.assertFalse(os.path.exists(os.path.join(transcripts.project_dir("/w", self.b), "s1.jsonl")))


if __name__ == "__main__":
    unittest.main()
