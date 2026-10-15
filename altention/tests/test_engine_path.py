"""Where the engine is on each machine: the machine list's "engine" (default ~/altention/engine), handed to a shell over
there with its ~ left to that shell (stage 3a.4). The commands are run through a real sh with a throwaway HOME."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import subprocess
import sys
import tempfile
import shutil
import unittest

from altention import config

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def shell(command, home):
    return subprocess.run(["sh", "-c", command], env={"HOME": home, "PATH": "/usr/bin:/bin"}, stdout=subprocess.PIPE,
                          universal_newlines=True).stdout


class EnginePath(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 home ")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_default_and_written(self):
        self.assertEqual(config.engine_raw({}), "~/altention/engine")
        self.assertEqual(config.engine_raw({"engine": "  "}), "~/altention/engine")
        self.assertEqual(config.engine_raw({"engine": "~/altention/engine"}), "~/altention/engine")
        self.assertEqual(config.engine_on({}), "~/'altention/engine'", "today's machines: the same folder")

    def test_a_shell_over_there_gets_its_own_home_and_the_whole_path(self):
        for written, want in (("~/my 引擎", "my 引擎"), ("~/altention/engine", "altention/engine"),
                              ("~/it's here", "it's here")):
            path = config.engine_on({"engine": written}) + "/dcbot/tools/dcremote"
            self.assertEqual(shell("printf %s " + path, self.home), os.path.join(self.home, want, "dcbot/tools/dcremote"))
        self.assertEqual(shell("printf %s " + config.engine_on({"engine": "/opt/alt engine"}), self.home), "/opt/alt engine")

    def test_apply_script_puts_the_engine_where_it_is(self):
        sys.path.insert(0, os.path.join(ENGINE, "ops"))
        import apply
        for template in (apply.SCRIPT, apply.PREVIEW, apply.HELD):
            script = template.format(instance="''", repo="''", name="''", engine=config.engine_on({"engine": "~/my 引擎"}),
                                     plugins="", names="progress", bot_leaves="0", allow="")
            line = next(l for l in script.splitlines() if l.startswith("H="))
            self.assertEqual(shell(line + '; printf %s "$H"', self.home), os.path.join(self.home, "my 引擎"))

    def test_the_bin_tools_reach_dcctl_over_there(self):
        import importlib.machinery
        import importlib.util
        for tool in ("post", "read", "notify"):
            path = os.path.join(ENGINE, "altention", "dc", tool + ".py")
            loader = importlib.machinery.SourceFileLoader(tool + "_t", path)
            mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(tool + "_t", loader))
            loader.exec_module(mod)
            self.assertEqual(mod.dcctl_on({}), "~/'altention/engine'/dcbot/tools/dcctl", tool)
            got = shell("printf %s " + mod.dcctl_on({"engine": "~/my 引擎"}), self.home)
            self.assertEqual(got, os.path.join(self.home, "my 引擎", "dcbot/tools/dcctl"), tool)
            self.assertEqual(shell("printf %s " + mod.dcctl_on({"engine": "~"}), self.home),
                             os.path.join(self.home, "dcbot/tools/dcctl"), tool)

    def test_on_the_bots_machine_the_bots_dcctl_even_from_a_copy_elsewhere(self):
        """From a worktree or a copy, posts must still reach the running bot, not a state/ nobody reads."""
        import importlib.machinery
        import importlib.util
        from unittest import mock
        copy = os.path.join(self.home, "worktree 副本")
        shutil.copytree(os.path.join(ENGINE, "altention", "dc"), os.path.join(copy, "altention", "dc"), ignore=shutil.ignore_patterns("tests", "__pycache__"))
        bots = os.path.join(self.home, "altention", "engine", "dcbot", "tools", "dcctl")  # the default place
        moved = os.path.join(self.home, "別的 引擎", "dcbot", "tools", "dcctl")
        for tool in ("post", "read", "notify"):
            path = os.path.join(copy, "altention", "dc", tool + ".py")
            loader = importlib.machinery.SourceFileLoader(tool + "_c", path)
            mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(tool + "_c", loader))
            loader.exec_module(mod)
            with mock.patch.dict(os.environ, {"HOME": self.home}):
                self.assertEqual(mod.local_dcctl(), os.path.join(copy, "dcbot", "tools", "dcctl"), "nothing else there")
                for f in (bots, moved):
                    os.makedirs(os.path.dirname(f), exist_ok=True)
                    open(f, "w").close()
                self.assertEqual(mod.local_dcctl(), bots, tool)
                self.assertEqual(mod.local_dcctl("~/別的 引擎"), moved, "the list's engine first")
                os.remove(bots)
                os.remove(moved)


if __name__ == "__main__":
    unittest.main()
