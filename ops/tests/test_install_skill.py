"""The install skill's fixed texts: Claude passes them on as they are, so they must be right here."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import re
import tempfile
import unittest

from altention import plugin
from ops import plugins

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


class Permissions(unittest.TestCase):
    """Step 4's block of scopes and bot permissions (copied by hand once, two went missing and one came out mistyped)."""

    def block(self):
        m = re.search(r"\n   ```text\n   SCOPES.*?\n   ```\n", read(".claude", "skills", "install", "SKILL.md"), re.S)
        self.assertTrue(m, "the block is in the skill")
        lines = [l.strip() for l in m.group(0).splitlines()[2:-1]]
        i = lines.index("")
        return lines[:i], lines[i + 1:]

    def test_the_readmes_list_word_for_word(self):
        scopes, perms = self.block()
        self.assertEqual(scopes[1:], ["bot", "applications.commands"])
        self.assertEqual(scopes[0], "SCOPES（勾這 %d 個）" % len(scopes[1:]))
        self.assertEqual(perms[0], "BOT PERMISSIONS（勾這 %d 個）" % len(perms[1:]))
        for readme, sep in (("README.md", ", "), ("README.zh-TW.md", "、")):
            text = " ".join(read(readme).split())
            m = re.search(r"bot permissions (?:勾 )?(View Channels.*?Use Application Commands)", text)
            self.assertTrue(m, readme)
            self.assertEqual(m.group(1).split(sep), perms[1:], readme)
        self.assertIn("Manage Threads", perms)
        self.assertIn("Pin Messages", perms)


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


if __name__ == "__main__":
    unittest.main()
