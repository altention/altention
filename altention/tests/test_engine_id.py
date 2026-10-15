"""altention/engine_id.py: is this checkout this machine's engine? Official, another checkout (a worktree, a second
clone), or cannot be told; the test switch counts only outside the real home; the supervisor's own copy agrees."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import importlib.util
import os
import pwd
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from altention import config, engine_id

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def git(path, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", path] + list(args), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Identity(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 engine ")
        self.env = mock.patch.dict(os.environ, {"HOME": self.home, "ALT_INSTANCE": "", "ALT_ROOT": ""})
        self.env.start()
        os.environ.pop("ALT_TEST_ANY_ENGINE", None)  # these tests ask the real rules
        self.main = os.path.join(self.home, "altention", "engine")  # the default place
        os.makedirs(self.main)
        git(self.main, "init", "-q")
        git(self.main, "commit", "-q", "--allow-empty", "-m", "x")
        spec = importlib.util.spec_from_file_location("sup_t", os.path.join(ENGINE, "dcbot", "supervisor.py"))
        self.sup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.sup)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def kind(self, path):
        with mock.patch.object(config, "inventory", return_value={}):
            return engine_id.check(path)[0]

    def supervisor_says(self, path):
        with mock.patch.object(self.sup, "ENGINE", path):
            return self.sup.not_this_machines_engine()

    def test_the_machines_own_engine_is_official(self):
        self.assertEqual(self.kind(self.main), "official")
        self.assertEqual(self.supervisor_says(self.main), "")

    def away(self):
        """The engine moved out of the default place, which is left empty for a link."""
        real = os.path.join(self.home, "real engine")
        os.rename(self.main, real)
        self.main = real

    def test_a_link_to_it_is_the_same_engine(self):
        self.away()
        os.symlink(self.main, os.path.join(self.home, "altention", "engine"))
        self.assertEqual(self.kind(os.path.join(self.home, "altention", "engine")), "official")

    def test_a_worktree_is_another_checkout(self):
        wt = os.path.join(self.home, "wt")
        git(self.main, "worktree", "add", "-q", wt)
        with mock.patch.object(config, "inventory", return_value={}):
            kind, where, why = engine_id.check(wt)
        self.assertEqual((kind, where), ("other", os.path.realpath(self.main)))
        self.assertIn("worktree", why)
        self.assertIn("worktree", self.supervisor_says(wt))

    def test_a_second_clone_is_another_checkout(self):
        second = os.path.join(self.home, "second")
        git(self.home, "clone", "-q", self.main, second)
        with mock.patch.object(config, "inventory", return_value={}):
            kind, where, _ = engine_id.check(second)
        self.assertEqual((kind, where), ("other", os.path.realpath(self.main)))
        self.assertIn(os.path.realpath(self.main), self.supervisor_says(second))

    def both(self, path, inv=None):
        """(engine_id's kind, the supervisor's kind) for the same case."""
        with mock.patch.object(config, "inventory", return_value=inv or {}), \
                mock.patch.object(config, "this_machine", return_value="box"), mock.patch.object(self.sup, "ENGINE", path):
            return engine_id.check(path)[0], self.sup.engine_verdict()[0]

    def clone(self, name):
        path = os.path.join(self.home, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        git(self.home, "clone", "-q", self.main, path)
        return path

    def test_both_copies_of_the_rules_agree(self):
        """The supervisor keeps its own copy (it never imports what it may roll back): one set of cases, the same
        answers (official/ok, other/other, unknown/wait), so the two never drift apart (manager 2026-10-06)."""
        same = {"official": "ok", "other": "other", "unknown": "wait"}
        cmd = os.path.join(self.home, ".local", "bin", "altention")
        os.makedirs(os.path.dirname(cmd))
        got = {}
        wt = os.path.join(self.home, "wt")
        git(self.main, "worktree", "add", "-q", wt)
        second = self.clone("second")
        os.makedirs(os.path.join(second, "bin"))
        open(os.path.join(second, "bin", "altention"), "w").close()  # what the command's link points at
        plain = os.path.join(self.home, "plain")
        os.makedirs(plain)
        got["the default place itself"] = (self.both(self.main), "official")
        got["a worktree"] = (self.both(wt), "other")
        got["a second clone"] = (self.both(second), "other")
        got["no .git"] = (self.both(plain), "unknown")
        os.symlink(os.path.join(second, "bin", "altention"), cmd)  # someone ran --link in the second clone
        got["the command links elsewhere, the default place is this"] = (self.both(self.main), "official")
        os.remove(cmd)
        moved = self.clone("moved/engine")
        aside = os.path.join(self.home, "aside")
        os.rename(self.main, aside)
        os.symlink(os.path.join(self.home, "share", "not-mounted"), self.main)
        got["<root>/engine into a share not mounted"] = (self.both(aside), "unknown")
        os.remove(self.main)
        os.rename(aside, self.main)
        pointer = os.path.join(self.home, ".config", "altention", "engine")
        os.makedirs(os.path.dirname(pointer))
        with open(pointer, "w") as f:
            f.write(moved + "\n")
        got["the pointer names it"] = (self.both(moved), "official")
        got["the pointer names another"] = (self.both(self.main), "other")
        with open(pointer, "w") as f:
            f.write(os.path.join(self.home, "gone") + "\n")
        got["the pointer names nothing: as if none"] = (self.both(self.main), "official")
        os.remove(pointer)
        shutil.rmtree(self.main)
        newbox = os.path.join(self.home, "newbox")
        os.makedirs(os.path.join(newbox, ".git"))
        got["a new machine"] = (self.both(newbox), "official")
        os.symlink(os.path.join(second, "bin", "altention"), cmd)
        got["nothing at the places, the command links elsewhere"] = (self.both(newbox), "other")
        got["nothing at the places, the command links here"] = (self.both(second), "official")
        for name, ((a, b), want) in got.items():
            self.assertEqual(a, want, "%s: engine_id said %s" % (name, a))
            self.assertEqual(same[a], b, "%s: engine_id %s, supervisor %s" % (name, a, b))

    def unpack(self, name):
        """A folder as an unpacked archive of the engine has it: the engine's files, no .git."""
        path = os.path.join(self.home, name)
        os.makedirs(os.path.join(path, "altention"))
        open(os.path.join(path, "altention", "engine_id.py"), "w").close()
        return path

    def four(self, path):
        """(engine_id, supervisor, update.py, cron_update, cron_sync) for one case, each as engine_id's kind."""
        from altention import update
        with mock.patch.object(config, "inventory", return_value={}), \
                mock.patch.object(config, "this_machine", return_value="box"):
            a = engine_id.check(path)[0]
        back = {"ok": "official", "other": "other", "wait": "unknown"}
        with mock.patch.object(self.sup, "ENGINE", path), mock.patch.object(update, "ENGINE", path):
            b, c = back[self.sup.engine_verdict()[0]], back[update.verdict()[0]]
        launchers = []
        for name in ("cron_update.py", "cron_sync.py"):
            sp = importlib.util.spec_from_file_location("l_" + name[:-3], os.path.join(ENGINE, "altention", name))
            lm = importlib.util.module_from_spec(sp)
            sp.loader.exec_module(lm)
            with mock.patch.object(lm, "ENGINE", path):
                launchers.append("other" if lm.elsewhere() else "official")
        return (a, b, c) + tuple(launchers)

    def test_an_unpacked_engine_on_all_four_copies(self):
        """An engine unpacked from an archive (no .git) is decided like a clone; a .git that is a link to nowhere
        and a folder without .git or the engine's files cannot be told: the same answer from every copy of the
        rules (manager 2026-10-14). The launchers only tell "other" from the rest: anything but this machine's engine,
        with that engine there, is "other" to them (they do nothing)."""
        shutil.rmtree(self.main)  # a machine whose engine is the unpacked one
        official = self.unpack(os.path.join("altention", "engine"))
        second = self.unpack("下載/altention engine")
        broken = self.unpack("broken")
        os.symlink(os.path.join(self.home, "share", "not mounted", ".git"), os.path.join(broken, ".git"))
        empty = os.path.join(self.home, "empty")
        os.makedirs(empty)
        def launch(kind):  # the launchers know no "cannot tell": beside this machine's engine they leave it alone
            return "official" if kind == "official" else "other"
        for name, path, want in (("the unpacked engine at <root>/engine", official, "official"),
                                 ("a second unpacked copy", second, "other"),
                                 ("an unpacked engine whose .git is a link to nowhere", broken, "unknown"),
                                 ("a folder without .git or the engine's files", empty, "unknown")):
            got = self.four(path)
            self.assertEqual(got, (want, want, want, launch(want), launch(want)), name)
        self.assertEqual(self.supervisor_says(official), "")
        pointer = os.path.join(self.home, ".config", "altention", "engine")
        os.makedirs(os.path.dirname(pointer))
        with open(pointer, "w") as f:
            f.write(second + "\n")
        self.assertEqual(self.four(second)[:3], ("official",) * 3, "the pointer names the second copy")
        self.assertEqual(self.four(official)[:3], ("other",) * 3)

    def test_the_list_naming_a_place_decides_for_engine_id(self):
        elsewhere = self.clone("opt/engine")
        inv = {"machines": {"box": {"engine": elsewhere}}}
        self.assertEqual(self.both(elsewhere, inv)[0], "official", "the default place does not count")
        self.assertEqual(self.both(self.main, inv)[0], "other")
        inv = {"machines": {"box": {"engine": os.path.join(self.home, "gone")}}}
        self.assertEqual(self.both(self.main, inv)[0], "unknown", "the list names a place not there")

    def test_the_installer_writes_the_pointer_the_supervisor_reads(self):
        elsewhere = self.clone("opt/engine")
        engine_id.write_pointer(elsewhere)
        self.assertEqual(engine_id.pointed(), os.path.realpath(elsewhere))
        self.assertEqual(self.both(elsewhere), ("official", "ok"))

    def test_where_the_altention_command_points(self):
        shutil.rmtree(self.main)
        other = os.path.join(self.home, "src", "eng")
        os.makedirs(os.path.join(other, "bin"))
        os.makedirs(os.path.join(self.home, ".local", "bin"))
        open(os.path.join(other, "bin", "altention"), "w").close()
        os.symlink(os.path.join(other, "bin", "altention"), os.path.join(self.home, ".local", "bin", "altention"))
        fresh = os.path.join(self.home, "fresh")
        os.makedirs(os.path.join(fresh, ".git"))
        self.assertEqual(self.kind(fresh), "other")

    def test_a_new_machine_is_official(self):
        shutil.rmtree(self.main)
        fresh = os.path.join(self.home, "fresh")
        os.makedirs(os.path.join(fresh, ".git"))
        self.assertEqual(self.kind(fresh), "official")
        self.assertEqual(self.supervisor_says(fresh), "")

    def test_a_list_that_cannot_be_read_cannot_tell(self):
        with mock.patch.object(config, "inventory", side_effect=config.ConfigError("壞了")):
            kind, _, why = engine_id.check(self.main)
            refused = engine_id.refusal(self.main, "x")
        self.assertEqual(kind, "unknown")
        self.assertIn("判斷不了", why)
        self.assertIn("判斷不了", refused)

    def test_the_test_switch_counts_only_outside_the_real_home(self):
        second = os.path.join(self.home, "second")
        git(self.home, "clone", "-q", self.main, second)
        with mock.patch.dict(os.environ, {"ALT_TEST_ANY_ENGINE": "1"}):
            self.assertTrue(engine_id.test_override(), "a throwaway home")
            self.assertEqual(self.kind(second), "official")
            self.assertEqual(self.supervisor_says(second), "")
            real = pwd.getpwuid(os.getuid()).pw_dir
            with mock.patch.dict(os.environ, {"HOME": real}):
                self.assertFalse(engine_id.test_override(), "the real home: no switch")
            with mock.patch.object(pwd, "getpwuid", return_value=mock.Mock(pw_dir=self.home)):
                self.assertFalse(engine_id.test_override())
                self.assertNotEqual(self.supervisor_says(second), "", "the supervisor's copy too")

    def test_the_supervisor_waits_for_a_path_that_is_not_there_yet(self):
        """At boot a share may not be mounted: tried again until it is, not refused at once."""
        self.away()
        link = os.path.join(self.home, "altention", "engine")
        os.symlink(os.path.join(self.home, "share"), link)  # dangling now
        calls = []

        def later(seconds):
            calls.append(seconds)
            os.remove(link)
            os.symlink(self.main, link)  # the share came up: it is this engine
        with mock.patch.object(self.sup, "ENGINE", self.main), mock.patch.object(self.sup.time, "sleep", later), \
                mock.patch.object(self.sup, "log") as logged:
            self.assertEqual(self.sup.not_this_machines_engine(wait=60), "")
        self.assertEqual(len(calls), 1)
        self.assertIn("trying again", logged.call_args[0][0])
        os.remove(link)
        os.symlink(os.path.join(self.home, "gone"), link)
        with mock.patch.object(self.sup, "ENGINE", self.main), mock.patch.object(self.sup, "log"):
            self.assertIn("判斷不了", self.sup.not_this_machines_engine(wait=0), "still not there: refused in the end")

    def test_the_supervisor_check_flag_starts_nothing(self):
        wt = os.path.join(self.home, "wt")
        git(self.main, "worktree", "add", "-q", wt)
        for path, code, word in ((self.main, 0, "ok"), (wt, 1, "other")):
            with mock.patch.object(self.sup, "ENGINE", path), mock.patch.object(self.sup.sys, "argv", ["supervisor.py", "--check-engine"]), \
                    mock.patch.object(self.sup.os, "makedirs") as made, mock.patch("builtins.print") as said:
                self.assertEqual(self.sup.main(), code)
            made.assert_not_called()
            self.assertTrue(said.call_args[0][0].startswith(word))

    def test_altention_link_refuses_from_another_checkout(self):
        wt = os.path.join(self.home, "wt")
        git(self.main, "worktree", "add", "-q", wt)
        p = subprocess.run(["python3", os.path.join(ENGINE, "bin", "altention"), "--link"], stdout=subprocess.PIPE,
                           universal_newlines=True, env=dict(os.environ, ALT_TEST_ANY_ENGINE=""))
        self.assertEqual(p.returncode, 1, p.stdout)  # this test's engine is a worktree, or not the default place here
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".local", "bin", "altention")))

    def test_the_shell_form(self):
        p = subprocess.run(["python3", os.path.join(ENGINE, "altention", "engine_id.py")], stdout=subprocess.PIPE,
                           universal_newlines=True, env=dict(os.environ, ALT_TEST_ANY_ENGINE="1"))
        self.assertTrue(p.stdout.startswith(engine_id.OK + " "), p.stdout)
        self.assertEqual(p.returncode, 0)


class Applied(unittest.TestCase):
    """Every writer asks: from another checkout nothing is written; read-only commands only say so first."""

    def setUp(self):
        self.patch = mock.patch.object(engine_id, "check", return_value=("other", "/the/engine", "這台的引擎在 /the/engine"))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_plugins_apply_write_and_restore_refuse_preview_warns(self):
        sys_path = os.path.join(ENGINE, "ops")
        _sys.path.insert(0, sys_path)
        import io
        from contextlib import redirect_stdout
        import plugins
        for argv in (["apply", "--write"], ["restore", "20261006-000000"]):
            out = io.StringIO()
            with mock.patch.object(plugins, "apply") as ap, mock.patch.object(plugins.applycore, "restore") as rs, \
                    redirect_stdout(out):
                self.assertEqual(plugins.main(argv), 1, argv)
            ap.assert_not_called()
            rs.assert_not_called()
            self.assertIn("/the/engine", out.getvalue())
        out = io.StringIO()
        with mock.patch.object(plugins, "plan", return_value=0), redirect_stdout(out):
            plugins.main(["plan"])
        self.assertTrue(out.getvalue().startswith("⚠ 這不是這台的正式引擎（正式的在 /the/engine）"))

    def test_instance_writing_commands_refuse_status_does_not(self):
        _sys.path.insert(0, os.path.join(ENGINE, "ops"))
        import io
        from contextlib import redirect_stdout
        import instance
        for argv in (["init"], ["join", "x"], ["leave"]):
            with mock.patch.object(instance, "join") as j, redirect_stdout(io.StringIO()):
                self.assertEqual(instance.main(argv), 1, argv)
            j.assert_not_called()
        with mock.patch.object(instance, "status", return_value=0) as st:
            self.assertEqual(instance.main(["status"]), 0)
        st.assert_called_once()

    def test_every_script_asks_first(self):
        with open(os.path.join(ENGINE, "ops", "install.py"), encoding="utf-8") as f:  # install.sh's check (3a.9)
            text = f.read()
        body = text[text.index("def main(argv):"):]
        self.assertLess(body.index("engine_id.refusal(ENGINE"), body.index("os.makedirs("), "asked before anything is made")
        for script in ("chatlog/install.sh", "dcbot/install.sh", "progress/install.sh",
                       "usage/install.sh", "dcbot/uninstall.sh"):
            with open(os.path.join(ENGINE, script), encoding="utf-8") as f:
                text = f.read()
            self.assertIn('altention/engine_id.py" 2>&1)" || true', text, script)
            self.assertIn('"ALT-ENGINE-OK "*) ;;', text, script)
            body = text[text.index("ALT-ENGINE-OK"):]
            before = text[:text.index("EID=")]
            for writer in ("crontab", "pip", "mkdir", "nohup", "touch", "printf"):
                self.assertNotIn(writer + " ", before.split("trap ")[0].split("NOCRON=")[-1], (script, writer))


if __name__ == "__main__":
    unittest.main()
