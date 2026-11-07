"""C-112a: a pane attached after a reboot takes the channel its conversation or window had, when that channel's own pane
is gone; a pane still alive keeps its channel. From 2026-10-30: resumed in a new tmux, --attach opened a second channel."""
import asyncio
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import unittest
from unittest import mock

from bot import archive, kinds, main as botmain, remote, routes, tmuxio
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_archive_put import Ch, Guild

NEW = {"host": "lap", "socket": "/s", "id": "%3", "pid": "900", "session": "work", "window": "0", "cwd": "/w/proj"}
OLD = {"host": "lap", "socket": "/s", "id": "%1", "pid": "100"}


class MakingGuild(Guild):
    async def create_text_channel(self, name, category=None, topic=None):
        return Ch(self, name, category, topic=topic)


class Reuse(unittest.TestCase):
    def setUp(self):
        self.g = MakingGuild()
        self.proj = self.g.cat("proj")
        self.bot = botmain.Bot.__new__(botmain.Bot)
        self.bot.channels_state = {}
        self.bot.save = lambda: None
        self.bot.archive = archive.Archive(self.bot)
        self.bot.get_channel = lambda cid: next((c for c in self.g.text_channels if c.id == cid), None)
        self.bot.backfill = self.backfill
        self.standing, self.unbound, self.bound, self.backfills = {}, [], [], []

    async def backfill(self, ch, messages):
        self.backfills.append(ch.id)

    def channel(self, topic=None, **st):
        ch = Ch(self.g, "old-%d" % len(self.g.text_channels), self.proj, topic=topic)
        self.bot.channels_state[str(ch.id)] = dict({"cwd": "/w/proj", "pane": dict(OLD), "pane_session": "work", "mode": "tmux",
                                                    "session_id": "sid-old", "busy": True, "bg": 2}, **st)
        return ch

    def attach(self, sid="sid-x", pane=None):
        """attach_one() of `pane` on a machine whose pane says it holds `sid`. What it said."""
        async def dcremote(host, *args, **kw):
            return 0, json.dumps({"session_id": sid, "turns": []} if sid else {})

        async def standing(t):
            return self.standing.get(t["id"], "gone")

        async def bind_any(p, cid):
            self.bound.append((p["id"], cid))

        async def unbind_any(cid, host=None):
            self.unbound.append((cid, host))

        async def go():
            self.bot.loop = asyncio.get_running_loop()
            said = await botmain.Bot.attach_one(self.bot, self.g, pane or NEW, "proj")
            await asyncio.sleep(0)
            return said
        with mock.patch.object(routes, "bind_any", bind_any), mock.patch.object(routes, "unbind_any", unbind_any), \
                mock.patch.object(remote, "dcremote", dcremote), mock.patch.object(tmuxio, "standing", standing):
            return asyncio.run(go())

    def test_the_same_conversation_takes_its_channel_back(self):
        ch = self.channel(session_id="sid-x")
        n = len(self.g.text_channels)
        said = self.attach()
        self.assertEqual(len(self.g.text_channels), n, "no second channel")
        st = self.bot.channels_state[str(ch.id)]
        self.assertEqual((st["pane"]["id"], st["pane"]["pid"], st["session_id"], st["busy"]), ("%3", "900", "sid-x", False))
        self.assertNotIn("bg", st, "what ran in the old pane ended with it")
        self.assertEqual(self.unbound, [(ch.id, "lap")], "the old pane's route goes first")
        self.assertEqual(self.bound, [("%3", ch.id)])
        self.assertEqual(self.backfills, [], "the channel holds its history")
        self.assertIn("重新接上了", ch.sent[-1], "the same conversation: nothing more to say")
        self.assertIn("<#%d>" % ch.id, said)
        self.assertIn("沿用原本的頻道", said)

    def test_the_same_window_and_folder_takes_its_channel_back(self):
        ch = self.channel(topic=routes.topic("lap", NEW))
        self.attach()
        st = self.bot.channels_state[str(ch.id)]
        self.assertEqual((st["pane"]["id"], st["session_id"]), ("%3", "sid-x"))
        self.assertIn("sid-old", st["history"])
        self.assertEqual(ch.sent[-1], "這次接上的是另一個對話。要換回原本的：在終端機那個窗格結束 Claude，再跑 `claude --resume sid-old`。")

    def test_the_conversation_goes_before_the_window(self):
        window = self.channel(topic=routes.topic("lap", NEW))
        talk = self.channel(session_id="sid-x")
        self.assertIn("<#%d>" % talk.id, self.attach())
        self.assertEqual(self.bot.channels_state[str(window.id)]["pane"]["id"], "%1")

    def test_a_pane_still_alive_or_not_answering_keeps_its_channel(self):
        for how in ("alive", "unknown"):
            ch = self.channel(session_id="sid-x")
            self.standing["%1"] = how
            n = len(self.g.text_channels)
            self.attach()
            self.assertEqual(len(self.g.text_channels), n + 1, how)
            self.assertEqual(self.bot.channels_state[str(ch.id)]["pane"]["id"], "%1", how)
            self.bot.channels_state.pop(str(ch.id))

    def test_what_is_not_this_panes_is_left_alone(self):
        others = [self.channel(session_id="other"),                                      # another conversation, another window
                  self.channel(session_id="sid-x", pane=dict(OLD, host="far")),          # another machine
                  self.channel(session_id="sid-x", home={"host": "lap", "session": "w"}),  # the bot's window: it opens it again itself
                  self.channel(session_id="sid-x", kind=kinds.TERMINAL)]
        self.channel(session_id="sid-x", archived={"why": kinds.DETACHED})
        before = [dict(self.bot.channels_state[str(c.id)]["pane"]) for c in others]
        n = len(self.g.text_channels)
        self.attach()
        self.assertEqual(len(self.g.text_channels), n + 1, "a channel of its own, as before")
        self.assertEqual([self.bot.channels_state[str(c.id)]["pane"] for c in others], before)
        self.assertEqual(self.unbound, [])

    def test_a_conversation_not_known_still_goes_by_the_window(self):
        talk = self.channel(session_id=None)
        window = self.channel(topic=routes.topic("lap", NEW))
        self.assertIn("<#%d>" % window.id, self.attach(sid=None))
        self.assertEqual(self.bot.channels_state[str(talk.id)]["pane"]["id"], "%1", "no id is no match")


    def test_a_channel_really_archived_comes_back_out_of_the_archive(self):
        """/archive'd, its pane gone and the window opened again as a new pane: --attach takes the channel out of the
        archive by its name and topic under the note /archive put in front (11-02: it opened a second channel)."""
        shelf = self.g.cat(botmain.config.ARCHIVE_CATEGORY)
        rec = {"why": "archived", "from": {"id": self.proj.id, "name": "proj"}, "at": 1767000000.0, "pane": dict(OLD),
               "topic": routes.topic("lap", NEW)}
        ch = Ch(self.g, routes.channel_name(NEW, "proj"), shelf, topic=archive.topic_with(rec["topic"], rec))
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w/proj", "session_id": "sid-old", "archived": rec}
        n = len(self.g.text_channels)
        said = self.attach()
        self.assertEqual(len(self.g.text_channels), n, "no second channel")
        self.assertIn("<#%d>" % ch.id, said)
        self.assertIs(ch.category, self.proj, "back where it came from")
        self.assertEqual(ch.topic, routes.topic("lap", NEW), "the note gone")
        st = self.bot.channels_state[str(ch.id)]
        self.assertNotIn("archived", st)
        self.assertEqual(st["pane"]["id"], "%3")


class TakenOrNot(unittest.TestCase):
    """attach(): the route table is by pane number, which a new tmux server gives out again."""
    setUp, channel, backfill = Reuse.setUp, Reuse.channel, Reuse.backfill

    def attach_ref(self, routed):
        pane = dict(NEW, routed=str(routed))

        async def panes(everywhere=False):
            return [pane]
        got = []

        async def one(guild, p, project, into=None):
            got.append(p["id"])
            return "attach_one"
        self.bot.attach_one = one
        self.bot.chan = lambda c: self.bot.channels_state.setdefault(str(c.id), {})
        self.bot.get_guild = lambda gid: self.g
        with mock.patch.object(tmuxio, "claude_panes", panes), mock.patch.object(routes, "pick", lambda ps, ref: pane), \
                mock.patch.object(routes, "project_of", lambda p: "proj"):
            return asyncio.run(botmain.Bot.attach(self.bot, None, "x")), got

    def test_attached_for_real_is_said_so(self):
        ch = self.channel(pane={"host": "lap", "socket": "/s", "id": "%3", "pid": "900"})
        said, got = self.attach_ref(ch.id)
        self.assertEqual((said, got), ("這個對話已經接在 <#%d> 了。" % ch.id, []))

    def test_a_number_given_out_again_is_free_in_the_list_and_for_all(self):
        table = {"/s|%3": {"channel": "9", "pid": "100"}, "/s|%4": {"channel": "8", "pid": "901"}, "/s|%5": "7"}
        local = [dict(NEW, host=None, id=i, pid=p) for i, p in (("%3", "900"), ("%4", "901"), ("%5", "902"))]
        self.assertEqual([routes.routed_to(p, table) for p in local], [None, "8", "7"], "(a route with no process: as before)")
        self.assertEqual(sum(routes.by_project(local, table).values(), [])[0]["id"], "%3")
        self.assertEqual(len(sum(routes.by_project(local, table).values(), [])), 1)
        self.assertIn(routes.key(local[0]), routes.buttons(local, table).children[0].custom_id)

    def test_another_machines_list_says_the_same(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "dcremote")
        loader = importlib.machinery.SourceFileLoader("dcremote_reuse", path)
        tool = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcremote_reuse", loader))
        loader.exec_module(tool)

        async def panes():
            return [{"socket": "/s", "id": "%3", "pid": "900", "cwd": "/w"}, {"socket": "/s", "id": "%4", "pid": "901", "cwd": "/w"}]
        out = io.StringIO()
        with mock.patch.object(tool.tmuxio, "claude_panes", panes), contextlib.redirect_stdout(out), \
                mock.patch.object(tool.routecore, "load", lambda: {"/s|%3": {"channel": "9", "pid": "100"}, "/s|%4": {"channel": "8", "pid": "901"}}), \
                mock.patch.object(tool.routecore, "project_name", lambda cwd: "p"):
            tool.panes()
        self.assertEqual([p["routed"] for p in json.loads(out.getvalue())], [None, "8"])

    def test_a_new_pane_with_the_old_number_is_not_taken_for_the_old_one(self):
        ch = self.channel(session_id="sid-x", pane={"host": "lap", "socket": "/s", "id": "%3", "pid": "100"})
        asked = []

        async def standing(t):
            asked.append(t["pid"])
            return "gone"
        self.assertEqual(asyncio.run(self.left_behind(standing)), ch)
        self.assertEqual(asked, ["100"], "asked with the old pane's own process")

    def left_behind(self, standing):
        async def dcremote(host, *args, **kw):
            return 0, json.dumps({"session_id": "sid-x"})

        async def go():
            with mock.patch.object(tmuxio, "standing", standing), mock.patch.object(remote, "dcremote", dcremote):
                return await botmain.Bot.left_behind(self.bot, NEW, "lap")
        return go()

    def test_the_number_of_a_pane_gone_is_not_attached(self):
        ch = self.channel(pane={"host": "lap", "socket": "/s", "id": "%3", "pid": "100"})
        said, got = self.attach_ref(ch.id)
        self.assertEqual((said, got), ("attach_one", ["%3"]))
