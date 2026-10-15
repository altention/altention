"""Archived channels: one pane, one channel; the ways back."""
import asyncio
import json
import unittest
from unittest import mock

import discord

from bot import archive, config, routes, tmuxio
from tests import fakepane

P = {"socket": "/s", "id": "%5", "pid": "100"}


class Pure(unittest.TestCase):
    def test_lookups(self):
        state = {"1": {"archived": {"pane": P}}, "2": {"pane": dict(P)}, "3": {"pane": dict(P, pid="999")}}
        self.assertEqual(archive.remembered_by(state, P), 1)
        self.assertEqual(archive.followed_by(state, P), 2, "pid 999 is another pane with the same number")
        self.assertIsNone(archive.followed_by(state, P, besides=2))
        self.assertTrue(archive.same_pane(P, {"socket": "/s", "id": "%5"}), "no pid on one side: number decides")
        self.assertEqual(archive.record({"pane": P, "cwd": "/w", "session_id": "s", "x": 1}),
                         {"pane": P, "pane_session": None, "home": None, "session_id": "s", "cwd": "/w"})
        self.assertEqual([b.custom_id for b in archive.view(7).children], ["arch:7:back", "arch:7:new"])
        self.assertEqual([b.custom_id for b in archive.view(7, False).children], ["arch:7:new"])


class Resp(object):
    def __init__(self):
        self.edited, self.said, self.view = None, None, None

    async def edit_message(self, content=None, view=None):
        self.edited = content

    async def send_message(self, text, view=None, ephemeral=False):
        self.said, self.view = text, view


class Chan(object):
    def __init__(self, cid):
        self.id, self.sent, self.category, self.guild = cid, [], None, None

    async def send(self, text, view=None):
        self.sent.append(text)

    async def edit(self, category=None):
        self.category = category


class Click(object):
    type = discord.InteractionType.component

    def __init__(self, ch, what):
        self.channel, self.data = ch, {"custom_id": "arch:%d:%s" % (ch.id, what)}
        self.user, self.response = type("U", (), {"id": 111})(), Resp()


class FakeTmux(object):
    def __init__(self):
        self.started, self.opens = [], True

    async def start_now(self, ch):
        self.started.append(ch.id)
        return self.opens


class FakeBot(object):
    def __init__(self, chans):
        self.chans, self.channels_state, self.tmux, self.detached = dict((c.id, c) for c in chans), {}, FakeTmux(), []

    def get_channel(self, cid):
        return self.chans.get(cid)

    def chan(self, ch):
        return self.channels_state.setdefault(str(ch.id), {"cwd": "/w"})

    def save(self):
        pass

    async def detach(self, ch, st, why="detached"):
        self.detached.append(ch.id)
        self.channels_state[str(ch.id)] = {"archived": archive.record(st, why)}


class Back(unittest.TestCase):
    def setUp(self):
        self.saved = (config.OWNER_ID, routes.bind)
        config.OWNER_ID = 111
        self.alive = True

        async def alive(t):
            return self.alive
        for patcher in fakepane.patchers(alive=alive):  # with the guard: nothing here may reach a real tmux
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bound = []
        routes.bind = lambda pane, cid: self.bound.append(cid)
        self.old, self.other = Chan(1), Chan(2)
        self.bot = FakeBot([self.old, self.other])
        self.arch = archive.Archive(self.bot)

        async def unarchive(ch, cwd):
            ch.category = "project"
        self.arch.unarchive = unarchive
        self.bot.channels_state["1"] = {"archived": {"pane": P, "pane_session": "st", "home": None,
                                                     "session_id": "sid", "cwd": "/w"}}

    def tearDown(self):
        config.OWNER_ID, routes.bind = self.saved

    def click(self, what):
        c = Click(self.old, what)
        asyncio.run(self.arch.on_interaction(c))
        return c

    def test_back_to_a_free_pane(self):
        self.click("back")
        st = self.bot.channels_state["1"]
        self.assertEqual((st["pane"], st["session_id"], self.bound), (P, "sid", [1]))
        self.assertNotIn("archived", st)
        self.assertEqual(self.old.category, "project")

    def test_pane_followed_elsewhere_asks_then_moves(self):
        self.bot.channels_state["2"] = {"pane": dict(P), "cwd": "/w"}
        c = self.click("back")
        self.assertIn("<#2>", c.response.said)
        self.assertNotIn("pane", self.bot.channels_state["1"], "nothing taken before the owner says so")
        self.click("move")
        self.assertEqual(self.bot.detached, [2])
        self.assertEqual(self.bot.channels_state["2"]["archived"].get("why"), "channel_moved", "why the other channel was put away")
        self.assertEqual(self.bot.channels_state["1"]["pane"], P)

    def test_gone_window_is_reopened_with_the_conversation(self):
        self.alive = False
        self.click("back")
        st = self.bot.channels_state["1"]
        self.assertEqual(st["home"]["session"], "st")
        self.assertEqual(st["session_id"], "sid")
        self.assertEqual(self.bot.tmux.started, [1])

    def test_gone_pane_of_another_machine_is_reopened_there(self):
        """A pane attached from another machine (no home of the bot's) whose window is gone (that machine restarted):
        the new window is opened over there, in its tmux, where the conversation's file is."""
        far = dict(P, socket="/tmp/tmux-7/default", host="lap")
        self.bot.channels_state["1"]["archived"]["pane"] = far
        self.alive = False
        self.click("back")
        st = self.bot.channels_state["1"]
        self.assertEqual(st["home"], {"socket": "/tmp/tmux-7/default", "window": "", "session": "st", "host": "lap"})
        self.assertEqual((st["session_id"], self.bot.tmux.started), ("sid", [1]))
        self.assertIn("lap 的 tmux `st`", self.old.sent[-1])

    def test_once_reopened_it_says_so(self):
        """Not only "opening…": the owner took that for the end of it and the window for one that had failed."""
        self.alive = False
        self.click("back")
        self.assertIn("開新視窗接回", self.old.sent[-2])
        self.assertIn("已接回原本的對話", self.old.sent[-1])
        self.assertIn("tmux `st` 的新視窗", self.old.sent[-1])

    def test_not_reopened_it_says_nothing_more(self):
        self.alive = False
        self.bot.tmux.opens = False  # start_now has said why, in the channel
        self.click("back")
        self.assertIn("開新視窗接回", self.old.sent[-1])
        self.assertFalse(any("已接回" in s for s in self.old.sent))

    def test_gone_pane_of_this_machine_stays_here(self):
        self.alive = False
        self.click("back")
        st = self.bot.channels_state["1"]
        self.assertNotIn("host", st["home"])
        self.assertEqual(st["home"]["socket"], archive.tmuxio.user_socket())

    def test_a_window_whose_machine_does_not_answer_is_not_opened_anew(self):
        """3b 2.6b: asked and not answered is not gone. The card said "the window is gone, opening a new one with
        the same conversation": on a machine that only dropped its line, a second Claude on that conversation."""
        far = dict(P, host="lap")
        self.bot.channels_state["1"]["archived"]["pane"] = far
        before = json.dumps(self.bot.channels_state["1"], sort_keys=True)

        async def no_answer(t):
            tmuxio.asked(t, answered=False)
            return False
        self.addCleanup(tmuxio.UNANSWERED.clear)
        from bot import remote
        with fakepane.replace("alive", no_answer), mock.patch.object(remote, "unreachable", lambda host: "連不上 %s" % host):
            c = self.click("back")
        self.assertIn("連不上 lap，所以不知道原本的視窗還在不在，沒有接回", c.response.said)
        self.assertEqual(json.dumps(self.bot.channels_state["1"], sort_keys=True), before, "nothing of the channel is changed")
        self.assertEqual((self.bot.tmux.started, self.bound, self.bot.detached), ([], [], []))
        self.alive = False   # it answers: the window is not there
        tmuxio.asked(far, answered=True)   # (as the real asking notes; the stand-in of setUp() notes nothing)
        self.click("back")
        self.assertEqual(self.bot.tmux.started, [1], "gone for a fact: opened again, as ever")

    def test_new_conversation_here(self):
        offered = []

        async def offer(ch):
            offered.append(ch.id)
        self.bot.setup = type("S", (), {"offer": staticmethod(offer)})()
        self.click("new")
        self.assertEqual(offered, [1])
        self.assertNotIn("1", self.bot.channels_state)


if __name__ == "__main__":
    unittest.main()
