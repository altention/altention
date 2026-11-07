"""altention/dc/botside.py (C-109): one place says which machine runs the bot; a command reaches its dcctl there once
(the way found with `true`, the command never sent again), and dcctl on another machine hands everything over without
reading its own settings. Through a fake ssh on PATH that logs what it was asked and plays a machine over there."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from altention.dc import botside

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The fake ssh: `ssh -o … ALIAS COMMAND`. Each call is a line in $FAKE/log (alias, command, what came on stdin);
# $FAKE/<alias>.true holds the exit status of `true` by that alias (no file: 255, it does not answer), and
# $FAKE/run.code the command's (default 0). The command's output: "ran" and the command.
FAKE_SSH = r'''#!/bin/sh
while [ "$1" = "-o" ]; do shift 2; done
alias="$1"; shift
if [ "$*" = "true" ]; then
  printf '%s\ttrue\t\n' "$alias" >> "$FAKE/log"
  [ -f "$FAKE/$alias.true" ] && exit "$(cat "$FAKE/$alias.true")"
  exit 255
fi
input="$(cat)"
printf '%s\t%s\t%s\n' "$alias" "$*" "$input" >> "$FAKE/log"
echo "ran $*"
exit "$(cat "$FAKE/run.code" 2>/dev/null || echo 0)"
'''


class Fake(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fake = os.path.join(self.tmp, "fake")
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(self.fake)
        os.makedirs(bindir)
        with open(os.path.join(bindir, "ssh"), "w") as f:
            f.write(FAKE_SSH)
        os.chmod(os.path.join(bindir, "ssh"), 0o755)
        self.env = mock.patch.dict(os.environ, {"FAKE": self.fake, "PATH": bindir + os.pathsep + os.environ["PATH"]})
        self.env.start()
        os.environ.pop(botside.HANDED, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp)

    def answers(self, *aliases):
        for alias in aliases:
            with open(os.path.join(self.fake, alias + ".true"), "w") as f:
                f.write("0")

    def runs_with(self, code):
        with open(os.path.join(self.fake, "run.code"), "w") as f:
            f.write(str(code))

    def log(self):
        try:
            with open(os.path.join(self.fake, "log")) as f:
                return [line.rstrip("\n").split("\t") for line in f]
        except OSError:
            return []

    def commands(self):
        return [row for row in self.log() if row[1] != "true"]


class Run(Fake):
    BOT = ("botbox", {"ssh": ["local", "A", "B"]}, False)

    def test_sent_once_by_the_way_found_with_stdin_and_status_as_they_are(self):
        self.answers("A")
        self.runs_with(3)
        r = botside.run(["edit", "chan", "42"], "新的字".encode("utf-8"), where=self.BOT, stdout=subprocess.PIPE)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.log()[0], ["A", "true", ""])
        self.assertEqual(self.commands(), [["A", "%s=1 ~/'altention/engine'/dcbot/tools/dcctl 'edit' 'chan' '42'"
                                            % botside.HANDED, "新的字"]])
        self.assertIn(b"ran ", r.stdout)

    def test_an_alias_that_does_not_answer_is_passed_by_finding_the_way(self):
        self.answers("B")
        botside.run(["channels"], where=self.BOT, stdout=subprocess.PIPE)
        self.assertEqual([(row[0], row[1]) for row in self.log()][:2], [("A", "true"), ("B", "true")])
        self.assertEqual([row[0] for row in self.commands()], ["B"])

    def test_a_line_lost_while_the_command_ran_is_not_sent_again(self):
        self.answers("A", "B")
        self.runs_with(255)
        with self.assertRaises(botside.NoBot) as e:
            botside.run(["post"], b"{}", where=self.BOT, stdout=subprocess.PIPE)
        self.assertIn("沒有重送", str(e.exception))
        self.assertEqual(len(self.commands()), 1, "B answers too, and is not tried with the command")

    def test_a_command_that_runs_too_long_is_cut_and_said_never_taken_for_done(self):
        """C-125: the SSH command has an overall limit; past it, ssh is stopped and NoBot says so."""
        self.answers("A")
        with open(os.path.join(self.fake, "run.code"), "w") as f:
            f.write("0")
        slow = os.path.join(os.path.dirname(self.fake), "bin", "ssh")
        with open(slow) as f:
            text = f.read()
        with open(slow, "w") as f:
            f.write(text.replace('echo "ran $*"', 'sleep 30; echo "ran $*"'))
        import time
        start = time.time()
        with self.assertRaises(botside.NoBot) as e:
            botside.run(["post"], b"{}", where=self.BOT, timeout=1, stdout=subprocess.PIPE)
        self.assertLess(time.time() - start, 20)
        self.assertEqual(str(e.exception), "連到 botbox 的指令 1 秒還沒結束（經 A），已經停掉；指令可能已經做了，沒有重送")
        self.assertEqual(len(self.commands()), 1, "not sent again")

    def test_the_limit_leaves_room_for_the_longest_wait(self):
        """#code-review on 4ee2ab6: a --wait at its most, dcctl's wait for its lock and a rate limit's, all within it."""
        import re
        with open(os.path.join(ENGINE, "dcbot", "tools", "dcctl"), encoding="utf-8") as f:
            lock = int(re.search(r"^LOCK_SECS = (\d+)", f.read(), re.M).group(1))
        self.assertGreaterEqual(botside.RUN_SECS, botside.WAIT_MOST + lock + 60)

    def test_no_way_says_which_machine_and_what_was_tried(self):
        with self.assertRaises(botside.NoBot) as e:
            botside.run(["channels"], where=self.BOT)
        self.assertEqual(str(e.exception), "連不上 botbox（試過 A、B）")
        self.assertEqual(self.commands(), [])
        with self.assertRaises(botside.NoBot) as e:
            botside.run(["channels"], where=("botbox", {"ssh": ["local"]}, False))
        self.assertIn("沒有它的 ssh 別名", str(e.exception))

    def test_on_the_bots_machine_its_own_dcctl(self):
        repo = os.path.join(self.tmp, "repo")
        tool = os.path.join(repo, "dcbot", "tools", "dcctl")
        os.makedirs(os.path.dirname(tool))
        with open(tool, "w") as f:
            f.write("#!/bin/sh\necho here \"$@\"\nexit 5\n")
        os.chmod(tool, 0o755)
        r = botside.run(["channels"], repo=repo, where=("me", {}, True), stdout=subprocess.PIPE)
        self.assertEqual((r.returncode, r.stdout), (5, b"here channels\n"))
        self.assertEqual(self.log(), [], "no ssh at all")

    def test_one_place_says_which_machine(self):
        """C-53 (a standby taking over) changes bot_machine() alone: run() and hand_over() ask it, nothing else."""
        asked = []

        def bot_machine(cache=None, prog="altention dc"):
            asked.append(prog)
            return self.BOT
        self.answers("A")
        with mock.patch.object(botside, "bot_machine", bot_machine):
            botside.run(["channels"], stdout=subprocess.PIPE)
            with self.assertRaises(SystemExit):
                botside.hand_over(["channels"])
        self.assertEqual(asked, ["altention dc", "dcctl"])


HAND_OVER = r'''
import sys
sys.path.insert(0, %(engine)r)
from altention.dc import botside
botside.bot_machine = lambda cache=None, prog="": %(bot)r
botside.hand_over(sys.argv[1:])
print("went on here")
'''


class HandOver(Fake):
    def hand_over(self, args, bot=("botbox", {"ssh": ["A"]}, False), stdin=None, close=True, env=None):
        p = subprocess.Popen([sys.executable, "-c", HAND_OVER % {"engine": ENGINE, "bot": bot}] + args,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=dict(os.environ, **(env or {})))
        try:
            if stdin is not None:
                p.stdin.write(stdin)
            if close:
                p.stdin.close()
            p.wait(timeout=15)  # a wait on stdin would end here (TimeoutExpired), not hang the run
            out, err = p.stdout.read(), p.stderr.read()
        finally:
            p.kill()
            p.wait()
            for f in (p.stdin, p.stdout, p.stderr):
                f.close()
        return p.returncode, out.decode("utf-8"), err.decode("utf-8")

    def test_a_command_that_reads_no_stdin_comes_back_with_the_pipe_left_open(self):
        """manager 2026-10-30: only post and edit read stdin; `channels` on a pipe nobody closes must not wait on it."""
        self.answers("A")
        self.runs_with(0)
        code, out, err = self.hand_over(["channels"], close=False)
        self.assertEqual(code, 0, err)
        self.assertNotIn("went on here", out)
        self.assertEqual(self.commands()[0][1:], ["%s=1 ~/'altention/engine'/dcbot/tools/dcctl 'channels'" % botside.HANDED, ""])

    def test_post_and_edit_take_their_stdin_along_and_the_status_comes_back(self):
        self.answers("A")
        self.runs_with(4)
        code, out, err = self.hand_over(["edit", "chan", "42"], stdin="改成這樣\n".encode("utf-8"))
        self.assertEqual(code, 4)
        self.assertEqual(self.commands()[0][2], "改成這樣")
        self.assertIn("ran ", out, "its output as it is")

    def test_on_the_bots_machine_dcctl_goes_on_here(self):
        code, out, err = self.hand_over(["channels"], bot=("me", {}, True))
        self.assertEqual((code, out.strip()), (0, "went on here"))
        self.assertEqual(self.log(), [])

    def test_help_goes_on_here(self):
        code, out, err = self.hand_over(["--help"])
        self.assertEqual(out.strip(), "went on here")

    def test_no_way_there_says_so_and_never_runs_here(self):
        code, out, err = self.hand_over(["topic", "x", "y"])
        self.assertNotEqual(code, 0)
        self.assertIn("dcctl：連不上 botbox（試過 A）", err)
        self.assertNotIn("went on here", out)

    def test_a_command_handed_over_is_not_handed_on(self):
        """Two machine lists that disagree (each names the other): refused, not sent round and round."""
        self.answers("A")
        code, out, err = self.hand_over(["channels"], env={botside.HANDED: "1"})
        self.assertNotEqual(code, 0)
        self.assertIn("別台轉來的", err)
        self.assertEqual(self.log(), [])


class TheRealDcctl(Fake):
    """tools/dcctl itself, on a machine whose list puts the bot on another: everything goes there, and this machine's
    bot settings are never opened: here they are a FIFO nobody writes to, which would hang whoever reads it."""

    def test_handed_over_before_its_settings_are_read(self):
        inst = os.path.join(self.tmp, "inst")
        os.makedirs(inst)
        with open(os.path.join(inst, "machines.json"), "w") as f:
            json.dump({"format": 1, "machines": {"botbox": {"ssh": ["A"], "roles": ["dcbot"]}}}, f)
        settings = os.path.join(self.tmp, "settings")
        os.mkfifo(settings)
        self.answers("A")
        self.runs_with(0)
        p = subprocess.Popen(["/usr/bin/python3", os.path.join(ENGINE, "dcbot", "tools", "dcctl"), "channels"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=dict(os.environ, ALT_INSTANCE=inst, DCBOT_ENV=settings))
        try:
            out, err = p.communicate(timeout=20)  # stdin left open until then: `channels` does not read it
        finally:
            p.kill()
        self.assertEqual(p.returncode, 0, err.decode("utf-8"))
        self.assertEqual([row[0] for row in self.commands()], ["A"])
        self.assertIn("dcctl 'channels'", self.commands()[0][1])


class Reach(Fake):
    def test_what_doctor_is_told(self):
        bot = ("botbox", {"ssh": ["A"]}, False)
        with mock.patch.object(botside, "bot_machine", lambda cache=None, prog="": bot):
            self.assertEqual(botside.reach(), {"bot": "botbox", "here": False, "way": None, "tried": ["A"]})
            self.answers("A")
            got = botside.reach()
        self.assertEqual((got["way"], got["dcctl"]), ("A", True), "the fake answers 0 to `test -f`")
        self.assertIn("test -f ~/'altention/engine'/dcbot/tools/dcctl", self.commands()[0][1])

        def none(cache=None, prog=""):
            raise botside.NoBot("找不到")
        with mock.patch.object(botside, "bot_machine", none):
            self.assertEqual(botside.reach(), {"error": "找不到"})


if __name__ == "__main__":
    unittest.main()
