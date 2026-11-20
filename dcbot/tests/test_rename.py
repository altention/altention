"""C-201 2b: a rename taken along at once (bot/rename.py; design section 3 points 1-3, owner 11-17, manager 11-18):
/rename, the channel renamed by hand in Discord, the window named by hand in tmux, --rename from another channel's
Claude. Real tmux servers of the test's own (test_reconcile's Base: a socket folder of its own, tests/tmuxguard)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import time
import unittest
from unittest import mock

import discord

from bot import rename, tmuxio
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests import test_reconcile
from tests.test_reconcile import tmux


class Chan(test_reconcile.Chan):
    """A channel whose rename Discord takes (or holds back: `held`, or refuses: `refused`)."""
    def __init__(self, cid, name):
        super().__init__(cid, name)
        self.edits, self.held, self.refused = [], False, None

    async def edit(self, name=None, **kw):
        if self.held:
            await asyncio.sleep(60)  # (discord.py sleeping out the limit of two in ten minutes)
        if self.refused is not None:
            raise self.refused
        self.edits.append(name)
        self.name = name  # (as the update event leaves it)


class Bot(test_reconcile.Bot):
    def __init__(self, bot_sock):
        super().__init__(bot_sock)
        self.submitted, self.modes, self.submit_gives = [], {}, None
        self.renamer = rename.Renamer(self)
        bot = self

        class Tmux(object):
            async def submit(self, origin, text, out=None):
                bot.submitted.append((origin.channel.id, text))
                if isinstance(bot.submit_gives, Exception):  # (NotOpened: ensure() refused, as the real one raises)
                    raise bot.submit_gives
                return bot.submit_gives  # (None: typed in, as the real one; a Typing refused or False: not)
        self.tmux = Tmux()

    def mode(self, st):
        return self.modes.get(id(st), "tmux")

    async def fetch_channel(self, cid):  # (the channel as Discord has it: `read_as` a name, or an error to raise)
        got = getattr(self, "read_as", None)
        if isinstance(got, Exception):
            raise got
        return type("Read", (), {"name": got})() if got is not None else self.chans[cid]

    def chan(self, ch):
        return self.channels_state.setdefault(str(ch.id), {})

    async def quiet(self, text):
        pass


def window_name(sock, target):
    return tmux(sock, "display-message", "-p", "-t", target, "#{window_name}").stdout.strip()


class Base(test_reconcile.Base):
    def setUp(self):
        super().setUp()
        bot = Bot(self.botsock)
        bot.channels_state = self.bot.channels_state
        self.bot = bot
        self.a, self.b, self.c = Chan(1, "a"), Chan(2, "b"), Chan(3, "c")
        bot.chans = {1: self.a, 2: self.b, 3: self.c}
        self.looks = test_reconcile.reconcile.Looks(bot)
        bot.save()

    def rename(self, ch, name, **kw):
        return asyncio.run(self.bot.renamer.rename(ch, self.bot.channels_state[str(ch.id)], name, **kw))


class NotTypedIn(Base):
    def test_a_rename_the_bridge_did_not_type_in_is_never_said_as_done(self):
        """Manager 11-20 (C-198 drill, X): a pane restarted and still being made sure of: submit() typed nothing and
        said so (to the quiet out); the card said ✅ all the same."""
        from bot.tmux_bridge import Typing
        r = Typing()
        r.refuse("claude_code.box_differs")
        self.bot.submit_gives = r
        ok, card = self.rename(self.a, "實驗 B")
        row = [l for l in card.splitlines() if "Claude 對話名" in l]
        self.assertEqual(len(row), 1, card)
        self.assertTrue(row[0].startswith("- ❌ Claude 對話名：沒送進去："), row)
        self.assertNotIn("**", row[0])
        self.assertTrue(card.startswith("⚠️ #a 改名，有一項沒改成"), card)
        self.assertIn("沒改成：Claude 對話名", self.bot.audits[-1])
        self.bot.submit_gives = False  # (told() after typing: the box was not what was typed)
        ok, card = self.rename(self.a, "實驗 C")
        self.assertIn("- ❌ Claude 對話名：沒送進去：沒有打進對話框", card)

    def test_a_pane_just_restarted_is_said_once_without_its_consequence(self):
        """Manager 11-20 (v0.7.x): the row said 沒送進去（…，訊息沒有送出，等一下再試）: the same thing twice, in
        brackets in brackets."""
        from bot.tmux_bridge import NotOpened
        e = NotOpened(NotOpened.PANE_GONE, "這個頻道的窗格剛重開（lap:dc-a），還在確認是不是同一個對話，訊息沒有送出，"
                                         "等一下再試。", where="lap:dc-a")
        self.bot.submit_gives = e
        ok, card = self.rename(self.a, "實驗 D")
        row, = [l for l in card.splitlines() if "Claude 對話名" in l]
        self.assertEqual(row, "- ❌ Claude 對話名：沒送進去：這個頻道的窗格剛重開（lap:dc-a），還在確認是不是同一個對話")


class Slug(unittest.TestCase):
    def test_as_discord_names_a_channel(self):
        self.assertEqual(rename.slug("  實驗 B!! "), "實驗-b")
        self.assertEqual(rename.slug("Exp_A-2"), "exp_a-2")
        self.assertEqual(rename.slug("!!!"), "")


class Command(Base):
    def test_an_attached_pane_its_channel_conversation_and_window(self):
        ok, card = self.rename(self.a, "實驗 B")
        self.assertTrue(ok)
        pane = self.bot.channels_state["1"]["pane"]["id"]
        self.assertEqual((self.a.edits, self.bot.submitted), (["實驗-b"], [(1, "/rename 實驗-b")]))
        self.assertEqual(window_name(self.work, pane), "實驗-b")
        self.assertEqual(card.splitlines(), ["✅ #a 改名為 #實驗-b", "- ✅ 頻道名", "- ✅ Claude 對話名",
                                             "- ✅ tmux window：實驗-b", "- ➖ tmux session：是你自己的 session，沒改"])
        self.assertEqual(self.bot.audits, ["[改名] #a → #實驗-b（改了：頻道名、Claude 對話名、tmux window；"
                                           "沒改：tmux session（是你自己的 session，沒改））"])
        self.assertEqual(self.bot.channels_state["1"]["named"], {"channel": "實驗-b", "window": "實驗-b"})
        self.assertEqual(self.bot.channels_state["1"]["where"]["names"]["channel"], "實驗-b")

    def test_the_bots_own_session_keeps_its_ending(self):
        ok, card = self.rename(self.b, "回測")
        self.assertTrue(ok)
        self.assertEqual(self.bot.channels_state["2"]["tmux"], tmuxio.session_name("回測", 2))
        self.assertEqual(tmux(self.botsock, "has-session", "-t", "=" + tmuxio.session_name("回測", 2)).returncode, 0)
        self.assertEqual(window_name(self.botsock, "=%s:" % tmuxio.session_name("回測", 2)), "回測")
        self.assertIn("- ✅ tmux session：" + tmuxio.session_name("回測", 2), card.splitlines())
        self.assertEqual(self.bot.channels_state["2"]["where"]["place"]["session"], tmuxio.session_name("回測", 2))

    def test_the_bots_own_session_not_open_now_is_opened_by_its_new_name(self):
        tmux(self.botsock, "kill-session", "-t", "=dc-b-0002")
        tmux(self.botsock, "new-session", "-d", "-s", "other", "sleep 300")  # (the server still there)
        ok, card = self.rename(self.b, "回測")
        self.assertEqual(self.bot.channels_state["2"]["tmux"], tmuxio.session_name("回測", 2))
        self.assertIn("- ✅ tmux session：" + tmuxio.session_name("回測", 2), card.splitlines())
        self.assertIn("- ❌ tmux window：", card, "no window to name: tried and said")
        self.assertFalse(ok)

    def test_no_answer_in_time_and_the_name_as_it_was_not_renamed(self):
        """No answer in CHANNEL_SECS: mostly discord.py sleeping out the limit, and the request that met it is not sent
        again. The channel read once, still its old name: not done (#code-review-2 on d81cd6f2, manager 11-18), and an
        update to that name later is not the bot's."""
        self.a.held = True
        with mock.patch.object(rename, "CHANNEL_SECS", 0.05):
            ok, card = self.rename(self.a, "x2")
        self.assertFalse(ok)
        lines = card.splitlines()
        self.assertEqual(lines[0], "⚠️ #a 改名，有一項沒改成")
        self.assertEqual(lines[1], "- ❌ 頻道名：Discord 限制 10 分鐘只能改兩次，頻道名先沒改，過幾分鐘再改一次")
        self.assertEqual(window_name(self.work, self.bot.channels_state["1"]["pane"]["id"]), "x2")
        self.assertFalse(self.bot.renamer.mine_said(1, "x2"))
        self.assertNotEqual((self.bot.channels_state["1"].get("named") or {}).get("channel"), "x2")

    def test_no_answer_in_time_but_renamed_after_all(self):
        self.a.held, self.bot.read_as = True, "x2"
        with mock.patch.object(rename, "CHANNEL_SECS", 0.05):
            ok, card = self.rename(self.a, "X2")
        self.assertTrue(ok)
        self.assertEqual(card.splitlines()[:2], ["✅ #a 改名為 #x2", "- ✅ 頻道名"])
        self.assertEqual(self.bot.channels_state["1"]["named"]["channel"], "x2")
        self.assertTrue(self.bot.renamer.mine_said(1, "x2"), "its update (Discord's own spelling): the bot's own")

    def test_no_answer_in_time_renamed_in_another_spelling(self):
        """Read back as Discord spells it, not as sent: the same name all the same (#code-review on b6c995fc)."""
        self.a.held, self.bot.read_as = True, "X2"
        with mock.patch.object(rename, "CHANNEL_SECS", 0.05):
            ok, card = self.rename(self.a, "x2")
        self.assertTrue(ok)
        self.assertEqual(card.splitlines()[1], "- ✅ 頻道名")

    def test_no_answer_in_time_and_the_name_not_read(self):
        """Not read either: not known; its update, should it come, is the bot's own (`mine` kept, MINE_SECS)."""
        self.a.held, self.bot.read_as = True, RuntimeError("no answer")
        with mock.patch.object(rename, "CHANNEL_SECS", 0.05):
            ok, card = self.rename(self.a, "x2")
        self.assertTrue(ok)
        lines = card.splitlines()
        self.assertEqual(lines[0], "⚠️ #a 改名，頻道名還不知道改了沒")
        self.assertEqual(lines[1], "- ⏳ 頻道名：Discord 0.05 秒內沒有回應，頻道名還不知道改了沒，過幾分鐘看一下")
        self.assertEqual(window_name(self.work, self.bot.channels_state["1"]["pane"]["id"]), "x2")
        self.assertTrue(self.bot.renamer.mine_said(1, "x2"))
        self.assertIn("；還不知道改了沒：頻道名（Discord 0.05 秒內沒有回應", self.bot.audits[-1])

    def test_a_429_is_the_same_limit(self):
        # (reached only with discord.py's max_ratelimit_timeout set: the bot sets none, so a 429 is slept out and met
        # as no answer in CHANNEL_SECS instead; #code-review on d81cd6f2)
        resp = type("R", (), {"status": 429, "reason": "Too Many Requests"})()
        self.a.refused = discord.HTTPException(resp, "rate limited")
        ok, card = self.rename(self.a, "x2")
        self.assertIn("- ❌ 頻道名：Discord 限制 10 分鐘只能改兩次", card)
        self.assertFalse(self.bot.renamer.mine_said(1, "x2"),
                         "refused, nothing renamed: the owner renaming it so by hand now is the owner's (#code-review on 74b5e7da)")

    def test_the_name_it_has_already(self):
        ok, card = self.rename(self.a, "A")
        self.assertTrue(ok)
        self.assertEqual(self.a.edits, [])
        self.assertIn("- ➖ 頻道名：已經是這個名字", card.splitlines())
        self.assertTrue(card.startswith("✅ "))

    def test_no_conversation_to_name_is_not_a_failure(self):
        self.bot.modes[id(self.bot.channels_state["1"])] = "print"
        ok, card = self.rename(self.a, "x2")
        self.assertTrue(ok)
        self.assertIn("- ➖ Claude 對話名：這個頻道不是在 tmux 裡跑的 Claude，沒有對話名", card.splitlines())
        self.assertEqual(self.bot.submitted, [])
        self.assertTrue(card.startswith("✅ #a 改名為 #x2"))

    def test_a_window_opened_in_the_owners_tmux_closed_now(self):
        """#code-review-2 on 74b5e7da: "=st:" was the owner's own window then (renamed, its automatic-rename off)."""
        tmux(self.work, "rename-window", "-t", "=st:0", "owners-editor")
        tmux(self.work, "set-option", "-w", "-t", "=st:0", "automatic-rename", "on")
        for window in ("", "2", "w1"):
            self.bot.channels_state["1"] = {"home": {"socket": self.work, "session": "st", "window": window},
                                            "session_id": "s1", "cwd": "/w"}
            ok, card = self.rename(self.a, "renamed-" + (window or "x"))
            self.assertTrue(ok, window)
            self.assertIn("- ➖ tmux window：視窗現在沒開，下次開的時候用新名字", card.splitlines(), window)
            self.assertEqual(tmux(self.work, "show-options", "-w", "-v", "-t", "=st:0", "automatic-rename").stdout.strip(),
                             "on", "the owner's window untouched")
            self.assertEqual(self.bot.channels_state["1"]["home"]["window"], "renamed-w1" if window == "w1" else window,
                             "a window opened by name: by the new one next time (by its number or none: as it was)")

    def test_another_machine_not_reached(self):
        st = self.bot.channels_state["1"]
        st["pane"] = dict(st["pane"], host="far")

        async def unreached(*a, **kw):
            return 255
        with mock.patch.object(tmuxio, "rename_window", unreached):
            ok, card = self.rename(self.a, "x2")
        self.assertIn("- ❌ tmux window：那台機器現在連不上，window 名沒改", card.splitlines())
        self.assertNotIn("window", st.get("named") or {})

    def test_nothing_done_at_all(self):
        self.bot.modes[id(self.bot.channels_state["1"])] = "print"
        resp = type("R", (), {"status": 429, "reason": "Too Many Requests"})()
        self.a.refused = discord.HTTPException(resp, "rate limited")
        tmux(self.work, "kill-server")
        ok, card = self.rename(self.a, "x2")
        self.assertEqual(card.splitlines()[0], "❌ #a 沒改成名字")

    def test_a_name_with_nothing_left(self):
        ok, card = self.rename(self.a, "!!!")
        self.assertEqual((ok, card), (False, "用法：`/rename <新名字>`（例如 `/rename 回測`）"))
        self.assertEqual(self.bot.audits, [])


class TheOthersFollow(Base):
    def test_renamed_in_discord_the_channel_left_as_it_is(self):
        self.a.name = "手改"
        asyncio.run(self.bot.renamer.follow_discord(self.a, self.bot.channels_state["1"], "a"))
        self.assertEqual(self.a.edits, [])
        self.assertEqual(self.bot.submitted, [(1, "/rename 手改")])
        self.assertEqual(window_name(self.work, self.bot.channels_state["1"]["pane"]["id"]), "手改")
        self.assertEqual(self.bot.audits, ["[改名] #a 在 Discord 改名為 #手改，跟著改：Claude 對話名、tmux window；"
                                           "沒改：tmux session（是你自己的 session，沒改）"])

    def test_the_bots_own_channel_rename_is_known_once(self):
        self.rename(self.a, "x2")
        r = self.bot.renamer
        self.assertTrue(r.mine_said(1, "x2"))
        self.assertFalse(r.mine_said(1, "x2"), "once: the next one to that name is the owner's")
        self.rename(self.a, "x3")
        self.assertFalse(r.mine_said(1, "other"), "a name of the owner's own")

    def test_a_window_named_in_tmux(self):
        self.look()
        pane = self.seen("1")["pane"]
        tmux(self.work, "rename-window", "-t", pane, "Exp 7")
        self.look()
        self.assertEqual(self.a.edits, ["exp-7"])
        self.assertEqual(self.bot.submitted, [(1, "/rename exp-7")])
        self.assertEqual(window_name(self.work, pane), "Exp 7", "the window as the owner named it")
        self.assertIn("[改名] #a 的 tmux window 在 tmux 裡改名為 Exp 7，跟著改：頻道名、Claude 對話名；"
                      "沒改：tmux session（是你自己的 session，沒改）", self.bot.audits)
        n = len(self.bot.audits)
        self.look()
        self.look()
        self.assertEqual((self.a.edits, len(self.bot.audits)), (["exp-7"], n), "taken along once")

    def test_the_bots_own_rename_is_no_owners_name(self):
        """Manager 11-18: rename-window turns automatic-rename off for the bot's own name too; the look must not take
        it back to the channel."""
        self.look()
        self.rename(self.a, "x2")
        self.a.name = "x2-by-hand"  # (renamed in Discord meanwhile: a window name taken back would undo it)
        self.look()
        self.look()
        self.assertEqual(self.a.edits, ["x2"])
        self.assertFalse([a for a in self.bot.audits if "在 tmux 裡改名" in a])

    def test_held_back_by_discord_from_tmux_is_not_tried_again(self):
        self.look()
        tmux(self.work, "rename-window", "-t", self.seen("1")["pane"], "exp7")
        self.a.held = True
        with mock.patch.object(rename, "CHANNEL_SECS", 0.05):
            self.look()
            self.a.held = False
            self.look()
        self.assertEqual(self.a.edits, [], "not retried")
        self.assertIn("沒改成：頻道名（Discord 限制 10 分鐘只能改兩次，頻道名先沒改，過幾分鐘再改一次）", self.bot.audits[-1])

    def test_a_pane_two_records_point_to_takes_nobody_along(self):
        self.bot.channels_state["3"] = {"pane": dict(self.bot.channels_state["1"]["pane"]), "pane_session": "st", "cwd": "/w"}
        self.look()
        tmux(self.work, "rename-window", "-t", self.seen("1")["pane"], "exp7")
        self.look()
        self.assertEqual((self.a.edits, self.c.edits, self.bot.submitted), ([], [], []))

    def test_tmuxs_own_name_and_the_channels_own_are_not_taken(self):
        self.look()
        pane = self.seen("1")["pane"]
        tmux(self.work, "rename-window", "-t", pane, "a")  # (the channel's name already)
        self.look()
        tmux(self.work, "set-option", "-w", "-t", pane, "automatic-rename", "on")  # (tmux names it after its program)
        self.look()
        self.assertEqual((self.a.edits, self.bot.submitted), ([], []))

    def test_a_window_opened_again_is_no_rename(self):
        self.look()
        tmux(self.work, "rename-window", "-t", self.seen("1")["pane"], "w1")
        old = dict(self.seen("1"), window="w0", pane="%77")  # (seen at another pane last time)
        new = dict(self.seen("1"), window="w1", state="here")
        st = self.bot.channels_state["1"]
        self.assertIsNone(rename.tmux_named(st, old, new, "a"))
        self.assertIsNone(rename.tmux_named(st, dict(old, pane=new["pane"], state="gone"), new, "a"))
        self.assertEqual(rename.tmux_named(st, dict(old, pane=new["pane"]), new, "a"), "w1")


class AnAdapter(Base):
    """Drill of d0f26f25, A (manager 11-20): a pane an adapter marked as its own (@altention_agent "<agent>|<pid>",
    C-198) has no conversation name: nothing typed into it (a /rename there is a message to its model), the card says
    so, on all three ways (/rename, --rename, the channel renamed in Discord). Real tmux: the pane's own process."""
    def mark(self, agent="pi", pid=None):
        st = self.bot.channels_state["1"]
        for _ in range(100):  # (just started, the pane's program reads as the shell that runs it: no adapter then)
            if tmux(self.work, "display-message", "-p", "-t", st["pane"]["id"], "#{pane_current_command}").stdout.strip() == "sleep":
                break
            time.sleep(0.05)
        tmux(self.work, "set-option", "-p", "-t", st["pane"]["id"], "@altention_agent", "%s|%s" % (agent, pid or st["pane"]["pid"]))
        return st

    def test_slash_rename_types_nothing(self):
        st = self.mark()
        ok, card = self.rename(self.a, "演練 pi")
        self.assertTrue(ok)
        self.assertEqual(self.bot.submitted, [])
        self.assertEqual(card.splitlines(), ["✅ #a 改名為 #演練-pi", "- ✅ 頻道名", "- ➖ 對話名：轉接層沒有",
                                             "- ✅ tmux window：演練-pi", "- ➖ tmux session：是你自己的 session，沒改"])
        self.assertEqual(window_name(self.work, st["pane"]["id"]), "演練-pi")
        self.assertEqual((st["pane"]["agent"], st["where"]["agent"]["kind"]), ("pi", "pi"))
        self.assertIn("沒改：對話名（轉接層沒有）", self.bot.audits[-1])

    def test_renamed_in_discord_types_nothing(self):
        st = self.mark("codex")
        self.a.name = "手改"
        asyncio.run(self.bot.renamer.follow_discord(self.a, st, "a"))
        self.assertEqual(self.bot.submitted, [])
        self.assertEqual(window_name(self.work, st["pane"]["id"]), "手改")
        self.assertEqual(st["where"]["agent"]["kind"], "codex")

    def test_by_another_claude_types_nothing(self):
        self.mark()
        from bot import deliver

        async def to(ch, text, **kw):
            pass
        with mock.patch.object(deliver, "deliver", to):
            ok, card = asyncio.run(self.bot.renamer.by_agent(self.a, self.c, {"rename": "回測", "why": "w"}))
        self.assertEqual((ok, self.bot.submitted), (True, []))
        self.assertIn("- ➖ 對話名：轉接層沒有", card)

    def test_a_mark_not_the_panes_is_no_adapter(self):
        self.mark(pid="1")  # (a pid not the pane's process nor under it)
        self.rename(self.a, "x")
        self.assertEqual(self.bot.submitted, [(1, "/rename x")])
        self.assertNotIn("agent", self.bot.channels_state["1"]["pane"])

    def test_a_mark_left_behind_is_claude_again(self):
        """Both reviews of b641f556 (manager 11-20): the adapter ended without clearing its mark, Claude Code (here a
        program that is no shell) runs in the same pane now: no adapter's any more, though it was kept as pi."""
        st = self.mark()
        self.rename(self.a, "x")
        self.assertEqual(st["where"]["agent"]["kind"], "pi")
        pane = st["pane"]["id"]
        tmux(self.work, "respawn-pane", "-k", "-t", pane, "sleep 301")  # (the pane option stays through it)
        new = self.pane_of(self.work, "st")[1]
        for _ in range(100):
            if tmux(self.work, "display-message", "-p", "-t", pane, "#{pane_current_command}").stdout.strip() == "sleep":
                break
            time.sleep(0.05)
        self.assertTrue(tmux(self.work, "show-options", "-p", "-t", pane, "-v", "@altention_agent").stdout.strip().startswith("pi|"))
        st["pane"] = dict(st["pane"], pid=new)  # (the record following it, as the look does)
        self.rename(self.a, "y")
        self.assertEqual(self.bot.submitted, [(1, "/rename y")])
        self.assertEqual(st["where"]["agent"]["kind"], "claude")
        self.assertNotIn("agent", st["pane"])

    def test_another_machine_not_answering_is_not_known(self):
        async def far(host, *args, **kw):
            return far.said
        t = {"host": "nv", "socket": "/s", "id": "%1"}
        with mock.patch("bot.remote.dcremote", far):
            far.said = (255, "")
            self.assertIsNone(asyncio.run(tmuxio.marked_agent(t, "10", "pi|11", "node")), "ssh failed")
            far.said = (2, "usage: dcremote …")
            self.assertIsNone(asyncio.run(tmuxio.marked_agent(t, "10", "pi|11", "node")), "a dcremote too old")
            far.said = (0, "0\n")
            self.assertEqual(asyncio.run(tmuxio.marked_agent(t, "10", "pi|11", "node")), "", "it answered: not one")
            far.said = (0, "1\n")
            self.assertEqual(asyncio.run(tmuxio.marked_agent(t, "10", "pi|11", "node")), "pi")

    def test_the_adapter_gone_claude_again(self):
        st = self.mark()
        self.rename(self.a, "x")
        tmux(self.work, "set-option", "-p", "-u", "-t", st["pane"]["id"], "@altention_agent")
        self.rename(self.a, "y")
        self.assertEqual(self.bot.submitted, [(1, "/rename y")])
        self.assertEqual(st["where"]["agent"]["kind"], "claude")


class TheBotsOwnWindowName(Base):
    def test_is_no_move_in_audit(self):
        """Drill of d0f26f25, B (manager 11-20): the window the bot named is not "換了位置"; the owner's still is."""
        self.look()
        self.rename(self.a, "演練")
        n = len(self.bot.audits)
        self.look()
        self.assertEqual(self.bot.audits[n:], [])
        tmux(self.work, "rename-session", "-t", "st", "s2")
        self.look()
        self.assertEqual([t for t in self.bot.audits[n:] if "換了位置" in t], ["[對帳] #演練 的窗格在 tmux 裡換了位置：st:演練 → s2:演練"])


class ByAnotherClaude(Base):
    def test_the_card_and_audit_say_whose(self):
        from bot import deliver
        posted = []

        async def to(ch, text, **kw):
            posted.append((ch.id, text))
        with mock.patch.object(deliver, "deliver", to):
            ok, card = asyncio.run(self.bot.renamer.by_agent(self.a, self.c, {"rename": "回測", "why": "使用者要的"}))
        self.assertTrue(ok)
        self.assertEqual(card.splitlines()[-1], "-# 由 #c 的 Claude 改名：使用者要的")
        self.assertEqual(posted, [(1, card)])
        self.assertTrue(self.bot.audits[-1].endswith(" · 由 #c 的 Claude：使用者要的"))

    def test_a_channel_with_no_conversation_its_name_alone(self):
        self.bot.channels_state["4"] = {"plain": True}
        ch = Chan(4, "notes")
        self.bot.chans[4] = ch
        from bot import deliver

        async def to(ch, text, **kw):
            pass
        with mock.patch.object(deliver, "deliver", to):
            ok, said = asyncio.run(self.bot.renamer.by_agent(ch, self.c, {"rename": "記錄", "why": "w"}))
        self.assertEqual((ok, ch.edits, self.bot.submitted), (True, ["記錄"], []))
        self.assertEqual(said.splitlines()[:2], ["✅ #notes 改名為 #記錄", "- ✅ 頻道名"])
        self.assertEqual(len(said.splitlines()), 3, "nothing else listed")


class Wired(unittest.TestCase):
    """Where the bot hands a rename over: --rename's post, the channel update event, /where's button."""

    def test_a_rename_post_is_answered_not_shown(self):
        from tests.test_posts import AgentAsks
        t = AgentAsks()
        t.setUp()
        got = []

        async def by_agent(ch, sender_ch, post):
            got.append((ch.id, sender_ch.id, post["rename"], post["why"]))
            return True, "card"
        t.bot.renamer = type("R", (), {"by_agent": staticmethod(by_agent)})()
        r = t.handled({"channel": "st-0", "rename": "回測", "why": "w", "from_channel": "1", "wait": 60})
        self.assertEqual((r["ok"], r["text"], got), (True, "card", [(2, 1, "回測", "w")]))
        self.assertEqual((t.b.sent, t.bot.tmux.given), ([], []))

    def updated(self, st, mine=False):
        from tests.test_setup_channel import bot, run
        _, b = bot({"7": st})
        b.is_audit = lambda ch: False
        followed = []

        class R(object):
            def mine_said(self, cid, name):
                return mine

            async def follow_discord(self, ch, st, old):
                followed.append((ch.name, old))
        b.renamer = R()
        before, after = type("C", (), {"name": "a", "id": 7})(), type("C", (), {"name": "b", "id": 7})()
        run(b.on_guild_channel_update(before, after))
        return b, followed

    def test_renamed_in_discord_by_hand_the_others_follow(self):
        st = {"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w", "where": {"place": {}}}
        b, followed = self.updated(st)
        self.assertEqual(followed, [("b", "a")])
        self.assertIn("已從 #a 改名為 #b", st["note_next"], "its Claude told as before")

    def test_the_bots_own_rename_is_not_followed_again(self):
        st = {"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w", "where": {"place": {}}}
        b, followed = self.updated(st, mine=True)
        self.assertEqual((followed, b.audits), ([], []))
        self.assertIn("note_next", st)

    def test_where_has_the_rename_button(self):
        from tests.test_setup_channel import Ch, bot, run
        _, b = bot({"7": {"tmux": "dc-a-7", "session_id": "s1", "cwd": "/w", "where": {"place": {}}}})
        b.renamer = rename.Renamer(b)
        from bot import moving
        b.mover = moving.Mover(b)  # (C-201 2c1: 〔搬到…〕 next to it)
        ch = Ch(b.guild, "a", 7)
        sent = []

        async def card(ch, st):
            return "📍 card"

        async def send(text, view=None):
            sent.append((text, view))
        b.where_card = card
        b.mode = lambda st: "tmux"
        run(b.command(ch, "!where", send))
        (text, view), = sent
        self.assertEqual(text, "📍 card")
        self.assertEqual([(i.label, i.custom_id) for i in view.children], [("改名…", "c201rn:7"), ("搬到…", "c201mv:7")])


class Button(unittest.TestCase):
    def pressed(self, user, custom_id, ch):
        from bot import config
        said = {}

        class Response(object):
            async def send_message(self, text, ephemeral=False):
                said["text"] = text

            async def send_modal(self, modal):
                said["modal"] = modal
        i = type("I", (), {"type": discord.InteractionType.component, "data": {"custom_id": custom_id},
                           "user": type("U", (), {"id": user})(), "channel": ch, "response": Response()})()
        with mock.patch.object(config, "OWNER_ID", 42):
            ours = asyncio.run(rename.Renamer(None).on_interaction(i))
        return ours, said

    def test_the_box_with_the_name_now(self):
        ch = Chan(7, "exp-a")
        ours, said = self.pressed(42, "c201rn:7", ch)
        self.assertTrue(ours)
        modal = said["modal"]
        self.assertEqual((modal.name.default, modal.ch), ("exp-a", ch))
        self.assertEqual(len(modal.children), 1, "the name alone (manager 11-18)")

    def test_only_the_owner_and_only_its_channel(self):
        self.assertEqual(self.pressed(5, "c201rn:7", Chan(7, "a"))[1], {"text": "只有擁有者能用。"})
        self.assertEqual(self.pressed(42, "c201rn:7", Chan(8, "a"))[1], {"text": "這個頻道已經不在了"})
        self.assertFalse(self.pressed(42, "menus:7:x", Chan(7, "a"))[0], "not ours")


class CommandLine(unittest.TestCase):
    def setUp(self):
        from tests.test_pauses import load
        self.dcpost = load("dcpost_c201b", "altention", "dc", "post.py")

    def build(self, *argv):
        return self.dcpost.build(list(argv), "", {"DCBOT_CHANNEL": "55"}, "host-a", "/w/proj", claude={"DCBOT_CHANNEL": "55"})

    def test_rename_with_why(self):
        p = self.build("st-0", "--rename", "回測", "--why", "使用者要的")
        self.assertEqual((p["rename"], p["why"], p["wait"], p["from_channel"], p["text"]), ("回測", "使用者要的", 60, "55", ""))

    def test_refused(self):
        for argv, said in ((("st-0", "--rename", "x"), "要帶 --why"),
                           (("st-0", "--rename", "x", "--why", "w", "還有一句"), "單獨用"),
                           (("st-0", "--rename", "x", "--why", "w", "--unpause"), "單獨用"),
                           (("st-0", "--rename", "x", "--why", "w", "--delete", "900"), "單獨用"),
                           (("st-0", "--rename"), "要給新名字")):
            with self.assertRaises(SystemExit) as e:
                self.build(*argv)
            self.assertIn(said, str(e.exception.code), argv)


if __name__ == "__main__":
    unittest.main()
