"""C-77 G3b: the 填入 button for a plugin's secret (bot/secretbox.py): who may press it, what reaches the core's store
(altention/secret_store.py, run for real behind a wrapper that only stands in for the plugin list, the machine's name
and #audit), what the owner is told, and that the value is nowhere else: not in replies, the state, or the output."""
import asyncio
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

import discord

from bot import config, messages, posts, secretbox
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_posts import Bot, Chan

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OWNER, OTHER = 42, 7
VALUE = "rig-secret-value-8c1f"
DECL = {"demo": [{"name": "api-token", "label": "Rig API token"}]}

# Runs the real store's main() with the plugin list, the machine's name and #audit stood in for (as its own tests do).
WRAPPER = textwrap.dedent("""\
    import json, os, sys
    sys.path.insert(0, %(engine)r)
    from altention import secret_store as ss
    decl = json.loads(%(decl)r)
    rows = [(n, "/p/" + n, {"name": n, "secrets": s}, "on", "") for n, s in decl.items()]
    ss.plugin.on_this_machine = lambda *a, **k: (rows, [])
    ss.config.this_machine = lambda: "lap"
    def audit(line):
        with open(os.path.join(os.environ["HOME"], "audit.txt"), "a") as f:
            f.write(line + "\\n")
    ss.tell_audit = audit
    sys.exit(ss.main(sys.argv[1:]))
    """)


class Seen(Chan):
    async def send(self, text=None, view=None, **kw):
        self.views = getattr(self, "views", []) + [view]
        return await Chan.send(self, text, **kw)


class Response(object):
    def __init__(self):
        self.said, self.modals, self.deferred = [], [], False

    async def send_message(self, text, ephemeral=False):
        self.said.append((text, ephemeral))

    async def send_modal(self, modal):
        self.modals.append(modal)

    async def defer(self, ephemeral=False, thinking=False):
        self.deferred = (ephemeral, thinking)


class Followup(object):
    def __init__(self, fail=False):
        self.said, self.fail = [], fail

    async def send(self, text, ephemeral=False):
        if self.fail:
            raise RuntimeError("followup refused " + text)  # (a text that is said in no output: see Quiet)
        self.said.append((text, ephemeral))


def answer(token, value=VALUE, user=OWNER, label=False, fail=False):
    field = {"type": 4, "custom_id": "value", "value": value}
    rows = [{"type": 18, "component": field}] if label else [{"type": 1, "components": [field]}]
    return type("I", (), {"type": discord.InteractionType.modal_submit,
                          "data": {"custom_id": "secm:" + token, "components": rows},
                          "user": type("U", (), {"id": user})(), "channel": type("C", (), {"id": 1})(),
                          "response": Response(), "followup": Followup(fail)})()


def press(token, user=OWNER):
    return type("I", (), {"type": discord.InteractionType.component, "data": {"custom_id": "sec:" + token},
                          "user": type("U", (), {"id": user})(), "channel": type("C", (), {"id": 1})(),
                          "response": Response()})()


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="secretbox home ")
        self.addCleanup(self.rmtree, self.home)
        wrapper = os.path.join(self.home, "store.py")
        with open(wrapper, "w") as f:
            f.write(WRAPPER % {"engine": ENGINE, "decl": json.dumps(DECL)})
        rows = [(n, "/p/" + n, {"name": n, "secrets": s}, "on", "") for n, s in DECL.items()]
        from altention import plugin as alt_plugin
        for p in (mock.patch.dict(os.environ, {"HOME": self.home, "ALT_INSTANCE": "-"}), mock.patch.object(config, "OWNER_ID", OWNER),
                  mock.patch.object(secretbox, "STORE", wrapper),
                  mock.patch.object(alt_plugin, "on_this_machine", lambda *a, **k: (rows, []))):
            p.start()
            self.addCleanup(p.stop)
        self.bot = Bot()
        self.ch = Seen(1, "st-1")
        self.bot.chans[1] = self.ch
        self.bot.guild.text_channels = [self.ch]
        self.bot.channels_state = {"1": {"tmux": "dc-x"}}
        self.bot.secrets = secretbox.Secrets(self.bot)

    @staticmethod
    def rmtree(path):
        for root, dirs, _files in os.walk(path):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o700)  # (a folder a test made unwritable)
        import shutil
        shutil.rmtree(path, True)

    def button(self, spec="demo/api-token", text="請填金鑰"):
        why, message = asyncio.run(self.bot.secrets.post(self.ch, text, {"secret": spec}))
        self.assertIsNone(why)
        return next(reversed(self.bot.channels_state["1"]["secret_boxes"]))

    def go(self, interaction):
        self.assertTrue(asyncio.run(self.bot.secrets.on_interaction(interaction)))
        return interaction

    def file(self, name="api-token"):
        return os.path.join(self.home, ".config", "altention", "secrets", "demo", name)

    def audit(self):
        try:
            with open(os.path.join(self.home, "audit.txt")) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []


class Posting(Base):
    def test_a_declared_secret_gets_a_button_holding_only_a_token(self):
        token = self.button()
        self.assertRegex(token, r"^[0-9a-f]{32}$")
        kept = self.bot.channels_state["1"]["secret_boxes"][token]
        self.assertEqual((kept["plugin"], kept["name"], kept["label"]), ("demo", "api-token", "Rig API token"))
        self.assertEqual(len(self.ch.sent), 1)
        self.assertTrue(self.ch.sent[0].startswith("請填金鑰\n-# 只有擁有者按得動。值會經過 Discord"), self.ch.sent[0])
        self.assertIn("altention secret set demo api-token", self.ch.sent[0])
        button = self.ch.views[-1].children[0]
        self.assertEqual((button.label, button.custom_id), ("填入 Rig API token", "sec:" + token))

    def test_no_text_gives_the_default_line(self):
        self.button(text="")
        self.assertTrue(self.ch.sent[0].startswith("按下面的按鈕，填入 demo 要的 Rig API token。\n-# "), self.ch.sent[0])

    def test_undeclared_unknown_or_malformed_is_not_posted(self):
        for spec, words in (("demo/other", "demo 沒有宣告叫 other 的金鑰"), ("nope/api-token", "這台找不到 nope 這個插件"),
                            ("Demo/x", "--secret 要寫成 <插件>/<名字>"), ("demo", "--secret 要寫成 <插件>/<名字>")):
            why, message = asyncio.run(self.bot.secrets.post(self.ch, "x", {"secret": spec}))
            self.assertIsNone(message, spec)
            self.assertIn(words, why, spec)
        self.assertEqual(self.ch.sent, [])
        self.assertNotIn("secret_boxes", self.bot.channels_state["1"])

    def test_a_declaration_that_cannot_be_read_in_time_posts_nothing(self):
        with mock.patch.object(secretbox, "declared", side_effect=ValueError("broken plugin.json")):
            why, message = asyncio.run(self.bot.secrets.post(self.ch, "x", {"secret": "demo/api-token"}))
        self.assertEqual((why, message), ("讀不到插件的設定", None))

    def test_old_buttons_are_dropped(self):
        st = {}
        first = secretbox.remember(st, "a", "b", "B", now=1000)
        for i in range(secretbox.KEEP):
            secretbox.remember(st, "a", "b", "B", now=2000 + i)
        self.assertNotIn(first, st["secret_boxes"])
        self.assertEqual(len(st["secret_boxes"]), secretbox.KEEP)
        st["secret_boxes"]["c" * 32] = {"plugin": "a", "name": "b", "at": "yesterday"}  # (hand-edited)
        secretbox.remember(st, "a", "b", "B", now=2000 + secretbox.KEEP_DAYS * 86400 + 100)
        self.assertEqual(len(st["secret_boxes"]), 1, "a week old or unreadable: gone")


class Pressing(Base):
    def test_the_owner_gets_the_box_not_kept_in_discords_store(self):
        token = self.button()
        r = self.go(press(token)).response
        self.assertEqual(r.said, [])
        (m,) = r.modals
        self.assertEqual((m.custom_id, m.title), ("secm:" + token, "Rig API token"))
        (field,) = m.children
        self.assertEqual((field.custom_id, field.style, field.max_length, field.label),
                         ("value", discord.TextStyle.paragraph, 4000, "Rig API token（貼上後按送出）"))
        self.assertTrue(m.is_finished(), "stopped: its answer comes by custom_id, also after a restart")

    def test_anyone_else_is_told_alone_and_gets_no_box(self):
        token = self.button()
        r = self.go(press(token, user=OTHER)).response
        self.assertEqual((r.said, r.modals), ([("只有擁有者能填這個。", True)], []))

    def test_a_forged_or_old_token_opens_nothing(self):
        token = self.button()
        self.bot.channels_state["1"]["secret_boxes"][token]["at"] -= secretbox.KEEP_DAYS * 86400 + 1
        for t in (token, "f" * 32, "../../x", ""):
            r = self.go(press(t)).response
            self.assertEqual((r.said, r.modals), ([("這顆按鈕已經失效了，請 Claude 再貼一次。", True)], []), t)

    def test_other_buttons_are_not_taken(self):
        for cid in ("act:" + "a" * 32, "secx:1", "fb:open"):
            i = press("x")
            i.data = {"custom_id": cid}
            self.assertFalse(asyncio.run(self.bot.secrets.on_interaction(i)), cid)


class Sending(Base):
    def test_the_value_is_stored_600_by_the_core_and_the_owner_told_alone(self):
        token = self.button()
        i = self.go(answer(token, VALUE + "\n"))
        self.assertEqual(i.response.deferred, (True, True))
        ((line, alone),) = i.followup.said
        self.assertTrue(alone)
        self.assertTrue(line.startswith("存好了：demo 的 api-token（%d 個字元" % len(VALUE)), line)
        self.assertNotIn(VALUE, line)
        with open(self.file()) as f:
            self.assertEqual(f.read(), VALUE)
        self.assertEqual(stat.S_IMODE(os.stat(self.file()).st_mode), 0o600)
        self.assertEqual(self.audit(), ["🔑 lap 存了 demo 的 api-token（從 Discord 的輸入框）"])
        self.assertNotIn(VALUE, json.dumps(self.bot.channels_state, ensure_ascii=False))

    def test_it_works_from_a_bot_started_after_the_button_and_with_label_components(self):
        token = self.button()
        self.bot.secrets = secretbox.Secrets(self.bot)  # (a restart: nothing in memory but the channel's state)
        i = self.go(answer(token, label=True))
        self.assertIn("存好了", i.followup.said[0][0])

    def test_anyone_else_cannot_send_a_box(self):
        token = self.button()
        i = self.go(answer(token, user=OTHER))
        self.assertEqual(i.response.said, [("只有擁有者能填這個。", True)])
        self.assertFalse(os.path.exists(self.file()))

    def test_a_box_for_an_old_token_stores_nothing(self):
        i = self.go(answer("e" * 32))
        self.assertEqual(i.response.said, [("這顆按鈕已經失效了，請 Claude 再貼一次。", True)])
        self.assertFalse(os.path.exists(self.file()))

    def test_the_cores_refusals_are_said_as_they_are(self):
        token = self.button()
        i = self.go(answer(token, "  \n"))
        self.assertEqual(i.followup.said, [("沒有存：是空的", True)])
        i = self.go(answer(token, "x" * (64 * 1024 + 1)))  # (Discord stops at 4000; the core's own limit holds too)
        self.assertEqual(i.followup.said, [("沒有存：太長了（最多 65536 個字元）", True)])
        self.bot.channels_state["1"]["secret_boxes"][token]["name"] = "gone"  # (the plugin no longer declares it)
        i = self.go(answer(token))
        self.assertEqual(i.followup.said, [("沒有存：demo 沒有宣告叫 gone 的金鑰（plugin.json 的 secrets）", True)])
        self.assertFalse(os.path.exists(self.file()))

    def test_a_file_that_cannot_be_written_is_said_without_the_value(self):
        token = self.button()
        os.makedirs(os.path.join(self.home, ".config"))
        os.chmod(os.path.join(self.home, ".config"), 0o500)
        i = self.go(answer(token))
        ((line, _),) = i.followup.said
        self.assertTrue(line.startswith("沒有存："), line)
        self.assertIn("Permission denied", line)
        self.assertNotIn(VALUE, line)
        self.assertEqual(self.audit(), [])

    def test_a_store_that_does_not_run_or_answer(self):
        token = self.button()
        with mock.patch.object(secretbox, "STORE", os.path.join(self.home, "missing.py")):
            i = self.go(answer(token))
        self.assertEqual(i.followup.said, [("沒有存：存的程式出錯了（結束碼 2）。這次的值沒有存下來，可以再按一次。", True)])
        slow = os.path.join(self.home, "slow.py")
        with open(slow, "w") as f:
            f.write("import sys, time\nsys.stdin.read()\ntime.sleep(30)\n")
        with mock.patch.object(secretbox, "STORE", slow), mock.patch.object(secretbox, "STORE_SECS", 1):
            i = self.go(answer(token))
        self.assertEqual(i.followup.said, [(messages.words("secret", "slow", {"secs": 1}), True)])
        with mock.patch.object(secretbox.sys, "executable", os.path.join(self.home, "no-python")):
            i = self.go(answer(token))
        self.assertEqual(i.followup.said, [("沒有存：存的程式出錯了（FileNotFoundError）。這次的值沒有存下來，可以再按一次。", True)])

    def test_every_line_the_store_prints_is_told(self):
        token = self.button()
        two = os.path.join(self.home, "two.py")
        with open(two, "w") as f:
            f.write("import sys\nsys.stdin.read()\nprint('存好了：demo 的 api-token')\nprint()\n"
                    "print('demo 還在 lab 用；要放過去，請 Claude 跑 setup.py secret-send demo api-token <機器>')\n")
        with mock.patch.object(secretbox, "STORE", two):
            i = self.go(answer(token))
        self.assertEqual(i.followup.said, [("存好了：demo 的 api-token\ndemo 還在 lab 用；要放過去，請 Claude 跑 "
                                            "setup.py secret-send demo api-token <機器>", True)])

    def test_the_value_goes_on_stdin_never_on_the_command_line(self):
        token = self.button()
        seen = os.path.join(self.home, "seen.py")
        with open(seen, "w") as f:
            f.write("import json, os, sys\nv = sys.stdin.read()\n"
                    "open(os.path.join(os.environ['HOME'], 'argv.json'), 'w').write(json.dumps([sys.argv[1:], v]))\n"
                    "print('存好了：test')\n")
        with mock.patch.object(secretbox, "STORE", seen):
            self.go(answer(token))
        with open(os.path.join(self.home, "argv.json")) as f:
            argv, got = json.load(f)
        self.assertEqual(argv, ["set", "demo", "api-token", "--stdin", "--via", "discord"])
        self.assertEqual(got, VALUE)

    def test_typed_reads_both_shapes_and_nothing_else(self):
        self.assertEqual(secretbox.typed(answer("x").data), VALUE)
        self.assertEqual(secretbox.typed(answer("x", label=True).data), VALUE)
        self.assertIsNone(secretbox.typed({"components": [{"type": 1, "components": [{"custom_id": "other", "value": "v"}]}]}))
        self.assertIsNone(secretbox.typed({}))


class Quiet(Base):
    def test_no_output_holds_the_value_whatever_happens(self):
        """The value in no line the bot prints (supervisor.out), also when a step fails with it on the stack."""
        token = self.button()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.go(answer(token))
            self.go(answer(token, fail=True))  # (the reply refused: an exception whose text holds the reply)
            with mock.patch.object(secretbox, "store", side_effect=RuntimeError("boom " + VALUE)):
                self.go(answer(token))
            with mock.patch.object(secretbox, "STORE", os.path.join(self.home, "missing.py")):
                self.go(answer(token))
        said = out.getvalue() + err.getvalue()
        self.assertNotIn(VALUE, said)
        self.assertIn("[secret] sent failed (RuntimeError)", said)


class Posts(Base):
    def setUp(self):
        Base.setUp(self)
        self.told = []

        async def tell(ch, text):
            self.told.append(text)
        self.bot.posts = posts.Posts(self.bot)
        self.bot.posts.tell = tell
        self.audits = []

        async def audit(text):
            self.audits.append(text)
        self.bot.audit = audit

    def test_a_post_elsewhere_is_a_card_with_the_button_and_never_handed_on(self):
        asyncio.run(self.bot.posts.handle({"channel": "st-1", "text": "請填", "secret": "demo/api-token",
                                           "from_name": "lap 上的 Claude（x）"}))
        (shown,) = self.ch.sent
        self.assertTrue(shown.startswith("📨 來自 lap 上的 Claude（x）\n請填\n-# 只有擁有者"), shown)
        self.assertEqual(self.ch.views[-1].children[0].custom_id[:4], "sec:")
        self.assertEqual(self.bot.tmux.given, [], "never handed to the channel's Claude")

    def test_not_posted_is_said_in_audit(self):
        asyncio.run(self.bot.posts.handle({"channel": "st-1", "text": "請填", "secret": "demo/other",
                                           "from_name": "lap 上的 Claude（x）"}))
        self.assertEqual(self.audits, ["altention dc post --secret demo/other（lap 上的 Claude（x））："
                                       "demo 沒有宣告叫 other 的金鑰（plugin.json 的 secrets）"])


class CommandLine(unittest.TestCase):
    def load(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(ENGINE, "altention", "dc", "post.py")
        loader = importlib.machinery.SourceFileLoader("dcpost_secret", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcpost_secret", loader))
        loader.exec_module(mod)
        return mod

    def test_secret_with_and_without_text(self):
        mod = self.load()
        p = mod.build(["st-1", "--secret", "demo/api-token", "請填"], "", {}, "h", "/x")
        self.assertEqual((p["secret"], p["text"]), ("demo/api-token", "請填"))
        p = mod.build(["st-1", "--secret", "demo/api-token"], "", {}, "h", "/x")
        self.assertEqual((p["secret"], p["text"]), ("demo/api-token", ""), "no text: the bot's default line")
        for argv in (["st-1", "--secret", "demo"], ["st-1", "--secret", "Demo/x"], ["st-1", "--secret"],
                     ["st-1", "--secret", "a/b", "--activity", "a/c", "x"], ["st-1", "--secret", "a/b", "--board", "b", "x"]):
            with self.assertRaises(SystemExit, msg=argv):
                mod.build(argv, "", {}, "h", "/x")


if __name__ == "__main__":
    unittest.main()
