"""handle() goes by the kind of a turn's events (stage 3b step 2.2b): something said to Claude and whose words they
are, a tool call, a message sent to another conversation, the turn's end, its failure, a dialog on the screen. What
is posted and when is as it was. (Held against the tree of before with every measured event and a made-up list of
each branch, 896 events: the same; here the rules themselves.)"""
import asyncio
import contextlib
import glob
import io
import json
import os
import types
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import hooks
from altention.tests.connectors.claude_code.test_normalize import HERE as SAMPLES
from bot import ask, peers, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_turn_end import Ch, Host

TERMINAL = "⌨️ **終端機那邊輸入**\n> "


class Sent(object):
    def __init__(self, n, content, embed):
        self.id, self.content, self.embed = n, content, embed

    async def add_reaction(self, emoji):
        pass

    async def edit(self, **kw):
        self.content = kw.get("content", self.content)

    async def create_thread(self, name=None, **kw):  # (a subagent's thread: what goes into it is not looked at here)
        async def send(text=None, **more):
            return Sent(0, text, None)

        async def edit(**more):
            pass
        return types.SimpleNamespace(id=9000 + self.id, name=name, send=send, edit=edit)


real_sleep = asyncio.sleep


async def nap(seconds, result=None):
    """Every wait (a subagent's task row is waited for), shortened."""
    await real_sleep(0)
    return result


class Channel(Ch):
    async def send(self, content=None, embed=None, **kw):
        self.sent.append(Sent(len(self.sent) + 1, content, embed))
        return self.sent[-1]


class Typed(object):
    """A message the owner sent from Discord, which the bot typed in and waits to see taken."""

    def __init__(self, text):
        self.text, self.got, self.late, self.reactions = text, asyncio.Event(), 0, []
        self.message = self

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)


def P(prompt, **more):
    return dict({"hook_event_name": "UserPromptSubmit", "prompt": prompt, "session_id": "s1"}, **more)


def play(events, typed=(), asking=(), state=None, keyed=()):
    """For each event: (what was posted with it: text, or "embed: author | description"), then the bridge's own doings
    beside posting, the channel's state and runtime, what was printed."""
    async def go():
        ch = Channel()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        bridge.last_reap = 1e12
        st, rt = bridge.host.chan(ch), bridge.rt(ch)
        st.update(dict({"tmux": "dc-x-1", "cwd": "/w"}, **(state or {})))
        rt.pending.extend(typed)
        rt.typed.extend(keyed)  # what the bot itself typed in lately (a slash command, a retry)
        did, rows = [], []

        async def post(c, s, ev):
            did.append("menu")

        async def resolve(c):
            did.append("resolve")

        async def cancel(c, why):
            did.append("cancel")
        with mock.patch.object(asyncio, "sleep", nap), mock.patch.object(bridge, "watch", lambda *a, **k: None), mock.patch.object(bridge.menus, "post", post), \
                mock.patch.object(bridge.menus, "resolve", resolve), mock.patch.object(bridge.asks, "cancel", cancel), \
                mock.patch.object(ask, "waiting", lambda cid: asking), mock.patch.object(peers, "directory", lambda cs: {"st-1": 5}), \
                mock.patch.object(peers, "who", lambda name, cs: "#" + str(name)):
            for ev in events:
                ev = dict(ev, channel=str(ch.id))
                st["own_panel"] = "the bot's"
                del did[:]
                before = len(ch.sent)
                await bridge.handle(ev)
                await real_sleep(0)
                shown = [m.content if m.embed is None else "embed: %s | %s" % (m.embed.author.name, m.embed.description) for m in ch.sent[before:]]
                rows.append((shown, list(did), "own_panel" in st, rt.turn.prompt if rt.turn else None))
        return rows, st, rt, bridge
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        rows, st, rt, bridge = asyncio.run(go())
    return rows, st, rt, bridge, printed.getvalue()


class WhoseWords(unittest.TestCase):
    def test_what_the_bot_typed_in_is_matched_before_the_connector_is_asked(self):
        """A message sent from Discord may begin like a notice of Claude Code's. The connector, which goes by how a
        prompt begins, would say it is no one's typing; the bot knows better, having typed it: it is asked first.
        The owner's message gets its 👀 and is not echoed as typed at the terminal (#code-review, manager)."""
        for text in ("整理一下 notes", "[TODO] 明天再說", "<div> 這段 html 是什麼", "[Cross-session idle notice] 這句是我打的", "<task-notification> 是什麼"):
            mine = Typed(text)
            self.assertNotEqual(hooks.normalize(P(text))["dc_from"], "peer")
            rows, st, rt, _, _ = play([P(text)], typed=[mine])
            self.assertEqual((rows[0][0], mine.reactions, mine.got.is_set(), rt.pending), ([], ["👀"], True, []), text)
            self.assertTrue(st["busy"])

    def test_typed_at_the_terminal(self):
        for text in ("手動打的一句", "/model opus"):
            self.assertEqual(play([P(text)])[0][0][0], [TERMINAL + text])
        for text in ("[TODO] 在終端機打的", "<div> x", "[Cross-session idle notice] st-1 is idle", "", "   "):
            self.assertEqual(play([P(text)])[0][0][0], [], "not echoed: it begins like something Claude Code says to itself (as before)")
        self.assertEqual(play([P("/compact keep the plan")], keyed=["/compact keep the plan"])[0][0][0], [], "nor what the bot itself keyed in")

    def test_a_message_from_another_conversation_is_shown_as_one(self):
        named = P('<cross-session-message from-name="st-1">\n done with it \n</cross-session-message>')
        unnamed = P("<cross-session-message>no name</cross-session-message>")
        rows = play([named, unnamed])[0]
        self.assertEqual([r[0] for r in rows], [["embed: 📨 來自 #st-1 | done with it"], ["embed: 📨 來自 #另一個對話 | no name"]])

    def test_a_finished_background_task_names_the_turn_it_begins(self):
        note = P("<task-notification>\n<task-id>t</task-id>\n<summary>done</summary>\n</task-notification>")
        rows = play([note])[0]
        self.assertEqual((rows[0][0], rows[0][3]), ([], "（背景工作完成）"))
        self.assertEqual(play([P("第一行\n第二行")])[0][0][3], "第一行")


class ToolsAndEnds(unittest.TestCase):
    def test_a_message_sent_to_another_conversation(self):
        send = lambda **k: dict({"hook_event_name": "PreToolUse", "tool_name": "SendMessage", "session_id": "s1"}, **k)  # noqa: E731
        rows, st, rt, _, _ = play([P("go"), send(send_to="st-1", send_text="report"), send(send_to="st-1", send_text="  "),
                                   send(send_to="stranger", send_text="hi"), send()])
        self.assertEqual([r[0] for r in rows[1:]], [["📤 **傳給 #st-1**\n> report"], [], [], []],
                         "only words, and only to a conversation that has a channel")
        self.assertEqual(rt.turn.tools, {"SendMessage": 4}, "each still counts as a tool call")

    def test_the_end_and_the_failures(self):
        stop = {"hook_event_name": "Stop", "last_assistant_message": "done.", "session_id": "s1", "background_tasks": [{"id": "b"}], "session_crons": [{}]}
        rows, st, rt, bridge, _ = play([P("go"), stop])
        self.assertTrue(rows[1][0][0].startswith("done."))
        self.assertEqual((rows[1][1], st["bg"], st["busy"]), (["cancel", "resolve"], 2, False))
        for error, words in (("rate_limit", "用量達到上限"), (None, "API 錯誤"), ("a_new_code", "API 錯誤（a_new_code）")):
            ev = {"hook_event_name": "StopFailure", "session_id": "s1"}
            if error:
                ev["error"] = error
            rows, st, rt, bridge, _ = play([P("go"), ev])
            self.assertTrue(rows[1][0][0].startswith("⚠️ **這一輪中斷了：%s**" % words), rows[1][0])
            self.assertEqual((rows[1][1], rows[1][2]), (["cancel"], True), "a failure cancels a question; the buttons of a dialog stay")
            self.assertIn("失敗（%s）" % words, bridge.host.audits[-1])

    def test_a_dialog_is_shown_an_idle_notice_is_not_and_a_question_waiting_comes_first(self):
        dialog = {"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": "Claude needs your permission", "session_id": "s1"}
        idle = {"hook_event_name": "Notification", "notification_type": "idle_prompt", "message": "x", "session_id": "s1"}
        words = {"hook_event_name": "Notification", "message": "Claude is waiting for your input", "session_id": "s1"}
        self.assertEqual([r[1] for r in play([dialog, idle, words])[0]], [["menu"], [], []])
        self.assertEqual([r[1] for r in play([dialog], asking=("q",))[0]], [[]])


class Lists(unittest.TestCase):
    """The two lists that were hook names: with every measured event, and each of them as if it came from inside a
    subagent, the kinds say what the names said."""

    def events(self):
        for path in sorted(glob.glob(os.path.join(SAMPLES, "hook_payloads", "*", "*.json")) + glob.glob(os.path.join(SAMPLES, "hook_payloads", "sessions", "*", "*.json"))):
            with open(path, encoding="utf-8") as f:
                got = json.load(f)
            for ev in got if isinstance(got, list) else [got]:
                yield ev
                yield dict(ev, agent_id="a1")

    def test_a_turn_moved_and_the_session_moved_on(self):
        seen = 0
        for ev in self.events():
            name, kind = ev["hook_event_name"], hooks.normalize(dict(ev))["dc_kind"]
            self.assertEqual(kind in tmux_bridge.TURN_MOVED, name in ("UserPromptSubmit", "PreToolUse", "Stop", "SessionStart", "SessionEnd"),
                             (name, kind, bool(ev.get("agent_id"))))
            if not (ev.get("agent_id") and name == "PreToolUse"):  # (a subagent's tool call never gets as far as the second list)
                self.assertEqual(kind in tmux_bridge.MOVED_ON, name in ("UserPromptSubmit", "PreToolUse", "Stop", "SessionEnd"), (name, kind))
            seen += 1
        self.assertGreater(seen, 600)

    def test_the_bots_dialog_is_the_turns_once_the_turn_moves(self):
        tool = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s1"}
        inside = dict(tool, agent_id="a9")
        idle = {"hook_event_name": "Notification", "notification_type": "idle_prompt", "session_id": "s1"}
        rows = play([P("go"), tool, inside, idle, {"hook_event_name": "StopFailure", "session_id": "s1"}])[0]
        self.assertEqual([r[2] for r in rows], [False, False, False, True, True])


class BearingASubagentsId(unittest.TestCase):
    """What comes from inside a subagent, played from the measured runs (the connector's hook_payloads/agents/; only
    the events the bot's hooks would bring). #code-review, b795b9a: as "agent_other" an event that bears a subagent's
    id and is not one of a subagent's own went into no branch."""

    def run_of(self, version, name):
        with open(os.path.join(SAMPLES, "hook_payloads", "agents", version, name + ".json"), encoding="utf-8") as f:
            return [ev for ev in json.load(f) if ev["hook_event_name"] in hooks.EVENTS + ("PostToolUse",)]

    def test_a_permission_dialog_asked_from_inside_a_subagent_is_shown_with_its_buttons(self):
        """Its Notification bears no subagent's id (measured): it was never at risk; held here all the same."""
        for version in ("2.1.296", "2.1.288"):
            for name, dialogs in (("subperm", 1), ("wfperm", 2)):
                events = self.run_of(version, name)
                rows, st, rt, bridge, printed = play(events)
                menus = [r[1] for r, ev in zip(rows, events) if ev["hook_event_name"] == "Notification"]
                self.assertEqual(menus, [["menu"]] * dialogs, (version, name))
                self.assertEqual(printed, "")

    def test_a_subagents_failed_request_is_said_in_its_thread_and_not_as_the_turn_cut_short(self):
        """The one event that does bear a subagent's id outside a subagent's own four (measured: a StopFailure, and no
        SubagentStop after it). Until step 2.2e it was posted in the channel as the turn's failure with its retry
        button, though the main conversation went on (#issues 30). Now: said in the subagent's thread, which ends;
        nothing in the channel, open questions stay, the dialog on the screen stays the bot's."""
        for version in ("2.1.296", "2.1.288"):
            events = self.run_of(version, "subfail")
            rows, st, rt, bridge, printed = play(events)
            at = next(i for i, ev in enumerate(events) if ev["hook_event_name"] == "StopFailure")
            self.assertTrue(events[at].get("agent_id"))
            everything = "\n".join(text for row in rows for text in row[0])
            self.assertNotIn("這一輪中斷了", everything)
            self.assertEqual(rows[at][0], ["**失敗**（0 秒）\n⚠️ API 錯誤\n> " + events[at]["last_assistant_message"]], "in the thread")
            self.assertEqual(rows[at][1:3], ([], True), "no question cancelled, no menu resolved, the bot's own panel kept")
            self.assertEqual((st.get("subs"), st.get("rooms")), ({}, {}), "the thread is over")
            self.assertEqual([ev["hook_event_name"] for ev in events[at + 1:]], ["UserPromptSubmit", "Stop"])
            self.assertTrue(rows[-1][0][-1].startswith(events[-1]["last_assistant_message"]),
                            "the main conversation's own next turn (the background task is done) is posted as ever")
            self.assertEqual(len(rows[-1][0]), 1, "and nothing is left to close with it")
            self.assertEqual(printed, "")
            self.assertTrue(any("子代理" in a and "失敗（API 錯誤）" in a for a in bridge.host.audits), bridge.host.audits)

    def test_a_failed_request_of_the_conversation_itself_is_the_turn_cut_short_as_ever(self):
        rows, st, rt, bridge, printed = play([P("typed"), {"hook_event_name": "StopFailure", "error": "rate_limit", "session_id": "s1"}])
        self.assertTrue(rows[1][0][-1].startswith("⚠️ **這一輪中斷了：用量達到上限**"), rows[1][0])
        self.assertEqual(rows[1][1], ["cancel"])

    def test_any_other_event_would_be_handled_as_without_the_id(self):
        """None of these has been seen to bear a subagent's id (measured: no UserPromptSubmit, Stop, Notification,
        SessionStart or SessionEnd does). Should a version send one, it is handled as the same event without it."""
        events = [P("typed"), {"hook_event_name": "Stop", "last_assistant_message": "done.", "session_id": "s1"}, P("again"),
                  {"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": "Claude needs your permission", "session_id": "s1"},
                  {"hook_event_name": "Notification", "notification_type": "idle_prompt", "session_id": "s1"},
                  {"hook_event_name": "SessionStart", "source": "clear", "session_id": "s2"}, {"hook_event_name": "SessionEnd", "reason": "other", "session_id": "s2"}]
        plain = play(events)
        borne = play([dict(ev, agent_id="a1") for ev in events])
        self.assertEqual(borne[0], plain[0])
        self.assertEqual((borne[1].get("session_id"), borne[4]), (plain[1].get("session_id"), ""))


class WhateverAnotherMachineSends(unittest.TestCase):
    def test_a_kind_that_is_no_text_does_not_stop_the_event(self):
        """#code-review (ce36d80): the line "came as kind X" kept its pairs in a set; a list or a dict for a kind,
        which only a dcremote gone wrong would send, raised there and the event went the way of a failed one."""
        for odd in (["a", "list"], {"a": "dict"}, 5, 0.5, True, None, ""):
            ev = {"hook_event_name": "Stop", "last_assistant_message": "still posted", "session_id": "s1", "host": "lap", "dc_kind": odd, "dc_why": odd}
            rows, st, rt, bridge, printed = play([P("go", host="lap"), ev])
            self.assertTrue(rows[1][0][0].startswith("still posted"), repr(odd))
            self.assertEqual(printed, "", "and nothing to say about it: it brought no kind")
        ev = {"hook_event_name": "Stop", "last_assistant_message": "x", "session_id": "s1", "host": "lap", "dc_kind": "input"}
        self.assertEqual(play([P("go", host="lap"), ev])[4].count("came as kind input and is turn_end here"), 1)


if __name__ == "__main__":
    unittest.main()
