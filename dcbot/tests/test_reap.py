"""The idle reaper: a channel's own session after IDLE_HOURS, and (2026-10-11, #issues 10) the sessions the bot
started for a channel that is gone. Those are closed only on facts that can be read and are plain; where one cannot
be, the session stays and is written down for the owner (manager: "停掉、移除只能靠讀得到而且明確的事實；不確定就
不關、列出來給人看"). The first version went by the session's name and by an idle screen: it would have closed a
terminal channel's session, and one with a training run in the background (#code-review)."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from bot import ask, config, tmux_bridge, tmuxio
from tests import fakepane
from tests.fakepane import box
from tests.test_turn_end import Ch, Host

HOUR = 3600
GONE, THERE = 100000000000000042, 100000000000000043  # a deleted channel's id, and one that is there
IDLE = box("❯ ")
BUSY = "\n".join(["", "✻ Working… (5s)", fakepane.RULE, "❯ ", fakepane.RULE, "  esc to interrupt", ""])
# a turn over, a background command still running (as 2.1.296 draws it): the screen is an idle one
BACKGROUND = "\n".join(["", "✻ Worked for 2s · 1 shell still running", fakepane.RULE, "❯ ", fakepane.RULE,
                        "  ⏵⏵ bypass permissions on · 1 shell · ← for agents · ↓ to manage", ""])


class TheHost(Host):
    def get_channel(self, cid):
        return self.ch if int(cid) in self.there else None


def session(idle=100, attached=0, screen=IDLE, command="claude", channel=GONE, root=None, under=(), pid=500):
    """One session of the bot's server and what can be read of its process."""
    return {"idle": idle, "attached": attached, "screen": screen, "panes": [(pid, command)],
            "env": None if channel is None else (root or config.ROOT, str(channel)), "under": under}


class Server(object):
    """The bot's tmux server and /proc, as far as the reaper asks."""

    def __init__(self, sessions):
        self.sessions, self.killed = dict(sessions), []

    def of(self, pid):
        return next(s for s in self.sessions.values() if any(p == pid for p, _ in s["panes"]))

    async def own_sessions(self):
        return [{"name": name, "at": time.time() - s["idle"] * HOUR, "attached": s["attached"], "panes": list(s["panes"])}
                for name, s in sorted(self.sessions.items())]

    def started_for(self, pid):
        return self.of(pid)["env"]

    def children(self, pid):
        under = self.of(pid)["under"]
        return None if under is None else list(under)

    async def alive(self, t):
        return t in self.sessions

    async def raw_screen(self, t):
        return self.sessions[t]["screen"]

    async def kill(self, t):
        self.killed.append(t)
        self.sessions.pop(t, None)


class Reap(unittest.TestCase):
    def setUp(self):
        self.ch = Ch()
        self.host = TheHost(self.ch)
        self.host.there = {THERE}
        self.bridge = tmux_bridge.TmuxBridge(self.host)
        self.state = self.host.channels_state
        self.state[str(THERE)] = {"tmux": "dc-project-a-0043", "cwd": "/w", "last_active": time.time()}
        self.tmp = tempfile.mkdtemp()

    def reap(self, sessions, delivered=None):
        server = Server(sessions)

        async def deliver_to(host, ask_id, result):
            (delivered if delivered is not None else []).append((host, ask_id, result))
        with fakepane.use(server), mock.patch.object(config, "IDLE_HOURS", 12.0), mock.patch.object(ask, "deliver_to", deliver_to), \
                mock.patch.object(ask, "ASKS", os.path.join(self.tmp, "asks")), mock.patch.object(config, "STATE", os.path.join(self.tmp, "state")):
            asyncio.run(self.bridge.reap())
        try:
            with open(os.path.join(self.tmp, "state", tmux_bridge.OWNERLESS)) as f:
                rows = json.load(f)["kept"]
        except OSError:
            rows = None  # nothing was looked at
        self.kept = None if rows is None else dict((k["name"], k["why"]) for k in rows)
        self.assertTrue(all(k["channel"] in (str(GONE), str(GONE + 2)) for k in rows or []))
        return server.killed

    def test_closed_when_every_fact_holds(self):
        killed = self.reap({"dc-gone-0042": session(idle=107), "dc-project-a-0043": session(idle=1, channel=THERE, pid=501)})
        self.assertEqual(killed, ["dc-gone-0042"])
        self.assertEqual(self.kept, {})
        (said,) = self.host.audits
        self.assertIn("關閉沒有頻道的 tmux 對話 `dc-gone-0042`（它的頻道 %d 已經不在，閒置 107 小時、沒有背景工作" % GONE, said)
        self.assertIn("claude --resume", said)

    def test_a_background_command_keeps_it_and_the_owner_is_told(self):
        """#code-review: the turn is over and the screen an idle one; the training run is a shell under Claude Code."""
        killed = self.reap({"dc-gone-0042": session(idle=13, screen=BACKGROUND, under=(777, 778))})
        self.assertEqual((killed, self.host.audits), ([], []))
        self.assertEqual(self.kept, {"dc-gone-0042": "它底下還有行程在跑（背景工作？pid 777、778）"})
        killed = self.reap({"dc-gone-0042": session(idle=13, under=None)})
        self.assertEqual((killed, self.kept), ([], {"dc-gone-0042": "讀不到它底下有沒有行程在跑（背景工作）"}))
        killed = self.reap({"dc-gone-0042": session(idle=500, screen=BUSY)})
        self.assertEqual((killed, self.kept), ([], {"dc-gone-0042": "有一輪在跑"}))

    def test_too_early_or_someone_at_it_is_neither_closed_nor_listed(self):
        killed = self.reap({"dc-gone-0042": session(idle=3), "dc-watched-0044": session(idle=500, attached=1, pid=502)})
        self.assertEqual((killed, self.kept, self.host.audits), ([], {}, []))

    def test_a_session_any_channel_names_is_that_channels(self):
        """#code-review: a terminal the setup card put in a session of the bot's server is named by
        st["home"]["session"], not st["tmux"]; someone ran claude in it."""
        names = {"terminal": {"kind": "terminal", "home": {"socket": "/tmp/tmux-1000/x", "session": "dc-term-0045", "window": ""}},
                 "window": {"home": {"socket": "/tmp/tmux-1000/x", "session": "dc-window-0046", "window": "w"}, "pane": {"id": "%3"}},
                 "attached": {"pane": {"id": "%4"}, "pane_session": "dc-attached-0047"},
                 "archived": {"archived": {"pane": {"id": "%5"}, "pane_session": "dc-archived-0048", "home": None}},
                 "archived home": {"archived": {"pane": None, "pane_session": None, "home": {"session": "dc-archived-home-0049"}}}}
        for i, st in enumerate(names.values()):
            self.state[str(100000000000000050 + i)] = st
        every = dict(("dc-%s" % n, session(idle=500, channel=GONE, pid=600 + i)) for i, n in enumerate(
            ["term-0045", "window-0046", "attached-0047", "archived-0048", "archived-home-0049"]))
        killed = self.reap(every)
        self.assertEqual((killed, self.kept), ([], {}))

    def test_a_pane_someone_added_beside_it_keeps_it_and_the_owner_is_told(self):
        """#code-review-2 2026-10-11: such a session stayed for ever and nobody was told. A pane split off in the
        session inherits the session's DCBOT_* but runs a shell: it is not the bot's Claude."""
        both = dict(session(idle=500, pid=502), panes=[(502, "claude"), (503, "zsh")])
        killed = self.reap({"dc-gone-0042": both})
        self.assertEqual((killed, self.host.audits), ([], []))
        self.assertEqual(self.kept, {"dc-gone-0042": "它還有 1 個別的窗格（不是機器人為這個頻道開的 Claude）"})
        self.assertEqual(self.reap({"dc-gone-0042": dict(both, idle=3)}), [])
        self.assertEqual(self.kept, {}, "too early: nothing to decide yet")

    def test_the_process_gone_between_two_looks_ends_nothing(self):
        """#code-review 2026-10-11: the channel's id was read a second time after the facts were judged; a process
        ending in between gave None and the round stopped there."""
        server = Server({"dc-gone-0042": session(idle=500), "dc-other-0044": session(idle=500, channel=GONE + 2, pid=501)})
        asked = []
        real = server.started_for

        def once(pid):
            asked.append(pid)
            return real(pid) if asked.count(pid) == 1 else None  # gone at the second look
        server.started_for = once
        with fakepane.use(server), mock.patch.object(config, "IDLE_HOURS", 12.0), mock.patch.object(ask, "ASKS", os.path.join(self.tmp, "asks")), \
                mock.patch.object(config, "STATE", os.path.join(self.tmp, "state")):
            asyncio.run(self.bridge.reap())
        self.assertEqual(server.killed, ["dc-gone-0042", "dc-other-0044"], "each read once, both dealt with")
        self.assertEqual(asked, [500, 501])

    def test_not_started_by_this_bot_for_a_channel_is_not_the_bots_to_judge(self):
        """Whatever it is called: the name decides nothing."""
        killed = self.reap({
            "dc-looks-like-one-0042": session(idle=500, channel=None),                               # someone's own claude here
            "dc-shell-0001": session(idle=500, command="zsh", pid=501),                                # a shell
            "dc-two-panes-0002": dict(session(idle=500, pid=502, channel=None), panes=[(502, "claude"), (503, "vim")]),
            "dc-other-bot-0003": session(idle=500, root="/somewhere/else/dcbot", pid=504),             # another installation's
            "dc-selftest-0004": session(idle=500, channel="selftest", pid=505),                        # not a channel's id
            "dc-channel-there-0005": session(idle=500, channel=THERE, pid=506)})                       # its channel exists
        self.assertEqual((killed, self.kept, self.host.audits), ([], {}, []))
        self.host.there.add(GONE)  # no state for it, but Discord still has the channel (the setup card not answered yet)
        self.assertEqual(self.reap({"dc-gone-0042": session(idle=500)}), [])

    def test_a_name_unlike_the_bots_is_closed_all_the_same_when_the_facts_hold(self):
        self.assertEqual(self.reap({"renamed-by-hand": session(idle=500)}), ["renamed-by-hand"])

    def test_with_no_state_at_all_nothing_is_closed(self):
        """The state file could not be read: the bot starts with none, and every session would look ownerless."""
        self.state.clear()
        self.host.there.clear()
        killed = self.reap({"dc-gone-0042": session(idle=500), "dc-project-a-0043": session(idle=500, channel=THERE, pid=501)})
        self.assertEqual((killed, self.kept, self.host.audits), ([], None, []))

    def test_while_discord_has_not_said_which_channels_there_are_nothing_is_closed(self):
        """#code-review: the bot just started (or the connection is down) and no channel can be had from Discord;
        a channel that is there but has lost its state would look gone."""
        self.host.there.clear()
        killed = self.reap({"dc-gone-0042": session(idle=500)})
        self.assertEqual((killed, self.kept, self.host.audits), ([], None, []))
        self.host.there.add(THERE)  # the list is there: the same session is closed
        self.assertEqual(self.reap({"dc-gone-0042": session(idle=500)}), ["dc-gone-0042"])

    def test_a_channels_own_idle_session_is_closed_as_before(self):
        self.state[str(THERE)]["last_active"] = time.time() - 13 * HOUR
        self.state["100000000000000060"] = {"tmux": "dc-project-b-0060", "cwd": "/w", "last_active": time.time() - 13 * HOUR, "bg": 1}
        self.host.there.add(100000000000000060)
        killed = self.reap({"dc-project-a-0043": session(idle=13, channel=THERE), "dc-project-b-0060": session(idle=13, channel=100000000000000060, pid=501)})
        self.assertEqual(killed, ["dc-project-a-0043"])
        self.assertEqual(self.host.audits, ["關閉閒置的 tmux 對話 `dc-project-a-0043`（下一則訊息會自動接回）"])

    def test_only_the_questions_of_that_very_channel_are_closed_with_it(self):
        os.makedirs(os.path.join(self.tmp, "asks"))
        for ask_id, channel in (("q-gone", GONE), ("q-same-ending", GONE + 10000), ("q-there", THERE)):
            with open(os.path.join(self.tmp, "asks", ask_id + ".json"), "w") as f:
                json.dump({"channel": channel, "questions": [], "answers": {}, "messages": {}}, f)
        delivered = []
        killed = self.reap({"dc-gone-0042": session(idle=107)}, delivered)
        self.assertEqual(killed, ["dc-gone-0042"])
        self.assertEqual([d[1] for d in delivered], ["q-gone"], "by the whole id: not one that only ends alike")
        self.assertIn("no longer exists", delivered[0][2]["reason"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp, "asks"))), ["q-same-ending.json", "q-there.json"])


class Named(unittest.TestCase):
    """Every place in bot/ that writes a tmux session's name into a channel's state, and the shape it writes:
    sessions_named() must find each. A new way of holding one is added here and there together."""
    N = "the-session"
    WRITERS = (
        ("bot/tmux_bridge.py", 'st.setdefault("tmux", tmuxio.session_name(', {"tmux": N}),                               # ensure
        ("bot/setup.py", '"session": tmuxio.session_name(ch.name, ch.id), "window": ""}, "kind": "terminal"',           # a terminal in the bot's tmux
         {"kind": "terminal", "home": {"socket": "/s", "session": N, "window": ""}}),
        ("bot/setup.py", 'st.update({"home": {"socket": tmuxio.user_socket(), "session": session, "window": window}',   # a window, this machine
         {"home": {"socket": "/s", "session": N, "window": "w"}}),
        ("bot/setup.py", 'st.update({"home": {"socket": info["socket"], "session": session, "window": window, "host": host}',
         {"home": {"socket": "/s", "session": N, "window": "w", "host": "host-a"}}),
        ("bot/main.py", 'st.update({"pane": address, "pane_session": pane["session"]', {"pane": {"id": "%1"}, "pane_session": N}),  # /attach
        ("bot/archive.py", 'rec = {"pane": st.get("pane"), "pane_session": st.get("pane_session"), "home": st.get("home")',       # !detach
         {"archived": {"pane": {"id": "%1"}, "pane_session": N, "home": None}}),
        ("bot/archive.py", 'rec = {"pane": st.get("pane"), "pane_session": st.get("pane_session"), "home": st.get("home")',
         {"archived": {"pane": None, "pane_session": None, "home": {"session": N}}}),
        ("bot/archive.py", 'st.update({"pane": pane, "pane_session": rec.get("pane_session")})', {"pane": {"id": "%1"}, "pane_session": N}),
        ("bot/archive.py", '"session": rec.get("pane_session") or tmuxio.safe_session(ch.name)}', {"home": {"session": N}}),
    )

    def test_each_writer_is_still_there_and_what_it_writes_is_found(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for path, snippet, st in self.WRITERS:
            with open(os.path.join(root, path), encoding="utf-8") as f:
                self.assertIn(snippet, f.read(), "%s no longer writes it this way: look at the table again" % path)
            self.assertEqual(tmux_bridge.sessions_named({"1": st}), {self.N}, (path, st))

    def test_odd_states_do_not_break_it(self):
        self.assertEqual(tmux_bridge.sessions_named({"1": {}, "2": {"home": None, "archived": None}, "3": "x", "4": {"archived": "x"}}), set())


class WhatTmuxAndProcTell(unittest.TestCase):
    def listed(self, rows):
        async def tmux(*args, **kw):
            self.asked = args
            return 0, "\n".join("\t".join(r) for r in rows) + "\n"
        with fakepane.use(tmux=tmux):
            return asyncio.run(tmuxio.own_sessions())

    def test_every_session_with_its_panes_nothing_judged(self):
        got = self.listed([("dc-project-a-0043", "1791660000", "0", "500", "claude"), ("a-shell", "1791170000", "0", "501", "zsh"),
                           ("two", "1791000000", "1", "502", "claude"), ("two", "1791660500", "1", "503", "vim"),
                           ("odd", "", "0", "504", "claude"), ("short", "1")])
        self.assertEqual(got, [{"name": "a-shell", "at": 1791170000, "attached": 0, "panes": [(501, "zsh")]},
                               {"name": "dc-project-a-0043", "at": 1791660000, "attached": 0, "panes": [(500, "claude")]},
                               {"name": "two", "at": 1791660500, "attached": 1, "panes": [(502, "claude"), (503, "vim")]}])
        self.assertIn("#{window_activity}", self.asked[-1])
        self.assertNotIn("#{session_activity}", self.asked[-1], "with no client attached it stays at the session's creation")

    def test_no_server_no_sessions(self):
        async def tmux(*args, **kw):
            return 1, ""
        with fakepane.use(tmux=tmux):
            self.assertEqual(asyncio.run(tmuxio.own_sessions()), [])

    @unittest.skipUnless(os.path.isdir("/proc/self"), "needs /proc")
    def test_a_process_environment_and_its_children(self):
        import subprocess
        import sys
        code = "import subprocess, sys, time; p = subprocess.Popen(['sleep', '30']); print(p.pid, flush=True); time.sleep(30)"
        env = dict(os.environ, DCBOT_ROOT="/the/bot", DCBOT_CHANNEL="100000000000000042")
        parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, env=env)
        try:
            child = int(parent.stdout.readline())
            self.assertEqual(tmuxio.started_for(parent.pid), ("/the/bot", "100000000000000042"))
            self.assertEqual(tmuxio.children(parent.pid), [child])
            self.assertEqual(tmuxio.children(child), [], "an idle one has none")
            plain = subprocess.Popen(["sleep", "30"], env={k: v for k, v in os.environ.items() if not k.startswith("DCBOT_")})
            try:
                self.assertIsNone(tmuxio.started_for(plain.pid), "not started by a bot for a channel")
            finally:
                plain.kill()
                plain.wait()
        finally:
            subprocess.run(["kill", str(child)], stderr=subprocess.DEVNULL)
            parent.kill()
            parent.wait()
            parent.stdout.close()
        self.assertIsNone(tmuxio.started_for(parent.pid), "gone: nothing to read")


if __name__ == "__main__":
    unittest.main()
