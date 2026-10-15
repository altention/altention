"""Tmux mode: key planning, hook events, and (when tmux is installed) a real session running `cat`."""
import asyncio
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import typist as cc_typing  # noqa: E402
from altention.connectors.claude_code import screen
from bot import config, events, tmuxio
from tests import fakepane

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PlanKeys(unittest.TestCase):
    def test_single_line(self):
        self.assertEqual(tmuxio.plan_keys('say "hi" $HOME'), [("text", 'say "hi" $HOME')])

    def test_lines_joined_with_alt_enter(self):
        self.assertEqual(tmuxio.plan_keys("a\r\nb\n\nc\n"),
                         [("text", "a"), ("key", "M-Enter"), ("text", "b"), ("key", "M-Enter"),
                          ("key", "M-Enter"), ("text", "c")])

    def test_control_characters_never_typed(self):
        steps = tmuxio.plan_keys("a\tb\x1b[0m\x03")
        self.assertEqual(steps, [("text", "a    b[0m")])

    def test_trailing_semicolon_is_escaped_for_tmux(self):
        self.assertEqual(tmuxio.plan_keys("a;b;"), [("text", "a;b"), ("text", "\\;")])
        self.assertEqual(tmuxio.plan_keys(";"), [("text", "\\;")])

    def test_mode_characters_and_trailing_traps(self):
        self.assertEqual(tmuxio.plan_keys("#note"), [("text", " #note")])
        self.assertEqual(tmuxio.plan_keys("see @file"), [("text", "see @file ")])
        self.assertEqual(tmuxio.plan_keys("path\\"), [("text", "path\\ ")])
        self.assertEqual(tmuxio.plan_keys("/compact"), [("text", "/compact")])

    def test_long_line_is_chunked(self):
        steps = tmuxio.plan_keys("x" * 1200)
        self.assertEqual([len(v) for _, v in steps], [500, 500, 200])
        self.assertLess(cc_typing.TYPE_CHUNK, screen.PASTE_AT, "a stretch is well under what Claude Code takes for a paste")


class Pace(unittest.TestCase):
    """A long message is typed as stretches with a pause between them, so Claude Code never takes it for a paste
    (manager 2026-10-09: a line past about 780 characters reached Claude as pasted content, not as the owner's
    words). Only the rhythm changes: what is typed is the same characters in the same order."""

    def typed(self, text):
        log = []

        async def tmux(*args, **kw):
            if args[0] == "send-keys":
                log.append(("text", args[-1]) if "-l" in args else ("key", args[-1]))
            return 0, ""

        async def sleep(seconds):
            log.append(("pause", seconds))
        with fakepane.replace("tmux", tmux), mock.patch.object(tmuxio.asyncio, "sleep", sleep):
            asyncio.run(tmuxio.type_text("s", text))
        return log

    def test_a_short_message_goes_in_one_go_as_before(self):
        self.assertEqual(self.typed("hello"), [("text", "hello")])
        self.assertEqual([k for k, _ in self.typed("x" * cc_typing.TYPE_CHUNK)], ["text"])
        self.assertEqual([k for k, _ in self.typed("line one\nline two")], ["text", "key", "text"])

    def test_a_long_line_is_typed_in_stretches_with_a_pause_between(self):
        log = self.typed("x" * 2000)
        self.assertEqual([k for k, _ in log], ["text", "pause", "text", "pause", "text", "pause", "text"])
        self.assertEqual({v for k, v in log if k == "pause"}, {cc_typing.TYPE_PAUSE})
        self.assertEqual("".join(v for k, v in log if k == "text"), "x" * 2000, "the same characters, nothing added")

    def test_never_more_than_a_stretch_without_a_pause_across_lines_too(self):
        for text in ("\n".join("第 %d 行 some words here" % i for i in range(60)), ("甲" * 400 + "\n") * 5 + "乙",
                     "short\n" + "x" * 900 + "\nend", "x" * 499 + "\ny"):
            log, burst, lines = self.typed(text), 0, []
            for kind, val in log:
                if kind == "pause":
                    burst = 0
                elif kind == "text":
                    burst += len(val)
                    self.assertLessEqual(burst, cc_typing.TYPE_CHUNK, text[:20])
            got = "".join("\n" if k == "key" else v for k, v in log if k != "pause")
            self.assertEqual(got, text, "typed as written: no line break added or lost")

    def test_the_marks_the_bot_may_still_have_made_if_pacing_fails(self):
        self.assertEqual(screen.paste_marks("x" * 599), 0)
        self.assertEqual(screen.paste_marks("x" * 2000), 3)
        self.assertEqual(screen.paste_marks("x" * 700 + "\n" + "y" * 100 + "\n" + "z" * 1300), 3)

    def test_session_name(self):
        self.assertEqual(tmuxio.session_name("bot-dev", 100000000000000096), "dc-bot-dev-0096")
        self.assertNotIn(".", tmuxio.session_name("a.b:c d", 1))


class Events(unittest.TestCase):
    def test_matches(self):
        self.assertTrue(events.matches("first\nsecond  line", "second line"))
        self.assertTrue(events.matches("one\n\ntwo", "two"))
        self.assertFalse(events.matches("<task-notification>x</task-notification>", "hello"))
        self.assertFalse(events.matches("anything", "  "))

    def test_footer(self):
        self.assertEqual(events.footer({"Bash": 2, "Read": 5}, 41.6, 1), "-# Read×5, Bash×2 · 42 秒 · 背景工作 1 個進行中")
        self.assertEqual(events.footer({}, None), "")
        self.assertEqual(events.footer({}, 3, ctx=[202000, "claude-opus-5-5"]), "-# 3 秒 · context 20%")
        self.assertEqual(events.context_share([100000, "claude-haiku-4-5"]), "context 50%")
        self.assertEqual(events.context_share(None), "")

    def test_idle_notice(self):
        self.assertTrue(events.is_idle_notice({"message": "Claude is waiting for your input"}))
        self.assertFalse(events.is_idle_notice({"message": "Claude needs your permission to use Bash"}))

    def test_hook_writes_event_and_drain_reads_it_once(self):
        root = tempfile.mkdtemp()
        try:
            env = dict(os.environ, DCBOT_ROOT=root, DCBOT_CHANNEL="42")
            for i, payload in enumerate([
                    {"hook_event_name": "PreToolUse", "tool_name": "Write",
                     "tool_input": {"file_path": "/tmp/x", "content": "y" * 100000}},
                    {"hook_event_name": "Stop", "last_assistant_message": "完成\n第二行", "background_tasks": []}]):
                r = subprocess.run([os.path.join(ROOT, "tools", "dchook")], input=json.dumps(payload).encode(),
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""))
            spool = os.path.join(root, "state", "events")
            got = events.drain(spool)
            self.assertEqual([e["hook_event_name"] for e in got], ["PreToolUse", "Stop"])
            self.assertEqual(got[0]["channel"], "42")
            self.assertNotIn("tool_input", got[0])
            self.assertEqual(events.progress_line(got[0]), "Write /tmp/x")
            self.assertEqual(got[1]["last_assistant_message"], "完成\n第二行")
            self.assertEqual(events.drain(spool), [])
        finally:
            shutil.rmtree(root)

    def test_hook_is_silent_outside_bot_sessions_and_on_garbage(self):
        env = dict((k, v) for k, v in os.environ.items() if not k.startswith("DCBOT_"))
        for e in (env, dict(env, DCBOT_ROOT="/nonexistent/x", DCBOT_CHANNEL="1")):
            r = subprocess.run([os.path.join(ROOT, "tools", "dchook")], input=b"not json", env=e,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""))


class Panels(unittest.TestCase):
    def test_open_panel_is_recognised(self):
        from bot.tmux_bridge import fence
        DIALOG = screen.DIALOG
        self.assertTrue(DIALOG.search("   Usage credits are off\n   Esc to cancel"))
        self.assertTrue(DIALOG.search(" Enter to confirm · Esc to cancel"))
        self.assertFalse(DIALOG.search("  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt"))
        self.assertLessEqual(len(fence("x" * 9000, 3600)), 3610)

    def test_panel_without_its_hint_line_is_recognised(self):
        panel_shown = screen.panel_shown
        rule = "─" * 40
        idle = "● done\n\n%s\n❯ \n%s\n  ⏵⏵ bypass permissions on (shift+tab to cycle)" % (rule, rule)
        busy = "✻ Working…\n\n%s\n❯ next\n%s\n  ⏵⏵ bypass permissions on · esc to interrupt" % (rule, rule)
        clipped = "▔▔▔▔\n   Settings  Status   Config   Usage   Stats\n   Current session\n   16% used\n   ↓"
        menu = "%s\n Select model\n ❯ 1. Default\n   2. Opus" % rule  # a cursor, not the input box
        self.assertTrue(screen.has_prompt(idle))
        self.assertFalse(screen.has_prompt(clipped))
        self.assertFalse(screen.has_prompt(menu))
        named = "reply\n" + "─" * 40 + " 修正明顯錯誤 ─\n❯\xa0suggested prompt\n" + "─" * 60 + "\n  ⏵⏵ bypass permissions on"
        self.assertTrue(screen.has_prompt(named), "a named session puts its name in the rule above the box")
        self.assertFalse(panel_shown(idle))
        self.assertFalse(panel_shown(busy))
        self.assertTrue(panel_shown(clipped))
        self.assertTrue(panel_shown(clipped + "\n   Esc to cancel"))
        self.assertTrue(panel_shown(menu))

    def test_btw_overlay(self):
        top = "● earlier reply\n\n" + "▔" * 50 + "\n"
        pending = top + "    /btw what is it?\n\n      · Answering…\n\n    Esc to close\n"
        done = (top + "    /btw what is it?\n\n      The first line of the answer is long enough to run all the way to"
                " the right edge of this terminal\n      window and continues here.\n\n      - a bullet\n      short\n\n"
                "    ↑/↓ to scroll · c to copy · f to fork · Esc to close\n")
        self.assertIsNone(screen.btw_answer("❯ \n  ⏵⏵ bypass permissions on"))
        self.assertIsNone(screen.btw_answer("❯ /btw typed but not sent yet\n"))
        self.assertEqual(screen.btw_answer(pending), "")
        self.assertEqual(screen.btw_answer(done),
                         "The first line of the answer is long enough to run all the way to the right edge of this terminal "
                         "window and continues here.\n\n- a bullet\nshort")

    def test_btw_overlay_skips_a_wrapped_question(self):
        view = ("▔▔▔\n    /btw （接續這段旁支對話。先前的問答：問：A → 答：B）現在的問題：這是一個很長的問題所以\n"
                "    換到第二行了\n\n      這是答案。\n\n    c to copy · Esc to close\n")
        self.assertEqual(screen.btw_answer(view), "這是答案。")

    def test_btw_answer_that_mentions_btw(self):
        view = ("▔▔▔\n    /btw how do I ask again?\n\n      In the channel type\n      /btw <question> to open another thread.\n\n"
                "    c to copy · Esc to close\n")
        self.assertEqual(screen.btw_answer(view), "In the channel type\n/btw <question> to open another thread.")

    def test_btw_prompt_carries_the_thread(self):
        self.assertEqual(events.btw_prompt([], "  first\nquestion "), "first question")
        p = events.btw_prompt([["q1", "a1"], ["q2", "a2 " * 3]], "q3")
        self.assertIn("先前的問答：問：q1 → 答：a1；問：q2 → 答：a2 a2 a2）", p)
        self.assertEqual(events.btw_prompt([], "q", fresh=True), "（這是新的旁支問題，和先前其他 /btw 的問答無關）q")
        self.assertTrue(p.endswith("現在的問題：q3"))
        self.assertNotIn("\n", events.btw_prompt([["多\n行", "答\n案"]], "q"))
        many = [["q%d" % i, "x" * 300] for i in range(20)]
        long = events.btw_prompt(many, "last")
        self.assertLessEqual(len(long), screen.BTW_MAX, "well under the ~780 characters Claude Code takes for a paste")
        self.assertIn("問：q19", long, "the newest exchanges are the ones kept")
        self.assertNotIn("問：q0 ", long)

    def test_btw_follow_ups_in_chinese_stay_under_the_paste_threshold(self):
        pairs = [["中文的問題" * 30, "很長的中文回答" * 100] for _ in range(6)]  # the third follow-up of 2026-10-06
        for question in ("再問一個問題", "問" * 300):
            text = "/btw " + events.btw_prompt(pairs, question)
            self.assertLessEqual(len(text), 5 + screen.BTW_MAX)
            self.assertTrue(text.endswith(question))
        alone = events.btw_prompt(pairs, "問" * 600)
        self.assertGreater(len(alone), screen.BTW_MAX, "a question too long alone is left for btw_ask to refuse")

    def test_btw_input_box_read_before_enter(self):
        rule = "─" * 60
        def box(*rows):
            return "\n".join(["Claude Code", "", rule] + list(rows) + [rule, "  ? for shortcuts", ""])
        self.assertEqual(screen.btw_input_state(box("❯\u00a0/btw " + "問" * 90, "問" * 90)), "ok")
        self.assertEqual(screen.btw_input_state(box("❯ [Pasted text #1]")), "pasted")
        self.assertEqual(screen.btw_input_state(box("❯ /btw 前面 [Pasted text #1 +3 lines]")), "pasted")
        self.assertEqual(screen.btw_input_state(box("❯ " + "問" * 50)), "other", "/btw lost")
        self.assertEqual(screen.btw_input_state(box("❯ Try \"how do I log an error?\"")), "empty")
        self.assertEqual(screen.btw_input_state(box("❯ ")), "empty")
        self.assertEqual(screen.btw_input_state("a panel, no input box\n"), "other")
        lookalike = "\n".join(["", "─" * 60, "❯ 1. Yes", "─" * 60]) + "\n"  # a dialog's rows above the real box
        self.assertEqual(screen.btw_input_state(lookalike + box("❯ /btw q")), "ok", "the box at the bottom is read")

    def test_btw_overlay_joins_wrapped_cjk_lines(self):
        wide = "      " + "這是一行很長的中文" * 6 + "，結尾 python3 -c 'import time;"   # about 114 columns
        view = "▔▔▔\n    /btw q\n\n" + wide + "\n      time.sleep(30)' 的說明\n\n    c to copy · Esc to close\n"
        self.assertTrue(screen.btw_answer(view).endswith("python3 -c 'import time; time.sleep(30)' 的說明"))
        self.assertEqual(screen.shown_width("中a"), 3)

    def test_claude_is_recognised_by_its_command_name(self):
        for name, yes in (("claude", True), ("node", True), ("2.1.289", True), ("zsh", False), ("2.1", False), ("", False)):
            self.assertEqual(screen.is_claude(name), yes, name)

    def test_key_names(self):
        self.assertEqual([tmuxio.key_name(k) for k in ("esc", "ESC", "enter", "Down", "C-c", "q")],
                         ["Escape", "Escape", "Enter", "Down", "C-c", "q"])


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class RealTmux(unittest.TestCase):
    """Types into a real session running `cat`, on a throwaway tmux server."""

    def setUp(self):
        self.name, self.was = "dcbot-test-%d" % os.getpid(), dict(tmuxio._set)
        tmuxio.configure(socket="dcbot-test-%d" % os.getpid())

    def tearDown(self):
        subprocess.run(["tmux", "-L", self.name, "kill-server"], stderr=subprocess.DEVNULL)
        try:
            os.remove(os.path.join(tmuxio.socket_dir(), self.name))  # tmux leaves the socket file behind
        except OSError:
            pass
        tmuxio._set.clear()
        tmuxio._set.update(self.was)

    def test_type_and_read_back(self):
        async def go():
            self.assertFalse(await tmuxio.alive("dc-t"))
            self.assertTrue(await tmuxio.start("dc-t-long", "/tmp", ["cat"], {"DCBOT_CHANNEL": "7"}))
            self.assertFalse(await tmuxio.alive("dc-t"), "a session name must not match by prefix")
            self.assertTrue(await tmuxio.alive("dc-t-long"))
            text = 'q "x" $HOME `bt` 中文; a;'
            await tmuxio.type_text("dc-t-long", text)
            await tmuxio.keys("dc-t-long", "Enter")
            await asyncio.sleep(0.5)
            view = await tmuxio.screen("dc-t-long")
            self.assertEqual(view.split("\n"), [text, text])  # typed line, then cat's echo
            self.assertEqual(await tmuxio.sessions(), ["dc-t-long"])
            await tmuxio.kill("dc-t-long")
            self.assertFalse(await tmuxio.alive("dc-t-long"))
        asyncio.run(go())


class WhichServers(unittest.TestCase):
    """tmuxio is told which tmux servers it works with (stage 3b step 1.8: it no longer reads the bot's settings)."""

    def setUp(self):
        self.was = dict(tmuxio._set)
        self.addCleanup(lambda: (tmuxio._set.clear(), tmuxio._set.update(self.was)))

    def test_not_told_is_an_error_not_some_default_server(self):
        tmuxio._set.clear()
        for ask in (tmuxio.bot_socket, tmuxio.user_socket, lambda: tmuxio.label("dc-x"),
                    lambda: asyncio.run(tmuxio.tmux("has-session", "-t", "=nope"))):
            with self.assertRaises(RuntimeError):
                ask()
        with mock.patch.object(tmuxio, "socket_dir", return_value=tempfile.gettempdir()):
            with self.assertRaises(RuntimeError):
                asyncio.run(tmuxio.local_claude_panes())

    def test_a_program_with_no_server_of_the_bot(self):
        """tools/dcremote: the owner's servers are read; a command for the bot's own server is an error."""
        tmuxio.configure(socket=None)
        self.assertEqual(tmuxio.user_socket(), os.path.join(tmuxio.socket_dir(), "default"))
        with self.assertRaises(RuntimeError):
            tmuxio.bot_socket()
        with self.assertRaises(RuntimeError):
            asyncio.run(tmuxio.tmux("has-session", "-t", "=nope"))
        with tempfile.TemporaryDirectory() as empty, mock.patch.object(tmuxio, "socket_dir", return_value=empty):
            self.assertEqual(asyncio.run(tmuxio.local_claude_panes()), [])

    def test_what_it_was_told(self):
        tmuxio.configure(socket="dcbot-test-x", user_tmux="/somewhere/else")
        self.assertEqual((tmuxio.bot_socket(), tmuxio.user_socket()), ("dcbot-test-x", "/somewhere/else"))
        self.assertEqual(tmuxio.label("dc-x"), "tmux -L dcbot-test-x attach -t dc-x")

    def test_the_bot_tells_it_when_it_starts_and_only_then(self):
        from bot import main
        self.assertEqual(self.was.get("socket") or "dcbot-test-none", "dcbot-test-none",
                         "importing the bot's modules told tmuxio nothing (tests/fakepane.py: a name no server has)")
        seen = {}

        class Bot:
            exit_code = 0

            def run(self, *a, **kw):
                seen.update(tmuxio._set)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(main, "Bot", Bot), mock.patch.object(main, "STATE", tmp), \
                mock.patch.multiple(config, TOKEN="t", OWNER_ID=1, TMUX_SOCKET="dcbot-test-main", USER_TMUX="/the/owner's"):
            with self.assertRaises(SystemExit):
                main.main()
        self.assertEqual((seen["socket"], seen["user_tmux"], seen["skip"]), ("dcbot-test-main", "/the/owner's", ()))

    def test_servers_whose_panes_are_not_the_owners_to_attach(self):
        """tools/dcremote: the server of a bot installed on that machine is left out, by the name its settings give
        (as before stage 3b step 1.8, when this was read from the bot's settings module)."""
        asked = []

        async def tmux(*args, socket=None, **kw):
            asked.append(os.path.basename(socket))
            return 1, ""

        with tempfile.TemporaryDirectory() as d:
            for n in ("default", "dcbot", "mybot", "dcbot-x", "dctest1", "work"):
                open(os.path.join(d, n), "w").close()
            with mock.patch.object(tmuxio, "socket_dir", return_value=d), mock.patch.object(tmuxio, "tmux", tmux):
                tmuxio.configure(socket=None, skip=("mybot",))
                asyncio.run(tmuxio.local_claude_panes())
                self.assertEqual(asked, ["dcbot", "default", "work"])
                del asked[:]
                tmuxio.configure(socket=None, skip=("dcbot",))
                asyncio.run(tmuxio.local_claude_panes())
                self.assertEqual(asked, ["default", "mybot", "work"])
                del asked[:]
                tmuxio.configure(socket="dcbot-test-x")
                self.assertEqual(tmuxio._set["skip"], (), "told again: nothing of the last time stays")

    def test_the_remote_tool_says_which_from_the_settings_of_its_machine(self):
        import importlib.machinery
        import importlib.util
        tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "dcremote")
        loader = importlib.machinery.SourceFileLoader("dcremote_which", tool)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcremote_which", loader))
        loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.dict(os.environ, {"DCBOT_ENV": os.path.join(tmp, "none")}):
                mod.main(["--help"])
                self.assertEqual((tmuxio._set["socket"], tmuxio._set["skip"]), (None, ("dcbot",)), "no settings there: the usual name")
            with mock.patch.dict(os.environ, {"DCBOT_ENV": ""}):
                from bot import envfile
                self.assertEqual((envfile.path(), envfile.load(), envfile.load("")), ("", {}, {}),
                                 "set to nothing: no file, not the usual one (how a rig keeps the real settings out)")
                mod.main(["--help"])
                self.assertEqual(tmuxio._set["skip"], ("dcbot",))
            with open(os.path.join(tmp, "env"), "w") as f:
                f.write("# a bot of this machine\nTMUX_SOCKET = 'mybot'\nDISCORD_TOKEN=not-read-out\n")
            with mock.patch.dict(os.environ, {"DCBOT_ENV": os.path.join(tmp, "env")}):
                mod.main(["--help"])
                self.assertEqual((tmuxio._set["socket"], tmuxio._set["skip"]), (None, ("mybot",)))


class NeverTheRunningBotsServer(unittest.TestCase):
    """No test may send a command to the tmux server of the bot that is running (2026-10-13: a test, redirected the
    way that had just stopped working, started a session there). Not kept by care: the name of that server reaches
    tmuxio in one place, the bot's start, which no test goes through with the real name."""
    HERE = os.path.dirname(os.path.abspath(__file__))

    def calls(self, path, name):
        import ast
        with open(path, encoding="utf-8") as f:
            src = f.read()
        return [(n, ast.get_source_segment(src, n)) for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", None)) == name]

    def test_the_bot_names_its_server_in_one_place(self):
        import ast
        found = []
        folder = os.path.join(os.path.dirname(self.HERE), "bot")
        for f in sorted(os.listdir(folder)):
            if f.endswith(".py") and f != "tmuxio.py":
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
                for fn in ast.walk(tree):
                    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        found += [(f, fn.name) for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "configure"
                                  and getattr(n.func.value, "id", None) == "tmuxio"]
                found += [(f, "<module>") for n in tree.body for c in ast.walk(n) if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                          and isinstance(c, ast.Call) and getattr(c.func, "attr", None) == "configure"]
        self.assertEqual(found, [("main.py", "main")])

    def test_importing_all_of_the_bot_tells_tmuxio_nothing(self):
        code = ("import importlib, os, sys\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "import bot\n"
                "for f in sorted(os.listdir(os.path.join(sys.argv[1], 'bot'))):\n"
                "    if f.endswith('.py') and f != '__init__.py':\n"
                "        importlib.import_module('bot.' + f[:-3])\n"
                "from bot import tmuxio\n"
                "print('TOLD' if tmuxio._set else 'NOTHING')\n")
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, DCBOT_ENV=os.path.join(home, "none"))
            got = subprocess.run([sys.executable, "-c", code, os.path.dirname(self.HERE)], env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
        self.assertEqual((got.returncode, got.stdout.strip().splitlines()[-1:]), (0, ["NOTHING"]), got.stderr[-600:])

    def test_what_the_tests_tell_it_is_never_a_name_from_the_settings(self):
        """Every configure() in the tests: no server of a bot (None), or a name that says it is a test's, written there."""
        import ast
        seen = 0
        for f in sorted(os.listdir(self.HERE)):
            if not f.endswith(".py"):
                continue
            for call, text in self.calls(os.path.join(self.HERE, f), "configure"):
                if getattr(getattr(call.func, "value", None), "id", None) != "tmuxio":
                    continue
                seen += 1
                arg = [k.value for k in call.keywords if k.arg == "socket"] or call.args[:1]
                self.assertTrue(arg, "%s: %s" % (f, text))
                ok = (isinstance(arg[0], ast.Constant) and arg[0].value is None) or any(
                    isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith("dcbot-test-") for n in ast.walk(arg[0]))
                self.assertTrue(ok and "config" not in ast.dump(arg[0]), "%s: %s" % (f, text))
            for call, text in self.calls(os.path.join(self.HERE, f), "main"):
                if getattr(getattr(call.func, "value", None), "id", None) == "main" and not call.args:
                    self.assertEqual(f, "test_tmux.py", "the bot's start, in a test: only where its settings are replaced (%s)" % text)
        self.assertGreater(seen, 5)

    def test_this_run_was_not_told_it_either(self):
        self.assertIn(tmuxio._set.get("socket"), (None, "dcbot-test-none"), "left by a test before this one: %r" % (tmuxio._set,))


if __name__ == "__main__":
    unittest.main()
