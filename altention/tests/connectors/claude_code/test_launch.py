"""Opening a conversation (3b step 2.4b): the command line, the settings file, the permission modes."""
import contextlib
import io
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

    def test_folders_to_read_go_in_the_settings_never_by_add_dir(self):
        """Claude Code 2.1.326 asks whether to trust a folder given by --add-dir (a channel waits on it); one in the
        settings it reads without asking. With no settings file: --settings carries the folders alone, as JSON."""
        cmd = launch.command("claude", "NOTE", "opus", "auto", resume="r", dirs=["/a/5", "/b c/5"])
        self.assertEqual(cmd, ["claude", "--model", "opus", "--settings",
                               '{"permissions":{"additionalDirectories":["/a/5","/b c/5"]}}', "--permission-mode", "auto",
                               "--append-system-prompt", "NOTE", "--resume", "r"])
        self.assertNotIn("--add-dir", launch.command("claude", "NOTE", "opus", "auto"))
        self.assertNotIn("--settings", launch.command("claude", "NOTE", "opus", "auto"), "no folders, no file: nothing")

    def test_with_a_settings_file_the_folders_go_in_a_copy_of_it(self):
        """Only the last --settings counts (tried on 2.1.326): one --settings, a copy of the file with the folders,
        the file's rules and hooks kept; the file itself untouched."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        base = os.path.join(tmp, "claude-settings.json")
        launch.write_settings(base, ["Read(x)"], "'/h/dchook'")
        with open(base) as f:
            before = f.read()
        cmd = launch.command("claude", "NOTE", "opus", "auto", settings=base, dirs=["/a/5"])
        self.assertNotIn("--add-dir", cmd)
        self.assertEqual(cmd.count("--settings"), 1)
        copy = cmd[cmd.index("--settings") + 1]
        self.assertNotEqual(copy, base)
        self.assertEqual(os.path.dirname(copy), tmp)
        with open(copy) as f:
            got = json.load(f)
        self.assertEqual(got, {"permissions": {"deny": ["Read(x)"], "additionalDirectories": ["/a/5"]},
                               "hooks": hooks.hooks("'/h/dchook'")})
        with open(base) as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(launch.command("claude", "NOTE", "opus", "auto", settings=base, dirs=["/a/5"]), cmd, "the same copy again")
        other = launch.command("claude", "NOTE", "opus", "auto", settings=base, dirs=["/a/6"])
        self.assertNotEqual(other[other.index("--settings") + 1], copy, "another folder, another copy")
        self.assertEqual(launch.command("claude", "NOTE", "opus", "auto", settings=base),
                         ["claude", "--model", "opus", "--settings", base, "--permission-mode", "auto", "--append-system-prompt", "NOTE"])

    def test_the_copy_follows_the_file(self):
        """Written from the file as it is now each time: a rule changed in the file is in the copy at the next opening."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        base = os.path.join(tmp, "s.json")
        launch.write_settings(base, ["Read(x)"], "'/h/dchook'")
        copy = launch.with_folders(base, ["/a/5"])
        launch.write_settings(base, ["Read(y)"], "'/h/dchook'")
        self.assertEqual(launch.with_folders(base, ["/a/5"]), copy)
        with open(copy) as f:
            self.assertEqual(json.load(f)["permissions"]["deny"], ["Read(y)"])

    def test_a_file_that_cannot_be_read_opens_without_the_folders(self):
        """As when the folder cannot be made (attachments.folders): opened all the same, the file as given; said."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        for what, text in (("missing", None), ("not JSON", "{"), ("not settings", "[1]")):
            base = os.path.join(tmp, "s-%d.json" % len(what))
            if text is not None:
                with open(base, "w") as f:
                    f.write(text)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cmd = launch.command("claude", "NOTE", "opus", "auto", settings=base, dirs=["/a/5"])
            self.assertEqual(cmd[cmd.index("--settings") + 1], base, what)
            self.assertIn("opened without them", out.getvalue(), what)

    def test_a_permission_mode_there_is_not_is_refused(self):
        for mode in ("", None, "bypass", "BYPASSPERMISSIONS", "yolo", "default"):
            with self.assertRaises(ValueError, msg=repr(mode)):
                launch.command("claude", "n", "opus", mode)

    def test_the_modes_as_the_program_lists_them(self):
        self.assertEqual(sorted(launch.permissions()), sorted(["acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"]))
        self.assertIn("bypassPermissions", launch.permissions())
        self.assertFalse(hasattr(launch, "BYPASS"))  # C-121: no last resort for a caller to fall back on


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
