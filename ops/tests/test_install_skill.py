"""The install skill's fixed texts: Claude passes them on as they are, so they must be right here."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import re
import sys
import tempfile
import unittest

from altention import plugin
from ops import plugins
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "ops"))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


class Permissions(unittest.TestCase):
    """The 14 permissions are no longer ticked by hand (copied by hand once, two went missing and one came out
    mistyped): install.sh discord puts them into the link it prints (C-20, C-21)."""
    PAGE = ("View Channels", "Manage Channels", "View Audit Log", "Send Messages", "Send Messages in Threads",
            "Create Public Threads", "Manage Threads", "Manage Messages", "Pin Messages", "Embed Links", "Attach Files",
            "Read Message History", "Add Reactions", "Use Application Commands")

    def test_the_link_asks_for_the_14_the_bot_needs(self):
        from ops import discordapp
        names = [n for n, _ in discordapp.PERMISSIONS]
        self.assertEqual([n.replace("_", " ").title().replace(" In ", " in ").replace("View Channel", "View Channels")
                          for n in names], list(self.PAGE))

    def test_no_hand_steps_left(self):
        for path in ((".claude", "skills", "install", "SKILL.md"), ("README.md",), ("README.zh-TW.md",), ("SECURITY.md",),
                     ("install.sh",)):
            text = read(*path)
            self.assertIn("install.sh discord", text, path)
            for gone in ("--token", "Developer Mode", "開發者模式", "URL Generator", "Copy Server ID"):
                self.assertNotIn(gone, text, path)


class TranslationExample(unittest.TestCase):
    """The setup skill's sentence-by-sentence English example quotes progress's question: it must be the printed
    one (the questions moved from the install skill to #setup, C-48)."""

    def test_the_example_quotes_what_questions_prints(self):
        text = read("skills", "setup", "SKILL.md")
        part = " ".join(text[text.index("For example, progress's question"):text.index("## 1. Where things stand")].split())
        found, _ = plugin.discover(plugin.ENGINE, instance="", root=tempfile.gettempdir())
        q = [q for q in plugins.questions(found) if q["plugin"] == "progress"][0]
        quoted = [x.replace(" ", "") for x in re.findall(r"「(.*?)」", part)]
        for words in [q["header"], q["question"]] + [o[k] for o in q["options"] for k in ("label", "description")]:
            self.assertIn(words.replace(" ", ""), quoted)


class Backup(unittest.TestCase):
    """C-25 (data-safety 2.2-2.4), now in #setup (C-48): the backup question is a printed one, and every remote is
    set through setup.py backup-set, which makes only private repositories and runs ops/remote.py set's checks."""

    def test_the_step_sets_through_setup_py(self):
        text = read("skills", "setup", "SKILL.md")
        step = text[text.index("## 4. Backups"):text.index("## 5. ")]
        self.assertIn("python3 <ENGINE>/ops/setup.py backup-set <name> --create <account>/altention-<name>", step)
        self.assertIn("login-check", step)
        for gone in ("remote add origin", "git push -u", "gh repo create", "remote.py set"):  # never by hand here
            self.assertNotIn(gone, step)
        code = read("ops", "setup.py")
        self.assertIn('"gh", "repo", "create", target, "--private"', code)
        self.assertNotIn("--public", code)
        self.assertIn('"remote.py"), "set", name, url]', code)
        import remote
        for name in remote.NAMES:
            self.assertIn("`altention-%s`" % name, step)

    def test_the_install_skill_asks_no_setting(self):
        text = read(".claude", "skills", "install", "SKILL.md")
        for gone in ("questions --setup", "questions --backup", "instance.py init", "AskUserQuestion), header 「方案」"):
            self.assertNotIn(gone, text)
        self.assertIn("#setup", text)

    def test_the_readmes_say_how_and_the_known_limit(self):
        for path, words in ((("README.md",), ("## Backup and restore", "up to an hour", "python3 ops/remote.py set")),
                            (("README.zh-TW.md",), ("## 備份與還原", "最多一小時", "python3 ops/remote.py set"))):
            text = read(*path)
            for w in words:
                self.assertIn(w, text, path)


class Restore(unittest.TestCase):
    """C-45, moved to #setup (C-57 S2): the install skill restores nothing in the terminal; #setup asks new or
    restore first, and goes: the login, which backup, restore (setup.py's checks), which machine, done."""

    def test_the_restore_path(self):
        text = read(".claude", "skills", "install", "SKILL.md")
        part = text[text.index("## 1b."):text.index("## 4. The Discord application")]
        for gone in ("instance.py names", "take-name", "host.bak", "AskUserQuestion"):
            self.assertNotIn(gone, part, "done in #setup now")
        self.assertIn("#setup", part)
        self.assertIn("instance.py join <URL>", part, "kept for the advanced way only")
        skill = read("skills", "setup", "SKILL.md")
        restore = skill[skill.index("## Back from the backups"):skill.index("## 5. The tour")]
        steps = [restore.index(w) for w in ("login-check", "setup.py repos --json", "setup.py restore <URL>",
                                            "answer machine", "machine.confirm", "a few minutes; run `done`")]
        self.assertEqual(steps, sorted(steps), "login, which backup, restore, which machine, its confirmation, done")
        self.assertIn("kept, not deleted", " ".join(restore.split()))

    def test_the_readmes_say_how_to_restore(self):
        for path, words in ((("README.md",), ("「從備份還原」", "instance.py take-name", "~/.local/share/usage", "core.git-user")),
                            (("README.zh-TW.md",), ("「從備份還原」", "instance.py take-name", "~/.local/share/usage", "core.git-user"))):
            text = read(*path)
            for w in words:
                self.assertIn(w, text, path)


if __name__ == "__main__":
    unittest.main()
