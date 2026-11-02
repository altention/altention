"""C-91: what a picture was made from (altention/sources.py), taken in on altention dc post --file (a sidecar) and on
a rewritten reply (rewrite.py's "sources"), kept by the bot (bot/sources.py) and given back when the owner taps 📝."""
import asyncio
import base64
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from unittest import mock

from bot import config, main, posts, rewrite, screens, sources
from altention import sources as core  # (after bot: it puts the engine on the path)
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_posts import Bot, Chan
from tests import test_rewrite
from tests.test_rewrite import spec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORMULA = {"kind": "公式", "lang": "latex", "tool": "KaTeX", "source": "e^{i\\pi}+1=0"}
KEY = "sk-" + "a1" * 12  # what altention.redact hides


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


class Folder(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="sources ")
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.folder = os.path.join(self.tmp, "sources")
        p = mock.patch.object(sources, "FOLDER", self.folder)
        p.start()
        self.addCleanup(p.stop)


class Check(unittest.TestCase):
    def test_the_four_fields(self):
        got, why = core.check(dict(FORMULA, kind="  公式\n", extra=1))
        self.assertIsNone(why)
        self.assertEqual(got, dict(FORMULA), "words cleaned up, other keys left out")
        for bad in (None, [], dict(FORMULA, kind=""), dict(FORMULA, tool=7), dict(FORMULA, kind="字" * 41),
                    dict(FORMULA, lang="la tex"), dict(FORMULA, lang="x" * 21), dict(FORMULA, lang="../x"),
                    dict(FORMULA, source="  "), dict(FORMULA, source="x" * (core.MAX_SOURCE + 1)),
                    {k: v for k, v in FORMULA.items() if k != "lang"}):
            self.assertEqual(core.check(bad)[0], None, bad)
        self.assertIsNone(core.check(dict(FORMULA, source="字" * (core.MAX_SOURCE // 3)))[1], "the limit is in bytes")
        self.assertIsNotNone(core.check(dict(FORMULA, source="字" * (core.MAX_SOURCE // 3 + 1)))[1])

    def test_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            pic = os.path.join(d, "x.png")
            self.assertEqual(core.sidecar(pic), (None, None), "none")
            with open(pic + core.SIDECAR, "w") as f:
                json.dump(FORMULA, f)
            self.assertEqual(core.sidecar(pic), (FORMULA, None))
            with open(pic + core.SIDECAR, "w") as f:
                f.write("{not json")
            got, why = core.sidecar(pic)
            self.assertIsNone(got)
            self.assertIn("x.png.source.json", why)
            with open(pic + core.SIDECAR, "w") as f:
                json.dump(dict(FORMULA, source="x" * (core.MAX_SOURCE + 1)), f)
            self.assertIn("超過 64 KB", core.sidecar(pic)[1])


class DcPost(unittest.TestCase):
    def build(self, argv):
        dcpost = load("dcpost_sources", os.path.join(os.path.dirname(ROOT), "altention", "dc", "post.py"))
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            return dcpost.build(argv, "", {}, "h", "/x"), err.getvalue()

    def test_each_file_takes_its_own_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            a, b, c = (os.path.join(d, n) for n in ("a.png", "b.png", "c.png"))
            for p in (a, b, c):
                with open(p, "wb") as f:
                    f.write(b"png")
            with open(b + core.SIDECAR, "w") as f:
                json.dump(FORMULA, f)
            with open(c + core.SIDECAR, "w") as f:
                json.dump({"kind": "表格"}, f)
            post, err = self.build(["notes", "--file", a, "--file", b, "--file", c, "x"])
        self.assertNotIn("file_source", post, "the first has none")
        self.assertEqual(post["more_files"][0]["source"], FORMULA)
        self.assertNotIn("source", post["more_files"][1], "a bad one: the file goes without")
        self.assertIn("c.png.source.json", err)
        self.assertEqual([f["name"] for f in post["more_files"]], ["b.png", "c.png"], "the sidecar is not attached")
        self.assertEqual(posts.sources_of(post), [None, FORMULA, None])

    def test_the_first_file(self):
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, "a.png")
            with open(a, "wb") as f:
                f.write(b"png")
            with open(a + core.SIDECAR, "w") as f:
                json.dump(FORMULA, f)
            post, _ = self.build(["notes", "--file", a, "x"])
        self.assertEqual(post["file_source"], FORMULA)
        self.assertEqual(posts.sources_of(post), [FORMULA])


class Posted(Folder):
    def setUp(self):
        super().setUp()
        self.bot = Bot()
        self.a, self.notes = Chan(1, "st-1"), Chan(3, "notes")
        for c in (self.a, self.notes):
            self.bot.chans[c.id] = c
        self.bot.guild.text_channels = [self.a, self.notes]
        self.bot.channels_state = {"1": {"pane": {"id": "%1"}}, "3": {"plain": True}}
        self.p = posts.Posts(self.bot)

    def post(self, **kw):
        files = kw.pop("files")
        kw["file_name"], kw["file_b64"] = "a.png", base64.b64encode(b"png").decode()
        if files[0]:
            kw["file_source"] = files[0]
        kw["more_files"] = [dict({"name": "%d.png" % i, "b64": kw["file_b64"]}, **({"source": s} if s else {}))
                            for i, s in enumerate(files[1:], 2)]
        asyncio.run(self.p.handle(kw))

    def test_a_card_with_pictures(self):
        self.post(channel="notes", text="x", from_name="lap", files=[FORMULA, None, dict(FORMULA, source=KEY)])
        rec = sources.kept(1, 3)
        self.assertEqual(rec["count"], 3)
        self.assertEqual([(i["n"], i["source"]) for i in rec["items"]], [(1, FORMULA["source"]), (3, "[REDACTED]")],
                         "masked before it is written")
        with open(os.path.join(self.folder, "1.json")) as f:
            self.assertNotIn(KEY, f.read())
        self.assertIsNone(sources.kept(1, 1), "another channel's message of the same ID: not this one")

    def test_kind_and_tool_masked_too(self):
        self.post(channel="notes", text="x", from_name="lap", files=[dict(FORMULA, kind=KEY, tool="t " + KEY)])
        item, = sources.kept(1, 3)["items"]
        self.assertEqual((item["kind"], item["tool"]), ("[REDACTED]", "t [REDACTED]"))
        with open(os.path.join(self.folder, "1.json")) as f:
            self.assertNotIn(KEY, f.read())
        (text, _, _), = sources.answers(sources.kept(1, 3))
        self.assertTrue(text.startswith("[REDACTED]，t [REDACTED] 做的"))

    def test_into_its_own_channel(self):
        self.post(channel="st-1", text="", from_channel="1", files=[FORMULA])
        self.assertEqual(self.a.how[-1]["files"], ["a.png"])
        self.assertEqual(sources.kept(1, 1)["items"][0]["kind"], "公式")

    def test_none_given_none_written(self):
        self.post(channel="notes", text="x", from_name="lap", files=[None, {"kind": "壞的"}])
        self.assertFalse(os.path.exists(self.folder))


class Rewritten(Folder):
    def setUp(self):
        super().setUp()
        r = self.r = test_rewrite.OnTheReply("test_laid_out_in_order_with_the_footer_on_the_last")  # its rig, not its tests
        r.setUp()
        self.addCleanup(r.doCleanups)
        self.addCleanup(r.tearDown)

    def test_read(self):
        msgs, why, token = asyncio.run(rewrite.run(spec("sources"), "x"))
        self.assertIsNone(why)
        (text, pics, srcs), (after, none) = msgs
        self.assertEqual(len(pics), 3)
        self.assertEqual([s and s["tool"] for s in srcs], ["Tectonic", "Tectonic", None],
                         "a bad one left out, one not among its pictures not used")
        self.assertEqual(none, [], "no sources: two items, as before")
        rewrite.drop(token)

    def test_posted_and_kept(self):
        rewrite.chosen["spec"] = spec("sources")

        async def go():
            await self.r.turn("看這個")
            await self.r.settle()
        asyncio.run(go())
        first = next(m for m in self.r.ch.sent if m.files)
        rec = sources.kept(first.id, 5)
        self.assertEqual((rec["count"], [i["n"] for i in rec["items"]]), (3, [1, 2]))
        (text, name, data), = sources.answers(rec)
        self.assertEqual(text, "第 1–2 張：LaTeX 文件，Tectonic 做的\n```latex\n\\section{a}\n```", "one source, once")


class Answers(unittest.TestCase):
    def rec(self, items, count):
        return {"count": count, "items": [dict(s, n=n) for n, s in items]}

    def test_numbers(self):
        self.assertEqual([sources.numbers(x) for x in ([1], [1, 2, 3], [1, 3], [1, 2, 4])], ["1", "1–3", "1、3", "1–2、4"])

    def test_one_picture_no_number(self):
        (text, name, data), = sources.answers(self.rec([(1, FORMULA)], 1))
        self.assertEqual(text, "公式，KaTeX 做的\n```latex\ne^{i\\pi}+1=0\n```")
        self.assertIsNone(name)

    def test_several(self):
        other = dict(FORMULA, kind="表格", tool="matplotlib", lang="python", source="plot()")
        got = sources.answers(self.rec([(1, FORMULA), (2, other), (3, FORMULA)], 4))
        self.assertEqual([t.split("\n")[0] for t, _, _ in got], ["第 1、3 張：公式，KaTeX 做的", "第 2 張：表格，matplotlib 做的"])

    def test_too_long_or_fenced_goes_as_a_file(self):
        for src, count, want in (("x" * 1900, 1, "source.latex"), ("a\n```\nb", 2, "source-2.latex")):
            (text, name, data), = sources.answers(self.rec([(2, dict(FORMULA, source=src))], count))
            self.assertEqual((name, data), (want, src.encode()))
            self.assertNotIn("```", text)


class Tapped(Folder):
    def setUp(self):
        super().setUp()
        self.ch = TapCh()
        self.bot = type("B", (), {"get_channel": lambda s, cid: self.ch if cid == 7 else None,
                                  "channels_state": {}})()
        self.saved = config.OWNER_ID
        config.OWNER_ID = 42
        self.addCleanup(setattr, config, "OWNER_ID", self.saved)

    def tap(self, mid, cid=7, user=42, emoji="📝"):
        payload = type("P", (), {"message_id": mid, "channel_id": cid, "user_id": user, "emoji": emoji})()
        asyncio.run(main.Bot.on_raw_reaction_add(self.bot, payload))

    def test_only_a_kept_message_of_that_channel_by_the_owner(self):
        sources.remember(type("M", (), {"id": 11})(), 7, [FORMULA], 1)
        self.tap(11, user=99)
        self.tap(12)
        self.tap(11, cid=8)
        self.assertEqual(self.ch.sent, [])
        self.tap(11)
        (text, ref, file, mentions), = self.ch.sent
        self.assertTrue(text.startswith("公式，KaTeX 做的\n```latex"))
        self.assertTrue(ref, "a reply to that message")
        self.assertEqual(mentions.everyone, False)

    def test_a_screen_picture_goes_first(self):
        sources.remember(type("M", (), {"id": 11})(), 7, [FORMULA], 1)
        self.bot.channels_state = {"7": {"screens": {"11": "the screen"}}}
        self.tap(11)
        self.assertEqual([t for t, *_ in self.ch.sent], ["```\nthe screen\n```"])

    def test_kept_at_most(self):
        old = time.time() - (sources.KEEP_DAYS + 1) * 86400
        for i in range(5):
            sources.remember(type("M", (), {"id": i})(), 7, [FORMULA], 1)
        os.utime(os.path.join(self.folder, "0.json"), (old, old))
        self.assertIsNone(sources.kept(0, 7), "too old")
        with mock.patch.object(sources, "KEEP_COUNT", 3):
            for i, at in enumerate(range(5)):
                os.utime(os.path.join(self.folder, "%d.json" % i), (time.time() - 100 + at,) * 2)
            sources.remember(type("M", (), {"id": 9})(), 7, [FORMULA], 1)
        self.assertEqual(sorted(os.listdir(self.folder)), ["3.json", "4.json", "9.json"])


class TapCh(object):
    def __init__(self):
        self.sent = []

    async def send(self, text, reference=None, mention_author=True, file=None, allowed_mentions=None):
        self.sent.append((text, reference is not None, file, allowed_mentions))

    def get_partial_message(self, mid):
        return type("M", (), {"id": mid})()


if __name__ == "__main__":
    unittest.main()
