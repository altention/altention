"""C-92 a: tmux:// identifiers, and the panes listed and read by them (bot/tmuxref.py, dcremote tmux-list/tmux-read/
tmux-at, altention tmux). Real tmux servers of the test's own, in a folder of its own (tests/tmuxguard.py lets
nothing else through)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from bot import tmuxref
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Ids(unittest.TestCase):
    def test_round_trip(self):
        folder = "/tmp/tmux-1000"
        for path, session, want in ((folder + "/default", "st", "tmux://lap/st:2.0#%7"),
                                    (folder + "/dcbot", "dc-x", "tmux://lap/dcbot/dc-x:2.0#%7"),
                                    ("/var/run/odd sock", "a/b:c", "tmux://lap/%2Fvar%2Frun%2Fodd%20sock/a%2Fb%3Ac:2.0#%7")):
            text = tmuxref.ident("lap", path, session, "2", "0", "%7", folder)
            self.assertEqual(text, want)
            ref = tmuxref.parse(text)
            self.assertEqual((ref["machine"], ref["session"], ref["window"], ref["pane"], ref["pane_id"]), ("lap", session, "2", "0", "%7"))
            self.assertEqual(tmuxref.socket_path(ref["socket"], folder), path)

    def test_without_the_pane_id(self):
        self.assertIsNone(tmuxref.parse("tmux://lap/st:0.1")["pane_id"])

    def test_what_is_refused(self):
        for bad in ("lap/st:0.0", "tmux://lap", "tmux://lap/st", "tmux://lap/st:0", "tmux://lap/st:x.0", "tmux://lap/a/b/st:0.0",
                    "tmux://lap/st:0.0#7", "tmux://lap/st:0.0#%x", "tmux:///st:0.0", "tmux://lap/:0.0", 7, None):
            with self.assertRaises(tmuxref.BadId, msg=bad):
                tmuxref.parse(bad)


class Servers(unittest.TestCase):
    """Two servers of the test's own: the default one of its folder, and one by name."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="dcq")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.socks = [os.path.join(self.folder, "default"), os.path.join(self.folder, "dctest-c92")]
        for i, sock in enumerate(self.socks):
            subprocess.run(["tmux", "-S", sock, "new-session", "-d", "-s", "s%d" % i, "-x", "80", "-y", "20",
                            "sh -c 'printf \"line one\\nline two\\n\"; exec sleep 300'"], check=True, timeout=10)
            self.addCleanup(subprocess.run, ["tmux", "-S", sock, "kill-server"], stderr=subprocess.DEVNULL, timeout=10)
        time.sleep(0.5)

    def panes(self):
        return tmuxref.list_panes("lap", lambda c, pid: c == "claude", folder=self.folder)

    def test_list_every_server(self):
        panes = self.panes()
        self.assertEqual(sorted(p["session"] for p in panes), ["s0", "s1"])
        ids = sorted(p["id"] for p in panes)
        self.assertTrue(ids[0].startswith("tmux://lap/dctest-c92/s1:0.0#%"), ids)
        self.assertTrue(ids[1].startswith("tmux://lap/s0:0.0#%"), "the default server: no socket segment")
        for p in panes:
            self.assertEqual(p["others"], [], "a foreground of this user's")
            self.assertFalse(p["claude"])
            self.assertEqual(tmuxref.parse(p["id"])["pane_id"], p["pane_id"])

    def test_read_by_its_id_and_refused_for_another_pane(self):
        p = next(p for p in self.panes() if p["session"] == "s0")
        got = tmuxref.read(tmuxref.parse(p["id"]), 20, folder=self.folder)
        self.assertIn("line two", got["text"])
        wrong = tmuxref.parse(p["id"].split("#")[0] + "#%9999")
        got = tmuxref.read(wrong, 20, folder=self.folder)
        self.assertEqual((got["code"], got["now"]), (3, p["pane_id"]), "the pane there now is said; never read by the id instead")
        self.assertIn("請重新 list", got["error"])
        self.assertIn("text", tmuxref.read(tmuxref.parse(p["id"].split("#")[0]), 5, folder=self.folder), "no #%: by its place")
        gone = tmuxref.read(tmuxref.parse("tmux://lap/s0:7.0"), 5, folder=self.folder)
        self.assertEqual(gone["code"], 3)

    def test_the_last_lines_and_no_more(self):
        """Tried on another machine 2026-11-03: asked for 8, 24 came (history above the screen and the whole screen)."""
        p = next(p for p in self.panes() if p["session"] == "s0")
        self.assertEqual(tmuxref.read(tmuxref.parse(p["id"]), 1, folder=self.folder)["text"], "line two\n",
                         "the last that holds something; the empty rows under it are not lines")
        self.assertEqual(tmuxref.read(tmuxref.parse(p["id"]), 2, folder=self.folder)["text"], "line one\nline two\n")
        self.assertIn("text", tmuxref.read(tmuxref.parse(p["id"]), 10 ** 6, folder=self.folder))
        self.assertIn("text", tmuxref.read(tmuxref.parse(p["id"]), 0, folder=self.folder))

    def test_through_dcremote(self):
        env = dict(os.environ, TMUX_TMPDIR=os.path.dirname(self.folder))
        # dcremote's folder is TMUX_TMPDIR/tmux-<uid>: a link to the test's own there
        mine = os.path.join(tempfile.mkdtemp(prefix="dcq"), "tmux-%d" % os.getuid())
        self.addCleanup(shutil.rmtree, os.path.dirname(mine), True)
        os.symlink(self.folder, mine)
        env["TMUX_TMPDIR"] = os.path.dirname(mine)
        run = lambda *a: subprocess.run([sys.executable, os.path.join(HERE, "tools", "dcremote")] + list(a),  # noqa: E731
                                        env=env, stdout=subprocess.PIPE, universal_newlines=True, timeout=30)
        r = run("tmux-list", "lap")
        panes = json.loads(r.stdout)
        self.assertEqual(sorted(p["session"] for p in panes), ["s0", "s1"])
        ident = next(p["id"] for p in panes if p["session"] == "s1")
        r = run("tmux-read", ident, "20")
        self.assertEqual(r.returncode, 0)
        self.assertIn("line one", json.loads(r.stdout)["text"])
        r = run("tmux-at", ident)
        self.assertEqual(json.loads(r.stdout)["pane"], tmuxref.parse(ident)["pane_id"])
        r = run("tmux-read", ident.split("#")[0] + "#%9999")
        self.assertEqual(r.returncode, 3)
        r = run("tmux-at", ident.split("#")[0] + "#%9999")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(run("tmux-read", "tmux://nope").returncode, 2)
        for word in ("tmux-list", "tmux-read", "tmux-at", "claude-at"):  # a word missing is 2: 1 is only "too old"
            r = run(word)
            self.assertEqual((r.returncode, json.loads(r.stdout)["code"]), (2, 2), word)
        r = run("claude-at", str(os.getpid()))
        self.assertEqual((r.returncode, r.stdout), (0, "0\n"), "a test is not Claude Code")


class Foreground(unittest.TestCase):
    def test_this_users_own(self):
        p = subprocess.Popen(["sleep", "30"])
        self.addCleanup(p.kill)
        self.assertIn(tmuxref.foreground(os.getpid()), ([], None), "a process with no terminal: nobody else, or not known")

    def test_not_known_is_none(self):
        self.assertIsNone(tmuxref.foreground(10 ** 9))


class Masked(unittest.TestCase):
    """manager 2026-11-03 (#code-review on fdff0dc): what altention tmux read prints is masked as the bot masks a
    screen it posts (C-127): a plugin may post it as it is. Masked where it is printed, whatever engine read it."""
    KEY = "sk-ant-api03-" + "Ab1" * 30 + "-AAAA"  # (a made-up one: the shape the pattern knows)

    def printed(self, text):
        import contextlib
        import io
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("alt_tmux_t", os.path.join(os.path.dirname(HERE), "altention", "tmux.py"))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with mock.patch.object(cli, "dcremote", lambda machine, *a: (0, json.dumps({"text": text, "pane_id": "%1"}), "")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["read", "tmux://lap/st:0.0#%1", "20"]), 0)
        return out.getvalue()

    def test_a_key_on_one_row(self):
        out = self.printed("$ export ANTHROPIC_API_KEY=%s\n$ \n" % self.KEY)
        self.assertNotIn(self.KEY[:20], out)
        self.assertIn("[REDACTED]", out)
        self.assertTrue(out.endswith("$ \n"), "the rest as it was")

    def test_a_key_wrapped_across_rows(self):
        width = 40
        line = "$ echo " + self.KEY
        rows = [line[i:i + width] for i in range(0, len(line), width)]
        out = self.printed("\n".join(rows) + "\n")
        for piece in (self.KEY[:10], self.KEY[30:40], self.KEY[-20:-5]):
            self.assertNotIn(piece, out.replace("\n", ""), piece)

    def test_masked_where_it_is_read_and_again_the_same(self):
        """dcremote tmux-read masks on the machine the pane is on (a plugin may run it over its own SSH line);
        altention tmux read masks again before it prints: twice is once."""
        width = 40
        line = "$ echo " + self.KEY
        texts = ["$ export K=%s\n" % self.KEY, "\n".join(line[i:i + width] for i in range(0, len(line), width)) + "\n",
                 "line one\n", "x " * 30 + "\n" + "sk-ant" + "\n"]
        for text in texts:
            once = tmuxref.masked(text)
            self.assertEqual(tmuxref.masked(once), once, text)
        self.assertNotIn(self.KEY[:20], tmuxref.masked(texts[0]))

    def test_read_itself_masks(self):
        from unittest import mock
        with mock.patch.object(tmuxref, "at", lambda ref, folder=None: ("/s", "%1")), \
                mock.patch.object(tmuxref, "tmux", lambda *a: (0, "$ export K=%s\n" % self.KEY, "")):
            got = tmuxref.read(tmuxref.parse("tmux://lap/st:0.0#%1"), 5)
        self.assertNotIn(self.KEY[:20], got["text"])
        self.assertIn("[REDACTED]", got["text"])

    def test_nothing_to_mask_is_printed_as_it_is(self):
        self.assertEqual(self.printed("line one\nline two\n"), "line one\nline two\n")


def cli_module():
    """altention/tmux.py loaded as a module of the test's own (it puts the engine on the path itself)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("alt_tmux_k", os.path.join(os.path.dirname(HERE), "altention", "tmux.py"))
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


class KeptConnection(unittest.TestCase):
    """ACT-71 A: altention tmux keeps its ssh connection to another machine open (ControlMaster, 10 minutes), by a
    socket in a folder of this user's own that only they can open, never the bot's."""

    def setUp(self):
        self.cli = cli_module()
        self.base = tempfile.mkdtemp(prefix="dcq")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.cli.CONTROL_BASE = self.base
        self.dir = os.path.join(self.base, "altention-ssh-%d" % os.getuid())

    def test_made_for_this_user_alone(self):
        self.assertEqual(self.cli.control_dir(), self.dir)
        self.assertEqual(os.stat(self.dir).st_mode & 0o777, 0o700)
        cmd = self.cli.ssh_command()
        self.assertIn("ControlPath=%s/%%C" % self.dir, cmd)
        self.assertIn("ControlMaster=auto", cmd)
        self.assertIn("ControlPersist=600", cmd)
        self.assertIn("ServerAliveInterval=15", cmd, "a dead kept line is found out and made anew (#code-review)")
        self.assertNotIn("dcbot", " ".join(cmd), "never the bot's connections")
        self.assertEqual(self.cli.control_dir(), self.dir, "there already: used as it is")

    def test_one_others_can_open_is_not_used(self):
        os.mkdir(self.dir, 0o700)
        os.chmod(self.dir, 0o755)
        self.assertIsNone(self.cli.control_dir())
        self.assertEqual(self.cli.ssh_command(), self.cli.SSH, "nothing kept: each call connects anew")

    def test_a_link_or_a_file_is_not_used(self):
        target = tempfile.mkdtemp(prefix="dcq")
        self.addCleanup(shutil.rmtree, target, True)
        os.chmod(target, 0o700)
        os.symlink(target, self.dir)
        self.assertIsNone(self.cli.control_dir())
        os.unlink(self.dir)
        open(self.dir, "w").close()
        self.assertIsNone(self.cli.control_dir())

    def test_cannot_be_made(self):
        self.cli.CONTROL_BASE = os.path.join(self.base, "not-there")
        self.assertIsNone(self.cli.control_dir())

    def test_the_path_fits_a_socket(self):
        self.cli.CONTROL_BASE = "/tmp"
        self.assertLess(len("/tmp/altention-ssh-%d/" % os.getuid()) + 40, 104, "macOS's room for a socket's path")


class OneLookAtProcesses(unittest.TestCase):
    """ACT-71 A: list_panes reads every process's group once for the whole listing (it read all of /proc for each
    pane: 43 panes took 0.7 s on another machine), and foreground() says the same with it as without."""

    def test_the_same_answer(self):
        if not os.path.isdir("/proc"):
            self.skipTest("Linux only")
        self.assertEqual(tmuxref.foreground(os.getpid(), tmuxref.groups()), tmuxref.foreground(os.getpid()))

    def test_read_once_a_listing(self):
        from unittest import mock
        reads = []
        real = tmuxref.groups
        rows = "".join("%%%d\ts\t0\t%d\tzsh\t/srv\t%d\n" % (i, i, os.getpid()) for i in range(5))
        with mock.patch.object(tmuxref, "groups", lambda: reads.append(1) or real()), \
                mock.patch.object(tmuxref, "sockets", lambda folder: ["/s"]), \
                mock.patch.object(tmuxref, "tmux", lambda *a: (0, rows, "")):
            panes = tmuxref.list_panes("lap", lambda c, pid: False, folder="/f")
        self.assertEqual(len(panes), 5)
        self.assertEqual(reads, [1] if os.path.isdir("/proc") else [])


class Cli(unittest.TestCase):
    def run_(self, *args):
        return subprocess.run([sys.executable, os.path.join(os.path.dirname(HERE), "altention", "tmux.py")] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)

    def test_help_and_wrong_arguments(self):
        self.assertEqual(self.run_("-h").returncode, 0)
        self.assertEqual(self.run_("read", "nope").returncode, 2)
        self.assertEqual(self.run_("read", "tmux://lap/st:0.0", "x").returncode, 2)
        self.assertEqual(self.run_("what").returncode, 2)
        self.assertEqual(self.run_("list", "a", "b").returncode, 2)


if __name__ == "__main__":
    unittest.main()
