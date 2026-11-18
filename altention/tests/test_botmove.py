"""altention/botmove.py (C-110): the bot's old settings file moved into the core's secrets and plugins.json, in the
order the manager approved (2026-10-30), each failure leaving the old file as it was; and dcbot/bot/envfile.py
reading the new places first, then the old file. In a throwaway home with a throwaway instance; the token is a fake
one, and no output, line or error may hold any part of it."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from altention import botmove, config, secret_store

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ENGINE, "dcbot"))
from bot import envfile  # noqa: E402

TOKEN = "rig-NOT-A-TOKEN.abcdefghij.0123456789KLMNOP"
PIECES = [TOKEN[i:i + 8] for i in range(0, len(TOKEN) - 8, 4)]  # any 8 characters of it, wherever they start
OLD = "DISCORD_TOKEN=%s\nOWNER_ID=42\nGUILD_ID=7\nCLAUDE_MODEL=sonnet\nMAX_PARALLEL=5\nINTERIM=0\nIDLE_HOURS=6\n" \
      "ARCHIVE_CATEGORY=old-archive\n# a comment\n" % TOKEN


class Rig(unittest.TestCase):
    FAKE_LIST = True  # bot_machine() and known_here() stood in for; RealList uses a real machine list instead

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 c110 ")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.inst = os.path.join(self.home, "altention", "instance")
        os.makedirs(self.inst)
        self.git("init", "-q", "-b", "main")
        self.write_settings({"core": {"timezone": "Etc/UTC"}})
        self.git("add", "-A")
        self.git("-c", "user.name=z", "-c", "user.email=z@example.org", "commit", "-qm", "start")
        env = mock.patch.dict(os.environ, {"HOME": self.home, "ALT_INSTANCE": self.inst})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("DCBOT_ENV", None)
        self.here = True
        self.known = True
        self.proven = True  # the supervisor's last_good reads the new places (Proven tests the real one)
        for p in (() if not self.FAKE_LIST else (
                mock.patch.object(config, "bot_machine", side_effect=lambda: ("lap", {}, self.here)),
                mock.patch.object(config, "known_here", side_effect=lambda: self.known))) + (
                mock.patch.object(botmove, "proven", side_effect=lambda here, engine=None: self.proven or not here),):
            p.start()
            self.addCleanup(p.stop)

    def git(self, *args):
        return subprocess.run(["git", "-C", self.inst] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, check=True).stdout

    def write_settings(self, d):
        with open(os.path.join(self.inst, "plugins.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

    def settings(self):
        with open(os.path.join(self.inst, "plugins.json"), encoding="utf-8") as f:
            return json.load(f)

    def old(self):
        return os.path.join(self.home, ".config", "dcbot", "env")

    def put_old(self, text=OLD):
        os.makedirs(os.path.dirname(self.old()), mode=0o700, exist_ok=True)
        with open(self.old(), "w") as f:
            f.write(text)
        os.chmod(self.old(), 0o600)

    def secret(self, name):
        return os.path.join(self.home, ".config", "altention", "secrets", "core", name)

    def stored(self, name):
        with open(self.secret(name)) as f:
            return f.read()

    def move(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code, line, self.done = botmove.move()
        self.clean(line + out.getvalue())
        return code, line

    def clean(self, text):
        for piece in PIECES:
            self.assertNotIn(piece, text, "a piece of the token was said")
        self.assertNotIn(hashlib.sha256(TOKEN.encode()).hexdigest()[:12], text)

    def untouched(self, before):
        with open(self.old()) as f:
            self.assertEqual(f.read(), before, "the old file is as it was")
        for name in ("discord-token", "owner-id", "guild-id"):
            self.assertFalse(os.path.exists(self.secret(name)), name)


class Moves(Rig):
    def test_everything_moves_once_then_nothing(self):
        self.put_old()
        code, line = self.move()
        self.assertEqual(code, 0, line)
        self.assertEqual(line, botmove.t("botmove.moved"))
        self.assertEqual(self.stored("discord-token"), TOKEN)
        self.assertEqual((self.stored("owner-id"), self.stored("guild-id")), ("42", "7"))
        for name in ("discord-token", "owner-id", "guild-id"):
            self.assertEqual(stat.S_IMODE(os.stat(self.secret(name)).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.secret("x"))).st_mode), 0o700)
        self.assertEqual(self.settings()["dcbot"], {"model": "sonnet", "max_parallel": 5, "interim": False,
                                                    "idle_hours": 6, "channels": {"archive": "old-archive"}})
        self.assertEqual(self.settings()["core"], {"timezone": "Etc/UTC"}, "the rest of plugins.json kept")
        self.assertFalse(os.path.exists(self.old()))
        self.assertFalse(os.path.exists(os.path.dirname(self.old())), "its folder, left empty, goes too")
        self.assertIn("dcbot: the bot's settings moved", self.git("log", "-1", "--format=%s"))
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.clean(self.git("log", "-p"))
        self.assertEqual(self.move(), (0, ""), "run again: nothing to do, nothing said")

    def test_the_folder_stays_when_something_else_is_in_it(self):
        self.put_old()
        with open(os.path.join(os.path.dirname(self.old()), "mine"), "w") as f:
            f.write("x")
        self.assertEqual(self.move()[0], 0)
        self.assertFalse(os.path.exists(self.old()))
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(self.old()), "mine")))

    def test_another_machine_moves_the_secrets_and_drops_the_rest(self):
        self.here = False
        self.put_old()
        before = self.settings()
        code, line = self.move()
        self.assertEqual(code, 0, line)
        self.assertEqual(self.stored("discord-token"), TOKEN)
        self.assertEqual(self.settings(), before, "plugins.json is the bot's machine's to write")
        self.assertIn("CLAUDE_MODEL、MAX_PARALLEL、INTERIM、IDLE_HOURS、ARCHIVE_CATEGORY", line)
        self.assertIn("這台不是機器人那台", line)
        self.assertFalse(os.path.exists(self.old()))

    def test_what_plugins_json_has_already_stays_and_is_named(self):
        self.write_settings({"dcbot": {"model": "opus", "max_parallel": 5.0}})
        self.put_old()
        code, line = self.move()
        self.assertEqual(code, 0, line)
        self.assertEqual(self.settings()["dcbot"]["model"], "opus")
        self.assertEqual(self.settings()["dcbot"]["max_parallel"], 5.0, "the same number: not named")
        self.assertIn("照新位置的：dcbot.model（舊檔是 sonnet，留 plugins.json 的 opus）。", line)
        self.assertNotIn("max_parallel", line)
        self.assertFalse(os.path.exists(self.old()))

    def test_a_store_value_that_differs_keeps_the_old_file(self):
        """manager 2026-11-03: the old file may be the newer one (edited by hand): it is never deleted then."""
        os.makedirs(os.path.dirname(self.secret("x")), mode=0o700)
        for name, value in (("discord-token", "rig-other-token\n"), ("guild-id", "7"), ("owner-id", "41")):
            with open(self.secret(name), "w") as f:
                f.write(value)
        self.put_old()
        before = self.settings()
        code, line = self.move()
        self.assertEqual(code, 1, line)
        self.assertIn("祕密資料夾已經有、值跟舊檔不一樣的：discord-token、owner-id", line)
        self.assertNotIn("guild-id", line, "the same value: not named")
        self.assertIn("altention secret set core <名字>", line)
        self.assertIn("）。機器人現在用的是祕密資料夾裡的，舊檔沒動。要留祕密資料夾的，把舊檔裡那一行拿掉", line)
        self.assertNotIn("照舊用", line)
        with open(self.old()) as f:
            self.assertEqual(f.read(), OLD, "the old file is as it was")
        self.assertEqual((self.stored("discord-token"), self.stored("owner-id")), ("rig-other-token\n", "41"))
        self.assertEqual(self.settings(), before, "nothing written")
        self.assertEqual(botmove.pending(), (botmove.t("botmove.doctor", why=line.split("（", 1)[1].split("）")[0]),
                                             botmove.t("botmove.todo_differs")))
        with open(self.secret("discord-token"), "w") as f:  # set to the old file's value: then it moves
            f.write(TOKEN)
        with open(self.secret("owner-id"), "w") as f:
            f.write("42\n")
        self.assertEqual(self.move()[0], 0)
        self.assertFalse(os.path.exists(self.old()))
        self.assertIsNone(botmove.pending())


class Waits(Rig):
    """manager 2026-11-03: the old file is deleted only once a rollback cannot bring back a version reading it alone."""

    def test_copied_first_deleted_once_proven(self):
        self.proven = False
        self.put_old()
        code, line = self.move()
        self.assertEqual((code, self.done), (0, False))
        self.assertEqual(line, botmove.t("botmove.copied"))
        self.assertEqual(self.stored("discord-token"), TOKEN)
        self.assertEqual(self.settings()["dcbot"]["model"], "sonnet")
        with open(self.old()) as f:
            self.assertEqual(f.read(), OLD, "the old file is as it was, beside the new places")
        self.assertIsNone(botmove.pending(), "nothing for a person to do: doctor quiet")
        commits = self.git("rev-list", "--count", "HEAD")
        code, line = self.move()
        self.assertEqual((code, line, self.done), (0, botmove.t("botmove.copied"), False), "again: the same, said the same")
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), commits, "no second commit")
        self.proven = True
        code, line = self.move()
        self.assertEqual((code, line, self.done), (0, botmove.t("botmove.moved"), True))
        self.assertFalse(os.path.exists(self.old()))

    def test_another_machine_has_no_supervisor_to_wait_for(self):
        self.here, self.proven = False, False
        self.put_old()
        code, line = self.move()
        self.assertEqual((code, self.done), (0, True))
        self.assertFalse(os.path.exists(self.old()))


class Proven(unittest.TestCase):
    """botmove.proven(): the supervisor's last_good, as a real git engine has it."""

    def setUp(self):
        self.eng = tempfile.mkdtemp(prefix="引擎 c110 ")
        self.addCleanup(shutil.rmtree, self.eng, True)
        run = lambda *a: subprocess.run(["git", "-C", self.eng] + list(a), stdout=subprocess.PIPE,  # noqa: E731
                                        stderr=subprocess.PIPE, universal_newlines=True, check=True).stdout.strip()
        run("init", "-q", "-b", "main")
        os.makedirs(os.path.join(self.eng, "altention"))
        with open(os.path.join(self.eng, "README"), "w") as f:
            f.write("old\n")
        run("add", "-A")
        run("-c", "user.name=z", "-c", "user.email=z@example.org", "commit", "-qm", "old")
        self.old = run("rev-parse", "HEAD")
        with open(os.path.join(self.eng, "altention", "botmove.py"), "w") as f:
            f.write("# new\n")
        run("add", "-A")
        run("-c", "user.name=z", "-c", "user.email=z@example.org", "commit", "-qm", "new")
        self.new = run("rev-parse", "HEAD")
        os.makedirs(os.path.join(self.eng, "dcbot", "state"))

    def good(self, rev):
        with open(os.path.join(self.eng, "dcbot", "state", "last_good"), "w") as f:
            f.write(rev + "\n")

    def test_last_good_must_read_the_new_places(self):
        self.assertFalse(botmove.proven(True, self.eng), "no last_good yet")
        self.good(self.old)
        self.assertFalse(botmove.proven(True, self.eng), "a rollback would bring back the old version")
        self.good(self.new)
        self.assertTrue(botmove.proven(True, self.eng))
        self.good("0" * 40)
        self.assertFalse(botmove.proven(True, self.eng), "a commit it cannot read: kept")
        self.assertTrue(botmove.proven(False, self.eng), "no supervisor on another machine")
        self.assertTrue(botmove.reads_new(self.new, self.eng))
        self.assertFalse(botmove.reads_new(self.old, self.eng))


class Restore(Rig):
    """manager 2026-11-03 (A): before undo goes back to a version that reads the old file alone, it is written back
    from the store and plugins.json as they are now, read back; the store stays."""

    def test_written_back_with_the_values_of_now(self):
        self.put_old()
        self.assertEqual(self.move()[0], 0)
        self.assertFalse(os.path.exists(self.old()))
        with open(self.secret("owner-id"), "w") as f:  # changed after the move (secret set)
            f.write("43\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok, line = botmove.restore_old()
        self.clean(line + out.getvalue())
        self.assertEqual((ok, line), (True, botmove.t("botmove.restored")))
        self.assertEqual(stat.S_IMODE(os.stat(self.old()).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.old())).st_mode), 0o700)
        got = envfile.parse(self.old())
        self.assertEqual(got, {"DISCORD_TOKEN": TOKEN, "OWNER_ID": "43", "GUILD_ID": "7", "CLAUDE_MODEL": "sonnet",
                               "MAX_PARALLEL": "5", "INTERIM": "0", "IDLE_HOURS": "6", "ARCHIVE_CATEGORY": "old-archive"})
        self.assertEqual(self.stored("discord-token"), TOKEN, "the store stays")
        self.assertEqual(botmove.restore_old(), (True, ""), "there already: nothing")
        self.assertEqual(self.move()[0], 0, "moving again: the same values, so it goes as before")
        self.assertFalse(os.path.exists(self.old()))

    def test_nothing_stored_nothing_written(self):
        self.assertEqual(botmove.restore_old(), (True, ""))
        self.assertFalse(os.path.exists(self.old()))

    def test_a_store_that_cannot_be_read_says_so(self):
        self.put_old()
        self.assertEqual(self.move()[0], 0)
        os.chmod(self.secret("discord-token"), 0)
        self.addCleanup(os.chmod, self.secret("discord-token"), 0o600)
        if os.access(self.secret("discord-token"), os.R_OK):
            self.skipTest("root reads it anyway")
        ok, line = botmove.restore_old()
        self.assertFalse(ok)
        self.assertIn("機器人的設定寫不回 ~/.config/dcbot/env（PermissionError）", line)
        self.assertFalse(os.path.exists(self.old()))


class Stops(Rig):
    def test_an_unknown_key_moves_nothing(self):
        text = OLD + "SOMETHING=secret-ish-value\n"
        self.put_old(text)
        before = self.settings()
        code, line = self.move()
        self.assertEqual(code, 1)
        self.assertIn("env 裡有不認得的鍵：SOMETHING", line)
        self.assertIn("把那個鍵拿掉後再跑一次 install 就會搬", line)
        self.assertNotIn("secret-ish-value", line)
        self.untouched(text)
        self.assertEqual(self.settings(), before)

    def test_unreadable_settings_or_list_move_nothing(self):
        self.put_old()
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            f.write("{ not json")
        code, line = self.move()
        self.assertEqual(code, 1)
        self.assertIn("plugins.json 讀不出來或寫不進去", line)
        self.untouched(OLD)
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            f.write("{}")
        with mock.patch.object(config, "bot_machine", side_effect=config.ConfigError("broken")):
            code, line = self.move()
        self.assertEqual(code, 1)
        self.assertIn("機器清單讀不出來", line)
        self.untouched(OLD)


    def test_the_reason_is_written_whole_and_stamped(self):
        """#code-review 11-03: under ~/.local/state/altention, as the tests' guard reads it (altention/statefile.py)."""
        from altention import statefile
        self.put_old("NOPE=1\n")
        self.assertEqual(self.move()[0], 1)
        with open(botmove.state_path(), encoding="utf-8") as f:
            d = json.load(f)
        self.assertIn(statefile.STAMP, d)
        self.assertIn("NOPE", d["why"])
        self.assertEqual([n for n in os.listdir(os.path.dirname(botmove.state_path())) if n.endswith(".tmp")], [])

    def test_pending_says_what_waits(self):
        self.assertIsNone(botmove.pending())
        self.put_old()
        self.assertEqual(botmove.pending(), (botmove.t("botmove.doctor_waiting"), botmove.t("botmove.todo_pending")))
        self.untouched(OLD)

    def test_the_store_failing_keeps_the_old_file(self):
        self.put_old()
        with mock.patch.object(secret_store, "write", side_effect=OSError("disk full: " + TOKEN)):
            code, line = self.move()
        self.assertEqual(code, 1)
        self.assertIn("祕密資料夾寫不進去：OSError", line)
        self.untouched(OLD)

    def test_a_read_back_that_differs_keeps_the_old_file(self):
        self.put_old()
        real = secret_store.write

        def wrong(plugin, name, value):
            return real(plugin, name, value + "x")
        with mock.patch.object(secret_store, "write", side_effect=wrong):
            code, line = self.move()
        self.assertEqual(code, 1)
        self.assertIn("讀回來對不上", line)
        with open(self.old()) as f:
            self.assertEqual(f.read(), OLD)

    def test_a_delete_that_fails_says_so(self):
        self.put_old()
        with mock.patch.object(botmove.os, "remove", side_effect=PermissionError("no")):
            code, line = self.move()
        self.assertEqual(code, 1)
        self.assertIn("舊檔刪不掉：PermissionError", line)
        self.assertEqual(self.stored("discord-token"), TOKEN)

    def test_anything_else_is_said_by_its_kind(self):
        self.put_old()
        with mock.patch.object(botmove, "write_settings", side_effect=RuntimeError(TOKEN)):
            code, line = self.move()
        self.assertEqual(code, 1)
        self.assertIn("搬的時候出錯：RuntimeError", line)
        self.untouched(OLD)

    def test_a_number_that_does_not_read_names_its_key(self):
        for bad in ("nan", "inf", "twelve"):
            text = OLD.replace("IDLE_HOURS=6", "IDLE_HOURS=" + bad)
            self.put_old(text)
            code, line = self.move()
            self.assertEqual(code, 1, bad)
            self.assertIn("env 裡有讀不懂的值：IDLE_HOURS（要是數字）", line)
            self.assertIn("把舊檔裡那個值改成數字，或整行拿掉", line)
            self.assertNotIn("等一下", line)
            self.untouched(text)

    def test_a_fifo_is_never_opened(self):
        os.makedirs(os.path.dirname(self.old()), mode=0o700)
        os.mkfifo(self.old())
        code, line = self.move()  # opening it would hang: the test would not end
        self.assertEqual(code, 1)
        self.assertIn("舊的設定檔讀不出來：not a file", line)
        self.assertIn("到 #chat 跟 Claude 說「機器人的設定搬不過去」", line)
        self.assertTrue(os.path.exists(self.old()))

    def test_no_old_file_nothing_said(self):
        self.assertEqual(self.move(), (0, ""))

    def test_audit_only_when_asked(self):
        told = []
        self.put_old()
        with mock.patch.object(botmove, "tell_audit", told.append):
            self.assertEqual(self.move()[0], 0)
            self.assertEqual(told, [], "the bot posts its own")
            self.put_old("NOPE=1\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(botmove.main([]), 1)
        self.assertEqual(len(told), 1)
        self.assertIn("env 裡有不認得的鍵：NOPE", told[0])
        self.assertEqual(out.getvalue().strip(), told[0])


class RealList(Rig):
    """A real machine list, as the examples teach it (the bot's entry reached "local"), and this machine's name
    given in ~/.config/altention/host: nothing stood in for (#code-review 2026-11-03)."""
    FAKE_LIST = False

    def list_and_name(self, me):
        with open(os.path.join(self.inst, "machines.json"), "w") as f:
            json.dump({"format": 1, "machines": {"botbox": {"ssh": ["local"], "roles": ["dcbot"]},
                                                 "far": {"ssh": ["far"], "roles": ["dcattach"]}}}, f)
        os.makedirs(os.path.join(self.home, ".config", "altention"), exist_ok=True)
        with open(os.path.join(self.home, ".config", "altention", "host"), "w") as f:
            f.write(me + "\n")

    def test_a_name_the_list_does_not_know_moves_nothing_though_an_entry_is_local(self):
        self.list_and_name("zzz")
        self.assertTrue(config.bot_machine()[2], "the posts' rule takes the local entry for this one")
        self.put_old()
        before = self.settings()
        code, line = self.move()
        self.assertEqual(code, 1)
        self.assertIn("機器清單上找不到這台的名字", line)
        self.untouched(OLD)
        self.assertEqual(self.settings(), before, "plugins.json not written on a guess")
        problem, fix = botmove.pending()
        self.assertEqual(problem, "這台的機器人設定還在舊位置（~/.config/dcbot/env），因為機器清單上找不到這台的名字，不知道它是不是機器人那台")
        self.assertIn("doctor 會說怎麼對上名字", fix)

    def test_the_bots_machine_by_name_moves_all(self):
        self.list_and_name("botbox")
        self.put_old()
        self.assertEqual(self.move(), (0, botmove.t("botmove.moved")))
        self.assertEqual(self.settings()["dcbot"]["model"], "sonnet")
        self.assertIsNone(botmove.pending())

    def test_another_machine_by_name_moves_the_secrets_only(self):
        self.list_and_name("far")
        self.put_old()
        before = self.settings()
        code, line = self.move()
        self.assertEqual(code, 0, line)
        self.assertIn("這台不是機器人那台", line)
        self.assertEqual(self.settings(), before)
        self.assertEqual(self.stored("discord-token"), TOKEN)

    def test_no_list_the_token_here_says_it_is_the_bots(self):
        self.put_old()
        self.assertEqual(self.move(), (0, botmove.t("botmove.moved")))


class Reads(Rig):
    def load(self):
        return envfile.load()

    def test_old_file_only(self):
        self.put_old()
        got = self.load()
        self.assertEqual((got["DISCORD_TOKEN"], got["OWNER_ID"], got["CLAUDE_MODEL"]), (TOKEN, "42", "sonnet"))

    def test_new_places_only(self):
        self.put_old()
        self.assertEqual(self.move()[0], 0)
        got = self.load()
        self.assertEqual((got["DISCORD_TOKEN"], got["OWNER_ID"], got["GUILD_ID"]), (TOKEN, "42", "7"))
        self.assertEqual((got["CLAUDE_MODEL"], got["MAX_PARALLEL"], got["INTERIM"], got["IDLE_HOURS"],
                          got["ARCHIVE_CATEGORY"]), ("sonnet", "5", "0", "6", "old-archive"))

    def test_both_the_new_place_first(self):
        self.put_old()
        self.write_settings({"dcbot": {"model": "haiku", "tmux_socket": "mine"}})
        os.makedirs(os.path.dirname(self.secret("x")), mode=0o700)
        with open(self.secret("owner-id"), "w") as f:
            f.write("43\n")
        got = self.load()
        self.assertEqual((got["CLAUDE_MODEL"], got["OWNER_ID"], got["DISCORD_TOKEN"]), ("haiku", "43", TOKEN))
        self.assertEqual(envfile.bot_server(), "mine")

    def test_bot_server_never_reads_a_secret(self):
        self.put_old()
        self.assertEqual(self.move()[0], 0)
        opened = []
        real = open

        def watch(path, *a, **k):
            opened.append(str(path))
            return real(path, *a, **k)
        with mock.patch("builtins.open", side_effect=watch):
            self.assertEqual(envfile.bot_server(), envfile.BOT_SERVER)
        self.assertFalse([p for p in opened if "secrets" in p], opened)

    def test_a_named_file_alone(self):
        self.put_old()
        self.assertEqual(self.move()[0], 0)
        rig = os.path.join(self.home, "rig-env")
        with open(rig, "w") as f:
            f.write("DISCORD_TOKEN=rig-other\n")
        with mock.patch.dict(os.environ, {"DCBOT_ENV": rig}):
            self.assertEqual(envfile.load(), {"DISCORD_TOKEN": "rig-other"}, "neither the store nor plugins.json")
        with mock.patch.dict(os.environ, {"DCBOT_ENV": ""}):
            self.assertEqual(envfile.load(), {})

    def test_nothing_anywhere(self):
        self.assertEqual(self.load(), {})
        self.assertFalse(config.bot_here())

    def bot_config(self):
        """What bot/config.py reads in a fresh process (the bot's start), in this home."""
        code = "import bot.config as c; print(c.OWNER_ID, c.GUILD_ID, c.MODEL, c.MAX_PARALLEL, c.INTERIM, len(c.TOKEN) > 0)"
        env = dict(os.environ)
        env.pop("DCBOT_ENV", None)
        r = subprocess.run([sys.executable, "-c", code], cwd=os.path.join(ENGINE, "dcbot"), env=env, timeout=60,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.clean(r.stdout + r.stderr)
        return r.stdout.split()

    def test_the_bot_starts_before_and_after_the_move(self):
        self.put_old()
        self.assertEqual(self.bot_config(), ["42", "7", "sonnet", "5", "False", "True"], "old file only")
        self.assertEqual(self.move()[0], 0)
        self.assertEqual(self.bot_config(), ["42", "7", "sonnet", "5", "False", "True"], "new places only")

    def test_bot_here_by_either_file(self):
        self.put_old()
        self.assertTrue(config.bot_here())
        self.assertEqual(self.move()[0], 0)
        self.assertTrue(config.bot_here())


class Tables(unittest.TestCase):
    def test_the_mover_and_the_reader_agree(self):
        self.assertEqual(botmove.SECRETS, envfile.SECRETS)
        self.assertEqual([(k, w) for k, w, _ in botmove.SETTINGS], list(envfile.SETTINGS))
        self.assertEqual(botmove.OLD, envfile.OLD)
        self.assertEqual(os.path.dirname(config.BOT_TOKEN), envfile.STORE)
        self.assertEqual(os.path.expanduser(config.BOT_TOKEN), envfile.secret_path("discord-token"))

    def test_the_core_declares_the_three(self):
        from altention import plugin
        with open(os.path.join(ENGINE, "altention", "core.plugin.json"), encoding="utf-8") as f:
            d = json.load(f)
        moved = [n for _, n in envfile.SECRETS]
        self.assertEqual([s["name"] for s in d["secrets"] if s["name"] in moved], moved)
        self.assertEqual([s["name"] for s in d["secrets"] if s["name"] not in moved], ["standby-webhook", "healthchecks-url"],
                         "C-163: the standby's webhook and the outside notice's address, none of them botmove's")
        self.assertFalse(any("path" in s for s in d["secrets"]))
        plugin.check(d, "core.plugin.json", core=True)

    def test_the_guard_covers_both_places(self):
        from altention import secrets
        for p in ("~/.config/altention/secrets/core/discord-token", "~/" + ".config/dcbot/env"):
            self.assertTrue(secrets.is_secret(os.path.expanduser(p)), p)


if __name__ == "__main__":
    unittest.main()
