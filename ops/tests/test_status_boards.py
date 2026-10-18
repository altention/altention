"""The status plugin's boards (status/boards.py): their words from made-up state, and that nothing a file can hold
(missing, cut short, another shape) stops a round. The plugin has no tests of its own that a deploy runs: these are
here, as the usage plugin's is."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("status_boards", os.path.join(ENGINE, "status", "boards.py"))
boards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boards)

NOW = time.mktime((2026, 10, 13, 17, 6, 0, 0, 0, -1))  # local time, like the boards' own clock times


def at(hour, minute, day=13):
    return time.mktime((2026, 10, day, hour, minute, 0, 0, 0, -1))


def conv(**kw):
    st = {"kind": "conversation", "tmux": "dc-x-0001", "busy": False, "bg": 0, "context": [160000, "claude-opus-5-5"],
          "last_active": at(17, 4)}
    st.update(kw)
    return st


def rec(check, machine, status="ok", msg="", value=None):
    r = {"id": check, "machine": machine, "status": status, "msg": msg, "fix": ""}
    if value is not None:
        r["value"] = value
    return r


def healthy(machine, sync):
    return [rec("core.reachable", machine, value=machine.upper()), rec("core.engine", machine, value="abc1234"),
            rec("core.last-sync-ok", machine, value=sync)]


class World(unittest.TestCase):
    """A throwaway engine folder (the bot's state) and home (doctor's records, what was posted)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "engine", "dcbot", "state")
        os.makedirs(self.state)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        for p in (mock.patch.object(boards, "ENGINE", os.path.join(self.tmp, "engine")),
                  mock.patch.dict(os.environ, {"HOME": self.home})):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp)

    def put(self, name, data, raw=False):
        path = os.path.join(self.state, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if raw else json.dumps(data, ensure_ascii=False))

    def records(self, recs, when=None, raw=None):
        os.makedirs(boards.state_dir(), exist_ok=True)
        with open(boards.records_path(), "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps({"at": NOW - 60 if when is None else when, "records": recs}))

    def texts(self):
        return dict(boards.boards(now=NOW, here="host-a"))


class Channels(World):
    def test_the_words(self):
        self.put("channels.json", {
            "100000000000000001": conv(busy=True, context=[760000, "claude-opus-5-5"], last_active=at(17, 5)),
            "100000000000000002": conv(bg=1, context=[66000, "claude-haiku-4-5"], last_active=at(16, 45), tmux=None,
                                        pane={"socket": "/tmp/s", "id": "%1", "host": "host-b"}),
            "100000000000000003": conv(last_active=at(8, 19, day=12)),
            "100000000000000004": {"kind": "record"},  # no Claude behind it
            "100000000000000005": conv(archived=True),
            "100000000000000006": conv(tmux=None),  # a conversation channel with no session kept: not listed
        })
        self.assertEqual(self.texts()["頻道"], "\n".join([
            "- <#100000000000000001> · __**在忙**__ · context 76% · 17:05 · host-a",
            "- <#100000000000000002> · 背景工作 1 · context 33% · 16:45 · host-b",
            "- <#100000000000000003> · 閒著 · context 16% · 10-12 08:19 · host-a",
            "共 3 個對話頻道，1 個在忙"]))

    def test_only_the_most_recent_are_listed_and_the_total_stays(self):
        self.put("channels.json", {str(100000000000000040 + i): conv(last_active=at(10, i)) for i in range(20)})
        lines = self.texts()["頻道"].splitlines()
        self.assertEqual(len(lines), 16)
        self.assertTrue(lines[0].startswith("- <#100000000000000059> "), lines[0])
        self.assertEqual(lines[-1], "共 20 個對話頻道，0 個在忙；只列最近 15 個")

    def test_fields_that_are_missing_or_odd(self):
        self.put("channels.json", {
            "100000000000000001": {"kind": "conversation", "tmux": "x"},
            "100000000000000002": conv(context="many", last_active="yesterday", bg="two", pane="%1"),
            "100000000000000003": conv(context=[True, "claude-opus-5-5"], last_active=float("nan")),
            "100000000000000004": conv(context=[5, None], last_active=1e300),
            "100000000000000005": "not a channel",
            "100000000000000006": conv(context=[], pane={"host": 7}, tmux=None),
        })
        text = self.texts()["頻道"]
        lines = text.splitlines()
        self.assertEqual(lines[0], "- <#100000000000000006> · 閒著 · context 不明 · 17:04 · host-a", "a pane with no machine named: here")
        self.assertEqual([l.split(" · ", 1)[1] for l in lines[1:5]], ["閒著 · context 不明 · 時間不明 · host-a"] * 4, text)
        self.assertEqual(sorted(l[4:22] for l in lines[1:5]), ["1000000000000000%02d" % i for i in (1, 2, 3, 4)],
                         "a time that is none (1e300, NaN, words) does not pass for the most recent")
        self.assertTrue(text.endswith("共 5 個對話頻道，0 個在忙"))

    def test_no_file_a_broken_file_another_shape(self):
        want = "讀不到機器人的頻道狀態（dcbot/state/channels.json）"
        self.assertEqual(self.texts()["頻道"], want)
        self.put("channels.json", '{"100000000000000001": {"kind": "conv', raw=True)
        self.assertEqual(self.texts()["頻道"], want)
        self.put("channels.json", [1, 2])
        self.assertEqual(self.texts()["頻道"], want)
        self.put("channels.json", {})
        self.assertEqual(self.texts()["頻道"], "目前沒有對話頻道")


class Machines(World):
    def bot(self, since=None, beat=None):
        self.put("ready", "0123456789abcdef0123456789abcdef01234567 %d\n" % (at(16, 28) if since is None else since), raw=True)
        self.put("heartbeat", "%d" % (NOW - 20 if beat is None else beat), raw=True)

    def test_the_words(self):
        self.bot()
        self.put("channels.json", {"100000000000000001": conv(), "100000000000000002": conv()})
        self.records(healthy("host-a", at(17, 3)) + healthy("host-b", at(16, 52))
                     + [rec("core.reachable", "host-c", "fail", "連不上"), rec("progress.single", None)])
        self.assertEqual(self.texts()["機器和機器人"], "\n".join([
            "- host-a · 連得上 · 引擎 abc1234 · 上次同步 17:03",
            "- host-b · 連得上 · 引擎 abc1234 · 上次同步 16:52",
            "- host-c · **連不上**",
            "機器人：16:28 起來，記著 2 個對話頻道"]))

    def test_old_records_say_from_when(self):
        self.bot()
        self.records(healthy("host-a", at(15, 3)), when=at(15, 8))
        self.assertEqual(self.texts()["機器和機器人"].splitlines()[-1], "-# 機器的資料是 15:08 的，之後沒有新的檢查結果")
        self.records(healthy("host-a", at(16, 33)), when=NOW - boards.STALE + 5)
        self.assertNotIn("-#", self.texts()["機器和機器人"])

    def test_no_records_broken_records(self):
        self.bot()
        none = "-# 系統檢查還沒跑完第一輪（每 15 分鐘一次），或結果檔讀不懂"
        self.assertEqual(self.texts()["機器和機器人"], "機器人：16:28 起來\n" + none)
        for raw in ('{"at": 1, "rec', "[]", '{"at": "now", "records": []}', '{"at": 1e999, "records": []}',
                    '{"at": 5, "records": {"a": 1}}'):
            self.records(None, raw=raw)
            self.assertEqual(self.texts()["機器和機器人"].splitlines()[-1], none, raw)
        self.records([7, "x", {"machine": "host-a"}, {"id": "core.reachable", "machine": "host-a", "status": "ok"},
                      {"id": "core.engine", "machine": "host-a", "value": 12},
                      {"id": "core.last-sync-ok", "machine": "host-a", "value": "soon"}])
        self.assertEqual(self.texts()["機器和機器人"].splitlines()[0], "- host-a · 連得上 · 引擎 不明 · 上次同步 時間不明")

    def test_the_bot_without_a_start_or_a_heartbeat(self):
        self.records([])
        self.assertEqual(self.texts()["機器和機器人"], "機器人：沒有啟動紀錄（還沒通過啟動後的自我檢查，或沒在跑）；**讀不到心跳**")
        self.bot(beat=at(16, 50))
        self.assertEqual(self.texts()["機器和機器人"], "機器人：16:28 起來；**心跳停了**（最後 16:50）")
        self.put("ready", "abc notatime\n", raw=True)
        self.put("heartbeat", "soon", raw=True)
        self.assertEqual(self.texts()["機器和機器人"], "機器人：沒有啟動紀錄（還沒通過啟動後的自我檢查，或沒在跑）；**讀不到心跳**")


class Attention(World):
    def test_nothing(self):
        self.records(healthy("host-a", at(17, 3)))
        self.put("ownerless-sessions.json", {"at": NOW, "kept": []})
        self.put("channels-unreachable.json", {"at": NOW, "channels": []})
        self.assertEqual(self.texts()["需要注意的事"], "目前沒有")

    def test_the_words(self):
        fresh = "這台記下 2 則要通知的事，最新一則（2026-10-13T07:46:05+0800，sync）：引擎更新沒有跑完"
        stale = "這台記下 %d 則要通知的事，最新一則（2026-10-%02dT04:23:19+0800，watch）：連不上"
        self.records([rec("core.alerts", "host-a", "warn", fresh, 2), rec("core.alerts", "host-b", "warn", stale % (1, 8), 1),
                      rec("core.alerts", "host-c", "warn", stale % (3, 9), 3),
                      rec("core.alerts", "host-d", "warn", "這台記下 1 則要通知的事（沒寫時間）", 1),
                      rec("sync.engine", "host-b", "fail", "引擎   同步\n失敗 " + "很長" * 100),
                      rec("progress.single", None, "fail", "進度跑在兩台上"), rec("core.engine", "host-a", "ok", "不該出現"),
                      rec("dcbot.posts-failed", "host-a", "fail", "doctor 也說了一次"),
                      rec("dcbot.ownerless-sessions", "host-a", "warn", "doctor 也說了一次")])
        for i in range(2):
            self.put("posts-failed/%d.json" % i, {})
        self.put("posts-failed/.half.json", {})
        self.put("ownerless-sessions.json", {"at": NOW, "kept": [{"session": "a"}, {"session": "b"}, {"session": "c"}]})
        self.put("channels-unreachable.json", {"at": NOW, "channels": [
            {"channel": "100000000000000007", "since": NOW - 900, "events": 3, "why": "…"}]})
        self.put("turn-ends-late.json", {"at": NOW, "count": 4, "since": at(12, 1)})
        self.assertEqual(self.texts()["需要注意的事"], "\n".join([
            "- **有問題** host-b：" + ("引擎 同步 失敗 " + "很長" * 100)[:140] + "…",
            "- **有問題** 全部機器：進度跑在兩台上",
            "- host-a：" + fresh,
            "- host-d：這台記下 1 則要通知的事（沒寫時間）",
            "- 另有 4 則超過 3 天的舊提醒（最近一則 10-09）",
            "- 有 2 則通知送不出去，機器人會再試",
            "- 有 3 個對話的頻道已經不在，機器人沒有關掉它們",
            "- Discord 拿不到 1 個頻道：<#100000000000000007>",
            "- 有 4 次回覆等到上限才收尾（從 12:01 算起）"]))

    def test_at_most_fifteen_items_what_is_wrong_first(self):
        self.records([rec("w.%d" % i, "host-a", "warn", "慢 %d" % i) for i in range(14)]
                     + [rec("f.%d" % i, "host-b", "fail", "壞 %d" % i) for i in range(3)])
        self.put("turn-ends-late.json", {"at": NOW, "count": 4, "since": at(12, 1)})
        lines = self.texts()["需要注意的事"].splitlines()
        self.assertEqual(lines[:3], ["- **有問題** host-b：壞 %d" % i for i in range(3)])
        self.assertEqual(lines[3:15], ["- host-a：慢 %d" % i for i in range(12)])
        self.assertEqual(lines[15:], ["另有 3 項沒有列出"])
        self.records([rec("w.%d" % i, "host-a", "warn", "慢 %d" % i) for i in range(15)])
        os.remove(os.path.join(self.state, "turn-ends-late.json"))
        self.assertEqual(len(self.texts()["需要注意的事"].splitlines()), 15, "fifteen fit")

    def test_an_alert_three_days_old_to_the_second(self):
        msg = "這台記下 1 則要通知的事，最新一則（2026-10-10T17:0%d:00+0800，sync）：x"
        self.records([rec("core.alerts", "host-a", "warn", msg % 7, 1)])
        self.assertEqual(self.texts()["需要注意的事"], "- host-a：" + msg % 7)
        self.records([rec("core.alerts", "host-a", "warn", msg % 5, "many")])
        self.assertEqual(self.texts()["需要注意的事"], "- 另有 1 則超過 3 天的舊提醒（最近一則 10-10）")

    def test_old_or_missing_records_are_not_called_nothing(self):
        self.assertEqual(self.texts()["需要注意的事"], "-# 系統檢查還沒跑完第一輪（每 15 分鐘一次），或結果檔讀不懂")
        self.records([rec("sync.engine", "host-a", "warn", "慢")], when=at(15, 8))
        self.assertEqual(self.texts()["需要注意的事"], "- host-a：慢\n-# 機器的資料是 15:08 的，之後沒有新的檢查結果")

    def test_files_of_another_shape(self):
        self.records([rec("x.y", "host-a", "warn"), {"id": "a.b", "status": "fail", "machine": 5, "msg": None}])
        for name, data in (("ownerless-sessions.json", [1]), ("channels-unreachable.json", {"channels": "all"}),
                           ("turn-ends-late.json", {"count": "lots", "since": None})):
            self.put(name, data)
        self.put("posts-failed", "a file, not a folder", raw=True)
        self.assertEqual(self.texts()["需要注意的事"], "- **有問題** 全部機器：a.b\n- host-a：x.y")
        self.put("ownerless-sessions.json", "{", raw=True)
        self.put("channels-unreachable.json", {"channels": [5, {"since": 1}]})
        self.put("turn-ends-late.json", {"count": 2})
        self.assertEqual(self.texts()["需要注意的事"].splitlines()[2:],
                         ["- Discord 拿不到 2 個頻道", "- 有 2 次回覆等到上限才收尾（從 時間不明 算起）"])


class Waiting(World):
    def test_nothing(self):
        self.assertEqual(self.texts()["在等你的事"], "目前沒有")
        os.makedirs(os.path.join(self.state, "asks"))
        self.assertEqual(self.texts()["在等你的事"], "目前沒有")

    def test_the_words(self):
        self.put("asks/b.json", {"channel": "100000000000000002", "questions": [{}, {}, {}], "answers": {"0": "x"}, "ts": at(16, 40)})
        self.put("asks/a.json", {"channel": "100000000000000001", "kind": "plan", "questions": [{}], "answers": {}, "ts": at(9, 5, day=12)})
        self.put("asks/c.json", {"channel": "100000000000000003", "questions": "?", "ts": "then"})
        self.put("asks/d.json", '{"channel": "10', raw=True)
        self.put("asks/e.json", {"questions": [{}]})
        self.put("asks/.f.json.tmp", {"channel": "100000000000000009"})
        self.assertEqual(self.texts()["在等你的事"], "\n".join([
            "- <#100000000000000003> · 提問還沒回答完 · 從 時間不明 等到現在",
            "- <#100000000000000001> · 計畫等你核准 · 從 10-12 09:05 等到現在",
            "- <#100000000000000002> · 提問還有 2 題沒回答 · 從 16:40 等到現在"]))

    def test_a_question_that_is_closed_and_only_owed_to_its_machine_is_not_waiting_for_anyone(self):
        """#code-review 2026-10-13: the record stays until its result reaches the other machine, with "owed"; the
        bot itself takes it for closed (ask.load). Every line here must be something the owner can answer now."""
        self.put("asks/a.json", {"channel": "100000000000000001", "questions": [{}], "answers": {}, "ts": at(16, 33),
                                 "host": "host-b", "owed": {"behavior": "deny"}})
        self.put("asks/b.json", {"channel": "100000000000000002", "kind": "plan", "questions": [{}], "ts": at(16, 34), "owed": None})
        self.assertEqual(self.texts()["在等你的事"], "目前沒有")
        self.put("asks/c.json", {"channel": "100000000000000003", "questions": [{}], "answers": {}, "ts": at(16, 40), "host": "host-b"})
        self.assertEqual(self.texts()["在等你的事"], "- <#100000000000000003> · 提問還有 1 題沒回答 · 從 16:40 等到現在")

    def test_nothing_here_speaks_of_permission_dialogs(self):
        self.put("asks/a.json", {"channel": "100000000000000001", "questions": [{}], "answers": {}, "ts": at(16, 40)})
        for text in (self.texts()["在等你的事"], boards.waiting_board(os.path.join(self.tmp, "nowhere"), NOW)):
            self.assertNotIn("權限", text)
            self.assertNotIn("對話框", text)


class Round(World):
    """main(): what is handed over to the bot and when. dcpost taking a board means it is in the bot's spool, not
    on Discord (#code-review 2026-10-13), so the round looks for itself: at the bot's heartbeat, at its spool, at
    the boards it has made, and every 10 minutes at what the channel really says."""

    def setUp(self):
        super().setUp()
        self.calls, self.failing, self.reads = [], set(), []
        self.channel = "100000000000000020"
        self.now = NOW  # 17:06: not a round that reads back
        self.shown = None  # what dcread prints; None: it fails
        self.auto = True  # the stand-in bot takes a board up at once: it is made and nothing waits in the spool
        self.made = {}

        def run(cmd, **kw):
            assert os.path.basename(cmd[0]) == "altention" and cmd[1] == "dc", cmd
            cmd = [cmd[2]] + cmd[3:]  # ["altention", "dc", "post", …] -> ["post", …]
            if cmd[0] == "read":
                self.reads.append(cmd[1:])
                return mock.Mock(returncode=0 if self.shown is not None else 1, stdout=self.shown or "")
            self.calls.append(cmd)
            if cmd[3] in self.failing:
                return mock.Mock(returncode=1, stdout="dcpost：不行")
            if self.auto:
                self.bot_makes(cmd[3])
            return mock.Mock(returncode=0, stdout="")
        for p in (mock.patch.object(boards.subprocess, "run", run),
                  mock.patch.object(boards, "settings", lambda: (self.channel, "host-a")),
                  mock.patch.object(boards.time, "time", lambda: self.now)):
            p.start()
            self.addCleanup(p.stop)
        self.records(healthy("host-a", at(17, 3)))
        self.put("ready", "0123456789abcdef0123456789abcdef01234567 %d\n" % at(16, 28), raw=True)
        self.beat()
        self.save()

    def beat(self, when=None):
        self.put("heartbeat", "%d" % (self.now - 20 if when is None else when), raw=True)

    def save(self, **dashboard):
        """The bot's channels.json: one conversation, and the dashboard channel with the boards it has made."""
        st = {"kind": "record", "boards": {n: [i] for n, i in self.made.items()}}
        st.update(dashboard)
        self.put("channels.json", {"100000000000000001": conv(), self.channel: st})

    def bot_makes(self, name):
        self.made.setdefault(name, 100000000000000030 + len(self.made))
        self.save()

    def spool(self, name, channel=None, n=0):
        self.put("posts/%020d-%d.json" % (n, n), {"channel": channel or self.channel, "board": name, "text": "…"})

    def step(self, minutes=2):
        """The next round, `minutes` later, the bot still running."""
        self.now += 60 * minutes
        self.tick()
        self.calls = []
        return self.main()

    def tick(self):
        """The world a moment later: the bot's heartbeat and doctor's records are fresh."""
        self.beat()
        self.records(healthy("host-a", at(17, 3)), when=self.now - 60)

    def main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = boards.main(list(argv))
        return code, out.getvalue()

    def names(self):
        return [c[3] for c in self.calls]

    def screen(self, **other):
        """What dcread prints for the boards as last handed over (`other`: a board saying something else)."""
        with open(boards.posted_path(), encoding="utf-8") as f:
            texts = {n: r["text"] for n, r in json.load(f)["boards"].items()}
        texts.update(other)
        return "\n".join("[10-13 17:00:00] 機器人 (id %d)\n📌 **%s**\n-# 更新於 10-13 17:06\n%s\n" % (self.made[n], n, t)
                         for n, t in texts.items() if n in self.made)

    def test_the_four_are_made_one_a_round_in_order_each_only_once_the_one_before_is_there(self):
        self.auto = False
        self.assertEqual(self.main(), (0, ""))
        first = self.calls[0]
        self.assertEqual((os.path.basename(first[0]), first[1:4], len(first)), ("post", [self.channel, "--board", "頻道"], 5))
        self.assertTrue(first[4].startswith("- <#100000000000000001> · 閒著"), first[4])
        self.assertEqual(self.names(), ["頻道"], "one board a round: four at once are posted by the bot in any order")
        self.spool("頻道")
        self.step()
        self.assertEqual(self.names(), [], "handed over, still in the spool: nothing behind it")
        os.remove(os.path.join(self.state, "posts", "%020d-0.json" % 0))
        self.step()
        self.assertEqual(self.names(), [], "taken up but not in the bot's state yet: the next waits")
        self.bot_makes("頻道")
        for want in ORDER_REST:
            self.step()
            self.assertEqual(self.names(), [want])
            self.step()
            self.assertEqual(self.names(), [], "%s is not there yet" % want)
            self.bot_makes(want)
        self.step()
        self.assertEqual(self.names(), [])

    def test_a_board_that_never_appears_is_handed_over_again_after_a_while(self):
        self.auto = False
        self.main()
        for _ in range(3):  # 2, 4, 6 minutes
            self.step()
            self.assertEqual(self.names(), [])
        self.step()
        self.assertEqual(self.names(), ["頻道"], "more than MAKING: the bot dropped it, or Discord refused")

    def test_unchanged_boards_are_not_handed_over_again_and_a_changed_one_alone_is(self):
        self.now = at(16, 59)
        for want in boards.ORDER:  # 17:01 … 17:07
            self.step()
            self.assertEqual(self.names(), [want])
        for _ in range(3):  # 17:11, 17:13, 17:15
            self.assertEqual(self.step(4 if _ == 0 else 2), (0, ""))
            self.assertEqual(self.calls, [], "nothing changed: nothing handed over, so no board's 更新於 moves")
        self.put("asks/a.json", {"channel": "100000000000000001", "questions": [{}], "answers": {}, "ts": at(17, 0)})
        self.step()
        self.assertEqual(self.names(), ["在等你的事"])
        self.assertEqual(self.reads, [], "none of these rounds was at a minute that reads back")

    def all_made(self):
        """The four made and handed over, the clock at 17:14."""
        for _ in boards.ORDER:
            self.main()
            self.now += 120
            self.tick()
        self.calls, self.reads = [], []
        self.assertEqual(sorted(self.made), sorted(boards.ORDER))

    def test_while_the_bot_is_away_nothing_is_handed_over_and_when_it_is_back_the_latest_is_once(self):
        self.all_made()
        self.put("asks/a.json", {"channel": "100000000000000001", "questions": [{}], "answers": {}, "ts": at(17, 0)})
        for _ in range(5):
            self.now += 120
            self.calls = []
            self.put("heartbeat", "%d" % at(17, 12), raw=True)  # stopped
            self.assertEqual(self.main(), (0, ""))
            self.assertEqual(self.calls, [], "no pile in the spool for the bot to race through when it is back")
        os.remove(os.path.join(self.state, "heartbeat"))
        self.main()
        self.assertEqual(self.calls, [], "no heartbeat at all is not a running bot")
        os.remove(os.path.join(self.state, "asks", "a.json"))
        self.put("asks/b.json", {"channel": "100000000000000001", "kind": "plan", "questions": [{}], "answers": {}, "ts": at(17, 20)})
        self.step()
        self.assertEqual(self.names(), ["在等你的事"])
        self.assertIn("計畫等你核准", [c[4] for c in self.calls if c[3] == "在等你的事"][0], "the latest, not what it was meanwhile")

    def test_a_version_still_in_the_spool_is_not_followed_by_another(self):
        self.all_made()
        self.put("asks/a.json", {"channel": "100000000000000001", "questions": [{}], "answers": {}, "ts": at(17, 0)})
        self.spool("在等你的事", n=1)
        self.spool("在等你的事", channel="100000000000000021", n=2)  # another channel's board of the same name: not ours
        self.spool("每天", n=3)
        self.put("posts/.half.json", "{", raw=True)
        self.put("posts/%020d-9.json" % 9, "{", raw=True)
        self.step()
        self.assertEqual(self.names(), [])
        os.remove(os.path.join(self.state, "posts", "%020d-1.json" % 1))
        self.step()
        self.assertEqual(self.names(), ["在等你的事"])

    def test_every_ten_minutes_the_channel_is_read_back_and_a_board_that_says_something_else_is_handed_over_again(self):
        self.all_made()
        self.shown = self.screen()
        self.now = at(17, 18)
        self.step()  # 17:20
        self.assertEqual((self.reads, self.names()), ([[self.channel, str(boards.READ_BACK)]], []), "one read, all as handed over")
        self.shown = self.screen(**{"在等你的事": "- <#100000000000000001> · 提問還有 1 題沒回答 · 從 16:00 等到現在"})
        self.reads = []
        for _ in range(4):  # 17:22 … 17:28
            self.step()
            self.assertEqual((self.reads, self.names()), ([], []))
        self.step()  # 17:30
        self.assertEqual((len(self.reads), self.names()), (1, ["在等你的事"]), "the channel was left saying an old thing")
        self.shown = self.screen()
        del self.made["機器和機器人"]  # its message is gone from what the channel shows (deleted by hand)
        shown = self.screen()
        self.made["機器和機器人"] = 100000000000000031
        self.shown = shown
        self.now = at(17, 38)
        self.step()  # 17:40
        self.assertEqual(self.names(), ["機器和機器人"])

    def test_what_was_just_handed_over_is_not_judged_before_the_bot_had_time(self):
        self.all_made()
        self.now = at(17, 17)
        self.put("asks/a.json", {"channel": "100000000000000001", "questions": [{}], "answers": {}, "ts": at(17, 0)})
        self.step()  # 17:19
        self.assertEqual(self.names(), ["在等你的事"])
        self.shown = self.screen(**{"在等你的事": "目前沒有"})  # the bot has not edited it yet
        self.step(1)  # 17:20, a reading round, 60 s after handing over
        self.assertEqual(self.names(), [])

    def test_when_reading_back_does_not_work_a_board_is_handed_over_again_after_its_fallback(self):
        self.all_made()  # dcread fails throughout
        start = self.now
        seen = []
        while self.now - start < 6 * 3600 + 20 * 60:
            self.step(10)
            seen += [(n, int(self.now - start) // 60) for n in self.names()]
        waits = [m for n, m in seen if n == "在等你的事"]
        self.assertEqual(len(waits), 12, "every 30 minutes or so: %r" % waits)
        self.assertTrue(all(30 <= b - a <= 40 for a, b in zip(waits, waits[1:])), waits)
        for name in boards.ORDER[:3]:
            self.assertEqual(len([m for n, m in seen if n == name]), 1, "%s: once in six hours" % name)

    def test_seen_to_be_right_counts_so_nothing_is_handed_over_blindly(self):
        self.all_made()
        self.now = at(17, 20) - 600
        for _ in range(40):  # nearly seven hours of rounds every 10 minutes, each reading back what was handed over
            self.shown = self.screen()
            self.step(10)
            self.assertEqual(self.names(), [])
        self.assertEqual(len(self.reads), 40)

    def test_a_board_that_could_not_be_handed_over_is_tried_again(self):
        self.failing = {"機器和機器人"}
        self.main()
        code, said = self.step()
        self.assertEqual(code, 0)
        self.assertIn("「機器和機器人」貼不出去（altention dc post 結束碼 1：dcpost：不行）", said)
        self.assertEqual(self.names(), ["機器和機器人"])
        self.failing = set()
        self.step()
        self.assertEqual(self.names(), ["機器和機器人"], "and only then the ones after it")
        self.step()
        self.assertEqual(self.names(), ["需要注意的事"])

    def test_dcpost_or_dcread_missing_or_hanging_stops_nothing(self):
        def run(cmd, **kw):
            raise boards.subprocess.TimeoutExpired(cmd, 60)
        with mock.patch.object(boards.subprocess, "run", run):
            code, said = self.main()
        self.assertEqual(code, 0)
        self.assertIn("「頻道」貼不出去", said)
        self.assertFalse(os.path.exists(boards.posted_path()))
        self.all_made()
        self.now = at(17, 18)
        with mock.patch.object(boards.subprocess, "run", run):
            self.assertEqual(self.step(), (0, ""))

    def test_the_bots_state_unreadable_or_without_the_channel_still_makes_them_one_a_round(self):
        self.auto = False
        for broken in ("{", json.dumps({"100000000000000001": conv()}), json.dumps({self.channel: "x"})):
            if os.path.exists(boards.posted_path()):
                os.remove(boards.posted_path())
            self.put("channels.json", broken, raw=True)
            got = []
            for _ in boards.ORDER:
                self.step()
                got.append(self.names())
            self.assertEqual(got, [[n] for n in boards.ORDER], broken)
            self.reads = []
            self.now += 240
            while time.localtime(self.now + 120).tm_min % 10:
                self.now += 120
            self.step()  # a reading round, well within the fallback
            self.assertEqual((self.names(), self.reads), ([], []), "which message is which cannot be told: not read back")

    def test_no_channel_set_skips_the_round(self):
        with mock.patch.object(boards, "settings", lambda: (None, "實例設定沒有 status.channel（看板頻道的 ID）")):
            code, said = self.main()
        self.assertEqual((code, self.calls), (0, []))
        self.assertEqual(said, "status：實例設定沒有 status.channel（看板頻道的 ID），這一輪跳過\n")

    def test_another_channel_or_a_broken_record_of_what_was_handed_over_starts_again(self):
        self.all_made()
        old, self.channel, self.made = self.channel, "100000000000000021", {}
        self.save()
        self.step()
        self.assertEqual(([c[1] for c in self.calls], self.names()), ([self.channel], ["頻道"]))
        self.channel, self.made = old, {n: 100000000000000030 + i for i, n in enumerate(boards.ORDER)}
        self.save()
        for raw in ("{", "[]", json.dumps({"channel": self.channel, "boards": [1]}),
                    json.dumps({"channel": self.channel, "boards": {"頻道": "old format", "在等你的事": {"text": 5, "at": "x"}}})):
            with open(boards.posted_path(), "w") as f:
                f.write(raw)
            self.step()
            self.assertEqual(self.names(), list(boards.ORDER), raw)

    def test_print_hands_over_and_writes_nothing(self):
        code, said = self.main("--print")
        self.assertEqual((code, self.calls, self.reads), (0, [], []))
        self.assertFalse(os.path.exists(boards.posted_path()))
        for name in boards.ORDER:
            self.assertIn("📌 %s\n" % name, said)
        self.assertEqual(self.main("--post")[0], 2)
        self.assertEqual(self.calls, [])


ORDER_REST = boards.ORDER[1:]


class ReadBack(unittest.TestCase):
    def test_says(self):
        text = "- <#100000000000000001> · 閒著 · context 16% · 17:04 · host-a\n共 1 個對話頻道，0 個在忙"
        shown = "📌 **頻道**\n-# 更新於 10-13 17:06\n%s\n"
        self.assertTrue(boards.says(shown % text, text))
        self.assertTrue(boards.says(shown % (text + "\n"), text + "  "))
        self.assertFalse(boards.says(shown % text.replace("16%", "17%"), text))
        self.assertFalse(boards.says("", text))
        busy = text.replace("閒著", "__**在忙**__")  # bold and underlined: read back as it was handed over
        self.assertTrue(boards.says(shown % busy, busy))
        self.assertFalse(boards.says(shown % busy.replace("__", ""), busy))
        self.assertFalse(boards.says(text, text), "not a board's message")
        self.assertFalse(boards.says("📌 **頻道**\n" + text, text))
        long = "- " + "很長" * 2000
        self.assertTrue(boards.says(shown % long[:1500], long), "dcread gives 1500 characters of a message: its beginning is compared")
        self.assertFalse(boards.says(shown % ("x" + long[:1500]), long))

    def test_reading_what_dcread_prints(self):
        out = ("[10-04 17:34:37] 機器人 (id 100000000000000031)\n📌 **每天**\n-# 更新於 10-13 17:07\n- a\n- b\n\n"
               "[10-13 17:06:00] 機器人 (id 100000000000000032)\n📌 **頻道**\n-# 更新於 10-13 17:06\n目前沒有對話頻道\n")
        for stdout, code, want in ((out, 0, ["100000000000000031", "100000000000000032"]), (out, 1, None), ("", 0, None),
                                   ("dcread：找不到\n", 0, None)):
            with mock.patch.object(boards.subprocess, "run", lambda cmd, **kw: mock.Mock(returncode=code, stdout=stdout)):
                got = boards.read_back("100000000000000020")
            self.assertEqual(sorted(got) if got else got, want)
        with mock.patch.object(boards.subprocess, "run", lambda cmd, **kw: mock.Mock(returncode=0, stdout=out)):
            got = boards.read_back("100000000000000020")
        self.assertTrue(boards.says(got["100000000000000032"], "目前沒有對話頻道"))
        self.assertTrue(boards.says(got["100000000000000031"], "- a\n- b"))


class Harmless(World):
    def test_words_from_elsewhere_break_nothing_and_call_nobody(self):
        bad = "壞了 ```sh\nrm``` @everyone @here <@100000000000000001> <@&100000000000000002> <@!100000000000000003> x@y"
        self.assertEqual(boards.harmless(bad),
                         "壞了 '''sh\nrm''' @\u200beveryone @\u200bhere <\u200b@100000000000000001> <\u200b@&100000000000000002> "
                         "<\u200b@!100000000000000003> x@y")
        self.put("channels.json", {"100000000000000001": conv(tmux=None, pane={"host": "@everyone```"}),
                                   "<@&100000000000000002>": conv()})
        self.put("asks/a.json", {"channel": "@here", "questions": [{}], "answers": {}, "ts": at(16, 40)})
        self.put("channels-unreachable.json", {"channels": [{"channel": "```"}]})
        self.records([rec("core.reachable", "@here", value="x"), rec("core.engine", "@here", value="```<@1>"),
                      rec("core.reachable", "<@&100000000000000002>", "fail", bad), rec("x.y", "```", "warn", bad)])
        whole = "\n".join(self.texts().values())
        for word in ("```", "@everyone", "@here", "<@"):
            self.assertNotIn(word, whole)
        self.assertIn("- <#100000000000000001> · 閒著 · context 16% · 17:04 · @\u200beveryone'''", whole)
        self.assertIn("- <\u200b@&100000000000000002> · 閒著", whole, "what is not an id is not made a channel mention")

    def test_only_ids_become_channel_mentions(self):
        self.assertEqual(boards.mention("100000000000000001"), "<#100000000000000001>")
        self.assertEqual(boards.mention(100000000000000001), "<#100000000000000001>")
        for odd in ("12> @everyone <#1", "１２３", "", "abc"):
            self.assertNotIn("<#", boards.mention(odd), odd)


class Settings(World):
    def test_the_channel_comes_from_the_instance_or_the_round_is_skipped(self):
        from altention import config
        for value, want in (("100000000000000020", "100000000000000020"), (100000000000000020, "100000000000000020"),
                            (None, None), ("", None), (True, None), (["x"], None)):
            with mock.patch.object(config, "setting", lambda path, default=None: value), \
                    mock.patch.object(config, "this_machine", lambda: "host-a"):
                self.assertEqual(boards.settings()[0], want, repr(value))

        def broken(path, default=None):
            raise ValueError("plugins.json 壞了")
        with mock.patch.object(config, "setting", broken):
            self.assertEqual(boards.settings(), (None, "實例設定讀不出來（ValueError: plugins.json 壞了）"))


if __name__ == "__main__":
    unittest.main()
