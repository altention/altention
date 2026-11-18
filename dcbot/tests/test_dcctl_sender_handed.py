"""C-172a P1d (C-186 for dcctl, manager 11-17): who runs dcctl on a machine that does not run the bot is looked up
there and carried over, through the real hand_over path: tools/dcctl itself, under a stand-in Claude Code (a process
whose name is `claude`, with an environment of its own), handed by botside over a fake ssh that runs the command on
"the bot's machine" (the same engine, its own machine list). Carried: that Claude's channel, never the command's
own DCBOT_CHANNEL. Not carried (an older engine on the calling machine): a program of the bot's machine, never a
channel's Claude."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest

from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DCCTL = os.path.join(ENGINE, "dcbot", "tools", "dcctl")
HOST = socket.gethostname().split(".")[0]

# `ssh -o … ALIAS COMMAND`: `true` answers; the command runs here as on the bot's machine (its machine list: $THERE),
# with nothing of this side's environment but PATH and HOME; $STRIP set: ALTENTION_SENDER taken out first, as an
# older engine on the calling machine would send it.
FAKE_SSH = r'''#!/bin/sh
while [ "$1" = "-o" ]; do shift 2; done
shift
[ "$*" = "true" ] && exit 0
cmd="$*"
[ -n "$STRIP" ] && cmd="$(printf '%s' "$cmd" | sed "s/ALTENTION_SENDER='[^']*' //")"
printf '%s\n' "$cmd" >> "$LOG"
exec env -i PATH="$PATH" HOME="$HOME" ALT_INSTANCE="$THERE" DCBOT_ENV="$THERE_ENV" /bin/sh -c "$cmd"
'''


class Handed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        bindir, here, there = (os.path.join(self.tmp, d) for d in ("bin", "here", "there"))
        for d in (bindir, here, there, os.path.join(self.tmp, "home")):
            os.makedirs(d)
        with open(os.path.join(bindir, "ssh"), "w") as f:
            f.write(FAKE_SSH)
        os.chmod(os.path.join(bindir, "ssh"), 0o755)
        with open(os.path.join(here, "machines.json"), "w") as f:  # this machine is "lapx"; the bot is on botbox
            json.dump({"format": 1, "machines": {"botbox": {"ssh": ["A"], "roles": ["dcbot"], "engine": ENGINE},
                                                 "lapx": {"ssh": ["local"], "hostname": HOST}}}, f)
        with open(os.path.join(there, "machines.json"), "w") as f:  # over there, this same host is botbox
            json.dump({"format": 1, "machines": {"botbox": {"ssh": ["local"], "roles": ["dcbot"], "hostname": HOST}}}, f)
        open(os.path.join(there, "env"), "w").close()
        self.log = os.path.join(self.tmp, "log")
        self.env = {"PATH": bindir + os.pathsep + os.environ.get("PATH", ""), "HOME": os.path.join(self.tmp, "home"),
                    "ALT_INSTANCE": here, "THERE": there, "THERE_ENV": os.path.join(there, "env"), "LOG": self.log}

    def whoami(self, claude_env, strip=False):
        """dcctl whoami run by a stand-in Claude Code (argv[0] `claude`, `claude_env` its own environment), the
        command's own DCBOT_CHANNEL saying 99: (exit, stdout lines, stderr)."""
        inner = "DCBOT_CHANNEL=99 /usr/bin/python3 %s whoami; exit $?" % DCCTL  # (not the shell's last word: the
        env = dict(self.env, **({"STRIP": "1"} if strip else {}))                # shell stays its parent)
        claude = " ".join("%s=%s" % kv for kv in claude_env.items())
        p = subprocess.run(["/bin/bash", "-c", "env %s bash -c 'exec -a claude /bin/sh -c \"$0\"' %s"
                            % (claude, _quote(inner))], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60, universal_newlines=True)
        return p.returncode, p.stdout.split("\n"), p.stderr

    def test_the_claudes_channel_is_carried_never_the_commands_own(self):
        code, out, err = self.whoami({"DCBOT_CHANNEL": "66"})
        self.assertEqual(code, 0, err)
        self.assertEqual(out[:2], ["channel:66", "<#66> 的 Claude（查證過，從 lapx 轉來）"])
        with open(self.log) as f:
            sent = f.read()
        self.assertIn("ALTENTION_HANDED=1 ALTENTION_SENDER='", sent)

    def test_a_claude_with_no_channel_by_its_machine_and_folder(self):
        code, out, err = self.whoami({})
        self.assertEqual(code, 0, err)
        self.assertEqual(out[0], "claude:lapx:%s" % os.path.basename(os.getcwd()), out)

    def test_nothing_carried_is_a_program_of_the_bots_machine(self):
        code, out, err = self.whoami({"DCBOT_CHANNEL": "66"}, strip=True)
        self.assertEqual(code, 0, err)
        self.assertEqual(out[:2], ["program:botbox:?", "botbox 上的程式（別台轉來，沒帶寄件人）"])
        with open(self.log) as f:
            self.assertNotIn("ALTENTION_SENDER", f.read(), "the fake took it out, as an older engine sends none")


def _quote(word):
    return "'" + word.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    unittest.main()
