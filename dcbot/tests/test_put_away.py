"""A channel put away by !detach gets nothing more from the session it followed (#issues 11, 2026-10-11).

A window the bot opened on this machine keeps reporting after the detach: DCBOT_CHANNEL is in its environment and
the bot's hooks in its --settings. Every turn it ran afterwards (a background task ending, the owner typing at the
terminal) was posted into the archived channel, and the archived state grew a session, a turn, a context again.
Played here on the whole road (tests/test_whole_turn.py: the real dchook, the spool, pump_once)."""
import asyncio
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from bot import archive, ask, tmux_bridge
from tests import fakepane
from tests import test_whole_turn as whole
from tests.test_turn_end import Ch, Host

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Window(whole.World, unittest.TestCase):
    """A window the bot opened in the owner's tmux on this machine, running Claude: what the tests below share."""
    test_a_whole_turn_and_the_one_after = None            # the turns of World are played where they belong
    test_what_was_said_before_a_message_sent_meanwhile = None

    def setUp(self):
        whole.World.setUp(self)
        self.window = {"socket": "/tmp/tmux-1/default", "id": "%5"}  # where the stand-in Claude runs, whatever becomes of the channel
        self.st.pop("tmux", None)
        self.st.update({"home": {"socket": "/tmp/tmux-1/default", "session": "work", "window": "notes"},
                        "pane": {"socket": "/tmp/tmux-1/default", "id": "%5"}, "pane_session": "work",
                        "session_id": self.claude.script.session, "mode": "tmux", "configured": True})
        self.claude.running = True
        self.claude.script.opened()

    def pane(self):
        return self.window

    def more_patches(self):  # where the real dchook waits for the answer to a question
        return [mock.patch.object(ask, "ANSWERS", os.path.join(self.bot_root, "state", "answers")),
                mock.patch.object(ask, "ASKS", os.path.join(self.bot_root, "state", "asks"))]

    async def detach(self):
        """What main.Bot.detach does to the bridge and to the channel's state."""
        st = self.bridge.host.chan(self.ch)
        await self.bridge.close(self.ch, kill=False)
        self.bridge.host.channels_state[str(self.ch.id)] = {"archived": archive.record(st), "cwd": st.get("cwd")}

    def back(self):
        """What archive.Archive.back does: the state made again from the record of the detach."""
        st = self.bridge.host.channels_state[str(self.ch.id)]
        rec = st["archived"]
        st.clear()
        st.update({"cwd": rec["cwd"], "mode": "tmux", "configured": True, "session_id": rec["session_id"],
                   "pane": rec["pane"], "pane_session": rec["pane_session"], "home": rec["home"]})

    def asking(self, flag, tool_input):
        """Claude Code runs the hook of a question (--ask) or of a plan (--plan): it waits for the bot's answer."""
        payload = {"session_id": self.claude.script.session, "transcript_path": self.claude.script.path, "cwd": self.tmp,
                   "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_input": tool_input}
        env = self.hook_env()
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "dchook"), flag], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, env=env)
        proc.stdin.write(json.dumps(payload).encode())
        proc.stdin.close()
        self.emitted += 1
        return proc

    async def answered(self, proc):
        for _ in range(500):
            if proc.poll() is not None:
                out = proc.stdout.read().decode()
                proc.stdout.close()
                return out
            await whole.real_sleep(0.02)
        proc.kill()
        self.fail("the hook is still waiting for an answer")

    def a_turn_at_the_terminal(self, words, background=()):
        self.claude.script.prompt("typed at the terminal")
        self.claude.busy = True
        self.claude.hook("UserPromptSubmit", prompt="typed at the terminal")
        self.claude.say("looking")
        self.claude.tool("Bash", {"command": "ls"})
        self.claude.stop(words, background=background)

class HomeWindow(Window):
    def test_after_the_detach_nothing_is_posted_written_or_logged(self):
        async def turns():
            await self.send("hello")
            self.claude.stop("hi")
            await self.settled()
            self.assertTrue(self.ch.sent[-1].content.startswith("hi\n-# "))
            await self.detach()
            host = self.bridge.host
            kept, posted, logged = copy.deepcopy(host.channels_state), len(self.ch.sent), len(host.audits)
            self.assertEqual(sorted(kept[str(self.ch.id)]), ["archived", "cwd"])

            self.a_turn_at_the_terminal("a reply nobody on Discord asked for", background=[{"id": "bg1"}])
            await self.settled()
            self.assertEqual(len(self.ch.sent), posted, "nothing posted: %r" % [m.content for m in self.ch.sent[posted:]])
            self.assertEqual(host.channels_state, kept, "the archived state is as the detach left it")
            self.assertEqual(host.audits[logged:], ["#c 已卸下，之後它的事件不再處理（那個對話在終端機照常跑；接回或重新 attach 就恢復）"])
            self.assertEqual(os.listdir(os.path.join(self.bot_root, "state", "events")), [], "handled, not piling up")
            self.assertFalse(os.path.exists(os.path.join(self.bot_root, "state", "events-bad")))
            self.assertIsNone(self.bridge.rt(self.ch).turn)

            self.a_turn_at_the_terminal("and another")  # a background task ending, hours later
            self.claude.hook("Notification", message="Claude needs your permission to use Bash")
            self.claude.hook("SessionEnd", reason="other")
            await self.settled()
            self.assertEqual((len(self.ch.sent), host.channels_state, len(host.audits)), (posted, kept, logged + 1), "#audit told once")
        self.play(turns)

    def test_a_question_asked_after_the_detach_is_handed_back_at_once(self):
        async def turns():
            await self.detach()
            posted = len(self.ch.sent)
            question = {"questions": [{"question": "Which one?", "header": "Pick", "options": [{"label": "A"}, {"label": "B"}]}]}
            out = await self.answered(self.asking("--ask", question))
            decision = json.loads(out)["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny")
            self.assertEqual(decision["permissionDecisionReason"], tmux_bridge.UNFOLLOWED)
            out = await self.answered(self.asking("--plan", {"plan": "1. do it"}))
            self.assertEqual(out, "", "a plan: the hook says nothing, so the terminal's own dialog takes it")
            await self.settled()
            self.assertEqual(len(self.ch.sent), posted)
            self.assertEqual(ask.waiting(self.ch.id), [])
        self.play(turns)

    def test_a_question_still_open_is_closed_by_the_detach(self):
        async def turns():
            await self.send("hello")
            question = {"questions": [{"question": "Which one?", "header": "Pick", "options": [{"label": "A"}, {"label": "B"}]}]}
            proc = self.asking("--ask", question)
            await self.settled()
            self.assertEqual(len(ask.waiting(self.ch.id)), 1, "shown on Discord, waiting for the owner")
            await self.detach()
            decision = json.loads(await self.answered(proc))["hookSpecificOutput"]
            self.assertEqual((decision["permissionDecision"], decision["permissionDecisionReason"]), ("deny", tmux_bridge.UNFOLLOWED))
            self.assertEqual(ask.waiting(self.ch.id), [])
        self.play(turns)

    def test_taken_back_it_goes_on_from_the_record_of_the_detach(self):
        async def turns():
            await self.send("hello")
            self.claude.stop("hi")
            await self.settled()
            await self.detach()
            record = copy.deepcopy(self.bridge.host.channels_state[str(self.ch.id)]["archived"])
            self.a_turn_at_the_terminal("meanwhile", background=[{"id": "bg1"}])
            await self.settled()
            self.assertEqual(self.bridge.host.channels_state[str(self.ch.id)]["archived"], record)
            self.back()
            st = self.bridge.host.chan(self.ch)
            self.assertNotIn("bg", st)
            posted, logged = len(self.ch.sent), len(self.bridge.host.audits)
            m = await self.send("and now?")
            self.assertEqual(m.reactions, ["👀"])
            self.claude.stop("back with you")
            await self.settled()
            self.assertTrue(self.ch.sent[-1].content.startswith("back with you\n-# "))
            self.assertEqual(self.bridge.host.audits[-1].split("（")[0], "#c：and now? → 完成")
            await self.detach()  # and put away again: #audit is told again, once
            self.a_turn_at_the_terminal("again alone")
            await self.settled()
            self.assertEqual(len([a for a in self.bridge.host.audits[logged:] if "已卸下" in a]), 1)
        self.play(turns)


class AnotherConversationSince(Window):
    """#code-review 2026-10-11, on the real road: the channel detached and then given another conversation ("在這裡
    開新對話", the setup card) is no longer put away, and the old window, still alive in the owner's tmux with this
    channel's DCBOT_CHANNEL, went on posting into it; its session id was written over the new conversation's."""

    async def a_turn_then_detach(self):
        await self.send("hello")
        self.claude.stop("hi")
        await self.settled()
        await self.detach()

    def test_a_the_state_dropped_for_a_new_start(self):
        async def turns():
            await self.a_turn_then_detach()
            host = self.bridge.host
            host.channels_state.pop(str(self.ch.id))  # what the button does before it shows the setup card
            posted, logged = len(self.ch.sent), len(host.audits)
            self.a_turn_at_the_terminal("the old window, still used at the terminal")
            await self.settled()
            self.assertEqual(len(self.ch.sent), posted, [m.content for m in self.ch.sent[posted:]])
            self.assertEqual(host.channels_state, {}, "no state grows back for it")
            self.assertEqual(host.audits[logged:], ["#c 收到別的視窗（%5）的事件：那個視窗是這個頻道以前的對話，現在不是，"
                                                    "之後它的事件不再處理（它在終端機照常跑）"])
            self.a_turn_at_the_terminal("again")
            await self.settled()
            self.assertEqual((len(self.ch.sent), len(host.audits)), (posted, logged + 1), "told once")
        self.play(turns)

    def test_b_a_conversation_of_its_own_since(self):
        for new in ({"tmux": "dc-c-0005"}, {"home": {"socket": "/tmp/tmux-1/default", "session": "work", "window": "notes"},
                                            "pane": {"socket": "/tmp/tmux-1/default", "id": "%9"}}):
            self.setUp()

            async def turns():
                await self.a_turn_then_detach()
                host = self.bridge.host
                host.channels_state[str(self.ch.id)] = dict({"cwd": self.tmp, "mode": "tmux", "configured": True,
                                                             "session_id": "new-session-id"}, **new)
                kept, posted = copy.deepcopy(host.channels_state), len(self.ch.sent)
                self.a_turn_at_the_terminal("the old window, still used at the terminal")
                question = {"questions": [{"question": "Which one?", "header": "Pick", "options": [{"label": "A"}, {"label": "B"}]}]}
                decision = json.loads(await self.answered(self.asking("--ask", question)))["hookSpecificOutput"]
                self.assertEqual(decision["permissionDecisionReason"], tmux_bridge.UNFOLLOWED)
                await self.settled()
                self.assertEqual(len(self.ch.sent), posted, [m.content for m in self.ch.sent[posted:]])
                self.assertEqual(host.channels_state, kept, "above all its session id: the next start would resume the old conversation")
                self.assertEqual(len([a for a in host.audits if "別的視窗（%5）" in a]), 1)
            self.play(turns)
            self.doCleanups()

    def test_the_channels_new_window_is_heard(self):
        async def turns():
            await self.a_turn_then_detach()
            new = {"socket": "/tmp/tmux-1/default", "id": "%9"}
            self.bridge.host.channels_state[str(self.ch.id)] = {
                "cwd": self.tmp, "mode": "tmux", "configured": True, "session_id": self.claude.script.session, "pane": new,
                "home": {"socket": "/tmp/tmux-1/default", "session": "work", "window": "notes"}}
            old, self.window = self.window, new  # the stand-in is the new window's Claude from here on
            m = await self.send("and here?")
            self.assertEqual(m.reactions, ["👀"])
            self.claude.stop("in the new window")
            await self.settled()
            self.assertTrue(self.ch.sent[-1].content.startswith("in the new window\n-# "))
            posted = len(self.ch.sent)
            subprocess.run([sys.executable, os.path.join(ROOT, "tools", "dchook")], check=True, env=self.hook_env(old), input=json.dumps(
                {"session_id": "old", "hook_event_name": "Stop", "last_assistant_message": "from the old one"}).encode())
            self.emitted += 1
            await self.settled()
            self.assertEqual(len(self.ch.sent), posted)
        self.play(turns)


class SameWindow(Window):
    """What is the channel's own although the session id changes, or the window does."""

    def test_clear_and_resume_in_the_same_window(self):
        async def turns():
            await self.send("hello")
            self.claude.stop("hi")
            await self.settled()
            self.claude.hook("SessionEnd", reason="clear")
            self.claude.script.session = "a-session-after-clear"
            self.claude.hook("SessionStart", source="clear")
            await self.settled()
            st = self.bridge.host.chan(self.ch)
            self.assertEqual(st["session_id"], "a-session-after-clear", "/clear: a new session in the same pane is the channel's")
            m = await self.send("after the clear")
            self.claude.stop("still here")
            await self.settled()
            self.assertEqual(m.reactions, ["👀"])
            self.assertTrue(self.ch.sent[-1].content.startswith("still here\n-# "))
            self.assertEqual([a for a in self.bridge.host.audits if "不再處理" in a], [])
        self.play(turns)

    def test_resume_inside_the_window_has_the_history_posted(self):
        """/resume typed in the window, another conversation picked: SessionStart says source "resume" with the
        other session's id, and its history goes to the channel (history.show)."""
        async def turns():
            await self.send("hello")
            self.claude.stop("hi")
            await self.settled()
            shown = []

            async def show(host, ch, st, session_id, path, held=None):
                shown.append((ch.id, session_id, path))
            with mock.patch.object(tmux_bridge.history, "show", show):
                self.claude.hook("SessionEnd", reason="resume")
                self.claude.script.session = "an-older-conversation"
                self.claude.hook("SessionStart", source="resume")
                await self.settled()
                for _ in range(5):
                    await whole.real_sleep(0.01)
            self.assertEqual(shown, [(self.ch.id, "an-older-conversation", self.claude.script.path)])
            self.assertEqual(self.bridge.host.chan(self.ch)["session_id"], "an-older-conversation")
        self.play(turns)

    def test_a_window_opened_again_is_heard_from_its_first_event(self):
        """The window was closed; the next message opens a new one (--resume). Its SessionStart comes before the
        channel remembers its pane, and is what the opening waits for; the old window's last word comes late."""
        async def turns():
            await self.send("hello")
            self.claude.stop("hi")
            await self.settled()
            old = dict(self.window)
            self.claude.running = False  # the owner closed the window
            opened = []

            async def open_window(socket, session, window_name, cwd, argv, env=None, host=None, why=None):
                opened.append(list(argv))
                self.window = {"socket": socket, "id": "%9"}
                self.claude.running = True
                self.claude.hook("SessionStart", source="resume")
                return dict(self.window)

            async def late():  # the old window's SessionEnd, as the new one comes up
                await whole.real_sleep(0.05)
                subprocess.run([sys.executable, os.path.join(ROOT, "tools", "dchook")], check=True, env=self.hook_env(old),
                               input=json.dumps({"session_id": "s", "hook_event_name": "SessionEnd", "reason": "other"}).encode())
                self.emitted += 1
            with fakepane.replace("open_window", open_window), mock.patch.object(tmux_bridge.history, "show", mock.AsyncMock()):
                asyncio.ensure_future(late())
                m = await self.send("are you there?")
            self.assertEqual(len(opened), 1)
            self.assertEqual(opened[0][-2:], ["--resume", self.claude.script.session])
            st = self.bridge.host.chan(self.ch)
            self.assertEqual(st["pane"]["id"], "%9")
            self.assertEqual(m.reactions, ["👀"], "typed into the new window once it reported")
            self.claude.stop("yes")
            await self.settled()
            said = [x.content for x in self.ch.sent]
            self.assertTrue(said[-1].startswith("yes\n-# "))
            self.assertFalse(any("Claude 結束了" in x for x in said), "the old window's end is not this conversation's")
            self.assertIsNone(self.bridge.rt(self.ch).opening)
        self.play(turns)


class OwnSession(whole.World, unittest.TestCase):
    """A session on the bot's own tmux server: the same hook, the same road."""
    test_a_whole_turn_and_the_one_after = None
    test_what_was_said_before_a_message_sent_meanwhile = None

    def test_resume_inside_it_has_the_history_posted(self):
        async def turns():
            await self.send("hello")  # this starts the session
            self.claude.stop("hi")
            await self.settled()
            shown = []

            async def show(host, ch, st, session_id, path, held=None):
                shown.append((ch.id, session_id, path))
            with mock.patch.object(tmux_bridge.history, "show", show):
                self.claude.hook("SessionEnd", reason="resume")
                self.claude.script.session = "an-older-conversation"
                self.claude.hook("SessionStart", source="resume")
                await self.settled()
                for _ in range(5):
                    await whole.real_sleep(0.01)
                self.claude.hook("SessionEnd", reason="clear")  # and a /clear after it: a new session, no history to show
                self.claude.script.session = "a-fresh-one"
                self.claude.hook("SessionStart", source="clear")
                await self.settled()
            self.assertEqual(shown, [(self.ch.id, "an-older-conversation", self.claude.script.path)])
            self.assertEqual(self.bridge.host.chan(self.ch)["session_id"], "a-fresh-one")
        self.play(turns)


class AttachedAgain(unittest.TestCase):
    """A window the bot opened, detached, then attached again with /attach (to the same channel or another): it
    still runs the hooks of its --settings with its DCBOT_CHANNEL, and the route table now names its pane, so the
    global hook reports it too. Each event once: the one of its own settings stands back. With the real dchook."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="bot ")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "tools"))
        os.makedirs(os.path.join(self.root, "state"))
        shutil.copy(os.path.join(ROOT, "tools", "dchook"), os.path.join(self.root, "tools", "dchook"))
        self.env = dict({k: v for k, v in os.environ.items() if not k.startswith(("DCBOT_", "TMUX"))},
                        TMUX="/tmp/tmux-1/default,123,0", TMUX_PANE="%5")

    def routes(self, table):
        with open(os.path.join(self.root, "state", "routes.json"), "w") as f:
            json.dump(table, f)

    def fire(self, *flags, **payload):
        own = "--global" not in flags
        env = dict(self.env, DCBOT_ROOT=self.root, DCBOT_CHANNEL="5") if own else self.env
        body = dict({"session_id": "s", "hook_event_name": "Stop", "last_assistant_message": "x"}, **payload)
        r = subprocess.run([sys.executable, os.path.join(self.root, "tools", "dchook")] + list(flags), input=json.dumps(body).encode(),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=20)
        self.assertEqual((r.returncode, r.stderr), (0, b""))
        return r.stdout

    def events(self):
        spool = os.path.join(self.root, "state", "events")
        out = []
        for name in sorted(os.listdir(spool)) if os.path.isdir(spool) else []:
            with open(os.path.join(spool, name)) as f:
                out.append(json.load(f))
        return out

    def test_while_it_is_the_channels_own_window_its_settings_report(self):
        self.routes({})
        self.fire()
        self.fire("--global")
        self.assertEqual([(e["channel"], "dc_pane" in e) for e in self.events()], [("5", True)])

    def test_attached_again_only_the_route_table_reports(self):
        for channel in ("5", "8"):  # to its old channel, or to another one
            shutil.rmtree(os.path.join(self.root, "state", "events"), ignore_errors=True)
            self.routes({"/tmp/tmux-1/default|%5": {"channel": channel, "pid": ""}})
            self.fire()
            self.fire("--global")
            self.assertEqual([(e["channel"], e.get("dc_routed")) for e in self.events()], [(channel, True)], "once, where the table says")

    def test_a_question_then_is_asked_once(self):
        """Its own --ask hook answers nothing (so decides nothing) and does not wait: the table's one asks."""
        self.routes({"/tmp/tmux-1/default|%5": {"channel": "5", "pid": ""}})
        question = {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "Which?"}]}}
        self.assertEqual(self.fire("--ask", **question), b"")
        self.assertEqual(self.fire("--plan", hook_event_name="PermissionRequest", tool_name="ExitPlanMode", tool_input={"plan": "x"}), b"")
        self.assertEqual(self.events(), [])


class Elsewhere(unittest.TestCase):
    def test_the_table(self):
        old = {"socket": "/tmp/tmux-1/default", "pane": "%5"}
        ev = lambda src=old, **kw: dict({"hook_event_name": "Stop", "dc_pane": src}, **kw)  # noqa: E731
        f = tmux_bridge.from_elsewhere
        mine = {"socket": "/tmp/tmux-1/default", "id": "%5", "pid": "77"}
        other = {"socket": "/tmp/tmux-1/default", "id": "%9"}
        home = {"socket": "/tmp/tmux-1/default", "session": "work"}
        own = {"socket": "/tmp/tmux-1000/" + tmux_bridge.config.TMUX_SOCKET, "pane": "%0"}
        self.assertFalse(f({"pane": mine, "home": home}, None, ev()), "its window")
        self.assertTrue(f({"pane": other, "home": home}, None, ev()), "another window of the same tmux")
        self.assertTrue(f({"pane": dict(mine, socket="/tmp/tmux-2/default")}, None, ev()), "the same number on another server")
        self.assertTrue(f({"home": home}, None, ev()), "a home with no window now, none being opened")
        self.assertFalse(f({"home": home}, True, ev()), "one is being opened: not yet known which")
        self.assertFalse(f({"home": home, "pane": other}, {"socket": "/tmp/tmux-1/default", "id": "%5"}, ev()), "the one just opened")
        self.assertTrue(f({"home": home, "pane": other}, {"socket": "/tmp/tmux-1/default", "id": "%7"}, ev()))
        self.assertFalse(f({"tmux": "dc-c-0005"}, None, ev(own)), "its session on the bot's own server, whichever process")
        self.assertTrue(f({"tmux": "dc-c-0005"}, None, ev()), "a window in the owner's tmux is not a session of the bot's")
        self.assertTrue(f({}, None, ev()), "no state at all")
        self.assertTrue(f({"mode": "print", "session_id": "s"}, None, ev()))
        for unjudged in (ev(None), ev({}), ev({"pane": "%5"}), ev("x"), ev(host="lap"), {"hook_event_name": "Stop"}):
            self.assertFalse(f({}, None, unjudged), unjudged)
        self.assertFalse(f({"pane": dict(mine, host="lap")}, None, ev(host="lap")), "another machine's: the route table's business")

    def test_by_the_route_table_only_a_window_the_bot_opened_is_judged(self):
        """#issues 35: the route table's hook says the pane it went by ("dc_pane", "dc_routed"). A window the bot
        opened on another machine reports that way: its events are told from those of the window the channel has
        now, as on this machine (the bot closes a window, and the window's end comes after). A pane that was
        attached is the table's business, here and there, as it always was."""
        f = tmux_bridge.from_elsewhere
        far = {"socket": "/tmp/tmux-1/default", "pane": "%5"}
        ev = lambda **kw: dict({"hook_event_name": "SessionEnd", "dc_pane": far, "dc_routed": True, "host": "lap"}, **kw)  # noqa: E731
        mine = {"socket": "/tmp/tmux-1/default", "id": "%5", "pid": "77", "host": "lap"}
        home = {"socket": "/tmp/tmux-1/default", "session": "work", "host": "lap"}
        self.assertFalse(f({"pane": mine, "home": home}, None, ev()), "its window")
        self.assertTrue(f({"home": home}, None, ev()), "the bot closed it: the channel has a home and no window now")
        self.assertTrue(f({"home": home, "pane": dict(mine, id="%9")}, None, ev()), "and has another by now")
        self.assertFalse(f({"home": home}, True, ev()), "one is being opened: not yet known which")
        self.assertFalse(f({"home": home, "pane": dict(mine, id="%9")}, dict(mine), ev()), "the one just opened")
        self.assertTrue(f({"home": home, "pane": dict(mine, host="desk")}, None, ev()), "the same pane number on another machine")
        self.assertTrue(f({"home": dict(home, host=None), "pane": {"socket": "/tmp/tmux-1/default", "id": "%5"}}, None, ev()), "nor this machine's pane of that number")
        self.assertFalse(f({"home": home, "pane": dict(mine, socket="/private/tmp/tmux-1/default")}, None, ev()), "macOS names the same socket two ways")
        for attached in ({"pane": mine}, {"pane": dict(mine, id="%9")}, {"pane": {"socket": "/tmp/tmux-1/default", "id": "%9"}}):
            self.assertFalse(f(attached, None, ev()), "a pane that was attached: not judged")
            self.assertFalse(f(attached, None, ev(host=None)), "nor on this machine")
        self.assertFalse(f({"home": home}, None, {k: v for k, v in ev().items() if k != "dc_routed"}), "another machine's and not by its table: as before")
        self.assertFalse(f({"home": home}, None, {"hook_event_name": "SessionEnd", "host": "lap"}),
                         "a hook of before this on that machine says no pane: nothing to judge by (the end is said, as it was)")
        here = {"socket": "/tmp/tmux-1/default", "session": "work"}
        self.assertTrue(f({"home": here}, None, ev(host=None)), "this machine's window attached as a pane and closed: the same rule")
        self.assertFalse(f({"home": here, "pane": {"socket": "/tmp/tmux-1/default", "id": "%5"}}, None, ev(host=None)))
        self.assertTrue(f({"tmux": "dc-c-0005", "home": home}, None, ev()), "a session on the bot's own server is not that machine's window")

    def test_the_hook_says_where_it_runs_only_for_the_channel_of_the_environment(self):
        root = tempfile.mkdtemp(prefix="hook ")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("DCBOT_", "TMUX"))}

        def fire(env, *flags):
            subprocess.run([sys.executable, os.path.join(ROOT, "tools", "dchook")] + list(flags), check=True, env=dict(clean, **env),
                           input=json.dumps({"session_id": "s", "hook_event_name": "Stop", "last_assistant_message": "x"}).encode())
            spool = os.path.join(root, "state", "events")
            newest = sorted(os.listdir(spool))[-1]
            with open(os.path.join(spool, newest)) as f:
                return json.load(f)
        base = {"DCBOT_ROOT": root, "DCBOT_CHANNEL": "5"}
        self.assertEqual(fire(dict(base, TMUX="/tmp/tmux-1/default,123,0", TMUX_PANE="%5"))["dc_pane"],
                         {"socket": "/tmp/tmux-1/default", "pane": "%5"})
        self.assertNotIn("dc_pane", fire(base), "not in tmux: nothing to say")
        self.assertNotIn("dc_pane", fire(dict(base, TMUX_PANE="%5")))
        self.assertNotIn("dc_routed", fire(dict(base, TMUX="/tmp/tmux-1/default,123,0", TMUX_PANE="%5")), "by the environment, not by the table")

    def test_the_route_tables_hook_says_the_pane_it_went_by(self):
        root = tempfile.mkdtemp(prefix="hook ")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        os.makedirs(os.path.join(root, "tools"))
        os.makedirs(os.path.join(root, "state"))
        hook = os.path.join(root, "tools", "dchook")
        shutil.copy(os.path.join(ROOT, "tools", "dchook"), hook)
        with open(os.path.join(root, "state", "routes.json"), "w") as f:
            json.dump({"/tmp/tmux-1/default|%5": {"channel": "5", "pid": str(os.getpid())}}, f)
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("DCBOT_", "TMUX"))}
        for payload in ({"session_id": "s", "hook_event_name": "SessionEnd", "reason": "other"},
                        {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "x"}}):
            subprocess.run([sys.executable, hook, "--global"], check=True, env=dict(clean, TMUX="/tmp/tmux-1/default,123,0", TMUX_PANE="%5"),
                           input=json.dumps(payload).encode())
        spool = os.path.join(root, "state", "events")
        got = []
        for name in sorted(os.listdir(spool)):
            with open(os.path.join(spool, name)) as f:
                got.append(json.load(f))
        self.assertEqual([(e["channel"], e["dc_pane"], e["dc_routed"]) for e in got], [("5", {"socket": "/tmp/tmux-1/default", "pane": "%5"}, True)] * 2)
        self.assertEqual(got[0]["reason"], "other", "what Claude Code said is left as it is")

    def test_the_pane_is_named_as_the_table_names_it(self):
        """#code-review on d64eaee: a machine whose tmux socket lies behind a link (its /tmp, a TMUX_TMPDIR): the bot
        wrote one name into the table, tmux says another in $TMUX. The hook finds the route all the same (it follows
        links: #issues 25), and says the pane by the table's name: the one the bot remembers for the channel's
        window and compares with, on another machine too, whose links it cannot follow. Said as $TMUX has it,
        every event of a window the bot opened there was another window's."""
        root = tempfile.mkdtemp(prefix="hook ")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        os.makedirs(os.path.join(root, "tools"))
        os.makedirs(os.path.join(root, "state"))
        os.makedirs(os.path.join(root, "real", "tmux-1"))
        os.symlink(os.path.join(root, "real"), os.path.join(root, "link"))
        written, by_tmux = os.path.join(root, "link", "tmux-1", "default"), os.path.join(os.path.realpath(root), "real", "tmux-1", "default")
        self.assertNotEqual(written, by_tmux)
        hook = os.path.join(root, "tools", "dchook")
        shutil.copy(os.path.join(ROOT, "tools", "dchook"), hook)
        with open(os.path.join(root, "state", "routes.json"), "w") as f:
            json.dump({written + "|%5": {"channel": "5", "pid": str(os.getpid())}}, f)
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("DCBOT_", "TMUX"))}
        subprocess.run([sys.executable, hook, "--global"], check=True, env=dict(clean, TMUX=by_tmux + ",123,0", TMUX_PANE="%5"),
                       input=json.dumps({"session_id": "s", "hook_event_name": "Stop"}).encode())
        spool = os.path.join(root, "state", "events")
        (name,) = os.listdir(spool)
        with open(os.path.join(spool, name)) as f:
            ev = json.load(f)
        self.assertEqual((ev["dc_pane"], ev["dc_routed"]), ({"socket": written, "pane": "%5"}, True))
        f = tmux_bridge.from_elsewhere
        far = dict(ev, host="lap")
        home = {"socket": written, "session": "work", "host": "lap"}
        mine = {"socket": written, "id": "%5", "host": "lap"}
        self.assertFalse(f({"pane": mine, "home": home}, None, far), "the channel's window, whatever tmux calls its socket")
        self.assertTrue(f({"home": home}, None, far), "closed by the bot")
        self.assertTrue(f({"home": home, "pane": dict(mine, id="%9")}, None, far))
        self.assertFalse(f({"pane": dict(mine, id="%9")}, None, far), "a pane that was attached: not judged, as before")
        said_by_tmux = dict(far, dc_pane={"socket": by_tmux, "pane": "%5"})
        self.assertTrue(f({"pane": mine, "home": home}, None, said_by_tmux), "(what d64eaee said, and why it was wrong)")

    def test_what_claude_code_says_in_the_event_is_left_as_it_is(self):
        """#code-review-2 2026-10-11: the pane was first written into "source", over Claude Code's own field of
        that name: a /resume inside the window no longer had the conversation's history posted."""
        root = tempfile.mkdtemp(prefix="hook ")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("DCBOT_", "TMUX"))}
        said = {"session_id": "s", "hook_event_name": "SessionStart", "source": "resume", "transcript_path": "/t/s.jsonl", "cwd": "/w",
                "model": "claude-opus-5-5", "permission_mode": "bypassPermissions"}
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "dchook")], check=True, input=json.dumps(said).encode(),
                       env=dict(clean, DCBOT_ROOT=root, DCBOT_CHANNEL="5", TMUX="/tmp/tmux-1/default,123,0", TMUX_PANE="%5"))
        spool = os.path.join(root, "state", "events")
        with open(os.path.join(spool, os.listdir(spool)[0])) as f:
            got = json.load(f)
        added = dict((k, got.pop(k)) for k in ("channel", "ts", "dc_pane"))
        self.assertEqual(got, said, "every field Claude Code sent, untouched")
        self.assertEqual((added["channel"], added["dc_pane"]), ("5", {"socket": "/tmp/tmux-1/default", "pane": "%5"}))


class Told(unittest.TestCase):
    """handle() itself, whatever road the event came by."""

    def setUp(self):
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.state = self.bridge.host.channels_state

    def test_what_counts_as_put_away(self):
        rec = {"pane": {"id": "%1"}, "session_id": "s", "cwd": "/w", "home": None, "pane_session": "st"}
        self.assertTrue(tmux_bridge.put_away({"archived": rec, "cwd": "/w"}))
        self.assertFalse(tmux_bridge.put_away({}))
        self.assertFalse(tmux_bridge.put_away({"tmux": "dc-x", "cwd": "/w"}))
        for taken_up in ({"pane": {"id": "%1"}}, {"tmux": "dc-x"}, {"home": {"session": "work"}}):
            self.assertFalse(tmux_bridge.put_away(dict({"archived": rec}, **taken_up)), taken_up)

    def test_an_event_from_another_machine_is_dropped_the_same(self):
        """Its window reports through that machine's route table, which the detach clears (the next test); an event
        already on its way, or a table that could not be written, ends here too."""
        self.state[str(self.ch.id)] = {"archived": {"pane": {"id": "%1", "host": "lap"}, "session_id": "s", "cwd": "/w"}, "cwd": "/w"}
        kept = copy.deepcopy(self.state)
        delivered = []

        async def deliver_to(host, ask_id, result):
            delivered.append((host, ask_id, result))

        async def go():
            for ev in ({"hook_event_name": "UserPromptSubmit", "prompt": "x"}, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "texts": ["said"]},
                       {"hook_event_name": "Stop", "last_assistant_message": "done", "texts": ["said"], "context": [5, "m"]},
                       {"hook_event_name": "Ask", "ask_id": "q1", "questions": []}, {"hook_event_name": "Plan", "ask_id": "p1", "plan": "x"},
                       {"hook_event_name": "SubagentStart", "agent_id": "a1"}):
                await self.bridge.handle(dict(ev, channel=str(self.ch.id), host="lap", session_id="s2"))
        with mock.patch.object(ask, "deliver_to", deliver_to):
            asyncio.run(go())
        self.assertEqual((self.ch.sent, self.state), ([], kept))
        self.assertEqual(delivered, [("lap", "q1", {"reason": tmux_bridge.UNFOLLOWED}), ("lap", "p1", {})])
        self.assertEqual(len(self.bridge.host.audits), 1)

    def test_a_channel_in_use_is_not_touched_by_this(self):
        async def go():
            await self.bridge.handle({"channel": str(self.ch.id), "hook_event_name": "UserPromptSubmit", "prompt": "do it", "session_id": "s"})
        asyncio.run(go())
        self.assertTrue(self.state[str(self.ch.id)]["busy"])
        self.assertEqual(self.bridge.host.audits, [])
        asyncio.run(self.bridge.shutdown())


class OtherMachinesWindow(unittest.TestCase):
    """A pane attached, or a window the bot opened on another machine, reports through the global hook, which goes
    by the route table alone (DCBOT_CHANNEL in the window's environment is for dcpost, not for the hook). The
    detach takes the route out, and with it every event: pinned here, with the real dchook."""

    def fire(self, root, env):
        payload = {"session_id": "s", "hook_event_name": "Stop", "last_assistant_message": "x"}
        hook = os.path.join(root, "tools", "dchook")
        r = subprocess.run([sys.executable, hook, "--global"], input=json.dumps(payload).encode(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=dict({k: v for k, v in os.environ.items() if not k.startswith(("DCBOT_", "TMUX"))}, **env))
        self.assertEqual((r.returncode, r.stdout), (0, b""))
        spool = os.path.join(root, "state", "events")
        return sorted(os.listdir(spool)) if os.path.isdir(spool) else []

    def test_no_route_no_event_whatever_the_environment_says(self):
        root = tempfile.mkdtemp(prefix="that machine ")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        os.makedirs(os.path.join(root, "tools"))
        os.makedirs(os.path.join(root, "state"))
        shutil.copy(os.path.join(ROOT, "tools", "dchook"), os.path.join(root, "tools", "dchook"))
        pane = {"TMUX": "/tmp/tmux-1/default,123,0", "TMUX_PANE": "%7"}
        with open(os.path.join(root, "state", "routes.json"), "w") as f:
            json.dump({"/tmp/tmux-1/default|%7": {"channel": "5", "pid": ""}}, f)
        self.assertEqual(len(self.fire(root, pane)), 1, "attached: it reports")
        with open(os.path.join(root, "state", "routes.json"), "w") as f:
            json.dump({}, f)  # what routes.unbind leaves once the channel is detached
        before = self.fire(root, pane)
        self.assertEqual(self.fire(root, dict(pane, DCBOT_CHANNEL="5")), before, "a window the bot opened there: DCBOT_CHANNEL alone")
        self.assertEqual(self.fire(root, dict(pane, DCBOT_CHANNEL="5", DCBOT_ROOT=root)), before, "nor with a root: --global goes by the table")


if __name__ == "__main__":
    unittest.main()
