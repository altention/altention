"""The setup card of a new channel, and windows the bot opens in the owner's tmux."""
import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest

from bot import config, setup, tmuxio
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Card(unittest.TestCase):
    def test_configured(self):
        self.assertFalse(setup.configured({"cwd": "/x"}))
        for st in ({"plain": True}, {"tmux": "dc-x"}, {"pane": {"id": "%1"}}, {"home": {"session": "s"}}, {"session_id": "s"}, {"mode": "print"}, {"configured": True}):
            self.assertTrue(setup.configured(st), st)

    def test_card(self):
        self.assertEqual([b.custom_id for b in setup.card_view(42).children], ["setup:42:claude", "setup:42:term", "setup:42:files", "setup:42:plain"])
        self.assertEqual([b.custom_id for b in setup.where_view(42, False).children],
                         ["setup:42:new", "setup:42:add", "setup:42:default", "setup:42:browse", "setup:42:back"])
        self.assertEqual([b.custom_id for b in setup.where_view(42, True).children],
                         ["setup:42:tnew", "setup:42:tadd", "setup:42:tdefault", "setup:42:tbrowse", "setup:42:back"])
        self.assertIn("交給機器人管", setup.where_text("idea", "/tmp", False))
        self.assertEqual([b.custom_id for b in setup.where_view(42, "files").children],
                         ["setup:42:fbrowse", "setup:42:ftype", "setup:42:back"])
        self.assertIn("要看哪個資料夾", setup.where_text("idea", "/tmp", "files"))
        self.assertIn("已選工作目錄 `/srv/x`", setup.where_text("idea", "/tmp", False, folder="/srv/x"))
        self.assertIn("#idea 要拿來做什麼", setup.card_text("idea", os.path.expanduser("~/projects/p")))
        self.assertEqual(tmuxio.safe_session(" my.proj:1 "), "my-proj-1")


class FakeTmux(object):
    def __init__(self):
        self.submitted, self.started = [], []

    async def submit(self, message, text):
        self.submitted.append(text)

    async def start_now(self, ch):
        self.started.append(ch.id)


class FakeBot(object):
    def __init__(self):
        self.channels_state, self.tmux, self.audits = {}, FakeTmux(), []

    def chan(self, ch):
        return self.channels_state.setdefault(str(ch.id), {"cwd": "/tmp"})

    def save(self):
        pass

    async def audit(self, text):
        self.audits.append(text)


class FakeMsg(object):
    def __init__(self, mid):
        self.id, self.content, self.view = mid, "", None

    async def edit(self, content=None, view=None):
        self.content, self.view = content, view


class FakeChannel(object):
    id, name = 5, "idea"

    def __init__(self):
        self.sent = []

    async def send(self, text, view=None):
        self.sent.append(FakeMsg(len(self.sent)))
        self.sent[-1].content, self.sent[-1].view = text, view
        return self.sent[-1]

    def get_partial_message(self, mid):
        return self.sent[mid]


class Flow(unittest.TestCase):
    def test_messages_wait_for_the_choice(self):
        async def go():
            bot, ch = FakeBot(), FakeChannel()
            s = setup.Setup(bot)
            await s.offer(ch, ("m1", "first"))
            await s.offer(ch, ("m2", "second"))
            self.assertEqual(len(ch.sent), 1, "one card, however many messages")
            st = bot.chan(ch)
            st["home"] = {"socket": "/s", "session": "idea", "window": ""}
            await s.done(ch, st, "tmux idea")
            self.assertEqual(bot.tmux.submitted, ["first", "second"])
            self.assertTrue(setup.configured(st))
            self.assertIn("✅ tmux idea", ch.sent[0].content)
            self.assertIsNone(ch.sent[0].view)
            ch2 = FakeChannel()
            ch2.id = 6
            await s.offer(ch2)
            await s.done(ch2, bot.chan(ch2), "default")
            self.assertEqual(bot.tmux.started, [6], "nothing waiting: it is opened right away")
        asyncio.run(go())


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class Windows(unittest.TestCase):
    def test_open_window_new_session_then_existing(self):
        socket = os.path.join(tempfile.gettempdir(), "dcsetup-test-%d" % os.getpid())
        run = lambda *a: subprocess.run(["tmux", "-S", socket] + list(a), stdout=subprocess.PIPE,  # noqa: E731
                                        stderr=subprocess.DEVNULL, universal_newlines=True)
        try:
            async def go():
                first = await tmuxio.open_window(socket, "proj", "", "/tmp", ["cat"])
                second = await tmuxio.open_window(socket, "proj", "notes", "/tmp", ["cat"])
                self.assertTrue(first and second and first["id"] != second["id"])
                windows = run("list-windows", "-t", "proj", "-F", "#{window_name}").stdout.split()
                self.assertEqual(len(windows), 2)
                self.assertIn("notes", windows)
                self.assertEqual([n for n, _ in await tmuxio.user_sessions(socket)], ["proj"])
                run("send-keys", "-t", first["id"], "", "")
                self.assertTrue(first.get("pid"))
                self.assertFalse(await tmuxio.alive(dict(first, pid="1")), "a pane whose process changed is not ours")
                await tmuxio.kill_pane(second)
                self.assertEqual(len(run("list-windows", "-t", "proj").stdout.split("\n")) - 1, 1, "only that window")
            asyncio.run(go())
        finally:
            run("kill-server")
            if os.path.exists(socket):
                os.remove(socket)


if __name__ == "__main__":
    unittest.main()


class MachinePicker(unittest.TestCase):
    def test_other_machines_are_offered_for_a_claude_only(self):
        from bot import setup as setup_mod
        kinds = lambda v: [type(i).__name__ for i in v.children]  # noqa: E731
        ids = lambda v: [getattr(i, "custom_id", "") for i in v.children]  # noqa: E731
        here = setup_mod.where_view(1, False, None, ["HostB", "mac"])
        self.assertEqual(kinds(here)[0], "Select")
        self.assertEqual([o.value for o in here.children[0].options], ["-", "HostB", "mac"])
        self.assertIn("setup:1:default", ids(here))
        there = setup_mod.where_view(1, False, "mac", ["HostB", "mac"])
        self.assertNotIn("setup:1:default", ids(there), "another machine has no bot-managed tmux")
        self.assertTrue([o for o in there.children[0].options if o.value == "mac"][0].default)
        term = setup_mod.where_view(1, True, "HostB", ["HostB"])
        self.assertEqual(term.children[0].custom_id, "setup:1:thost", "a terminal can live there too")
        self.assertNotIn("setup:1:tdefault", ids(term))
        self.assertNotIn("Select", kinds(setup_mod.where_view(1, False, None, [])))
        self.assertIn("機器：**mac**", setup_mod.where_text("c", "/w", False, "mac", ["mac"]))


class Resp(object):
    def __init__(self):
        self.modal, self.sent = None, []

    async def send_modal(self, modal):
        self.modal = modal

    async def defer(self, **kw):
        pass

    async def send_message(self, text, **kw):
        self.sent.append(text)

    async def edit_message(self, **kw):
        pass


class Followup(object):
    def __init__(self):
        self.sent = []

    async def send(self, text, **kw):
        self.sent.append(text)


class Click(object):
    def __init__(self, ch, what, values=None):
        import discord
        self.type = discord.InteractionType.component
        self.data = {"custom_id": "setup:%d:%s" % (ch.id, what), "values": values or []}
        self.channel, self.user = ch, type("U", (), {"id": config.OWNER_ID})()
        self.response, self.followup = Resp(), Followup()


class OtherMachine(unittest.TestCase):
    """2026-10-07, a new channel on another machine: a path on another machine that starts with this machine's home was
    shown as ~/…, which over there is another folder; and the machine picked on the card was lost in a restart."""
    def setUp(self):
        from unittest import mock
        self.mock = mock
        self.home = os.path.expanduser("~")
        self.hosts = mock.patch.object(setup.remote, "hosts", return_value={"far": "FAR"})
        self.hosts.start()
        self.problem, setup.remote.problem = setup.remote.problem, None

    def tearDown(self):
        setup.remote.problem = self.problem
        self.hosts.stop()

    def test_this_homes_prefix_is_not_cut_from_another_machines_path(self):
        path = self.home + "/someproj"
        self.assertEqual(setup.shown(path, "far"), path)
        self.assertEqual(setup.shown(path), "~/someproj")
        self.assertEqual(setup.NewModal(None, FakeChannel(), path, host="far").cwd.default, path)
        self.assertEqual(setup.AddModal(None, FakeChannel(), "s", path, host="far").cwd.default, path)
        self.assertEqual(setup.NewModal(None, FakeChannel(), path).cwd.default, "~/someproj")

    def test_the_full_path_is_what_goes_to_that_machine_and_the_pick_survives_a_restart(self):
        path = self.home + "/someproj"
        asked = []

        async def info(host, folder=None):
            asked.append((host, folder))
            return {"socket": "/far/sock", "home": "/elsewhere/me", "folder": folder if folder == path else None}

        async def sessions(*a, **kw):
            return []

        async def go():
            bot, ch = FakeBot(), FakeChannel()
            s = setup.Setup(bot)
            await s.on_interaction(Click(ch, "host", ["far"]))
            s2 = setup.Setup(bot)  # the bot restarted halfway through
            s2.info = info
            self.assertEqual(s2.machine.get(ch.id), "far")
            s2.folder[ch.id] = path  # picked with 📁 over there
            s3 = setup.Setup(bot)  # and again
            s3.info = info
            click = Click(ch, "new")
            await s3.on_interaction(click)
            modal = click.response.modal
            self.assertEqual(modal.cwd.default, path)
            modal.cwd._value = modal.cwd.default  # sent as shown
            with self.mock.patch.object(setup.tmuxio, "user_sessions", sessions):
                sub = Click(ch, "new")
                await modal.on_submit(sub)
            self.assertEqual(asked, [("far", path)], "the full path went to that machine")
            st = bot.chan(ch)
            self.assertEqual(st["home"]["host"], "far")
            self.assertEqual(st["cwd"], path)
            self.assertNotIn("setup_picks", st, "picks cleared once set up")
        asyncio.run(go())

    def test_a_folder_not_found_says_on_which_machine(self):
        async def go():
            bot, ch = FakeBot(), FakeChannel()
            s = setup.Setup(bot)
            click = Click(ch, "new")
            await s.choose(click, ch, "sess", "", "/no/such/folder/here", new=True)
            self.assertIn(setup.remote.this_machine(), click.response.sent[-1])
        asyncio.run(go())

    def test_a_picked_machine_no_longer_listed_is_dropped(self):
        bot = FakeBot()
        s = setup.Setup(bot)
        s.machine[5] = "far"
        s.folder[5] = "/x"
        self.assertEqual(s.host_of(5), "far")
        with self.mock.patch.object(setup.remote, "hosts", return_value={}):
            self.assertIsNone(setup.Setup(bot).host_of(5), "taken out of the list (or its dcattach role) since")
        self.assertIsNone(s.machine.get(5))
        self.assertIsNone(s.folder.get(5), "its folder goes with it")

    def test_a_broken_list_keeps_the_picked_machine(self):
        bot = FakeBot()
        s = setup.Setup(bot)
        s.machine[5] = "far"

        def broken():
            setup.remote.broken(ValueError("machines.json cut short"))
            return {}
        try:
            with self.mock.patch.object(setup.remote, "inventory", broken):
                self.assertEqual(s.host_of(5), "far")
        finally:
            setup.remote.problem = None
        self.assertEqual(s.machine.get(5), "far")
