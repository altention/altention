"""Attachments are saved where Claude can read them, under names that are safe to type into a terminal."""
import asyncio
import os
import tempfile
import time
import unittest

from bot import attachments
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class FakeAttachment(object):
    def __init__(self, filename, data=b"x", size=None, fail=False):
        self.filename, self.data, self.size, self.fail = filename, data, size or len(data), fail
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

    def test_save_and_fallback(self):
        msg = FakeMessage(FakeAttachment("a b.png", b"PNG"), FakeAttachment("bad.bin", fail=True),
                          FakeAttachment("huge.iso", size=attachments.MAX_BYTES + 1))
        lines = asyncio.run(attachments.save(msg))
        path = os.path.join(self.tmp.name, "7", "99-a_b.png")
        self.assertIn(path, lines[0])
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"PNG")
        self.assertIn("https://cdn.example/bad.bin", lines[1])
        self.assertIn("https://cdn.example/huge.iso", lines[2])
        self.assertNotIn("\n", "".join(lines))
        self.assertEqual(asyncio.run(attachments.save(FakeMessage())), [])

    def test_prune(self):
        old, new = os.path.join(self.tmp.name, "7", "old"), os.path.join(self.tmp.name, "7", "new")
        os.makedirs(os.path.dirname(old))
        for p in (old, new):
            open(p, "w").close()
        os.utime(old, (time.time() - 30 * 86400,) * 2)
        self.assertEqual(attachments.prune(), 1)
        self.assertEqual(os.listdir(os.path.dirname(old)), ["new"])


if __name__ == "__main__":
    unittest.main()
