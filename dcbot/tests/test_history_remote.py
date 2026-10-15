"""The history of a conversation taken up with /resume in a pane on another machine (#issues 31, stage 3b step
2.4c): read there by `dcremote history`, posted here in the words an attach posts it in."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import sessions as cc_sessions
from bot import history, remote, sessions, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_turn_end import Ch, Host

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SID = "0f6e1c1e-1111-4222-8333-444455556666"


def row(kind, content, **more):
    return json.dumps(dict({"type": kind, "timestamp": "2026-10-13T11:07:13.524Z", "message": {"role": kind, "content": content}}, **more), ensure_ascii=False, separators=(",", ":")) + "\n"  # (as Claude Code writes its rows)


def conversation(turns=2):
    out = ""
    for n in range(1, turns + 1):
        out += row("user", "問題 %d" % n, promptId="p%d" % n)
        out += row("assistant", [{"type": "text", "text": "回答 %d。" % n}])
    return out


class Tool(unittest.TestCase):
    """tools/dcremote history, run as the bot runs it (a process of its own), in a folder of its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conf = os.path.join(self.tmp.name, "conf")
        self.folder = os.path.join(self.conf, "projects", "-w-proj")
        os.makedirs(self.folder)
        self.path = os.path.join(self.folder, SID + ".jsonl")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(conversation())

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args):
        env = dict((k, v) for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE"))
        env.update({"HOME": os.path.join(self.tmp.name, "home"), "CLAUDE_CONFIG_DIR": self.conf, "DCBOT_ENV": os.path.join(self.tmp.name, "none"),
                    "TMUX_TMPDIR": self.tmp.name})
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "dcremote")] + list(args), env=env, cwd="/",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        return r.returncode, r.stdout, r.stderr

    def test_the_turns_of_the_file_as_the_connector_reads_them(self):
        code, out, err = self.run_tool("history", self.path)
        self.assertEqual((code, err), (0, ""))
        got = json.loads(out)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.conf}):
            want = json.loads(json.dumps(cc_sessions.turns(self.path, None, None)))
        self.assertEqual(got, {"session_id": SID, "turns": want})
        self.assertEqual([t[1] for t in got["turns"]], ["問題 1", "問題 2"])
        self.assertEqual(json.loads(self.run_tool("history", self.path, "given-id")[1])["session_id"], "given-id")

    def test_what_attach_asks_for_answers_in_the_same_shape(self):
        """backfill PANE …: {"session_id", "turns"}; the bot words both with sessions.backfilled()."""
        got = json.loads(self.run_tool("history", self.path)[1])
        self.assertEqual(sorted(got), ["session_id", "turns"])
        self.assertEqual(len(sessions.backfilled(got)), 4, "each turn: what was asked, what was answered")

    def test_only_a_transcript_of_claude_codes_own_is_read(self):
        outside = os.path.join(self.tmp.name, "other.jsonl")
        with open(outside, "w", encoding="utf-8") as f:
            f.write(conversation())
        link = os.path.join(self.folder, "link.jsonl")
        os.symlink(outside, link)
        for path in (outside, link, "/etc/passwd", os.path.join(self.folder, "..", "..", "..", "other.jsonl"), os.path.join(self.folder, "notes.txt")):
            code, out, err = self.run_tool("history", path)
            self.assertEqual((code, out), (3, ""), path)
            self.assertIn("不讀", err)

    def test_a_file_that_is_gone_answers_null_and_nothing_is_written(self):
        before = sorted(os.listdir(self.folder))
        code, out, _ = self.run_tool("history", os.path.join(self.folder, "gone.jsonl"))
        self.assertEqual((code, json.loads(out)), (0, None))
        self.assertEqual(self.run_tool("history")[0], 1, "no file named: not this command (as an unknown one)")
        self.assertEqual(sorted(os.listdir(self.folder)), before)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "state", "none")))

    def test_rows_no_reader_expects_do_not_end_in_a_traceback(self):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("{not json\n[1, 2]\n" + row("user", 5).replace('{"role":"user","content":5}', "5") + row("assistant", [{"type": "text", "text": 7}]) + row("user", None))
        code, out, err = self.run_tool("history", self.path)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(json.loads(out)["turns"]), 2)


class Shown(unittest.TestCase):
    def setUp(self):
        self.ch = Ch()
        self.host = Host(self.ch)
        self.st = self.host.chan(self.ch)
        self.st["session_id"] = SID   # the pane's event made it the channel's before the history is asked for
        self.posted, self.asked = [], []
        self.turns = [["asked", "問題 1", "回答 1。", "2026-10-13T11:07:13.524Z"], ["asked", "問題 2", "回答 2。", "2026-10-13T11:08:13.524Z"]]

        async def backfill(ch, messages):
            self.posted.append(messages)
        self.host.backfill = backfill
        self.host.save = lambda: None

    def show(self, code=0, out=None, force=False, wait=0, stderr="", meanwhile=None):
        async def dcremote(host, *args, input=None, timeout=30, err=None):
            self.asked.append((host, args, timeout))
            if wait:
                await asyncio.sleep(wait)
            if meanwhile:
                meanwhile()
            if stderr and err is not None:
                err.append(stderr)
            return code, json.dumps({"session_id": SID, "turns": self.turns}) if out is None else out

        async def go():
            with mock.patch.object(remote, "dcremote", dcremote):
                done = await history.show_remote(self.host, self.ch, self.st, "lap", SID, "/far/projects/-w/%s.jsonl" % SID, force)
                await asyncio.sleep(0)
                return done
        return asyncio.run(go())

    def said(self):
        return [m.content for m in self.ch.sent]

    def test_the_whole_of_it_in_the_words_of_an_attach(self):
        self.assertTrue(self.show())
        self.assertEqual(self.asked, [("lap", ("history", "/far/projects/-w/%s.jsonl" % SID, SID), 20)])
        self.assertEqual(self.posted, [sessions.messages_of(self.turns)], "the very messages an attach would post of these turns")
        self.assertEqual(self.posted[0][1], "回答 1。")
        self.assertEqual(self.st["shown"], [SID])
        self.assertEqual(self.said(), [], "the rest is the backfill's own (its heading and its end)")

    def test_a_conversation_the_channel_has_shown_before_gets_its_last_turns(self):
        self.st["shown"] = [SID]
        self.turns = [["asked", "問題 %d" % n, "回答 %d。" % n, ""] for n in range(1, 6)]
        self.assertTrue(self.show())
        self.assertEqual(self.posted, [])
        (text,) = self.said()
        self.assertTrue(text.startswith("📜 **接回對話 `0f6e1c1e`**（這個頻道之前貼過它的完整紀錄），目前的最後幾輪："), text)
        self.assertEqual([n for n in range(1, 6) if "問題 %d" % n in text], [3, 4, 5])
        self.assertTrue(self.show(force=True))
        self.assertEqual(len(self.posted), 1, "asked for again by hand: the whole of it")

    def test_a_machine_that_cannot_give_it_is_said_once_and_not_asked_again(self):
        unreadable = "那台回來的內容讀不出來"
        for code, out, stderr, words in (
                (255, "", "", "現在連不上"),
                (1, "", "dcremote：不認得 history", "那台的程式還沒更新到能讀它"),
                (1, "", "ModuleNotFoundError: No module named 'altention'", "那台的 dcremote 跑不起來（結束碼 1：ModuleNotFoundError: No module named 'altention'）"),
                (1, "", "", "那台的 dcremote 跑不起來（結束碼 1）"),
                (4, "", "dcremote history：讀不出來（ValueError: x）", "那台不給讀或讀不出來（結束碼 4：dcremote history：讀不出來（ValueError: x））"),
                (3, "", "dcremote：不是這台 Claude Code 的對話檔，不讀", "那台不給讀或讀不出來（結束碼 3：dcremote：不是這台 Claude Code 的對話檔，不讀）"),
                (0, "{not json", "", unreadable), (0, json.dumps({"turns": "x"}), "", unreadable), (0, json.dumps([1]), "", unreadable),
                (0, json.dumps({"session_id": SID, "turns": [["a", "b"]]}), "", unreadable)):
            self.ch.sent.clear()
            self.asked.clear()
            self.assertFalse(self.show(code, out, stderr=stderr), (code, out))
            self.assertEqual(self.said(), ["📜 接回的對話 `0f6e1c1e` 的紀錄在 lap 上，%s，所以沒有貼出來。" % words], (code, out))
            self.assertEqual((len(self.asked), self.posted, self.st.get("shown")), (1, [], None), "asked once; not marked as shown")

    def test_a_tool_that_did_not_run_is_not_said_to_be_a_step_behind(self):
        """#code-review on 53d9fc7: exit code 1 is what a dcremote of before this command answers ("不認得 history"),
        and also what Python answers when the tool cannot start at all. Told apart by what it wrote."""
        self.show(1, "", stderr="Traceback (most recent call last): / File \"dcremote\", line 29 / ImportError: cannot import name 'x'")
        self.assertIn("跑不起來（結束碼 1：", self.said()[0])
        self.assertIn("ImportError", self.said()[0])
        self.assertNotIn("還沒更新", self.said()[0])

    def test_a_channel_that_moved_on_meanwhile_gets_nothing(self):
        """#code-review on 53d9fc7: up to 20 s go by. The channel detached, its state replaced, or its pane on another
        conversation by then: neither the history nor a word about it is posted; one line in the log."""
        import contextlib
        import io
        other = "22222222-2222-4222-8222-222222222222"
        moves = {"another conversation": lambda: self.st.update(session_id=other),
                 "put away": lambda: (self.st.pop("tmux", None), self.st.update(archived={"at": 1})),
                 "its state replaced": lambda: self.host.channels_state.update({str(self.ch.id): dict(self.st)}),
                 "deleted": lambda: self.host.channels_state.pop(str(self.ch.id))}
        for name, move in moves.items():
            for code in (0, 255):
                self.setUp()
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertFalse(self.show(code, meanwhile=move), (name, code))
                self.assertEqual((self.said(), self.posted), ([], []), (name, code))
                self.assertNotIn(SID, self.st.get("shown") or [], name)
                self.assertEqual(out.getvalue().count("history: the history of 0f6e1c1e (on lap) came after its channel"), 1, (name, code))
        self.setUp()
        self.assertTrue(self.show(meanwhile=lambda: self.st.update(archived={"at": 1})), "an old record of having been put away, and a window now: the channel is in use")
        self.assertEqual(len(self.posted), 1)

    def test_a_file_that_is_gone_or_holds_nothing_is_passed_over_without_a_word(self):
        for out in ("null", json.dumps({"session_id": SID, "turns": []})):
            self.assertFalse(self.show(0, out))
        self.assertEqual((self.said(), self.posted), ([], []))
        asyncio.run(history.show_remote(self.host, self.ch, self.st, "lap", "", ""))
        self.assertEqual(self.asked.__len__(), 2, "no file named: not asked at all")

    def test_whatever_goes_wrong_ends_in_the_log_not_in_the_air(self):
        async def broken(host, *args, input=None, timeout=30, err=None):
            raise RuntimeError("no such machine list")
        import contextlib
        import io
        err = io.StringIO()
        with mock.patch.object(remote, "dcremote", broken), contextlib.redirect_stderr(err):
            self.assertFalse(asyncio.run(history.show_remote(self.host, self.ch, self.st, "lap", SID, "/far/x.jsonl")))
        self.assertIn("RuntimeError", err.getvalue())


class NotWaitedFor(unittest.TestCase):
    """The events of a machine are handled one after the other by its stream: reading a conversation there takes as
    long as that machine takes, and nothing of it may wait behind that."""

    def test_the_event_is_handled_at_once_and_the_history_comes_when_it_comes(self):
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        bridge.last_reap = 1e12
        st = bridge.host.chan(ch)
        st.pop("tmux", None)
        st.update({"pane": {"socket": "/s", "id": "%1", "host": "lap"}, "session_id": "11111111-1111-4111-8111-111111111111", "cwd": "/w/proj"})
        posted = []

        async def backfill(c, messages):
            posted.append(messages)
        bridge.host.backfill = backfill

        async def slow(host, *args, input=None, timeout=30, err=None):
            await asyncio.sleep(0.5)
            return 0, json.dumps({"session_id": SID, "turns": [["asked", "問題", "回答。", ""]]})

        async def go():
            with mock.patch.object(remote, "dcremote", slow), mock.patch.object(bridge, "watch", lambda *a, **k: None):
                began = time.monotonic()
                await bridge.handle({"hook_event_name": "SessionStart", "source": "resume", "session_id": SID, "host": "lap",
                                     "transcript_path": "/far/projects/-w/%s.jsonl" % SID, "channel": str(ch.id), "ts": time.time()})
                took = time.monotonic() - began
                self.assertEqual(posted, [], "not yet")
                await asyncio.sleep(0.7)
                return took
        took = asyncio.run(go())
        self.assertLess(took, 0.2, "handle() did not wait for the other machine")
        self.assertEqual(st["session_id"], SID)
        self.assertEqual(posted, [sessions.messages_of([["asked", "問題", "回答。", ""]])])


if __name__ == "__main__":
    unittest.main()
