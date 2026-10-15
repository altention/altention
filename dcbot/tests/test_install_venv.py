"""dcbot/install.sh, the block that makes the bot's venv: a venv left without pip by a first try (Debian/Ubuntu
without python3-venv) is made again, and a venv that cannot be made says what to install (support-4's dry run on a
clean ubuntu:24.04). Run on its own, with stand-ins for python3 and for the venv's python: nothing is installed."""
import os
import subprocess
import tempfile
import unittest

from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def block():
    with open(os.path.join(HERE, "install.sh"), encoding="utf-8") as f:
        text = f.read()
    return text[text.index("# venv-make: begin"):text.index("# venv-make: end")]


def script(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, 0o755)


class VenvMake(unittest.TestCase):
    def run_block(self, venv_python=None, venv_ok=True):
        """(exit code, output, what python3 was asked) with .venv/bin/python as given (None: no venv at all)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work, bin_ = os.path.join(tmp.name, "w"), os.path.join(tmp.name, "bin")
        os.makedirs(work)
        log = os.path.join(tmp.name, "python3.log")
        script(os.path.join(bin_, "python3"), 'echo "$@" >> "%s"\n%s' % (log, "exit 0" if venv_ok else
                                                                        'echo "ensurepip is not available"; exit 1'))
        if venv_python is not None:
            script(os.path.join(work, ".venv", "bin", "python"), venv_python)
        r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + block()], cwd=work, capture_output=True, text=True,
                           env=dict(os.environ, PATH=bin_ + os.pathsep + os.environ.get("PATH", "")), timeout=20)
        asked = open(log).read().split("\n")[:-1] if os.path.exists(log) else []
        return r.returncode, r.stdout + r.stderr, asked

    def test_no_venv_is_made(self):
        self.assertEqual(self.run_block()[::2], (0, ["-m venv --clear .venv"]))

    def test_a_venv_left_without_pip_is_made_again(self):
        code, _, asked = self.run_block(venv_python='[ "$*" = "-m pip --version" ] && exit 1; exit 0')
        self.assertEqual((code, asked), (0, ["-m venv --clear .venv"]))

    def test_a_working_venv_is_left_as_it_is(self):
        self.assertEqual(self.run_block(venv_python="exit 0")[::2], (0, []))

    def test_a_venv_that_cannot_be_made_says_what_to_install(self):
        code, out, _ = self.run_block(venv_ok=False)
        self.assertEqual(code, 1)
        self.assertIn("python3-venv", out)


if __name__ == "__main__":
    unittest.main()
