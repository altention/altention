"""C-92 b-1: altention tmux send (bot/tmuxsend.py), on real tmux servers of the test's own (-S in its own folder;
tests/tmuxguard.py lets those through): a shell pane, typed into after #audit took the line and the pane was looked
at twice; each refusal types nothing."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import os
import shutil
import subprocess
import tempfile
import time
import glob
import unittest
from unittest import mock

import bot  # noqa: F401,E402  (first: it puts the engine's folder on the path)
from altention.connectors.claude_code import screen as cc_screen  # noqa: E402

from bot import tmuxref, tmuxsend
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

KEY = "sk-ant-api03-" + "x" * 80 + "-AAAAAAAAAAAAAAAAAAAA"


@unittest.skipUnless(shutil.which("tmux"), "no tmux here")
class Server(unittest.TestCase):
    """A tmux server of the test's own with one shell pane."""
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="dcq")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.sock = os.path.join(self.folder, "default")
        self.T("new-session", "-d", "-s", "w", "-x", "120", "-y", "20", "env PS1='$ ' bash --norc --noprofile -i")
        self.addCleanup(subprocess.run, ["tmux", "-S", self.sock, "kill-server"], stderr=subprocess.DEVNULL, timeout=10)
        self.pane = self.T("display-message", "-p", "-t", "w:0.0", "#{pane_id}").strip()
        self.id = tmuxref.ident("lap", self.sock, "w", "0", "0", self.pane, self.folder)
        self.lines = []
        time.sleep(0.3)

    def T(self, *args):
        return subprocess.run(["tmux", "-S", self.sock] + list(args), stdout=subprocess.PIPE, universal_newlines=True,
                              timeout=10).stdout

    def audit(self, line):
        self.lines.append(line)
        return True

    def send(self, ident=None, **kw):
        ident = ident or self.id
        kw.setdefault("audit", self.audit)
        kw.setdefault("is_claude", lambda command, pid: False)
        kw.setdefault("pane_state", lambda view: "box")
        kw.setdefault("box_text", lambda view, colour_view: None)
        kw.setdefault("caller", "頻道 <#42>")
        return tmuxsend.send(tmuxref.parse(ident), ident, folder=self.folder, **kw)

    def screen(self):
        time.sleep(0.3)
        return self.T("capture-pane", "-p", "-t", self.pane)

    def assertNothingTyped(self):
        self.assertEqual([r for r in self.screen().split("\n") if r.strip()], ["$"])



class Send(Server):
    def test_typed_after_audit_without_enter(self):
        self.assertEqual(self.send(text="echo hi"), {"sent": True, "pane": self.pane})
        self.assertIn("$ echo hi", self.screen())
        self.assertNotIn("\nhi\n", self.screen(), "no Enter unless asked")
        self.assertEqual(len(self.lines), 1)
        self.assertIn("打字「echo hi」", self.lines[0])
        self.assertIn(self.id, self.lines[0])
        self.assertIn("頻道 <#42>", self.lines[0])

    def test_enter_and_keys(self):
        self.assertTrue(self.send(text="echo one", enter=True)["sent"])
        self.assertIn("\none\n", self.screen())
        self.assertTrue(self.send(keys=["e", "c", "h", "o", "Space", "t", "w", "o", ";", "Enter"])["sent"])
        self.assertIn("\ntwo\n", self.screen())
        self.assertIn("按鍵 e c h o Space t w o ; Enter", self.lines[-1])

    def test_a_semicolon_where_a_stretch_ends(self):
        self.assertTrue(self.send(text="echo a;echo b;", enter=True, pace=(7, 0))["sent"])  # "echo a;" then "echo b;"
        self.assertIn("$ echo a;echo b;\na\nb\n", self.screen())

    def test_refused_before_anything(self):
        cases = [(dict(text="ls\nrm -rf x"), 4, "換行"), (dict(text="sudo ls"), 4, "sudo"), (dict(text="  su -"), 4, "sudo"),
                 (dict(text="doas ls"), 4, "sudo"), (dict(keys=["s", "u", "d", "o", "Space", "l"]), 4, "sudo"),
                 (dict(keys=["NoSuchKey"]), 2, "不認得"), (dict(), 2, "其中一種"), (dict(text="x", keys=["Enter"]), 2, "其中一種"),
                 (dict(text=""), 2, "空的")]
        for kw, code, words in cases:
            got = self.send(**kw)
            self.assertEqual(got["code"], code, kw)
            self.assertIn(words, got["error"], kw)
        self.assertEqual(self.lines, [], "#audit is not even asked")
        self.assertTrue(self.send(text="sudoku")["sent"], "only the word itself")
        self.assertTrue(self.send(text="echo sudo")["sent"])

    def test_root_where_any_command_starts(self):
        """#code-review-2 on 0303aec, manager's ruling: anywhere a command starts, and keys line by line."""
        for text in ("true && sudo id", "a;sudo id", "x || su -", "ls | doas id", "echo $(sudo id)", "echo `sudo id`",
                     "(pkexec id)", "{ sudo id; }", "env sudo id", "env A=1 B=x sudo id", "command sudo id",
                     "exec su", "nohup sudo id &", "time sudo id", "true & sudo id"):
            got = self.send(text=text, enter=True)
            self.assertEqual(got["code"], 4, text)
            self.assertIn("pkexec", got["error"], text)
        for keys in (["Enter", "s", "u", "d", "o", "Space", "i", "d", "Enter"], ["C-c", "s", "u", "d", "o"],
                     ["e", "c", "h", "o", "C-m", "s", "u", "Enter"]):
            self.assertEqual(self.send(keys=keys)["code"], 4, keys)
        self.assertEqual(self.lines, [])
        self.assertNothingTyped()
        for text in ("visudo -c", "echo sudo", "sudoku", "envsudo"):
            self.assertTrue(self.send(text=text)["sent"], text)

    def test_root_in_two_goes_is_not_caught(self):
        """Best effort (manager): each send is looked at by itself, so `s` then `udo id` goes through; written in
        interfaces.md, and what bounds it is when send is used at all and #audit."""
        self.assertTrue(self.send(text="s")["sent"])
        self.assertTrue(self.send(text="udo id")["sent"])

    def test_the_identifier(self):
        bare = self.id.split("#")[0]
        self.assertEqual(self.send(bare, text="x")["code"], 2, "no #%: refused")
        self.assertEqual(self.send(bare + "#%9999", text="x")["code"], 3, "another pane there")
        gone = tmuxref.ident("lap", self.sock, "w", "7", "0", self.pane, self.folder)
        self.assertEqual(self.send(gone, text="x")["code"], 3, "no window 7")
        self.assertNothingTyped()

    def test_audit_not_taken_types_nothing(self):
        got = self.send(text="echo hi", audit=lambda line: False)
        self.assertEqual(got["code"], 5)
        self.assertNothingTyped()

    def test_the_pane_changes_while_audit_waits(self):
        def respawn(line):
            self.lines.append(line)
            self.T("respawn-pane", "-k", "-t", self.pane, "env PS1='$ ' bash --norc --noprofile -i")
            time.sleep(0.3)
            return True
        got = self.send(text="echo hi", audit=respawn)
        self.assertEqual(got["code"], 4)
        self.assertIn("變了", got["error"])
        self.assertNothingTyped()
        self.assertEqual(len(self.lines), 2)
        self.assertIn("上一則沒有送出", self.lines[1])

    def test_a_claude_pane_takes_keys_only_in_its_box(self):
        for state, words in (("dialog", "不是一般輸入框"), ("shell", "! 模式"), ("agents", "背景對話清單"), (None, "讀不到")):
            got = self.send(text="x", is_claude=lambda c, p: True, pane_state=lambda view, s=state: s)
            self.assertEqual(got["code"], 4, state)
            self.assertIn(words, got["error"], state)
        self.assertNothingTyped()
        self.assertTrue(self.send(text="x", is_claude=lambda c, p: True, pane_state=lambda view: "box")["sent"])

    def test_the_screen_changing_while_audit_waits(self):
        states = iter(["box", "dialog"])
        got = self.send(text="x", is_claude=lambda c, p: True, pane_state=lambda view: next(states))
        self.assertEqual(got["code"], 4)
        self.assertNothingTyped()

    def test_a_foreground_not_mine_or_not_known(self):
        for others, words in ((["root"], "root"), (None, "看不出")):
            with mock.patch.object(tmuxref, "foreground", lambda pid, by_group=None, o=others: o):
                got = self.send(text="x")
            self.assertEqual(got["code"], 4)
            self.assertIn(words, got["error"])
        self.assertNothingTyped()
        self.assertEqual(self.lines, [])

    def test_the_audit_line_is_masked_and_short(self):
        self.send(text="echo " + KEY)
        self.assertNotIn(KEY[:20], self.lines[0])
        self.assertIn("[REDACTED]", self.lines[0])
        self.lines.clear()
        self.send(text="x", reason="because " + KEY, caller="lap:/srv/" + KEY)  # (manager: the reason and caller too)
        self.assertNotIn(KEY[:20], self.lines[0])
        self.assertEqual(self.lines[0].count("[REDACTED]"), 2, self.lines[0])
        self.lines.clear()
        self.send(text="y" * 400)
        self.assertIn("…（共 400 字）", self.lines[0])
        self.assertLess(len(self.lines[0]), 450)


SCREENS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "altention", "tests",
                       "connectors", "claude_code", "screens")


def collected(name):
    """(raw screen, the same with its colours) of a screen collected from a real Claude Code (version/set/name)."""
    base = os.path.join(SCREENS, name)
    with open(base + ".txt", encoding="utf-8") as f, open(base + ".esc.txt", encoding="utf-8") as g:
        return f.read(), g.read()


class TheInputBox(Server):
    """#code-review on 0303aec: a Claude Code pane in its input box with someone's half-typed line in it: typing
    would join it (and --enter send it). The real screens collected from Claude Code stand in for the pane's
    (capture-pane answers with them); the screen readers are the connector's, as dcremote hands them in."""

    def showing(self, *names):
        """capture-pane answers with these collected screens, one look after the other (the last one stays)."""
        shown = [collected(n) for n in names]
        real = tmuxref.tmux

        def tmux(path, *args):
            if args and args[0] == "capture-pane":
                plain, colour = shown[0] if len(shown) == 1 else shown.pop(0) if "-e" in args else shown[0]
                return 0, colour if "-e" in args else plain, ""
            return real(path, *args)
        return mock.patch.object(tmuxref, "tmux", tmux)

    def send(self, ident=None, **kw):
        kw.setdefault("is_claude", lambda command, pid: True)
        kw.setdefault("pane_state", cc_screen.pane_state)
        kw.setdefault("box_text", tmuxsend.box_check(cc_screen))
        return Server.send(self, ident, **kw)

    def test_someones_text_in_the_box_is_refused(self):
        for name in ("2.1.288/colour-120x50/idle-typed", "2.1.288/colour-120x50/slash-typed",
                     "2.1.288/colour-120x50/busy-queued-typed", "2.1.296/colour-80x24/idle-typed"):
            with self.showing(name):
                got = self.send(text="幫我看一下", enter=True)
            self.assertEqual(got["code"], 4, name)
            self.assertIn("已經有字", got["error"], name)
        self.assertEqual(self.lines, [], "refused at the first look: #audit is not asked")

    def test_a_grey_suggestion_or_an_empty_box_is_typed_over(self):
        for name in ("2.1.288/colour-120x50/idle-suggestion", "2.1.296/colour-80x24/idle-suggestion",
                     "2.1.288/colour-120x50/idle-after-turns"):
            with self.showing(name):
                self.assertTrue(self.send(text="x")["sent"], name)

    def test_no_colours_cannot_tell_so_nothing_is_typed(self):
        for name in ("2.1.288/nocolor-120x50/idle-typed", "2.1.288/nocolor-120x50/idle-suggestion"):
            with self.showing(name):
                got = self.send(text="x")
            self.assertEqual(got["code"], 4, name)
            self.assertIn("顏色", got["error"], name)
        self.assertNothingTyped()

    def test_editing_keys_with_typing_keys_into_claude_code_are_refused(self):
        """#code-review on afce55b, manager's rule: no simulation of editing; both together into Claude Code is 4."""
        for keys in (["x", "BSpace", "!", "l", "s", "Enter"], ["s", "x", "BSpace", "u", "d", "o", "Space", "i", "d"],
                     ["a", "Left", "b"], ["h", "i", "C-u"], ["Space", "Home"]):
            with self.showing("2.1.288/colour-120x50/idle-after-turns"):
                got = self.send(keys=keys)
            self.assertEqual(got["code"], 4, keys)
            self.assertIn("編輯鍵", got["error"], keys)
        self.assertEqual(self.lines, [])
        for keys in (["Down", "Enter"], ["C-u"], ["Escape"], ["h", "i"]):  # each kind alone still goes
            with self.showing("2.1.288/colour-120x50/idle-after-turns"):
                self.assertTrue(self.send(keys=keys).get("sent"), keys)

    def test_a_bang_into_claude_code_is_refused(self):
        """! at the start of Claude Code's box turns it into a shell command (as its ! mode, refused already)."""
        for kw in (dict(text="!rm -rf x", enter=True), dict(keys=["!", "l", "s", "Enter"]),
                   dict(keys=["Enter", "!", "l", "s", "Enter"]), dict(keys=["Escape", "!", "l", "s"]),  # (#code-review-2
                   dict(keys=["a", "C-j", "!", "l", "s"])):                                               # on e5f710b)
            with self.showing("2.1.288/colour-120x50/idle-after-turns"):
                got = self.send(**kw)
            self.assertEqual(got["code"], 4, kw)
            self.assertIn("! 模式", got["error"], kw)
        self.assertEqual(self.lines, [])
        with self.showing("2.1.288/colour-120x50/idle-after-turns"):
            self.assertTrue(self.send(text="a ! later")["sent"])

    def test_typed_into_between_the_two_looks(self):
        with self.showing("2.1.288/colour-120x50/idle-after-turns", "2.1.288/colour-120x50/idle-typed"):
            got = self.send(text="x")
        self.assertEqual(got["code"], 4)
        self.assertIn("已經有字", got["error"])
        self.assertIn("上一則沒有送出", self.lines[-1])
        self.assertNothingTyped()

    def test_every_collected_box_screen(self):
        """All the box screens collected, every version and size: refused unless the box is empty or grey."""
        check, seen = tmuxsend.box_check(cc_screen), {"typed": 0, "grey": 0, "colourless": 0}
        for plain_path in glob.glob(os.path.join(SCREENS, "*", "*", "*.txt")):
            if plain_path.endswith(".esc.txt") or not os.path.exists(plain_path[:-4] + ".esc.txt"):
                continue
            plain, colour = collected(os.path.relpath(plain_path[:-4], SCREENS))
            if cc_screen.pane_state(plain) != "box" or not cc_screen.input_text(plain):
                continue
            why = check(plain, colour)
            if cc_screen.colourless(colour):
                seen["colourless"] += 1
                self.assertIn("顏色", why or "", plain_path)
            elif cc_screen.input_dim(colour):
                seen["grey"] += 1
                self.assertIsNone(why, plain_path)
            else:
                seen["typed"] += 1
                self.assertIn("已經有字", why or "", plain_path)
        self.assertTrue(all(n >= 3 for n in seen.values()), seen)
        plain, _ = collected("2.1.288/colour-120x50/idle-typed")
        self.assertIn("顏色", check(plain, None), "the colours not read: not known, not sent")


class Audit(Server):
    def test_no_answer_in_time_says_it_was_not_sent(self):
        said = []
        got = self.send(text="echo hi", audit=lambda line: said.append(line) or (None if len(said) == 1 else True))
        self.assertEqual(got["code"], 5)
        self.assertEqual(len(said), 2)
        self.assertIn("上一則沒有送出", said[1])
        self.assertNothingTyped()

    def test_the_second_line_is_not_waited_for(self):
        """#code-review-2 on e5f710b, manager: a second wait ran past altention tmux's SEND_TIMEOUT (255, not 5)."""
        waited, later = [], []
        got = self.send(text="echo hi", audit=lambda line: waited.append(line) or None, audit_later=later.append)
        self.assertEqual((got["code"], len(waited), len(later)), (5, 1, 1))
        self.assertIn("上一則沒有送出", later[0])

    def test_surely_not_taken_says_nothing_more(self):
        said = []
        self.assertEqual(self.send(text="echo hi", audit=lambda line: said.append(line) or False)["code"], 5)
        self.assertEqual(len(said), 1)


class ThroughDcremote(Server):
    """dcremote tmux-send, as the other machine runs it: only what stops before #audit is asked (the rest would post
    to the real #audit; it is run on another machine by hand, see the report)."""

    def dcremote(self, ident, stdin):
        import sys
        env = dict(os.environ)
        mine = os.path.join(tempfile.mkdtemp(prefix="dcq"), "tmux-%d" % os.getuid())  # dcremote: TMUX_TMPDIR/tmux-<uid>
        self.addCleanup(shutil.rmtree, os.path.dirname(mine), True)
        os.symlink(self.folder, mine)
        env["TMUX_TMPDIR"] = os.path.dirname(mine)
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run([sys.executable, os.path.join(here, "tools", "dcremote"), "tmux-send", ident], input=stdin,
                              env=env, stdout=subprocess.PIPE, universal_newlines=True, timeout=30)

    def test_stops_before_audit(self):
        import json
        for stdin, code in (("not json", 2), ("[1]", 2), (json.dumps({"text": "sudo ls"}), 4),
                            (json.dumps({"text": "a\nb"}), 4), (json.dumps({"keys": ["Nope"]}), 2)):
            r = self.dcremote(self.id, stdin)
            self.assertEqual((r.returncode, json.loads(r.stdout)["code"]), (code, code), stdin)
        r = self.dcremote(self.id.split("#")[0] + "#%9999", json.dumps({"text": "x"}))
        self.assertEqual(r.returncode, 3)
        self.assertNothingTyped()


class Cli(unittest.TestCase):
    """altention tmux send's own arguments (nothing is sent: they are wrong before any machine is asked)."""

    def run_(self, *args):
        import sys
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run([sys.executable, os.path.join(os.path.dirname(here), "altention", "tmux.py"), "send"] +
                              list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=30)

    def test_wrong_arguments(self):
        for args in ((), ("tmux://lap/w:0.0#%1",), ("nope", "x"), ("tmux://lap/w:0.0#%1", "--keys"),
                     ("tmux://lap/w:0.0#%1", "x", "--reason"), ("tmux://lap/w:0.0#%1", "--enter", "--keys", "Enter")):
            self.assertEqual(self.run_(*args).returncode, 2, args)


if __name__ == "__main__":
    unittest.main()
