"""The engine moved to another folder (C-14, C-17): apply writes each item's line once, at the new path, in the old
line's place; the old engine's lines that have no counterpart go; the same hook at the old path is replaced in place,
not added a second time. Only an old folder that is plainly an engine's (a link to this one, another checkout, or
a folder gone that a person named with --moved-from) is taken; someone else's folder, and a gone one nobody named,
is left alone (manager 2026-10-16). C-15: a line marked with the plugin alone gets
the item's name in its marker. C-16: doctor does not say "runs twice" before the new line is there."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import applycore  # noqa: E402
import plancore  # noqa: E402
import test_applycore as base  # noqa: E402
from test_applycore import ENGINE, OTHERS, want  # noqa: E402

OLD = "/srv/old place/agent-home"  # not there: the old folder of an engine that moved away


def core_wants(engine=ENGINE):
    dc = '%s/dcbot' % engine
    return [want("core", "supervisor-boot", "@reboot", 'cd "%s" && /usr/bin/python3 supervisor.py >> state/supervisor.out 2>&1' % dc),
            want("core", "supervisor", "*/5 * * * *", 'cd "%s" && /usr/bin/python3 supervisor.py >> state/supervisor.out 2>&1' % dc),
            want("core", "schedule", "* * * * *", 'f="%s/altention/cron_schedule.py"; [ ! -f "$f" ] || /usr/bin/python3 "$f" >> '
                 '"$HOME/.local/state/altention/cron-schedule.log" 2>&1' % engine),
            want("chatlog", "sync", "10,25,40,55 * * * *", '/usr/bin/python3 "%s/chatlog/chatlog.py" sync >/dev/null 2>&1' % engine)]


def planner_wants(engine=ENGINE):
    return [want("planner", n, t, '"%s/bin/altention" dc post 22 \'%s\' >>"$HOME/.cache/planner.out" 2>&1' % (engine, n))
            for n, t in (("morning", "0 8 * * *"), ("evening", "0 22 * * *"))]


def old_lines(folder):
    """The lines an older engine at `folder` wrote: the plugin alone in the marker; the planner's through bin/dcpost."""
    lines = [("%s  # altention:%s" % (w["text"], w["plugin"])).encode("utf-8") for w in core_wants(folder)]
    lines += [('0 8 * * * "%s/bin/dcpost" 22 "morning" >>"$HOME/.cache/planner.out" 2>&1  # altention:planner' % folder).encode("utf-8"),
              ('0 22 * * * "%s/bin/dcpost" 22 "evening" >>"$HOME/.cache/planner.out" 2>&1  # altention:planner' % folder).encode("utf-8")]
    return lines


def named(ws):
    return [("%s  # altention:%s/%s" % (w["text"], w["plugin"], w["name"])).encode("utf-8") for w in ws]


class Moved(base.Rig):
    def apply_moved(self, lines, wanted, engine=ENGINE, moved_from=(OLD,)):
        self.put(lines)
        raw = plancore.read_crontab_raw()
        texts = [t for t in (plancore.text(l) for l in plancore.raw_lines(raw)) if t is not None]
        have, paused, _ = plancore.installed_cron(texts, engine)
        result = plancore.compare(list(wanted), have, [], paused, [], engine, (), moved_from)
        new, actions, removed, kept = applycore.new_crontab(plancore.raw_lines(raw), result, [], ())
        backup = applycore.make_backup(raw, None)
        return applycore.write_crontab(raw, new, removed, kept, backup), actions, result

    def test_one_line_per_item_at_the_new_path_and_the_old_ones_gone(self):
        ws = core_wants() + planner_wants()
        mine = b"0 8 * * * /opt/me/brief.sh  # not ours"
        wrote, actions, result = self.apply_moved(OTHERS + old_lines(OLD) + [mine], ws)
        self.assertTrue(wrote)
        after = self.get()
        self.assertEqual([l for l in after if l in OTHERS + [mine]], OTHERS + [mine], "other people's lines, byte for byte")
        ours = [l for l in after if b"# altention:" in l]
        self.assertEqual(sorted(ours), sorted(named(ws)), "each item once, the new path, its name in the marker")
        self.assertFalse(any(OLD.encode("utf-8") in l for l in after), "nothing of the old folder left")
        self.assertEqual(sorted(r[0] for r in result if r[0] != "same"), ["missing"] * 2 + ["moved"] * 2 + ["replaced"] * 4)
        self.assertTrue(any("搬家前的舊路徑" in a for a in actions))
        wrote, _, _ = self.apply_moved(after, ws)
        self.assertFalse(wrote, "then nothing to change")

    def test_the_new_line_takes_the_old_ones_place(self):
        w = core_wants()[3:]
        self.apply_moved(OTHERS + old_lines(OLD)[3:4] + [b"0 1 * * * /opt/after.sh"], w)
        self.assertEqual(self.get(), OTHERS + named(w) + [b"0 1 * * * /opt/after.sh"])

    def test_a_link_to_this_engine_is_its_old_name(self):
        home = tempfile.mkdtemp(prefix="家 moved ")
        try:
            engine = os.path.join(home, "altention", "engine")
            os.makedirs(engine)
            link = os.path.join(home, "agent-home")
            os.symlink(engine, link)
            w = core_wants(engine)[3:]
            self.apply_moved(OTHERS + old_lines(link)[3:4], w, engine)
            self.assertEqual(self.get(), OTHERS + named(w))
        finally:
            base.shutil.rmtree(home, ignore_errors=True)

    def test_someone_elses_folder_is_not_taken(self):
        home = tempfile.mkdtemp(prefix="家 other ")
        try:
            other = os.path.join(home, "my tools")  # there, and not an engine (no altention/config.py)
            os.makedirs(os.path.join(other, "chatlog"))
            w = core_wants()[3:]
            theirs = old_lines(other)[3:4]
            wrote, actions, result = self.apply_moved(OTHERS + theirs, w)
            self.assertEqual(self.get(), OTHERS + theirs + named(w), "left as it is; the new line added")
            self.assertIn("extra", [r[0] for r in result])
            os.makedirs(os.path.join(other, "altention"))
            open(os.path.join(other, "altention", "config.py"), "w").close()  # now another checkout of the engine
            self.apply_moved(OTHERS + theirs, w)
            self.assertEqual(self.get(), OTHERS + named(w))
        finally:
            base.shutil.rmtree(home, ignore_errors=True)

    def test_a_gone_folder_nobody_named_is_left_and_said(self):
        w = core_wants()[3:]
        old = old_lines(OLD)[3:4]
        wrote, actions, result = self.apply_moved(OTHERS + old, w, moved_from=())
        self.assertIn("gone", [r[0] for r in result])
        self.assertEqual(self.get(), OTHERS + old + named(w), "kept; the new line added")
        self.assertTrue(any("--moved-from" in a for a in actions))

    def test_every_line_of_a_gone_folder_is_said_the_same_way(self):
        """S4: the old planner lines (no item of their own) in the same gone folder were said as "ours, nobody asks
        for it" beside the others' "folder gone": all of them are "gone" now, kept, one thing to do."""
        ws = core_wants() + planner_wants()
        _, actions, result = self.apply_moved(OTHERS + old_lines(OLD), ws, moved_from=())
        statuses = sorted(r[0] for r in result if r[2])
        self.assertEqual(statuses, ["gone"] * 6)
        self.assertFalse(any("沒有插件要" in a for a in actions))

    def test_two_old_folders_each_taken(self):
        """#code-review-2 2026-10-16: a second old folder, gone and named, or a link to this engine, is dealt with too."""
        home = tempfile.mkdtemp(prefix="家 two ")
        try:
            engine = os.path.join(home, "altention", "engine")
            os.makedirs(engine)
            first = os.path.join(home, "agent-home")
            os.symlink(engine, first)
            for second, named_ in ((os.path.join(home, "older-engine"), (os.path.join(home, "older-engine"),)),  # gone, named
                                   (os.path.join(home, "older-link"), ())):                                     # a link
                if second.endswith("link"):
                    os.symlink(engine, second)
                ws = core_wants(engine) + planner_wants(engine)
                self.apply_moved(OTHERS + old_lines(first) + old_lines(second), ws, engine, named_)
                after = self.get()
                self.assertEqual(sorted(l for l in after if b"# altention:" in l), sorted(named(ws)), second)
                self.assertFalse(any(first.encode() in l or second.encode() in l for l in after), second)
        finally:
            base.shutil.rmtree(home, ignore_errors=True)

    def test_this_engine_inside_another_checkout_keeps_its_own_lines(self):
        """C-38: a folder above this engine that is an engine too (this engine put inside another checkout) took this
        engine's own lines for moved, and apply removed them. A line in the outer checkout is still taken."""
        home = tempfile.mkdtemp(prefix="家 nested ")
        try:
            outer = os.path.join(home, "outer")
            os.makedirs(os.path.join(outer, "altention"))
            open(os.path.join(outer, "altention", "config.py"), "w").close()
            engine = os.path.join(outer, "sub", "engine")
            os.makedirs(engine)
            ws = core_wants(engine)[3:] + planner_wants(engine)
            own = old_lines(engine)[4:5]  # this engine's own line no item asks for: not moved
            wrote, actions, result = self.apply_moved(OTHERS + own + old_lines(outer)[3:4], ws, engine, ())
            kinds = {r[2]: r[0] for r in result if r[2]}
            self.assertEqual(kinds[plancore.text(own[0])], "extra")
            self.assertEqual(sorted(kinds.values()), ["extra", "replaced"], "the outer checkout's line is taken")
            after = self.get()
            self.assertIn(own[0], after)
            self.assertFalse(any(b"%s/chatlog" % outer.encode() in l for l in after))
        finally:
            base.shutil.rmtree(home, ignore_errors=True)

    def test_without_the_engine_nothing_is_taken_for_moved(self):
        """compare() called as before (no engine): the old behaviour, lines kept and the new ones added."""
        self.put(OTHERS + old_lines(OLD)[3:4])
        raw = plancore.read_crontab_raw()
        texts = [t for t in (plancore.text(l) for l in plancore.raw_lines(raw)) if t is not None]
        have, paused, _ = plancore.installed_cron(texts, ENGINE)
        self.assertEqual([r[0] for r in plancore.compare(core_wants()[3:], have, [], paused, [])], ["missing", "extra"])

    def test_a_plugin_alone_marker_gets_the_name(self):
        """C-15: a line plan finds the same, marked with its plugin alone, gets <plugin>/<name>; its command stays."""
        w = core_wants()[3:]
        line = ("%s  # altention:chatlog" % w[0]["text"]).encode("utf-8")
        self.apply_moved(OTHERS + [line], w)
        self.assertEqual(self.get(), OTHERS + named(w))
        renamed = ("%s  # altention:chatlog/older-name" % w[0]["text"]).encode("utf-8")
        self.apply_moved(OTHERS + [renamed], w)
        self.assertEqual(self.get(), OTHERS + [renamed], "a name already there is not rewritten")


class MovedHooks(base.Rig):
    def setUp(self):  # as test_applycore.Settings (not subclassed: its tests would run twice)
        super().setUp()
        self.path = os.path.expanduser("~/.claude/settings.json")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)
        super().tearDown()

    def hook(self, event, matcher, command, timeout):
        return {"plugin": "chatlog", "name": event, "kind": "hook", "text": command,
                "key": (event, matcher, tuple(plancore.words(command)), timeout)}

    def put_settings(self, s):
        self.before = s
        self.raw = json.dumps(s).encode("utf-8")
        with open(self.path, "wb") as f:
            f.write(self.raw)

    def run_apply(self, wants, engine=ENGINE, moved_from=(OLD,)):
        result = plancore.compare(wants, [], plancore.installed_hooks(self.before, engine), [], [], engine,
                                  plancore.other_hooks(self.before, engine), moved_from)
        new, actions, added = applycore.new_settings(self.before, result)
        wrote = applycore.write_settings(self.raw, self.before, new, added, "b")
        with open(self.path) as f:
            return wrote, json.load(f), actions, result

    def test_the_same_hook_at_the_old_path_is_replaced_in_place(self):
        old = '/usr/bin/python3 "%s/chatlog/chatlog.py" hook' % OLD
        guard = "'%s/dcbot/tools/dchook' --global --guard" % OLD
        self.put_settings({"model": "x", "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "other-tool", "timeout": 5}, {"type": "command", "command": old}]}],
            "PreToolUse": [{"hooks": [{"type": "command", "command": guard, "timeout": 20}]}]}})
        new_cmd = '/usr/bin/python3 "%s/chatlog/chatlog.py" hook' % ENGINE
        new_guard = "'%s/dcbot/tools/dchook' --global --guard" % ENGINE
        wants = [self.hook("Stop", "", new_cmd, 30), self.hook("PreToolUse", "", new_guard, 5)]
        wrote, after, actions, result = self.run_apply(wants)
        self.assertTrue(wrote)
        self.assertEqual([r[0] for r in result], ["replaced", "replaced"])
        self.assertEqual(after["hooks"]["Stop"][0]["hooks"], [{"type": "command", "command": "other-tool", "timeout": 5},
                                                             {"type": "command", "command": new_cmd, "timeout": 30}])
        self.assertEqual(after["hooks"]["PreToolUse"][0]["hooks"], [{"type": "command", "command": new_guard, "timeout": 5}])
        self.assertEqual(after["model"], "x")
        self.assertTrue(any("換掉 hook" in a for a in actions))
        self.put_settings(after)
        wrote, again, _, result = self.run_apply(wants)
        self.assertFalse(wrote, "then nothing to change")
        self.assertEqual([r[0] for r in result], ["same", "same"])

    def test_a_hook_in_someone_elses_folder_is_left_and_the_new_one_added(self):
        home = tempfile.mkdtemp(prefix="家 hooks ")
        try:
            os.makedirs(os.path.join(home, "chatlog"))
            theirs = '/usr/bin/python3 "%s/chatlog/chatlog.py" hook' % home
            self.put_settings({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": theirs}]}]}})
            new_cmd = '/usr/bin/python3 "%s/chatlog/chatlog.py" hook' % ENGINE
            wrote, after, _, result = self.run_apply([self.hook("Stop", "", new_cmd, None)])
            self.assertEqual([r[0] for r in result], ["missing"])
            self.assertEqual([h["command"] for g in after["hooks"]["Stop"] for h in g["hooks"]], [theirs, new_cmd])
            self.assertEqual(len(after["hooks"]["Stop"]), 2, "ours in a group of its own")
        finally:
            base.shutil.rmtree(home, ignore_errors=True)

    def test_a_gone_folder_hook_nobody_named_is_left(self):
        """manager 2026-10-16: hooks carry no marker; a hook in a folder that is gone is someone's as far as we can
        tell, unless a person names that folder."""
        theirs = '/usr/bin/python3 "/srv/gone-tools/chatlog/chatlog.py" hook'
        self.put_settings({"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": theirs}]}]}})
        new_cmd = '/usr/bin/python3 "%s/chatlog/chatlog.py" hook' % ENGINE
        wrote, after, actions, result = self.run_apply([self.hook("SessionEnd", "", new_cmd, None)], moved_from=())
        self.assertEqual(sorted(r[0] for r in result), ["gone", "missing"])
        self.assertEqual([h["command"] for g in after["hooks"]["SessionEnd"] for h in g["hooks"]], [theirs, new_cmd])
        self.assertTrue(any("--moved-from" in a for a in actions))

    def test_a_second_copy_at_another_old_folder_is_taken_out(self):
        home = tempfile.mkdtemp(prefix="家 copies ")
        try:
            engine = os.path.join(home, "eng")
            os.makedirs(engine)
            link = os.path.join(home, "older-link")  # a second old name: a link to this engine
            os.symlink(engine, link)
            a = '/usr/bin/python3 "%s/chatlog/chatlog.py" hook' % OLD
            b = '/usr/bin/python3 "%s/chatlog/chatlog.py" hook' % link
            self.put_settings({"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-tool"},
                                                                           {"type": "command", "command": a}]},
                                                                {"hooks": [{"type": "command", "command": b}]}]}})
            new_cmd = '/usr/bin/python3 "%s/chatlog/chatlog.py" hook' % engine
            wrote, after, actions, result = self.run_apply([self.hook("Stop", "", new_cmd, None)], engine)
            self.assertTrue(wrote)
            self.assertEqual(sorted(r[0] for r in result), ["moved", "replaced"])
            self.assertEqual(sorted(h["command"] for g in after["hooks"]["Stop"] for h in g["hooks"]), sorted(["other-tool", new_cmd]))
            self.put_settings(after)
            self.assertFalse(self.run_apply([self.hook("Stop", "", new_cmd, None)], engine)[0], "then nothing to change")
        finally:
            base.shutil.rmtree(home, ignore_errors=True)

    def test_a_replaced_hook_with_another_timeout_is_refused(self):
        """#code-review-2 2026-10-16: the guard checks the replaced hook's new timeout is the item's."""
        old = '/usr/bin/python3 "%s/chatlog/chatlog.py" hook' % OLD
        self.put_settings({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": old, "timeout": 20}]}]}})
        new_cmd = '/usr/bin/python3 "%s/chatlog/chatlog.py" hook' % ENGINE
        result = plancore.compare([self.hook("Stop", "", new_cmd, 5)], [], [], [], [], ENGINE, plancore.other_hooks(self.before, ENGINE), (OLD,))
        new, _, added = applycore.new_settings(self.before, result)
        new["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 999
        with self.assertRaises(applycore.Refused) as e:
            applycore.write_settings(self.raw, self.before, new, added, "b")
        self.assertIn("timeout", str(e.exception))
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), self.raw)

    def test_the_guard_still_refuses_anything_else_changed(self):
        old = '/usr/bin/python3 "%s/chatlog/chatlog.py" hook' % OLD
        self.put_settings({"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": old}]}]}})
        new_cmd = '/usr/bin/python3 "%s/chatlog/chatlog.py" hook' % ENGINE
        result = plancore.compare([self.hook("Stop", "", new_cmd, None)], [], [], [], [], ENGINE, plancore.other_hooks(self.before, ENGINE), (OLD,))
        new, _, added = applycore.new_settings(self.before, result)
        new["model"] = "y"
        with self.assertRaises(applycore.Refused):
            applycore.write_settings(self.raw, self.before, new, added, "b")


class Doctor(unittest.TestCase):
    def records(self, report):
        import test_doctor
        t = test_doctor.Records("test_one_item_with_two_lines_is_a_problem")  # its helpers, not its test
        inv = {"machines": {"host-a": {"ssh": ["HOST-A"], "roles": ["chatlog", "progress", "dcbot", "planner"]}}}
        code, recs = t.records({"host-a": test_doctor.healthy(apply_report=report)}, inv=inv)
        return [r for r in recs if r["machine"] == "host-a" and r["id"] == "plugins.cron-twice"][0]

    def test_an_old_line_before_the_new_one_is_written_is_not_twice(self):
        old = {"status": "extra", "plugin": "planner", "name": None, "kind": "cron", "installed": "0 8 * * * x  # altention:planner"}
        add = {"status": "missing", "plugin": "planner", "name": "morning", "kind": "cron", "wanted": "0 8 * * * y"}
        r = self.records({"remove": [old], "add": [add]})
        self.assertEqual(r["status"], "warn")
        self.assertIn("還不會跑兩次", r["msg"])
        self.assertNotIn("會跟新的一起跑", r["msg"])
        self.assertEqual(self.records({"remove": [old]})["status"], "fail", "the new line there: twice")
        moved = dict(old, status="moved")
        r = self.records({"remove": [moved], "add": [add]})
        self.assertEqual(r["status"], "warn")
        self.assertIn("apply 會換掉", r["msg"])

    def test_old_engine_lists_every_line_and_hook_and_cron_twice_does_not_cry_twice(self):
        import test_doctor
        t = test_doctor.Records("test_one_item_with_two_lines_is_a_problem")
        inv = {"machines": {"host-a": {"ssh": ["HOST-A"], "roles": ["chatlog", "progress", "dcbot", "planner"]}}}
        gone = [{"status": "gone", "plugin": "core", "name": "sync", "kind": "cron", "installed": "3 * * * * /old/altention/cron_sync.py"},
                {"status": "gone", "plugin": "planner", "name": None, "kind": "cron", "installed": "0 8 * * * /old/bin/dcpost 1 x"}] + \
               [{"status": "gone", "plugin": "core", "name": "Stop", "kind": "hook", "installed": "'/old/dcbot/tools/dchook' --global %d" % i}
                for i in range(13)]
        dead = "0 22 * * * /old/bin/dcpost 1 y  # altention:planner"
        rep_ = {"format": 1, "add": [], "remove": [{"status": "extra", "plugin": "planner", "kind": "cron", "installed": dead}],
                "retime": [], "core": [], "unknown": [], "problems": [], "disabled": [], "gone": gone, "at": 2000000000}
        code, recs = t.records({"host-a": test_doctor.healthy(apply_report=rep_, cron_dead=[dead], now=2000000000)}, inv=inv)
        by = {r["id"]: r for r in recs if r["machine"] == "host-a"}
        self.assertEqual(by["plugins.old-engine"]["status"], "warn")
        self.assertIn("2 行排程、13 個 hook", by["plugins.old-engine"]["msg"])
        self.assertEqual(by["plugins.old-engine"]["msg"].count("dchook"), 13, "every hook listed")
        self.assertIn("--moved-from", by["plugins.old-engine"]["fix"])
        self.assertEqual(by["plugins.cron-twice"]["status"], "warn")
        self.assertIn("跑不起來，不會重複", by["plugins.cron-twice"]["msg"])
        self.assertNotEqual(by["plugins.would-apply"]["status"], "fail")
        self.assertIn("plugins.old-engine", by["plugins.would-apply"]["msg"])

    def test_the_probe_lists_lines_whose_program_is_gone(self):
        import shlex
        import subprocess
        import doctor
        home = tempfile.mkdtemp(prefix="家 dead ")
        try:
            eng = os.path.join(home, "engine")
            os.makedirs(os.path.join(eng, "chatlog"))
            open(os.path.join(eng, "chatlog", "chatlog.py"), "w").close()
            table = os.path.join(home, "table")
            with open(table, "w") as f:
                f.write('1 * * * * /usr/bin/python3 "%s/chatlog/chatlog.py" sync  # altention:chatlog/sync\n'
                        '2 * * * * /usr/bin/python3 "%s/old/chatlog/chatlog.py" sync  # altention:chatlog\n'
                        '3 * * * * "%s/old/bin/dcpost" 1 x  # altention:planner\n'
                        '4 * * * * /opt/gone.py  # not ours\n'
                        '5 * * * * PROGRESS_CLAUDE="%s/gone/.local/bin/claude" /usr/bin/python3 "%s/chatlog/chatlog.py" run  # altention:progress\n'
                        '@reboot cd "%s/old/dcbot" && /usr/bin/python3 supervisor.py >> state/x.out 2>&1  # altention:core\n'
                        '@reboot cd "%s" && /usr/bin/python3 chatlog/chatlog.py >> x.out 2>&1  # altention:core\n'
                        '6 * * * * f="%s/old/altention/cron_schedule.py"; [ ! -f "$f" ] || /usr/bin/python3 "$f"  # altention:core\n'
                        '7 * * * * f="%s/chatlog/chatlog.py"; [ ! -f "$f" ] || /usr/bin/python3 "$f"  # altention:core\n'
                        % (eng, home, home, home, eng, home, eng, home, eng))
            bindir = os.path.join(home, "bin")
            os.makedirs(bindir)
            with open(os.path.join(bindir, "crontab"), "w") as f:
                f.write("#!/bin/sh\ncat %s\n" % shlex.quote(table))
            os.chmod(os.path.join(bindir, "crontab"), 0o755)
            r = subprocess.run([sys.executable, "-"], input="ENGINE = %r\n" % eng + doctor.PROBE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, timeout=120,
                               env=dict(os.environ, HOME=home, PATH=bindir + ":" + os.environ.get("PATH", "")))
            dead = json.loads(r.stdout.strip().splitlines()[-1])["cron_dead"]
            self.assertEqual([d.split()[0] for d in dead], ["2", "3", "@reboot", "6"],
                             "a VAR= path is no program of the line (5 is not dead); cd and $f followed (@reboot old, 6)")
        finally:
            base.shutil.rmtree(home, ignore_errors=True)

    def test_a_gone_old_folder_is_said_with_the_way_to_name_it(self):
        import test_doctor
        t = test_doctor.Records("test_one_item_with_two_lines_is_a_problem")
        gone = {"status": "gone", "plugin": "chatlog", "name": None, "kind": "hook", "installed": "/usr/bin/python3 /x/chatlog/chatlog.py hook"}
        code, recs = t.records({"host-a": test_doctor.healthy(apply_report={"remove": [gone]})})
        r = [r for r in recs if r["machine"] == "host-a" and r["id"] == "plugins.old-engine"][0]
        self.assertEqual(r["status"], "warn")
        self.assertIn("--moved-from", r["fix"])
        code, recs = t.records({"host-a": test_doctor.healthy()})
        self.assertEqual([r for r in recs if r["machine"] == "host-a" and r["id"] == "plugins.old-engine"][0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
