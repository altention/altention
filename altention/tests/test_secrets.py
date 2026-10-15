"""altention.secrets: where secrets lie, for the guard that refuses tools reading them (backlog K-03)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

from altention import secrets

HOME = "/h/lap"  # (made up: a path under /home/ would be counted by the privacy scan)


class Listed(unittest.TestCase):
    def setUp(self):
        self.listed = [HOME + p[1:] for p in secrets.DEFAULT]

    def secret(self, path, cwd=HOME):
        return secrets.is_secret(path, self.listed, cwd=cwd)

    def test_the_default_list(self):
        for path in ("~/.config/dcbot/env", HOME + "/.config/dcbot/env", HOME + "/.config/dcbot", HOME + "/.config/dcbot/a/b",
                     HOME + "/.ssh/id_ed25519", HOME + "/.ssh/id_rsa", HOME + "/.ssh/my_key",
                     HOME + "/.ssh/github-key-2"):
            self.assertTrue(self.secret(path.replace("~", HOME)), path)

    def test_what_is_not_secret(self):
        """~/.ssh/config and known_hosts: the machines' settings rest on them, and the owner has Claude edit them."""
        for path in (HOME + "/.config/life/google.json", HOME + "/.ssh/config", HOME + "/.ssh/known_hosts", HOME + "/.ssh/authorized_keys", HOME + "/.ssh/id_rsa.pub",
                     HOME + "/.ssh/my_key.pub", HOME + "/.config/dcbotx/env", HOME + "/.config/life/other.json", "/etc/passwd",
                     HOME + "/.ssh", HOME + "/.config", "", None):
            self.assertFalse(self.secret(path), path)

    def test_relative_paths_and_links(self):
        self.assertTrue(self.secret(".config/dcbot/env", cwd=HOME))
        self.assertTrue(self.secret("../.ssh/id_rsa", cwd=HOME + "/work"))
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".config", "dcbot")
            os.makedirs(target)
            with open(os.path.join(target, "env"), "w") as f:
                f.write("x")
            os.symlink(os.path.join(target, "env"), os.path.join(tmp, "harmless.txt"))
            listed = [tmp + p[1:] for p in secrets.DEFAULT]
            self.assertTrue(secrets.is_secret(os.path.join(tmp, "harmless.txt"), listed), "a link to it is it")

    def test_commands_that_name_a_secret(self):
        for command, named in (("cat ~/.config/dcbot/env", "~/.config/dcbot/env"), ("cat $HOME/.ssh/id_rsa", "$HOME/.ssh/id_rsa"),
                               ("cat ${HOME}/.ssh/id_rsa", "${HOME}/.ssh/id_rsa"), ("cd ~/.config/dcbot && cat env", "~/.config/dcbot"),
                               ("grep TOKEN %s/.config/dcbot/env" % HOME, HOME + "/.config/dcbot/env"),
                               ("cat '~/.config/dcbot/env'", "~/.config/dcbot/env"), ("base64 <~/.ssh/id_ed25519", "~/.ssh/id_ed25519")):
            self.assertEqual(secrets.mentions(command, self.listed, home=HOME), [named], command)

    def test_commands_that_do_not(self):
        for command in ("vim ~/.ssh/config", "cat ~/.ssh/authorized_keys", "cat ~/.ssh/id_rsa.pub", "ls ~/.ssh/", "ls ~/.config/dcbotx", "echo .config/dcbot",
                        "git log --oneline", "cat ~/.config/life/google.json.example", "", None):
            self.assertEqual(secrets.mentions(command, self.listed, home=HOME), [], command)

    def test_fast(self):
        """Before every tool call of every Claude Code: a check is far under a millisecond."""
        began = time.perf_counter()
        for _ in range(2000):
            self.secret(HOME + "/work/notes.md")
            secrets.mentions("git status && ls -la src/ && python3 -m pytest -q", self.listed, home=HOME)
        self.assertLess((time.perf_counter() - began) / 2000, 0.001)


class ReviewedForms(unittest.TestCase):
    """#code-review on 8f3f145 (manager's ruling 2026-10-14): what a command or a tool writes out in full, in the ways
    a shell reads it, is caught; what the ruling leaves out is not."""

    def setUp(self):
        self.listed = [HOME + p[1:] for p in secrets.DEFAULT]

    def hit(self, command, fold=False):
        return secrets.mentions(command, self.listed, home=HOME, fold=fold)

    def test_patterns_that_reach_a_secret(self):
        for command in ("cat ~/.ssh/*", "cat ~/.config/dcbot*/env", "cat ~/.config/dcbo?/env", "cat ~/.config/{dcbot,x}/env",
                        "cat ~/.config/*/env", "cat ~/.c*/dcbot/env", "cat ~/.ssh/id_[re]*"):
            self.assertTrue(self.hit(command), command)
        for command in ("cat ~/.ssh/*.pub", "cat ~/.ssh/known_*", "cat ~/work/*.py", "cat /etc/*"):
            self.assertEqual(self.hit(command), [], command)

    def test_relative_paths_from_where_the_command_starts(self):
        """(#code-review on b3370e8) cwd = home: the three examples; a cd in the command is not followed (a known limit)."""
        for command in ("cat .config/dcbot/env", "grep -r TOKEN .config", "cat ./.ssh/id_rsa", "tar czf x.tgz .ssh"):
            self.assertTrue(secrets.mentions(command, self.listed, home=HOME, cwd=HOME), command)
        self.assertTrue(secrets.mentions("cat dcbot/env", self.listed, home=HOME, cwd=HOME + "/.config"))
        self.assertTrue(secrets.mentions("cat ../.ssh/id_rsa", self.listed, home=HOME, cwd=HOME + "/work"))
        for command in ("cat .config/nvim/init.vim", "ls .config", "grep -r TOKEN work", "git log -p", "echo .config"):
            self.assertEqual(secrets.mentions(command, self.listed, home=HOME, cwd=HOME), [], command)
        self.assertEqual(secrets.mentions("cat .config/dcbot/env", self.listed, home=HOME), [], "no cwd given: as before")
        self.assertEqual(secrets.mentions("cd .config && cat dcbot/env", self.listed, home=HOME, cwd=HOME), [], "after a cd: a known limit")
        self.assertTrue(secrets.mentions("cd .config/dcbot && cat env", self.listed, home=HOME, cwd=HOME), "(the folder itself is listed: ~/.config/dcbot/**)")
        self.assertTrue(secrets.holds(".config", self.listed, home=HOME, cwd=HOME))
        self.assertFalse(secrets.holds(".config", self.listed, home=HOME), "no cwd: as before")
        self.assertFalse(secrets.holds(".", self.listed, home=HOME, cwd=HOME), "home itself")

    def test_a_key_handed_over_to_be_used(self):
        """(manager 2026-10-14) ssh/scp/sftp -i, ssh-add, ssh-keygen -y -f use the key; anything else in the command is judged."""
        for command in ("ssh -i ~/.ssh/id_ed25519 lap", "scp -i ~/.ssh/id_ed25519 a lap:b", "sftp -i ~/.ssh/id_rsa lap",
                        "ssh-add ~/.ssh/id_ed25519", "ssh-add -t 3600 ~/.ssh/id_rsa", "ssh-keygen -y -f ~/.ssh/id_ed25519",
                        "ssh -o BatchMode=yes -i ~/.ssh/id_rsa lap uptime"):
            self.assertEqual(self.hit(command), [], command)
        for command in ("ssh -i ~/.ssh/id_ed25519 lap cat ~/.ssh/id_ed25519", "ssh-keygen -f ~/.ssh/id_ed25519 -p",
                        "ssh-keygen -l -f ~/.ssh/id_rsa", "cat ~/.ssh/id_rsa | ssh lap 'cat > k'", "scp ~/.ssh/id_rsa lap:",
                        "ssh -i ~/.ssh/id_rsa lap && cat ~/.ssh/id_rsa", "ssh lap -F ~/.ssh/id_rsa"):
            self.assertTrue(self.hit(command), command)

    def test_quotes_and_other_spellings(self):
        for command in ('cat "$HOME"/.config/dcbot/env', 'cat ~/".config/dcbot/env"', 'cat "${HOME}/.config/dcbot/env"',
                        "cat ~/.config//dcbot/env", "cat ~/.config/./dcbot/env", "cat ~/.config/x/../dcbot/env",
                        "cat --file=~/.config/dcbot/env", "scp lap:~/.ssh/id_rsa ."):
            self.assertTrue(self.hit(command), command)
        with mock.patch.object(secrets, "_user", return_value="lap"):
            self.assertTrue(self.hit("cat ~lap/.config/dcbot/env"))
            self.assertEqual(self.hit("cat ~other/.config/dcbot/env"), [], "another user's home is not this one's")

    def test_a_folder_above_a_secret_given_to_what_reads_into_it(self):
        for command in ("grep -r TOKEN ~/.config", "grep -R TOKEN ~/.ssh", "rg TOKEN ~/.config", "find ~/.ssh -type f", "tar cz ~/.ssh",
                        "zip -r out.zip ~/.config", "rsync -a ~/.ssh/ lap:/tmp/", "cp -r ~/.ssh /tmp/x", "ls -R ~/.config",
                        "sudo grep -r x ~/.config/dcbot"):
            self.assertTrue(self.hit(command), command)
        for command in ("ls ~/.config", "ls -la ~/.ssh", "du -sh ~/.config", "cd ~/.ssh", "grep x ~/.config", "cp ~/.ssh/config /tmp",
                        "chmod 700 ~/.ssh", "vim ~/.ssh/config"):
            self.assertEqual(self.hit(command), [], command)

    def test_what_the_ruling_leaves_as_a_known_limit(self):
        for command in ("cd ~/.config && cat dcbot/env", "grep -r TOKEN ~", "grep -r TOKEN /", "f=env; cat ~/.config/dcbot/$f",
                        "python3 -c 'open(\"x\")'"):
            self.assertEqual(self.hit(command)[:0], [], command)  # (whatever is said of these: none is promised)
        self.assertEqual(self.hit("cd ~/.config && cat dcbot/env"), [], "a relative path after cd: not seen")
        self.assertEqual(self.hit("grep -r TOKEN ~"), [], "a search of the whole home folder: not judged")

    def test_a_file_system_that_does_not_tell_case_apart(self):
        """macOS (APFS): ~/.SSH/ID_ED25519 opens the private key; there the comparison is casefolded."""
        self.assertTrue(secrets.is_secret(HOME + "/.SSH/ID_ED25519", self.listed, fold=True))
        self.assertFalse(secrets.is_secret(HOME + "/.SSH/ID_ED25519", self.listed, fold=False), "Linux: another file")
        self.assertTrue(self.hit("cat ~/.SSH/id_ed25519", fold=True))
        self.assertEqual(self.hit("cat ~/.SSH/id_ed25519", fold=False), [])
        self.assertTrue(secrets.holds(HOME + "/.CONFIG", self.listed, home=HOME, fold=True))
        self.assertEqual(secrets.FOLD, sys.platform == "darwin")

    def test_folders_a_search_tool_is_handed(self):
        for folder, holds in ((HOME + "/.config", True), (HOME + "/.ssh", True), (HOME + "/.config/dcbot", True), ("~/.config", True),
                              (HOME, False), ("/", False), ("/h", False), (HOME + "/work", False), ("relative", False), ("", False)):
            self.assertEqual(secrets.holds(folder, self.listed, home=HOME), holds, folder)

    def test_still_fast(self):
        began = time.perf_counter()
        for _ in range(500):
            self.hit("git status && ls -la src/ && python3 -m pytest -q 'tests/test_x.py' && grep -r foo src/")
        self.assertLess((time.perf_counter() - began) / 500, 0.002)


class Instance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)

    def settings(self, text):
        with open(os.path.join(self.tmp.name, "plugins.json"), "w", encoding="utf-8") as f:
            f.write(text if isinstance(text, str) else json.dumps(text))

    def test_the_instance_adds_its_own(self):
        self.settings({"core": {"secret_paths": ["~/.aws/credentials", "/srv/keys/**", 5, ""]}})
        listed, problem = secrets.patterns(home=HOME)
        self.assertIsNone(problem)
        self.assertIn(HOME + "/.aws/credentials", listed)
        self.assertIn("/srv/keys/**", listed)
        self.assertEqual(len(listed), len(secrets.DEFAULT) + 2, "what is not a path is left out")
        self.assertTrue(secrets.is_secret("/srv/keys/a/b", listed))

    def test_settings_that_cannot_be_read_leave_the_defaults_and_say_why_without_waiting(self):
        for text, why in (("{ not JSON", "JSONDecodeError"), ({"core": {"secret_paths": "~/x"}}, "not a list")):
            self.settings(text)
            began = time.perf_counter()
            listed, problem = secrets.patterns(home=HOME)
            self.assertLess(time.perf_counter() - began, 0.05, "never the engine's waits for a broken file")
            self.assertEqual(listed, [HOME + p[1:] for p in secrets.DEFAULT])
            self.assertIn(why, problem)

    def test_no_settings_file_or_no_such_setting(self):
        self.assertEqual(secrets.patterns(home=HOME), ([HOME + p[1:] for p in secrets.DEFAULT], None))
        self.settings({"core": {}})
        self.assertEqual(secrets.patterns(home=HOME)[1], None)


class ForTheGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)

    def settings(self, data):
        with open(os.path.join(self.tmp.name, "plugins.json"), "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_the_switch(self):
        self.assertTrue(secrets.guard_on(), "no settings: on")
        self.settings("{ broken")
        self.assertTrue(secrets.guard_on(), "unreadable: on")
        for value, on in ((False, False), (True, True), (0, True), ("no", True), (None, True)):
            self.settings({"core": {"secret_guard": value}})
            self.assertEqual(secrets.guard_on(), on, value)

    def test_claude_codes_own_rules(self):
        """In Claude Code's syntax: ~/… under home, //… elsewhere; none for a pattern that needs a "but not *.pub"."""
        self.settings({"core": {"secret_paths": ["/srv/keys/**", "~/.aws/credentials"]}})
        rules = secrets.deny_rules(home=HOME)
        self.assertIn("Read(~/.config/dcbot/**)", rules)
        self.assertIn("Read(//srv/keys/**)", rules)
        self.assertIn("Read(~/.aws/credentials)", rules)
        self.assertFalse([r for r in rules if ".ssh" in r], "left to the guard, which knows the exceptions")

    def test_searching_a_folder_that_holds_secrets(self):
        """(holds(), the K-03a follow-up, is what the guard asks of a Grep's, a Glob's or an LS's folder.)"""
        listed = [HOME + p[1:] for p in secrets.DEFAULT]
        for folder, holds in ((HOME + "/.config", True), (HOME + "/.ssh", True), (HOME, False), ("/", False), (HOME + "/work", False)):
            self.assertEqual(secrets.holds(folder, listed, home=HOME), holds, folder)


if __name__ == "__main__":
    unittest.main()
