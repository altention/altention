"""Opening a conversation (3b step 2.4b): the command line, the settings file, the permission modes."""
import json
import os
import shutil
import tempfile
import unittest

from altention.connectors.claude_code import hooks, launch


class Command(unittest.TestCase):
    def test_it_is_what_the_bot_wrote_before(self):
        """The two lines the bot built itself until this step, word for word."""
        for resume in (None, "saved-1"):
            tail = ["--resume", resume] if resume else []
            self.assertEqual(launch.command("/x/claude", "NOTE", "opus", "bypassPermissions", settings="/s/file.json", resume=resume),
                             ["/x/claude", "--model", "opus", "--settings", "/s/file.json",
                              "--permission-mode", "bypassPermissions", "--append-system-prompt", "NOTE"] + tail)
            self.assertEqual(launch.command("claude", "NOTE", "opus", "bypassPermissions", resume=resume),
                             ["claude", "--model", "opus", "--permission-mode", "bypassPermissions",
                              "--append-system-prompt", "NOTE"] + tail)

    def test_model_and_permission_are_what_the_caller_says(self):
        for mode in launch.permissions():
            cmd = launch.command("claude", "n", "some-model", mode)
            self.assertEqual((cmd[cmd.index("--model") + 1], cmd[cmd.index("--permission-mode") + 1]), ("some-model", mode))

    def test_a_permission_mode_there_is_not_is_refused(self):
        for mode in ("", None, "bypass", "BYPASSPERMISSIONS", "yolo", "default"):
            with self.assertRaises(ValueError, msg=repr(mode)):
                launch.command("claude", "n", "opus", mode)

    def test_the_modes_as_the_program_lists_them(self):
        self.assertEqual(sorted(launch.permissions()), sorted(["acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"]))
        self.assertIn(launch.BYPASS, launch.permissions())


class Settings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "settings.json")

    def test_what_it_holds(self):
        self.assertEqual(launch.settings(["Read(x)"], "'/h/dchook'"),
                         {"permissions": {"deny": ["Read(x)"]}, "hooks": hooks.hooks("'/h/dchook'")})

    def test_written_as_the_bot_wrote_it(self):
        self.assertTrue(launch.write_settings(self.path, ["Read(x)"], "'/h/dchook'"))
        with open(self.path) as f:
            text = f.read()
        self.assertEqual(text, json.dumps({"permissions": {"deny": ["Read(x)"]}, "hooks": hooks.hooks("'/h/dchook'")}, indent=1))
        self.assertEqual(os.listdir(self.dir), ["settings.json"], "nothing left beside it")

    def test_one_that_says_the_same_is_left_as_it_is(self):
        launch.write_settings(self.path, ["Read(x)"], "'/h/dchook'")
        os.utime(self.path, (1000, 1000))
        before = os.stat(self.path)
        self.assertFalse(launch.write_settings(self.path, ["Read(x)"], "'/h/dchook'"))
        after = os.stat(self.path)
        self.assertEqual((after.st_ino, after.st_mtime), (before.st_ino, before.st_mtime))

    def test_one_that_says_otherwise_is_written_again(self):
        launch.write_settings(self.path, ["Read(x)"], "'/h/dchook'")
        for what, change in (("another rule of the same length", lambda: launch.write_settings(self.path, ["Read(y)"], "'/h/dchook'")),
                             ("another hook", lambda: launch.write_settings(self.path, ["Read(y)"], "'/h/other'"))):
            self.assertTrue(change(), what)
        for what, text in (("cut short", "{"), ("empty", ""), ("not text", b"\xff\xfe\x00")):
            with open(self.path, "wb") as f:
                f.write(text if isinstance(text, bytes) else text.encode())
            self.assertTrue(launch.write_settings(self.path, ["Read(x)"], "'/h/dchook'"), what)
            with open(self.path) as f:
                self.assertEqual(json.load(f)["permissions"], {"deny": ["Read(x)"]}, what)


if __name__ == "__main__":
    unittest.main()
