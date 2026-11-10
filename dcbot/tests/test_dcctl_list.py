"""C-87: the server's channel list, read from Discord once a minute for every dcctl on the machine (a file, a lock),
read again when a name or ID is not in it; a post or notification given by ID reads no list at all. C-80: two
dcctl making one channel or category at once make it once."""
import fcntl
import importlib.machinery
import importlib.util
import io
import json
import multiprocessing
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

from bot import postbox
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = "/guilds/42/channels"


def dcctl():
    loader = importlib.machinery.SourceFileLoader("dcctl_list", os.path.join(ROOT, "tools", "dcctl"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_list", loader))
    loader.exec_module(mod)
    return mod


def chan(i, name, kind=0, parent=None):
    return {"id": str(i), "name": name, "type": kind, "parent_id": parent, "position": i}


SERVER = [chan(1, "project-a", 4), chan(2, "notes", 0, "1"), chan(3, "audit")]


class Base(unittest.TestCase):
    def setUp(self):
        self.d = dcctl()
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        self.state = state.name
        self.server = [dict(c) for c in SERVER]
        self.calls = []
        for p in (mock.patch.object(self.d.config, "STATE", self.state), mock.patch.object(self.d.config, "GUILD_ID", 42),
                  mock.patch.object(self.d, "call", self.call), mock.patch.object(self.d, "audit", lambda text: None)):
            p.start()
            self.addCleanup(p.stop)

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if (method, path) == ("GET", LIST):
            return [dict(c) for c in self.server]
        if method == "POST":
            c = chan(100 + len(self.server), body["name"], body.get("type", 0), body.get("parent_id"))
            self.server.append(c)
            return c
        return {}

    def gets(self):
        return sum(1 for c in self.calls if c == ("GET", LIST))

    def post(self, post):
        written, out = [], io.StringIO()
        with mock.patch.object(postbox, "write", written.append), mock.patch.object(postbox, "heartbeat_age", lambda: 1), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(post))), mock.patch("sys.stdout", out):
            self.d.main(["post"])
        return written, out.getvalue()


class SharedList(Base):
    def test_read_once_a_minute(self):
        self.assertEqual(self.d.channels(), SERVER)
        self.assertEqual(self.d.channels(), SERVER)
        self.assertEqual(self.gets(), 1, "the second is the file's")
        with open(self.d.list_file()) as f:
            saved = json.load(f)
        saved["at"] -= self.d.LIST_SECS + 1
        with open(self.d.list_file(), "w") as f:
            json.dump(saved, f)
        self.d.channels()
        self.assertEqual(self.gets(), 2, "older than a minute: read again")

    def test_channels_fresh_reads_discord_now(self):
        """setup.py waiting for a channel the bot is making (B5 of v0.5.2): `dcctl channels --fresh` reads the
        server now; plain `dcctl channels` gives the minute's list, without the new channel."""
        self.d.channels()
        self.server.append(chan(7, "secretary"))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.d.main(["channels"])
        self.assertNotIn("#secretary", out.getvalue())
        self.assertEqual(self.gets(), 1)
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.d.main(["channels", "--fresh"])
        self.assertIn("#secretary", out.getvalue())
        self.assertEqual(self.gets(), 2)
        self.assertIn("secretary", [c["name"] for c in self.d.channels()], "and the list kept is the new one")
        self.assertEqual(self.gets(), 2)

    def test_another_servers_or_a_broken_file_is_not_taken(self):
        with open(self.d.list_file(), "w") as f:
            json.dump({"at": time.time(), "guild": 7, "channels": [chan(9, "elsewhere")]}, f)
        self.assertEqual(self.d.channels(), SERVER)
        for bad in ("{", "[]", json.dumps({"at": "x", "guild": 42, "channels": []}),
                    json.dumps({"at": time.time() + 3600, "guild": 42, "channels": []}),
                    json.dumps({"at": time.time(), "guild": 42, "channels": "no"})):
            with open(self.d.list_file(), "w") as f:
                f.write(bad)
            self.assertEqual(self.d.channels(), SERVER, bad)
        self.assertEqual(self.gets(), 6)

    def test_a_name_not_in_the_cached_list_is_looked_for_in_a_fresh_one(self):
        self.d.channels()
        self.server.append(chan(4, "made-since"))  # made by the bot a moment ago
        written, said = self.post({"channel": "made-since", "text": "x"})
        self.assertEqual(len(written), 1)
        self.assertEqual(self.gets(), 2)
        with self.assertRaises(SystemExit) as e:
            self.post({"channel": "no-such", "text": "x"})
        self.assertIn("找不到頻道 #no-such", str(e.exception.code))
        self.assertEqual(self.gets(), 3, "read again once, not more")
        self.post({"channel": "notes", "text": "x"})
        self.assertEqual(self.gets(), 3, "found in the file: no read")

    def test_an_id_not_in_the_cached_list_is_looked_for_in_a_fresh_one(self):
        self.d.channels()
        self.server.append(chan(5, "made-since"))
        self.assertEqual(self.d.find("5")["name"], "made-since")
        self.assertEqual(self.gets(), 2)

    def test_a_sender_channel_made_since_is_looked_for_in_a_fresh_list(self):
        """#code-review on 649e1c3: a notification without a channel goes to the sender's project's #notifications;
        a sender (or that #notifications) the bot made in the last minute was not in the file, and it went to the
        default without a word."""
        self.d.channels()
        self.server += [chan(6, "project-b", 4), chan(7, "notifications", 0, "6"), chan(8, "st-9", 0, "6")]
        with mock.patch.object(self.d, "notify_default", lambda: "notifications"):
            written, said = self.post({"text": "x", "notify": True, "from_channel": "8"})
        self.assertEqual(written[0]["channel"], "7")
        self.assertEqual(self.gets(), 2)
        with mock.patch.object(self.d, "notify_default", lambda: "notifications"):
            self.post({"text": "x", "notify": True, "from_channel": "8"})
        self.assertEqual(self.gets(), 2, "in the file now")

    def test_notifications_by_name_share_the_list(self):
        for _ in range(3):
            written, said = self.post({"channel": "notes", "text": "x", "notify": True})
            self.assertEqual(written[0]["channel"], "notes")
        self.assertEqual(self.gets(), 1)

    def test_a_change_made_here_is_read_fresh_next_time(self):
        self.d.channels()
        with mock.patch("sys.stdout", io.StringIO()):
            self.d.main(["rename", "notes", "notes-2"])
        self.assertFalse(os.path.exists(self.d.list_file()))
        self.server[1]["name"] = "notes-2"
        self.assertIn("notes-2", [c["name"] for c in self.d.channels()])


class ById(Base):
    """manager on C-87: a post or notification given an ID reads no list (the bot posts it, or says it found none)."""
    def test_post_and_notify_by_id_read_no_list(self):
        for post in ({"channel": "2", "text": "x"}, {"channel": "#2", "text": "x", "from_channel": "3"},
                     {"channel": "2", "text": "x", "notify": True}, {"channel": "999", "text": "x", "notify": True},
                     {"channel": "2", "text": "x", "notify": True, "from_channel": "3"}):
            written, said = self.post(post)
            self.assertEqual(len(written), 1, post)
        self.assertEqual(self.calls, [], "not one call to Discord")

    def test_the_reserved_targets_read_no_list(self):
        for post in ({"channel": "@audit", "text": "x"}, {"channel": "@notify", "text": "x", "notify": True},
                     {"channel": "#@audit", "text": "x", "notify": True, "from_channel": "3"},
                     {"channel": "@notify", "text": "x", "activity": "rigact/main"}):
            written, said = self.post(post)
            self.assertEqual(written[0]["channel"], post["channel"], "the bot finds it")
        self.assertEqual(self.calls, [])

    def test_the_reserved_targets_only_take_posts(self):
        for post in ({"channel": "@audit", "new_claude": "/tmp/x"}, {"channel": "@notify", "move_to": "系統"},
                     {"channel": "@audit", "archive": True}, {"channel": "@audit", "resume": "ab12"},
                     {"channel": "@notify", "attach": "s:0"}, {"channel": "@audit", "history": True}):
            with self.assertRaises(SystemExit, msg=post) as e:
                self.post(post)
            self.assertIn("只能用來貼訊息或通知", str(e.exception.code))
        self.assertEqual(self.calls, [])

    def test_a_notification_without_a_channel_or_by_name_still_reads_it(self):
        self.post({"text": "x", "notify": True})
        self.assertEqual(self.gets(), 1)


def read_list(d, out):
    d.channels()
    with open(out, "a") as f:
        f.write("done\n")


def ensure(d, name):
    with mock.patch("sys.stdout", io.StringIO()):
        d.main(["ensure", name])


def move(d, name, category):
    with mock.patch("sys.stdout", io.StringIO()):
        d.main(["move", name, category])


class OneAtATime(unittest.TestCase):
    """Separate dcctl processes (as `altention dc post` starts them), sharing one server held in a file."""
    def setUp(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        self.state = state.name
        self.served = os.path.join(self.state, "server.json")
        self.log = os.path.join(self.state, "calls")
        with open(self.served, "w") as f:
            json.dump(SERVER, f)
        self.d = dcctl()
        served, log = self.served, self.log

        def call(method, path, body=None):  # slow, as Discord: the others start meanwhile
            with open(log, "a") as f:
                f.write("%s %s\n" % (method, path))
            time.sleep(0.3)
            with open(served) as f:
                server = json.load(f)
            if method == "GET":
                return server
            if method == "POST":
                c = chan(100 + len(server), body["name"], body.get("type", 0), body.get("parent_id"))
                with open(served, "w") as f:
                    json.dump(server + [c], f)
                return c
            return {}
        for p in (mock.patch.object(self.d.config, "STATE", self.state), mock.patch.object(self.d.config, "GUILD_ID", 42),
                  mock.patch.object(self.d, "call", call), mock.patch.object(self.d, "audit", lambda text: None)):
            p.start()
            self.addCleanup(p.stop)

    def together(self, target, *args, n=4):
        ctx = multiprocessing.get_context("fork")
        procs = [ctx.Process(target=target, args=(self.d,) + args) for _ in range(n)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(30)
            self.assertEqual(p.exitcode, 0)
        with open(self.log) as f:
            return f.read().splitlines()

    def test_four_at_once_read_the_list_once(self):
        calls = self.together(read_list, os.path.join(self.state, "done"))
        self.assertEqual(calls, ["GET " + LIST])

    def test_two_ensures_at_once_make_one_channel(self):
        calls = self.together(ensure, "made-once", n=3)
        self.assertEqual([c for c in calls if c.startswith("POST")], ["POST " + LIST])

    def test_two_moves_into_a_new_category_make_it_once(self):
        calls = self.together(move, "notes", "project-new", n=3)
        self.assertEqual([c for c in calls if c.startswith("POST")], ["POST " + LIST], "one category made")
        with open(self.served) as f:
            self.assertEqual(sum(1 for c in json.load(f) if c["name"] == "project-new"), 1)

    def test_a_lock_held_too_long_is_not_waited_for_forever(self):
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, "channel-list.lock"), "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            ctx = multiprocessing.get_context("fork")
            with mock.patch.object(self.d, "LOCK_SECS", 0.5):
                p = ctx.Process(target=read_list, args=(self.d, os.path.join(self.state, "done")))
                began = time.time()
                p.start()
                p.join(30)
            self.assertEqual(p.exitcode, 0)
            self.assertLess(time.time() - began, 10)


if __name__ == "__main__":
    unittest.main()
