"""handle() goes by the kind of a session's events (stage 3b step 2.2a): a conversation started, replaced, left,
ended, compacted; an event of a kind not known is left alone. Played from the measured runs (the connector's
hook_payloads/sessions/, both versions), for the three kinds of channel a conversation can be behind. What is posted
and when is as it was; the one thing that changed is said in its own test."""
import asyncio
import contextlib
import copy
import io
import json
import os
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import hooks
from altention.tests.connectors.claude_code.test_normalize import SESSIONS, VERSIONS
from bot import history, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_turn_end import Ch, Host

OWN = {"tmux": "dc-x-1"}
WINDOW = {"home": {"socket": "/s", "session": "work", "window": "w"}, "pane": {"socket": "/s", "id": "%1"}}
ATTACHED = {"pane": {"socket": "/s", "id": "%1"}}
ENDED_WINDOW, ENDED_PANE = "ℹ️ 這個頻道的 Claude 結束了", "ℹ️ 這個對話在終端機那邊結束了"


def load(version, name):
    with open(os.path.join(SESSIONS, version, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def play(events, shape, host=None):
    """[(the event's kind, what was posted with it, the channel's session id after it)], the ids whose history was
    shown, whether the session was said to be there, and what was printed."""
    async def go():
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        bridge.last_reap = 1e12
        st = bridge.host.chan(ch)
        st.update(copy.deepcopy(shape))
        st["cwd"] = "/w/proj"
        shown, rows = [], []

        async def show(the_host, c, s, sid, path=None):
            shown.append(sid)

        async def show_remote(the_host, c, s, far, sid, path=None):
            shown.append((far, sid))
        with mock.patch.object(history, "show", show), mock.patch.object(history, "show_remote", show_remote), \
                mock.patch.object(bridge, "watch", lambda *a, **k: None):
            for ev in events:
                ev = dict(copy.deepcopy(ev), channel=str(ch.id))
                if host:
                    ev["host"] = host
                before = len(ch.sent)
                await bridge.handle(ev)
                await asyncio.sleep(0)
                rows.append((ev.get("dc_kind"), [m.content for m in ch.sent[before:]], st.get("session_id")))
        return rows, shown, bridge.rt(ch).ready.is_set(), st
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        rows, shown, ready, st = asyncio.run(go())
    return rows, shown, ready, st, printed.getvalue()


def said(rows, kind):
    return [text for k, texts, _ in rows if k == kind for text in texts]


class Sessions(unittest.TestCase):
    def each(self, name):
        for version in VERSIONS:
            yield version, load(version, name)

    def test_clear_replaces_the_conversation_and_says_nothing_of_an_end(self):
        for version, events in self.each("clear"):
            for shape in (OWN, WINDOW, ATTACHED):
                rows, shown, ready, st, _ = play(events, shape)
                self.assertEqual([k for k, _, _ in rows], ["ready", "input", "turn_end", "session_leaving", "session_changed", "input", "turn_end"])
                self.assertEqual(said(rows, "session_leaving") + said(rows, "session_changed"), [], (version, shape))
                old, new = events[0]["session_id"], events[4]["session_id"]
                self.assertEqual([sid for _, _, sid in rows], [old, old, old, old, new, new, new], "the id changes with the start, not with the end")
                self.assertEqual((shown, ready), ([], True))
                self.assertIn(old, json.dumps(st), "the one left can be taken up again")

    def test_an_end_is_said_where_the_owner_has_a_window_or_a_pane(self):
        for name in ("exit", "ctrld", "killpane", "killwin", "server", "term", "hup"):
            for version, events in self.each(name):
                self.assertEqual(said(play(events, OWN)[0], "session_ended"), [], "the bot's own session: reopened without a word")
                self.assertTrue(said(play(events, WINDOW)[0], "session_ended")[0].startswith(ENDED_WINDOW), (version, name))
                self.assertTrue(said(play(events, ATTACHED)[0], "session_ended")[0].startswith(ENDED_PANE), (version, name))
        for version, events in self.each("kill9"):
            rows = play(events, WINDOW)[0]
            self.assertEqual([k for k, _, _ in rows], ["ready", "input", "turn_end"], "killed: no event, nothing said (found when it is next looked for)")

    def test_leaving_a_conversation_for_another_is_not_said_as_an_end(self):
        """#issues 20 (step 2.3): /resume in a window the bot opened made the channel say "Claude 結束了（tmux 視窗被
        關掉了？）", and in a pane attached "這個對話在終端機那邊結束了". The Claude Code goes on with another
        conversation: nothing is said of an end; the history of the one resumed is shown, as before."""
        for name in ("resume", "resumeid", "resumepick", "resumesame", "clear", "clearbusy"):
            for version, events in self.each(name):
                for shape in (WINDOW, ATTACHED, OWN):
                    rows, shown, ready, st, printed = play(events, shape)
                    leaving = [texts for k, texts, _ in rows if k == "session_leaving"]
                    self.assertEqual([t for texts in leaving for t in texts], [], (version, name))
                    everything = [t for _, texts, _ in rows for t in texts]
                    if not any(k == "session_ended" for k, _, _ in rows):
                        self.assertFalse(any(t.startswith((ENDED_WINDOW, ENDED_PANE)) for t in everything), (version, name, everything))
                    self.assertEqual(printed, "")
        for version, events in self.each("resume"):
            rows, shown, _, _, _ = play(events, WINDOW)
            self.assertEqual(len([k for k, _, _ in rows if k == "session_leaving"]), 2, "the sample leaves twice: by /clear, and to resume")
            self.assertEqual(shown, [events[0]["session_id"]], "the history of the one resumed is shown, once")
            self.assertEqual(rows[-1][2], events[0]["session_id"])

    def test_an_end_is_still_said_after_a_conversation_was_left_for_another(self):
        for version, events in self.each("resume"):
            end = dict(events[-1], hook_event_name="SessionEnd", reason="prompt_input_exit")
            for shape, words in ((WINDOW, ENDED_WINDOW), (ATTACHED, ENDED_PANE)):
                rows = play(events + [end], shape)[0]
                self.assertEqual(rows[-1][0], "session_ended")
                self.assertTrue(rows[-1][1][0].startswith(words), (version, rows[-1]))

    def test_the_history_is_shown_for_a_resume_that_changed_the_conversation_on_this_machine(self):
        for version, events in self.each("resumeid"):
            self.assertEqual(play(events, ATTACHED)[1], [], "/resume <its own id>: the same conversation, nothing shown again")
        for name in ("resumestart", "continuestart", "resumepick"):
            for version, events in self.each(name):
                rows, shown, ready, _, _ = play(events, ATTACHED)
                self.assertEqual((shown, ready), ([], True), "started again with the conversation it had: nothing changed")
        for version, events in self.each("resume"):
            self.assertEqual(play(events, ATTACHED, host="lap")[1], [("lap", events[0]["session_id"])],
                             "another machine's file is read over there (step 2.4c, #issues 31; until then nothing was shown)")

    def test_compacting_is_neither_an_end_nor_a_change(self):
        for name in ("compact", "autocompact"):
            for version, events in self.each(name):
                rows, shown, ready, _, _ = play(events, WINDOW)
                self.assertEqual(len({sid for _, _, sid in rows}), 1)
                self.assertEqual([t for _, texts, _ in rows for t in texts if t.startswith("ℹ️")], [])
                self.assertEqual((shown, ready), ([], True))

    def test_a_reset_by_the_bot_ends_one_and_starts_another(self):
        for version, events in self.each("reset"):
            rows = play(events, OWN)[0]
            self.assertEqual([k for k, _, _ in rows], ["ready", "input", "turn_end", "session_ended", "ready", "input", "turn_end"])
            self.assertNotEqual(rows[4][2], rows[3][2])


class StartedWithAnAgent(unittest.TestCase):
    """A conversation started with an agent (`claude --agent x`, or "agent" in the settings) bears agent_type on every
    event. It is a conversation like any other. (bb740ac took a SessionStart with agent_type for something not known and
    left it alone: a conversation the bot opened on a machine whose settings name an agent was then never seen to
    start, and each message ended, 90 s later, in "Claude 沒有啟動成功": #code-review, #code-review-2.)"""

    def test_the_bots_own_conversation_is_seen_to_start_and_its_turns_are_posted(self):
        for version in VERSIONS:
            for name in ("agentset", "agent"):
                events = load(version, name)
                rows, shown, ready, st, printed = play(events[:1], OWN)
                self.assertEqual((rows[0][0], ready, rows[0][2], printed), ("ready", True, events[0]["session_id"], ""), (version, name))
                rows, shown, ready, st, printed = play(events, OWN)
                replies = [t for k, texts, _ in rows if k == "turn_end" for t in texts]
                self.assertEqual(len(replies), sum(1 for ev in events if ev["hook_event_name"] == "Stop"), "every turn's reply is posted")
                self.assertGreaterEqual(len(replies), 3)
                self.assertEqual((printed, said(rows, "unknown")), ("", []))

    def test_clear_and_resume_follow_as_for_any_conversation(self):
        for version in VERSIONS:
            for name in ("agent", "agentset"):
                events = load(version, name)
                plain = [{k: v for k, v in ev.items() if k != "agent_type"} for ev in events]
                for shape in (OWN, WINDOW, ATTACHED):
                    with_it, without = play(events, shape), play(plain, shape)
                    self.assertEqual(with_it[:3], without[:3], (version, name))
                rows, shown, ready, st, _ = play(events, ATTACHED)
                self.assertEqual([sid for k, _, sid in rows if k == "session_changed"], [ev["session_id"] for ev in events if ev["hook_event_name"] == "SessionStart"][1:])
                self.assertEqual(shown, [events[0]["session_id"]], "the one resumed within the conversation: its history, once")


class FromAMachineAVersionBehind(unittest.TestCase):
    """The kind that counts is the bot's own (manager, 2026-10-13). Another machine's dcremote may be a version
    behind: the one of step 2.1 marked a SessionStart that bears agent_type, and one with a source it had not seen,
    as not known, and sends them so. A bot that took that as it came left them alone: after /clear or /resume in
    a pane of that machine whose conversation was started with an agent, the channel's conversation id did not
    follow (#code-review-2, on the real thing)."""

    def as_step_2_1_sent_it(self, ev):
        ev = dict(ev)
        if ev["hook_event_name"] == "SessionStart" and ev.get("agent_type"):
            ev.update(dc_kind="unknown", dc_why="session_start_of_an_agent")
        else:
            hooks.normalize(ev)
        return ev

    def test_the_conversation_id_follows_and_nothing_is_written_about_it(self):
        for version in VERSIONS:
            for name in ("agent", "agentset"):
                events = load(version, name)
                came = [self.as_step_2_1_sent_it(ev) for ev in events]
                self.assertGreaterEqual(sum(ev.get("dc_kind") == "unknown" for ev in came), 4, "the old relay does mark them")
                rows, shown, ready, st, printed = play(came, ATTACHED, host="lap")
                fresh = play(events, ATTACHED, host="lap")
                self.assertEqual((rows, shown, ready), fresh[:3], (version, name))
                self.assertEqual([sid for k, _, sid in rows if k == "session_changed"], [ev["session_id"] for ev in events if ev["hook_event_name"] == "SessionStart"][1:])
                lines = printed.splitlines()
                self.assertEqual(sorted(l.split(" (said once)")[0] for l in lines),
                                 ["bridge: an event came as kind unknown and is ready here", "bridge: an event came as kind unknown and is session_changed here"],
                                 "that the two sides made it out differently is said, once for each pair, not for each event")
                self.assertNotIn("left alone", printed)

    def test_a_source_the_old_relay_did_not_know(self):
        ev = {"hook_event_name": "SessionStart", "source": "teleport", "session_id": "new-one", "dc_kind": "unknown", "dc_why": "session_start_teleport"}
        rows, shown, ready, st, printed = play([ev], dict(ATTACHED, session_id="old-one"), host="lap")
        self.assertEqual((rows[0][0], st["session_id"], ready), ("session_changed", "new-one", True))
        self.assertEqual(printed.count("came as kind unknown and is session_changed here"), 1)

    def test_whatever_kind_an_event_came_with_this_versions_is_taken(self):
        """Also one that is not "not known": the two sides can only differ when one of them is wrong, and the one to
        go by is the version that is handling the event. Said once for each pair."""
        ev = {"hook_event_name": "SessionEnd", "reason": "prompt_input_exit", "session_id": "old-one", "dc_kind": "session_leaving", "dc_why": "clear"}
        rows, shown, ready, st, printed = play([ev, dict(ev), dict(ev)], dict(ATTACHED, session_id="old-one"), host="lap")
        self.assertEqual([k for k, _, _ in rows], ["session_ended"] * 3)
        self.assertTrue(rows[0][1][0].startswith(ENDED_PANE), "handled as the end it is (as a leaving by /clear nothing would have been said)")
        self.assertEqual(printed.count("came as kind session_leaving and is session_ended here"), 1)
        same = {"hook_event_name": "Stop", "session_id": "old-one", "dc_kind": "turn_end"}
        self.assertEqual(play([same], dict(ATTACHED, session_id="old-one"), host="lap")[4], "", "the same on both sides: nothing to say")

    def test_what_this_version_does_not_know_either_is_still_left_alone(self):
        ev = {"hook_event_name": "SomethingNew", "session_id": "zzz", "dc_kind": "unknown"}
        rows, shown, ready, st, printed = play([ev, dict(ev)], dict(ATTACHED, session_id="old-one"), host="lap")
        self.assertEqual(([k for k, _, _ in rows], st["session_id"], printed.count("left alone")), (["unknown", "unknown"], "old-one", 1))
        newer = {"hook_event_name": "SomethingNew", "session_id": "zzz", "dc_kind": "a_kind_of_a_newer_version"}
        rows, shown, ready, st, printed = play([newer, dict(newer)], dict(ATTACHED, session_id="old-one"), host="lap")
        self.assertEqual([k for k, _, _ in rows], ["unknown", "unknown"], "a newer machine's kind this version cannot act on either")
        self.assertEqual(printed.count("it came as kind a_kind_of_a_newer_version"), 1, "but the name it came with is in the line")


class NotKnown(unittest.TestCase):
    def test_a_kind_not_known_is_left_alone_and_said_once(self):
        """Only a hook this version has no name for. Nothing is done with it; a line is written the first time each
        sort comes, not for every event."""
        odd = {"hook_event_name": "SomethingNew", "session_id": "zzz"}
        rows, shown, ready, st, printed = play([odd, dict(odd), dict(odd), {"hook_event_name": "AnotherNew"}, dict(odd)], OWN)
        self.assertEqual((rows, shown, ready, st.get("session_id")), ([("unknown", [], None)] * 5, [], False, None))
        self.assertEqual((printed.count("left alone"), printed.count("SomethingNew"), printed.count("AnotherNew")), (2, 1, 1), printed)

    def test_a_start_with_a_source_not_seen_before_is_still_a_session_that_is_there(self):
        """Whoever waits for a Claude Code to say it has started must not wait in vain under a version that words
        the start otherwise: the id is taken, the session is there; no history is shown (that is for a resume)."""
        ev = {"hook_event_name": "SessionStart", "source": "teleport", "session_id": "new-one", "transcript_path": "/w/x.jsonl"}
        rows, shown, ready, st, printed = play([ev], dict(OWN, session_id="old-one"))
        self.assertEqual((rows[0][0], hooks.normalize(dict(ev))["dc_why"]), ("session_changed", "teleport"))
        self.assertEqual((st["session_id"], shown, ready, printed), ("new-one", [], True, ""))


if __name__ == "__main__":
    unittest.main()
