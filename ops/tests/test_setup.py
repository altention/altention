"""ops/setup.py (C-48): #setup's questions in order, each answer written nested into the instance, read back and
committed; values not offered refused; done only when nothing is left, then install.sh, the boards and the bot's
#chat. A throwaway HOME and instance; nothing reaches Discord, the crontab or Claude Code."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from ops import setup


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.org"] + list(args),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True).stdout


class Rig(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 setup ")
        self.inst = os.path.join(self.home, "實例")
        os.makedirs(self.inst)
        os.makedirs(os.path.join(self.home, ".config", "altention"))
        with open(os.path.join(self.home, ".config", "altention", "host"), "w") as f:
            f.write("box\n")
        self.env = mock.patch.dict(os.environ, {"HOME": self.home, "ALT_INSTANCE": self.inst})
        self.env.start()
        self.write({"format": 1, "core": {"timezone": "UTC"}, "dcbot": {"default_permission": "auto"},
                    "setup": {"done": False}})
        git(self.inst, "init", "-q")
        git(self.inst, "config", "user.name", "t")
        git(self.inst, "config", "user.email", "t@example.org")
        git(self.inst, "add", "-A")
        git(self.inst, "commit", "-qm", "init")
        self.modes = mock.patch.object(setup, "claude_modes", return_value=["acceptEdits", "auto", "bypassPermissions",
                                                                            "manual"])
        self.modes.start()
        self.posted = []
        self.dcctl = mock.patch.object(setup, "dcctl", self.fake_dcctl)
        self.dcctl.start()

    def tearDown(self):
        self.dcctl.stop()
        self.modes.stop()
        self.env.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def fake_dcctl(self, *args, inp=None, timeout=120):
        self.posted.append((args, inp))
        return subprocess.CompletedProcess(args, 0, "")

    def write(self, d):
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump(d, f)

    def settings(self):
        with open(os.path.join(self.inst, "plugins.json")) as f:
            return json.load(f)

    def run_(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = setup.main(list(argv))
        return code, out.getvalue()

    def status(self):
        code, out = self.run_("status", "--json")
        self.assertEqual(code, 0, out)
        return json.loads(out)

    def answer(self, key, value, code=0):
        got, out = self.run_("answer", key, str(value))
        self.assertEqual(got, code, out)
        return out

    def basics(self):
        for k, v in (("core.timezone", "Asia/Taipei"), ("dcbot.default_permission", "auto"), ("dcbot.talk_limit", "10"),
                     ("core.update", "notify")):
            self.answer(k, v)


class Order(Rig):
    def test_the_questions_in_order_and_each_answer_moves_on(self):
        self.assertEqual(self.status()["next"], "install", "C-57 S2: new or back from the backups, first")
        self.assertEqual(self.status()["left"][:6],
                         ["install", "core.timezone", "dcbot.default_permission", "dcbot.talk_limit", "core.update", "plugins"])
        self.answer("install", "new")
        self.assertEqual(self.status()["next"], "core.timezone")
        self.basics()
        self.answer("plugins", "keep")
        self.answer("plugins.planner", "on")
        self.assertEqual(self.status()["left"], ["planner.times", "usage.plan", "progress.every", "backup"])
        code, out = self.run_("questions", "--json")
        asked = json.loads(out)
        self.assertEqual([q["key"] for q in asked], ["planner.times", "usage.plan", "progress.every", "backup"])
        self.assertTrue(all(len(q["options"]) == len(q["values"]) for q in asked))

    def test_answers_go_in_nested_read_back_and_committed(self):
        self.basics()
        d = self.settings()
        self.assertEqual(d["core"]["timezone"], "Asia/Taipei")
        self.assertEqual(d["dcbot"], {"default_permission": "auto", "talk_limit": 10})
        self.assertEqual(d["core"]["update"], "notify")
        self.assertNotIn("core.timezone", d, "never a dotted key")
        self.assertEqual(d["setup"]["answered"]["dcbot.talk_limit"], 10)
        self.assertEqual(git(self.inst, "status", "--porcelain"), "")
        self.assertIn("setup: core.update", git(self.inst, "log", "--format=%s"))

    def test_values_not_offered_are_refused_and_nothing_is_written(self):
        before = self.settings()
        self.answer("core.timezone", "Mars/Olympus", code=1)
        self.answer("core.timezone", "../../etc/passwd", code=1)
        self.answer("dcbot.default_permission", "yolo", code=1)
        self.answer("dcbot.talk_limit", "7", code=1)
        self.answer("core.update", "branch", code=1, )
        self.assertEqual(self.settings(), before)
        self.answer("planner.times", "suggested", code=1)  # not asked yet: the secretary comes first

    def test_a_mode_this_claude_code_does_not_have_is_refused(self):
        with mock.patch.object(setup, "claude_modes", return_value=["acceptEdits", "bypassPermissions", "default"]):
            out = self.answer("dcbot.default_permission", "auto", code=1)
        self.assertIn("claude update", out)

    def test_an_instance_without_setup_asks_nothing(self):
        self.write({"format": 1})
        self.assertEqual(self.status()["setup"], "none")
        self.answer("core.timezone", "UTC", code=1)
        self.assertEqual(self.run_("done")[0], 0)
        self.assertEqual(self.posted, [], "nothing asked of the bot")


class Parts(Rig):
    def test_some_asks_each_part_and_keeps_what_a_kept_part_needs(self):
        self.basics()
        self.answer("plugins", "some")
        left = self.status()["left"]
        self.assertIn("plugin.chatlog", left)
        self.assertIn("plugin.progress", left)
        self.assertNotIn("plugin.planner", left, "the secretary has its own question")
        self.answer("plugin.chatlog", "off")
        self.assertEqual(self.settings()["plugins"]["chatlog"], {"disabled_on": ["box"]})
        out = self.answer("plugin.progress", "keep")
        self.assertIn("注意：", out)
        self.assertNotIn("chatlog", self.settings().get("plugins", {}), "kept: progress reads its records")
        self.assertEqual(self.status()["answered"]["plugin.chatlog"], "keep")
        self.answer("plugin.progress", "off")
        self.answer("plugin.chatlog", "off")
        self.assertEqual(self.settings()["plugins"]["chatlog"], {"disabled_on": ["box"]})
        self.assertNotIn("progress.every", self.status()["left"], "progress is off: no frequency")
        self.answer("plugins", "keep")
        self.assertNotIn("plugins", self.settings(), "keep: every part back on, no empty disabled_on left")

    def test_the_secretary_and_its_times(self):
        self.basics()
        self.answer("plugins", "keep")
        self.answer("plugins.planner", "off")
        self.assertEqual(self.settings()["plugins"]["planner"], {"disabled_on": ["box"]})
        self.assertNotIn("planner.times", self.status()["left"])
        self.answer("plugins.planner", "on")
        self.assertNotIn("plugins", self.settings())
        self.answer("planner.times", "suggested")
        self.assertEqual(self.settings()["planner"]["briefings"], {
            "morning": {"at": "08:00", "days": "daily"}, "evening": {"at": "22:00", "days": "daily"},
            "weekly": {"at": "21:00", "days": ["sun"]}})
        self.answer("planner.times", "custom")
        self.assertEqual(self.status()["left"][:1], ["usage.plan"], "the three were answered by the suggestion")

    def test_times_chosen_one_by_one(self):
        self.basics()
        self.answer("plugins", "keep")
        self.answer("plugins.planner", "on")
        self.answer("planner.times", "custom")
        self.assertEqual(self.status()["left"][:3],
                         ["planner.briefings.morning", "planner.briefings.evening", "planner.briefings.weekly"])
        self.answer("planner.briefings.morning", "7:30")
        self.answer("planner.briefings.evening", '{"at": "21:00", "days": "daily"}')
        self.answer("planner.briefings.weekly", "21:00", code=1)  # the day is needed
        self.answer("planner.briefings.weekly", "sat 10:00")
        self.answer("planner.briefings.morning", "25:00", code=1)
        self.answer("planner.briefings.morning", "mon 07:00", code=1)
        b = self.settings()["planner"]["briefings"]
        self.assertEqual(b["morning"], {"at": "07:30", "days": "daily"})
        self.assertEqual(b["weekly"], {"at": "10:00", "days": ["sat"]})

    def test_plan_and_progress(self):
        self.basics()
        self.answer("plugins", "keep")
        self.answer("plugins.planner", "off")
        self.answer("usage.plan", "Max 5x")
        self.assertEqual(self.settings()["usage"], {"plan": ["Max 5x", 5], "subscription_limits": True})
        self.answer("usage.plan", "none")
        self.assertEqual(self.settings()["usage"], {"subscription_limits": False})
        self.answer("usage.plan", "Team=5")
        self.assertEqual(self.settings()["usage"]["plan"], ["Team", 5])
        self.answer("usage.plan", "Team", code=1)
        self.answer("progress.every", "30m")
        self.assertEqual(self.settings()["plugins"]["progress"], {"schedules": {"run": {"when": {"every": "30m"}}}})
        self.answer("progress.every", "5m", code=1)

    def test_backup_now_or_later(self):
        self.basics()
        self.answer("plugins", "keep")
        self.answer("plugins.planner", "off")
        self.answer("usage.plan", "Pro")
        self.answer("progress.every", "10m")
        self.answer("backup", "now")
        q = [x for x in json.loads(self.run_("questions", "--json")[1]) if x["key"] == "backup.items"][0]
        self.assertNotIn("planner", q["values"], "the secretary is off: not offered")
        self.answer("backup.items", "instance,secrets", code=1)
        self.answer("backup.items", "")
        self.assertEqual(self.status()["answered"]["backup"], "later", "nothing ticked: later")
        self.assertEqual(self.status()["left"], [])


class Done(Rig):
    def all_answered(self, backup="later"):
        self.basics()
        self.answer("plugins", "keep")
        self.answer("plugins.planner", "off")
        self.answer("usage.plan", "Pro")
        self.answer("progress.every", "10m")
        self.answer("backup", backup)

    def patched(self):
        ran = []

        def script(argv, timeout=1800):
            ran.append(argv)
            return subprocess.CompletedProcess(argv, 0, "ok core.done 安裝跑完了\n")
        return ran, mock.patch.multiple(setup, script=script, dashboard=mock.DEFAULT, guide=mock.DEFAULT,
                                        secretary=mock.DEFAULT)

    def test_refused_while_anything_is_left(self):
        self.basics()
        code, out = self.run_("done")
        self.assertEqual(code, 1, out)
        self.assertIn("plugins", out)
        self.assertIs(self.settings()["setup"]["done"], False)
        self.assertEqual(self.posted, [])

    def test_done_installs_then_asks_the_bot_for_chat(self):
        self.all_answered()
        ran, p = self.patched()
        with p as m:
            code, out = self.run_("done")
        self.assertEqual(code, 0, out)
        self.assertIs(self.settings()["setup"]["done"], True)
        self.assertEqual(ran, [[os.path.join(setup.ENGINE, "install.sh")]])
        m["dashboard"].assert_called_once()
        m["guide"].assert_called_once()
        m["secretary"].assert_not_called()  # turned off
        (args, inp), = self.posted
        self.assertEqual(args, ("post",))
        self.assertEqual(json.loads(inp), {"channel": "chat", "open_chat": True, "text": ""})
        self.assertIn("setup: done", git(self.inst, "log", "--format=%s"))
        code, out = self.run_("questions", "--json")
        self.assertEqual([q["key"] for q in json.loads(out)], ["tour"], "after done: only the tour")

    def test_the_chat_channels_own_name(self):
        self.all_answered()
        d = self.settings()
        d["dcbot"]["channels"] = {"chat": "閒聊"}
        self.write(d)
        _, p = self.patched()
        with p:
            self.assertEqual(self.run_("done")[0], 0)
        self.assertEqual(json.loads(self.posted[-1][1])["channel"], "閒聊")

    def test_install_failing_stops_before_the_bot_is_asked(self):
        self.all_answered()

        def script(argv, timeout=1800):
            return subprocess.CompletedProcess(argv, 1, "fail core.done 安裝沒跑完\n")
        with mock.patch.object(setup, "script", script):
            code, out = self.run_("done")
        self.assertEqual(code, 2, out)
        self.assertIs(self.settings()["setup"]["done"], True, "written first: the run again goes on from there")
        self.assertEqual(self.posted, [])

    def test_backups_chosen_must_be_set_first(self):
        self.all_answered(backup="now")
        self.answer("backup.items", "instance")
        code, out = self.run_("done")
        self.assertEqual(code, 1, out)
        self.assertIn("backup-set", out)

    def test_the_tour_is_answered_after_done(self):
        self.all_answered()
        self.answer("tour", "later", code=1)  # not done yet
        _, p = self.patched()
        with p:
            self.run_("done")
        self.answer("tour", "start", code=1)  # setup.py tutorial does that
        self.answer("tour", "no")
        self.assertEqual(self.status()["tour"], "no")


class Boards(Rig):
    """setup.py done's #dashboard step judges usage/collect.py by its exit status, not by its output: a board it did
    post prints dcctl's 「已送出到 #…」, and its notes go to stderr, both into the output script() gives (Z acceptance
    of 486fb69, manager 2026-10-20: every new install stopped at 「用量看板沒貼上：已送出到 #5000」)."""

    def collect(self, body, code):
        eng = tempfile.mkdtemp(prefix="引擎 ")
        self.addCleanup(shutil.rmtree, eng, True)
        os.makedirs(os.path.join(eng, "usage"))
        with open(os.path.join(eng, "usage", "collect.py"), "w") as f:
            f.write("import sys\n%s\nsys.exit(%d)\n" % (body, code))
        rows = [("usage", "/x", {}, "on", ""), ("status", "/x", {}, "elsewhere", "")]  # the status boards not on this machine
        return mock.patch.multiple(setup, ENGINE=eng, channel_ids=mock.Mock(return_value=["105"])), \
            mock.patch.object(setup.plugin, "on_this_machine", return_value=(rows, []))

    def test_a_board_posted_is_posted_whatever_it_printed(self):
        with open(os.path.join(setup.ENGINE, "dcbot", "tools", "dcctl"), encoding="utf-8") as f:
            self.assertIn('print(moved or "已送出到 #%s"', f.read(), "the line dcctl post prints for each board")
        a, b = self.collect('print("已送出到 #105")\nprint("2026-10-20 usage：讀不到用量額度：Not logged in", file=sys.stderr)', 0)
        out = io.StringIO()
        with a, b:
            self.assertEqual(setup.dashboard(out), 0)
        self.assertIn("用量看板貼了", out.getvalue())
        self.assertEqual(self.settings()["usage"]["channel"], "105")

    def test_collect_failing_is_said_with_its_reason(self):
        a, b = self.collect('sys.stderr.write("usage：機器清單讀不出來，這一輪不收集\\n")', 1)
        with a, b, self.assertRaises(setup.Failed) as e:
            setup.dashboard(io.StringIO())
        self.assertIn("用量看板沒貼上：usage：機器清單讀不出來", str(e.exception))
        self.assertNotIn("channel", self.settings().get("usage", {}), "taken back (C-68): the next done writes it and posts")
        a, b = self.collect("pass", 3)
        with a, b, self.assertRaises(setup.Failed) as e:
            setup.dashboard(io.StringIO())
        self.assertIn("結束碼 3", str(e.exception))


class TheBotsClaude(unittest.TestCase):
    """The modes are asked of the Claude Code the bot starts (CLAUDE_BIN, ~/.local/bin/claude, then PATH), not of
    whatever `claude` PATH finds first (#code-review of 9a72d2a: no ~/.local/bin on PATH asked nothing)."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 claude ")
        self.addCleanup(shutil.rmtree, self.home, True)

    def tool(self, path, choices):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write('#!/bin/sh\necho \'  --permission-mode <mode>   Permission mode\'\n'
                    'echo \'          (choices: %s)\'\n' % ", ".join('"%s"' % c for c in choices))
        os.chmod(path, 0o755)
        return path

    def test_which_one(self):
        local = self.tool(os.path.join(self.home, ".local", "bin", "claude"), ["auto", "manual"])
        named = self.tool(os.path.join(self.home, "named", "claude"), ["manual"])
        onpath = self.tool(os.path.join(self.home, "path", "claude"), ["default"])
        env = {"HOME": self.home, "PATH": os.path.dirname(onpath) + ":/usr/bin:/bin"}
        with mock.patch.dict(os.environ, dict(env, CLAUDE_BIN=named)):
            self.assertEqual((setup.claude_binary(), setup.claude_modes()), (named, ["manual"]))
        with mock.patch.dict(os.environ, env):
            os.environ.pop("CLAUDE_BIN", None)
            self.assertEqual((setup.claude_binary(), setup.claude_modes()), (local, ["auto", "manual"]))
            os.remove(local)
            self.assertEqual((setup.claude_binary(), setup.claude_modes()), (onpath, ["default"]))
            os.environ["PATH"] = "/nowhere"
            self.assertEqual((setup.claude_binary(), setup.claude_modes()), (None, None))
class TheSkill(unittest.TestCase):
    """skills/setup/SKILL.md: every command it runs is one setup.py subcommand on its own (the allow rule lets only
    that through; a joined line is checked whole and refused), and it is linked into #setup's folder only."""

    def test_its_commands(self):
        path = os.path.join(setup.ENGINE, "skills", "setup", "SKILL.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        blocks = [b.split("```", 1)[0] for b in text.split("```bash\n")[1:]]
        self.assertTrue(blocks)
        subs = set()
        for b in blocks:
            for line in b.strip().splitlines():
                self.assertTrue(line.startswith("python3 <ENGINE>/ops/setup.py "), line)
                self.assertNotRegex(line, r"&&|\||;", line)
                subs.add(line.split()[2])
        for inline in __import__("re").findall(r"`(?:setup\.py )?(answer|status|questions|done|tutorial|backup-set|login-check)\b", text):
            subs.add(inline)
        usage = setup.__doc__.split("\n\n")[1]
        for sub in subs:
            self.assertIn("setup.py %s" % sub, usage, sub)
        self.assertTrue(os.path.exists(os.path.join(setup.ENGINE, "skills", "setup", ".local-only")))


class UpdateUnfinished(Rig):
    """S4b (manager 2026-10-20): status says whether the last update go got to its end, from go's own progress file
    (written here with go's write_progress), so #setup's Claude reads nothing outside its folder."""

    def test_from_gos_progress_file(self):
        from altention import update_go
        self.assertTrue(update_go.progress_path().startswith(self.home))
        self.assertIsNone(self.status()["update_unfinished"])
        update_go.write_progress({"card": None, "asked": "v1.2.3", "tag": "v1.2.3", "start": "v1.2.2", "first": "",
                                  "newer": "", "done": ["update", "install"], "notes": [], "at": 1})
        self.assertEqual(self.status()["update_unfinished"],
                         {"version": "v1.2.3", "moved": True, "left": ["補齊頻道和設定", "系統檢查"]})
        self.assertIn("上次更新到 v1.2.3 沒做完：引擎已經換到新版；還沒做：補齊頻道和設定、系統檢查", self.run_("status")[1])
        update_go.write_progress({"card": None, "asked": "v1.2.3", "tag": "v1.2.3", "done": [], "at": 1})
        self.assertEqual(self.status()["update_unfinished"]["moved"], False)
        os.remove(update_go.progress_path())
        self.assertIsNone(self.status()["update_unfinished"])


class TheUpdateSkill(unittest.TestCase):
    """skills/update (C-57 S4a, S4b): every command it runs is one the allow rule of #setup's folder lets through, on its
    own; it is linked into #setup's folder only; it says what the person can do now for every kind the check can
    stop at (manager 2026-10-20: not only "wait for the next version")."""

    def setUp(self):
        with open(os.path.join(setup.ENGINE, "skills", "update", "SKILL.md"), encoding="utf-8") as f:
            self.text = f.read()

    def test_its_commands_are_the_allowed_ones(self):
        allowed = ["Bash(%s:*)" % r[5:-3] for r in setup.setup_rules("<ENGINE>")["permissions"]["allow"]]
        prefixes = [a[5:-3] for a in allowed]
        blocks = [b.split("```", 1)[0] for b in self.text.split("```bash\n")[1:]]
        self.assertTrue(blocks)
        for b in blocks:
            for line in b.strip().splitlines():
                self.assertTrue(any(line.startswith(p.replace("python3 <ENGINE>", "python3 <ENGINE>")) for p in prefixes)
                                or line.startswith("python3 <ENGINE>/ops/setup.py "), line)
                self.assertNotRegex(line, r"&&|\||;", line)
        self.assertNotIn("git ", " ".join(blocks))
        self.assertTrue(os.path.exists(os.path.join(setup.ENGINE, "skills", "update", ".local-only")))
        self.assertIn("update", setup.SETUP_SKILLS)

    def test_every_kind_the_check_can_stop_at(self):
        from altention import update_go
        for kind in update_go.PLAIN:
            self.assertIn("`%s`" % kind, self.text, kind)
        stuck = self.text[self.text.index("## 2."):self.text.index("## 3.")]
        for kind in ("edits", "untracked", "conflict", "mixed", "detached"):
            part = stuck[stuck.index("**`%s`**" % kind):]
            part = part[:part.index("\n- **") if "\n- **" in part[3:] else len(part)]
            self.assertIn("系統照現在的版本繼續跑，沒事", "".join(part.split()), kind)
        for word in ("rebase", "stash", "HEAD", "worktree", "還沒存"):  # 還沒存: an editor's, not ours (U1, 0f291f5)
            self.assertNotIn(word, stuck)

    def test_every_subcommand_it_may_run_is_shown(self):
        """S4b: save, apply and undo as well as check and go, each in a bash block of its own; the long ones in the
        background, waited for when the tool says still running (no check meanwhile: it says `none` while go still
        installs), and one that ended without its last line told from setup.py status's update_unfinished, reading nothing
        outside #setup's folder (manager 2026-10-20, #code-review-2 of 28fcc88: that read's question changes a machine-wide setting)."""
        blocks = [b.split("```", 1)[0].strip() for b in self.text.split("```bash\n")[1:]]
        for sub in setup.UPDATE_SUBCOMMANDS:
            self.assertTrue([b for b in blocks if b.startswith("<ENGINE>/bin/altention update %s" % sub)], sub)
        self.assertIn("run_in_background", self.text)
        self.assertNotIn("600000", self.text)
        self.assertNotIn("update-go.json", self.text)
        self.assertNotIn("~/.local/state", self.text)
        rules = self.text[:self.text.index("## 1.")]
        self.assertIn("wait for its end notice", rules)
        self.assertIn("Do not run `check` meanwhile", rules)
        self.assertIn("「這次更新還沒做完」", rules)
        self.assertIn("`update_unfinished`", rules)
        self.assertIn("「接著做完」", rules)  # U6: go takes up what an interrupted one left (manager 2026-10-20)
        self.assertIn("「上次換到 vX.Y.Z 之後被中斷了，接著把剩下的做完」", rules)
        self.assertNotIn("跑 ./install.sh 和 doctor", rules)
        self.assertIn("Never read files outside this folder", rules)

    def test_what_it_reads_is_what_the_subcommands_print(self):
        """The outputs the skill tells apart are update_go's own words (a rewording there breaks this, not the person)."""
        from altention import update_go
        with open(update_go.__file__, encoding="utf-8") as f:
            src = f.read()
        said = ["已經把你改過的檔…保留下來了", "這些檔還沒合好", "這一段合好了；下一段還有要合的檔", "合好的版本沒通過檢查",
                "沒有 <vX.Y.Z> 的合併副本", "這份副本不能用了", "什麼都還沒換", "沒有換", "這次先不換", "都沒動",
                "已經回到更新前的版本", "這台設成有新版就自己更新", "更新之後這台的引擎又有變動",
                "跟要回去的版本改到同一個檔", "沒有更新前的記號", "跑 ./install.sh",
                "上次換到 vX.Y.Z 之後被中斷了，接著把剩下的做完", "已經把擋住新版的檔改名了", "沒有擋住 <vX.Y.Z> 的同名檔"]
        for words in said:
            self.assertIn("「%s」" % words, self.text, words)
            for piece in words.replace("<vX.Y.Z>", "%s").replace("vX.Y.Z", "%s").split("…"):
                self.assertIn(piece, src, words)

    def test_which_side_of_a_conflict_is_whose(self):
        """Z trial (manager 2026-10-20): in the copy (a rebase) the HEAD side is the new version's, the other theirs."""
        part = self.text[self.text.index("### 3b."):self.text.index("## 4.")]
        self.assertIn("between `<<<<<<< HEAD` and `=======` is the new version's", " ".join(part.split()))
        self.assertIn("is **theirs**", part)

    def test_the_copy_is_where_apply_takes_it_from(self):
        from altention import update_go
        self.assertEqual(os.path.relpath(update_go.copy_path("v1.2.3"), os.path.expanduser(setup.SETUP_FOLDER)),
                         os.path.join("update-work", "engine-v1.2.3"))
        self.assertIn("`update-work/engine-<vX.Y.Z>`", self.text)


class LoginCheck(Rig):
    def test_only_statuses_and_the_account(self):
        def run(argv, **k):
            if argv[:3] == ["gh", "auth", "status"]:
                return subprocess.CompletedProcess(argv, 0)
            if argv[:2] == ["gh", "api"]:
                return subprocess.CompletedProcess(argv, 0, "alice\n")
            if argv[0] == "ssh":
                return subprocess.CompletedProcess(argv, 1, "Hi alice! You've successfully authenticated, but …")
            raise AssertionError(argv)
        with mock.patch.object(setup.subprocess, "run", run):
            code, out = self.run_("login-check", "--json")
        self.assertEqual(json.loads(out), {"gh": "in", "account": "alice", "ssh": True})

        def new_machine(argv, **k):
            if argv[0] == "ssh":
                return subprocess.CompletedProcess(argv, 255, "Host key verification failed.\r\n")
            return subprocess.CompletedProcess(argv, 1)
        with mock.patch.object(setup.subprocess, "run", new_machine):
            self.assertEqual(json.loads(self.run_("login-check", "--json")[1])["ssh"], "unknown-host")
            out = self.run_("login-check")[1]
        self.assertIn("gh：裝了，沒登入", out)
        self.assertIn("這台還沒連過 GitHub", out)
        self.assertNotIn("不認得", out, "not a missing key")

        def missing(argv, **k):
            raise FileNotFoundError(argv[0])
        with mock.patch.object(setup.subprocess, "run", missing):
            code, out = self.run_("login-check")
        self.assertIn("gh：沒有裝", out)
        self.assertIn("GitHub 不認得這台", out)


class Script(unittest.TestCase):
    def test_taking_too_long_kills_the_whole_group(self):
        """#code-review-2 of 244dabf: a timeout killed install.sh alone; its children (git, pip) lived on."""
        tmp = tempfile.mkdtemp(prefix="群組 ")
        self.addCleanup(shutil.rmtree, tmp, True)
        pidfile = os.path.join(tmp, "pid")
        sh = os.path.join(tmp, "slow.sh")
        with open(sh, "w") as f:
            f.write("#!/bin/sh\nsleep 30 &\necho $! > '%s'\nwait\n" % pidfile)
        os.chmod(sh, 0o755)
        with self.assertRaises(subprocess.TimeoutExpired):
            setup.script([sh], timeout=2)
        pid = int(open(pidfile).read())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            self.fail("the child was left running")


class Reconcile(Rig):
    """C-57 S3: what the version declares next to the instance and the server. Filled in when it needs nobody,
    asked in #setup when it adds a channel to the main area or spends the subscription; a dry run writes nothing."""

    def setUp(self):
        super().setUp()
        self.chans = [("101", "audit", "其他"), ("102", "chat", "")]
        self.said, self.moved = [], []
        self.dcctl.stop()
        self.dcctl = mock.patch.object(setup, "dcctl", self.server)
        self.dcctl.start()
        rows = [(n, "/x", d, "on", "") for n, _, d in setup.plugin.discover()[0]]
        for p in (mock.patch.object(setup, "altention", self.alt), mock.patch.object(setup, "guide", self.guide),
                  mock.patch.object(setup, "sleep", lambda s: None),
                  mock.patch.object(setup, "engine_head", return_value="1" * 40),  # an exported copy has no .git
                  mock.patch.object(setup.config, "bot_here", return_value=True),
                  mock.patch.object(setup.plugin, "on_this_machine", return_value=(rows, []))):
            p.start()
            self.addCleanup(p.stop)
        self.guided = 0
        self.write({"format": 1, "core": {"timezone": "UTC"}, "usage": {"subscription_limits": True},
                    "planner": {"channel": "999"}})  # an installation from before #setup; its secretary's channel gone
        git(self.inst, "commit", "-qam", "old")

    def server(self, *args, inp=None, timeout=120):
        if args[0] == "channels":
            return subprocess.CompletedProcess(args, 0, "".join("%-22s %-28s %s\n" % (i, "#" + n, c) for i, n, c in self.chans))
        if args[0] == "create":
            self.chans.append((str(200 + len(self.chans)), args[1], ""))
            return subprocess.CompletedProcess(args, 0, "建立 #%s\n" % args[1])
        self.posted.append((args, inp))
        return subprocess.CompletedProcess(args, 0, "")

    def alt(self, *args, timeout=120):
        if "--move-to" in args:
            self.moved.append((args[2], args[4]))
            self.chans = [(i, n, args[4] if n == args[2] else c) for i, n, c in self.chans]
        elif args[:3] == ("dc", "notify", "-c"):
            self.said.append((args[3], args[4]))
        else:  # anything else said in a channel would read as a Claude's card
            self.said.append(("not a notice", args))
        return subprocess.CompletedProcess(args, 0, "")

    def guide(self, out=None):
        self.guided += 1
        return 0

    def plan(self):
        code, out = self.run_("reconcile", "--dry-run", "--json")
        self.assertEqual(code, 0, out)
        return {p["what"]: p["kind"] for p in json.loads(out)}

    def test_dry_run_says_and_writes_nothing(self):
        before = self.settings()
        self.assertEqual(self.plan(), {"make-update": "fill", "make-guide": "fill", "move-audit": "fill",
                                       "dashboard": "ask", "plugins.planner": "ask", "dcbot.default_permission": "ask",
                                       "usage.plan": "ask", "setup-folder": "fill"})
        self.assertEqual((self.settings(), self.said, self.moved, self.posted, self.guided), (before, [], [], [], 0))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local", "state", "altention")), "not even a lock")

    def test_an_older_installation_is_filled_in_and_asked(self):
        code, out = self.run_("reconcile")
        self.assertEqual(code, 0, out)
        self.assertIn(("update", setup.UPDATE_HELLO), self.said, "#update made, and says what it is first")
        self.assertEqual([w for w, _ in self.said if w != "update"], [], "every line a notice, none a Claude's card")
        self.assertEqual(self.moved, [("audit", "系統")])
        said = [t for _, t in self.said]
        self.assertLess(said.index(setup.UPDATE_HELLO), [i for i, t in enumerate(said) if t.startswith("📦")][0])
        self.assertEqual(self.guided, 1)
        self.assertEqual(self.settings()["setup"], {"done": True, "since": "reconcile", "ask": [
            "dcbot.default_permission", "plugins.planner", "dashboard", "usage.plan"]})
        opened = [json.loads(i) for a, i in self.posted if a == ("post",)]
        self.assertEqual(opened, [{"channel": "setup", "open_setup": True, "text": ""}])
        told = [t for t in said if t.startswith("📝 ")]
        self.assertEqual(len(told), 1)
        self.assertTrue(told[0].startswith("📝 有 4 件事要你決定：權限、秘書、看板頻道、方案。"), told[0])
        self.assertNotIn("例如", told[0], "what is really asked, never a fixed example")
        self.assertTrue(os.path.islink(os.path.join(self.home, ".local", "state", "altention", "setup", ".claude", "skills", "setup")))
        n = len(self.said)
        self.run_("reconcile")
        self.assertEqual((len(self.said), len(opened)), (n, 1), "nothing new: said once, #setup asked for once")
        code, out = self.run_("reconcile", "--if-changed")
        self.assertIn("引擎沒換過", out)

    def test_the_questions_then_done(self):
        self.run_("reconcile")
        self.assertEqual(self.status()["left"], ["dcbot.default_permission", "plugins.planner", "dashboard", "usage.plan"])
        self.answer("core.timezone", "UTC", code=1)  # not asked
        self.answer("dashboard", "make")
        self.answer("plugins.planner", "on")
        self.assertNotIn("channel", self.settings().get("planner", {}), "the gone channel taken out: opened again")
        self.answer("dcbot.default_permission", "manual")
        self.answer("usage.plan", "Pro")
        st = self.status()
        self.assertEqual((st["left"], st["asking"]), ([], True), "the last one answered: still to be done")
        code, out = self.run_("status")
        self.assertIn("接著 python3 ops/setup.py done", out)
        ran = []
        with mock.patch.multiple(setup, script=lambda argv, timeout=1800: ran.append(argv) or subprocess.CompletedProcess(argv, 0, "ok core.done\n"),
                                 dashboard=mock.DEFAULT, secretary=mock.DEFAULT) as m:
            code, out = self.run_("done")
        self.assertEqual(code, 0, out)
        m["dashboard"].assert_called_once()
        m["secretary"].assert_called_once()
        self.assertNotIn("ask", self.settings()["setup"])
        self.assertIs(self.status()["asking"], False, "done: no longer asking")
        self.assertFalse([1 for a, i in self.posted if a == ("post",) and "open_chat" in i], "#chat is there already")
        self.assertIn(("update", "✅ 你在 #setup 回答的都設定好了。"), self.said, "a notice in #update")
        self.assertEqual(json.loads(self.run_("questions", "--json")[1]), [], "no tour asked of an older installation")

    def test_done_failing_at_the_usage_board_then_run_again(self):
        """C-68: the reconcile's questions are taken away only once done got through; a run again after a step
        failed is the same older installation's: no #guide again, no #chat opened, and the ✅ said."""
        self.run_("reconcile")
        for key, value in (("dashboard", "make"), ("plugins.planner", "off"), ("dcbot.default_permission", "manual"),
                           ("usage.plan", "Pro")):
            self.answer(key, value)
        guided = self.guided
        ok = lambda argv, timeout=1800: subprocess.CompletedProcess(argv, 0, "ok core.done\n")  # noqa: E731
        with mock.patch.multiple(setup, script=ok, dashboard=mock.Mock(side_effect=setup.Failed("用量看板沒貼上：結束碼 1")),
                                 secretary=mock.DEFAULT):
            code, out = self.run_("done")
        self.assertEqual(code, 2, out)
        self.assertIn("ask", self.settings()["setup"], "kept: the run again knows it is the reconcile's")
        self.assertNotIn(("update", "✅ 你在 #setup 回答的都設定好了。"), self.said)
        with mock.patch.multiple(setup, script=ok, dashboard=mock.DEFAULT, secretary=mock.DEFAULT):
            code, out = self.run_("done")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.guided, guided, "no #guide posted again")
        self.assertFalse([1 for a, i in self.posted if a == ("post",) and "open_chat" in i], "no #chat opened")
        self.assertIn(("update", "✅ 你在 #setup 回答的都設定好了。"), self.said)
        self.assertNotIn("ask", self.settings()["setup"])

    def test_turning_the_boards_off(self):
        self.run_("reconcile")
        self.answer("dashboard", "off")
        plugins_ = self.settings()["plugins"]
        self.assertEqual((plugins_["usage"], plugins_["status"]), ({"disabled_on": ["box"]}, {"disabled_on": ["box"]}))

    def test_one_board_channel_gone_the_other_there(self):
        """C-67: status.channel on #dashboard (there), usage.channel on a channel deleted: asked of the usage board
        alone, not "#dashboard 已經不在了"; "off" turns off that board only, "make" leaves #dashboard's setting."""
        self.chans.append(("105", "dashboard", ""))
        d = self.settings()
        d["status"], d["usage"]["channel"] = {"channel": "105"}, "998"
        self.write(d)
        self.run_("reconcile")
        self.assertIn("dashboard", self.status()["left"])
        asked = self.settings()
        q = {q["key"]: q for q in json.loads(self.run_("questions", "--json")[1])}["dashboard"]
        self.assertEqual(q["question"], "用量看板的頻道已經不在了（狀態看板還在 <#105>）。要把用量看板放回看板頻道嗎？")
        self.assertEqual(q["options"][1]["description"], "只關掉用量看板，狀態看板照舊")
        self.assertEqual(q["values"], ["make", "off"])
        self.answer("dashboard", "off")
        d = self.settings()
        self.assertEqual(d["plugins"], {"usage": {"disabled_on": ["box"]}}, "the status board stays on")
        self.assertEqual(d["status"], {"channel": "105"})
        self.write(asked)  # asked again, answered the other way
        self.answer("dashboard", "make")
        d = self.settings()
        self.assertEqual((d["status"].get("channel"), d["usage"].get("channel")), ("105", None), "the gone one made again by done")

    def test_both_board_channels_gone_is_asked_as_before(self):
        d = self.settings()
        d["status"], d["usage"]["channel"] = {"channel": "997"}, "998"
        self.write(d)
        self.run_("reconcile")
        q = {q["key"]: q for q in json.loads(self.run_("questions", "--json")[1])}["dashboard"]
        self.assertEqual(q["question"], "#dashboard 已經不在了，要重新建一個嗎？")
        self.answer("dashboard", "off")
        plugins_ = self.settings()["plugins"]
        self.assertEqual((plugins_["usage"], plugins_["status"]), ({"disabled_on": ["box"]}, {"disabled_on": ["box"]}))

    def test_a_new_installation_in_setup_gets_no_extra_questions(self):
        d = self.settings()
        d["setup"] = {"done": False}
        self.write(d)
        self.run_("reconcile")
        self.assertEqual(self.settings()["setup"], {"done": False})
        self.assertFalse([1 for a, i in self.posted if a == ("post",)])

    def test_all_in_place(self):
        self.chans = [("101", "audit", "系統"), ("103", "guide", "系統"), ("104", "update", ""), ("105", "dashboard", ""),
                      ("106", "secretary", "")]
        self.write({"format": 1, "usage": {"channel": "105", "plan": ["Pro", 1]}, "status": {"channel": "105"},
                    "planner": {"channel": "106"}, "dcbot": {"default_permission": "auto"}})
        setup.write_setup_folder()
        self.assertEqual(self.plan(), {})
        code, out = self.run_("reconcile")
        self.assertIn("都齊了", out)
        self.assertEqual((self.said, self.moved, self.posted), ([], [], []))

    def test_the_system_categorys_own_name(self):
        """dcbot.channels.system names it, as the bot reads it (650fad1)."""
        d = self.settings()
        d["dcbot"] = {"channels": {"system": "後台"}}
        self.write(d)
        self.chans = [("101", "audit", "系統")]
        self.assertEqual(self.plan().get("move-audit"), "fill", "系統 is not its name here")
        self.run_("reconcile")
        self.assertEqual(self.moved, [("audit", "後台")])
        self.assertTrue(any("「後台」" in t for _, t in self.said))

    def test_only_on_the_bots_machine(self):
        with mock.patch.object(setup.config, "bot_here", return_value=False):
            code, out = self.run_("reconcile")
        self.assertIn("機器人不在這台", out)
        self.assertEqual(self.posted, [])


class AskedAgain(Rig):
    """Z trial (manager 2026-10-20): they chose to open #secretary or #dashboard, setup.py done did not get to making
    it, and the reconcile asks again: the question says so ("上次你選了開"), not as if new; the values stay."""

    def questions(self, answered):
        self.write({"format": 1, "core": {"timezone": "UTC"},
                    "setup": {"done": True, "ask": ["plugins.planner", "dashboard"], "answered": answered}})
        code, out = self.run_("questions", "--json")
        self.assertEqual(code, 0, out)
        return {q["key"]: q for q in json.loads(out)}

    def test_chosen_before_is_said(self):
        qs = self.questions({"plugins.planner": "on", "dashboard": "make"})
        for key, chan in (("plugins.planner", "#secretary"), ("dashboard", "#dashboard")):
            self.assertIn("上次你選了開", qs[key]["question"], key)
            self.assertIn(chan, qs[key]["question"], key)
            self.assertIn("還沒建好", qs[key]["question"], key)
            self.assertEqual(qs[key]["options"][0]["label"], "建好它（建議）", key)
        self.assertEqual((qs["plugins.planner"]["values"], qs["dashboard"]["values"]), (["on", "off"], ["make", "off"]))

    def test_a_channel_deleted_is_asked_as_gone(self):
        """#code-review of e51ad8d: the setting names a channel the server has not (stale): "已經不在了", not "還沒建好"."""
        d = {"format": 1, "core": {"timezone": "UTC"}, "planner": {"channel": "999"}, "status": {"channel": "998"},
             "setup": {"done": True, "ask": ["plugins.planner", "dashboard"],
                       "answered": {"plugins.planner": "on", "dashboard": "make"}}}
        self.write(d)
        qs = {q["key"]: q for q in json.loads(self.run_("questions", "--json")[1])}
        for key, chan in (("plugins.planner", "#secretary"), ("dashboard", "#dashboard")):
            self.assertEqual(qs[key]["question"], "%s 已經不在了，要重新建一個嗎？" % chan, key)
            self.assertEqual(qs[key]["options"][0]["label"], "重新建（建議）", key)
            self.assertNotIn("還沒建好", qs[key]["question"], key)
        self.assertEqual((qs["plugins.planner"]["values"], qs["dashboard"]["values"]), (["on", "off"], ["make", "off"]))
        del d["setup"]["answered"]
        self.write(d)
        qs = {q["key"]: q for q in json.loads(self.run_("questions", "--json")[1])}
        self.assertIn("已經不在了", qs["plugins.planner"]["question"], "gone, whatever was answered")
        self.assertEqual(sorted(setup.asked_channels("dashboard", setup.plugin.discover()[0])), ["status.channel", "usage.channel"])

    def test_first_time_or_said_no_before_is_asked_as_new(self):
        for answered in ({}, {"plugins.planner": "off", "dashboard": "off"}):
            qs = self.questions(answered)
            for key in ("plugins.planner", "dashboard"):
                self.assertNotIn("上次", qs[key]["question"], (key, answered))
                self.assertEqual(qs[key]["options"][0]["label"], "開（建議）", (key, answered))


class Restore(unittest.TestCase):
    """C-57 S2 (strict): only the instance install.sh has just made, untouched, is swapped for the backup; the fresh
    one is kept (never deleted) and put back when anything fails; then which machine, done, the tour."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 restore ")
        self.addCleanup(shutil.rmtree, self.home, True)
        os.makedirs(os.path.join(self.home, ".config", "altention"))
        with open(os.path.join(self.home, ".config", "altention", "host"), "w") as f:
            f.write("box\n")
        env = mock.patch.dict(os.environ, {"HOME": self.home, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
                                           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("ALT_INSTANCE", None)
        os.environ.pop("ALT_ROOT", None)
        import instance
        self.instance = instance
        with redirect_stdout(io.StringIO()):
            self.assertEqual(instance.init("auto"), 0)
        self.inst = instance.place()
        self.backup = self.make_backup({"format": 1, "core": {"timezone": "Europe/Berlin"},
                                        "plugins": {"council": {"disabled_on": ["old"]}}})
        self.ran, self.install_rc, self.posted = [], 0, []
        for p in (mock.patch.object(setup, "script", self.script),
                  mock.patch.object(setup, "claude_modes", return_value=["auto", "manual", "bypassPermissions"]),
                  mock.patch.object(setup, "dcctl", self.server), mock.patch.object(setup, "altention", self.alt),
                  mock.patch.object(setup, "sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)
        self.chans = [("101", "audit", "系統"), ("102", "guide", "系統")]

    def make_backup(self, settings, empty=False):
        bare = os.path.join(self.home, "備份 %d.git" % len(os.listdir(self.home)))
        git(self.home, "init", "-q", "--bare", bare)
        if not empty:
            work = bare + ".work"
            git(self.home, "init", "-q", work)
            with open(os.path.join(work, "plugins.json"), "w") as f:
                json.dump(settings, f)
            git(work, "add", "-A")
            git(work, "commit", "-qm", "backup")
            git(work, "push", "-q", bare, "HEAD:main")
            git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
            shutil.rmtree(work)
        return bare

    def script(self, argv, timeout=1800):
        self.ran.append(argv)
        return subprocess.CompletedProcess(argv, self.install_rc, "ok core.done\n" if not self.install_rc else "fail core.done x\n")

    def server(self, *args, inp=None, timeout=120):
        if args[0] == "channels":
            return subprocess.CompletedProcess(args, 0, "".join("%-22s %-28s %s\n" % (i, "#" + n, c) for i, n, c in self.chans))
        self.posted.append((args, inp))
        return subprocess.CompletedProcess(args, 0, "")

    def alt(self, *args, timeout=120):
        return subprocess.CompletedProcess(args, 0, "")

    def run_(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = setup.main(list(argv))
        return code, out.getvalue()

    def state(self):
        return (git(self.inst, "rev-parse", "HEAD"), sorted(os.listdir(os.path.dirname(self.inst))),
                open(os.path.join(self.inst, "plugins.json")).read())

    def choose_restore(self):
        code, out = self.run_("answer", "install", "restore")
        self.assertEqual(code, 0, out)

    def test_the_fresh_one_is_kept_and_the_backup_put_in_its_place(self):
        self.choose_restore()
        st = json.loads(self.run_("status", "--json")[1])
        self.assertEqual((st["restore"], st["left"]), (True, []))
        self.assertEqual(self.run_("done")[0], 1, "not done: the restore comes first")
        pointer = open(os.path.join(self.home, ".config", "altention", "instance")).read()
        code, out = self.run_("restore", self.backup)
        self.assertEqual(code, 0, out)
        self.assertEqual(json.load(open(os.path.join(self.inst, "plugins.json")))["core"]["timezone"], "Europe/Berlin")
        fresh = [n for n in os.listdir(os.path.dirname(self.inst)) if n.startswith("instance.fresh-")]
        self.assertEqual(len(fresh), 1, "kept, never deleted")
        self.assertIn('"done": false', open(os.path.join(os.path.dirname(self.inst), fresh[0], "plugins.json")).read())
        self.assertEqual(open(os.path.join(self.home, ".config", "altention", "instance")).read(), pointer)
        self.assertTrue(os.path.exists(os.path.join(self.inst, ".git", "hooks", "pre-push")))
        self.assertEqual(self.ran, [], "installed once the name is settled (#code-review-2 of 244dabf)")
        st = json.loads(self.run_("status", "--json")[1])
        self.assertEqual((st["setup"], st["next"]), ("restored", "machine"))
        self.assertIn("instance.fresh-", out)

    def test_each_condition_missing_refuses_and_touches_nothing(self):
        def used(how):
            if how == "env":
                os.environ["ALT_INSTANCE"] = self.inst
            elif how == "pointer":
                other = os.path.join(self.home, "別的")
                shutil.copytree(self.inst, other)
                with open(os.path.join(self.home, ".config", "altention", "instance"), "w") as f:
                    f.write(other + "\n")
            elif how == "remote":
                git(self.inst, "remote", "add", "origin", self.backup)
            elif how == "dirty":
                with open(os.path.join(self.inst, "memory", "MEMORY.md"), "w") as f:
                    f.write("- something\n")
            elif how == "commit":
                with open(os.path.join(self.inst, "knowledge", "note.md"), "w") as f:
                    f.write("x\n")
                git(self.inst, "add", "-A")
                git(self.inst, "commit", "-qm", "a note")
            elif how in ("done", "answered", "new"):
                d = json.load(open(os.path.join(self.inst, "plugins.json")))
                d["setup"] = {"done": True, "answered": {"install": "restore"}} if how == "done" else \
                    {"done": False, "answered": {"install": "restore", "core.timezone": "UTC"} if how == "answered"
                     else {"install": "new"}}
                with open(os.path.join(self.inst, "plugins.json"), "w") as f:
                    json.dump(d, f)
                git(self.inst, "commit", "-qam", "setup: install")
        for how in ("env", "pointer", "remote", "dirty", "commit", "done", "answered", "new"):
            with self.subTest(how=how):
                shutil.rmtree(self.home)
                self.setUp()
                self.choose_restore()
                used(how)
                before = self.state()
                code, out = self.run_("restore", self.backup)
                self.assertEqual(code, 1, out)
                self.assertIn("沒有換：", out)
                self.assertEqual(self.state(), before, "nothing moved")
                self.assertEqual(self.ran, [], "no install")
                self.assertIsNone(setup.restore_mark())
                os.environ.pop("ALT_INSTANCE", None)

    def test_an_instance_in_use_is_never_swapped(self):
        """The way the owner's own machines are: a joined instance with history, no setup."""
        git(self.inst, "remote", "add", "origin", self.backup)
        d = json.load(open(os.path.join(self.inst, "plugins.json")))
        del d["setup"]
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump(d, f)
        git(self.inst, "commit", "-qam", "settings")
        before = self.state()
        code, out = self.run_("restore", self.backup)
        self.assertEqual((code, self.state(), self.ran), (1, before, []), out)

    def to_done(self):
        """restore, take the old machine's name, then done (where install.sh runs)."""
        self.assertEqual(self.run_("restore", self.backup)[0], 0)
        self.run_("answer", "machine", "old")
        self.assertEqual(self.run_("answer", "machine.confirm", "yes")[0], 0)
        with mock.patch.multiple(setup, dashboard=mock.DEFAULT, guide=mock.DEFAULT, secretary=mock.DEFAULT):
            return self.run_("done")

    def test_install_failing_puts_the_fresh_one_back(self):
        self.choose_restore()
        before = self.state()
        self.install_rc = 1
        code, out = self.to_done()
        self.assertEqual(code, 2, out)
        cfg = os.path.join(self.home, ".config", "altention")
        self.assertEqual(open(os.path.join(cfg, "host")).read(), "box\n", "this machine's own name back")
        self.assertIn("原來的實例、資料夾和這台的名字都放回去了", out)
        self.assertEqual(self.state()[0], before[0], "the fresh one in place again")
        self.assertIn('"done": false', open(os.path.join(self.inst, "plugins.json")).read())
        names = os.listdir(os.path.dirname(self.inst))
        self.assertEqual(len([n for n in names if n.startswith("instance.restore-failed-")]), 1, "the restored one kept")
        self.assertFalse([n for n in names if n.startswith("instance.fresh-")])
        self.assertIsNone(setup.restore_mark())
        self.assertEqual(len(self.ran), 2, "installed again with the fresh one's settings")

    def test_install_taking_too_long_puts_the_fresh_one_back_too(self):
        self.choose_restore()
        before = self.state()
        calls = []

        def slow_once(argv, timeout=1800):
            calls.append(argv)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(argv, timeout)
            return subprocess.CompletedProcess(argv, 0, "ok core.done\n")
        with mock.patch.object(setup, "script", slow_once):
            code, out = self.to_done()
        self.assertEqual(code, 2, out)
        self.assertIn("原來的實例、資料夾和這台的名字都放回去了", out)
        self.assertIn("30 分鐘", out)
        self.assertEqual(self.state()[0], before[0])
        self.assertEqual(len([n for n in os.listdir(os.path.dirname(self.inst)) if n.startswith("instance.restore-failed-")]), 1)
        self.assertIsNone(setup.restore_mark())
        self.assertEqual(len(calls), 2, "installed again with the fresh one's settings")

    def test_a_list_without_this_machine_is_taken_over_not_added_to(self):
        """#code-review-2 of 244dabf: the backup's machine list has only the old machines; a restore takes one
        over (adding a machine to a set still in use is not a restore: manager 2026-10-20)."""
        self.backup = self.make_backup({"format": 1, "core": {"timezone": "Europe/Berlin"}})
        bare = self.backup
        work = bare + ".w2"
        git(self.home, "clone", "-q", bare, work)
        with open(os.path.join(work, "machines.json"), "w") as f:
            json.dump({"format": 1, "machines": {"oldbox": {"ssh": ["oldbox-host"], "roles": ["dcbot"]}}}, f)
        git(work, "add", "-A")
        git(work, "commit", "-qm", "list")
        git(work, "push", "-q", "origin", "HEAD:main")
        self.choose_restore()
        self.assertEqual(self.run_("restore", self.backup)[0], 0)
        q = json.loads(self.run_("questions", "--json")[1])[0]
        self.assertEqual(q["values"], ["oldbox"], "no new machine: the list would have no place for it")
        self.assertIn("加一台機器", q["question"])
        code, out = self.run_("answer", "machine", "")
        self.assertEqual(code, 1, out)
        self.run_("answer", "machine", "oldbox")
        code, out = self.run_("answer", "machine.confirm", "no")
        self.assertEqual(code, 1, out)
        self.assertIn("接替清單上的其中一台", out)
        self.run_("answer", "machine", "oldbox")
        with mock.patch.object(self.instance, "answers", return_value=[]):  # no real ssh from a test
            code, out = self.run_("answer", "machine.confirm", "yes")
        self.assertEqual(code, 0, out)
        self.assertIn("連線方式還是舊的那台", out)
        with mock.patch.multiple(setup, dashboard=mock.DEFAULT, guide=mock.DEFAULT, secretary=mock.DEFAULT):
            code, out = self.run_("done")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.ran), 1)

    def test_the_fresh_installs_empty_records_are_set_aside_and_come_back_on_failure(self):
        import remote
        logs = remote.folder_of("logs")
        os.makedirs(logs)
        git(logs, "init", "-q")  # as the fresh install made it: no commit, no remote, no file
        progress = remote.folder_of("progress")
        os.makedirs(progress)
        with open(os.path.join(progress, "mine.md"), "w") as f:
            f.write("x\n")
        self.choose_restore()
        self.install_rc = 0
        code, out = self.to_done()
        self.assertEqual(code, 0, out)
        aside = [n for n in os.listdir(os.path.dirname(logs)) if n.startswith("logs.fresh-")]
        self.assertEqual(len(aside), 1, "set aside, never deleted: the install clones the records back")
        self.assertFalse(os.path.exists(logs))
        self.assertTrue(os.path.exists(os.path.join(progress, "mine.md")), "not empty: left as it was")
        self.assertIn("沒有動它", out)

    def test_set_aside_records_come_back_when_the_install_fails(self):
        import remote
        logs = remote.folder_of("logs")
        os.makedirs(logs)
        git(logs, "init", "-q")
        self.choose_restore()
        self.install_rc = 1

        def install(argv, timeout=1800):  # the restored install clones the records, then fails
            self.ran.append(argv)
            if len(self.ran) == 1:
                os.makedirs(os.path.join(logs, "old"), exist_ok=True)
                os.makedirs(os.path.join(remote.folder_of("progress"), "oldbox"))  # cloned by the restored settings
                return subprocess.CompletedProcess(argv, 1, "fail core.done x\n")
            return subprocess.CompletedProcess(argv, 0, "ok core.done\n")
        with mock.patch.object(setup, "script", install):
            code, out = self.to_done()
        self.assertEqual(code, 2, out)
        self.assertEqual(os.listdir(logs), [".git"], "the fresh one's empty records back")
        progress = remote.folder_of("progress")
        self.assertFalse(os.path.exists(progress), "not there before the restore: not there after it")
        self.assertEqual(len([n for n in os.listdir(os.path.dirname(logs)) if n.startswith("logs.restore-failed-")]), 1)
        kept = [n for n in os.listdir(os.path.dirname(remote.folder_of("progress"))) if n.startswith("progress.restore-failed-")]
        self.assertEqual(len(kept), 1, "kept beside, never deleted")
        self.assertIn(kept[0], out, "said where")

    def test_a_clone_that_fails_or_is_no_instance(self):
        self.choose_restore()
        before = self.state()
        for url, words in ((os.path.join(self.home, "nowhere.git"), "clone 不下來"),
                           (self.make_backup(None, empty=True), "不像是實例的備份"),
                           (self.make_backup(["not", "an", "object"]), "不像是實例的備份")):
            with self.subTest(url=url):
                code, out = self.run_("restore", url)
                self.assertEqual(code, 2, out)
                self.assertIn(words, out)
                self.assertEqual(self.state()[0], before[0])
                self.assertFalse([n for n in os.listdir(os.path.dirname(self.inst)) if n.startswith("instance.fresh-")])
        self.assertEqual(self.ran, [])

    def test_which_machine_then_done_then_the_tour(self):
        self.choose_restore()
        self.assertEqual(self.run_("restore", self.backup)[0], 0)
        q = json.loads(self.run_("questions", "--json")[1])
        self.assertEqual([x["key"] for x in q], ["machine"])
        self.assertEqual(q[0]["values"], ["old", ""])
        self.assertIn("只出現在設定裡", q[0]["options"][0]["description"])
        self.assertEqual(self.run_("done")[0], 1, "which machine first")
        self.run_("answer", "machine", "old")
        q = json.loads(self.run_("questions", "--json")[1])
        self.assertEqual(q[0]["key"], "machine.confirm")
        self.assertIn("只出現在設定裡，可能是打錯的", q[0]["question"])
        code, out = self.run_("answer", "machine.confirm", "yes")
        self.assertEqual(code, 0, out)
        cfg = os.path.join(self.home, ".config", "altention")
        self.assertEqual((open(os.path.join(cfg, "host")).read(), open(os.path.join(cfg, "host.bak")).read()),
                         ("old\n", "box\n"), "the name install.sh wrote is kept")
        with mock.patch.multiple(setup, dashboard=mock.DEFAULT, guide=mock.DEFAULT, secretary=mock.DEFAULT):
            code, out = self.run_("done")
        self.assertEqual(code, 0, out)
        self.assertEqual([json.loads(i) for a, i in self.posted if a == ("post",)],
                         [{"channel": "chat", "open_chat": True, "text": ""}])
        self.assertNotIn("setup", json.load(open(os.path.join(self.inst, "plugins.json"))), "the restored one is left as it is")
        self.assertEqual([x["key"] for x in json.loads(self.run_("questions", "--json")[1])], ["tour"])
        self.assertEqual(self.run_("answer", "tour", "later")[0], 0)
        self.assertIsNone(setup.restore_mark())
        self.assertEqual(json.loads(self.run_("status", "--json")[1])["setup"], "none")

    def test_a_name_with_records_is_kept(self):
        self.choose_restore()
        self.run_("restore", self.backup)
        import remote
        os.makedirs(os.path.join(remote.folder_of("logs"), "box"))
        self.run_("answer", "machine", "old")
        code, out = self.run_("answer", "machine.confirm", "yes")
        self.assertIn("已經用 box 這個名字寫過對話紀錄", out)
        self.assertEqual(open(os.path.join(self.home, ".config", "altention", "host")).read(), "box\n")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config", "altention", "host.bak")))
        self.assertEqual(json.loads(self.run_("status", "--json")[1])["machine"], "")

    def test_a_machine_still_answering_keeps_this_ones_name(self):
        self.choose_restore()
        self.run_("restore", self.backup)
        with mock.patch.object(self.instance, "names", return_value=[("old", ["machines.json"])]), \
                mock.patch.object(self.instance.config, "inventory", return_value={"machines": {"old": {"ssh": ["old-box"]}}}), \
                mock.patch.object(self.instance, "answers", return_value=["old-box"]):
            self.run_("answer", "machine", "old")
            code, out = self.run_("answer", "machine.confirm", "yes")
        self.assertIn("還連得上", out)
        cfg = os.path.join(self.home, ".config", "altention")
        self.assertEqual(open(os.path.join(cfg, "host")).read(), "box\n", "its own name back")
        self.assertFalse(os.path.exists(os.path.join(cfg, "host.bak")))


class Usage(Rig):
    def test_usage(self):
        for argv in ([], ["answer", "x"], ["status", "x"], ["backup-set", "logs"], ["done", "--json"]):
            self.assertEqual(self.run_(*argv)[0], 2, argv)


if __name__ == "__main__":
    unittest.main()
