"""C-28: the channels' state backed up by a whitelist (dcctl backup) and put back (dcctl restore-state)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()

import fcntl
import glob
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from tests import tmuxguard  # noqa: F401,E402
from bot import agentanswer, backup, lease, main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
loader = importlib.machinery.SourceFileLoader("dcctl_backup", os.path.join(ROOT, "tools", "dcctl"))
dcctl = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_backup", loader))
loader.exec_module(dcctl)

SID, SID2 = "0b8f6c3e-1d2a-4c5b-9e7f-112233445566", "aa11bb22-cc33-4d44-8e55-66778899aabb"
SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FULL = {  # one channel with every kind of field, as the bot keeps them
    "cwd": "/w/proj", "cwd_from": "proj", "configured": True, "mode": "tmux", "permission": "auto",
    "talk_limit": 3, "talk_free": [7], "agent_answers": False, "home": {"socket": "s", "window": "w", "session": "x"},
    "session_id": SID, "boards": {"b": [1]}, "boards_v2": {"design": [True]}, "system": "audit",
    "history": [SID, "not an id: the title of a talk", SID2], "shown": [SID2, "words"],
    "files": {"root": "/w", "host": "lap", "browser": 99},
    "archived": {"pane": {"socket": "s", "id": "%1", "pid": "2"}, "pane_session": "p", "home": None, "tmux": "t",
                 "session_id": SID, "cwd": "/w", "why": "free words", "topic": "a topic", "kind": "conversation",
                 "at": 5.0, "from": {"id": 3, "name": "proj", "extra": 1}},
    "tmux": "dc-x", "pane": {"socket": "s", "id": "%1", "pid": "2"}, "pane_session": "p",
    "took_from": {"machine": "lap", "pending": True}, "secret_boxes": {"k": {"label": "L"}},
    "activity": {"t": {"plugin": "p", "args": {"token": SECRET}}}, "named": "x", "rooms": {}, "subs": {},
    "talk_held": [{"text": "a message"}], "unsent": {"1": "words"}, "note_next": "words", "doing": "words",
    "term_done": "words", "turns": {"1": {"text": "a turn"}}, "turn_ids": {"1": 2}, "turn_hosts": {"1": "lap"},
    "calls": {"1": {}}, "links": {"1": "u"}, "screens": {"1": "s"}, "context": [1], "more": {"1": ["x"]},
    "cmds": [[1]], "cmd_seq": 2, "busy": True, "bg": 1, "last_active": 1.0, "cc_version": "2.1", "asking": {},
    "btw": {}, "workflows": {}, "browsers": {"1": {"path": "/w"}}, "board_fails": {}, "paused": 1,
}


class Snapshot(unittest.TestCase):
    def test_the_whitelist(self):
        snap, unknown = backup.snapshot({"1": dict(FULL), "2": "not a channel", "3": {"busy": True}})
        self.assertEqual(unknown, [])
        self.assertEqual(list(snap), ["1"], "a channel with nothing kept is not written")
        got = snap["1"]
        self.assertEqual(got["history"], [SID, SID2], "conversation ids only")
        self.assertEqual(got["shown"], [SID2])
        self.assertEqual(got["files"], {"root": "/w", "host": "lap"})
        self.assertEqual(got["archived"], {"session_id": SID, "cwd": "/w", "kind": "conversation", "at": 5.0,
                                           "from": {"id": 3, "name": "proj"}})
        self.assertIs(got["agent_answers"], False, "a False setting kept")
        for key in backup.DROP:
            self.assertNotIn(key, got)
        self.assertNotIn(SECRET, json.dumps(snap))

    def test_strings_redacted_and_new_fields_left_out_but_named(self):
        snap, unknown = backup.snapshot({"1": {"cwd": "/w/" + SECRET, "brand_new": "words", "zz": 1}})
        self.assertNotIn(SECRET, snap["1"]["cwd"])
        self.assertEqual(snap["1"], {"cwd": snap["1"]["cwd"]}, "an unclassified field is left out")
        self.assertEqual(unknown, ["brand_new", "zz"])

    def test_keep_and_drop_do_not_overlap(self):
        self.assertEqual(set(backup.KEEP) & backup.DROP, set())


CHANNEL_KEY = re.compile(r'\bst(?:\[|\.get\(|\.setdefault\(|\.pop\()"([a-z_0-9]+)"')
CHAN_UPDATE = re.compile(r'chan\([^()]*\)\.update\(\{([^}]*)\}')
CHAN_SETDEFAULT = re.compile(r'chan\([^()]*\)(?:\[|\.setdefault\(|\.get\()"([a-z_0-9]+)"')


class EveryKeyClassified(unittest.TestCase):
    def test_every_key_the_bot_writes_is_kept_or_dropped(self):
        """Every key bot/*.py reads or writes on a channel's state (st[...], chan(...)...) is in KEEP or DROP, so a
        new field is a decision, not a leak or a silent loss (manager 11-20)."""
        found = {"kind", "connector", "where", agentanswer.FLAG}  # (kinds.stamp, where.stamp; the flag's constant)
        for path in glob.glob(os.path.join(ROOT, "bot", "*.py")):
            with open(path, encoding="utf-8") as f:
                src = f.read()
            found.update(CHANNEL_KEY.findall(src))
            found.update(CHAN_SETDEFAULT.findall(src))
            for body in CHAN_UPDATE.findall(src):
                found.update(re.findall(r'"([a-z_0-9]+)"\s*:', body))
        self.assertIn("browsers", found, "the scan reaches chan(...).setdefault")
        self.assertIn("permission", found, "the scan reaches chan(...).update({...})")
        missing = sorted(found - set(backup.KEEP) - backup.DROP)
        self.assertEqual(missing, [], "classify these in bot/backup.py (KEEP or DROP)")


class FromBotSave(unittest.TestCase):
    def test_what_bot_save_writes_goes_through_without_a_dropped_field(self):
        """The file as Bot.save() writes it (stamps and all), then through the backup (reader-tests-from-the-writer)."""
        written = {}
        bot = mock.Mock(channels_state={"1": dict(FULL), "2": {"tmux": "dc-y", "cwd": "/w", "session_id": SID}})
        bot.where_context = lambda: ("lap", "/tmp/sock")
        bot.channel_name_of = lambda cid: "chan-%s" % cid
        with mock.patch.object(main, "save_later", lambda path, data: written.update(json.loads(json.dumps(data)))):
            main.Bot.save(bot)
        self.assertIn("where", written["2"], "the save stamped where")
        self.assertEqual(written["2"].get("kind"), "conversation")
        snap, unknown = backup.snapshot(written)
        self.assertEqual(unknown, [])
        for cid, rec in snap.items():
            for key in rec:
                self.assertIn(key, backup.KEEP, (cid, key))
        self.assertEqual(set(snap["2"]["where"]), {"follow"}, "where: follow only")
        self.assertEqual(snap["2"]["kind"], "conversation")
        self.assertNotIn("tmux", snap["2"])


class Holds(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.state)
        self.standby = {"lap": {"roles": ["dcbot"]}, "nv": {"roles": ["dcbot-standby"]}}

    def alive(self):
        with open(os.path.join(self.state, "bot.pid"), "w") as f:
            f.write(str(os.getpid()))

    def test_without_a_standby_the_bots_machine(self):
        self.assertEqual(backup.holds(self.state, {"lap": {"roles": ["dcbot"]}}, lambda: True), (True, ""))
        self.assertFalse(backup.holds(self.state, {}, lambda: False)[0])

    def test_with_a_standby_only_the_one_holding_the_lease(self):
        clock = lambda: 1000.0  # noqa: E731
        self.assertFalse(backup.holds(self.state, self.standby, lambda: True, clock)[0], "no bot running here")
        self.alive()
        self.assertFalse(backup.holds(self.state, self.standby, lambda: True, clock)[0], "never held it")
        mark = os.path.join(self.state, "lease-true-at")
        with mock.patch("time.time", lambda: 5000.0):
            lease.wrote_true(mark, 990.0)
        with mock.patch("time.time", lambda: 5010.0):
            self.assertEqual(backup.holds(self.state, self.standby, lambda: False, clock), (True, ""), "fresh: it is the bot")
        with mock.patch("time.time", lambda: 5000.0 + lease.FRESH_SECS + 20):
            got = backup.holds(self.state, self.standby, lambda: True, lambda: 990.0 + lease.FRESH_SECS + 10)
        self.assertFalse(got[0], "stale: another machine may have taken over")


class Command(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.state, self.inst = os.path.join(self.dir, "state"), os.path.join(self.dir, "inst")
        os.makedirs(self.state)
        os.makedirs(self.inst)
        with open(os.path.join(self.state, "channels.json"), "w") as f:
            json.dump({"1": dict(FULL)}, f)
        self.out = os.path.join(self.inst, backup.FILE)

    def run_cmd(self, machines, here):
        from altention import config as alt
        out = io.StringIO()
        with mock.patch.object(dcctl.config, "STATE", self.state), \
                mock.patch.object(alt, "inventory", lambda: {"machines": machines}), \
                mock.patch.object(alt, "bot_machine", lambda: ("lap", {}, here)), \
                mock.patch.object(alt, "instance_dir", lambda: self.inst), mock.patch("sys.stdout", out):
            code = dcctl.backup_cmd()
        return code, out.getvalue()

    def test_written_once_then_unchanged(self):
        code, said = self.run_cmd({}, True)
        self.assertEqual(code, 0, said)
        self.assertIn("written to", said)
        with open(self.out) as f:
            got = json.load(f)
        self.assertEqual((got["v"], list(got["channels"]), got["unclassified"]), (1, ["1"], []))
        before = os.stat(self.out).st_mtime_ns
        code, said = self.run_cmd({}, True)
        self.assertIn("unchanged", said)
        self.assertEqual(os.stat(self.out).st_mtime_ns, before, "not written again")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.out))), ["dcbot-channels.json"], "no temp file left")
        with open(os.path.join(self.state, backup.STATUS)) as f:
            st = json.load(f)
        self.assertEqual((st["changed"], st["channels"], st["unclassified"]), (False, 1, []))

    def test_a_machine_without_the_lease_writes_nothing(self):
        """manager 11-20: the standby (or the primary after a takeover) does not overwrite the backup with its old
        channels.json, nor its status."""
        code, said = self.run_cmd({"lap": {"roles": ["dcbot"]}, "nv": {"roles": ["dcbot-standby"]}}, True)
        self.assertEqual(code, 0)
        self.assertIn("nothing written", said)
        self.assertFalse(os.path.exists(self.out))
        self.assertFalse(os.path.exists(os.path.join(self.state, backup.STATUS)))
        code, said = self.run_cmd({}, False)
        self.assertIn("nothing written", said)
        self.assertFalse(os.path.exists(self.out))

    def test_unclassified_named(self):
        with open(os.path.join(self.state, "channels.json"), "w") as f:
            json.dump({"1": {"cwd": "/w", "new_field": 1}}, f)
        code, said = self.run_cmd({}, True)
        self.assertIn("new_field", said)
        with open(self.out) as f:
            self.assertEqual(json.load(f)["unclassified"], ["new_field"])

    def test_never_handed_to_another_machine(self):
        from altention.dc import botside
        with mock.patch.object(botside, "bot_machine", side_effect=AssertionError("asked")):
            for cmd in ("backup", "restore-state"):
                self.assertIsNone(botside.hand_over([cmd]))


class Restore(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.state)
        self.src = os.path.join(self.state, "backup.json")
        with open(self.src, "w") as f:
            json.dump({"v": 1, "channels": {"1": {"cwd": "/old"}, "2": {"cwd": "/two", "session_id": SID}},
                       "unclassified": []}, f)
        self.path = os.path.join(self.state, "channels.json")
        with open(self.path, "w") as f:
            json.dump({"1": {"cwd": "/new", "busy": True}}, f)

    def current(self):
        with open(self.path) as f:
            return json.load(f)

    def test_only_the_missing_channels_and_the_old_file_kept(self):
        added, kept = backup.restore(self.src, self.state)
        self.assertEqual(added, ["2"])
        self.assertEqual(self.current(), {"1": {"cwd": "/new", "busy": True}, "2": {"cwd": "/two", "session_id": SID}})
        with open(kept) as f:
            self.assertEqual(json.load(f), {"1": {"cwd": "/new", "busy": True}})
        self.assertTrue(os.path.basename(kept).startswith("channels.json.bak-"))

    def test_nothing_to_add_writes_nothing(self):
        backup.restore(self.src, self.state)
        files = sorted(os.listdir(self.state))
        self.assertEqual(backup.restore(self.src, self.state), ([], None))
        self.assertEqual(sorted(os.listdir(self.state)), files)

    def test_no_state_yet(self):
        os.remove(self.path)
        added, kept = backup.restore(self.src, self.state)
        self.assertEqual((added, kept), (["1", "2"], None))

    def test_refused_while_the_bot_runs(self):
        with open(os.path.join(self.state, "bot.pid"), "w") as f:
            f.write(str(os.getpid()))
        with self.assertRaises(backup.Refused):
            backup.restore(self.src, self.state)
        self.assertEqual(self.current(), {"1": {"cwd": "/new", "busy": True}})

    def test_refused_while_the_supervisor_holds_its_lock(self):
        lock = open(os.path.join(self.state, "supervisor.lock"), "w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX)
        pid = os.fork()
        if pid == 0:  # (a lock is per open file: another process must try it)
            try:
                backup.restore(self.src, self.state)
                os._exit(1)
            except backup.Refused:
                os._exit(0)
            except BaseException:
                os._exit(2)
        self.assertEqual(os.waitpid(pid, 0)[1], 0)
        self.assertEqual(self.current(), {"1": {"cwd": "/new", "busy": True}})

    def test_refused_on_a_file_that_is_not_a_backup_or_a_broken_state(self):
        with open(self.src, "w") as f:
            json.dump({"1": {"cwd": "/x"}}, f)
        with self.assertRaises(backup.Refused):
            backup.restore(self.src, self.state)
        with open(self.src, "w") as f:
            json.dump({"v": 1, "channels": {"2": {}}}, f)
        with open(self.path, "w") as f:
            f.write("{cut sho")
        with self.assertRaises(backup.Refused):
            backup.restore(self.src, self.state)
        with open(self.path) as f:
            self.assertEqual(f.read(), "{cut sho", "left as it was")

    def test_the_command_says_refused_and_exits_1(self):
        with open(os.path.join(self.state, "bot.pid"), "w") as f:
            f.write(str(os.getpid()))
        out = io.StringIO()
        with mock.patch.object(dcctl.config, "STATE", self.state), mock.patch("sys.stdout", out):
            self.assertEqual(dcctl.restore_cmd(self.src), 1)
        self.assertIn("refused", out.getvalue())


if __name__ == "__main__":
    unittest.main()
