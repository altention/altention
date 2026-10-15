"""The bot's test files that use a connector (altention/connectors/<tool>/) or its samples
(altention/tests/connectors/<tool>/) import `bot` first. The rules of a connector's own modules are read in
altention/tests/connectors/claude_code/test_rules.py (there since stage 3b step 1.3)."""
import ast
import os
import unittest
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

OF_A_CONNECTOR = ("altention.connectors", "altention.tests.connectors")  # the code, and its tests and samples


def top_imports(path):
    """The modules a file imports at its top level, in order (relative ones left out)."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    out = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            out += ["%s.%s" % (node.module, a.name) for a in node.names] if node.module == "tests" else [node.module or ""]
    return out


class TestsThatUseAConnector(unittest.TestCase):
    HERE = os.path.dirname(os.path.abspath(__file__))

    def brings_bot(self, module, seen=()):
        """Does importing this module import `bot` (whose __init__ puts the engine's folder on the path)? `bot`
        itself, or a module of tests/ that does so before anything of a connector: tests.fakepane does,
        tests.transcript does not."""
        if module == "bot" or module.startswith("bot."):
            return True
        if not module.startswith("tests.") or module in seen:
            return False
        path = os.path.join(self.HERE, module.split(".")[1] + ".py")
        if not os.path.isfile(path):
            return False
        for inner in top_imports(path):
            if inner.startswith(OF_A_CONNECTOR):
                return False
            if self.brings_bot(inner, seen + (module,)):
                return True
        return False

    def test_each_puts_the_engine_on_the_path_itself(self):
        """A test file of the bot that imports a connector imports `bot` before it. Without that the file imports
        only when some other test has run first: the whole suite passes and the file run alone does not (found on
        step 1.1's own tests, 2026-10-12). Only connectors are meant: a file that uses altention's other modules
        sees to the path itself."""
        checked = 0
        for name in sorted(os.listdir(self.HERE)):
            if not name.endswith(".py"):
                continue
            ready = False
            for module in top_imports(os.path.join(self.HERE, name)):
                if module.startswith(OF_A_CONNECTOR):
                    self.assertTrue(ready, "%s imports %s before anything that brings bot" % (name, module))
                    checked += 1
                ready = ready or self.brings_bot(module)
        self.assertGreaterEqual(checked, 5, "the files of step 1.1 are among them")

    def test_the_rule_tells_what_brings_bot(self):
        self.assertTrue(self.brings_bot("bot"))
        self.assertTrue(self.brings_bot("bot.tmux_bridge"))
        self.assertTrue(self.brings_bot("tests.fakepane"))
        self.assertTrue(self.brings_bot("tests.test_turn_end"))
        self.assertFalse(self.brings_bot("tests.transcript"), "it imports nothing of the bot: not enough")
        self.assertFalse(self.brings_bot("altention.tests.connectors.claude_code.samples"))
        self.assertFalse(self.brings_bot("altention.testing"))
        self.assertFalse(self.brings_bot("os"))


if __name__ == "__main__":
    unittest.main()
