"""Where Claude Code keeps its conversations and its record of what is running (sessions.py). The tests of
project_dir, summarize, recent, live_session and of_pane came from the bot's test_sessions as they were (stage 3b
step 1.7); those of the config folder, the process records, a viewer's session, and the turns as data are new."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from altention.connectors.claude_code import sessions


def line(kind, content, **extra):
    return json.dumps(dict({"type": kind, "message": {"role": kind, "content": content}}, **extra),
                      ensure_ascii=False, separators=(",", ":")) + "\n"


def human(text):
    return line("user", text, origin={"kind": "human"})


class InAConfigFolder(unittest.TestCase):
    """Each test with a config folder of its own (CLAUDE_CONFIG_DIR), put back afterwards."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp.name

    def tearDown(self):
        if self.old is None:
            del os.environ["CLAUDE_CONFIG_DIR"]
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.old
        self.tmp.cleanup()


class Folder(InAConfigFolder):
    def test_the_config_folder_is_asked_for_each_time(self):
        self.assertEqual(sessions.config_dir(), self.tmp.name)
        del os.environ["CLAUDE_CONFIG_DIR"]
        try:
            self.assertEqual(sessions.config_dir(), os.path.expanduser("~/.claude"), "no variable: the home's own")
            os.environ["CLAUDE_CONFIG_DIR"] = ""
            self.assertEqual(sessions.config_dir(), os.path.expanduser("~/.claude"), "an empty one is none")
        finally:
            os.environ["CLAUDE_CONFIG_DIR"] = self.tmp.name

    def test_project_dir(self):
        self.assertTrue(sessions.project_dir("/work/my.proj").endswith(os.path.join("projects", "-work-my-proj")))
        self.assertTrue(sessions.project_dir("/work/my.proj").startswith(self.tmp.name))
        self.assertEqual(sessions.transcript("/work/my.proj", "abc"), os.path.join(sessions.project_dir("/work/my.proj"), "abc.jsonl"))


class Conversations(InAConfigFolder):
    def setUp(self):
        InAConfigFolder.setUp(self)
        self.dir = sessions.project_dir("/work/my.proj")
        os.makedirs(self.dir)

    def write(self, sid, text, mtime):
        path = os.path.join(self.dir, sid + ".jsonl")
        with open(path, "w") as f:
            f.write(text)
        os.utime(path, (mtime, mtime))
        return path

    def test_summarize_counts_only_what_the_user_typed(self):
        path = self.write("aaaa1111-x", human("第一句") + line("assistant", [{"type": "text", "text": "好"}])
                          + line("user", [{"type": "tool_result", "content": "out"}])
                          + line("user", "<task-notification>done</task-notification>",
                                 origin={"kind": "task-notification"})
                          + line("user", "[Request interrupted by user]")
                          + '{"type":"ai-title","aiTitle":"標題"}\n' + "not json\n" + human("最後一句"), 100)
        s = sessions.summarize(path)
        self.assertEqual((s["id"], s["prompts"], s["first"], s["last"], s["title"]),
                         ("aaaa1111-x", 2, "第一句", "最後一句", "標題"))

    def test_recent_and_find(self):
        self.write("aaaa1111-x", human("old one"), 100)
        self.write("bbbb2222-y", human("new one") + human("its last"), 200)
        self.write("cccc3333-z", line("assistant", [{"type": "text", "text": "nobody typed here"}]), 300)
        self.assertEqual([s["id"] for s in sessions.recent("/work/my.proj")], ["bbbb2222-y", "aaaa1111-x"])
        self.assertEqual([s["id"] for s in sessions.recent("/work/my.proj", limit=1)], ["bbbb2222-y"])
        self.assertEqual(sessions.recent("/nowhere"), [])
        self.assertEqual(sessions.find("/work/my.proj", "aaaa1111")["id"], "aaaa1111-x")
        self.assertIsNone(sessions.find("/work/my.proj", "aaaa"), "at least 8 characters")
        self.assertIsNone(sessions.find("/work/my.proj", "cccc3333"), "nobody typed in it")
        self.assertIsNone(sessions.find("/nowhere", "aaaa1111"))

    def test_the_live_transcript_is_found_by_what_is_on_screen(self):
        folder = sessions.project_dir("/w/p")
        os.makedirs(folder)
        for sid, text in (("started", "the conversation as it was on 09-28"), ("now", "novelty report written to remaining-fulltext.md")):
            with open(os.path.join(folder, sid + ".jsonl"), "w") as f:
                f.write(line("assistant", [{"type": "text", "text": text}]))
        rec = {"sessionId": "started", "startedAt": 0}
        screen = "● 這輪全文報告寫好了\n  novelty report written to remaining-fulltext.md\n❯ "
        self.assertEqual(sessions.live_session("/w/p", screen, rec), "now")
        self.assertEqual(sessions.live_session("/w/p", "nothing that matches here at all", rec), "started", "no match: the record")


class Running(InAConfigFolder):
    def record(self, pid, **more):
        os.makedirs(os.path.join(self.tmp.name, "sessions"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "sessions", "%s.json" % pid), "w") as f:
            json.dump(dict({"pid": pid}, **more), f)

    def test_session_of_a_pane(self):
        self.record(os.getpid(), sessionId="mine", tmux="st:@22.%25", cwd="/w", startedAt=5)
        self.record(999999999, sessionId="dead", tmux="st:@22.%25", cwd="/w", startedAt=9)    # that process is gone
        self.record(os.getppid(), sessionId="other", tmux="st:@23.%26", cwd="/w", startedAt=7)
        self.assertEqual(sessions.of_pane("%25", "st")["sessionId"], "mine")
        self.assertEqual(sessions.of_pane("%26", "st")["sessionId"], "other")
        self.assertIsNone(sessions.of_pane("%2", "st"), "%2 must not match %25")
        self.assertIsNone(sessions.of_pane("%25", "elsewhere"))

    def test_every_record_that_can_be_read(self):
        self.assertEqual(sessions.process_records(), [], "no folder: none")
        self.record(os.getpid(), name="one", sessionId="a")
        self.record(999999999, name="gone", sessionId="b")
        with open(os.path.join(self.tmp.name, "sessions", "broken.json"), "w") as f:
            f.write("{not json")
        with open(os.path.join(self.tmp.name, "sessions", "list.json"), "w") as f:
            f.write("[1, 2]")
        with open(os.path.join(self.tmp.name, "sessions", "notes.txt"), "w") as f:
            f.write("x")
        self.assertEqual(sorted(r["name"] for r in sessions.process_records()), ["gone", "one"],
                         "whether its process is still there is the caller's question")

    def test_the_process_a_conversation_was_moved_out_of(self):
        """The left key on an empty input box: the record of the process in the pane gets "parkedJobId" (the first
        eight characters of the id the conversation goes on under); the fields are those of the measured records
        (2.1.288 and 2.1.296), cut down to what is read."""
        new = "78cb859a-4150-4c2e-b027-033964dcd9b7"
        self.record(os.getpid(), sessionId="a2511139-65da-48cd-8ea8-449b387b50dd", kind="interactive", tmux="c:@1.%1", parkedJobId="78cb859a")
        self.record(os.getppid(), sessionId=new, kind="bg", jobId="78cb859a")                      # the background process itself
        self.record(999999999, sessionId="gone", kind="interactive", tmux="c:@1.%2", parkedJobId="78cb859a")  # that process is gone
        self.assertEqual([r["pid"] for r in sessions.parked(new)], [os.getpid()])
        self.assertEqual(sessions.parked("78cb859b-4150-4c2e-b027-033964dcd9b7"), [], "another conversation")
        self.assertEqual(sessions.parked("a2511139-65da-48cd-8ea8-449b387b50dd"), [], "the id it had in the pane is not the one it moved to")
        for nothing in (None, "", "78cb", 5):
            self.assertEqual(sessions.parked(nothing), [], nothing)
        self.record(os.getpid(), sessionId="a2511139-65da-48cd-8ea8-449b387b50dd", kind="interactive", tmux="c:@1.%1")
        self.assertEqual(sessions.parked(new), [], "a pane whose conversation was not moved")

    def test_the_session_a_viewer_shows(self):
        """`claude attach <id>`: the pane's process is a viewer of a background session. Looked up in the config folder
        that is in force (until this step: always ~/.claude, whatever CLAUDE_CONFIG_DIR said)."""
        self.record(1, sessionId="e7b58260-5ae2-4412-9e6f-e505208c2b16", kind="bg")
        viewer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "attach", "e7b58260"])
        plain = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            got = None
            for _ in range(50):  # the child shows its arguments once it has started
                got = sessions.attached_session(viewer.pid)
                if got:
                    break
                time.sleep(0.1)
            self.assertEqual(got, "e7b58260-5ae2-4412-9e6f-e505208c2b16")
            self.assertIsNone(sessions.attached_session(plain.pid), "not a viewer")
            os.remove(os.path.join(self.tmp.name, "sessions", "1.json"))
            self.assertEqual(sessions.attached_session(viewer.pid), "e7b58260", "no record of it: what the command line says")
        finally:
            viewer.kill()
            plain.kill()
            viewer.wait()
            plain.wait()


class Turns(unittest.TestCase):
    NOTE = "<task-notification>\n<task-id>T1</task-id>\n<status>completed</status>\n<summary>Run  B1b\n finished</summary>\n</task-notification>"

    def queued(self, prompt, mode):
        return json.dumps({"type": "attachment", "attachment": {"type": "queued_command", "prompt": prompt, "commandMode": mode},
                           "timestamp": "2026-10-02T18:00:00.000Z"}) + "\n"

    def test_what_opens_a_turn_as_a_kind_and_a_text(self):
        """No words of ours here: the bot puts them (its sessions.worded)."""
        seen = set()
        self.assertEqual(sessions.turn_start(json.loads(human(" typed ")), seen), ("asked", "typed"))
        self.assertEqual(sessions.turn_start(json.loads(self.queued(" also log it ", "prompt")), seen), ("midway", "also log it"))
        self.assertIsNone(sessions.turn_start(json.loads(self.queued("<system-reminder>x</system-reminder>", "prompt")), seen))
        self.assertEqual(sessions.turn_start(json.loads(self.queued(self.NOTE, "task-notification")), seen), ("task", "Run B1b finished"))
        self.assertIsNone(sessions.turn_start(json.loads(line("user", self.NOTE, origin={"kind": "task-notification"})), seen),
                          "the same notification, queued first and delivered later: once")
        bare = "<task-notification>\n<task-id>T2</task-id>\n</task-notification>"
        self.assertEqual(sessions.turn_start(json.loads(line("user", bare, origin={"kind": "task-notification"})), seen), ("task", None))
        for other in (line("assistant", [{"type": "text", "text": "x"}]), line("user", [{"type": "tool_result", "content": "x"}]),
                      line("user", "typed in a subagent", origin={"kind": "human"}, isSidechain=True)):
            self.assertIsNone(sessions.turn_start(json.loads(other), seen))

    def test_the_last_turns_of_a_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            say = lambda t: line("assistant", [{"type": "text", "text": t}])  # noqa: E731
            with open(path, "w") as f:
                f.write(line("user", "one", origin={"kind": "human"}, timestamp="2026-10-02T17:00:00.000Z") + say("thinking aloud")
                        + line("assistant", [{"type": "tool_use", "name": "Bash"}]) + line("user", [{"type": "tool_result", "content": "x"}])
                        + say("answer one") + line("assistant", [{"type": "text", "text": "side"}], isSidechain=True)
                        + self.queued(self.NOTE, "task-notification") + say("B1b: loss 0.12")
                        + self.queued("also log it", "prompt") + say("Logged.") + human("three"))
            self.assertEqual(sessions.turns(path, None, None),
                             [["asked", "one", "answer one", "2026-10-02T17:00:00.000Z"],
                              ["task", "Run B1b finished", "B1b: loss 0.12", "2026-10-02T18:00:00.000Z"],
                              ["midway", "also log it", "Logged.", "2026-10-02T18:00:00.000Z"],
                              ["asked", "three", "", ""]])
            self.assertEqual([t[1] for t in sessions.turns(path, 2)], ["also log it", "three"])
            self.assertEqual(sessions.turns(os.path.join(tmp, "missing")), [])
            small = sessions.turns(path, 9, tail_bytes=200)  # only the end is read; a cut first line is dropped
            self.assertEqual(small[-1][:3], ["asked", "three", ""])
            self.assertLess(len(small), 4)
            json.dumps(sessions.turns(path, None, None))  # plain data: tools/dcremote sends it as it is


class OnePass(unittest.TestCase):
    """The id and the summary of a background task's notice are found in one pass (the patterns they were,
    "<x>(.*?)</x>", took the square of the length for many openings and no closing)."""

    def test_the_same_answers_as_the_patterns_gave(self):
        import random
        import re
        rnd = random.Random(21)
        bits = ["<task-id>", "</task-id>", "<summary>", "</summary>", "\n", " ", "a", "b", "<", ">"]
        hits = 0
        for _ in range(20000):
            text = "".join(rnd.choice(bits) for _ in range(rnd.randint(0, 10)))
            for opening, closing, lines, flags in (("<task-id>", "</task-id>", False, 0), ("<summary>", "</summary>", True, re.S)):
                m = re.search(re.escape(opening) + "(.*?)" + re.escape(closing), text, flags)
                self.assertEqual(sessions._between(text, opening, closing, lines), m.group(1) if m else None, (text, opening))
                hits += m is not None
        self.assertGreater(hits, 100)

    def test_a_long_notice_of_any_shape_takes_no_time(self):
        n = 200000
        began = time.time()
        for text in ("<summary>" * (n // 9), "<task-id>" * (n // 9), "<task-id>\n" * (n // 10) + "</task-id>", "<summary>" + "x" * n,
                     ("<task-id>a\n" * 5 + "</task-id>") * (n // 65)):
            sessions._between(text, "<summary>", "</summary>")
            sessions._between(text, "<task-id>", "</task-id>", lines=False)
        self.assertLess(time.time() - began, 0.5)

    def test_an_empty_id_is_an_id(self):
        """As the pattern had it: two notices with an empty <task-id></task-id> are one and the same."""
        seen = set()
        note = {"type": "user", "origin": {"kind": "task-notification"},
                "message": {"role": "user", "content": "<task-notification>\n<task-id></task-id>\n<summary>x</summary>\n</task-notification>"}}
        self.assertEqual(sessions.turn_start(note, seen), ("task", "x"))
        self.assertIsNone(sessions.turn_start(note, seen))


if __name__ == "__main__":
    unittest.main()
