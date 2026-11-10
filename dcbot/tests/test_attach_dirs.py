"""C-151: every way the bot opens a channel's Claude hands it the channel's attachments folder (--add-dir), made first
on the machine that Claude works on, so what the owner attaches is read without a question. One left out, and an
attachment is read now without asking, now with a question (manager 2026-11-03: each way listed in a test).

The ways, each below:
  1. a new channel (the setup card, --new-claude here, an archived channel taken back here): start_now()
  2. the first message after the bot restarted, or after its Claude ended or the idle reaper put it away: the saved
     conversation resumed
  3. a saved conversation that does not come back: the new one opened in its place
  4. !resume and `altention dc post --resume`: the window closed, the channel given another conversation, opened
  5. /btw in a channel with no Claude running yet
  6. a window in the owner's own tmux on this machine
  7. a window on another machine (--new-claude --on, a channel whose Claude works there, taken back from the archive
     there): the folder made there, as that machine's engine names it
  8. print mode (one `claude -p` per message): the command line here, the folder from Bot.turn() in
     test_print_permission
An attached pane (/attach) is the owner's own Claude Code, never started by the bot: nothing is handed to it."""
import asyncio
import io
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from bot import attachcore, attachments, claude_bridge, tmux_bridge
from tests import fakepane
from tests.test_dcremote import dcremote
from tests.test_open_session import Base, Tmux


def dirs_of(argv):
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]


class Ways(Base):
    def here(self):
        return os.path.join(fakepane.attachments_dir(), str(self.ch.id))

    def started(self, tmux):
        """The command line of every start, in order (the bot's own tmux and windows alike)."""
        return [c[3] if c[0] == "start" else c[5] for c in tmux.calls if c[0] in ("start", "open_window")]

    def test_1_a_new_channel(self):
        tmux = Tmux(self)
        self.assertTrue(self.go(self.bridge.start_now(self.ch), tmux))
        argv, = self.started(tmux)
        self.assertEqual(dirs_of(argv), [self.here()])
        self.assertTrue(os.path.isdir(self.here()), "made before Claude Code starts: a folder not there is dropped")
        self.assertEqual(stat.S_IMODE(os.stat(self.here()).st_mode), 0o700)
        self.assertEqual(argv.index("--add-dir") + 2, argv.index("--append-system-prompt"), "a flag after it: it takes every word up to one")

    def test_2_the_saved_conversation_resumed(self):
        tmux = Tmux(self)
        self.st["session_id"] = "saved-1"
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        argv, = self.started(tmux)
        self.assertEqual((argv[-2:], dirs_of(argv)), (["--resume", "saved-1"], [self.here()]))

    def test_3_the_new_conversation_in_place_of_one_that_does_not_come_back(self):
        tmux = Tmux(self, lambda argv: "exits" if "--resume" in argv else "ready")
        self.st["session_id"] = "saved-1"
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        first, second = self.started(tmux)
        self.assertIn("--resume", first)
        self.assertNotIn("--resume", second)
        self.assertEqual((dirs_of(first), dirs_of(second)), ([self.here()], [self.here()]))

    def test_4_another_conversation_by_resume(self):
        tmux = Tmux(self)
        self.st["session_id"] = "saved-1"
        self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux)
        self.go(self.bridge.close(self.ch), tmux)  # as Bot.resume_now() and the post's --resume do, then the id
        self.st["session_id"] = "other-2"
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        argv = self.started(tmux)[-1]
        self.assertEqual((argv[-2:], dirs_of(argv)), (["--resume", "other-2"], [self.here()]))

    def test_5_btw_with_no_claude_running(self):
        tmux = Tmux(self)
        checked = mock.AsyncMock(return_value=None)  # what follows the opening (the screen, typing) is not this test's
        with mock.patch.object(self.bridge, "checked", checked):
            self.go(self.bridge.btw_ask(self.ch, None, "what is this?"), tmux)
        checked.assert_awaited_once()
        argv, = self.started(tmux)
        self.assertEqual(dirs_of(argv), [self.here()])

    def home(self, host=None):
        self.st.pop("tmux", None)
        self.st["home"] = dict({"socket": "/tmp/tmux-1/default", "session": "work", "window": ""}, **({"host": host} if host else {}))

    def test_6_a_window_in_the_owners_tmux_here(self):
        tmux = Tmux(self)
        self.home()
        self.st["session_id"] = "saved-1"
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        argv, = self.started(tmux)
        self.assertEqual(dirs_of(argv), [self.here()])

    def test_7_a_window_on_another_machine_its_own_folder(self):
        tmux = Tmux(self, lambda argv: "silent")
        asked = []

        async def far(host, channel_id):
            asked.append((host, channel_id))
            return "/home/u/eng/dcbot/state/attachments/%s" % channel_id
        tmux.far_folder = far
        self.home("lap")
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        argv, = self.started(tmux)
        self.assertEqual(dirs_of(argv), ["/home/u/eng/dcbot/state/attachments/5"], "that machine's path, not this one's")
        self.assertEqual(asked, [("lap", 5)], "made there, once for the opening")
        self.assertFalse(os.path.exists(self.here()), "nothing made here for a Claude that works there")

    def test_7b_not_made_there_opened_all_the_same(self):
        tmux = Tmux(self, lambda argv: "silent")

        async def far(host, channel_id):
            return None  # (far_folder has written why to the log)
        tmux.far_folder = far
        self.home("lap")
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        argv, = self.started(tmux)
        self.assertEqual(dirs_of(argv), [], "it asks before reading an attachment, as before; the channel is not left without Claude")

    def test_a_folder_that_cannot_be_made_here_opens_all_the_same(self):
        tmux = Tmux(self)
        with mock.patch.object(attachcore, "folder", side_effect=OSError("read-only")), redirect_stdout(io.StringIO()) as log:
            self.assertTrue(self.go(self.bridge.start_now(self.ch), tmux))
        argv, = self.started(tmux)
        self.assertEqual(dirs_of(argv), [])
        self.assertIn("read-only", log.getvalue())

    def test_nothing_made_when_no_conversation_is_opened(self):
        """C-121: no permission mode set, nothing opened: nor is a folder made, here or (over ssh) there."""
        far = mock.AsyncMock(return_value="/there/5")
        tmux = Tmux(self)
        tmux.far_folder = far
        self.installation = lambda name: None
        for host in (None, "lap"):
            self.setUp()
            if host:
                self.home(host)
            err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux)
            self.assertIsInstance(err, tmux_bridge.NotOpened)
        self.assertEqual(self.started(tmux), [])
        self.assertFalse(os.path.exists(self.here()))
        far.assert_not_awaited()

    def test_an_attached_pane_is_handed_nothing(self):
        tmux = Tmux(self)
        tmux.up.add("%7")
        self.st.pop("tmux", None)
        self.st["pane"] = {"socket": "/tmp/tmux-1/default", "id": "%7"}
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        self.assertEqual(tmux.calls, [])
        self.assertFalse(os.path.exists(self.here()))

    def tearDown(self):
        path = self.here()
        if os.path.isdir(path):
            os.rmdir(path)


class PrintMode(unittest.TestCase):
    def test_8_one_claude_p_per_message(self):
        seen = []

        async def exec_(*cmd, **kw):
            seen.append(list(cmd))
            raise OSError("stop here")
        with mock.patch.object(asyncio, "create_subprocess_exec", exec_):
            with self.assertRaises(OSError):
                asyncio.run(claude_bridge.run_turn("hi", None, "/w", "note", "auto", dirs=["/a/5"]))
        cmd, = seen
        self.assertEqual(dirs_of(cmd), ["/a/5"])
        self.assertEqual(cmd.index("--add-dir") + 2, cmd.index("--append-system-prompt"))


class Folders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dcq-attach-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, True)

    def test_only_the_channels_own_folder_its_path_as_given(self):
        root = os.path.join(self.tmp, "state", "attachments")
        self.assertEqual(attachcore.folder(root, 42), os.path.join(root, "42"))
        self.assertEqual(os.listdir(root), ["42"])
        self.assertEqual(attachcore.folder(root, "42"), os.path.join(root, "42"), "there already: the same")
        link = os.path.join(self.tmp, "linked")
        os.symlink(root, link)
        self.assertEqual(attachcore.folder(link, 42), os.path.join(link, "42"), "not resolved: the link covers reads through both")

    def test_an_id_that_is_not_discords(self):
        for bad in ("../x", "4 2", "", "a1"):
            with self.assertRaises(ValueError):
                attachcore.folder(self.tmp, bad)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_here_or_there(self):
        with mock.patch.object(attachments, "DIR", self.tmp), mock.patch.object(attachments.remote, "this_machine", lambda: "box"):
            self.assertEqual(asyncio.run(attachments.folders(None, 5)), [os.path.join(self.tmp, "5")])
            self.assertEqual(asyncio.run(attachments.folders("box", 6)), [os.path.join(self.tmp, "6")], "this machine by its name")
            with mock.patch.object(attachments, "far_folder", mock.AsyncMock(return_value="/there/7")) as far:
                self.assertEqual(asyncio.run(attachments.folders("lap", 7)), ["/there/7"])
            far.assert_awaited_once_with("lap", 7)

    def test_asked_of_the_other_machine(self):
        calls = []

        async def dcremote_(host, *args, **kw):
            calls.append((host, args, kw.get("timeout")))
            return (0, "/there/eng/dcbot/state/attachments/7\n") if host == "lap" else (1, "")
        with mock.patch.object(attachments.remote, "dcremote", dcremote_), redirect_stdout(io.StringIO()) as log:
            self.assertEqual(asyncio.run(attachments.far_folder("lap", 7)), "/there/eng/dcbot/state/attachments/7")
            self.assertIsNone(asyncio.run(attachments.far_folder("old", 7)), "an engine there without attachdir")
        self.assertEqual(calls[0], ("lap", ("attachdir", "7"), attachments.FOLDER_SECS))
        self.assertIn("old", log.getvalue())

    def test_dcremote_attachdir(self):
        # (main() tells tmuxio which server is the owner's, for the whole process: not here, or every test after this
        # one would have no server of the bot's)
        with mock.patch.object(dcremote, "STATE", self.tmp), mock.patch.object(dcremote.tmuxio, "configure"), \
                redirect_stdout(io.StringIO()) as out:
            dcremote.main(["attachdir", "7"])
        self.assertEqual(out.getvalue().strip(), os.path.join(self.tmp, "attachments", "7"))
        self.assertTrue(os.path.isdir(out.getvalue().strip()))
        with mock.patch.object(dcremote, "STATE", self.tmp), mock.patch.object(dcremote.tmuxio, "configure"), \
                mock.patch("sys.stderr", io.StringIO()) as err:
            with self.assertRaises(SystemExit) as e:
                dcremote.main(["attachdir", "../x"])
        self.assertEqual(e.exception.code, 2)
        self.assertIn("沒有建", err.getvalue())


if __name__ == "__main__":
    unittest.main()
