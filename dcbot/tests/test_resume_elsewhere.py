"""`!resume` in a channel whose conversation is on another machine (#issues 35): this machine's folder of the same
path was listed, and a conversation of it "switched to". Now the other machine is asked (`dcremote sessions`), and
the list, the choice and the history are worded as for a conversation here."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import bot  # noqa: F401
from altention.connectors.claude_code import sessions as cc_sessions
from bot import config, history, remote, sessions
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A, B, OLD = "0f6e1c1e-1111-4222-8333-444455556666", "1a2b3c4d-1111-4222-8333-444455556666", "9e8d7c6b-1111-4222-8333-444455556666"


def said_of(sid, n, title=""):
    return {"id": sid, "mtime": 1790000000.0 + n, "title": title, "first": "問題 %d" % n, "last": "最後 %d" % n, "prompts": 2,
            "path": "/far/.claude/projects/-far-proj/%s.jsonl" % sid}


def row(kind, content, **more):
    return json.dumps(dict({"type": kind, "timestamp": "2026-10-13T11:07:13.524Z", "message": {"role": kind, "content": content}}, **more),
                      ensure_ascii=False, separators=(",", ":")) + "\n"


class Tool(unittest.TestCase):
    """tools/dcremote sessions, run as the bot runs it (a process of its own), in a folder of its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conf, self.work = os.path.join(self.tmp.name, "conf"), os.path.join(self.tmp.name, "work", "proj")
        os.makedirs(self.work)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.conf}):
            self.folder = cc_sessions.project_dir(self.work)
        os.makedirs(self.folder)
        for n, sid in enumerate((A, B), 1):
            with open(os.path.join(self.folder, sid + ".jsonl"), "w", encoding="utf-8") as f:
                f.write(row("user", "第 %d 個對話的第一句 %s" % (n, "很長" * 60)) + row("assistant", [{"type": "text", "text": "好。"}])
                        + row("user", "它的最後一句   有   空白\n和換行 %s" % ("長" * 80)))
            os.utime(os.path.join(self.folder, sid + ".jsonl"), (1790000000 + n, 1790000000 + n))
        with open(os.path.join(self.folder, "empty.jsonl"), "w", encoding="utf-8") as f:
            f.write(row("assistant", [{"type": "text", "text": "nobody asked"}]))

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args, cwd="/"):
        env = dict((k, v) for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE"))
        env.update({"HOME": os.path.join(self.tmp.name, "home"), "CLAUDE_CONFIG_DIR": self.conf, "DCBOT_ENV": os.path.join(self.tmp.name, "none"),
                    "TMUX_TMPDIR": self.tmp.name})
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "dcremote")] + list(args), env=env, cwd=cwd,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        return r.returncode, r.stdout, r.stderr

    def test_the_folders_conversations_newest_first_with_their_files(self):
        code, out, err = self.run_tool("sessions", self.work)
        self.assertEqual((code, err), (0, ""))
        got = json.loads(out)
        self.assertEqual([s["id"] for s in got], [B, A], "the one written last first; a file nobody typed in is left out")
        self.assertEqual([s["path"] for s in got], [os.path.join(self.folder, sid + ".jsonl") for sid in (B, A)])
        self.assertTrue(all(history.a_session(s) for s in got), "what the bot holds an answer to")
        self.assertEqual(got[0]["prompts"], 2)

    def test_no_more_of_what_was_said_than_the_list_shows(self):
        got = json.loads(self.run_tool("sessions", self.work)[1])[0]
        self.assertEqual((len(got["first"]), len(got["last"])), (41, 51), "40 and 50 characters and the mark that it was cut")
        self.assertTrue(got["last"].startswith("它的最後一句 有 空白 和換行 "), "one line, as the list shows it")
        self.assertEqual(sessions._short(got["first"], 40), got["first"], "the bot's own cut leaves it as it is")

    def test_one_by_the_start_of_its_id(self):
        code, out, _ = self.run_tool("sessions", self.work, A[:8])
        self.assertEqual((code, json.loads(out)["id"], json.loads(out)["path"]), (0, A, os.path.join(self.folder, A + ".jsonl")))
        for ref in ("ffffffff", A[:4], "empty"):
            self.assertEqual(self.run_tool("sessions", self.work, ref)[:2], (0, "null\n"), ref)

    def test_only_a_folder_that_is_there_and_nothing_is_written(self):
        before = sorted(os.listdir(self.folder))
        for folder in (os.path.join(self.tmp.name, "none"), os.path.join(self.folder, A + ".jsonl"), ""):
            code, out, err = self.run_tool("sessions", folder) if folder else self.run_tool("sessions", "/nonexistent-folder-x")
            self.assertEqual((code, out), (3, ""), folder)
            self.assertIn("不列", err)
        self.assertEqual(self.run_tool("sessions")[0], 1, "no folder named: not this command")
        elsewhere = os.path.join(self.tmp.name, "work", "other")
        os.makedirs(elsewhere)
        self.assertEqual(self.run_tool("sessions", elsewhere)[:2], (0, "[]\n"), "a folder Claude Code keeps nothing for")
        self.assertEqual(sorted(os.listdir(self.folder)), before)

    def test_no_folder_named_is_not_the_folder_it_was_started_in(self):
        for folder in ("", "  "):
            code, out, err = self.run_tool("sessions", folder, cwd=self.work)
            self.assertEqual((code, out), (3, ""), repr(folder))
            self.assertIn("不列", err)
            self.assertEqual(self.run_tool("sessions", folder, A[:8], cwd=self.work)[:2], (3, ""), repr(folder))
        self.assertEqual(len(json.loads(self.run_tool("sessions", ".", cwd=self.work)[1])), 2, "a folder named from where it is: as ever")

    def test_the_file_it_names_is_one_history_reads(self):
        path = json.loads(self.run_tool("sessions", self.work, B[:8])[1])["path"]
        code, out, _ = self.run_tool("history", path)
        self.assertEqual((code, len(json.loads(out)["turns"])), (0, 2))


class Elsewhere(unittest.TestCase):
    def setUp(self):
        self.state, config.STATE = config.STATE, tempfile.mkdtemp()
        self.addCleanup(setattr, config, "STATE", self.state)
        from bot import main as botmain
        self.main = botmain
        self.bot = botmain.Bot()
        self.closed, self.shown, self.asked, self.looked = [], [], [], []

        async def nothing(*a, **k):
            return None

        async def close(ch, kill=True):
            self.closed.append(ch.id)
        self.bot.audit = nothing
        self.bot.tmux.close = close
        self.bot.save = lambda: None
        self.items = [said_of(B, 2, "第二個"), said_of(A, 1)]
        self.answer = None   # (code, stdout, stderr) for the next calls; None: the list, or the one found
        self.st = {"cwd": "/far/proj", "session_id": A, "home": {"host": "lap", "socket": "/s", "session": "work", "window": "w"},
                   "pane": {"host": "lap", "socket": "/s", "id": "%1"}}

    def ask(self, text="!resume", st=None):
        class Ch(object):
            id, name, category = 7, "c", None
        self.ch = Ch()
        self.bot.channels_state["7"] = self.st if st is None else st
        said = []

        async def send(body, view=None):
            said.append(body)

        async def dcremote(host, *args, input=None, timeout=30, err=None):
            self.asked.append((host, args, timeout))
            if self.answer is not None:
                code, out, stderr = self.answer
                if stderr and err is not None:
                    err.append(stderr)
                return code, out
            if len(args) > 2:
                hit = [s for s in self.items + [said_of(OLD, 0)] if s["id"].startswith(args[2])]
                return 0, json.dumps(hit[0] if hit else None)
            return 0, json.dumps(self.items)

        async def show_remote(the_bot, ch, st, host, sid, path, force=False):
            self.shown.append((host, sid, path, force))
            return True

        async def go():
            with mock.patch.object(remote, "dcremote", dcremote), mock.patch.object(history, "show_remote", show_remote), \
                    mock.patch.object(self.main.cc_sessions, "recent", lambda *a, **k: self.looked.append(a) or []), \
                    mock.patch.object(self.main.cc_sessions, "find", lambda *a, **k: self.looked.append(a)):
                await self.bot.command(self.ch, text, send)
                await asyncio.sleep(0)
        asyncio.run(go())
        return said

    def test_the_list_is_that_machines_in_the_words_of_this_machines(self):
        (text,) = self.ask()
        self.assertEqual(self.asked, [("lap", ("sessions", "/far/proj"), 20)])
        self.assertEqual(self.looked, [], "nothing is looked up in this machine's folder")
        self.assertEqual(text, sessions.listing(self.items, A, [], []), "the very text a list of these would be here")
        self.assertIn("`2` %s · 2 則 · `0f6e1c1e`　← 目前" % time.strftime("%m-%d %H:%M", time.localtime(1790000001.0)), text)
        self.assertEqual((self.closed, self.shown, self.st["session_id"]), ([], [], A))

    def test_by_number_and_by_id_the_window_is_closed_and_the_history_asked_of_that_machine(self):
        for ref in ("1", B[:8]):
            self.setUp()
            (text,) = self.ask("!resume " + ref)
            self.assertEqual(text, "已切到對話 `1a2b3c4d`（第二個）。下一則訊息會接回它；原本的對話一樣可以用 `!resume` 換回來。", ref)
            self.assertEqual((self.closed, self.st["session_id"]), ([7], B), ref)
            self.assertEqual(self.shown, [("lap", B, said_of(B, 2)["path"], False)], "by the file that machine named")
            self.assertIn(A, json.dumps(self.st), "the one it was on can be taken up again")
            self.assertEqual(self.looked, [])

    def test_one_older_than_the_list_shows_is_found_by_its_id(self):
        (text,) = self.ask("!resume " + OLD[:8])
        self.assertEqual([a[1] for a in self.asked], [("sessions", "/far/proj"), ("sessions", "/far/proj", OLD[:8])])
        self.assertTrue(text.startswith("已切到對話 `9e8d7c6b`（問題 0）"), text)
        self.assertEqual(self.st["session_id"], OLD)

    def test_a_ref_never_falls_to_this_machines_folder(self):
        """#code-review on 02708ed: a ref too short to be asked of that machine fell through to this machine's
        find(), which only happened to refuse it for the same length. Whatever this machine's folder holds and
        whatever its find() would answer: nothing of it is looked at, and nothing found."""
        here = said_of("abcd1234-1111-4222-8333-444455556666", 5, "這台的對話")
        for ref in ("abcd", "abcd123", "abcd1234", "abcd1234-1111"):
            self.setUp()
            asked_here = []

            def find(cwd, wanted):
                asked_here.append(wanted)
                return here
            def ask(text):
                with mock.patch.object(self.main.cc_sessions, "find", find):
                    class Ch(object):
                        id, name, category = 7, "c", None
                    self.bot.channels_state["7"] = self.st
                    said = []

                    async def send(body, view=None):
                        said.append(body)

                    async def dcremote(host, *args, input=None, timeout=30, err=None):
                        self.asked.append(args)
                        return 0, json.dumps(None if len(args) > 2 else self.items)
                    with mock.patch.object(remote, "dcremote", dcremote):
                        asyncio.run(self.bot.command(Ch(), text, send))
                    return said
            self.assertEqual(ask("!resume " + ref), ["找不到 `%s`。先用 `!resume` 看清單。" % ref], ref)
            self.assertEqual(asked_here, [], ref)
            self.assertEqual((self.closed, self.st["session_id"]), ([], A), ref)
            self.assertEqual(len(self.asked), 2 if len(ref) >= 8 else 1, "that machine is asked for it only when the ref can name one")

    def test_one_at_a_time_for_a_channel(self):
        """#code-review on 02708ed: while the first waits for that machine, a second `!resume` of the channel is told to
        wait: it closes no window and switches nothing. Afterwards the channel takes one again; another channel is
        not held up meanwhile."""
        class Ch(object):
            id, name, category = 7, "c", None

        class Other(object):
            id, name, category = 8, "d", None
        self.bot.channels_state["7"] = self.st
        self.bot.channels_state["8"] = {"cwd": "/far/other", "session_id": "x", "home": {"host": "lap"}, "pane": {"host": "lap", "id": "%2"}}
        said = {"first": [], "second": [], "other": [], "later": []}

        async def go():
            release = asyncio.Event()

            async def dcremote(host, *args, input=None, timeout=30, err=None):
                if args[1] == "/far/proj" and not release.is_set():
                    await release.wait()
                return 0, json.dumps(self.items if args[1] == "/far/proj" else [])

            async def show_remote(*a, **k):
                return True

            def sender(key):
                async def send(body, view=None):
                    said[key].append(body)
                return send
            with mock.patch.object(remote, "dcremote", dcremote), mock.patch.object(history, "show_remote", show_remote):
                first = asyncio.ensure_future(self.bot.command(Ch(), "!resume 1", sender("first")))
                await asyncio.sleep(0.05)
                await self.bot.command(Ch(), "!resume 2", sender("second"))
                await self.bot.command(Other(), "!resume", sender("other"))
                self.assertEqual((self.closed, self.st["session_id"]), ([], A), "nothing has happened yet")
                release.set()
                await first
                await self.bot.command(Ch(), "!resume", sender("later"))
        asyncio.run(go())
        self.assertEqual(said["second"], ["這個頻道正在接回另一個對話，等它完成再下一次 `!resume`。"])
        self.assertEqual(said["other"], ["這個工作目錄還沒有可以接回的對話。"], "another channel is not held up")
        self.assertTrue(said["first"][0].startswith("已切到對話 `1a2b3c4d`"))
        self.assertEqual((self.closed, self.st["session_id"]), ([7], B), "closed once, by the first")
        self.assertIn("這個工作目錄最近的對話", said["later"][0])

    def test_the_mark_is_taken_off_whatever_happens(self):
        async def broken(host, *args, input=None, timeout=30, err=None):
            raise RuntimeError("x")

        class Ch(object):
            id, name, category = 7, "c", None
        self.bot.channels_state["7"] = self.st

        async def send(body, view=None):
            pass
        with mock.patch.object(remote, "dcremote", broken):
            with self.assertRaises(RuntimeError):
                asyncio.run(self.bot.resume(Ch(), self.st, ""))
        self.assertEqual(self.bot.resuming, set())
        self.assertEqual(self.ask("!resume 2"), ["這個頻道已經在 `0f6e1c1e` 這個對話上。"])

    def test_what_cannot_be_switched_to_is_said_as_here(self):
        self.assertEqual(self.ask("!resume 2"), ["這個頻道已經在 `0f6e1c1e` 這個對話上。"])
        self.assertEqual(self.ask("!resume 9"), ["找不到 `9`。先用 `!resume` 看清單。"])
        self.assertEqual(self.ask("!resume ffffffff"), ["找不到 `ffffffff`。先用 `!resume` 看清單。"])
        self.bot.channels_state["8"] = {"session_id": B}
        self.assertEqual(self.ask("!resume 1"), ["`1a2b3c4d` 正被另一個頻道使用，同一個對話不能同時開兩處。"])
        self.assertEqual((self.closed, self.shown, self.st["session_id"]), ([], [], A))

    def test_a_machine_that_cannot_say_leaves_the_channel_as_it_is(self):
        for answer, words in (((255, "", ""), "現在連不上"), ((1, "", "dcremote：不認得 sessions"), "那台的程式還沒更新到能讀它"),
                              ((1, "", "ImportError: x"), "那台的 dcremote 跑不起來（結束碼 1：ImportError: x）"),
                              ((3, "", "dcremote：沒有這個資料夾，不列"), "那台不給讀或讀不出來（結束碼 3：dcremote：沒有這個資料夾，不列）"),
                              ((0, "{not json", ""), "那台回來的內容讀不出來"), ((0, json.dumps({"id": A}), ""), "那台回來的內容讀不出來"),
                              ((0, json.dumps([{"id": A, "mtime": "x"}]), ""), "那台回來的內容讀不出來")):
            for text in ("!resume", "!resume 1"):
                self.setUp()
                self.answer = answer
                self.assertEqual(self.ask(text), ["這個頻道的對話在 lap 上，%s，所以列不出那台的對話、也沒有換對話。" % words], (answer, text))
                self.assertEqual((self.closed, self.shown, self.st["session_id"], self.looked), ([], [], A, []))

    def test_an_answer_is_held_to_its_shape(self):
        self.assertTrue(history.a_session(said_of(A, 1)))
        for odd in (None, [], "x", {}, dict(said_of(A, 1), id=""), dict(said_of(A, 1), prompts="2"), dict(said_of(A, 1), mtime=True),
                    {k: v for k, v in said_of(A, 1).items() if k != "path"}, dict(said_of(A, 1), title=None)):
            self.assertFalse(history.a_session(odd), odd)

    def test_a_channel_of_this_machine_lists_as_before(self):
        for st in ({"cwd": "/w/proj", "tmux": "dc-c-7"}, {"cwd": "/w/proj", "home": {"socket": "/s", "session": "work", "window": "w"}, "pane": {"socket": "/s", "id": "%1"}}):
            self.setUp()
            said = self.ask(st=dict(st))
            self.assertEqual((len(self.looked), self.asked), (1, []), st)
            self.assertEqual(said, ["這個工作目錄還沒有可以接回的對話。"])

    def test_which_machine(self):
        self.assertIsNone(sessions.elsewhere({}))
        self.assertIsNone(sessions.elsewhere({"home": {"session": "w"}, "pane": {"id": "%1"}}))
        self.assertEqual(sessions.elsewhere({"home": {"host": "lap"}}), "lap")
        self.assertEqual(sessions.elsewhere({"pane": {"host": "lap"}}), "lap")


class ClosedByTheBot(unittest.TestCase):
    """#code-review-2 on 02708ed: after `!resume <one>` in a channel whose window is on another machine, the channel
    was told "ℹ️ 這個頻道的 Claude 結束了（tmux 視窗被關掉了？）…". The bot had closed the window itself; the window's
    end comes after, by that machine's route table, and was taken for an end nobody asked for. So it was for every
    way the bot closes such a window (!reset, !cwd, !mode, an idle one put down). The event now says its pane, and
    is told from the channel's window as on this machine."""
    ENDED = "ℹ️ 這個頻道的 Claude 結束了"
    PANE = {"socket": "/tmp/tmux-1/default", "id": "%5", "pid": "77", "host": "lap"}
    SAYS = {"dc_pane": {"socket": "/tmp/tmux-1/default", "pane": "%5"}, "dc_routed": True}

    def setUp(self):
        from tests.test_turn_end import Ch, Host
        from bot import tmux_bridge
        self.tb = tmux_bridge
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.bridge.last_reap = 1e12
        self.st = self.bridge.host.chan(self.ch)
        self.st.pop("tmux", None)
        self.st.update({"cwd": "/far/proj", "session_id": A, "pane": dict(self.PANE),
                        "home": {"socket": "/tmp/tmux-1/default", "session": "work", "window": "w", "host": "lap"}})
        self.killed = []

    def play(self, *steps):
        async def kill_pane(pane):
            self.killed.append(pane.get("id"))

        async def go():
            with mock.patch.object(self.tb.tmuxio, "kill_pane", kill_pane), mock.patch.object(self.bridge, "watch", lambda *a, **k: None):
                for step in steps:
                    if step == "close":
                        await self.bridge.close(self.ch)
                    else:
                        await self.bridge.handle(dict(step, channel=str(self.ch.id), host="lap"))
                    await asyncio.sleep(0)
        asyncio.run(go())
        return [m.content for m in self.ch.sent]

    def end(self, **more):
        return dict({"hook_event_name": "SessionEnd", "reason": "other", "session_id": A}, **more)

    def test_the_end_of_a_window_the_bot_closed_is_not_said(self):
        self.assertEqual(self.play("close", self.end(**self.SAYS)), [], "!reset, !cwd, !mode, an idle window put down: close() and nothing else")
        self.assertEqual(self.killed, ["%5"])
        self.assertEqual(self.st["session_id"], A, "and nothing of the channel is touched by it")

    def test_nor_after_resume_gave_the_channel_another_conversation(self):
        self.play("close")
        self.st["session_id"] = B   # as bot.resume() does after closing
        self.assertEqual(self.play(self.end(**self.SAYS)), [])
        self.assertEqual(self.st["session_id"], B)

    def test_nor_when_another_window_is_open_for_the_channel_by_then(self):
        self.play("close")
        self.st["pane"] = dict(self.PANE, id="%9")
        self.assertEqual(self.play(self.end(**self.SAYS)), [])

    def opening(self):
        self.bridge.rt(self.ch).opening = True
        return self.bridge.rt(self.ch)

    def test_nor_when_the_next_window_is_being_opened_as_the_end_comes(self):
        """#code-review-2 on 16da7f6: a message 0.2 s after the close. The next window is being opened (which pane
        it will be is not known yet, so every pane's events count as the channel's) when the closed window's end
        comes: two runs of three said it."""
        self.play("close")
        self.opening()
        self.assertEqual(self.play(self.end(**self.SAYS)), [])
        self.assertEqual(self.bridge.rt(self.ch).closed[0]["session"], A, "kept with the conversation it had")

    def turn(self, sid, answer="新視窗的回答。"):
        return [dict({"hook_event_name": "UserPromptSubmit", "prompt": "hi", "session_id": sid, "prompt_id": "p"}, **self.SAYS),
                dict({"hook_event_name": "Stop", "last_assistant_message": answer, "session_id": sid, "prompt_id": "p"}, **self.SAYS)]

    def test_the_new_window_may_bear_the_closed_ones_number(self):
        """Closing the last window of a tmux server ends the server; the new window's first pane has the same number
        again. What tells them apart is the conversation: the closed window's end bears the id the channel had in
        it. In whichever order the two come."""
        start = dict({"hook_event_name": "SessionStart", "source": "resume", "session_id": B, "transcript_path": "/far/x.jsonl"}, **self.SAYS)
        for order in ("the end first", "the start first"):
            self.setUp()
            self.play("close")
            self.st["session_id"] = B
            self.opening()
            late = self.end(**self.SAYS)
            with mock.patch.object(history, "show_remote", mock.AsyncMock(return_value=True)):
                said = self.play(*([late, start] if order == "the end first" else [start, late]) + self.turn(B))
            self.assertTrue(self.bridge.rt(self.ch).ready.is_set(), order + ": the new window's start is the channel's")
            self.assertTrue(any(text.startswith("新視窗的回答。") for text in said), (order, said))
            self.assertFalse(any(text.startswith(self.ENDED) for text in said), (order, said))
            self.assertEqual(self.st["session_id"], B, order)

    def test_a_new_window_whose_start_never_came(self):
        """#code-review on ddf02aa: on another machine the new window's SessionStart may be sent before the route is
        written, and is then never spooled. Same number, the first turn within the minute: all of it was taken for
        the closed window's (nothing posted, the channel left on the old id)."""
        self.play("close")
        self.opening()
        said = self.play(*self.turn(B))
        self.assertTrue(any("hi" in text for text in said), said)
        self.assertTrue(any(text.startswith("新視窗的回答。") for text in said), said)
        self.assertEqual(self.st["session_id"], B)
        self.assertEqual(self.play(self.end(**self.SAYS)), said, "and the closed window's end, late, is still not said")

    def test_the_same_conversation_taken_up_again_in_the_same_pane(self):
        """!mode, !cwd back, an idle window opened again: the new window resumes the id the closed one had. Its
        start, when it comes, says so; when it never comes, the window being known open does."""
        resumed = dict({"hook_event_name": "SessionStart", "source": "resume", "session_id": A}, **self.SAYS)
        for how in ("its start came", "its start never came"):
            self.setUp()
            self.play("close")
            rt = self.opening()
            self.assertEqual(self.play(self.end(**self.SAYS)), [], how)
            if how == "its start came":
                self.play(resumed)
            self.assertEqual(len(rt.closed), 0 if how == "its start came" else 1, how)
            self.st["pane"] = dict(self.PANE)   # as ensure_home()'s kept() does
            rt.opening = None
            self.tb.opened(self.st, rt)
            self.assertEqual(rt.closed, [], how)
            said = self.play(*self.turn(A))
            self.assertTrue(any(text.startswith("新視窗的回答。") for text in said), (how, said))

    def test_only_the_end_is_the_closed_windows(self):
        """2.6c (manager 2026-10-14): never a turn swallowed. The low item of 14b8c85: the same number, the same
        conversation taken up again, its SessionStart never spooled, and a turn that comes before the window is
        known open (somebody typing at that terminal in those seconds): it was not posted. A closed window is
        only known to send its SessionEnd after it (48 of 48 closed in the middle of a turn, #code-review-2); that
        one event is what the record holds back."""
        self.play("close")
        rt = self.opening()
        said = self.play(*self.turn(A))
        self.assertTrue(any("hi" in text for text in said), said)
        self.assertTrue(any(text.startswith("新視窗的回答。") for text in said), said)
        self.assertEqual(len(rt.closed), 1, "the record stays for the end that may still come")
        self.assertEqual(self.play(self.end(**self.SAYS)), said, "and that end, late, is still not said")

    def test_every_other_kind_of_event_from_that_pane_is_handled(self):
        from bot import tmux_bridge
        for name in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "StopFailure", "SubagentStop", "Notification",
                     "SessionStart", "PreCompact", "a kind nobody knows"):
            ev = dict({"hook_event_name": name, "session_id": A}, **self.SAYS)
            closed = [dict(self.PANE, at=__import__("time").time(), session=A)]
            self.assertFalse(tmux_bridge.from_elsewhere(dict(self.st, pane=None), True, dict(ev, host="lap"), closed), name)
        closed = [dict(self.PANE, at=__import__("time").time(), session=A)]
        end = dict({"hook_event_name": "SessionEnd", "session_id": A, "host": "lap"}, **self.SAYS)
        self.assertTrue(tmux_bridge.from_elsewhere(dict(self.st, pane=None), True, end, closed))
        self.assertFalse(tmux_bridge.from_elsewhere(dict(self.st, pane=None), True, dict(end, session_id=B), closed), "another conversation's end")

    def test_open_forgets_it_only_for_the_same_pane_and_conversation(self):
        for pane, sid, left in ((dict(self.PANE), A, 0), (dict(self.PANE, id="%9"), A, 1), (dict(self.PANE), B, 1),
                                (dict(self.PANE, host="other"), A, 1), (dict(self.PANE, socket="/tmp/tmux-1/else"), A, 1)):
            self.setUp()
            self.play("close")
            self.st.update({"pane": pane, "session_id": sid})
            self.tb.opened(self.st, self.bridge.rt(self.ch))
            self.assertEqual(len(self.bridge.rt(self.ch).closed), left, (pane, sid))

    def test_open_is_told_when_a_window_is_ready(self):
        self.play("close")
        rt = self.bridge.rt(self.ch)

        async def start(resume, how):
            return self.tb.tmuxio.pane(dict(self.PANE))

        async def kept(pane, fresh):
            self.st["pane"] = dict(self.PANE)

        async def go():
            with mock.patch.object(self.bridge, "wait_ready", mock.AsyncMock(return_value=True)):
                return await self.bridge.open(self.ch, self.st, rt, start, kept, mock.AsyncMock())
        self.assertTrue(asyncio.run(go()))
        self.assertEqual(rt.closed, [])

    def test_what_cannot_be_told_is_handled_as_it_always_was(self):
        """No id in the event, or none the channel had when the window was closed: not judged by the closed window
        (rather an end said once too often than a turn swallowed)."""
        self.play("close")
        self.opening()
        said = self.play(dict({"hook_event_name": "SessionEnd", "reason": "other"}, **self.SAYS))
        self.assertEqual(len([text for text in said if text.startswith(self.ENDED)]), 1, said)
        self.setUp()
        self.st.pop("session_id")
        self.play("close")
        self.assertEqual(self.bridge.rt(self.ch).closed, [], "nothing to tell it by: not kept")
        self.assertEqual(self.killed, ["%5"])

    def test_a_window_on_this_machine_alike(self):
        """The same line of the same rule: a window the bot opened in the owner's tmux here says its pane by its own
        hook (no route table). Closed, and its end coming while the next is being opened: not said."""
        here = {"socket": "/tmp/tmux-1/default", "id": "%5", "pid": "77"}
        self.st.update({"pane": dict(here), "home": {"socket": "/tmp/tmux-1/default", "session": "work", "window": "w"}})

        async def kill_pane(pane):
            self.killed.append(pane.get("id"))

        async def go(opening):
            with mock.patch.object(self.tb.tmuxio, "kill_pane", kill_pane), mock.patch.object(self.bridge, "watch", lambda *a, **k: None):
                await self.bridge.close(self.ch)
                self.bridge.rt(self.ch).opening = opening
                await self.bridge.handle(dict(self.end(dc_pane={"socket": "/tmp/tmux-1/default", "pane": "%5"}), channel=str(self.ch.id)))
        for opening in (None, True):
            self.st["pane"] = dict(here)
            self.ch.sent.clear()
            asyncio.run(go(opening))
            self.assertEqual([m.content for m in self.ch.sent], [], opening)

    def test_a_closed_window_is_forgotten_after_a_while(self):
        self.play("close")
        self.bridge.rt(self.ch).closed[0]["at"] -= self.tb.CLOSED_SECS + 1
        self.opening()
        said = self.play(self.end(**self.SAYS))
        self.assertEqual(len(said), 1, "nothing of it came in time: an end from that number is anybody's again")
        self.assertEqual(self.bridge.rt(self.ch).closed, [])

    def test_an_end_nobody_asked_for_is_said_as_before(self):
        said = self.play(self.end(**self.SAYS))
        self.assertEqual(len(said), 1)
        self.assertTrue(said[0].startswith(self.ENDED), said)

    def test_the_windows_own_events_go_on_as_before(self):
        said = self.play(dict({"hook_event_name": "UserPromptSubmit", "prompt": "hi", "session_id": A, "prompt_id": "p"}, **self.SAYS),
                         dict({"hook_event_name": "Stop", "last_assistant_message": "回答。", "session_id": A, "prompt_id": "p"}, **self.SAYS))
        self.assertTrue(any(text.startswith("回答。") for text in said), said)

    def test_a_hook_of_before_this_on_that_machine_leaves_things_as_they_were(self):
        """The four mixes of old and new: only a new hook there says the pane. An old one: the sentence, as today
        (no worse); a new hook with a bot of before this: the two fields are fields it never read."""
        said = self.play("close", self.end())
        self.assertEqual(len(said), 1, "nothing to judge by: said, as it always was")
        self.assertTrue(said[0].startswith(self.ENDED))

    def test_the_two_fields_come_through_the_relay_as_they_are(self):
        import importlib.machinery
        import importlib.util
        import io
        loader = importlib.machinery.SourceFileLoader("dcremote_relay", os.path.join(ROOT, "tools", "dcremote"))
        tool = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcremote_relay", loader))
        loader.exec_module(tool)
        with tempfile.TemporaryDirectory() as spool:
            with open(os.path.join(spool, "%020d-1.json" % 1), "w") as f:
                json.dump(dict(self.end(**self.SAYS), channel="7", ts=1.0), f)
            out = io.StringIO()
            self.assertEqual(tool.relay(spool, {}, out), 1)
            got = json.loads(out.getvalue().splitlines()[0])
        self.assertEqual((got["dc_pane"], got["dc_routed"]), (self.SAYS["dc_pane"], True))


if __name__ == "__main__":
    unittest.main()
