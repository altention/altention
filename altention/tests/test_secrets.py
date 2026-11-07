"""altention.secrets: where secrets lie, for the guard that refuses tools reading them (backlog K-03)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import shlex
import sys
import tempfile
import time
import unittest
from unittest import mock

from altention import secrets

HOME = "/h/lap"  # (made up: a path under /home/ would be counted by the privacy scan)


class Listed(unittest.TestCase):
    def setUp(self):
        self.listed = [HOME + p[1:] for p in secrets.DEFAULT]

    def secret(self, path, cwd=HOME):
        return secrets.is_secret(path, self.listed, cwd=cwd)

    def test_the_default_list(self):
        for path in ("~/.config/dcbot/env", HOME + "/.config/dcbot/env", HOME + "/.config/dcbot", HOME + "/.config/dcbot/a/b",
                     HOME + "/.ssh/id_ed25519", HOME + "/.ssh/id_rsa", HOME + "/.ssh/my_key",
                     HOME + "/.ssh/github-key-2"):
            self.assertTrue(self.secret(path.replace("~", HOME)), path)

    def test_what_is_not_secret(self):
        """~/.ssh/config and known_hosts: the machines' settings rest on them, and the owner has Claude edit them."""
        for path in (HOME + "/.config/life/google.json", HOME + "/.ssh/config", HOME + "/.ssh/known_hosts", HOME + "/.ssh/authorized_keys", HOME + "/.ssh/id_rsa.pub",
                     HOME + "/.ssh/my_key.pub", HOME + "/.config/dcbotx/env", HOME + "/.config/life/other.json", "/etc/passwd",
                     HOME + "/.ssh", HOME + "/.config", "", None):
            self.assertFalse(self.secret(path), path)

    def test_relative_paths_and_links(self):
        self.assertTrue(self.secret(".config/dcbot/env", cwd=HOME))
        self.assertTrue(self.secret("../.ssh/id_rsa", cwd=HOME + "/work"))
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, ".config", "dcbot")
            os.makedirs(target)
            with open(os.path.join(target, "env"), "w") as f:
                f.write("x")
            os.symlink(os.path.join(target, "env"), os.path.join(tmp, "harmless.txt"))
            listed = [tmp + p[1:] for p in secrets.DEFAULT]
            self.assertTrue(secrets.is_secret(os.path.join(tmp, "harmless.txt"), listed), "a link to it is it")

    def test_commands_that_name_a_secret(self):
        for command, named in (("cat ~/.config/dcbot/env", "~/.config/dcbot/env"), ("cat $HOME/.ssh/id_rsa", "$HOME/.ssh/id_rsa"),
                               ("cat ${HOME}/.ssh/id_rsa", "${HOME}/.ssh/id_rsa"), ("cd ~/.config/dcbot && cat env", "~/.config/dcbot"),
                               ("grep TOKEN %s/.config/dcbot/env" % HOME, HOME + "/.config/dcbot/env"),
                               ("cat '~/.config/dcbot/env'", "~/.config/dcbot/env"), ("base64 <~/.ssh/id_ed25519", "~/.ssh/id_ed25519")):
            self.assertEqual(secrets.mentions(command, self.listed, home=HOME), [named], command)

    def test_commands_that_do_not(self):
        for command in ("vim ~/.ssh/config", "cat ~/.ssh/authorized_keys", "cat ~/.ssh/id_rsa.pub", "ls ~/.ssh/", "ls ~/.config/dcbotx", "echo .config/dcbot",
                        "git log --oneline", "cat ~/.config/life/google.json.example", "", None):
            self.assertEqual(secrets.mentions(command, self.listed, home=HOME), [], command)

    def test_fast(self):
        """Before every tool call of every Claude Code: a check is far under a millisecond."""
        began = time.perf_counter()
        for _ in range(2000):
            self.secret(HOME + "/work/notes.md")
            secrets.mentions("git status && ls -la src/ && python3 -m pytest -q", self.listed, home=HOME)
        self.assertLess((time.perf_counter() - began) / 2000, 0.001)


class ReviewedForms(unittest.TestCase):
    """#code-review on 8f3f145 (manager's ruling 2026-10-14): what a command or a tool writes out in full, in the ways
    a shell reads it, is caught; what the ruling leaves out is not."""

    def setUp(self):
        self.listed = [HOME + p[1:] for p in secrets.DEFAULT]

    def hit(self, command, fold=False):
        return secrets.mentions(command, self.listed, home=HOME, fold=fold)

    def test_patterns_that_reach_a_secret(self):
        for command in ("cat ~/.ssh/*", "cat ~/.config/dcbot*/env", "cat ~/.config/dcbo?/env", "cat ~/.config/{dcbot,x}/env",
                        "cat ~/.config/*/env", "cat ~/.c*/dcbot/env", "cat ~/.ssh/id_[re]*"):
            self.assertTrue(self.hit(command), command)
        for command in ("cat ~/.ssh/*.pub", "cat ~/.ssh/known_*", "cat ~/work/*.py", "cat /etc/*"):
            self.assertEqual(self.hit(command), [], command)

    def test_relative_paths_from_where_the_command_starts(self):
        """(#code-review on b3370e8) cwd = home: the three examples; a cd in the command is not followed (a known limit)."""
        for command in ("cat .config/dcbot/env", "grep -r TOKEN .config", "cat ./.ssh/id_rsa", "tar czf x.tgz .ssh"):
            self.assertTrue(secrets.mentions(command, self.listed, home=HOME, cwd=HOME), command)
        self.assertTrue(secrets.mentions("cat dcbot/env", self.listed, home=HOME, cwd=HOME + "/.config"))
        self.assertTrue(secrets.mentions("cat ../.ssh/id_rsa", self.listed, home=HOME, cwd=HOME + "/work"))
        for command in ("cat .config/nvim/init.vim", "ls .config", "grep -r TOKEN work", "git log -p", "echo .config"):
            self.assertEqual(secrets.mentions(command, self.listed, home=HOME, cwd=HOME), [], command)
        self.assertEqual(secrets.mentions("cat .config/dcbot/env", self.listed, home=HOME), [], "no cwd given: as before")
        self.assertEqual(secrets.mentions("cd .config && cat dcbot/env", self.listed, home=HOME, cwd=HOME), [], "after a cd: a known limit")
        self.assertTrue(secrets.mentions("cd .config/dcbot && cat env", self.listed, home=HOME, cwd=HOME), "(the folder itself is listed: ~/.config/dcbot/**)")
        self.assertTrue(secrets.holds(".config", self.listed, home=HOME, cwd=HOME))
        self.assertFalse(secrets.holds(".config", self.listed, home=HOME), "no cwd: as before")
        self.assertFalse(secrets.holds(".", self.listed, home=HOME, cwd=HOME), "home itself")

    def test_a_key_handed_over_to_be_used(self):
        """(manager 2026-10-14) ssh/scp/sftp -i, ssh-add, ssh-keygen -y -f use the key; anything else in the command is judged."""
        for command in ("ssh -i ~/.ssh/id_ed25519 lap", "scp -i ~/.ssh/id_ed25519 a lap:b", "sftp -i ~/.ssh/id_rsa lap",
                        "ssh-add ~/.ssh/id_ed25519", "ssh-add -t 3600 ~/.ssh/id_rsa", "ssh-keygen -y -f ~/.ssh/id_ed25519",
                        "ssh -o BatchMode=yes -i ~/.ssh/id_rsa lap uptime"):
            self.assertEqual(self.hit(command), [], command)
        for command in ("ssh -i ~/.ssh/id_ed25519 lap cat ~/.ssh/id_ed25519", "ssh-keygen -f ~/.ssh/id_ed25519 -p",
                        "ssh-keygen -l -f ~/.ssh/id_rsa", "cat ~/.ssh/id_rsa | ssh lap 'cat > k'", "scp ~/.ssh/id_rsa lap:",
                        "ssh -i ~/.ssh/id_rsa lap && cat ~/.ssh/id_rsa", "ssh lap -F ~/.ssh/id_rsa"):
            self.assertTrue(self.hit(command), command)

    def test_quotes_and_other_spellings(self):
        for command in ('cat "$HOME"/.config/dcbot/env', 'cat ~/".config/dcbot/env"', 'cat "${HOME}/.config/dcbot/env"',
                        "cat ~/.config//dcbot/env", "cat ~/.config/./dcbot/env", "cat ~/.config/x/../dcbot/env",
                        "cat --file=~/.config/dcbot/env", "scp lap:~/.ssh/id_rsa ."):
            self.assertTrue(self.hit(command), command)
        with mock.patch.object(secrets, "_user", return_value="lap"):
            self.assertTrue(self.hit("cat ~lap/.config/dcbot/env"))
            self.assertEqual(self.hit("cat ~other/.config/dcbot/env"), [], "another user's home is not this one's")

    def test_a_folder_above_a_secret_given_to_what_reads_into_it(self):
        for command in ("grep -r TOKEN ~/.config", "grep -R TOKEN ~/.ssh", "rg TOKEN ~/.config", "find ~/.ssh -type f", "tar cz ~/.ssh",
                        "zip -r out.zip ~/.config", "rsync -a ~/.ssh/ lap:/tmp/", "cp -r ~/.ssh /tmp/x", "ls -R ~/.config",
                        "sudo grep -r x ~/.config/dcbot"):
            self.assertTrue(self.hit(command), command)
        for command in ("ls ~/.config", "ls -la ~/.ssh", "du -sh ~/.config", "cd ~/.ssh", "grep x ~/.config", "cp ~/.ssh/config /tmp",
                        "chmod 700 ~/.ssh", "vim ~/.ssh/config"):
            self.assertEqual(self.hit(command), [], command)

    def test_what_the_ruling_leaves_as_a_known_limit(self):
        for command in ("cd ~/.config && cat dcbot/env", "grep -r TOKEN ~", "grep -r TOKEN /", "f=env; cat ~/.config/dcbot/$f",
                        "python3 -c 'open(\"x\")'", "echo 'cat ~/.ssh/*' | sh",
                        "flock /tmp/l $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)"):  # (C-108: a $(…)'s output run by a program not listed)
            self.assertEqual(self.hit(command)[:0], [], command)  # (whatever is said of these: none is promised)
        self.assertEqual(self.hit("cd ~/.config && cat dcbot/env"), [], "a relative path after cd: not seen")
        self.assertEqual(self.hit("grep -r TOKEN ~"), [], "a search of the whole home folder: not judged")

    def test_a_file_system_that_does_not_tell_case_apart(self):
        """macOS (APFS): ~/.SSH/ID_ED25519 opens the private key; there the comparison is casefolded."""
        self.assertTrue(secrets.is_secret(HOME + "/.SSH/ID_ED25519", self.listed, fold=True))
        self.assertFalse(secrets.is_secret(HOME + "/.SSH/ID_ED25519", self.listed, fold=False), "Linux: another file")
        self.assertTrue(self.hit("cat ~/.SSH/id_ed25519", fold=True))
        self.assertEqual(self.hit("cat ~/.SSH/id_ed25519", fold=False), [])
        self.assertTrue(secrets.holds(HOME + "/.CONFIG", self.listed, home=HOME, fold=True))
        self.assertEqual(secrets.FOLD, sys.platform == "darwin")

    def test_folders_a_search_tool_is_handed(self):
        for folder, holds in ((HOME + "/.config", True), (HOME + "/.ssh", True), (HOME + "/.config/dcbot", True), ("~/.config", True),
                              (HOME, False), ("/", False), ("/h", False), (HOME + "/work", False), ("relative", False), ("", False)):
            self.assertEqual(secrets.holds(folder, self.listed, home=HOME), holds, folder)

    def test_still_fast(self):
        began = time.perf_counter()
        for _ in range(500):
            self.hit("git status && ls -la src/ && python3 -m pytest -q 'tests/test_x.py' && grep -r foo src/")
        self.assertLess((time.perf_counter() - began) / 500, 0.002)


class Searched(unittest.TestCase):
    """C-126 (manager 2026-11-03): a search's pattern is looked at too, and judged broadly: the folder a glob starts
    from, the home folder and the root included."""

    def setUp(self):
        self.listed = [HOME + p[1:] for p in secrets.DEFAULT] + ["/etc/mine/key.pem"]

    def reach(self, base, pattern=None, deep=True, cwd=HOME + "/work", any_level=False):
        return secrets.searched(base, pattern, self.listed, home=HOME, cwd=cwd, deep=deep, any_level=any_level)

    def test_glob_patterns(self):
        """Glob as dchook judges it: the whole tree (manager), a last name with no wildcard looked for at every level
        under its folder (ripgrep's way: #code-review-2 on e815c9b)."""
        for base, pattern in ((None, "~/.ssh/*"), (None, HOME + "/.config/dcbot/*"), (None, "~/.c*/dcbot/env"),
                              (HOME, "**/*"), (HOME, ".ssh/id_*"), (None, "/h/*/.ssh/id_rsa"), ("~", "**/env"),
                              (None, "**/../../.ssh/*"), ("/", "etc/**/*.pem"), (None, "~/.config/altention/secrets/core/*"),
                              (HOME + "/.config", "dcbot"), (None, "$HOME/.ssh/*key*"), (HOME + "/.config", "env"),
                              (HOME + "/.config", "discord-token"), ("~", "id_rsa"), (None, HOME + "/id_rsa"),
                              (HOME, "*.md"), (HOME, "notes.md"), (HOME + "/.ssh", "config")):
            self.assertIsNotNone(self.reach(base, pattern, any_level=True), (base, pattern))
        self.assertIsNotNone(self.reach(None, "id_rsa", cwd=HOME, any_level=True), "a conversation in the home folder")
        for base, pattern in ((None, "**/*.py"), (None, "src/*.md"), (None, "a.py"), (HOME + "/work", "dcbot/env")):
            self.assertIsNone(self.reach(base, pattern, any_level=True), (base, pattern))
        self.assertIsNone(self.reach(None, "**/*", cwd=None, any_level=True), "nothing absolute to start from: not judged")

    def test_grep_trees(self):
        for base in ("~", HOME, "/", "/h", HOME + "/.config", ".config", "/etc", "~/.ssh"):
            self.assertIsNotNone(self.reach(base, cwd=HOME), base)
        for base in (HOME + "/work", "src", HOME + "/.ssh/config", "/tmp"):
            self.assertIsNone(self.reach(base, cwd=HOME), base)
        self.assertIsNotNone(self.reach(HOME + "/work", "~/.ssh/*"), "an absolute glob looks elsewhere")
        self.assertIsNotNone(secrets.searched(HOME + "/.SSH", None, self.listed, home=HOME, fold=True))


class TextACommandHandsOver(unittest.TestCase):
    """C-108 (manager 2026-10-28): a heredoc's body or a quoted string is text the command hands over, not its
    arguments; a pattern there is not expanded, so only a path written out in full counts. Text a shell runs is
    commands. What the reader does not follow is judged by lines, as before (refuses more)."""

    GLOB = "cat ~/.ss[h]/id_rsa"  # a pattern that reaches a key, while the same text taken literally names no secret

    def setUp(self):
        self.listed = [HOME + p[1:] for p in secrets.DEFAULT]

    def hit(self, command):
        return secrets.mentions(command, self.listed, home=HOME, cwd=HOME + "/work")

    def test_a_doc_comment_in_text_goes_through(self):
        for command in ("cat > a.js <<'EOF'\n/**\n * Adds.\n */\nfunction add() {}\nEOF",
                        "cat > a.js <<EOF\n/**\n * Adds.\n */\nEOF",
                        "cat > a.js <<-EOF\n\t/**\n\tEOF",
                        "python3 -c \"\nprint('''\n/**\n''')\n\"",
                        "node -e '\n/**\n * x\n */\nconsole.log(1)\n'",
                        "echo '/**'", 'echo "/**"', "cat '/**'", "echo \\/\\*\\*",
                        "cat > n.md <<'EOF'\n" + self.GLOB + "\nEOF",
                        "cat > n.md <<'EOF'\n$(" + self.GLOB + ")\nEOF"):
            self.assertEqual(self.hit(command), [], command)

    def test_arguments_are_judged_as_before(self):
        for command in ("cat /**", "ls /**/env", "cat ~/.ssh/*", self.GLOB, "cat ~/.ssh/id_[re]*",
                        "cat > a.js <<'EOF' && cat /**\n/**\nEOF"):
            self.assertTrue(self.hit(command), command)

    def test_a_path_written_out_in_text_still_counts(self):
        for command in ("cat > n.md <<'EOF'\nthe token is in ~/.config/dcbot/env\nEOF",
                        "cat > n.md <<EOF\ncat $HOME/.ssh/id_rsa\nEOF",
                        "cat > n.md <<'EOF'\ngrep -r TOKEN ~/.config\nEOF",
                        "python3 -c \"\nopen('x')\nprint('''\n~/.config/dcbot/env\n''')\n\"",
                        "echo '~/.config/dcbot/env'",
                        "echo \"bot_here = ~/.config/dcbot/env); then\"", "git commit -m 'keys: ~/.ssh/id_rsa and more'",
                        "grep -E 'dcpost|~/.config/dcbot/env|x' f", "M='see ~/.config/dcbot/env; then'; echo \"$M\"", "cat > n.md <<'EOF'\n~/.ssh/id_rsa is the key\nEOF"):
            self.assertTrue(self.hit(command), command)

    def test_what_the_shell_runs_in_text(self):
        for command in ("cat > n.md <<EOF\n$(" + self.GLOB + ")\nEOF", "cat > n.md <<EOF\n`" + self.GLOB + "`\nEOF",
                        'echo "$(' + self.GLOB + ')"', "echo `" + self.GLOB + "`", "x=$(" + self.GLOB + ")",
                        'echo "\n$(' + self.GLOB + ')\n"'):
            self.assertTrue(self.hit(command), command)

    def test_text_handed_to_a_shell(self):
        g = self.GLOB
        for command in ("bash -c '%s'" % g, "sh -c \"%s\"" % g, "bash -c '\n%s\n'" % g, "zsh -c 'cd /tmp && %s'" % g,
                        "timeout 60 bash -c '%s'" % g, "env X=1 sh -c '%s'" % g, "sudo bash -c '%s'" % g,
                        "nohup sh -c '%s' &" % g, "nice -n 5 bash -c '%s'" % g, "ls | xargs -I{} sh -c '%s'" % g,
                        "ssh lap '%s'" % g, "ssh -i ~/.ssh/id_rsa lap '%s'" % g, "eval '%s'" % g,
                        "bash <<'EOF'\n%s\nEOF" % g, "cat <<'EOF' | bash\n%s\nEOF" % g, "ssh lap <<'EOF'\n%s\nEOF" % g,
                        "ssh lap bash <<EOF\n%s\nEOF" % g, "cat <<'EOF' |\n%s\nEOF\nsh" % g,
                        "bash -c 'bash -c \"%s\"'" % g):
            self.assertTrue(self.hit(command), command)
        p, full = "~/.ss[h]/id_rsa", HOME + "/.ss[h]/id_rsa"  # (#code-review on be4b29d: a shell over there reads them again)
        for command in ("ssh lap cat '%s'" % p, "ssh lap cat ~/.ss\\[h\\]/id_rsa", 'ssh lap cat "%s"' % full,
                        "eval cat '%s'" % p, "eval 'cat' \"%s\"" % full, "watch cat '%s'" % p,
                        "ssh lap 'cat %s'" % p, "bash -c cat\\ '%s'" % p, "ssh h cat '/**'",
                        "su lap -c cat '%s'" % p, "tmux send-keys -t w cat '%s' Enter" % p,
                        # (#code-review-2 on be4b29d)
                        "eval cat '~/.ssh/*'", "ssh lap cat '~/.ssh/*'", 'ssh lap cat "~/.ssh/*"',
                        "watch cat '~/.ssh/*'", "ssh lap cat '%s'" % p,
                        "bash -c \"$(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)\"", "source /dev/stdin <<'EOF'\ncat ~/.ssh/*\nEOF",
                        ". /dev/stdin <<'EOF'\ncat ~/.ssh/*\nEOF", "sudo source /dev/stdin <<EOF\ncat ~/.ssh/*\nEOF",
                        # (#code-review-2 on 4324c8e, manager: $(…) in a command's name runs what it prints)
                        "$(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)", "bash <<'OUT'\n$(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)\nOUT",
                        "X=1 \"$(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)\"", "`echo 'cat ~/.ssh/*'`",
                        # (#code-review on a829a2b: after reserved words and launchers, every word may be the command)
                        "timeout 5 $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)", "nice $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)",
                        "env -i $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)", "if $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n); then :; fi",
                        "nice -n 5 $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)", "while true; do $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n); done",
                        "! $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)", "xargs $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n) < /dev/null",
                        # (#code-review-2 on b062ca1, the manager's ruling: time with options, redirections first, the
                        # shell's own builtin, coproc, function)
                        "time -p $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)", "2>/dev/null $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)",
                        ">/tmp/o $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)", "> /tmp/o $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)",
                        "builtin $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)", "coproc $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n)",
                        "function f { $(cat <<'EOF'\ncat ~/.ssh/*\nEOF\n); }"):
            self.assertTrue(self.hit(command), command)
        for command in ("ssh -i ~/.ssh/id_rsa lap 'uptime'", "bash -c 'echo hi'", "ssh lap <<'EOF'\nuptime\nEOF",
                        "cat > a.js <<'EOF'\n/**\nEOF\nbash build.sh",
                        "git commit -m \"$(cat <<'EOF'\nfix the /** comment\nEOF\n)\"", "find . -name '*.py' <<'EOF'\n/**\nEOF",
                        "msg=$(cat <<'EOF'\nfix the /** comment\nEOF\n)", "git commit -m \"$(cat <<'EOF'\nsee ~/.ssh/*\nEOF\n)\"",
                        "echo $(cat <<'EOF'\nsee ~/.ssh/*\nEOF\n)", "if true; then msg=$(cat <<'EOF'\n/**\nEOF\n); fi",
                        "cat > /tmp/o \"$(cat <<'EOF'\n/**\nEOF\n)\"", "git commit -m \"$(cat <<'EOF'\n/**\nEOF\n)\" 2>/dev/null"):
            self.assertEqual(self.hit(command), [], command)

    def test_a_pattern_only_the_files_there_resolve(self):
        """~/.ss?/id_rsa reaches the key only through the files there (#code-review-2 on be4b29d, in a scratch home)."""
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".ssh"))
            for name in ("id_rsa", "id_rsa.pub"):
                with open(os.path.join(home, ".ssh", name), "w") as f:
                    f.write("x")
            listed = [home + p[1:] for p in secrets.DEFAULT]
            for command in ("eval cat '~/.ss?/id_rsa'", "ssh localhost cat '~/.ss?/id_rsa'", "cat ~/.ss?/id_rsa"):
                self.assertTrue(secrets.mentions(command, listed, home=home), command)
            self.assertEqual(secrets.mentions("echo '~/.ss?/id_rsa'", listed, home=home), [], "quoted, no shell: literal")

    def test_what_the_reader_does_not_follow_is_judged_by_lines(self):
        for command in ("echo 'not closed\n/**", "cat <<EOF\n/**\n", "echo $'a'\n/**", "cat > a <<EOF\n`odd\n/**\nEOF",
                        "cat <<$X\n/**\n$X", "echo $(oops\n/**", "echo \"open\n/**"):
            self.assertEqual(self.hit(command), ["/**"], command)

    def test_a_fault_in_the_reader_refuses_more(self):
        """The guard lets a call through when it fails: a reader that trips must fall back, not raise."""
        with mock.patch.object(secrets, "_lex", side_effect=IndexError("x")):
            self.assertEqual(self.hit("cat > a.js <<'EOF'\n/**\nEOF"), ["/**"])
            self.assertTrue(self.hit("cat ~/.config/dcbot/env"))

    def test_deep_nesting_still_refused(self):
        deep = self.GLOB
        for _ in range(8):
            deep = "bash -c %s" % shlex.quote(deep)
        self.assertTrue(self.hit(deep))

    def test_comments_are_text(self):
        self.assertEqual(self.hit("# don't stop\ncat > a.js <<'EOF'\n/**\nEOF"), [], "an apostrophe in a comment is no unclosed quote")
        self.assertEqual(self.hit("ls  # see /** below"), [])
        self.assertTrue(self.hit("ls  # the token: ~/.config/dcbot/env"))
        self.assertTrue(self.hit("# x\ncat /**"))
        self.assertTrue(self.hit("echo a#b && cat /**"))

    def test_braces_in_quotes_are_not_expanded_and_never_multiply_for_long(self):
        self.assertTrue(self.hit("cat ~/.config/{dcbot,x}/env"))
        self.assertEqual(self.hit("cat '~/.config/{dcbot,x}/env'"), [], "the shell does not expand braces in quotes")
        row = ",".join('{"m":"%d","v":%d,"s":"A"}' % (k, k) for k in range(12))
        for command in ("python3 - <<'EOF'\nspec = {\"data\":{\"values\":[%s]}}\nEOF" % row, "echo '%s'" % row, "echo %s" % row):
            began = time.perf_counter()
            self.assertEqual(self.hit(command), [], command[:40])
            self.assertLess(time.perf_counter() - began, 0.5, command[:40])
        self.assertEqual(len(secrets._braces("{a,b}" * 20)), secrets.BRACES)

    def test_still_fast_with_a_long_heredoc(self):
        body = "\n".join("const x%d = '/a/b/c'; // a line of a file being written" % k for k in range(2000))
        began = time.perf_counter()
        self.hit("cat > big.js <<'EOF'\n" + body + "\nEOF")
        self.assertLess(time.perf_counter() - began, 0.5)


class Instance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)

    def settings(self, text):
        with open(os.path.join(self.tmp.name, "plugins.json"), "w", encoding="utf-8") as f:
            f.write(text if isinstance(text, str) else json.dumps(text))

    def test_the_instance_adds_its_own(self):
        self.settings({"core": {"secret_paths": ["~/.aws/credentials", "/srv/keys/**", 5, ""]}})
        listed, problem = secrets.patterns(home=HOME)
        self.assertIsNone(problem)
        self.assertIn(HOME + "/.aws/credentials", listed)
        self.assertIn("/srv/keys/**", listed)
        self.assertEqual(len(listed), len(secrets.DEFAULT) + 2, "what is not a path is left out")
        self.assertTrue(secrets.is_secret("/srv/keys/a/b", listed))

    def test_settings_that_cannot_be_read_leave_the_defaults_and_say_why_without_waiting(self):
        for text, why in (("{ not JSON", "JSONDecodeError"), ({"core": {"secret_paths": "~/x"}}, "not a list")):
            self.settings(text)
            began = time.perf_counter()
            listed, problem = secrets.patterns(home=HOME)
            self.assertLess(time.perf_counter() - began, 0.05, "never the engine's waits for a broken file")
            self.assertEqual(listed, [HOME + p[1:] for p in secrets.DEFAULT])
            self.assertIn(why, problem)

    def test_no_settings_file_or_no_such_setting(self):
        self.assertEqual(secrets.patterns(home=HOME), ([HOME + p[1:] for p in secrets.DEFAULT], None))
        self.settings({"core": {}})
        self.assertEqual(secrets.patterns(home=HOME)[1], None)


class ForTheGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)

    def settings(self, data):
        with open(os.path.join(self.tmp.name, "plugins.json"), "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_the_switch(self):
        self.assertTrue(secrets.guard_on(), "no settings: on")
        self.settings("{ broken")
        self.assertTrue(secrets.guard_on(), "unreadable: on")
        for value, on in ((False, False), (True, True), (0, True), ("no", True), (None, True)):
            self.settings({"core": {"secret_guard": value}})
            self.assertEqual(secrets.guard_on(), on, value)

    def test_claude_codes_own_rules(self):
        """In Claude Code's syntax: ~/… under home, //… elsewhere; none for a pattern that needs a "but not *.pub"."""
        self.settings({"core": {"secret_paths": ["/srv/keys/**", "~/.aws/credentials"]}})
        rules = secrets.deny_rules(home=HOME)
        self.assertIn("Read(~/.config/dcbot/**)", rules)
        self.assertIn("Read(//srv/keys/**)", rules)
        self.assertIn("Read(~/.aws/credentials)", rules)
        self.assertFalse([r for r in rules if ".ssh" in r], "left to the guard, which knows the exceptions")

    def test_searching_a_folder_that_holds_secrets(self):
        """(holds(), the K-03a follow-up, is what the guard asks of a Grep's, a Glob's or an LS's folder.)"""
        listed = [HOME + p[1:] for p in secrets.DEFAULT]
        for folder, holds in ((HOME + "/.config", True), (HOME + "/.ssh", True), (HOME, False), ("/", False), (HOME + "/work", False)):
            self.assertEqual(secrets.holds(folder, listed, home=HOME), holds, folder)


if __name__ == "__main__":
    unittest.main()
