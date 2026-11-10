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
import stat
import subprocess
import sys
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
        self.answer("dcbot.talk_limit", "-1", code=1)
        self.answer("dcbot.talk_limit", "abc", code=1)
        self.answer("dcbot.talk_limit", "", code=1)
        self.answer("core.update", "branch", code=1, )
        self.assertEqual(self.settings(), before)
        self.answer("planner.times", "suggested", code=1)  # not asked yet: the secretary comes first

    def test_a_typed_talk_limit_is_any_count(self):
        """「其他」 under the talk limit: any whole number from 0 up, as /talklimit takes it (3 was refused, the
        question asked again)."""
        for typed, kept in (("3", 3), (" 45 ", 45), ("0", 0)):
            self.answer("dcbot.talk_limit", typed)
            self.assertEqual(self.settings()["dcbot"]["talk_limit"], kept)
        out = self.answer("dcbot.talk_limit", "-1", code=1)
        self.assertIn("來回上限要是 0 以上的整數，例如 5；0 是不限", out)

    def test_plan_options_follow_the_pricing_page(self):
        from ops import plugins
        q = plugins.plan_question()
        self.assertEqual(q["values"], ["Pro", "Max", "Ultra", "none"])
        self.assertLessEqual(len(q["options"]), 4, "AskUserQuestion takes at most 4")
        self.assertEqual([o["label"] for o in q["options"]][:3], ["Pro", "Max（5 倍 Pro）", "Ultra（10 倍 Pro）"])
        self.assertNotIn("Max 20x", json.dumps(q, ensure_ascii=False))

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
        self.answer("usage.plan", "Ultra")
        self.assertEqual(self.settings()["usage"], {"plan": ["Ultra", 10], "subscription_limits": True})
        self.answer("usage.plan", "Max")
        self.assertEqual(self.settings()["usage"]["plan"], ["Max", 5])
        self.answer("usage.plan", "Max 5x")  # older names, typed under 「其他」
        self.assertEqual(self.settings()["usage"]["plan"], ["Max", 5])
        self.answer("usage.plan", "Max 20x")
        self.assertEqual(self.settings()["usage"]["plan"], ["Max 20x", 20], "a subscriber not moved to Ultra yet")
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
        for inline in __import__("re").findall(r"`python3 <ENGINE>/ops/setup\.py ([a-z-]+)", text):  # C-77 G2: section 7's
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

    def test_the_plugins_step_only_with_plugins(self):
        """C-61 E3: 換插件 is left only where the instance declares a plugin with a source; settings that cannot be
        read: every step."""
        from altention import update_go
        update_go.write_progress({"card": None, "tag": "v1.2.3", "done": ["update", "install"], "at": 1})
        with mock.patch.object(update_go.plugin_update, "enabled", lambda settings=None: [{"name": "demo"}]):
            self.assertEqual(self.status()["update_unfinished"]["left"], ["換插件", "補齊頻道和設定", "系統檢查"])
        with mock.patch.object(update_go.config, "settings", mock.Mock(side_effect=ValueError("壞了"))):
            self.assertEqual(update_go.steps_left(update_go.read_progress()), ["換插件", "補齊頻道和設定", "系統檢查"])
        os.remove(update_go.progress_path())


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

    def test_the_plugin_step_reads_what_update_plugin_prints(self):
        """C-61 E3: step 7 tells apart update_go's own words for `update plugin` (support-3's one_plugin, plugin_ask),
        and covers every kind the card can hand over (PLUGIN_PLAIN)."""
        from altention import plugin_update, update_go
        with open(update_go.__file__, encoding="utf-8") as f:
            src = f.read()
        with open(plugin_update.__file__, encoding="utf-8") as f:
            src += f.read()
        part = self.text[self.text.index("## 7. Plugins"):self.text.index("## Afterwards")]
        for words in ("已經換到 <vX.Y.Z>", "別台會在下一輪自己跟上", "實例還沒推出去", "已經是 <vX.Y.Z> 了，不用換",
                      "自己的測試沒過，這次不換", "這次沒換到", "沒換到", "放回", "這要在機器人那台跑",
                      "它會看到你的對話內容"):  # a hook or a rewrite of replies (manager 2026-10-21)
            self.assertIn("「%s」" % words, part, words)
            for piece in words.split("<vX.Y.Z>"):
                self.assertIn(piece, src, words)
        self.assertIn("「插件的更新要使用者看過才換」", self.text)
        self.assertIn("插件的更新要使用者看過才換", src)
        self.assertIn("「插件」一節", src, "the handed text names this step as the skill says it")
        for kind in update_go.PLUGIN_PLAIN:
            self.assertIn("`%s`" % kind, part, kind)
        self.assertIn("--anyway", part)

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


class ReconcileRig(Rig):
    """An installation from before #setup, a fake server and fake channel posts, for the reconcile."""

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
                  mock.patch.object(setup.plugin, "on_this_machine", return_value=(rows, [])),
                  mock.patch("altention.catalog.look_before_asking", side_effect=self.look)):
            p.start()
            self.addCleanup(p.stop)
        self.guided, self.looked = 0, 0
        self.look_said = ("same", "官方插件清單沒有變")
        self.write({"format": 1, "core": {"timezone": "UTC"}, "usage": {"subscription_limits": True},
                    "planner": {"channel": "999"}})  # an installation from before #setup; its secretary's channel gone
        git(self.inst, "commit", "-qam", "old")

    def look(self):  # the official list's look before the questions: never GitHub in a test
        self.looked += 1
        return self.look_said

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


class PermissionWritten(ReconcileRig):
    """C-121: the bot no longer opens a conversation with bypassPermissions when no permission mode is set. An
    older installation ran with that mode unwritten; its first reconcile after the update writes it out (nothing
    changes), says so in #audit, in the instance's commit and in #update, and asks in #setup with the buttons."""

    def test_an_older_installation_gets_the_mode_it_ran_with_written_and_asked(self):
        code, out = self.run_("reconcile")
        self.assertEqual(code, 0, out)
        d = self.settings()
        self.assertEqual(d["dcbot"]["default_permission"], "bypassPermissions", "the mode it ran with: nothing changes")
        self.assertEqual(d["setup"]["permission_written"], "bypassPermissions")
        self.assertIn("dcbot.default_permission", d["setup"]["ask"])
        self.assertIn(("audit", "把原本沒寫明的權限模式 bypassPermissions 寫進設定（行為不變）"), self.said)
        self.assertIn(("update", "🔐 這個安裝之前沒設權限模式，機器人一直用 bypassPermissions 在跑：Claude 做什麼都不先問你。"
                                 "這次更新把它寫明了，行為不變。建議到 #setup 改成 auto。"), self.said)
        subjects = subprocess.run(["git", "-C", self.inst, "log", "--format=%s"], stdout=subprocess.PIPE,
                                  universal_newlines=True).stdout
        self.assertIn("把原本沒寫明的權限模式 bypassPermissions 寫進設定（行為不變）", subjects, "the commit says it too")

    def test_the_question_says_why_and_offers_the_three_modes_as_buttons(self):
        self.run_("reconcile")
        q = setup.question_for("dcbot.default_permission")
        self.assertTrue(q["question"].startswith(setup.WRITTEN_SAY), q["question"])
        self.assertEqual(q["values"], ["auto", "manual", "bypassPermissions"])
        self.assertEqual(len(q["options"]), 3, "one button each")
        self.answer("dcbot.default_permission", "auto")
        d = self.settings()
        self.assertEqual(d["dcbot"]["default_permission"], "auto", "changed in one go")
        self.assertNotIn("permission_written", d["setup"])
        self.assertFalse(setup.question_for("dcbot.default_permission")["question"].startswith(setup.WRITTEN_SAY))

    def test_written_once_only(self):
        self.run_("reconcile")
        self.answer("dcbot.default_permission", "manual")
        n = len(self.said)
        self.run_("reconcile")
        self.assertEqual(self.settings()["dcbot"]["default_permission"], "manual", "an answer is never written over")
        self.assertFalse([t for _, t in self.said[n:] if "bypassPermissions" in t])

    def test_a_mode_set_already_is_left_alone(self):
        d = self.settings()
        d["dcbot"] = {"default_permission": "manual"}
        self.write(d)
        self.assertNotIn("permission-written", self.plan())
        self.run_("reconcile")
        self.assertEqual(self.settings()["dcbot"]["default_permission"], "manual")
        self.assertNotIn("permission_written", self.settings()["setup"])
        self.assertFalse([t for _, t in self.said if "bypassPermissions" in t])

    def test_a_new_installation_still_in_setup_is_not_written_for(self):
        d = self.settings()
        d["setup"] = {"done": False}
        self.write(d)
        self.assertNotIn("permission-written", self.plan())
        self.run_("reconcile")
        self.assertNotIn("dcbot", self.settings(), "asked in #setup's own questions, never written for it")

    def test_a_reconcile_that_stopped_after_writing_asks_next_time(self):
        d = self.settings()  # as write_permission() leaves it, the run stopping before the asks
        d["dcbot"] = {"default_permission": "bypassPermissions"}
        d["setup"] = {"done": True, "since": "reconcile", "permission_written": "bypassPermissions"}
        self.write(d)
        plan = self.plan()
        self.assertEqual(plan.get("dcbot.default_permission"), "ask")
        self.assertNotIn("permission-written", plan, "written once")
        self.run_("reconcile")
        self.assertIn("dcbot.default_permission", self.settings()["setup"]["ask"])

    def test_a_mode_chosen_or_written_then_taken_out_by_hand_is_not_written_again(self):
        """#code-review on 85cd909: chosen in #setup (or written here once), then removed from plugins.json by hand:
        no mode, so the bot opens nothing; the next update's reconcile does not write bypassPermissions back."""
        for state in ({"done": True, "answered": {"dcbot.default_permission": "manual"}},
                      {"done": True, "since": "reconcile", "permission_written": "bypassPermissions"}):
            d = self.settings()
            d.pop("dcbot", None)
            d["setup"] = dict(state)
            self.write(d)
            self.assertNotIn("permission-written", self.plan(), state)
            n = len(self.said)
            self.run_("reconcile")
            after = self.settings()
            self.assertNotIn("default_permission", after.get("dcbot", {}), state)
            self.assertFalse([t for _, t in self.said[n:] if "bypassPermissions" in t], state)
            q = setup.question_for("dcbot.default_permission")
            self.assertFalse(q["question"].startswith(setup.WRITTEN_SAY), "not said to be what it runs with: it has none")
            self.assertNotIn("把原本沒寫明的權限模式", subprocess.run(["git", "-C", self.inst, "log", "-1", "--format=%s"],
                             stdout=subprocess.PIPE, universal_newlines=True).stdout, state)

    def test_a_dry_run_writes_nothing(self):
        before = self.settings()
        self.assertEqual(self.plan()["permission-written"], "fill")
        self.assertEqual(self.settings(), before)


class Reconcile(ReconcileRig):
    """C-57 S3: what the version declares next to the instance and the server. Filled in when it needs nobody,
    asked in #setup when it adds a channel to the main area or spends the subscription; a dry run writes nothing."""

    def test_dry_run_says_and_writes_nothing(self):
        before = self.settings()
        self.assertEqual(self.plan(), {"make-update": "fill", "make-guide": "fill", "move-audit": "fill",
                                       "permission-written": "fill", "dashboard": "ask", "plugins.planner": "ask", "dcbot.default_permission": "ask",
                                       "usage.plan": "ask", "setup-folder": "fill"})
        self.assertEqual((self.settings(), self.said, self.moved, self.posted, self.guided), (before, [], [], [], 0))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local", "state", "altention")), "not even a lock")

    def test_an_older_installation_is_filled_in_and_asked(self):
        code, out = self.run_("reconcile")
        self.assertEqual(code, 0, out)
        self.assertIn(("update", setup.UPDATE_HELLO), self.said, "#update made, and says what it is first")
        self.assertEqual([w for w, _ in self.said if w not in ("update", "audit")], [], "every line a notice, none a Claude's card")
        self.assertEqual(self.moved, [("audit", "系統")])
        said = [t for _, t in self.said]
        self.assertLess(said.index(setup.UPDATE_HELLO), [i for i, t in enumerate(said) if t.startswith("📦")][0])
        self.assertEqual(self.guided, 1)
        self.assertEqual(self.settings()["setup"], {"done": True, "since": "reconcile", "permission_written": "bypassPermissions",
                                                    "ask": ["dcbot.default_permission", "plugins.planner", "dashboard", "usage.plan"]})
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


class OpenedChannel(Rig):
    """A channel with its own Claude that setup.py asks the bot to open (#secretary, #tutorial), looked for through the
    real dcctl: its channel list is kept for a minute for every dcctl on the machine, so the wait reads a fresh one
    (B5 of v0.5.2: every new install waited the 60 s out at #secretary, while it was there); run again after a wait
    that ran out, the channel the bot made since is used, not a #secretary-2 next to it."""

    def setUp(self):
        super().setUp()
        import importlib.machinery
        import importlib.util
        path = os.path.join(setup.ENGINE, "dcbot", "tools", "dcctl")
        loader = importlib.machinery.SourceFileLoader("dcctl_setup", path)
        self.d = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_setup", loader))
        loader.exec_module(self.d)
        state = os.path.join(self.home, "dcbot-state")
        os.makedirs(state)
        self.server = [{"id": "11", "name": "audit", "type": 0, "parent_id": None, "position": 0}]
        self.gets, self.asked, self.slept = 0, [], 0
        self.bot_makes = True
        for p in (mock.patch.object(self.d.config, "STATE", state), mock.patch.object(self.d.config, "GUILD_ID", 42),
                  mock.patch.object(self.d, "call", self.call), mock.patch.object(self.d, "audit", lambda text: None),
                  mock.patch.object(setup, "dcctl", self.real_dcctl), mock.patch.object(setup, "altention", self.bot),
                  mock.patch.object(setup, "sleep", self.sleep), mock.patch.object(setup, "script", self.script)):
            p.start()
            self.addCleanup(p.stop)
        self.d.channels()  # a dcctl here read the list a moment ago: kept for a minute, no #secretary in it

    def call(self, method, path, body=None):
        self.assertEqual((method, path), ("GET", "/guilds/42/channels"))
        self.gets += 1
        return [dict(c) for c in self.server]

    def real_dcctl(self, *args, inp=None, timeout=120):
        out = io.StringIO()
        with redirect_stdout(out):
            self.d.main(list(args))
        return subprocess.CompletedProcess(args, 0, out.getvalue())

    def make(self, name):
        self.server.append({"id": str(500 + len(self.server)), "name": name, "type": 0, "parent_id": None,
                            "position": len(self.server)})

    def bot(self, *args, timeout=120):
        self.asked.append(args)
        if args[:2] == ("dc", "post") and "--new-claude" in args and self.bot_makes:
            self.make(args[2])  # the bot makes it at once
        return subprocess.CompletedProcess(args, 0, "")

    def sleep(self, s):
        self.slept += 1

    def script(self, argv, timeout=1800):
        folder = os.path.join(self.home, "planner")
        os.makedirs(folder, exist_ok=True)
        return subprocess.CompletedProcess(argv, 0, folder + "\n")

    def id_of(self, name):
        return next(c["id"] for c in self.server if c["name"] == name)

    def opened(self):
        return [a[2] for a in self.asked if "--new-claude" in a]

    def test_the_wait_sees_the_channel_while_the_kept_list_is_old(self):
        self.assertIsNotNone(self.d.cached_list(), "the list of a moment ago is there")
        out = io.StringIO()
        self.assertEqual(setup.secretary(out), 0)
        self.assertEqual(self.opened(), ["secretary"])
        self.assertEqual(self.slept, 0, "seen on the first look, not after the list ran out")
        self.assertEqual(self.settings()["planner"]["channel"], self.id_of("secretary"))
        self.assertIn("秘書開在 <#%s>" % self.id_of("secretary"), out.getvalue())
        self.assertIsNone(setup.opening("secretary"), "taken out once written down")
        plain = self.d.channels()  # (what the wait read before: the kept list, without it)
        self.assertIn("secretary", [c["name"] for c in plain])

    def test_run_again_after_the_wait_ran_out_uses_the_channel_made_since(self):
        self.bot_makes = False  # the bot makes it only after the wait has run out
        with self.assertRaises(setup.Failed) as e:
            setup.secretary(io.StringIO())
        self.assertIn("再跑一次", str(e.exception))
        self.assertEqual(setup.opening("secretary"), "secretary")
        self.make("secretary")
        self.bot_makes = True
        out = io.StringIO()
        self.assertEqual(setup.secretary(out), 0)
        self.assertEqual(self.opened(), ["secretary"], "asked once: no #secretary-2")
        self.assertNotIn("secretary-2", [c["name"] for c in self.server])
        self.assertEqual(self.settings()["planner"]["channel"], self.id_of("secretary"))
        self.assertNotIn("已經有 #secretary", out.getvalue())

    def test_someone_elses_secretary_still_gets_a_name_of_its_own(self):
        self.make("secretary")  # there before setup.py asked for anything (not the one it asked for)
        out = io.StringIO()
        self.assertEqual(setup.secretary(out), 0)
        self.assertEqual(self.opened(), ["secretary-2"])
        self.assertIn("已經有 #secretary 了，秘書開在 #secretary-2", out.getvalue())
        self.assertEqual(self.settings()["planner"]["channel"], self.id_of("secretary-2"))

    def test_the_tour_channel_the_same(self):
        self.write({"format": 1, "core": {"timezone": "UTC"}, "setup": {"done": True}})
        self.bot_makes = False
        with self.assertRaises(setup.Failed):
            setup.tutorial(io.StringIO())
        self.make("tutorial")
        out = io.StringIO()
        self.assertEqual(setup.tutorial(out), 0)
        self.assertEqual(self.opened(), ["tutorial"], "asked once: no #tutorial-2")
        self.assertIn("導覽開在 <#%s>" % self.id_of("tutorial"), out.getvalue())
        self.assertEqual(self.settings()["setup"]["answered"]["tour"], "start")


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


class PluginSteps(Rig):
    """C-77 G1: plugin-steps prints the plan from checks run now; plugin-said keeps an answer only for an item that
    cannot be checked here (its check null, or giving 3 now)."""
    def setUp(self):
        super().setUp()
        self.folder = os.path.join(self.home, "插件 demo")
        os.makedirs(self.folder)
        with open(os.path.join(self.folder, "code"), "w") as f:
            f.write("3")
        d = {"name": "demo", "needs": [{"name": "tool", "check": "true"}],
             "guide": [{"id": "far", "title": "到後台", "check": 'exit $(cat "{plugin}/code")', "ask": "後台好了嗎？"},
                       {"id": "word", "title": "問一句", "check": None, "ask": "好了嗎？"}]}
        rows = [("demo", self.folder, d, "on", ""), ("off", self.folder, d, "off", "不是它跑的機器")]
        p = mock.patch.object(setup.plugin, "on_this_machine", return_value=(rows, []))
        p.start()
        self.addCleanup(p.stop)

    def test_plan_then_answers_then_done(self):
        code, out = self.run_("plugin-steps", "demo")
        self.assertEqual(code, 3, out)
        self.assertIn("1. ❓ 到後台（far）：要問你：「後台好了嗎？」　← 下一步", out)
        self.assertIn("✅ tool：有了", out)
        self.assertEqual(self.run_("plugin-said", "demo", "far", "yes"), (0, "記下了：demo far yes（做好了）\n"))
        self.assertEqual(self.run_("plugin-said", "demo", "word", "yes")[0], 0)
        code, out = self.run_("plugin-steps", "demo", "--json")
        self.assertEqual((code, json.loads(out)["left"]), (0, 0), out)

    def test_said_refused_where_a_check_can_tell(self):
        with open(os.path.join(self.folder, "code"), "w") as f:
            f.write("1")
        code, out = self.run_("plugin-said", "demo", "far", "yes")
        self.assertEqual(code, 1)
        self.assertIn("far 這台檢查得到，以檢查結果為準：還沒好", out)
        code, out = self.run_("plugin-said", "demo", "needs:tool", "yes")
        self.assertIn("needs:tool 這台檢查得到，以檢查結果為準：已經好了", out)
        for argv in (("plugin-said", "demo", "nope", "yes"), ("plugin-said", "demo", "word", "maybe"),
                     ("plugin-steps", "off"), ("plugin-steps", "none")):
            self.assertEqual(self.run_(*argv)[0], 1, argv)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local", "state", "altention", "setup", "guide", "demo.json")))


class FinishingAPlugin(Rig):
    """C-77 G2: plugin-run does a step's "run" and checks it again; plugin-tool installs a tool without sudo only;
    plugin-steps --all asks the other machines that use the plugin for their own plans (ssh faked)."""
    def setUp(self):
        super().setUp()
        self.folder = os.path.join(self.home, "插件 demo")
        os.makedirs(self.folder)
        self.d = {"name": "demo",
                  "needs": [{"name": "tool", "check": 'test -e "{plugin}/tool"',
                             "install": {"debian": 'touch "{0}/tool"'.format(self.folder), "macos": 'touch "{0}/tool"'.format(self.folder)}},
                            {"name": "root", "check": "false", "install": {"debian": "sudo apt install -y root", "macos": "sudo true"}},
                            {"name": "bare", "check": "false", "hint": "看 README"}],
                  "guide": [{"id": "deploy", "title": "部署", "run": 'echo deployed; touch "{plugin}/deployed"',
                             "check": 'test -e "{plugin}/deployed"'},
                            {"id": "broken", "title": "壞的", "run": "echo nope; exit 5", "check": "false"},
                            {"id": "far", "title": "到後台", "check": None, "ask": "好了嗎？"}]}
        rows = [("demo", self.folder, self.d, "on", "")]
        for p in (mock.patch.object(setup.plugin, "on_this_machine", return_value=(rows, [])),
                  mock.patch("altention.plugin_steps.os_name", return_value="debian")):
            p.start()
            self.addCleanup(p.stop)

    def test_run_does_the_step_and_checks_it_again(self):
        code, out = self.run_("plugin-run", "demo", "deploy")
        self.assertEqual(code, 0, out)
        self.assertIn("deploy 跑完了（結束碼 0）：\ndeployed", out)
        self.assertIn("deploy 檢查過了：好了", out)
        code, out = self.run_("plugin-run", "demo", "broken")
        self.assertEqual(code, 2, out)
        self.assertIn("broken 沒有成功（結束碼 5）", out)
        for step in ("far", "nope"):
            code, out = self.run_("plugin-run", "demo", step)
            self.assertEqual(code, 1, out)
            self.assertIn("不是插件代做的步驟", out)

    def test_tool_without_sudo_only(self):
        code, out = self.run_("plugin-tool", "demo", "tool")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "tool 跑完了（結束碼 0）\ntool 檢查過了：好了\n", "printed nothing: one line, no empty one")
        code, out = self.run_("plugin-tool", "demo", "root")
        self.assertEqual(code, 1, out)
        self.assertIn("root 要用 sudo 裝，請使用者在自己的終端機跑：sudo apt install -y root", out)
        code, out = self.run_("plugin-tool", "demo", "bare")
        self.assertIn("沒有寫 bare 在這個系統上怎麼裝", out)

    def test_all_asks_the_other_machines(self):
        here = {"left": 0, "guide": [], "needs": []}
        seen = []

        def run_on(m, script, timeout=600):
            seen.append((m["name"], timeout, script))
            return {"far": ("far", 3, json.dumps({"plugin": "demo", "level": 2, "setup": [], "needs": [
                {"name": "tool", "state": "todo", "reason": "", "why": "", "size": "", "optional": False, "for": [],
                 "install": "x", "sudo": False, "hint": "", "ask": ""}], "guide": [], "next": None, "left": 1,
                "offers": 0, "failing": []}), ""),
                    "old": ("old", 2, "用法：python3 ops/setup.py status", ""),
                    "gone": (None, 255, "", "unreachable")}[m["name"]]
        machines = {n: {"name": n, "ssh": [n]} for n in ("far", "old", "gone")}
        with mock.patch.object(setup.plugins, "users_elsewhere", return_value=["far", "old", "gone", "nowhere"]), \
                mock.patch("common.inventory", return_value={"machines": machines}), \
                mock.patch("common.run_on", run_on), \
                mock.patch("altention.plugin_steps.plan", return_value=dict(here, plugin="demo", level=1, setup=[],
                                                                            next=None, offers=0, failing=[])):
            code, out = self.run_("plugin-steps", "demo", "--all")
            self.assertEqual(code, 3, out)
            self.assertIn("far：\ndemo 在這台的安裝計畫（第 2 級：要裝系統工具）", out)
            self.assertIn("old：那台的引擎還沒有這個檢查，要先更新那台的引擎", out)
            self.assertIn("gone：連不上，這次查不到（unreachable）", out)
            self.assertIn("nowhere：機器清單裡沒有這台，查不到", out)
            code, out = self.run_("plugin-steps", "demo", "--all", "--json")
        self.assertEqual(json.loads(out)["others"]["far"]["left"], 1)
        self.assertEqual({t for _, t, _ in seen}, {setup.ELSEWHERE_TIMEOUT})
        self.assertIn('"plugin-steps", \'demo\', "--json"', seen[0][2])


class PluginFinish(Rig):
    """C-77 G5: plugin-finish runs install.sh once nothing is left, so what the gates held starts."""
    def setUp(self):
        super().setUp()
        self.folder = os.path.join(self.home, "插件 demo")
        os.makedirs(self.folder)
        self.d = {"name": "demo", "guide": [{"id": "key", "title": "拿金鑰", "check": 'test -e "{plugin}/done"', "gate": ["tools"]}]}
        p = mock.patch.object(setup.plugin, "on_this_machine", return_value=([("demo", self.folder, self.d, "on", "")], []))
        p.start()
        self.addCleanup(p.stop)

    def test_refused_while_something_is_left_then_install(self):
        ran = []
        with mock.patch.object(setup, "script", lambda argv, timeout=1800: ran.append(argv) or subprocess.CompletedProcess(argv, 0, "ok core.done\n")):
            code, out = self.run_("plugin-finish", "demo")
            self.assertEqual(code, 1, out)
            self.assertIn("demo 還有 1 件事沒做完", out)
            self.assertEqual(ran, [])
            open(os.path.join(self.folder, "done"), "w").close()
            code, out = self.run_("plugin-finish", "demo")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "install.sh：ok core.done\ndemo 要等的都開始了\n")
        self.assertTrue(ran[0][0].endswith("install.sh"))


class SecretSend(Rig):
    """C-77 G3a: secret-send writes a secret stored here on a machine that uses the plugin, through a script on ssh's
    stdin (run here for real in another throwaway home); never to another machine, never with nothing stored, never
    printing the value."""
    VALUE = "rig-value-0123456789"

    def setUp(self):
        super().setUp()
        from altention import secret_store
        self.ss = secret_store
        self.d = {"name": "demo", "secrets": [{"name": "rig-token"}]}
        rows = [("demo", self.home, self.d, "on", "")]
        self.audit, self.scripts = [], []
        self.far = tempfile.mkdtemp(prefix="家 far ")
        self.addCleanup(shutil.rmtree, self.far, True)
        for p in (mock.patch.object(setup.plugin, "on_this_machine", return_value=(rows, [])),
                  mock.patch.object(secret_store.plugin, "on_this_machine", return_value=(rows, [])),
                  mock.patch.object(secret_store, "tell_audit", self.audit.append),
                  mock.patch.object(setup.plugins, "users_elsewhere", return_value=["far"]),
                  mock.patch("common.inventory", return_value={"machines": {"far": {"ssh": ["far"]}, "lap": {"ssh": ["local"]}}}),
                  mock.patch("common.run_on", self.run_on)):
            p.start()
            self.addCleanup(p.stop)

    def run_on(self, m, script, timeout=600):
        self.scripts.append((m, timeout, script))
        r = subprocess.run([sys.executable, "-c", script], env=dict(os.environ, HOME=self.far), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        return "far", r.returncode, r.stdout, r.stderr

    def test_sent_600_to_a_machine_that_uses_it(self):
        self.assertEqual(self.run_("secret-send", "demo", "rig-token", "far")[0], 1, "nothing stored here yet")
        self.ss.write("demo", "rig-token", self.VALUE)
        code, out = self.run_("secret-send", "demo", "rig-token", "far")
        self.assertEqual(code, 0, out)
        self.assertEqual(out, "rig-token 送到 far 了\n")
        there = os.path.join(self.far, ".config", "altention", "secrets", "demo", "rig-token")
        with open(there) as f:
            self.assertEqual(f.read(), self.VALUE)
        self.assertEqual(stat.S_IMODE(os.stat(there).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(there)).st_mode), 0o700)
        self.assertEqual(self.scripts[-1][1], setup.SECRET_SEND_TIMEOUT)
        self.assertEqual(self.audit, ["🔑 far 存了 demo 的 rig-token（從 box 送過去）"])

    def test_refused_elsewhere_or_undeclared(self):
        self.ss.write("demo", "rig-token", self.VALUE)
        for argv, words in ((("demo", "rig-token", "lap"), "lap 不用 demo，不送"), (("demo", "rig-token", "nowhere"), "不用 demo"),
                            (("demo", "other", "far"), "沒有宣告叫 other"), (("nope", "rig-token", "far"), "找不到 nope")):
            code, out = self.run_("secret-send", *argv)
            self.assertEqual(code, 1, (argv, out))
            self.assertIn(words, out)
            self.assertNotIn(self.VALUE, out)
        self.assertEqual(self.scripts, [])


OFFERED = {"plugins": {
    "rig-pdf": {"plugin": {"name": "rig-pdf", "title": "PDF", "pitch": "看 PDF。", "offer": {"when": "install"}}},
    "rig-two": {"plugin": {"name": "rig-two", "title": "第二個", "pitch": "另一個。", "offer": {"when": "install"}}},
    "rig-cal": {"plugin": {"name": "rig-cal", "title": "日曆", "pitch": "接日曆。", "offer": {"when": "asked"}}}}}


class Offers(ReconcileRig):
    """C-64, C-77 G4: optional plugins of the official list offered in #setup in fixed words: by setup.py offer
    (asked); the reconcile asks an install offer never answered (an installation older than the question, or one whose
    list was not there when #setup asked) once, and one answered 之後再說 again, at most three times; 先不要 and 好，開
    are never asked again."""
    def setUp(self):
        super().setUp()
        p = mock.patch("altention.offers.catalog.load", return_value=OFFERED)
        p.start()
        self.addCleanup(p.stop)

    def test_an_installation_never_asked_is_asked_once_after_its_update(self):
        code, out = self.run_("reconcile")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.looked, 1, "the official list looked at before the questions")
        self.assertLess(out.index("官方插件清單沒有變\n"), out.index("問 "), "said before the plan")
        ask = self.settings()["setup"]["ask"]
        self.assertIn("offer.rig-pdf", ask, "no offers record (from before the question): asked")
        self.assertIn("offer.rig-two", ask)
        self.assertNotIn("offer.rig-cal", ask, "an asked offer only when something asks for it")
        self.assertIn("問 可選的插件 rig-pdf（要不要開，到 #setup 問）", out)
        n = len(ask)
        self.run_("reconcile")
        self.assertEqual(len(self.settings()["setup"]["ask"]), n, "not answered yet: not added a second time")
        self.answer("offer.rig-pdf", "no")
        self.answer("offer.rig-two", "yes")
        for _ in range(2):
            self.run_("reconcile")
            ask = self.settings()["setup"]["ask"]
            self.assertNotIn("offer.rig-pdf", ask, "先不要: never again")
            self.assertNotIn("offer.rig-two", ask, "好，開: never again")

    def test_an_old_installation_updated_says_nothing_in_update_and_is_asked_in_setup(self):
        """A v0.1.0 installation updated: no told record, no offers record. catalog.news() still says nothing the
        first time (every plugin marked told: "#setup offers them"); the reconcile is what asks, so the plugin is not
        missed by both ways (the manager, 2026-11-03)."""
        from altention import catalog
        self.assertFalse(os.path.exists(catalog.told_path()))
        self.assertEqual(catalog.news(), [], "the first time: nothing said in #update (the rule stays)")
        self.assertEqual(catalog._told(), {"rig-pdf", "rig-two", "rig-cal"})
        self.assertNotIn("offers", self.settings())
        self.run_("reconcile")
        ask = self.settings()["setup"]["ask"]
        self.assertIn("offer.rig-pdf", ask, "asked in #setup instead")
        self.assertIn("offer.rig-two", ask)
        self.assertNotIn("offer.rig-cal", ask)
        self.assertTrue([t for c, t in self.said if c == "update" and "到 #setup 回答" in t and "可選功能" in t], self.said)

    def test_the_permission_mode_is_written_before_the_list_is_looked_at(self):
        """#code-review of 1ca3c0e: the look may take a minute; the bot opens no conversation until the mode is
        written, so it is written first."""
        seen = []

        def look():
            seen.append(setup.config.setting(setup.PERMISSION_KEY))
            return self.look_said
        self.assertTrue(setup.permission_unwritten(setup.setup_state()), "an installation from before #setup")
        with mock.patch("altention.catalog.look_before_asking", side_effect=look):
            code, out = self.run_("reconcile")
        self.assertEqual(code, 0, out)
        self.assertEqual(seen, [setup.WRITTEN_MODE], "written when the list was looked at")
        self.assertEqual(out.count(setup.WRITTEN_AUDIT), 2, "the plan's line and the write's, as before: written once")
        self.assertEqual(len([c for c in self.said if c == ("audit", setup.WRITTEN_AUDIT)]), 1, self.said)
        self.assertIn("offer.rig-pdf", self.settings()["setup"]["ask"])

    def test_dry_run_looks_at_nothing(self):
        code, out = self.run_("reconcile", "--dry-run")
        self.assertEqual(self.looked, 0, "a dry run writes nothing, the list's file included")
        self.assertIn("問 可選的插件 rig-pdf", out)

    def test_the_list_not_reached_the_reconcile_goes_on(self):
        self.look_said = ("unreached", "連不上 GitHub，官方插件清單用上次的（x）")
        code, out = self.run_("reconcile")
        self.assertEqual(code, 0, out)
        self.assertIn("連不上 GitHub", out)
        self.assertIn("offer.rig-pdf", self.settings()["setup"]["ask"], "the list kept is used")

    def test_one_the_instance_names_on_or_off_is_not_asked(self):
        d = self.settings()
        d["plugins"] = {"rig-pdf": {"disabled_on": ["lap"]}, "rig-two": {"source": "x", "version": "v1.0.0"}}
        self.write(d)
        self.run_("reconcile")
        ask = self.settings()["setup"]["ask"]
        self.assertNotIn("offer.rig-pdf", ask, "turned off")
        self.assertNotIn("offer.rig-two", ask, "put on already")

    def test_a_later_is_asked_again_three_times_at_most(self):
        d = self.settings()
        d["offers"] = {"rig-pdf": {"answer": "later", "at": 1, "times": 1}, "rig-two": {"answer": "no", "at": 1, "times": 0}}
        self.write(d)
        self.run_("reconcile")
        ask = self.settings()["setup"]["ask"]
        self.assertIn("offer.rig-pdf", ask)
        self.assertNotIn("offer.rig-two", ask, "先不要 at setup: never asked again")
        q = next(q for q in json.loads(self.run_("questions", "--json")[1]) if q["key"] == "offer.rig-pdf")
        self.assertEqual(q["question"], "PDF：看 PDF。\n需要：不用另外裝東西。要開嗎？")
        for n in range(2):  # the first later was at setup
            self.answer("offer.rig-pdf", "later")
            self.assertNotIn("offer.rig-pdf", self.settings()["setup"]["ask"])
            self.run_("reconcile")
            self.assertEqual("offer.rig-pdf" in self.settings()["setup"]["ask"], n < 1, n)
        self.assertEqual(self.settings()["offers"]["rig-pdf"]["answer"], "no")
        self.answer("offer.rig-two", "maybe", code=1)

    def test_offer_asks_in_setup_only_what_may_be_asked(self):
        self.run_("reconcile")
        n = len(self.posted)
        code, out = self.run_("offer", "rig-cal")
        self.assertEqual((code, out), (0, "rig-cal 會在 #setup 問\n"))
        self.assertIn("offer.rig-cal", self.settings()["setup"]["ask"])
        self.assertEqual([json.loads(i) for a, i in self.posted[n:] if a == ("post",)], [{"channel": "setup", "open_setup": True, "text": ""}])
        code, out = self.run_("answer", "offer.rig-cal", "yes")
        self.assertIn("好，接著裝 rig-cal", out)
        for name, words in (("rig-cal", "已經問過了"), ("rig-pdf", "不是官方清單上"), ("nope", "不是官方清單上")):
            code, out = self.run_("offer", name)
            self.assertEqual(code, 1, out)
            self.assertIn(words, out)


class OffersAtSetup(Rig):
    def test_an_install_offer_is_among_the_questions(self):
        with mock.patch("altention.offers.catalog.load", return_value=OFFERED):
            keys = [q["key"] for q in json.loads(self.run_("questions", "--json")[1])]
        self.assertIn("offer.rig-pdf", keys)
        self.assertIn("offer.rig-two", keys)
        self.assertNotIn("offer.rig-cal", keys)
        self.assertLess(keys.index("offer.rig-pdf"), keys.index("backup"))


class Usage(Rig):
    def test_usage(self):
        for argv in ([], ["answer", "x"], ["status", "x"], ["backup-set", "logs"], ["done", "--json"]):
            self.assertEqual(self.run_(*argv)[0], 2, argv)


if __name__ == "__main__":
    unittest.main()
