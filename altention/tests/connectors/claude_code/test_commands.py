"""What Claude Code says it can be asked with a "/", and its skills' descriptions (commands.py). The test of the
descriptions came from the bot's test_catalog as it was (stage 3b step 1.9); those of asking are new: until then
nothing ran that function but the bot itself."""
import asyncio
import json
import os
import stat
import tempfile
import time
import unittest

from altention.connectors.claude_code import commands


class Asking(unittest.TestCase):
    """A stand-in for `claude`: a script that writes what it was started with and prints what the test gives it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def claude(self, body):
        path = os.path.join(self.tmp.name, "claude")
        with open(path, "w") as f:
            f.write("#!/bin/sh\necho \"$@\" > %s/args\necho \"$MARK|$PWD\" > %s/env\necho $$ > %s/pid\n%s\n"
                    % (self.tmp.name, self.tmp.name, self.tmp.name, body))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def read(self, name):
        with open(os.path.join(self.tmp.name, name)) as f:
            return f.read().strip()

    def test_the_init_line_is_the_answer_and_the_process_is_ended_there(self):
        init = json.dumps({"type": "system", "subtype": "init", "slash_commands": ["model", "a:b"], "skills": ["a:b"]})
        claude = self.claude("echo 'not json'\necho '{\"type\":\"system\",\"subtype\":\"hook\"}'\necho '%s'\nexec sleep 30" % init)
        began = time.time()
        got = asyncio.run(commands.ask(claude, self.tmp.name, {"MARK": "given", "PATH": os.environ["PATH"]}))
        self.assertEqual(got, {"slash_commands": ["model", "a:b"], "skills": ["a:b"]})
        self.assertLess(time.time() - began, 10, "not waited for: it would go on to ask the model")
        with self.assertRaises(OSError):
            os.kill(int(self.read("pid")), 0)
        self.assertEqual(self.read("args"), "-p x --model haiku --output-format stream-json --verbose --no-session-persistence")
        self.assertEqual(self.read("env"), "given|" + os.path.realpath(self.tmp.name), "the environment and the folder it was given")

    def test_no_answer(self):
        env = {"PATH": os.environ["PATH"]}
        self.assertIsNone(asyncio.run(commands.ask(os.path.join(self.tmp.name, "missing"), self.tmp.name, env)))
        self.assertIsNone(asyncio.run(commands.ask(self.claude("echo '{\"type\":\"result\"}'"), self.tmp.name, env)), "ended without it")
        began = time.time()
        self.assertIsNone(asyncio.run(commands.ask(self.claude("exec sleep 30"), self.tmp.name, env, timeout=1)))
        self.assertLess(time.time() - began, 10)
        with self.assertRaises(OSError):
            os.kill(int(self.read("pid")), 0)
        bare = json.dumps({"type": "system", "subtype": "init"})
        self.assertEqual(asyncio.run(commands.ask(self.claude("echo '%s'" % bare), self.tmp.name, env)), {"slash_commands": [], "skills": []})


class Descriptions(unittest.TestCase):
    def test_skill_descriptions_are_read_from_front_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("CLAUDE_CONFIG_DIR")
            os.environ["CLAUDE_CONFIG_DIR"] = tmp
            try:
                for where, text in (("skills/report", "---\nname: report\ndescription: 匯報進度。\n  第二行\n---\nbody"),
                                    ("plugins/synced/x/pl/skills/thing", "---\ndescription: \"A thing\"\n---\n"),
                                    ("plugins/.trash/1/pl/skills/gone", "---\nname: gone\ndescription: old\n---\n")):
                    os.makedirs(os.path.join(tmp, where))
                    with open(os.path.join(tmp, where, "SKILL.md"), "w") as f:
                        f.write(text)
                self.assertEqual(commands.skill_descriptions("/nowhere"), {"report": "匯報進度。 第二行", "thing": "A thing"})
                project = os.path.join(tmp, "proj")
                os.makedirs(os.path.join(project, ".claude", "skills", "local"))
                with open(os.path.join(project, ".claude", "skills", "local", "SKILL.md"), "w") as f:
                    f.write("---\nname: report\ndescription: the project's own\n---\n---\nname: x\n")
                with open(os.path.join(tmp, "skills", "report", "SKILL.md"), "w") as f:
                    f.write("no front matter")
                self.assertEqual(commands.skill_descriptions(project), {"report": "the project's own", "thing": "A thing"})
            finally:
                if old is None:
                    del os.environ["CLAUDE_CONFIG_DIR"]
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = old

    def test_a_skills_name_without_its_plugin(self):
        self.assertEqual((commands.short("a:b"), commands.short("plain"), commands.short("x:y:z")), ("b", "plain", "z"))


class Harmful(unittest.TestCase):
    """Which slash commands are not to be typed in by one who is not at the terminal, and what the harm is: names and
    codes only (stage 3b step 2.1; the words are the bot's)."""

    def test_the_commands_and_their_codes(self):
        self.assertEqual(commands.REFUSED, {"/logout": "logs_out_everything"})
        self.assertEqual(commands.CONFIRM, {"/exit": "ends_conversation", "/quit": "ends_conversation", "/background": "leaves_pane",
                                            "/fork": "leaves_pane", "/teleport": "leaves_pane", "/remote-control": "leaves_pane",
                                            "/rewind": "restore_panel", "/checkpoint": "restore_panel"})
        self.assertEqual(set(commands.REFUSED) & set(commands.CONFIRM), set())
        for table in (commands.REFUSED, commands.CONFIRM):
            for name, code in table.items():
                self.assertTrue(name.startswith("/") and name == name.lower() and " " not in name, name)
                self.assertRegex(code, r"^[a-z_]+$")

    def test_the_command_a_text_begins_with(self):
        for text, command in (("/exit", "/exit"), (" /Exit now", "/exit"), ("\n/LOGOUT", "/logout"), ("/model opus", "/model"),
                              ("exit", ""), ("say /exit", ""), ("", ""), ("   ", ""), ("/", "/"), (None, ""), (5, "")):
            self.assertEqual(commands.command_of(text), command, text)

    def test_no_words_for_the_owner_in_it(self):
        with open(commands.__file__, encoding="utf-8") as f:
            tail = f.read().split("LOGS_OUT, ENDS", 1)[1]
        self.assertFalse([ch for ch in tail if "\u4e00" <= ch <= "\u9fff"], "the reasons are worded by whoever shows them")


if __name__ == "__main__":
    unittest.main()
