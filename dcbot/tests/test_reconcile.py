"""C-201 first batch: the look (bot/reconcile.py, manager 11-17). Real tmux servers of the test's own, in a socket
folder of its own (TMUX_TMPDIR at a scratch folder: tests/tmuxguard lets nothing reach the owner's or the bot's
servers), another machine stood in for (remote.dcremote), and dcremote look run as the script it is."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from bot import reconcile, routecore, tmuxio, where
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tmux(sock, *args):
    return subprocess.run(["tmux", "-S", sock] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=10)


class Chan(object):
    def __init__(self, cid, name):
        self.id, self.name, self.sent = cid, name, []

    async def send(self, text=None, **kw):
        self.sent.append(text)
        return type("M", (), {"id": len(self.sent), "channel": self})()


class Bot(object):
    def __init__(self, bot_sock):
        self.channels_state, self.audits, self.lease, self.bot_sock = {}, [], None, bot_sock
        self.logged = []  # (what save()'s stamp() says, as main.Bot.save() prints it)
        self.chans = {}

    def where_context(self):
        return "lap", self.bot_sock

    def channel_name_of(self, cid):
        return getattr(self.chans.get(int(cid)), "name", None)

    def get_channel(self, cid):
        return self.chans.get(cid)

    def save(self):
        where.stamp(self.channels_state, self.channel_name_of, "lap", self.bot_sock, say=self.logged.append)

    async def audit(self, text):
        self.audits.append(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="dcq")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.folder = os.path.join(self.base, "tmux-%d" % os.getuid())
        os.mkdir(self.folder, 0o700)
        for p in (mock.patch.dict(os.environ, {"TMUX_TMPDIR": self.base}),
                  mock.patch.dict(tmuxio._set, {"done": True, "socket": "bot", "skip": ()}),
                  mock.patch.object(reconcile.remote, "hosts", lambda: []),
                  mock.patch.object(routecore, "ROUTES", os.path.join(self.base, "routes.json"))):
            p.start()
            self.addCleanup(p.stop)
        self.work, self.botsock = os.path.join(self.folder, "work"), os.path.join(self.folder, "bot")
        self.server(self.work, "st")
        self.server(self.botsock, "dc-b-0002")
        self.bot = Bot(self.botsock)
        self.a, self.b, self.c = Chan(1, "a"), Chan(2, "b"), Chan(3, "c")
        self.bot.chans = {1: self.a, 2: self.b, 3: self.c}
        pane, pid = self.pane_of(self.work, "st")
        self.bot.channels_state = {
            "1": {"pane": {"socket": self.work, "id": pane, "pid": pid}, "pane_session": "st", "cwd": "/w"},
            "2": {"tmux": "dc-b-0002", "cwd": "/w"},
            "3": {"pane": {"socket": self.work, "id": "%999", "pid": "1"}, "pane_session": "st", "cwd": "/w"}}
        self.looks = reconcile.Looks(self.bot)

    def server(self, sock, session):
        tmux(sock, "new-session", "-d", "-s", session, "-x", "80", "-y", "20", "sleep 300")
        self.addCleanup(tmux, sock, "kill-server")

    def pane_of(self, sock, session):
        out = tmux(sock, "list-panes", "-t", "=%s:" % session, "-F", "#{pane_id} #{pane_pid}").stdout.split()
        return out[0], out[1]

    def look(self):
        asyncio.run(self.looks.once())

    def seen(self, cid):
        return self.bot.channels_state[cid]["where"]["seen"]


class Look(Base):
    def test_the_first_look_tags_what_is_there_and_says_the_old_ones_once(self):
        self.look()
        self.assertEqual((self.seen("1")["state"], self.seen("2")["state"], self.seen("3")["state"]), ("here", "here", "gone"))
        self.assertEqual(tmux(self.work, "show-options", "-p", "-t", self.seen("1")["pane"], "-v", "@altention_channel").stdout.strip(), "1")
        self.assertEqual(tmux(self.botsock, "show-options", "-p", "-t", self.seen("2")["pane"], "-v", "@altention_channel").stdout.strip(), "2")
        self.assertEqual(self.bot.audits, ["[對帳] 第一次對帳：1 個頻道的紀錄指向已經不在的窗格：c（%999）（只記這一次，沒有在頻道說）",
                                           "[對帳] 補上路由：lap 的窗格 %s → #a（原本：沒有）" % self.seen("1")["pane"]])  # (2a)
        self.assertEqual((self.a.sent, self.b.sent, self.c.sent), ([], [], []), "nothing said in the channels")
        self.look()
        self.assertEqual(len(self.bot.audits), 2, "said once")

    def test_a_place_cleared_by_a_restart_is_no_move(self):
        """C-198 L3 drill 11-20, low: the pane restarted, its place cleared to None, then seen again: no 換了位置
        None:None → … line."""
        self.look()
        seen = self.bot.channels_state["1"]["where"]["seen"]
        seen["session"] = seen["window"] = None
        n = len(self.bot.audits)
        self.look()
        self.assertFalse([a for a in self.bot.audits[n:] if "換了位置" in a], self.bot.audits[n:])
        self.assertEqual(self.seen("1")["session"], "st", "seen again where it is")

    def test_renamed_and_moved_in_tmux_is_followed_by_its_tag(self):
        self.look()
        tmux(self.work, "rename-session", "-t", "st", "renamed")
        tmux(self.work, "rename-window", "-t", "renamed:0", "exp")
        self.look()
        seen = self.seen("1")
        self.assertEqual((seen["state"], seen["session"], seen["window"]), ("here", "renamed", "exp"))
        self.assertEqual(self.bot.channels_state["1"]["where"]["place"]["session"], "renamed", "the record follows")
        self.assertEqual(self.bot.audits[-1], "[對帳] #a 的窗格在 tmux 裡換了位置：st:0 → renamed:exp")
        self.assertEqual(self.bot.channels_state["1"]["pane_session"], "renamed",
                         "2a: the old field too (opening it again, !detach's record go by it)")

    def test_a_window_the_bot_opened_by_name_follows_its_session_and_window(self):
        pane, pid = self.pane_of(self.work, "st")
        tmux(self.work, "rename-window", "-t", "st:0", "w1")
        self.bot.channels_state["1"] = {"home": {"socket": self.work, "session": "st", "window": "w1"},
                                        "pane": {"socket": self.work, "id": pane, "pid": pid}, "cwd": "/w"}
        self.look()
        tmux(self.work, "rename-session", "-t", "st", "s2")
        self.look()
        self.assertEqual(self.bot.channels_state["1"]["home"], {"socket": self.work, "session": "s2", "window": "w1"})
        self.assertNotIn("pane_session", self.bot.channels_state["1"], "none written where there was none")
        tmux(self.work, "rename-window", "-t", "s2:0", "w2")
        self.look()
        self.assertEqual(self.bot.channels_state["1"]["home"]["window"], "w2")
        self.assertEqual(self.bot.channels_state["1"]["where"]["place"]["session"], "s2")
        tmux(self.work, "set-option", "-w", "-t", "s2:0", "automatic-rename", "on")  # (tmux names it again: its index seen)
        self.look()
        self.assertEqual(self.bot.channels_state["1"]["home"]["window"], "w2", "not the window's number")

    def test_a_window_opened_unnamed_keeps_no_name(self):
        """home.window "" (the session's first or a new window, tmux naming it after its program): not given the
        name tmux shows."""
        pane, pid = self.pane_of(self.work, "st")
        self.bot.channels_state["1"] = {"home": {"socket": self.work, "session": "st", "window": ""},
                                        "pane": {"socket": self.work, "id": pane, "pid": pid}, "cwd": "/w"}
        tmux(self.work, "rename-session", "-t", "st", "s2")
        self.look()
        self.assertEqual(self.bot.channels_state["1"]["home"], {"socket": self.work, "session": "s2", "window": ""})

    def test_a_pane_not_surely_its_own_changes_no_field(self):
        tmux(self.work, "rename-session", "-t", "st", "s2")
        self.bot.channels_state["1"]["pane"].pop("pid")  # (unsure: no process recorded, not tagged)
        self.look()
        self.assertEqual(self.seen("1")["state"], "unsure")
        self.assertEqual(self.bot.channels_state["1"]["pane_session"], "st")

    def test_the_topic_of_an_attached_channel_follows_once_in_ten_minutes(self):
        edits = []

        async def edit(**kw):
            edits.append(kw["topic"])
            self.a.topic = kw["topic"]
        self.a.edit = edit
        self.a.topic = "lap 上的 Claude Code：tmux st:0，/w"
        self.look()
        self.assertEqual(edits, [], "as it is")
        tmux(self.work, "rename-session", "-t", "st", "s2")
        self.look()
        self.assertEqual(edits, ["lap 上的 Claude Code：tmux s2:0，/w"])
        tmux(self.work, "rename-session", "-t", "s2", "s3")
        self.look()
        self.assertEqual(len(edits), 1, "not again within TOPIC_SECS")
        self.looks.topic_at["1"] -= reconcile.TOPIC_SECS
        self.look()
        self.assertEqual(edits[-1], "lap 上的 Claude Code：tmux s3:0，/w")
        self.a.topic = "my own words"
        self.looks.topic_at["1"] -= reconcile.TOPIC_SECS
        tmux(self.work, "rename-session", "-t", "s3", "s4")
        self.look()
        self.assertEqual(len(edits), 2, "a topic the owner wrote is left alone")

    def test_a_pane_that_goes_is_said_once_in_its_channel_and_once_back(self):
        self.look()
        pane = self.seen("1")["pane"]
        tmux(self.work, "new-window", "-d", "-t", "st", "sleep 300")  # (the server stays when the pane goes)
        tmux(self.work, "kill-pane", "-t", pane)
        self.look()
        self.assertEqual(self.seen("1")["state"], "gone")
        self.assertEqual(len(self.a.sent), 1)
        self.assertTrue(self.a.sent[0].startswith("⚠️ 這個頻道的窗格不見了（lap · %s），最後一次看到是 " % pane), self.a.sent)
        self.assertIn("/attach", self.a.sent[0], "an attached pane: how to take it back")
        self.assertEqual(self.bot.audits[-1], "[對帳] lap：1 個頻道的窗格不見了：a（%s）" % pane)
        self.look()
        self.assertEqual(len(self.a.sent), 1, "once")

    def test_a_whole_server_gone_is_one_audit_line(self):
        self.bot.channels_state["4"] = {"pane": {"socket": self.work, "id": self.pane_of(self.work, "st")[0]}, "cwd": "/w"}
        self.bot.chans[4] = Chan(4, "d")
        tmux(self.work, "new-window", "-d", "-t", "st", "sleep 300")
        self.look()
        tmux(self.work, "kill-server")
        self.look()
        self.assertEqual(self.bot.audits[-1].split("：")[0:2], ["[對帳] lap", "2 個頻道的窗格不見了"])
        self.assertEqual((len(self.a.sent), len(self.bot.chans[4].sent)), (1, 1), "each channel said once")

    def test_its_number_now_another_process_is_gone(self):
        """The pane's number taken by another process (tmux numbers again after a restart): not it, and not tagged."""
        pane, pid = self.pane_of(self.work, "st")
        self.bot.channels_state["1"]["pane"]["pid"] = str(int(pid) + 100000)
        self.look()
        self.assertEqual(self.seen("1")["state"], "gone")
        self.assertEqual(tmux(self.work, "show-options", "-p", "-t", pane, "-v", "@altention_channel").stdout.strip(), "")

    def test_a_session_of_the_bots_own_not_there_is_written_not_said(self):
        self.look()
        tmux(self.botsock, "kill-server")
        self.look()
        self.assertEqual(self.seen("2")["state"], "closed")
        self.assertEqual(self.b.sent, [], "the bot opens it again with the next message")

    def test_a_pane_attached_to_another_channel_since_is_that_ones(self):
        """#code-review-2 on 265dc930: a pane tagged for #a, detached and attached to #h (same number, same process):
        #h's, its tag rewritten; nobody's pane gone."""
        self.look()
        pane, pid = self.pane_of(self.work, "st")
        st = self.bot.channels_state.pop("1")
        self.bot.channels_state["8"] = dict(st, where={})
        self.bot.chans[8] = Chan(8, "h")
        self.look()
        self.assertEqual(self.seen("8")["state"], "here")
        self.assertEqual(tmux(self.work, "show-options", "-p", "-t", pane, "-v", "@altention_channel").stdout.strip(), "8")
        self.assertEqual((self.a.sent, self.bot.chans[8].sent), ([], []))
        self.assertFalse(any("不見了" in a for a in self.bot.audits[1:]), self.bot.audits)

    def test_a_tag_whose_channel_points_elsewhere_is_taken_away(self):
        """manager 11-17: a stale tag is taken away and said in #audit; it makes nobody's pane gone."""
        self.look()
        tmux(self.work, "new-window", "-d", "-t", "st", "sleep 300")
        other = tmux(self.work, "list-panes", "-a", "-F", "#{pane_id}").stdout.split()[-1]
        tmux(self.work, "set-option", "-p", "-t", other, "@altention_channel", "1")
        tmux(self.work, "set-option", "-p", "-t", self.pane_of(self.work, "st")[0], "@altention_channel", "1")
        self.bot.channels_state["9"] = {"cwd": "/w"}  # (no pane: a tag for it is stale too)
        tmux(self.work, "new-window", "-d", "-t", "st", "sleep 300")
        third = tmux(self.work, "list-panes", "-a", "-F", "#{pane_id}").stdout.split()[-1]
        tmux(self.work, "set-option", "-p", "-t", third, "@altention_channel", "9")
        self.look()
        self.assertEqual(self.seen("1")["state"], "here")
        for p in (other, third):
            self.assertEqual(tmux(self.work, "show-options", "-p", "-t", p, "-v", "@altention_channel").stdout.strip(), "", p)
        self.assertTrue(self.bot.audits[-1].startswith("[對帳] 拿掉 2 個舊的頻道標記"), self.bot.audits[-1])
        self.assertEqual(self.a.sent, [])

    def test_no_process_recorded_is_unsure_and_never_tagged_by_its_number(self):
        """#code-review-2 on 265dc930, manager 11-17: a record with no process: the pane at that number may be
        another's (tmux numbers again after a restart): unsure, not tagged; tagged already for it (at its attach): here."""
        pane = self.pane_of(self.work, "st")[0]
        self.bot.channels_state["1"]["pane"].pop("pid")
        self.look()
        self.assertEqual(self.seen("1")["state"], "unsure")
        self.assertEqual(tmux(self.work, "show-options", "-p", "-t", pane, "-v", "@altention_channel").stdout.strip(), "")
        tmux(self.work, "set-option", "-p", "-t", pane, "@altention_channel", "1")
        self.look()
        self.assertEqual(self.seen("1")["state"], "here")


class Respawned(Base):
    """C-198 (manager 11-18): a pane respawned (respawn-pane: its number and tag kept, its process new) is not gone
    at once: the bridge's pane_again() tells (the conversation when it can be read, else the tag), asked once for
    that process; not followed, it is gone from the next look on; not known yet (None), asked again."""
    def setUp(self):
        super().setUp()
        self.asked, self.follows = [], True
        test = self

        class Bridge(object):
            async def pane_again(self, ch, st):
                test.asked.append(ch.id)
                if test.follows:
                    st["pane"] = dict(st["pane"], pid=test.pane_of(test.work, "st")[1])
                return test.follows
        self.bot.tmux = Bridge()

    def respawn(self):
        pane, pid = self.pane_of(self.work, "st")
        tmux(self.work, "respawn-pane", "-k", "-t", pane, "sleep 300")
        self.assertNotEqual(self.pane_of(self.work, "st"), (pane, pid))
        return pane

    def test_followed_by_the_bridge_is_here_and_nothing_said(self):
        self.look()
        pane = self.respawn()
        self.assertEqual(tmux(self.work, "show-options", "-p", "-t", pane, "-v", "@altention_channel").stdout.strip(), "1",
                         "the tag kept through respawn-pane")
        self.look()
        self.assertEqual(self.asked, [1])
        self.assertEqual(self.seen("1")["state"], "respawned")
        self.assertEqual(self.a.sent, [], "not said gone")
        self.look()
        self.assertEqual(self.seen("1")["state"], "here")
        self.assertEqual(self.asked, [1], "asked once")

    def test_not_followed_is_gone_from_the_next_look_and_not_asked_again(self):
        self.follows = False
        self.look()
        self.respawn()
        self.look()
        self.look()
        self.assertEqual(self.seen("1")["state"], "gone")
        self.assertEqual(self.asked, [1])
        self.assertEqual(len(self.a.sent), 1, "said gone once")
        self.look()
        self.assertEqual(self.asked, [1])

    def test_not_known_is_asked_again_then_gone_after_some_looks(self):
        """Its record not read (None): asked again at the next look, not said gone; UNKNOWN_LOOKS in a row, gone
        (said once): not "being checked" for ever (manager 11-18)."""
        self.follows = None
        self.look()
        self.respawn()
        for _ in range(reconcile.UNKNOWN_LOOKS):
            self.look()
            self.assertEqual((self.seen("1")["state"], self.a.sent), ("respawned", []))
        self.assertEqual(self.asked, [1] * reconcile.UNKNOWN_LOOKS)
        self.look()
        self.look()
        self.assertEqual(self.seen("1")["state"], "gone")
        self.assertEqual((len(self.asked), len(self.a.sent)), (reconcile.UNKNOWN_LOOKS, 1), "not asked again; said once")

    def test_not_known_then_followed_starts_its_count_over(self):
        self.follows = None
        self.look()
        self.respawn()
        self.look()
        self.follows = True
        self.look()
        self.look()
        self.assertEqual(self.seen("1")["state"], "here")
        self.assertEqual(self.looks.not_known, {})

    def test_no_tag_of_its_own_is_gone_and_not_asked(self):
        self.look()
        pane = self.respawn()
        tmux(self.work, "set-option", "-p", "-t", pane, "-u", "@altention_channel")
        self.look()
        self.assertEqual(self.seen("1")["state"], "gone")
        self.assertEqual(self.asked, [])


class RealBridge(Base):
    """C-198 (#code-review-2 on b6420e2d, manager 11-18): nothing mocked between the look and the bridge: a real tmux
    pane respawned as a small adapter that marks it (@altention_agent "<agent>|<its pid>") is taken back, its route
    too; one with no mark, or with the mark its last process left, is gone."""
    def setUp(self):
        super().setUp()
        from bot import tmux_bridge
        test = self
        self.audits = []

        async def audit(text):
            test.audits.append(text)

        async def pane_session_id(pane):
            return None  # (no Claude Code record: an adapter writes none)
        self.bot.audit, self.bot.pane_session_id = audit, pane_session_id
        self.bot.tmux = tmux_bridge.TmuxBridge(self.bot)
        from bot import routes
        routes.bind(self.bot.channels_state["1"]["pane"], 1)

    def respawn(self, marked, exec_="exec "):
        pane, pid = self.pane_of(self.work, "st")
        script = ('tmux -S "%s" set-option -p -t "$TMUX_PANE" @altention_agent "pi|$$"; %ssleep 300%s'
                  % (self.work, exec_, "" if exec_ else "; :") if marked else "sleep 300")
        tmux(self.work, "respawn-pane", "-k", "-t", pane, script)
        new = self.pane_of(self.work, "st")[1]
        self.assertNotEqual(new, pid)
        for _ in range(100):
            mark = tmux(self.work, "show-options", "-p", "-t", pane, "-v", "@altention_agent").stdout.strip()
            if not marked or mark == "pi|%s" % new:
                break
            time.sleep(0.05)
        return pane, new

    def test_an_adapter_that_marks_its_pane_is_taken_back_with_its_route(self):
        self.look()
        pane, new = self.respawn(True)
        self.assertEqual(tmux(self.work, "show-options", "-p", "-t", pane, "-v", "@altention_agent").stdout.strip(), "pi|%s" % new)
        self.look()
        self.assertEqual(str(self.bot.channels_state["1"]["pane"]["pid"]), new)
        self.assertEqual(routecore.load()["%s|%s" % (self.work, pane)]["pid"], new, "the route takes its process too")
        self.assertEqual([t for t in self.a.sent if "窗格重開過" in (t or "")], ["-# 這個頻道的窗格重開過，已經接回同一個窗格"])
        self.assertTrue([t for t in self.audits if "換成 pi" in t])
        self.assertEqual(self.bot.channels_state["1"]["where"]["agent"]["kind"], "pi", "the adapter's name in the record (drill of d0f26f25, A)")
        self.look()
        self.assertEqual(self.seen("1")["state"], "here")
        st = self.bot.channels_state["1"]  # #code-review-2 on fffc1f4a: the next message is typed in, not "gone"
        self.assertEqual(asyncio.run(tmuxio.standing(st["pane"])), "alive", "a valid mark: alive() too (manager 11-18)")
        self.assertIsNone(asyncio.run(self.bot.tmux.ensure(self.a, st, None)))
        self.assertEqual(str(st["pane"]["pid"]), new)

    def gone_for_a_message(self):
        from bot import tmux_bridge
        st = self.bot.channels_state["1"]
        self.assertEqual(asyncio.run(tmuxio.standing(dict(st["pane"], pid=self.pane_of(self.work, "st")[1]))), "gone",
                         "not alive even with its own pid recorded")
        with mock.patch.object(tmux_bridge, "AGAIN_SECS", 0):
            with self.assertRaises(tmux_bridge.NotOpened) as e:
                asyncio.run(self.bot.tmux.ensure(self.a, st, None))
        self.assertEqual(e.exception.why, tmux_bridge.NotOpened.PANE_GONE)

    def test_no_mark_is_gone(self):
        self.look()
        self.respawn(False)
        self.gone_for_a_message()
        self.look()
        self.look()
        self.assertEqual(self.seen("1")["state"], "gone")
        self.assertFalse([t for t in self.a.sent if "窗格重開過" in (t or "")])

    def test_a_mark_naming_the_shell_does_not_count(self):
        """#code-review-2 low on 6d91c5b7 (manager): a wrapper that marks with its shell's $$ and does not exec: the
        shell would stay "an adapter" after the adapter ended. Not taken back; gone for a message."""
        self.look()
        pane, new = self.respawn(True, exec_="")  # ("; :" after: a shell runs its last command by exec itself)
        self.assertEqual(tmux(self.work, "show-options", "-p", "-t", pane, "-v", "@altention_agent").stdout.strip(), "pi|%s" % new)
        self.gone_for_a_message()
        self.look()
        self.look()
        self.assertEqual(self.seen("1")["state"], "gone")

    def test_the_mark_its_last_process_left_does_not_count(self):
        self.look()
        self.respawn(True)
        self.look()
        self.respawn(False)  # (the option stays through respawn-pane, naming the process before)
        self.gone_for_a_message()
        self.look()
        self.look()
        self.assertEqual(self.seen("1")["state"], "gone")


class CutShort(Base):
    """#code-review-2 low on 265dc930: what a look found is written and said after it, outside its time cap."""
    def test_a_look_cut_short_writes_nothing_and_says_nothing(self):
        self.look()
        pane = self.seen("1")["pane"]
        tmux(self.work, "new-window", "-d", "-t", "st", "sleep 300")
        tmux(self.work, "kill-pane", "-t", pane)
        real = self.looks.stale

        async def slow(*a):
            got = await real(*a)
            await asyncio.sleep(5)
            return got
        with mock.patch.object(reconcile, "LOOK_CAP", 0.5), mock.patch.object(self.looks, "stale", slow):
            self.look()
        self.assertEqual((self.seen("1")["state"], self.seen("1")["known"]), ("here", "here"), "nothing written")
        self.look()
        self.assertEqual(len(self.a.sent), 1, "said by the next look")


class FirstLookPerMachine(Base):
    """#code-review low on 265dc930, manager 11-17: a machine's own first look keeps its old records quiet; another
    machine known already does not make it say them, and one never reached till now does not keep the others quiet."""
    def test_per_machine(self):
        self.bot.channels_state["5"] = {"pane": {"host": "nv", "socket": "/tmp/tmux-7/default", "id": "%4", "pid": "9"},
                                        "cwd": "/w"}
        self.bot.chans[5] = Chan(5, "e")
        with mock.patch.object(reconcile.remote, "dcremote", mock.AsyncMock(return_value=(255, ""))):
            self.look()  # (lap known now; nv not reached)
        pane = self.seen("1")["pane"]
        tmux(self.work, "new-window", "-d", "-t", "st", "sleep 300")
        tmux(self.work, "kill-pane", "-t", pane)
        with mock.patch.object(reconcile.remote, "dcremote", mock.AsyncMock(return_value=(0, '{"/tmp/tmux-7/default": {"gone": true}}'))):
            self.look()
        self.assertEqual(len(self.a.sent), 1, "lap's pane gone after lap was known: said")
        self.assertEqual(self.bot.chans[5].sent, [], "nv's first look: its old record only in #audit")
        self.assertTrue(any(a.startswith("[對帳] 第一次對帳") and "e（%4）" in a for a in self.bot.audits), self.bot.audits)


class LeftFromBatchOne(Base):
    """C-201 2a: the first batch's low items (both reviews on 0fff85ee, manager 11-17)."""
    def test_a_binding_while_the_look_runs_is_neither_tagged_over_nor_taken_away(self):
        pane, pid = self.pane_of(self.work, "st")
        tmux(self.work, "new-window", "-d", "-t", "st", "sleep 300")
        other = tmux(self.work, "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}").stdout.split()[-2:]
        real = self.looks.machine
        tmux(self.work, "set-option", "-p", "-t", other[0], "@altention_channel", "1")  # (/attach tags it first...)

        async def machine(host, here):
            got = await real(host, here)  # (...the look sees it; then the record follows, while the look runs)
            self.bot.channels_state["1"]["pane"] = {"socket": self.work, "id": other[0], "pid": other[1]}
            return got
        with mock.patch.object(self.looks, "machine", machine):
            self.look()
        show = lambda p: tmux(self.work, "show-options", "-p", "-t", p, "-v", "@altention_channel").stdout.strip()  # noqa: E731
        self.assertEqual((show(pane), show(other[0])), ("", "1"), "the new binding's tag stays; the old pane not tagged")
        self.assertFalse(any("舊的頻道標記" in a for a in self.bot.audits), self.bot.audits)

    def test_a_window_the_bot_opened_keeps_its_tag_when_its_session_is_renamed(self):
        pane = self.pane_of(self.work, "st")[0]
        self.bot.channels_state["6"] = {"home": {"socket": self.work, "session": "st", "window": ""}, "cwd": "/w"}
        tmux(self.work, "set-option", "-p", "-t", pane, "@altention_channel", "6")
        self.bot.channels_state["1"]["pane"]["id"] = "%998"  # (#a elsewhere: the pane is #f's window)
        tmux(self.work, "rename-session", "-t", "st", "renamed")
        self.look()
        self.assertEqual(tmux(self.work, "show-options", "-p", "-t", pane, "-v", "@altention_channel").stdout.strip(), "6")

    def test_a_socket_written_another_way_is_the_same(self):
        """macOS writes /private/tmp/… for the bot's /tmp/…: the same socket (util.same_socket); here, a link."""
        link = os.path.join(self.base, "link")
        os.symlink(self.folder, link)
        self.bot.channels_state["1"]["pane"]["socket"] = os.path.join(link, "work")
        self.look()
        self.assertEqual(self.seen("1")["state"], "here")


class Repair(Base):
    """C-201 2a (manager 11-17, point 2): the route tables follow the records, on facts only, one #audit line each."""
    def table(self):
        return routecore.load()

    def test_a_pane_surely_its_routed_elsewhere_or_nowhere_is_bound(self):
        pane, pid = self.pane_of(self.work, "st")
        k = "%s|%s" % (self.work, pane)
        routecore.save({k: {"channel": "9", "pid": pid}})
        self.look()
        self.assertEqual(self.table()[k]["channel"], "1")
        self.assertIn("[對帳] 補上路由：lap 的窗格 %s → #a（原本：#9）" % pane, self.bot.audits)
        n = len(self.bot.audits)
        self.look()
        self.assertEqual(len(self.bot.audits), n, "once")

    def test_no_process_recorded_or_another_one_is_not_bound(self):
        pane, pid = self.pane_of(self.work, "st")
        self.bot.channels_state["1"]["pane"].pop("pid")
        self.look()
        self.assertEqual(self.table(), {})
        self.bot.channels_state["1"]["pane"]["pid"] = "1"  # (gone: another process in it)
        self.look()
        self.assertEqual(self.table(), {})
        self.assertFalse(any("路由" in a for a in self.bot.audits), self.bot.audits)

    def test_tagged_but_no_process_recorded_is_not_bound(self):
        """Its tag makes it surely the channel's for the look (here), but bound only with the recorded process."""
        pane, pid = self.pane_of(self.work, "st")
        tmux(self.work, "set-option", "-p", "-t", pane, "@altention_channel", "1")
        self.bot.channels_state["1"]["pane"].pop("pid")
        routecore.save({"%s|%%990" % self.work: {"channel": "7", "pid": "5"}})  # (gone: the rest of the look goes on)
        self.look()
        self.assertEqual(self.seen("1")["state"], "here")
        self.assertEqual(self.table(), {})

    def test_a_machine_whose_panes_were_not_read_keeps_its_routes(self):
        self.bot.channels_state["5"] = {"pane": {"host": "aa", "socket": "/tmp/tmux-7/default", "id": "%4", "pid": "44"},
                                        "pane_session": "x", "cwd": "/w"}
        calls = []

        async def dcremote(host, *args, **kw):
            calls.append(args[0])
            if args[0] == "routes":
                return 0, json.dumps({"/tmp/tmux-7/default|%6": {"channel": "2", "pid": "60"}})
            return 1, ""  # (its look: no answer)
        routecore.save({"%s|%%990" % self.work: {"channel": "7", "pid": "5"}})
        with mock.patch.object(reconcile.remote, "dcremote", dcremote):
            self.look()
        self.assertNotIn("unbind-key", calls)
        self.assertEqual(self.table().get("%s|%%990" % self.work), None, "this machine's repaired all the same")

    def test_a_window_the_bot_opened_here_needs_no_route(self):
        pane, pid = self.pane_of(self.work, "st")
        self.bot.channels_state["1"]["home"] = {"socket": self.work, "session": "st", "window": ""}
        self.look()
        self.assertEqual(self.table(), {})

    def test_a_route_to_a_pane_surely_not_its_is_taken_away(self):
        pane, pid = self.pane_of(self.work, "st")
        tmux(self.work, "new-window", "-d", "-t", "st", "sleep 300")
        other = tmux(self.work, "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}").stdout.split()[-2:]
        del self.bot.channels_state["3"]
        routecore.save({"%s|%%990" % self.work: {"channel": "7", "pid": "5"},          # gone
                        "%s|%s" % (self.work, other[0]): {"channel": "8", "pid": "5"},  # another process
                        "%s|%%991" % os.path.join(self.folder, "nosuch"): {"channel": "7", "pid": "5"},  # server not read
                        "%s|%%992" % self.work: {"channel": "7"},                       # no process: nothing to go by
                        "%s|%s" % (self.work, pane): {"channel": "1", "pid": pid}})      # the record's own
        self.look()
        self.assertEqual(sorted(self.table()), sorted(["%s|%%991" % os.path.join(self.folder, "nosuch"),
                                                       "%s|%%992" % self.work, "%s|%s" % (self.work, pane)]))
        self.assertIn("[對帳] 拿掉路由：lap 的窗格 %990（原本給 #7；窗格已經不在）", self.bot.audits)
        self.assertIn("[對帳] 拿掉路由：lap 的窗格 %s（原本給 #8；窗格裡換了別的程式）" % other[0], self.bot.audits)

    def test_tagged_for_another_channel_is_taken_away_its_records_own_never(self):
        tmux(self.work, "new-window", "-d", "-t", "st", "sleep 300")
        other = tmux(self.work, "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}").stdout.split()[-2:]
        self.bot.channels_state["3"]["pane"] = {"socket": self.work, "id": other[0], "pid": other[1]}  # (#c there)
        routecore.save({"%s|%s" % (self.work, other[0]): {"channel": "8", "pid": other[1]}})
        self.look()
        self.assertEqual(self.table()["%s|%s" % (self.work, other[0])]["channel"], "3", "bound to the record's channel")
        self.assertTrue(any("補上路由" in a and "原本：#8" in a for a in self.bot.audits), self.bot.audits)

    def test_another_machine_by_dcremote_and_one_not_known(self):
        self.bot.channels_state["5"] = {"pane": {"host": "nv", "socket": "/tmp/tmux-7/default", "id": "%4", "pid": "44"},
                                        "pane_session": "x", "cwd": "/w"}
        calls = []

        async def dcremote(host, *args, **kw):
            calls.append((host,) + args)
            if args[0] == "look":
                return 0, json.dumps({"/tmp/tmux-7/default": {"panes": [
                    {"id": "%4", "session": "x", "window": "0", "window_name": "", "pid": "44", "channel": "5"},
                    {"id": "%6", "session": "x", "window": "1", "window_name": "", "pid": "66", "channel": ""}]}})
            if args[0] == "routes":
                return 0, json.dumps({"/tmp/tmux-7/default|%6": {"channel": "2", "pid": "60"}})
            if args[0] == "unbind-key":
                return 0, '{"unbound": true}'
            return 0, ""
        with mock.patch.object(reconcile.remote, "dcremote", dcremote):  # (routes.bind_any's too: the same module)
            self.look()
        self.assertIn(("nv", "bind", "/tmp/tmux-7/default|%4", "5", "44"), calls)
        self.assertIn(("nv", "unbind-key", "/tmp/tmux-7/default|%6", "2"), calls)
        self.assertTrue(any("補上路由：nv 的窗格 %4 → #5（原本：沒有）" in a for a in self.bot.audits), self.bot.audits)
        calls.clear()

        async def old(host, *args, **kw):  # (an older dcremote: no routes)
            calls.append((host,) + args)
            return (1, "") if args[0] == "routes" else await dcremote(host, *args, **kw)
        with mock.patch.object(reconcile.remote, "dcremote", old):
            self.look()
        self.assertEqual([c[1] for c in calls if c[1] not in ("look", "routes")], [], "not known: nothing done there")


class DetachMeanwhile(Base):
    """#code-review-2 on 1a4cad50: a !detach letting go of the route (routes.unbind_any(), then the tag taken away
    over awaits, then the archive record) while a look read the tables: the route was bound again and, the pane still
    there with its process and no tag, never taken away. Now a route to a channel that records no pane goes."""
    def test_a_route_bound_again_meanwhile_goes_once_the_channel_has_no_pane(self):
        pane, pid = self.pane_of(self.work, "st")
        k = "%s|%s" % (self.work, pane)
        self.look()
        self.assertEqual(routecore.load()[k]["channel"], "1")
        real = self.looks.routes_of

        async def detaching(host, here):
            routecore.unbind("1")  # (detach's first step, as the look reads the tables)
            return await real(host, here)
        with mock.patch.object(self.looks, "routes_of", detaching):
            self.look()
        self.assertEqual(routecore.load()[k]["channel"], "1", "bound again by the look (the record still had it)")
        tmux(self.work, "set-option", "-p", "-u", "-t", pane, "@altention_channel")
        self.bot.channels_state["1"] = {"cwd": "/w", "archived": {"why": "detached"}}  # (detach's last step)
        self.look()
        self.assertNotIn(k, routecore.load())
        self.assertIn("[對帳] 拿掉路由：lap 的窗格 %s（原本給 #a；那個頻道現在沒有接任何窗格）" % pane, self.bot.audits)
        self.look()
        self.assertEqual(sum("拿掉路由" in a for a in self.bot.audits), 1)

    def test_on_another_machine_with_no_other_channel_too(self):
        """#code-review-2 on d4ca3f00: the record let go of, no look went to that machine again and its table was
        never read. repair() reads the tables of the machines it bound on again till nothing there goes to a channel
        with no pane (manager 11-18: those machines only)."""
        self.bot.channels_state["5"] = {"pane": {"host": "aa", "socket": "/tmp/tmux-7/default", "id": "%4", "pid": "44"},
                                        "pane_session": "x", "cwd": "/w"}
        table, calls, first = {"/tmp/tmux-7/default|%4": {"channel": "5", "pid": "44"}}, [], {"read": True}

        async def dcremote(host, *args, **kw):
            calls.append((host,) + args)
            if args[0] == "look":
                return 0, json.dumps({"/tmp/tmux-7/default": {"panes": [
                    {"id": "%4", "session": "x", "window": "0", "window_name": "", "pid": "44", "channel": ""}]}})
            if args[0] == "routes":
                if first.pop("read", None):
                    table.clear()  # (detach's routes.unbind_any() as the look reads the table)
                return 0, json.dumps(table)
            if args[0] == "bind":
                table[args[1]] = {"channel": args[2], "pid": args[3]}
            if args[0] == "unbind-key":
                gone = table.get(args[1], {}).get("channel") == args[2] and table.pop(args[1])
                return 0, json.dumps({"unbound": bool(gone)})
            return 0, ""
        with mock.patch.object(reconcile.remote, "dcremote", dcremote):
            self.look()
            self.assertIn("[對帳] 補上路由：aa 的窗格 %4 → #5（原本：沒有）", self.bot.audits)
            self.bot.channels_state["5"] = {"cwd": "/w", "archived": {"why": "detached"}}  # (detach's last step)
            calls.clear()
            self.look()
            self.assertEqual(table, {})
            self.assertIn("[對帳] 拿掉路由：aa 的窗格 %4（原本給 #5；那個頻道現在沒有接任何窗格）", self.bot.audits)
            calls.clear()
            self.look()
        self.assertEqual([c for c in calls if c[0] == "aa"], [], "nothing left there: not read again")

    def test_a_route_not_taken_away_there_is_read_again(self):
        """#code-review on 770e0845 (low): unbind-key failing (no answer, or unbound false) keeps that machine read."""
        self.bot.channels_state["5"] = {"cwd": "/w", "archived": {"why": "detached"}}
        self.looks.route_hosts = {"aa"}  # (bound there at the look before)
        table, calls, answers = {"/tmp/tmux-7/default|%4": {"channel": "5", "pid": "44"}}, [], [(1, ""), (0, '{"unbound": false}')]

        async def dcremote(host, *args, **kw):
            calls.append((host,) + args)
            if args[0] == "routes":
                return 0, json.dumps(table)
            if args[0] == "unbind-key":
                if answers:
                    return answers.pop(0)
                table.pop(args[1], None)
                return 0, '{"unbound": true}'
            return 1, ""
        with mock.patch.object(reconcile.remote, "dcremote", dcremote):
            for _ in range(3):
                self.look()
                self.assertEqual(self.looks.route_hosts, {"aa"} if answers or table else set())
        self.assertEqual(table, {})
        self.assertEqual(sum(1 for c in calls if c[1] == "unbind-key"), 3, "tried again at each look till it went")
        self.assertEqual(sum("拿掉路由" in a for a in self.bot.audits), 1)

    def test_a_window_the_bot_opens_whose_pane_is_not_known_yet_keeps_its_route(self):
        pane, pid = self.pane_of(self.work, "st")
        k = "%s|%s" % (self.work, pane)
        routecore.save({k: {"channel": "2", "pid": pid}})  # ("2": tmux only, its pane not known yet)
        self.bot.channels_state["4"] = {"home": {"host": "aa", "socket": "/s", "session": "s", "window": ""}, "cwd": "/w"}
        routecore.save(dict(routecore.load(), **{"%s|%%990" % self.work: {"channel": "4", "pid": "5"}}))
        del self.bot.channels_state["1"]
        self.look()
        self.assertEqual(routecore.load()[k]["channel"], "2")
        self.assertFalse(any("那個頻道現在沒有接任何窗格" in a for a in self.bot.audits), self.bot.audits)

    def test_a_channel_not_in_the_state_is_not_known(self):
        routecore.save({"%s|%%991" % os.path.join(self.folder, "nosuch"): {"channel": "77", "pid": "5"}})
        self.look()
        self.assertIn("%s|%%991" % os.path.join(self.folder, "nosuch"), routecore.load())


class Shared(Base):
    """#code-review on 1a4cad50: two channels' records pointing to one pane: each look routed (and tagged) it for one
    and then the other, two #audit lines a look. Now neither is routed nor tagged by the look, said once."""
    def test_two_records_on_one_pane_change_nothing_and_are_said_once(self):
        pane, pid = self.pane_of(self.work, "st")
        st = self.bot.channels_state
        st["3"] = {"pane": dict(st["1"]["pane"]), "pane_session": "st", "cwd": "/w"}
        self.look()
        self.look()
        self.assertEqual(routecore.load(), {})
        self.assertEqual(tmux(self.work, "show-options", "-p", "-t", pane, "-v", "@altention_channel").stdout.strip(), "")
        said = [a for a in self.bot.audits if "好幾個頻道" in a]
        self.assertEqual(said, ["[對帳] lap 的窗格 %s 有好幾個頻道的紀錄都指著它：#a、#c。它的路由和標記先不動，"
                                "請只留一個頻道接著它（其他的用 !detach 卸下）" % pane])
        self.assertFalse(any("補上路由" in a for a in self.bot.audits))
        n = len(self.bot.audits)
        self.look()
        self.assertEqual(len(self.bot.audits), n)
        del st["3"]  # (one let go of it: the other is routed and tagged)
        self.look()
        self.assertEqual(routecore.load()["%s|%s" % (self.work, pane)]["channel"], "1")
        st["3"] = {"pane": dict(st["1"]["pane"]), "pane_session": "st", "cwd": "/w"}
        self.look()
        self.assertEqual(sum("好幾個頻道" in a for a in self.bot.audits), 2, "said again when it comes back")
        self.assertEqual(routecore.load()["%s|%s" % (self.work, pane)]["channel"], "1", "the route already there kept")


class SharedSpelledOtherwise(Base):
    """#code-review on d4ca3f00 (low), manager 11-18: one socket written two ways (a link, macOS's /private) is one
    pane shared, as util.same_socket() has it."""
    def test_macos_private_tmp_is_the_same_socket(self):
        recs = {"1": {"place": {"machine": "mac", "socket": "/private/tmp/tmux-501/default", "pane": "%4"}},
                "2": {"place": {"machine": "mac", "socket": "/tmp/tmux-501/default", "pane": "%4"}},
                "3": {"place": {"machine": "nv", "socket": "/tmp/tmux-501/default", "pane": "%4"}}}
        shared = reconcile.Looks.sharing(recs, "lap")
        self.assertEqual(shared, {("mac", "/tmp/tmux-501/default", "%4"): ["1", "2"]})
        for sock in ("/private/tmp/tmux-501/default", "/tmp/tmux-501/default"):
            self.assertIn(reconcile.shared_key("mac", sock, "%4", "lap"), shared)
        self.assertNotIn(reconcile.shared_key("nv", "/tmp/tmux-501/default", "%4", "lap"), shared)

    def test_a_link_to_this_machines_socket_is_the_same_socket(self):
        pane, pid = self.pane_of(self.work, "st")
        link = os.path.join(self.base, "work-link")
        os.symlink(self.work, link)
        st = self.bot.channels_state
        st["3"] = {"pane": dict(st["1"]["pane"], socket=link), "pane_session": "st", "cwd": "/w"}
        self.look()
        self.look()
        self.assertEqual(routecore.load(), {})
        self.assertEqual(sum("好幾個頻道" in a for a in self.bot.audits), 1, self.bot.audits)


class StampAfterALook(Base):
    def test_a_window_named_in_tmux_is_no_place_written_past_put(self):
        """#code-review-2 on 1a4cad50 (low): the look merged seen into the place without making it again: the next
        save's stamp() said every attached channel's place was written past where.put()."""
        tmux(self.work, "rename-window", "-t", "st:0", "notes")
        self.look()
        self.look()
        self.assertEqual(self.bot.logged, [], "the saves of the look itself")
        said = []
        where.stamp(self.bot.channels_state, self.bot.channel_name_of, "lap", self.botsock, say=said.append)
        self.assertEqual(said, [])
        self.assertEqual(self.bot.channels_state["1"]["where"]["place"].get("window"), "notes")


class LeaderLost(Base):
    def test_a_failback_while_topics_waited_repairs_nothing(self):
        """#code-review-2 on 1a4cad50 (low): leader_ok() asked again at repair()."""
        lead = {"ok": True}
        self.bot.lease, self.bot.leader_ok = object(), lambda: lead["ok"]

        async def topics():
            lead["ok"] = False
        with mock.patch.object(self.looks, "topics", topics):
            self.look()
        self.assertEqual(routecore.load(), {})
        self.assertFalse(any("路由" in a for a in self.bot.audits))


class FollowsTheBinding(Base):
    """manager 11-17: routes.bind_any() tags the pane, unbind_any() takes the tag away while it still says the channel."""
    def test_bound_and_unbound(self):
        from bot import routes
        pane, pid = self.pane_of(self.work, "st")
        with mock.patch.object(routes, "bind"), mock.patch.object(routes, "unbind"):
            asyncio.run(routes.bind_any({"socket": self.work, "id": pane, "pid": pid}, 5))
            show = lambda: tmux(self.work, "show-options", "-p", "-t", pane, "-v", "@altention_channel").stdout.strip()  # noqa: E731
            self.assertEqual(show(), "5")
            asyncio.run(routes.unbind_any(6, None, {"socket": self.work, "id": pane}))
            self.assertEqual(show(), "5", "tagged for another channel since: left alone")
            asyncio.run(routes.unbind_any(5, None, {"socket": self.work, "id": pane}))
            self.assertEqual(show(), "")
            asyncio.run(routes.unbind_any(5, None, {"socket": os.path.join(self.folder, "none"), "id": "%1"}))  # (never in the way)


class NotKnown(Base):
    """manager 11-17: not reached, no answer in time, a server that cannot be read: not known, never gone."""
    def test_a_machine_not_reached(self):
        self.bot.channels_state = {"5": {"pane": {"host": "nv", "socket": "/tmp/tmux-7/default", "id": "%4", "pid": "9"},
                                         "cwd": "/w", "where": {"seen": {"state": "here", "known": "here", "at": 1}}}}
        self.bot.chans = {5: Chan(5, "e")}
        for answer in ((255, ""), (0, "not json"), (2, '{"error": "unknown command"}'), (0, '{"error": "Permission denied"}')):
            with mock.patch.object(reconcile.remote, "dcremote", mock.AsyncMock(return_value=answer)):
                self.look()
            seen = self.seen("5")
            self.assertEqual((seen["state"], seen["known"]), ("unknown", "here"), answer)
        self.assertEqual((self.bot.chans[5].sent, self.bot.audits), ([], []))

    def test_another_machine_answering(self):
        self.bot.channels_state = {"5": {"pane": {"host": "nv", "socket": "/tmp/tmux-7/default", "id": "%4", "pid": "9"},
                                         "cwd": "/w", "where": {"seen": {"state": "here", "known": "here", "at": 1}}}}
        self.bot.chans = {5: Chan(5, "e")}
        answer = {"/tmp/tmux-7/default": {"panes": [{"id": "%4", "session": "w", "window": "1", "window_name": "",
                                                     "pid": "9", "channel": ""}]}}
        tags = []
        with mock.patch.object(reconcile.remote, "dcremote", mock.AsyncMock(return_value=(0, json.dumps(answer)))), \
                mock.patch.object(reconcile.tmuxio, "tag", mock.AsyncMock(side_effect=lambda *a, **k: tags.append((a, k)) or True)):
            self.look()
        self.assertEqual(self.seen("5")["state"], "here")
        self.assertEqual(tags, [(("/tmp/tmux-7/default", "%4", "5"), {"host": "nv"})], "tagged over there")
        answer["/tmp/tmux-7/default"] = {"error": "permission denied"}
        with mock.patch.object(reconcile.remote, "dcremote", mock.AsyncMock(return_value=(0, json.dumps(answer)))):
            self.look()
        self.assertEqual(self.seen("5")["state"], "unknown", "a server that cannot be read")
        answer["/tmp/tmux-7/default"] = {"gone": True}
        with mock.patch.object(reconcile.remote, "dcremote", mock.AsyncMock(return_value=(0, json.dumps(answer)))):
            self.look()
        self.assertEqual(self.seen("5")["state"], "gone", "its server plainly not there")
        self.assertEqual(len(self.bot.chans[5].sent), 1)

    def test_a_local_server_that_cannot_be_read(self):
        self.look()
        with mock.patch.object(reconcile.tmuxio, "look_sockets", mock.AsyncMock(return_value={self.work: {"error": "x"}, self.botsock: {"error": "x"}})):
            self.look()
        self.assertEqual((self.seen("1")["state"], self.seen("1")["known"]), ("unknown", "here"))
        with mock.patch.object(reconcile.tmuxio, "look_sockets", mock.AsyncMock(return_value={"error": "folder"})):
            self.look()
        self.assertEqual(self.seen("1")["state"], "unknown")
        self.assertEqual(self.a.sent, [])


class Bounded(Base):
    def test_a_look_past_its_time_is_dropped_and_the_next_comes(self):
        async def slow():
            await asyncio.sleep(5)
        with mock.patch.object(reconcile, "LOOK_CAP", 0.05), mock.patch.object(self.looks, "look", slow):
            t = time.time()
            self.look()
        self.assertLess(time.time() - t, 2)
        self.assertFalse(self.looks.busy)

    def test_a_standby_not_leading_looks_at_nothing(self):
        self.bot.lease = object()
        self.bot.leader_ok = lambda: False
        self.look()
        self.assertNotIn("where", self.bot.channels_state["1"])


class Remote(Base):
    def test_dcremote_look_as_the_script(self):
        tmux(self.work, "set-option", "-p", "-t", self.pane_of(self.work, "st")[0], "@altention_channel", "7")
        env = dict(os.environ, TMUX_TMPDIR=self.base)
        r = subprocess.run([sys.executable, os.path.join(HERE, "tools", "dcremote"), "look"], env=env,
                           stdout=subprocess.PIPE, universal_newlines=True, timeout=30)
        got = json.loads(r.stdout)
        self.assertEqual([p["channel"] for p in got[self.work]["panes"]], ["7"])
        self.assertEqual([p["session"] for p in got[self.botsock]["panes"]], ["dc-b-0002"])


    def test_dcremote_routes_and_unbind_key_as_the_script(self):
        """C-201 2a: on a throwaway copy of the engine (its route table under the copy's dcbot/state)."""
        engine = os.path.dirname(HERE)
        copy = os.path.join(self.base, "eng")
        shutil.copytree(os.path.join(HERE, "bot"), os.path.join(copy, "dcbot", "bot"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(copy, "dcbot", "tools"))
        shutil.copy(os.path.join(HERE, "tools", "dcremote"), os.path.join(copy, "dcbot", "tools", "dcremote"))
        os.makedirs(os.path.join(copy, "altention"))
        shutil.copy(os.path.join(engine, "altention", "__init__.py"), os.path.join(copy, "altention", "__init__.py"))
        shutil.copytree(os.path.join(engine, "altention", "connectors"), os.path.join(copy, "altention", "connectors"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(os.path.join(copy, "dcbot", "state"))
        table = os.path.join(copy, "dcbot", "state", "routes.json")
        with open(table, "w") as f:
            json.dump({"/s|%1": {"channel": "7", "pid": "5"}, "/s|%2": {"channel": "8", "pid": "6"}}, f)
        env = dict(os.environ, TMUX_TMPDIR=self.base, DCBOT_ENV=os.path.join(self.base, "no-settings"))

        def says(*args):
            r = subprocess.run(["/usr/bin/python3", os.path.join(copy, "dcbot", "tools", "dcremote")] + list(args), env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)
        self.assertEqual(sorted(says("routes")), ["/s|%1", "/s|%2"])
        self.assertEqual(says("unbind-key", "/s|%1", "8"), {"unbound": False}, "not that channel's: left")
        self.assertEqual(says("unbind-key", "/s|%1", "7"), {"unbound": True})
        self.assertEqual(sorted(says("routes")), ["/s|%2"])

if __name__ == "__main__":
    unittest.main()
