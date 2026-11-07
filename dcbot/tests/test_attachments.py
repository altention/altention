"""Attachments are saved where Claude can read them, under names that are safe to type into a terminal."""
import asyncio
import base64
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

from bot import attachcore, attachments, remote
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


def old_safe_name(name):
    """safe_name as engines before 0.5.1 have it (their dcremote attach takes a name only when this leaves it as it is)."""
    name = re.sub(r"[^\w.\-]", "_", os.path.basename((name or "").replace("\\", "/")), flags=re.UNICODE)
    return name.lstrip(".")[:80] or "file"


class FakeAttachment(object):
    ids = iter(range(500, 10 ** 6))

    def __init__(self, filename, data=b"x", size=None, fail=False, title=None):
        self.id = next(FakeAttachment.ids)
        self.filename, self.data, self.size, self.fail, self.title = filename, data, size or len(data), fail, title
        self.url = "https://cdn.example/" + filename

    async def save(self, path):
        if self.fail:
            raise OSError("network")
        with open(path, "wb") as f:
            f.write(self.data)


class FakeMessage(object):
    id = 99

    def __init__(self, *atts):
        self.attachments = list(atts)
        self.channel = type("C", (), {"id": 7})()


class Attachments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old, attachments.DIR = attachments.DIR, self.tmp.name

    def tearDown(self):
        attachments.DIR = self.old
        self.tmp.cleanup()

    def test_safe_name(self):
        self.assertEqual(attachments.safe_name("../../etc/pass wd;rm.png"), "pass_wd_rm.png")
        self.assertEqual(attachments.safe_name("截圖 2026.png"), "截圖_2026.png")
        self.assertEqual(attachments.safe_name("..."), "file")
        self.assertEqual(attachments.safe_name(None), "file")
        self.assertEqual(attachments.safe_name("神秘綠色粉末.HEIC"), "神秘綠色粉末.HEIC")
        self.assertEqual(attachments.safe_name("a\x01b\nc\x7f.png"), "a_b_c_.png")

    def test_a_long_name_fits_a_file_name_and_keeps_its_suffix(self):
        """80 Chinese characters are 240 bytes: with the ids in front, past the 255 a file name may have."""
        for name in ("中" * 100 + ".png", "a" * 300 + ".png", "中" * 60 + "." + "中" * 30, "x" * 90):
            got = attachments.safe_name(name)
            self.assertLessEqual(len(got.encode("utf-8")), attachcore.NAME_BYTES, name)
            self.assertLessEqual(len(got), attachcore.NAME_CHARS, name)
            self.assertEqual(attachments.safe_name(got), got, "made safe once is safe")
            self.assertEqual(old_safe_name(got), got, "an engine before this one takes it too")
            if name.endswith(".png"):
                self.assertTrue(got.endswith(".png"), got)
        path = attachcore.store(self.tmp.name, "7" * 25, "9" * 25, "5" * 25, attachments.safe_name("中" * 100 + ".png"), b"x")
        self.assertLessEqual(len(os.path.basename(path).encode("utf-8")), 255)

    def test_the_name_the_owner_gave(self):
        """Discord: 「神秘綠色粉末.HEIC」 comes as filename fb7cca6d412d21b1.HEIC, title 神秘綠色粉末 (owner, 11-03)."""
        cases = ((FakeAttachment("fb7cca6d412d21b1.HEIC", title="神秘綠色粉末"), "神秘綠色粉末.HEIC"),
                 (FakeAttachment("fb7cca6d412d21b1.HEIC", title="神秘綠色粉末.heic"), "神秘綠色粉末.heic"),
                 (FakeAttachment("a.png"), "a.png"), (FakeAttachment("a.png", title=" "), "a.png"),
                 (FakeAttachment("README", title="說明"), "說明"))
        for a, want in cases:
            self.assertEqual(attachments.name_of(a), want)
        msg = FakeMessage(FakeAttachment("fb7cca6d412d21b1.HEIC", b"HEIC", title="神秘綠色粉末"))
        line = asyncio.run(attachments.save(msg))[0]
        path = os.path.join(self.tmp.name, "7", "99-%d-神秘綠色粉末.HEIC" % msg.attachments[0].id)
        self.assertIn("[附件] 神秘綠色粉末.HEIC 已存到 %s" % path, line)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"HEIC")

    def test_save_and_fallback(self):
        msg = FakeMessage(FakeAttachment("a b.png", b"PNG"), FakeAttachment("bad.bin", fail=True),
                          FakeAttachment("huge.iso", size=attachments.MAX_BYTES + 1))
        lines = asyncio.run(attachments.save(msg))
        path = os.path.join(self.tmp.name, "7", "99-%d-a_b.png" % msg.attachments[0].id)
        self.assertIn(path, lines[0])
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"PNG")
        self.assertIn("https://cdn.example/bad.bin", lines[1])
        self.assertIn("https://cdn.example/huge.iso", lines[2])
        self.assertNotIn("\n", "".join(lines))
        self.assertEqual(asyncio.run(attachments.save(FakeMessage())), [])

    def test_two_attachments_with_one_name_are_two_files(self):
        msg = FakeMessage(FakeAttachment("image.png", b"one"), FakeAttachment("image.png", b"two"))
        lines = asyncio.run(attachments.save(msg))
        paths = [os.path.join(self.tmp.name, "7", "99-%d-image.png" % a.id) for a in msg.attachments]
        self.assertNotEqual(paths[0], paths[1])
        self.assertNotEqual(lines[0], lines[1])
        for path, line, data in zip(paths, lines, (b"one", b"two")):
            self.assertIn(path, line)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), data)
        self.assertEqual(len(os.listdir(os.path.join(self.tmp.name, "7"))), 2)

    def test_two_attachments_with_one_title_are_two_files(self):
        msg = FakeMessage(FakeAttachment("1a2b.png", b"one", title="圖片"), FakeAttachment("3c4d.png", b"two", title="圖片"))
        lines = asyncio.run(attachments.save(msg))
        paths = [os.path.join(self.tmp.name, "7", "99-%d-圖片.png" % a.id) for a in msg.attachments]
        self.assertNotEqual(paths[0], paths[1])
        for path, line, data in zip(paths, lines, (b"one", b"two")):
            self.assertIn(path, line)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), data)

    def test_prune(self):
        old, new = os.path.join(self.tmp.name, "7", "old"), os.path.join(self.tmp.name, "7", "new")
        os.makedirs(os.path.dirname(old))
        for p in (old, new):
            open(p, "w").close()
        os.utime(old, (time.time() - 30 * 86400,) * 2)
        self.assertEqual(attachments.prune(), 1)
        self.assertEqual(os.listdir(os.path.dirname(old)), ["new"])


class ToAnotherMachine(unittest.TestCase):
    """C-107: a channel whose Claude works on another machine gets the file there, and that machine's path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = attachments.DIR, remote.dcremote, remote.this_machine
        attachments.DIR = os.path.join(self.tmp.name, "here")
        remote.this_machine = lambda: "botbox"
        self.sent = []

    def tearDown(self):
        attachments.DIR, remote.dcremote, remote.this_machine = self.saved
        self.tmp.cleanup()

    def answer(self, code, out, said=None, *later):
        answers = [(code, out, said)] + list(later)

        async def dcremote(host, *args, input=None, timeout=30, err=None):
            self.sent.append((host, args, input, timeout))
            code, out, said = answers.pop(0) if len(answers) > 1 else answers[0]
            if said and err is not None:
                err.append(said)
            return code, out
        remote.dcremote = dcremote

    def test_the_owners_name_is_sent_and_a_refused_one_goes_again_as_discords(self):
        """A machine that does not take the name (exit 2: nothing written) gets Discord's own; once, and only then."""
        msg = FakeMessage(FakeAttachment("fb7cca6d412d21b1.HEIC", b"HEIC", title="神秘綠色粉末"))
        self.answer(0, "/far/7/99-1-神秘綠色粉末.HEIC\n")
        line = asyncio.run(attachments.save(msg, "far"))[0]
        self.assertEqual([args[4] for _, args, _, _ in self.sent], ["神秘綠色粉末.HEIC"])
        self.assertIn("[附件] 神秘綠色粉末.HEIC 已存到 /far/7/99-1-神秘綠色粉末.HEIC", line)
        self.sent = []
        self.answer(2, "", "dcremote attach：沒有存（檔名不安全）", (0, "/far/7/99-1-fb7cca6d412d21b1.HEIC\n", None))
        line = asyncio.run(attachments.save(msg, "far"))[0]
        self.assertEqual([args[4] for _, args, _, _ in self.sent], ["神秘綠色粉末.HEIC", "fb7cca6d412d21b1.HEIC"])
        self.assertIn("[附件] 神秘綠色粉末.HEIC 已存到 /far/7/99-1-fb7cca6d412d21b1.HEIC", line)
        for code in (255, 1):  # not known to have been refused: never sent again
            self.sent = []
            self.answer(code, "", "far 經 far-lab：逾時")
            line = asyncio.run(attachments.save(msg, "far"))[0]
            self.assertEqual(len(self.sent), 1, code)
            self.assertIn("沒有送到 far", line)

    def test_sent_there_and_its_path_given(self):
        self.answer(0, "/far/state/attachments/7/99-1-a_b.png\n")
        msg = FakeMessage(FakeAttachment("a b.png", b"PNG"))
        lines = asyncio.run(attachments.save(msg, "far"))
        host, args, data, timeout = self.sent[0]
        self.assertEqual((host, args), ("far", ("attach", 7, 99, msg.attachments[0].id, "a_b.png")))
        self.assertEqual((base64.b64decode(data), timeout), (b"PNG", attachments.REMOTE_SECS))
        self.assertIn("已存到 /far/state/attachments/7/99-1-a_b.png", lines[0])
        self.assertIn("https://cdn.example/a b.png", lines[0])

    def test_this_machine_by_name_is_here(self):
        self.answer(0, "/far/x\n")
        lines = asyncio.run(attachments.save(FakeMessage(FakeAttachment("a.png")), "botbox"))
        self.assertEqual(self.sent, [])
        self.assertIn(os.path.join(attachments.DIR, "7"), lines[0])

    def test_not_sent_says_where_it_is_and_the_url(self):
        self.answer(255, "", "far 經 far-lab：逾時")
        msg = FakeMessage(FakeAttachment("a.png"))
        line = asyncio.run(attachments.save(msg, "far"))[0]
        self.assertIn("沒有送到 far（far 經 far-lab：逾時）", line)
        self.assertIn("檔案在 botbox 的 %s（要的話用 scp 從那台拿）" % attachments.target(7, 99, msg.attachments[0].id, "a.png"), line)
        self.assertIn("https://cdn.example/a.png", line)

    def test_an_engine_without_attach_is_said_plainly_with_no_call_to_update(self):
        self.answer(1, "", "dcremote：不認得 attach")
        line = asyncio.run(attachments.save(FakeMessage(FakeAttachment("a.png")), "far"))[0]
        self.assertIn("沒有送到 far（那台的引擎還不會收附件）", line)
        self.assertNotIn("更新", line)

    def test_too_big_for_the_way_over_stays_here_and_says_why(self):
        self.answer(0, "/far/x\n")
        old, attachments.REMOTE_MAX = attachments.REMOTE_MAX, 10
        try:
            line = asyncio.run(attachments.save(FakeMessage(FakeAttachment("big.zip", b"x" * 11)), "far"))[0]
        finally:
            attachments.REMOTE_MAX = old
        self.assertEqual(self.sent, [])
        self.assertIn("太大", line)
        self.assertIn("（要的話用 scp 從那台拿），網址：", line)
        self.assertIn("沒有送到 far；檔案在 botbox 的", line)
        self.assertIn("https://cdn.example/big.zip", line)


class Store(unittest.TestCase):
    """What the other machine writes: Discord ids, a name already made safe, a path inside its folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "attachments")

    def tearDown(self):
        self.tmp.cleanup()

    def test_written_for_this_user_only_inside_the_folder(self):
        path = attachcore.store(self.root, "7", "99", "5", "a_b.png", b"PNG")
        self.assertEqual(path, os.path.join(os.path.realpath(self.root), "7", "99-5-a_b.png"))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"PNG")

    def test_refused(self):
        for args in (("..", "99", "5", "x"), ("7/../8", "99", "5", "x"), ("", "99", "5", "x"), ("7", "9 9", "5", "x"),
                     ("7", "99", "5", "../x"), ("7", "99", "5", "a/b"), ("7", "99", "5", ".x"), ("7", "99", "5", "")):
            with self.assertRaises(ValueError, msg=args):
                attachcore.store(self.root, *args, data=b"x")
        self.assertFalse(os.path.exists(self.tmp.name + "/x"))
        self.assertEqual([f for _, _, fs in os.walk(self.tmp.name) for f in fs], [])

    def test_a_channel_folder_that_leads_elsewhere_is_refused(self):
        os.makedirs(self.root)
        os.symlink(self.tmp.name, os.path.join(self.root, "7"))
        with self.assertRaises(ValueError):
            attachcore.store(self.root, "7", "99", "5", "x", b"x")


class TheRealTool(unittest.TestCase):
    """tools/dcremote attach on a throwaway copy of the engine, reached the way the bot reaches another machine:
    remote.run with ssh replaced by a local shell that takes its time."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        engine = os.path.dirname(root)
        shutil.copytree(os.path.join(root, "bot"), os.path.join(self.tmp.name, "dcbot", "bot"))
        os.makedirs(os.path.join(self.tmp.name, "dcbot", "tools"))
        self.tool = os.path.join(self.tmp.name, "dcbot", "tools", "dcremote")
        shutil.copy(os.path.join(root, "tools", "dcremote"), self.tool)
        os.makedirs(os.path.join(self.tmp.name, "altention"))
        shutil.copy(os.path.join(engine, "altention", "__init__.py"), os.path.join(self.tmp.name, "altention", "__init__.py"))
        shutil.copytree(os.path.join(engine, "altention", "connectors"), os.path.join(self.tmp.name, "altention", "connectors"))
        self.folder = os.path.join(self.tmp.name, "dcbot", "state", "attachments")
        self.env = dict(os.environ, TMUX_TMPDIR=os.path.join(self.tmp.name, "sockets"),
                        DCBOT_ENV=os.path.join(self.tmp.name, "no-settings"))
        self.saved = (attachments.DIR, remote.SSH, remote.aliases, remote.dcremote_command, remote.this_machine,
                      dict(remote.LAST_GOOD))
        attachments.DIR = os.path.join(self.tmp.name, "here")

    def tearDown(self):
        (attachments.DIR, remote.SSH, remote.aliases, remote.dcremote_command, remote.this_machine, good) = self.saved
        remote.LAST_GOOD.clear()
        remote.LAST_GOOD.update(good)
        self.tmp.cleanup()

    def tool_says(self, *args, input=""):
        return subprocess.run(["/usr/bin/python3", self.tool] + list(args), input=input, env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)

    def test_kept_under_its_own_folder_and_pruned_there(self):
        old = os.path.join(self.folder, "7", "old")
        os.makedirs(os.path.dirname(old))
        open(old, "w").close()
        os.utime(old, (time.time() - 30 * 86400,) * 2)
        r = self.tool_says("attach", "7", "99", "5", "a_b.png", input=base64.b64encode(b"PNG").decode())
        self.assertEqual(r.returncode, 0, r.stderr)
        path = r.stdout.strip()
        self.assertEqual(path, os.path.join(os.path.realpath(self.folder), "7", "99-5-a_b.png"))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(os.listdir(os.path.join(self.folder, "7")), ["99-5-a_b.png"], "the old one pruned")

    def test_the_owners_name_reaches_the_tool_and_is_kept_there(self):
        """From save() on the bot's side to the file over there, under the name the owner gave (manager 11-03)."""
        remote.SSH = ["/bin/sh", "-c", 'exec /bin/sh -c "$2"', "ssh"]
        remote.aliases = lambda host: ["far-lab"]
        remote.LAST_GOOD["far"] = "far-lab"
        remote.dcremote_command = lambda host: "DCBOT_ENV=%s /usr/bin/python3 %s" % (self.env["DCBOT_ENV"], self.tool)
        remote.this_machine = lambda: "botbox"
        long = "很長的名字" * 30
        msg = FakeMessage(FakeAttachment("fb7cca6d412d21b1.HEIC", b"HEIC", title="神秘綠色粉末"),
                          FakeAttachment("0a1b.png", b"PNG", title=long))
        lines = asyncio.run(attachments.save(msg, "far"))
        folder = os.path.join(os.path.realpath(self.folder), "7")
        first = os.path.join(folder, "99-%d-神秘綠色粉末.HEIC" % msg.attachments[0].id)
        self.assertIn("[附件] 神秘綠色粉末.HEIC 已存到 %s" % first, lines[0])
        with open(first, "rb") as f:
            self.assertEqual(f.read(), b"HEIC")
        second = os.path.join(folder, "99-%d-%s" % (msg.attachments[1].id, attachments.safe_name(long + ".png")))
        self.assertIn("已存到 %s" % second, lines[1])
        self.assertTrue(second.endswith(".png"))
        self.assertEqual(sorted(os.listdir(folder)), sorted(os.path.basename(p) for p in (first, second)))

    def test_refused_with_nothing_written(self):
        for args in (("..", "99", "5", "x"), ("7", "99", "5", "../x")):
            r = self.tool_says("attach", *args, input=base64.b64encode(b"x").decode())
            self.assertEqual((r.returncode, r.stdout), (2, ""), args)
            self.assertIn("沒有存", r.stderr)
        r = self.tool_says("attach", "7", "99", "5", "x", input="not base64!")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(self.folder))

    def test_other_channels_go_on_while_a_file_is_on_its_way(self):
        """The send is a subprocess the loop waits on: only that message waits (manager 2026-10-28)."""
        remote.SSH = ["/bin/sh", "-c", 'sleep 1; exec /bin/sh -c "$2"', "ssh"]
        remote.aliases = lambda host: ["far-lab"]
        remote.LAST_GOOD["far"] = "far-lab"
        remote.dcremote_command = lambda host: "DCBOT_ENV=%s /usr/bin/python3 %s" % (self.env["DCBOT_ENV"], self.tool)
        remote.this_machine = lambda: "botbox"
        ticks = []

        async def other_channel():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.05)

        async def both():
            other = asyncio.ensure_future(other_channel())
            started = time.monotonic()
            lines = await attachments.save(FakeMessage(FakeAttachment("a.png", b"PNG")), "far")
            took = time.monotonic() - started
            other.cancel()
            return lines, took

        lines, took = asyncio.run(both())
        self.assertGreaterEqual(took, 1)
        self.assertGreater(len(ticks), 10, "the other channel went on while the file was sent")
        self.assertLess(max(b - a for a, b in zip(ticks, ticks[1:])), 0.5)
        self.assertIn("已存到 %s" % os.path.join(os.path.realpath(self.folder), "7"), lines[0])


if __name__ == "__main__":
    unittest.main()
