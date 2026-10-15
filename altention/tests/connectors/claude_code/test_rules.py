"""The rules every module of a connector keeps (altention/connectors/<tool>/, stage 3b): standard library only,
nothing of the Discord bot, nothing that names the bot's environment or state. Read from the source, file by file:
a module that breaks one would still import here, and fail only on another machine's system Python."""
import ast
import os
import sys
import unittest

ENGINE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
CONNECTORS = os.path.join(ENGINE, "altention", "connectors")
FORBIDDEN_NAMES = ("DCBOT_", "dcbot/state", "dcbot\\state")


def modules():
    for top, _dirs, files in os.walk(CONNECTORS):
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(top, name)


def imported(tree):
    """(module, level) of every import in a file, wherever in it."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, 0
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", node.level


class Rules(unittest.TestCase):
    def test_there_is_a_connector_to_read(self):
        self.assertIn(os.path.join(CONNECTORS, "claude_code", "screen.py"), list(modules()))

    def test_only_the_standard_library_and_itself(self):
        stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else None
        for path in modules():
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for module, level in imported(tree):
                top = module.split(".")[0]
                where = "%s imports %s" % (os.path.relpath(path, ENGINE), module or "." * level)
                if level:  # from . import x: its own package
                    continue
                self.assertNotIn(top, ("bot", "discord", "dcbot"), where + ": nothing of the Discord bot")
                if top == "altention":
                    self.assertTrue(module.startswith("altention.connectors"), where + ": of the engine, only connectors")
                elif stdlib is not None:
                    self.assertIn(top, stdlib, where + ": not in the standard library")

    def test_python_3_8_reads_every_module(self):
        for path in modules():
            with open(path, encoding="utf-8") as f:
                ast.parse(f.read(), feature_version=(3, 8))

    def test_nothing_names_the_bots_environment_or_state(self):
        """What a connector needs of the bot (a settings file, the hook's command line, paths not to read) is
        passed to it. Looked for in the code, not in docstrings and comments, which may say what is not done."""
        for path in modules():
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body and \
                        isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant):
                    docstrings.add(id(node.body[0].value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    for word in FORBIDDEN_NAMES:
                        self.assertNotIn(word, node.value, "%s line %d" % (os.path.relpath(path, ENGINE), node.lineno))

if __name__ == "__main__":
    unittest.main()
