"""Deploy gate: these must pass before ./deploy.sh lets a change go live."""
import importlib
import unittest

from bot.util import reply_context, split_message
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Basic(unittest.TestCase):
    def test_modules_import(self):
        for m in ("bot.config", "bot.util", "bot.events", "bot.tmuxio", "bot.sessions", "bot.attachments", "bot.slash", "bot.ask", "bot.routes", "bot.agents", "bot.catalog", "bot.menus", "bot.peers", "bot.setup", "bot.archive", "bot.terminal", "bot.posts", "bot.process", "bot.screens", "bot.screenshot", "bot.claude_bridge", "bot.tmux_bridge",
                  "bot.main"):
            importlib.import_module(m)

    def test_no_function_reuses_a_module_name(self):
        """A local variable named like an imported module (say `archive`) makes Python treat the name as local in
        the whole function, and every use of the module before the assignment fails."""
        import ast
        import os
        folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(folder, name)).read())
            modules = set()
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) or isinstance(node, ast.Import):
                    modules |= set((a.asname or a.name).split(".")[0] for a in node.names)
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for node in ast.walk(fn):
                        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                            self.assertNotIn(node.id, modules, "%s: %s() assigns to %r, a module name" % (name, fn.name, node.id))

    def test_every_builtin_command_answers(self):
        """From Discord's "/" menu a command is deferred first; one that never sends anything shows as failed."""
        import asyncio
        import tempfile
        from bot import config
        old = config.STATE
        config.STATE = tempfile.mkdtemp()
        try:
            from bot import main as botmain
            bot = botmain.Bot()

            async def nothing(*a, **k):
                return None
            bot.audit = nothing
            bot.setup.offer = nothing
            bot.tmux.screen = nothing
            bot.tmux.live_target = nothing
            bot.tmux.key = nothing

            class Ch(object):
                id, name, category = 7, "c", None
            for cmd in ("!help", "!status", "!setup", "!talklimit", "!recap", "!commands", "!screen", "!key x", "!stop",
                        "!detach", "!mode", "!nonsense"):
                said = []

                async def send(text, view=None):
                    said.append(text)
                asyncio.run(bot.command(Ch(), cmd, send))
                self.assertTrue(said, "%s sent nothing" % cmd)
        finally:
            config.STATE = old

    def test_reply_context(self):
        self.assertEqual(reply_context(True, "🔔 Claude Code needs\n your approval"),
                         "（這則是回覆，針對你（Claude）先前的這則訊息：「🔔 Claude Code needs your approval」）\n")
        self.assertIn("使用者自己", reply_context(False, "x"))
        self.assertIn("附 2 個附件", reply_context(False, "", 2))
        self.assertTrue(reply_context(True, "y" * 2000).endswith("…」）\n"))

    def test_split_short(self):
        self.assertEqual(split_message("hello"), ["hello"])

    def test_split_respects_limit_and_fences(self):
        text = "intro\n```py\n" + "\n".join("x = %d" % i for i in range(800)) + "\n```\nend"
        parts = split_message(text, 500)
        self.assertTrue(all(len(p) <= 500 for p in parts))
        for p in parts:
            self.assertEqual(p.count("```") % 2, 0, p[:60])

    def test_split_long_line(self):
        parts = split_message("a" * 5000, 1900)
        self.assertTrue(all(len(p) <= 1900 for p in parts))
        self.assertEqual("".join(parts), "a" * 5000)



class SameSocket(unittest.TestCase):
    def test_one_socket_under_two_names(self):
        import os
        import tempfile
        from bot import util
        self.assertTrue(util.same_socket("/tmp/tmux-1/default", "/tmp/tmux-1/default"))
        self.assertTrue(util.same_socket("/private/tmp/tmux-501/default", "/tmp/tmux-501/default"), "macOS, a path of another machine")
        self.assertTrue(util.same_socket("/tmp/tmux-501/default", "/private/tmp/tmux-501/default"))
        self.assertFalse(util.same_socket("/tmp/tmux-501/default", "/tmp/tmux-502/default"))
        self.assertFalse(util.same_socket("/private/tmp/a", "/var/tmp/a"))
        for nothing in (None, "", 0):
            self.assertFalse(util.same_socket(nothing, "/tmp/a"))
            self.assertFalse(util.same_socket(nothing, nothing), "no path is no socket")
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "real"))
            os.symlink(os.path.join(tmp, "real"), os.path.join(tmp, "link"))
            a, b = os.path.join(tmp, "real", "s"), os.path.join(tmp, "link", "s")
            self.assertTrue(util.same_socket(a, b, here=True), "on this machine: the same file, whether or not it exists")
            self.assertFalse(util.same_socket(a, b), "another machine's paths cannot be followed from here")
        self.assertFalse(util.same_socket("/tmp/a\0b", "/tmp/c", here=True), "a path no file system takes: not the same, no error")


if __name__ == "__main__":
    unittest.main()
