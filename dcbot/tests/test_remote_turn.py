"""📋 on a reply of a Claude on another machine (stage 3b step 1.10): the record, `dcremote turn` over there, the
turn sent back as data and worded here, and what the owner is told when it cannot be had."""
import asyncio
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import transcript
from altention.tests.connectors.claude_code import replay
from altention.tests.connectors.claude_code.test_turns import played, turns_of
from bot import process, remote, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "dcremote")


def line(kind, content, **extra):
    return json.dumps(dict({"type": kind, "message": {"role": kind, "content": content}}, **extra), ensure_ascii=False) + "\n"


def over_the_wire(items):
    """As `dcremote turn` sends a turn and the bot reads it: made small, through JSON (tuples come back as lists)."""
    return json.loads(json.dumps(transcript.slim(items), ensure_ascii=False))


class SameAsHere(unittest.TestCase):
    def test_every_turn_of_the_recorded_runs_reads_the_same_by_both_roads(self):
        seen = 0
        for version in replay.VERSIONS:
            for name in replay.names(version):
                _events, rows, path, tmp, _pos = played(version, name, "piled")
                with tmp:
                    for start in sorted(turns_of(rows).values()):
                        items = transcript.turn(path, start, transcript.turn_end(path, start))
                        self.assertEqual(process.pieces(over_the_wire(items)), process.pieces(items), (version, name, start))
                        seen += len(items)
        self.assertGreater(seen, 100)

    def test_a_long_output_is_cut_there_and_still_says_how_long_it_was(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            long, blocks = "  " + "字" * 9000 + "\n", [{"type": "text", "text": "a" * 3000}, {"type": "image"}, {"type": "text", "text": "b" * 3000}]
            with open(path, "w") as f:
                f.write(line("user", "run them") + line("assistant", [{"type": "tool_use", "name": "Bash", "input": {"command": "x" * 5000}}])
                        + line("user", [{"type": "tool_result", "content": long}]) + line("user", [{"type": "tool_result", "content": blocks, "is_error": True}])
                        + line("user", [{"type": "tool_result", "content": ""}]) + line("user", [{"type": "tool_result", "content": "short ```x```"}])
                        + line("assistant", [{"type": "text", "text": "s" * 5000}]))
            items = transcript.turn(path, 0, None)
            sent = over_the_wire(items)
            self.assertEqual(process.pieces(sent), process.pieces(items))
            self.assertIn("（還有 7800 字，對這則按 📋 接著看）", process.pieces(sent)[2])
            self.assertIn("（還有 4801 字，對這則按 📋 接著看）", process.pieces(sent)[3])
            self.assertTrue(process.pieces(sent)[3].startswith("❌\n```"))
            self.assertEqual([len(i[1]["content"]) for i in sent if i[0] == "result"], [4000, 4000, 0, 13], "cut over there")
            self.assertLess(len(json.dumps(sent)), len(json.dumps(items)) - 7000)

    def test_a_turn_too_long_to_send_says_how_much_is_left_out(self):
        items = [("asked", "go")] + [("said", "x" * 1000)] * 50
        sent = json.loads(json.dumps(transcript.slim(items, total=10500)))
        self.assertEqual((len(sent), sent[-1]), (12, ["more", 40]), "what fits whole (the message and ten of them), then the count")
        self.assertEqual(process.pieces(sent)[-1], "…（這一輪太長，後面還有 40 項沒有傳回來）")
        self.assertEqual(process.pieces(sent)[:11], process.pieces(items)[:11])
        self.assertEqual(transcript.slim(items), items, "within the limit: as it was")


class TheCommand(unittest.TestCase):
    """tools/dcremote turn, run as the bot runs it over SSH: the system Python, another folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = os.path.join(self.tmp.name, "claude")
        self.folder = os.path.join(self.config, "projects", "-w")
        os.makedirs(self.folder)
        self.path = os.path.join(self.folder, "s.jsonl")
        self.first = line("user", "one", promptId="p1") + line("assistant", [{"type": "text", "text": "reply one"}])
        with open(self.path, "w") as f:
            f.write(self.first + line("user", "two", promptId="p2")
                    + line("assistant", [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}])
                    + line("user", [{"type": "tool_result", "content": "a\nb"}]) + line("assistant", [{"type": "text", "text": "reply two"}]))
        self.outside = os.path.join(self.tmp.name, "secret.jsonl")
        with open(self.outside, "w") as f:
            f.write(line("user", "not a conversation of Claude Code's"))

    def run_(self, *args):
        env = {"PATH": "/usr/bin:/bin", "HOME": self.tmp.name, "CLAUDE_CONFIG_DIR": self.config,
               "DCBOT_ENV": os.path.join(self.tmp.name, "none"), "TMUX_TMPDIR": self.tmp.name}
        r = subprocess.run(["/usr/bin/python3", TOOL, "turn"] + list(args), cwd="/", env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        return r.returncode, r.stdout, r.stderr

    def test_a_turn_by_its_start_and_its_id(self):
        code, out, err = self.run_(self.path, "0", "p1")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), [["asked", "one"], ["said", "reply one"]], "to where the next turn begins")
        second = len(self.first.encode())
        code, out, err = self.run_(self.path, str(second), "p2")
        self.assertEqual(json.loads(out), [["asked", "two"], ["tool", "Bash", {"command": "ls"}],
                                           ["result", {"content": "a\nb", "is_error": False, "chars": 3}], ["said", "reply two"]])
        self.assertEqual(out.count("\n"), 1, "one line")
        self.assertEqual(json.loads(self.run_(self.path, "0")[1]), [["asked", "one"], ["said", "reply one"]], "no id given: the first one met")
        self.assertEqual(json.loads(self.run_(self.path, "0", "")[1]), [["asked", "one"], ["said", "reply one"]])

    def test_a_file_that_is_gone_is_null(self):
        self.assertEqual(self.run_(os.path.join(self.folder, "gone.jsonl"), "0", "p1"), (0, "null\n", ""))

    def test_only_a_conversation_of_claude_codes_own_is_read(self):
        """It reads a file for whoever can run it: nothing but a .jsonl under Claude Code's projects folder, links
        resolved. Exit code 3, nothing on stdout."""
        link = os.path.join(self.folder, "link.jsonl")
        os.symlink(self.outside, link)
        other = os.path.join(self.folder, "notes.txt")
        with open(other, "w") as f:
            f.write(line("user", "x"))
        for path in (self.outside, link, other, os.path.join(self.folder, "..", "..", "..", "secret.jsonl"),
                     os.path.join(self.config, "projects-else", "s.jsonl"), "/etc/passwd", ""):
            code, out, err = self.run_(path, "0", "p1")
            self.assertEqual((code, out), (3, ""), path)
            self.assertIn("不讀", err)
        for start in ("x", "-1", "1.5", ""):
            code, out, err = self.run_(self.path, start, "p1")
            self.assertEqual((code, out), (3, ""), start)
        code, out, err = self.run_(self.path)
        self.assertEqual((code, out), (1, ""), "too few words: as a command it does not have")

    def test_a_failure_to_read_is_not_taken_for_a_command_it_does_not_have(self):
        """Exit code 4 and one line, not a traceback with exit code 1 (which the bot reads as "a step behind")."""
        copy = os.path.join(self.tmp.name, "engine")
        import shutil
        shutil.copytree(os.path.join(ROOT, "bot"), os.path.join(copy, "dcbot", "bot"))
        os.makedirs(os.path.join(copy, "dcbot", "tools"))
        shutil.copy(TOOL, os.path.join(copy, "dcbot", "tools", "dcremote"))
        shutil.copytree(os.path.join(os.path.dirname(ROOT), "altention"), os.path.join(copy, "altention"),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))
        with open(os.path.join(copy, "altention", "connectors", "claude_code", "transcript.py"), "a") as f:
            f.write("\n\ndef slim(items, **kw):\n    raise RuntimeError('cannot')\n")
        env = {"PATH": "/usr/bin:/bin", "HOME": self.tmp.name, "CLAUDE_CONFIG_DIR": self.config,
               "DCBOT_ENV": os.path.join(self.tmp.name, "none"), "TMUX_TMPDIR": self.tmp.name}
        r = subprocess.run(["/usr/bin/python3", os.path.join(copy, "dcbot", "tools", "dcremote"), "turn", self.path, "0", "p1"], cwd="/",
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout, r.stderr.count("\n")), (4, "", 1), r.stderr)
        self.assertIn("讀不出來（RuntimeError: cannot）", r.stderr)

    def test_nothing_is_written(self):
        before = sorted(os.listdir(self.folder)), os.path.getsize(self.path), os.path.getmtime(self.path)
        self.run_(self.path, "0", "p1")
        self.assertEqual((sorted(os.listdir(self.folder)), os.path.getsize(self.path), os.path.getmtime(self.path)), before)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "state", "turn")), "no state of its own")

class ThePartCommand(TheCommand):
    """tools/dcremote part (stage 3b step 2.3b), run as the bot runs it over SSH: a stretch of one field of one item of
    the turn `turn` would give, by the same file rule and the same exit codes."""

    def part(self, *args):
        env = {"PATH": "/usr/bin:/bin", "HOME": self.tmp.name, "CLAUDE_CONFIG_DIR": self.config,
               "DCBOT_ENV": os.path.join(self.tmp.name, "none"), "TMUX_TMPDIR": self.tmp.name}
        r = subprocess.run(["/usr/bin/python3", TOOL, "part"] + list(args), cwd="/", env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        return r.returncode, r.stdout, r.stderr

    def test_a_stretch_of_a_field(self):
        start = str(len(self.first.encode("utf-8")))
        code, out, err = self.part(self.path, start, "p2", "2", "text", "0", "2")
        self.assertEqual((code, err, json.loads(out)), (0, "", {"text": "a\n", "total": 3, "next": 2, "prose": False, "fence": None, "open": None, "of": {"kind": "result", "tool": "Bash", "last": False}}))
        code, out, err = self.part(self.path, start, "p2", "1", "command", "1", "10000")
        self.assertEqual((code, json.loads(out)["text"]), (0, "s"))
        code, out, err = self.part(self.path, "0", "p1", "1", "text", "0", "99999")
        self.assertEqual((code, json.loads(out)["text"]), (0, "reply one"), "by the turn's own id: not past its end")
        code, out, err = self.part(self.path, "0", "", "1", "text", "6")
        self.assertEqual((code, json.loads(out)["text"]), (0, "one"), "without a length: ten thousand at most")

    def test_what_is_not_there_is_null(self):
        for args in ((self.path, "0", "p1", "7", "text", "0"), (self.path, "0", "p1", "0", "command", "0"), (os.path.join(self.folder, "gone.jsonl"), "0", "", "0", "text", "0")):
            code, out, err = self.part(*args)
            self.assertEqual((code, out.strip(), err), (0, "null", ""), args)

    def test_only_a_transcript_of_claude_codes_and_only_numbers(self):
        for args in ((self.outside, "0", "", "0", "text", "0"), (self.path, "x", "", "0", "text", "0"), (self.path, "0", "", "-1", "text", "0"),
                     (self.path, "0", "", "0", "text", "y"), (self.path, "0", "", "0", "text", "0", "-3")):
            code, out, err = self.part(*args)
            self.assertEqual((code, out), (3, ""), args)
            self.assertTrue(err.startswith("dcremote："), err)
        code, out, err = self.part(self.path, "0", "p1")
        self.assertEqual(code, 1, "too few arguments: as a command it does not have")

    def test_no_more_than_twenty_thousand_characters_in_one_go(self):
        with open(self.path, "a") as f:
            f.write(line("user", "three", promptId="p3") + line("assistant", [{"type": "text", "text": "z" * 50000}]))
        start = str(os.path.getsize(self.path) - len((line("user", "three", promptId="p3") + line("assistant", [{"type": "text", "text": "z" * 50000}])).encode()))
        code, out, err = self.part(self.path, start, "p3", "1", "text", "0", "999999")
        got = json.loads(out)
        self.assertEqual((code, len(got["text"]), got["total"], got["next"]), (0, 20000, 50000, 20000))


for inherited in [n for n in dir(TheCommand) if n.startswith("test_") and n not in ThePartCommand.__dict__]:
    setattr(ThePartCommand, inherited, None)  # (TheCommand's world, not its tests a second time)


class Asking(unittest.TestCase):
    ST = {"turns": {"7": ["/there/s.jsonl", 120, None, None]}, "turn_ids": {"7": "p9"}, "turn_hosts": {"7": "lap"}}

    def ask(self, code, out):
        calls = []

        async def dcremote(host, *args, **kw):
            calls.append((host, args, kw))
            return code, out
        with mock.patch.object(remote, "dcremote", dcremote):
            got = asyncio.run(process.remote_pieces("lap", self.ST, 7))
        self.assertEqual(calls, [("lap", ("turn", "/there/s.jsonl", "120", "p9"), {"timeout": 20})])
        return got

    def test_what_comes_back(self):
        self.assertEqual(self.ask(0, json.dumps([["asked", "hi"], ["said", "yo"]])), (["🧑 **你**\nhi", "💬 yo"], None))
        self.assertEqual(self.ask(0, "[]"), ([], None))
        self.assertEqual(self.ask(0, "null\n"), (None, None), "gone over there: as a transcript gone here")
        self.assertEqual(self.ask(255, ""), (None, process.UNREACHABLE), "no way to it, or it took too long")
        self.assertEqual(self.ask(1, ""), (None, process.BEHIND), "a dcremote of before this step: no such command")
        self.assertEqual(self.ask(3, ""), (None, process.UNREADABLE))
        self.assertEqual(self.ask(4, ""), (None, process.UNREADABLE), "it could not read it over there")
        for odd in ("", "Traceback…", '{"a": 1}', '[["asked"]]', "[1, 2]", '"text"'):
            self.assertEqual(self.ask(0, odd), (None, process.UNREADABLE), odd)
        with mock.patch.object(process.traceback, "print_exc"):
            for shaped_wrong in ('[["result", "not a block"]]', '[["result", {"content": "x", "chars": "many"}]]', '[["asked", 5]]',
                                 '[["tool", "Bash"]]', '[["said", null]]'):
                got = self.ask(0, shaped_wrong)
                self.assertTrue(got == (None, process.UNREADABLE) or (isinstance(got[0], list) and got[1] is None), shaped_wrong)
            self.assertEqual(self.ask(0, '[["result", "not a block"]]'), (None, process.UNREADABLE))
            self.assertEqual(self.ask(0, '[["tool", "Bash"]]'), (None, process.UNREADABLE))

    def test_a_record_without_an_id_asks_without_one(self):
        st = {"turns": {"7": ["/there/s.jsonl", 0, None, None]}}
        calls = []

        async def dcremote(host, *args, **kw):
            calls.append(args)
            return 0, "null"
        with mock.patch.object(remote, "dcremote", dcremote):
            asyncio.run(process.remote_pieces("lap", st, 7))
        self.assertEqual(calls, [("turn", "/there/s.jsonl", "0", "")])


class Thread(object):
    def __init__(self):
        self.id, self.sent = 900, []

    async def send(self, text, **kw):
        self.sent.append(text)


class Channel(object):
    def __init__(self):
        self.sent, self.thread = [], None

    async def send(self, text, **kw):
        self.sent.append((text, kw.get("delete_after")))

    async def fetch_message(self, mid):
        channel = self

        class Message(object):
            async def create_thread(self, name, **kw):
                channel.thread = Thread()
                channel.thread.name = name
                return channel.thread
        return Message()


class Bot(object):
    def __init__(self, st):
        self.channels_state, self.ch, self.saved = {"5": st}, Channel(), 0

    def get_channel(self, cid):
        return self.ch if cid == 5 else None

    def save(self):
        self.saved += 1


class Shown(unittest.TestCase):
    def show(self, st, answer, mid=7):
        async def dcremote(host, *args, **kw):
            return answer
        the_bot = Bot(st)
        with mock.patch.object(remote, "dcremote", dcremote):
            asyncio.run(process.show(the_bot, mock.Mock(channel_id=5, message_id=mid)))
        return the_bot

    def record(self):
        return {"turns": {"7": ["/there/s.jsonl", 120, None, None]}, "turn_ids": {"7": "p9"}, "turn_hosts": {"7": "lap"}}

    def test_the_turn_of_another_machine_in_a_thread(self):
        st = self.record()
        b = self.show(st, (0, json.dumps([["asked", "整理 notes"], ["tool", "Bash", {"command": "ls"}], ["said", "好了"]])))
        self.assertEqual(b.ch.thread.name, "📋 整理 notes")
        self.assertEqual("\n".join(b.ch.thread.sent), "🧑 **你**\n整理 notes\n🔧 **Bash**\n```\nls\n```\n💬 好了")
        self.assertEqual((st["turns"]["7"][3], b.saved, b.ch.sent), (900, 1, []))

    def test_what_the_owner_is_told_when_it_cannot_be_had(self):
        for answer, words in (((255, ""), "📋 這一輪在 lap 上，現在連不上，等它回來再按一次。"),
                              ((1, ""), "📋 這一輪在 lap 上，那台的程式還沒更新到能讀它，稍後再按一次。"),
                              ((0, "garbage"), "📋 這一輪在 lap 上，那台回來的內容讀不出來。"),
                              ((0, "null"), "📋 這一輪的對話紀錄已經不在了。")):
            st = self.record()
            b = self.show(st, answer)
            self.assertEqual(b.ch.sent, [(words, 15)], "a message that goes away by itself")
            self.assertEqual((st, b.ch.thread, b.saved), (self.record(), None, 0), "the record stays as it was: asked again later")

    def test_a_turn_of_this_machine_asks_no_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.jsonl")
            with open(path, "w") as f:
                f.write(line("user", "here", promptId="p1") + line("assistant", [{"type": "text", "text": "yes"}]))
            st = {"turns": {"7": [path, 0, None, None]}, "turn_ids": {"7": "p1"}}
            b = self.show(st, (255, ""))  # were it asked, it would say "cannot be reached"
            self.assertEqual("\n".join(b.ch.thread.sent), "🧑 **你**\nhere\n💬 yes")


class Remembered(unittest.TestCase):
    def test_the_machine_is_kept_beside_the_record_and_goes_with_it(self):
        st = {}
        process.remember(st, [1, 2], "/here.jsonl", 10, None, "p1")
        self.assertNotIn("turn_hosts", st, "a channel of this machine: nothing new in its state")
        process.remember(st, [3], "/there.jsonl", 20, None, "p2", host="lap")
        self.assertEqual((st["turns"]["3"], st["turn_hosts"], st["turn_ids"]["3"]), (["/there.jsonl", 20, None, None], {"3": "lap"}, "p2"))
        with mock.patch.object(process, "KEEP", 2):
            process.remember(st, [4, 5], "/there.jsonl", 30, None, "p3", host="lap")
        self.assertEqual((sorted(st["turns"]), st["turn_hosts"], sorted(st["turn_ids"])), (["4", "5"], {"4": "lap", "5": "lap"}, ["4", "5"]))


class Filled(unittest.TestCase):
    """The bridge's fill() for an event of another machine: where the turn begins is what that machine's dcremote
    worked out; nothing is read here."""

    def fill(self, rt, ev):
        bridge = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        with mock.patch.object(tmux_bridge.cc_hooks, "enrich", side_effect=AssertionError("read here")):
            bridge.fill(rt, ev)
        return rt.where

    def test_where_the_turn_begins_as_that_machine_says(self):
        rt = mock.Mock(where=None, turn=None, pos={})
        ev = {"host": "lap", "transcript_path": "/there/s.jsonl", "dc_turn_start": 120, "dc_turn_sure": True}
        self.assertEqual(self.fill(rt, ev), ("/there/s.jsonl", 120))
        rt.turn = mock.Mock(src=("/there/s.jsonl", 100))
        self.assertEqual(self.fill(rt, dict(ev, dc_turn_start=140)), ("/there/s.jsonl", 140))
        self.assertEqual(rt.turn.src, ("/there/s.jsonl", 140), "the running turn's too, as for this machine")
        self.assertIsNone(self.fill(rt, dict(ev, dc_turn_start=None)), "not found over there: no 📋")
        self.assertEqual(rt.turn.src, (None, 0))

    def test_a_machine_whose_engine_does_not_say(self):
        rt = mock.Mock(where=("/there/s.jsonl", 120), turn=None, pos={})
        self.assertIsNone(self.fill(rt, {"host": "lap", "transcript_path": "/there/s.jsonl"}))
        rt.where = ("/there/s.jsonl", 120)
        self.assertEqual(self.fill(rt, {"host": "lap"}), ("/there/s.jsonl", 120), "an event that names no file changes nothing")


if __name__ == "__main__":
    unittest.main()
