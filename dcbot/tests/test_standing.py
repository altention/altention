"""A pane on another machine that was asked and did not answer is not a pane that is gone (3b 2.6b, #issues 29;
manager 2026-10-14: removing goes only by facts that can be read and are plain). tmuxio.standing() tells the two
apart; each caller that does something on "not alive" is kept here for what it does on "unknown": nothing is
detached, closed, opened anew or called gone. (ensure() and a window the bot opened: tests/test_open_params.py;
an archived channel taken back: tests/test_archive.py.)"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import unittest
from unittest import mock

from bot import tmux_bridge, tmuxio
from tests import fakepane
from tests.fakepane import box
from tests.test_turn_end import Ch, Host

FAR = {"socket": "/tmp/x/default", "id": "%7", "host": "lap", "pid": "77"}
HERE = {"socket": "/tmp/x/default", "id": "%7", "pid": "77"}


class Standing(unittest.TestCase):
    def setUp(self):
        self.addCleanup(tmuxio.UNANSWERED.clear)

    def ask(self, t, code, out=""):
        async def tmux(*args, **kw):
            return code, out
        with fakepane.use(tmux=tmux):
            return asyncio.run(tmuxio.standing(t)), asyncio.run(tmuxio.alive(t))

    def test_the_three_answers_of_a_pane_on_another_machine(self):
        self.assertEqual(self.ask(FAR, 0, "claude\t77\n"), ("alive", True))
        self.assertEqual(self.ask(FAR, 0, "zsh\t77\n"), ("gone", False), "back at a shell")
        self.assertEqual(self.ask(FAR, 0, "claude\t99\n"), ("gone", False), "the number is someone else's pane now")
        self.assertEqual(self.ask(FAR, 1, ""), ("gone", False), "tmux there answered: no such pane")
        self.assertEqual(self.ask(FAR, 255, ""), ("unknown", False), "ssh's own failure: nothing answered")
        self.assertEqual(self.ask(FAR, 1, ""), ("gone", False), "and an answer after it is an answer")

    def test_claude_started_by_a_script(self):
        """C-92 b: tmux names the script's shell; what runs under the pane says Claude. Asked over there only when
        the name does not say it."""
        under = []

        async def claude_under(t, pid):
            under.append(pid)
            return True
        async def tmux(*args, **kw):
            return 0, "bash\t77\n"
        with fakepane.use(tmux=tmux, claude_under=claude_under):
            self.assertTrue(asyncio.run(tmuxio.alive(FAR)))
            self.assertTrue(asyncio.run(tmuxio.alive(HERE)))
        self.assertEqual(under, ["77", "77"])
        self.assertEqual(self.ask(FAR, 0, "claude\t77\n"), ("alive", True))  # (the default fake: none asked)

    def test_what_another_machine_says_runs_under_its_pane(self):
        real = tmuxio.claude_under  # the real one, over a fake dcremote (the fake pane's default: not known)
        for command, said, want in (
                ("bash", (0, "1\n"), ("alive", True)), ("bash", (0, "0\n"), ("gone", False)),
                ("bash", (1, ""), ("gone", False)),  # a dcremote too old to know claude-at: as the name says
                ("bash", (255, ""), ("unknown", False)),  # ssh's own failure: nothing answered
                ("node", (0, "1\n"), ("alive", True)),  # Claude installed with npm
                ("node", (0, "0\n"), ("gone", False)),  # manager: a Node dev server is not Claude, in alive() too
                ("node", (1, ""), ("alive", True)),  # too old to tell: by the name, as before
                ("node", (255, ""), ("unknown", False)),
                ("node", (0, "?\n"), ("alive", True)),  # that machine cannot tell: by the name
                ("bash", (0, "?\n"), ("gone", False)),
                ("node", (0, "what\n"), ("alive", True))):  # an answer not 0, 1 or ?: not known, by the name
            async def dcremote(host, *args, said=said, **kw):
                self.assertEqual((host, args), ("lap", ("claude-at", "77")))
                return said

            async def tmux(*args, command=command, **kw):
                return 0, "%s\t77\n" % command
            with fakepane.use(tmux=tmux, claude_under=real), \
                    mock.patch("bot.remote.dcremote", dcremote):
                self.assertEqual((asyncio.run(tmuxio.standing(FAR)), asyncio.run(tmuxio.alive(FAR))), want,
                                 (command, said))

    def test_this_machine_s_processes_for_a_pane_here(self):
        with mock.patch.object(tmuxio.cc_process, "runs_claude", lambda command, pid: pid == "77"):
            self.assertTrue(asyncio.run(tmuxio.claude_under(HERE, "77")))
            self.assertFalse(asyncio.run(tmuxio.claude_under(HERE, "78")))

    def test_node_here_is_told_by_what_runs(self):
        """manager on 6e93ae2: alive() does not go by the name "node"; a Node dev server here is not Claude."""
        async def tmux(*args, **kw):
            return 0, "node\t77\n"
        for under, want in ((True, ("alive", True)), (False, ("gone", False))):
            with fakepane.use(tmux=tmux, claude_under=tmuxio.claude_under), \
                    mock.patch.object(tmuxio.cc_process, "runs_claude", lambda command, pid, under=under: under):
                self.assertEqual((asyncio.run(tmuxio.standing(HERE)), asyncio.run(tmuxio.alive(HERE))), want, under)

    def test_here_not_known_goes_by_the_name(self):
        """No process table here (or the pane's process not in it): by the name, as before C-92 b."""
        for command, want in (("node", ("alive", True)), ("bash", ("gone", False))):
            async def tmux(*args, command=command, **kw):
                return 0, "%s\t77\n" % command
            with fakepane.use(tmux=tmux, claude_under=tmuxio.claude_under), \
                    mock.patch.object(tmuxio.cc_process, "table", lambda: {}):
                self.assertEqual((asyncio.run(tmuxio.standing(HERE)), asyncio.run(tmuxio.alive(HERE))), want, command)

    def test_claude_s_own_name_asks_nothing_more(self):
        async def claude_under(t, pid):
            raise AssertionError("asked")
        async def tmux(*args, **kw):
            return 0, "claude\t77\n"
        with fakepane.use(tmux=tmux, claude_under=claude_under):
            self.assertTrue(asyncio.run(tmuxio.alive(FAR)))
            self.assertTrue(asyncio.run(tmuxio.alive(HERE)))

    def test_a_pane_on_this_machine_or_a_session_of_the_bots_is_never_unknown(self):
        self.assertEqual(self.ask(HERE, 255, ""), ("gone", False))
        self.assertEqual(self.ask(HERE, 0, "claude\t77\n"), ("alive", True))
        self.assertEqual(self.ask("dc-x-1", 255), ("gone", False))
        self.assertEqual(self.ask("dc-x-1", 0), ("alive", True))

    def test_one_panes_silence_says_nothing_of_another(self):
        self.ask(FAR, 255, "")

        async def tmux(*args, **kw):
            return 1, ""
        with fakepane.use(tmux=tmux):
            self.assertEqual(asyncio.run(tmuxio.standing(dict(FAR, id="%8"))), "gone")
        self.assertEqual(asyncio.run(self.silent(dict(FAR))), "unknown")

    async def silent(self, t):
        async def alive(pane):
            tmuxio.asked(pane, answered=False)
            return False
        with fakepane.use(alive=alive):
            return await tmuxio.standing(t)

    def test_a_stand_in_that_only_says_alive_or_not_is_never_unknown(self):
        """The tests' fakes replace alive(): what they say no to is gone, as it always was for them."""
        async def no(t):
            return False
        with fakepane.use(alive=no):
            self.assertEqual(asyncio.run(tmuxio.standing(dict(FAR))), "gone")
            self.assertEqual(asyncio.run(tmuxio.Pane(dict(FAR)).standing()), "gone")


class Callers(unittest.TestCase):
    def setUp(self):
        self.addCleanup(tmuxio.UNANSWERED.clear)
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.st, self.rt = self.bridge.host.chan(self.ch), self.bridge.rt(self.ch)
        self.st.pop("tmux", None)
        self.st["pane"] = dict(FAR)
        self.answers = []

    async def alive(self, t):
        said = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        tmuxio.asked(t, answered=said is not None)
        return bool(said)

    def said(self):
        return "\n".join(m.content for m in self.ch.sent)

    def test_a_running_turn_is_not_said_to_have_ended(self):
        """watch_busy(): "Claude's process ended, this turn was not finished" on a line that dropped for a moment."""
        self.rt.turn, self.st["busy"] = tmux_bridge.Turn("go"), True
        self.answers = [None, None, None, True, True]
        naps = []

        async def nap(seconds):
            naps.append(seconds)
            if len(naps) >= 6:
                self.st["busy"] = False

        async def go():
            with fakepane.use(alive=self.alive, raw_screen=mock.AsyncMock(return_value="  esc to interrupt")), \
                    mock.patch.object(tmux_bridge.asyncio, "sleep", nap), mock.patch("builtins.print"):
                await self.bridge.watch_busy(self.ch, self.st, self.rt)
        asyncio.run(go())
        self.assertEqual(self.said(), "")
        self.assertIsNotNone(self.rt.turn, "the turn is still the channel's")
        self.answers = [None, False]
        self.st["busy"], self.st["bg"], naps[:] = True, 2, []

        async def go_on():
            with fakepane.use(alive=self.alive), mock.patch.object(tmux_bridge.asyncio, "sleep", mock.AsyncMock()), mock.patch("builtins.print"):
                await self.bridge.watch_busy(self.ch, self.st, self.rt)
        asyncio.run(go_on())
        self.assertIn("Claude 的行程結束了", self.said(), "gone for a fact, once its machine answers: said as ever")
        self.assertNotIn("bg", self.st, "what ran under it ended with it")

    def test_a_window_just_opened_is_not_given_up_while_its_machine_is_silent(self):
        """wait_ready(): taken for gone, open() closed the window and began a new conversation in its place."""
        self.answers = [None, None, True]
        screens = []

        async def raw_screen(t):
            screens.append(1)
            return box("❯ ")

        async def go():
            with fakepane.use(alive=self.alive, raw_screen=raw_screen), mock.patch.object(tmux_bridge.asyncio, "sleep", mock.AsyncMock()):
                return await self.bridge.wait_ready(dict(FAR), self.rt)
        self.assertTrue(asyncio.run(go()))
        self.assertEqual(len(screens), 1, "not read while nothing answers; read once it does")
        self.answers = [False]
        self.assertFalse(asyncio.run(go()), "gone for a fact: given up, as ever")

    def test_after_a_restart_a_busy_channel_is_not_said_to_be_gone(self):
        """pump(): "this channel's tmux conversation is gone (machine rebooted?), the work was interrupted", and its
        busy mark dropped, for a machine that did not answer in that moment."""
        self.st.update({"busy": True, "bg": 2})
        self.bridge.host.get_channel = lambda cid: self.ch
        self.bridge.host.channels_state = {str(self.ch.id): self.st}
        watched = []

        class Stop(Exception):
            pass

        nap = asyncio.sleep

        async def go(answers):
            self.answers = answers
            with fakepane.use(alive=self.alive), mock.patch.object(tmux_bridge, "write_settings", lambda: None), \
                    mock.patch.object(self.bridge, "read_words", mock.AsyncMock()), mock.patch.object(self.bridge, "remember_pids", mock.AsyncMock()), \
                    mock.patch.object(self.bridge, "start_streams", lambda: None), mock.patch.object(self.bridge, "watch", lambda *a: watched.append(1)), \
                    mock.patch.object(self.bridge, "pump_once", mock.AsyncMock(side_effect=Stop())), \
                    mock.patch.object(tmux_bridge.remote, "problem", None), mock.patch("traceback.print_exc"):
                with mock.patch.object(tmux_bridge.asyncio, "sleep", mock.AsyncMock(side_effect=Stop())):
                    try:
                        await self.bridge.pump()
                    except Stop:
                        pass
                for _ in range(5):  # look_again() runs on a task of its own (manager 11-19): let it finish
                    await nap(0)
        asyncio.run(go([None]))
        self.assertEqual(self.said(), "")
        self.assertEqual((self.st.get("busy"), self.st.get("bg"), watched), (True, 2, [1]), "kept, and watched until its machine answers")
        asyncio.run(go([False]))
        self.assertIn("tmux 對話不見了", self.said(), "gone for a fact: said as ever")
        self.assertEqual((self.st.get("busy"), self.st.get("bg")), (False, None))

    def test_a_machine_that_hangs_keeps_no_event_waiting_at_start(self):
        """The 11-18 rollback (manager 11-19): at start pump() asked each busy channel's machine in turn (look_again(),
        remember_pids()) before its first round; ssh timeouts in a row kept the self test unread past the trial.
        Now each on a task of its own: the first round comes at once, whatever the machines do."""
        self.st.update({"busy": True})
        self.st["pane"] = {k: v for k, v in FAR.items() if k != "pid"}  # (remember_pids() asks for it too)
        self.bridge.host.get_channel = lambda cid: self.ch
        self.bridge.host.channels_state = {str(self.ch.id): self.st}
        asked, rounds = [], []

        class Stop(Exception):
            pass

        async def hangs(t):
            asked.append(t)
            await asyncio.Event().wait()  # never answers

        async def first_round():
            rounds.append(len(asked))
            raise Stop()

        async def go():
            with fakepane.use(alive=hangs), mock.patch.object(tmux_bridge, "write_settings", lambda: None), \
                    mock.patch.object(self.bridge, "read_words", mock.AsyncMock()), \
                    mock.patch.object(self.bridge, "start_streams", lambda: None), \
                    mock.patch.object(self.bridge, "pump_once", first_round), \
                    mock.patch.object(tmux_bridge.remote, "problem", None), mock.patch("traceback.print_exc"):
                real = asyncio.sleep
                with mock.patch.object(tmux_bridge.asyncio, "sleep", mock.AsyncMock(side_effect=Stop())):
                    try:
                        await asyncio.wait_for(self.bridge.pump(), 5)
                    except Stop:
                        pass
                await real(0.05)
                left = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for t in left:  # (the two still asking)
                    t.cancel()
                await asyncio.gather(*left, return_exceptions=True)
        asyncio.run(go())
        self.assertEqual(len(rounds), 1, "the first round came")
        self.assertGreaterEqual(len(asked), 2, "both asked meanwhile, on tasks of their own: %r" % asked)
        self.assertEqual(self.said(), "", "nothing said of a machine that has not answered")
        self.assertTrue(self.st.get("busy"))

    def test_an_answer_about_a_window_the_channel_left_meanwhile_is_not_acted_on(self):
        """look_again() on a task: a message moved the channel to another window while the old one's machine was
        asked; its "gone" is not this channel's any more."""
        self.st.update({"busy": True})
        self.bridge.host.get_channel = lambda cid: self.ch

        async def gone_later(t):
            self.st["pane"] = dict(FAR, id="%9")  # meanwhile
            tmuxio.asked(t, answered=True)
            return False
        with fakepane.use(alive=gone_later), mock.patch.object(tmux_bridge.remote, "problem", None):
            asyncio.run(self.bridge.look_again(str(self.ch.id), self.st))
        self.assertEqual(self.said(), "")
        self.assertTrue(self.st.get("busy"))

    def far_silent(self):
        self.answers = [None]
        return mock.patch.object(tmux_bridge.remote, "unreachable", lambda host: "連不上 %s" % host)

    def test_a_turn_whose_machine_stays_silent_is_said_once_and_asked_less_often(self):
        """Manager on 25caa93: it went on every 8 seconds without end or word, one log line each. After two minutes
        the channel is told once (not that the turn ended); then every minute; the log says when it begins and
        when it is over. The channel stays busy, the turn is kept."""
        self.rt.turn, self.st["busy"] = tmux_bridge.Turn("go"), True
        clock, naps = [1000.0], []

        async def nap(seconds):
            naps.append(seconds)
            clock[0] += seconds
            if len(naps) == 30:
                self.answers = [True]   # the machine is back, and the turn still runs
            if len(naps) >= 33:
                self.st["busy"] = False

        async def go():
            import contextlib
            import io
            out = io.StringIO()
            with fakepane.use(alive=self.alive, raw_screen=mock.AsyncMock(return_value="  esc to interrupt")), self.far_silent(), \
                    mock.patch.object(tmux_bridge.asyncio, "sleep", nap), mock.patch.object(tmux_bridge.time, "time", lambda: clock[0]), \
                    contextlib.redirect_stdout(out):
                await self.bridge.watch_busy(self.ch, self.st, self.rt)
            return out.getvalue().splitlines()
        logged = asyncio.run(go())
        said = [m.content for m in self.ch.sent]
        self.assertEqual(len(said), 1, said)
        self.assertEqual(said[0], "⚠️ 連不上 lap，這一輪的狀態暫時不知道。連得上之後會接著貼；這個頻道還算在忙。")
        self.assertNotIn("結束", said[0])
        short = [n for n in naps if n == tmux_bridge.WATCH_SECS]
        self.assertEqual(naps[:16], [8] * 16, "every 8 seconds for the first two minutes")
        self.assertEqual(naps[16:30], [60] * 14, "then every minute")
        self.assertEqual(naps[30:], [8] * 3, "and every 8 seconds again once it answers")
        self.assertGreater(len(short), 0)
        self.assertEqual(len(logged), 2, logged)
        self.assertIn("lap does not answer", logged[0])
        self.assertIn("lap answers again", logged[1])
        self.assertIsNotNone(self.rt.turn)

    def test_a_line_that_dropped_for_a_moment_is_not_said(self):
        self.rt.turn, self.st["busy"] = tmux_bridge.Turn("go"), True
        self.answers = [None, None, None, True, True]
        naps = []

        async def nap(seconds):
            naps.append(seconds)
            if len(naps) >= 6:
                self.st["busy"] = False

        async def go():
            with fakepane.use(alive=self.alive, raw_screen=mock.AsyncMock(return_value="  esc to interrupt")), self.far_silent() as _, \
                    mock.patch.object(tmux_bridge.asyncio, "sleep", nap), mock.patch("builtins.print"):
                self.answers = [None, None, None, True, True]
                await self.bridge.watch_busy(self.ch, self.st, self.rt)
        asyncio.run(go())
        self.assertEqual(self.said(), "")
        self.assertEqual(set(naps), {tmux_bridge.WATCH_SECS})

    def test_the_turn_that_ended_meanwhile_has_its_end_posted_when_the_machine_is_back(self):
        """The Stop of a turn that ended while its machine was out of reach comes once the machine's events come
        again: the reply is posted with what the turn used, the channel is no longer busy, and the watch is over."""
        self.rt.turn, self.st["busy"] = tmux_bridge.Turn("go"), True
        self.st["session_id"] = "s-1"
        naps = []

        async def nap(seconds):
            naps.append(seconds)
            if len(naps) == 20:   # the machine's events come again: the turn's end among them
                await self.bridge.handle({"channel": str(self.ch.id), "host": "lap", "hook_event_name": "Stop", "session_id": "s-1",
                                          "last_assistant_message": "跑完了。", "dc_pane": {"socket": FAR["socket"], "pane": FAR["id"]},
                                          "dc_routed": True})
            if len(naps) > 40:
                raise AssertionError("the watch went on after the turn had ended")

        async def go():
            with fakepane.use(alive=self.alive, raw_screen=mock.AsyncMock(return_value=box("❯ "))), self.far_silent(), \
                    mock.patch.object(tmux_bridge.asyncio, "sleep", nap), mock.patch("builtins.print"), \
                    mock.patch.object(tmux_bridge, "WATCH_SAY_SECS", 0), mock.patch.object(self.bridge, "watch", lambda *a, **k: None):
                await self.bridge.watch_busy(self.ch, self.st, self.rt)
        asyncio.run(go())
        said = [m.content for m in self.ch.sent]
        self.assertEqual(len([text for text in said if "狀態暫時不知道" in text]), 1, said)
        self.assertTrue(any(text.startswith("跑完了。") for text in said), said)
        self.assertFalse(self.st.get("busy"))
        self.assertNotIn("行程結束了", "\n".join(said))

    def test_the_owners_commands_say_the_machine_cannot_be_reached_not_that_nothing_runs(self):
        """!stop, !screen, !key and !status on a channel whose pane is on a machine that does not answer."""
        import tempfile
        from bot import config, main as botmain, messages
        with mock.patch.object(config, "STATE", tempfile.mkdtemp()):
            bot = botmain.Bot()
        bot.audit = mock.AsyncMock()
        st = bot.chan(self.ch)
        st.pop("tmux", None)
        st.update({"pane": dict(FAR), "mode": "tmux", "configured": True})

        async def shot(the_bot, ch, t, send):
            await send("SHOT")

        def run(cmd, answers):
            self.answers, said = answers, []

            async def send(text, view=None, file=None):
                said.append(text)
            with fakepane.use(alive=self.alive, raw_screen=mock.AsyncMock(return_value=box("❯ ")),
                              screen=mock.AsyncMock(return_value="SCREEN"), keys=mock.AsyncMock()), self.far_silent(), \
                    mock.patch.object(botmain.screens, "post", shot), \
                    mock.patch.object(tmux_bridge.asyncio, "sleep", mock.AsyncMock()):
                self.answers = answers
                asyncio.run(bot.command(self.ch, cmd, send))
            return "\n".join(said)
        far = messages.far("command", said="連不上 lap")
        self.assertIn("這個指令沒有做", far)
        for cmd, gone in (("!stop", "這個頻道沒有正在跑的工作。"), ("!screen", "這個頻道沒有正在跑的 tmux 對話。"),
                          ("!key Enter", "用法：`!key Down Enter`（這個頻道要有正在跑的 tmux 對話）")):
            self.assertEqual(run(cmd, [None]), far, cmd)
            self.assertEqual(run(cmd, [False]), gone, cmd + ": its machine answered, the pane is not there: as ever")
        self.assertIn("SHOT", run("!screen", [True]))
        self.assertIn("那台連不上，不知道 Claude 還在不在這個窗格", run("!status", [None]))
        self.assertIn("Claude 不在這個窗格了", run("!status", [False]))
        self.assertIn("待命", run("!status", [True]))
        self.assertEqual(run("!key", [None]), "用法：`!key Down Enter`（這個頻道要有正在跑的 tmux 對話）", "no key named: the usage, whatever the machine")

    def test_a_dialog_on_a_silent_machine_keeps_its_buttons(self):
        from bot import menus
        m = self.bridge.menus
        m.open["77"] = {"channel": self.ch.id, "msg": 5, "title": "t"}

        class Response(object):
            said = edited = None

            async def send_message(self, text, ephemeral=False):
                self.said = text

            async def edit_message(self, view=None, content=None):
                self.edited = True

            async def defer(self):
                pass

        class Click(object):
            channel = self.ch

            def __init__(self):
                self.response = Response()

        def press(answers):
            self.answers, click = answers, Click()
            with fakepane.use(alive=self.alive), self.far_silent():
                self.answers = answers
                asyncio.run(m.act(click, "77", "enter"))
            return click.response
        r = press([None])
        self.assertEqual((r.said, r.edited), ("⚠️ 連不上 lap，這個按鈕沒有送出，按鈕留著。等那台連得上之後再按一次。", None))
        self.assertIn("77", m.open, "the menu is still the channel's")
        r = press([False])
        self.assertEqual((r.said, r.edited), (None, True), "gone for a fact: the buttons go, as ever")
        self.assertIsNotNone(menus)

    def test_a_dialog_that_cannot_be_read_says_why(self):
        async def go(answers):
            self.answers = answers
            self.ch.sent.clear()
            with fakepane.use(alive=self.alive), self.far_silent():
                self.answers = answers
                await self.bridge.menus.post(self.ch, self.st, {"message": "Claude needs your permission"})
            return self.said()
        said = asyncio.run(go([None]))
        self.assertTrue(said.startswith("🔔 Claude needs your permission\n-# ⚠️ 連不上 lap，讀不到畫面上的選項"), said)
        self.assertEqual(asyncio.run(go([False])), "🔔 Claude needs your permission", "gone for a fact: as ever")

    def test_no_caller_that_acts_on_gone_asks_only_alive(self):
        """The places in tmux_bridge.py and archive.py that do something when a pane is not alive go by standing().
        Left on alive() (it is "cannot be done just now" there, nothing is let go of or said to be gone by the
        bridge itself): remember_pids, stop, live_target, screen, key, reap (the bot's own sessions) and the own
        session of ensure()."""
        import inspect
        from bot import archive
        for function in (tmux_bridge.TmuxBridge.ensure_home, tmux_bridge.TmuxBridge.wait_ready, tmux_bridge.TmuxBridge.look_again,
                         tmux_bridge.TmuxBridge.watch_busy, archive.Archive.back):
            source = inspect.getsource(function)
            self.assertIn("standing(", source, function.__name__)
            self.assertNotIn("alive()", source, function.__name__)
            self.assertNotIn("tmuxio.alive(", source, function.__name__)
        ensure = inspect.getsource(tmux_bridge.TmuxBridge.ensure)
        self.assertIn("attached.standing()", ensure)
        self.assertNotIn("attached.alive()", ensure)


if __name__ == "__main__":
    unittest.main()
