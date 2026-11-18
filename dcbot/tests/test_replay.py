"""C-163 step 3d: replies a takeover left unposted (design 1.4 points 4 and 5, 3.1), the questions waiting, and the
lows of c3's reviews."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import datetime
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from bot import events, main, remote, standby  # (first: it puts the engine on the path)
from altention.connectors.claude_code import transcript  # noqa: E402
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)


def at(minutes):
    return (T0 + datetime.timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def rows(*turns):
    """Transcript rows: (promptId, words, reply, began in minutes, ended or not)."""
    out = []
    for pid, words, reply, began, ended in turns:
        out.append({"type": "user", "promptId": pid, "timestamp": at(began), "message": {"role": "user", "content": words}})
        out.append({"type": "assistant", "timestamp": at(began + 1),
                    "message": {"role": "assistant", "content": [{"type": "text", "text": reply}],
                                "stop_reason": "end_turn" if ended else "tool_use"}})
    return "".join(json.dumps(r) + "\n" for r in out)


class Replies(unittest.TestCase):
    def test_turns_by_id_since_a_time(self):
        path = os.path.join(tempfile.mkdtemp(), "s.jsonl")
        with open(path, "w") as f:
            f.write(rows(("aaaaaaaa-1", "one", "first", 0, True), ("bbbbbbbb-2", "two", "second", 20, True),
                         ("cccccccc-3", "three", "still going", 30, False)))
        got = transcript.replies_since(path, at(10))
        self.assertEqual([(r["pid"], r["final"]) for r in got], [("bbbbbbbb-2", "second")], "since, ended only")
        self.assertEqual(len(transcript.replies_since(path, at(-5))), 2)
        self.assertEqual(transcript.replies_since(os.path.join(tempfile.mkdtemp(), "none.jsonl"), at(0)), [])

    def test_dcremote_replies(self):
        home = tempfile.mkdtemp()
        cwd = "/w/project"
        env = dict(os.environ, HOME=home, CLAUDE_CONFIG_DIR=os.path.join(home, ".claude"))
        folder = os.path.join(home, ".claude", "projects", "-w-project")
        os.makedirs(folder)
        with open(os.path.join(folder, "s1.jsonl"), "w") as f:
            f.write(rows(("aaaaaaaa-1", "one", "first", 0, True)))
        r = subprocess.run([sys.executable, os.path.join(HERE, "tools", "dcremote"), "replies", cwd, "s1", at(-1)],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)[0]["pid"], "aaaaaaaa-1")


class RealTurn(unittest.TestCase):
    """A real Claude Code turn (tests/samples/c163/README.md): the Stop hook's prompt_id is the transcript's promptId
    (#code-review-2 on 43bfe53, manager)."""

    def test_one_value(self):
        folder = os.path.join(HERE, "tests", "samples", "c163")
        with open(os.path.join(folder, "real-turn-stop.json")) as f:
            stop = json.load(f)
        path = os.path.join(folder, "real-turn.jsonl")
        got = transcript.replies_since(path, "2026-01-01T00:00:00Z")
        self.assertEqual([r["pid"] for r in got], [stop["prompt_id"]])
        self.assertEqual(transcript.last_begun(path), stop["prompt_id"])


class Once(unittest.TestCase):
    """#code-review-2 on 43bfe53 (manager): a turn ended while the bot was away comes both by the stream's Stop and
    by the replay: posted once, whichever comes first."""

    def setUp(self):
        from bot import ask, config, tmux_bridge
        from tests.test_turn_end import Ch, Host
        self.tmp = tempfile.mkdtemp()
        self.saved = (ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM)
        ask.ASKS, ask.ANSWERS = os.path.join(self.tmp, "asks"), os.path.join(self.tmp, "answers")
        config.OWNER_ID, config.INTERIM = 42, 0
        self.addCleanup(self.restore)
        self.ch = Ch()
        self.host = Host(self.ch)
        self.host.lease = object()
        self.host.claim_turn = types.MethodType(main.Bot.claim_turn, self.host)
        self.bridge = tmux_bridge.TmuxBridge(self.host)

    def restore(self):
        from bot import ask, config
        ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM = self.saved

    def stop(self, pid):
        ev = {"channel": "5", "hook_event_name": "Stop", "session_id": "abc", "last_assistant_message": "the reply",
              "prompt_id": pid, "transcript_path": os.path.join(self.tmp, "none.jsonl")}

        async def go():
            with mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
                await self.bridge.handle(ev)
        asyncio.run(go())
        return [m.content for m in self.ch.sent if m.content.startswith("the reply")]

    def test_the_replay_first(self):
        self.assertTrue(self.host.claim_turn("5", "bbbbbbbb-2"))  # (what replay_one does before it sends)
        self.assertEqual(self.stop("bbbbbbbb-2"), [], "the Stop after it: not posted again")

    def test_the_stop_first(self):
        posted = self.stop("bbbbbbbb-2")
        self.assertEqual(len(posted), 1)
        self.assertTrue(posted[0].endswith("host-b · bbbbbbbb"), posted[0])
        b = types.SimpleNamespace(user=types.SimpleNamespace(id=1), sent=[], posted_turns=self.host.posted_turns,
                                  REPLAY_BACK=main.Bot.REPLAY_BACK, REPLAY_READ=main.Bot.REPLAY_READ)
        b.claim_turn = types.MethodType(main.Bot.claim_turn, b)

        async def hist(limit=50):
            yield types.SimpleNamespace(author=types.SimpleNamespace(id=1), content="old\n-# host-a · aaaaaaaa", created_at=T0)

        async def send(text):
            b.sent.append(text)
        b.get_channel = lambda i: types.SimpleNamespace(history=hist, send=send)

        async def dcremote(host, *args, **kw):
            return 0, json.dumps([{"pid": "bbbbbbbb-2", "final": "the reply"}])
        with mock.patch.object(main.remote, "dcremote", dcremote), mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            self.assertEqual(asyncio.run(main.Bot.replay_one(b, "5", {"session_id": "s", "cwd": "/c", "home": {"host": "host-c"}})), 0)
        self.assertEqual(b.sent, [], "the replay after it: not posted again")


class Footer(unittest.TestCase):
    def test_the_machine_then_the_id_last(self):
        self.assertEqual(events.footer({"Read": 1}, 272, tail=events.footer_tail("host-b", "a1b2c3d4-ffff")),
                         "-# Read×1 · 272 秒 · host-b · a1b2c3d4")
        self.assertEqual(events.footer({}, 3, tail=events.footer_tail("host-b", None)), "-# 3 秒 · host-b",
                         "no turn id: the machine alone (#release)")
        self.assertEqual(events.posted_ids(["text\n-# Read×1 · 2 秒 · host-b · a1b2c3d4", "-# 3 秒", "x · 0123abcd"]),
                         {"a1b2c3d4", "0123abcd"})


class Replay(unittest.TestCase):
    def make(self, history, transcript_text, host=None, remote_out=None):
        b = types.SimpleNamespace(user=types.SimpleNamespace(id=1), sent=[])
        b.replay_one = types.MethodType(main.Bot.replay_one, b)
        b.replay_all = types.MethodType(main.Bot.replay_all, b)
        b.claim_turn = types.MethodType(main.Bot.claim_turn, b)
        b.REPLAY_BACK, b.REPLAY_READ = main.Bot.REPLAY_BACK, main.Bot.REPLAY_READ

        async def hist(limit=50):
            for m in history:
                yield m

        async def send(text):
            b.sent.append(text)
        ch = types.SimpleNamespace(history=hist, send=send)
        b.get_channel = lambda i: ch
        home = tempfile.mkdtemp()
        folder = os.path.join(home, "projects", "-w")
        os.makedirs(folder)
        with open(os.path.join(folder, "s1.jsonl"), "w") as f:
            f.write(transcript_text)
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": home})
        self.env.start()
        self.addCleanup(self.env.stop)
        return b

    def msg(self, author, content, minutes):
        return types.SimpleNamespace(author=types.SimpleNamespace(id=author), content=content, created_at=T0 + datetime.timedelta(minutes=minutes))

    def test_posts_only_what_is_not_there(self):
        history = [self.msg(1, "first\n-# 3 秒 · host-a · aaaaaaaa", 1), self.msg(42, "the owner", 2)]
        b = self.make(history, rows(("aaaaaaaa-1", "one", "first", 0, True), ("bbbbbbbb-2", "two", "second **reply**", 3, True)))
        st = {"session_id": "s1", "cwd": "/w", "tmux": "dc-x", "kind": "conversation"}
        with mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            n = asyncio.run(b.replay_one("11", st))
        self.assertEqual(n, 1)
        self.assertEqual(b.sent, ["second **reply**\n-# 換機器時補貼 · host-b · bbbbbbbb"])

    def test_the_whole_window_is_read(self):
        """Back to REPLAY_BACK before the last reply, not a fixed 50 messages (#code-review-2 on 43bfe53)."""
        history = [self.msg(1, "x\n-# 1 秒", 30 - i * 0.1) for i in range(80)] + [self.msg(1, "first\n-# host-a · aaaaaaaa", 21)]
        b = self.make(history, rows(("aaaaaaaa-1", "one", "first", 20, True)))
        with mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            self.assertEqual(asyncio.run(b.replay_one("11", {"session_id": "s1", "cwd": "/w"})), 0, "found 81 messages back")

    def test_a_print_turn_is_not_posted_again(self):
        """#code-review on 43bfe53 (manager): a print-mode reply's footer carries the turn's id too."""
        b = self.make([], rows(("cccccccc-3", "q", "print reply", 0, True)))
        path = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "-w", "s1.jsonl")
        self.assertEqual(transcript.last_begun(path), "cccccccc-3")
        b.lease = object()
        with mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            tail = main.Bot.print_tail(b, "11", {"cwd": "/w"}, "s1")
        self.assertEqual(tail, " · host-b · cccccccc", "the print path's footer end")
        self.assertFalse(b.claim_turn("11", "cccccccc-3"), "claimed as it was posted")
        history = [self.msg(1, "print reply\n-# 3 秒 · $0.01" + tail, 2)]
        b2 = self.make(history, rows(("cccccccc-3", "q", "print reply", 0, True)))
        with mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            self.assertEqual(asyncio.run(b2.replay_one("11", {"session_id": "s1", "cwd": "/w"})), 0)

    def test_nothing_without_ids(self):
        b = self.make([self.msg(1, "first\n-# 3 秒", 1)], rows(("bbbbbbbb-2", "two", "second", 3, True)))
        with mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            self.assertEqual(asyncio.run(b.replay_one("11", {"session_id": "s1", "cwd": "/w"})), 0)
        self.assertEqual(b.sent, [])

    def test_another_machines_by_dcremote(self):
        b = self.make([self.msg(1, "x\n-# host-a · aaaaaaaa", 30)], "")
        calls = []

        async def dcremote(host, *args, **kw):
            calls.append((host,) + args)
            return 0, json.dumps([{"pid": "aaaaaaaa-1", "final": "old"}, {"pid": "cccccccc-3", "final": "new"}])
        st = {"session_id": "s9", "cwd": "/c", "home": {"host": "host-c", "socket": "/c"}}
        with mock.patch.object(main.remote, "dcremote", dcremote), mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            self.assertEqual(asyncio.run(b.replay_one("11", st)), 1)
        self.assertEqual(calls[0][:4], ("host-c", "replies", "/c", "s9"))
        self.assertEqual(calls[0][4], (T0 + datetime.timedelta(minutes=20)).isoformat(), "10 minutes before the last reply")
        self.assertTrue(b.sent[0].startswith("new\n-# 換機器時補貼"))


class Asks(unittest.TestCase):
    """The questions waiting go along with the state (their buttons are found by custom_id in these files), each
    taken in through ask.save()."""

    def setUp(self):
        from bot import ask
        self.ask = ask
        self.mine = tempfile.mkdtemp()
        p = mock.patch.object(ask, "ASKS", os.path.join(self.mine, "asks"))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(ask.awaiting, "note", mock.Mock())
        self.note = p.start()
        self.addCleanup(p.stop)

    def push(self, questions):
        root = os.path.join(tempfile.mkdtemp(), "state-standby")
        os.makedirs(os.path.join(root, "asks"))
        for name, st in questions.items():
            with open(os.path.join(root, "asks", name + ".json"), "w") as f:
                json.dump(st, f)
        return root

    def test_taken_in_with_where_each_answer_goes(self):
        self.ask.save("stale", {"channel": "9"})
        root = self.push({"a": {"channel": "1"}, "b": {"channel": "2", "host": "host-b"}, "c": {"channel": "3", "host": "host-c"}})
        self.note.reset_mock()
        self.assertEqual(self.ask.take_from(root, "host-b", "host-a"), 3)
        self.assertEqual(self.ask.ids(), ["a", "b", "c"], "one the push does not have: let go of")
        self.assertEqual((self.ask.read("a").get("host"), self.ask.read("b").get("host"), self.ask.read("c").get("host")),
                         ("host-a", None, "host-c"))
        self.assertTrue(self.note.called, "through save(): noted")

    def test_the_bot_takes_them_at_a_takeover(self):
        root = self.push({"a": {"channel": "1"}})
        b = types.SimpleNamespace()
        with mock.patch.object(main.config, "ROOT", os.path.dirname(root)), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            main.Bot.take_questions(b, "host-a")
        self.assertEqual(self.ask.read("a")["host"], "host-a")

    def test_an_empty_folder_says_none(self):
        """#code-review on 43bfe53 (manager): the folder is always sent; empty, it says there are none (the old ones
        here are let go of), which is not "nothing known"."""
        self.ask.save("stale", {"channel": "9"})
        state = tempfile.mkdtemp()
        target = os.path.join(tempfile.mkdtemp(), "state-standby")
        with mock.patch.object(self.ask, "ASKS", os.path.join(tempfile.mkdtemp(), "asks")):
            text = standby.bundle(state, {"at": 1}, self.ask.bundled(), [self.ask.folder_name()])
        standby.unpack(text, target)
        self.assertTrue(os.path.isdir(os.path.join(target, "asks")))
        self.assertEqual(self.ask.take_from(target, "host-b", "host-a"), 0)
        self.assertEqual(self.ask.ids(), [], "none over there: none here")

    def test_nothing_without_a_pushed_folder(self):
        self.ask.save("mine", {"channel": "9"})
        self.assertEqual(self.ask.take_from(tempfile.mkdtemp(), "host-b", "host-a"), 0)
        self.assertEqual(self.ask.ids(), ["mine"])

    def test_bundled_and_counted(self):
        self.ask.save("q1", {"channel": "1"})
        state = tempfile.mkdtemp()
        target = os.path.join(tempfile.mkdtemp(), "state-standby")
        standby.unpack(standby.bundle(state, {"at": 1}, self.ask.bundled()), target)
        self.assertTrue(os.path.isfile(os.path.join(target, "asks", "q1.json")))
        self.assertNotEqual(standby.what_matters({}, ["q1"]), standby.what_matters({}, []))


class Lows(unittest.TestCase):
    def test_a_cancelled_call_leaves_no_ssh_behind(self):
        """#code-review on 7377217 (manager): remote._once kills and reaps what it started when it is cancelled."""
        started = []
        real = asyncio.create_subprocess_exec

        async def spawn(*argv, **kw):
            p = await real("sleep", "30", **kw)
            started.append(p)
            return p

        async def go():
            with mock.patch.object(remote, "argv", lambda host, command, alias=None: ["sleep", "30"]), \
                    mock.patch.object(remote.asyncio, "create_subprocess_exec", spawn):
                task = asyncio.ensure_future(remote._once("host-x", "x", "true", None, 60))
                await asyncio.sleep(0.3)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertIsNotNone(started[0].returncode, "killed and reaped")
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
