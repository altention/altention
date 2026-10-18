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
    """The skill's sentence-by-sentence English example quotes progress's question: it must be the printed one."""

    def test_the_example_quotes_what_questions_prints(self):
        text = read(".claude", "skills", "install", "SKILL.md")
        part = " ".join(text[text.index("**The printed questions**"):text.index("Then check what they kept")].split())
        found, _ = plugin.discover(plugin.ENGINE, instance="", root=tempfile.gettempdir())
        q = [q for q in plugins.questions(found) if q["plugin"] == "progress"][0]
        quoted = [x.replace(" ", "") for x in re.findall(r"「(.*?)」", part)]
        for words in [q["header"], q["question"]] + [o[k] for o in q["options"] for k in ("label", "description")]:
            self.assertIn(words.replace(" ", ""), quoted)


class Backup(unittest.TestCase):
    """C-25 (data-safety 2.2-2.4): the install asks about the backup with the printed questions, after the instance
    and before Discord, and sets every remote through ops/remote.py set (its checks), never by hand."""

    def test_the_step_asks_the_printed_questions_and_sets_through_remote_py(self):
        text = read(".claude", "skills", "install", "SKILL.md")
        step = text[text.index("## 3b. Backup"):text.index("## 4. ")]
        self.assertLess(text.index("python3 ops/instance.py init"), text.index("## 3b. Backup"))
        self.assertIn("python3 ops/plugins.py questions --backup --json", step)
        self.assertIn("python3 ops/remote.py set <instance|logs|progress|planner> <URL>", step)
        self.assertIn("gh repo create <account>/<repo> --private", step)
        self.assertFalse(re.search(r"gh repo create[^\n`]*--public", text), "no public repository is ever created")
        for gone in ("remote add origin", "git push -u"):  # the old hand way, with no checks
            self.assertNotIn(gone, text)
        import remote
        for name in remote.NAMES:
            self.assertIn("altention-%s" % name, step)

    def test_the_readmes_say_how_and_the_known_limit(self):
        for path, words in ((("README.md",), ("## Backup and restore", "up to an hour", "python3 ops/remote.py set")),
                            (("README.zh-TW.md",), ("## 備份與還原", "最多一小時", "python3 ops/remote.py set"))):
            text = read(*path)
            for w in words:
                self.assertIn(w, text, path)


class Restore(unittest.TestCase):
    """C-45 (C-30 drill): /install asks new install or restore first; the restore path asks none of the setup
    questions and never runs init (they would write over what came back), and takes the old machine's name only
    after the person says it will not be used again (manager 2026-10-17)."""

    def test_the_restore_path(self):
        text = read(".claude", "skills", "install", "SKILL.md")
        self.assertLess(text.index("## 1b. A new install, or back from the backups"), text.index("## 2. Ask the questions"))
        first = text[text.index("## 1b."):text.index("### Restoring from the backups")]
        self.assertIn("config.instance_dir()", first)
        self.assertIn("「新安裝」", first)
        self.assertIn("「從備份還原」", first)
        restore = text[text.index("### Restoring from the backups"):text.index("## 2. Ask the questions")]
        for gone in ("instance.py init", "questions --setup", "questions --backup", "remote.py set"):
            self.assertNotIn(gone, restore)
        steps = [restore.index(w) for w in ("instance.py join <URL>", "instance.py names --json", "「不會再用了」",
                                            "instance.py take-name", "install.sh discord")]
        self.assertEqual(steps, sorted(steps), "join, the names, the person's yes, take-name, then the install")
        self.assertIn("連不上不代表它沒了", restore)
        self.assertIn("--only-in-settings", restore)
        self.assertIn("mv ~/.config/altention/host ~/.config/altention/host.bak", restore, "kept, never deleted")
        self.assertNotIn("rm ~/.config/altention/host", restore)
        self.assertLess(restore.index("log --oneline -1 -- X/"), restore.index("host.bak"), "no records under X first")

    def test_the_readmes_say_how_to_restore(self):
        for path, words in ((("README.md",), ("「從備份還原」", "instance.py take-name", "~/.local/share/usage", "core.git-user")),
                            (("README.zh-TW.md",), ("「從備份還原」", "instance.py take-name", "~/.local/share/usage", "core.git-user"))):
            text = read(*path)
            for w in words:
                self.assertIn(w, text, path)


if __name__ == "__main__":
    unittest.main()
