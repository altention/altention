"""What kind of thing a hook event is (hooks.normalize, stage 3b step 2.1), held against real payloads: the 22
measured cases of a conversation ending or being replaced (hook_payloads/sessions/, both versions) and the samples
of an ordinary turn (hook_payloads/2.1.296/)."""
import copy
import json
import os
import random
import re
import time
import unittest

from altention.connectors.claude_code import hooks

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS = os.path.join(HERE, "hook_payloads", "sessions")
VERSIONS = ("2.1.296", "2.1.288")
A, T, E = ("ready", None), ("input", None), ("turn_end", None)   # a conversation started, something said, the turn over
EXPECTED = {
    "clear": [A, T, E, ("session_leaving", "clear"), ("session_changed", "clear"), T, E],
    "clearbusy": [A, T, E, T, E, ("session_leaving", "clear"), ("session_changed", "clear")],
    "resume": [A, T, E, ("session_leaving", "clear"), ("session_changed", "clear"), T, E,
               ("session_leaving", "resume"), ("session_changed", "resume"), T, E],
    "resumeid": [A, T, E, ("session_leaving", "resume"), ("session_changed", "resume"), T, E],
    "resumesame": [A, T, E],
    "exit": [A, T, E, ("session_ended", "exited")], "ctrld": [A, T, E, ("session_ended", "exited")], "ctrlc": [A, T, E, ("session_ended", "exited")],
    "killpane": [A, T, E, ("session_ended", "closed")], "killwin": [A, T, E, ("session_ended", "closed")],
    "killsession": [A, T, E, ("session_ended", "closed")], "server": [A, T, E, ("session_ended", "closed")],
    "term": [A, T, E, ("session_ended", "closed")], "hup": [A, T, E, ("session_ended", "closed")],
    "kill9": [A, T, E],
    "resumestart": [A, T, E, ("session_ended", "exited"), ("session_changed", "resume"), T, E],
    "continuestart": [A, T, E, ("session_ended", "exited"), ("session_changed", "resume"), T, E],
    "resumepick": [A, T, E, ("session_ended", "exited"), ("session_changed", "resume"), T, E],
    "reset": [A, T, E, ("session_ended", "closed"), A, T, E],
    "compact": [A, T, E, ("compacting", None), ("agent_stop", None), ("compacted", None), ("compact_done", None), T, E],
    "autocompact": [A, T, E, T, E, T, ("compacting", None), ("agent_stop", None), ("compacted", None), ("compact_done", None), E],
    "bg": [A, T, E, ("session_ended", "exited"), A, T, E, T, E, A, ("session_ended", "closed")],
    # a conversation started with an agent (`claude --agent x`; "agent" in the settings): every event bears agent_type
    "agent": [A, T, E, T, ("progress", None), ("agent_launched", None), E, ("session_leaving", "clear"), ("session_changed", "clear"), T, E,
              ("session_leaving", "resume"), ("session_changed", "resume"), T, E, ("session_ended", "exited"), ("session_changed", "resume"), T, E,
              ("session_ended", "exited"), ("session_changed", "resume"), T, E],
}
EXPECTED["agentset"] = EXPECTED["agent"]
# The left key on an empty input box (Claude Code's agents list): the pane's conversation goes on in a background
# process under a new id, which starts with source "fork". Other background processes start beside it, in an order
# that differs from run to run, and typing into the list opens conversations of their own: these samples are read by
# the ids of the pane's conversation and the one it moved to (moved()), which is what the bot's hook lets through.
F, P, C = ("session_changed", "fork"), ("progress", None), ("session_ended", "closed")
LEFT = {
    "leftbusy": [A, T, E, T, P, F, E, T, E, C, C],                        # moved while a tool ran: its turn ends under the new id
    "leftesc": [A, T, E, F, T, P, ("agent_launched", None), E, T, E, C, C],  # Escape in the list: back in the conversation moved
    "leftnew": [A, T, E, F, C, C],                                        # typed into the list: another conversation, no pane's
    # started as the bot starts its own (hooks by --settings, the channel by DCBOT_CHANNEL): moved, a turn in it, then
    # a conversation typed into the list with two turns; every background process's hooks have the same variable
    "leftown": [A, T, E, F, T, P, ("agent_launched", None), E, C, C],
}


def moved(version, name):
    """A left-key sample's events of the pane's conversation and of the one it was moved to, and the two ids. (A
    SubagentStop with an empty agent_type comes after some of the turn ends there and not after others: left out.)"""
    events = case(version, name)
    old, new = events[0]["session_id"], next(ev["session_id"] for ev in events if ev.get("source") == "fork")
    return [ev for ev in events if ev["session_id"] in (old, new) and ev["dc_kind"] != hooks.AGENT_STOP], old, new


def case(version, name):
    with open(os.path.join(SESSIONS, version, name + ".json"), encoding="utf-8") as f:
        return [hooks.normalize(ev) for ev in json.load(f)]


def kinds(events):
    return [(ev["dc_kind"], ev.get("dc_why")) for ev in events]


class Measured(unittest.TestCase):
    def test_every_case_of_both_versions(self):
        for version in VERSIONS:
            self.assertEqual(sorted(n[:-5] for n in os.listdir(os.path.join(SESSIONS, version))), sorted(list(EXPECTED) + list(LEFT)), version)
            for name, expected in EXPECTED.items():
                self.assertEqual(kinds(case(version, name)), expected, (version, name))

    def test_a_conversation_moved_to_the_background_goes_on_under_a_new_id(self):
        for version in VERSIONS:
            for name, expected in LEFT.items():
                events, old, new = moved(version, name)
                self.assertEqual(kinds(events), expected, (version, name))
                others = [ev for ev in case(version, name) if ev["session_id"] not in (old, new)]
                self.assertTrue(others, "and beside it background processes that are no pane's conversation")
                self.assertTrue(all(ev.get("source") != "fork" for ev in others), (version, name))
                start = next(ev for ev in events if ev.get("source") == "fork")
                self.assertTrue(start["transcript_path"].endswith(new + ".jsonl"))
                self.assertNotIn(old, json.dumps(start), "nothing in it says where the conversation came from")
                after = events[events.index(start) + 1:]
                self.assertEqual([ev["session_id"] for ev in after[:-1]], [new] * (len(after) - 1), (version, name))
                self.assertEqual(after[-1]["session_id"], old, "the pane's own process says only that it ended, and last")
            busy, old, new = moved(version, "leftbusy")
            self.assertEqual([ev["session_id"] for ev in busy[3:7]], [old, old, new, new], "asked and begun under the old id, ended under the new")
            self.assertEqual(busy[3]["prompt_id"], busy[4]["prompt_id"])
            self.assertIsNone(busy[6].get("prompt_id"), "the end of the turn that was moved bears no prompt_id")

    def test_leaving_is_followed_at_once_by_the_change_and_nothing_ended(self):
        seen = 0
        for version in VERSIONS:
            for name in EXPECTED:
                events = case(version, name)
                for here, ev in enumerate(events):
                    if ev["dc_kind"] == hooks.SESSION_LEAVING:
                        after = events[here + 1]
                        self.assertEqual((after["dc_kind"], after["dc_why"]), (hooks.SESSION_CHANGED, ev["dc_why"]), (version, name))
                        seen += 1
        self.assertGreaterEqual(seen, 8)

    def test_the_end_names_the_old_conversation_and_the_start_the_new_one(self):
        for version in VERSIONS:
            events = case(version, "clear")
            old, new = events[3], events[4]
            self.assertEqual(old["session_id"], events[0]["session_id"])
            self.assertNotEqual(new["session_id"], old["session_id"])
            self.assertTrue(new["transcript_path"].endswith(new["session_id"] + ".jsonl"))
            self.assertEqual(events[5]["session_id"], new["session_id"], "what is said next is said to the new one")
            same = case(version, "resumeid")
            self.assertEqual(same[3]["session_id"], same[4]["session_id"], "/resume <its own id>: a change that changes nothing")
            back = case(version, "resume")
            self.assertEqual(back[8]["session_id"], back[0]["session_id"], "resumed: the first conversation again")

    def test_compacting_ends_nothing_and_keeps_the_conversation(self):
        for version in VERSIONS:
            for name in ("compact", "autocompact"):
                events = case(version, name)
                self.assertFalse([ev for ev in events if ev["dc_kind"] in (hooks.SESSION_ENDED, hooks.SESSION_LEAVING, hooks.SESSION_CHANGED)])
                self.assertEqual(len({ev["session_id"] for ev in events}), 1, (version, name))
                self.assertEqual(len({ev["transcript_path"] for ev in events}), 1, (version, name))

    def test_agent_type_says_nothing_about_the_kind(self):
        """A conversation started with an agent bears agent_type on every event and is a conversation like any other
        (stage 3b step 2.2a: a rule that took a SessionStart with agent_type for something else, from the one
        sample there was, left the bot waiting for such a conversation to start). Every sample there is, with the
        field taken off, with it put on: the same kind."""
        seen = with_it = 0
        for version in VERSIONS:
            for name in EXPECTED:
                with open(os.path.join(SESSIONS, version, name + ".json"), encoding="utf-8") as f:
                    for ev in json.load(f):
                        bare = {k: v for k, v in ev.items() if k != "agent_type"}
                        answers = [kinds([hooks.normalize(dict(e))])[0] for e in (ev, bare, dict(bare, agent_type="rigagent"), dict(bare, agent_type="claude"))]
                        self.assertEqual(len(set(answers)), 1, (version, name, ev["hook_event_name"], answers))
                        seen += 1
                        with_it += "agent_type" in ev
        self.assertGreater(with_it, 80)
        self.assertGreater(seen - with_it, 200)
        for version in VERSIONS:
            for name in ("agent", "agentset"):
                starts = [ev for ev in case(version, name) if ev["hook_event_name"] == "SessionStart"]
                self.assertEqual([ev["dc_kind"] for ev in starts], ["ready", "session_changed", "session_changed", "session_changed", "session_changed"])
                self.assertGreaterEqual(sum("agent_type" in ev for ev in starts), 4)

    def test_the_start_seen_after_exit_in_a_viewer_is_a_start_like_any_other_here(self):
        """1.6 s after /exit in a viewer of a background conversation a SessionStart comes with a new id (and
        agent_type "claude"). To this function it is a start. That it is not the conversation of the pane is not told
        from the event: the hook that would report it has no pane and its id is in no route, so it reaches no
        channel (the bot's tests/test_routes.py holds that, with this very event)."""
        for version in VERSIONS:
            odd = [ev for ev in case(version, "bg") if ev["hook_event_name"] == "SessionStart" and ev.get("agent_type")]
            self.assertEqual([(ev["dc_kind"], ev["source"], ev["agent_type"]) for ev in odd], [("ready", "startup", "claude")])

    def test_a_killed_process_says_nothing(self):
        for version in VERSIONS:
            self.assertNotIn(hooks.SESSION_ENDED, [k for k, _ in kinds(case(version, "kill9"))])

    def test_nothing_added_bears_a_name_claude_code_uses(self):
        theirs = set()
        for version in VERSIONS:
            for name in EXPECTED:
                with open(os.path.join(SESSIONS, version, name + ".json"), encoding="utf-8") as f:
                    for ev in json.load(f):
                        theirs |= set(ev)
        self.assertEqual(sorted(theirs & set(hooks.NORMALIZED + hooks.ADDED)), [])
        self.assertTrue(all(n.startswith("dc_") for n in hooks.NORMALIZED))


AGENTS = os.path.join(HERE, "hook_payloads", "agents")
AGENT_CASES = ("subperm", "wfperm", "subfail", "subkinds")


def agent_case(version, name):
    with open(os.path.join(AGENTS, version, name + ".json"), encoding="utf-8") as f:
        return [hooks.normalize(ev) for ev in json.load(f)]


class FromInsideSubagents(unittest.TestCase):
    """What bears a subagent's id, measured (hook_payloads/agents/): the rules of normalize() rest on these, where
    until then they rested on no sample at all (stage 3b step 2.2b)."""

    def test_which_events_bear_a_subagents_id(self):
        for version in VERSIONS:
            self.assertEqual(sorted(n[:-5] for n in os.listdir(os.path.join(AGENTS, version))), sorted(AGENT_CASES))
            bearing, plain = set(), set()
            for name in AGENT_CASES:
                for ev in agent_case(version, name):
                    (bearing if ev.get("agent_id") else plain).add(ev["hook_event_name"])
            self.assertEqual(bearing, {"SubagentStart", "SubagentStop", "PreToolUse", "PostToolUse", "PermissionRequest", "StopFailure"}, version)
            self.assertEqual(plain & {"UserPromptSubmit", "Stop", "SessionStart", "SessionEnd", "Notification"},
                             {"UserPromptSubmit", "Stop", "SessionStart", "SessionEnd", "Notification"}, "these come, and never with one")

    def test_a_permission_dialog_asked_from_inside_a_subagent_is_a_dialog_like_any_other(self):
        for version in VERSIONS:
            for name, dialogs in (("subperm", 1), ("wfperm", 2)):
                notices = [ev for ev in agent_case(version, name) if ev["hook_event_name"] == "Notification"]
                self.assertEqual([(ev["dc_kind"], ev["notification_type"], "agent_id" in ev, "dc_agent" in ev) for ev in notices],
                                 [("dialog", "permission_prompt", False, False)] * dialogs, (version, name))
                asked = [ev for ev in agent_case(version, name) if ev["hook_event_name"] == "PermissionRequest"]
                self.assertEqual(sum(bool(ev.get("agent_id")) for ev in asked), 1, "the request itself does say which subagent (not hooked by the bot)")
                self.assertEqual({ev["dc_kind"] for ev in asked}, {"unknown"})

    def test_a_subagents_failed_request_is_a_failure_that_says_whose(self):
        for version in VERSIONS:
            events = agent_case(version, "subfail")
            failed = [ev for ev in events if ev["hook_event_name"] == "StopFailure"]
            self.assertEqual([(ev["dc_kind"], ev["dc_why"], ev["dc_agent"] == ev["agent_id"], ev["agent_type"]) for ev in failed],
                             [("turn_failed", "unknown", True, "general-purpose")])
            self.assertEqual([ev["hook_event_name"] for ev in events if ev["hook_event_name"] == "SubagentStop"], [], "and no stop of that subagent comes")
            self.assertEqual([ev["dc_kind"] for ev in events if ev["hook_event_name"] == "SubagentStart"], ["agent_start"])

    def test_the_subagents_own_kinds(self):
        for version in VERSIONS:
            for name in AGENT_CASES:
                for ev in agent_case(version, name):
                    if ev.get("agent_id") and ev["hook_event_name"] in ("SubagentStart", "SubagentStop", "PreToolUse", "PostToolUse"):
                        self.assertIn(ev["dc_kind"], ("agent_start", "agent_stop", "agent_progress", "agent_launched", "workflow_start"), (version, name))
                        self.assertNotIn("dc_agent", ev)
            kinds_seen = [ev["dc_kind"] for ev in agent_case(version, "subkinds")]
            self.assertEqual((kinds_seen.count("agent_start"), kinds_seen.count("agent_stop"), kinds_seen.count("agent_progress")), (2, 2, 1), version)


class EveryKind(unittest.TestCase):
    def kind(self, **ev):
        out = hooks.normalize(dict(ev))
        return tuple(out.get(k) for k in ("dc_kind", "dc_why", "dc_from") if out.get(k) is not None)

    def test_an_ordinary_turn(self):
        folder = os.path.join(HERE, "hook_payloads", "2.1.296")
        got = []
        for name in sorted(n for n in os.listdir(folder) if n.endswith(".json")):
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                ev = hooks.normalize(json.load(f))
            got.append((name.split("-", 1)[1][:-5], ev["dc_kind"], ev.get("dc_why") or ev.get("dc_from")))
        self.assertEqual(got, [("SessionStart-startup", "ready", None), ("UserPromptSubmit", "input", "typed"), ("PreToolUse", "progress", None),
                               ("PostToolUse", "agent_launched", None), ("Stop", "turn_end", None), ("SessionEnd-clear", "session_leaving", "clear"),
                               ("SessionStart-clear", "session_changed", "clear"), ("SessionEnd-prompt_input_exit", "session_ended", "exited")])

    def test_who_an_input_is_from(self):
        k = lambda prompt: self.kind(hook_event_name="UserPromptSubmit", prompt=prompt)  # noqa: E731
        self.assertEqual(k("整理一下 notes"), ("input", "typed"))
        self.assertEqual(k("  /model opus"), ("input", "typed"))
        self.assertEqual(k("<task-notification>\n<task-id>t</task-id>\n</task-notification>"), ("input", "task"))
        self.assertEqual(k("\n <task-notification>x"), ("input", "task"))
        self.assertEqual(k("<system-reminder>x</system-reminder>"), ("input", "system"))
        self.assertEqual(k("[Cross-session idle notice] st-1 is idle"), ("input", "notice"))
        self.assertEqual(k("[see the file] and tell me"), ("input", "typed"), "brackets of the owner's own")
        self.assertEqual(k(""), ("input", "typed"))
        self.assertEqual(self.kind(hook_event_name="UserPromptSubmit"), ("input", "typed"))
        self.assertEqual(self.kind(hook_event_name="UserPromptSubmit", prompt=["not text"]), ("input", "typed"))
        peer = hooks.normalize({"hook_event_name": "UserPromptSubmit",
                                "prompt": '<cross-session-message from-name="st-1" x="y">\n  done with it\n second line </cross-session-message>\n'})
        self.assertEqual((peer["dc_kind"], peer["dc_from"], peer["dc_peer"], peer["dc_text"]), ("input", "peer", "st-1", "done with it\n second line"))
        unnamed = hooks.normalize({"hook_event_name": "UserPromptSubmit", "prompt": "<cross-session-message>hi</cross-session-message>"})
        self.assertEqual((unnamed["dc_from"], unnamed["dc_peer"], unnamed["dc_text"]), ("peer", "", "hi"), "no name: none, the words for that are the reader's")
        self.assertEqual(k("<cross-session-message>hi</cross-session-message> and more"), ("input", "system"), "not the whole prompt: not one")

    def test_tools_turns_and_dialogs(self):
        self.assertEqual(self.kind(hook_event_name="PreToolUse", tool_name="Bash", hint="ls"), ("progress",))
        self.assertEqual(self.kind(hook_event_name="PreToolUse", tool_name="SendMessage", send_to="st-1", send_text="done"), ("peer_sent",))
        self.assertEqual(self.kind(hook_event_name="PreToolUse", tool_name="SendMessage", send_to="st-1", send_text="  "), ("progress",),
                         "a bare wish to be told when the other is idle: nothing was sent")
        self.assertEqual(self.kind(hook_event_name="Stop", last_assistant_message="ok"), ("turn_end",))
        self.assertEqual(self.kind(hook_event_name="StopFailure", error="rate_limit"), ("turn_failed", "rate_limit"))
        self.assertEqual(self.kind(hook_event_name="StopFailure"), ("turn_failed", "unknown"))
        self.assertEqual(self.kind(hook_event_name="StopFailure", error="a_new_one"), ("turn_failed", "a_new_one"), "open")
        self.assertEqual(self.kind(hook_event_name="Notification", notification_type="idle_prompt", message="x"), ("idle",))
        self.assertEqual(self.kind(hook_event_name="Notification", message="Claude is waiting for your input"), ("idle",))
        self.assertEqual(self.kind(hook_event_name="Notification", notification_type="permission_prompt", message="Claude needs your permission"), ("dialog",))
        self.assertEqual(self.kind(hook_event_name="Ask", ask_id="1-2"), ("ask",))
        self.assertEqual(self.kind(hook_event_name="Plan", ask_id="1-2"), ("plan",))
        self.assertEqual(self.kind(hook_event_name="Ask", ask_id="1-2", agent_id="a1"), ("ask",), "a subagent's question is a question")

    def test_subagents_and_workflows(self):
        self.assertEqual(self.kind(hook_event_name="SubagentStart", agent_id="a1", agent_type="Explore"), ("agent_start",))
        self.assertEqual(self.kind(hook_event_name="SubagentStop", agent_id="a1"), ("agent_stop",))
        self.assertEqual(self.kind(hook_event_name="SubagentStop", agent_type=""), ("agent_stop",), "the helper Claude Code runs for itself")
        self.assertEqual(self.kind(hook_event_name="PreToolUse", tool_name="Read", agent_id="a1"), ("agent_progress",))
        self.assertEqual(self.kind(hook_event_name="PreToolUse", tool_name="SendMessage", send_text="x", agent_id="a1"), ("agent_progress",))
        self.assertEqual(self.kind(hook_event_name="PostToolUse", tool_name="Agent", agent_id="a1"), ("agent_launched",))

    def test_any_other_event_that_bears_a_subagents_id_keeps_its_own_kind(self):
        """Only the four hooks seen to come from a subagent are a subagent's kinds (stage 3b step 2.2b; no sample has
        any other with agent_id). A dialog, a turn's end, something said, with an agent_id: the kind it has without,
        and `dc_agent` says whose id it bears. (A kind of its own for them, as step 2.1 had, would have kept a
        permission dialog asked from inside a subagent off the channel.)"""
        for ev, kind in (({"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": "Claude needs your permission"}, ("dialog",)),
                         ({"hook_event_name": "Notification", "notification_type": "idle_prompt"}, ("idle",)),
                         ({"hook_event_name": "Stop"}, ("turn_end",)), ({"hook_event_name": "StopFailure", "error": "rate_limit"}, ("turn_failed", "rate_limit")),
                         ({"hook_event_name": "UserPromptSubmit", "prompt": "x"}, ("input", "typed")), ({"hook_event_name": "SessionStart", "source": "clear"}, ("session_changed", "clear")),
                         ({"hook_event_name": "SessionEnd", "reason": "other"}, ("session_ended", "closed")), ({"hook_event_name": "SomethingNew"}, ("unknown",))):
            self.assertEqual(self.kind(**ev), kind)
            with_id = hooks.normalize(dict(ev, agent_id="a1"))
            self.assertEqual(tuple(with_id.get(k) for k in ("dc_kind", "dc_why", "dc_from") if with_id.get(k) is not None), kind, ev)
            self.assertEqual((with_id["dc_agent"], "dc_agent" in hooks.normalize(dict(ev))), ("a1", False))
        for own in ({"hook_event_name": "PreToolUse", "tool_name": "Read"}, {"hook_event_name": "SubagentStart"}, {"hook_event_name": "SubagentStop"}):
            self.assertNotIn("dc_agent", hooks.normalize(dict(own, agent_id="a1")), "a subagent's own kinds need no such note")
        self.assertFalse(hasattr(hooks, "AGENT_OTHER"))
        self.assertEqual(self.kind(hook_event_name="PostToolUse", tool_name="Workflow", started={"runId": "r"}), ("workflow_start",))
        self.assertEqual(self.kind(hook_event_name="PostToolUse", tool_name="Agent"), ("agent_launched",))

    def test_sessions_by_what_claude_code_says_and_no_more(self):
        self.assertEqual(self.kind(hook_event_name="SessionEnd", reason="logout"), ("session_ended", "logout"), "a reason not seen before: as it is")
        self.assertEqual(self.kind(hook_event_name="SessionEnd"), ("session_ended", "closed"))
        self.assertEqual(self.kind(hook_event_name="SessionStart"), ("ready",))
        self.assertEqual(self.kind(hook_event_name="SessionStart", source="teleport"), ("session_changed", "teleport"),
                         "a source not seen before: still a session that is there (2.2a), as a reason not seen before is still an end")
        self.assertEqual(self.kind(hook_event_name="SessionStart", source="resume", agent_type="claude"), ("session_changed", "resume"))
        self.assertEqual(self.kind(hook_event_name="SessionStart", source="startup", agent_type="reviewer"), ("ready",))
        self.assertEqual(self.kind(hook_event_name="PreCompact", trigger="auto"), ("compacting",))
        self.assertEqual(self.kind(hook_event_name="PostCompact", trigger="auto"), ("compact_done",))

    def test_what_it_does_not_know_and_what_is_no_event(self):
        self.assertEqual(self.kind(hook_event_name="SomethingNew"), ("unknown",))
        self.assertEqual(self.kind(), ("unknown",))
        self.assertEqual(hooks.normalize(None), None)
        self.assertEqual(hooks.normalize("text"), "text")
        self.assertEqual(hooks.normalize({"hook_event_name": "PreToolUse", "send_text": object(), "tool_name": "SendMessage"})["dc_kind"], "peer_sent")
        self.assertEqual(hooks.normalize({"hook_event_name": ["x"]})["dc_kind"], "unknown")

    def test_only_from_the_event_and_once(self):
        ev = {"hook_event_name": "Stop", "transcript_path": "/nowhere/at/all.jsonl", "session_id": "s", "channel": "1"}
        before = copy.deepcopy(ev)
        out = hooks.normalize(ev)
        self.assertIs(out, ev)
        self.assertEqual({k: v for k, v in out.items() if k not in hooks.NORMALIZED}, before, "nothing else touched; no file looked for")
        self.assertEqual(hooks.normalize({"hook_event_name": "Stop", "dc_kind": "said_by_the_sender"})["dc_kind"], "said_by_the_sender",
                         "an event that has a kind keeps it")

    def test_the_one_who_acts_on_an_event_makes_its_kind_out_again(self):
        """The bot reads with anew=True: the kind is worked out by the side that handles the event, whatever it came
        with. A dcremote of step 2.1 marked as not known what this version knows (a SessionStart bearing agent_type,
        a source not seen before); what the old kind brought along goes with it; nothing else is touched."""
        came = {"hook_event_name": "SessionStart", "source": "clear", "agent_type": "rigagent", "session_id": "new",
                "dc_kind": "unknown", "dc_why": "session_start_of_an_agent"}
        self.assertEqual(hooks.normalize(dict(came))["dc_kind"], "unknown", "without anew: as it came")
        again = hooks.normalize(dict(came), anew=True)
        self.assertEqual((again["dc_kind"], again["dc_why"], again["session_id"]), ("session_changed", "clear", "new"))
        odd = hooks.normalize({"hook_event_name": "SessionStart", "source": "teleport", "dc_kind": "unknown", "dc_why": "session_start_teleport"}, anew=True)
        self.assertEqual((odd["dc_kind"], odd["dc_why"]), ("session_changed", "teleport"))
        ready = hooks.normalize({"hook_event_name": "SessionStart", "source": "startup", "agent_type": "x", "dc_kind": "unknown", "dc_why": "y"}, anew=True)
        self.assertEqual((ready["dc_kind"], "dc_why" in ready), ("ready", False), "what the old kind brought with it goes with it")
        self.assertEqual(hooks.normalize({"hook_event_name": "SomethingNew", "dc_kind": "unknown"}, anew=True)["dc_kind"], "unknown")
        wrong = hooks.normalize({"hook_event_name": "Stop", "dc_kind": "said_by_the_sender", "dc_from": "x", "dc_newer": 1}, anew=True)
        self.assertEqual((wrong["dc_kind"], "dc_from" in wrong, wrong["dc_newer"]), ("turn_end", False, 1), "whatever it came with; a field not ours is left")
        self.assertEqual(hooks.normalize({"hook_event_name": "Stop"}, anew=True)["dc_kind"], "turn_end")
        for version in VERSIONS:  # and for every measured event, made out anew is made out the same
            for name in EXPECTED:
                for ev in case(version, name):
                    self.assertEqual(kinds([hooks.normalize(dict(ev), anew=True)]), kinds([ev]))


class OnePass(unittest.TestCase):
    """What is read out of a prompt or a notification is read in one pass over it (#code-review, 9e6ed4b): the
    pattern for a message from another conversation took the cube of the length for an opening tag, thousands of
    blanks and no closing tag (4000 blanks: 10 s), and normalize() runs where every event of a machine waits."""
    WAS = re.compile(r'^\s*<cross-session-message\b([^>]*)>\s*(.*?)\s*</cross-session-message>\s*$', re.S)

    def was(self, prompt):
        m = self.WAS.match(prompt or "")
        if not m:
            return None
        who = re.search(r'from-name="([^"]*)"', m.group(1))
        return (who.group(1) if who else ""), m.group(2)

    def test_the_same_answers_as_the_pattern_gave(self):
        rnd = random.Random(21)
        bits = ["<cross-session-message", "</cross-session-message>", ">", " ", "\n", "\t", 'from-name="', '"', "a", "b c", "s", "_", "<", "x=1",
                "\u3000", "\x0b"]
        hits = 0
        for _ in range(20000):
            prompt = "".join(rnd.choice(bits) for _ in range(rnd.randint(0, 9)))
            self.assertEqual(hooks.peer_message(prompt), self.was(prompt), repr(prompt))
            hits += self.was(prompt) is not None
        self.assertGreater(hits, 10, "the made-up prompts do hold messages")
        for whole in ('<cross-session-message from-name="st-1">done</cross-session-message>', ' <cross-session-message a="1" from-name="a b" >\n x \n</cross-session-message>\n',
                      "<cross-session-message>a</cross-session-message>b</cross-session-message>", "<cross-session-messages>a</cross-session-message>",
                      "<cross-session-message</cross-session-message>", '<cross-session-message from-name="x>a</cross-session-message>', None, 5):
            self.assertEqual(hooks.peer_message(whole), self.was(whole) if isinstance(whole, str) else None, whole)

    def test_twenty_thousand_characters_of_any_shape_take_no_time(self):
        n = 20000
        o, c = "<cross-session-message>", "</cross-session-message>"
        shapes = [o + " " * n, o + "\n" * n, o + " " * n + c, o + "hi" + " \n" * (n // 2), "<cross-session-message " + 'from-name="x' * (n // 12) + ">a" + c,
                  " " * n + o + "a" + c, o * (n // 23), o + (c + " ") * (n // 25), "[A" + "x" * n, "<" * n, "[" * n]
        began = time.time()
        for prompt in shapes:
            hooks.normalize({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
        self.assertLess(time.time() - began, 0.05 * len(shapes))
        slow = o + " " * 600  # (the pattern as it was, on a small one of these: already hundredths of a second)
        began = time.time()
        self.was(slow)
        was = time.time() - began
        began = time.time()
        hooks.peer_message(slow)
        self.assertLess(time.time() - began, max(was, 0.001))


if __name__ == "__main__":
    unittest.main()
